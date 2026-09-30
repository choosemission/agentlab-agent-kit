#!/usr/bin/env bash
# Set up a virtualenv, then run the agent, its tests, or an owner command.
#
#   ./run.sh serve                 # run the agent (reads .env)
#   ./run.sh test [pytest args]    # the suite
#   ./run.sh inbox                 # anything else goes to the owner CLI
#
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating .venv/ ..."
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements-dev.txt
  echo "Done."
fi

# Read .env without sourcing it, so a value with spaces — LABAGENT_NAME=Alice's
# agent — is read as a value rather than run as a command. The .env wins over
# the environment.
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key=${line%%=*}
    val=${line#*=}
    case "$key" in *[!A-Za-z0-9_]*) continue ;; esac
    val=${val%$'\r'}
    case "$val" in
      \"*\") val=${val#\"}; val=${val%\"} ;;
      \'*\') val=${val#\'}; val=${val%\'} ;;
    esac
    export "$key=$val"
  done < .env
fi

if [ "${1:-}" = "test" ]; then
  shift
  exec ./.venv/bin/python -m pytest "$@"
fi
exec ./.venv/bin/python -m labagent "$@"
