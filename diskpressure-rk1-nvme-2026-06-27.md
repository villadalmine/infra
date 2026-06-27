# DiskPressure en `srv-rk1-nvme-01` — diagnóstico y opciones (2026-06-27)

## Síntoma
- El deploy de la web de Tormenta Solar (`tormenta-web`, ns `ai`) **no propagaba**: el sitio seguía sirviendo una
  versión vieja aunque `helm upgrade` decía "successfully rolled out".
- En `ns ai` había **~1727 pods `Evicted`/`Failed`** acumulados. `deployment/rk1-npu-01` estaba **0/1 (caído)**.
- Todas las evictions con el mismo motivo: `Pod was rejected: The node had condition: [DiskPressure].` en
  `srv-rk1-nvme-01`.

## Causa raíz
`srv-rk1-nvme-01` tiene el **root en la eMMC/SD de 29 GB** (`/dev/mmcblk0p2`), al **88–90 %**:

```
/dev/mmcblk0p2   29G   25G  ~3G  90% /
/dev/nvme0n1    469G  106G  340G 24% /mnt/nvme   <- NVMe, casi vacío
```

Lo que llena la SD es **containerd (k3s)**: `~21 GB` en `/var/lib/rancher/k3s/agent/containerd`
(15 GB en `…/snapshotter.v1.overlayfs/snapshots` + 5.7 GB en `…/content.v1.content`).

- **No** es el registry: `registry-data` PVC (100Gi) está en **`longhorn-nvme`** ✅.
- **No** es Kaniko: tanto `install-hermes-agent-image` (infra-ai) como los `kaniko-build.yaml` de tormenta
  (proxy/web) usan `storageClassName: longhorn-nvme` ✅.
- Son las **imágenes que el nodo baja para CORRER sus ~22 contenedores** (registry + NPU `rk1-npu-01` + hermes-agent
  + holmesgpt + tormenta + componentes de longhorn/csi). Cada imagen tiene **una sola versión** y está **en uso**:
  `k3s crictl rmi --prune` no libera nada. La SD es simplemente **chica** para el set de imágenes de este nodo.

`DiskPressure` ⇒ el kubelet rechaza pods nuevos ⇒ los controllers (deployments) recrean ⇒ se apilan cientos de
`Evicted`. `rk1-npu-01` queda 0/1 porque su pod **debe** correr en este nodo (NPU) y no puede admitirse.

## Por qué los playbooks de migración existentes NO aplican tal cual
`playbooks/mount-rk1-nvme.yml` y `roles/migrate-etcd-to-nvme` mueven `/var/lib/rancher` a una **partición NVMe
dedicada** (`/dev/nvme0n1p1`). Pero en este nodo **el NVMe entero (`/dev/nvme0n1`, 469 GB) ya está montado en
`/mnt/nvme` y es de Longhorn** (disco `nvme-fs`, `allowScheduling: true`). **Reparticionar destruiría los datos de
Longhorn.** ⇒ Hay que mover containerd a un **directorio dentro de `/mnt/nvme`** (que convive con Longhorn), no a una
partición nueva.

## ⚠️ Salvedad de capacidad de Longhorn (la pregunta del dueño: "¿se queda sin espacio?")
**Sí, hay riesgo si se hace ingenuamente.** El NVMe ya está **muy sobre-provisionado** por Longhorn en este nodo:

| Métrica (disco `nvme-fs`)        | Valor       |
|----------------------------------|-------------|
| `storageMaximum`                 | ~469 GB     |
| `storageAvailable` (FS real libre)| ~363 GB    |
| `storageScheduled` (suma provisionada de réplicas) | **~414 GB** |
| `storageReserved`                | **0**       |
| réplicas en el nodo              | **22** (sum volumeSize ≈ 444 GB) |

Las réplicas son **thin** (usan ~106 GB reales hoy), pero están **provisionadas a ~414 GB sobre 469 GB**. Si volcás
~21 GB de containerd **sin reservar espacio** y esas réplicas crecen, **containerd y Longhorn compiten por los
últimos GB** → ambos pueden quedarse cortos. `storageReserved=0` significa que Longhorn no está dejando colchón.

## Opciones (de menos a más invasiva)

### A. Mover containerd a un dir en `/mnt/nvme` + RESERVAR espacio en Longhorn (recomendada)
1. Setear `storageReserved` del disco `nvme-fs` a ~**30 GB** (para que Longhorn nunca pelee por el espacio de
   containerd): `kubectl -n longhorn-system edit nodes.longhorn.io srv-rk1-nvme-01` → `spec.disks.nvme-fs.storageReserved: 32212254720`.
2. Drenar y parar k3s en el nodo, mover el dir, bind-mount, arrancar:
   ```bash
   kubectl drain srv-rk1-nvme-01 --ignore-daemonsets --delete-emptydir-data --force --timeout=300s
   sudo systemctl stop k3s            # o k3s-agent si es agente
   sudo /usr/local/bin/k3s-killall.sh
   sudo mkdir -p /mnt/nvme/k3s-containerd
   sudo rsync -aHAX --remove-source-files /var/lib/rancher/k3s/agent/containerd/ /mnt/nvme/k3s-containerd/
   sudo rm -rf /var/lib/rancher/k3s/agent/containerd
   echo '/mnt/nvme/k3s-containerd /var/lib/rancher/k3s/agent/containerd none bind 0 0' | sudo tee -a /etc/fstab
   sudo mkdir -p /var/lib/rancher/k3s/agent/containerd && sudo mount /var/lib/rancher/k3s/agent/containerd
   sudo systemctl start k3s
   kubectl uncordon srv-rk1-nvme-01
   ```
   **Impacto:** corte de ~2–5 min de los pods de ESTE nodo (registry/NPU/hermes/holmes/tormenta); los demás nodos
   siguen (tienen imágenes cacheadas). Libera ~21 GB de la SD (queda ~13 % usada). **Mata la DiskPressure de raíz.**
   **Idealmente: codificarlo como rol `migrate-containerd-to-nvme-dir` en infra-ai** (espejo de `migrate-etcd-to-nvme`
   pero a DIRECTORIO, no a partición, + el `storageReserved` previo).

### B. Rebalancear Longhorn (sacar réplicas de este nodo)
22 réplicas / 414 GB provisionados es mucho para un nodo con SD chica. Bajar `allowScheduling` un rato y/o evictar
réplicas a `srv-t7910` u otros, hasta dejar el nodo más liviano. No toca k3s, pero mueve mucho dato por red.

### C. Partición dedicada (la "ortodoxa", pero acá costosa)
Para usar `mount-rk1-nvme.yml` tal cual habría que **achicar Longhorn primero** (drenar sus datos del NVMe),
reparticionar (`nvme0n1p1` para k3s + `nvme0n1p2` para Longhorn) y recrear el disco Longhorn. Mucho trabajo y
riesgo; no vale la pena vs. la opción A.

## Limpieza inmediata (segura, no toca k3s)
- Borrar los pods muertos: `kubectl -n ai delete pods --field-selector status.phase=Failed` (en curso 2026-06-27).
  **No** libera disco del nodo (ya estaban muertos) — solo limpia el clutter de la API. Volverán a aparecer mientras
  haya DiskPressure: el fix de verdad es la opción **A**.

## ✅ RESOLUCIÓN aplicada (2026-06-27) — sin tocar k3s
El consumidor #1 de la SD era la imagen **`hermes-agent` = 2.38 GB**, del pod `hermes-agent-mcp`, **pineado a -01**
por `roles/install-hermes-agent/defaults/main.yml: hermes_node_hostname: "srv-rk1-nvme-01"` (el comentario decía
"high-resource node", pero -01 es justo el de SD chica). Sus PVCs (`hermes-data`, `hermes-home`) son `longhorn-nvme`
⇒ **movible**.

Acción (alivio inmediato + durable):
1. **infra-ai:** `hermes_node_hostname` → **`srv-rk1-nvme-04`** (tiene ~11 GB libres). *(commitear y `ansible` para
   que quede; el cambio en vivo de abajo lo revertiría un re-run si no se actualiza el repo — ya hecho aquí.)*
2. **En vivo:** `kubectl -n ai patch deployment hermes-agent-mcp` nodeSelector → `srv-rk1-nvme-04`; el pod migró,
   y `k3s crictl rmi --prune` en -01 sacó la imagen de 2.38 GB ahora sin referencia.

Resultado: **-01 pasó de 87 % → 67 %** (9.3 GB libres), **`DiskPressure: False`**, nodo `uncordon`. `rk1-npu-01`
(NPU, debe quedarse en -01; su modelo ya está en `longhorn-nvme`) volvió a arrancar. `tormenta-web` v203 quedó live.
Los 1727 pods `Evicted` se limpiaron.

**Fix A (containerd → /mnt/nvme) queda como mejora a futuro, ya NO urgente** (67 % es cómodo). Considerar también
mover `holmesgpt-holmes` (395 MB) o re-pinear más livianos si -01 vuelve a apretar.

## Estado al 2026-06-27 (previo a la resolución)
- Diagnóstico hecho; pods `Evicted` limpiados.
- Causa de fondo secundaria (no bloqueante): el deploy de la **web reusa siempre el tag `0.1.94`** con
  `imagePullPolicy: IfNotPresent` → un rebuild del mismo tag no propaga confiable. Conviene **bumpear el tag por
  build** (como el proxy) o `Always` en el chart de la web.
