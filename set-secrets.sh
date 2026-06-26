#!/usr/bin/env bash
#
# Stage the deployed app's secrets on fly from the local .env (+ deploy key).
# Run via `make deploy` (which then runs `fly deploy`) or on its own to rotate
# secrets without a code change. See template.env / DEPLOY.md.

set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
    echo "No .env found. Copy template.env to .env and fill it in." >&2
    exit 1
fi

# The simple KEY=VALUE secrets. Strip comments and blank lines so the documented
# template.env format pipes cleanly into `fly secrets import`, which only wants
# NAME=VALUE pairs. --stage defers the restart to the deploy that follows.
grep -vE '^[[:space:]]*(#|$)' .env | fly secrets import --stage

# The SSH deploy key is multi-line, which `secrets import` can't parse, so set it
# from the gitignored key file (minted per DEPLOY.md step 3). The app writes this
# onto the volume at startup and uses it to clone/push the courses repo.
if [[ -f deploy_key ]]; then
    fly secrets set --stage "LESSON_DEPLOY_KEY=$(cat deploy_key)"
else
    echo "Note: no ./deploy_key file -- skipping LESSON_DEPLOY_KEY." >&2
    echo "      Mint one per DEPLOY.md step 3 if the app needs push access." >&2
fi
