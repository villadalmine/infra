# Infra Project Rules

## Project Purpose

Ansible-managed K3s cluster on ARM64 nodes (Ubuntu 24.04), managed entirely via Ansible.
**Never apply changes manually — always run the bootstrap playbook.**

**Stack:** K3s + Cilium CNI + cert-manager + Gateway API + Pi-hole + ArgoCD + observability (Prometheus/Grafana/Tempo/Loki/Alloy) + AI stack (LiteLLM + Hermes + HolmesGPT + kagent)

> **MetalLB removed.** Cilium LB-IPAM + L2 Announcements replaces MetalLB entirely.

---

## Cluster Topology

### Control plane + agent nodes (Super6C CM4 — 8GB RAM each)

| Node | IP | Role |
|------|----|------|
| `srv-super6c-01-nvme` | 192.168.178.85 | K3s server + agent |
| `srv-super6c-02-nvme` | 192.168.178.86 | K3s server + agent |
| `srv-super6c-03-nvme` | 192.168.178.87 | K3s server + agent |
| `srv-super6c-04-emmc` | 192.168.178.133 | standalone (not in K3s) |

### Agent-only nodes (TuringPi 2 RK1 — 32GB RAM each, Rockchip RK3588S)

| Node | IP | MAC (fixed) |
|------|----|-------------|
| `srv-rk1-nvme-01` | 192.168.178.30 | `ce:16:3f:8e:19:cf` |
| `srv-rk1-nvme-02` | 192.168.178.48 | `86:df:be:ad:dd:97` |
| `srv-rk1-nvme-03` | 192.168.178.51 | `72:1d:5a:f8:35:48` |
| `srv-rk1-nvme-04` | 192.168.178.54 | `8e:f8:04:7e:96:92` |

### Inventory groups

```ini
[server_nodes]   srv-super6c-01-nvme, srv-super6c-02-nvme, srv-super6c-03-nvme
[agent_nodes]    server_nodes + all rk1_nodes
[rk1_nodes]      srv-rk1-nvme-01..04   ← used by fix-mac playbook
[k3s_nodes]      server_nodes + agent_nodes
```

### Shared infrastructure

| IP | What it is |
|----|------------|
| `192.168.178.1` | Router / default gateway |
| `192.168.178.102` | LG N2R1 NAS (SMB1, share: `//192.168.178.102/service`) |
| `192.168.178.200` | Cilium shared Gateway (HTTP/HTTPS) |
| `192.168.178.203` | Pi-hole DNS (TCP+UDP port 53) |
| `192.168.178.204` | NeuVector dedicated LoadBalancer |

---

## Repository Layout

```text
infra/
├── AGENTS.md                          ← project rules (this file)
├── CLAUDE.md                          ← AI assistant instructions + component versions
├── README.md                          ← services table + quick start
├── Makefile                           ← shortcut targets (make help)
├── ansible.cfg
├── inventory/
│   └── hosts.ini
├── playbooks/
│   ├── bootstrap.yml                  ← full cluster bootstrap (order matters)
│   ├── healthcheck.yml                ← node identity + resource stats via Ansible
│   ├── fix-mac.yml                    ← fix MAC address rotation on RK1 nodes
│   ├── security.yml                   ← NeuVector monitor (after password change)
│   └── uninstall.yml                  ← full teardown + reboot
├── scripts/
│   ├── node-identity-check            ← fast: hostname/IP vs inventory table
│   ├── node-stats                     ← fast: CPU/RAM/temp table
│   ├── setup-dns-split.sh             ← workstation DNS split (Fedora Silverblue)
│   └── holmes-chat                    ← HolmesGPT CLI wrapper
└── roles/
    ├── install-k3s/                   ← K3s server/agent install + inotify sysctl
    ├── get-kubeconfig/                ← fetch kubeconfig to ~/.kube/config
    ├── install-gateway-api-crds/      ← Gateway API CRDs (standard channel)
    ├── install-cilium/                ← CNI + kube-proxy replacement + L2 + Gateway API
    ├── install-cilium-pools/          ← CiliumLoadBalancerIPPool + L2AnnouncementPolicy
    ├── install-cert-manager/          ← internal CA + wildcard *.cluster.home cert
    ├── install-gateway/               ← shared Cilium Gateway at .200
    ├── install-pihole/                ← DNS at .203 + wildcard *.cluster.home → .200
    ├── install-argocd/                ← ClusterIP + HTTPRoute
    ├── install-helm-dashboard/        ← Helm release management UI
    ├── install-kube-prometheus-stack/ ← Prometheus + Grafana + AlertManager
    ├── install-tempo/                 ← Grafana Tempo (grafana-community chart, pinned v1.26.7)
    ├── install-loki/                  ← Grafana Loki SingleBinary + MinIO
    ├── install-alloy/                 ← Grafana Alloy OTLP pipeline
    ├── install-version-checker/       ← image version tracking
    ├── install-neuvector/             ← container runtime security (LoadBalancer at .204)
    ├── install-neuvector-monitor/     ← NeuVector Prometheus exporter (security.yml)
    ├── install-cifs-nas/              ← CSI SMB driver + StorageClasses (smb-nas, smb-nas-pg)
    ├── install-registry/              ← Docker registry:2 for ARM64 builds
    ├── install-hermes-agent-image/    ← Kaniko ARM64 build job
    ├── install-hermes-agent/          ← Hermes Agent + LiteLLM proxy
    ├── install-holmes/                ← HolmesGPT SRE assistant
    ├── install-kagent/                ← kagent + kmcp AI agent platform
    ├── fix-mac-address/               ← fix MAC rotation on RK1 nodes (TuringPi 2)
    ├── healthcheck-nodes/             ← identity asserts + resource stats
    └── uninstall/                     ← K3s uninstall + cleanup + reboot
```

---

## Makefile Targets

```bash
make help             # Show all targets
make core             # K3s + kubeconfig
make networking       # + Cilium, LB-IPAM, Gateway API CRDs
make ingress          # + cert-manager, Gateway
make services         # + Pi-hole, ArgoCD, helm-dashboard
make storage          # SMB CSI driver (required before services/observability PVCs)
make observability    # + Prometheus, Grafana, Tempo, Loki, Alloy
make ai               # Full AI stack (registry + hermes build + deploy)
make kagent           # kagent + kmcp AI agent platform
make security         # NeuVector core
make full             # All roles
make clean            # Full uninstall (5s countdown, destructive)

# Node health
make healthcheck      # Ansible: identity asserts + resource stats (all nodes)
make node-identity    # Fast script: hostname/IP vs inventory table
make node-stats       # Fast script: CPU/RAM/temp table

# RK1 maintenance
# ansible-playbook playbooks/fix-mac.yml -i inventory/hosts.ini --limit srv-rk1-nvme-XX
```

---

## Bootstrap Role Order (CRITICAL — order matters)

```
install-k3s → get-kubeconfig → install-gateway-api-crds → install-cilium
→ install-cilium-pools → install-cert-manager → install-gateway
→ install-pihole → install-argocd → install-helm-dashboard
→ install-kube-prometheus-stack → install-tempo → install-loki → install-alloy
→ install-version-checker → install-neuvector
→ install-cifs-nas (storage — must precede PVC-backed services)
→ install-registry → install-hermes-agent-image
→ install-litellm-proxy → install-hermes-agent
→ install-holmes → install-kagent
```

**Storage dependency**: `install-cifs-nas` must run before any role that uses `smb-nas` PVCs
(Pi-hole, NeuVector, Prometheus, Loki, Tempo, registry, Hermes, kagent postgres).

Bootstrap tags:

| Tag | Roles | Requires |
|-----|-------|----------|
| `core` | k3s + kubeconfig | — |
| `networking` | gateway-api-crds + cilium + cilium-pools | `core` |
| `ingress` | cert-manager + gateway | `networking` |
| `dns-metrics`| pihole | `ingress`, `storage` |
| `services` | argocd + helm-dashboard | `ingress` |
| `storage` | cifs-nas (SMB CSI driver) | `networking` |
| `observability` | prometheus + tempo + loki + alloy + version-checker | `networking` |
| `security` | neuvector | `services`, `storage` |
| `ai` | registry + hermes-image + litellm-proxy + hermes-agent | `networking`, `storage` |
| `kagent` | kagent + kmcp | `networking`, LiteLLM deployed |

---

## RK1 Node Maintenance (TuringPi 2)

### MAC address rotation bug

RK1 modules (Rockchip RK3588S on TuringPi 2) use locally-administered MACs
that can change between reboots, causing DHCP to assign a different IP each boot.

**Symptoms**: node expected at `.30` shows up at `.67` or not at all.

**Fix applied**: `playbooks/fix-mac.yml` — pins current MAC via:
1. `/etc/systemd/network/10-<iface>-mac.link` (systemd-networkd link file)
2. `/etc/netplan/60-static.yaml` (static IP, disables DHCP)
3. `/etc/cloud/cloud.cfg.d/99-disable-network.cfg` (prevents cloud-init overwrite)

```bash
# Run on a specific node (current IP may differ from inventory if MAC rotated)
ansible-playbook playbooks/fix-mac.yml -i inventory/hosts.ini \
  --limit srv-rk1-nvme-01 \
  -e "ansible_host=192.168.178.67 rk1_static_ip=192.168.178.30"

# Run on all RK1 nodes (when IPs are correct)
ansible-playbook playbooks/fix-mac.yml -i inventory/hosts.ini
```

### TuringPi 2 power consumption

Each RK1 module: ~10–25W under load (RK3588S TDP ~15W + NVMe + RAM).
4 modules at full load: **60–100W peak** — can exceed PSU capacity on startup.

**If nodes fail to boot with all 4 powered simultaneously**:
power on one at a time, wait 30s between each.

### Finding a node that changed IP

```bash
# Scan subnet for RK1 nodes (31GB RAM = RK1)
for ip in $(seq 1 254); do
  ssh -o ConnectTimeout=2 -o BatchMode=yes dalmine@192.168.178.$ip \
    "free -h | grep Mem" 2>/dev/null | grep -q "31G" && \
    echo "192.168.178.$ip is an RK1"
done
```

---

## Node Health Checks

```bash
# Fast scripts (no Ansible overhead)
make node-identity      # hostname / actual IP / inventory IP — all must match
make node-stats         # CPU% / RAM / temp per node

# Ansible with asserts (fails if any mismatch)
make healthcheck

# Manual one-liner
ansible -i inventory/hosts.ini all -m shell -a "hostname; hostname -I | awk '{print \$1}'"
```

---

## Golden Rules

- All roles run on `localhost` (Helm/kubectl) except `install-k3s`, `uninstall`, `fix-mac-address` (remote SSH)
- Role defaults in `roles/<role>/defaults/main.yml` — change versions there
- `install-gateway-api-crds` must run before `install-cilium`
- `install-cilium-pools` must run after `install-cilium` (CRDs only exist once operator is up)
- `install-cert-manager` must run before `install-gateway` (wildcard TLS cert must exist in gateway namespace)
- `install-cifs-nas` must run before any PVC-backed service
- **HTTP services**: always `ClusterIP` + `HTTPRoute`. Never `LoadBalancer`. Never `Ingress`.
  Exception: NeuVector (self-signed HTTPS backend) → dedicated LoadBalancer at `.204`
- Never kubectl-apply resources manually that Ansible manages — it will diverge
- **Storage dependency pattern**: every role that uses `smb-nas` (or `smb-nas-pg`) declares
  `<role>_storage_role: "install-cifs-nas"` in its defaults and calls `include_role` as its
  **first task** guarded by `when: <role>_storage_class != 'local-path' and <role>_storage_role is defined`.
  `install-cifs-nas` is idempotent — safe to call from multiple roles in the same playbook run.
  Roles: pihole, kube-prometheus-stack, loki, tempo, registry, neuvector, hermes-agent,
  hermes-agent-image, kagent, kubernetes-mcp-server-image.
- **Never commit before running the playbook and verifying it passes.** Write → deploy → fix → commit.
- `k3s_token` in `roles/install-k3s/defaults/main.yml` is a placeholder — use Ansible Vault for production
- **Built-in `metrics-server`**: K3s bundles and automatically deploys `metrics-server` in the `kube-system` namespace. **DO NOT** attempt to install `metrics-server` via Helm or Ansible, as it will cause APIService registration conflicts and fail liveness probes. Use `kubectl top nodes` out of the box.

---

## Cilium — Critical Knowledge

### rollOutPods flags (REQUIRED)

```yaml
rollOutCiliumPods: true
operator.rollOutPods: true
envoy.rollOutPods: true
```

Without these, `helm upgrade` updates the ConfigMap but pods keep running with
stale in-memory config — silent deadlock.

### externalTrafficPolicy — MUST be Cluster with L2 Announcements

`externalTrafficPolicy: Local` is incompatible with Cilium L2 Announcements.
Always use `Cluster`.

### GatewayClass status

- `Unknown` — operator/agent not yet running with new config
- `True` — fully operational

### Requesting a specific IP

```yaml
annotations:
  lbipam.cilium.io/ips: "192.168.178.203"
  lbipam.cilium.io/sharing-key: "pihole-dns"  # share TCP+UDP on same IP
```

---

## Scheduling — Global Tolerations Required

The K3s control plane node (`srv-super6c-04-emmc`) has intermittent network
instability, causing transient `unreachable:NoExecute` taints that the scheduler
sees on ALL nodes simultaneously.

**Fix**: add `tolerations: [{operator: Exists}]` to all Helm deployments.
**Do NOT** combine with `nodeSelector` pointing to a single node — this causes
DiskPressure eviction loops when all images pile onto one node.

---

## NAS Storage

| StorageClass | uid/gid | Created by | Used by |
|-------------|---------|------------|--------|
| `smb-nas` | 1000/1000 | `install-cifs-nas` | Pi-hole, Prometheus, Loki, Tempo, registry, Hermes, kaniko builds |
| `smb-nas-pg` | 999/999 | `install-kagent` (inline) | kagent PostgreSQL (postgres requires uid=999) |

NAS: LG N2R1 @ `192.168.178.102`, share `//192.168.178.102/service`, SMB1 only.

---

## AI Stack

| Service | Namespace | URL | Notes |
|---------|-----------|-----|-------|
| LiteLLM proxy | `ai` | cluster-internal only | OpenRouter fallback: free→free2→cheap |
| Hermes Agent | `ai` | `hermes.cluster.home` | ARM64 custom build via kaniko |
| HolmesGPT | `ai` | `holmes.cluster.home` | SRE assistant |
| kagent | `kagent` | `kagent.cluster.home` | AI agent platform + kmcp, multi-tenant |
| **OpenClaw** | `openclaw` | `openclaw.cluster.home` | Personal AI gateway (Telegram + LiteLLM) |

kagent uses `smb-nas-pg` StorageClass for bundled PostgreSQL.
Built-in agents need `tolerations: [{operator: Exists}]` patched onto Agent CRDs after deploy.

---

## Pi-hole — Critical Knowledge

- Use `extraEnvVars: {FTLCONF_dns_listeningMode: "ALL"}` (map format, not list)
- Do NOT use `customSettings: [local-service=false]` — causes duplicate keyword error
- liveness probe: `initialDelaySeconds: 30, failureThreshold: 18, periodSeconds: 10` (210s window for first boot)

---

## Useful Commands

```bash
# Node health
make node-identity
make node-stats

# Cluster status
kubectl get nodes -o wide
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded

# Cilium
kubectl get ciliumloadbalancerippool
kubectl get ciliuml2announcementpolicy
kubectl -n kube-system get lease | grep cilium-l2announce

# Helm releases
helm list -A

# ArgoCD password
kubectl get secret argocd-initial-admin-secret -n argocd \
  -o jsonpath='{.data.password}' | base64 -d

# SSH to a node
ssh dalmine@192.168.178.85
```

---

## Available Skills (dotfiles repo)

| Skill | Covers |
|-------|--------|
| `k3s` | server flags, kubeconfig, upgrades |
| `cilium` | CNI, LB-IPAM, L2, Gateway API, BPF |
| `gateway` | shared Gateway, HTTPRoutes, DNS |
| `cert-manager` | internal CA, wildcard cert |
| `argocd` | GitOps, ApplicationSets, sync waves |
| `pihole` | wildcard DNS, Pi-hole 6 gotchas |
| `monitoring` | Prometheus, Grafana, Tempo, Loki, Alloy |
| `storage` | CSI SMB, StorageClasses, dependency pattern (all PVC-backed roles) |
| `ai` | registry + LiteLLM + Hermes Agent + OpenClaw |
| `openclaw` | Personal AI gateway, Telegram bot, modular RBAC, LiteLLM config |
| `kagent` | AI agent platform, CRDs, RBAC, LiteLLM integration |
| `infra-ops` | node health checks, RK1 MAC fix, TuringPi 2 ops |
| `k8s-debug` | debug pods, network, nodes |
| `platform-engineering` | Helm, Terraform, CI/CD |
