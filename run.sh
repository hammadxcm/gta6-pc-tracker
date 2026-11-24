#!/usr/bin/env bash
# Start the data server, then stream it to YouTube.
#   export GITHUB_TOKEN=...      (fine-grained token, public-repo read only, no scopes needed)
#   export YT_STREAM_KEY=...     (YouTube Studio > Go live > Stream key)
#   ./run.sh                     go live
#   ./run.sh --test 20           record 20 s to data/test.mp4 instead
set -euo pipefail
cd "$(dirname "$0")"
[ -z "${GITHUB_TOKEN:-}" ] && [ ! -s ~/.config/repo-live/github_token ] && echo "⚠  No GitHub token: polling only every 15 min."
if ! curl -s -o /dev/null localhost:${PORT:-8080}/api/state; then
  python3 server.py > data/server.log 2>&1 & echo $! > data/server.pid
  trap 'kill $(cat data/server.pid) 2>/dev/null' EXIT
  sleep 20   # first poll
fi
.venv/bin/python stream.py "$@"
