"""Settings read from the environment (`.env` is loaded by `main`)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

REQUIRED = (
    "KEYCARD_ZONE_URL",
    "KEYCARD_CLIENT_ID",
    "KEYCARD_CLIENT_SECRET",
    "KEYCARD_RESOURCE_URL",
    "KEYCARD_DEPENDENCY_URL",
)


class MissingSettingsError(RuntimeError):
    def __init__(self, missing: list[str]):
        super().__init__(
            "Missing settings: "
            + ", ".join(missing)
            + ". Run `keycard new agent` in this directory, or copy .env.example to .env."
        )
        self.missing = missing


@dataclass(frozen=True)
class Settings:
    zone_url: str
    client_id: str
    client_secret: str
    resource_url: str
    dependency_url: str
    dependency_request_url: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        missing = [key for key in REQUIRED if not env.get(key)]
        if missing:
            raise MissingSettingsError(missing)
        return cls(
            zone_url=env["KEYCARD_ZONE_URL"],
            client_id=env["KEYCARD_CLIENT_ID"],
            client_secret=env["KEYCARD_CLIENT_SECRET"],
            resource_url=env["KEYCARD_RESOURCE_URL"],
            dependency_url=env["KEYCARD_DEPENDENCY_URL"],
            dependency_request_url=env.get("DEPENDENCY_REQUEST_URL")
            or env["KEYCARD_DEPENDENCY_URL"],
        )
