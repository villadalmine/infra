# Session State - 2026-04-09

## Lo que hicimos hoy

### ✅ COMPLETADO: Hermes Agent - Fix + Documentación

**Problema**: Error 401 "Missing Authentication header" + arquitectura de seguridad violada

**Solución**: 
- ConfigMap con config.yaml completo (provider custom + MCP servers)
- InitContainer para crear .env con OPENAI_API_KEY
- Mounts correctos (/opt/data/config.yaml)
- Arquitectura de seguridad restaurada: Hermes → LiteLLM → OpenRouter

**Verificado funcionando**:
- ✅ Telegram conectado (polling mode)
- ✅ LiteLLM proxy autenticando correctamente
- ✅ MCP server con 23 Kubernetes tools registrados
- ✅ Tool calling funciona (probado: mcp_kubernetes_pods_list_in_namespace)
- ✅ Respuesta correcta (listó 6 pods en namespace ai)

**Commits pusheados**:
1. `988dcf1` - fix(hermes): route all LLM traffic through LiteLLM proxy with MCP support
2. `3854e95` - docs(hermes): add complete architecture guide and troubleshooting case study
3. `c0126b6` - docs: add Hermes documentation to README

**Documentación creada**:
- `docs/hermes-architecture.md` - 998 líneas, guía completa de arquitectura
- `docs/hermes-troubleshooting-2026-04-08.md` - Case study del troubleshooting
- README actualizado con links a docs

**Estado del pod**:
```
kubectl get pods -n ai -l app=hermes-agent-mcp
# hermes-agent-mcp-c799f445-vl4zx   2/2 Running

kubectl exec -n ai hermes-agent-mcp-c799f445-vl4zx -c hermes-agent -- cat /opt/data/logs/gateway.log | tail -5
# INFO tools.mcp_tool: MCP server 'kubernetes' (HTTP): registered 23 tool(s)
# INFO gateway.run: response ready: time=191.4s api_calls=2 response=313 chars
```

---

## 🔜 PRÓXIMO: kagent

### Estado actual desconocido

**Tareas pendientes**:
1. Verificar si kagent está deployed
2. Verificar si los pods están corriendo
3. Probar la funcionalidad (web UI + agent CRDs)
4. Verificar integración con LiteLLM
5. Documentar arquitectura si funciona
6. Fix si está roto

**Comandos para empezar mañana**:

```bash
# Ver estado de kagent
kubectl get pods -n kagent
kubectl get agents -A 2>/dev/null || echo "CRDs no instalados"

# Ver logs
kubectl logs -n kagent -l app=kagent --tail=50

# Ver configuración
kubectl get configmap -n kagent
kubectl get secret -n kagent

# Verificar integración con LiteLLM
kubectl exec -n kagent <pod> -- env | grep -i litellm

# Probar UI
curl -v https://kagent.cluster.home
```

**Archivos relevantes**:
- `roles/install-kagent/` - rol de Ansible
- `roles/install-kagent/defaults/main.yml` - configuración
- `roles/install-kagent/defaults/secrets.yml` - secretos (gitignored)
- `skills/kagent/SKILL.md` - documentación de skill (si existe)

**Posibles problemas a chequear**:
- ¿Usa LiteLLM proxy o acceso directo?
- ¿Los agents CRDs tienen tolerations?
- ¿PostgreSQL está usando smb-nas-pg StorageClass?
- ¿Los built-in agents están deployados?
- ¿La UI es accesible via HTTPRoute?

---

## Estado del Cluster

### Namespace ai

```
hermes-agent-mcp-c799f445-vl4zx     2/2 Running   # ✅ FUNCIONANDO
holmes-ui-587f6bdc57-msq7f          1/1 Running
holmesgpt-holmes-6747bc46bd-l9stp   1/1 Running
litellm-proxy-c66774c9d-hkb8z       2/2 Running   # ✅ Multi-tenant funcionando
litellm-test                        1/1 Running
wget-test                           0/1 Completed
```

### Arquitectura de Seguridad (ENFORCED)

```
Hermes  → sk-hermes-internal  → LiteLLM → OpenRouter
Holmes  → sk-holmes-internal  → LiteLLM → OpenRouter
kagent  → sk-kagent-internal  → LiteLLM → OpenRouter  (por verificar)
```

**Ningún agente tiene acceso directo a OpenRouter** - todos pasan por LiteLLM.

---

## Archivos modificados no commiteados

```bash
cd /var/home/dalmine/Nextcloud/Repos/infra-ai/infra
git status --short
```

Si hay archivos modificados, revisarlos mañana antes de trabajar en kagent.

---

## Lecciones de Hoy

1. **NUNCA hacer patches manuales** - siempre Ansible
2. **Arquitectura primero** - no shortcuts que violan principios
3. **Hermes 0.7.0 necesita config.yaml** - no solo env vars
4. **InitContainer pattern** para readOnlyRootFilesystem
5. **Logs internos** (/opt/data/logs/) son más útiles que kubectl logs
6. **Documentar mientras trabajás** - no después
7. **Verificar antes de commitear** - deploy → test → commit
8. **Multi-tenancy en LiteLLM** es la clave de la arquitectura

---

## Para mañana

1. Leer este archivo
2. Verificar `git status` por cambios uncommitted
3. Empezar con kagent: `kubectl get pods -n kagent`
4. Seguir el mismo patrón:
   - Diagnosticar estado actual
   - Verificar arquitectura de seguridad
   - Fix si necesario
   - Documentar
   - Commit + push

---

Generated: 2026-04-09 00:35:00
