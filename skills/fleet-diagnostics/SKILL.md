# Skill: fleet-diagnostics — depurar el fleet KubeVirt capa por capa

Cómo diagnosticar cuándo los **host clusters KubeVirt** (nebul-idp en el homelab) se rompen: nodo que cae
de la red, control-planes que no levantan, DNS roto, volúmenes que no schedulan, etc. Destila TODO lo que
aprendimos depurando el fleet. **Principio rector: MIRAR Y esperar, nunca asumir — capa por capa.**

## Script
`scripts/fleet-diagnose.sh` (overview) · `scripts/fleet-diagnose.sh <host-cluster>` (deep dive: apiserver,
ruteo, y el VM POR DENTRO vía t7910). Requiere kubectl al Root + SSH a `dalmine@192.168.178.90` (sudo sin pass).

## Modelo mental — las capas (de abajo hacia arriba)
```
srv-t7910  [hypervisor físico, única NIC e1000e enp0s25, único /dev/kvm]
  └─ KubeVirt VMs (virt-launcher pod = qemu; binding masquerade = NAT por-VM)
       └─ guest k8s (kubeadm; control-plane = static pods etcd+apiserver)
            └─ CNI del guest (calico-vxlan)
                 └─ vCluster (control-plane del tenant)
                      └─ workload del tenant (pg+api+web)
```
**El patrón clave:** todo lo que corre en el **Root k3s** (nodos físicos super6c/rk1/pi) **sobrevive** a un
crash de t7910. Todo lo que es **VM en t7910** (los host clusters) **cae** cuando t7910 cae. Por eso los 7
vClusters centralizados (en el Root) andan y los control-planes de los host clusters no. NO es misterio: es
qué corre dónde.

## Regla de oro de diagnóstico: RST vs timeout
Al testear un apiserver (`/dev/tcp/IP/6443` o `nc`):
- **"Connection refused" (RST)** = el paquete LLEGA al VM (ruteo + Cilium + masquerade OK) y **el apiserver
  NO escucha** → problema DENTRO del VM (control-plane caído). **NO es ruteo.**
- **timeout / "no route to host"** = problema de **RUTEO/CNI/L2** (no llega al VM).
Esto desambigua "red rota" de "servicio caído" en un solo test. Probar desde **t7910** (alcanza los pod IPs
10.0.6.x directo) o desde un **jump pod** (la laptop NO llega por ARP, ver skill remote-access).

## Flujo completo — qué CONSUME qué y qué se CONECTA con qué (management-of-managements)
Diagrama fuente: `nebul-idp/flow-management-of-managements.mermaid` (→ `.svg`). Resumen del grafo:
```
Git → ArgoCD(Root) → {CAPI/CAPK/Crossplane, KubeVirt/CDI/Longhorn, cert-manager, CAAPH, 7 vClusters}
ROOT (management+hypervisor, nodos físicos):
  • CAPI/CAPK crea las VMs de los host clusters (host-euw1, host-mgmt) en KubeVirt(t7910)
  • CAAPH instala EN cada cluster: ArgoCD+cert-manager (role in mgmt,regional) y CAPI (role=management)
  • CRS siembra CNI(calico-vxlan) + region-root
host-euw1 (workload/regional): ArgoCD regional → region-root → vCluster tenant
host-mgmt (management): ArgoCD + cert-manager + CAPI(operator+providers)
  • Secret mgmt-child-infra = kubeconfig al ROOT (external-infra)
  • su CAPK CREA el Cluster hijo, pero las VMs del hijo corren en KubeVirt(t7910) del ROOT (ns mgmt-child)
mgmt-child (workload creado por host-mgmt): control-plane+worker (kubeadm) + calico-vxlan
```
**Dependencias clave (qué necesita qué):** todo VM ⟶ KubeVirt(t7910) (único KVM). CAPI operator ⟶ cert-manager.
host-mgmt's CAPK ⟶ Secret(kubeconfig al Root) ⟶ KubeVirt del Root. Cada cluster nuevo ⟶ CNI(CRS) + ArgoCD(CAAPH).
Tenants ⟶ vCluster ⟶ workload cluster (NUNCA en un management). cert-manager ⟶ TLS de Gateways + webhook CAPI.

## ¿EL CONTROL-PLANE ANDA O NO? — el test definitivo (esto bloquea TODO)
Saber si el apiserver de un host cluster está vivo es lo primero — si no, nada (ArgoCD, region-root,
vcluster, tenant) puede avanzar. **Señales que NO sirven solas (me confundieron toda una sesión):**
- `VMI Ready=True` → es qemu + guest-agent, **NO** el k8s de adentro. Engaña.
- `KCP conditions Ready=True` → CAPI **cachea** el último estado; si no puede llegar al cluster, las deja
  en True viejas (**stale**). Engaña.
- `KCP readyReplicas` → **laggea** en el boot (0/1 un rato aunque ya esté). Engaña al principio.

**El ÚNICO test confiable: pegarle al apiserver.** Desde t7910 (alcanza el pod IP 10.0.6.x directo) o un
jump pod, con la IP del VMI del control-plane:
```
curl -sk --max-time4 https://<vmIP>:6443/healthz        # o:  echo > /dev/tcp/<vmIP>/6443
```
| Resultado | Veredicto | Qué falla |
|---|---|---|
| `healthz` responde **`ok`** / puerto **abierto** | ✅ control-plane VIVO | nada — seguir con CNI/ArgoCD |
| **`Connection refused` (RST)** | ❌ apiserver CAÍDO adentro | el RUTEO está OK (el paquete llegó). Mirar etcd/apiserver DENTRO del VM (serial log). Cold-boot tras crash. |
| **timeout / `no route to host`** | ❌ no llega | RUTEO/CNI/L2 roto (o el VM no bootea). Mirar Cilium/masquerade/VMI phase. |

**Cadena de componentes a chequear (en orden — el script lo hace):**
1. **VM corre?** `virsh list` en el virt-launcher / VMI phase=Running. (qemu)
2. **Ruteo llega?** RST vs timeout (tabla de arriba). Distingue red-rota de servicio-caído.
3. **apiserver vivo?** healthz=ok. ← EL test.
4. **etcd?** si apiserver da RST/crashloop, etcd es la causa (apiserver no arranca sin etcd). Serial log del VM.
5. **Node Ready?** `kubectl --kubeconfig <secret-CAPI> get nodes` (jump pod) → Ready = CNI(calico-vxlan)+kubelet ok.
6. **CAAPH ArgoCD instalado?** `helmreleaseproxy` Ready=True → recién ahí region-root/vcluster pueden ir.
`scripts/fleet-diagnose.sh <cluster>` corre 1→6 y dice en qué eslabón se rompe.

## Catálogo de causas (síntoma → chequear → fix)

| Síntoma | Chequear | Causa / Fix |
|---|---|---|
| **Nodo t7910 cae de la red bajo carga; "se arregla" reconectando el cable** | `dmesg \| grep "Hardware Unit Hang"`; `ethtool -k enp0s25` (offloads) | **Bug NIC Intel e1000e**. NO es el cable/Mikrotik (verificar: switch sin errores fcs/overflow). Fix: `ethtool -K enp0s25 tso off gso off gro off`. Persistir: `playbooks/tune-t7910-nic.yml`. |
| **VMs corren (virsh running) pero apiserver `6443` refused** | RST (ruteo OK) + serial log del VM | **Control-plane no rearranca tras crash sucio** (cold-boot). El DataVolume persiste bytes pero etcd/apiserver tardan o no levantan. Esperar (WAL replay) o **re-provisionar** (etcd fresco). NO hay backup de etcd configurado en estos clusters CAPK. |
| **Multi-nodo: TODOS los nodos con InternalIP 10.0.2.2 (worker NotReady, install-cni exit 1)** | `kubectl get nodes -o wide` (IPs repetidas); host-network pods en 10.0.2.2; node "cni plugin not initialized" | **VM binding = masquerade** → NAT-ea cada VM a la MISMA IP fija (10.0.2.2) → colisión de node IP → calico no rutea, el worker no inicializa CNI. **Fix: bridge** = NO setear `interfaces`/`networks` en el KubevirtMachineTemplate (CAPK default = bridge → IP única del pod por VM, 10.0.6.x). Los clusters que andan (euw1/host-a/b/c) NO setean nada = bridge. Masquerade sólo para 1 VM aislada, no para nodos k8s. |
| **DNS cross-node roto dentro de un guest cluster** | `cli/fleet-test dns <c>`; CRS binding cni | **CNI anidado**: con bridge (IPs únicas) usar `cni: calico-vxlan` (UDP 4789, atraviesa el overlay). MATIZ honesto: el síntoma multi-nodo que antes atribuí a "masquerade tira IPIP" era en realidad la colisión de IP de arriba (los clusters del Root usan bridge, nunca tuvieron masquerade). VXLAN sigue siendo lo validado para el overlay. |
| **Boot-volume Pending/faulted, `ReplicaSchedulingFailure`** | `storage-over-provisioning-percentage`; `storageScheduled` vs `storageMaximum` por disco | **Longhorn over-provisioning**, NO capacidad real (cuenta reservado, no usado; thin). Subir a 300%. |
| **CRS no aplica: `no matches for kind "Application"` / HelmChartProxy** | logs `capi-controller-manager`; CRS binding `applied:false` | **Race de discovery-cache**: el CRD (Application de ArgoCD, o HelmChartProxy de CAAPH) se instaló DESPUÉS de que el CRS cacheó el discovery del cluster remoto. Auto-sana en ~10min (TTL) o forzar: `kubectl -n capi-system rollout restart deploy/capi-controller-manager`. Si un CRS bundlea varios objetos y uno falla por CRD-faltante, el binding entero queda `applied:false` y reintenta (ApplyOnce ≠ once-and-give-up). |
| **vCluster pod en un guest cluster: `Evicted` (ephemeral-storage) → `FailedScheduling` (taint disk-pressure)** | `get events -n vcluster-*`; `get nodes` (taint `node.kubernetes.io/disk-pressure`) | **Guest VM con containerDisk (~3Gi root) sin espacio** para la imagen del vcluster. La entrega GitOps puede estar perfecta (ArgoCD+region-root+appset+PVC Bound) y aún fallar por SIZING. Fix: **DataVolume-Longhorn** (30Gi) en vez de containerDisk en el KubevirtMachineTemplate (lo que ya hace la composición Crossplane de los host clusters). |
| **Borré el tenant: el wl-app queda `Deleting` para siempre** | `get app wl-* -o jsonpath='{.metadata.deletionTimestamp}{.metadata.finalizers}'`; destino = vcluster | **Finalizer contra destino muerto**: el wl-app vive DENTRO del vCluster (destination=name:vcluster-*); al borrar el tenant, el vcluster-app prunea el vCluster y el `resources-finalizer` del wl-app no puede limpiar contra un destino que ya no existe → cuelga. El appset-controller **FUERZA** el finalizer (ignora `finalizers:[]` del template). Fix: el reconciliador (CronJob vcluster-register) saca el finalizer de los wl-apps en Deleting cuyo cluster destino ya no está registrado. |
| **Tras borrar el tenant quedan ns/PVC/secret colgados** | `get ns vcluster-*`, `get pvc`, `get secret -l argocd...secret-type=cluster` | **Delete cascade incompleto**: el prune borra el App pero NO el ns auto-creado, su PVC (`data-vcluster-*-0`, de StatefulSet) ni el cluster-secret de registro. Fix: el CronJob vcluster-register GC-ea ns vcluster-* huérfanos (sin StatefulSet+pods → borra ns que cascadea PVC) + secrets de registro sin ns. Toma ~2 ciclos (uno destraba+secret, otro el ns ya huérfano) → auto-sana. El delete es GitOps (`cli/platform delete` quita el yaml), NUNCA loft `vcluster delete` (selfHeal lo recrea). |
| **Un cluster `role=management` tiene un tenant vCluster** | `hostcluster -o ...spec.role` | Viola el modelo CAPI (**management CREA, no hospeda**). Sacar el tenant; los tenants van en `role=regional/host`. |
| **`Object ... already owned by another controller`** | ownerRefs del Cluster | Crossplane↔CAPI: envolver objetos CAPI en `Object` de provider-kubernetes. |
| **kubeadm wait-control-plane cuelga eterno** | `controlPlaneEndpoint` del Cluster | Endpoint inmutable stale (surgery mid-teardown). Rebuild limpio: git-rm → esperar teardown COMPLETO → re-add. |

## Acceso (recordatorio)
- Laptop → IPs internas del cluster: **roto por ARP/WiFi** → usar **jump pod** (`cli/fleet-test kc/vc`) o
  `kubectl run --overrides hostNetwork` en un nodo que NO sea t7910 (su kubelet a veces da 502 bajo carga).
- **t7910** (`dalmine@192.168.178.90`, sudo sin pass): es el hypervisor → alcanza pod IPs 10.0.6.x directo,
  `virsh` dentro del virt-launcher para ver el VM por dentro. Diagnosticar nodo caído: ping/nc desde un pod LAN.
- `setup-dns-split.sh` arregla VIPs de LAN (Gateway/Pi-hole), NO los IPs cluster-internos.

## Tradeoffs de diseño (decisiones, con su porqué)

| Decisión | Opciones | Lo que elegimos / por qué |
|---|---|---|
| **Disco de las VMs** | containerDisk (efímero, boot rápido) vs **DataVolume-Longhorn** (persistente, boot lento) | DataVolume: persistencia > velocidad; el boot lento se absorbe con `cpInitTimeout: 15m`. |
| **CNI del guest** | calico-IPIP / cilium / **calico-vxlan** | VXLAN: único que pasa el masquerade de KubeVirt anidado (validado end-to-end). |
| **Instalar ArgoCD/CAPI en un guest** | CRS (ConfigMap ≤1MB) vs **CAAPH/Helm** | CAAPH para addons grandes (ArgoCD ~1.4MB no entra en un CRS); CRS sólo para CNI (chico, antes de pods). |
| **host-mgmt crea un hijo (management-of-managements)** | A: CAPI instalado + manifest del hijo (demuestra capacidad, sin bootear) · **B: bootea el hijo de verdad** | El hijo SIEMPRE corre sobre el KubeVirt del Root (t7910) — no hay otro KVM. B no es pesado: host-mgmt corre CAPK con `KubevirtCluster.spec.infraClusterSecretRef` → Root (external-infra). Sólo necesita: CAPI en host-mgmt (CAAPH) + un secret kubeconfig+RBAC al Root + manifests del hijo + **fleet ACOTADO** (sólo su hijo; si le das el platform-root global, recrearía host-a/b/c = recursión). |
| **Modelo de roles** | host/management/regional | management=crea clusters (no tenants); regional/host=hospeda vClusters. Sólo el Root (real+hypervisor) es management CON tenants. |
| **Servicios de plataforma (qué va en cada cluster)** | por-rol vs fleet-wide | **ArgoCD + cert-manager = fleet-wide** (todos: management Y regional; CAAPH selector `role In (management,regional)`). **CAPI = sólo management** (es quien crea clusters). cert-manager lo usan: management→webhook del CAPI operator; regional→TLS de Gateways de tenants. |
| **cert-manager para el CAPI operator** | subchart del operator vs **HelmChartProxy aparte** | Aparte: si va como subchart (`cert-manager.enabled:true`) el operator templatea su Certificate antes de que los CRDs existan → `no matches for kind Certificate`. Separado + operator con `cert-manager.enabled:false`; CAAPH reintenta hasta que cert-manager esté listo. |

## Fixes de infra VALIDADOS (no teoría — se probaron bajo carga)
- **NIC e1000e**: `ethtool -K enp0s25 tso off gso off gro off` → t7910 aguantó un build completo sin caer
  (antes crasheaba). Persistente: `playbooks/tune-t7910-nic.yml`.
- **Longhorn over-provisioning 100→300%** → 0 volúmenes faulted en rebuilds posteriores. Persistente:
  `roles/install-longhorn` (`storageOverProvisioningPercentage`).

## Errores de proceso a NO repetir (aprendidos en carne propia)
- **No tirar abajo la evidencia antes de diagnosticar.** Si un cluster está roto, inspeccionarlo (script
  deep-dive) ANTES de hacer git-rm/teardown. Pausar auto-sync de ArgoCD si hace falta conservar estado.
- **No asumir la causa** ("es etcd") sin mirar el serial log del VM / los logs reales.
- **El nodo casi nunca es "frágil"**: load suele estar <25%. Buscar la causa real (NIC, CNI, Longhorn, etcd).
