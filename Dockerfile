# Image for the git-backed collaboration deployment (see DEPLOY.md).
#
# IMPORTANT: build with the *parent* directory as the context, so both this repo
# and its sibling `bells` checkout are visible (pyproject.toml's bells path
# source is `../bells/libs/python`). From the parent of this repo:
#
#     docker build -f lesson-planning/Dockerfile -t lesson-planning .
#
# fly.toml already sets this up (build context = "..", dockerfile = the path
# below), so `fly deploy` from this repo does the right thing.

FROM python:3.13-slim

# git + ssh for the corpus repo (clone/fetch/push over the deploy key).
RUN apt-get update && apt-get install -y --no-install-recommends \
        git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv (the project's package manager).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Lay the two repos out so the `../bells` path source resolves.
WORKDIR /app
COPY bells /app/bells
COPY lesson-planning /app/lesson-planning
WORKDIR /app/lesson-planning

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
