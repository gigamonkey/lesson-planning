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
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-5001}"

default_courses_repo="../bhs-cs-courses"

# Corpus: a directory of course directories. Single-user mode requires it to be a
# git repo (a checkout of the courses repo) and autosaves + commits edits to it
# (see app.py). Default to a sibling ../bhs-cs-courses checkout when present; else
# fall back to the bundled examples/ demo, which app.py copies into a throwaway git
# repo so edits still commit (to disposable git). Override with LESSON_CORPUS_DIR.

if [ -z "${LESSON_CORPUS_DIR:-}" ]; then
  if [ -d "$default_courses_repo" ]; then
    export LESSON_CORPUS_DIR="$(cd "$default_courses_repo" && pwd)"
  else
    export LESSON_CORPUS_DIR="examples"
  fi
fi
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
