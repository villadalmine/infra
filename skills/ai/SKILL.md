---
name: ai
description: >
  AI Agent stack: Hermes Agent (self-improving AI assistant) routed through
  in-cluster LiteLLM proxy with OpenRouter fallback chains (free→free2→cheap).
  Built for ARM64 (Raspberry Pi CM4) using in-cluster kaniko build.
  Includes Docker registry:2 for storing custom ARM64 images.
license: MIT
compatibility:
  - opencode
metadata:
  author: dotfiles
  tags: [kubernetes, ai, hermes, openrouter, litellm, llm, kaniko, arm64, registry]
---

# AI Agent Skill

## Stack Overview

| Component | Image | Version | Namespace | Notes |
|-----------|-------|---------|-----------|-------|
| Docker registry | `registry:2` | 2 | registry | ARM64-compatible image storage (5Gi PVC) |
| LiteLLM proxy | `ghcr.io/berriai/litellm` | main-latest | ai | In-cluster OpenRouter router with fallbacks |
| Hermes Agent | `registry.registry:5000/ai/hermes-agent` | 0.7.0 | ai | Gateway mode + Telegram polling + MCP sidecar |
| kubernetes-mcp-server | `registry.registry:5000/ai/kubernetes-mcp-server` | v0.0.60 | ai (sidecar) | K8s read-only MCP server sidecar in Hermes pod |
| Kaniko | `gcr.io/kaniko-project/executor` | latest | kaniko | In-cluster ARM64 image builder |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Namespace: ai                          │
│                                                             │
│  ┌─────────────────┐    ┌──────────────────────────────┐   │
│  │  Hermes Agent   │───▶│  LiteLLM Proxy               │   │
│  │  model=free     │    │  port 4000                   │   │
│  │  OPENAI_API_BASE│    │  fallback: free→free2→cheap  │   │
│  └─────────────────┘    └─────────────┬────────────────┘   │
│                                       │ HTTPS:443           │
│                                       ▼                     │
│                               OpenRouter API                │
│                               (external)                    │
│  PVC: hermes-data (/opt/data)                               │
│  Secret: litellm-secrets (OPENROUTER_API_KEY)               │
│  Secret: hermes-secrets (OPENROUTER_API_KEY + bot tokens)   │
└─────────────────────────────────────────────────────────────┘

Namespace: registry
  registry:2 pod ← kaniko pushes here ← Kaniko job (namespace: kaniko)
  registries.yaml on K3s nodes → mirror registry.registry:5000 → ClusterIP
```

---

## Why Custom Build?

Hermes Agent official Docker image (`nousresearch/hermes-agent`) is **amd64-only**.
For ARM64 clusters (Raspberry Pi CM4), we build in-cluster using kaniko.

**Build process:**
1. Kaniko job clones `https://github.com/NousResearch/hermes-agent`
2. Builds ARM64 image using custom Dockerfile (`--snapshot-mode=redo` for low memory)
3. Pushes to local registry: `registry.registry:5000/ai/hermes-agent:0.7.0`
4. Hermes deployment uses the local image

**Build time:** ~60 min on Raspberry Pi CM4 (heavy: debian + nodejs + pip deps + ffmpeg)

**Kaniko gotchas:**
- `--snapshot-mode=redo` — uses mtime for change detection (much less memory than default)
- Node affinity → control-plane node (more disk space than agent)
- `backoffLimit: 3` — OOM on agent node caused earlier failures
- Wait timeout: `3600s`

---

## LiteLLM Proxy — Model Routing

Hermes does NOT call OpenRouter directly. It calls the in-cluster LiteLLM proxy:

```
OPENAI_API_BASE=http://litellm-proxy.ai.svc.cluster.local:4000
OPENAI_API_KEY=sk-hermes-internal  (LiteLLM master key)
HERMES_MODEL=free
```

LiteLLM config (`roles/install-litellm-proxy/tasks/main.yml`):

| Virtual model | Real model | Provider |
|--------------|-----------|---------|
| `free` | `openrouter/qwen/qwen3-coder:free` | coding-first free tier |
| `free2` | `openrouter/google/gemini-2.0-flash-exp:free` | Google free fallback |
| `cheap` | `openrouter/qwen/qwen-turbo` | reliable paid fallback |
| `strong` | `openrouter/deepseek/deepseek-chat-v3-0324` | best balance for hard tasks |

### Hermes MCP lessons learned

- Kubernetes MCP works reliably when exposed as HTTP and configured with `url: http://127.0.0.1:8080/mcp`.
- `type: sse` was not enough in practice; the HTTP endpoint had to be explicit.
- Removing the terminal block did not force MCP usage by itself.
- Adding a strong system prompt helped, but Hermes can still prefer shell fallbacks unless the MCP transport is correct.
- `kubectl`/`oc` inside the Hermes container were not required for MCP to work once the client connected correctly.
- The working static manifest uses a sidecar pattern: Hermes agent + `kubernetes-mcp-server` sidecar + `/opt/data` + `serviceAccountName` + `mcp_servers.kubernetes.url=/mcp`.
- For cluster metrics, Hermes still needs either `metrics-server` or a custom bridge; Prometheus alone is not enough for `pods_top` / `nodes_top`.
- Telegram privacy is enforced with `TELEGRAM_ALLOWED_USERS` and the gateway platform `allowed_users` list. Keep those aligned to a single user ID when you want a private bot.

Fallback chain: `free → free2 → cheap` (automatic, transparent to Hermes).
Use `cheap` or `strong` directly when you want to skip free tiers.

---

## Installation

### Step 1: Install registry (fast)

```bash
make ai-registry
```

### Step 2: Build ARM64 images (60 min + 1 min)

```bash
make ai-hermes-build        # hermes-agent (~60 min on CM4)
make ai-kubernetes-mcp-build  # kubernetes-mcp-server sidecar (~1 min)
# Monitor with:
kubectl get jobs -n kaniko
kubectl logs -n kaniko job/build-hermes-arm64 -f | grep -v "npm WARN"
kubectl logs -n kaniko job/build-kubernetes-mcp-server-arm64 -f
```

Both images are required before Hermes deploy — `hermes-agent-mcp` pod has 2 containers.

### Step 3: Deploy LiteLLM proxy + Hermes (2 min)

```bash
make ai-hermes-deploy
```

### All at once

```bash
make ai  # registry + hermes-build + kubernetes-mcp-build + hermes-deploy (~70 min total)
```

---

## Configuration

### API key (required)

Create `roles/install-hermes-agent/defaults/secrets.yml` (gitignored):

```yaml
hermes_openrouter_api_key: "sk-or-v1-..."
hermes_telegram_token: ""   # optional
hermes_discord_token: ""    # optional
```

LiteLLM proxy loads this same file automatically.

### Change default model tier

Edit `roles/install-hermes-agent/defaults/main.yml`:

```yaml
hermes_model: "free"    # default — uses LiteLLM fallback chain
hermes_model: "cheap"   # skip free tiers entirely
```

### Resources (CM4-friendly defaults)

| Component | CPU req | CPU limit | Mem req | Mem limit |
|-----------|---------|-----------|---------|-----------|
| LiteLLM proxy | 100m | 500m | 128Mi | 512Mi |
| Hermes Agent | 100m | 500m | 128Mi | 512Mi |

---

## Access

```bash
# Hermes web UI (requires ingress stack)
https://hermes.cluster.home

# Port-forward (no ingress required)
kubectl port-forward -n ai svc/hermes-agent 8080:8080
# → http://localhost:8080

# Hermes CLI
kubectl exec -it -n ai deployment/hermes-agent -- hermes

# LiteLLM proxy health
kubectl port-forward -n ai svc/litellm-proxy 4000:4000
curl http://localhost:4000/health
```

---

## Troubleshooting

### Kaniko build fails with OOM / disk pressure

```bash
kubectl describe node cm4-unknow  # check disk/memory
kubectl get events -n kaniko --sort-by=.lastTimestamp
```

Fix: build always runs on control-plane node (`node-role.kubernetes.io/control-plane`).
Job has `backoffLimit: 3` — it will retry up to 3 times.

### Kaniko build push fails (registry unreachable)

```bash
# Check registries.yaml on server node
ssh srv-rk1-01 "cat /etc/rancher/k3s/registries.yaml"
# Should show:
# mirrors:
#   "registry.registry:5000":
#     endpoint:
#       - "http://<ClusterIP>:5000"
```

Reapply: `make ai-registry`

### Hermes pod ImagePullBackOff (2 containers — both must exist)

```bash
kubectl describe pod -n ai -l app=hermes-agent-mcp | grep -A5 Events
```

The pod `hermes-agent-mcp` has 2 containers:
- `hermes-agent` → `registry.registry:5000/ai/hermes-agent:0.7.0` (build: `make ai-hermes-build`)
- `kubernetes-mcp-server` → `registry.registry:5000/ai/kubernetes-mcp-server:v0.0.60` (build: `make ai-kubernetes-mcp-build`)

Both builds must complete before the pod can start. If the sidecar image is missing, pod shows
`1/2` or `ImagePullBackOff` on the second container. Fix: run the missing build, then
`kubectl rollout restart deployment/hermes-agent-mcp -n ai`.

- `registries.yaml` not configured → run `make ai-registry`

### LiteLLM proxy CrashLoopBackOff

```bash
kubectl logs -n ai -l app=litellm-proxy --tail=50
```

- Missing `litellm-secrets` → run `make ai-hermes-deploy`
- Bad API key → check `roles/install-hermes-agent/defaults/secrets.yml`

### Hermes calling wrong model / 429 errors

LiteLLM handles 429s automatically via fallback chain.
Debug routing:
```bash
kubectl port-forward -n ai svc/litellm-proxy 4000:4000
curl http://localhost:4000/v1/models
```

---

## Useful Commands

```bash
# Build status
kubectl get job -n kaniko build-hermes-arm64
kubectl logs -n kaniko job/build-hermes-arm64 --tail=30 | grep -v "npm WARN"

# Registry images
kubectl port-forward -n registry svc/registry 5000:5000 &
curl http://localhost:5000/v2/_catalog

# AI stack health
kubectl get pods -n ai
kubectl logs -n ai -l app=litellm-proxy --tail=20
kubectl logs -n ai -l app=hermes-agent --tail=20

# LiteLLM models
kubectl port-forward -n ai svc/litellm-proxy 4000:4000 &
curl http://localhost:4000/v1/models -H "Authorization: Bearer sk-hermes-internal"
```

---

## Storage Dependency

All AI build/deploy roles use `smb-nas` StorageClass by default.
The storage backend (`install-cifs-nas`) is **auto-installed** as the first task — no need to run `make storage` separately.

| Role | Storage var | StorageClass |
|------|-------------|-------------|
| `install-registry` | `registry_storage_class` | `smb-nas` |
| `install-hermes-agent` | `hermes_storage_class` | `smb-nas` |
| `install-hermes-agent-image` | `kaniko_storage_class` | `smb-nas` |
| `install-kubernetes-mcp-server-image` | `kubernetes_mcp_storage_class` | `smb-nas` |

To override to local-path (no NAS):
```bash
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags ai \
  -e "registry_storage_class=local-path hermes_storage_class=local-path kaniko_storage_class=local-path"
```

See `skills/storage/SKILL.md` for the full pattern documentation.

---

## Repo Paths

- Roles: `roles/install-registry/`, `roles/install-litellm-proxy/`, `roles/install-hermes-agent-image/`, `roles/install-hermes-agent/`
- Playbook tags: `ai`, `ai-registry`, `ai-hermes-build`, `ai-hermes-deploy`
- Makefile: `make ai`, `make ai-registry`, `make ai-hermes-build`, `make ai-hermes-deploy`
- Secrets: `roles/install-hermes-agent/defaults/secrets.yml` (gitignored, shared with litellm-proxy)

### Hermes secrets — `include_vars` required

`roles/install-hermes-agent/defaults/secrets.yml` is NOT auto-loaded by Ansible
(only `defaults/main.yml` is auto-loaded). The role explicitly calls `include_vars`
at the start to load secrets. If `secrets.yml` is missing, the role continues with
empty vars (`failed_when: false`) — resulting in empty OPENROUTER_API_KEY.

Credentials location: `roles/install-hermes-agent/defaults/secrets.yml` (gitignored).

### Operational notes

- Deployment name is `hermes-agent-mcp` (not `hermes-agent`) — sidecar pattern since 2026-04.
- The sidecar `kubernetes-mcp-server` exposes port 8080 → Hermes connects via `http://127.0.0.1:8080/mcp`.
- `hermes-secrets` Secret and `hermes-gateway-config` ConfigMap are created by the Ansible template on every deploy.
- Use `/mcp` for the Kubernetes MCP HTTP endpoint in Hermes configs.
