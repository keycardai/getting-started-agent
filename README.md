# getting-started-agent

Agent starter project for onboarding agent flow: a LangGraph agent that
authenticates to Keycard **as itself** and reads PagerDuty directly, with no
user and no gateway.

## Quickstart

```bash
git clone https://github.com/keycard-sandbox/getting-started-agent.git
cd getting-started-agent
keycard new agent     # registers the agent in your zone and writes .env
# then add ANTHROPIC_API_KEY=sk-ant-... to .env
make install
make dev
```

`make dev` asks the agent what's open in PagerDuty and prints its summary, once.
Each run shows up in your zone's activity log with the agent as the actor.
In localdev nothing real sits behind the PagerDuty resource yet, so the run
stops after the token exchange and says so; that's expected.

`make watch` keeps it running: the agent re-checks on an interval (every 10s;
`--interval SECONDS` to change), waking, reading, and settling on a loop until
you Ctrl-C. Every cycle re-authenticates and re-exchanges, so each read is its
own entry in the activity log — the "background agent" rhythm, made visible.

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

## What happens on `make dev`

The graph (`src/getting_started_agent/graph.py`) is a LangChain agent with one
tool, `list_open_incidents`. The model calls it once, and the tool uses the
Keycard calls in `src/getting_started_agent/keycard.py`:

1. **Authenticate as itself.** A `client_credentials` grant with
   `resource=KEYCARD_RESOURCE_URL` (the agent's own resource), using the
   agent's client ID and secret.
2. **Exchange for PagerDuty.** An RFC 8693 token exchange of that token for
   the PagerDuty resource (`KEYCARD_DEPENDENCY_URL`). PagerDuty is backed by a vaulted API key, so the
   exchanged token is that key.
3. **Read PagerDuty.** `GET {KEYCARD_DEPENDENCY_URL}/incidents` for triggered
   and acknowledged incidents, page by page up to 200, sent as `Authorization: Token token=<key>`. The
   key lives in the zone, not in `.env`.

The model then summarizes the incidents. The key never enters graph state, so
it is not checkpointed, sent to tracing, or shown to the model.

## Configuration

`keycard new agent` writes `.env`. To set it up by hand, copy `.env.example`
to `.env` and fill it in:

| Variable | Meaning |
| --- | --- |
| `KEYCARD_ZONE_URL` | Your zone, e.g. `https://<zone-id>.keycard.cloud` |
| `KEYCARD_CLIENT_ID` / `KEYCARD_CLIENT_SECRET` | The agent application's credentials |
| `KEYCARD_RESOURCE_URL` | The agent's own resource |
| `KEYCARD_DEPENDENCY_URL` | Optional PagerDuty resource; defaults to `https://api.pagerduty.com` |
| `ANTHROPIC_API_KEY` | Your model key (not written by `keycard new agent`) |
| `ANTHROPIC_MODEL` | Optional; defaults to `claude-sonnet-5-5` |

URLs must be `https://` (plain `http://` is allowed only for `localhost`).

## Development

```bash
make test     # pytest
make lint     # ruff check + format check
make format   # ruff fix + format
```
