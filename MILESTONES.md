# Project Milestones

## May 2026
- **RK1 NPU Pool Deployed:** Successfully deployed 4x TuringPi 2 RK1 nodes running `rkllama` with Llama-3.1-8B w8a8.
- **Agent Mesh Architecture:** Implemented OpenClaw, HolmesGPT, and Hermes agents routing through a centralized LiteLLM proxy.
- **Context Window Protection:** Documented and enforced the 4096 token limit for the RK3588S NPU. Added `context_window_fallbacks` to route large context prompts to the local GPU and Nemotron (OpenRouter), preventing `rkllama` crashes.

## April 2026
- **Gateway API & Cilium Migration:** Removed MetalLB, fully migrated to Cilium L2 Announcements and Gateway API.
- **Storage:** Migrated cluster storage to Longhorn NVMe for performance-intensive AI tasks and kept SMB NAS for persistent logs/metrics.
