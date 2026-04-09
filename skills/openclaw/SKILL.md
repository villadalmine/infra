---
name: openclaw
description: >
  OpenClaw personal AI gateway deployed on K8s. Multi-channel (Telegram first),
  backed by the shared LiteLLM proxy (Gemini-free → qwen-free → qwen-free2 chain).
  Modular Kubernetes RBAC with 4 permission levels, all Ansible-controlled.
  No Helm chart exists — upstream uses Kustomize; this role uses Jinja2 templates.
license: MIT
compatibility:
  - opencode
metadata:
  author: dotfiles
  tags: [kubernetes, ai, openclaw, telegram, litellm, openrouter, rbac, arm64]
---

# OpenClaw Skill

## What is OpenClaw?

`openclaw/openclaw` (~352k ⭐, TypeScript) — personal AI assistant gateway.
Runs a WebSocket control plane (port 18789) that connects to messaging channels
and routes to any LLM backend. Official Docker image is multi-arch (arm64 ✅).

- Website: https://openclaw.ai
- Docs: https://docs.openclaw.ai
- K8s docs: https://docs.openclaw.ai/install/kubernetes

> **No Helm chart exists.** Upstream uses Kustomize (`scripts/k8s/`).
> This role wraps Jinja2 templates directly — consistent with every other role in this repo.

---

## Stack

| Component | Image | Namespace | URL |
|-----------|-------|-----------|-----|
| OpenClaw Gateway | `ghcr.io/openclaw/openclaw:latest` | `openclaw` | `openclaw.cluster.home` |
| LiteLLM proxy | shared — `ai` namespace | `ai` | internal only |

---

## Architecture

```
Telegram (bot)
    │ polling
    ▼
┌────────────────────────────────────────┐
│  Namespace: openclaw                   │
│                                        │
│  Deployment/openclaw                   │
│  ├─ initContainer: openclaw-init       │
│  └─ container: openclaw-gateway :18789 │
│     ├─ PVC: openclaw-data (10Gi smb)   │
│     ├─ ConfigMap: openclaw-config      │
│     │   ├─ openclaw.json               │
│     │   └─ AGENTS.md (system prompt)   │
│     └─ Secret: openclaw-secrets        │
│         ├─ OPENCLAW_GATEWAY_TOKEN      │
│         ├─ TELEGRAM_BOT_TOKEN          │
│         └─ TELEGRAM_ALLOWED_USERS      │
│                                        │
│  Service/openclaw → ClusterIP :18789   │
│  HTTPRoute → openclaw.cluster.home     │
│  NetworkPolicy: egress-controlled      │
│  ServiceAccount: openclaw              │
│  ClusterRole: openclaw-<level>         │
└────────────────────────────────────────┘
    │ OPENAI_API_BASE
    ▼
┌─────────────────────────────────┐
│  Namespace: ai                  │
│  LiteLLM proxy :4000            │
│  model chain:                   │
│    gemini-free → gemini 2.5 pro │
│    free        → qwen3-coder    │
│    free2       → gemini 2.0     │
└──────────────┬──────────────────┘
               │ HTTPS:443
               ▼
          OpenRouter API
```

---

## LiteLLM — Shared vs Dedicated

**Decision: use the shared LiteLLM proxy in namespace `ai`.**

Reasons:
- Already deployed, already has `OPENROUTER_API_KEY`
- Prometheus metrics already scraped → visible in Grafana
- Single place to add/change models for all agents
- No operational overhead of a second proxy

OpenClaw connects via `OPENAI_API_BASE=http://litellm-proxy.ai.svc.cluster.local:4000`
with the shared master key `sk-hermes-internal`.

To add OpenClaw-specific virtual models to LiteLLM, edit
`roles/install-litellm-proxy/tasks/main.yml` and add entries like:
```yaml
- model_name: gemini-free
  litellm_params:
    model: openrouter/google/gemini-2.5-pro-exp-03-25:free
- model_name: qwen-free
  litellm_params:
    model: openrouter/qwen/qwen3-coder:free
```
Then run `make ai-hermes-deploy` to reload LiteLLM.

---

## Modular RBAC

### Levels

| Level | What it grants | Use case |
|-------|---------------|----------|
| `readonly` | get/list/watch — pods, svc, nodes, events, deploys | Default. Safe inspection |
| `operator` | + exec/logs + create/patch/delete jobs & deployments | Active cluster management |
| `admin` | Namespaced admin (openclaw ns) + cluster view | Full control in own ns |
| `cluster-admin` | ClusterRoleBinding cluster-admin | Unrestricted — explicit opt-in |

### Change permission level

```bash
# Drop to read-only (safest)
make openclaw-rbac LEVEL=readonly

# Promote to operator for active management
make openclaw-rbac LEVEL=operator

# Full cluster access (temporary — drop back when done)
make openclaw-rbac LEVEL=cluster-admin
ansible-playbook playbooks/bootstrap.yml --tags openclaw \
  -e "openclaw_rbac_level=readonly"
```

Extending the RBAC: edit `roles/install-openclaw/templates/openclaw-rbac.yaml.j2`.
Each level is a separate `{% if %}` block — add new verbs/resources as needed.
The `operator` block is the most commonly extended.

---

## Telegram Integration

1. Create bot via `@BotFather` → `/newbot` → copy token
2. Find your Telegram user ID via `@myidbot`
3. Add to `roles/install-openclaw/defaults/secrets.yml`:
   ```yaml
   openclaw_telegram_token: "<your-bot-token>"
   openclaw_telegram_allowed_users: "<your-user-id>"
   ```
4. `make openclaw`

**Security model:**
- `dmPolicy: "pairing"` — unknown users get a pairing code, bot ignores them until approved
- `openclaw_telegram_allowed_users` — hardcoded allow-list (your user ID)
- Both controls are active simultaneously for defense in depth

**Note:** The bot token is currently shared with Hermes Agent. Both bots use the
same Telegram account. You can create a separate bot anytime via `@BotFather`.

---

## Installation

### Prerequisites

```bash
# Secrets file must exist
ls roles/install-openclaw/defaults/secrets.yml
# If missing:
cp roles/install-openclaw/defaults/secrets.yml.example \
   roles/install-openclaw/defaults/secrets.yml
vim roles/install-openclaw/defaults/secrets.yml

# Generate gateway token
openssl rand -hex 32
```

### Deploy

```bash
make openclaw
```

### Override defaults

```bash
# Different node
ansible-playbook playbooks/bootstrap.yml --tags openclaw \
  -e "openclaw_node_hostname=srv-rk1-nvme-03"

# Local storage (no NAS)
ansible-playbook playbooks/bootstrap.yml --tags openclaw \
  -e "openclaw_storage_class=local-path"

# Different RBAC level
make openclaw-rbac LEVEL=operator
```

---

## Access

```bash
# Web UI (Control UI + WebChat)
https://openclaw.cluster.home

# Port-forward (no DNS needed)
kubectl port-forward -n openclaw svc/openclaw 18789:18789
open http://localhost:18789

# Get gateway token
kubectl get secret openclaw-secrets -n openclaw \
  -o jsonpath='{.data.OPENCLAW_GATEWAY_TOKEN}' | base64 -d

# Pod status
kubectl get pods -n openclaw
kubectl logs -n openclaw -l app=openclaw --tail=50

# Health check
curl http://localhost:18789/healthz
curl http://localhost:18789/readyz
```

---

## Grafana — Tracking LLM Traffic

All OpenClaw requests go through LiteLLM → Prometheus → Grafana.

```bash
# See requests per model in Grafana:
# Dashboard: LiteLLM → "Requests by model"
# Filter: model=~"gemini-free|free|free2"

# Direct LiteLLM usage check
kubectl port-forward -n ai svc/litellm-proxy 4000:4000 &
curl http://localhost:4000/spend/logs \
  -H "Authorization: Bearer sk-hermes-internal" | jq '.[-10:]'
```

---

## Troubleshooting

### Pod stuck in Init

```bash
kubectl describe pod -n openclaw -l app=openclaw
kubectl logs -n openclaw -l app=openclaw -c openclaw-init
```
Usually caused by missing/wrong `OPENCLAW_GATEWAY_TOKEN` or wrong image tag.

### Telegram not responding

```bash
kubectl logs -n openclaw -l app=openclaw --tail=100 | grep -i telegram
```
Check: token set in secret, user ID in allowed list, bot started via @BotFather.

### LiteLLM errors / model not found

```bash
kubectl port-forward -n ai svc/litellm-proxy 4000:4000 &
curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer sk-hermes-internal" | jq '.data[].id'
```
If `gemini-free` not listed → add it to `roles/install-litellm-proxy/tasks/main.yml`
and run `make ai-hermes-deploy`.

### NetworkPolicy blocking egress

```bash
kubectl get networkpolicy -n openclaw
# Temporarily disable for debugging:
kubectl delete networkpolicy openclaw-egress -n openclaw
```

---

## Storage Dependency

Follows the project-wide pattern (`install-cifs-nas` idempotent guard):

```yaml
# In tasks/main.yml:
- include_role:
    name: "{{ openclaw_storage_role }}"
  when: openclaw_storage_class != 'local-path' and openclaw_storage_role is defined
```

| Var | Default | Override |
|-----|---------|----------|
| `openclaw_storage_class` | `smb-nas` | `local-path` |
| `openclaw_storage_role` | `install-cifs-nas` | — |
| `openclaw_storage_size` | `10Gi` | any |

See `skills/storage/SKILL.md` for full pattern documentation.

---

## Future: kgateway / solo.io

When you want to route through [kgateway](https://kgateway.dev/) (solo.io) instead of
or in addition to LiteLLM, change:

```yaml
# roles/install-openclaw/defaults/main.yml
openclaw_llm_backend: "openrouter"     # or "kgateway"
openclaw_litellm_url: "http://kgateway.kgateway.svc.cluster.local:8080"
```

No other changes needed — the Deployment template conditionally sets `OPENAI_API_BASE`
based on `openclaw_llm_backend`.

---

## Repo Paths

- Role: `roles/install-openclaw/`
- Secrets: `roles/install-openclaw/defaults/secrets.yml` (gitignored)
- Example: `roles/install-openclaw/defaults/secrets.yml.example`
- Playbook tag: `openclaw`
- Makefile: `make openclaw`, `make openclaw-rbac LEVEL=<level>`
