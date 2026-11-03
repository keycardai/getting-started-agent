.PHONY: install dev watch test lint format checks check-licenses \
        notice check-notice licenses-allowed env-push env-pull env-list

VERCEL_ENV ?= production

# Keys from .env that `make env-push` copies to Vercel. The deployed agent
# authenticates with Vercel's OIDC workload identity, so the client id and
# secret stay local, and it fetches the Anthropic key from the zone, so no
# model key is pushed. It refuses to start without the other two.
DEPLOY_ENV_VARS ?= KEYCARD_ZONE_URL KEYCARD_RESOURCE_URL

install:
	uv sync

# One shot: fetch the model key, authenticate, exchange, read, summarize, exit.
# Two model calls on the zone's Anthropic key, and three grants in the activity log.
dev:
	uv run getting-started-agent

# The loop: fetches the model key once, then re-runs the one shot every 15s,
# stopping after an hour (or Ctrl-C) so an unattended run can't burn tokens
# overnight. Pass --for SECONDS or --forever to change the cap. Every cycle is a
# model call, so this is the opt-in demo, not the default. Forward flags with:
# make watch ARGS="--forever".
watch:
	uv run getting-started-agent --watch $(ARGS)

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

# The licence gates, both reading the environment `uv sync --locked` produces.
checks: check-licenses check-notice

# Every installed distribution carries a licence on the allow-list.
check-licenses:
	uv run python scripts/licenses.py check-licenses

# Regenerate NOTICE's third-party attributions from that environment.
notice:
	uv run python scripts/licenses.py notice

# Fail when the committed NOTICE no longer matches the environment.
check-notice:
	uv run python scripts/licenses.py check-notice

# Print the allow-list, one SPDX id per line.
licenses-allowed:
	uv run python scripts/licenses.py --allowed

# Push DEPLOY_ENV_VARS from .env to the linked Vercel project.
env-push:
	@VERCEL_ENV=$(VERCEL_ENV) DEPLOY_ENV_VARS="$(DEPLOY_ENV_VARS)" ./scripts/env-push.sh

# Pull the Vercel project's environment into .env.local.
env-pull:
	@command -v vercel >/dev/null 2>&1 || { echo "vercel not found. Install it with: npm i -g vercel"; exit 1; }
	vercel env pull .env.local --environment $(VERCEL_ENV)

# List the environment set on the Vercel project.
env-list:
	@command -v vercel >/dev/null 2>&1 || { echo "vercel not found. Install it with: npm i -g vercel"; exit 1; }
	vercel env ls $(VERCEL_ENV)
