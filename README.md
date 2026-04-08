# infra-ai — Homelab K3s Cluster + AI-Native Ops Platform

10-node ARM64 K3s cluster on Super6C (CM4) + TuringPi 2 (RK1), managed entirely via Ansible.
This repo is the single source of truth — never apply changes manually.

**Key idea:** the cluster is also a learning platform. A knowledge graph (`stacks.yaml`, `projects.yaml`, `learners.yaml`, `hardware-catalog.yaml`) connects hardware survey data to deployment recommendations, learning curricula, and CNCF project metadata — all queryable via an MCP server.

---

## Quick Start

```bash
# 0. See what 'make deps' will install (no changes made)
make preview

# 1. Install workstation tools (mise + ansible + python packages)
make deps
# Undo at any time: make uninstall-local

# 2. Configure SSH access on nodes (run once, needs password)
make setup-nodes

# 3. Survey hardware — collect facts from all nodes
make survey

# 4. Start AI assistant (optional — needs OPENROUTER_API_KEY)
make litellm

# 5. Minimal cluster (K3s + Cilium — minimum viable, DIY from here)
make quick                      # = make core && make networking

# 6. Full cluster bootstrap
make core && make networking    # K3s + Cilium (required pair — core alone is broken)
make ingress                    # cert-manager + Gateway API
make dns                        # Pi-hole wildcard DNS
make gitops                     # ArgoCD
make storage                    # SMB/CIFS CSI (MUST come before observability/ai/security)
make observability              # Prometheus + Grafana + Tempo + Loki + Alloy
make ai && make ai-holmes && make kagent   # Full AI stack
make security                   # NeuVector runtime security

# Or everything at once
make full
```

See `cluster-report.html` for a visual report of cluster status, stacks, and project catalog.

---

## Services

### Public (HTTPRoute via Gateway at 192.168.178.200)

| Service | URL | What it does | Credentials |
|---|---|---|---|
| ArgoCD | https://argocd.cluster.home | GitOps | `kubectl get secret argocd-initial-admin-secret -n argocd -o jsonpath='{.data.password}' \| base64 -d` |
| Grafana | https://grafana.cluster.home | Metrics, logs, traces, dashboards | `admin` / `admin` |
| Pi-hole | https://pihole.cluster.home | DNS admin + ad-block | `changeme` |
| helm-dashboard | https://helm-dashboard.cluster.home | Helm release UI | N/A |
| HolmesGPT | https://holmes.cluster.home | AI SRE API | — |
| Holmes UI | https://holmes-ui.cluster.home | Chat UI for HolmesGPT | — |
| Hermes Agent | https://hermes.cluster.home | AI assistant (Telegram + web) | — |
| kagent | https://kagent.cluster.home | AI agent platform (CRDs + MCP) | — |

### Security (dedicated LoadBalancer — Cilium doesn't support TLS passthrough)

| Service | URL | Notes |
|---|---|---|
| NeuVector | https://192.168.178.204 | Runtime security, vuln scanning | `admin` / set on first login |

### Internal (cluster-only)

| Service | Internal DNS | What it does |
|---|---|---|
| Prometheus | `kube-prometheus-stack-prometheus.monitoring:9090` | Metrics |
| AlertManager | `kube-prometheus-stack-alertmanager.monitoring:9093` | Alerts |
| Tempo | `tempo.monitoring:3200` | Distributed tracing |
| Loki | `loki-gateway.monitoring:80` | Log aggregation |
| Alloy | `alloy.monitoring:4317` (gRPC) | OTel pipeline |
| LiteLLM | `litellm-proxy.ai:4000` | LLM router (free→cheap→paid) |
| Registry | `registry.registry:5000` | ARM64 image storage |

---

## AI Stack

```bash
make ai-registry       # deploy registry
make ai-hermes-build   # kaniko ARM64 build (~60 min on CM4)
make ai-hermes-deploy  # litellm-proxy + hermes-agent
make ai-holmes         # HolmesGPT + Holmes UI
make kagent            # multi-tenant agent platform + kmcp
make ai                # all in sequence
```

Ask HolmesGPT from CLI:
```bash
./scripts/holmes-chat "What pods are using more than 500 MiB of RAM?"
./scripts/holmes-chat "Check whether Grafana error rate spiked in the last 30m"
```

Hermes (Telegram): send plain text like `che como está mi cluster` or `cuantos pods hay en monitoring`.

All AI services route through the in-cluster LiteLLM proxy (`sk-hermes-internal`).
OpenRouter API key: `roles/install-hermes-agent/defaults/secrets.yml` (gitignored).

---

## Knowledge Graph (cluster-advisor MCP)

The cluster-advisor MCP cross-references hardware survey data against a knowledge graph to answer planning questions, recommend hardware, and guide learning.

```
survey/*.json           → live hardware facts per node (make survey)
stacks.yaml             → 29 modular stacks (11 live, 12 planned)
projects.yaml           → 44 projects: CNCF status, license, stars, maintenance health
learners.yaml           → 5 learning profiles with curricula and milestones
hardware-catalog.yaml   → 12 ARM64 boards with prices and vendor URLs
skills/*/SKILL.md       → deep technical docs per component
```

**Start the MCP:**
```bash
python3 mcp/cluster-advisor/server.py
# or: configured in .mcp.json (Claude Code) and opencode.json (OpenCode)
```

**Ask questions:**
```
"Analyze my cluster and tell me what I can deploy"
"I want to learn DevOps — what should I deploy and in what order?"
"What's the CNCF status of the AI stack projects?"
"What hardware should I buy to run local AI inference?"
"How powerful is my cluster for ML workloads?"
```

**MCP tools:**

| Tool | What it does |
|---|---|
| `list_nodes()` | Table of all surveyed nodes |
| `node_profile(hostname)` | Deep hardware + K8s readiness |
| `analyze_cluster()` | Flavor + node assignments + make commands |
| `cluster_stacks()` | RAM budget per stack + storage tiers |
| `cluster_roadmap()` | 7-phase deployment plan |
| `cluster_power_score()` | S/A/B/C/D across 5 dimensions |
| `learning_roadmap(profile)` | Curriculum for beginner/devops/ai-builder/security/full-stack |
| `hardware_catalog()` | Boards to buy with prices + K8s readiness |
| `what_to_buy(goal)` | ha / npu / budget / local-inference / full-cluster |
| `stack_projects(stack)` | CNCF status + stars + maintenance health per stack |
| `get_skill(name)` | Read deep technical skill doc |

**Planned stacks** (YAML-defined, roles to be written):
`gitops-alternatives` · `data-engineering` · `databases` · `ml-platform` ·
`networking-advanced` · `observability-advanced` · `wasm` · `virtualization` ·
`cost-modeling` · `sustainability` · `gpu-sharing` · `storage-distributed`

---

## Infrastructure

| IP | Host | Role |
|---|---|---|
| `192.168.178.85` | srv-super6c-01-nvme | K3s server, etcd CP |
| `192.168.178.86` | srv-super6c-02-nvme | K3s server, etcd CP |
| `192.168.178.87` | srv-super6c-03-nvme | K3s server, etcd CP |
| `192.168.178.104` | srv-super6c-05-emmc | K3s server, etcd CP |
| `192.168.178.105` | srv-super6c-06-emmc | K3s server, etcd CP |
| `192.168.178.30` | srv-rk1-nvme-01 | K3s agent, AI worker (31GB, NPU) |
| `192.168.178.48` | srv-rk1-nvme-02 | K3s agent, AI worker (31GB, NPU) |
| `192.168.178.51` | srv-rk1-nvme-03 | K3s agent, AI worker (31GB, NPU) |
| `192.168.178.54` | srv-rk1-nvme-04 | K3s agent, AI worker (31GB, NPU) |
| `192.168.178.133` | srv-super6c-04-emmc | Standalone (not in K3s) |
| `192.168.178.200` | — | Shared Cilium Gateway (LB-IPAM, all HTTP/HTTPS) |
| `192.168.178.203` | — | Pi-hole DNS — wildcard `*.cluster.home → .200` |
| `192.168.178.204` | — | NeuVector HTTPS (dedicated LoadBalancer) |

- **Domain:** `cluster.home` — wildcard TLS via cert-manager internal CA
- **DNS:** Pi-hole at `.203` → `*.cluster.home → .200` (Gateway)
- **Storage:** `local-path` (default, K3s built-in) + `smb-nas` (NAS at 192.168.178.102)
- **Pi-hole** MUST use `local-path` — SQLite FTL is incompatible with SMB/CIFS file locking

---

## Bootstrap Tags

| Tag | Roles | Requires |
|-----|-------|---------|
| `core` | K3s + kubeconfig | — |
| `networking` | Cilium + LB-IPAM + Gateway API CRDs | `core` |
| `ingress` | cert-manager + Gateway | `networking` |
| `dns` | Pi-hole (local-path) | `ingress` |
| `gitops` | ArgoCD | `ingress` |
| `storage` | SMB/CIFS CSI driver | `networking` |
| `observability` | Prometheus + Tempo + Loki + Alloy | `networking`, `storage` |
| `security` | NeuVector | `gitops`, `storage` |
| `ai` | registry + hermes-image + litellm-proxy + hermes-agent | `networking`, `storage` |
| `ai-registry` | registry only | `networking`, `storage` |
| `ai-hermes-build` | kaniko ARM64 build (~60 min) | `ai-registry` |
| `ai-hermes-deploy` | litellm-proxy + hermes-agent | `ai-hermes-build` |
| `ai-holmes` | HolmesGPT + Holmes UI | `ai-hermes-deploy` |
| `kagent` | kagent + kmcp | `networking` + LiteLLM |

⚠ `make core` alone = broken cluster (K3s installed with `--flannel-backend=none`). Always pair with `make networking`.

---

## Workflow — adding a new service

1. `helm install` manually → verify working
2. `helm uninstall` to clean up
3. Write Ansible role in `roles/<name>/`
4. Add role to `playbooks/bootstrap.yml`
5. `ansible-playbook playbooks/bootstrap.yml` → must pass `failed=0`
6. Create skill: `skills/<name>/SKILL.md`
7. Add project to `mcp/cluster-advisor/projects.yaml` (CNCF status, license, stars, health)
8. Add stack entry to `mcp/cluster-advisor/stacks.yaml` (remove `status: planned` if it was there)
9. **Update this README** — add to services table
10. Commit + push

See `CLAUDE.md` for bootstrap role order, architectural constraints, and security rules.
See `skills/cluster-planning/SKILL.md` for the knowledge graph architecture.
