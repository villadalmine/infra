# Hermes Agent - Issue Resolution 2026-04-08

## Problema Inicial

Hermes Agent estaba roto después de que otra IA hizo cambios manuales en Kubernetes.

### Síntomas

1. **Error 401**: "Missing Authentication header" cuando intentaba hacer llamadas LLM
2. **Arquitectura violada**: Otra IA había configurado acceso directo a OpenRouter (bypass de LiteLLM)
3. **Patches manuales**: Cambios aplicados con `kubectl` que Ansible luego pisó
4. **MCP desconfigurado**: Configuración de MCP servers perdida

### Contexto del Desastre

La otra IA:
- Aplicó `kubectl patch` manualmente al Deployment
- Cambió variables de entorno para apuntar directo a OpenRouter
- Configuró `OPENROUTER_API_KEY` directamente en el pod
- **Violó el principio de seguridad**: ningún agente debe tener acceso directo a APIs externas

Cuando corrimos Ansible después, los patches manuales se perdieron y Hermes quedó en estado inconsistente.

## Diagnóstico

### Estado al inicio

```bash
kubectl get pods -n ai
# hermes-agent-mcp-5767ffdcfb-fswm4   2/2 Running

kubectl logs -n ai hermes-agent-mcp-5767ffdcfb-fswm4 -c hermes-agent
# (sin output - logs internos)

kubectl exec -n ai hermes-agent-mcp-5767ffdcfb-fswm4 -c hermes-agent -- cat /opt/data/logs/gateway.log
# 2026-04-08 ... INFO gateway.run: inbound message: msg='Hola'
# 2026-04-08 ... ERROR: 401 - Missing Authentication header
```

### Investigación de archivos

```bash
# Ver config.yaml actual
kubectl exec -n ai <pod> -c hermes-agent -- cat /opt/data/config.yaml
# provider: custom
# base_url: http://litellm-proxy...
# api_key_env: OPENAI_API_KEY
# (FALTA: sección mcp_servers)

# Ver .env
kubectl exec -n ai <pod> -c hermes-agent -- cat /opt/data/.env
# OPENAI_API_KEY=sk-hermes-internal
# OPENAI_API_BASE=http://litellm-proxy...

# Ver archivos montados
kubectl exec -n ai <pod> -c hermes-agent -- ls -la /opt/data/
# .env existe (100 bytes)
# config.yaml existe (615 bytes)
# gateway.json existe
```

### Test de LiteLLM

```bash
# Probar LiteLLM desde otro pod
kubectl run curl-test --image=curlimages/curl --rm -it -- sh
curl -X POST http://litellm-proxy.ai.svc.cluster.local:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-hermes-internal" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-qwen","messages":[{"role":"user","content":"test"}]}'
# 200 OK - LiteLLM funcionando
```

### Comparación con versión que funcionaba

```bash
# Commit anterior funcionando (hace 8 horas)
git show 5d25562:roles/install-hermes-agent/defaults/main.yml
# hermes_model: "openai/gpt-4o-mini"
# hermes_inference_provider: "openrouter"  # ← ACCESO DIRECTO (problema de seguridad)

git show 5d25562:roles/install-hermes-agent/templates/hermes-static-mcp.yaml.j2
# HERMES_SYSTEM_PROMPT como env var (no config.yaml)
# HERMES_MODEL como env var
# HERMES_INFERENCE_PROVIDER como env var
# config.yaml NO existía (todo por env vars)
```

**Descubrimiento clave**: Antes funcionaba pero con arquitectura INCORRECTA (acceso directo a OpenRouter).

## Root Cause Analysis

### Problema 1: Arquitectura inconsistente

**Antes**: Variables de entorno → Hermes usa OpenRouter directo
**Después**: config.yaml → Hermes usa LiteLLM proxy (correcto)

**Pero**: La migración de env vars a config.yaml estaba incompleta:
- ✅ Sección `model` con `provider: custom`
- ✅ API key en `.env`
- ❌ Sección `mcp_servers` faltante en config.yaml
- ❌ `agent.tool_use_enforcement` no estaba configurado

### Problema 2: Provider "custom" y autenticación

Hermes 0.7.0 con `provider: custom` requiere:
1. `api_key_env` apuntando a variable de entorno
2. Archivo `.env` con esa variable
3. `base_url` del endpoint OpenAI-compatible

**Pero**: Hermes no estaba enviando el header `Authorization` correctamente en algunas versiones/configs.

**Solución**: La combinación de `.env` file + `api_key_env` + initContainer funcionó.

### Problema 3: MCP configuration missing

El config.yaml no tenía:
```yaml
mcp_servers:
  kubernetes:
    url: http://127.0.0.1:8080/mcp
    timeout: 120
    connect_timeout: 30
```

Sin esto, aunque el sidecar estuviera corriendo, Hermes no sabía cómo conectarse.

## Solución Implementada

### 1. Crear ConfigMap con config.yaml completo

**Archivo**: `roles/install-hermes-agent/templates/hermes-config-configmap.yaml.j2`

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: hermes-config
  namespace: {{ hermes_namespace }}
data:
  config.yaml: |
    model:
      provider: custom
      default: {{ hermes_model }}
      base_url: {{ hermes_openai_api_base }}
      api_key_env: OPENAI_API_KEY

    agent:
      tool_use_enforcement: true
      system_prompt: |
{{ hermes_system_prompt | indent(8, first=True) }}

    mcp_servers:
      kubernetes:
        url: {{ hermes_mcp_url }}
        timeout: {{ hermes_mcp_timeout }}
        connect_timeout: {{ hermes_mcp_connect_timeout }}
```

### 2. InitContainer para crear .env

**Problema**: `readOnlyRootFilesystem: true` impide crear archivos en runtime.

**Solución**: InitContainer sin esa restricción crea el `.env` antes de que arranque el contenedor principal.

```yaml
initContainers:
  - name: create-env-file
    image: busybox:latest
    command: ["sh", "-c"]
    args:
      - |
        cat <<EOF > /opt/data/.env
        OPENAI_API_KEY={{ hermes_openai_api_key }}
        OPENAI_API_BASE={{ hermes_openai_api_base }}
        EOF
        chmod 644 /opt/data/.env
    volumeMounts:
      - name: hermes-data
        mountPath: /opt/data
```

### 3. Mount de config.yaml en el path correcto

**Antes**: `/opt/data/.hermes/config.yaml` (no funcionaba)
**Después**: `/opt/data/config.yaml` (Hermes lo lee automáticamente)

```yaml
volumeMounts:
  - name: hermes-config
    mountPath: /opt/data/config.yaml
    subPath: config.yaml
```

### 4. Secret con OPENAI_API_KEY

```yaml
- name: Create Hermes Secret
  kubernetes.core.k8s:
    state: present
    definition:
      apiVersion: v1
      kind: Secret
      metadata:
        name: hermes-secrets
        namespace: "{{ hermes_namespace }}"
      stringData:
        TELEGRAM_BOT_TOKEN: "{{ hermes_telegram_token }}"
        OPENAI_API_KEY: "{{ hermes_openai_api_key }}"
```

### 5. Defaults actualizados

**Archivo**: `roles/install-hermes-agent/defaults/main.yml`

```yaml
# Forzar LiteLLM proxy
hermes_model: "hermes-qwen"
hermes_inference_provider: "openai"  # provider type para Hermes (no usado con config.yaml)
hermes_openai_api_base: "http://litellm-proxy.ai.svc.cluster.local:4000/v1"
hermes_openai_api_key: "sk-hermes-internal"

# MCP configuration
hermes_mcp_url: "http://127.0.0.1:8080/mcp"
hermes_mcp_timeout: 120
hermes_mcp_connect_timeout: 30

# System prompt para forzar uso de MCP tools
hermes_system_prompt: |
  You are an assistant for Kubernetes cluster inspection.
  When the user asks about Kubernetes pods, namespaces, deployments, or logs,
  you must use the Kubernetes MCP tools.
  Do not use kubectl, oc, or terminal commands for cluster inspection.
  If a Kubernetes MCP tool is available, call it directly.
```

### 6. Tasks actualizadas

**Archivo**: `roles/install-hermes-agent/tasks/main.yml`

```yaml
# Aplicar ConfigMaps ANTES del Deployment
- name: Apply Hermes gateway ConfigMap
  kubernetes.core.k8s:
    state: present
    template: hermes-gateway-configmap.yaml.j2

- name: Apply Hermes config ConfigMap
  kubernetes.core.k8s:
    state: present
    template: hermes-config-configmap.yaml.j2

# Luego el Deployment
- name: Apply Hermes MCP deployment
  kubernetes.core.k8s:
    state: present
    src: "{{ hermes_manifest_path }}"
```

## Verificación

### Deploy

```bash
cd ~/Nextcloud/Repos/infra-ai/infra
ansible-playbook playbooks/bootstrap.yml --tags ai-hermes-agent
# PLAY RECAP: localhost: ok=X changed=Y failed=0
```

### Estado del pod

```bash
kubectl get pods -n ai -l app=hermes-agent-mcp
# hermes-agent-mcp-c799f445-vl4zx   2/2 Running

kubectl exec -n ai hermes-agent-mcp-c799f445-vl4zx -c hermes-agent -- cat /opt/data/logs/gateway.log | tail -20
# INFO gateway.run: Starting Hermes Gateway...
# INFO gateway.platforms.telegram: Connected to Telegram (polling mode)
# INFO tools.mcp_tool: MCP server 'kubernetes' (HTTP): registered 23 tool(s)
# INFO gateway.run: Gateway running with 1 platform(s)
```

### Prueba funcional (Telegram)

**Input**: "cuántos pods hay en el namespace ai?"

**Expected flow**:
1. Hermes recibe mensaje
2. Conecta a LiteLLM con `sk-hermes-internal`
3. LiteLLM enruta a OpenRouter (qwen3-coder:free)
4. Modelo decide usar `mcp_kubernetes_pods_list_in_namespace`
5. Hermes llama al MCP server (127.0.0.1:8080/mcp)
6. MCP devuelve lista de 6 pods
7. Modelo formatea respuesta
8. Hermes envía a Telegram

**Logs observados**:

```
2026-04-09 00:25:01 INFO gateway.run: inbound message: msg='Cuántos pod hay en el ns'
2026-04-09 00:25:01 INFO agent.model_metadata: Could not detect context length for model 'hermes-qwen'
2026-04-09 00:28:13 INFO gateway.run: response ready: time=191.4s api_calls=2 response=313 chars
2026-04-09 00:28:13 INFO gateway.platforms.base: [Telegram] Sending response (313 chars)
```

**Respuesta recibida en Telegram**:

```
⚙️ mcp_kubernetes_pods_list_in_namespace...

En el namespace ai hay 6 pods en total.

Los pods son:
1. hermes-agent-mcp-c799f445-vl4zx (2/2 Running)
2. holmes-ui-587f6bdc57-msq7f (1/1 Running)
3. holmesgpt-holmes-6747bc46bd-l9stp (1/1 Running)
4. litellm-proxy-c66774c9d-hkb8z (2/2 Running)
5. litellm-test (1/1 Running)
6. wget-test (0/1 Completed)
```

✅ **ÉXITO**: MCP tools funcionando, respuesta precisa.

### Verificación de arquitectura de seguridad

```bash
# Verificar que Hermes NO tenga OPENROUTER_API_KEY
kubectl get secret hermes-secrets -n ai -o yaml | grep OPENROUTER
# (vacío - correcto)

# Verificar que use LiteLLM
kubectl exec -n ai hermes-agent-mcp-c799f445-vl4zx -c hermes-agent -- cat /opt/data/config.yaml | grep base_url
# base_url: http://litellm-proxy.ai.svc.cluster.local:4000/v1

# Verificar multi-tenancy key
kubectl exec -n ai hermes-agent-mcp-c799f445-vl4zx -c hermes-agent -- cat /opt/data/.env
# OPENAI_API_KEY=sk-hermes-internal

# Verificar que LiteLLM tenga la key de OpenRouter (no Hermes)
kubectl get secret litellm-secrets -n ai -o jsonpath='{.data.OPENROUTER_API_KEY}' | base64 -d
# sk-or-v1-... (solo LiteLLM la tiene)
```

✅ **Arquitectura de seguridad respetada**: Hermes → LiteLLM → OpenRouter

## Lecciones Aprendidas

### 1. NUNCA hacer cambios manuales en producción

```bash
# ❌ MAL
kubectl patch deployment hermes-agent-mcp -n ai -p '...'
kubectl set env deployment/hermes-agent-mcp -n ai OPENROUTER_API_KEY=...

# ✅ BIEN
vim roles/install-hermes-agent/defaults/main.yml
ansible-playbook playbooks/bootstrap.yml --tags ai-hermes-agent
git commit && git push
```

**Por qué**: Ansible es la fuente de verdad. Cualquier cambio manual se pierde en el próximo deploy.

### 2. Architecture first, workarounds second

La otra IA hizo que funcionara **violando el principio de seguridad** (acceso directo a OpenRouter).

Mejor: tomar más tiempo y arreglarlo correctamente (via LiteLLM proxy).

### 3. Hermes 0.7.0 requiere config.yaml

Las versiones anteriores de Hermes se configuraban 100% via env vars.
Hermes 0.7.0 prefiere `config.yaml` para configuración compleja (MCP servers, provider custom, etc).

**Migración correcta**:
- ✅ Variables simples: env vars (`HERMES_MODEL`, `HERMES_TEMPERATURE`)
- ✅ Configuración compleja: `config.yaml` (provider custom, MCP servers, system prompt)
- ✅ Secretos: `.env` file (creado por initContainer o Secret mount)

### 4. InitContainer pattern para readOnlyRootFilesystem

Cuando `securityContext.readOnlyRootFilesystem: true`, no se puede crear archivos en runtime.

**Pattern**:
1. InitContainer sin esa restricción
2. Crea archivos necesarios en volumen compartido (emptyDir)
3. Contenedor principal los lee

### 5. MCP configuration es crítica

Aunque el sidecar esté corriendo, si `config.yaml` no tiene la sección `mcp_servers`,
Hermes no sabe cómo conectarse.

**Mandatory config**:
```yaml
mcp_servers:
  kubernetes:
    url: http://127.0.0.1:8080/mcp
    timeout: 120
    connect_timeout: 30
```

### 6. Performance de modelos gratis

`qwen3-coder:free` (OpenRouter) es **lento** (191 segundos para primera consulta con tool).

**Trade-offs**:
- Free tier: $0 pero 2-3 minutos de latencia
- Cheap tier (`qwen-turbo`): $0.033/M tokens, 10-30 segundos
- Strong tier (`deepseek-chat-v3`): barato, 5-15 segundos

**Para producción**: usar `cheap` o `strong`.

### 7. Multi-tenancy en LiteLLM es la clave

Cada agente tiene su propia API key:
- Hermes: `sk-hermes-internal`
- Holmes: `sk-holmes-internal`
- kagent: `sk-kagent-internal`

Esto permite:
- Rate limiting por agente
- Cost tracking por agente
- Revocación individual sin afectar otros

### 8. Logs internos > kubectl logs

`kubectl logs` de Hermes está vacío (por diseño).

Logs reales en `/opt/data/logs/`:
- `gateway.log` - flujo completo de mensajes
- `agent.log` - reasoning y tool calls
- `errors.log` - stack traces

**Always check**:
```bash
kubectl exec -n ai <pod> -c hermes-agent -- cat /opt/data/logs/gateway.log | tail -100
```

## Files Changed

```
roles/install-hermes-agent/
├── defaults/main.yml                            # LiteLLM config, MCP URLs
├── tasks/main.yml                               # Apply ConfigMaps before Deployment
├── templates/
│   ├── hermes-config-configmap.yaml.j2         # NEW: config.yaml with MCP
│   ├── hermes-gateway-configmap.yaml.j2        # Telegram settings
│   └── hermes-static-mcp.yaml.j2               # InitContainer for .env + mounts
```

## Commit

```
commit 988dcf1
Author: OpenCode
Date:   Wed Apr 9 00:30:00 2026

fix(hermes): route all LLM traffic through LiteLLM proxy with MCP support

- Add hermes-config ConfigMap with config.yaml
- Add initContainer to create .env file
- Mount config.yaml at /opt/data/config.yaml
- Configure LiteLLM endpoint with multi-tenant key
- Enforce security architecture: Hermes → LiteLLM → OpenRouter
- Verified MCP tools working (23 Kubernetes operations)
```

## Referencias

- [Hermes 0.7.0 Release Notes](https://github.com/nousresearch/hermes/releases/tag/v0.7.0)
- [Hermes Custom Providers](https://hermes-agent.nousresearch.com/docs/integrations/providers)
- [MCP Protocol Spec](https://modelcontextprotocol.io/)
- [Kubernetes MCP Server](https://github.com/strowk/kubernetes-mcp-server)
- [LiteLLM Multi-Tenancy](https://docs.litellm.ai/docs/proxy/virtual_keys)

## Estado Final

✅ Hermes Agent 0.7.0 funcionando con:
- LiteLLM proxy (security enforced)
- 23 Kubernetes MCP tools
- Telegram polling mode
- Read-only RBAC
- Multi-tenant API key
- Model: qwen3-coder:free (480B params)

**Next steps**:
- Considerar upgrade a modelo más rápido (`cheap` tier)
- Agregar más MCP servers (GitHub, ArgoCD, Monitoring)
- Implementar confirmation flow para operations destructivas
