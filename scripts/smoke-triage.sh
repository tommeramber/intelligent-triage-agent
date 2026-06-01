#!/usr/bin/env bash
# POST /triage smoke scenarios — requires the app on localhost (make run or k8s-forward).
set -euo pipefail

PORT="${PORT:-8080}"
BASE="http://localhost:${PORT}/triage"
PASSED=0
FAILED=0
TOTAL=6

print_banner() {
  echo ""
  echo "════════════════════════════════════════════════════════════════════"
  echo "  Triage smoke test — POST ${BASE}"
  echo "  Prerequisites: make k8s-forward (or make run) in another terminal"
  echo "  K8s namespace checks: K8S_MCP_ENABLED + make demo-up for full coverage"
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
    detail = data["detail"]
    if isinstance(detail, str) and "content moderation" in detail.lower():
        print("    (none — input rejected by content moderation; triage did not run)")
    else:
        print("    (none — validation rejected input before triage)")
    raise SystemExit(0)
es = data.get("evidence_sources") or {}
kb = es.get("knowledge_base")
k8s = es.get("kubernetes") or {}
if isinstance(k8s, list):
    invoked = k8s
    obtained = bool(k8s)
    k8s_status = "obtained" if obtained else "not_invoked"
    k8s_message = None
else:
    invoked = k8s.get("invoked") or []
    obtained = bool(k8s.get("evidence_obtained"))
    k8s_status = k8s.get("status") or ("obtained" if obtained else ("not_invoked" if not invoked else "no_accessible_evidence"))
    k8s_message = k8s.get("message")
kw = data.get("kb_keyword_match")
print("  Evidence used:")
print(kb_line(kb))
if kw is False:
    print("    KB keywords: no relevant match in description")
elif kw is True:
    print("    KB keywords: matched")
useful = data.get("docs_useful") or []
consulted = data.get("docs_consulted") or []
if useful:
    print("    KB useful: " + ", ".join(useful))
if consulted and consulted != useful:
    print("    KB consulted: " + ", ".join(consulted))
if invoked:
    if k8s_status == "obtained":
        print("    Kubernetes MCP: yes (workload evidence obtained)")
    elif k8s_status == "access_denied":
        print("    Kubernetes MCP: invoked — namespace access denied / not opted in")
    elif k8s_status == "no_accessible_evidence":
        print("    Kubernetes MCP: invoked — no workload evidence for requested target")
    else:
        print("    Kubernetes MCP: invoked")
    if k8s_message:
        print(f"      {k8s_message}")
    for tool in invoked:
        print(f"      - {tool}")
else:
    print("    Kubernetes MCP: no")
'
}

# Prints structured K8s PASS/FAIL expectations (exit 0 = met, 1 = not met).
# Modes: allowed — triage-demo workload evidence; denied — blocked namespace guard.
check_k8s_expectations() {
  local mode="$1"
  local body="$2"
  K8S_CHECK_MODE="${mode}" python3 -c '
import json, os, sys

mode = os.environ.get("K8S_CHECK_MODE", "")
raw = sys.stdin.read().strip()
print("")
print("  K8s expectations:")

def fail(msg):
    print(f"    FAIL: {msg}")
    raise SystemExit(1)

def pass_msg(msg):
    print(f"    PASS: {msg}")
    raise SystemExit(0)

def note(msg):
    print(f"    NOTE: {msg}")

if not raw:
    fail("empty response body")
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    fail("response is not JSON")

if "detail" in data:
    fail("validation error — triage did not run")

k8s = (data.get("evidence_sources") or {}).get("kubernetes") or {}
if isinstance(k8s, list):
    invoked = k8s
    obtained = bool(k8s)
    status = "obtained" if obtained else ("not_invoked" if not invoked else "no_accessible_evidence")
else:
    invoked = k8s.get("invoked") or []
    obtained = bool(k8s.get("evidence_obtained"))
    status = k8s.get("status") or (
        "obtained" if obtained else ("not_invoked" if not invoked else "no_accessible_evidence")
    )

if mode == "allowed":
    print("    - Allowed namespace (triage-demo): workload evidence when K8s MCP is used")
    print("    - evidence_obtained true and/or status=obtained (not LLM summary text)")
    if not invoked:
        note("Kubernetes MCP not invoked — enable K8S_MCP_ENABLED and run make demo-up to exercise this check")
        pass_msg("HTTP OK; K8s namespace check skipped (MCP not used)")
    if status == "access_denied":
        fail(f"triage-demo should be allowed, got status={status!r}")
    if obtained or status == "obtained":
        pass_msg(f"workload evidence for allowed namespace (status={status!r}, evidence_obtained={obtained})")
    fail(
        f"expected workload evidence in triage-demo (status={status!r}, evidence_obtained={obtained}); "
        "ensure make demo-up and demo pods are present"
    )

if mode == "denied":
    print("    - Blocked / non-opted-in namespace (e.g. kube-system): no workload evidence")
    print("    - evidence_obtained must be false")
    print("    - status access_denied or no_accessible_evidence when K8s tools run")
    if obtained:
        fail("evidence_obtained must be false for a blocked namespace probe")
    if not invoked:
        note("Kubernetes MCP not invoked — agent may skip cluster tools; re-run or check K8S_MCP_ENABLED")
        pass_msg("HTTP OK; K8s blocked-namespace check skipped (MCP not used)")
    if status in ("access_denied", "no_accessible_evidence"):
        pass_msg(f"namespace guard behaved as expected (status={status!r}, evidence_obtained=false)")
    if status == "obtained":
        fail("blocked namespace must not yield workload evidence (status=obtained)")
    fail(f"unexpected kubernetes status={status!r} with evidence_obtained=false")

if mode == "not_invoked":
    print("    - Vague cluster-wide input: Kubernetes MCP must not run")
    print("    - evidence_sources.kubernetes invoked must be empty")
    if invoked:
        fail(f"Kubernetes MCP must be withheld for vague input, invoked={invoked!r}")
    pass_msg("Kubernetes MCP correctly withheld (not invoked)")

fail(f"unknown K8S_CHECK_MODE={mode!r}")
' <<<"${body}"
}

# Exit 0 when 400 body is a content-moderation rejection (no triage evidence).
check_moderation_expectations() {
  local body="$1"
  python3 -c '
import json, sys

raw = sys.stdin.read().strip()
print("")
print("  Moderation expectations:")

def fail(msg):
    print(f"    FAIL: {msg}")
    raise SystemExit(1)

def pass_msg(msg):
    print(f"    PASS: {msg}")
    raise SystemExit(0)

if not raw:
    fail("empty response body")
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    fail("response is not JSON")

detail = data.get("detail")
if not isinstance(detail, str):
    fail(f"expected string detail, got {detail!r}")
if "content moderation" not in detail.lower():
    fail(f"expected content moderation detail, got {detail!r}")
if "violence" not in detail.lower():
    fail(f"expected violence category in detail, got {detail!r}")
if data.get("evidence_sources") or data.get("summary"):
    fail("triage must not run — response must not include triage fields")
pass_msg("local moderation guard rejected input before triage")
' <<<"${body}"
}

run_scenario() {
  local title="$1"
  local body="$2"
  local expected_code="$3"
  local status_note="$4"
  local k8s_check="${5:-}"
  local moderation_check="${6:-}"

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

  local http_ok=0 k8s_ok=1 mod_ok=1
  if [ "${http_code}" = "${expected_code}" ]; then
    http_ok=1
  fi

  local k8s_evaluated=0
  if [ -n "${k8s_check}" ] && [ "${http_ok}" -eq 1 ]; then
    k8s_evaluated=1
    if check_k8s_expectations "${k8s_check}" "${http_body}"; then
      k8s_ok=1
    else
      k8s_ok=0
    fi
  elif [ -n "${k8s_check}" ]; then
    echo ""
    echo "  K8s expectations:"
    echo "    SKIP: HTTP status mismatch — structured K8s checks not evaluated"
  fi

  local mod_evaluated=0
  if [ -n "${moderation_check}" ] && [ "${http_ok}" -eq 1 ]; then
    mod_evaluated=1
    if check_moderation_expectations "${http_body}"; then
      mod_ok=1
    else
      mod_ok=0
    fi
  elif [ -n "${moderation_check}" ]; then
    echo ""
    echo "  Moderation expectations:"
    echo "    SKIP: HTTP status mismatch — moderation checks not evaluated"
  fi

  echo ""
  if [ "${http_ok}" -eq 1 ] && [ "${k8s_ok}" -eq 1 ] && [ "${mod_ok}" -eq 1 ]; then
    if [ "${k8s_evaluated}" -eq 1 ]; then
      echo "  Result: PASS (HTTP ${expected_code} + K8s namespace expectations met)"
    elif [ "${mod_evaluated}" -eq 1 ]; then
      echo "  Result: PASS (HTTP ${expected_code} + content moderation rejection as expected)"
    else
      echo "  Result: PASS (expected HTTP ${expected_code}, got ${http_code})"
    fi
    PASSED=$((PASSED + 1))
  else
    if [ "${http_ok}" -eq 0 ]; then
      echo "  Result: FAIL (expected HTTP ${expected_code}, got ${http_code})"
    elif [ "${mod_ok}" -eq 0 ]; then
      echo "  Result: FAIL (HTTP ${expected_code} but moderation expectations not met)"
    else
      echo "  Result: FAIL (HTTP OK but K8s namespace expectations not met)"
    fi
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
  "[2/${TOTAL}] K8s allowed namespace — triage-demo workload evidence" \
  '{"503": "No healthy upstream — pods in triage-demo namespace CrashLoopBackOff; inspect cluster state"}' \
  "200" \
  "successful triage; allowed namespace should yield workload evidence when demo is up" \
  "allowed"

run_scenario \
  "[3/${TOTAL}] K8s blocked namespace — kube-system must not yield workload evidence" \
  '{"503": "Upstream failure — list pods and events in kube-system namespace (coredns, metrics-server)"}' \
  "200" \
  "successful triage; blocked/non-opted-in namespace must not set evidence_obtained" \
  "denied"

run_scenario \
  "[4/${TOTAL}] K8s withheld — vague cluster input must not invoke MCP" \
  '{"500": "node is down in cluster — widespread scheduling failures"}' \
  "200" \
  "successful triage; no namespace/pod/workload named — K8s tools gated off" \
  "not_invoked"

run_scenario \
  "[5/${TOTAL}] Content moderation — local violence guard (demo-safe)" \
  '{"500": "death"}' \
  "400" \
  "input rejected by content moderation; triage not run" \
  "" \
  "yes"

run_scenario \
  "[6/${TOTAL}] Invalid input — unknown error code" \
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
