# Deploy helpers for the fly.io collaboration deployment. See DEPLOY.md.
#
# `make deploy` stages the secrets from .env (and the deploy key) and then ships
# the app, so a deploy is one command and no `fly secrets set` by hand.

# App name read from fly.toml so it isn't duplicated here.
app := $(shell sed -n 's/^app = "\(.*\)"/\1/p' fly.toml)

.PHONY: deploy secrets logs ssh restart

deploy: secrets
	fly deploy

secrets:
	./set-secrets.sh

logs:
	fly logs

ssh:
	fly ssh console

restart:
	fly apps restart $(app)
