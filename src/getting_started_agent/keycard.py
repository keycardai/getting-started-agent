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

from keycardai.oauth import AsyncClient, BasicAuth, TokenResponse, TokenType

from .config import Settings


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
    exchanged = await client.exchange_token(
        subject_token=own.access_token,
        subject_token_type=TokenType.ACCESS_TOKEN,
        resource=settings.dependency_url,
    )
    return exchanged.access_token
