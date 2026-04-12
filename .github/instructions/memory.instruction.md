---
applyTo: '**'
---

The `fix-mac-address` role ensures persistent MAC address, static IP configuration, and hostname consistency across cluster nodes. It was tested successfully on the following nodes:

- `srv-super6c-02-nvme` and `srv-super6c-03-nvme` (Super6C series).
- `srv-rk1-nvme-01`, `srv-rk1-nvme-02`, `srv-rk1-nvme-03`, and `srv-rk1-nvme-04` (RK1 series).

Details:
- The role uses `systemd-networkd` to guarantee MAC persistence, `netplan` for static IPs, and `hostnamectl` to enforce consistent hostnames.
- Prevents Cloud-Init from overwriting configurations, ensuring reliability upon reboot.
- Idempotent structure — tasks skip if configurations are already correct.
- Changes result in selective `k3s.service` or `k3s-agent.service` restarts if necessary.

Updates:
- The playbook `fix-all-nodes.yml` was revised to use `hosts: all`, enabling targeted execution with `--limit`.
- A `Makefile` target (`fix-mac-address`) was added to simplify cluster-wide execution of the role.

Behavior is confirmed stable across environments. Future adjustments should account for node-type differences (e.g., Super6C vs RK1 network interfaces named eth0/end0).