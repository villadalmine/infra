# Argo Workflows Kaniko Build

## Descripción
Este workflow detalla cómo interactuar con el proceso de compilación (CI/CD) basado en Argo Workflows y Kaniko, implementado en este clúster K3s para construir imágenes para arquitectura ARM64 de manera asíncrona.

## Contexto
El sistema de build local utiliza Ansible únicamente como el motor que inyecta la receta (Dockerfile a través de ConfigMaps) y despliega la solicitud de compilación (`Workflow` CRD). Argo Workflows toma el control orquestando un pod que primero usa un contenedor `init` para clonar el repositorio Git en un volumen local rápido (`local-path`), y luego dispara a Kaniko para armar la imagen usando un caché montado en NAS SMB, subiendo el resultado final al registro interno de Docker.

## Instrucciones y Comandos Útiles

**1. Disparar un nuevo Build (Trigger)**
Siempre usa Ansible a través del Makefile para regenerar las plantillas del Dockerfile y disparar el Workflow:
```bash
make ai-hermes-build
```
*(Opcional: puedes eliminar manualmente el anterior antes con `kubectl delete workflow --all -n kaniko` para limpiar la vista).*

**2. Monitorear el progreso en tiempo real**
Puedes ver los Workflows activos con:
```bash
kubectl get workflows -n kaniko
```
Para ver los pods asociados al build:
```bash
kubectl get pods -n kaniko
```

**3. Revisar los Logs de Construcción**
El proceso tiene dos etapas, `init` (clonado del código) y `main` (compilación en Kaniko).
```bash
# Ver errores en la clonación
kubectl logs -l app=hermes-agent-build -c init -n kaniko

# Seguir el log de compilación de la imagen
kubectl logs -l app=hermes-agent-build -c main -n kaniko -f
```

## Solución de Problemas Comunes (Troubleshooting)
- **Error `map[operator:Exists] does not contain declared merge key: key`:** Si ves este error al desplegar, asegúrate de que todas las tolerancias (tolerations) inyectadas en la definición del `Workflow` tengan la propiedad `key` definida explícitamente, ya que el *Strategic Merge Patch* de Argo exige las claves para cruzar las tolerancias globales.
- **Error `file exists` durante el clonado en el contenedor Init:** Argo Workflows extrae artefactos (como repositorios Git) directamente en los volúmenes indicados. Si se monta simultáneamente un archivo simple (como un Dockerfile desde un ConfigMap) dentro de la carpeta destino (`/workspace/source`), Argo lanzará un error. Asegúrate siempre de montar archivos externos fuera del *target path* de Git (ej. `/workspace/Dockerfile`).
- **Bloqueo en la creación del Pod (PVC Pending):** Revisa que el servidor NAS SMB esté encendido y que el StorageClass de Argo Workflows (`local-path`) esté activo en los nodos.
