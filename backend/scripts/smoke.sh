#!/usr/bin/env bash
# Post-deploy smoke check. Exits non-zero if the deployment is not serving.
#
#   ./scripts/smoke.sh                      # production
#   ./scripts/smoke.sh http://127.0.0.1:8000
#
# Checks the two things that have actually broken here: the database path
# (via /api/health/, which runs a real query) and the WebSocket upgrade,
# which fails silently -- a WSGI server keeps HTTP working and only stops
# live status being live.
set -uo pipefail

BASE="${1:-https://caughtondash.onrender.com}"
BASE="${BASE%/}"
failures=0

note() { printf '%s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; failures=$((failures + 1)); }
pass() { printf 'ok    %s\n' "$*"; }

note "Smoke checking ${BASE}"
note

# --- health -----------------------------------------------------------------
# --max-time guards against a hung dyno holding the script open forever.
body=$(curl -sS --max-time 20 -w '\n%{http_code}' "${BASE}/api/health/" 2>/dev/null) || {
  fail "health: no response from ${BASE}"
  body=$'\n000'
}
code=$(printf '%s' "$body" | tail -n1)
payload=$(printf '%s' "$body" | sed '$d')

if [ "$code" = "200" ]; then
  pass "health: 200 ${payload}"
else
  # 503 is the endpoint working correctly and reporting a real problem, so the
  # payload is the diagnosis -- print it rather than just the status code.
  fail "health: HTTP ${code} ${payload}"
fi

# --- websocket upgrade ------------------------------------------------------
# Force HTTP/1.1: upgrade headers are meaningless over HTTP/2, so a default
# curl reports 404 whatever the server is.
ws_status=$(curl -sS -i -N --http1.1 --max-time 20 \
  -H 'Connection: Upgrade' \
  -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  "${BASE}/ws/analysis/" 2>/dev/null | head -n1)

case "$ws_status" in
  *101*) pass "websocket: ${ws_status}" ;;
  '')    fail "websocket: no response" ;;
  *)     fail "websocket: expected 101, got ${ws_status} -- live status is off, HTTP still works" ;;
esac

note
if [ "$failures" -eq 0 ]; then
  note "All checks passed."
else
  note "${failures} check(s) failed."
fi
exit "$failures"
