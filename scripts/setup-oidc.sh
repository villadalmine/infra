#!/usr/bin/env bash
# setup-oidc.sh — Completa la configuración OIDC tras crear el GitHub OAuth App
# =============================================================================
# Corre este script UNA SOLA VEZ después de crear la GitHub OAuth App:
#   Name:         Leloir Dex
#   Homepage URL: https://leloir.cluster.home
#   Callback URL: https://dex.cluster.home/callback
#
# Uso:
#   ./scripts/setup-oidc.sh <GITHUB_CLIENT_ID> <GITHUB_CLIENT_SECRET>
#
# Ejemplo:
#   ./scripts/setup-oidc.sh Iv1.abc123def456 abcdef1234567890abcdef1234567890abcdef12

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Argumentos ────────────────────────────────────────────────────────────────
if [[ $# -lt 2 ]]; then
  echo "Uso: $0 <GITHUB_CLIENT_ID> <GITHUB_CLIENT_SECRET>"
  echo ""
  echo "Encontrás estos valores en:"
  echo "  https://github.com/settings/developers → 'Leloir Dex'"
  exit 1
fi

GITHUB_CLIENT_ID="$1"
GITHUB_CLIENT_SECRET="$2"

echo "=== Leloir OIDC Setup ==="
echo ""

# ── Validar que los secrets.yml existen ──────────────────────────────────────
DEX_SECRETS="$INFRA_DIR/roles/install-dex/defaults/secrets.yml"
LELOIR_SECRETS="$INFRA_DIR/roles/install-leloir/defaults/secrets.yml"

if [[ ! -f "$DEX_SECRETS" ]]; then
  echo "ERROR: $DEX_SECRETS no existe. Reclonés el repo o corriste make setup?"
  exit 1
fi

# ── Inyectar valores GitHub en install-dex/defaults/secrets.yml ──────────────
echo "[1/4] Actualizando $DEX_SECRETS ..."
sed -i \
  -e "s|^dex_github_client_id:.*|dex_github_client_id: \"${GITHUB_CLIENT_ID}\"|" \
  -e "s|^dex_github_client_secret:.*|dex_github_client_secret: \"${GITHUB_CLIENT_SECRET}\"|" \
  "$DEX_SECRETS"

echo "      ✓ dex_github_client_id y dex_github_client_secret actualizados"

# ── Verificar que el shared secret ya está generado ──────────────────────────
echo "[2/4] Verificando shared secret Dex ↔ Leloir ..."
DEX_CLIENT_SECRET=$(grep "dex_leloir_client_secret:" "$DEX_SECRETS" | awk '{print $2}' | tr -d '"')
LELOIR_CLIENT_SECRET=$(grep "leloir_oidc_client_secret:" "$LELOIR_SECRETS" | awk '{print $2}' | tr -d '"')

if [[ -z "$DEX_CLIENT_SECRET" || -z "$LELOIR_CLIENT_SECRET" ]]; then
  echo "ERROR: shared secret vacío. Verificar secrets.yml"
  exit 1
fi

if [[ "$DEX_CLIENT_SECRET" != "$LELOIR_CLIENT_SECRET" ]]; then
  echo "ERROR: dex_leloir_client_secret != leloir_oidc_client_secret"
  echo "  Dex:   $DEX_CLIENT_SECRET"
  echo "  Leloir: $LELOIR_CLIENT_SECRET"
  echo "Editar manualmente para que coincidan."
  exit 1
fi

echo "      ✓ Shared secret coincide en ambos roles"

# ── Cambiar auth_mode a oidc ──────────────────────────────────────────────────
echo "[3/4] Activando auth.mode=oidc en install-leloir/defaults/main.yml ..."
LELOIR_MAIN="$INFRA_DIR/roles/install-leloir/defaults/main.yml"
sed -i 's|^leloir_auth_mode:.*|leloir_auth_mode: "oidc"|' "$LELOIR_MAIN"
echo "      ✓ leloir_auth_mode: oidc"

# ── Deploy ────────────────────────────────────────────────────────────────────
echo ""
echo "[4/4] Ejecutando make leloir-oidc ..."
echo "      (Dex primero, luego Leloir en modo oidc con ArgoCD appset)"
echo ""
cd "$INFRA_DIR"
make leloir-oidc

echo ""
echo "=== Verificar ==="
echo ""
echo "  # Pods"
echo "  kubectl get pods -n dex"
echo "  kubectl get pods -n leloir"
echo ""
echo "  # OIDC discovery"
echo "  curl -sk https://dex.cluster.home/.well-known/openid-configuration | jq .issuer"
echo ""
echo "  # Leloir"
echo "  curl -s https://leloir.cluster.home/healthz"
echo "  curl -s https://leloir.cluster.home/auth/whoami  # debe devolver 401"
echo ""
echo "  # Login (abrir en browser)"
echo "  open https://leloir.cluster.home"
