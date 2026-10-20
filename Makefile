.PHONY: install dev test lint format

install:
	uv sync

dev:
	uv run getting-started-agent

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .
