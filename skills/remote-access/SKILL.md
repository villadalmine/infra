# Remote Access to Homelab Cluster

Guide for accessing the K3s cluster from a remote laptop (e.g. demo, travel).
Three options, ordered by recommendation.

---

## Quick decision matrix

| | Tailscale subnet router | HAProxy + MikroTik | SSH tunnel |
|---|---|---|---|
| Setup time | ~20 min | ~30 min | ~5 min |
| Works on any network | ✅ | ✅ (needs port open) | ✅ (needs port open) |
| Full `*.cluster.home` in browser | ✅ | ✅ | partial (manual `-L`) |
| `kubectl` works unchanged | ✅ | ✅ | needs kubeconfig tweak |
| Exposes cluster to internet | ❌ no | ✅ yes (port 443) | ❌ no |
| Single point of failure | Tailscale DERP relay | MikroTik + Mac Mini | SSH connection |
| Needs extra software on cluster | tailscale pkg | nothing new | nothing new |

---

## Option A — Tailscale subnet router (recommended)

Install Tailscale on **one cluster node** (not the master). That node advertises
`192.168.178.0/24` as a subnet route. From the laptop, all cluster IPs are
reachable over an encrypted WireGuard tunnel.

```
laptop (Tailscale) ──WireGuard──► [tailscale node] ──L2──► 192.168.178.0/24
                                                               ├─ .120  kubectl API
                                                               ├─ .200  Gateway (all URLs)
                                                               └─ .203  Pi-hole DNS
```

### Which node to use

**DO NOT use `srv-super6c-01-nvme` (.120)** — it runs K3s control plane + etcd +
all system pods. It's already stressed.

Good choices (pick one):

| Node | IP | Reason |
|---|---|---|
| `srv-super6c-02-nvme` | .121 | CM4 node, **not joined to K3s** → nearly idle. Best option. |
| `srv-pi-rack1` | .65 | Pi 4, light K3s agent workload. Reliable. |
| `srv-pi-rack2b` | .130 | Same as above. |

### Ansible deploy (automated)

```bash
# 1. Uncomment your chosen node in inventory/hosts.ini under [tailscale_nodes]
# 2. Get a pre-auth key: https://login.tailscale.com/admin/settings/keys
#    Settings: Reusable=no, Expiry=90d, Ephemeral=no
# 3. Put the key in roles/install-tailscale/defaults/secrets.yml
# 4. Run ONLY the tailscale tag:
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags tailscale
```

### After the role runs — two manual steps

```
1. Tailscale admin console → Machines → find your node → "..." → Edit route settings
   → enable the 192.168.178.0/24 subnet route (must approve once, persists forever)

2. Laptop:
   sudo tailscale up --accept-routes
```

### Laptop DNS for `*.cluster.home`

The laptop needs to resolve `*.cluster.home` → `192.168.178.200`.
After `tailscale up --accept-routes`, `.200` is reachable, so:

```bash
# Fedora Silverblue — run on HOST (not toolbox)
bash scripts/setup-dns-split.sh
# adds cluster.home → 192.168.178.203 (Pi-hole) to systemd-resolved
```

Or manually:
```bash
sudo mkdir -p /etc/systemd/resolved.conf.d
sudo tee /etc/systemd/resolved.conf.d/cluster-home.conf <<EOF
[Resolve]
DNS=192.168.178.203
Domains=~cluster.home
EOF
sudo systemctl restart systemd-resolved
```

### Verify end-to-end

```bash
tailscale status              # laptop — should show cluster node as online
ping 192.168.178.200          # gateway reachable
curl -sk https://grafana.cluster.home/api/health   # Grafana up
kubectl get nodes             # kubeconfig unchanged — works directly
```

---

## Option B — HAProxy SNI + MikroTik port forward

You already have HAProxy on the Mac Mini and MikroTik. Open port 443 externally
and route SNI traffic to the cluster gateway.

```
internet:443 → MikroTik DST-NAT → Mac Mini:443 → HAProxy SNI → 192.168.178.200:443
```

### MikroTik rule (WebFig / Winbox)

```
/ip firewall nat add chain=dstnat protocol=tcp dst-port=443 \
  action=dst-nat to-addresses=<mac-mini-ip> to-ports=443 \
  comment="cluster HTTPS"
```

### HAProxy config snippet (`/etc/haproxy/haproxy.cfg`)

```haproxy
frontend cluster_https
    bind *:443
    mode tcp
    option tcplog
    tcp-request inspect-delay 5s
    tcp-request content accept if { req_ssl_hello_type 1 }
    default_backend cluster_gateway

backend cluster_gateway
    mode tcp
    server gateway 192.168.178.200:443 check
```

### Laptop setup

Add DNS record pointing `*.cluster.home` to your home public IP.
Options:
- Edit `/etc/hosts` on laptop with each service (tedious)
- Use a public DNS zone with a wildcard A record → home IP (needs static IP or DDNS)
- Local override: `echo "<home-ip> grafana.cluster.home argocd.cluster.home" | sudo tee -a /etc/hosts`

### Risks

- Exposes TLS to internet (cert-manager internal CA → browser will warn unless you trust it)
- Requires home IP to be stable during demo
- Mac Mini / HAProxy must be running — single extra failure point

---

## Option C — SSH tunnel (no-install fallback)

Minimal setup. No software changes on cluster. Useful if options A/B aren't available.

### Requirements

SSH access to any cluster node from the internet (open one SSH port on MikroTik).

```bash
# Open a tunnel to the cluster gateway:
ssh -N \
  -L 8443:192.168.178.200:443 \
  -L 6443:192.168.178.120:6443 \
  dalmine@<home-ip> -p <ssh-port>
```

### Access services

```bash
# Override DNS locally (while tunnel is active):
echo "127.0.0.1 grafana.cluster.home argocd.cluster.home litellm.cluster.home" \
  | sudo tee -a /etc/hosts
# Browser → https://grafana.cluster.home:8443  (adjust port in URL)

# Or: use curl with resolve flag (no /etc/hosts change):
curl -sk --resolve "grafana.cluster.home:8443:127.0.0.1" https://grafana.cluster.home:8443/api/health
```

### kubectl via tunnel

```bash
# Temporarily patch kubeconfig server (reset after demo):
kubectl config set-cluster default --server=https://127.0.0.1:6443
kubectl config set-cluster default --insecure-skip-tls-verify=true
kubectl get nodes
```

### Limitations

- Each service needs its own `-L` port mapping
- Non-standard ports (`:8443`) show in URLs — awkward in a demo
- Tunnel drop = full outage

---

## Laptop checklist before demo

- [ ] Tailscale installed and logged in on laptop
- [ ] Cluster node added to `[tailscale_nodes]` in inventory
- [ ] `ansible-playbook ... --tags tailscale` run successfully
- [ ] Subnet route approved in Tailscale admin console
- [ ] `tailscale up --accept-routes` run on laptop
- [ ] `scripts/setup-dns-split.sh` run on laptop host
- [ ] `ping 192.168.178.200` works from laptop
- [ ] `kubectl get nodes` returns all nodes
- [ ] `https://grafana.cluster.home` loads in browser
- [ ] `https://argocd.cluster.home` loads in browser
- [ ] Test from a phone hotspot (not home WiFi) before the demo day
