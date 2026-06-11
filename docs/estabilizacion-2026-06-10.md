# Estabilización 2026-06-10 — A2A OpenClaw↔Hermes + Honcho + VIPs

Sesión de diagnóstico y reparación en vivo (vía K8s API). Estado final: **A2A operacional
en ambas direcciones, memoria Honcho persistente, VIPs y DNS de la LAN restaurados.**

## Causas raíz encontradas

| # | Síntoma | Causa raíz | Fix |
|---|---------|-----------|-----|
| 1 | "Hermes no contesta" a OpenClaw | `hermes-free` (qwen3-coder:free) tardaba 181s/llamada → respuestas de 466s; OpenClaw cortaba a los 300s (y el deployment vivo ni tenía el patch → 60s) | `hermes_model: kimi-free` + patch MCP SDK 60/300→**600s** |
| 2 | 429 sin recuperación en Hermes/Honcho | `kimi-free` no tenía fallback chain en LiteLLM | chain `kimi-free → gpt-oss-free, free2, llama70b-free, paid-final` |
| 3 | VIPs .200/.203 sin ARP → **DNS de toda la LAN caído** + image pulls colgados en nodos | L2 announcer de Cilium zombie tras cambios de IP de nodos (lease renovado pero sin responder ARP) | Reinicio de los cilium-agent que sostenían los leases → re-elección → ARP OK |
| 4 | Gateway "no responde" en 443 | Falsa alarma: el listener TLS passthrough sin hostname se traga conexiones sin SNI. Con SNI funciona (los browsers siempre mandan SNI) | n/a (comportamiento esperado) |
| 5 | kagent MCP timeout en ambos agentes | kagent-tools corría en `srv-pi-rack2a`, nodo con IP flapeada (.40→.60) y cert de kubelet inválido | kagent-tools movido a `srv-rk1-nvme-03` (live patch) |
| 6 | LiteLLM Pending eterno | Sin nodeSelector cayó en una Pi 8GB y el pull de `main-latest` se colgó (sin DNS por #3) | nodeSelector a `srv-rk1-nvme-04` (en repo y vivo) |
| 7 | Gateway de OpenClaw no arrancaba (05:05) | Bloque nuevo `tools.media.audio` (whisper STT): la imagen `latest-honcho` rechaza el schema | Quitado del ConfigMap vivo; defaults `openclaw_audio_stt_enabled/tts_enabled: false` hasta actualizar imagen |
| 8 | Sesiones de OpenClaw se perdían en cada restart | `agents/main` montado como emptyDir | emptyDir eliminado → persiste en PVC `openclaw-data` |

## Verificación E2E realizada

- `ask_hermes_agent("PING")` → **PONG** en segundos (antes timeout).
- Hermes registra **155 tools de 3 servers** (kubernetes 21, kagent 124, openclaw 10) — dirección Hermes→OpenClaw operacional.
- OpenClaw: `Honcho memory ready`; Hermes: `Honcho session 'data' retrieved` (memoria previa recuperada → persistencia confirmada).
- PVCs Bound en longhorn-nvme: honcho-postgres (10Gi), openclaw-data (10Gi), hermes-home (10Gi), hermes-data (5Gi).
- `https://grafana.cluster.home` via VIP .200 → 302; DNS .203 resuelve interno y externo.
- Todos los pods del cluster en Running/Ready.

## Cambios en el repo (commitear)

- `roles/install-hermes-agent/defaults/main.yml` — hermes_model: kimi-free
- `roles/install-openclaw/templates/openclaw-deployment.yaml.j2` — timeout MCP 600s, agents/main al PVC
- `roles/install-openclaw/defaults/main.yml` — audio/tts off (imagen no soporta el schema)
- `roles/install-litellm-proxy/tasks/main.yml` — fallbacks kimi-free + nodeSelector RK1
- `CLAUDE.md` — tabla de nodos con IPs reales, honcho en el orden de roles
- `.gitignore` — `.kubeconfig`

**Importante:** los fixes ya están aplicados EN VIVO. Correr ansible con estos cambios
del repo es idempotente y deja todo consistente:

```bash
make ai            # litellm (fallbacks/nodeSelector) + hermes (kimi-free)
make openclaw      # configmap sin audio + deployment con PVC/600s
# Orden de restart si se hace a mano: openclaw primero, hermes después
# (Hermes registra el bridge de OpenClaw solo en el arranque).
```

## Pendientes (no bloqueantes, pero recomendados)

1. **HA del control plane**: solo `srv-super6c-01-nvme` (.120) está joined. Los otros 4
   super6c (.121-.124) tienen SSH pero no K3s → `ansible-playbook ... --tags core`.
2. **DHCP reservations** en el router para TODOS los nodos (los Pi y super6c flapean
   IP — causa raíz #3 y #5). Alternativa: rol fix-mac-address / IPs estáticas.
3. **srv-pi-rack2a**: cert de kubelet no incluye la IP nueva (.60) → `kubectl logs/exec`
   fallan contra ese nodo. Reiniciar k3s-agent en el nodo lo regenera.
4. **Imagen OpenClaw con audio**: actualizar/reconstruir `ghcr.io/villadalmine/openclaw-honcho`
   a una versión que soporte `tools.media.audio` y recién ahí reactivar
   `openclaw_audio_stt_enabled/tts_enabled` en defaults.
5. kagent-tools quedó pineado a rk1-nvme-03 por live patch — persistirlo en el rol/Helm
   values de kagent si se quiere mantener.
