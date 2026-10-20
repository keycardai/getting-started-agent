# getting-started-agent

Agent starter project for onboarding agent flow: a LangGraph agent that
authenticates to Keycard **as itself** and calls a dependency directly, with no
user and no gateway.

## Quickstart

```bash
git clone https://github.com/keycard-sandbox/getting-started-agent.git
cd getting-started-agent
keycard new agent     # registers the agent in your zone and writes .env
make install
make dev
```

`make dev` runs the graph once and prints the dependency's response. Each run
shows up in your zone's activity log with the agent as the actor.

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

## What happens on `make dev`

1. **Authenticate as itself.** A `client_credentials` grant with
   `resource=KEYCARD_RESOURCE_URL` (the agent's own resource), using the
   agent's client ID and secret.
2. **Exchange for the dependency.** An RFC 8693 token exchange of that token
   for one scoped to `KEYCARD_DEPENDENCY_URL`.
3. **Call the dependency.** `GET DEPENDENCY_REQUEST_URL` with the exchanged
   token. The process never holds the dependency's own credential.

The graph is `authenticate -> call_dependency` in
`src/getting_started_agent/graph.py`; the Keycard calls are in
`src/getting_started_agent/keycard.py`.

## Configuration

`keycard new agent` writes `.env`. To set it up by hand, copy `.env.example`
to `.env` and fill it in:

| Variable | Meaning |
| --- | --- |
| `KEYCARD_ZONE_URL` | Your zone, e.g. `https://<zone-id>.keycard.cloud` |
| `KEYCARD_CLIENT_ID` / `KEYCARD_CLIENT_SECRET` | The agent application's credentials |
| `KEYCARD_RESOURCE_URL` | The agent's own resource |
| `KEYCARD_DEPENDENCY_URL` | The resource the agent depends on |
| `DEPENDENCY_REQUEST_URL` | Optional URL to call; defaults to `KEYCARD_DEPENDENCY_URL` |

## Development

```bash
make test     # pytest
make lint     # ruff check + format check
make format   # ruff fix + format
```
