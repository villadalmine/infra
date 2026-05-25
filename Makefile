# Infra Makefile - Simplified Ansible workflow
# Usage: make <target>

ANSIBLE := $(shell test -f .venv/bin/ansible-playbook && echo .venv/bin/ansible-playbook || echo ansible-playbook)
INVENTORY := inventory/hosts.ini
BOOTSTRAP := playbooks/bootstrap.yml
UNINSTALL := playbooks/uninstall.yml

# Overridable via env
SSH_KEY      ?=
SUDOERS_MODE ?= full
ANSIBLE_USER ?=
ARGO_NS      ?= kaniko

.PHONY: help deps deps-ai deps-ops deps-full preview preview-ai preview-ops preview-full uninstall-local hermes-install holmesgpt-install setup-nodes setup-sudoers core networking encryption networking-observability networking-observability-basic networking-observability-security networking-observability-full ingress dns-metrics services observability storage ai ai-all ai-registry ai-hermes-build ai-hermes-deploy ai-litellm-proxy-deploy ai-hermes-agent-deploy ai-holmes holmes-ui ai-kubernetes-mcp-build kagent security full clean healthcheck node-identity node-stats survey litellm openclaw openclaw-rbac fix-mac-address gpu-bench gpu-evict gpu-status argo-workflows leloir-build leloir leloir-all leloir-oidc dex nas-admin-build nas-admin-build-logs nas-admin nas-admin-test nas-admin-all longhorn longhorn-bench

help: ## Show this help message (start here if you're new)
	@echo ""
	@echo "  First time? Run in order:"
	@echo "    0. make preview       see exactly what make deps will install (read-only)"
	@echo "    1. make deps          install mandatory tools (ansible, kubectl, helm, jq)"
	@echo "       make deps-ai       add AI tools  (litellm, fastmcp, opencode)"
	@echo "       make deps-ops      add ops tools (k9s, nova, ansible-lint)"
	@echo "       make deps-full     install everything at once"
	@echo "    2. make setup-nodes   copy SSH key + configure sudo on nodes (needs password once)"
	@echo "    3. make survey        collect hardware info from all nodes"
	@echo "    4. make litellm       start local AI assistant (optional — needs deps-ai)"
	@echo "    5. make core          bootstrap K3s cluster"
	@echo ""
	@echo "  Undo workstation install: make uninstall-local"
	@echo ""
	@echo "  All targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

preview: ## Show what 'make deps' (core) will install — no changes made
	@bash scripts/deps-preview core

preview-ai: ## Show what 'make deps-ai' will install — no changes made
	@bash scripts/deps-preview ai

preview-ops: ## Show what 'make deps-ops' will install — no changes made
	@bash scripts/deps-preview ops

preview-full: ## Show what 'make deps-full' will install — no changes made
	@bash scripts/deps-preview full

uninstall-local: ## Remove all workstation tools installed by any deps target
	@bash scripts/uninstall-local

hermes-install: ## Install Hermes Agent CLI locally — hermes chat -q "ask something" (optional)
	@mise run install-hermes

holmesgpt-install: ## Install HolmesGPT CLI locally — holmes ask "why is X crashing?" (optional)
	@mise run install-holmesgpt

deps: ## Install mandatory tools only — ansible, kubectl, helm, jq (run this first)
	@echo "Installing core workstation tools. Run 'make preview' to see what changes."
	@echo ""
	@command -v mise >/dev/null 2>&1 || (echo "Installing mise..." && curl https://mise.run | sh && echo 'eval "$$(~/.local/bin/mise activate bash)"' >> ~/.bashrc)
	@mise install python "aqua:kubernetes/kubectl" "aqua:helm/helm" "aqua:jqlang/jq"
	@mise run setup-core

deps-ai: deps ## Add AI tools — litellm, fastmcp, opencode, node
	@mise install node "npm:opencode-ai"
	@mise run setup-ai

deps-ops: deps ## Add ops tools — k9s, nova, ansible-lint
	@mise install "aqua:derailed/k9s" "aqua:FairwindsOps/nova"
	@mise run setup-ops

deps-full: deps deps-ai deps-ops ## Install everything — core + ai + ops

setup-nodes: ## Configure SSH access + sudo on nodes (run once, needs password)
	@SSH_KEY="$(SSH_KEY)" SUDOERS_MODE="$(SUDOERS_MODE)" bash scripts/setup-node-access

setup-sudoers: ## Update sudoers on all nodes — shows diff and asks approval before changing
	$(ANSIBLE) playbooks/setup-node-access.yml -i $(INVENTORY) --tags sudoers --diff

litellm: ## Start local LiteLLM proxy (AI router — needed for OpenCode AI features)
	@echo "Starting LiteLLM on http://localhost:4000"
	@echo "Set at least one API key first:"
	@echo "  export OPENROUTER_API_KEY=sk-or-..."
	@echo "  export ANTHROPIC_API_KEY=sk-ant-...  (optional)"
	@echo ""
	@litellm --config setup/litellm/config.yaml --port 4000

quick: ## Quick cluster — K3s + Cilium only. DIY from here. No ingress/DNS/storage CSI.
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags core,networking

core: ## Install K3s + kubeconfig only (WARNING: cluster unusable without make networking)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags core

networking: ## Install core + networking (Cilium, LB-IPAM, Gateway API) + WireGuard encryption
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags networking,encryption

encryption: ## WireGuard encryption (requires networking)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags core,networking,encryption

networking-observability: ## Install networking + Hubble metrics ServiceMonitor (requires observability)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags core,networking,observability,networking-observability

networking-observability-basic: ## Install networking + Hubble metrics with basic profile
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags core,networking,observability,networking-observability -e "cilium_hubble_metrics_profile=basic"

networking-observability-security: ## Install networking + Hubble metrics with security profile  
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags core,networking,observability,networking-observability -e "cilium_hubble_metrics_profile=security"

networking-observability-full: ## Install networking + Hubble metrics with full profile (default)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags core,networking,observability,networking-observability -e "cilium_hubble_metrics_profile=full"

ingress: ## Install networking + ingress (cert-manager, Gateway)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ingress

dns-metrics: ## Install DNS and Metrics (Pi-hole)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags dns-metrics

services: ## Install ingress + services (ArgoCD, Argo Workflows, helm-dashboard, registry)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags core,networking,ingress,services

argo-workflows: ## Install Argo Workflows + expose UI at argo.cluster.home (idempotent)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags argo-workflows

longhorn: ## Prepare NVMe mounts on RK1 nodes + deploy Longhorn (StorageClass: longhorn-nvme)
	$(ANSIBLE) -i $(INVENTORY) playbooks/storage-longhorn.yml

longhorn-bench: ## Run fio benchmark on a Longhorn NVMe PVC (deploys pod, prints results, cleans up)
	$(ANSIBLE) -i $(INVENTORY) playbooks/storage-longhorn.yml -e longhorn_run_benchmark=true

observability: ## Install networking + observability (Prometheus, Grafana, Tempo, Loki, Alloy)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags observability

storage: ## Install networking + storage (CSI SMB driver + PV/PVC test)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags storage

ai: ## Install full AI stack (registry + hermes-agent-image + kubernetes-mcp + hermes-agent)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai

ai-all: ai ai-holmes openclaw kagent ## Deploy the ENTIRE AI Stack (Hermes + HolmesGPT + OpenClaw + Kagent)

ai-registry: ## Install only Docker registry (5GB PVC, ARM64 compatible)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-registry

ai-hermes-build: ## Build Hermes Agent ARM64 image with kaniko (takes ~15 min on CM4)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-hermes-build

build-remote-hermes: ## Compilar Hermes remotamente con GitHub Actions
	@if [ -z "$(GITHUB_PAT)" ] && command -v gh >/dev/null 2>&1; then \
		echo "GITHUB_PAT not provided, attempting to use local gh CLI token..."; \
		GITHUB_PAT=$$(gh auth token); \
	fi; \
	if [ -z "$$GITHUB_PAT" ]; then \
		echo "Error: GITHUB_PAT is not set and gh CLI is not authenticated. Run: make build-remote-hermes GITHUB_PAT=ghp_xxx"; \
		exit 1; \
	fi; \
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-hermes-build-remote -e "github_pat=$$GITHUB_PAT"

build-remote-leloir: ## Compilar Leloir remotamente con GitHub Actions
	@if [ -z "$(GITHUB_PAT)" ] && command -v gh >/dev/null 2>&1; then \
		echo "GITHUB_PAT not provided, attempting to use local gh CLI token..."; \
		GITHUB_PAT=$$(gh auth token); \
	fi; \
	if [ -z "$$GITHUB_PAT" ]; then \
		echo "Error: GITHUB_PAT is not set and gh CLI is not authenticated. Run: make build-remote-leloir GITHUB_PAT=ghp_xxx"; \
		exit 1; \
	fi; \
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags leloir-build-remote -e "github_pat=$$GITHUB_PAT"

build-remote-nas: ## Compilar NAS Admin remotamente con GitHub Actions
	@if [ -z "$(GITHUB_PAT)" ] && command -v gh >/dev/null 2>&1; then \
		echo "GITHUB_PAT not provided, attempting to use local gh CLI token..."; \
		GITHUB_PAT=$$(gh auth token); \
	fi; \
	if [ -z "$$GITHUB_PAT" ]; then \
		echo "Error: GITHUB_PAT is not set and gh CLI is not authenticated. Run: make build-remote-nas GITHUB_PAT=ghp_xxx"; \
		exit 1; \
	fi; \
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags nas-admin-build-remote -e "github_pat=$$GITHUB_PAT"

build-remote-openclaw: ## Compilar OpenClaw (con Honcho) remotamente con GitHub Actions
	@if [ -z "$(GITHUB_PAT)" ] && command -v gh >/dev/null 2>&1; then \
		echo "GITHUB_PAT not provided, attempting to use local gh CLI token..."; \
		GITHUB_PAT=$$(gh auth token); \
	fi; \
	if [ -z "$$GITHUB_PAT" ]; then \
		echo "Error: GITHUB_PAT is not set and gh CLI is not authenticated. Run: make build-remote-openclaw GITHUB_PAT=ghp_xxx"; \
		exit 1; \
	fi; \
	gh workflow run build-openclaw.yml --ref main -f image_tag=latest-honcho
	@echo "OpenClaw remote build triggered. Check GitHub Actions UI."

build-remote-all: build-remote-hermes build-remote-leloir build-remote-nas build-remote-openclaw ## Compilar TODAS las imágenes remotamente
	@echo "All remote builds submitted."

# Alias for backwards compatibility
ai-hermes-build-remote: build-remote-hermes

ai-kubernetes-mcp-build: ## Build Kubernetes MCP server ARM64 image with kaniko
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-kubernetes-mcp-build

ai-hermes-deploy: ## Deploy Hermes Agent + LiteLLM proxy
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-hermes-deploy

ai-litellm-proxy-deploy: ## Deploy/Upgrade LiteLLM proxy only (no Hermes Agent restart)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-litellm-proxy

ai-hermes-agent-deploy: ## Deploy/Upgrade Hermes Agent pod only (no LiteLLM restart)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-hermes-agent

ai-holmes: ## Deploy HolmesGPT + Holmes UI (OpenAI-compatible backend via LiteLLM)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-holmes

holmes-ui: ## Deploy Holmes UI only (chat interface at holmes-ui.cluster.home)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-holmes-ui

leloir-build: ## Build leloir-controlplane ARM64 image with Argo Workflows + Kaniko (~5 min — requires ai-registry + argo-workflows)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags leloir-build

leloir: ## Deploy Leloir control plane — Postgres + controlplane + HTTPRoute at leloir.cluster.home
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags leloir

leloir-all: ## Full Leloir deploy: registry → argo → build → deploy (idempotent)
	$(MAKE) ai-registry
	$(MAKE) argo-workflows
	$(MAKE) leloir-build
	$(MAKE) leloir

dex: ## Bootstrap Dex secrets — ArgoCD deploys the chart (requires GitHub OAuth App + secrets.yml)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags dex

leloir-oidc: ## Enable OIDC on Leloir (deploy Dex + reconfigure Leloir — requires secrets.yml)
	$(MAKE) dex
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags leloir -e "leloir_auth_mode=oidc"

kagent: ## Deploy kagent + kmcp AI agent platform (multi-tenant, LiteLLM backend)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags kagent

openclaw: ## Deploy OpenClaw personal AI gateway (Telegram + LiteLLM + modular RBAC)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags openclaw

openclaw-rbac: ## Change OpenClaw RBAC level — LEVEL=readonly|operator|admin|cluster-admin
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags openclaw -e "openclaw_rbac_level=$(LEVEL)"

security: ## Install NeuVector core (controller, enforcer, manager, scanner)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags security

security-monitor: ## Install NeuVector Prometheus exporter (requires password change in UI first)
	$(ANSIBLE) playbooks/security.yml -i $(INVENTORY)

full: ## Full bootstrap - all roles
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY)

clean: ## Full uninstall - destroys cluster
	@echo "⚠️  WARNING: This will destroy the entire cluster!"
	@echo "Press Ctrl+C to cancel or wait 5 seconds..."
	@sleep 5
	$(ANSIBLE) $(UNINSTALL) -i $(INVENTORY)

idempotent: ## Test idempotency - run full bootstrap twice
	@echo "=== First run ==="
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY)
	@echo ""
	@echo "=== Second run (idempotency test) ==="
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY)

survey: ## Full hardware survey — CPU/RAM/storage/GPU/NIC/K8s-readiness + JSON output in survey/
	$(ANSIBLE) playbooks/node-survey.yml -i $(INVENTORY)

healthcheck: ## Run full node health check (identity + stats) via Ansible
	$(ANSIBLE) playbooks/healthcheck.yml -i $(INVENTORY)

node-identity: ## Check hostnames and IPs match inventory (fast script)
	@bash scripts/node-identity-check

node-stats: ## Show CPU, RAM, temperature for all nodes (fast script)
	@bash scripts/node-stats

fix-mac-address: ## Run fix-mac-address role for all nodes (limit individual ones if needed)
	$(ANSIBLE) playbooks/fix-all-nodes.yml -i $(INVENTORY)

status: ## Show cluster status
	@echo "=== Nodes ==="
	@kubectl get nodes -o wide
	@echo ""
	@echo "=== Pods by namespace ==="
	@kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded 2>/dev/null || echo "All pods running"
	@echo ""
	@echo "=== Helm releases ==="
	@helm list -A 2>/dev/null || echo "No helm releases"

gpu-bench: ## Benchmark Tesla P4 vs Quadro M4000 — 3 tests (P4-solo, M4000-solo, parallel) with report
	$(ANSIBLE) playbooks/test-gpu.yml -i $(INVENTORY)

gpu-evict: ## Evict all models from all Ollama instances on gpu_nodes (frees VRAM for dual-GPU tests)
	@ansible gpu_nodes -i $(INVENTORY) -m shell -a '\
	  for PORT in 11434 11435 11436; do \
	    MODELS=$$(curl -s http://localhost:$$PORT/api/ps | python3 -c "import json,sys; [print(m[chr(110)+chr(97)+chr(109)+chr(101)]) for m in json.load(sys.stdin).get(chr(109)+chr(111)+chr(100)+chr(101)+chr(108)+chr(115),[])]" 2>/dev/null); \
	    [ -n "$$MODELS" ] && echo "$$MODELS" | while read M; do \
	      curl -s -X POST http://localhost:$$PORT/api/generate \
	        -H "Content-Type: application/json" \
	        -d "{\"model\":\"$$M\",\"keep_alive\":0,\"prompt\":\"\"}" --max-time 10 -o /dev/null; \
	      echo "evicted $$M from port $$PORT"; \
	    done || echo "port $$PORT: empty"; \
	  done' 2>&1

gpu-status: ## Show GPU utilization and loaded Ollama models on gpu_nodes
	@ansible gpu_nodes -i $(INVENTORY) -m shell -a '\
	  echo "=== GPU VRAM ===" && nvidia-smi --query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.free --format=csv,noheader && \
	  echo "=== Loaded models ===" && \
	  for PORT in 11434 11435 11436; do \
	    echo -n "port $$PORT: "; \
	    curl -s http://localhost:$$PORT/api/ps | python3 -c "import json,sys; ms=json.load(sys.stdin).get(chr(109)+chr(111)+chr(100)+chr(101)+chr(108)+chr(115),[]); print([(m[chr(110)+chr(97)+chr(109)+chr(101)],m[chr(115)+chr(105)+chr(122)+chr(101)+chr(95)+chr(118)+chr(114)+chr(97)+chr(109)]//1024//1024) for m in ms]) if ms else print(chr(40)+chr(101)+chr(109)+chr(112)+chr(116)+chr(121)+chr(41))" 2>/dev/null; \
	  done' 2>&1

nas-admin-build: ## Build NAS admin panel ARM64 image with kaniko (~2 min, standalone — no cluster bootstrap needed)
	$(ANSIBLE) playbooks/build-nas-admin.yml -i $(INVENTORY)

nas-admin-build-logs: ## Tail kaniko build logs for nas-admin (run while nas-admin-build is in progress)
	kubectl logs -n build job/build-nas-admin --follow 2>/dev/null || kubectl logs -n build job/build-nas-admin

nas-admin: ## Deploy/upgrade NAS admin panel Helm chart at nas-admin.cluster.home
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags nas-admin

nas-admin-test: ## Run nas-admin local unit tests (no cluster needed)
	cd apps/nas-admin && python -m pytest test_main.py -v

nas-admin-all: ## Full NAS admin: build image → deploy Helm chart (idempotent)
	$(MAKE) nas-admin-build
	$(MAKE) nas-admin

logs: ## Show logs of failing pods
	@echo "=== Failing pods ==="
	@kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded 2>/dev/null || echo "All pods running"
	@echo ""
	@echo "=== Logs ==="
	@kubectl logs -A --field-selector=status.phase!=Running,status.phase!=Succeeded --tail=10 2>/dev/null || echo "No failing pods"
