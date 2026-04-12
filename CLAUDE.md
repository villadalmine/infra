# infra-ai/infra — Homelab K3s Infrastructure

10-node K3s cluster on ARM64 (Ubuntu 24.04) — Super6C CM4 + TuringPi 2 RK1.
Managed entirely via Ansible. **Never apply changes manually — always run the bootstrap playbook.**

## Cluster

| | |
|---|---|
| K3s servers | `srv-super6c-01-nvme` (.85), `srv-super6c-02-nvme` (.86), `srv-super6c-03-nvme` (.87), `srv-super6c-05-emmc` (.104), `srv-super6c-06-emmc` (.105) |
| K3s agents | `srv-rk1-nvme-01` (.30), `srv-rk1-nvme-02` (.48), `srv-rk1-nvme-03` (.51), `srv-rk1-nvme-04` (.54) |
| Standalone | `srv-super6c-04-emmc` (.133) — not in K3s cluster |
| Gateway IP | `192.168.178.200` (Cilium LB-IPAM, L2 announced, shared) |
| DNS | Pi-hole @ `192.168.178.203` — wildcard `*.cluster.home → .200` |
| Domain | `cluster.home` — wildcard TLS via cert-manager internal CA |
| Storage | `smb-nas` / `smb-nas-pg` (default for PVC-backed roles) + `local-path` (K3s built-in) |

## Key commands

```bash
# Full bootstrap (idempotent — safe to re-run)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini

# Selective bootstrap with tags
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags core,networking

# Resume from a specific role
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini \
  --start-at-task "Add prometheus-community Helm repository"

# Makefile shortcuts (preferred for day-to-day iteration)
make help            # show all targets
make observability   # deploy only observability stack
make ai              # build + deploy Hermes Agent (ARM64 kaniko build)
make status          # show cluster pod status
make logs            # show failing pod logs
```

## Bootstrap role order (CRITICAL — order matters)

```
install-k3s → get-kubeconfig → install-gateway-api-crds → install-cilium
→ install-cilium-pools → install-cert-manager → install-gateway
→ install-pihole → install-argocd
→ install-kube-prometheus-stack → install-tempo → install-loki → install-alloy
→ install-registry → install-hermes-agent-image
→ install-litellm-proxy → install-hermes-agent
→ install-holmes → install-holmes-ui → install-kagent → install-openclaw
```

## Bootstrap Tags

Each role is tagged for selective deployment. Tags are cumulative — include
all tags up to the layer you need.

| Tag | Roles | Requires |
|-----|-------|----------|
| `core` | k3s + kubeconfig | — |
| `networking` | gateway-api-crds + cilium + cilium-pools | `core` |
| `networking-observability` | cilium-hubble-monitoring (ServiceMonitor) | `networking` + `observability` |
| `ingress` | cert-manager + gateway | `networking` |
| `services` | pihole + argocd + helm-dashboard | `ingress` |
| `observability` | prometheus + tempo + loki + alloy | `networking` |
| `security` | neuvector | `services` |
| `ai` | registry + hermes-image + litellm-proxy + hermes-agent | `networking` |
| `ai-registry` | registry only | `networking` |
| `ai-hermes-build` | kaniko ARM64 build (~60 min) | `ai-registry` |
| `ai-hermes-deploy` | litellm-proxy + hermes-agent | `ai-hermes-build` |
| `ai-holmes` | holmes + holmes-ui (chat interface) | `ai-hermes-deploy` |
| `ai-holmes-ui` | holmes-ui only (nginx:alpine + ConfigMap) | `ai-holmes` |
| `kagent` | kagent + kmcp operator (multi-tenant agent platform) | `networking` + LiteLLM |
| `openclaw` | OpenClaw personal AI gateway (Telegram + LiteLLM + RBAC) | `networking` + LiteLLM |

```bash
# Minimal cluster (kubectl works, no networking)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags core

# Cluster with networking (deploy ClusterIPs, internal services)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags core,networking

# Full stack with public URLs (HTTPS + DNS + GitOps)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags core,networking,ingress,services

# Add observability to an existing cluster
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags observability

# Full bootstrap (all roles)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini
```

Roles are idempotent — running `--tags observability` on a cluster that already
has `core` + `networking` will skip those roles automatically.

## Service pattern (all HTTP services)

`ClusterIP` + `HTTPRoute` → `cluster-gateway` in namespace `gateway`.
**Never `LoadBalancer` for HTTP. Never `Ingress`. Always `HTTPRoute`.**
Pi-hole wildcard covers DNS. cert-manager wildcard covers TLS. Zero extra config per service.

## Security — NEVER expose credentials in git

**Before every commit, scan for secrets:**
```bash
git diff --cached | grep -iE "(api_key|token|password|secret)\s*[=:]\s*['\"]?[a-zA-Z0-9_\-]{20,}"
```

**Rules (non-negotiable):**
- Secrets live ONLY in `roles/*/defaults/secrets.yml` (gitignored) — never in task files, static YAMLs, or docs
- Static manifests with real credentials (`scripts/*.yaml`) are gitignored — keep only placeholder versions in git
- If a secret is committed accidentally: rotate it immediately, then rewrite history with `git filter-repo --replace-text` + force push
- Never hardcode API keys, bot tokens, or passwords in any file that gets committed
- If unsure whether a file with secrets is gitignored: run `git check-ignore -v <file>` before staging

**Gitignored secret locations:**
- `roles/*/defaults/secrets.yml` — per-role secrets (API keys, passwords)
- `roles/*/defaults/secrets.yaml` — same, alternate extension
- `scripts/hermes-static.yaml` — K8s manifest with real credentials (use placeholder template in git)

## Workflow for new Helm roles

1. `helm install` manually on cluster → verify working
2. `helm uninstall` to clean up
3. Write Ansible role (`roles/<name>/`) + defaults (`defaults/main.yml`)
   - If the role uses a PVC: add `<role>_storage_class: "smb-nas"` and `<role>_storage_role: "install-cifs-nas"` to defaults
   - Add `include_role: install-cifs-nas` as first task, guarded by `when: <role>_storage_class != 'local-path' and <role>_storage_role is defined`
   - See `skills/storage/SKILL.md` for the full pattern
4. Add role to `playbooks/bootstrap.yml`
5. `ansible-playbook playbooks/bootstrap.yml` → must pass `failed=0`
6. Create skill in `skills/<name>/SKILL.md` (in this repo)
7. **Update `README.md`** — add the service to the public or internal services table
8. Commit

## Installed component versions

| Component | Helm Chart | Version | App Version |
|---|---|---|---|
| K3s | — | v1.35.2+k3s1 | — |
| Cilium | `cilium/cilium` | 1.19.2 | 1.19.2 |
| cert-manager | `jetstack/cert-manager` | v1.20.1 | v1.20.1 |
| kube-prometheus-stack | `prometheus-community/kube-prometheus-stack` | 82.18.0 | v0.89.0 |
| Tempo | `grafana-community/tempo` | 1.26.7 | 2.10.1 |
| Loki | `grafana/loki` | 6.55.0 | 3.x |
| Alloy | `grafana/alloy` | 1.7.0 | v1.15.0 |
| NeuVector | `neuvector/core` | 2.8.12 | 5.5.0 |
| Docker Registry | `registry:2` | 2 | 2.x |
| LiteLLM proxy | `ghcr.io/berriai/litellm` | main-latest | in-cluster |
| Hermes Agent | `registry.registry:5000/ai/hermes-agent` | 0.7.0 | ARM64 custom build |
| HolmesGPT | `robusta/holmes` | 0.24.0 | 0.24.0 |
| Holmes UI | `nginx:alpine` | — | ConfigMap-mounted static UI |
| kagent | `oci://ghcr.io/kagent-dev/kagent/helm/kagent` | 0.8.5 | 0.8.5 (multi-arch) |
| OpenClaw | `registry.registry:5000/ai/openclaw` (custom build) | — | ARM64, namespace `openclaw` |

Hermes is deployed as a persistent gateway on the high-resource node with a
mounted `gateway.json` so the webhook platform stays enabled and the pod does
not exit after startup.

For long-running Ansible validation, prefer: manual/Helm proof first, then
background `ansible-playbook` with logs redirected, then foreground Ansible
only after the background run proves the change.

> **Check outdated charts:** `nova --format table find --helm`
> Cilium 1.20.0-pre.1 is pre-release — do NOT upgrade. Tempo pinned to 1.26.7 (2.0.0 buggy).

## Skills (deep technical context per component)

Located in `./skills/` (this repo — self-contained, no dotfiles dependency).
Read the relevant skill before working on a component.

| Skill | Covers |
|---|---|
| `onboarding` | First-time setup: mise, SSH keys, sudoers, inventory format |
| `survey` | gather-node-info role: what it collects, JSON output, node profiles |
| `infra-ops` | 10-node topology, all make targets, RK1 MAC fix, health checks |
| `k3s` | Server flags, kubeconfig, upgrades |
| `cilium` | CNI, LB-IPAM, L2, Gateway API, BPF, **Hubble metrics** |
| `gateway` | Shared Gateway, HTTPRoutes, DNS setup |
| `cert-manager` | Internal CA, wildcard cert, workstation trust |
| `argocd` | GitOps, ApplicationSets, sync waves |
| `pihole` | Wildcard DNS, Pi-hole 6 gotchas |
| `monitoring` | Prometheus, Grafana, Tempo, Alloy — full observability stack |
| `ai` | Registry + LiteLLM proxy + Hermes + HolmesGPT + Holmes UI — full ARM64 AI stack |
| `kagent` | kagent + kmcp — multi-tenant AI agent platform, CRDs, RBAC, LiteLLM integration |
| `openclaw` | Personal AI gateway — Telegram bot, LiteLLM routing, RBAC levels, double `message_start` fix |
| `k8s-debug` | Debug pods, network, nodes systematically |
| `storage` | CIFS/SMB CSI driver, PV/PVC patterns |
| `ai-memory` | Guidelines for cross-session AI Memory persistence |

## AI Tools (self-contained)

This repo is configured to work with both **OpenCode** and **Claude Code** out of the box.

### OpenCode
Config: `opencode.json` — points to in-cluster LiteLLM (`litellm.cluster.home`).
Deploy the AI stack first: `make ai` — then OpenCode routes all models through LiteLLM.
Override locally (e.g. for direct OpenRouter before cluster is up): create `opencode.local.json`.

### Claude Code
Config: `.claude/settings.json` — permissions for ansible-playbook, kubectl, helm, make, etc.
Context: `CLAUDE.md` (this file) is loaded automatically.

### LiteLLM — universal AI router
- **Local (workstation):** `http://localhost:4000` — start with `make litellm`
  Config: `setup/litellm/config.yaml` — edit to add/remove providers
- **In-cluster (after `make ai`):** `http://litellm.cluster.home/v1`
  Key: `sk-hermes-internal`

Models: `default`, `claude-sonnet`, `claude-opus`, `gemini-flash`, `gpt-4o-mini`, `free`, `cheap`
All route through OpenRouter by default (one `OPENROUTER_API_KEY` covers everything).

### MCP Servers
Configured in `.mcp.json` (Claude Code) and `opencode.json` → `mcp` key (OpenCode).
- `kubernetes` — `npx -y kubernetes-mcp-server@latest` — requires `~/.kube/config`
  Available after `make core` deploys K3s and kubeconfig is fetched.
  *Survey note: For cluster survey reports, the MCP server parses `make node-stats` and `kubectl top nodes` outputs directly, feeding them into the AI memory.*
- `memory` — Continuous AI memory persistence across sessions via Knowledge Items (KIs).

Full AI setup guide: `docs/ai-setup.md`

## Repo paths

- Infra: this repo (clone anywhere)
- Skills: `./skills/` (relative to repo root)

## docs/

- `docs/ai-agents.md` — OpenCode + HolmesGPT architecture and integration map
- `docs/tailscale-multisite.md` — multi-site K3s + Cilium Cluster Mesh plan (not yet implemented)
