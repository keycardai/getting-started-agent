"""Settings read from the environment (`.env` is loaded by `main`)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

REQUIRED = {
    "zone_url": "KEYCARD_ZONE_URL",
    "client_id": "KEYCARD_CLIENT_ID",
    "client_secret": "KEYCARD_CLIENT_SECRET",
    "resource_url": "KEYCARD_RESOURCE_URL",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
}
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
    client_id: str
    client_secret: str = field(repr=False)
    resource_url: str
    anthropic_api_key: str = field(repr=False)
    dependency_url: str = PAGERDUTY_URL
    model: str = DEFAULT_MODEL

    def __post_init__(self) -> None:
        _require_https(REQUIRED["zone_url"], self.zone_url)
        _require_https(DEPENDENCY, self.dependency_url)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        missing = [var for var in REQUIRED.values() if not env.get(var)]
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
        return cls(
            **values,
            dependency_url=env.get(DEPENDENCY) or PAGERDUTY_URL,
            model=env.get(MODEL) or DEFAULT_MODEL,
        )
