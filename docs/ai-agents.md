# AI Agents en el Cluster

Documentación de los agentes de IA que operan sobre este cluster: qué hacen,
cómo se integran con el stack, y el rol de cada uno.

---

## Arquitectura completa

```
  VOS
   │
   │  hablas en lenguaje natural
   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         OpenCode (laptop)                           │
│                                                                     │
│  Modelo default: litellm/free (qwen3-coder:free, 480B)             │
│    → decide solo cuándo usar cada MCP tool                         │
│    → si rate-limit 429: auto-fallback free → free2 → cheap         │
│                                                                     │
│  MCP servers (tools que el modelo llama automáticamente):           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ kubernetes-mcp      │ list-pods, get-logs, describe, events │   │
│  ├─────────────────────┼───────────────────────────────────────┤   │
│  │ llm-router          │ ask_expert(question) / ask_model(...) │   │
│  ├─────────────────────┼───────────────────────────────────────┤   │
│  │ github              │ PRs, issues, commits                  │   │
│  └─────────────────────┴───────────────────────────────────────┘   │
└──────────┬──────────────────────────────────────────────────────────┘
           │  (alternativa sin TUI)
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  k8s-ask (CLI — laptop)                                             │
│  k8s-ask "qué pods crashean?"   → LiteLLM:4000 → kubectl → stdout  │
│  stdlib only · max 8 iter · tool calls → stderr (dim)              │
└──────────┬──────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────┐   ┌──────────────────────────────────────┐
│  LiteLLM proxy (laptop)  │   │  K3s cluster (ARM64)                 │
│  localhost:4000          │   │                                      │
│  systemctl --user litellm│   │  ┌─────────────────────────────┐    │
│                          │   │  │  Namespace: ai               │    │
│  free  → qwen3-coder:free│   │  │                             │    │
│  free2 → nemotron:free   │   │  │  Hermes Agent               │    │
│  cheap → qwen-turbo      │   │  │  model=free                 │    │
│  claude → Anthropic      │   │  │  OPENAI_API_BASE=           │    │
│                          │   │  │    litellm-proxy:4000       │    │
└──────────┬───────────────┘   │  │         │                   │    │
           │ HTTPS             │  │         ▼                   │    │
           ▼                   │  │  LiteLLM proxy (in-cluster) │    │
     OpenRouter API            │  │  free→free2→cheap           │    │
                               │  │         │                   │    │
                               │  └─────────┼───────────────────┘    │
                               │            │ HTTPS:443              │
                               │            ▼                        │
                               │      OpenRouter API                 │
                               │                                     │
                               │  Cilium · Gateway · ArgoCD          │
                               │  Prometheus · Loki · Tempo · Alloy  │
                               └──────────────────────────────────────┘
```

## Flujo típico — cómo trabaja el modelo principal

```
Vos: "¿por qué crashea el pod argocd-server?"
        │
        ▼
OpenCode (qwen3-coder:free — modelo default)
        │
        ├── [tool] kubernetes-mcp: get-pod argocd-server -n argocd
        │       └── respuesta: OOMKilled, límite 128Mi
        │       (aparece en UI como bloque: "kubernetes_pod_get [...]")
        │
        ├── [tool] kubernetes-mcp: get-events -n argocd
        │       └── respuesta: "killed process" × 3
        │
        ├── [tool] ask_expert("¿cuánta memoria necesita argocd-server
        │                      con 5 repos y ApplicationSets?")
        │       └── analiza → "mínimo 512Mi, recomendado 1Gi"
        │
        └── Edita roles/install-argocd/defaults/main.yml
            Corre ansible-playbook → verifica → commitea
```

---

## Hermes Agent — Agente in-cluster (ARM64)

**Rol:** agente de IA desplegado dentro del cluster K3s. Self-improving — puede
usar skills, memoria persistente y herramientas para operar de forma autónoma.
Accesible vía web UI en `https://hermes.cluster.home`.

### Arquitectura in-cluster

```
Hermes Agent (namespace: ai)
  │  model=free  OPENAI_API_BASE=http://litellm-proxy.ai.svc:4000
  ▼
LiteLLM proxy (in-cluster, namespace: ai)
  │  fallback: free → free2 → cheap
  │  OPENROUTER_API_KEY en litellm-secrets
  ▼
OpenRouter API (externo)
```

### Build ARM64 (kaniko in-cluster)

El docker oficial de Hermes es **amd64-only**. Se construye un custom ARM64 image:

```bash
make ai-registry       # deploy registry:2 (almacena el resultado)
make ai-hermes-build   # kaniko clona el repo y buildealo (~60 min en CM4)
make ai-hermes-deploy  # despliega litellm-proxy + hermes-agent
```

**Gotchas del build:**
- `--snapshot-mode=redo` — menos memoria que el default (evita OOM en CM4)
- Node affinity: solo se ejecuta en el nodo `control-plane` (más disco)
- `backoffLimit: 3` — puede fallar si el nodo tiene poca RAM disponible
- Dockerfile clona el repo con `RUN git clone` (no usa git context de kaniko)

### Validation workflow

When a change might take a while to prove, validate it in this order:
1. Manual or Helm proof of the desired state
2. Background Ansible run with logs redirected
3. Inspect the background log and fix issues
4. Run the same Ansible command in the foreground only after the background
   run proves the change

### Persistencia

```
PVC: hermes-data (1Gi, local-path)
  mountPath: /opt/data  (skills, memory, config)
  HERMES_HOME=/opt/data
```

The gateway platform config is mounted from the `hermes-gateway-config`
ConfigMap as `/opt/data/gateway.json`. That keeps the webhook platform enabled
so the pod remains `1/1 Running` instead of exiting after startup.

### Acceso

```bash
# Web UI (requiere ingress stack)
https://hermes.cluster.home

# Sin ingress (port-forward)
kubectl port-forward -n ai svc/hermes-agent 8080:8080
# → http://localhost:8080

# CLI directo
kubectl exec -it -n ai deployment/hermes-agent -- hermes

# Debug
kubectl logs -n ai -l app=hermes-agent --tail=50
kubectl get pods -n ai
```

### Secrets

```bash
# Crear roles/install-hermes-agent/defaults/secrets.yml (gitignored):
hermes_openrouter_api_key: "sk-or-v1-..."
hermes_telegram_token: ""   # opcional
hermes_discord_token: ""    # opcional
```
LiteLLM proxy carga automáticamente el mismo archivo.

---

## División de tareas entre agentes

| Tarea | Agente | Modelo | Herramienta |
|---|---|---|---|
| Escribir/editar Ansible role | OpenCode / Claude Code | sonnet | — skills como contexto |
| Debuggear pod crashlooping | Claude Code | sonnet | kubernetes-mcp → live state |
| Query rápida sin abrir TUI | **k8s-ask** | cheap (default) | kubectl directo |
| "¿Qué sale mal en esta alerta?" | HolmesGPT | claude/ollama | kubectl nativo |
| Formatear YAML, rename simple | Claude Code | haiku (barato) | — |
| Analizar logs con info privada | HolmesGPT / subagent | ollama local | Loki MCP |
| Review PR / crear issue | Claude Code | sonnet | github MCP |
| Tarea autónoma in-cluster 24/7 | **Hermes Agent** | free (LiteLLM) | skills + tools |

---

## OpenCode — Agente de infraestructura

**Rol:** operar el cluster desde el código. No accede al cluster directamente
en producción — trabaja sobre los source files de Ansible en `~/projects/infra`
y ejecuta los playbooks para aplicar cambios.

### Cómo opera

```
OpenCode
  │
  ├── Lee/edita roles Ansible  (~/projects/infra/roles/)
  ├── Corre playbooks          (ansible-playbook playbooks/bootstrap.yml)
  ├── Verifica con kubectl     (kubectl get pods, curl, dig, arp)
  └── Commitea solo cuando     (playbook passed + failed=0)
      el deploy es exitoso
```

### Regla de oro

**Nunca editar archivos deployados directamente** (`~/.kube`, `/etc/rancher/`, etc.).
Siempre editar el source en `~/projects/infra/roles/<role>/` y correr Ansible.
Si algo requiere un paso manual después del playbook, es un bug.

### Skills como memoria de largo plazo

OpenCode usa skills (archivos Markdown en `~/.config/opencode/skills/`) como
contexto persistente entre sesiones. Los skills documentan:
- Decisiones de arquitectura y por qué se tomaron
- Gotchas conocidos (ej: Pi-hole 6 `local-service` crash, MetalLB + Cilium incompatibilidad)
- Comandos de verificación y troubleshooting probados
- Estado actual del cluster

Los skills del cluster:

| Skill | Contenido |
|-------|-----------|
| `cilium` | CNI, LB-IPAM, L2 Announcements, Gateway API, BPF |
| `gateway` | Shared Gateway, HTTPRoutes, flujos de tráfico reales |
| `cert-manager` | CA interna, wildcard cert, Keychain trust |
| `k3s` | Flags del servidor, kubeconfig, upgrades |
| `pihole` | DNS wildcard, Pi-hole 6 gotchas, integración |
| `argocd` | GitOps, ApplicationSets, sync waves |
| `monitoring` | Prometheus, Grafana, Tempo, Alloy — stack completo de observabilidad |
| `metallb` | OBSOLETO — reemplazado por Cilium LB-IPAM |
| `k8s-debug` | Debug sistemático de pods, red, nodos |
| `platform-engineering` | Helm, Terraform, CI/CD best practices |
| `cifs-nas` | CSI SMB driver, SMB1 mount options, PV/PVC static + dynamic tests |

**Source de los skills:** `~/dotfiles/ansible/roles/opencode/files/skills/`
Deployados via Ansible al editar, nunca a mano.

### Storage workflow

Para el NAS SMB1, OpenCode usa el rol `install-cifs-nas` con dos rutas opcionales:
- `cifs_enable_static=true` para PV/PVC estáticos con `storageClassName: ""`
- `cifs_enable_dynamic_test=true` para `StorageClass` + PVC + pod de prueba

El target de ejecución es `make storage`, que llama `ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags storage`.

---

## LiteLLM — Router de modelos local

**Rol:** proxy local (puerto 4000) con endpoint OpenAI-compatible. Enruta todos los
modelos hacia OpenRouter. OpenCode apunta a `http://localhost:4000` como provider.

**Instalado como servicio:** `systemctl --user start/stop/restart litellm`

### Modelos configurados y fallback chain

```yaml
# ~/.config/litellm/config.yaml  (source: dotfiles/ansible/roles/opencode/files/litellm-config.yaml)
model_list:
  - model_name: free      # qwen/qwen3-coder:free — 480B, $0, tool use ✅
  - model_name: free2     # nvidia/nemotron-3-super-120b-a12b:free — backup, NVIDIA provider
  - model_name: cheap     # qwen/qwen-turbo — $0.033/M, tool use ✅
  - model_name: claude-sonnet-4-6   # anthropic/claude-sonnet-4-6 via OpenRouter
  - model_name: claude-haiku-4-5    # anthropic/claude-haiku-4-5 via OpenRouter

litellm_settings:
  fallbacks: [{"free": ["free2", "cheap"]}, {"free2": ["cheap"]}]
  # free y free2 son Venice-hosted → pueden tener 429 simultáneo
  # cada modelo en la cadena necesita su propio entry de fallback
```

### Gotchas aprendidos en producción

- **Venice rate limits**: `qwen3-coder:free` y `llama-3.3-70b:free` comparten provider
  Venice en OpenRouter → 429 simultáneo. Usar `nemotron:free` (NVIDIA) como `free2`.
- **Bug de fallback chain**: si `free2` no tiene su propio entry en `fallbacks[]`,
  LiteLLM tira `"No fallback model group found"` en vez de seguir hacia `cheap`.
- **Tool use en :free**: modelos `:free` pueden listar `tools` en su metadata pero
  fallar en runtime. Verificar: `journalctl --user -u litellm -f`

### Comandos operativos

```bash
systemctl --user status litellm          # estado del servicio
systemctl --user restart litellm         # aplicar cambios de config
journalctl --user -u litellm -f          # logs en tiempo real (ver routing y fallbacks)
curl http://localhost:4000/models        # modelos disponibles
```

**Source config:** `~/dotfiles/ansible/roles/opencode/files/litellm-config.yaml`
**API key:** `~/.config/litellm/litellm.env` (no sobrescrito por Ansible, contiene `OPENROUTER_API_KEY`)

---

## Kubernetes MCP — Estado live del cluster

**Rol:** MCP server que expone el estado live del cluster como tools que el modelo
llama automáticamente cuando la pregunta lo requiere — sin que el usuario apruebe
cada `kubectl`.

**Confirmado funcionando:** pregunta "get events en kube-system" → modelo llama
`kubernetes_events_list [namespaces=kube-system]` → responde con los eventos reales.

**Cómo aparece en OpenCode:** bloque colapsable antes de la respuesta en texto:
```
kubernetes_events_list [namespaces=kube-system]
```

**Si el modelo responde de memoria sin llamar el tool:** reformular la pregunta
explícitamente: *"consultá el cluster y decime..."*

**Configurado en:** `~/dotfiles/ansible/roles/opencode/files/opencode.jsonc`

```jsonc
"kubernetes": {
  "type": "local",
  "command": ["npx", "-y", "kubernetes-mcp-server@latest"],
  "enabled": true
}
```

---

## HolmesGPT — Agente SRE

**Proyecto:** CNCF Sandbox — [github.com/HolmesGPT/holmesgpt](https://github.com/HolmesGPT/holmesgpt)

**Rol:** investigar incidentes y encontrar root causes en el cluster de forma
autónoma. Mientras OpenCode opera sobre el código, HolmesGPT opera sobre el
estado en vivo del cluster.

### Qué hace

HolmesGPT corre un **agentic loop**: recibe una pregunta o alerta, llama a
herramientas (kubectl, logs, Prometheus, etc.) iterativamente hasta tener
suficiente contexto, y produce un análisis en lenguaje natural con el root cause.

```
Pregunta / alerta
      │
      ▼
  HolmesGPT
      │
      ├── kubectl describe pod / get events
      ├── kubectl logs (últimas N líneas)
      ├── Prometheus metrics (si está configurado)
      ├── Helm history / values
      └── ... itera hasta tener root cause
      │
      ▼
  Respuesta: "El pod crashea porque X, el fix es Y"
```

### Casos de uso en este cluster

```bash
# ¿Por qué crashea este pod?
holmes ask "why is pod X in namespace Y crashlooping?"

# ¿Qué está mal con este deployment?
holmes ask "why is deployment argocd-server not ready?"

# Investigar una alerta de Prometheus
holmes investigate alertmanager

# Modo interactivo — preguntas y seguimiento
holmes chat
```

### Integración con el stack actual

| Componente | ¿HolmesGPT puede usarlo? | Notas |
|---|---|---|
| K8s API (pods, events, logs) | ✅ nativo | toolset `kubernetes` built-in |
| Helm | ✅ nativo | toolset `helm` built-in |
| ArgoCD | ✅ built-in | toolset `argocd` — estado de apps, sync history |
| Cilium | ✅ built-in | toolset listado en docs oficiales |
| Prometheus / AlertManager | ✅ built-in | si se despliega Prometheus stack |
| Hubble (Cilium) | via kubectl | no toolset nativo, pero puede correr `hubble observe` |
| Grafana Loki | ✅ built-in | si se despliega Loki |

### Modos de deploy

**CLI (más simple para homelab):**
```bash
pip install holmesgpt
export OPENAI_API_KEY=sk-...   # o cualquier otro provider
holmes ask "why is my pod failing?"
```

**In-cluster (Operator mode — 24/7):**
```yaml
# Helm chart — puede ir como ArgoCD Application
helm install holmesgpt holmesgpt/holmesgpt \
  --set llm.provider=openai \
  --set llm.apiKey=sk-...
```

El Operator mode corre health checks scheduled y manda alertas a Slack con
el análisis ya hecho — sin que tengas que notar el problema primero.

### LLM providers soportados

OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, Google Gemini, Ollama (local),
y cualquier endpoint compatible con OpenAI API.

Para homelab: Ollama con un modelo local evita costos y mantiene los datos privados.

```yaml
# config.yaml con Ollama local
llm:
  provider: ollama
  model: llama3.1
  base_url: http://ollama.cluster.home  # si Ollama está en el cluster
```

### Privacidad / seguridad

HolmesGPT tiene **acceso de solo lectura** al cluster por diseño y respeta los
permisos RBAC configurados. No aplica cambios — solo lee y analiza.

---

## Diferencia clave entre los dos agentes

```
┌─────────────────┬────────────────────────────┬──────────────────────────────┐
│                 │        OpenCode             │        HolmesGPT             │
├─────────────────┼────────────────────────────┼──────────────────────────────┤
│ Qué opera       │ Código fuente (Ansible)     │ Estado en vivo del cluster   │
│ Cuándo se usa   │ Para cambiar la infra       │ Para entender qué está roto  │
│ Output          │ Playbooks, commits, PRs     │ Análisis de root cause       │
│ Acceso cluster  │ Indirecto (via Ansible)     │ Directo (kubectl read-only)  │
│ Persistencia    │ Git + skills Markdown       │ Stateless (cada query fresh) │
│ Modo operación  │ Interactivo (vos lo pedís)  │ Interactivo o 24/7 Operator  │
└─────────────────┴────────────────────────────┴──────────────────────────────┘
```

**Flujo típico combinado:**

```
1. HolmesGPT detecta / diagnostica el problema
        "El pod argocd-server crashea por OOMKilled, límite de memoria muy bajo"

2. OpenCode aplica el fix en Ansible
        Edita roles/install-argocd/defaults/main.yml → aumenta resources.limits.memory
        Corre playbook → verifica → commitea
```

---

## Próximos pasos / ideas

- [x] Desplegar kube-prometheus-stack (Prometheus + Grafana + AlertManager) — ✅ hecho
- [x] Desplegar Grafana Tempo (tracing backend) — ✅ hecho
- [x] Desplegar Grafana Alloy (OTLP pipeline) — ✅ hecho
- [x] Desplegar kube-prometheus-stack (Prometheus + Grafana + AlertManager) — ✅
- [x] Desplegar Grafana Tempo (tracing backend) — ✅
- [x] Desplegar Grafana Alloy (OTLP pipeline) — ✅
- [x] LiteLLM como systemd user service con fallback chain — ✅ free → free2 → cheap
- [x] Kubernetes MCP server — ✅ confirmado: llama tools automáticamente
- [x] llm-router MCP (ask_expert / ask_model) — ✅ FastMCP, stdio transport
- [x] Skill scripts (`diagnose.sh`, `health-check.sh`, `connectivity-test.sh`) — ✅
- [x] Modelo free con tool use (qwen3-coder:free) + fallback a nemotron (NVIDIA) — ✅
- [x] k8s-ask CLI — ✅ lenguaje natural → LiteLLM → kubectl tools → stdout
- [x] Hermes Agent in-cluster — ✅ ARM64 kaniko build + LiteLLM proxy + hermes-agent
- [ ] Desplegar HolmesGPT como ArgoCD Application (in-cluster, Operator mode)
- [ ] Conectar HolmesGPT con Prometheus + Alertmanager — prerequisito cumplido ✅
- [ ] Probar Ollama in-cluster como LLM backend local (sin costos de API, datos privados)
- [ ] Conectar HolmesGPT Slack integration para alertas con análisis automático
- [ ] Explorar toolset de Cilium en HolmesGPT para diagnóstico de red
- [ ] ArgoCD app-of-apps / GitOps handover (`handover.yml`)

---

## Referencias

- HolmesGPT docs: https://holmesgpt.dev/
- HolmesGPT GitHub: https://github.com/HolmesGPT/holmesgpt
- HolmesGPT Helm chart: https://holmesgpt.dev/installation/in-cluster-installation/
- HolmesGPT Operator mode: https://holmesgpt.dev/operator/
- OpenCode docs: https://opencode.ai/docs
