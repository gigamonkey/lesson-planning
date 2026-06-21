#!/usr/bin/env bash
# Run the lesson-planning app listening on all interfaces, so it can be reached
# from outside this container (e.g. port-forwarded to the host).
#
#   ./serve.sh            # 0.0.0.0:5001
#   PORT=8080 ./serve.sh  # 0.0.0.0:8080
set -euo pipefail
cd "$(dirname "$0")"
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-5001}"
echo "Serving lesson-planning on http://${HOST}:${PORT} (Ctrl-C to stop)"
exec uv run app.py
