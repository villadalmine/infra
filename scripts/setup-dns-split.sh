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
#   - Flushes stale ARP/neighbour entries for the DNS and Gateway VIPs
#   - Restarts systemd-resolved
#   - Verifies DNS, ARP, and internet resolution works

set -euo pipefail

PIHOLE_IP="192.168.178.203"
GATEWAY_IP="192.168.178.200"
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

echo "==> Flushing stale neighbour entries"
sudo ip neigh flush to "$PIHOLE_IP" || true
sudo ip neigh flush to "$GATEWAY_IP" || true

echo "==> Checking ARP/neighbour state"
ip neigh show "$PIHOLE_IP" || true
ip neigh show "$GATEWAY_IP" || true

echo "==> Verifying..."
sleep 1

echo "==> Testing Pi-hole DNS VIP directly"
if dig +short "@${PIHOLE_IP}" "grafana.${DOMAIN}" >/tmp/cluster-home-dns-test.$$ 2>/dev/null; then
  RESULT=$(tr -d '\n' </tmp/cluster-home-dns-test.$$)
  if [[ -n "$RESULT" ]]; then
    echo "OK: ${PIHOLE_IP} answered grafana.${DOMAIN} → $RESULT"
  else
    echo "WARN: ${PIHOLE_IP} answered but returned no A record for grafana.${DOMAIN}"
  fi
else
  echo "ERROR: ${PIHOLE_IP} did not answer DNS queries for grafana.${DOMAIN}"
  echo "       Check ARP/L2 announcement for the Pi-hole VIP and the host neighbour cache."
fi
rm -f /tmp/cluster-home-dns-test.$$ || true

if dig +short "@${PIHOLE_IP}" "grafana.${DOMAIN}" 2>/dev/null | grep -qx "${GATEWAY_IP}"; then
  echo "OK: grafana.${DOMAIN} resolves to ${GATEWAY_IP} via Pi-hole"
else
  echo "WARN: grafana.${DOMAIN} did not resolve to ${GATEWAY_IP} via Pi-hole"
fi

INTERNET=$(dig +short google.com 2>/dev/null | head -1 || true)
if [[ -n "$INTERNET" ]]; then
  echo "OK: internet DNS still works (google.com → $INTERNET)"
else
  echo "WARN: internet DNS not working — check your upstream DNS"
fi

echo "==> Final connectivity hints"
echo "    DNS VIP  : ${PIHOLE_IP}"
echo "    Gateway  : ${GATEWAY_IP}"
echo "    Test DNS : dig @${PIHOLE_IP} grafana.${DOMAIN}"
echo "    Test HTTP: curl -k https://grafana.${DOMAIN}"

echo ""
echo "Done. resolvectl status for ${DOMAIN}:"
resolvectl status | grep -A3 "$DOMAIN" || true
