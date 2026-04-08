---
name: storage
description: >
  CIFS/SMB storage on K3s for a legacy SMB1 NAS using csi-driver-smb.
  Covers static PV/PVC mounts, dynamic StorageClasses, the storage dependency
  pattern used by every PVC-backed role, and troubleshooting permission issues.
license: MIT
compatibility:
  - opencode
metadata:
  author: dotfiles
  tags: [kubernetes, storage, smb, cifs, csi, nas, pv, pvc, smb1, storageclass, dependency-pattern]
---

# Storage Skill

## Cluster Context

This cluster uses `csi-driver-smb` for a legacy NAS that only supports SMB1.

Role: `install-cifs-nas`
Tag: `storage`

| Key | Value |
|---|---|
| NAS IP | `192.168.178.102` |
| Share | `//192.168.178.102/service` |
| Dynamic source | `//192.168.178.102/service/Torrent` |
| Static PV | `smb-nas-pv` |
| Static PVC | `smb-nas-pvc` |
| Dynamic SC default | `smb-nas` (uid=1000, gid=1000) |
| Dynamic SC postgres | `smb-nas-pg` (uid=999, gid=999) |

---

## StorageClasses

### `smb-nas` — general purpose (uid=1000)

Created by `install-cifs-nas`. Used by almost all PVC-backed services.

```yaml
mountOptions:
  - vers=1.0
  - uid=1000
  - gid=1000
  - file_mode=0777
  - dir_mode=0777
  - noperm
  - mfsymlinks
  - cache=strict
  - noserverino
parameters:
  source: "//192.168.178.102/service/Torrent"
```

### `smb-nas-pg` — PostgreSQL only (uid=999)

**Created by `install-kagent`** (not by `install-cifs-nas`). Defined inline in
`roles/install-kagent/tasks/main.yml`. Same NAS share as `smb-nas` but with
uid=999/gid=999 so PostgreSQL can own its data directory.
Default `smb-nas` (uid=1000) causes Postgres `wrong ownership` errors.

```yaml
mountOptions:
  - vers=1.0
  - uid=999
  - gid=999
  - file_mode=0700
  - dir_mode=0700
  - noperm
```

---

## Storage Dependency Pattern

**Every role that uses `smb-nas` or `smb-nas-pg` self-installs `install-cifs-nas`
before doing anything else.** This makes each role independently deployable without
requiring the operator to run `make storage` first.

### The pattern (identical across all roles)

**`defaults/main.yml`** — two variables:
```yaml
<role>_storage_class: "smb-nas"          # or smb-nas-pg, or local-path
<role>_storage_role: "install-cifs-nas"  # the storage backend role
```

**`tasks/main.yml`** — first task in the file:
```yaml
- name: Ensure custom Storage backend is installed before deploying <Service>
  ansible.builtin.include_role:
    name: "{{ <role>_storage_role }}"
  when: <role>_storage_class != 'local-path' and <role>_storage_role is defined
```

The `when` condition means:
- If `storage_class` is `local-path` → skip (K3s built-in, no backend needed)
- If `storage_role` is undefined → skip (defensive; shouldn't happen with current defaults)
- Otherwise → run `install-cifs-nas` (idempotent — safe to call multiple times)

### Roles that use this pattern

| Role | Storage var | StorageClass | Condition |
|------|-------------|--------------|-----------|
| `install-pihole` | `pihole_storage_class` | `local-path` (FORCED) | SQLite incompatible with SMB — never use smb-nas |
| `install-kube-prometheus-stack` | `kube_prometheus_stack_storage_class` | `smb-nas` | `!= 'local-path'` |
| `install-loki` | `loki_storage_class` | `smb-nas` | `!= 'local-path'` |
| `install-tempo` | `tempo_storage_class` | `smb-nas` | `!= 'local-path'` |
| `install-registry` | `registry_storage_class` | `smb-nas` | `!= 'local-path'` |
| `install-neuvector` | `neuvector_pvc_storage_class` | `smb-nas` | `pvc_enabled AND != 'local-path'` |
| `install-hermes-agent` | `hermes_storage_class` | `smb-nas` | `!= 'local-path'` |
| `install-hermes-agent-image` | `kaniko_storage_class` | `smb-nas` | `!= 'local-path'` |
| `install-kagent` | `kagent_db_storage_class` | `smb-nas-pg` | `!= 'local-path'` |
| `install-kubernetes-mcp-server-image` | `kubernetes_mcp_storage_class` | `smb-nas` | `!= 'local-path'` |

### How to add this pattern to a new role

1. In `roles/<role>/defaults/main.yml`:
   ```yaml
   <role>_storage_class: "smb-nas"
   <role>_storage_role: "install-cifs-nas"
   ```

2. As the **first task** in `roles/<role>/tasks/main.yml`:
   ```yaml
   - name: Ensure custom Storage backend is installed before deploying <Service>
     ansible.builtin.include_role:
       name: "{{ <role>_storage_role }}"
     when: <role>_storage_class != 'local-path' and <role>_storage_role is defined
   ```

3. In the Helm values block, reference `{{ <role>_storage_class }}` for the PVC storageClassName.

### To override to local-path (no NAS)

```bash
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags observability \
  -e "loki_storage_class=local-path tempo_storage_class=local-path kube_prometheus_stack_storage_class=local-path"
```

---

## Static flow

Binds a fixed PV to a fixed PVC (not used by services — test only).

- `storageClassName: ""`
- `volumeName: smb-nas-pv`
- Mount options: `vers=1.0 uid=1000 gid=1000 file_mode=0777 dir_mode=0777 noperm`
- Test pod: `smb-test-pod` (mounts `subPath: Torrent`)

---

## Dynamic flow

Provisions PVCs through a `StorageClass`.

- `source` must point to a writable subdirectory, not the share root
- Working value: `//192.168.178.102/service/Torrent`
- Test pod: `smb-dynamic-test-pod` — writes to `/mnt/smb/prueba-escritura-dinamica.txt`

---

## Usage

```bash
# Install storage backend only
make storage

# Or directly:
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags storage

# Any service role auto-installs storage if needed:
make observability   # installs cifs-nas if storage_class != local-path
make ai              # installs cifs-nas automatically
```

---

## Troubleshooting

### `failed to make subdirectory: permission denied`
The `StorageClass.source` points to a path the NAS does not allow the CSI driver to
create under. Use a known writable subdirectory like `//192.168.178.102/service/Torrent`.

### PVC stays Pending
1. Check StorageClass exists: `kubectl get sc`
2. Check events: `kubectl get events -n <ns> --sort-by=.lastTimestamp`
3. Verify SMB CSI driver is running: `kubectl get pods -n kube-system | grep smb`

### PostgreSQL `FATAL: data directory ... has wrong ownership`
Use `smb-nas-pg` StorageClass (uid=999) instead of `smb-nas` (uid=1000).

### Mount works statically but not dynamically
Static PV/PVC and dynamic CSI provisioning are separate paths.
Check `cifs_enable_static` and `cifs_enable_dynamic_test` flags in `install-cifs-nas` defaults.

### StorageClass exists but pods still can't write
Check `mountOptions` — `noperm` is required to bypass permission checks at the CIFS level.
