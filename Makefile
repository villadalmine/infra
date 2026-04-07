# Infra Makefile - Simplified Ansible workflow
# Usage: make <target>

ANSIBLE := ansible-playbook
INVENTORY := inventory/hosts.ini
BOOTSTRAP := playbooks/bootstrap.yml
UNINSTALL := playbooks/uninstall.yml

.PHONY: help core networking ingress dns-metrics services observability storage ai ai-registry ai-hermes-build ai-hermes-deploy ai-holmes ai-kubernetes-mcp-build kagent security full clean healthcheck node-identity node-stats survey

help: ## Show this help message
	@echo "Infra Makefile - Simplified Ansible workflow"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

core: ## Install K3s + kubeconfig only
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags core

networking: ## Install core + networking (Cilium, LB-IPAM, Gateway API)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags networking

ingress: ## Install networking + ingress (cert-manager, Gateway)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ingress

dns-metrics: ## Install DNS and Metrics (Pi-hole)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags dns-metrics

services: ## Install ingress + services (ArgoCD, helm-dashboard, registry)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags core,networking,ingress,services

observability: ## Install networking + observability (Prometheus, Grafana, Tempo, Loki, Alloy, version-checker)
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

ai-holmes: ## Deploy HolmesGPT (OpenAI-compatible backend via LiteLLM)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags ai-holmes

kagent: ## Deploy kagent + kmcp AI agent platform (multi-tenant, LiteLLM backend)
	$(ANSIBLE) $(BOOTSTRAP) -i $(INVENTORY) --tags kagent

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

survey: ## Full hardware survey — CPU/RAM/storage/GPU/NIC/K8s-readiness + JSON output in survey-output/
	$(ANSIBLE) playbooks/node-survey.yml -i $(INVENTORY)

healthcheck: ## Run full node health check (identity + stats) via Ansible
	$(ANSIBLE) playbooks/healthcheck.yml -i $(INVENTORY)

node-identity: ## Check hostnames and IPs match inventory (fast script)
	@bash scripts/node-identity-check

node-stats: ## Show CPU, RAM, temperature for all nodes (fast script)
	@bash scripts/node-stats

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
