#!/usr/bin/env bash
# POST /triage smoke scenarios — requires the app on localhost (make run or k8s-forward).
set -euo pipefail

PORT="${PORT:-8080}"
BASE="http://localhost:${PORT}/triage"
PASSED=0
FAILED=0
TOTAL=3

print_banner() {
  echo ""
  echo "════════════════════════════════════════════════════════════════════"
  echo "  Triage smoke test — POST ${BASE}"
  echo "  Prerequisites: make k8s-forward (or make run) in another terminal"
  echo "════════════════════════════════════════════════════════════════════"
}

scenario_rule() {
  echo ""
  echo "────────────────────────────────────────────────────────────────────"
  echo "  $1"
  echo "────────────────────────────────────────────────────────────────────"
}

pretty_json() {
  python3 -c '
import json, sys

raw = sys.stdin.read()
if not raw.strip():
    sys.exit(0)
try:
    print(json.dumps(json.loads(raw), indent=4, ensure_ascii=False))
except json.JSONDecodeError:
    print(raw, end="")
'
}

indent_block() {
  sed 's/^/    /'
}

print_request_payload() {
  echo "  Request payload:"
  echo "$1" | pretty_json | indent_block
}

print_response_body() {
  echo ""
  echo "  Response body:"
  if [ -z "$1" ]; then
    echo "    (empty)"
  else
    echo "$1" | pretty_json | indent_block
  fi
}

print_mcp_tools_line() {
  echo ""
  echo "$1" | python3 -c '
import json, sys

def kb_line(value):
    if value is None:
        return "    KB MCP: no"
    if value == "mcp":
        return "    KB MCP: yes"
    if value == "in_process_fallback":
        return "    KB MCP: fallback"
    return f"    KB MCP: {value}"

raw = sys.stdin.read().strip()
if not raw:
    print("  Evidence used:")
    print("    (empty response)")
    raise SystemExit(0)
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    print("  Evidence used:")
    print("    (response is not JSON)")
    raise SystemExit(0)
if "detail" in data:
    print("  Evidence used:")
    print("    (none — validation rejected input before triage)")
    raise SystemExit(0)
es = data.get("evidence_sources") or {}
kb = es.get("knowledge_base")
k8s = es.get("kubernetes") or []
print("  Evidence used:")
print(kb_line(kb))
if k8s:
    print("    Kubernetes MCP: yes")
    for tool in k8s:
        print(f"      - {tool}")
else:
    print("    Kubernetes MCP: no")
'
}

run_scenario() {
  local title="$1"
  local body="$2"
  local expected_code="$3"
  local status_note="$4"

  scenario_rule "${title}"
  print_request_payload "${body}"

  local resp http_body http_code
  resp="$(curl -s -w "\n%{http_code}" -X POST "${BASE}" \
    -H "Content-Type: application/json" \
    -d "${body}")" || true
  http_body="$(echo "${resp}" | head -n -1)"
  http_code="$(echo "${resp}" | tail -n 1)"

  echo ""
  echo "  HTTP status: ${http_code} (expected ${expected_code}${status_note:+ — ${status_note}})"
  print_response_body "${http_body}"
  print_mcp_tools_line "${http_body}"

  echo ""
  if [ "${http_code}" = "${expected_code}" ]; then
    echo "  Result: PASS (expected HTTP ${expected_code}, got ${http_code})"
    PASSED=$((PASSED + 1))
  else
    echo "  Result: FAIL (expected HTTP ${expected_code}, got ${http_code})"
    FAILED=$((FAILED + 1))
  fi
}

print_banner

run_scenario \
  "[1/${TOTAL}] KB path — database connection refused" \
  '{"500": "DB connection refused to postgres:5432"}' \
  "200" \
  "successful triage"

run_scenario \
  "[2/${TOTAL}] K8s MCP path — CrashLoop in triage-demo (needs K8S_MCP_ENABLED + demo-up)" \
  '{"503": "No healthy upstream — pods in triage-demo namespace CrashLoopBackOff; inspect cluster state"}' \
  "200" \
  "successful triage with cluster evidence"

run_scenario \
  "[3/${TOTAL}] Invalid input — unknown error code" \
  '{"999": "This error code is not in the allowed set"}' \
  "422" \
  "validation rejected bad input; triage not run"

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "${FAILED}" -eq 0 ]; then
  echo "  Overall: ${PASSED}/${TOTAL} scenarios passed"
else
  echo "  Overall: ${PASSED}/${TOTAL} passed, ${FAILED} failed"
fi
echo "════════════════════════════════════════════════════════════════════"
echo ""

if [ "${FAILED}" -gt 0 ]; then
  exit 1
fi
