Summary of runtime fixes applied on 2026-04-09

What I changed
- roles/install-openclaw/templates/openclaw-deployment.yaml.j2
  - Replaced upstream ``--yes`` onboarding flag (unsupported) with a non-interactive
    onboarding sequence. The initContainer now removes any stale/invalid
    /home/node/.openclaw/openclaw.json and runs:

      printf 'y\n' | node dist/index.js onboard --mode local --no-install-daemon

    This prevents the initContainer from failing on first-run when the image
    does not support a --yes flag or when a broken config file exists.

- roles/install-litellm-proxy/tasks/main.yml
  - Added a compatibility alias so OpenClaw's provider-prefixed model ID
    ``openai/gemini-free`` resolves to the same backend as ``gemini-free``.
  - Added a higher-quality Qwen model alias ``qwen-pro`` and adjusted
    Hermes fallback order to prefer ``qwen-pro`` for heavier workloads.

What I ran (relevant commands)
- Render & apply OpenClaw role (Ansible):
  ansible-playbook playbooks/bootstrap.yml --tags openclaw -e "ansible_connection=local"
- Approve Telegram pairing inside the running OpenClaw pod:
  kubectl exec -n openclaw <pod> -c openclaw-gateway -- node dist/index.js pairing approve telegram KEQ7QDXB
- Scale Hermes down (to avoid Telegram polling conflicts while OpenClaw runs):
  kubectl scale deployment hermes-agent-mcp -n ai --replicas=0
- Apply litellm-proxy config changes and restart the proxy:
  ansible-playbook playbooks/bootstrap.yml --tags ai-hermes-deploy -e "ansible_connection=local"
  kubectl rollout restart deployment/litellm-proxy -n ai

Observed runtime status
- OpenClaw pod is running (1/1). Telegram pairing approved for user id 8492872858.
- Earlier errors:
  - initContainer failed due to invalid ~/.openclaw/openclaw.json and unsupported
    ``--yes`` flag — fixed by the change above.
  - Telegram 409 conflict due to Hermes also polling with the same bot token —
    resolved by scaling Hermes to 0 replicas (left down as requested).
  - OpenClaw warmup errors: "Unknown model: openai/gemini-free" — fixed by
    adding an alias in the LiteLLM config so that provider-prefixed IDs resolve.

Next recommended steps
1. If you want Hermes back online, scale its deployment up once you have a
   separate token for OpenClaw or you accept only one process polling the bot.
2. Consider creating a distinct Telegram bot for OpenClaw to avoid future
   token conflicts.
3. If you prefer different model priorities, update roles/install-litellm-proxy/tasks/main.yml
   to select preferred backends and run the ai-hermes-deploy tag again.

Files changed (committed):
- roles/install-openclaw/templates/openclaw-deployment.yaml.j2
- roles/install-litellm-proxy/tasks/main.yml

If anything needs rewording in this doc before pushing, tell me now.
