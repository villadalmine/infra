# Infra Project Rules

## Project Purpose

Ansible-managed K3s cluster bootstrap on Raspberry Pi CM4 nodes.
Target: single-node cluster `srv-rk1-01` (192.168.178.133), expandable to multi-node.

**Stack:** K3s + Cilium CNI + MetalLB + cert-manager + Gateway API + Pi-hole + ArgoCD

## Repository Layout

```text
~/projects/infra/
├── AGENTS.md                        ← project rules (this file)
├── opencode.jsonc                   ← OpenCode project config (context7 local)
├── ansible.cfg
├── inventory/
│   └── hosts.ini                    ← srv-rk1-01 @ 192.168.178.133
└── playbooks/
    ├── bootstrap.yml                ← full cluster bootstrap (order matters — see below)
    └── uninstall.yml                ← full teardown
└── roles/
    ├── install-k3s/                 ← K3s server/agent install
    ├── get-kubeconfig/              ← fetch kubeconfig to ~/.kube/config
    ├── install-gateway-api-crds/    ← Gateway API CRDs v1.4.1 (standard channel)
    ├── install-cilium/              ← Cilium CNI via Helm (kube-proxy replacement + Gateway API + Ingress)
    ├── install-metallb/             ← MetalLB L2 mode, pool 192.168.178.200-210
    ├── install-cert-manager/        ← cert-manager + internal CA + wildcard *.cluster.home cert
    ├── install-gateway/             ← shared Cilium Gateway at .200 (all HTTP/HTTPS services)
    ├── install-pihole/              ← Pi-hole DNS at .203 + *.cluster.home → .200 wildcard
    ├── install-argocd/              ← ArgoCD via Helm (ClusterIP + HTTPRoute, not LoadBalancer)
    └── uninstall/                   ← K3s uninstall script + cleanup
```

## Cluster Facts

| Key | Value |
|-----|-------|
| Node | `srv-rk1-01` / `cm4-unknow-3` |
| IP | `192.168.178.133` |
| OS | Ubuntu 24.04.3 LTS (ARM64) |
| K3s | `v1.35.1+k3s1` |
| Cilium | `1.19.2` (helm chart) |
| Gateway API CRDs | `v1.4.1` (standard channel) |
| MetalLB | `0.15.3` — IP pool `192.168.178.200-210` |
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
  → install-metallb             # node must be Ready (Cilium CNI up) first
  → install-cert-manager        # wildcard *.cluster.home TLS cert must exist before gateway
  → install-gateway             # shared Gateway at .200 — needs cert-manager wildcard
  → install-pihole              # HTTPRoute needs the Gateway to exist
  → install-argocd              # ClusterIP + HTTPRoute at argocd.cluster.home
```

Do NOT reorder. Cilium's operator will error if Gateway API CRDs are missing
when `gatewayAPI.enabled=true`.

## Ansible Workflow

```bash
cd ~/projects/infra

# Full bootstrap from scratch
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini

# Resume after a specific task (skip already-done steps)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini \
  --start-at-task "Add MetalLB Helm repository"

# Full teardown
ansible-playbook playbooks/uninstall.yml -i inventory/hosts.ini
```

## Golden Rules

- All roles run on `localhost` (Helm/kubectl) except `install-k3s` and `uninstall` (remote via SSH)
- Role defaults in `roles/<role>/defaults/main.yml` — change versions there
- `install-metallb` must run before `install-argocd` (MetalLB provides the LoadBalancer IP)
- `install-gateway-api-crds` must run before `install-cilium`
- `install-cilium` must run before `install-metallb` (node must be Ready first)
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

### externalTrafficPolicy
Both `gatewayAPI` and `ingressController` create `LoadBalancer` Services via MetalLB.
- Chart default is `Cluster` → SNAT → client source IP is lost
- Use `Local` on single-node + MetalLB L2 → preserves real client IP
- Safe on single-node because all traffic lands on the same node anyway
Set in defaults: `cilium_gateway_external_traffic_policy: Local` and
`cilium_ingress_external_traffic_policy: Local`

### ingressController options
- `default: true` — handle Ingress resources without explicit `ingressClassName`
- `enforceHttps: false` — keep false until cert-manager is deployed (avoids 308 loops)
- `loadbalancerMode: shared` — one `cilium-ingress` LB Service for all Ingresses (saves IPs)

### Helm stuck states
If a `helm install/upgrade` is interrupted (Ansible timeout, Ctrl+C, network drop),
Helm leaves the release in `failed` or `pending-*` state. Subsequent runs hang or
fail immediately. **This is handled automatically** — every Helm role detects a stuck
release and purges its state secrets before attempting install/upgrade. Pods keep
running untouched.

Manual diagnosis if needed:
```bash
helm history <release> -n <namespace>

# Manual fix if automation fails for some reason:
kubectl get secrets -n <namespace> -l owner=helm,name=<release>
kubectl delete secret <secret-name> -n <namespace>
```

### GatewayClass status
After enabling `gatewayAPI.enabled=true`, `kubectl get gatewayclass` shows:
- `Unknown` — operator/agent not yet running with new config (or MetalLB not up)
- `True` — fully operational; `cilium` GatewayClass ready for Gateway resources

## Service Architecture

Every HTTP/HTTPS service: `ClusterIP` + `HTTPRoute` → shared Gateway at `.200`
Hostname: `<service>.cluster.home`
Pi-hole wildcard `*.cluster.home → .200` covers all DNS automatically.

### IP Map

| IP | Service |
|----|---------|
| `.200` | Cilium shared Gateway (`cluster-gateway` in `gateway` ns) |
| `.203` | Pi-hole DNS port 53 (TCP+UDP shared IP via MetalLB) |

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
FTL then starts without a DNS listener (only port 80 comes up). The web UI
appears healthy but `ss -tlnp` shows no port 53.

**Fix**: Remove `customSettings: [local-service=false]` entirely. Instead set
`FTLCONF_dns_listeningMode=ALL` via `extraEnvVars` — this is the Pi-hole 6 native
way to accept queries from all interfaces/networks (including external LAN clients).

### FTLCONF env vars (Pi-hole 6.x)
Pi-hole 6 uses `FTLCONF_*` environment variables instead of classic dnsmasq config
files for FTL settings. The chart sets these via `extraEnvVars`. Key ones in use:
- `FTLCONF_dns_listeningMode=ALL` — accept queries from all networks
- `FTLCONF_dns_upstreams=8.8.8.8;8.8.4.4` — upstream resolvers (set via `DNS1`/`DNS2`)
- `FTLCONF_webserver_port=80` — web UI port
- `FTLCONF_webserver_api_password=...` — admin password
- `FTLCONF_misc_etc_dnsmasq_d=true` — enable /etc/dnsmasq.d/ includes

### DNS TCP+UDP shared IP
Chart v2.30.0 creates two separate LoadBalancer services: `pihole-dns-tcp` and
`pihole-dns-udp`. Use MetalLB annotations on both to share the same IP:
```yaml
metallb.universe.tf/loadBalancerIPs: "192.168.178.203"
metallb.universe.tf/allow-shared-ip: "pihole-dns"
```

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
- `install-cilium` — Cilium 1.19.2, GatewayClass `cilium` Ready, Hubble relay enabled
- `install-metallb` — pool `192.168.178.200-210`, L2Advertisement active
- `install-cert-manager` — SelfSigned CA, wildcard `*.cluster.home` cert Ready
- `install-gateway` — `cluster-gateway` at `.200`, `PROGRAMMED=True`
- `install-pihole` — DNS port 53 listening (TCP+UDP), wildcard `*.cluster.home → .200` resolving, web UI via HTTPRoute
- `install-argocd` — ClusterIP + HTTPRoute at `argocd.cluster.home`
- Full bootstrap: `ansible-playbook playbooks/bootstrap.yml` → `failed=0`
- DNS verified: `dig argocd.cluster.home @192.168.178.203` → `192.168.178.200` ✅
- Upstream DNS verified: `dig google.com @192.168.178.203` → resolves ✅

### Broken / Not Yet Verified ❌
- **HTTP routing through Gateway hangs** — `curl http://argocd.cluster.home` (resolves
  to `.200` correctly) gets no response. TCP connect to `192.168.178.200:80` hangs.
  Gateway is `PROGRAMMED=True`, HTTPRoutes are `Accepted`, Cilium pod is Running.
  Hubble relay was just enabled this session — use `hubble observe --verdict DROPPED`
  on next session to find where packets are dropped.
  Possible causes to investigate:
  1. `externalTrafficPolicy: Local` on the gateway LB service + MetalLB L2 — packets
     may arrive at the node but not reach the envoy pod if health check is failing
  2. Cilium envoy not ready / CiliumEnvoyConfig misconfigured after Hubble enable
  3. iptables/BPF rule missing — check `cilium bpf lb list` on the node
- **Mac DNS not configured** — `networksetup -setdnsservers Wi-Fi 192.168.178.203 8.8.8.8`
  not yet run (dotfiles ansible not applied this session)
- **Pi-hole web UI not verified** — HTTPRoute exists, but routing is blocked (same issue above)
- **ArgoCD web UI not verified** — same routing issue

### Next Session Checklist
1. `kubectl port-forward -n kube-system svc/hubble-relay 4245:80 &`
2. `hubble observe --verdict DROPPED --follow` while doing `curl http://argocd.cluster.home`
3. Fix whatever Hubble shows is dropping packets
4. Verify: `curl -si http://argocd.cluster.home` and `curl -si http://pihole.cluster.home`
5. Run dotfiles ansible: `cd ~/dotfiles/ansible && ansible-playbook playbook.yml`
6. Commit both repos

## Useful Commands

```bash
# Cluster status
kubectl get nodes -o wide
kubectl get pods -A

# Cilium health
kubectl get pods -n kube-system -l k8s-app=cilium
kubectl get gatewayclass
kubectl get crd | grep -E "gateway|cilium"

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
- `cilium` — CNI operations, upgrades, BPF/kube-proxy replacement, Gateway API, troubleshooting
- `metallb` — L2/BGP modes, IP pools, L2Advertisement, troubleshooting
- `argocd` — ApplicationSets, sync waves, app management, GitOps patterns
- `k8s-debug` — systematic pod/network/node debugging (global skill)
- `platform-engineering` — Helm, Terraform, CI/CD best practices (global skill)
