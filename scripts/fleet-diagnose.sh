#!/usr/bin/env bash
# fleet-diagnose.sh — diagnóstico capa-por-capa del fleet KubeVirt (nebul-idp en el cluster homelab).
# Encapsula TODO lo aprendido depurando el fleet: NIC e1000e, cold-boot de control-planes, CNI anidado,
# Longhorn over-provisioning, CRS discovery-race, modelo management vs workload, ruteo (RST vs timeout).
#
# Uso:
#   scripts/fleet-diagnose.sh                 # overview del fleet + capas base
#   scripts/fleet-diagnose.sh <host-cluster>  # + deep dive de un cluster (apiserver, VM por dentro)
#
# Requiere: kubectl al cluster mgmt (Root) + SSH a t7910 (dalmine@192.168.178.90, sudo sin pass).
# NS del fleet = "fleet". Hypervisor = srv-t7910 (192.168.178.90), única NIC e1000e enp0s25.
set -uo pipefail
T7910=dalmine@192.168.178.90
NIC=enp0s25
NS=fleet
C="${1:-}"
b(){ printf "\n\033[1;36m══ %s ══\033[0m\n" "$*"; }
ok(){ printf "  \033[32m✔\033[0m %s\n" "$*"; }
no(){ printf "  \033[31m✗\033[0m %s\n" "$*"; }
info(){ printf "  • %s\n" "$*"; }
ssht(){ timeout 30 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 "$T7910" "$@" 2>&1; }

b "0. HYPERVISOR srv-t7910 — el nodo y su NIC (bug e1000e Hardware Unit Hang)"
kubectl get node srv-t7910 --no-headers 2>/dev/null | awk '{print "  node:",$1,$2}'
info "load/mem (¿sobrecargado? casi nunca lo es):"; ssht 'uptime; free -g | awk "/Mem:/{print \"  mem used/total: \"\$3\"/\"\$2\" GiB\"}"; echo "  cores: $(nproc)"'
info "offloads NIC (DEBEN estar off — si on, la NIC se cuelga bajo carga):"
ssht "ethtool -k $NIC 2>/dev/null | grep -E '^tcp-segmentation-offload|^generic-segmentation-offload|^generic-receive-offload'"
info "dmesg: ¿hay 'Detected Hardware Unit Hang'? (la firma del bug):"
ssht "sudo dmesg 2>/dev/null | grep -c 'Hardware Unit Hang' | sed 's/^/  ocurrencias: /'"
echo "  → si hay hangs y offloads en 'on': sudo ethtool -K $NIC tso off gso off gro off (persistir: playbooks/tune-t7910-nic.yml)"

b "1. MODELO de clusters (CAPI: management CREA, workload HOSPEDA vClusters)"
echo "  XR        ROLE        REGION       CNI"
kubectl -n $NS get hostcluster -o custom-columns='N:.metadata.name,ROLE:.spec.role,REGION:.spec.region,CNI:.spec.cni' --no-headers 2>/dev/null | sed 's/^/  /'
echo "  REGLA: role=management → corre CAPI, NO hospeda tenants. role=regional/host → hospeda vClusters."
echo "  Único 'management' con vClusters = el Root k3s (real + hypervisor + 7 centralizados)."

b "2. ESTADO por cluster (CAPI level — mgmt-side, confiable)"
for x in $(kubectl -n $NS get cluster --no-headers 2>/dev/null | awk '{print $1}'); do
  ph=$(kubectl -n $NS get cluster $x -o jsonpath='{.status.phase}' 2>/dev/null)
  kcp=$(kubectl -n $NS get kubeadmcontrolplane ${x}-control-plane -o jsonpath='{.status.readyReplicas}/{.status.replicas}' 2>/dev/null)
  ep=$(kubectl -n $NS get endpoints ${x}-lb -o jsonpath='{.subsets[0].addresses[0].ip}' 2>/dev/null)
  info "$x: phase=$ph KCP_ready=$kcp apiEndpoint=${ep:-vacío}"
done

b "3. VMs (KubeVirt) — ¿corren? (el nodo cae → VMs caen; sólo el Root sobrevive)"
kubectl -n $NS get vmi --no-headers 2>/dev/null | awk '{print "  "$1,"phase="$3,"ip="$4,"ready="$6}'
echo "  NOTA: VMI 'Ready=True' = qemu+agent ok; NO garantiza que el apiserver k8s adentro esté arriba."

b "4. LONGHORN — over-provisioning (causa REAL de ReplicaSchedulingFailure, no capacidad)"
op=$(kubectl -n longhorn-system get settings.longhorn.io storage-over-provisioning-percentage -o jsonpath='{.value}' 2>/dev/null)
info "over-provisioning: ${op}%  (Longhorn cuenta RESERVADO, no usado; con boot-volumes thin sube a 300)"
fz=$(kubectl -n longhorn-system get volumes.longhorn.io --no-headers 2>/dev/null | grep -c faulted)
info "volúmenes faulted: ${fz:-0}  (faulted+ReplicaSchedulingFailure = subir over-provisioning)"

b "5. vClusters — Root (centralizados) vs regionales (en host clusters)"
info "CENTRALIZADOS (en el Root, sobreviven crashes de t7910):"
kubectl get ns --no-headers 2>/dev/null | awk '$1 ~ /^vcluster-tenant-/{print "    "$1}'
echo "  REGIONALES (dentro de host clusters): usar cli/fleet-test kc <cluster> get ns (jump pod por ARP)."

# ── CONTROL-PLANE HEALTH (definitivo, paso a paso) ──────────────────────────
if [ -n "$C" ]; then
  b "6. CONTROL-PLANE HEALTH de $C — la cadena definitiva (¿anda o no, y dónde falla?)"
  vip=$(kubectl -n $NS get vmi --no-headers 2>/dev/null | grep "${C}-control-plane" | head -1 | awk '{print $4}')
  info "CP VMI IP (pod): ${vip:-NO-HAY-VMI}"

  echo "  [1/6] VM corre (qemu)?"
  vmphase=$(kubectl -n $NS get vmi --no-headers 2>/dev/null | grep "${C}-control-plane" | head -1 | awk '{print $3}')
  [ "$vmphase" = "Running" ] && ok "VMI phase=Running" || no "VMI phase=${vmphase:-ausente} (el VM no corre → mirar KubeVirt/scheduling/DataVolume)"

  echo "  [2/6]+[3/6] Ruteo + apiserver (EL test — desde t7910 al pod IP):"
  verdict=$(ssht "
    if curl -sk --max-time 4 https://$vip:6443/healthz 2>/dev/null | grep -qx ok; then echo UP;
    elif timeout 4 bash -c 'echo > /dev/tcp/$vip/6443' 2>/dev/null; then echo OPEN;
    elif (timeout 4 bash -c 'echo > /dev/tcp/$vip/6443') 2>&1 | grep -qi refused; then echo REFUSED;
    else echo TIMEOUT; fi")
  case "$verdict" in
    UP|OPEN) ok "apiserver VIVO (healthz/port ok) → control-plane OK. Seguí con [5] y [6]." ;;
    REFUSED) no "RST: ruteo OK pero apiserver CAÍDO adentro (etcd/apiserver no arrancan; cold-boot tras crash). → paso [4]" ;;
    TIMEOUT) no "timeout/no-route: RUTEO/CNI roto o el VM no bootea. → revisar Cilium/masquerade, VMI phase, calico-vxlan" ;;
    *) no "no se pudo testear ($verdict)" ;;
  esac

  if [ "$verdict" = "REFUSED" ] || [ "$verdict" = "TIMEOUT" ]; then
    echo "  [4/6] DENTRO del VM (etcd/apiserver/kubelet) — serial console:"
    cid=$(ssht "sudo crictl ps 2>/dev/null | grep compute | grep $C | head -1 | awk '{print \$1}'")
    ssht "sudo crictl exec $cid virsh list --all 2>/dev/null | sed 's/^/    /';
          sudo crictl exec $cid sh -c 'cat \$(find /var/run/kubevirt* -name \"*serial*log*\" -o -name \"*console*log*\" 2>/dev/null | head -1) 2>/dev/null | tail -20' 2>&1 | sed 's/^/    /'"
  fi

  echo "  [5/6] Node Ready DENTRO del cluster (CNI calico-vxlan + kubelet)?"
  if kubectl -n $NS get secret ${C}-kubeconfig >/dev/null 2>&1; then
    info "kubeconfig existe → usar jump pod off-t7910: cli/fleet-test kc $C get nodes (o montar el secret en un pod)"
  else no "sin ${C}-kubeconfig (CAPI no lo emitió → control-plane nunca llegó a estar listo)"; fi

  echo "  [6/6] Addons: CRS (CNI+region-root) y CAAPH (ArgoCD):"
  kubectl -n $NS get clusterresourcesetbinding $C -o jsonpath='{range .spec.bindings[*]}    CRS {.clusterResourceSetName}={.resources[0].applied}{"\n"}{end}' 2>/dev/null
  kubectl -n $NS get helmreleaseproxy -o jsonpath='{range .items[?(@.spec.clusterRef.name=="'$C'")]}    CAAPH {.metadata.name}={.status.conditions[?(@.type=="Ready")].status}/{.status.conditions[?(@.type=="Ready")].reason}{"\n"}{end}' 2>/dev/null
  echo "  RECORDÁ: VMI-Ready / KCP-conditions / KCP-readyReplicas ENGAÑAN (stale/lag). El veredicto es [2/3]."
fi

b "RESUMEN — causas conocidas (qué mirar)"
cat <<'TXT'
  • Nodo cae de la red bajo carga      → bug NIC e1000e "Hardware Unit Hang" (offloads off). §0
  • VMs corren pero apiserver refused   → control-plane no rearranca tras crash (cold-boot). RST=ruteo OK. §6
  • DNS cross-node roto en un cluster    → CNI anidado: usar calico-vxlan (IPIP/Cilium no pasan masquerade).
  • ReplicaSchedulingFailure/faulted     → Longhorn over-provisioning (reservado vs usado), no capacidad. §4
  • CRS 'no matches for kind Application' → discovery-cache race; restart capi-controller-manager.
  • Tenant en un cluster role=management → viola el modelo CAPI; tenants sólo en regional/workload. §1
TXT
