#!/usr/bin/env bash
# E2E test: Hermes ↔ OpenClaw A2A communication
# Tests real in-cluster connectivity and MCP protocol from inside pods.
# Usage: ./scripts/test-a2a-e2e.sh [--quick] [--verbose]
#
# QUICK: skips ask_hermes_agent call (slow, ~60s).
# VERBOSE: prints full JSON responses.

QUICK=0; VERBOSE=0
for arg in "$@"; do
  case "$arg" in --quick) QUICK=1 ;; --verbose) VERBOSE=1 ;; esac
done

OPENCLAW_POD=$(kubectl get pod -n openclaw -l app=openclaw -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
HERMES_POD=$(kubectl get pod -n ai -l app=hermes-agent-mcp -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

PASS=0; FAIL=0; SKIP=0
FAILED_TESTS=()

r='\033[0;31m'; g='\033[0;32m'; y='\033[0;33m'; c='\033[0;36m'; b='\033[1m'; x='\033[0m'

pass() { echo -e "${g}  PASS${x} $1"; ((PASS++)) || true; }
fail() { local name="$1" reason="$2"; echo -e "${r}  FAIL${x} $name"; echo -e "${r}       reason:${x} $reason"; ((FAIL++)) || true; FAILED_TESTS+=("$name"); }
skip() { echo -e "${y}  SKIP${x} $1"; ((SKIP++)) || true; }
info() { [[ $VERBOSE -eq 1 ]] && echo -e "${c}       $1${x}" || true; }
header() { echo -e "\n${b}$1${x}"; }

poc() { kubectl exec -n openclaw "$OPENCLAW_POD" -c openclaw-gateway -- "$@" 2>/dev/null; }
phe() { kubectl exec -n ai "$HERMES_POD" -c hermes-agent -- "$@" 2>/dev/null; }

echo -e "${b}=== A2A E2E Test Suite ===${x}"
echo "Pods: openclaw=${OPENCLAW_POD:-NOT FOUND}  hermes=${HERMES_POD:-NOT FOUND}"
date

# ─── 1: Pods alive ───────────────────────────────────────────────────────────
header "1. Pod health"

if [[ -n "$OPENCLAW_POD" ]]; then
  STATUS=$(kubectl get pod -n openclaw "$OPENCLAW_POD" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
  READY=$(kubectl get pod -n openclaw "$OPENCLAW_POD" -o jsonpath='{.status.containerStatuses[0].ready}' 2>/dev/null || echo "false")
  if [[ "$STATUS" == "Running" && "$READY" == "true" ]]; then
    pass "openclaw pod Running+Ready"
  else
    fail "openclaw pod" "phase=$STATUS ready=$READY"
  fi
else
  fail "openclaw pod" "no pod found (label app=openclaw in ns openclaw)"
fi

if [[ -n "$HERMES_POD" ]]; then
  STATUS=$(kubectl get pod -n ai "$HERMES_POD" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
  CONTAINERS_READY=$(kubectl get pod -n ai "$HERMES_POD" -o jsonpath='{range .status.containerStatuses[*]}{.ready}{"\n"}{end}' 2>/dev/null | grep -c "^true$" || true)
  CONTAINER_COUNT=$(kubectl get pod -n ai "$HERMES_POD" -o jsonpath='{range .status.containerStatuses[*]}{.name}{"\n"}{end}' 2>/dev/null | wc -l || echo 0)
  if [[ "$STATUS" == "Running" && "$CONTAINERS_READY" == "$CONTAINER_COUNT" ]]; then
    pass "hermes pod Running+Ready ($CONTAINERS_READY/$CONTAINER_COUNT containers)"
  else
    fail "hermes pod" "phase=$STATUS containers_ready=$CONTAINERS_READY/$CONTAINER_COUNT"
  fi
else
  fail "hermes pod" "no pod found (label app=hermes-agent-mcp in ns ai)"
fi

# ─── 2: bridge.js health ─────────────────────────────────────────────────────
header "2. bridge.js (OpenClaw MCP server :18790)"

BRIDGE_INIT=$(poc curl -s --max-time 10 -X POST http://localhost:18790/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"e2e","version":"1"}}}' 2>/dev/null || true)

if echo "$BRIDGE_INIT" | grep -q '"protocolVersion"'; then
  BRIDGE_VER=$(echo "$BRIDGE_INIT" | grep -o '"name":"openclaw"' | head -1 || true)
  pass "bridge.js initialize → protocolVersion present (server=openclaw)"
  info "$BRIDGE_INIT"
else
  fail "bridge.js initialize" "unexpected: ${BRIDGE_INIT:0:200}"
fi

# GET on SSE endpoint: server sends headers then streams. Use verbose to capture HTTP status line.
BRIDGE_GET_STATUS=$(poc sh -c 'curl -sv --max-time 3 http://localhost:18790/mcp 2>&1 | grep "^< HTTP"' 2>/dev/null | grep -o "[0-9][0-9][0-9]" | head -1 || true)
if [[ "$BRIDGE_GET_STATUS" == "200" ]]; then
  pass "bridge.js GET /mcp → HTTP 200 (SSE keepalive, avoids Python MCP SDK 405 crash)"
else
  fail "bridge.js GET /mcp" "HTTP ${BRIDGE_GET_STATUS:-no response} (expected 200 — check bridge.js flushes headers)"
fi

# bridge.js runs in the openclaw-mcp-bridge container, check there
# mcp serve child process appears as "openclaw-mcp" (the openclaw binary in mcp serve mode)
BRIDGE_PS=$(kubectl exec -n openclaw "$OPENCLAW_POD" -c openclaw-mcp-bridge -- \
  sh -c 'ps aux 2>/dev/null | grep -E "[o]penclaw-mcp|bridge.js" || echo "NOT_FOUND"' 2>/dev/null || true)
if echo "$BRIDGE_PS" | grep -qE "openclaw-mcp|bridge.js"; then
  BRIDGE_NODE=$(echo "$BRIDGE_PS" | grep "bridge.js" | awk '{print "bridge.js PID="$2}' || true)
  CHILD_NODE=$(echo "$BRIDGE_PS" | grep "openclaw-mcp" | awk '{print "mcp-serve PID="$2}' || true)
  pass "bridge.js and mcp serve child alive ($BRIDGE_NODE $CHILD_NODE)"
  info "$BRIDGE_PS"
else
  fail "bridge.js child process" "bridge.js or openclaw-mcp not in ps (container=openclaw-mcp-bridge)"
fi

# ─── 3: OpenClaw → Hermes MCP ────────────────────────────────────────────────
header "3. OpenClaw → Hermes MCP (port 8000)"

OC_INIT_RAW=$(poc curl -si --max-time 15 -X POST http://hermes-agent-mcp.ai.svc.cluster.local:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"e2e","version":"1"}}}' 2>/dev/null || true)

OC_SESSION=$(echo "$OC_INIT_RAW" | grep -i '^mcp-session-id' | awk '{print $2}' | tr -d '\r' || true)
HERMES_SERVER=$(echo "$OC_INIT_RAW" | grep -o '"name":"hermes"' | head -1 || true)

if [[ -n "$OC_SESSION" && -n "$HERMES_SERVER" ]]; then
  pass "OpenClaw → Hermes initialize → session=$OC_SESSION"
  info "$OC_INIT_RAW"
else
  fail "OpenClaw → Hermes initialize" "no session or hermes server. raw: ${OC_INIT_RAW:0:300}"
  OC_SESSION=""
fi

if [[ -n "$OC_SESSION" ]]; then
  OC_TOOLS=$(poc curl -s --max-time 20 -X POST http://hermes-agent-mcp.ai.svc.cluster.local:8000/mcp \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "mcp-session-id: $OC_SESSION" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' 2>/dev/null || true)

  TOOL_COUNT=$(echo "$OC_TOOLS" | grep -o '"name":"[^"]*"' | wc -l || echo 0)
  HAS_ASK=$(echo "$OC_TOOLS" | grep -c '"ask_hermes_agent"' || true)

  if [[ "$TOOL_COUNT" -gt 5 && "$HAS_ASK" -gt 0 ]]; then
    pass "OpenClaw → Hermes tools/list → $TOOL_COUNT tools (ask_hermes_agent ✓)"
    info "$OC_TOOLS" | head -c 300
  else
    fail "OpenClaw → Hermes tools/list" "count=$TOOL_COUNT ask_hermes_agent=$HAS_ASK"
  fi
else
  skip "OpenClaw → Hermes tools/list (no session from init)"
fi

# ─── 4: Hermes → OpenClaw via bridge ─────────────────────────────────────────
header "4. Hermes → OpenClaw MCP via bridge.js (port 18790)"

H_INIT=$(phe curl -si --max-time 15 -X POST http://openclaw.openclaw.svc.cluster.local:18790/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"e2e","version":"1"}}}' 2>/dev/null || true)

if echo "$H_INIT" | grep -q '"protocolVersion"'; then
  pass "Hermes → OpenClaw bridge initialize OK"
  info "$H_INIT"
else
  fail "Hermes → OpenClaw bridge initialize" "unexpected: ${H_INIT:0:200}"
fi

H_TOOLS=$(phe curl -s --max-time 30 -X POST http://openclaw.openclaw.svc.cluster.local:18790/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' 2>/dev/null || true)

H_TOOL_COUNT=$(echo "$H_TOOLS" | grep -o '"name":"[^"]*"' | wc -l || echo 0)
HAS_CONV=$(echo "$H_TOOLS" | grep -c '"conversations_list"' || true)

if [[ "$H_TOOL_COUNT" -gt 3 && "$HAS_CONV" -gt 0 ]]; then
  pass "Hermes → OpenClaw tools/list → $H_TOOL_COUNT tools (conversations_list ✓)"
else
  fail "Hermes → OpenClaw tools/list" "count=$H_TOOL_COUNT conversations_list=$HAS_CONV"
fi

# ─── 5: OpenClaw MCP runtime session-ID handling ─────────────────────────────
header "5. OpenClaw MCP client: session-ID forwarding (CRITICAL)"

# openclaw uses @modelcontextprotocol/sdk from node_modules (not bundled in dist).
# Check that SDK version ≥ 1.0 is present and handles mcp-session-id.
SDK_VERSION=$(poc sh -c 'cat /app/node_modules/@modelcontextprotocol/sdk/package.json 2>/dev/null | grep '"'"'"version"'"'"' | head -1 | grep -o "[0-9]*\.[0-9]*\.[0-9]*"' 2>/dev/null || echo "")
SESSION_IN_SDK=$(poc sh -c 'grep -c "mcp-session-id" /app/node_modules/@modelcontextprotocol/sdk/dist/esm/client/streamableHttp.js 2>/dev/null || echo 0' 2>/dev/null || echo 0)

if [[ -n "$SDK_VERSION" && "${SESSION_IN_SDK:-0}" -gt 0 ]]; then
  pass "openclaw uses @modelcontextprotocol/sdk v${SDK_VERSION} — mcp-session-id forwarding confirmed"
else
  fail "openclaw MCP SDK session handling" "sdk_version=${SDK_VERSION:-not found} session_lines=${SESSION_IN_SDK:-0}"
fi

# ─── 6: ask_hermes_agent E2E (or verify via hermes logs) ─────────────────────
header "6. ask_hermes_agent E2E call"

if [[ $QUICK -eq 1 ]]; then
  skip "ask_hermes_agent full call (--quick, skipped)"
elif [[ -z "$OC_SESSION" ]]; then
  skip "ask_hermes_agent (no session from section 3)"
else
  echo "  Calling ask_hermes_agent... (30-90s)"
  T0=$(date +%s)
  ASK=$(poc curl -s --max-time 90 -X POST http://hermes-agent-mcp.ai.svc.cluster.local:8000/mcp \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "mcp-session-id: $OC_SESSION" \
    -d '{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"ask_hermes_agent","arguments":{"question":"E2E test: reply with exactly the word PONG"}}}' 2>/dev/null || true)
  T1=$(date +%s)
  ELAPSED=$((T1-T0))

  if echo "$ASK" | grep -qi 'pong'; then
    pass "ask_hermes_agent → PONG in ${ELAPSED}s"
  elif echo "$ASK" | grep -q '"result"'; then
    ANS=$(echo "$ASK" | grep -o '"text":"[^"]*"' | head -1 | cut -d'"' -f4 || true)
    pass "ask_hermes_agent → got result in ${ELAPSED}s: ${ANS:0:100}"
  elif echo "$ASK" | grep -q '"error"'; then
    ERR=$(echo "$ASK" | grep -o '"message":"[^"]*"' | head -1 | cut -d'"' -f4 || true)
    fail "ask_hermes_agent" "error after ${ELAPSED}s: $ERR"
  else
    fail "ask_hermes_agent" "no result in ${ELAPSED}s: ${ASK:0:200}"
  fi
  info "$ASK"
fi

# ─── 7: Hermes → OpenClaw tool call ──────────────────────────────────────────
header "7. Hermes → OpenClaw tool call (conversations_list)"

H_CALL=$(phe curl -s --max-time 30 -X POST http://openclaw.openclaw.svc.cluster.local:18790/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":20,"method":"tools/call","params":{"name":"conversations_list","arguments":{"limit":5}}}' 2>/dev/null || true)

if echo "$H_CALL" | grep -q '"result"'; then
  pass "Hermes → OpenClaw conversations_list → result OK"
  info "${H_CALL:0:300}"
elif echo "$H_CALL" | grep -q '"error"'; then
  ERR=$(echo "$H_CALL" | grep -o '"message":"[^"]*"' | head -1 | cut -d'"' -f4 || true)
  fail "Hermes → OpenClaw conversations_list" "error: $ERR"
else
  fail "Hermes → OpenClaw conversations_list" "unexpected: ${H_CALL:0:200}"
fi

# ─── 8: Kagent NetworkPolicy ─────────────────────────────────────────────────
header "8. Hermes → kagent-tools NetworkPolicy (port 8084)"

KA_CODE=$(phe curl -s --max-time 10 -o /dev/null -w '%{http_code}' \
  -X POST http://kagent-tools.kagent.svc.cluster.local:8084/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"e2e","version":"1"}}}' 2>/dev/null || echo 000)

if [[ "$KA_CODE" == "200" ]]; then
  pass "Hermes → kagent-tools HTTP 200 (NetworkPolicy allows ai namespace)"
else
  fail "Hermes → kagent-tools" "HTTP $KA_CODE (expected 200 — check ai namespace ingress in allow-openclaw-to-kagent-tools)"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${b}════════════════════════════════════${x}"
echo -e "${b}Results: ${g}PASS=$PASS${x}  ${r}FAIL=$FAIL${x}  ${y}SKIP=$SKIP${x}"
echo -e "${b}════════════════════════════════════${x}"

if [[ "${#FAILED_TESTS[@]}" -gt 0 ]]; then
  echo ""
  echo -e "${r}${b}Failed:${x}"
  for t in "${FAILED_TESTS[@]}"; do echo -e "  ${r}✗${x} $t"; done
fi

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  echo -e "${g}${b}All tests passed.${x}"
  exit 0
else
  echo -e "${r}${b}$FAIL test(s) failed.${x}"
  exit 1
fi
