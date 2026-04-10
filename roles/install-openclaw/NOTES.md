# OpenClaw Integration Notes

## Goal

Route all LLM traffic from OpenClaw through the in-cluster LiteLLM proxy instead of calling
`api.openai.com` directly, and enable the Telegram channel.

---

## Key Discoveries

### 1. OpenClaw ignores `OPENAI_API_BASE` env var

The `baseUrl` for the OpenAI provider is **not** read from environment variables.
It is read exclusively from `openclaw.json`:

```js
// From /app/dist/provider-catalog-shared-Cxm2QDAO.js (minified source)
function resolveConfiguredOpenAIBaseUrl(cfg) {
  return normalizeOptionalString(cfg?.models?.providers?.openai?.baseUrl)
    ?? "https://api.openai.com/v1";
}
```

**Fix**: set `models.providers.openai.baseUrl` in the ConfigMap JSON.

### 2. `models.providers.openai.models` must be an array of objects, NOT strings

Discovered by reading `/app/dist/provider-catalog-shared-Cxm2QDAO.js`:

```js
function readConfiguredProviderCatalogEntries(params) {
  const models = resolveConfiguredProviderModels(params.config, params.providerId);
  for (const model of models) {
    if (!model || typeof model !== "object") continue;  // <-- strings are skipped/rejected
    const id = typeof model.id === "string" ? model.id.trim() : "";
    ...
  }
}
```

**Schema** for each entry:
```json
{
  "id": "gpt-4o",          // required - model ID sent to the API
  "name": "GPT-4o",        // optional - display name (defaults to id)
  "contextWindow": 128000,  // optional - integer
  "reasoning": false,       // optional - boolean
  "input": ["text", "image"] // optional - array of "text"|"image"|"document"
}
```

**Broken config (caused CrashLoopBackOff)**:
```json
"models": ["gpt-4o", "gpt-4o-mini"]
```

**Correct config**:
```json
"models": [
  { "id": "gpt-4o", "name": "GPT-4o" },
  { "id": "gpt-4o-mini", "name": "GPT-4o Mini" }
]
```

### 3. Model ID prefix behavior

Any model ID without a provider prefix (e.g. `gemini-free`, `gpt-4o`) gets automatically
routed to the `openai` provider internally. This is fine since we override `baseUrl` to
point to LiteLLM, which handles the aliasing.

### 4. Init-container copies ConfigMap to PVC

The deployment has an init-container that copies `openclaw.json` from the ConfigMap
to the PVC before the main container starts. ConfigMap changes only take effect
after a full pod restart (rollout restart), not just a ConfigMap update.

### 5. LiteLLM in-cluster works correctly

Verified via port-forward: LiteLLM responds correctly to `openai/gpt-4o` and
model aliases when called with the internal master key `sk-hermes-internal`.

### 6. OpenRouter rate limits (openclaw API key)

| Model | Status |
|-------|--------|
| `google/gemma-4-31b-it:free` | 200 OK (occasional rate limit) |
| `nvidia/nemotron-3-super-120b-a12b:free` | 200 OK (reliable) |
| `google/gemma-3-27b-it:free` | 429 rate limit |
| `qwen/qwen3-coder:free` | 429 rate limit |

### 7. Telegram bot confirmed working

Bot `@tito_es_tu_bot` connects correctly when `openclaw_telegram_enabled: true`
and the token is set in `defaults/secrets.yml`.

---

## Files Changed

### `roles/install-openclaw/templates/openclaw-configmap.yaml.j2`

- Added `models.providers.openai` block with `baseUrl`, `apiKey`, and correct `models` array format
- `baseUrl` points to `{{ openclaw_litellm_url }}/v1` (in-cluster LiteLLM)
- `apiKey` uses `{{ openclaw_litellm_master_key }}` (`sk-hermes-internal`)

### `roles/install-openclaw/defaults/main.yml`

- `openclaw_telegram_enabled: true`
- `openclaw_model_primary: "gpt-4o"` (routed via LiteLLM)

### `roles/install-litellm-proxy/tasks/main.yml` (commit `67df14d`)

- Fixed invalid model IDs: `qwen/qwen-pro` → `qwen/qwen3.6-plus`
- Removed `hermes-qwen` from openclaw fallback chain (it used a different API key quota)
- Updated free model: `gemini-2.5-pro-exp` → `google/gemma-4-31b-it:free`

---

## Architecture

```
Telegram → OpenClaw pod (openclaw namespace)
              │
              └─ POST http://litellm-proxy.ai.svc.cluster.local:4000/v1/chat/completions
                    │  Authorization: Bearer sk-hermes-internal
                    │  model: gpt-4o  (aliased in LiteLLM config)
                    │
                    └─ OpenRouter → google/gemma-4-31b-it:free (or nemotron fallback)
```

### LiteLLM model aliases (relevant to openclaw)

| Alias | Real model | Key used |
|-------|-----------|----------|
| `gpt-4o` | `google/gemma-4-31b-it:free` | OPENCLAW_OPENROUTER_API_KEY |
| `gpt-4o-mini` | `nvidia/nemotron-3-super-120b-a12b:free` | OPENCLAW_OPENROUTER_API_KEY |

---

## Deployment Commands

```bash
# Deploy only openclaw (fast, ~30s)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags openclaw

# Deploy litellm only (without touching hermes agent)
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini \
  --tags ai-hermes-deploy --skip-tags ai-hermes-agent

# Watch openclaw logs in real time
kubectl logs -n openclaw -l app=openclaw -f

# Check pod status
kubectl get pods -n openclaw

# Verify LiteLLM is reachable from within cluster (run from any pod)
curl -s http://litellm-proxy.ai.svc.cluster.local:4000/v1/models \
  -H "Authorization: Bearer sk-hermes-internal"
```

---

## Current Status (as of session end)

- [x] Telegram enabled and bot connects
- [x] ConfigMap schema fixed (objects, not strings)
- [x] LiteLLM model aliases correct
- [ ] **Not yet verified**: openclaw actually routes to LiteLLM after fix deployment
- [ ] **Next step**: run `--tags openclaw` playbook, watch logs for API calls to confirm
      they go to `litellm-proxy.ai.svc` and NOT to `api.openai.com`

---

## Relevant K8s Resources

| Resource | Namespace | Purpose |
|----------|-----------|---------|
| `deployment/openclaw` | `openclaw` | Main gateway pod |
| `configmap/openclaw-config` | `openclaw` | JSON config + AGENTS.md |
| `secret/openclaw-secrets` | `openclaw` | Telegram token, allowed users |
| `deployment/litellm-proxy` | `ai` | LiteLLM proxy |
| `secret/litellm-secrets` | `ai` | OPENCLAW_OPENROUTER_API_KEY etc. |

---

## Dist Files Inspected for Schema Discovery

All in `/app/dist/` inside the `ghcr.io/openclaw/openclaw:latest` image:

- `provider-catalog-shared-Cxm2QDAO.js` — model array parsing, `baseUrl` resolution
- `models-config-hVLegEdl.js` — model config merge logic, field names
- `zod-schema.providers-core-WNCesmUy.js` — channel/provider Zod schemas
- `provider-model-shared-BTeN02cH.js` — provider model API compatibility

The error `models.providers.openai.models.0: Invalid input: expected object, received string`
comes from the Zod validation in `zod-schema.providers-core-WNCesmUy.js` when model entries
are plain strings instead of objects.
