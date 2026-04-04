#!/usr/bin/env bash
# setup-dns-split.sh
# Configures split-DNS on the workstation so *.cluster.home resolves via Pi-hole.
#
# Requirements: Fedora Silverblue / Sericea with systemd-resolved (default).
# Run this on the HOST — not inside a toolbox container.
#
# What it does:
#   - Creates /etc/systemd/resolved.conf.d/cluster-home.conf
#   - Routes *.cluster.home queries to Pi-hole @ 192.168.178.203
#   - Restarts systemd-resolved
#   - Verifies resolution works

set -euo pipefail

PIHOLE_IP="192.168.178.203"
DOMAIN="cluster.home"
DROP_IN="/etc/systemd/resolved.conf.d/cluster-home.conf"

# Guard: must run on host, not inside toolbox
if [[ -L /etc/resolv.conf && "$(readlink /etc/resolv.conf)" == "/run/host/etc/resolv.conf" ]]; then
  echo "ERROR: You are inside a toolbox container."
  echo "       Run this script on the host OS, not inside toolbox."
  exit 1
fi

# Guard: systemd-resolved must be active
if ! systemctl is-active --quiet systemd-resolved; then
  echo "ERROR: systemd-resolved is not active."
  echo "       This script requires systemd-resolved (default on Fedora Silverblue)."
  exit 1
fi

echo "==> Writing $DROP_IN"
sudo mkdir -p /etc/systemd/resolved.conf.d
sudo tee "$DROP_IN" > /dev/null <<EOF
[Resolve]
DNS=${PIHOLE_IP}
Domains=~${DOMAIN}
EOF

echo "==> Restarting systemd-resolved"
sudo systemctl restart systemd-resolved

echo "==> Verifying..."
sleep 1

RESULT=$(dig +short "grafana.${DOMAIN}" 2>/dev/null || true)
if [[ -n "$RESULT" ]]; then
  echo "OK: grafana.${DOMAIN} → $RESULT"
else
  echo "WARN: grafana.${DOMAIN} did not resolve — cluster may be down or Pi-hole unreachable"
fi

INTERNET=$(dig +short google.com 2>/dev/null | head -1 || true)
if [[ -n "$INTERNET" ]]; then
  echo "OK: internet DNS still works (google.com → $INTERNET)"
else
  echo "WARN: internet DNS not working — check your upstream DNS"
fi

echo ""
echo "Done. resolvectl status for ${DOMAIN}:"
resolvectl status | grep -A3 "$DOMAIN" || true
