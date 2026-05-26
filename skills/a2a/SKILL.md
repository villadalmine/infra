---
name: a2a
description: >
  Agent-to-Agent (A2A) communication between OpenClaw y Hermes.
  Hermes expone un messaging bridge MCP completo (11 tools).
  OpenClaw puede enviar mensajes reales a las conversaciones de Hermes
  y leer respuestas via events_poll — A2A probado y funcional en un sentido.
license: MIT
compatibility:
  - opencode
  - claude-code
metadata:
  author: dotfiles
  tags: [a2a, agent-to-agent, mcp, messaging-bridge, hermes, openclaw, honcho, telegram]
---

# A2A — Agent-to-Agent entre OpenClaw y Hermes

## Contexto: MCP vs A2A

| Concepto | Descripción |
|----------|-------------|
| **MCP tool calling** | OpenClaw llama una función en Hermes. Síncrono, pull-only. El resultado es un string. |
| **A2A via messaging bridge** | OpenClaw envía un mensaje al canal de conversación de Hermes. Hermes lo procesa con su loop autónomo completo (LLM + MCPs). OpenClaw lee la respuesta con `events_poll`. |
| **A2A protocol (Google)** | Estándar peer-to-peer con Agent Cards, streaming, push proactivo. No implementado aún. |

En esta homelab usamos el **messaging bridge** de Hermes como capa A2A. Es funcional,
probado, y no requiere nuevo protocolo.

---

## Arquitectura actual (2026-05-26)

```
Usuario (Telegram)
    │
    ▼
OpenClaw (Tito) — orquestador
    │
    ├─[MCP :8000]──────────────────────────────────┐
    │  messaging bridge tools                       │
    │  conversations_list / messages_send           ▼
    │  events_poll / ask_hermes_agent        hermes-agent-mcp.ai:8000
    │                                               │
    │                                        Hermes — agente autónomo
    │                                        ├─ LiteLLM (razonamiento)
    │                                        ├─ kubernetes MCP (sidecar)
    │                                        └─ kagent MCP
    │                                               │
    │                                        responde en su chat Telegram
    │                                               │
    └─[events_poll / messages_read]─────────────────┘
                  ▲
          OpenClaw lee la respuesta
```

**Dirección confirmada:** OpenClaw → Hermes ✅
**Dirección pendiente:** Hermes → OpenClaw ❌ (OpenClaw no expone MCP server)

---

## Tests realizados (todos ✅)

### 1. Conectividad MCP

```bash
# Desde pod openclaw → hermes-agent-mcp:8000
curl -X POST http://hermes-agent-mcp.ai.svc.cluster.local:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
# → serverInfo: {"name":"hermes","version":"1.26.0"}
# → mcp-session-id: <uuid> en header de respuesta
```

### 2. conversations_list

```bash
SESSION=<uuid-from-initialize>
curl -X POST http://hermes-agent-mcp.ai.svc.cluster.local:8000/mcp \
  -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"conversations_list","arguments":{"platform":"telegram","limit":10}}}'
# → session_key: "agent:main:telegram:dm:8492872858"
# → display_name: "villadalmine", chat_type: "dm"
```

### 3. messages_read

```bash
curl -X POST ... -d '{"method":"tools/call","params":{"name":"messages_read","arguments":{
  "session_key": "agent:main:telegram:dm:8492872858",
  "limit": 5
}}}'
# → últimos 5 mensajes con role/content/timestamp/id
# → total_in_session: 72 (sesión activa con historial)
```

### 4. messages_send ✅ FUNCIONA — pero es outgoing

```bash
curl -X POST ... -d '{"method":"tools/call","params":{"name":"messages_send","arguments":{
  "target": "telegram:8492872858",
  "message": "[OpenClaw] Mensaje de prueba A2A"
}}}'
# → {"success": true, "platform": "telegram", "chat_id": "8492872858",
#     "message_id": "847", "mirrored": true}
```

**Schema de messages_send:**
- `target` (required): `"platform:chat_id"` → `"telegram:8492872858"` o `"discord:#general"`
- `message` (required): texto del mensaje

⚠️ **IMPORTANTE — comportamiento real (verificado):**
`messages_send` envía un mensaje FROM el bot de Hermes TO el usuario. Es un mensaje **saliente**.
NO dispara el loop del agente de Hermes. `mirrored: true` significa que el mensaje
aparece en el historial de la conversación pero como mensaje de bot (outgoing), no como
mensaje de usuario entrante que dispararía el agente.

**Uso correcto de messages_send:** mostrar transcripciones / notificaciones al usuario en tiempo real
a través del bot de Hermes. No usar para disparar el razonamiento de Hermes.

**Para disparar el razonamiento de Hermes → usar `ask_hermes_agent`.**

### 5. events_poll

```bash
curl -X POST ... -d '{"method":"tools/call","params":{"name":"events_poll","arguments":{
  "session_key": "agent:main:telegram:dm:8492872858",
  "cursor": "188",
  "limit": 5
}}}'
# → lista de eventos desde cursor 188, con role/content/timestamp
# → cursor es el message_id del último evento visto
```

### 6. ask_hermes_agent

```bash
curl -X POST ... -d '{"method":"tools/call","params":{"name":"ask_hermes_agent","arguments":{
  "question": "En una frase: qué es A2A y cómo te comunicarías con OpenClaw?"
}}}'
# → respuesta autónoma de Hermes usando su LLM + contexto completo
# → Hermes entiende A2A y puede describir cómo se comunicaría
```

---

## Tools completos del messaging bridge (Hermes MCP :8000)

| Tool | Descripción | Args clave |
|------|-------------|------------|
| `conversations_list` | Lista conversaciones activas | `platform`, `limit`, `search` |
| `conversation_get` | Detalle de una conversación | `session_key` |
| `messages_read` | Lee mensajes recientes | `session_key`, `limit` |
| `attachments_fetch` | Attachments de un mensaje | `session_key`, `message_id` |
| `events_poll` | Eventos desde un cursor | `session_key`, `cursor`, `limit` |
| `events_wait` | Espera bloqueante de eventos | `session_key`, `timeout` |
| `messages_send` | **Envía mensaje a un canal** | `target`, `message` |
| `channels_list` | Lista canales disponibles | — |
| `permissions_list_open` | Permisos pendientes de aprobación | — |
| `permissions_respond` | Responde a un permiso | `permission_id`, `approved` |
| `ask_hermes_agent` | Corre el AIAgent completo de Hermes | `question` |

---

## Patrón de debate A2A (OpenClaw orquesta)

OpenClaw puede implementar un debate multi-turno llamando al messaging bridge:

```
Turno 1: OpenClaw forma posición inicial (con su LLM + MCPs)
Turno 2: OpenClaw → messages_send → Hermes recibe y razona (su LLM + MCPs)
Turno 3: OpenClaw ← events_poll  ← lee respuesta de Hermes
Turno 4: OpenClaw forma réplica
Turno 5: OpenClaw → messages_send → Hermes...
...
Final:   OpenClaw sintetiza y envía al usuario por Telegram
```

Diferencia clave con tool calling clásico:
- Hermes no ejecuta una función — recibe un mensaje y razona autónomamente
- Hermes puede usar todos sus MCPs (kubernetes, kagent) para fundamentar su respuesta
- El historial queda en la conversación de Hermes (trazable, persistente via Honcho)

---

## Honcho como memoria compartida A2A

Ambos agentes tienen Honcho configurado con workspaces separados:
- OpenClaw: workspace `openclaw`
- Hermes: workspace `hermes`

Para conocimiento compartido en debates/coordinación A2A, usar un workspace común:

```yaml
# En defaults de ambos agentes
honcho_debate_workspace: "a2a-debate"
```

Flujo con Honcho:
```
OpenClaw escribe contexto/conclusión → Honcho workspace "a2a-debate"
Hermes lee ese contexto             → Honcho workspace "a2a-debate"
Hermes escribe su análisis          → Honcho workspace "a2a-debate"
OpenClaw lee el análisis de Hermes  → Honcho workspace "a2a-debate"
```

Ventaja: el conocimiento persiste entre sesiones, no depende de la conversación activa.

---

## Lo que falta — Roadmap A2A

### Fase 1 — DONE ✅
- [x] MCP connectivity OpenClaw → Hermes (port 8000)
- [x] `conversations_list` — Hermes ve su conversación Telegram
- [x] `messages_read` — lectura de historial
- [x] `messages_send` — OpenClaw puede enviar a Hermes vía Telegram
- [x] `events_poll` — polling de respuestas
- [x] `ask_hermes_agent` — delegación de agente completo

### Fase 2 — Debate orquestado (pendiente)
- [ ] Tool `start_debate(topic, turns)` en OpenClaw que implementa el loop multi-turno
- [ ] System prompt de OpenClaw: instrucciones explícitas para iniciar debate con `messages_send`
- [ ] Filtrar eventos propios vs respuestas de Hermes (evitar eco)

### Fase 3 — Bidireccional (pendiente)
- [ ] OpenClaw MCP server via sidecar HTTP wrapper
  - OpenClaw tiene `openclaw mcp serve` que expone los mismos 11 tools que Hermes (conversations_list, messages_send, events_poll, etc.) — pero **solo stdio**, no HTTP
  - Para exponerlo en-cluster: sidecar `npx supergateway` o `mcp-proxy` que envuelve el proceso stdio → HTTP streamable-http
  - El bridge conecta al gateway via WebSocket: `wss://openclaw.openclaw.svc.cluster.local:18789`
  - Puerto sugerido: 18790 (sidecar HTTP wrapper en el mismo pod)
  - NetworkPolicy: permitir Hermes (ai ns) → OpenClaw (openclaw ns) :18790
- [ ] Honcho workspace compartido `a2a-debate`

Ejemplo de sidecar para exponer `openclaw mcp serve` como HTTP:
```yaml
- name: openclaw-mcp-bridge
  image: node:22-alpine
  command: ["sh", "-c"]
  args:
    - npx -y supergateway --port 18790 -- openclaw mcp serve
        --url wss://localhost:18789
        --token "$(cat /etc/openclaw/gateway-token)"
  ports:
    - containerPort: 18790
```

### Fase 4 — Google A2A Protocol (futuro)
- [ ] Implementar Agent Cards en ambos agentes
- [ ] Endpoints `/a2a` con streaming SSE
- [ ] Push proactivo (Hermes inicia sin ser llamado)

---

## Verificación rápida

```bash
# ¿Está el MCP de Hermes respondiendo?
kubectl exec -n openclaw deploy/openclaw -c openclaw-gateway -- \
  curl -s -m 5 -X POST http://hermes-agent-mcp.ai.svc.cluster.local:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  | grep serverInfo
# → "serverInfo":{"name":"hermes","version":"1.26.0"}

# ¿OpenClaw expone MCP server?
kubectl exec -n openclaw deploy/openclaw -c openclaw-gateway -- \
  curl -si -m 3 http://localhost:18789/mcp | head -1
# → HTTP/1.1 404 Not Found  ← no expuesto aún
```

---

## Repo Paths

```
skills/a2a/SKILL.md                          # este archivo
skills/openclaw/SKILL.md                     # arquitectura OpenClaw + sección A2A
roles/install-openclaw/defaults/main.yml     # openclaw_hermes_url, timeouts
roles/install-hermes-agent/defaults/main.yml # honcho workspace, telegram config
```
