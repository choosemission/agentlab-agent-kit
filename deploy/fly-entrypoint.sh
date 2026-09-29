#!/bin/sh
# Fly mounts a volume owned by root, and the agent runs as `agent`. Hand the
# volume over, then drop root before anything else runs.
set -eu
chown agent /app/data
exec setpriv --reuid=agent --regid=agent --init-groups "$@"
