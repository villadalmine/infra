# Pipeline de CI/CD: Argo Workflows + Kaniko + Ansible

Este documento explica cómo funciona el proceso de compilación (build) de imágenes en este clúster, cómo están entrelazadas las distintas herramientas (Ansible, Argo Workflows y Kaniko), y el historial de hitos en su implementación.

## Arquitectura del Proceso de Build

Históricamente, los builds se realizaban inyectando `batch/v1 Jobs` estáticos via Ansible, con almacenamiento SMB del NAS heredado. Esto generaba bloqueos sincrónicos en Ansible y cuellos de botella de I/O.

El sistema actual es un pipeline asíncrono basado en **Argo Workflows**:

1. **Ansible (El Disparador):**
   - Crea el PVC de caché (`local-path` o `smb-nas`) si no existe.
   - Aplica el CRD `Workflow` al clúster (namespace `kaniko`). Fire-and-forget para hermes, con `kubectl wait` para leloir (necesario para que `make leloir-all` encadene correctamente build → deploy).
   - En leloir: también espera la condición `phase=Succeeded` antes de retornar.

2. **Argo Workflows (El Orquestador):**
   - Detecta el `Workflow` y crea el pod de build.
   - Clona el repo via `inputs.artifacts.git` (nativo de Argo, sin initContainer extra).
   - El workspace es efímero: `volumeClaimTemplates` crea una PVC nueva por run, que se auto-elimina via `ttlStrategy` (1h éxito / 24h fallo).
   - El workspace usa `local-path` (NVMe del nodo) — desacoplado del NAS SMB lento.

3. **Kaniko (El Constructor):**
   - Corre en el pod gestionado por Argo en `srv-rk1-nvme-01`.
   - Lee el código clonado desde `/workspace/source`.
   - Usa un PVC de caché persistente (`kaniko-cache` o `leloir-kaniko-cache`) para acelerar builds consecutivos.
   - Push directo al registry in-cluster (`registry.registry:5000`).

## Builds activos

| Make target | Role Ansible | Imagen destino | Cache storage |
|-------------|-------------|----------------|---------------|
| `make ai-hermes-build` | `install-hermes-agent-image` | `ai/hermes-agent:v2026.5.16-telegram` | `smb-nas` 60Gi |
| `make leloir-build` | `install-leloir-image` | `ai/leloir-controlplane:latest` | `local-path` 10Gi |
| `make ai-hermes-build-remote` | `install-hermes-agent-remote-build` | idem hermes | GitHub Actions |

## Dependencias de Make

```
make leloir-all
├── make ai-registry       # registry in-cluster
├── make argo-workflows    # instala Argo + RBAC para namespace kaniko
├── make leloir-build      # dispara Argo Workflow + espera Succeeded
└── make leloir            # Postgres + Deployment rolling update

make argo-workflows
└── tags: [argo-workflows] en bootstrap.yml
    ├── install-argo-workflows  # Helm + HTTPRoute + RBAC kaniko SA
    └── (disponible también dentro de make services)
```

---

## Historial de Hitos y Troubleshooting

| Fecha / Hito | Problema | Solución |
|---|---|---|
| **Paso 1:** Reemplazo Job → Argo Workflow (hermes) | Ansible se bloqueaba esperando el `Job` o fallaba si no se purgaba primero. | Se instaló `argo-workflows` (Helm, modo server) y se refactorizó `install-hermes-agent-image` para desplegar un `Workflow` CRD. |
| **Paso 2:** Offload remoto via GitHub Actions | Builds de hermes muy pesados para el cluster. | Rol `install-hermes-agent-remote-build`: workflow de dos pasos `gh-trigger` → `skopeo-sync`. Auto-fetch del token via `gh auth token`. |
| **Paso 3:** Deadlocks en Storage SMB | PV de `smb-nas` quedaba en `Released` bloqueando recreación del caché. | Se depuró `install-cifs-nas` eliminando el `claimRef` residual del PV. |
| **Paso 4:** CRD Tolerations Merge Key Error | `Init:Error`: `map[operator:Exists] does not contain declared merge key: key`. | Se agregó `key: node.kubernetes.io/unreachable` explícita en todas las tolerations de todos los Workflow specs. |
| **Paso 5:** Conflicto volumen + git clone | `file exists` al clonar en `/workspace/source` porque el Dockerfile de ConfigMap estaba montado allí. | Dockerfile montado en `/workspace/Dockerfile` (fuera del path de git clone). |
| **Paso 6:** Migración `install-leloir-image` Job → Argo Workflow | `install-leloir-image` aún usaba `batch/v1 Job` con initContainer manual. No aparecía en Argo UI, sin retry nativo. | Migrado a `argoproj.io/v1alpha1 Workflow` con `inputs.artifacts.git`. Agrega `ttlStrategy` (1h/24h). Makefile actualizado: `argo-workflows` target standalone, `leloir-all` incluye `make argo-workflows` como prerequisito. |
| **Paso 7:** RBAC `workflowtaskresults` forbidden | Nuevo namespace `kaniko` sin Role para el SA `default` → Argo no puede reportar el estado del workflow. Error: `workflowtaskresults.argoproj.io is forbidden`. | Role `argo-workflow-runner` + RoleBinding para `default` SA en `kaniko` ns. Persistido en `install-argo-workflows` via variable `argo_workflow_namespaces`. Idempotente via `make argo-workflows`. |
| **Paso 8:** DiskPressure por PVCs acumulados | Build fallaba con exit code 137 + `ephemeral-storage` eviction en `srv-rk1-nvme-01`. PVCs de workspace de workflows fallidos + PVC orphanado del viejo Job (`leloir-kaniko-workspace`) acumularon espacio. | Borrar workflows fallidos/completados libera sus workspace PVCs (reclaim: Delete). PVC orphanada del Job viejo eliminada manualmente. TTL strategy previene acumulación futura. |

---

## Guías de referencia

- Comandos de uso y troubleshooting operacional: `.agents/workflows/argo-kaniko-build.md`
- Argo Workflows UI: `https://argo.cluster.home`

## Siguientes Pasos

- Evaluar `CronWorkflow` para re-build automático semanal de imágenes base.
- Si se agregan más builds (ej. leloir mcp-gateway, webhook-receiver), evaluar crear un `WorkflowTemplate` reutilizable para unificar el patrón en lugar de mantener specs inline por rol.
