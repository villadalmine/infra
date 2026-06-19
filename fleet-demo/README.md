# fleet-demo — KubeVirt + CAPK + Crossplane v2 on the homelab

Turn the **existing** homelab cluster into a Cluster API **management** cluster and
create **workload (host) clusters as VMs** on `srv-t7910`. No kind, no pivot — your
cluster already exists, so it *is* the management cluster.

```
HostCluster (Crossplane v2 XR, namespaced)
        │  Composition (function-patch-and-transform)
        ▼
Cluster + KubevirtCluster + KubeadmControlPlane + KubevirtMachineTemplate
        + MachineDeployment + KubeadmConfigTemplate          (Cluster API objects)
        │  CAPK
        ▼
KubeVirt VMs on srv-t7910  ──►  a real Kubernetes workload cluster
```

## 0. Versions (verify before running — cluster is on k8s 1.36.1)
| Component | Pin | Note |
|---|---|---|
| KubeVirt | `v1.8.0` | built for k8s 1.35; **for 1.36 use v1.9+** (check the k8s support matrix) |
| Cluster API / CAPK | latest / `v0.11.1` | `clusterctl version`, CAPK releases page |
| Crossplane | `2.3.0` | v2 = namespaced XRs, no claims, composes any resource |
| Workload k8s | `v1.31.0` | must have a `quay.io/capk/ubuntu-2404-container-disk` tag |

## 1. Install the stack (Ansible)
```bash
ansible-playbook playbooks/fleet-kubevirt.yml -i inventory/hosts.ini
# selective: --tags kubevirt | capi | crossplane
```
Verify:
```bash
kubectl -n kubevirt get kv kubevirt -o jsonpath='{.status.phase}'   # Deployed
kubectl get providers -A                                            # core/bootstrap/cp/kubevirt
kubectl get pods -n crossplane-system                               # crossplane + function
```

## 2. Create a host cluster (Crossplane path)
```bash
kubectl apply -f fleet-demo/00-namespace.yaml
kubectl apply -f fleet-demo/10-xrd-hostcluster.yaml
kubectl apply -f fleet-demo/20-composition-hostcluster.yaml
kubectl apply -f fleet-demo/50-hostcluster-example.yaml

kubectl -n fleet get hostcluster,cluster,machines,kubevirtmachines
kubectl -n fleet get vmi          # the VMs, scheduled on srv-t7910
```

### …or the raw CAPI path (no Crossplane, to prove CAPK alone)
```bash
kubectl apply -f fleet-demo/00-namespace.yaml
kubectl apply -f fleet-demo/raw-capi-cluster.yaml
# canonical generator (always matches the installed contract):
#   clusterctl generate cluster host-a -n fleet --infrastructure kubevirt \
#     --kubernetes-version v1.31.0 --control-plane-machine-count 1 --worker-machine-count 1
```

## 3. Get the workload kubeconfig + finish the cluster
```bash
clusterctl get kubeconfig host-a -n fleet > /tmp/host-a.kubeconfig
KUBECONFIG=/tmp/host-a.kubeconfig kubectl get nodes   # NotReady until a CNI is installed
```
**Install a CNI inside the workload cluster** (kubeadm clusters have none):
```bash
KUBECONFIG=/tmp/host-a.kubeconfig kubectl apply -f \
  https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml
```
(For production, automate this with a `ClusterResourceSet` so every new host cluster
gets its CNI — and its ArgoCD seed — automatically.)

## 4. Upgrade (B)
See [`upgrade-example.md`](upgrade-example.md) — control-plane-then-workers rolling
replacement, the immutable-template gotcha, and the one-line Crossplane version bump.

## 5. Tear down
```bash
kubectl -n fleet delete hostcluster host-a       # Crossplane path (cascades to CAPI + VMs)
# raw path: kubectl delete -f fleet-demo/raw-capi-cluster.yaml
```

## Notes / caveats
- **Why t7910 only:** KubeVirt needs hardware virtualization (`/dev/kvm`). The ARM
  CM4/RK1/Pi nodes don't host VMs; `virt-handler` is pinned to t7910 by the role.
- **Networking:** each workload node VM is a pod on the management pod network; the
  workload API is exposed via a Service (ClusterIP here). Use `LoadBalancer`
  (Cilium LB-IPAM) in `KubevirtCluster.controlPlaneServiceTemplate` to reach it
  from your workstation.
- **This is opt-in / not committed.** It is the runnable form of the "code / future"
  box in the architecture diagrams — the homelab path to the fleet model.
