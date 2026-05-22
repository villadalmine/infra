# AI Tooling Roadmap — homelab infra

Ideas de integración entre las herramientas de observabilidad K8s y la capa de IA
(HolmesGPT, LiteLLM, Hermes Agent).

---

## 1. Radar + HolmesGPT

**Qué es Radar**: Kubernetes cluster explorer (Go binary, UI web en :9280).
Expone un MCP server en `:9280/mcp` con herramientas de lectura/escritura del cluster:
`get_topology`, `get_pod_logs`, `get_events`, `manage_workload`, etc.

**Qué es HolmesGPT**: Agente de investigación K8s con HTTP API.
Recibe un contexto (alerta, recurso en mal estado) y corre un loop agentic
que llama herramientas para encontrar la causa raíz.

### Nivel 1 — Solo config, sin código (5 min)

Agregar Radar como toolset MCP en el config de HolmesGPT CLI:

```yaml
# ~/.holmesconfig.yaml  (workstation)
toolsets:
  - type: mcp
    url: http://localhost:9280/mcp
    name: radar
```

**Limitación**: solo funciona en CLI local — el HolmesGPT in-cluster no llega a
`localhost:9280`. Para in-cluster hay que desplegar Radar como Deployment.

### Nivel 2 — Integración en código de Radar (valor alto)

Requisitos para agregar al repo de Radar (`skyhook-io/radar`):

**Backend Go** (`internal/` o `api/`):
- [ ] Endpoint `POST /api/diagnose` — recibe `{resource_type, namespace, name}`,
      construye el payload enriquecido con los datos que Radar ya tiene en caché
      (spec, status, eventos, logs recientes), y lo envía al HTTP API de HolmesGPT.
- [ ] Config en settings: `holmesgpt_url` (default `http://holmes.default.svc:8080`)
- [ ] Manejo de errores y timeout (HolmesGPT puede tardar 60-90 s).

**Frontend** (React/TypeScript en `ui/` o `frontend/`):
- [ ] Badge / indicador visual en recursos con `status != Healthy`
      (CrashLoopBackOff, Pending, OOMKilled, etc.).
- [ ] Botón "Diagnose with Holmes" en el panel de detalle de Pod, Deployment,
      StatefulSet, Job.
- [ ] Modal o side-panel con el resultado — texto del análisis de HolmesGPT +
      acción sugerida.
- [ ] (Opcional) Auto-diagnóstico al cargar un recurso en estado degradado.

**Flujo completo**:
```
Radar UI detecta CrashLoopBackOff en pod "app-xyz"
  → usuario hace click en "Diagnose with Holmes"
  → POST /api/diagnose {pod: "app-xyz", ns: "default"}
  → Radar arma payload: spec + status + últimos 50 eventos + últimas 200 líneas de log
  → POST http://holmes.default.svc:8080/api/investigate
  → HolmesGPT corre loop agentic (60-90 s):
      tool: get_pod_logs        → ya tiene Radar
      tool: get_prometheus      → Radar o directo
      tool: search_runbooks     → HolmesGPT nativo
  → respuesta: causa raíz + fix sugerido
  → Radar muestra inline en UI
```

---

## 2. Headlamp + HolmesGPT

**Qué es Headlamp**: Kubernetes web UI extensible via plugins TypeScript/React.
Plugins pueden agregar botones, secciones y acciones en cualquier vista de recurso.

### Plugin "headlamp-holmes" (~100 líneas TS)

**Arquitectura**:
```
Headlamp UI (browser)
  → headlamp-holmes plugin (React + TypeScript)
  → HolmesGPT HTTP API (in-cluster: http://holmes.cluster.home/api/investigate)
```

**Qué haría el plugin**:

1. **Botón en resource detail views** — aparece en Pod, Deployment, StatefulSet,
   DaemonSet, Job, CronJob:
   ```typescript
   Headlamp.registerResourceDetailsListItem({
     resourceKind: 'Pod',
     label: 'Diagnose with HolmesGPT',
     component: HolmesPanel
   });
   ```

2. **Auto-trigger en recursos degradados** — si el recurso tiene
   `status.phase != Running` o tiene `restartCount > 5`, muestra el panel
   automáticamente con el diagnóstico en curso.

3. **Panel de resultados** — muestra el análisis de HolmesGPT con:
   - Causa raíz identificada
   - Pasos para resolverlo
   - Links a recursos relacionados (ConfigMaps, Secrets, Services involucrados)

4. **Historial de diagnósticos** — guarda los últimos N diagnósticos en
   `localStorage` para referencias futuras sin re-invocar.

**Payload que envía al API de HolmesGPT**:
```typescript
async function diagnose(resource: KubeObject) {
  const res = await fetch(`${HOLMES_URL}/api/investigate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: `Investigate why ${resource.kind} ${resource.metadata.name} is unhealthy`,
      context: {
        resource: resource.jsonData,   // spec + status completo
        namespace: resource.metadata.namespace,
        cluster_context: headlampClusterContext()
      }
    })
  });
  return res.json();
}
```

### Requisitos para implementar

- [ ] Crear repo `headlamp-holmes` (plugin standalone, igual que `headlamp-opencost`)
- [ ] `npx @kinvolk/headlamp-plugin create headlamp-holmes`
- [ ] Configurar `HOLMES_URL` vía env var o settings panel del plugin
- [ ] Registrar acciones en los resource kinds relevantes (Pod, Deployment, etc.)
- [ ] Renderizar respuesta de HolmesGPT en markdown (ya usa el modelo `gpt-5.4` alias)
- [ ] Publicar en Headlamp plugin catalog (opcional)

**Ventaja vs Radar+Holmes**: Headlamp ya está deployed en el cluster como parte del
stack de observabilidad → plugin disponible para todos los namespaces sin instalar
nada adicional. Radar requiere correr el binario en la workstation.

---

## 3. Comparativa

| | Radar + Holmes | Headlamp + Holmes |
|---|---|---|
| **Effort** | Medio (cambios en repo Go) | Bajo (plugin TS independiente) |
| **Contexto disponible** | Muy rico (Radar pre-procesa todo) | Bueno (recurso K8s completo) |
| **Deploy** | Binary local + in-cluster Holmes | Plugin Headlamp (in-cluster) |
| **Audience** | Power users con Radar instalado | Todos los que usan Headlamp |
| **Bidireccional** | Holmes puede escribir via Radar MCP | Solo lectura → Holmes |
| **Prioridad sugerida** | Segunda | **Primera** |

**Recomendación**: empezar con el plugin Headlamp (más rápido de implementar, mayor
impacto) y luego la integración Radar como segunda fase.

---

## Estado actual — qué está hecho

### Headlamp local (workstation) — FUNCIONANDO
- [x] Headlamp 0.42.0 binary en `/tmp/headlamp-work/`
- [x] Plugin `headlamp-holmes` v0.1.0 — diagnóstico por recurso → LiteLLM `:4000`
- [x] Plugin `ai-assistant` v0.2.0-alpha (oficial) — chat lateral → LiteLLM `:4000`
- [x] LiteLLM confirmado recibiendo requests desde browser (CORS ok)
- [x] Guía completa en `docs/headlamp-setup.md`

### Pendiente
- [ ] Deploy Headlamp in-cluster con plugins pre-instalados (`make headlamp`)
- [ ] Conectar `ai-assistant` a HolmesGPT cuando esté deployado (`make ai-holmes`)
- [ ] Cambiar `headlamp-holmes` para usar `POST /api/investigate` de HolmesGPT
      en vez de LiteLLM directo (más contexto: logs reales, eventos, runbooks)

---

## Próximos pasos

1. `make ai-holmes` — deploy HolmesGPT en el cluster (requiere `make ai` primero)
2. Conectar `ai-assistant` a `http://holmes.cluster.home`
3. Actualizar `headlamp-holmes` para hablarle a Holmes en vez de LiteLLM directo
4. Para Radar: fork `skyhook-io/radar`, agregar `/api/diagnose` endpoint en Go
   y el botón en el frontend
