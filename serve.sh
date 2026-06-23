#!/usr/bin/env bash
# Run the lesson-planning app, listening on all interfaces so it can be reached
# from outside this container (e.g. port-forwarded to the host).
#
#   ./serve.sh              # foreground on 0.0.0.0:5001 (Ctrl-C to stop)
#   ./serve.sh -d           # detached: own session, no TTY, logs to $LOG, returns
#                           #   immediately -- safe to call at startup from .yolorc
#   PORT=8080 ./serve.sh    # HOST and PORT are both overridable
#
# Detached mode is idempotent: if an instance is already running it does nothing,
# so re-running it at startup won't pile up servers.
set -euo pipefail
cd "$(dirname "$0")"
# In a 'cwd' yolo session the project's .venv lives on the macOS bind mount, so a
# Linux `uv run` recreates it every time -- and Flask's debug reloader then dies
# when .venv/bin/python is swapped out under it. Use a container-local environment
# off the mount instead, leaving the host's .venv untouched. (A 'worktree' session's
# .venv is already container-side and stable, so it needs no redirect; and each
# container is isolated, so a fixed /tmp path can't collide across sessions.) An
# explicit UV_PROJECT_ENVIRONMENT still wins.
if [ "${YOLO_SESSION:-}" = "cwd" ]; then
  export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-/tmp/lesson-planning-venv}"
fi
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-5001}"
# Auto-populate a blank db on startup from the corpus: a directory of course
# directories (no-op if absent). Override with LESSON_CORPUS_DIR=... .
export LESSON_CORPUS_DIR="${LESSON_CORPUS_DIR:-courses}"
LOG="${LESSON_LOG:-/tmp/lesson-planning.log}"

if [ "${1:-}" = "-d" ] || [ "${1:-}" = "--detach" ]; then
  if pgrep -f '[a]pp\.py' >/dev/null 2>&1; then
    echo "lesson-planning already running; leaving it (log: $LOG)"
    exit 0
  fi
  # setsid detaches into a new session with no controlling terminal, so the
  # server survives this shell exiting and never touches the TTY Claude needs.
  # stdin from /dev/null, all output to the log.
  setsid uv run app.py </dev/null >"$LOG" 2>&1 &
  echo "lesson-planning starting detached on http://${HOST}:${PORT} (log: $LOG)"
  exit 0
fi

echo "Serving lesson-planning on http://${HOST}:${PORT} (Ctrl-C to stop)"
exec uv run app.py
