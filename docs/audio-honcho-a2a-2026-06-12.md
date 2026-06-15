# Análisis: Honcho, A2A y Audio bidireccional — 2026-06-12

Sesión de análisis + acciones. Resultado: **audio bidireccional (STT+TTS) funcionando
y VERIFICADO E2E en AMBAS imágenes (OpenClaw y Hermes), deployado vía Ansible, sin
rebuild.** La causa raíz real fue el SSRF guard de OpenClaw (ver sección 0 — corrección),
no el apiKey. Verificado con los runtimes reales en los pods de producción.

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

## 0. CORRECCIÓN 2026-06-12 (tarde) — la causa raíz real era SSRF, no el apiKey

El análisis previo (abajo) concluyó que el audio quedaba habilitado con quitar `apiKey`.
**Eso era incompleto: el config parseaba pero el audio NO se transcribía.** Tito siguió
respondiendo "no puedo procesar mensajes de audio". Diagnóstico en vivo definitivo:

- El voice note llegaba pero NO se transcribía → se inlineaba crudo (base64) en el
  prompt → `267588 tokens` → `Context overflow` → el agente respondía que no podía.
- Causa: el **SSRF guard** de OpenClaw (`isBlockedHostnameOrIp` en `dist/ssrf-*.js`)
  bloquea `whisper-stt.ai.svc.cluster.local` DOS veces: por sufijo `.local` y por IP
  privada del ClusterIP — sin mirar la policy. El schema de media-understanding
  (`ConfiguredProviderRequestSchema`) NO expone `allowPrivateNetwork` (solo lo tienen
  los LLM models), así que **no hay knob de config**.
- Probado invocando el provider real de OpenClaw contra whisper-stt:
  `ERR: Blocked hostname or private/internal/special-use IP address`.

**Fix (idempotente, as-code, sin rebuild):** patch sed en el arranque del gateway
(igual patrón que el del timeout MCP) que cortocircuita `isBlockedHostnameOrIp` cuando
`OPENCLAW_ALLOW_PRIVATE_MEDIA=1`. En `roles/install-openclaw/templates/openclaw-deployment.yaml.j2`.

### Verificación E2E REAL (no aislada) — ambas imágenes, ambas direcciones

Probado con el comando del runtime de OpenClaw (`openclaw infer ...`, que lee la config
deployada) y con las funciones Python reales de Hermes, en los pods en producción:

| Imagen | audio→texto | texto→audio | Cómo se verificó |
|---|---|---|---|
| **OpenClaw** (Tito) | ✅ | ✅ | `infer audio transcribe` → transcript correcto; `infer tts convert` → mp3 |
| **Hermes** | ✅ | ✅ | `transcribe_audio()` → transcript correcto; `text_to_speech_tool()` → .ogg |

Audio de prueba generado con edge-tts ("Probando audio a texto…") → whisper-stt lo
transcribe textual. **Sin rebuild de imagen**: todo vía runtime patches/instalación a PVC.

### Hermes TTS — edge-tts en PVC (NO rebuild)

Hermes tiene `readOnlyRootFilesystem` + venv, así que `lazy_deps` no puede `pip install`
en runtime. Solución: initContainer `install-tts` instala `edge-tts==7.2.7` (== el pin
que espera `tools/lazy_deps.py`) a `/opt/data/pylibs` (PVC) con **`--no-deps`** + `srt`
+ `tabulate`, y el contenedor lo ve vía `PYTHONPATH=/opt/data/pylibs`.
- **`--no-deps` es crítico**: sin él, pip arrastra `pydantic 2.13.4` al PVC y, como
  PYTHONPATH lo antepone, shadowea el `pydantic 2.12.5` pineado de hermes-agent → rompe
  el agente. Verificado: con `--no-deps` el agente mantiene 2.12.5 y el tts_tool sintetiza.

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
- **Hermes**: `tts.provider: edge` + `voice.auto_tts: false` (on-demand vía tts_tool;
  el agente lo usa cuando corresponde, no fuerza audio en cada respuesta). edge-tts
  instalado en el PVC por el initContainer `install-tts` (ver sección 0). **Verificado:
  `text_to_speech_tool()` genera .ogg.** Poné `hermes_tts_auto: true` para audio siempre.

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

## 4. Causa raíz del crash de audio inicial (schema strict)

El gateway había muerto antes con `tools.media.audio.models.0: Invalid input`. Extraído
el zod schema real del bundle (`zod-schema.core-*.js`):

```js
MediaUnderstandingModelSchema = object({
  provider, model, capabilities, type("provider"|"cli"), command, args,
  maxChars, maxBytes, prompt, timeoutSeconds, language, providerOptions,
  deepgram, baseUrl, headers, request, profile, preferredProfile
}).strict()        // ← .strict(): cualquier campo extra invalida TODO el entry
```

**No existe `apiKey`** en el schema → un entry con `"apiKey": "sk-local-whisper"`
invalidaba el modelo completo. Fix: quitar `apiKey` del template. ESTO arregló el
ARRANQUE del gateway, pero NO la transcripción (ver sección 0 — el SSRF era el segundo
bloqueo, el que impedía que el audio se procesara).

`messages.tts` (TtsConfigSchema, también strict): keys válidas `auto/enabled/mode/
provider/persona/personas/summaryModel/modelOverrides/providers/prefsPath/
maxTextLength/timeoutMs`. El bloque original `provider: microsoft` era válido.

---

## 5. Acciones tomadas (todo como código, idempotente — sin rebuild)

| Acción | Archivo | Estado |
|---|---|---|
| Quitar `apiKey` del bloque audio (schema strict) | `roles/install-openclaw/templates/openclaw-configmap.yaml.j2` | ✅ deployado |
| **SSRF patch — el fix real del STT** (`isBlockedHostnameOrIp` cortocircuitado, env-gated) | `roles/install-openclaw/templates/openclaw-deployment.yaml.j2` | ✅ deployado |
| Reactivar STT+TTS OpenClaw | `roles/install-openclaw/defaults/main.yml` | ✅ deployado |
| Hermes TTS: edge-tts==7.2.7 `--no-deps` a PVC + PYTHONPATH | `roles/install-hermes-agent/templates/hermes-static-mcp.yaml.j2` | ✅ deployado |
| Deploy | `make openclaw` + `make ai-hermes-agent-deploy` → `failed=0` | ✅ |
| Verificación E2E real (4 rutas) | runtime CLI OpenClaw + funciones Python Hermes | ✅ |

## 6. Pendientes recomendados

1. **Actualizar `scripts/test-a2a-e2e.sh`** al modo stateless (no exigir `mcp-session-id`).
2. **Confirmación final por Telegram**: mandar un voice note a @tito_es_tu_bot → debe
   transcribir y (al haber mandado audio) responder con voz. Es el único tramo que no
   pude disparar yo (no puedo enviar mensajes de Telegram); el resto del pipeline está
   verificado componente por componente en los pods de producción.
3. **TTS 100% local (piper)** sigue requiriendo rebuild de imagen de OpenClaw (su runtime
   solo trae el provider `microsoft`/edge, que es cloud-gratis). El edge actual es gratis
   y funciona; el rebuild a piper es opcional para quitar la dependencia de Microsoft.
4. **Timeout dialectic de Honcho en Hermes** (30s) — investigar si es configurable.
