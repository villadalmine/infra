# infra-ai/infra — Homelab K3s Infrastructure

Single-node K3s on Raspberry Pi CM4 (ARM64, Ubuntu 24.04).
Managed entirely via Ansible. **Never apply changes manually — always run the bootstrap playbook.**

## Cluster

| | |
|---|---|
| Server | `srv-rk1-01` @ `192.168.178.133`, ARM64, Ubuntu 24.04 |
| Agent | `srv-super6c-cm4-eemc-nvme` @ `192.168.178.104`, ARM64, Ubuntu 24.04 |
| Gateway IP | `192.168.178.200` (Cilium LB-IPAM, L2 announced, shared) |
| DNS | Pi-hole @ `192.168.178.203` — wildcard `*.cluster.home → .200` |
| Domain | `cluster.home` — wildcard TLS via cert-manager internal CA |
| Storage | `local-path` (default StorageClass) |

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
→ install-holmes → install-kagent
```

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
| `security` | neuvector | `services` |
| `ai` | registry + hermes-image + litellm-proxy + hermes-agent | `networking` |
| `ai-registry` | registry only | `networking` |
| `ai-hermes-build` | kaniko ARM64 build (~60 min) | `ai-registry` |
| `ai-hermes-deploy` | litellm-proxy + hermes-agent | `ai-hermes-build` |
| `kagent` | kagent + kmcp operator (multi-tenant agent platform) | `networking` + LiteLLM |

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

## Workflow for new Helm roles

1. `helm install` manually on cluster → verify working
2. `helm uninstall` to clean up
3. Write Ansible role (`roles/<name>/`) + defaults (`defaults/main.yml`)
4. Add role to `playbooks/bootstrap.yml`
5. `ansible-playbook playbooks/bootstrap.yml` → must pass `failed=0`
6. Create skill in dotfiles: `~/dotfiles/ansible/roles/opencode/files/skills/<name>/SKILL.md`
7. **Update `README.md`** — add the service to the public or internal services table
8. Commit + push both repos

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
| kagent | `oci://ghcr.io/kagent-dev/kagent/helm/kagent` | 0.8.5 | 0.8.5 (multi-arch) |

Hermes is deployed as a persistent gateway on the high-resource node with a
mounted `gateway.json` so the webhook platform stays enabled and the pod does
not exit after startup.

For long-running Ansible validation, prefer: manual/Helm proof first, then
background `ansible-playbook` with logs redirected, then foreground Ansible
only after the background run proves the change.

> **Check outdated charts:** `nova --format table find --helm`
> Cilium 1.20.0-pre.1 is pre-release — do NOT upgrade. Tempo pinned to 1.26.7 (2.0.0 buggy).

## Skills (deep technical context per component)

Located in `~/dotfiles/ansible/roles/opencode/files/skills/`
Read the relevant skill before working on a component.

| Skill | Covers |
|---|---|
| `k3s` | Server flags, kubeconfig, upgrades |
| `cilium` | CNI, LB-IPAM, L2, Gateway API, BPF |
| `gateway` | Shared Gateway, HTTPRoutes, DNS setup |
| `cert-manager` | Internal CA, wildcard cert, workstation trust |
| `argocd` | GitOps, ApplicationSets, sync waves |
| `pihole` | Wildcard DNS, Pi-hole 6 gotchas |
| `monitoring` | Prometheus, Grafana, Tempo, Alloy — full observability stack |
| `ai` | Registry + LiteLLM proxy + Hermes Agent — ARM64 AI stack |
| `kagent` | kagent + kmcp — multi-tenant AI agent platform, CRDs, RBAC, LiteLLM integration |
| `infra-ops` | node health checks, RK1 MAC fix, TuringPi 2 ops, global tolerations |
| `k8s-ask` | CLI de lenguaje natural → LiteLLM → kubectl tools |
| `k8s-debug` | Debug pods, network, nodes systematically |
| `platform-engineering` | Helm, Terraform, CI/CD patterns |

## Repo paths

- Infra: `/var/home/dalmine/Nextcloud/Repos/infra-ai/infra`
- Dotfiles (skills): `/home/dalmine/Nextcloud/Repos/dotfiles/ansible/roles/opencode/files/skills/`

## docs/

- `docs/ai-agents.md` — OpenCode + HolmesGPT architecture and integration map
- `docs/tailscale-multisite.md` — multi-site K3s + Cilium Cluster Mesh plan (not yet implemented)
