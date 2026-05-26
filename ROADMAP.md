# Infra-AI Project Roadmap

## Q2 2026 - AI Integration & Stability
- [x] Migrate LiteLLM to support `least_busy` load balancing across RK1 NPU pool.
- [x] Implement robust context-window fallbacks for OpenClaw/LiteLLM to avoid NPU crashes.
- [ ] Add monitoring dashboards specifically for NPU health (temperature, utilization, context limits).
- [ ] Implement automatic failover for local GPU if it goes offline.

## Q3 2026 - Scale & Storage
- [ ] Upgrade Longhorn CSI to support node-specific NVMe affinity for the RK1 pool.
- [ ] Introduce a secondary NPU model (e.g., Qwen 1.5) for lighter tasks to save KV cache.
- [ ] Evaluate Gateway API v1.1 features for AI traffic routing.

## Q4 2026 - Automation
- [ ] Full GitOps automation with ArgoCD ApplicationSets for all AI agents.
- [ ] Nightly model weight updates via automated CronJobs.
