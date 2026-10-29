.PHONY: install dev watch test lint format env-push env-pull env-list

VERCEL_ENV ?= production

# Keys from .env that `make env-push` copies to Vercel. The deployed agent
# authenticates with Vercel's OIDC workload identity, so the client id and
# secret stay local. It refuses to start without the other three.
DEPLOY_ENV_VARS ?= KEYCARD_ZONE_URL KEYCARD_RESOURCE_URL ANTHROPIC_API_KEY

install:
	uv sync

# One shot: authenticate, exchange, read, summarize, exit. One model call on
# the user's key, and one grant + exchange in the activity log.
dev:
	uv run getting-started-agent

# The loop: re-runs the one shot every 15s until Ctrl-C. Every cycle is a model
# call on the user's key, so this is the opt-in demo, not the default.
watch:
	uv run getting-started-agent --watch

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

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
