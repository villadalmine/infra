# Tailscale Multi-site: K3s + Cilium Mesh

Documento de planificación para experimentar conceptos de multi-cloud / multi-site
usando hardware doméstico: un nodo en casa propia y un nodo en casa de un amigo,
conectados via Tailscale.

**Estado:** borrador / ideas — aún no implementado.

---

## Objetivo

- Sumar un worker en una red diferente (casa del amigo) al cluster K3s actual
- Aprender en la práctica cómo Cilium maneja networking entre sitios
- Probar Cilium Cluster Mesh: dos clusters federados, service discovery cross-site
- HA real: si el nodo master cae, el cluster sigue operando
- Simular en casa los mismos conceptos que se usan en entornos multi-cloud (GKE + EKS,
  dos regiones AWS, etc.) pero con Raspberry Pis y un amigo

---

## Topología objetivo

### Fase 1 — Single cluster, dos sitios

```
╔══════════════════════════════════╗       ╔══════════════════════════════════╗
║         SITE A (tu casa)         ║       ║       SITE B (casa del amigo)    ║
║                                  ║       ║                                  ║
║  ┌────────────────────────┐      ║       ║  ┌────────────────────────┐      ║
║  │  srv-rk1-01 (master)   │      ║       ║  │  srv-rk1-03 (worker)   │      ║
║  │  LAN:  192.168.178.133 │      ║       ║  │  LAN:  192.168.y.y.x   │      ║
║  │  TS:   100.x.x.1       │      ║       ║  │  TS:   100.x.x.3       │      ║
║  └───────────┬────────────┘      ║       ║  └────────────┬───────────┘      ║
║              │ K3s API :6443     ║       ║               │ K3s agent        ║
║  ┌───────────▼────────────┐      ║       ║               │                  ║
║  │  srv-rk1-02 (worker)   │      ║       ║               │                  ║
║  │  LAN:  192.168.178.x   │      ║       ║               │                  ║
║  │  TS:   100.x.x.2       │      ║       ║               │                  ║
║  └────────────────────────┘      ║       ║               │                  ║
║                                  ║       ║               │                  ║
╚══════════╤═══════════════════════╝       ╚═══════════════╤══════════════════╝
           │                                               │
           │          ┌─────────────────────┐              │
           └──────────►   Tailscale overlay  ◄─────────────┘
                      │   (WireGuard L3)     │
                      │                      │
                      │  VIP: 100.x.x.100    │  ← kube-vip (leader election)
                      │  DNS: *.cluster.home │  ← Pi-hole → VIP
                      └─────────────────────┘
```

### Fase 2 — Cluster Mesh: dos clusters independientes

```
╔══════════════════════════════════╗       ╔══════════════════════════════════╗
║     CLUSTER A (tu casa)          ║       ║     CLUSTER B (casa del amigo)   ║
║     Pod CIDR: 10.10.0.0/16       ║       ║     Pod CIDR: 10.20.0.0/16       ║
║                                  ║       ║                                  ║
║  ┌─────────────────────────┐     ║       ║  ┌─────────────────────────┐     ║
║  │  Cilium                 │     ║       ║  │  Cilium                 │     ║
║  │  clustermesh-apiserver  ├─────╫───────╫──►  clustermesh-apiserver  │     ║
║  │  :2379                  │     ║       ║  │  :2379                  │     ║
║  └─────────────────────────┘     ║       ║  └─────────────────────────┘     ║
║                                  ║       ║                                  ║
║  Services exportados:            ║       ║  Services exportados:            ║
║    ServiceExport: redis          ║       ║    ServiceExport: postgres        ║
║                                  ║       ║                                  ║
╚══════════════════════════════════╝       ╚══════════════════════════════════╝
           │                                               │
           └──────────────── Tailscale ────────────────────┘
                          (WireGuard L3 tunnel)
```

---

## Por qué la config actual NO funciona en multi-site

| Componente | Config actual | Problema en multi-site | Cambio necesario |
|---|---|---|---|
| **L2 Announcements** | `l2announcements.enabled: true` | ARP es capa 2 — no cruza redes. El worker remoto nunca ve los anuncios del master. Si el lease cae en el worker remoto, el VIP `.200` queda muerto | Desactivar. Reemplazar con kube-vip |
| **LB-IPAM pool** | `192.168.178.200-210` | Son IPs de la LAN local, no routable desde Tailscale ni desde la red del amigo | Pool con IPs Tailscale-routable (ej. `100.x.x.100-110`) |
| **`k3s_api_server_host`** | `192.168.178.133` | El worker remoto no puede llegar a esa IP | Tailscale IP del master (`100.x.x.1`) |
| **`k8sServiceHost` en Cilium** | `192.168.178.133` | Mismo problema — hardcodeado a LAN IP | Tailscale IP del master |
| **Pi-hole DNS** | `*.cluster.home → 192.168.178.200` | `.200` no es alcanzable desde fuera de la LAN local | `*.cluster.home → 100.x.x.100` (kube-vip VIP) |
| **Cilium tunnel mode** | `L2` (default single-node) | Sin encapsulación cross-site — los pods de Site B no pueden hablar con pods de Site A | `tunnel: vxlan` o `geneve` sobre Tailscale |
| **TLS SAN en K3s** | Solo LAN IP | El kubeconfig generado no acepta conexiones via Tailscale IP | Agregar `--tls-san <tailscale-ip>` |

---

## Fase 1: Multi-node single-cluster sobre Tailscale

### Prerequisitos

- Tailscale instalado y activo en todos los nodos
- IPs Tailscale estables (Tailscale permite fijar IPs en la consola de admin)
- El amigo tiene el nodo en la subnet Tailscale compartida

### 1. K3s: bind a Tailscale

Cambios en `roles/install-k3s/defaults/main.yml`:

```yaml
# Flags adicionales para el server:
k3s_server_extra_args: >-
  --node-ip {{ tailscale_ip }}
  --tls-san {{ tailscale_ip }}
  --advertise-address {{ tailscale_ip }}
  --flannel-iface tailscale0

# Flags para los agents:
k3s_agent_extra_args: >-
  --node-ip {{ tailscale_ip }}
  --flannel-iface tailscale0
```

El `k3s_api_server_host` en Cilium pasa a ser la Tailscale IP del master.

### 2. Cilium: VXLAN tunnel sobre Tailscale

Cambios en `roles/install-cilium/defaults/main.yml`:

```yaml
k8sServiceHost: "100.x.x.1"        # Tailscale IP del master
k8sServicePort: "6443"

# Encapsulación cross-node (necesario en L3):
cilium_tunnel_mode: "vxlan"         # nuevo valor
cilium_native_routing_cidr: "100.64.0.0/10"  # rango Tailscale

# Desactivar L2 Announcements (no funciona en L3/Tailscale):
cilium_l2announcements_enabled: false

# Auto-routing entre nodos via Tailscale:
cilium_auto_direct_node_routes: true
```

### 3. kube-vip: VIP L3 sin ARP

kube-vip reemplaza Cilium L2 Announcements para el LoadBalancer VIP.
Corre como DaemonSet y usa leader election de Kubernetes (Leases) para elegir
qué nodo responde por el VIP — sin ARP, sin BGP, puro L3.

Nuevo rol: `install-kube-vip`

```yaml
# roles/install-kube-vip/defaults/main.yml
kube_vip_version: "v0.8.x"
kube_vip_vip: "100.x.x.100"        # IP libre en la subnet Tailscale
kube_vip_interface: "tailscale0"    # interface donde anuncia el VIP
kube_vip_enable_arp: false          # L3 mode — sin ARP
kube_vip_enable_bgp: false
```

```yaml
# Manifest que genera el rol (DaemonSet en kube-system):
env:
  - name: vip_arp
    value: "false"
  - name: vip_interface
    value: "tailscale0"
  - name: address
    value: "100.x.x.100"
  - name: vip_leaderelection
    value: "true"
```

### 4. LB-IPAM pool: IPs Tailscale

```yaml
# roles/install-cilium-pools/defaults/main.yml
cilium_lb_pool_start: "100.x.x.100"
cilium_lb_pool_stop: "100.x.x.110"
```

### 5. Pi-hole DNS

```yaml
# roles/install-pihole/defaults/main.yml
pihole_custom_dns: "address=/cluster.home/100.x.x.100"
```

### 6. Inventory: multi-site

```ini
# inventory/hosts.ini
[servers]
srv-rk1-01 ansible_host=100.x.x.1 tailscale_ip=100.x.x.1

[agents]
srv-rk1-02 ansible_host=100.x.x.2 tailscale_ip=100.x.x.2
srv-rk1-03 ansible_host=100.x.x.3 tailscale_ip=100.x.x.3   # nodo del amigo
```

### Checklist de verificación — Fase 1

```bash
# Todos los nodos Ready
kubectl get nodes -o wide

# Pods cross-node se comunican
kubectl run -it --rm pingtest --image=nicolaka/netshoot -- \
  ping <ip-de-pod-en-nodo-remoto>

# VIP responde (desde cualquier nodo Tailscale)
curl -sk https://argocd.cluster.home

# kube-vip leader election activo
kubectl -n kube-system get lease | grep kube-vip

# Cilium conectividad cross-node
kubectl -n kube-system exec ds/cilium -- cilium status
kubectl -n kube-system exec ds/cilium -- cilium node list
```

---

## Fase 2: Cilium Cluster Mesh

### Qué es

Cilium Cluster Mesh federa dos o más clusters Kubernetes independientes.
Cada cluster mantiene su propio control plane (etcd, API server, etc.)
pero comparte service discovery y puede aplicar network policies cross-cluster.

### Qué habilita para experimentar

| Feature | Descripción |
|---|---|
| **ServiceExport / ServiceImport** | Exportar un Service de Cluster A e importarlo en Cluster B como si fuera local |
| **Global Services** | Un Service con el mismo nombre en ambos clusters actúa como uno solo con failover automático |
| **Network Policy cross-cluster** | `CiliumNetworkPolicy` puede referenciar identidades del otro cluster |
| **Observabilidad unificada** | Hubble ve flujos cross-cluster en una sola vista |
| **Failover automático** | Si todos los endpoints de un Service caen en Cluster A, el tráfico va a Cluster B |

### Requerimientos previos

- **Pod CIDRs distintos** — no se pueden solapar entre clusters:
  - Cluster A: `10.10.0.0/16` (pods), `10.11.0.0/16` (services)
  - Cluster B: `10.20.0.0/16` (pods), `10.21.0.0/16` (services)
- El puerto **2379** del `clustermesh-apiserver` debe ser alcanzable entre clusters (via Tailscale)
- Misma versión de Cilium en ambos clusters
- `cluster-id` único por cluster (1-255)

### Activación via Helm

```yaml
# Cluster A — values adicionales en install-cilium:
cluster:
  name: cluster-a
  id: 1
clustermesh:
  useAPIServer: true
  apiserver:
    service:
      type: LoadBalancer    # expone el apiserver via LB-IPAM
```

```yaml
# Cluster B:
cluster:
  name: cluster-b
  id: 2
clustermesh:
  useAPIServer: true
  apiserver:
    service:
      type: LoadBalancer
```

```bash
# Conectar los dos clusters (desde kubectl con acceso a ambos):
cilium clustermesh connect \
  --context cluster-a \
  --destination-context cluster-b

# Verificar
cilium clustermesh status --context cluster-a
```

### Ejemplo: Global Service con failover

```yaml
# En ambos clusters — Service con la misma anotación:
apiVersion: v1
kind: Service
metadata:
  name: redis
  annotations:
    service.cilium.io/global: "true"
    service.cilium.io/shared: "true"
spec:
  selector:
    app: redis
  ports:
    - port: 6379
```

Si Redis cae en Cluster A, el tráfico va automáticamente a Cluster B sin cambios en los clientes.

---

## Preguntas abiertas

Estas decisiones hay que tomar antes de implementar:

- [ ] **Tailscale IPs concretas** de cada nodo (fijarlas en la consola Tailscale admin para que no cambien)
- [ ] **Hardware del amigo**: ¿qué tiene? ¿Raspberry Pi? ¿x86? ¿ARM64 o AMD64? (afecta la imagen K3s)
- [ ] **¿Un solo cluster (Fase 1) o dos clusters federados (Fase 2) directamente?**
- [ ] **Pod CIDRs**: definir rangos que no se solapen con ninguna LAN ni con el rango Tailscale (`100.64.0.0/10`)
- [ ] **¿kube-vip o NodePort + DNS directo?** Para experimentar, DNS directo a la Tailscale IP del master es más simple. kube-vip agrega HA real pero es otra pieza.
- [ ] **Tailscale ACLs**: ¿el nodo del amigo puede llegar al puerto 6443 del master? ¿Y al 2379 para Cluster Mesh?
- [ ] **¿Incluir al amigo en el repo infra** (como co-maintainer) o manejar su nodo de forma independiente?

---

## Referencias

- [Cilium Cluster Mesh docs](https://docs.cilium.io/en/stable/network/clustermesh/)
- [Cilium Multi-cluster networking](https://docs.cilium.io/en/stable/network/clustermesh/clustermesh/)
- [kube-vip docs](https://kube-vip.io/docs/)
- [kube-vip con K3s](https://kube-vip.io/docs/usage/k3s/)
- [Tailscale Kubernetes operator](https://tailscale.com/kb/1236/kubernetes-operator)
- [K3s multi-server HA](https://docs.k3s.io/datastore/ha-embedded)
- [Cilium Tunnel modes](https://docs.cilium.io/en/stable/network/concepts/routing/)
