#!/usr/bin/env bash
# health-check.sh — verify full observability stack is healthy
# Usage: bash health-check.sh
set -euo pipefail

echo "=== Monitoring namespace pods ==="
kubectl get pods -n monitoring -o wide

echo ""
echo "=== Helm releases in monitoring ==="
helm list -n monitoring

echo ""
echo "=== Prometheus readiness ==="
kubectl exec -n monitoring \
  "$(kubectl get pod -n monitoring -l app.kubernetes.io/name=prometheus -o jsonpath='{.items[0].metadata.name}')" \
  -- wget -qO- http://localhost:9090/-/ready 2>/dev/null && echo "Prometheus: OK" || echo "Prometheus: NOT READY"

echo ""
echo "=== Tempo readiness ==="
kubectl exec -n monitoring tempo-0 \
  -- wget -qO- http://localhost:3200/ready 2>/dev/null && echo "" || echo "Tempo: NOT READY"

echo ""
echo "=== Loki readiness ==="
kubectl exec -n monitoring \
  "$(kubectl get pod -n monitoring -l app.kubernetes.io/name=loki -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)" \
  -- wget -qO- -H 'X-Scope-OrgId: fake' http://localhost:3100/ready 2>/dev/null && echo "" || echo "Loki: NOT READY"

echo ""
echo "=== Grafana via HTTPRoute ==="
curl -sk https://grafana.cluster.home/api/health 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('Grafana:', d.get('database','?'))" \
  || echo "Grafana: NOT REACHABLE (check DNS + HTTPRoute)"

echo ""
echo "=== Alloy pods ==="
kubectl get pods -n monitoring -l app.kubernetes.io/name=alloy -o wide

echo ""
echo "=== HTTPRoutes in monitoring ==="
kubectl get httproute -n monitoring
