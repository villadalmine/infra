# Project Milestones

## May 2026
- **RK1 NPU Pool Deployed:** Successfully deployed 4x TuringPi 2 RK1 nodes running `rkllama` with Llama-3.1-8B w8a8.
- **Agent Mesh Architecture:** Implemented OpenClaw, HolmesGPT, and Hermes agents routing through a centralized LiteLLM proxy.
- **Context Window Protection:** Documented and enforced the 4096 token limit for the RK3588S NPU. Added `context_window_fallbacks` to route large context prompts to the local GPU and Nemotron (OpenRouter), preventing `rkllama` crashes.
- **DeepSeek Integration & Fallbacks:** Added `deepseek-v4-pro` and `deepseek-v4-flash:free` to context window fallback chains, prioritizing paid credits but safely falling back to free models when OpenRouter quota is exhausted (403 errors).
- **Deployment Velocity Improvement:** Refactored Makefile and Ansible tags to decouple Kaniko ARM64 image builds from `make ai`. Reduced AI stack deployment time from ~15 minutes to seconds. Created a dedicated `make ai-build` target for image compilation.

## April 2026
- **Gateway API & Cilium Migration:** Removed MetalLB, fully migrated to Cilium L2 Announcements and Gateway API.
- **Storage:** Migrated cluster storage to Longhorn NVMe for performance-intensive AI tasks and kept SMB NAS for persistent logs/metrics.
- **Headlamp Native UI Integration:** Migrated Headlamp from a local workstation binary to a fully automated in-cluster Helm deployment (`make headlamp`). Created a dynamic initContainer pipeline to inject both the official `ai-assistant` plugin and our custom `headlamp-holmes` plugin, binding it directly to the cluster's Holmes/LiteLLM AI stack.
