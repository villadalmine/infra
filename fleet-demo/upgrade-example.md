# B — CAPI cluster upgrade flow (with KubeVirt), by example

There are **two independent upgrade axes**. Don't mix them.

## Axis 1 — Upgrade the MANAGEMENT cluster's CAPI providers (clusterctl)
This bumps the controllers (core / kubeadm / CAPK), **not** any workload cluster.

```bash
clusterctl upgrade plan          # shows what can move to a newer contract
clusterctl upgrade apply --contract v1beta1   # or the target the plan prints
```
Idempotent and safe; controllers roll one by one. Do this when you adopt a new
Cluster API or CAPK release. (KubeVirt itself is upgraded separately, via the
`install-kubevirt` role / its operator.)

## Axis 2 — Upgrade a WORKLOAD cluster's Kubernetes version (the interesting one)
CAPI does a **rolling replacement** of Machines — it never upgrades a node in place.
A node is a VM here, so "replace a Machine" = boot a new VM at the new version,
join it, cordon/drain + delete the old one. Control plane first, then workers.

### The KubeVirt gotcha: machine templates are IMMUTABLE
`KubevirtMachineTemplate` (like every CAPI infra template) **cannot be edited in place**.
To change the node image you create a **new** template (new name) and point the
controller's `infrastructureRef` at it. The name change is what triggers the rollout.

### 2a. Control plane (raw CAPI)
```bash
# 1) new CP machine template with the new container-disk image
cat <<'EOF' | kubectl apply -f -
apiVersion: infrastructure.cluster.x-k8s.io/v1alpha1
kind: KubevirtMachineTemplate
metadata: { name: host-a-control-plane-v132, namespace: fleet }
spec:
  template:
    spec:
      virtualMachineTemplate:
        spec:
          runStrategy: Always
          template:
            spec:
              nodeSelector: { kubernetes.io/hostname: srv-t7910 }
              evictionStrategy: External
              domain:
                cpu: { cores: 2 }
                memory: { guest: 4Gi }
                devices: { disks: [{ name: containervolume, disk: { bus: virtio } }] }
              volumes:
                - name: containervolume
                  containerDisk: { image: quay.io/capk/ubuntu-2404-container-disk:v1.32.0 }
EOF

# 2) point the KCP at the new template AND bump the version → rolling upgrade
kubectl -n fleet patch kubeadmcontrolplane host-a-control-plane --type merge -p '
spec:
  version: v1.32.0
  machineTemplate:
    infrastructureRef:
      name: host-a-control-plane-v132
'
# watch new CP Machine come up, old one go away (one at a time; quorum preserved if 3)
kubectl -n fleet get machines -w
```

### 2b. Workers (raw CAPI)
```bash
# new worker template (new image) ...
kubectl apply -f - <<'EOF'
apiVersion: infrastructure.cluster.x-k8s.io/v1alpha1
kind: KubevirtMachineTemplate
metadata: { name: host-a-md-0-v132, namespace: fleet }
spec: { template: { spec: { virtualMachineTemplate: { spec: { runStrategy: Always, template: { spec: {
  nodeSelector: { kubernetes.io/hostname: srv-t7910 }, evictionStrategy: External,
  domain: { cpu: { cores: 2 }, memory: { guest: 4Gi }, devices: { disks: [{ name: containervolume, disk: { bus: virtio } }] } },
  volumes: [{ name: containervolume, containerDisk: { image: quay.io/capk/ubuntu-2404-container-disk:v1.32.0 } }] } } } } } } }
EOF

# ... then bump the MachineDeployment version + ref → MaxSurge/MaxUnavailable rolling update
kubectl -n fleet patch machinedeployment host-a-md-0 --type merge -p '
spec:
  template:
    spec:
      version: v1.32.0
      infrastructureRef:
        name: host-a-md-0-v132
'
```

### 2c. The SAME upgrade via Crossplane (the clean path)
With the Composition, you only touch the `HostCluster` XR:
```bash
kubectl -n fleet patch hostcluster host-a --type merge -p '
spec:
  kubernetesVersion: v1.32.0
  nodeImage: quay.io/capk/ubuntu-2404-container-disk:v1.32.0
'
```
**But** remember the immutability gotcha: if the Composition keeps a *fixed*
template name, changing the image fails. The production fix is to make the
template name **version-derived** in the Composition (e.g. append the
`kubernetesVersion` to the `KubevirtMachineTemplate` name via a Format transform),
so a version bump produces a *new* template name and CAPI rolls automatically.

> Frase de defensa (B): *"CAPI nunca upgradea un nodo in-place: hace rolling
> replacement de Machines. Con VMs eso es bootear una VM nueva en la versión
> nueva, joinearla y descartar la vieja — control plane primero (preservando
> quórum), workers después. La sutileza KubeVirt es que los machine templates son
> inmutables: cambiás la imagen creando un template nuevo y reapuntando el
> infrastructureRef; ese cambio de nombre es lo que dispara el rollout. Vía
> Crossplane lo modelo con un nombre de template derivado de la versión."*
