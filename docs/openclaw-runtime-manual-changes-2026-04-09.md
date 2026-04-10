
Runtime manual changes performed 2026-04-09
==========================================

Purpose
-------
Document every manual action taken during the OpenClaw troubleshooting session on 2026-04-09.
This file is a single-source, chronological record so you (or another operator) can resume,
reproduce, audit or revert the changes later. Nothing in this document changes Ansible templates
unless explicitly stated.

High-level goal
---------------
- Get OpenClaw to use the in-cluster LiteLLM proxy (litellm-proxy) instead of talking directly
  to api.openai.com with the internal master key (sk-hermes-internal), which produced 401s.

Chronological actions (detailed)
--------------------------------
Note: every command below was executed interactively on the control machine against the cluster.

1) Inspection
   - List OpenClaw pods and inspect pod JSON to see init containers / mounts:
     kubectl get pods -n openclaw
     kubectl get pod openclaw-68cb7457bf-slff8 -n openclaw -o json

2) Init container behavior and missing config
   - Observed init container ran onboarding interactively and gateway reported: "Missing config".

3) Ensure writable config in PVC (safe copy)
   - Created a PVC-mounted helper pod and wrote the ConfigMap content into the PVC path that the gateway expects:
     kubectl run -n openclaw openclaw-pvc-writer --rm -it --restart=Never --image=busybox --overrides='{"spec":...}' -- /bin/sh
     # then inside or with kubectl exec:
     kubectl exec -n openclaw openclaw-pvc-writer -- sh -c "printf '%s' \"$(kubectl get configmap openclaw-config -n openclaw -o jsonpath='{.data.openclaw.json}')\" > /home/node/.openclaw/openclaw.json && chmod 664 /home/node/.openclaw/openclaw.json"
   - Verified file exists on PVC: ls -la /home/node/.openclaw

4) Make OpenClaw read the PVC copy (temporary env patch)
   - Patch Deployment so OPENCLAW_CONFIG_DIR=/home/node/.openclaw:
     kubectl patch deployment openclaw -n openclaw --type='json' -p='[{"op":"replace","path":"/spec/template/spec/containers/0/env/1/value","value":"/home/node/.openclaw"}]'

5) Allow startup without full config (temporary arg)
   - Add --allow-unconfigured so the gateway can boot during testing:
     kubectl patch deployment openclaw -n openclaw --type='json' -p='[{"op":"replace","path":"/spec/template/spec/containers/0/command","value":["node","dist/index.js","gateway","--port","18789","--allow-unconfigured"]}]'

6) Investigate 401s and API key usage
   - Observed OpenClaw sending Authorization: Bearer sk-hermes-internal to the endpoint; OpenAI rejected it with 401.
   - Environment in the deployment showed OPENAI_API_BASE=http://litellm-proxy.ai.svc.cluster.local:4000/v1
   - But model id in active config was prefixed with provider (openai/gpt-5.4) which caused the client to call provider=openai and bypass the base URL.

7) Try options to route through the proxy
   - Wrote aliases and changed litellm-proxy config to add mappings and enabled verbose logging.
     * Edit ConfigMap ai/litellm-proxy-config: set set_verbose: true and ensure model alias entries exist for gpt-5.4 and openai/gpt-5.4
     kubectl get configmap litellm-proxy-config -n ai -o yaml > /tmp/litellm-cm.yaml
     # manual edit to add aliases or sed/awk replacement
     kubectl apply -f /tmp/litellm-cm.yaml
     kubectl rollout restart deployment/litellm-proxy -n ai

8) Created missing secret in openclaw namespace used by the pod
   - The pod expected litellm-secrets in openclaw but only existed in ai. Copy key into openclaw namespace (secret value not committed here):
     VAL=$(kubectl get secret -n ai litellm-secrets -o jsonpath='{.data.OPENCLAW_OPENROUTER_API_KEY}' | base64 --decode)
     kubectl create secret generic litellm-secrets -n openclaw --from-literal=OPENCLAW_OPENROUTER_API_KEY="$VAL"

9) Force the pod to use litellm master key (trial)
   - Set OPENAI_API_KEY=sk-hermes-internal to encourage use of the proxy while testing:
     kubectl set env deployment/openclaw -n openclaw OPENAI_API_KEY=sk-hermes-internal

10) Try to make api.openai.com resolve to proxy (hostAliases)
    - Temporary hostAliases were added to the Deployment so api.openai.com pointed to the cluster IP of litellm-proxy (this is a heavy-handed temporary test):
      kubectl patch deployment openclaw -n openclaw --type='merge' -p '{"spec":{"template":{"spec":{"hostAliases":[{"ip":"10.43.213.201","hostnames":["api.openai.com"]}]}}}}'
    - Verified inside the gateway pod: /etc/hosts contains entry mapping api.openai.com -> 10.43.213.201

11) Observed security block
    - With hostAliases present the gateway attempted to fetch the responses endpoint but blocked URL fetches that resolve to private addresses:
      [security] blocked URL fetch (url-fetch) target=https://api.openai.com/v1/responses reason=Blocked: resolves to private/
    - This is a runtime security policy inside OpenClaw that prevents it calling hosts that resolve to private IPs even if those hosts are our proxy.

12) Reverted hostAliases and tried a more declarative approach
    - Edited the ConfigMap openclaw-config in namespace openclaw to set agents.defaults.model = "gpt-5.4" (replacing previous value). Applied and restarted deployment:
      kubectl get configmap openclaw-config -n openclaw -o yaml > /tmp/openclaw-cm.yaml
      # edit the JSON inside data.openclaw.json to change model
      kubectl apply -f /tmp/openclaw-cm-new.yaml
      kubectl rollout restart deployment/openclaw -n openclaw

13) Test traffic path and proxy
    - From inside the gateway container: verified that OPENAI_API_BASE resolves and that curl $OPENAI_API_BASE/models returns the model list (proxy responded OK).
    - Verified litellm-proxy logs showed GET /v1/models from the OpenClaw pod IP.

14) Performed an in-cluster POST test to the proxy (attempt)
    - Created temporary pod curl-test and attempted POST to http://litellm-proxy.ai.svc.cluster.local:4000/v1/chat/completions (used the master key). Some commands were aborted or timed out during iterative testing.

15) Changes to source tree (only docs and examples committed)
    - Edited roles/install-litellm-proxy/defaults/secrets.yml to clear hardcoded keys (file is gitignored) — to avoid committing real secrets.
    - Added group_vars/all/vault.yml.example with placeholders.
    - Added docs/vault-setup.md describing how to use Ansible Vault.
    - Created and updated docs/openclaw-runtime-manual-changes-2026-04-09.md (this file) with a running log.

Resources changed (cluster)
---------------------------
- Namespaces: openclaw, ai
- Deployments:
  - openclaw (namespace: openclaw) — patched (env, command, hostAliases added/removed, rollout restarts)
  - litellm-proxy (namespace: ai) — ConfigMap changed and rollout restarted
- ConfigMaps:
  - openclaw-config (namespace: openclaw) — data.openclaw.json edited (model changed to gpt-5.4)
  - litellm-proxy-config (namespace: ai) — aliases added, set_verbose toggled
- Secrets:
  - litellm-secrets (namespace: ai) — original
  - litellm-secrets (namespace: openclaw) — temporary copy created from ai/litellm-secrets (to satisfy pod env refs)
  - openclaw-secrets (namespace: openclaw) — existed and was used for tokens
- Pods created for testing/debugging (temporary):
  - openclaw-pvc-writer (namespace: openclaw) — helper to write to PVC
  - debug-pod (namespace: openclaw) — ad-hoc checks
  - curl-test (namespace: openclaw) — temporary POST test (deleted afterwards)

Files edited in repository (doc-only commits)
-----------------------------------------
- roles/install-litellm-proxy/defaults/secrets.yml (values cleared locally; file is gitignored)
- group_vars/all/vault.yml.example (new)
- docs/vault-setup.md (new)
- docs/openclaw-runtime-manual-changes-2026-04-09.md (this file, updated)

Current observed state (end of session)
--------------------------------------
- OpenClaw gateway boots and is Ready, but runs still fail when trying to reach responses/completions because of a security block that prevents requests to hostnames resolving to private addresses ("Blocked: resolves to private/...").
- Litellm-proxy is Running and responds to /v1/models when contacted; it logged requests from the OpenClaw pod IP.
- The PVC has several variants of openclaw.json (backups) and the runtime appears to create or clobber these during startup. The authoritative source that controls startup is the ConfigMap openclaw-config (mounted at /etc/openclaw) — we updated that.

Commands to reproduce / inspect (safe, read-only where possible)
------------------------------------------------------------
# List pods and their status
kubectl get pods -n openclaw
kubectl get pods -n ai -l app=litellm-proxy

# Show the openclaw config that the Deployment mounts
kubectl get configmap openclaw-config -n openclaw -o yaml

# Dump litellm-proxy config
kubectl get configmap litellm-proxy-config -n ai -o yaml

# Check logs for OpenClaw gateway
kubectl logs -n openclaw -l app=openclaw -c openclaw-gateway --tail=200

# Check litellm-proxy logs
kubectl logs -n ai -l app=litellm-proxy --tail=200

Revert / rollback commands (if you need to undo the runtime changes)
---------------------------------------------------------------
# 1) Restore the original openclaw-config (if you have a saved copy):
kubectl apply -f /path/to/backup/openclaw-config.yaml -n openclaw && kubectl rollout restart deployment/openclaw -n openclaw

# 2) Remove the temporary secret in openclaw namespace (if created by this session):
kubectl delete secret litellm-secrets -n openclaw || true

# 3) Remove hostAliases (if left behind):
kubectl patch deployment openclaw -n openclaw --type='json' -p='[{"op":"remove","path":"/spec/template/spec/hostAliases"}]' && kubectl rollout restart deployment/openclaw -n openclaw

# 4) Revert any env changes to deployment (restore via original manifest or roll back):
kubectl rollout history deployment/openclaw -n openclaw
kubectl rollout undo deployment/openclaw -n openclaw

Notes, lessons learned and next steps
------------------------------------
- Root cause: OpenClaw sometimes encodes provider in the model id (openai/...), which causes the client to target provider-specific endpoints instead of honoring OPENAI_API_BASE. When that happens, the in-cluster master key gets sent to the public OpenAI endpoint and is rejected (401). The safe approach is to ensure OpenClaw uses model ids that the proxy maps (no provider prefix) or to ensure the proxy accepts the provider-prefixed id via explicit alias.
- The gateway enforces a security policy blocking URL fetches to hostnames that resolve to private addresses. This blocks the hostAlias technique. Fixing requires either whitelisting the specific hostname/IP in OpenClaw security config or avoiding hostAliases and forcing the gateway to use the proxy by config (preferred).
- Next time: implement the permanent fix via Ansible templates (roles/install-openclaw/templates and roles/install-litellm-proxy) only after the runtime fix is validated.

Record end
