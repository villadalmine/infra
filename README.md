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

# Workstation DNS (run on host, not toolbox)
bash scripts/setup-dns-split.sh
```

After DNS is set up, all `*.cluster.home` URLs resolve from the workstation.

---

## Services — public (HTTPRoute)

| Service | URL | What it does | Credentials |
|---|---|---|---|
| ArgoCD | https://argocd.cluster.home | GitOps — deploy and sync K8s apps | `kubectl get secret argocd-initial-admin-secret -n argocd -o jsonpath='{.data.password}' \| base64 -d` |
| Grafana | https://grafana.cluster.home | Dashboards — metrics, alerts, traces | `admin` / `admin` |
| Pi-hole | https://pihole.cluster.home | DNS admin — blocklists, query log | `changeme` |

---

## Services — internal (no public URL)

Access via `kubectl port-forward` or from within the cluster using the internal DNS name.

| Service | Internal DNS | What it does |
|---|---|---|
| Prometheus | `kube-prometheus-stack-prometheus.monitoring:9090` | Scrapes cluster metrics |
| AlertManager | `kube-prometheus-stack-alertmanager.monitoring:9093` | Routes alerts |
| Tempo | `tempo.monitoring:3200` | Distributed tracing backend |
| Alloy | `alloy.monitoring:4317` (gRPC) / `alloy.monitoring:4318` (HTTP) | OTLP pipeline — receives traces from apps |
| Loki | `loki-gateway.monitoring:80` | Log aggregation backend |

---

## Infrastructure

| IP | What it is |
|---|---|
| `192.168.178.133` | K3s server (`srv-rk1-01`) |
| `192.168.178.105` | K3s agent (`srv-super6-cm4-emmc-01`) |
| `192.168.178.200` | Shared Gateway (all HTTP/HTTPS via Cilium LB-IPAM) |
| `192.168.178.203` | Pi-hole DNS — wildcard `*.cluster.home → .200` |

- **Domain**: `cluster.home` — wildcard TLS via cert-manager internal CA
- **Storage**: `local-path` (default StorageClass)

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
