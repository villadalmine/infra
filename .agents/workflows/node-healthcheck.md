---
description: Perform cluster nodes health check (Identity and Stats)
---
# Workflow: Infra K3s Cluster Health Check

This workflow reviews the health of K3s nodes to verify that their identity and resources match the hardware documented in `AGENTS.md`.

## Steps

1. Check node identity against the inventory table.
// turbo
```bash
make node-identity
```

2. Check node resources (CPU, RAM, temp).
// turbo
```bash
make node-stats
```

3. If anomalous results are found, trigger the full Ansible healthcheck.
```bash
make healthcheck
```
