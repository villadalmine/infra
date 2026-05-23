#!/usr/bin/env bash
# diagnose.sh — cluster-wide snapshot for debugging sessions
# Usage: bash diagnose.sh [namespace]
set -euo pipefail

NS="${1:-}"

echo "=== Nodes ==="
kubectl get nodes -o wide

echo ""
echo "=== All pods (non-Running) ==="
kubectl get pods -A --field-selector='status.phase!=Running' 2>/dev/null || \
  kubectl get pods -A | grep -v -E 'Running|Completed' || true

echo ""
echo "=== All pods ==="
kubectl get pods -A -o wide

echo ""
echo "=== Recent events (warnings) ==="
kubectl get events -A --field-selector=type=Warning \
  --sort-by='.lastTimestamp' 2>/dev/null | tail -30

if [[ -n "$NS" ]]; then
  echo ""
  echo "=== Pods in namespace: $NS ==="
  kubectl get pods -n "$NS" -o wide

  echo ""
  echo "=== Events in namespace: $NS ==="
  kubectl get events -n "$NS" --sort-by='.lastTimestamp' | tail -20
fi

echo ""
echo "=== Node resource usage ==="
kubectl top nodes 2>/dev/null || echo "(metrics-server not available)"

echo ""
echo "=== Top pods by memory ==="
kubectl top pods -A --sort-by=memory 2>/dev/null | head -20 || echo "(metrics-server not available)"
