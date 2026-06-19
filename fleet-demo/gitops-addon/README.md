# GitOps addon form — install CAPI the declarative way (no clusterctl)

`clusterctl init` is **imperative** (a CLI that pushes YAML once). The GitOps-native
equivalent is the **Cluster API Operator**: you install the operator, then you
*declare* the providers as CRs (`CoreProvider`, `BootstrapProvider`,
`ControlPlaneProvider`, `InfrastructureProvider`). ArgoCD reconciles those CRs, so
CAPI providers are managed like any other addon in `platform/addons` — versioned in
Git, self-healing, no `clusterctl` on anyone's laptop.

> Same result as the `install-capi` Ansible role (CRDs + controllers in your existing
> cluster, no pivot) — just declarative instead of imperative. Pick one.

## Files
- `10-capi-operator-application.yaml` — ArgoCD Application that installs the
  cluster-api-operator Helm chart (sync-wave 0).
- `20-capi-providers.yaml` — the four provider CRs (sync-wave 1). Editing a
  `version:` here and letting ArgoCD sync is how you upgrade providers in GitOps.

## Apply (or point an ApplicationSet at this folder)
```bash
kubectl apply -f fleet-demo/gitops-addon/10-capi-operator-application.yaml
# once the operator is Healthy, ArgoCD (or you) applies the providers:
kubectl apply -f fleet-demo/gitops-addon/20-capi-providers.yaml
kubectl get coreprovider,bootstrapprovider,controlplaneprovider,infrastructureprovider -A
```

## The other two pieces, also as addons (optional)
- **KubeVirt**: an ArgoCD Application pointing at the release `kubevirt-operator.yaml`
  + a KubeVirt CR manifest (same content as the `install-kubevirt` role).
- **Crossplane**: an ArgoCD Application using the Crossplane Helm chart
  (`https://charts.crossplane.io/stable`, v2.3) + the Function + RBAC manifests.

So every layer here has BOTH forms: Ansible role (imperative) and GitOps addon
(declarative). Cert-manager must exist first (it already does in this cluster).
