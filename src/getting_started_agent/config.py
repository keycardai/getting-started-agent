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
    "dependency_url": "KEYCARD_DEPENDENCY_URL",
}
REQUEST_URL = "DEPENDENCY_REQUEST_URL"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class SettingsError(RuntimeError):
    pass


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


def _require_https(name: str, url: str) -> None:
    """Credentials and tokens only travel over TLS, except to a local dev server."""
    parts = urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS):
        return
    raise SettingsError(f"{name} must be an https:// URL, got {url!r}.")


@dataclass(frozen=True)
class Settings:
    zone_url: str
    client_id: str
    client_secret: str = field(repr=False)
    resource_url: str
    dependency_url: str
    dependency_request_url: str

    def __post_init__(self) -> None:
        _require_https(REQUIRED["zone_url"], self.zone_url)
        _require_https(REQUEST_URL, self.dependency_request_url)
        if _origin(self.dependency_request_url) != _origin(self.dependency_url):
            raise SettingsError(
                f"{REQUEST_URL} must be on the same origin as {REQUIRED['dependency_url']}, "
                "so the exchanged token is only sent to the resource it was issued for."
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        missing = [var for var in REQUIRED.values() if not env.get(var)]
        if missing:
            raise SettingsError(
                f"Missing settings: {', '.join(missing)}. "
                "Run `keycard new agent` in this directory, or copy .env.example to .env."
            )
        values = {name: env[var] for name, var in REQUIRED.items()}
        return cls(
            **values,
            dependency_request_url=env.get(REQUEST_URL) or values["dependency_url"],
        )
