# Headlamp + AI Assistant — Setup Guide

Guía completa para levantar Headlamp localmente con el plugin oficial de AI
(`ai-assistant`) y el plugin `headlamp-holmes` (diagnóstico por recurso), ambos
apuntando al LiteLLM interno del cluster.

---

## Requisitos previos

- Cluster K3s corriendo (`make core` completado)
- `~/.kube/config` apuntando al cluster (`kubectl get nodes` funciona)
- LiteLLM corriendo en `192.168.178.90:4000` (parte de la GPU workstation `t7910`)
- `curl` disponible

---

## 1. Instalar Headlamp (binary local)

Headlamp no necesita estar deployado en el cluster para desarrollo/testing local.
Se corre el binary en la workstation y se le pasa el kubeconfig.

```bash
# Descargar Headlamp 0.42.0 para linux-x64
mkdir -p /tmp/headlamp-work
cd /tmp/headlamp-work
curl -sL https://github.com/headlamp-k8s/headlamp/releases/download/v0.42.0/Headlamp-0.42.0-linux-x64.tar.gz \
  -o headlamp.tar.gz
tar -xzf headlamp.tar.gz
```

> Para Mac: reemplazar `linux-x64` por `mac-x64` o `mac-arm64`.
> Para Windows: descargar el `.exe` desde el mismo release.

---

## 2. Crear el directorio de plugins

```bash
mkdir -p /tmp/headlamp-plugins
```

---

## 3. Instalar el plugin oficial `ai-assistant`

```bash
# Descargar pre-built desde GitHub releases
curl -sL https://github.com/headlamp-k8s/plugins/releases/download/ai-assistant-0.2.0-alpha/headlamp-k8s-ai-assistant-0.2.0-alpha.tar.gz \
  -o /tmp/ai-assistant.tar.gz

# Extraer al directorio de plugins
tar -xzf /tmp/ai-assistant.tar.gz -C /tmp/headlamp-plugins/

# Verificar
ls /tmp/headlamp-plugins/ai-assistant/
# → main.js  package.json
```

---

## 4. Instalar el plugin `headlamp-holmes` (diagnóstico por recurso)

Plugin liviano (~5KB) que agrega una sección "AI Diagnosis" en el detalle de
cada Pod/Deployment/StatefulSet/DaemonSet/Job/CronJob.

```bash
mkdir -p /tmp/headlamp-holmes/src

# Instalar SDK
cd /tmp/headlamp-holmes
npm install --save-dev @kinvolk/headlamp-plugin@^0.14.0

# Copiar tsconfig del template
cp node_modules/@kinvolk/headlamp-plugin/template/tsconfig.json .
cp node_modules/@kinvolk/headlamp-plugin/template/src/headlamp-plugin.d.ts src/
```

Crear `package.json`:

```json
{
  "name": "headlamp-holmes",
  "version": "0.1.0",
  "description": "Diagnose Kubernetes resources with AI via LiteLLM",
  "scripts": {
    "build": "headlamp-plugin build",
    "start": "headlamp-plugin start"
  },
  "keywords": ["headlamp", "headlamp-plugin", "kubernetes", "ai"],
  "devDependencies": {
    "@kinvolk/headlamp-plugin": "^0.14.0"
  }
}
```

Crear `src/index.tsx` — ver el archivo completo en
`/tmp/headlamp-holmes/src/index.tsx` (o más abajo en esta guía).

```bash
# Compilar
cd /tmp/headlamp-holmes
node_modules/.bin/headlamp-plugin build

# Instalar en el directorio de plugins
mkdir -p /tmp/headlamp-plugins/headlamp-holmes
cp dist/main.js     /tmp/headlamp-plugins/headlamp-holmes/
cp package.json     /tmp/headlamp-plugins/headlamp-holmes/
```

---

## 5. Arrancar Headlamp

```bash
/tmp/headlamp-work/Headlamp-0.42.0-linux-x64/resources/headlamp-server \
  -kubeconfig ~/.kube/config \
  -html-static-dir /tmp/headlamp-work/Headlamp-0.42.0-linux-x64/resources/frontend \
  -plugins-dir /tmp/headlamp-plugins \
  -dev \
  -port 4466
```

Abrir: **http://localhost:4466**

---

## 5b. Parche de namespace para Holmes (obligatorio)

El plugin `ai-assistant` busca el servicio Holmes en el namespace **`default`**,
pero en este cluster Holmes corre en **`ai`**. El parche es un sed de una línea:

```bash
sed -i 's/"holmesgpt-holmes",dB=80,fB="default"/"holmesgpt-holmes",dB=80,fB="ai"/' \
  /tmp/headlamp-plugins/ai-assistant/main.js
```

Verificar:
```bash
grep -o '"holmesgpt-holmes".*fB="[^"]*"' /tmp/headlamp-plugins/ai-assistant/main.js
# debe mostrar: "holmesgpt-holmes",dB=80,fB="ai"
```

> **Si instalás una versión nueva del plugin**, hay que volver a aplicar este parche.

---

## 6. Configurar `ai-assistant` para usar LiteLLM

El plugin `ai-assistant` pide las credenciales por UI la primera vez.

En Headlamp → icono de AI (esquina superior derecha) → **Settings**:

### Provider: Local Models (Ollama) → LiteLLM GPU
| Campo | Valor |
|---|---|
| Provider | `Local Models` |
| Base URL | `http://192.168.178.90:11434` |
| Model | `local-fast` |

> "Local Models" usa el formato Ollama (`/api/chat`). LiteLLM expone Ollama en
> el mismo endpoint. Requiere que t7910 esté prendido.

### Provider: Holmes Agent (cluster-aware)
| Campo | Valor |
|---|---|
| Provider | `Holmes` |
| (URL) | auto-descubierto via k8s proxy → `holmesgpt-holmes.ai:80` |

Holmes se conecta via el k8s API proxy de Headlamp — no requiere URL externa.
El namespace está parchado a `ai` (ver sección 5b).

> El plugin guarda la config en `localStorage` del browser.
> No hay credenciales en disco ni en el repo.

### Modelos disponibles en LiteLLM (t7910)

| Model ID | Backend | VRAM | Mejor para |
|---|---|---|---|
| `local-fast` | Qwen2.5 7B Q4 — P4 :11434 | 4.5 GB | Chat rápido, diagnósticos |
| `local-llama` | Llama 3.1 8B — P4 :11434 | 5 GB | Instrucciones generales |
| `local-reason` | DeepSeek-R1 8B — P4 :11434 | 5 GB | Razonamiento paso a paso |
| `local-coder-7b` | Qwen2.5-Coder 7B Q8 — P4 :11434 | 7.5 GB | Código |
| `local-codestral` | Codestral 22B — dual GPU :11436 | 13 GB | Código complejo |
| `local-deepseek` | DeepSeek-V2 16B — dual GPU :11436 | 10 GB | Razonamiento largo |
| `or-nemotron-super` | Nemotron 120B — OpenRouter | cloud | Tareas complejas |
| `or-qwen3-coder` | Qwen3-Coder 480B — OpenRouter | cloud | Código a escala |

---

## 7. Usar `headlamp-holmes`

1. Ir a **Workloads → Pods**
2. Hacer click en cualquier pod
3. Bajar hasta el final del detalle → sección **"AI Diagnosis (LiteLLM)"**
4. Pods **unhealthy** (CrashLoopBackOff, Pending, restartCount > 3): se diagnostican automáticamente al abrir
5. Pods sanos: botón manual **"Diagnose with AI"**

El plugin llama a `http://192.168.178.90:4000/v1/chat/completions` directamente
desde el browser. LiteLLM tiene CORS habilitado por defecto.

---

## 8. Comparativa de plugins

| | `headlamp-holmes` | `ai-assistant` (oficial) |
|---|---|---|
| **UX** | Sección inline en el recurso | Chat lateral conversacional |
| **Contexto** | JSON del recurso actual | Cualquier pregunta sobre el cluster |
| **HolmesGPT** | No (LiteLLM directo) | Sí — cuando Holmes está deployado |
| **MCP** | No | Sí (desktop only) |
| **Tamaño** | 5 KB | 950 KB |
| **Config** | Hardcoded en el build | Configurable por UI |

---

## Estado de la integración Holmes + ai-assistant

### ✅ Funcionando (2026-05-22)

```
Headlamp (browser)
  → localhost:4466 (headlamp-server)
  → k8s API proxy: /api/v1/namespaces/ai/services/holmesgpt-holmes:80/proxy/api/agui/chat
  → Holmes pod:5050 (combined server.py)
  → in-cluster LiteLLM :4000 (gpt-5.4 → llama3.1:8b)
  → Ollama @ t7910:11434 (DIRECTO — sin pasar por t7910 LiteLLM)
```

Holmes usa el protocolo **AG-UI** (streaming SSE events). El pod corre el servidor
combinado (`server.py` montado via ConfigMap `holmes-agui-server` en namespace `ai`)
que expone tanto `/api/chat` (UI nativa) como `/api/agui/chat` (Headlamp).

### Re-deploy (idempotente)

```bash
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags ai-holmes
```

El rol `install-holmes` aplica automáticamente el ConfigMap y el patch del Deployment.

### Mapa de modelos y endpoints por superficie (2026-05-22)

Cada superficie de UI usa un path distinto hasta el modelo. Tabla completa:

| Superficie | Modelo efectivo | GPU/Cloud | Dónde se configura |
|---|---|---|---|
| **Holmes UI** (`holmes-ui.cluster.home`) | `llama3.1:8b` via `gpt-5.4` | t7910 P4 :11434 | ver abajo |
| **Headlamp ai-assistant → Holmes** | `llama3.1:8b` via `gpt-5.4` | t7910 P4 :11434 | ver abajo |
| **Headlamp ai-assistant → Local Models** | `qwen2.5-coder:7b` o lo que configure el usuario | t7910 :11434 directo | browser localStorage |
| **headlamp-holmes** (inline pod diagnosis) | `qwen2.5-coder:7b` (`local-fast`) | t7910 P4 :11434 | hardcoded en `src/index.tsx` |

Ninguna superficie usa OpenRouter. Todo es GPU local en t7910.

---

#### Holmes UI — cadena completa

```
browser → holmes-ui.cluster.home (HTTPRoute)
  → nginx:alpine pod (ConfigMap: holmes-ui-config, namespace ai)
    nginx.conf: proxy_pass http://holmesgpt-holmes.ai.svc.cluster.local:80
  → Holmes pod :5050, endpoint POST /api/chat
  → OPENAI_API_BASE=http://litellm-proxy.ai.svc.cluster.local:4000/v1
  → in-cluster LiteLLM: model=gpt-5.4 → openai/llama3.1:8b @ 192.168.178.90:11434/v1
  → Ollama t7910 P4 GPU :11434
```

**Dónde está cada parte:**
- nginx + proxy: `roles/install-holmes-ui/tasks/main.yml` (ConfigMap `holmes-ui-config`)
- Holmes Helm values (modelo + env): `roles/install-holmes/tasks/main.yml` líneas 40-75
  - `additionalEnvVars.OPENAI_API_BASE` → `http://litellm-proxy.ai.svc.cluster.local:4000/v1`
  - `additionalEnvVars.OPENAI_API_KEY` → `sk-hermes-internal`
  - `modelList.holmes-litellm.model` → `{{ holmes_model_name }}` (= `local-reason` en defaults, pero Holmes v0.24.0 usa `gpt-5.4` por defecto)
- `gpt-5.4` alias en LiteLLM: `roles/install-litellm-proxy/tasks/main.yml`, sección "Legacy Aliases"
- Timeout 5 min: HTTPRoute en `roles/install-holmes-ui/tasks/main.yml` (`timeouts.request: 5m`)

---

#### Headlamp ai-assistant → Holmes provider — cadena completa

```
browser Headlamp (localhost:4466)
  → k8s API proxy: GET /clusters/default/api/v1/namespaces/ai/services/holmesgpt-holmes:80/proxy/api/agui/chat
  → Holmes pod :5050, endpoint POST /api/agui/chat
  → mismo path que Holmes UI (gpt-5.4 → llama3.1:8b → t7910 P4)
```

**Dónde está cada parte:**
- Namespace patch (OBLIGATORIO post-install): `sed -i 's/fB="default"/fB="ai"/'` sobre `/tmp/headlamp-plugins/ai-assistant/main.js`
- AG-UI server: `roles/install-holmes/files/holmes-combined-server.py`
  - ConfigMap `holmes-agui-server` montado en `/app/server.py` via subPath
  - Parche del Deployment: `roles/install-holmes/tasks/main.yml` (strategic merge patch)
  - Env `HOLMES_CONFIGPATH_DIR=/tmp/.holmes` (readOnlyRootFilesystem workaround)
- Protocolo: AG-UI SSE (`RUN_STARTED → TEXT_MESSAGE_CONTENT → RUN_FINISHED`)

---

#### Headlamp ai-assistant → Local Models provider

Config en browser UI (localStorage, **no hay código en el repo**):
- Base URL: `http://192.168.178.90:11434` → Ollama directo (P4 GPU)
- Model: nombre real de Ollama p.ej. `llama3.1:8b` o `qwen2.5-coder:7b`
  - ⚠️ `local-fast` NO funciona aquí — es un alias de t7910 LiteLLM (:4000), no existe en Ollama (:11434)
  - Para usar aliases (`local-fast`, `local-llama`, etc.) cambiar Base URL a `http://192.168.178.90:4000`

---

#### headlamp-holmes plugin (inline pod diagnosis)

```
browser → fetch() directo a http://192.168.178.90:4000/v1/chat/completions
  → t7910 LiteLLM :4000 (single hop)
  → model local-fast → qwen2.5-coder:7b → Ollama t7910 P4 :11434
```

**Dónde está:**
- `LITELLM_URL` y `LITELLM_MODEL`: hardcoded en `src/index.tsx` del plugin (build local, no en cluster)
- t7910 LiteLLM config: `/etc/litellm/config.yaml` en t7910 (no en este repo), `local-fast: ollama/qwen2.5-coder:7b`
- ⚠️ `qwen2.5-coder:7b` NO retorna `tool_calls` API — embebe el call como JSON en `content`.
  Esto está bien para diagnosis inline (no usa tool calling), pero NO sirve para Holmes.

---

### Gotchas de debugging (lecciones aprendidas)

#### Double-hop LiteLLM rompe tool calling

**Problema:** Holmes → in-cluster LiteLLM → t7910 LiteLLM → Ollama.
Con este doble salto, `llama3.1:8b` genera JSON inline en lugar de `tool_calls` API.
El resultado: Holmes crea TodoWrite plans pero no ejecuta tools reales.

**Fix:** Configurar `gpt-5.4` en in-cluster LiteLLM para apuntar **directo a Ollama**:
```yaml
- model_name: gpt-5.4
  litellm_params:
    model: openai/llama3.1:8b
    api_base: http://192.168.178.90:11434/v1
    api_key: "dummy"
```

#### deepseek-r1:8b devuelve content vacío

`deepseek-r1:8b` es un reasoning model — pone todo en `reasoning_content`, `content` queda vacío.
El event generator de AG-UI buscaba `content` y no emitía nada. No usar deepseek-r1 para Holmes.

#### TodoWrite JSON en ANSWER_END

`llama3.1:8b` a veces devuelve la respuesta envuelta en JSON: `{"content": "la respuesta", "status": "pending"}`.
El event generator lo parsea y extrae el campo `"content"` en lugar de descartar el blob.

#### OOMKill bajo carga

Holmes con límite de 1Gi muere con exit code 137 bajo carga concurrente.
**Fix**: `holmes_memory_limit: "3Gi"` en `roles/install-holmes/defaults/main.yml`.
Node: `srv-rk1-nvme-01` (configurado en `holmes_node_hostname`).

### Próximo paso: Headlamp in-cluster

```bash
# TODO: make headlamp   (role install-headlamp con plugins montados via ConfigMap)
```

---

## Código fuente — `headlamp-holmes/src/index.tsx`

```typescript
import {
  DetailsViewSectionProps,
  registerDetailsViewSection,
} from '@kinvolk/headlamp-plugin/lib';
import { SectionBox } from '@kinvolk/headlamp-plugin/lib/CommonComponents';
import React from 'react';

const LITELLM_URL = 'http://192.168.178.90:4000/v1/chat/completions';
const LITELLM_MODEL = 'local-fast';

const SYSTEM_PROMPT = `You are an expert Kubernetes troubleshooter similar to HolmesGPT.
Analyze the Kubernetes resource JSON provided and identify:
1. The root cause of any issues
2. Specific remediation steps
3. Any related resources that may be involved

Be concise and actionable. Format your response with clear sections.`;

function isUnhealthy(resource: any): boolean {
  const phase = resource?.status?.phase;
  const containerStatuses = resource?.status?.containerStatuses || [];
  const conditions = resource?.status?.conditions || [];

  if (phase && !['Running', 'Succeeded'].includes(phase)) return true;
  for (const cs of containerStatuses) {
    if (cs.restartCount > 3) return true;
    if (cs.state?.waiting?.reason === 'CrashLoopBackOff') return true;
    if (cs.state?.waiting?.reason === 'OOMKilled') return true;
    if (!cs.ready && cs.state?.waiting) return true;
  }
  for (const cond of conditions) {
    if (cond.type === 'Ready' && cond.status === 'False') return true;
  }
  return false;
}

function DiagnosePanel({ resource }: DetailsViewSectionProps) {
  const [diagnosis, setDiagnosis] = React.useState<string>('');
  const [loading, setLoading]     = React.useState(false);
  const [error, setError]         = React.useState<string>('');
  const [ran, setRan]             = React.useState(false);

  const unhealthy = isUnhealthy(resource);

  async function runDiagnosis() {
    setLoading(true); setError(''); setDiagnosis(''); setRan(true);
    const resourceJson = JSON.stringify(
      { kind: resource.kind, metadata: resource.metadata,
        spec: (resource as any).spec, status: (resource as any).status },
      null, 2
    );
    const userMessage =
      `Investigate why ${resource.kind} "${resource.metadata?.name}" ` +
      `in namespace "${resource.metadata?.namespace}" is unhealthy.\n\n` +
      `Resource JSON:\n\`\`\`json\n${resourceJson}\n\`\`\``;
    try {
      const resp = await fetch(LITELLM_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer sk-dummy' },
        body: JSON.stringify({
          model: LITELLM_MODEL,
          messages: [
            { role: 'system', content: SYSTEM_PROMPT },
            { role: 'user',   content: userMessage },
          ],
          max_tokens: 1024, stream: false,
        }),
      });
      if (!resp.ok) throw new Error(`API error ${resp.status}: ${await resp.text()}`);
      const data = await resp.json();
      const content = data?.choices?.[0]?.message?.content;
      if (content) setDiagnosis(content);
      else throw new Error('Empty response from AI');
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  React.useEffect(() => { if (unhealthy && !ran) runDiagnosis(); }, []);

  // ... UI rendering (ver archivo completo en /tmp/headlamp-holmes/src/index.tsx)
}

const SUPPORTED_KINDS = ['Pod','Deployment','StatefulSet','DaemonSet','Job','CronJob'];

registerDetailsViewSection(({ resource }: DetailsViewSectionProps) => {
  if (!resource || !SUPPORTED_KINDS.includes(resource.kind)) return null;
  return <DiagnosePanel resource={resource} />;
});
```
