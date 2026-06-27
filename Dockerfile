# Image for the git-backed collaboration deployment (see DEPLOY.md).
#
# The build context is just this repo: the `bells` (bell-schedule) and
# `bhs-calendars` libraries are now PyPI dependencies, so the sibling checkout no
# longer needs to be in the build context. Build from this directory:
#
#     docker build -t lesson-planning .
#
# fly.toml points at this Dockerfile, so `fly deploy` from this repo does the
# right thing.

FROM python:3.13-slim

# git + ssh for the courses repo (clone/fetch/push over the deploy key).
RUN apt-get update && apt-get install -y --no-install-recommends \
        git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv (the project's package manager).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY . /app

# Pre-build the virtualenv into the image.
RUN uv sync --frozen || uv sync

# Production settings.
ENV FLASK_DEBUG=0 \
    PORT=8080 \
    LESSON_DATA_DIR=/data \
    LESSON_COLLAB_CONFIG=/data/collab.json

EXPOSE 8080

# Production WSGI server (gunicorn), not Flask's dev server.
#
# EXACTLY ONE worker -- the app keeps live in-process state (collab.startup()'s
# push queue + worker thread + refresh/autosave timers, the per-handle SQLite
# caches, the single git clone). Concurrency comes from --threads, never from
# more workers. Do NOT add --preload: it would import the app (and run
# collab.startup()) in the master, then fork workers where those threads are
# dead -- requests would serve but pushes would silently never drain. Importing
# app:app in the one worker keeps the threads in the process that serves.
# See plans/production-wsgi-server.md.
CMD ["uv", "run", "gunicorn", \
     "--workers", "1", "--threads", "8", "--worker-class", "gthread", \
     "--bind", "0.0.0.0:8080", "--timeout", "120", "app:app"]
