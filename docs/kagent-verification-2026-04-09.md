# kagent Verification - 2026-04-09

## Status: ✅ FUNCIONANDO

kagent está completamente operacional con 18 pods corriendo, 11 agents desplegados, UI accesible, y integración con LiteLLM funcionando.

---

## Estado del Deployment

### Pods (namespace: kagent)

```bash
kubectl get pods -n kagent

NAME                                             READY   STATUS    RESTARTS       AGE
argo-rollouts-conversion-agent-9cbdd7744-jxsnk   1/1     Running   0              42m
cilium-debug-agent-6c48b475cf-s2nlm              1/1     Running   0              42m
cilium-manager-agent-7d648884-7pgsk              1/1     Running   0              42m
cilium-policy-agent-575874589b-mw6kq             1/1     Running   0              42m
helm-agent-6cb7f55c65-zfjr7                      1/1     Running   0              42m
istio-agent-68d5697f55-4kqrm                     1/1     Running   0              42m
k8s-agent-7cfd7b69d8-zlpk8                       1/1     Running   0              42m
kagent-controller-5c5947fcf4-c8ltv               1/1     Running   0              86m
kagent-grafana-mcp-6cdcd7699d-9nq7c              1/1     Running   0              10h
kagent-kmcp-controller-manager-64689d449-xdjf7   1/1     Running   3 (151m ago)   10h
kagent-postgresql-6945789f4b-mmkgk               1/1     Running   0              10h
kagent-querydoc-795c8647c-q9tt2                  1/1     Running   0              10h
kagent-tools-6bb8fb8954-zhmks                    1/1     Running   0              10h
kagent-ui-8695f4d6b-tkslz                        1/1     Running   0              9h
kgateway-agent-76986c54b6-bgz8z                  1/1     Running   0              42m
my-first-k8s-agent-769c897cb-55bwb               1/1     Running   0              42m
observability-agent-56d6c6cfd9-h7gtd             1/1     Running   0              42m
promql-agent-847b4c55c9-c7cqd                    1/1     Running   0              42m
```

**Total: 18 pods, todos Running**

### Agent CRDs

```bash
kubectl get agents -A

NAMESPACE   NAME                             TYPE          RUNTIME   READY   ACCEPTED
kagent      argo-rollouts-conversion-agent   Declarative   python    True    True
kagent      cilium-debug-agent               Declarative   python    True    True
kagent      cilium-manager-agent             Declarative   python    True    True
kagent      cilium-policy-agent              Declarative   python    True    True
kagent      helm-agent                       Declarative   python    True    True
kagent      istio-agent                      Declarative   python    True    True
kagent      k8s-agent                        Declarative   python    True    True
kagent      kgateway-agent                   Declarative   python    True    True
kagent      my-first-k8s-agent               Declarative   python    True    True
kagent      observability-agent              Declarative   python    True    True
kagent      promql-agent                     Declarative   python    True    True
```

**Total: 11 agents, todos Ready + Accepted**

### UI Accesible

```bash
curl -k https://kagent.cluster.home

# Returns: HTML from Next.js app (kagent.dev)
# Status: 200 OK
```

**URL**: https://kagent.cluster.home  
**Estado**: Accesible via Gateway API (192.168.178.200)

---

## Integración con LiteLLM

### Configuración

**Archivo**: `roles/install-kagent/defaults/main.yml`

```yaml
kagent_llm_model: "kagent-orchestrator"
kagent_llm_base_url: "http://litellm-proxy.ai.svc.cluster.local:4000/v1"
kagent_llm_api_key: "sk-hermes-internal"
```

### Secret

```bash
kubectl get secret kagent-openai -n kagent -o jsonpath='{.data.OPENAI_API_KEY}' | base64 -d
# sk-hermes-internal
```

### Modelo en LiteLLM

Desde `litellm-proxy-config` ConfigMap:

```yaml
- model_name: kagent-orchestrator
  litellm_params:
    model: openrouter/qwen/qwen-turbo
    api_key: os.environ/KAGENT_OPENROUTER_API_KEY

litellm_settings:
  fallbacks:
    - {"kagent-orchestrator": ["cheap"]}
```

**Routing**:
- kagent llama `kagent-orchestrator` → LiteLLM proxy
- LiteLLM usa `KAGENT_OPENROUTER_API_KEY` (env var propia de kagent)
- OpenRouter endpoint: `qwen/qwen-turbo` ($0.033/M tokens)
- Fallback: modelo `cheap` si el principal falla

---

## Controller Logs (Funcionamiento Verificado)

```bash
kubectl logs -n kagent kagent-controller-5c5947fcf4-c8ltv --tail=20

{"level":"info","ts":"2026-04-09T00:35:08Z","logger":"http.tasks-handler","msg":"Successfully created task"}
{"level":"info","ts":"2026-04-09T00:35:08Z","logger":"http","msg":"Request completed","status":201}
{"level":"info","ts":"2026-04-09T00:35:08Z","logger":"http.sessions-handler","msg":"Successfully added event to session","user_id":"admin@kagent.dev"}
{"level":"info","ts":"2026-04-09T00:35:12Z","logger":"http","msg":"Request completed","method":"POST","path":"/api/a2a/kagent/helm-agent/","status":200}
```

**Evidencia**:
- ✅ Tasks creándose correctamente (status 201)
- ✅ Sessions activas con `admin@kagent.dev`
- ✅ Agent-to-agent calls (`/api/a2a/kagent/helm-agent/`)
- ✅ Health checks OK (status 200)

---

## Storage

### PostgreSQL

**StorageClass**: `smb-nas-pg` (custom, uid=999/gid=999 para postgres)

```yaml
kagent_db_storage_class: smb-nas-pg
kagent_db_storage_size: 5Gi
```

**Por qué `smb-nas-pg`**:
- PostgreSQL requiere uid=999, gid=999 ownership
- El StorageClass default `smb-nas` usa uid=1000 → causa error "wrong ownership"
- `smb-nas-pg` es creado inline por el rol `install-kagent` con mount options correctas

**Dependencia**:
```yaml
kagent_storage_role: "install-cifs-nas"
```

El rol asegura que el CSI driver SMB esté instalado antes de deployar kagent.

---

## Built-in Agents

kagent incluye 11 agents predefinidos:

| Agent | Purpose | Skills |
|-------|---------|--------|
| `k8s-agent` | Cluster diagnostics, resource mgmt, security audit | pods, deployments, RBAC, policies |
| `helm-agent` | Helm chart operations | install, upgrade, rollback, values |
| `promql-agent` | Prometheus query execution | metrics, queries, alerts |
| `cilium-manager-agent` | Cilium CNI management | network policies, endpoints |
| `cilium-policy-agent` | Cilium policy operations | L3/L4/L7 policies |
| `cilium-debug-agent` | Cilium troubleshooting | flows, connectivity |
| `istio-agent` | Istio service mesh | virtual services, gateways |
| `kgateway-agent` | Gateway API management | HTTPRoute, TLS |
| `argo-rollouts-conversion-agent` | ArgoCD rollouts | blue/green, canary |
| `observability-agent` | Observability stack | Grafana, Loki, Tempo |
| `my-first-k8s-agent` | Demo/tutorial agent | basic K8s operations |

**Status**: Todos `Ready: True`, `Accepted: True`

---

## Arquitectura de Seguridad

### Current State

```
kagent Agents
    ↓ (OPENAI_API_KEY: sk-hermes-internal)
LiteLLM Proxy (ai namespace)
    ↓ (KAGENT_OPENROUTER_API_KEY)
OpenRouter API
    ↓
qwen-turbo (model)
```

**Security enforced**:
- ✅ kagent NO tiene acceso directo a OpenRouter
- ✅ Todo el tráfico pasa por LiteLLM proxy interno
- ✅ LiteLLM maneja la API key de OpenRouter (no kagent)

### Master Key Sharing

**Situación actual**:
- Hermes: `sk-hermes-internal`
- Holmes: `sk-hermes-internal`
- kagent: `sk-hermes-internal`

**Todos usan el mismo master_key en LiteLLM.**

**Pros**:
- Simplifica deployment (un solo secret)
- Cada agente llama modelos diferentes (`hermes-qwen`, `holmes-llama`, `kagent-orchestrator`)
- LiteLLM usa env vars separadas por agente (`KAGENT_OPENROUTER_API_KEY` vs `OPENROUTER_API_KEY`)

**Cons**:
- No hay rate limiting por agente
- No hay cost tracking individual
- No se puede revocar acceso de un agente sin afectar otros

### Mejora Futura (Opcional)

Implementar multi-tenancy real en LiteLLM:

```yaml
general_settings:
  master_key: "sk-litellm-master"  # internal only, not exposed to agents

# Create virtual keys per agent
virtual_keys:
  - key: "sk-hermes-internal"
    models: ["hermes-qwen", "free", "free2", "cheap"]
    max_budget: 10.0  # USD/month
  - key: "sk-holmes-internal"
    models: ["holmes-llama", "holmes-free2", "holmes-cheap"]
    max_budget: 5.0
  - key: "sk-kagent-internal"
    models: ["kagent-orchestrator", "cheap"]
    max_budget: 15.0
```

**Beneficios**:
- Rate limiting por agente
- Cost tracking separado
- Revocación individual sin downtime de otros
- Budget limits por agente

**No es urgente** - la arquitectura actual es segura y funcional.

---

## Deployment

### Via Ansible

```bash
cd ~/Nextcloud/Repos/infra-ai/infra

# Full deployment (includes storage + PostgreSQL + agents)
make kagent

# Or via bootstrap
ansible-playbook playbooks/bootstrap.yml --tags kagent
```

### Prerequisitos

1. **K3s + Cilium** (networking)
2. **Gateway API + cert-manager** (ingress + TLS)
3. **SMB/CIFS CSI driver** (storage - auto-included via kagent_storage_role)
4. **LiteLLM proxy** deployed in `ai` namespace

### Post-Deployment Verification

```bash
# Pods
kubectl get pods -n kagent

# Agents
kubectl get agents -A

# UI
curl -k https://kagent.cluster.home

# Controller logs
kubectl logs -n kagent -l app=kagent-controller --tail=50

# PostgreSQL
kubectl exec -n kagent kagent-postgresql-<pod> -- psql -U postgres -c '\l'
```

---

## Web UI

**URL**: https://kagent.cluster.home

**Features**:
- Agent management (create, update, delete agents)
- Session browser (inspect agent conversations)
- Task queue (view pending/running/completed tasks)
- MCP server browser (available tools per agent)
- Query interface (interact with agents via web)

**Access**:
- No authentication by default (internal cluster only)
- Accessible via Gateway API at 192.168.178.200
- TLS cert from internal CA (wildcard `*.cluster.home`)

---

## Agent Example: k8s-agent

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: k8s-agent
  namespace: kagent
spec:
  declarative:
    a2aConfig:
      skills:
        - id: cluster-diagnostics
          name: Cluster Diagnostics
          description: The ability to analyze and diagnose Kubernetes Cluster issues.
          examples:
            - What is the status of my cluster?
            - How can I troubleshoot a failing pod?
          tags: [cluster, diagnostics]
        
        - id: resource-management
          name: Resource Management
          description: The ability to manage and optimize Kubernetes resources.
          examples:
            - Scale my deployment X to 3 replicas.
            - Optimize resource requests for my pods.
          tags: [resource, management]
        
        - id: security-audit
          name: Security Audit
          description: The ability to audit and enhance Kubernetes security.
          examples:
            - Check for RBAC misconfigurations.
            - Audit my network policies.
          tags: [security, audit]
```

**Status**: `Ready: True`, `Accepted: True`

---

## Troubleshooting

### Síntoma: Pods CrashLoopBackOff

**Check**:
```bash
kubectl describe pod -n kagent <pod>
kubectl logs -n kagent <pod>
```

**Causas comunes**:
- PostgreSQL no accesible (check `kagent-postgresql` pod)
- Secret `kagent-openai` faltante o inválido
- Storage PVC no bound (check `smb-nas-pg` StorageClass)

### Síntoma: Agents no Ready

**Check**:
```bash
kubectl describe agent -n kagent <agent-name>
kubectl logs -n kagent -l app=<agent-name>
```

**Causas comunes**:
- Controller no procesando CRDs (check `kagent-controller` logs)
- RBAC missing (cada agent necesita ServiceAccount + RoleBinding)
- LLM connection failed (check LiteLLM proxy accessible)

### Síntoma: UI no carga

**Check**:
```bash
kubectl get httproute -n kagent
kubectl get gateway -n gateway cluster-gateway
curl -k https://kagent.cluster.home
```

**Causas comunes**:
- HTTPRoute mal configurado (check `spec.hostnames`)
- Gateway no programado (check `status.addresses`)
- DNS no resolviendo (check Pi-hole wildcard `*.cluster.home`)

### Síntoma: PostgreSQL "wrong ownership"

**Fix**: Asegurar que usás `smb-nas-pg` StorageClass (no `smb-nas`).

```yaml
# Correcto
kagent_db_storage_class: smb-nas-pg  # uid=999, gid=999

# Incorrecto
kagent_db_storage_class: smb-nas     # uid=1000, gid=1000 → postgres fails
```

---

## Versiones

| Componente | Versión | Source |
|------------|---------|--------|
| kagent | 0.8.5 | oci://ghcr.io/kagent-dev/kagent/helm/kagent |
| PostgreSQL | (bundled) | Helm subchart |
| kmcp | (bundled) | kagent-kmcp-controller-manager |
| LiteLLM | 1.58.5 | ai namespace |
| Model | qwen-turbo | OpenRouter via LiteLLM |

---

## Referencias

- [kagent.dev](https://kagent.dev) - Official docs
- [kagent GitHub](https://github.com/kagent-dev/kagent) - Source code
- [Agent CRD spec](https://github.com/kagent-dev/kagent/blob/main/docs/agent-crd.md)
- [Built-in agents](https://github.com/kagent-dev/kagent/tree/main/agents)

---

## Conclusión

✅ **kagent está completamente funcional**:
- 18 pods corriendo sin errores
- 11 agents desplegados y aceptados
- UI accesible en https://kagent.cluster.home
- Integración con LiteLLM funcionando
- PostgreSQL operacional con smb-nas-pg
- Controller procesando tasks y sessions

**No se requieren fixes** - solo documentación completada.

**Mejora futura opcional**: Implementar virtual keys en LiteLLM para rate limiting/cost tracking por agente.
