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
    "anthropic_api_key": "ANTHROPIC_API_KEY",
}
# Local only. On Fly the agent proves who it is with the Machine's identity
# token instead, so no client secret leaves the laptop.
CLIENT_CREDENTIALS = {
    "client_id": "KEYCARD_CLIENT_ID",
    "client_secret": "KEYCARD_CLIENT_SECRET",
}
# Fly sets this in every Machine. The console's Deploy step registers the
# Machine's identity as a workload credential on the agent's Application.
FLY = "FLY_APP_NAME"
DEPENDENCY = "KEYCARD_DEPENDENCY_URL"
PAGERDUTY_URL = "https://api.pagerduty.com"
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
    anthropic_api_key: str = field(repr=False)
    # Either identifies the agent: a client id + secret locally, or, on Fly,
    # the Machine's identity token.
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    on_fly: bool = False
    dependency_url: str = PAGERDUTY_URL
    model: str = DEFAULT_MODEL

    def __post_init__(self) -> None:
        _require_https(REQUIRED["zone_url"], self.zone_url)
        _require_https(DEPENDENCY, self.dependency_url)
        if not self.on_fly and not (self.client_id and self.client_secret):
            raise SettingsError("The agent needs a client id and secret, or to run on Fly.")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        on_fly = bool(env.get(FLY))
        required = REQUIRED if on_fly else {**REQUIRED, **CLIENT_CREDENTIALS}
        missing = [var for var in required.values() if not env.get(var)]
        if missing and on_fly:
            # Deployed: there is no .env to edit, the host holds the secrets.
            names = ", ".join(f"`{var}`" for var in missing)
            raise SettingsError(
                f"The deployed agent needs {names} set. "
                f"Set {'it' if len(missing) == 1 else 'them'} as app secrets "
                "(`fly secrets set NAME=value`) and run it again."
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
                "`keycard new agent` writes the KEYCARD_* ones to `.env`; "
                f"add the rest there too, {RETRY}"
            )
        values = {name: env[var] for name, var in REQUIRED.items()}
        # A deployed agent may still carry a local .env's client id; the Machine's
        # identity wins, so the secret is never sent even if it was shipped.
        credentials = {
            name: None if on_fly else env.get(var) or None
            for name, var in CLIENT_CREDENTIALS.items()
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
            on_fly=on_fly,
            dependency_url=dependency or PAGERDUTY_URL,
            model=env.get(MODEL) or DEFAULT_MODEL,
        )
