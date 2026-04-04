# AI Agents en el Cluster

Documentación de los agentes de IA que operan sobre este cluster: qué hacen,
cómo se integran con el stack, y el rol de cada uno.

---

## Mapa de agentes

```
┌──────────────────────────────────────────────────────────────────┐
│                           LAPTOP                                 │
│                                                                  │
│  LiteLLM (local proxy :4000)                                     │
│  ├── claude-sonnet-4-6   → Anthropic  (razonamiento / planif.)  │
│  ├── claude-haiku-4-5    → Anthropic  (tareas rápidas / baratas) │
│  └── ollama/llama3.1     → in-cluster (datos privados / logs)    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │       OpenCode / Claude Code                                │ │
│  │  model: via LiteLLM (elige modelo por tarea)               │ │
│  │                                                             │ │
│  │  MCP servers:                                               │ │
│  │  ├── context7 (docs)                                        │ │
│  │  ├── github (PRs/issues)                                    │ │
│  │  └── kubernetes-mcp (live cluster state)                    │ │
│  │                                                             │ │
│  │  Skills con scripts/:                                       │ │
│  │  ├── cilium/scripts/connectivity-test.sh                    │ │
│  │  ├── k8s-debug/scripts/diagnose.sh                          │ │
│  │  └── monitoring/scripts/health-check.sh                     │ │
│  └──────────────┬──────────────────────────────────────────────┘ │
│                 │ Ansible SSH / kubectl                           │
└─────────────────┼────────────────────────────────────────────────┘
                  │                         │ kubectl read-only
                  ▼                         ▼
┌──────────────────────────────────────────────────────────────────┐
│               K3s cluster (srv-rk1-01 + srv-super6-cm4-emmc-01)  │
│                                                                  │
│   Cilium · cert-manager · Gateway · Pi-hole · ArgoCD            │
│   Prometheus · Grafana · Tempo · Loki · Alloy                   │
│                                                                  │
│   HolmesGPT (Operator mode) ← PENDIENTE                         │
│   Ollama (in-cluster)        ← FUTURO                           │
└──────────────────────────────────────────────────────────────────┘
```

---

## División de tareas entre agentes

| Tarea | Agente | Modelo | Herramienta |
|---|---|---|---|
| Escribir/editar Ansible role | OpenCode / Claude Code | sonnet | — skills como contexto |
| Debuggear pod crashlooping | Claude Code | sonnet | kubernetes-mcp → live state |
| "¿Qué sale mal en esta alerta?" | HolmesGPT | claude/ollama | kubectl nativo |
| Formatear YAML, rename simple | Claude Code | haiku (barato) | — |
| Analizar logs con info privada | HolmesGPT / subagent | ollama local | Loki MCP |
| Review PR / crear issue | Claude Code | sonnet | github MCP |
| Health check completo | skill script | — | scripts/health-check.sh |

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

**Source de los skills:** `~/dotfiles/ansible/roles/opencode/files/skills/`
Deployados via Ansible al editar, nunca a mano.

---

## LiteLLM — Router de modelos local

**Rol:** proxy local que expone un único endpoint OpenAI-compatible y enruta a múltiples
providers/modelos. OpenCode y Claude Code apuntan a `http://localhost:4000` en lugar
de directo a Anthropic.

**Beneficio principal:** puedes pedir a Claude Code que use `haiku` para sub-tareas baratas
(renombrar, formatear YAML) sin cambiar la configuración global.

```yaml
# ~/.config/litellm/config.yaml (ejemplo)
model_list:
  - model_name: claude-sonnet-4-6
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY
  - model_name: claude-haiku-4-5
    litellm_params:
      model: anthropic/claude-haiku-4-5-20251001
      api_key: os.environ/ANTHROPIC_API_KEY
  - model_name: ollama/llama3.1
    litellm_params:
      model: ollama/llama3.1
      api_base: http://ollama.cluster.home
```

```bash
# Iniciar LiteLLM
litellm --config ~/.config/litellm/config.yaml

# Verificar modelos disponibles
curl http://localhost:4000/models
```

**Configurado en:** `~/dotfiles/ansible/roles/opencode/files/opencode.jsonc`

---

## Kubernetes MCP — Estado live del cluster

**Rol:** MCP server que da a Claude Code acceso directo al estado del cluster
(pods, eventos, logs) sin necesidad de aprobar cada comando `kubectl`.

```bash
# Explorar herramientas disponibles antes de usar
npx mcporter list
npx mcporter call kubernetes list-pods -- --namespace monitoring
```

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
- [x] LiteLLM como router local de modelos — ✅ configurado en opencode.jsonc
- [x] Kubernetes MCP server — ✅ configurado en opencode.jsonc
- [x] Skill scripts (`diagnose.sh`, `health-check.sh`, `connectivity-test.sh`) — ✅ creados
- [ ] Instalar y arrancar LiteLLM localmente (`pip install litellm`, config YAML)
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
