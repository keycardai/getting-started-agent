"""The agent's Keycard identity: a token as itself, exchanged for a dependency.

Two requests to the zone's token endpoint, both authenticated as the agent's
Application:

1. `client_credentials` with `resource` set to the agent's own resource. This
   is the agent proving who it is; no user is involved.
2. RFC 8693 token exchange of that token for one scoped to the dependency.

How the agent proves who it is depends on where it runs. Locally it is a
client id and secret from `.env`. Deployed, it is the host's identity: Fly
issues each Machine an OIDC token (``sub`` is ``<org>:<app>:<machine>``), the
console's Deploy step registered that subject as a workload credential, and
the agent presents the token as an RFC 7523 client assertion. No secret ships.

Each request appears in the zone's activity log with the agent as the actor.
"""

from __future__ import annotations

import ssl
from typing import Any, Protocol

import httpx
from keycardai.oauth import (
    AsyncClient,
    BasicAuth,
    ClientConfig,
    OAuthError,
    TokenResponse,
    TokenType,
)

from .config import Settings

JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
# Fly's per-Machine API, reachable only from inside the Machine.
FLY_API_SOCKET = "/.fly/api"


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


class WorkloadIdentityError(Exception):
    """The host did not hand the agent its identity token."""


class TokenClient(Protocol):
    async def client_credentials_grant(self, **kwargs) -> TokenResponse: ...

    async def exchange_token(self, **kwargs) -> TokenResponse: ...


def connect(settings: Settings, *, ssl_context: ssl.SSLContext | None = None) -> AsyncClient:
    """An OAuth client for the zone, authenticating as the agent's application.

    With a workload identity the client carries no credentials of its own: each
    request authenticates with the assertion `dependency_token` attaches.

    ``ssl_context``, when given, is forwarded to the underlying httpx client as
    its ``verify`` so a local dev CA (mkcert) is trusted. TEMPORARY: remove once
    the zone presents a publicly trusted certificate.
    """
    config = ClientConfig(verify_ssl=ssl_context) if ssl_context is not None else None
    auth = (
        None
        if settings.workload_host
        else BasicAuth(settings.client_id or "", settings.client_secret or "")
    )
    return AsyncClient(settings.zone_url, auth=auth, config=config)


async def fly_identity_token(audience: str) -> str:
    """The Machine's OIDC token, minted by Fly for this run.

    Fly signs it under ``https://oidc.fly.io/<org>`` with ``sub``
    ``<org>:<app>:<machine>``, which is what the Deploy step registered.
    """
    transport = httpx.AsyncHTTPTransport(uds=FLY_API_SOCKET)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://fly", timeout=5.0
        ) as fly:
            response = await fly.post("/v1/tokens/oidc", json={"aud": audience})
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise WorkloadIdentityError(
            f"Fly didn't issue this Machine an identity token ({error})."
        ) from error
    return response.text.strip()


async def client_assertion(settings: Settings) -> dict[str, Any]:
    """Extra token-request parameters that prove the agent's identity to the
    zone when it runs with a workload identity; empty when a client secret does.

    The zone resolves the credential by the assertion's issuer and subject, so
    no client id travels with it.
    """
    if settings.workload_host == "fly":
        token = await fly_identity_token(settings.zone_url)
        return {"client_assertion": token, "client_assertion_type": JWT_BEARER}
    return {}


async def dependency_token(client: TokenClient, settings: Settings) -> str:
    proof = await client_assertion(settings)
    own = await client.client_credentials_grant(resource=settings.resource_url, **proof)
    try:
        exchanged = await client.exchange_token(
            subject_token=own.access_token,
            subject_token_type=TokenType.ACCESS_TOKEN,
            resource=settings.dependency_url,
            **proof,
        )
    except OAuthError as error:
        # The grant above proved the agent's identity, so a denied exchange
        # means the dependency isn't wired up yet — not a bad zone URL or
        # client secret. Say so, rather than let it read as an auth failure.
        raise DependencyAccessError(settings.dependency_url) from error
    return exchanged.access_token
