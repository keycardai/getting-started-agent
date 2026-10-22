# The deployed agent: one cycle per container start, then exit. Fly's
# scheduler (`fly machine run . --name agent --schedule hourly`) starts it
# again on the schedule, so this is `make dev`, not `make watch`.
#
# No secrets are baked in. `.dockerignore` keeps `.env` out of the build
# context; the deployed agent reads its settings from Fly secrets.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so a source change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# hatchling reads README.md (pyproject `readme`) when it builds the project.
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

CMD ["uv", "run", "--no-sync", "getting-started-agent"]
