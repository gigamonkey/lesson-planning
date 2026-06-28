# Deploy helpers for the fly.io collaboration deployment. See DEPLOY.md.
#
# `make deploy` stages the secrets from .env (and the deploy key) and then ships
# the app, so a deploy is one command and no `fly secrets set` by hand.

# App name read from fly.toml so it isn't duplicated here.
app := $(shell sed -n 's/^app = "\(.*\)"/\1/p' fly.toml)

# The commit being shipped, baked into the image so the running server can show
# it (the .git dir is excluded from the Docker build context). `-dirty` marks an
# image built with uncommitted changes.
git_sha := $(shell git describe --always --dirty --abbrev=7)

.PHONY: deploy secrets logs ssh restart

deploy: secrets
	fly deploy --build-arg GIT_SHA=$(git_sha)

secrets:
	./set-secrets.sh

logs:
	fly logs

ssh:
	fly ssh console

restart:
	fly apps restart $(app)
