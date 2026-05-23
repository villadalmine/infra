# Argo Workflows Kaniko Build

## Descripción
Este workflow detalla cómo interactuar con el pipeline de build basado en Argo Workflows y Kaniko para construir imágenes ARM64 en el clúster K3s (infra-ai). Cubre todos los builds activos: `leloir-controlplane` y `hermes-agent`, y el modo de offload remoto via GitHub Actions.

## Contexto
Ansible actúa como disparador: crea PVCs de caché, aplica el `Workflow` CRD y opcionalmente espera a que termine. Argo Workflows orquesta el pod usando `inputs.artifacts.git` para clonar el repositorio antes de que arranque el container principal de Kaniko. El workspace es efímero (`local-path`, auto-limpiado via TTL); el caché de capas es persistente (PVC nombrado por build).

**Builds disponibles:**

| Make target | Imagen | Cache PVC | Storage workspace |
|-------------|--------|-----------|-------------------|
| `make leloir-build` | `ai/leloir-controlplane:latest` | `leloir-kaniko-cache` (local-path, 10Gi) | `local-path` 5Gi |
| `make ai-hermes-build` | `ai/hermes-agent:v2026.5.16-telegram` | `kaniko-cache` (smb-nas, 60Gi) | `local-path` 40Gi |
| `make ai-hermes-build-remote` | idem, offload a GitHub Actions | — | GitHub-hosted runner |

---

## Comandos de uso

### Disparar un build
```bash
# Leloir control plane (~5 min)
make leloir-build

# Hermes Agent (~15 min)
make ai-hermes-build

# Hermes via GitHub Actions offload (requiere gh CLI autenticado)
make ai-hermes-build-remote
# Si no tenés gh CLI: make ai-hermes-build-remote GITHUB_PAT=ghp_...
```

### Monitorear progreso
```bash
# Ver todos los workflows activos
kubectl get workflows -n kaniko

# Ver pods del build
kubectl get pods -n kaniko

# Logs del build de leloir
kubectl logs -l app=leloir-build -c main -n kaniko -f

# Logs del build de hermes
kubectl logs -l app=hermes-agent-build -c main -n kaniko -f

# Logs del init (git clone / gh-trigger)
kubectl logs -l app=leloir-build -c init -n kaniko
```

### Limpiar workflows completados/fallidos
```bash
# Ver todos los workflows (incluye completados)
kubectl get workflows -n kaniko

# Borrar un workflow específico (libera su workspace PVC si tiene TTL)
kubectl delete workflow -n kaniko <nombre>

# Borrar todos los completados/fallidos
kubectl delete workflow -n kaniko --field-selector status.phase=Failed
kubectl delete workflow -n kaniko --field-selector status.phase=Succeeded
```

---

## Prerequisitos para que los builds funcionen

### RBAC del namespace `kaniko`
El SA `default` en el namespace `kaniko` necesita permisos para crear `workflowtaskresults`. Esto se aplica automáticamente con `make argo-workflows` (idempotente). Si se rompe, reaplicar:

```bash
make argo-workflows
```

O aplicar manualmente:
```bash
kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: argo-workflow-runner
  namespace: kaniko
rules:
- apiGroups: ["argoproj.io"]
  resources: ["workflowtaskresults"]
  verbs: ["create", "patch"]
- apiGroups: [""]
  resources: ["pods", "pods/log"]
  verbs: ["get", "watch", "patch", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: argo-workflow-runner
  namespace: kaniko
subjects:
- kind: ServiceAccount
  name: default
  namespace: kaniko
roleRef:
  kind: Role
  name: argo-workflow-runner
  apiGroup: rbac.authorization.k8s.io
EOF
```

### PVC de caché
Cada build necesita su PVC de caché pre-creada por Ansible. Si falta:
```bash
make leloir-build   # Ansible la crea si no existe (idempotente)
```

---

## Solución de Problemas (Troubleshooting)

### `workflowtaskresults.argoproj.io is forbidden`
**Causa:** El SA `default` del namespace `kaniko` no tiene el Role `argo-workflow-runner`.
**Fix:** `make argo-workflows` (crea el Role/RoleBinding en todos los namespaces de workflow).

### `map[operator:Exists] does not contain declared merge key: key`
**Causa:** Argo usa Strategic Merge Patch para toleration globals. Requiere `key` explícita en cada toleration.
**Estado:** Resuelto en todos los roles — cada toleration tiene `key: node.kubernetes.io/unreachable` o `key: node.kubernetes.io/not-ready`.

### `file exists` durante git clone
**Causa:** Un ConfigMap montado dentro de `/workspace/source` choca con el directorio que Argo usa para el git clone.
**Fix:** Montar archivos externos en `/workspace/<archivo>` (fuera de `/workspace/source`). Ver hermes como referencia: `mountPath: /workspace/Dockerfile`.

### Workflow falla con `ephemeral-storage` / `DiskPressure`
**Causa:** El nodo de build (`srv-rk1-nvme-01`) tiene DiskPressure — Kubernetes mide los container writable layers y logs contra el threshold de ephemeral storage.
**Síntoma:** `The node was low on resource: ephemeral-storage. Threshold quantity: 1533718755, available: ~1.4GB`. Pod evicted con exit code 137.
**Fix:**
```bash
# 1. Borrar workflows completados/fallidos (libera workspace PVCs acumulados)
kubectl delete workflow -n kaniko --field-selector status.phase=Failed
kubectl delete workflow -n kaniko --field-selector status.phase=Succeeded

# 2. Borrar PVCs orphanados del namespace kaniko (de Jobs viejos)
kubectl get pvc -n kaniko   # identificar PVCs sin workflow dueño
kubectl delete pvc -n kaniko <nombre>

# 3. Verificar que el taint se levantó
kubectl describe node srv-rk1-nvme-01 | grep DiskPressure

# 4. Reintentar el build cuando DiskPressure=False
make leloir-build
```

**Prevención:** El TTL strategy (1h success / 24h failure) en los Workflows limpia las workspace PVCs automáticamente. **No acumular** workflows fallidos sin atender.

### PVC `Terminating` bloqueada
**Causa:** Un finalizer del `local-path` provisioner no se libera hasta que el pod que monta el PVC termina. Si el pod fue evicted, puede quedar colgado.
**Fix:** Esperar unos minutos. Si persiste más de 10 min, forzar:
```bash
kubectl patch pvc <nombre> -n kaniko -p '{"metadata":{"finalizers":null}}'
```

### Build de Leloir específicamente — contexto del monorepo
El `Dockerfile.controlplane` requiere que el contexto de build sea la **raíz del repo** (no `leloir-core/`) porque `go.mod` tiene `replace ../leloir-sdk`. El workflow está configurado correctamente con `--context=dir:///workspace/source` y `--dockerfile=/workspace/source/leloir-core/Dockerfile.controlplane`. No cambiar estos paths.

---

## Scenario: Remote Compilation (GitHub Actions Offloading) — Hermes only

```bash
# Automático con gh CLI autenticado localmente
make ai-hermes-build-remote

# Manual con PAT
make ai-hermes-build-remote GITHUB_PAT=ghp_...
```

Usa un workflow de dos pasos: `gh-trigger` → `skopeo-sync`. Si `gh-trigger` falla:
```bash
kubectl logs -l app=hermes-agent-build-remote -c main -n kaniko
# Fix común: verificar que el GitHub Actions pipeline esté verde y el token tenga scope workflow
```
