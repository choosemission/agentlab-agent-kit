#!/usr/bin/env bash
# The feed, end to end, between the two harness agents. Start them first:
#   docker compose -f harness/docker-compose.yml up --build -d
set -euo pipefail
cd "$(dirname "$0")"

dc() { docker compose -f docker-compose.yml "$@"; }
alice() { dc exec -T alice python -m labagent "$@"; }
bob() { dc exec -T bob python -m labagent "$@"; }
say() { printf '\n== %s\n' "$*"; }
settle() { sleep "${SETTLE:-3}"; }   # let the outbox workers run

say "Bob pings Alice: how did Alice's agent label Bob?"
bob ping alice

say "Bob asks for Alice's feed"
bob feed subscribe alice
settle

say "Alice's agent asks her first"
alice approvals
id=$(alice approvals | python3 -c 'import json,sys; print(json.load(sys.stdin)["approvals"][0]["id"])')
alice approve "$id"
settle

say "Bob's subscription"
bob feed subscriptions

say "Alice posts"
alice feed post "Harness check: the feed works end to end." --topic demo
settle

say "Bob reads"
bob feed read

say "Alice adds a peer at runtime: Bob's agent itself, not through her gateway"
alice peers add bob-direct http://bob:8080/ --mode direct --api-key-env BOB_DIRECT_KEY

say "Direct, nothing signs Alice's message: Bob sees her as self-asserted"
alice ping bob-direct

say "The peer survives a restart"
dc restart alice >/dev/null
sleep "${SETTLE:-3}"
alice peers | python3 -c 'import json,sys; print([p["name"] + ":" + p["mode"] for p in json.load(sys.stdin)["peers"]])'
