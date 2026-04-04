# Session Notes — Last updated: 2026-04-04

## Current cluster state

- Node: `srv-rk1-01` at `192.168.178.133`, ARM64, Ubuntu 24.04
- K3s: `v1.35.1+k3s1`
- Cilium: `1.19.2`
- ArgoCD admin password: `79YpdVIZByWW75dy`
- All services verified working: HTTP ✅ HTTPS ✅ DNS ✅ ARP ✅
- Last bootstrap run: `failed=0`, `changed=3`

## IP map

| IP | Service |
|----|---------|
| `.200` | Cilium shared Gateway (`cluster-gateway`) |
| `.203` | Pi-hole DNS port 53 (TCP+UDP) |

## Bootstrap role order (CRITICAL)

```
install-k3s → get-kubeconfig → install-gateway-api-crds → install-cilium
→ install-cilium-pools → install-cert-manager → install-gateway
→ install-pihole → install-argocd
```

## Run the playbook

```bash
cd ~/projects/infra
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini
```

## What was done (session 2026-04-04)

- Migrated MetalLB → Cilium LB-IPAM + L2 Announcements
- Fixed stale MetalLB annotation in `install-gateway` role
- Verified HTTPS fully working end-to-end
- Updated skills: cilium, metallb, gateway
- Created `docs/tailscale-multisite.md` (full Phase 1 + Phase 2 plan)
- Created `docs/ai-agents.md` (HolmesGPT + OpenCode roles)
- All changes committed and pushed to both repos

## Pending next steps (pick up here)

### 1. Deploy Prometheus + kube-prometheus-stack
- **Why:** prerequisite for HolmesGPT alert integration
- **What:** new Ansible role `install-prometheus`, Helm chart `kube-prometheus-stack`
- Expose via HTTPRoute at `prometheus.cluster.home` and `grafana.cluster.home`
- See `docs/ai-agents.md`

### 2. Deploy HolmesGPT
- **Why:** AI-powered alert triage — reads Prometheus alerts, explains root cause in plain English
- **What:** ArgoCD Application (in-cluster Operator mode), needs Prometheus running first
- See `docs/ai-agents.md`

### 3. ArgoCD app-of-apps / GitOps handover
- **Why:** right now everything is Ansible-managed imperatively; goal is ArgoCD owning all app deployments
- **What:** create `handover.yml` playbook that registers all current services as ArgoCD Applications pointing at the infra repo
- Note: K3s + base Cilium/cert-manager bootstrap stays Ansible; everything above that moves to ArgoCD

### 4. Tailscale multi-site — Phase 1
- **Why:** extend cluster access across two home networks
- **What:** deploy Tailscale subnet router on each site, test `kubectl` across sites
- See `docs/tailscale-multisite.md`

### 5. Tailscale multi-site — Phase 2 (Cilium Cluster Mesh)
- **Why:** true multi-cluster service discovery and failover
- **What:** Cilium Cluster Mesh + shared service CIDRs — do Phase 1 first
- See `docs/tailscale-multisite.md`
