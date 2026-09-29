#!/usr/bin/env bash
# Do the doors behave, seen from the internet? Run after every start, tunnel
# restart or key rotation: the open path is the property that regresses silently.
#
#   ./deploy/check.sh                      # the tunnel only
#   ./deploy/check.sh <access-point-url>   # and the card through your gateway
#
# Writes what it saw to captures/check-<time>.txt as well as the terminal.
# Never prints the key.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f deploy/docker-compose.yml)
ACCESS_POINT=${1:-}

TUNNEL=$("${COMPOSE[@]}" logs tunnel 2>/dev/null |
  grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)
[ -n "$TUNNEL" ] || { echo "No tunnel hostname in the logs. Is it up?"; exit 2; }

# The first key in LABAGENT_API_KEYS, read without sourcing .env.
KEY=$(grep -E '^LABAGENT_API_KEYS=' .env | head -1 | cut -d= -f2- | cut -d, -f1 | tr -d "\"' ")
[ -n "$KEY" ] || { echo "LABAGENT_API_KEYS is empty in .env"; exit 2; }

mkdir -p captures
OUT="captures/check-$(date -u +%Y%m%dT%H%M%SZ).txt"
SEND='{"jsonrpc":"2.0","id":"check","method":"message/send","params":{"message":{"role":"user","messageId":"check","parts":[{"kind":"data","data":{"skill":"ping","v":1}}]}}}'
FAILED=0

status() { curl -s -o /dev/null -m 20 -w '%{http_code}' "$@" || echo "000"; }

expect() {  # expect <want> <label> <curl args…>
  local want=$1 label=$2; shift 2
  local got; got=$(status "$@")
  if [ "$got" = "$want" ]; then mark="PASS"; else mark="FAIL"; FAILED=1; fi
  printf '%s  %-58s want %s got %s\n' "$mark" "$label" "$want" "$got" | tee -a "$OUT"
}

{
  echo "checked $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "tunnel  $TUNNEL"
  if [ -n "$ACCESS_POINT" ]; then echo "access  $ACCESS_POINT"; fi
} | tee "$OUT"

J=(-H 'content-type: application/json')
expect 200 "tunnel: health answers" "$TUNNEL/healthz"
expect 401 "tunnel: message/send without the key is refused" -X POST "${J[@]}" -d "$SEND" "$TUNNEL/"
expect 403 "tunnel: message/send with a wrong key is refused" -X POST "${J[@]}" -H "x-api-key: not-the-key-$RANDOM" -d "$SEND" "$TUNNEL/"
expect 200 "tunnel: the card is served without the key" "$TUNNEL/.well-known/agent-card.json"
# The owner API is a different listener on the container's loopback. Through
# the tunnel its paths reach the public app, which has no such routes: 401
# without the key, 404 with it. A 200 here means the owner API is exposed.
for path in /health /approvals /peers /cmd/ping/ping; do
  expect 401 "tunnel: owner path $path without the key" "$TUNNEL$path"
  expect 404 "tunnel: owner path $path with the key is not found" -H "x-api-key: $KEY" "$TUNNEL$path"
done

if [ -n "$ACCESS_POINT" ]; then
  expect 200 "gateway: the card through your access point" "${ACCESS_POINT%/}/.well-known/agent-card.json"
fi

echo "written to $OUT"
exit $FAILED
