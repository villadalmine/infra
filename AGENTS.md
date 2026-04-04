# Infra Project Rules

## Project Purpose

Ansible-managed K3s cluster bootstrap on Raspberry Pi CM4 nodes.
Target: multi-node cluster — `srv-rk1-01` (server, 192.168.178.133) + `srv-super6-cm4-emmc-01` (agent, 192.168.178.105).

**Stack:** K3s + Cilium CNI + cert-manager + Gateway API + Pi-hole + ArgoCD

> **MetalLB removed.** Cilium LB-IPAM + L2 Announcements replaces MetalLB entirely.
> No external load balancer needed.

## Repository Layout

```text
~/projects/infra/
├── AGENTS.md                        ← project rules (this file)
├── opencode.jsonc                   ← OpenCode project config (context7 local)
├── ansible.cfg
├── inventory/
│   └── hosts.ini                    ← srv-rk1-01 @ .133, srv-super6-cm4-emmc-01 @ .105
└── playbooks/
    ├── bootstrap.yml                ← full cluster bootstrap (order matters — see below)
    └── uninstall.yml                ← full teardown
└── roles/
    ├── install-k3s/                 ← K3s server/agent install
    ├── get-kubeconfig/              ← fetch kubeconfig to ~/.kube/config
    ├── install-gateway-api-crds/    ← Gateway API CRDs v1.4.1 (standard channel)
    ├── install-cilium/              ← Cilium CNI via Helm (kube-proxy replacement + Gateway API + L2 announcements)
    ├── install-cilium-pools/        ← CiliumLoadBalancerIPPool + CiliumL2AnnouncementPolicy
    ├── install-cert-manager/        ← cert-manager + internal CA + wildcard *.cluster.home cert
    ├── install-gateway/             ← shared Cilium Gateway at .200 (all HTTP/HTTPS services)
    ├── install-pihole/              ← Pi-hole DNS at .203 + *.cluster.home → .200 wildcard
    ├── install-argocd/              ← ArgoCD via Helm (ClusterIP + HTTPRoute, not LoadBalancer)
    ├── install-kube-prometheus-stack/ ← Prometheus + Grafana + AlertManager
    ├── install-tempo/               ← Grafana Tempo distributed tracing
    ├── install-alloy/               ← Grafana Alloy OTLP pipeline
    └── uninstall/                   ← K3s uninstall script + cleanup
```

## Cluster Facts

| Key | Value |
|-----|-------|
| Node (server) | `srv-rk1-01` / `cm4-unknow-3` |
| Node (agent) | `srv-super6-cm4-emmc-01` |
| IP (server) | `192.168.178.133` |
| IP (agent) | `192.168.178.105` |
| OS | Ubuntu 24.04.3 LTS (ARM64) |
| K3s | `v1.35.1+k3s1` |
| Cilium | `1.19.2` (helm chart) |
| Gateway API CRDs | `v1.4.1` (standard channel) |
| LB-IPAM | Cilium native — pool `192.168.178.200-210` (`CiliumLoadBalancerIPPool`) |
| ArgoCD | `9.4.17` (chart) / `v3.3.6` (app) — `argocd.cluster.home` via Gateway |
| Pi-hole | chart `2.30.0` (mojo2600) — DNS at `.203`, web UI at `pihole.cluster.home` |
| Gateway | shared Cilium Gateway at `192.168.178.200` (all HTTP/HTTPS services) |
| SSH | `dalmine@192.168.178.133`, key `~/.ssh/id_ed25519` |
| kubeconfig | `~/.kube/config` (fetched by `get-kubeconfig` role) |

## K3s Disabled Components

`servicelb`, `traefik`, `metrics-server`, `local-storage`, `flannel` (CNI),
`kube-proxy`, `network-policy`, `cloud-controller`

## Bootstrap Role Order (CRITICAL)

```
install-k3s              (remote SSH)
  → get-kubeconfig
  → install-gateway-api-crds   # CRDs must exist before Cilium enables gatewayAPI
  → install-cilium              # wait:false + kubectl rollout status
  → install-cilium-pools        # CiliumLoadBalancerIPPool + CiliumL2AnnouncementPolicy
                                # Must run AFTER install-cilium (CRDs registered by operator)
  → install-cert-manager        # wildcard *.cluster.home TLS cert must exist before gateway
  → install-gateway             # shared Gateway at .200 — needs cert-manager wildcard
  → install-pihole              # HTTPRoute needs the Gateway to exist
  → install-argocd              # ClusterIP + HTTPRoute at argocd.cluster.home
  → install-kube-prometheus-stack ← Prometheus + Grafana + AlertManager
  → install-tempo               ← Grafana Tempo distributed tracing
  → install-alloy               ← Grafana Alloy OTLP pipeline
```

Do NOT reorder. Cilium's operator will error if Gateway API CRDs are missing
when `gatewayAPI.enabled=true`. `install-cilium-pools` CRDs only exist after
the Cilium operator is running.

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

## Ansible Workflow

```bash
cd ~/projects/infra

# Full bootstrap from scratch
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini

# Selective bootstrap with tags (see Bootstrap Tags section above)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags core,networking

# Resume from a specific role (e.g. after tweaking IP pool)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini \
  --start-at-task "Create CiliumLoadBalancerIPPool"

# Full teardown
ansible-playbook playbooks/uninstall.yml -i inventory/hosts.ini
```

## Golden Rules

- All roles run on `localhost` (Helm/kubectl) except `install-k3s` and `uninstall` (remote via SSH)
- Role defaults in `roles/<role>/defaults/main.yml` — change versions there
- `install-cilium-pools` must run after `install-cilium` (CRDs only exist once operator is up)
- `install-gateway-api-crds` must run before `install-cilium`
- `install-cert-manager` must run before `install-gateway` (wildcard TLS cert must exist in gateway namespace)
- `install-gateway` must run before `install-pihole` (HTTPRoute needs the Gateway to exist)
- `k3s_token` in `roles/install-k3s/defaults/main.yml` is a placeholder — use Ansible Vault for production
- Never kubectl-apply resources manually that Ansible manages — it will diverge
- Always `git pull --rebase` before pushing — another agent may have pushed
- **Never commit before running the playbook and verifying it passes.** Write → deploy → fix → commit.
- **Playbooks must be OS-agnostic.** Never hardcode OS-specific tasks that `fatal` on other platforms.
  OS-specific tasks must use `when: ansible_facts['system'] == 'Darwin'` (or Linux/Windows) and
  must never block the play on other OSes. A missing `when` guard that causes a fatal on Linux
  or Windows is a bug.
- **Zero manual steps.** If `ansible-playbook playbooks/bootstrap.yml` requires any manual
  intervention before, during, or after the run, it is a bug. Fix it in Ansible. No exceptions.
  This includes Helm stuck states, kubeconfig setup, DNS config, CA cert placement — everything.

## Cilium — Critical Knowledge

### rollOutPods flags (REQUIRED)
Always set these three values in the Cilium Helm chart:
```yaml
rollOutCiliumPods: true
operator.rollOutPods: true
envoy.rollOutPods: true
```
These inject a hash of `cilium-config` ConfigMap into pod template annotations.
Without them, `helm upgrade` updates the ConfigMap but pods keep running with
stale in-memory config — **silent deadlock**: agent waits forever for CRDs that
the stale operator never registers.

### Envoy CRDs (ciliumenvoyconfigs, ciliumclusterwideenvoyconfigs)
Registered by the operator ONLY when `enable-envoy-config=true`, which is set
automatically when `gatewayAPI.enabled=true` or `ingressController.enabled=true`.
If the operator is stale (started before those flags), it won't register them and
the agent hangs. The `rollOutPods` flags above prevent this entirely.

### ztunnel — NOT for Cilium
`ztunnel` is the Istio Ambient Mesh L4 proxy. It is NOT part of Cilium.
Cilium has its own service mesh via `cilium-envoy` + `CiliumEnvoyConfig`.
Never install ztunnel alongside Cilium.

### externalTrafficPolicy — MUST be Cluster with L2 Announcements
`externalTrafficPolicy: Local` is **incompatible** with Cilium L2 Announcements.
L2 Announcements may announce a VIP on nodes that have no local pod, and with
`Local` policy traffic arriving at such nodes is dropped silently.
Always use `Cluster` — Cilium handles load balancing entirely in BPF.

### Helm stuck states
If a `helm install/upgrade` is interrupted (Ansible timeout, Ctrl+C, network drop),
Helm leaves the release in `failed` or `pending-*` state. Subsequent runs hang or
fail immediately. **This is handled automatically** — every Helm role detects a stuck
release and purges its state secrets before attempting install/upgrade.

Manual diagnosis:
```bash
helm history cilium -n kube-system
kubectl get secrets -n kube-system -l owner=helm,name=cilium
kubectl delete secret <stuck-secret-name> -n kube-system
```

### GatewayClass status
After enabling `gatewayAPI.enabled=true`:
- `Unknown` — operator/agent not yet running with new config
- `True` — fully operational; `cilium` GatewayClass ready for Gateway resources

## Cilium LB-IPAM + L2 Announcements (replaces MetalLB)

MetalLB was removed. Cilium handles both IP allocation and ARP natively.

### Why Cilium over MetalLB
MetalLB requires `kubernetes.io/service-name` on EndpointSlices to elect a node
for L2 announcement. Cilium's Gateway API creates EndpointSlices without that
label — causing MetalLB to never respond to ARP for the Gateway VIP. This is
a fundamental incompatibility with no clean fix.

### How it works
- **LB-IPAM**: always enabled, dormant until first `CiliumLoadBalancerIPPool` is created
- **L2 Announcements**: responds to ARP for LoadBalancer IPs via leader election (Kubernetes Leases)
- One node holds the lease per service and responds to ARP with its MAC address
- Leader election via `cilium-l2announce-<namespace>-<service>` leases in `kube-system`

### Requesting a specific IP
Use the `lbipam.cilium.io/ips` annotation (replaces `metallb.universe.tf/loadBalancerIPs`):
```yaml
annotations:
  lbipam.cilium.io/ips: "192.168.178.203"
```

### Sharing an IP across services (e.g. TCP+UDP on same port 53)
Use `lbipam.cilium.io/sharing-key` (replaces `metallb.universe.tf/allow-shared-ip`):
```yaml
annotations:
  lbipam.cilium.io/ips: "192.168.178.203"
  lbipam.cilium.io/sharing-key: "pihole-dns"
```

### Health checks
```bash
# IP pool status
kubectl get ciliumloadbalancerippool

# L2 policy
kubectl get ciliuml2announcementpolicy

# Active leases (which node announces each service)
kubectl -n kube-system get lease | grep cilium-l2announce

# Verify ARP from Mac
arp -n 192.168.178.200
arp -n 192.168.178.203
```

### k8sClientRateLimit sizing
L2 Announcements generate continuous lease-renewal API traffic.
Formula: `QPS = #services * (1 / leaseRenewDeadline)`
Currently: 5 services × (1/5s) = 1 QPS → set to 10 QPS / 20 burst (headroom).

## Service Architecture

Every HTTP/HTTPS service: `ClusterIP` + `HTTPRoute` → shared Gateway at `.200`
Hostname: `<service>.cluster.home`
Pi-hole wildcard `*.cluster.home → .200` covers all DNS automatically.

### IP Map

| IP | Service |
|----|---------|
| `.200` | Cilium shared Gateway (`cluster-gateway` in `gateway` ns) |
| `.203` | Pi-hole DNS port 53 (TCP+UDP shared IP via Cilium LB-IPAM) |

Exceptions: Pi-hole DNS port 53 gets its own LoadBalancer at `.203`.
Port 53 cannot share the HTTP Gateway.

## Pi-hole 6.x — Critical Knowledge

### DNS not listening on port 53 (solved)
Pi-hole 6.x FTL defaults to `dns.listeningMode=LOCAL` which internally maps to
dnsmasq's `local-service` directive. If you also add `local-service=false` via
`dnsmasq.customSettings` in the chart values, dnsmasq sees a **duplicate keyword**
and FTL exits with:
```
CRIT: Error in dnsmasq configuration: illegal repeated keyword at line 3 of /etc/dnsmasq.d/02-custom.conf
```
FTL then starts without a DNS listener (only port 80 comes up).

**Fix**: Remove `customSettings: [local-service=false]` entirely. Instead set
`FTLCONF_dns_listeningMode=ALL` via `extraEnvVars`.

### FTLCONF env vars (Pi-hole 6.x)
Pi-hole 6 uses `FTLCONF_*` environment variables for FTL settings:
- `FTLCONF_dns_listeningMode=ALL` — accept queries from all networks
- `FTLCONF_dns_upstreams=8.8.8.8;8.8.4.4` — upstream resolvers (set via `DNS1`/`DNS2`)

### DNS TCP+UDP shared IP (Cilium LB-IPAM)
Chart v2.30.0 creates two separate LoadBalancer services: `pihole-dns-tcp` and
`pihole-dns-udp`. Share the same IP via Cilium annotations:
```yaml
lbipam.cilium.io/ips: "192.168.178.203"
lbipam.cilium.io/sharing-key: "pihole-dns"
```
**Note:** `externalTrafficPolicy: Cluster` required (L2 Announcements incompatible with Local).

### Wildcard DNS
`dnsmasq.customDnsEntries: [address=/cluster.home/192.168.178.200]` covers all
`*.cluster.home` and `cluster.home` itself. No per-service DNS entries needed.

## Hubble Observability

Hubble relay is enabled in Cilium (`hubble.relay.enabled=true`). To use the CLI:
```bash
# Port-forward relay to localhost
kubectl port-forward -n kube-system svc/hubble-relay 4245:80 &

# Observe all flows
hubble observe --follow

# Observe traffic to/from gateway
hubble observe --to-label "io.cilium.k8s.namespace=gateway" --follow

# Observe dropped packets only
hubble observe --verdict DROPPED --follow
```

## Current Status

### Working ✅
- `install-k3s` — K3s v1.35.1, node Ready, worker label applied
- `install-gateway-api-crds` — Gateway API CRDs v1.4.1 established
- `install-cilium` — Cilium 1.19.2, GatewayClass `cilium` Ready, Hubble relay enabled, L2 Announcements enabled
- `install-cilium-pools` — `CiliumLoadBalancerIPPool` + `CiliumL2AnnouncementPolicy` active
- `install-cert-manager` — SelfSigned CA, wildcard `*.cluster.home` cert Ready
- `install-gateway` — `cluster-gateway` at `.200`, `PROGRAMMED=True`
- `install-pihole` — DNS port 53 listening (TCP+UDP at `.203`), wildcard `*.cluster.home → .200` resolving, web UI via HTTPRoute
- `install-argocd` — ClusterIP + HTTPRoute at `argocd.cluster.home`
- Full bootstrap: `ansible-playbook playbooks/bootstrap.yml` → `failed=0`
- ARP verified: `.200` and `.203` both resolve to `2c:cf:67:27:1e:24` ✅
- HTTP verified: `curl http://argocd.cluster.home` → `200 OK` ✅
- HTTP verified: `curl http://pihole.cluster.home/admin` → `308` (correct Pi-hole redirect) ✅
- DNS verified: `dig argocd.cluster.home @192.168.178.203` → `192.168.178.200` ✅
- Mac Wi-Fi DNS configured to `.203` ✅

### Next Steps
- HTTPS routing (`https://argocd.cluster.home`) — cert-manager wildcard cert is ready, needs TLS listener on Gateway
- ArgoCD app-of-apps / GitOps handover via handover.yml

## Useful Commands

```bash
# Cluster status
kubectl get nodes -o wide
kubectl get pods -A

# Cilium health
kubectl get pods -n kube-system -l k8s-app=cilium
kubectl get gatewayclass
kubectl get crd | grep -E "gateway|cilium"

# LB-IPAM + L2 announcements
kubectl get ciliumloadbalancerippool
kubectl get ciliuml2announcementpolicy
kubectl -n kube-system get lease | grep cilium-l2announce

# Helm release state
helm history cilium -n kube-system
helm get values cilium -n kube-system

# ArgoCD admin password
kubectl get secret argocd-initial-admin-secret -n argocd \
  -o jsonpath='{.data.password}' | base64 -d

# SSH to node
ssh dalmine@192.168.178.133

# K3s logs on node
ssh dalmine@192.168.178.133 'sudo journalctl -u k3s -f'
```

## Available OpenCode Skills

Load these when working on the relevant component:

- `k3s` — K3s server flags, service management, node operations
- `cilium` — CNI operations, upgrades, BPF/kube-proxy replacement, Gateway API, LB-IPAM, L2 Announcements
- `argocd` — ApplicationSets, sync waves, app management, GitOps patterns
- `k8s-debug` — systematic pod/network/node debugging (global skill)
- `platform-engineering` — Helm, Terraform, CI/CD best practices (global skill)
