#!/usr/bin/env bash
# Do the doors behave, seen from the internet? Run after every deploy, restart
# or key rotation: an open door is the property that regresses silently.
#
#   ./deploy/check.sh <agent-url> [<owner-url>] [<access-point-url>]
#   ./deploy/check.sh                  # the local quick tunnel (docker compose), public door only
#
# On Fly:  ./deploy/check.sh https://<app>.fly.dev https://<app>.fly.dev:8443
#
# <agent-url> is where your host serves the A2A port (8080); <owner-url> is
# where it serves the owner MCP port (8081). Both are what your gateway's
# external targets point at, not your access points. Keys are read from .env
# (or $LABAGENT_ENV_FILE) and never printed. Writes what it saw to captures/check-<time>.txt.
set -euo pipefail
cd "$(dirname "$0")/.."

AGENT=${1:-}
OWNER=${2:-}
ACCESS_POINT=${3:-}

if [ -z "$AGENT" ]; then
  AGENT=$(docker compose -f deploy/docker-compose.yml logs tunnel 2>/dev/null |
    grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)
  [ -n "$AGENT" ] || { echo "No URL given and no tunnel hostname in the logs. Usage: $0 <agent-url> [<owner-url>]"; exit 2; }
fi
AGENT=${AGENT%/}
OWNER=${OWNER%/}

# The first key of each set, read from .env without sourcing it.
ENV_FILE=${LABAGENT_ENV_FILE:-.env}
first_key() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | cut -d, -f1 | tr -d "\"' " || true; }
KEY=$(first_key LABAGENT_API_KEYS)
OWNER_KEY=$(first_key LABAGENT_OWNER_KEYS)
[ -n "$KEY" ] || { echo "LABAGENT_API_KEYS is empty in $ENV_FILE"; exit 2; }
[ -z "$OWNER" ] || [ -n "$OWNER_KEY" ] || { echo "LABAGENT_OWNER_KEYS is empty in $ENV_FILE"; exit 2; }

mkdir -p captures
OUT="captures/check-$(date -u +%Y%m%dT%H%M%SZ).txt"
SEND='{"jsonrpc":"2.0","id":"check","method":"message/send","params":{"message":{"role":"user","messageId":"check","parts":[{"kind":"text","text":"ping"}]}}}'
LIST='{"jsonrpc":"2.0","id":"check","method":"tools/list"}'
FAILED=0

status() { curl -s -o /dev/null -m 20 -w '%{http_code}' "$@" || echo "000"; }

expect() {  # expect <want> <label> <curl args…>; a want of !200 means anything but 200
  local want=$1 label=$2; shift 2
  local got; got=$(status "$@")
  if [ "$want" = "!200" ]; then
    if [ "$got" != "200" ] && [ "$got" != "000" ]; then mark="PASS"; else mark="FAIL"; FAILED=1; fi
  elif [ "$got" = "$want" ]; then mark="PASS"; else mark="FAIL"; FAILED=1; fi
  printf '%s  %-62s want %s got %s\n' "$mark" "$label" "$want" "$got" | tee -a "$OUT"
}

{
  echo "checked $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "agent   $AGENT"
  if [ -n "$OWNER" ]; then echo "owner   $OWNER"; fi
  if [ -n "$ACCESS_POINT" ]; then echo "access  $ACCESS_POINT"; fi
} | tee "$OUT"

J=(-H 'content-type: application/json')
expect 200 "public: health answers" "$AGENT/healthz"
expect 200 "public: the card is served without a key" "$AGENT/.well-known/agent-card.json"
expect 401 "public: message/send without a key is refused" -X POST "${J[@]}" -d "$SEND" "$AGENT/"
expect 403 "public: message/send with a wrong key is refused" -X POST "${J[@]}" -H "x-api-key: not-the-key-$RANDOM" -d "$SEND" "$AGENT/"
# The owner's tools are not on the public port, whatever key is presented.
expect '!200' "public: no owner tools here, even with the inbound key" -X POST "${J[@]}" -H "x-api-key: $KEY" -d "$LIST" "$AGENT/mcp"

if [ -n "$OWNER" ]; then
  expect 200 "owner: health answers" "$OWNER/healthz"
  expect 401 "owner: tools/list without a key is refused" -X POST "${J[@]}" -d "$LIST" "$OWNER/mcp"
  expect 403 "owner: tools/list with a wrong key is refused" -X POST "${J[@]}" -H "x-api-key: not-the-key-$RANDOM" -d "$LIST" "$OWNER/mcp"
  expect 403 "owner: the inbound key does not open it" -X POST "${J[@]}" -H "x-api-key: $KEY" -d "$LIST" "$OWNER/mcp"
  expect 200 "owner: the owner key opens it" -X POST "${J[@]}" -H "x-api-key: $OWNER_KEY" -d "$LIST" "$OWNER/mcp"
  expect 403 "public: the owner key does not open the public door" -X POST "${J[@]}" -H "x-api-key: $OWNER_KEY" -d "$SEND" "$AGENT/"
fi

if [ -n "$ACCESS_POINT" ]; then
  expect 200 "gateway: the card through your access point" "${ACCESS_POINT%/}/.well-known/agent-card.json"
fi

echo "written to $OUT"
exit $FAILED
