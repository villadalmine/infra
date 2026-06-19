# Skill: HA Control Plane (3 masters, embedded etcd, HAProxy VIP)

Deep context for the K3s high-availability control plane. Read before touching masters,
etcd, the VIP, or the `install-k3s` / `install-haproxy` / `prepare-etcd-nvme` /
`migrate-etcd-to-nvme` roles.

## Topology

| Master | IP | etcd disk | Notes |
|---|---|---|---|
| `srv-super6c-01-nvme` | .120 | **NVMe** | cluster-init node (`server_nodes[0]`) |
| `srv-super6c-02-nvme` | .121 | **NVMe** | join (`server_join_nodes`) |
| `srv-super6c-04-nvme` | .122 | **eMMC** | join — has NO physical NVMe |

- **etcd quorum = 2/3** → the cluster tolerates losing any one master.
- **VIP** `192.168.178.130:6443` — HAProxy on `srv-pi-rack2b`. kubectl + agents target it.
- `super6c-03` (the expected 3rd NVMe node) is **offline**. `super6c-05/06-emmc` (.124/.123)
  have no NVMe and currently run nothing (orphans) — candidates to convert to agents.

## How etcd actually runs in k3s (CRITICAL mental model)

etcd is **embedded in the `k3s server` binary** — it is NOT a pod and NOT a separate
systemd service:
- The `k3s-server` process itself listens on `:2379`/`:2380` (`ss -tlnp | grep 2379`).
- There is only `k3s.service`; no `etcd.service`; `kubectl get pods -A` shows no etcd pod.
- There is **no `etcdctl`** on the nodes; `kubectl`/`ctr`/`crictl` are symlinks to `k3s`.
- etcd data lives in `/var/lib/rancher/k3s/server/db/etcd/` (`member/snap`, `member/wal`).
- k3s serves etcd over gRPC only — the gRPC-gateway REST API is disabled (curl → HTTP 415).

Consequences:
- `systemctl stop k3s` stops etcd too (same process).
- `k3s.service` uses `KillMode=process`, so stopping k3s does **not** kill the pods/containers
  (containerd keeps them running); the node just goes NotReady until k3s returns.
- Moving `/var/lib/rancher` to another disk + restarting k3s moves etcd with its member
  identity intact — no `etcdctl member remove` needed.

## Roles

| Role / playbook | Purpose |
|---|---|
| `install-haproxy` | HAProxy TCP-passthrough VIP for the API. Backends auto-rendered from `server_nodes`+`server_join_nodes`. |
| `prepare-etcd-nvme` | Mounts NVMe at `/var/lib/rancher` for a master with an **empty** datadir (fresh bootstrap). Skips if a live datadir is on a non-NVMe disk. |
| `install-k3s` | Dispatches by inventory group: `server-init.yml` (cluster-init), `server-join.yml` (join), `agent.yml` (worker → VIP). State-based idempotency. |
| `migrate-etcd-to-nvme` (playbook `migrate-etcd-nvme.yml`) | Moves an EXISTING master's datadir eMMC→NVMe in place, preserving the etcd member. `serial: 1`. |

### install-k3s idempotency (state-based, not flag-based)
- init: migrates SQLite→etcd only while `db/etcd/member` is absent (adds `--cluster-init`).
- join: wipes+joins only while the node is not already a **Ready** member (checked via
  `kubectl get node <name> --no-headers`, column 2 == `Ready`).
- agent: (re)installs only when inactive or not yet pointing at the VIP.

## Procedures

### Add / re-join a master
Put the node in `[server_join_nodes]`, then:
```bash
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags core --limit <node>
```
The role detects an orphan (standalone SQLite cluster), runs `k3s-uninstall.sh` (full wipe),
and re-joins via the init node's IP with `--tls-san <VIP>`.

### Move a master's etcd from eMMC to NVMe (no control-plane downtime)
Quorum protects this — do **one master at a time**:
```bash
# disposable join node first, then the init node:
ansible-playbook playbooks/migrate-etcd-nvme.yml -i inventory/hosts.ini --limit srv-super6c-02-nvme
ansible-playbook playbooks/migrate-etcd-nvme.yml -i inventory/hosts.ini --limit srv-super6c-01-nvme
```
The role asserts ≥2 other Ready etcd members before stopping k3s, renames the eMMC datadir
aside (`/var/lib/rancher.pre-nvme`), formats+mounts the NVMe (LABEL `k3s-data`, fstab), copies
the data back (`cp -a` preserves the member), restarts k3s, waits Ready. Delete the
`.pre-nvme` backup once verified.

### Verify HA failover
```bash
ssh dalmine@<master> "sudo systemctl stop k3s"   # simulate a master down
kubectl get nodes                                # must keep working via the VIP
ssh dalmine@<master> "sudo systemctl start k3s"
```

### Rolling version upgrade (zero downtime)
**k3s:** bump `k3s_version` in `roles/install-k3s/defaults/main.yml`, then roll one node
at a time (control-plane first, quorum + VIP hold the API):
```bash
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags core -e k3s_install_serial=1
```
install-k3s detects version drift (`k3s --version` vs `k3s_version`) and re-runs the
installer even though the state guards would skip it; each node waits Ready before the next.

**Cilium:** bump `cilium_version` in `roles/install-cilium/defaults/main.yml`, then:
```bash
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags networking
```
`helm upgrade` + `rollOutCiliumPods` restart the DaemonSet; the BPF datapath persists in
the kernel, so there's no network outage. **Upgrade Cilium BEFORE k8s if k8s would move
out of Cilium's tested range.**

**Version ceiling:** Cilium 1.19.x supports k8s **1.32–1.35 only**. Going to k8s 1.36
needs Cilium 1.20.x, which is still pre-release — so the cluster stays on the 1.35 line
(latest k3s `v1.35.5+k3s1`, Cilium `1.19.5`) until Cilium 1.20 is stable.

## Gotchas (learned the hard way)

- **`option redispatch` is mandatory on the VIP.** Without it, when round-robin picks a
  down master (before the health check marks it down) HAProxy returns **503 Service
  Unavailable** to the client instead of retrying another backend → intermittent failures
  during failover. Config also uses an L7 health check (`option httpchk GET /readyz` +
  `http-check expect status 401`, since the k3s apiserver requires auth) and `inter 2s fall 2`.
- **`--tls-san <VIP>` on every master** — HAProxy is TCP passthrough, so kubectl/agents verify
  the master's serving cert against the VIP IP. Without the SAN: `x509: cert valid for .120,
  not .130`. Set in `k3s_server_exec` (install-k3s defaults).
- **jsonpath in the `command` module gets mangled.** `-o jsonpath={...@.type=="Ready"...}`
  loses its quotes to shlex → `unrecognized identifier Ready`. Use `get node --no-headers`
  and read column 2 instead.
- **Migrate one master at a time.** With 2 healthy members, stopping one loses quorum. The
  `migrate-etcd-nvme.yml` play is `serial: 1` and the role asserts ≥2 other Ready etcd members.
- **Delegated kubectl during a migration must target a SURVIVING master**, never the one being
  stopped (its apiserver is down). The role computes `mig_query_host` = first master ≠ self.
- **super6c `-nvme` hostnames lie.** Only .120/.121 physically have an NVMe; .122/.123/.124 are
  eMMC-only. The NVMe on .120/.121 had stale Longhorn remnants (active Longhorn is on the RK1
  nodes) — safe to wipe.
- **kubeconfig points at the VIP** (`get-kubeconfig` replaces 127.0.0.1 with `k3s_ha_vip`,
  read via `hostvars[groups['haproxy_nodes'][0]]` because the implicit localhost doesn't
  inherit inventory `[all:vars]`).

## Inventory groups
`server_nodes` (init, 1) · `server_join_nodes` (joins) · `etcd_nvme_nodes` (masters with NVMe)
· `haproxy_nodes` (VIP host) · `agent_nodes` (workers). `k3s_ha_vip` in `[all:vars]`.
