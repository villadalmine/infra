---
name: openclaw
description: >
  OpenClaw personal AI gateway — orquestador principal que habla con Holmes, Kagent y Hermes
  como subagentes. Todo el tráfico LLM pasa por LiteLLM (nunca directo a OpenRouter).
  RBAC readonly sin acceso a Secrets. kubernetes-mcp sidecar para visibilidad del cluster.
license: MIT
compatibility:
  - opencode
  - claude-code
metadata:
  author: dotfiles
  tags: [kubernetes, ai, openclaw, telegram, litellm, openrouter, rbac, arm64, orchestrator]
---

# OpenClaw Skill

## ¿Qué es OpenClaw?

`openclaw/openclaw` — gateway de IA personal (TypeScript). Servidor en puerto 18789,
conecta canales (Telegram) y rutea a cualquier backend LLM. Imagen oficial multi-arch (arm64 ✅).

- Docs: https://docs.openclaw.ai / K8s: https://docs.openclaw.ai/install/kubernetes
- **Sin Helm chart.** Upstream usa Kustomize. Este rol usa Jinja2, consistente con el repo.

---

## Arquitectura (estado actual)

```
Usuario (Telegram)
      │
      ▼
┌─────────────────────────────────────────────────┐
│  Namespace: openclaw                            │
│                                                 │
│  Deployment/openclaw                            │
│  ├─ initContainer: init-config (busybox)        │
│  │   copia ConfigMap → PVC en cada restart      │
│  ├─ container: openclaw-gateway :18789          │
│  │   OPENAI_API_BASE → litellm-proxy.ai:4000    │
│  │   OPENAI_API_KEY  → sk-hermes-internal       │
│  └─ container: kubernetes-mcp (sidecar :8080)   │
│      kubernetes-mcp-server v0.0.60              │
│      expone /mcp vía HTTP en localhost           │
│                                                 │
│  PVC: openclaw-data (10Gi smb-nas)              │
│  ConfigMap: openclaw-config                     │
│    ├─ openclaw.json  (gateway + model + mcp)    │
│    └─ AGENTS.md      (system prompt completo)   │
│  Secret: openclaw-secrets                       │
│    ├─ OPENCLAW_GATEWAY_TOKEN                    │
│    ├─ TELEGRAM_BOT_TOKEN (token propio)         │
│    └─ TELEGRAM_ALLOWED_USERS                    │
│                                                 │
│  Service/openclaw  → ClusterIP :18789           │
│  HTTPRoute         → openclaw.cluster.home      │
│  NetworkPolicy     → egress controlado          │
│  ServiceAccount    → openclaw (readonly+net)    │
└──────┬──────────────────────┬───────────────────┘
       │ OPENAI_API_BASE       │ localhost:8080/mcp
       ▼                       ▼
┌─────────────────┐   ┌──────────────────────┐
│ ai/litellm-proxy│   │ kubernetes API (RBAC) │
│ gpt-4o alias    │   │ readonly, sin Secrets │
│ → gpt-oss-120b  │   └──────────────────────┘
└────────┬────────┘
         │ OPENCLAW_OPENROUTER_API_KEY (nunca expuesta al pod)
         ▼
    OpenRouter API
```

---

## Routing LLM — cadena completa

| Capa | Valor |
|------|-------|
| Config (`openclaw_model_primary`) | `litellm/gpt-4o` |
| OpenClaw lo envía a LiteLLM como | `openai/gpt-4o` |
| LiteLLM alias `openai/gpt-4o` | `openai/gpt-oss-120b:free` + `api_base: openrouter.ai` |
| Fallback 1 | `openai/nvidia/nemotron-3-super-120b-a12b:free` |
| Fallback 2 | `openai/qwen/qwen-turbo` (alias `openclaw-cheap`) |
| Key en OpenRouter | `OPENCLAW_OPENROUTER_API_KEY` (solo en `ai/litellm-secrets`) |

**OpenClaw nunca ve la API key de OpenRouter.** Solo conoce `sk-hermes-internal`.

### Bug fix: `openai/` prefix + `api_base` (no `openrouter/`)

LiteLLM 1.82.x tiene un bug en `/v1/messages` (Anthropic passthrough): emite
`message_start` dos veces → `Unexpected event order` en el stream parser de OpenClaw.

**Fix**: usar `model: openai/<modelo>` + `api_base: https://openrouter.ai/api/v1`
en vez de `model: openrouter/<modelo>`. Fuerza `/v1/chat/completions` (OpenAI-compat).

Env var en el Deployment de LiteLLM:
```yaml
LITELLM_USE_CHAT_COMPLETIONS_URL_FOR_ANTHROPIC_MESSAGES: "true"
```

### Prefijo `openai/` automático

OpenClaw añade `openai/` a cualquier modelo sin provider prefix. Si config tiene
`gpt-5.4`, lo envía como `openai/gpt-5.4`. LiteLLM necesita alias con ese nombre exacto.
Usar `litellm/gpt-4o` en config evita el warning `[model-selection] Falling back to openai/...`.

---

## kubernetes-mcp sidecar

Sidecar `kubernetes-mcp-server:v0.0.60` en el mismo pod. Mismo patrón que Hermes Agent.

- Expone MCP en `http://127.0.0.1:8080/mcp`
- Genera kubeconfig desde el ServiceAccount token al arrancar
- RBAC: readonly sin Secrets (ver sección RBAC)

En `openclaw.json`:
```json
"mcpServers": {
  "kubernetes": {
    "url": "http://127.0.0.1:8080/mcp",
    "timeout": 120
  }
}
```

---

## RBAC — readonly extendido (sin Secrets)

**Regla de oro: ningún nivel permite leer `secrets` excepto `cluster-admin`.**

### Nivel actual: `readonly`

Recursos con `get/list/watch`:
- Core: pods, pods/log, services, endpoints, namespaces, nodes, events, configmaps, PVCs, PVs
- Apps: deployments, replicasets, statefulsets, daemonsets
- Batch: jobs, cronjobs
- Network: networkpolicies (networking.k8s.io)
- Cilium: networkpolicies, clusterwidenetworkpolicies, endpoints, identities, LBIPPools, L2Policies
- Gateway: httproutes, gateways, grpcroutes

### Cambiar nivel
```bash
# Operator (añade exec/logs + create/patch deployments/jobs)
ansible-playbook playbooks/bootstrap.yml --tags openclaw \
  -e "openclaw_rbac_level=operator"

# Volver a readonly
ansible-playbook playbooks/bootstrap.yml --tags openclaw
```

### Verificar
```bash
kubectl auth can-i get secrets \
  --as=system:serviceaccount:openclaw:openclaw -n ai
# → no

kubectl auth can-i list networkpolicies \
  --as=system:serviceaccount:openclaw:openclaw
# → yes
```

---

## NetworkPolicy — egress

| Destino | Puerto | Propósito |
|---------|--------|-----------|
| DNS | 53 UDP/TCP | Resolución de nombres |
| `ai/litellm-proxy` | 4000 TCP | Todo el tráfico LLM |
| `ai/holmesgpt-holmes` | 80 TCP | Subagente Holmes |
| `kagent/*` | 8080 TCP | Subagente Kagent |
| `ai/hermes-agent-mcp` | 7860 TCP | Subagente Hermes (cuando vuelva) |
| `monitoring/*` | 9090 TCP | Prometheus queries |
| External HTTPS | 443 TCP | Telegram API |
| K8s API server | 6443 TCP | RBAC / kubectl via SA |
| `192.168.178.0/24` | 80,443,8080,554 TCP | Red home (análisis read-only) |

NetworkPolicy también tiene **ingress** desde `monitoring` en port 18789 para
recibir webhooks de AlertManager.

---

## Telegram

### Token propio (separado de Hermes)
OpenClaw debe tener su propio bot token. Solo un proceso puede hacer polling por token.
Hermes está a 0 réplicas mientras OpenClaw usa el token compartido temporal.
**Próximo paso:** crear bot nuevo en `@BotFather` y actualizar `secrets.yml`.

### health-monitor 120s
Si Telegram no conecta en 120s, el health-monitor mata el proceso → CrashLoopBackOff.
Para debug sin Telegram: `openclaw_telegram_enabled: false` en defaults antes del deploy.

---

## Init container (versión actual — busybox copy)

```yaml
initContainers:
  - name: init-config
    image: busybox:1.36
    command: [sh, -c, |
      mkdir -p /home/node/.openclaw
      cp /etc/openclaw/openclaw.json /home/node/.openclaw/openclaw.json
      chown 1000:1000 /home/node/.openclaw/openclaw.json || true
    ]
```

**No usar** el patrón anterior con `onboard` — regeneraba el config ignorando el ConfigMap.

---

## Gotchas conocidos

| Problema | Causa | Fix |
|----------|-------|-----|
| CrashLoop a los 120s exactos | Telegram no conecta → health-monitor mata el proceso | Token válido o `telegram_enabled: false` |
| `Unknown model: openai/X` | LiteLLM no tiene alias `openai/X` | Añadir entry con ese nombre en litellm tasks |
| ConfigMap no recarga en caliente | subPath mount no propaga updates | Rollout restart (el checksum annotation lo fuerza) |
| `ai-hermes-deploy` levanta Hermes | Tag cubre litellm + hermes | Usar `--skip-tags ai-hermes-agent` |
| 429 en todos los modelos :free | Rate limit OpenRouter por key | Fallback a nemotron (más estable) |
| `message_start` error en stream | Bug LiteLLM `/v1/messages` doble emit | `openai/` prefix + `api_base` + env var |

---

## Deploy

```bash
# Prerequisitos
cp roles/install-openclaw/defaults/secrets.yml.example \
   roles/install-openclaw/defaults/secrets.yml
vim roles/install-openclaw/defaults/secrets.yml  # token Telegram + gateway token

# Deploy
make openclaw

# Solo litellm sin tocar Hermes
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini \
  --tags ai-hermes-deploy --skip-tags ai-hermes-agent
```

---

## Verificación post-deploy

```bash
# 1. Pod tiene 2 containers (gateway + kubernetes-mcp sidecar)
kubectl get pod -n openclaw -l app=openclaw \
  -o jsonpath='{.items[0].spec.containers[*].name}'
# → openclaw-gateway kubernetes-mcp

# 2. No hay OPENROUTER_API_KEY expuesta al pod
kubectl exec -n openclaw <pod> -- env | grep -E "OPENAI|OPENROUTER"
# → OPENAI_API_BASE=http://litellm-proxy... OPENAI_API_KEY=sk-hermes-internal

# 3. LiteLLM responde para openclaw
kubectl exec -n openclaw <pod> -- sh -c \
  'curl -s -X POST http://litellm-proxy.ai.svc.cluster.local:4000/v1/chat/completions \
   -H "Authorization: Bearer sk-hermes-internal" \
   -H "Content-Type: application/json" \
   -d "{\"model\":\"openai/gpt-4o\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":5}" \
   | grep -o "\"model\":\"[^\"]*\""'
# → "model":"nvidia/..." o "model":"gpt-oss-..."

# 4. kubernetes-mcp sidecar responde
kubectl exec -n openclaw <pod> -c kubernetes-mcp -- \
  wget -qO- http://localhost:8080/mcp | head -c 100

# 5. RBAC: NO puede leer secrets
kubectl auth can-i get secrets \
  --as=system:serviceaccount:openclaw:openclaw -n ai
# → no
```

---

## Acceso

```bash
# Web UI
https://openclaw.cluster.home

# Gateway token
kubectl get secret openclaw-secrets -n openclaw \
  -o jsonpath='{.data.OPENCLAW_GATEWAY_TOKEN}' | base64 -d

# Port-forward
kubectl port-forward -n openclaw svc/openclaw 18789:18789
```

---

## Grafana

```bash
# Tráfico OpenClaw en LiteLLM dashboard
# Dashboard: "LiteLLM AI Traffic" → filter model=~"openai/gpt-4o|openclaw-.*"

kubectl port-forward -n ai svc/litellm-proxy 4000:4000 &
curl http://localhost:4000/spend/logs \
  -H "Authorization: Bearer sk-hermes-internal" | jq '.[-5:]'
```

---

## Arquitectura multi-agente (roadmap activo)

OpenClaw es el orquestador. Los otros bots son sus minions internos:

```
Usuario (Telegram)
    │
    ▼
OpenClaw (orquestador)
    ├── kubernetes MCP (sidecar)  → cluster inspection
    ├── Holmes  MCP               → investiga alertas/incidentes
    ├── Kagent  MCP               → ejecuta agentes K8s
    └── Hermes  MCP (offline)     → código y ops (cuando tenga token propio)
```

### AlertManager → OpenClaw (pendiente)
```yaml
# kube-prometheus-stack values
alertmanager:
  config:
    receivers:
      - name: openclaw-webhook
        webhook_configs:
          - url: "http://openclaw.openclaw.svc.cluster.local:18789/webhook/alertmanager"
            send_resolved: true
```

Flujo: AlertManager → OpenClaw → llama a Holmes (investiga) → resume → Telegram.

### Red home y análisis de seguridad (read-only)
NetworkPolicy permite egress a `192.168.178.0/24`. OpenClaw puede:
- Consultar dispositivos (Hue, cámaras ONVIF, NAS)
- Analizar tráfico vía Prometheus/Cilium metrics
- **No puede escribir ni actuar** en esta fase (todo read-only)

---

## Storage

| Var | Default | Override |
|-----|---------|----------|
| `openclaw_storage_class` | `smb-nas` | `local-path` |
| `openclaw_storage_size` | `10Gi` | any |

---

## Repo Paths

```
roles/install-openclaw/
├── defaults/main.yml              # vars (modelo, RBAC, Telegram, LiteLLM, MCP)
├── defaults/secrets.yml           # gitignored
├── defaults/secrets.yml.example
└── templates/
    ├── openclaw-deployment.yaml.j2  # Deployment + init + kubernetes-mcp sidecar
    ├── openclaw-configmap.yaml.j2   # openclaw.json (mcpServers) + AGENTS.md
    ├── openclaw-rbac.yaml.j2        # SA + ClusterRole (readonly+net, sin Secrets)
    ├── openclaw-network.yaml.j2     # Service + HTTPRoute + NetworkPolicy expandida
    ├── openclaw-pvc.yaml.j2
    └── openclaw-secret.yaml.j2

roles/install-litellm-proxy/tasks/main.yml  # aliases openai/gpt-4o, openclaw-*
skills/openclaw/SKILL.md                    # este archivo
```

Makefile: `make openclaw` | `make openclaw-rbac LEVEL=<level>`
