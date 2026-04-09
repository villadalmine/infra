# Hermes Agent - Arquitectura y Troubleshooting

## Resumen Ejecutivo

Hermes Agent es un asistente de IA con acceso a Kubernetes vía MCP (Model Context Protocol).
**Toda comunicación LLM pasa por LiteLLM proxy interno** - sin acceso directo a APIs externas.

## Arquitectura de Componentes

```
Usuario (Telegram)
    │
    ▼
Hermes Agent Pod (2 contenedores)
    │
    ├─► hermes-agent (contenedor principal)
    │   ├── Gateway: Telegram polling
    │   ├── Agent: tool calling + reasoning
    │   └── Config: /opt/data/config.yaml + .env
    │
    └─► kubernetes-mcp-server (sidecar)
        ├── Puerto: 8080 (HTTP /mcp)
        ├── RBAC: ClusterRole read-only
        └── Tools: 23 Kubernetes operations
    │
    ▼
LiteLLM Proxy (ai namespace)
    ├── Multi-tenant: sk-hermes-internal
    ├── Model: hermes-qwen (qwen/qwen3-coder:free via OpenRouter)
    ├── Fallback chain: free → cheap → strong
    └── Endpoint: http://litellm-proxy.ai.svc.cluster.local:4000/v1
    │
    ▼
OpenRouter API (externo)
```

## Arquitectura de Seguridad

### Principio: Zero Direct Access

**NINGÚN agente tiene acceso directo a APIs externas.**

- ✅ Hermes → LiteLLM proxy → OpenRouter
- ✅ Holmes → LiteLLM proxy → OpenRouter
- ✅ kagent → LiteLLM proxy → OpenRouter
- ❌ Hermes -/→ OpenRouter (bloqueado)

### Multi-tenancy en LiteLLM

Cada agente tiene su propia API key en LiteLLM:

| Agente | API Key | Modelo default |
|--------|---------|----------------|
| Hermes | `sk-hermes-internal` | `hermes-qwen` |
| Holmes | `sk-holmes-internal` | `cheap` |
| kagent | `sk-kagent-internal` | `cheap` |
| OpenClaw | `sk-openclaw-internal` | `free` |

### RBAC de Kubernetes

El ServiceAccount `hermes-agent-mcp` tiene permisos **read-only**:

```yaml
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "namespaces", "services", "nodes", "events"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets", "daemonsets"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["batch"]
    resources: ["jobs", "cronjobs"]
    verbs: ["get", "list", "watch"]
```

**NO puede**: create, update, delete, patch recursos del cluster.

## Configuración Crítica

### 1. config.yaml (ConfigMap hermes-config)

```yaml
model:
  provider: custom              # OpenAI-compatible endpoint
  default: hermes-qwen          # Modelo en LiteLLM
  base_url: http://litellm-proxy.ai.svc.cluster.local:4000/v1
  api_key_env: OPENAI_API_KEY   # Lee de variable de entorno

agent:
  tool_use_enforcement: true    # Fuerza uso de tools cuando están disponibles
  system_prompt: |
    You are an assistant for Kubernetes cluster inspection.
    When the user asks about Kubernetes pods, namespaces, deployments, or logs,
    you must use the Kubernetes MCP tools.
    Do not use kubectl, oc, or terminal commands for cluster inspection.
    If a Kubernetes MCP tool is available, call it directly.

mcp_servers:
  kubernetes:
    url: http://127.0.0.1:8080/mcp
    timeout: 120
    connect_timeout: 30
```

**Ubicación en pod**: `/opt/data/config.yaml`

### 2. .env (creado por initContainer)

```bash
OPENAI_API_KEY=sk-hermes-internal
OPENAI_API_BASE=http://litellm-proxy.ai.svc.cluster.local:4000/v1
```

**Ubicación en pod**: `/opt/data/.env`

**Por qué initContainer**: `readOnlyRootFilesystem: true` en securityContext.
No se puede crear archivos en runtime, entonces el initContainer (sin esa restricción)
lo crea antes de que arranque el contenedor principal.

### 3. gateway.json (ConfigMap hermes-gateway-config)

```json
{
  "telegram": {
    "mode": "polling",
    "allowed_users": ["8492872858"]
  }
}
```

**Ubicación en pod**: `/opt/data/gateway.json`

### 4. Secretos (Secret hermes-secrets)

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: hermes-secrets
  namespace: ai
stringData:
  TELEGRAM_BOT_TOKEN: "..."
  DISCORD_BOT_TOKEN: ""
  OPENAI_API_KEY: "sk-hermes-internal"
```

**Fuente de verdad**: `roles/install-hermes-agent/defaults/secrets.yml` (gitignored).

## Flujo de una Consulta

### Ejemplo: "Cuántos pods hay en el namespace ai?"

```
1. Usuario envía mensaje en Telegram
   ↓
2. Telegram API → Hermes (polling)
   Log: "inbound message: platform=telegram user=villadalmine msg='Cuántos pod hay en el ns'"
   ↓
3. Hermes lee config.yaml + .env
   Log: "Loaded environment variables from /opt/data/.env"
   ↓
4. Hermes conecta al MCP server (sidecar)
   Log: "Received session ID: 7ZURTO66MQHW65YA5ITRQIO5QS"
   Log: "MCP server 'kubernetes' (HTTP): registered 23 tool(s)"
   ↓
5. Hermes envía pregunta a LiteLLM (modelo hermes-qwen)
   Request: POST http://litellm-proxy.ai.svc.cluster.local:4000/v1/chat/completions
   Headers: Authorization: Bearer sk-hermes-internal
   ↓
6. LiteLLM enruta a OpenRouter (qwen/qwen3-coder:free)
   ↓
7. Modelo decide usar tool: mcp_kubernetes_pods_list_in_namespace
   ↓
8. Hermes llama al MCP server (127.0.0.1:8080/mcp)
   Tool call: mcp_kubernetes_pods_list_in_namespace(namespace="ai")
   ↓
9. MCP server ejecuta kubectl (via client-go)
   Usa ServiceAccount token + kubeconfig generado en sidecar
   ↓
10. MCP devuelve lista de pods a Hermes
    Result: 6 pods con nombres + status
    ↓
11. Hermes envía resultado al modelo para formatear respuesta
    ↓
12. Modelo genera respuesta en lenguaje natural
    Log: "response ready: time=191.4s api_calls=2 response=313 chars"
    ↓
13. Hermes envía respuesta a Telegram
    Log: "Sending response (313 chars) to 8492872858"
    ↓
14. Usuario recibe respuesta en Telegram
    "En el namespace ai hay 6 pods en total..."
```

### Tiempos esperados

- **Primera consulta con tool**: 60-200 segundos (cold start de modelo + MCP)
- **Consultas subsecuentes**: 10-30 segundos
- **Consultas sin tool**: 5-15 segundos

El modelo `qwen3-coder:free` es lento pero gratis. Si se necesita velocidad,
cambiar a `cheap` (qwen-turbo) en `hermes_model`.

## MCP Tools Disponibles

El sidecar kubernetes-mcp-server expone **23 tools**:

### Lectura de estado
- `mcp_kubernetes_namespaces_list` - listar namespaces
- `mcp_kubernetes_pods_list` - listar pods (todos los namespaces)
- `mcp_kubernetes_pods_list_in_namespace` - listar pods en un namespace
- `mcp_kubernetes_pods_get` - detalles de un pod específico
- `mcp_kubernetes_resources_list` - listar recursos genéricos
- `mcp_kubernetes_resources_get` - obtener un recurso específico
- `mcp_kubernetes_list_resources` - listar tipos de recursos
- `mcp_kubernetes_read_resource` - leer recurso por API path

### Logs y eventos
- `mcp_kubernetes_pods_log` - logs de un pod/contenedor
- `mcp_kubernetes_events_list` - eventos del cluster
- `mcp_kubernetes_nodes_log` - logs de journald de un nodo

### Métricas
- `mcp_kubernetes_pods_top` - CPU/RAM de pods
- `mcp_kubernetes_nodes_top` - CPU/RAM de nodos
- `mcp_kubernetes_nodes_stats_summary` - métricas summary de nodos

### Ejecución
- `mcp_kubernetes_pods_exec` - ejecutar comando en pod

### Escritura (NO recomendado en producción sin confirmation flow)
- `mcp_kubernetes_pods_run` - crear pod temporal
- `mcp_kubernetes_pods_delete` - borrar pod
- `mcp_kubernetes_resources_create_or_update` - crear/actualizar recurso
- `mcp_kubernetes_resources_delete` - borrar recurso
- `mcp_kubernetes_resources_scale` - escalar deployment/statefulset

### Configuración
- `mcp_kubernetes_configuration_view` - ver kubeconfig en uso

### Prompts
- `mcp_kubernetes_list_prompts` - listar prompts predefinidos
- `mcp_kubernetes_get_prompt` - obtener un prompt específico

## Troubleshooting

### Síntoma: Error 401 "Missing Authentication header"

**Causa**: Hermes no está enviando el header `Authorization` a LiteLLM.

**Verificaciones**:
1. ¿Existe `/opt/data/.env` en el pod?
   ```bash
   kubectl exec -n ai <pod> -c hermes-agent -- cat /opt/data/.env
   ```
   Debe contener:
   ```
   OPENAI_API_KEY=sk-hermes-internal
   OPENAI_API_BASE=http://litellm-proxy.ai.svc.cluster.local:4000/v1
   ```

2. ¿Existe `/opt/data/config.yaml`?
   ```bash
   kubectl exec -n ai <pod> -c hermes-agent -- cat /opt/data/config.yaml
   ```
   Debe tener `api_key_env: OPENAI_API_KEY`

3. ¿El secret tiene OPENAI_API_KEY?
   ```bash
   kubectl get secret hermes-secrets -n ai -o yaml
   ```

4. ¿El initContainer corrió?
   ```bash
   kubectl describe pod -n ai <pod> | grep -A 10 "Init Containers:"
   ```

**Fix**: Redeployar con Ansible:
```bash
make ai-hermes-agent
```

### Síntoma: MCP tools no aparecen / "No such tool"

**Causa**: Sidecar kubernetes-mcp-server no está corriendo o no es alcanzable.

**Verificaciones**:
1. ¿El sidecar está corriendo?
   ```bash
   kubectl get pod -n ai <pod> -o jsonpath='{.status.containerStatuses[*].name}'
   ```
   Debe mostrar: `hermes-agent kubernetes-mcp-server`

2. ¿El sidecar tiene logs?
   ```bash
   kubectl logs -n ai <pod> -c kubernetes-mcp-server
   ```
   Debe mostrar: `HTTP server starting on port 8080`

3. ¿Hermes ve el MCP server?
   ```bash
   kubectl logs -n ai <pod> -c hermes-agent | grep "MCP server"
   ```
   Debe mostrar: `registered 23 tool(s)`

**Fix**: Verificar que `config.yaml` tenga:
```yaml
mcp_servers:
  kubernetes:
    url: http://127.0.0.1:8080/mcp
```

### Síntoma: Se queda colgado / no responde

**Causa 1**: Modelo lento (qwen3-coder:free es gratis pero lento).

**Solución**: Cambiar a modelo más rápido:
```yaml
# roles/install-hermes-agent/defaults/main.yml
hermes_model: "cheap"  # qwen-turbo, $0.033/M tokens
```

**Causa 2**: Tool loop (modelo sigue llamando tools sin terminar).

**Verificación**:
```bash
kubectl logs -n ai <pod> -c hermes-agent | grep "api_calls"
```
Si `api_calls` es > 10, hay loop.

**Solución**: Mejorar system_prompt para ser más específico sobre cuándo parar.

### Síntoma: RBAC Forbidden errors en MCP

**Causa**: ServiceAccount no tiene permisos para el recurso solicitado.

**Verificación**:
```bash
kubectl describe clusterrole hermes-agent-mcp-readonly
```

**Fix**: Agregar el verbo/recurso necesario al ClusterRole en
`roles/install-hermes-agent/templates/hermes-static-mcp.yaml.j2`

### Síntoma: Telegram no conecta

**Causa 1**: Token inválido o revocado.

**Verificación**:
```bash
kubectl get secret hermes-secrets -n ai -o jsonpath='{.data.TELEGRAM_BOT_TOKEN}' | base64 -d
```

Probar el token manualmente:
```bash
curl https://api.telegram.org/bot<TOKEN>/getMe
```

**Causa 2**: IP bloqueada por Telegram (raro).

**Verificación**: Ver logs de gateway:
```bash
kubectl exec -n ai <pod> -c hermes-agent -- cat /opt/data/logs/gateway.log | grep -i "telegram"
```

### Síntoma: Usa kubectl en lugar de MCP tools

**Causa**: System prompt no es suficientemente directivo.

**Fix**: Asegurar que `hermes_system_prompt` en defaults/main.yml dice:
```
When the user asks about Kubernetes..., you MUST use the Kubernetes MCP tools.
Do not use kubectl, oc, or terminal commands.
```

**Verificar en runtime**:
```bash
kubectl exec -n ai <pod> -c hermes-agent -- cat /opt/data/config.yaml
```

## Logs y Debugging

### Archivos de log dentro del pod

```
/opt/data/logs/
├── gateway.log    # Telegram, gateway lifecycle
├── agent.log      # Agent reasoning, tool calls (same as gateway.log)
└── errors.log     # Stack traces (empty = good)
```

### Comandos útiles

```bash
# Ver pod actual
kubectl get pods -n ai -l app=hermes-agent-mcp

# Logs del contenedor principal
kubectl logs -n ai <pod> -c hermes-agent --tail=100

# Logs del MCP server
kubectl logs -n ai <pod> -c kubernetes-mcp-server --tail=50

# Logs internos de Hermes (más detallados)
kubectl exec -n ai <pod> -c hermes-agent -- cat /opt/data/logs/gateway.log

# Ver archivos montados
kubectl exec -n ai <pod> -c hermes-agent -- ls -la /opt/data/

# Ver config actual
kubectl exec -n ai <pod> -c hermes-agent -- cat /opt/data/config.yaml

# Ver .env
kubectl exec -n ai <pod> -c hermes-agent -- cat /opt/data/.env

# Ver estado de sesiones activas
kubectl exec -n ai <pod> -c hermes-agent -- ls -la /opt/data/sessions/

# Ver si el gateway está vivo
kubectl exec -n ai <pod> -c hermes-agent -- cat /opt/data/gateway.pid
```

### Debugging de tool calls

Cuando Hermes usa un tool, aparece en los logs:

```
INFO gateway.run: inbound message: platform=telegram msg='Cuántos pod hay en el ns'
INFO tools.mcp_tool: MCP server 'kubernetes' (HTTP): registered 23 tool(s)
INFO gateway.run: response ready: time=191.4s api_calls=2 response=313 chars
```

Si `api_calls=2`, significa que hizo 2 llamadas a LiteLLM:
1. Primera: decidir qué tool usar
2. Segunda: formatear respuesta con el resultado del tool

Si `api_calls=1`, no usó tools (respondió de memoria).

## Deployment

### Deploy completo (build + deploy)

```bash
cd ~/Nextcloud/Repos/infra-ai/infra
make ai  # registry + litellm + hermes build + hermes deploy
```

### Redeploy sin rebuild (cambios de config)

```bash
make ai-hermes-agent
```

### Rollback a versión anterior

```bash
# Ver history
kubectl rollout history deployment hermes-agent-mcp -n ai

# Rollback
kubectl rollout undo deployment hermes-agent-mcp -n ai
```

## Configuración Ansible

### Archivo de variables (defaults/main.yml)

```yaml
hermes_model: "hermes-qwen"                    # Modelo en LiteLLM
hermes_openai_api_base: "http://litellm-proxy.ai.svc.cluster.local:4000/v1"
hermes_openai_api_key: "sk-hermes-internal"   # Multi-tenant key

hermes_telegram_allowed_users: "8492872858"   # Solo este user ID
hermes_node_hostname: "srv-rk1-nvme-01"       # Nodo con más RAM

hermes_mcp_url: "http://127.0.0.1:8080/mcp"
hermes_mcp_timeout: 120
hermes_mcp_connect_timeout: 30

hermes_system_prompt: |
  You are an assistant for Kubernetes cluster inspection.
  When the user asks about Kubernetes pods, namespaces, deployments, or logs,
  you must use the Kubernetes MCP tools.
  Do not use kubectl, oc, or terminal commands for cluster inspection.
  If a Kubernetes MCP tool is available, call it directly.
```

### Archivo de secretos (defaults/secrets.yml - gitignored)

```yaml
hermes_telegram_token: "1234567890:AAAA..."
hermes_discord_token: ""
```

## Versiones

| Componente | Versión | Repo/Chart |
|------------|---------|------------|
| Hermes Agent | 0.7.0 | nousresearch/hermes |
| Kubernetes MCP | v0.0.60 | strowk/kubernetes-mcp-server |
| LiteLLM | 1.58.5 | BerriAI/litellm |
| Model (default) | qwen3-coder:free (480B params) | OpenRouter |

## Referencias

- [Hermes Docs - Custom Providers](https://hermes-agent.nousresearch.com/docs/integrations/providers)
- [MCP Protocol](https://modelcontextprotocol.io/)
- [Kubernetes MCP Server](https://github.com/strowk/kubernetes-mcp-server)
- [LiteLLM Proxy](https://docs.litellm.ai/docs/proxy/quick_start)
