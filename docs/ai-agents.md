# AI Agents en el Cluster

Documentación de los agentes de IA que operan sobre este cluster: qué hacen,
cómo se integran con el stack, y el rol de cada uno.

---

## Mapa de agentes

```
┌──────────────────────────────────────────────────────────────────┐
│                        TU LAPTOP                                 │
│                                                                  │
│  ┌─────────────────────────┐   ┌──────────────────────────────┐  │
│  │       OpenCode          │   │         HolmesGPT            │  │
│  │  AI coding agent        │   │  SRE / troubleshooting agent │  │
│  │                         │   │                              │  │
│  │  - Edita Ansible roles  │   │  - Investiga incidentes K8s  │  │
│  │  - Corre playbooks      │   │  - Analiza pods/logs/eventos │  │
│  │  - Itera infra as code  │   │  - Da root cause en lenguaje │  │
│  │  - Contexto via skills  │   │    natural                   │  │
│  └────────────┬────────────┘   └──────────────┬───────────────┘  │
│               │ Ansible SSH / kubectl          │ kubectl          │
└───────────────┼────────────────────────────────┼─────────────────┘
                │                                │
                ▼                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                  K3s cluster (srv-rk1-01)                        │
│                                                                  │
│   Cilium · cert-manager · Gateway · Pi-hole · ArgoCD            │
└──────────────────────────────────────────────────────────────────┘
```

---

## OpenCode — Agente de infraestructura

**Rol:** operar el cluster desde el código. No accede al cluster directamente
en producción — trabaja sobre los source files de Ansible en `~/projects/infra`
y ejecuta los playbooks para aplicar cambios.

### Cómo opera

```
OpenCode
  │
  ├── Lee/edita roles Ansible  (~/projects/infra/roles/)
  ├── Corre playbooks          (ansible-playbook playbooks/bootstrap.yml)
  ├── Verifica con kubectl     (kubectl get pods, curl, dig, arp)
  └── Commitea solo cuando     (playbook passed + failed=0)
      el deploy es exitoso
```

### Regla de oro

**Nunca editar archivos deployados directamente** (`~/.kube`, `/etc/rancher/`, etc.).
Siempre editar el source en `~/projects/infra/roles/<role>/` y correr Ansible.
Si algo requiere un paso manual después del playbook, es un bug.

### Skills como memoria de largo plazo

OpenCode usa skills (archivos Markdown en `~/.config/opencode/skills/`) como
contexto persistente entre sesiones. Los skills documentan:
- Decisiones de arquitectura y por qué se tomaron
- Gotchas conocidos (ej: Pi-hole 6 `local-service` crash, MetalLB + Cilium incompatibilidad)
- Comandos de verificación y troubleshooting probados
- Estado actual del cluster

Los skills del cluster:

| Skill | Contenido |
|-------|-----------|
| `cilium` | CNI, LB-IPAM, L2 Announcements, Gateway API, BPF |
| `gateway` | Shared Gateway, HTTPRoutes, flujos de tráfico reales |
| `cert-manager` | CA interna, wildcard cert, Keychain trust |
| `k3s` | Flags del servidor, kubeconfig, upgrades |
| `pihole` | DNS wildcard, Pi-hole 6 gotchas, integración |
| `argocd` | GitOps, ApplicationSets, sync waves |
| `monitoring` | Prometheus, Grafana, Tempo, Alloy — stack completo de observabilidad |
| `metallb` | OBSOLETO — reemplazado por Cilium LB-IPAM |
| `k8s-debug` | Debug sistemático de pods, red, nodos |
| `platform-engineering` | Helm, Terraform, CI/CD best practices |

**Source de los skills:** `~/dotfiles/ansible/roles/opencode/files/skills/`
Deployados via Ansible al editar, nunca a mano.

---

## HolmesGPT — Agente SRE

**Proyecto:** CNCF Sandbox — [github.com/HolmesGPT/holmesgpt](https://github.com/HolmesGPT/holmesgpt)

**Rol:** investigar incidentes y encontrar root causes en el cluster de forma
autónoma. Mientras OpenCode opera sobre el código, HolmesGPT opera sobre el
estado en vivo del cluster.

### Qué hace

HolmesGPT corre un **agentic loop**: recibe una pregunta o alerta, llama a
herramientas (kubectl, logs, Prometheus, etc.) iterativamente hasta tener
suficiente contexto, y produce un análisis en lenguaje natural con el root cause.

```
Pregunta / alerta
      │
      ▼
  HolmesGPT
      │
      ├── kubectl describe pod / get events
      ├── kubectl logs (últimas N líneas)
      ├── Prometheus metrics (si está configurado)
      ├── Helm history / values
      └── ... itera hasta tener root cause
      │
      ▼
  Respuesta: "El pod crashea porque X, el fix es Y"
```

### Casos de uso en este cluster

```bash
# ¿Por qué crashea este pod?
holmes ask "why is pod X in namespace Y crashlooping?"

# ¿Qué está mal con este deployment?
holmes ask "why is deployment argocd-server not ready?"

# Investigar una alerta de Prometheus
holmes investigate alertmanager

# Modo interactivo — preguntas y seguimiento
holmes chat
```

### Integración con el stack actual

| Componente | ¿HolmesGPT puede usarlo? | Notas |
|---|---|---|
| K8s API (pods, events, logs) | ✅ nativo | toolset `kubernetes` built-in |
| Helm | ✅ nativo | toolset `helm` built-in |
| ArgoCD | ✅ built-in | toolset `argocd` — estado de apps, sync history |
| Cilium | ✅ built-in | toolset listado en docs oficiales |
| Prometheus / AlertManager | ✅ built-in | si se despliega Prometheus stack |
| Hubble (Cilium) | via kubectl | no toolset nativo, pero puede correr `hubble observe` |
| Grafana Loki | ✅ built-in | si se despliega Loki |

### Modos de deploy

**CLI (más simple para homelab):**
```bash
pip install holmesgpt
export OPENAI_API_KEY=sk-...   # o cualquier otro provider
holmes ask "why is my pod failing?"
```

**In-cluster (Operator mode — 24/7):**
```yaml
# Helm chart — puede ir como ArgoCD Application
helm install holmesgpt holmesgpt/holmesgpt \
  --set llm.provider=openai \
  --set llm.apiKey=sk-...
```

El Operator mode corre health checks scheduled y manda alertas a Slack con
el análisis ya hecho — sin que tengas que notar el problema primero.

### LLM providers soportados

OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, Google Gemini, Ollama (local),
y cualquier endpoint compatible con OpenAI API.

Para homelab: Ollama con un modelo local evita costos y mantiene los datos privados.

```yaml
# config.yaml con Ollama local
llm:
  provider: ollama
  model: llama3.1
  base_url: http://ollama.cluster.home  # si Ollama está en el cluster
```

### Privacidad / seguridad

HolmesGPT tiene **acceso de solo lectura** al cluster por diseño y respeta los
permisos RBAC configurados. No aplica cambios — solo lee y analiza.

---

## Diferencia clave entre los dos agentes

```
┌─────────────────┬────────────────────────────┬──────────────────────────────┐
│                 │        OpenCode             │        HolmesGPT             │
├─────────────────┼────────────────────────────┼──────────────────────────────┤
│ Qué opera       │ Código fuente (Ansible)     │ Estado en vivo del cluster   │
│ Cuándo se usa   │ Para cambiar la infra       │ Para entender qué está roto  │
│ Output          │ Playbooks, commits, PRs     │ Análisis de root cause       │
│ Acceso cluster  │ Indirecto (via Ansible)     │ Directo (kubectl read-only)  │
│ Persistencia    │ Git + skills Markdown       │ Stateless (cada query fresh) │
│ Modo operación  │ Interactivo (vos lo pedís)  │ Interactivo o 24/7 Operator  │
└─────────────────┴────────────────────────────┴──────────────────────────────┘
```

**Flujo típico combinado:**

```
1. HolmesGPT detecta / diagnostica el problema
        "El pod argocd-server crashea por OOMKilled, límite de memoria muy bajo"

2. OpenCode aplica el fix en Ansible
        Edita roles/install-argocd/defaults/main.yml → aumenta resources.limits.memory
        Corre playbook → verifica → commitea
```

---

## Próximos pasos / ideas

- [x] Desplegar kube-prometheus-stack (Prometheus + Grafana + AlertManager) — ✅ hecho
- [x] Desplegar Grafana Tempo (tracing backend) — ✅ hecho
- [x] Desplegar Grafana Alloy (OTLP pipeline) — ✅ hecho
- [ ] Desplegar HolmesGPT como ArgoCD Application (in-cluster, Operator mode)
- [ ] Conectar HolmesGPT con Prometheus + Alertmanager — prerequisito cumplido ✅
- [ ] Probar Ollama in-cluster como LLM backend local (sin costos de API, datos privados)
- [ ] Conectar HolmesGPT Slack integration para alertas con análisis automático
- [ ] Explorar toolset de Cilium en HolmesGPT para diagnóstico de red
- [ ] ArgoCD app-of-apps / GitOps handover (`handover.yml`)

---

## Referencias

- HolmesGPT docs: https://holmesgpt.dev/
- HolmesGPT GitHub: https://github.com/HolmesGPT/holmesgpt
- HolmesGPT Helm chart: https://holmesgpt.dev/installation/in-cluster-installation/
- HolmesGPT Operator mode: https://holmesgpt.dev/operator/
- OpenCode docs: https://opencode.ai/docs
