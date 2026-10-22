.PHONY: install dev watch test lint format

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
