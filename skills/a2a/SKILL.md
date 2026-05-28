---
name: a2a
description: >
  Agent-to-Agent (A2A) bidireccional OpenClaw↔Hermes.
  OpenClaw→Hermes: ✅ operacional (ask_hermes_agent via MCP :8000).
  Hermes→OpenClaw: ⚠️ bridge desplegado (supergateway streamableHttp :18790/mcp), Python mcp client falla por GET /mcp sin soporte stateless.
  ask_hermes_agent timeout fix: max_iterations=15 (era 90), NPU hermes-qwen local.
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

## Arquitectura actual (2026-05-28)

```
Usuario (Telegram @tito_es_tu_bot)
    │
    ▼
OpenClaw (Tito) — namespace: openclaw, node: srv-rk1-nvme-02
    │ pod: openclaw-* (4 containers)
    │   ├─ openclaw-gateway     :18789  — AI gateway, Telegram, Honcho memory
    │   ├─ kubernetes-mcp       :8080   — K8s MCP sidecar
    │   ├─ smee-client          —       — webhook relay GitHub→Telegram
    │   └─ openclaw-mcp-bridge  :18790  — supergateway SSE wrapping `openclaw mcp serve`
    │
    ├─[MCP HTTP :8000]─────────────────────────────────────────────────────────┐
    │  Tools: hermes__ask_hermes_agent / conversations_list / messages_send/etc │
    │                                                                           ▼
    │                                                          hermes-agent-mcp.ai:8000
    │                                                                           │
    │                                                          Hermes — ns: ai, node: srv-rk1-nvme-01
    │                                                          ├─ LiteLLM proxy (razonamiento)
    │                                                          ├─ kubernetes MCP sidecar :8080
    │                                                          ├─ kagent MCP :8084
    │                                                          └─ openclaw MCP :18790/sse ←─┐
    │                                                                           │             │
    │                                                          Hermes razona                 │
    │                                                          y responde                    │
    │                                                                                        │
    └─[SSE Bridge :18790]───────────────────────────────────────────────────────────────────┘
       supergateway SSE mode: `openclaw mcp serve` (stdio → SSE /sse)
       while-true restart loop (container stays alive, supergateway restarts in ~2s)
       Tools: openclaw__ask_openclaw_agent / conversations_list / messages_send / etc.

Honcho (namespace: honcho, :80 → pod :8000)
    ├─ workspace "openclaw" — memoria de OpenClaw (peers, conversaciones)
    └─ workspace "hermes"   — memoria de Hermes
```

---

## Estado por dirección (2026-05-28)

| Dirección | Estado | Detalle |
|-----------|--------|---------|
| **OpenClaw → Hermes** | ✅ Operacional | `ask_hermes_agent` funciona, debate confirmado. Fix: max_iterations=15 (era 90) — timeout resuelto. |
| **Hermes → OpenClaw** | ⚠️ Bridge desplegado, conexión falla | supergateway streamableHttp: initialize+tools/list OK, pero Python mcp client intenta GET /mcp → 405 (stateless no lo soporta) → CancelledError. |
| **Honcho memoria** | ✅ Ambos | `[plugins] Honcho memory ready` en ambos pods |
| **Scope upgrade** | ✅ Resuelto | paired.json bypass aplicado (ver AGENTS.md) |

### Fix aplicado: ask_hermes_agent timeout (2026-05-28)

**Síntoma**: `[tools] hermes__ask_hermes_agent failed: MCP error -32001: Request timed out`
**Causa**: AIAgent con max_iterations=90 + hermes-qwen (llama-3.1-8b NPU, ~30s/turno) → excede timeout MCP
**Fix**: max_iterations=15 en hermes-static-mcp.yaml.j2 + kubectl patch directo
**Resultado esperado**: 2-3 turns × 30s = ~60-90s por debate, dentro del timeout de 600s de OpenClaw

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
curl ... -d '{"method":"tools/call","params":{"name":"messages_read","arguments":{
  "session_key": "agent:main:telegram:dm:8492872858", "limit": 5
}}}'
# → últimos 5 mensajes con role/content/timestamp/id
```

### 4. messages_send ✅ — pero es OUTGOING (del bot al usuario)

⚠️ **CRÍTICO — comportamiento real verificado:**
`messages_send` envía un mensaje FROM el bot de Hermes TO el usuario. Es **saliente**.
NO dispara el loop del agente de Hermes. **Para disparar razonamiento → usar `ask_hermes_agent`.**

### 5. events_poll ✅

⚠️ `events_poll` y `events_wait` esperan mensajes del USUARIO, no respuestas de Hermes.

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

## Tests realizados (Hermes → OpenClaw bridge)

### 1. SSE endpoint accesible ✅

```bash
# Desde dentro del pod openclaw-mcp-bridge
curl -sN --max-time 3 http://127.0.0.1:18790/sse -H "Accept: text/event-stream"
# → event: endpoint
# → data: /message?sessionId=<uuid>

# Cross-namespace: desde hermes pod
curl -sN --max-time 3 http://openclaw.openclaw.svc.cluster.local:18790/sse -H "Accept: text/event-stream"
# → event: endpoint
# → data: /message?sessionId=<uuid>
```

### 2. Container restarts ✅ (0 restarts, while-true working)

```bash
kubectl get pod -n openclaw -o jsonpath='{range .status.containerStatuses[*]}{.name}: {.restartCount}{"\n"}{end}'
# → openclaw-mcp-bridge: 0  (supergateway restarts inside while-true, no container crash)
```

### 3. Hermes MCP connection — ⚠️ sesión inestable

Hermes conecta, obtiene sesión SSE, hace initialize exitosamente.
Pero `openclaw mcp serve` sale después de ~16s de inactividad → keepalive falla → Hermes intenta reconectar.

```
# Hermes logs (patrón esperado):
[05:39:06] WARNING  MCP server 'openclaw' keepalive failed: Session terminated
[05:39:07] WARNING  MCP server 'openclaw' connection lost (attempt 1/5), reconnecting in 1s
[05:39:09] WARNING  MCP server 'openclaw' connection lost (attempt 2/5), reconnecting in 2s
# ... (5 intentos, luego "giving up")
```

**Causa**: `openclaw mcp serve` en v2026.5.22 cierra la sesión si no hay actividad por ~16s.
**Workaround**: El while-true loop reinicia supergateway. Hermes debe ser reiniciado manualmente después para reestablecer la conexión.

---

## Configuración deployed

### openclaw-mcp-bridge sidecar (openclaw-deployment.yaml.j2)

```yaml
- name: openclaw-mcp-bridge
  image: {{ openclaw_image }}:{{ openclaw_version }}
  command: ["sh", "-c"]
  args:
    - |
      while true; do
        npx -y supergateway \
          --port {{ openclaw_mcp_bridge_port }} \
          --stdio "node dist/index.js mcp serve --url ws://localhost:{{ openclaw_gateway_port }} --token $OPENCLAW_GATEWAY_TOKEN"
        echo "[mcp-bridge] supergateway exited (code $?), restarting in 2s..."
        sleep 2
      done
  env:
    - name: OPENCLAW_GATEWAY_TOKEN
      valueFrom:
        secretKeyRef:
          name: openclaw-secrets
          key: OPENCLAW_GATEWAY_TOKEN
  ports:
    - name: mcp-bridge
      containerPort: 18790
```

### Hermes MCP config (hermes-config-configmap.yaml.j2)

```yaml
mcp_servers:
  kubernetes:
    url: http://127.0.0.1:8080/mcp
    timeout: 120
  kagent:
    url: http://kagent-tools.kagent.svc.cluster.local:8084/mcp
    timeout: 60
  openclaw:
    url: http://openclaw.openclaw.svc.cluster.local:18790/sse   # SSE transport
    timeout: 120
    connect_timeout: 30
```

---

## Scope Upgrade Bypass (CRÍTICO)

`openclaw mcp serve` necesita scopes `operator.read, operator.write, operator.approvals, operator.pairing`.
El token de gateway solo tiene `operator.write` → scope upgrade → deadlock.

**Fix:** editar `paired.json` directamente en el PVC para pre-aprobar los scopes. Documentado en `AGENTS.md` sección "OpenClaw MCP Bridge Deadlock Bypass".

```bash
# Verificar si el device está en paired.json:
kubectl exec -n openclaw deploy/openclaw -c openclaw-gateway -- \
  node -e 'const d=JSON.parse(require("fs").readFileSync("/home/node/.openclaw/devices/paired.json"));
  console.log(JSON.stringify(Object.keys(d).map(k=>({k,scopes:d[k].scopes})),null,2))'
```

---

## NetworkPolicy — gotcha post-DNAT

**Honcho** escucha en puerto 8000 (pod port), pero el Service es puerto 80. Kubernetes evalúa NetworkPolicy DESPUÉS del DNAT, con el puerto real del pod.

```yaml
# CORRECTO (egress desde openclaw → honcho):
egress:
  - to:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: honcho
    ports:
      - port: 8000   # POD port, no Service port
        protocol: TCP
```

---

## Cómo probar desde Telegram

### 1. OpenClaw → Hermes (dirección 1)

```
Tú → @tito_es_tu_bot: "Consulta a Hermes sobre el estado de los pods en el namespace ai"
```
OpenClaw usa `ask_hermes_agent` → Hermes consulta kubernetes MCP → responde con análisis.

### 2. Debate bidireccional

```
Tú → @tito_es_tu_bot: "Inicia un debate con Hermes sobre si deberíamos migrar a longhorn para los PVCs de observabilidad"
```
OpenClaw debate con Hermes, síntesis final al usuario.

### 3. Hermes → OpenClaw (dirección 2)

```
Tú → Hermes (Telegram): "Consulta a Tito (OpenClaw) sobre qué conversaciones recientes tiene"
```
Hermes usa `ask_openclaw_agent` → OpenClaw responde (requiere que la sesión SSE esté activa).

---

## Diagnóstico rápido

```bash
# Estado de pods
kubectl get pods -n openclaw -o wide
kubectl get pods -n ai -l app=hermes-agent-mcp

# Restarts del bridge
kubectl get pod -n openclaw -o jsonpath='{range .status.containerStatuses[*]}{.name}: {.restartCount}{"\n"}{end}'

# Logs bridge (ver si while-true está funcionando)
kubectl logs -n openclaw deploy/openclaw -c openclaw-mcp-bridge --tail=20

# Logs gateway (scope upgrades, WS connections)
kubectl exec -n openclaw deploy/openclaw -c openclaw-gateway -- \
  node -e 'require("fs").readFileSync("/tmp/openclaw/openclaw-$(date +%Y-%m-%d).log","utf8").split("\n").filter(Boolean).forEach(l=>{try{const j=JSON.parse(l);console.log(j.time.slice(0,19),j.message.slice(0,100));}catch(e){}})' 2>/dev/null | grep -E "scope|ws|mcp|upgrade" | tail -20

# Hermes MCP connections
kubectl logs -n ai deploy/hermes-agent-mcp -c hermes-agent | grep -E "openclaw|connected|failed" | tail -20

# Test manual SSE desde hermes namespace
kubectl exec -n ai deploy/hermes-agent-mcp -c hermes-agent -- \
  curl -sN --max-time 3 http://openclaw.openclaw.svc.cluster.local:18790/sse -H "Accept: text/event-stream"
# Esperado: event: endpoint + data: /message?sessionId=<uuid>
```

---

## Bugs encontrados y resueltos

| Bug | Causa | Fix |
|-----|-------|-----|
| `Honcho NetworkPolicy` | K8s evalúa NetworkPolicy post-DNAT → puerto 8000 (pod), no 80 (service) | egress port 8000 en openclaw-network.yaml.j2 |
| `events_wait timeout` | messages_send es OUTGOING — no dispara Hermes | usar ask_hermes_agent por turno |
| `scope upgrade deadlock` | mcp serve necesita scopes que el token no tiene; approval requiere token → chicken-and-egg | paired.json bypass (AGENTS.md) |
| `Already connected to transport` | supergateway SSE reutiliza un hijo; segundo cliente lo mata | Cambiado a streamableHttp (--outputTransport streamableHttp) |
| `ask_hermes_agent timeout (-32001)` | max_iterations=90 + NPU lento → excede timeout MCP (~90s) | max_iterations=15 en hermes-static-mcp.yaml.j2 |
| `GET /mcp → 405` | supergateway stateless streamableHttp no soporta GET /mcp | Pendiente: stateful mode o bridge Python propio |
| `Python mcp client CancelledError` | Cliente intenta GET /mcp para notificaciones SSE, recibe 405 → TaskGroup falla | Raíz: mismo que GET /mcp → 405 |

---

## Roadmap

| Fase | Descripción | Estado |
|------|-------------|--------|
| Fase 1 | OpenClaw → Hermes: ask_hermes_agent via MCP :8000 | ✅ Operacional |
| Fase 2 | Honcho compartido (ambos agentes usan misma instancia) | ✅ Operacional |
| Fase 3 | Hermes → OpenClaw: sidecar openclaw-mcp-bridge :18790 streamableHttp | ⚠️ Bridge desplegado, GET /mcp falla en Python client |
| Fase 3.1 | Fix GET /mcp: stateful supergateway o bridge Python propio con FastMCP | 🔄 Pendiente |
| Fase 4 | Google A2A protocol (Agent Cards, push bidireccional) | ⬜ Backlog |
