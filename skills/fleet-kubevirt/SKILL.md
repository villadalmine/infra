# Skill: fleet-kubevirt — KubeVirt + CAPK + Crossplane v2 (homelab fleet)

Turn the existing homelab cluster into a Cluster API **management** cluster and
create **workload (host) clusters as VMs** on `srv-t7910`. This is the runnable
form of the "code/future" box in the architecture diagrams. **Opt-in, not in
`bootstrap.yml`.**

## Mental model
- Your cluster already exists → it **is** the management cluster. No kind, no pivot.
- KubeVirt runs VMs; CAPK (Cluster API Provider KubeVirt) turns CAPI Machines into
  VMs; Crossplane v2 gives a one-object `HostCluster` API that composes the CAPI
  objects.
- VMs only run on `srv-t7910` (only node with real KVM / `/dev/kvm`). `virt-handler`
  is pinned there by the `install-kubevirt` role.

## Roles & playbook
| Piece | What it does |
|---|---|
| `install-kubevirt` | KubeVirt operator + CR, VM workloads pinned to t7910, `useEmulation: false` |
| `install-capi` | `clusterctl init --infrastructure kubevirt` (core+kubeadm+CAPK); idempotent (skips if CAPI CRDs exist) |
| `install-crossplane` | Helm Crossplane v2.3 + `function-patch-and-transform` + ClusterRole `crossplane-compose-capi` (RBAC to compose CAPI) |
| `playbooks/fleet-kubevirt.yml` | runs the three on `localhost`; tags `kubevirt`/`capi`/`crossplane` |
| `fleet-demo/` | XRD + Composition (v2), `HostCluster` example, raw CAPI equivalent, `upgrade-example.md`, runbook |

## Versions (verify — cluster on k8s 1.36.1)
- **KubeVirt v1.8** is for k8s 1.35; **use v1.9+ for 1.36** (check the k8s support matrix). Var in defaults.
- **CAPK** `v0.11.x`. **Crossplane v2.3**. **Workload k8s** `v1.31.0` (container-disk tag must exist).

## Crossplane v2 gotchas
- XRs are **namespaced**, **no claims** — the `HostCluster` XR is the request.
- v2 composes **any** resource (CAPI `Cluster` directly), but needs **RBAC** (granted by the role).
- Native patch&transform is **gone** → Compositions use the **function pipeline**.
- Package images must be **fully-qualified**.

## Upgrade (B)
- Providers: `clusterctl upgrade plan && clusterctl upgrade apply`.
- Workload k8s: rolling **Machine replacement** (never in-place). CP first (keep quorum), workers after.
- **Immutable `KubevirtMachineTemplate`** → change image = new template + repoint `infrastructureRef`
  (the name change triggers the rollout). Via Crossplane: version-derived template name.

## Verify
```bash
kubectl -n kubevirt get kv kubevirt -o jsonpath='{.status.phase}'   # Deployed
kubectl get providers -A
kubectl -n fleet get hostcluster,cluster,machines,vmi
clusterctl get kubeconfig host-a -n fleet > /tmp/host-a.kubeconfig   # then install a CNI
```

## Gotchas seen
- Workload nodes stay **NotReady** until a CNI is applied inside the workload cluster (kubeadm ships none).
- The workload API is a Service in the mgmt cluster: ClusterIP (in-cluster) or LoadBalancer (Cilium LB-IPAM)
  to reach it from your workstation.
- `clusterctl init` is **not** idempotent on its own — the role guards it by checking for CAPI CRDs.
