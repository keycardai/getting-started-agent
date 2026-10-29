# getting-started-agent

Agent starter project for onboarding agent flow: a LangGraph agent that
authenticates to Keycard **as itself** and reads PagerDuty directly, with no
user and no gateway.

## Quickstart

```bash
git clone https://github.com/keycardai/getting-started-agent.git
cd getting-started-agent
keycard new agent     # registers the agent in your zone and writes .env
# then add ANTHROPIC_API_KEY=sk-ant-... to .env
make install
make dev
```

`make dev` asks the agent what's open in PagerDuty and prints its summary,
once. That one run is one model call on your `ANTHROPIC_API_KEY`, and it shows
up in your zone's activity log as a grant and an exchange with the agent as
the actor. Run it again whenever you want another entry.

A run that fails for a known reason, e.g. because the PagerDuty resource isn't
in Console yet, prints why. A missing `.env` setting stops it at startup with a
note on what to add. In localdev nothing real sits behind the PagerDuty
resource yet, so a run says the identity flow worked and there were no
incidents; that's expected.

`make watch` keeps it running: the agent re-checks every 15s until you Ctrl-C
(`--interval SECONDS`, 5 or more, to change). Each cycle re-authenticates and
re-exchanges, so each read is its own activity-log entry: the "background
agent" rhythm, made visible. A cycle that fails prints why and the next one
tries again. Every cycle is at least one model call on your key, even when the
PagerDuty read fails: about 240 an hour at 15s, so leave it running only while
you're watching.

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

## What happens on `make dev`

The graph (`src/getting_started_agent/graph.py`) is a LangChain agent with one
tool, `list_open_incidents`. The model calls it once, and the tool uses the
Keycard calls in `src/getting_started_agent/keycard.py`:

1. **Authenticate as itself.** A `client_credentials` grant with
   `resource=KEYCARD_RESOURCE_URL` (the agent's own resource). Locally the
   agent proves who it is with its client ID and secret; deployed, with the
   host's identity (see [Deploying](#deploying)).
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
| `KEYCARD_CLIENT_ID` / `KEYCARD_CLIENT_SECRET` | The agent application's credentials, for running locally |
| `KEYCARD_RESOURCE_URL` | The agent's own resource |
| `KEYCARD_DEPENDENCY_URL` | Optional PagerDuty resource; defaults to `https://api.pagerduty.com` |
| `ANTHROPIC_API_KEY` | Your model key (not written by `keycard new agent`) |
| `ANTHROPIC_MODEL` | Optional; defaults to `claude-sonnet-5-5` |

URLs must be `https://` (plain `http://` is allowed only for `localhost`).

## Deploying

Deployed, the agent runs one cycle per invocation on a schedule and carries no
client secret. On Fly it is a scheduled Machine (`fly.toml`, `Dockerfile`):

```sh
fly launch --no-deploy
fly secrets set --stage KEYCARD_ZONE_URL=... KEYCARD_RESOURCE_URL=... ANTHROPIC_API_KEY=...
fly machine run . --name agent --schedule hourly --restart no
```

`--restart no` matters: Fly otherwise restarts a Machine that exits non-zero,
and each retry that gets as far as the model is another call on your key.

Fly issues every Machine an OIDC token whose subject is `<org>:<app>:<machine>`.
The Deploy step in the Keycard console registers that subject as a workload
identity credential on the agent's application, and the agent presents the
token as a client assertion on each request (`keycard.py`). The Machine name
is part of the subject, so it has to match what you told the console. To run a
cycle now instead of waiting for the hour: `fly machine start $(fly machine list -q)`.

## Development

```bash
make test     # pytest
make lint     # ruff check + format check
make format   # ruff fix + format
```

For a Vercel project linked with `vercel link`:

```bash
make env-push   # push KEYCARD_ZONE_URL and ANTHROPIC_API_KEY from .env to Vercel (production)
make env-pull   # pull the Vercel environment into .env.local
make env-list   # list the environment set on the Vercel project
```

`make env-push` doesn't send `KEYCARD_CLIENT_ID` or `KEYCARD_CLIENT_SECRET`,
because the deployed agent authenticates with Vercel's workload identity.
Redeploy after it runs. `VERCEL_ENV=preview` targets another environment.

## License

Apache-2.0. See [LICENSE](./LICENSE) and, for the copyright notice,
[NOTICE](./NOTICE). The Keycard name and logo are trademarks;
[TRADEMARKS.md](./TRADEMARKS.md) states how they may be used.
