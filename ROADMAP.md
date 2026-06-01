# Infra-AI Project Roadmap

## Q2 2026 - AI Integration & Stability
- [x] Migrate LiteLLM to support `least_busy` load balancing across RK1 NPU pool.
- [x] Implement robust context-window fallbacks for OpenClaw/LiteLLM to avoid NPU crashes.
- [ ] Add monitoring dashboards specifically for NPU health (temperature, utilization, context limits).
- [x] Implement automatic failover for local GPU if it goes offline.
- [x] Deploy Headlamp Kubernetes UI in-cluster with custom headlamp-holmes AI plugin.
- [x] Evaluate and integrate the official Headlamp `ai-assistant` plugin once stable.
- [x] Fix kaniko DiskPressure: all build workspaces migrated from `local-path` → `longhorn-nvme`; Argo Workflows have ttlStrategy + ephemeral-storage limits.
- [x] Fix Honcho memory reasoning failure: switch reasoning model to `kimi-free` (kimi-k2.6:free); add `modify_params: true` to LiteLLM to handle `tool_choice: "any"` from Honcho deriver.
- [x] Add free model tier expansion: `kimi-free` (262K ctx), `gemma4-free` (31B), updated `default` fallback chain to `kimi-free → gemma4-free → deepseek4v-free → deepseek-pro`.
- [ ] Verify OpenClaw→Honcho memory end-to-end: check official openclaw+honcho integration docs, confirm plugin config matches SDK expectations, test memory save/recall across sessions.
- [ ] Hermes→Honcho memory integration: verify hermes-agent uses correct workspace/peer IDs per official Hermes+Honcho guide.

## Q3 2026 - Scale & Storage
- [ ] Upgrade Longhorn CSI to support node-specific NVMe affinity for the RK1 pool.
- [ ] Introduce a secondary NPU model (e.g., Qwen 1.5) for lighter tasks to save KV cache.
- [ ] Evaluate Gateway API v1.1 features for AI traffic routing.
- [ ] Scale Hermes to 0 replicas gracefully when RK1 node DiskPressure detected — add PodDisruptionBudget and eviction alerts.

## Q4 2026 - Automation
- [ ] Full GitOps automation with ArgoCD ApplicationSets for all AI agents.
- [ ] Nightly model weight updates via automated CronJobs.
