"""Settings read from the environment (`.env` is loaded by `main`)."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

REQUIRED = {
    "zone_url": "KEYCARD_ZONE_URL",
    "resource_url": "KEYCARD_RESOURCE_URL",
}
# Local only. Deployed, the agent proves who it is with the host's identity
# token instead, so no client secret leaves the laptop.
CLIENT_CREDENTIALS = {
    "client_id": "KEYCARD_CLIENT_ID",
    "client_secret": "KEYCARD_CLIENT_SECRET",
}
# Each host sets its variable in every deployment. The console's Deploy step
# registers the host's identity as a workload credential on the agent's Application.
HOSTS = {"fly": "FLY_APP_NAME", "vercel": "VERCEL"}
# Where a deployed agent's settings live, for the message when one is missing.
HOST_SECRETS = {
    "fly": "as app secrets (`fly secrets set NAME=value`) and run it again",
    "vercel": "on the Vercel project (`make env-push`), then run `vercel deploy --prod`",
}
# Vercel sends each request its identity token; the Vercel entrypoint passes it on here.
VERCEL_OIDC_TOKEN = "VERCEL_OIDC_TOKEN"
DEPENDENCY = "KEYCARD_DEPENDENCY_URL"
PAGERDUTY_URL = "https://api.pagerduty.com"
# The zone resource that holds the vaulted Anthropic API key. The agent fetches
# the key from the zone as itself before each run, so no key lives in `.env`.
ANTHROPIC = "KEYCARD_ANTHROPIC_URL"
ANTHROPIC_URL = "https://api.anthropic.com"
MODEL = "ANTHROPIC_MODEL"
DEFAULT_MODEL = "claude-sonnet-5-5"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


RETRY = "then run `make dev` again."


class SettingsError(RuntimeError):
    """A setup step is missing; the message tells the user how to finish it."""


def _require_https(name: str, url: str) -> None:
    """Credentials and tokens only travel over TLS, except to a local dev server."""
    parts = urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS):
        return
    raise SettingsError(
        f"`make dev` needs one more thing: `{name}` must be an https:// URL, got {url!r}. "
        f"Fix it in `.env`, {RETRY}"
    )


@dataclass(frozen=True)
class Settings:
    zone_url: str
    resource_url: str
    # Either identifies the agent: a client id + secret locally, or, deployed,
    # the host's identity token.
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    # "fly" or "vercel" when deployed, None locally.
    host: str | None = None
    vercel_oidc_token: str | None = field(default=None, repr=False)
    dependency_url: str = PAGERDUTY_URL
    anthropic_url: str = ANTHROPIC_URL
    model: str = DEFAULT_MODEL

    def __post_init__(self) -> None:
        _require_https(REQUIRED["zone_url"], self.zone_url)
        _require_https(DEPENDENCY, self.dependency_url)
        _require_https(ANTHROPIC, self.anthropic_url)
        if not self.host and not (self.client_id and self.client_secret):
            raise SettingsError(
                "The agent needs a client id and secret, or to run on Fly or Vercel."
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        host = next((name for name, var in HOSTS.items() if env.get(var)), None)
        required = REQUIRED if host else {**REQUIRED, **CLIENT_CREDENTIALS}
        missing = [var for var in required.values() if not env.get(var)]
        if missing and host:
            # Deployed: there is no .env to edit, the host holds the secrets.
            names = ", ".join(f"`{var}`" for var in missing)
            raise SettingsError(
                f"The deployed agent needs {names} set. "
                f"Set {'it' if len(missing) == 1 else 'them'} {HOST_SECRETS[host]}."
            )
        if len(missing) == 1:
            raise SettingsError(
                f"`make dev` needs one more thing: `{missing[0]}` isn't set. "
                f"Add it to `.env`, {RETRY}"
            )
        if missing:
            names = ", ".join(f"`{var}`" for var in missing)
            raise SettingsError(
                f"`make dev` needs a few more things: {names} aren't set. "
                f"`keycard new agent` writes them to `.env`, {RETRY}"
            )
        values = {name: env[var] for name, var in REQUIRED.items()}
        # A deployed agent may still carry a local .env's client id; the host's
        # identity wins, so the secret is never sent even if it was shipped.
        credentials = {
            name: None if host else env.get(var) or None for name, var in CLIENT_CREDENTIALS.items()
        }
        # `keycard new agent` can write the agent's own resource here. That's what the agent
        # *is*, not what it reads, so say so and read PagerDuty instead.
        dependency = env.get(DEPENDENCY, "")
        if dependency.rstrip("/") == values["resource_url"].rstrip("/"):
            print(
                f"`{DEPENDENCY}` is the agent's own resource, not a dependency; "
                f"reading {PAGERDUTY_URL} instead.",
                file=sys.stderr,
            )
            dependency = ""
        return cls(
            **values,
            **credentials,
            host=host,
            vercel_oidc_token=env.get(VERCEL_OIDC_TOKEN) if host == "vercel" else None,
            dependency_url=dependency or PAGERDUTY_URL,
            anthropic_url=env.get(ANTHROPIC) or ANTHROPIC_URL,
            model=env.get(MODEL) or DEFAULT_MODEL,
        )
