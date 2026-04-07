# AI Tools Setup

This repo supports two AI coding assistants: **Claude Code** and **OpenCode**.
Both are configured to use a local LiteLLM proxy as the AI backend,
giving you a single place to manage providers, API keys, and fallback chains.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Your workstation                                               │
│                                                                 │
│  Claude Code  ──┐                                               │
│                 ├──▶  LiteLLM (localhost:4000)                  │
│  OpenCode     ──┘     setup/litellm/config.yaml                       │
│                            │                                    │
│                            ├──▶ OpenRouter (default)            │
│                            │    └─ Claude, Gemini, GPT-4,       │
│                            │       free models — one API key    │
│                            │                                    │
│                            ├──▶ Anthropic API (direct, optional)│
│                            ├──▶ Google AI (direct, optional)    │
│                            └──▶ Ollama (local, no key needed)   │
│                                                                 │
│  MCP servers:                                                   │
│    kubernetes-mcp ──▶ ~/.kube/config ──▶ K3s cluster            │
└─────────────────────────────────────────────────────────────────┘

After deploying the AI stack (make ai):
  Claude Code / OpenCode can also point to:
    LiteLLM in-cluster → http://litellm.cluster.home/v1
    (same config, same API key, runs on the cluster)
```

---

## Quick Start

### 1. Install dependencies

```bash
make deps   # installs mise → python/node/kubectl/helm + pip packages + ansible collections
```

### 2. Start LiteLLM

```bash
export OPENROUTER_API_KEY=sk-or-v1-...   # get one at openrouter.ai (free tier available)
make litellm
# LiteLLM running at http://localhost:4000
```

### 3. Start your AI tool

```bash
# Claude Code
claude   # reads CLAUDE.md + .claude/settings.json + .mcp.json automatically

# OpenCode
opencode   # reads opencode.json automatically
```

That's it — both tools are pre-configured to use `localhost:4000`.

---

## Configuration Files

### `setup/litellm/config.yaml` — AI model routing

Central config for the local LiteLLM proxy. Defines which models are available,
which provider serves each model, and the fallback chain.

**Models available after starting LiteLLM:**

| Model name | Routed to | Cost |
|-----------|-----------|------|
| `default` | claude-sonnet-4-5 via OpenRouter | ~$0.003/1k tokens |
| `claude-sonnet` | claude-sonnet-4-5 via OpenRouter | ~$0.003/1k tokens |
| `claude-opus` | claude-opus-4 via OpenRouter | ~$0.015/1k tokens |
| `gemini-flash` | gemini-flash-1.5 via OpenRouter | ~$0.00015/1k tokens |
| `gpt-4o-mini` | gpt-4o-mini via OpenRouter | ~$0.00015/1k tokens |
| `free` | qwen3-coder:free → nemotron:free | free |
| `cheap` | qwen-turbo via OpenRouter | ~$0.00003/1k tokens |

**Fallback chain:** `default → claude-sonnet → cheap` (automatic on rate limit or error)

**Adding a direct provider** (lower latency, bypass OpenRouter):

Uncomment the relevant section in `setup/litellm/config.yaml` and set the env var:

```bash
# Direct Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
# Uncomment: model_name: claude-direct in setup/litellm/config.yaml

# Direct Gemini
export GOOGLE_API_KEY=AI...
# Uncomment: model_name: gemini-direct in setup/litellm/config.yaml

# Local Ollama (no key needed)
# Uncomment: model_name: local in setup/litellm/config.yaml
# Requires: ollama serve (running locally)
```

### `opencode.json` — OpenCode configuration

Pre-configured to use LiteLLM at `localhost:4000` with all model aliases declared.
The default model is `litellm/default`.

To switch model in OpenCode: use the model picker in the UI, or set `"model": "litellm/claude-opus"`.

### `.claude/settings.json` — Claude Code permissions

Pre-configured with permissions for all common operations:
`ansible-playbook`, `kubectl`, `helm`, `make`, `git`, `ssh`, `curl`, `dig`, etc.

No changes needed — Claude Code reads this automatically.

### `.mcp.json` — MCP servers (Claude Code + OpenCode)

Configures the **kubernetes MCP server** — lets AI tools query and manage
the K3s cluster directly via natural language.

```json
{
  "mcpServers": {
    "kubernetes": {
      "command": "npx",
      "args": ["-y", "kubernetes-mcp-server@latest"]
    }
  }
}
```

**Requires:** `~/.kube/config` exists (created by `make core` → `roles/get-kubeconfig`).

Once active, you can ask Claude Code:
- "Which pods are failing in the monitoring namespace?"
- "Show me the resource usage of the RK1 nodes"
- "What events happened in kube-system in the last 10 minutes?"

---

## After Cluster Deployment (`make ai`)

The AI stack deploys an in-cluster LiteLLM at `litellm.cluster.home`.
You can switch to it instead of the local proxy:

**OpenCode** — edit `opencode.json`:
```json
"baseURL": "http://litellm.cluster.home/v1",
"apiKey": "sk-hermes-internal"
```

**Or** create `opencode.local.json` (gitignored) with just the override:
```json
{
  "provider": {
    "litellm": {
      "options": {
        "baseURL": "http://litellm.cluster.home/v1"
      }
    }
  }
}
```

**Claude Code** — no change needed (Claude Code uses its own API, not LiteLLM).
But the Kubernetes MCP server in `.mcp.json` already points to the cluster kubeconfig.

---

## Available Models Reference

### Via OpenRouter (one `OPENROUTER_API_KEY` covers all)

Free models (no cost):
```
openrouter/qwen/qwen3-coder:free          ← best free coding model
openrouter/nvidia/nemotron-3-super-120b:free
openrouter/google/gemini-2.0-flash-exp:free
```

Paid models (cheap):
```
openrouter/qwen/qwen-turbo                ← cheapest reliable
openrouter/google/gemini-flash-1.5        ← fast + cheap
openrouter/openai/gpt-4o-mini
```

Premium:
```
openrouter/anthropic/claude-sonnet-4-5    ← best for code + long context
openrouter/anthropic/claude-opus-4        ← most capable
```

### Direct providers (optional, lower latency)

| Provider | Key env var | Best for |
|---------|------------|---------|
| Anthropic | `ANTHROPIC_API_KEY` | Claude directly, no OpenRouter overhead |
| Google | `GOOGLE_API_KEY` | Gemini directly |
| OpenAI | `OPENAI_API_KEY` | GPT-4 directly |
| Ollama | none | 100% local, no data leaves machine |

---

## Debugging

```bash
# Is LiteLLM running?
curl http://localhost:4000/health

# What models are available?
curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer sk-infra-local" | python3 -m json.tool

# Test a model
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-infra-local" \
  -H "Content-Type: application/json" \
  -d '{"model": "default", "messages": [{"role": "user", "content": "hello"}]}'

# LiteLLM logs (verbose mode)
# Edit setup/litellm/config.yaml: uncomment "set_verbose: true"
# Then restart: make litellm
```

### Claude Code: MCP server not connecting

```bash
# Verify kubeconfig exists
ls ~/.kube/config

# Test kubernetes-mcp manually
npx -y kubernetes-mcp-server@latest

# Check Claude Code MCP status
# In Claude Code: /mcp
```

### OpenCode: model not responding

```bash
# Check LiteLLM is running
curl http://localhost:4000/health

# Check API key is set
echo $OPENROUTER_API_KEY

# Restart LiteLLM after changing API key
make litellm
```
