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

# git + ssh for the corpus repo (clone/fetch/push over the deploy key).
RUN apt-get update && apt-get install -y --no-install-recommends \
        git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv (the project's package manager).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY . /app

# Pre-build the virtualenv into the image.
RUN uv sync --frozen || uv sync

# Production settings: no Flask reloader (collab.startup runs once), bind all
# interfaces, listen on fly's internal port.
ENV FLASK_DEBUG=0 \
    HOST=0.0.0.0 \
    PORT=8080 \
    LESSON_DATA_DIR=/data \
    LESSON_COLLAB_CONFIG=/data/collab.json

EXPOSE 8080
CMD ["uv", "run", "app.py"]
