# Infra Makefile - Simplified Ansible workflow
# Usage: make <target>

ANSIBLE := ansible-playbook
INVENTORY := inventory/hosts.ini
BOOTSTRAP := playbooks/bootstrap.yml
UNINSTALL := playbooks/uninstall.yml

# Overridable via env
SSH_KEY      ?=
SUDOERS_MODE ?= full
ANSIBLE_USER ?=

.PHONY: help deps deps-ai deps-ops deps-full preview preview-ai preview-ops preview-full uninstall-local hermes-install holmesgpt-install setup-nodes setup-sudoers core networking ingress dns-metrics services observability storage ai ai-registry ai-hermes-build ai-hermes-deploy ai-holmes holmes-ui ai-kubernetes-mcp-build kagent security full clean healthcheck node-identity node-stats survey litellm openclaw openclaw-rbac fix-mac-address

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

networking: ## Install core + networking (Cilium, LB-IPAM, Gateway API)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags networking

ingress: ## Install networking + ingress (cert-manager, Gateway)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ingress

dns-metrics: ## Install DNS and Metrics (Pi-hole)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags dns-metrics

services: ## Install ingress + services (ArgoCD, helm-dashboard, registry)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags core,networking,ingress,services

observability: ## Install networking + observability (Prometheus, Grafana, Tempo, Loki, Alloy)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags observability

storage: ## Install networking + storage (CSI SMB driver + PV/PVC test)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags storage

ai: ## Install full AI stack (registry + hermes-agent-image + kubernetes-mcp + hermes-agent)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai

ai-registry: ## Install only Docker registry (5GB PVC, ARM64 compatible)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-registry

ai-hermes-build: ## Build Hermes Agent ARM64 image with kaniko (takes ~15 min on CM4)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-hermes-build

ai-kubernetes-mcp-build: ## Build Kubernetes MCP server ARM64 image with kaniko
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-kubernetes-mcp-build

ai-hermes-deploy: ## Deploy Hermes Agent (requires ai-hermes-build to complete first)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-hermes-deploy

ai-holmes: ## Deploy HolmesGPT + Holmes UI (OpenAI-compatible backend via LiteLLM)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-holmes

holmes-ui: ## Deploy Holmes UI only (chat interface at holmes-ui.cluster.home)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-holmes-ui

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

logs: ## Show logs of failing pods
	@echo "=== Failing pods ==="
	@kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded 2>/dev/null || echo "All pods running"
	@echo ""
	@echo "=== Logs ==="
	@kubectl logs -A --field-selector=status.phase!=Running,status.phase!=Succeeded --tail=10 2>/dev/null || echo "No failing pods"
