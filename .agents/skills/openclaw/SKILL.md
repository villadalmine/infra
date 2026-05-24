---
name: "openclaw"
description: "OpenClaw Personal AI Gateway: Telegram bot, Modular RBAC, LiteLLM proxy integration, and webhook management"
---

# OpenClaw — Personal AI Gateway

OpenClaw is a personal AI assistant and gateway that integrates directly into the Kubernetes cluster. It listens for commands via Telegram and executes tasks locally in the homelab environment.

## Overview
- **Deployment**: `ghcr.io/openclaw/openclaw` (Multi-arch/ARM64)
- **Namespace**: `openclaw`
- **Domain**: `openclaw.cluster.home`
- **Configuration**: Managed entirely via Ansible (`roles/install-openclaw`)

## Integrations

### 1. Webhooks & CI/CD Automation
OpenClaw can function as an autonomous agent processing webhooks.
- **Endpoint**: `https://openclaw.cluster.home/hooks/agent` (for complex analysis) or `/hooks/wake` (for simple ping)
- **Allowed Ingress**: Traffic is permitted from `gateway` (for external sources like GitHub Actions) and internal namespaces like `argocd`, `monitoring` (AlertManager), and `kaniko`.
- **Behavior**:
  - Automatically parses payloads (e.g. `alertname`, `status`, `repo`).
  - If a job/workflow is successful (`severity=info`, `sync-success`), it notifies Telegram directly without polling Kubernetes.
  - If a job fails, it leverages the `kubernetes-mcp` sidecar to fetch logs/events for context before alerting.

### 2. Honcho Memory (Dialectic Reasoning)
OpenClaw uses **Honcho** to provide persistent, AI-native memory across sessions.
- **Provider**: `@honcho-ai/openclaw-honcho` plugin (auto-installed on pod startup via `init-plugins`).
- **Configuration**: 
  - `HONCHO_WORKSPACE=openclaw`
  - `HONCHO_API_KEY` (secret)
- **How it works**: The "Dreaming Agent" continuously infers facts and user preferences asynchronously in the background. OpenClaw injects this derived context into the prompt, giving it a persistent personality and deep context about the user's homelab.

### 3. LiteLLM Proxy
All model requests flow through the in-cluster LiteLLM Proxy (`http://litellm-proxy.ai.svc.cluster.local:4000`), ensuring usage is tracked in Prometheus.
- **Primary Model**: `litellm/gpt-4o` (which LiteLLM routes according to its own fallback chains, typically `openai/gpt-oss-120b:free` via OpenRouter).

### 4. Kubernetes MCP Sidecar
OpenClaw includes a `kubernetes-mcp-server` sidecar container running on `localhost:8080`.
- Used for fast, read-only inspection of the cluster (listing pods, checking node resources, reading logs).
- Controlled by `openclaw_rbac_level` (default: `readonly`, can be escalated to `operator` or `cluster-admin`).

## Debugging

**View gateway logs:**
```bash
kubectl logs -n openclaw deploy/openclaw -c openclaw-gateway -f
```

**Check Honcho plugin installation:**
```bash
kubectl logs -n openclaw deploy/openclaw -c init-plugins
```

**Trigger a test webhook:**
```bash
curl -X POST https://openclaw.cluster.home/hooks/wake \
  -H "Authorization: Bearer <OPENCLAW_GATEWAY_TOKEN>" \
  -d '{"event": "test_ping", "status": "success"}'
```
