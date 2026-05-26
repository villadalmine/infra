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
make observability              # Prometheus + Grafana + Tempo + Loki + Alloy
make ai && make ai-holmes && make kagent   # Full AI stack
make leloir-all            # Leloir agentic incident analysis (Postgres + control plane)
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

---

## Leloir — Agentic Incident Analysis

[Leloir](https://github.com/villadalmine/leloir) is the agentic incident analysis platform running on this cluster.
It receives Alertmanager webhooks, routes them to HolmesGPT, and streams structured analysis to a React UI.

**Deploy (first time):**
```bash
make leloir-all   # registry → kaniko build (~5 min) → Postgres + controlplane
```

**Update after code change:**
```bash
make leloir-build   # rebuild image from latest main
make leloir         # re-deploy (rolling update)
```

**Access:** `https://leloir.cluster.home`
**Alertmanager webhook:** `http://leloir-controlplane.leloir.svc.cluster.local/webhook/alertmanager`
**API:** `https://leloir.cluster.home/api/v1/`

**Credentials:** `roles/install-leloir/defaults/secrets.yml` (gitignored) — override `leloir_db_password`.

---

## NAS Admin Panel

Admin panel for browsing and cleaning up K8s PersistentVolumes/Claims backed by the LG N2R1 NAS (SMBv1).
Built with FastAPI + HTMX + Tailwind CDN, deployed as a Helm chart.

**Deploy (first time):**
```bash
make nas-admin-all   # kaniko build (~2 min) → Helm deploy
```

**Update after code change:**
```bash
make nas-admin-build   # rebuild image
make nas-admin         # helm upgrade (rolling restart)
```

**Config-only change (auth, password, hostname) — no rebuild:**
```bash
# edit roles/install-nas-admin/defaults/main.yml or secrets.yml
make nas-admin
```

**Access:** `https://nas-admin.cluster.home`
**Credentials:** `roles/install-nas-admin/defaults/secrets.yml` (gitignored) — override `nas_admin_password`.
Auth mode: `basic` (default) or `oidc` (GitHub OAuth via oauth2-proxy sidecar) — set `nas_admin_auth_mode`.

**Features:** PV list (SMB-filtered, orphan detection + **delete orphaned PVs**), PVC list (all namespaces, pod owner, delete with confirm), NAS file browser (mounts NAS share ReadOnly via dedicated PVC in `storage` ns).
