---
name: storage
description: >
  Storage on K3s. Default is longhorn-nvme (NVMe NAS-free block storage on RK1 nodes).
  smb-nas is legacy/opt-in for RWX workloads. Covers StorageClasses, dependency pattern,
  PVC migration, and the cifs-nas role.
license: MIT
compatibility:
  - opencode
metadata:
  author: dotfiles
  tags: [kubernetes, storage, longhorn, smb, cifs, csi, nas, pv, pvc, storageclass, dependency-pattern]
---

# Storage Skill

## StorageClasses in use (2026-05-25)

| StorageClass | Driver | Access | Use |
|---|---|---|---|
| `longhorn-nvme` | driver.longhorn.io | RWO | **Default for all new PVCs** — NVMe-backed, 3 replicas on RK1 nodes |
| `local-path` | rancher.io/local-path | RWO | K3s built-in, node-local. Used only for pihole (SQLite, SMB-incompatible) and build workspaces |
| `smb-nas` | smb.csi.k8s.io | RWX | Legacy. NAS LG N2R1 (192.168.178.102, SMBv1). No active PVCs as of 2026-05-25. Opt-in. |

**All service PVCs are on `longhorn-nvme`.** Zero smb-nas PVs remain.

---

## Storage Dependency Pattern

Every role that uses a non-local-path SC self-installs its storage backend as its first task.
This makes each role independently deployable.

### Role defaults

```yaml
# For longhorn-nvme (default — no storage_role needed):
<role>_storage_class: "longhorn-nvme"
# storage_role is NOT defined → guard skips automatically

# For smb-nas (legacy opt-in):
<role>_storage_class: "smb-nas"
<role>_storage_role: "install-cifs-nas"
```

### First task in tasks/main.yml

```yaml
- name: Ensure custom Storage backend is installed before deploying <Service>
  ansible.builtin.include_role:
    name: "{{ <role>_storage_role }}"
  when: <role>_storage_class != 'local-path' and <role>_storage_role is defined
```

The `when` condition:
- `local-path` → skip (K3s built-in)
- `storage_role` undefined (longhorn-nvme default) → skip (Longhorn is always installed)
- `storage_role` defined (smb-nas) → run `install-cifs-nas`

### Current role storage classes

| Role | StorageClass | storage_role |
|---|---|---|
| `install-registry` | `longhorn-nvme` | — |
| `install-hermes-agent-image` | `longhorn-nvme` | — |
| `install-hermes-agent` | `longhorn-nvme` | — |
| `install-kubernetes-mcp-server-image` | `longhorn-nvme` | — |
| `run-remote-build` | `longhorn-nvme` | — |
| `install-kube-prometheus-stack` | `longhorn-nvme` | — |
| `install-loki` | `longhorn-nvme` | — |
| `install-tempo` | `longhorn-nvme` | — |
| `install-kagent` | `longhorn-nvme` | — |
| `install-leloir` | `longhorn-nvme` | — |
| `install-honcho` | `longhorn-nvme` | — |
| `install-openclaw` | `longhorn-nvme` | — |
| `install-pihole` | `local-path` (FORCED) | — |
| `install-neuvector` | `smb-nas` | `install-cifs-nas` |

### How to add longhorn-nvme to a new role

1. In `defaults/main.yml` — just the storage class (no storage_role):
   ```yaml
   <role>_storage_class: "longhorn-nvme"
   <role>_storage_size: "10Gi"
   ```

2. First task in `tasks/main.yml` (guard handles future smb-nas override):
   ```yaml
   - name: Ensure custom Storage backend is installed before deploying <Service>
     ansible.builtin.include_role:
       name: "{{ <role>_storage_role }}"
     when: <role>_storage_class != 'local-path' and <role>_storage_role is defined
   ```

3. Reference `{{ <role>_storage_class }}` in PVC definition.

---

## install-cifs-nas role

Role: `roles/install-cifs-nas/`
Tag in bootstrap: **none** (removed 2026-05-25 — no AI role needs NAS anymore)

### What it does

1. Wake-on-LAN → wait for port 445 → apply CIFS Secret → install csi-driver-smb Helm chart
2. **Always creates** StorageClass `smb-nas` (decoupled from tests since 2026-05-25)
3. Optional static PV/PVC + write test: `cifs_enable_static: true`
4. Optional dynamic StorageClass test pod: `cifs_enable_dynamic_test: true`

### Key defaults

```yaml
cifs_nas_ip: "192.168.178.102"
cifs_nas_mac: "00:e0:91:80:fb:f0"     # Wake-on-LAN
cifs_nas_share: "service"
cifs_nas_user: "admin"
cifs_nas_pass: "changeme"              # real value in defaults/secrets.yml (gitignored)
cifs_enable_static: false              # opt-in: static PV/PVC + write test
cifs_enable_dynamic_test: false        # opt-in: dynamic test pod (StorageClass always created)
cifs_storage_class_name: "smb-nas"
cifs_storage_class_source: "//192.168.178.102/service/Torrent"
```

### When to use smb-nas

Only if you need **RWX** (ReadWriteMany) access — multiple pods writing to the same volume simultaneously. Longhorn V1 supports RWX via NFS share mode but at lower performance. smb-nas is simpler for RWX if the NAS is available.

### To install cifs-nas manually

```bash
# Install SMB CSI driver + StorageClass only (no test pods)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags storage

# Install + run dynamic test (verifies NAS write access)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags storage \
  -e cifs_enable_dynamic_test=true
```

---

## PVC Migration (smb-nas → longhorn-nvme)

Pattern used to migrate all services in 2026-05-25:

```bash
# StatefulSet (Prometheus, Loki, Tempo): volumeClaimTemplates are immutable
kubectl delete pvc <old-pvc> -n <ns>   # triggers PV → Released
kubectl delete pv <old-pv>             # Retain policy → manual cleanup
# Edit role defaults: storage_class = longhorn-nvme, remove storage_role
make <service>                         # recreates with longhorn-nvme

# Deployment (registry, kaniko caches): simpler
kubectl delete pvc <old-pvc> -n <ns>
# Edit defaults
make <service>

# StatefulSets with data (kagent-pg, leloir-pg):
# backup first, then recreate fresh (data was non-critical)
```

---

## Troubleshooting

### PVC stuck Pending on longhorn-nvme
1. `kubectl get events -n <ns> --sort-by=.lastTimestamp`
2. Check CSI plugin runs on the target node: `kubectl get pods -n longhorn-system -l app=longhorn-csi-plugin`
3. CSI plugin must run on ALL nodes (not just RK1): `kubectl get ds longhorn-csi-plugin -n longhorn-system`

### CSINode stale after csi-plugin deploy
Pod stays in `AttachVolume.Attach failed — CSINode does not contain driver`:
```bash
kubectl delete pod <stuck-pod>   # force reschedule; CSINode refreshes on next attach
```

### smb-nas mount error 111 (Connection refused)
Port 445 closed — NAS SMB service is down or NAS is sleeping.
The `install-cifs-nas` role sends WoL automatically; wait ~60s after the WoL for SMB to start.

### PostgreSQL `wrong ownership` on smb-nas
Use `smb-nas-pg` StorageClass (uid=999) instead of `smb-nas` (uid=1000).
This SC is defined inline in `install-kagent` tasks, not by `install-cifs-nas`.
