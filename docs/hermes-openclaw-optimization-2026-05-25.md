# Optimización de Seguridad, Métricas y Persistencia de IA (2026-05-25)

Este documento registra los diagnósticos técnicos, las soluciones y la verificación de la optimización realizada en el stack de agentes de IA del clúster homelab (Hermes Agent y OpenClaw).

---

## 1. Persistencia de Estado de Hermes (Telegram Gateway)

### El Diagnóstico
Se detectó que al reiniciar el pod `hermes-agent-mcp`, el agente perdía la dirección postal del canal de alertas de Telegram (el Chat ID configurado con `/sethome`).
* **Causa**: El archivo `gateway.json` estaba montado como un ConfigMap de Kubernetes a través de un `subPath` directo en `/opt/data/gateway.json`. Los montajes de ConfigMap son de solo lectura, por lo que el bot no podía persistir permanentemente los Chat IDs dinámicos. En el reinicio del pod, los cambios temporales se borraban.

### Solución Implementada
* **Modificación en Ansible**: [roles/install-hermes-agent/templates/hermes-static-mcp.yaml.j2](file:///var/home/dalmine/Nextcloud/Repos/infra-ai/infra/roles/install-hermes-agent/templates/hermes-static-mcp.yaml.j2)
* **Mecanismo de Persistencia**:
  1. Se eliminó el montaje directo del ConfigMap de solo lectura del contenedor principal.
  2. En el contenedor de inicialización (`create-env-file`), se montó el ConfigMap en `/etc/hermes` y la PVC persistente en `/opt/data/.hermes`.
  3. El init container comprueba de forma idempotente si ya existe `gateway.json` en la PVC. Si no existe, copia la plantilla por defecto. Si ya existe, **no lo sobrescribe** (preservando el estado anterior).
  4. Se crea un **enlace simbólico (`symlink`)** dinámico desde el directorio writable del pod `/opt/data/gateway.json` apuntando a la PVC.
* **Resultado**: La configuración de Telegram es ahora completamente mutable y persistente, sobreviviendo a cualquier recreación de pods en K3s.

---

## 2. Exposición y Recolección de Métricas de LiteLLM

### El Diagnóstico
OpenClaw (Tito) detectó que no había métricas financieras disponibles en Prometheus, a pesar de que el proxy de LiteLLM tenía habilitados los callbacks de métricas en su configuración.
* **Causa**: Faltaba la creación de un recurso `ServiceMonitor` en el namespace `ai` que le indicara al operador de Prometheus que realizara el scraping del puerto `4000`.

### Solución Implementada
* **Modificación en Ansible**: [roles/install-litellm-proxy/tasks/main.yml](file:///var/home/dalmine/Nextcloud/Repos/infra-ai/infra/roles/install-litellm-proxy/tasks/main.yml)
* **Ajustes Realizados**:
  1. Se nombró el puerto del servicio `litellm-proxy` como `metrics`.
  2. Se añadió una tarea de Ansible para crear el recurso `ServiceMonitor` (`litellm-proxy-metrics`) en el namespace `ai` con las etiquetas de descubrimiento `release: kube-prometheus-stack`.
* **Resultado**: Prometheus ya tiene acceso a las métricas del proxy. Tito (OpenClaw) y Hermes ya pueden realizar autodiagnósticos de costes de OpenRouter usando consultas PromQL locales de solo lectura, tales como `sum(litellm_spend_metric_total)` (coste actual validado en `$1.32 USD`).

---

## 3. Desacoplamiento de Dependencias de Despliegue

### El Diagnóstico
Antes, para actualizar LiteLLM proxy se debía correr la etiqueta general `ai-hermes-deploy`, lo que provocaba reinicios colaterales e innecesarios de Hermes Agent.

### Solución Implementada
* **Modificaciones en Ansible y Makefile**:
  - Se añadieron etiquetas ultra-granulares en [playbooks/bootstrap.yml](file:///var/home/dalmine/Nextcloud/Repos/infra-ai/infra/playbooks/bootstrap.yml) (`ai-litellm-proxy`, `pihole`, `argocd`, `helm-dashboard`).
  - Se crearon nuevos comandos independientes en el [Makefile](file:///var/home/dalmine/Nextcloud/Repos/infra-ai/infra/Makefile):
    * **`make ai-litellm-proxy-deploy`**: Actualiza únicamente el proxy de LiteLLM.
    * **`make ai-hermes-agent-deploy`**: Actualiza únicamente el pod de Hermes Agent.

---

## 4. Mitigación de Vulnerabilidades y Limpieza de Recursos

### Correciones Realizadas
1. **Fijación de Versión (busybox)**: Modificado `hermes-static-mcp.yaml.j2` para fijar la versión del contenedor de inicialización de `busybox:latest` a la versión estable e inmutable `busybox:1.36`.
2. **Auto-recolección de pods de compilación**: Modificado [roles/install-nas-admin-image/tasks/main.yml](file:///var/home/dalmine/Nextcloud/Repos/infra-ai/infra/roles/install-nas-admin-image/tasks/main.yml) para usar `propagationPolicy: Background` y un TTL de 5 minutos (`ttlSecondsAfterFinished: 300`) en el Job Kaniko. Esto evita que queden pods completados huérfanos acumulándose en el clúster.
3. **Limpieza**: Se eliminaron los 10 pods completados huérfanos acumulados del namespace `build`.
