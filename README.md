# infra-ai — Homelab K3s Cluster

Multi-node K3s on Raspberry Pi CM4 (ARM64, Ubuntu 24.04), managed entirely via Ansible.
This repo is the single source of truth — never apply changes manually.

## Quick Start

```bash
# Full bootstrap (idempotent — safe to re-run)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini

# Minimal cluster (kubectl only)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags core

# Cluster with networking (internal services, ClusterIPs)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags core,networking

# Full stack (HTTPS + DNS + GitOps)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags core,networking,ingress,services

# Add observability to existing cluster
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags observability

# Add security (NeuVector) after bootstrap + password change
ansible-playbook playbooks/security.yml -i inventory/hosts.ini

# Add SMB storage (CSI driver + static/dynamic NAS tests)
make storage

# Workstation DNS (run on host, not toolbox)
bash scripts/setup-dns-split.sh
```

After DNS is set up, all `*.cluster.home` URLs resolve from the workstation.

---

## Services — public (HTTPRoute via Gateway)

| Service | URL | What it does | Credentials |
|---|---|---|---|
| ArgoCD | https://argocd.cluster.home | GitOps — deploy and sync K8s apps | `kubectl get secret argocd-initial-admin-secret -n argocd -o jsonpath='{.data.password}' \| base64 -d` |
| Grafana | https://grafana.cluster.home | Dashboards — metrics, alerts, traces, logs | `admin` / `admin` |
| Pi-hole | https://pihole.cluster.home | DNS admin — blocklists, query log | `changeme` |
| helm-dashboard | https://helm-dashboard.cluster.home | Helm release management UI (read-only) | N/A |

---

## Services — security (dedicated LoadBalancer)

| Service | URL | What it does | Credentials |
|---|---|---|---|
| NeuVector | https://192.168.178.204 | Container runtime security, vulnerability scanning | `admin` / set in UI on first login |

NeuVector is installed in two steps:
1. **bootstrap.yml** → core (controller, enforcer, manager, scanner)
2. **security.yml** → Prometheus exporter + Grafana dashboard (requires password change in UI first)

---

## Services — AI stack (namespace: ai + registry)

| Service | Access | What it does |
|---|---|---|
| Hermes Agent | `https://hermes.cluster.home` | Self-improving AI assistant (ARM64, NousResearch) |
| HolmesGPT | `https://holmes.cluster.home` | SRE assistant with Kubernetes + logs + metrics toolsets |
| LiteLLM proxy | `http://litellm-proxy.ai:4000` (cluster-internal) | OpenRouter model router — free→free2→cheap fallback |
| Docker Registry | `registry.registry:5000` (cluster-internal) | ARM64 image storage for kaniko builds (5Gi PVC) |

```bash
make ai-registry       # deploy registry (fast)
make ai-hermes-build   # kaniko ARM64 build (~60 min)
make ai-hermes-deploy  # deploy litellm-proxy + hermes-agent
make ai-holmes         # deploy HolmesGPT HTTP API
make ai               # all three in sequence
```

```bash
./scripts/holmes-chat "Using Prometheus, how many pods are using more than 500 MiB of RAM right now?"
./scripts/holmes-chat "Using Prometheus, show the top 5 pods by CPU in namespace monitoring over the last 15m."
```

The wrapper uses a local `kubectl port-forward` to the Holmes service and prints only the `analysis` field.

Hermes runs as a persistent gateway on the high-resource node and mounts its
`gateway.json` from the `hermes-gateway-config` ConfigMap into `HERMES_HOME`.
The gateway is kept alive by the webhook platform so the pod stays `1/1` under
Ansible as well as manual cluster updates.

HolmesGPT runs in the same `ai` namespace and points at LiteLLM through the
OpenAI-compatible API. It reuses the same OpenRouter secret pattern as Hermes
and exposes `https://holmes.cluster.home` through the shared Gateway.
Future work: add Holmes MCP servers once the base HTTP install is stable.

Example questions:
- `Using Prometheus, show the top 5 pods by CPU in namespace monitoring over the last 15m.`
- `Using Prometheus, check whether Grafana latency or error rate spiked in the last 30m.`

---

## Services — internal (no public URL)

Access via `kubectl port-forward` or from within the cluster.

| Service | Internal DNS | What it does |
|---|---|---|
| Prometheus | `kube-prometheus-stack-prometheus.monitoring:9090` | Scrapes cluster metrics |
| AlertManager | `kube-prometheus-stack-alertmanager.monitoring:9093` | Routes alerts |
| Tempo | `tempo.monitoring:3200` | Distributed tracing backend |
| Alloy | `alloy.monitoring:4317` (gRPC) / `alloy.monitoring:4318` (HTTP) | OTLP pipeline — receives traces from apps, scrapes pod logs → Loki |
| Loki | `loki-gateway.monitoring:80` | Log aggregation backend |

## MCPs

OpenCode uses MCPs to inspect live infrastructure and the GitHub repo without
guessing. Keep these in mind when debugging or documenting changes:

| MCP | Use |
|---|---|
| Kubernetes | Live cluster state: pods, services, events, leases, logs |
| GitHub | PRs, issues, file contents, repo metadata |
| context7 | Upstream docs for libraries and tools |

Workflow rule: prove a change manually or with Helm first, then run Ansible in
the background with logs redirected, then re-run the same Ansible command in
foreground only after the background run is clean.

---

## Infrastructure

| IP | What it is |
|---|---|
| `192.168.178.133` | K3s server (`srv-rk1-01`) |
| `192.168.178.104` | K3s agent (`srv-super6c-cm4-eemc-nvme`) |
| `192.168.178.200` | Shared Cilium Gateway (all HTTP/HTTPS via LB-IPAM) |
| `192.168.178.203` | Pi-hole DNS — wildcard `*.cluster.home → .200` |
| `192.168.178.204` | NeuVector HTTPS (dedicated LoadBalancer) |

- **Domain**: `cluster.home` — wildcard TLS via cert-manager internal CA
- **Storage**: `local-path` (default StorageClass)
- **DNS**: Pi-hole at `.203` resolves `*.cluster.home → .200` automatically
- **Important**: `.203` is the DNS VIP and `.200` is the HTTP/HTTPS Gateway.
  They are different services; `dig @192.168.178.203` tests DNS, while `curl`
  to `https://<name>.cluster.home` uses the Gateway at `.200` after DNS resolves.

---

## Bootstrap Tags

Each role is tagged for selective deployment. Tags are cumulative — include
all tags up to the layer you need.

| Tag | Roles | Requires |
|-----|-------|----------|
| `core` | k3s + kubeconfig | — |
| `networking` | gateway-api-crds + cilium + cilium-pools | `core` |
| `ingress` | cert-manager + gateway | `networking` |
| `services` | pihole + argocd + helm-dashboard | `ingress` |
| `observability` | prometheus + tempo + loki + alloy | `networking` |
| `security` | neuvector (PVC backend: smb-nas) | `services`, `storage` |
| `storage` | storage backends / PVC backends | `networking` |
| `ai` | registry + hermes-image + litellm-proxy + hermes-agent | `networking`, `storage` |
| `ai-registry` | registry only | `networking`, `storage` |
| `ai-hermes-build` | kaniko ARM64 build (~60 min) | `ai-registry` |
| `ai-hermes-deploy` | litellm-proxy + hermes-agent | `ai-hermes-build` |

Pi-hole and NeuVector use `smb-nas`. Those roles require the storage backend
role to be installed first.

Prometheus, Loki, Tempo, NeuVector, the registry, Hermes, and kaniko use `smb-nas`.
Hermes also uses a dedicated workspace PVC on `smb-nas` to avoid node ephemeral-storage pressure.
Those roles require the storage backend role to be installed first.

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

---

## Workflow — adding a new service

1. `helm install` manually on cluster → verify working
2. `helm uninstall` to clean up
3. Write Ansible role in `roles/<name>/`
4. Add role to `playbooks/bootstrap.yml`
5. `ansible-playbook playbooks/bootstrap.yml` → must pass `failed=0`
6. Create skill: `~/dotfiles/ansible/roles/opencode/files/skills/<name>/SKILL.md`
7. **Update this README** — add the service to the table above
8. Commit + push both repos

See `CLAUDE.md` for the full bootstrap role order and architectural constraints.
See `AGENTS.md` for project rules, golden rules, and troubleshooting.
