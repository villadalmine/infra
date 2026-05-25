# Honcho Memory Integration Guide (OpenClaw & Hermes)

Este documento detalla el funcionamiento de la memoria persistente a largo plazo mediante la plataforma **Honcho** (`https://honcho.ai`) en los servicios de IA de la homelab (**OpenClaw** y **Hermes Agent**), y proporciona ejemplos prácticos para corroborar y auditar la información almacenada sin exponer credenciales.

---

## 1. Concepto y Arquitectura de Memoria

Honcho actúa como un servicio de base de datos de memoria semántica y relacional en la nube. Esto permite que los agentes recuerden el contexto, las preferencias y los historiales de conversación a través de reinicios o reconstrucciones completas de sus contenedores locales.

Para evitar la mezcla de datos y mantener los contextos separados, ambos agentes comparten la misma clave de cuenta (`HONCHO_API_KEY`) pero operan en **Workspaces (espacios de trabajo) aislados**:

```text
               ┌───────────────────────────────┐
               │    Tu Cuenta de Honcho        │
               │      (HONCHO_API_KEY)         │
               └───────────────┬───────────────┘
                               │
               ┌───────────────┴───────────────┐
               │                               │
       [Workspace: "openclaw"]         [Workspace: "hermes"]
               │                               │
   ┌───────────┴───────────┐       ┌───────────┴───────────┐
   │ Chats de Telegram     │       │ Tareas del agente     │
   │ con el bot OpenClaw   │       │ de código Hermes      │
   └───────────────────────┘       └───────────────────────┘
```

* **Workspace `openclaw`:** Almacena sesiones de chat individuales correspondientes a cada usuario de Telegram (indexadas por su ID de Telegram). El plugin `openclaw-honcho` recupera dinámicamente recuerdos semánticos anteriores y los inyecta en el prompt del LLM en cada interacción.
* **Workspace `hermes`:** Almacena el historial técnico de Hermes Agent, el perfil consolidado del administrador de la homelab, y su bucle interno de aprendizaje continuo para refinar sus comportamientos y scripts de operaciones.

---

## 2. Verificación y Auditoría por CLI / API (Desde tu Workstation)

Puedes interactuar directamente con la API REST de Honcho desde tu workstation para auditar qué información han registrado tus bots. 

> [!IMPORTANT]
> Reemplaza `<HONCHO_API_KEY>` con la clave real almacenada en los archivos de secrets (`roles/*/defaults/secrets.yml`). NUNCA expongas ni subas tu clave real en repositorios git públicos.

### A. Listar los Workspaces Activos
Comprueba que los espacios de trabajo de tus agentes existen en tu cuenta:
```bash
curl -s -H "Authorization: Bearer <HONCHO_API_KEY>" \
  https://api.honcho.ai/v1/workspaces | jq
```
*Debería devolver un JSON que liste los nombres de los workspaces `"openclaw"` y `"hermes"`.*

### B. Listar Sesiones de Chat en OpenClaw
Consulta todas las sesiones de conversación individuales registradas por OpenClaw:
```bash
curl -s -H "Authorization: Bearer <HONCHO_API_KEY>" \
  https://api.honcho.ai/v1/workspaces/openclaw/sessions | jq
```
*Cada sesión representa una conversación activa de Telegram.*

### C. Extraer los Mensajes de una Sesión Específica
Copia el `"id"` de una sesión devuelta en el comando anterior (por ejemplo, `sess_...`) para ver la transcripción exacta de la conversación guardada en la nube:
```bash
curl -s -H "Authorization: Bearer <HONCHO_API_KEY>" \
  https://api.honcho.ai/v1/workspaces/openclaw/sessions/<SESSION_ID>/messages | jq
```

### D. Consultar el Perfil de Usuario y Metamemorias
Consulta las memorias estructuradas que el agente ha inferido sobre tus preferencias:
```bash
curl -s -H "Authorization: Bearer <HONCHO_API_KEY>" \
  https://api.honcho.ai/v1/workspaces/openclaw/users | jq
```

---

## 3. Verificación en Tiempo Real mediante Logs en Kubernetes

Puedes comprobar el inicio, la conexión y el precalentamiento de la memoria de Honcho directamente en el clúster:

### Logs en OpenClaw
Para auditar la inicialización del plugin de memoria en OpenClaw:
```bash
kubectl logs -n openclaw deploy/openclaw -c openclaw-gateway | grep -i honcho
```
*Salida esperada:*
```text
[plugins] Honcho memory plugin loaded
[plugins] Initializing Honcho memory...
[plugins] Honcho memory ready — peer map: /home/node/.honcho/openclaw-peers.json
```

### Logs en Hermes Agent
Para comprobar el estado de carga y restauración de perfiles en Hermes:
```bash
kubectl logs -n ai deploy/hermes-agent-mcp -c hermes-agent | grep -i honcho
```
