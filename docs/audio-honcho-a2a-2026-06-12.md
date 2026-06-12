# Análisis: Honcho, A2A y Audio bidireccional — 2026-06-12

Sesión de análisis + acciones. Resultado: **audio de OpenClaw (STT+TTS) REACTIVADO y
deployado vía Ansible** — la causa raíz del crash era un campo `apiKey` que el schema
no acepta, no la imagen. Todo verificado contra el parser real del gateway.

---

## 1. Memoria Honcho — cómo está integrada

```
                    honcho (namespace)
   honcho-api ── honcho-postgres ── honcho-redis ── honcho-worker
        │  (svc :80 → pod :8000 — NetworkPolicy usa el puerto del POD)
        │
   ┌────┴─────────────────────┐
   │ workspace "openclaw"     │ workspace "hermes"
   │ JWT HS256 propio         │ JWT HS256 propio
   ▼                          ▼
 OpenClaw                   Hermes
```

**OpenClaw** — plugin `openclaw-honcho` (imagen custom `openclaw-honcho`):
- Config en `openclaw.json → plugins.entries.openclaw-honcho` (baseUrl, apiKey JWT,
  workspaceId, timeoutMs 90000) + slot `memory`.
- Peer map `openclaw-peers.json` persiste en PVC `openclaw-data` (subPath `.honcho-state`
  montado como `/home/node/.honcho`) — sin esto la memoria quedaba "amnésica" por restart.
- Estado vivo: `Honcho memory ready`.

**Hermes** — provider nativo `memory.provider: honcho` en `config.yaml`:
- Env: HONCHO_URL/WORKSPACE/API_KEY (de `hermes-secrets`).
- `memory_char_limit: 2200`, `user_char_limit: 1375`, user_profile habilitado.
- Estado vivo: funciona, pero los **dialectic queries a veces exceden el timeout de 30s**
  (el razonamiento dialéctico de Honcho usa LLM → con modelos free lentos se corta).
  Mitigación ya aplicada: chain de fallback para `kimi-free` en LiteLLM. Pendiente
  opcional: subir el timeout del cliente Honcho de Hermes si la imagen lo permite.

**Aislamiento**: cada agente solo ve su workspace (JWT con claim `w`). Comparten la
instancia, no los datos. El "Honcho compartido" del A2A es a nivel infraestructura.

---

## 2. A2A — cómo funciona (estado 2026-06-12)

Dos planos:

**MCP tool-calling (síncrono)** — cada agente expone tools del otro:
- OpenClaw → Hermes: MCP streamable-http en `hermes-agent-mcp.ai:8000/mcp`.
  Desde el commit `992ad4b` el server de Hermes es **STATELESS**: `initialize` NO
  devuelve `mcp-session-id` y los `tools/*` funcionan sin sesión (verificado:
  `tools/list` sin sesión → 11 tools con `ask_hermes_agent`).
  ⚠️ `scripts/test-a2a-e2e.sh` aún espera el header de sesión → su test
  "OpenClaw → Hermes initialize" da FAIL falso. Actualizar el script.
- Hermes → OpenClaw: bridge stateful Node.js (`bridge.js` embebido en el deployment)
  en `:18790/mcp`. Spawnea `openclaw mcp serve` una vez y rutea por stdio.
  Tool extra sintetizada: `ask_openclaw_agent`.

**A2A real (razonamiento)** — `ask_hermes_agent` / `ask_openclaw_agent` disparan el
loop completo del agente remoto (con sus propios MCPs). Timeouts: MCP SDK parcheado
a 600s en el arranque del gateway; `mcp.servers.hermes.timeout: 600`.

Orden de arranque (importa): **openclaw primero, hermes después** — Hermes registra
el bridge de OpenClaw solo en su startup. Codificado como orden de roles en bootstrap.

Verificación de hoy: e2e 10 PASS / 1 FAIL (el falso del session-id) / 2 SKIP, con
`tools/list` cruzados OK en ambas direcciones tras el redeploy.

---

## 3. Audio ↔ texto en ambos sentidos

### Entrante (voz → texto) — 100% LOCAL ✅
```
Telegram voice note (OGG/Opus)
  → OpenClaw tools.media.audio (provider openai, baseUrl custom)      ─┐
  → Hermes stt.openai (fallback sin Groq key)                          ├→ whisper-stt
  → http://whisper-stt.ai.svc.cluster.local:9000/v1/audio/transcriptions ┘   (in-cluster)
```
- `whisper-stt`: `hwdsl2/whisper-server`, faster-whisper `small` int8, CPU RK1,
  PVC longhorn para cache del modelo. **Modelo local, sin API keys, costo $0.**
- Hermes alternativa: Groq Whisper (cloud free) si se carga `hermes_groq_api_key`.

### Saliente (texto → voz)
- **OpenClaw**: `messages.tts` provider `microsoft` (alias `edge`) → `node-edge-tts`
  (Microsoft Edge neural, voz `es-AR-TomasNeural`, `auto: inbound`). Gratis pero
  **cloud** (best-effort, sin SLA).
- **Hermes**: `voice.auto_tts: false` — TTS de Hermes no habilitado (la imagen tiene
  el flag pero no se validó su pipeline; OpenClaw es el canal de voz primario).

### ¿TTS 100% local (piper)?
Evaluado contra la imagen desplegada (`openclaw-honcho` digest `13bd3a5d`):
- El runtime implementa **UN solo speech provider**: `buildMicrosoftSpeechProvider`
  (id `microsoft`, alias `edge`). **No existe provider TTS `openai` ni soporte de
  `baseUrl`** → no se puede enchufar un Piper/openedai-speech local hoy.
- Para lograrlo hace falta **upgrade/rebuild de la imagen** a una versión con provider
  TTS OpenAI-compatible (o plugin); recién entonces: rol `install-piper-tts`
  (imagen arm64 con `/v1/audio/speech`) + `messages.tts.providers.openai.baseUrl`.
- STT ya es local; el único tramo cloud del pipeline de audio es el TTS Edge.

---

## 4. Causa raíz del crash de audio (resuelta hoy)

El gateway moría con `tools.media.audio.models.0: Invalid input`. Extraído el zod
schema real del bundle (`zod-schema.core-*.js`):

```js
MediaUnderstandingModelSchema = object({
  provider, model, capabilities, type("provider"|"cli"), command, args,
  maxChars, maxBytes, prompt, timeoutSeconds, language, providerOptions,
  deepgram, baseUrl, headers, request, profile, preferredProfile
}).strict()        // ← .strict(): cualquier campo extra invalida TODO el entry
```

**No existe `apiKey`** en el schema → nuestro entry con `"apiKey": "sk-local-whisper"`
invalidaba el modelo completo. Fix: quitar `apiKey` del template (whisper-stt no
exige auth in-cluster; si hiciera falta: `headers: {"Authorization": "Bearer ..."}`).

`messages.tts` (TtsConfigSchema, también strict): keys válidas `auto/enabled/mode/
provider/persona/personas/summaryModel/modelOverrides/providers/prefsPath/
maxTextLength/timeoutMs`. El bloque original `provider: microsoft` era válido.

Validación: variantes testeadas contra `node dist/index.js gateway` con HOME
alternativo dentro del contenedor (sin tocar el pod productivo) — la variante sin
`apiKey` pasa el parse; con `apiKey` falla; `type` debe ser `provider`/`cli`.

---

## 5. Acciones tomadas (todo como código, idempotente)

| Acción | Archivo | Estado |
|---|---|---|
| Quitar `apiKey` del bloque audio | `roles/install-openclaw/templates/openclaw-configmap.yaml.j2` | ✅ deployado |
| Reactivar STT+TTS | `roles/install-openclaw/defaults/main.yml` (`openclaw_audio_stt_enabled/tts_enabled: true`) | ✅ deployado |
| Deploy | `ansible-playbook --tags openclaw` → `failed=0, changed=15` | ✅ |
| Restart ordenado | openclaw → hermes (rollout) | ✅ |
| Verificación | gateway 4/4 Running sin crash; CM vivo con audio+tts; A2A tools/list ambas direcciones | ✅ |
| Gotcha documentado | `skills/whisper-stt/SKILL.md` | ✅ |

## 6. Pendientes recomendados

1. **Actualizar `scripts/test-a2a-e2e.sh`** al modo stateless (no exigir `mcp-session-id`).
2. **TTS local**: rebuild de `openclaw-honcho` con provider TTS OpenAI-compatible →
   rol `install-piper-tts` (modelos es_ES/es_MX de piper corren bien en RK1).
3. **Timeout dialectic de Honcho en Hermes** (30s) — investigar si es configurable.
4. Probar e2e desde Telegram: voice note → transcripción → respuesta en audio
   (`/tts status` muestra el último intento de TTS).
