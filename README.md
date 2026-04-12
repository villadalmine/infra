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
make openclaw              # Personal AI gateway (Telegram + LiteLLM)
make security                   # NeuVector runtime security

# Or everything at once
make full
```

See `cluster-report.html` for a visual report of cluster status, stacks, and project catalog.

---

## Update: Consistent MAC/IP Management

### Summary
The `fix-mac-address` role was updated and validated to ensure:
1. Persistent and correct MAC addresses using `systemd-networkd`.
2. Static IP configurations using `netplan`.
3. Hostnames enforced persistently with `hostnamectl`.
4. Idempotence (no unnecessary changes).

### Nodes Confirmed
#### Super6C Nodes:
- **srv-super6c-02-nvme**
- **srv-super6c-03-nvme**

#### RK1 Nodes:
- **srv-rk1-nvme-01**
- **srv-rk1-nvme-02**
- **srv-rk1-nvme-03**
- **srv-rk1-nvme-04**

### Changes Applied
- Updated `fix-all-nodes.yml` to enable testing on any node using `--limit`.
- Roles validated using Ansible with configured `Cloud-Init` prevention and static IPs.
- Playbook demonstrates consistent results across nodes and is safe for cluster-wide runs.

---

For further details, see `AGENTS.md` or related Ansible playbooks.
