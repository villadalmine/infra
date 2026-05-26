---
name: a2a
description: >
  Agent-to-Agent (A2A) communication entre OpenClaw y Hermes.
  Bidireccional: OpenClaw→Hermes via MCP :8000 (probado ✅).
  Hermes→OpenClaw via MCP bridge :18790 (supergateway, implementado 2026-05-26).
  Debate autónomo multi-turno usando ask_hermes_agent / ask_openclaw_agent.
license: MIT
compatibility:
  - opencode
  - claude-code
metadata:
  author: dotfiles
  tags: [a2a, agent-to-agent, mcp, messaging-bridge, hermes, openclaw, honcho, telegram, debate]
---

# A2A — Agent-to-Agent entre OpenClaw y Hermes

## Conceptos clave: MCP vs A2A

| Concepto | Descripción |
|----------|-------------|
| **MCP tool calling** | Un agente llama una función en otro. Síncrono, pull-only. El resultado es un string. Es "usar una herramienta". |
| **A2A via messaging bridge** | Un agente envía una pregunta al loop de razonamiento autónomo del otro. El receptor razona con su LLM + MCPs propios y responde con análisis propio. Es "hablar con otro agente". |
| **A2A protocol (Google)** | Estándar peer-to-peer con Agent Cards, streaming SSE, push proactivo. No implementado aún (Fase 4). |

La distinción importante: `ask_hermes_agent("DEBATE...")` no llama una función — **dispara el AIAgent completo de Hermes** con sus propios MCPs (kubernetes, kagent, prometheus). Hermes razona de forma autónoma y devuelve su análisis. Eso es A2A real.

---

## Diagrama de flujo completo

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  MODO MCP CLÁSICO (tool calling, síncrono)                                   │
│                                                                              │
│  OpenClaw ──[MCP call]──► Hermes tool:                                       │
│                            conversations_list / messages_send / messages_read │
│                            events_poll / events_wait                          │
│                            ← devuelve string inmediato, sin razonamiento      │
│                                                                              │
│  Hermes ──[MCP call]──► OpenClaw tool (vía bridge :18790):                   │
│                           conversations_list / messages_send / messages_read  │
│                           events_poll / events_wait                           │
│                           ← devuelve string inmediato, sin razonamiento       │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│  MODO A2A (debate autónomo, cada agente razona con sus MCPs)                 │
│                                                                              │
│  OpenClaw llama ask_hermes_agent("DEBATE T1 — Mi posición: X. Tu análisis?") │
│       │                                                                      │
│       ▼                                                                      │
│  Hermes AIAgent loop:                                                        │
│    ├─ consulta kubernetes MCP (pods, logs, estado)                            │
│    ├─ consulta kagent MCP (helm, prometheus, cilium)                          │
│    ├─ consulta openclaw MCP (historial conversación con usuario)              │
│    └─ forma respuesta autónoma fundamentada en datos reales                   │
│       │                                                                      │
│       ▼                                                                      │
│  OpenClaw recibe respuesta de Hermes                                         │
│  OpenClaw forma réplica e itera (ask_hermes_agent("DEBATE T2 — Historial..."))│
│       │                                                                      │
│       ▼                                                                      │
│  Síntesis final → usuario via Telegram (@tito_es_tu_bot)                    │
│                                                                              │
│  (Hermes también puede iniciar: ask_openclaw_agent("Mi análisis: X"))        │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│  DIFERENCIA CLAVE: MCP vs A2A                                                │
│                                                                              │
│  MCP: "Ejecuta esta función y devuélveme el resultado"                       │
│       → El receptor NO razona, solo ejecuta                                  │
│       → Resultado determinístico, rápido                                     │
│       → Ejemplo: conversations_list() → lista de sesiones                   │
│                                                                              │
│  A2A: "Piensa sobre este problema y dame tu análisis"                        │
│       → El receptor razona autónomamente con sus propios MCPs                │
│       → Resultado no determinístico, puede tardar 30-90s                    │
│       → Ejemplo: ask_hermes_agent("Debate T1: ¿mejor storage para K8s?")    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Arquitectura actual (2026-05-26)

```
Usuario (Telegram @tito_es_tu_bot)
    │
    ▼
OpenClaw (Tito) — namespace: openclaw, node: srv-rk1-nvme-02
    │ pod: openclaw-* (4 containers: gateway, kubernetes-mcp, smee-client, mcp-bridge)
    │
    ├─[MCP :8000]──────────────────────────────────────────────────────┐
    │  Tool: hermes__ask_hermes_agent(question)                         │
    │  Tool: hermes__conversations_list / messages_send / etc.          ▼
    │                                                         hermes-agent-mcp.ai:8000
    │                                                                   │
    │                                                         Hermes — namespace: ai
    │                                                         ├─ LiteLLM (razonamiento)
    │                                                         ├─ kubernetes MCP (sidecar :8080)
    │                                                         ├─ kagent MCP (:8084)
    │                                                         └─ openclaw MCP (:18790) ←─┐
    │                                                                   │                 │
    │                                                         Hermes razona               │
    │                                                         y responde                  │
    │                                                                                     │
    └─[MCP Bridge :18790]──────────────────────────────────────────────────────────────┘
       supergateway wrapping `openclaw mcp serve` (stdio → streamable-http)
       Tool: openclaw__ask_openclaw_agent(question)
       Tool: openclaw__conversations_list / messages_send / etc.

Honcho (namespace: honcho, :80 → pod :8000)
    ├─ workspace "openclaw" — memoria de OpenClaw
    └─ workspace "hermes"   — memoria de Hermes
```

**Dirección OpenClaw → Hermes:** ✅ probado y funcionando
**Dirección Hermes → OpenClaw:** ✅ implementado (deploy pendiente)

---

## Tests realizados (OpenClaw → Hermes) — todos ✅

### 1. Conectividad MCP (initialize)

```bash
# Desde pod openclaw → hermes-agent-mcp:8000
kubectl exec -n openclaw deploy/openclaw -c openclaw-gateway -- \
  curl -s -X POST http://hermes-agent-mcp.ai.svc.cluster.local:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  -D /tmp/h.txt && grep -i mcp-session-id /tmp/h.txt
# → mcp-session-id: <uuid>
# → serverInfo: {"name":"hermes","version":"1.26.0"}
```

### 2. conversations_list ✅

```bash
SESSION=<uuid>
curl -X POST http://hermes-agent-mcp.ai.svc.cluster.local:8000/mcp \
  -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"conversations_list","arguments":{"platform":"telegram","limit":10}}}'
# → session_key: "agent:main:telegram:dm:8492872858"
# → display_name: "villadalmine", chat_type: "dm"
```

### 3. messages_read ✅

```bash
# IMPORTANTE: messages_read lee historial, NO dispara razonamiento de Hermes
curl ... -d '{"method":"tools/call","params":{"name":"messages_read","arguments":{
  "session_key": "agent:main:telegram:dm:8492872858", "limit": 5
}}}'
# → últimos 5 mensajes con role/content/timestamp/id
```

### 4. messages_send ✅ — pero es OUTGOING (del bot al usuario)

```bash
curl ... -d '{"method":"tools/call","params":{"name":"messages_send","arguments":{
  "target": "telegram:8492872858",
  "message": "[OpenClaw] Mensaje de prueba A2A"
}}}'
# → {"success": true, "mirrored": true}
```

⚠️ **CRÍTICO — comportamiento real verificado:**
`messages_send` envía un mensaje FROM el bot de Hermes TO el usuario. Es **saliente**.
NO dispara el loop del agente de Hermes. `mirrored: true` = aparece en historial como mensaje de bot.
**Usar solo para mostrar transcripción del debate al usuario en tiempo real.**
**Para disparar razonamiento de Hermes → usar `ask_hermes_agent`.**

### 5. events_poll ✅

```bash
curl ... -d '{"method":"tools/call","params":{"name":"events_poll","arguments":{
  "session_key": "agent:main:telegram:dm:8492872858",
  "cursor": "188", "limit": 5
}}}'
# → eventos desde cursor 188 (message_id del último evento visto)
```

⚠️ `events_poll` y `events_wait` esperan mensajes del USUARIO, no respuestas de Hermes.
Si OpenClaw usa messages_send para "preguntar" a Hermes, NO habrá eventos — Hermes no responde ahí.

### 6. ask_hermes_agent ✅ — DEBATE REAL CONFIRMADO

```bash
curl ... -d '{"method":"tools/call","params":{"name":"ask_hermes_agent","arguments":{
  "question": "DEBATE T1/2 — Tema: mejor storage para PVCs en K3s ARM64\nMi posición (OpenClaw): longhorn-nvme sobre NVMe en RK1 porque da replicación y failover. ¿Tu análisis?"
}}}'
# → Hermes razona con su LLM + kubernetes MCP + kagent
# → Devuelve análisis con tabla comparativa, recomendaciones por caso de uso
# → Tardó ~30s (normal para AIAgent completo)
```

---

## Protocolo de debate A2A (implementado en OpenClaw system prompt)

```
T1: OpenClaw forma posición inicial (2-3 oraciones)
T2: ask_hermes_agent("DEBATE T1/N — Tema: X\nMi posición (OpenClaw): [pos]\nAnalizá pros/contras")
    → Hermes corre AIAgent completo con sus MCPs (~30-90s)
T3: OpenClaw lee respuesta de Hermes
T4: OpenClaw forma réplica integrando argumentos de Hermes
T5: ask_hermes_agent("DEBATE T2/N — Historial:\nOpenClaw T1: [pos]\nHermes T1: [resp]\nMi réplica: [réplica]\n¿Contraargumento?")
T6: Repetir para N turnos (default 3, máximo 5)
T7: Síntesis → consenso o desacuerdo documentado → al usuario

OPCIONAL (mostrar en tiempo real):
Después de cada turno de Hermes:
  messages_send(target="telegram:CHAT_ID", message="[Hermes dice] " + resp_hermes)
  — muestra la respuesta de Hermes en su propio chat Telegram para que el usuario lo siga
  CHAT_ID: extraer del session_key de conversations_list
```

### ¿Cuándo OpenClaw inicia debate automáticamente?
El sistema prompt de OpenClaw tiene esta regla:
> **DEBATE MANDATORIO CON HERMES**: Antes de proponer o solicitar aprobación al usuario para CUALQUIER operación de escritura en el cluster (apply/scale/delete/shell), DEBES iniciar debate técnico con Hermes via `hermes__ask_hermes_agent`.

---

## 11 tools del messaging bridge (disponibles en ambos sentidos)

| Tool | Descripción | Args clave |
|------|-------------|------------|
| `conversations_list` | Lista conversaciones activas | `platform`, `limit`, `search` |
| `conversation_get` | Detalle de una conversación | `session_key` |
| `messages_read` | Lee mensajes recientes | `session_key`, `limit` |
| `attachments_fetch` | Attachments de un mensaje | `session_key`, `message_id` |
| `events_poll` | Eventos desde un cursor | `session_key`, `cursor`, `limit` |
| `events_wait` | Espera bloqueante de eventos (USER messages only) | `session_key`, `timeout` |
| `messages_send` | Envía mensaje **outgoing** del bot al usuario | `target`, `message` |
| `channels_list` | Lista canales disponibles | — |
| `permissions_list_open` | Permisos pendientes | — |
| `permissions_respond` | Responde a un permiso | `permission_id`, `approved` |
| `ask_hermes_agent` / `ask_openclaw_agent` | Corre el AIAgent completo | `question` |

---

## Cómo probarlo desde Telegram (@tito_es_tu_bot)

### Test 1 — Delegación simple a Hermes
```
Tú → Tito: "Consulta a Hermes: ¿qué pods están corriendo en el namespace ai?"
```
Esperado: Tito llama `hermes__ask_hermes_agent`, Hermes usa su kubernetes MCP y responde con la lista.

### Test 2 — Debate técnico multi-turno
```
Tú → Tito: "Debatan con Hermes sobre cuál es mejor storage para bases de datos en el cluster, 3 turnos"
```
Esperado: Tito forma posición → llama `hermes__ask_hermes_agent` T1 → réplica → T2 → T3 → síntesis final.
Opcionalmente verás mensajes de Hermes en tu chat con @hermes_bot mostrando el debate en vivo.

### Test 3 — Debate mandatorio antes de operación de escritura
```
Tú → Tito: "Escala el deployment de openclaw a 2 réplicas"
```
Esperado: Tito PRIMERO debate con Hermes los riesgos, LUEGO presenta la propuesta consensuada y pide confirmación.

### Test 4 — Hermes inicia contacto con OpenClaw (Fase 3, post-deploy)
```
Tú → Hermes: "Consulta a Tito (OpenClaw) qué estuvo haciendo hoy"
```
Esperado: Hermes llama `openclaw__messages_read` o `openclaw__ask_openclaw_agent` y responde.

---

## Cómo probarlo desde dentro del cluster

### Verificar conectividad OpenClaw → Hermes
```bash
kubectl exec -n openclaw deploy/openclaw -c openclaw-gateway -- \
  curl -s -m 5 http://hermes-agent-mcp.ai.svc.cluster.local:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | grep serverInfo
# → "serverInfo":{"name":"hermes","version":"1.26.0"}
```

### Verificar MCP bridge OpenClaw (Hermes → OpenClaw)
```bash
# ¿Está el sidecar corriendo?
kubectl get pod -n openclaw -o jsonpath='{.items[0].spec.containers[*].name}'
# → openclaw-gateway kubernetes-mcp smee-client openclaw-mcp-bridge

# ¿El bridge responde?
kubectl exec -n openclaw deploy/openclaw -c openclaw-gateway -- \
  curl -s -m 5 http://localhost:18790/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
# → serverInfo con openclaw tools

# ¿Hermes puede llegar?
kubectl exec -n ai deploy/hermes-agent-mcp -c hermes-agent -- \
  curl -s -m 5 http://openclaw.openclaw.svc.cluster.local:18790/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"hermes","version":"1.0"}}}'
```

### Verificar Honcho (ambos agentes)
```bash
# OpenClaw
kubectl logs -n openclaw deploy/openclaw -c openclaw-gateway | grep -i honcho
# → [plugins] Honcho memory ready — peer map: /home/node/.honcho/openclaw-peers.json

# Hermes
kubectl logs -n ai deploy/hermes-agent-mcp -c hermes-agent | grep -i honcho
```

### Test debate completo vía MCP directo
```bash
# Desde openclaw pod, simular un turno de debate
SESSION=$(curl -s -X POST http://hermes-agent-mcp.ai.svc.cluster.local:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  -D /tmp/h.txt > /dev/null && grep -i mcp-session-id /tmp/h.txt | tr -d '\r' | awk '{print $2}')

curl -s -X POST http://hermes-agent-mcp.ai.svc.cluster.local:8000/mcp \
  -H "mcp-session-id: $SESSION" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"ask_hermes_agent","arguments":{"question":"DEBATE T1/2 — Tema: longhorn vs local-path\nMi posición (OpenClaw): longhorn-nvme para stateful crítico. ¿Tu análisis?"}}}'
# → respuesta autónoma de Hermes en JSON
```

---

## Componentes del sistema

### OpenClaw pod (4 containers en openclaw namespace)
```
openclaw-gateway      :18789  — gateway principal, LLM, Telegram bot (@tito_es_tu_bot)
kubernetes-mcp        :8080   — kubernetes MCP sidecar (read-only)
smee-client           —       — relay de webhooks GitHub/CI
openclaw-mcp-bridge   :18790  — supergateway wrapping `openclaw mcp serve` → HTTP
```

### Hermes pod (2 containers en ai namespace)
```
hermes-agent          :8000   — Hermes gateway: MCP server + AIAgent + Telegram bot
kubernetes-mcp-server :8080   — kubernetes MCP sidecar (cluster-admin)
```

### Services y URLs en-cluster
```
OpenClaw gateway:      http://openclaw.openclaw.svc.cluster.local:18789
OpenClaw MCP bridge:   http://openclaw.openclaw.svc.cluster.local:18790/mcp
Hermes MCP server:     http://hermes-agent-mcp.ai.svc.cluster.local:8000/mcp
Honcho API:            http://honcho-api.honcho.svc.cluster.local (port 80 → pod 8000)
```

### NetworkPolicy — puertos permitidos
```
OpenClaw egress:
  → ai:8000 (hermes)      MCP client OpenClaw→Hermes
  → kagent:8084           kagent MCP
  → honcho:8000 (pod)     memoria Honcho (post-DNAT: usar pod port 8000, no svc port 80)
  → ai:4000 (litellm)     LLM proxy
  → :443 ext              Telegram API

OpenClaw ingress:
  ← monitoring, kaniko, gateway, argocd, openclaw  :18789  (webhooks + gateway)
  ← ai:hermes-agent-mcp                            :18790  (A2A reverse MCP)

Hermes: sin NetworkPolicy → permite todo egress/ingress por default K8s
```

---

## Bugs encontrados y fixes

### Bug 1 — events_wait timeout (RESUELTO)
**Síntoma:** `hermes__events_wait failed: MCP error -32001: Request timed out`
**Causa:** `messages_send` es OUTGOING (del bot al usuario). No dispara el loop de Hermes.
`events_wait` espera mensajes del USUARIO, no respuestas autónomas de Hermes.
**Fix:** Debate usa `ask_hermes_agent` para cada turno (no messages_send + events_wait).

### Bug 2 — Honcho NetworkPolicy port mismatch (RESUELTO)
**Síntoma:** `ConnectionError: fetch failed` al init de OpenClaw. Timeout en `curl http://honcho-api.../health`.
**Causa:** NetworkPolicy usaba service port (80) pero K8s evalúa **post-DNAT** → pod port (8000).
**Fix:** `roles/install-openclaw/templates/openclaw-network.yaml.j2` → cambiar port 80 a 8000 en egress a honcho.
Resultado: `[plugins] Honcho memory ready — peer map: /home/node/.honcho/openclaw-peers.json (0 known senders)`

---

## Roadmap A2A

### Fase 1 — DONE ✅
- [x] MCP connectivity OpenClaw → Hermes (port 8000)
- [x] Todos los tools del bridge: conversations_list, messages_read, messages_send, events_poll, events_wait, ask_hermes_agent
- [x] Debate multi-turno A2A funcionando end-to-end (ask_hermes_agent por turno)
- [x] System prompts de OpenClaw: protocolo de debate documentado e implementado
- [x] Honcho fix: NetworkPolicy post-DNAT (port 8000, no 80)

### Fase 2 — DONE ✅
- [x] Documentación completa en skills/a2a/SKILL.md
- [x] Debate confirmado funcionando: test con tema "storage longhorn vs local-path"
- [x] ask_hermes_agent probado y respondiendo con análisis autónomo (~30s)

### Fase 3 — Bidireccional: Hermes → OpenClaw (IMPLEMENTADO, pendiente deploy)
- [x] Sidecar `openclaw-mcp-bridge` en `openclaw-deployment.yaml.j2`
- [x] Service port 18790 en `openclaw-network.yaml.j2`
- [x] Ingress NetworkPolicy: Hermes (ai) → OpenClaw :18790
- [x] Hermes config: `openclaw` MCP server en `hermes-config-configmap.yaml.j2`
- [x] Hermes system prompt: instrucciones para usar openclaw MCP + `ask_openclaw_agent`
- [ ] Deploy: `ansible-playbook playbooks/bootstrap.yml --tags openclaw,ai-hermes-deploy`
- [ ] Test: `kubectl exec -n ai deploy/hermes-agent-mcp -- curl http://openclaw.openclaw.svc.cluster.local:18790/mcp`

### Fase 4 — Google A2A Protocol (futuro)
- [ ] Implementar Agent Cards en ambos agentes
- [ ] Endpoints `/a2a` con streaming SSE
- [ ] Push proactivo (Hermes inicia sin ser llamado)

---

## Repo Paths

```
skills/a2a/SKILL.md                                              # este archivo
skills/openclaw/SKILL.md                                         # arquitectura OpenClaw + sección A2A
roles/install-openclaw/defaults/main.yml                         # openclaw_mcp_bridge_enabled/port
roles/install-openclaw/templates/openclaw-deployment.yaml.j2     # sidecar openclaw-mcp-bridge
roles/install-openclaw/templates/openclaw-network.yaml.j2        # Service :18790 + ingress Hermes
roles/install-hermes-agent/defaults/main.yml                     # hermes_openclaw_mcp_url + system_prompt
roles/install-hermes-agent/templates/hermes-config-configmap.yaml.j2  # openclaw en mcp_servers
```
