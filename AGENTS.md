# Infra Project Rules

## Project Purpose

Ansible-managed K3s cluster bootstrap on Raspberry Pi CM4 nodes.
Target: single-node cluster `srv-rk1-01` (192.168.178.133), expandable to multi-node.

**Stack:** K3s + Cilium CNI + MetalLB + ArgoCD

**Planned:** Gateway API CRDs + cert-manager + GitOps app manifests via ArgoCD

## Repository Layout

```text
~/projects/infra/
├── AGENTS.md                        ← project rules (this file)
├── opencode.jsonc                   ← OpenCode project config (context7 local)
├── ansible.cfg
├── inventory/
│   └── hosts.ini                    ← srv-rk1-01 @ 192.168.178.133
└── playbooks/
    ├── bootstrap.yml                ← full cluster bootstrap (K3s → Cilium → MetalLB → ArgoCD)
    └── uninstall.yml                ← full teardown
└── roles/
    ├── install-k3s/                 ← K3s server/agent install
    ├── get-kubeconfig/              ← fetch kubeconfig to ~/.kube/config
    ├── install-cilium/              ← Cilium CNI via Helm (kubeProxyReplacement)
    ├── install-metallb/             ← MetalLB L2 mode, pool 192.168.178.200-210
    ├── install-argocd/              ← ArgoCD via Helm, LoadBalancer at .200
    └── uninstall/                   ← K3s uninstall script + cleanup
```

## Cluster Facts

| Key | Value |
|-----|-------|
| Node | `srv-rk1-01` / `cm4-unknow-3` |
| IP | `192.168.178.133` |
| OS | Ubuntu 24.04.3 LTS (ARM64) |
| K3s | `v1.35.1+k3s1` |
| Cilium | `1.19.2` (helm chart) |
| MetalLB | `0.15.3` — IP pool `192.168.178.200-210` |
| ArgoCD | `9.4.17` (chart) / `v3.3.6` (app) — UI at `http://192.168.178.200` |
| SSH | `dalmine@192.168.178.133`, key `~/.ssh/id_ed25519` |
| kubeconfig | `~/.kube/config` (fetched by `get-kubeconfig` role) |

## K3s Disabled Components

`servicelb`, `traefik`, `metrics-server`, `local-storage`, `flannel` (CNI),
`kube-proxy`, `network-policy`, `cloud-controller`

## Ansible Workflow

```text
cd ~/projects/infra

# Full bootstrap from scratch
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini

# Individual role (idempotent, re-run safe)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini \
  --start-at-task "Add Cilium Helm repository"

# Full teardown
ansible-playbook playbooks/uninstall.yml -i inventory/hosts.ini
```

## Golden Rules

- All roles run on `localhost` (Helm/kubectl) except `install-k3s` and `uninstall` (remote via SSH)
- Role defaults in `roles/<role>/defaults/main.yml` — change versions there
- `install-metallb` must run before `install-argocd` (MetalLB provides the LoadBalancer IP)
- `install-cilium` must run before `install-metallb` (node must be Ready first)
- `k3s_token` in `roles/install-k3s/defaults/main.yml` is a placeholder — use Ansible Vault for production
- Never kubectl-apply resources manually that Ansible manages — it will diverge

## Useful Commands

```bash
# Cluster status
kubectl get nodes -o wide
kubectl get pods -A

# ArgoCD admin password
kubectl get secret argocd-initial-admin-secret -n argocd \
  -o jsonpath='{.data.password}' | base64 -d

# SSH to node
ssh dalmine@192.168.178.133

# K3s logs on node
ssh dalmine@192.168.178.133 'sudo journalctl -u k3s -f'
```

## Available OpenCode Skills

Load these when working on the relevant component:

- `k3s` — K3s server flags, service management, node operations
- `cilium` — CNI operations, upgrades, BPF/kube-proxy replacement, troubleshooting
- `metallb` — L2/BGP modes, IP pools, L2Advertisement, troubleshooting
- `argocd` — ApplicationSets, sync waves, app management, GitOps patterns
- `k8s-debug` — systematic pod/network/node debugging (global skill)
- `platform-engineering` — Helm, Terraform, CI/CD best practices (global skill)
