---
name: onboarding
description: >
  First-time cluster setup: workstation tool installation (mise), node access
  configuration (SSH keys + sudoers), inventory setup, and node survey.
  Everything a new operator needs before bootstrapping K3s.
metadata:
  tags: [onboarding, setup, ssh, sudoers, mise, inventory, survey]
---

# onboarding — First-Time Setup

## Full Workflow (in order)

```bash
# 1. Install workstation tools
make deps

# 2. Fill in your nodes
cp inventory/hosts.ini.example inventory/hosts.ini
# edit with your node IPs and username

# 3. Configure passwordless access on nodes (once, needs password)
make setup-nodes

# 4. Collect hardware info
make survey

# 5. (Optional) Start local AI assistant
export OPENROUTER_API_KEY=sk-or-...
make litellm   # in a separate terminal

# 6. Bootstrap the cluster
make core      # just K3s
make full      # everything
```

---

## Step 1 — Workstation Tools (`make deps`)

Uses **mise** to install pinned versions of all tools.

```
mise install    → python 3.13, node 22, kubectl 1.33, helm 3.17, nova
mise run setup  → pip install (ansible, litellm), ansible-galaxy collections
```

**First time on a new machine:**
```bash
# mise not installed yet?
curl https://mise.run | sh
# Then restart your shell, or:
eval "$(~/.local/bin/mise activate bash)"
```

mise installs tools to `~/.local/share/mise/` — no sudo, no system changes.
Tools are isolated per-project via `.mise.toml` in the repo root.

**Python packages installed (`requirements.txt`):**
- `ansible` — cluster automation
- `kubernetes` — required by `kubernetes.core` Ansible collection
- `litellm[proxy]` — local AI model router

**Ansible collections installed (`requirements.yml`):**
- `ansible.posix` — authorized_key module
- `kubernetes.core` — K8s resource management

---

## Step 2 — Inventory (`inventory/hosts.ini`)

Copy from example, fill in your nodes:

```ini
[all]
node01 ansible_host=192.168.1.10
node02 ansible_host=192.168.1.11
node03 ansible_host=192.168.1.12

[server_nodes]   ← K3s control-plane (1 or 3 for HA)
node01

[agent_nodes]    ← K3s workers
node02
node03

[all:vars]
ansible_user=ubuntu                              # user on your nodes
ansible_ssh_private_key_file=~/.ssh/id_ed25519  # your local SSH key
ansible_become_method=sudo                       # sudo or su
```

**Groups:**
- `server_nodes` — run etcd + control-plane. Need fast storage (<5ms write latency). 1 or 3 nodes.
- `agent_nodes` — run workloads. Any storage. Add as many as you have.
- `standalone` — not in K3s. For isolated services or testing.
- `k3s_nodes` — auto-computed from server_nodes + agent_nodes.

**Common `ansible_user` values:**
| Device | Default user |
|--------|-------------|
| Ubuntu/Debian cloud image | `ubuntu` |
| Raspberry Pi OS | `pi` |
| Direct root access | `root` |
| Custom image | whatever you set |

---

## Step 3 — Node Access (`make setup-nodes`)

```bash
make setup-nodes                         # auto-detect SSH key, sudoers_mode=full
make setup-nodes SSH_KEY=~/.ssh/id_rsa.pub    # specify key
make setup-nodes SUDOERS_MODE=minimal    # command-by-command sudoers
```

**What it does:**
1. Reads your inventory and shows every node it will touch
2. Shows exactly what will be written to `/etc/sudoers.d/ansible-operator`
3. Asks for your approval before writing
4. Connects once using your node password
5. After this: SSH and sudo are passwordless forever

**Sudoers modes:**
- `full` (default) — `NOPASSWD: ALL` — simplest, recommended for homelabs
- `minimal` — specific commands only — auditable, production-like

**The script auto-detects:**
- SSH key: checks `~/.ssh/id_ed25519`, `~/.ssh/id_ecdsa`, `~/.ssh/id_rsa` in order
- Become method: reads `ansible_become_method` from `inventory/hosts.ini`
- Whether to use `-K` (skipped if `ansible_user=root`)

**Extending sudoers later** (when new roles need new commands):
```bash
# Edit roles/setup-node-access/templates/sudoers.j2
# Then:
make setup-sudoers   # shows diff + asks approval — never applies silently
```

---

## Step 4 — Survey (`make survey`)

Collects hardware info from all nodes. Results in `playbooks/survey-output/`.
See `skills/survey/SKILL.md` for full documentation.

---

## Step 5 — Local LiteLLM (`make litellm`)

Optional — enables AI-assisted operations via OpenCode or Claude Code.

```bash
export OPENROUTER_API_KEY=sk-or-...   # get one at openrouter.ai
make litellm
# LiteLLM starts at http://localhost:4000
```

OpenCode (`opencode.json`) and Claude Code (via MCP) already point to `localhost:4000`.

Minimum: one API key from any of these providers:
- **OpenRouter** — covers Claude, Gemini, GPT-4, and free models with one key
- **Anthropic** — direct Claude access (`ANTHROPIC_API_KEY`)
- **Google** — direct Gemini (`GOOGLE_API_KEY`)
- **OpenAI** — direct GPT-4 (`OPENAI_API_KEY`)
- **Ollama** — no key needed, runs locally (set `api_base: http://localhost:11434`)

---

## Troubleshooting

### SSH connection refused / timeout

```bash
# Test directly first
ssh ubuntu@192.168.1.10

# Common issues:
# - Wrong IP in hosts.ini
# - SSH service not running on node
# - Firewall blocking port 22
# - Wrong username
```

### "sudo: a password is required" during setup-nodes

Normal — that's why you run `make setup-nodes` instead of other targets first.
The script uses `-K` to ask for your sudo password once.

### "Permission denied (publickey)" after setup-nodes

The key wasn't copied correctly. Re-run:
```bash
make setup-nodes  # it's idempotent — safe to run again
```

### Node unreachable (ping fails)

Check that `ansible_host` in `hosts.ini` is the correct IP:
```bash
ping 192.168.1.10
ansible -i inventory/hosts.ini all -m ping
```

### Sudoers validation failed

The `validate: visudo -cf %s` check prevents broken sudoers from being written.
If this fails, the file is NOT written — your node is safe.
Check the error output for syntax issues in the template.

### mise: command not found after install

```bash
eval "$(~/.local/bin/mise activate bash)"
# Or restart your shell
# Or add to ~/.bashrc: eval "$(~/.local/bin/mise activate bash)"
```

---

## Role Structure

```
roles/setup-node-access/
  defaults/main.yml     ← ssh_public_key_file, sudoers_mode, sudoers_file path
  tasks/
    main.yml            ← includes ssh-keys + sudoers
    ssh-keys.yml        ← ansible.posix.authorized_key
    sudoers.yml         ← diff preview → pause for approval → template apply
  templates/
    sudoers.j2          ← source of truth for sudoers content

scripts/setup-node-access   ← interactive first-time setup wrapper
playbooks/setup-node-access.yml
inventory/hosts.ini.example ← copy this to hosts.ini
```
