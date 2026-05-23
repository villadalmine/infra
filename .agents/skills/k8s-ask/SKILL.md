# k8s-ask — CLI de lenguaje natural para el cluster

CLI liviano para hacer preguntas sobre el cluster K3s en lenguaje natural
desde la terminal, sin abrir la TUI de OpenCode.

Instalado en `~/.local/bin/k8s-ask` vía Ansible (rol `opencode`).

---

## Uso

```bash
# Pregunta simple — usa modelo 'cheap' por defecto
k8s-ask "qué pods crashean en monitoring?"

# Cambiar modelo con -m
k8s-ask -m free "por qué crashea argocd-server?"
k8s-ask -m claude-sonnet-4-6 "analiza el estado de los nodos"
k8s-ask -m strong "revisa este chart de helm"

# Cambiar modelo con variable de entorno
K8S_ASK_MODEL=free k8s-ask "get nodes"
```

## Salida esperada

```
[cheap]                                                ← stderr (dim/gris)
  → kubectl_get_pods({'namespace': 'monitoring'})      ← stderr (dim/gris)
  → kubectl_get_events({'namespace': 'monitoring'})    ← stderr (dim/gris)
El pod alertmanager-0 está en CrashLoopBackOff...      ← stdout
```

Los tool calls van a **stderr** (gris/dim). La respuesta final va a **stdout**.
Esto permite hacer `k8s-ask "..." > output.txt` para capturar solo la respuesta.

---

## Arquitectura

```
k8s-ask "pregunta"
    │
    ▼
LiteLLM localhost:4000  (OpenAI-compatible)
    │
    ├── tool_calls? → kubectl subprocess → resultado → feed back al modelo
    ├── tool_calls? → kubectl subprocess → ...
    │   (loop hasta 8 iteraciones)
    │
    └── respuesta final → stdout
```

- **stdlib only** — funciona con el Python del sistema, sin deps extra
- **KUBECONFIG**: `~/.kube/config` (igual que el resto del setup)
- **Max iteraciones**: 8

---

## Modelos disponibles

| Alias | Modelo | Costo | Notas |
|-------|--------|-------|-------|
| `cheap` | qwen-turbo | $0.033/M | **Default** — rápido, tool use ✅ |
| `free` | qwen3-coder:free | $0 | Venice provider, puede tener 429 |
| `free2` | gemini-2.0-flash-exp:free | $0 | Google free, buen fallback para codeo |
| `strong` | deepseek-chat-v3-0324 | barato | mejor equilibrio para Ansible/Bash/TS/K8s |
| `claude-sonnet-4-6` | Claude Sonnet 4.6 | ~$3/M | Máxima calidad para análisis complejos |
| `claude-haiku-4-5` | Claude Haiku 4.5 | ~$0.8/M | Rápido y barato |

---

## Tools kubectl disponibles

| Tool | kubectl equivalente |
|------|---------------------|
| `kubectl_get_pods` | `kubectl get pods -n <ns> -o wide` |
| `kubectl_get_events` | `kubectl get events -n <ns> --sort-by=.lastTimestamp` |
| `kubectl_describe_pod` | `kubectl describe pod <name> -n <ns>` |
| `kubectl_logs` | `kubectl logs <name> -n <ns> --tail=50` |
| `kubectl_get_nodes` | `kubectl get nodes -o wide` |
| `kubectl_get_resource` | `kubectl get <resource> [-n <ns>]` (genérico) |

---

## Casos de uso

```bash
# Estado general
k8s-ask "qué pods no están running?"
k8s-ask "muéstrame los nodos y su estado"

# Troubleshooting
k8s-ask "por qué crashea el pod X en namespace Y?"
k8s-ask "qué eventos hay en argocd?"
k8s-ask -m claude-sonnet-4-6 "analiza el estado completo de monitoring"

# Recursos
k8s-ask "qué deployments hay en el namespace default?"
k8s-ask "muéstrame los PVCs en monitoring"
```

---

## Diferencia con OpenCode + kubernetes-mcp

| | k8s-ask | OpenCode |
|---|---|---|
| Interfaz | CLI, una pregunta | TUI interactiva, sesión |
| Contexto | Sin historia entre queries | Historia de conversación |
| Velocidad de inicio | Inmediato | ~2s (carga TUI) |
| Ideal para | Queries rápidas, scripts | Análisis profundo, multi-step |
| Output | stdout captureable | TUI |

### Nota sobre métricas

- `kubectl top pods` / `kubectl top nodes` requieren `metrics-server` y RBAC a `metrics.k8s.io`.
- `kube-prometheus-stack` por sí solo no habilita `pods_top` / `nodes_top` en Hermes.
- Para memoria/CPU desde Hermes, necesitás metrics-server o un puente propio contra Prometheus.

---

## Source

`~/dotfiles/ansible/roles/opencode/files/k8s-ask`
Deployado por Ansible a `~/.local/bin/k8s-ask` (mode: 0755).

```bash
# Redesplegar
ansible-playbook ansible/playbook.yml --tags opencode
```
