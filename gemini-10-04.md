# Informe de Errores y Cambios - 10 de Abril

Este archivo resume cómo se han modificado (y en algunos casos, roto) los componentes de Hermes, OpenClaw y el proceso de Kaniko durante esta sesión.

## 1. Kaniko y el Proceso de Build
**Cambio realizado**: Se reordenaron las tareas en `roles/install-hermes-agent-image/tasks/main.yml`.
- **Por qué**: Se detectó que el Job de Kaniko empezaba a construir utilizando una versión anterior del `Dockerfile` porque el `ConfigMap` se actualizaba *después* de lanzar el Job.
- **Resultado**: Ahora el build es más coherente, pero las imágenes resultantes (`v1.0.4` y `v1.0.5`) siguen sin funcionar correctamente por problemas de dependencias internas.

## 2. Cómo se rompió Hermes
**Estado actual**: El pod está en `1/2` (el contenedor `hermes-agent-mcp` está en `CrashLoopBackOff`).
- **Error introducido**: `ModuleNotFoundError: No module named 'langchain_openai'`.
- **Intento de solución fallido**:
    - Se añadió `langchain-openai` al `Dockerfile`.
    - Se eliminó el modo editable (`pip install -e .`) instalando el paquete de forma estática.
    - Se subieron las versiones a `v1.0.4` y `v1.0.5`.
- **Por qué sigue roto**: Existe un desajuste entre el entorno de ejecución de Python en Debian 13 y la ubicación de las librerías instaladas por `pip`, lo que impide que el servidor MCP arranque.

## 3. Cómo se modificó OpenClaw
**Estado actual**: Arranca (`2/2`), pero la comunicación con los subagentes MCP falla.
- **Cambios en la configuración**:
    - Se cambió la estructura de `plugins` en `openclaw-configmap.yaml.j2` de objeto a lista. Esto arregló el crash inicial del gateway, pero alteró la forma en que se cargan los plugins.
    - Se cambió el puerto de acceso a Hermes de `7860` a `8000`.
    - Se modificó el `systemPromptOverride` para forzar al modelo a usar herramientas.
- **Fallo de comunicación**: Aunque el gateway indexa las herramientas, el bot sigue alegando "falta de acceso".

## 4. Resumen de archivos afectados
- `roles/install-hermes-agent-image/templates/Dockerfile.j2`
- `roles/install-hermes-agent-image/tasks/main.yml`
- `roles/install-hermes-agent/defaults/main.yml`
- `roles/install-hermes-agent/templates/hermes-deployment.yaml.j2`
- `roles/install-openclaw/templates/openclaw-configmap.yaml.j2`

---
*Este documento ha sido generado por la IA (Antigravity) a petición del usuario.*
