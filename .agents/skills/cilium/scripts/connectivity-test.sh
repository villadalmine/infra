#!/usr/bin/env bash
# connectivity-test.sh — Cilium network health check
# Usage: bash connectivity-test.sh [--full]
# --full runs cilium connectivity test (takes ~5 min, creates test namespaces)
set -euo pipefail

FULL="${1:-}"

echo "=== Cilium pods ==="
kubectl get pods -n kube-system -l k8s-app=cilium -o wide

echo ""
echo "=== Cilium status ==="
kubectl exec -n kube-system \
  "$(kubectl get pod -n kube-system -l k8s-app=cilium -o jsonpath='{.items[0].metadata.name}')" \
  -- cilium status --brief 2>/dev/null || echo "cilium status unavailable"

echo ""
echo "=== Gateway + HTTPRoutes ==="
kubectl get gateway -A
kubectl get httproute -A

echo ""
echo "=== L2 Announcements (CiliumL2AnnouncementPolicy) ==="
kubectl get ciliuml2announcementpolicy -A 2>/dev/null || echo "(no L2 policies)"

echo ""
echo "=== LoadBalancer IP pools ==="
kubectl get ciliumloadbalancerippool -A 2>/dev/null || echo "(no LB IP pools)"

echo ""
echo "=== Cilium endpoints (sample — first 10) ==="
kubectl get ciliumendpoints -A 2>/dev/null | head -12 || echo "(CiliumEndpoints not available)"

if [[ "$FULL" == "--full" ]]; then
  echo ""
  echo "=== Running full Cilium connectivity test (takes ~5 min) ==="
  cilium connectivity test --test-concurrency=1 2>/dev/null || \
    kubectl exec -n kube-system \
      "$(kubectl get pod -n kube-system -l k8s-app=cilium -o jsonpath='{.items[0].metadata.name}')" \
      -- cilium connectivity test
fi
