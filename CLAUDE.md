# infra-ai/infra — Homelab K3s Infrastructure

Single-node K3s on Raspberry Pi CM4 (ARM64, Ubuntu 24.04).
Managed entirely via Ansible. **Never apply changes manually — always run the bootstrap playbook.**

## Cluster

| | |
|---|---|
| Node | `srv-rk1-01` @ `192.168.178.133`, ARM64, Ubuntu 24.04 |
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
```

## Bootstrap role order (CRITICAL — order matters)

```
install-k3s → get-kubeconfig → install-gateway-api-crds → install-cilium
→ install-cilium-pools → install-cert-manager → install-gateway
→ install-pihole → install-argocd
→ install-kube-prometheus-stack → install-tempo → install-alloy
```

## Bootstrap Tags

Each role is tagged for selective deployment. Tags are cumulative — include
all tags up to the layer you need.

| Tag | Roles | Requires |
|-----|-------|----------|
| `core` | k3s + kubeconfig | — |
| `networking` | gateway-api-crds + cilium + cilium-pools | `core` |
| `ingress` | cert-manager + gateway | `networking` |
| `services` | pihole + argocd | `ingress` |
| `observability` | prometheus + tempo + alloy | `networking` |

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
7. Commit + push both repos

## Installed component versions

| Component | Helm Chart | Version | App Version |
|---|---|---|---|
| K3s | — | v1.35.1+k3s1 | — |
| Cilium | `cilium/cilium` | 1.19.2 | 1.19.2 |
| kube-prometheus-stack | `prometheus-community/kube-prometheus-stack` | 82.17.0 | v0.89.0 |
| Tempo | `grafana-community/tempo` | 1.26.7 | 2.10.1 |
| Alloy | `grafana/alloy` | 1.7.0 | v1.15.0 |

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
| `k8s-debug` | Debug pods, network, nodes systematically |
| `platform-engineering` | Helm, Terraform, CI/CD patterns |

## Repo paths

- Infra: `/var/home/dalmine/Nextcloud/Repos/infra-ai/infra`
- Dotfiles (skills): `/home/dalmine/Nextcloud/Repos/dotfiles/ansible/roles/opencode/files/skills/`

## docs/

- `docs/ai-agents.md` — OpenCode + HolmesGPT architecture and integration map
- `docs/tailscale-multisite.md` — multi-site K3s + Cilium Cluster Mesh plan (not yet implemented)
