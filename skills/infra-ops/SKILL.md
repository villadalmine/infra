# infra-ops — Node Operations, Health Checks, RK1 Maintenance

Operational knowledge for the 10-node ARM64 K3s homelab cluster.

## Cluster Topology

### Super6C CM4 nodes — NVMe (8GB RAM, K3s server)

| Node | IP | Role |
|------|----|------|
| `srv-super6c-01-nvme` | 192.168.178.85 | K3s server |
| `srv-super6c-02-nvme` | 192.168.178.86 | K3s server |
| `srv-super6c-03-nvme` | 192.168.178.87 | K3s server |

### Super6C CM4 nodes — eMMC (8GB RAM)

| Node | IP | Role |
|------|----|------|
| `srv-super6c-04-emmc` | 192.168.178.133 | standalone (not in K3s) |
| `srv-super6c-05-emmc` | 192.168.178.104 | K3s server |
| `srv-super6c-06-emmc` | 192.168.178.105 | K3s server |

### TuringPi 2 RK1 nodes (32GB RAM, Rockchip RK3588S, K3s agent)

| Node | IP | MAC (fixed) |
|------|----|-------------|
| `srv-rk1-nvme-01` | 192.168.178.30 | `ce:16:3f:8e:19:cf` |
| `srv-rk1-nvme-02` | 192.168.178.48 | `86:df:be:ad:dd:97` |
| `srv-rk1-nvme-03` | 192.168.178.51 | `72:1d:5a:f8:35:48` |
| `srv-rk1-nvme-04` | 192.168.178.54 | `8e:f8:04:7e:96:92` |

Identify RK1 vs CM4: `free -h | grep Mem` → 31GB = RK1, 7.6GB = CM4.
Storage: NVMe nodes have Samsung/WD NVMe (~476GB). eMMC nodes have ~29GB onboard only.

---

## Make Targets Reference

### First-time setup
```bash
make deps          # install workstation tools (mise + pip + ansible collections)
make setup-nodes   # copy SSH key + configure sudo (needs password once)
make setup-sudoers # update sudoers only (shows diff, asks approval)
make survey        # collect hardware info → survey/*.json
make litellm       # start local LiteLLM AI proxy (needs OPENROUTER_API_KEY)
```

### Cluster bootstrap
```bash
make core          # K3s + kubeconfig
make networking    # + Cilium + LB-IPAM + Gateway API
make ingress       # + cert-manager + Gateway
make services      # + Pi-hole + ArgoCD + helm-dashboard
make observability # + Prometheus + Grafana + Tempo + Loki + Alloy
make ai            # + registry + hermes image build + LiteLLM + Hermes
make full          # everything
make clean         # destroy cluster (prompts for confirmation)
```

### Day-to-day ops
```bash
make status        # kubectl get nodes + pods + helm releases
make logs          # logs of failing pods
make node-identity # fast: hostname/IP table (no Ansible)
make node-stats    # fast: CPU%/RAM/temp per node
make healthcheck   # Ansible asserts (fails if mismatch)
```

## Health Checks

```bash
# Fast scripts (no Ansible overhead, ~5s)
make node-identity      # table: hostname / actual IP / inventory IP — all must match
make node-stats         # table: CPU% / RAM used/total / temp per node

# Ansible with asserts (fails playbook if any mismatch, ~30s)
make healthcheck

# One-liner
ansible -i inventory/hosts.ini all -m shell -a "hostname; hostname -I | awk '{print \$1}'"
```

Scripts live in `scripts/node-identity-check` and `scripts/node-stats`.
Role: `roles/healthcheck-nodes/tasks/main.yml`.

---

## RK1 MAC Address Rotation — Fix

### Problem

RK1 modules on TuringPi 2 use locally-administered MACs (first octet has bit 1 set,
e.g. `72:xx`, `86:xx`, `ce:xx`, `8e:xx`). These are generated at boot and can change,
causing DHCP to assign a different IP each reboot.

**Detection**: `ethtool -P end1` — if Permanent address matches current and starts with
an odd second-nibble, it's locally administered and potentially random.

### Fix

`playbooks/fix-mac.yml` applies to all `[rk1_nodes]` and:
1. Creates `/etc/systemd/network/10-<iface>-mac.link` — pins MAC via systemd-networkd
2. Creates `/etc/netplan/60-static.yaml` — static IP, disables DHCP
3. Creates `/etc/cloud/cloud.cfg.d/99-disable-network.cfg` — prevents cloud-init overwrite

```bash
# All RK1 nodes (when IPs are correct)
ansible-playbook playbooks/fix-mac.yml -i inventory/hosts.ini

# Single node — override IP if MAC already rotated
ansible-playbook playbooks/fix-mac.yml -i inventory/hosts.ini \
  --limit srv-rk1-nvme-01 \
  -e "ansible_host=192.168.178.67 rk1_static_ip=192.168.178.30"
```

After `netplan apply` the node drops SSH (IP changes) — this is expected. Wait 10s and reconnect at the new static IP.

### Finding a node that changed IP

```bash
# Scan full /24 for RK1 nodes (31GB RAM)
for i in $(seq 1 254); do
  result=$(ssh -o ConnectTimeout=2 -o BatchMode=yes dalmine@192.168.178.$i \
    "free -h | grep Mem" 2>/dev/null)
  echo "$result" | grep -q "31G" && echo "192.168.178.$i is an RK1"
done

# Or use Ansible to find by hostname
ansible -i inventory/hosts.ini rk1_nodes -m ping 2>&1 | grep -v SUCCESS
```

---

## TuringPi 2 Power Consumption

Each RK1 module: ~10–25W (RK3588S TDP ~15W + NVMe + RAM).
4 modules simultaneously: **60–100W peak**.

The TuringPi 2 uses an ATX/SFX 12V power connector. If the PSU rail can't supply
enough current, nodes fail to POST or reset immediately after boot.

**Workaround**: power on nodes one at a time with 30s intervals. The inrush
current on boot is higher than steady-state.

**Monitoring**: `/sys/class/thermal/thermal_zone0/temp` gives CPU temp.
INA3221 power sensor on TuringPi 2 is accessible via the BMC, not from the OS.
Typical idle: 46–55°C per RK1 module.

---

## Hostname Fix

If a node was imaged with a wrong hostname (e.g. `ubuntu`, `cm2-super6c`, `testcm42`):

```bash
# Single node
ansible -i inventory/hosts.ini srv-super6c-02-nvme -m shell \
  -a "hostnamectl set-hostname srv-super6c-02-nvme" --become

# Verify
ansible -i inventory/hosts.ini all -m shell -a "hostname" 2>/dev/null | grep -v WARNING
```

---

## Node Tuning (applied by install-k3s role)

- `fs.inotify.max_user_watches` and `fs.inotify.max_user_instances` persisted via
  `/etc/sysctl.d/99-k3s-inotify.conf` on every K3s node.
- Required for fsnotify-heavy workloads (Pi-hole, config reloaders, file watchers).
- If you see `failed to create fsnotify watcher: too many open files` — check sysctl first.

---

## Global Tolerations — Why Required

`srv-super6c-04-emmc` (control plane adjacent node) has intermittent network instability.
This causes transient `node.kubernetes.io/unreachable:NoExecute` taints that the K3s
scheduler sees on ALL nodes simultaneously (not visible via `kubectl describe node`).

**Result**: all pods in Pending with `0/7 nodes had untolerated taint(s)`.

**Fix**: `tolerations: [{operator: Exists}]` on every Helm chart and Agent CRD.
**Never combine** with `nodeSelector` pointing to one node — DiskPressure eviction loop.

```bash
# Check taints
kubectl get nodes -o json | jq '.items[].spec.taints'

# Remove stale unreachable taint manually
kubectl taint nodes srv-rk1-nvme-01 node.kubernetes.io/unreachable:NoExecute-
```

---

## Useful Ansible Commands

```bash
# Ping all nodes
ansible -i inventory/hosts.ini all -m ping

# Run command on all nodes
ansible -i inventory/hosts.ini all -m shell -a "uptime"

# Run only on RK1 nodes
ansible -i inventory/hosts.ini rk1_nodes -m shell -a "cat /sys/class/thermal/thermal_zone0/temp"

# Restart k3s-agent on a node (clears stale DiskPressure)
ansible -i inventory/hosts.ini srv-rk1-nvme-01 -m systemd \
  -a "name=k3s-agent state=restarted" --become
```
