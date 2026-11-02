# getting-started-agent

A starter LangGraph agent for Keycard. The agent authenticates to Keycard **as
itself**, exchanges that token for a PagerDuty API key, and summarizes the open
PagerDuty incidents. No user signs in and no gateway sits in between.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) and Python 3.11 or later. uv can install
  Python for you.
- The [Keycard CLI](https://github.com/keycardai/cli/releases), v0.0.39 or
  later, signed in to your zone (`keycard auth signin`). `keycard new agent`
  first shipped in v0.0.39 and doesn't appear in `keycard --help` yet.
- An Anthropic API key. The agent only supports Anthropic models: it uses
  `langchain-anthropic`, and another provider means changing the model in
  `src/getting_started_agent/main.py`.
- PagerDuty added as a dependency of the agent in the Keycard console, with a
  PagerDuty REST API key stored on it. The key stays in Keycard; the agent
  never reads it from `.env`.

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
once. That one run is two model calls on your `ANTHROPIC_API_KEY`: one that
calls the PagerDuty tool and one that writes the summary. Run it again whenever
you want another summary.

A run that fails for a known reason prints one line saying why:

- **No dependency yet.** Keycard denied the exchange because the agent has no
  dependency on PagerDuty. Add it in the console.
- **Denied by a policy.** The dependency exists, but a policy in your zone
  denied the exchange. The line names the policy.
- **PagerDuty returned 404.** `KEYCARD_DEPENDENCY_URL` doesn't point at
  PagerDuty's REST API (`https://api.pagerduty.com`).
- **A missing `.env` setting.** The run stops at startup and says what to add.

`make watch` keeps it running: the agent re-checks every 15s until you Ctrl-C
(`--interval SECONDS`, 5 or more, to change). Each cycle authenticates and
exchanges again, so each read shows up in Activity on its own. A cycle that
fails prints why and the next one tries again. Every cycle calls the model on
your key, even when the PagerDuty read fails. That's about 240 cycles an hour
at 15s, so leave it running only while you're watching.

## What you'll see in Activity

Each run makes two requests to your zone, and each one shows up in the zone's
Activity log with the agent's application as the actor and no user:

1. A token issued to the agent for its own resource (`KEYCARD_RESOURCE_URL`).
2. A token exchange of that token for PagerDuty (`KEYCARD_DEPENDENCY_URL`).

If Keycard refuses the exchange, for a missing dependency or a policy, the
denied request is in Activity too. The PagerDuty call itself goes straight to
PagerDuty, so it isn't in Activity.

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

On Vercel it is a function that Cron calls once a day (`vercel.json`, the most
a Hobby plan allows; Pro allows down to every minute):

```sh
vercel link
make env-push
vercel deploy --prod
```

Vercel sends each request an OIDC token whose subject is
`owner:<team>:project:<project>:environment:production`, which the Deploy step
registers the same way. Only production deployments match it. To run a cycle
now: `curl https://<project>.vercel.app/api/run`. The response only says whether
it ran; the summary is in `vercel logs`. Anyone with the URL can call it, and
each call is a model request on your key, so set `CRON_SECRET` on the project
to make it answer only Vercel's Cron (and `curl -H "Authorization: Bearer $CRON_SECRET"`).

## Development

```bash
make test     # pytest
make lint     # ruff check + format check
make format   # ruff fix + format
```

For a Vercel project linked with `vercel link`:

```bash
make env-push   # push KEYCARD_ZONE_URL, KEYCARD_RESOURCE_URL and ANTHROPIC_API_KEY from .env to Vercel (production)
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
