# Pipeline de CI/CD: Argo Workflows + Kaniko + Ansible

Este documento explica cómo funciona el proceso de compilación (build) de imágenes en este clúster, cómo están entrelazadas las distintas herramientas (Ansible, Argo Workflows y Kaniko), y el historial de hitos en su implementación.

## Arquitectura del Proceso de Build

Históricamente, los builds de la infraestructura se realizaban directamente inyectando `Jobs` estáticos de Kubernetes mediante Ansible, y utilizando el almacenamiento SMB1 de un NAS heredado. Esto presentaba cuellos de botella de I/O importantes. 

El proceso ha evolucionado hacia un pipeline asíncrono y robusto basado en **Argo Workflows**:

1. **Ansible (El Disparador):** 
   - Sigue siendo la única fuente de verdad y el encargado de la configuración. 
   - A través del comando `make ai-hermes-build` (que llama al playbook con tags específicos), Ansible crea los `ConfigMaps` con los Dockerfiles y aplica el CRD `Workflow` de Argo en el clúster (namespace `kaniko`).
   - *Ventaja:* Ansible no se queda esperando sincrónicamente el resultado del build, liberando el hilo de ejecución.

2. **Argo Workflows (El Orquestador):**
   - Detecta la creación del `Workflow` y orquesta la creación de pods.
   - Consta de pasos bien definidos. Primero ejecuta un contenedor `init` que se encarga de clonar los repositorios (`inputs.artifacts.git`) directamente a un volumen temporal (usando la `StorageClass: local-path`).
   - El volumen del `workspace` se asocia al almacenamiento NVMe rápido del nodo, desconectando el I/O intensivo de Kaniko del lento protocolo SMB1 del NAS.

3. **Kaniko (El Constructor):**
   - Se ejecuta dentro del pod gestionado por Argo. 
   - Utiliza el directorio clonado por Argo en `/workspace/source`.
   - Utiliza un `PersistentVolumeClaim` estático llamado `kaniko-cache` (que sí reside en el NAS SMB o almacenamiento dedicado a elección) para guardar las *cachés de las capas de Docker*, acelerando significativamente los builds consecutivos.
   - Realiza un push directo al `registry` interno del clúster (`registry.registry:5000`).

## Hitos y Solución de Problemas (Troubleshooting History)

| Fecha / Hito | Problema | Solución Implementada (Commit) |
| --- | --- | --- |
| **Paso 1:** Reemplazo de Job por Argo Workflow | Ansible se bloqueaba esperando el estado de `Job` o fallaba si no se purgaba. | Se instaló `argo-workflows` (modo server) y se refactorizó el rol `install-hermes-agent-image` para desplegar un `Workflow`. |
| **Paso 2:** GitHub Actions Auth & Automatic Token Fetch | N/A | To trigger GitHub Actions from inside the cluster, the `gh-trigger` Argo container relies on the `github-pat-secret`. **User Convenience (Auto-Fetching):** If you have the `gh` CLI natively authenticated on your local machine (`gh auth status` shows an active token), the `Makefile` will **automatically** extract your token and pass it to the cluster: `make ai-hermes-build-remote`. If you don't have `gh` CLI installed, you can pass it manually: `make ai-hermes-build-remote GITHUB_PAT=ghp_...`. **Implementation Detail:** The `gh-trigger` Argo pod uses the minimal `alpine:latest` image and installs `github-cli` dynamically via `apk add github-cli`. This avoids relying on `ghcr.io/cli/cli`, which sometimes blocks anonymous pulls and causes `ErrImagePull`. |
| **Paso 3:** Deadlocks en Storage SMB | El PV de `smb-nas` se quedaba en estado `Released` impidiendo la creación del caché o el workspace de build. | Se depuró la lógica de Ansible `install-cifs-nas` eliminando el `claimRef` residual del PV, logrando redespliegues limpios y exitosos. |
| **Paso 4:** CRD Tolerations Merge Key Error | Argo Workflows devolvía `Init:Error` o `Error` indicando `map: map[operator:Exists] does not contain declared merge key: key`. | Se parcheó el `workflow-controller-configmap` y los manifiestos de Ansible asignando una clave explícita (`node.kubernetes.io/unreachable`) para que el parche estratégico de Kubernetes no fallase al hacer merge. |
| **Paso 5:** Conflicto de Volúmenes y Git Clone | El contenedor `init` de Argo daba el error `file exists` al intentar clonar el repo Git en `/workspace/source` porque ahí ya estaba montado el `Dockerfile`. | Se modificó el montaje del ConfigMap para inyectar el Dockerfile en `/workspace/Dockerfile` (afuera de la carpeta clonada), separando el código fuente inyectado por Argo de la receta inyectada por Ansible. |

## Tareas Completadas Recientemente
- Implementación de **GitHub Actions** offload mediante el rol `install-hermes-agent-remote-build`.
- Extracción automática de tokens OAuth nativos con la CLI de GitHub (`gh auth token`) para facilitar la ejecución remota de compilaciones mediante el Makefile.

## Siguientes Pasos (Roadmap Extendido)
- Considerar integrar notificaciones directas desde GitHub Actions de regreso a Slack/Telegram mediante OpenClaw.
- Optimizar la limpieza de imágenes estancadas y workflows finalizados (Garbage Collection de Argo).
