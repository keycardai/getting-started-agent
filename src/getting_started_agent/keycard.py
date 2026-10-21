"""The agent's Keycard identity: a token as itself, exchanged for a dependency.

Two requests to the zone's token endpoint, both authenticated with the agent's
client credentials:

1. `client_credentials` with `resource` set to the agent's own resource. This
   is the agent proving who it is; no user is involved.
2. RFC 8693 token exchange of that token for one scoped to the dependency.

Each request appears in the zone's activity log with the agent as the actor.
"""

from __future__ import annotations

from typing import Protocol

from keycardai.oauth import AsyncClient, BasicAuth, OAuthError, TokenResponse, TokenType

from .config import Settings


class DependencyAccessError(Exception):
    """The agent authenticated as itself, but Keycard had no dependency to
    exchange that token for.

    This is the expected state before the dependency (and its API key) is added
    in the console: the identity grant succeeds and only the exchange is denied,
    so it is a setup-not-finished nudge, not a credentials misconfiguration
    (those fail the client-credentials grant first). Carries the resource URL so
    the message can name what to add (CX-1366).
    """

    def __init__(self, dependency_url: str) -> None:
        self.dependency_url = dependency_url
        super().__init__(dependency_url)


class TokenClient(Protocol):
    async def client_credentials_grant(self, **kwargs) -> TokenResponse: ...

    async def exchange_token(self, **kwargs) -> TokenResponse: ...


def connect(settings: Settings) -> AsyncClient:
    """An OAuth client for the zone, authenticating as the agent's application."""
    return AsyncClient(
        settings.zone_url,
        auth=BasicAuth(settings.client_id, settings.client_secret),
    )


async def dependency_token(client: TokenClient, settings: Settings) -> str:
    own = await client.client_credentials_grant(resource=settings.resource_url)
    try:
        exchanged = await client.exchange_token(
            subject_token=own.access_token,
            subject_token_type=TokenType.ACCESS_TOKEN,
            resource=settings.dependency_url,
        )
    except OAuthError as error:
        # The grant above proved the agent's identity, so a denied exchange
        # means the dependency isn't wired up yet — not a bad zone URL or
        # client secret. Say so, rather than let it read as an auth failure.
        raise DependencyAccessError(settings.dependency_url) from error
    return exchanged.access_token
