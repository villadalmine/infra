# Longhorn NVMe Storage — Skill

## Estado actual (2026-05-24)

Longhorn v1.11.2 desplegado y operativo. StorageClass `longhorn-nvme` disponible en los 4 nodos RK1.

```
make longhorn        # deploy/upgrade (idempotente)
make longhorn-bench  # benchmark fio completo (despliega pod, imprime, limpia)
```

## Arquitectura

```
Pod (cualquier nodo) → CSI driver → Longhorn Manager
                                         ↓
                        3 réplicas distribuidas en RK1 nodes
                        srv-rk1-nvme-01..04 / /mnt/nvme (ext4 sobre NVMe)
```

- **Engine**: V1 (iSCSI/NVMe-oF TCP). V2/SPDK no es posible en TuringPi 2 (IOMMU group compartido con PCIe bridge).
- **Discos**: `diskType: filesystem` en `/mnt/nvme` — NVMe completo formateado ext4, sin partición.
- **Réplicas**: 3 por defecto. Pods de cualquier nodo del cluster pueden usar el volumen.
- **nodeSelector en Longhorn**: `storage: rk1-longhorn` — solo los RK1 hospedan datos.

## StorageClass

```yaml
storageClassName: longhorn-nvme   # block storage replicado, RWO
storageClassName: smb-nas         # shared filesystem, RWX — NAS Synology
storageClassName: local-path      # local sin réplica — solo para dev/test
```

## Cuándo usar longhorn-nvme

**Sí — workloads I/O intensivos:**
- PostgreSQL (kagent, Leloir) — random I/O, latencia crítica
- Prometheus — TSDB, scrapes constantes
- Loki — ingesta de logs write-heavy
- Tempo — traces write-heavy
- Cualquier base de datos o índice

**No — mantener smb-nas:**
- Registry de imágenes — necesita persistir entre rebuilds, acceso RWX
- Hermes / OpenClaw / LiteLLM — config, estado ligero, no I/O intensivo
- NAS Admin — por definición accede al NAS

**No usar nunca:**
- `local-path` para producción — sin réplica, datos perdidos si el nodo muere

## Benchmark resultados (2026-05-24, parámetros qd=128 — no óptimo)

```
4K Random Read  (qd=128): 1710 IOPS / 6.7 MiB/s / Lat=18ms
4K Random Write (qd=128): 1392 IOPS / 5.4 MiB/s / Lat=23ms
Sequential 128K Read:      156 MiB/s
```

El 156 MiB/s secuencial supera el tope de GbE (119 MiB/s) porque la réplica primaria sirve desde el NVMe local. Los IOPS 4K se ven bajos porque qd=128 satura el pipeline de replicación; a qd=1 (workload real de DB) se esperan 1-3ms de latencia.

## Migración de servicios (patrón)

Para cambiar un servicio de `smb-nas` a `longhorn-nvme`:

1. Editar `roles/<service>/defaults/main.yml`:
   ```yaml
   # antes:
   <service>_storage_class: "smb-nas"
   # después:
   <service>_storage_class: "longhorn-nvme"
   ```
2. Si el servicio tiene datos existentes en el PVC smb-nas → necesita migración manual o recreación.
3. Si es stateless o reiniciable desde cero (Prometheus retiene métricas históricas; Loki retiene logs) → decidir si migrar datos o arrancar fresh.
4. Correr `make <service>` — el role recrea el PVC con la nueva StorageClass.

**Servicios stateless (safe recrear):** Prometheus (pierde historial), Loki (pierde logs), Tempo (pierde traces).  
**Servicios stateful (requieren backup/migración):** kagent PostgreSQL, Leloir PostgreSQL.

## Playbook y role

```
playbooks/storage-longhorn.yml
  Play 1 (rk1_nodes):  detecta /dev/nvme0n1, monta /mnt/nvme si no está, persiste fstab
  Play 2 (localhost):  helm install longhorn + patch discos Longhorn CRD + StorageClass

roles/install-longhorn/
  defaults/main.yml              longhorn_chart_version, longhorn_run_benchmark
  tasks/main.yml                 include helm.yml
  tasks/helm.yml                 label nodos, helm install, patch CRD discos, SC, benchmark
  templates/longhorn-values.yaml.j2    V2 disabled, nodeSelector rk1-longhorn
  templates/storageclass-v2.yaml.j2   StorageClass longhorn-nvme
  templates/fio-benchmark.yaml.j2     Pod alpine:latest + fio qd=1/16/seq
```

## Gotchas conocidos

- `node.longhorn.io/default-disks-config` annotation solo aplica en primer setup del nodo. Para nodos ya inicializados, el role parchea directamente el CRD `nodes.longhorn.io`.
- `v2-data-engine` Setting no se sobreescribe en Helm upgrades. El role lo forza a `false` explícitamente tras cada install/upgrade.
- La image del benchmark debe ser multi-arch (`alpine:latest`) — `nixery.dev/shell/fio` es x86_64 only.
- El NVMe en estos nodos está formateado directamente en `/dev/nvme0n1` (sin partición, `Partition Table: loop`). No intentar reparticionar.
