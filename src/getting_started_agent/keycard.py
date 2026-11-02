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

import re
from typing import Any, Protocol

import httpx
from keycardai.oauth import (
    AsyncClient,
    BasicAuth,
    OAuthError,
    OAuthProtocolError,
    TokenResponse,
    TokenType,
)

from .config import Settings

JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
# Fly's per-Machine API, reachable only from inside the Machine.
FLY_API_SOCKET = "/.fly/api"


class DependencyAccessError(Exception):
    """The agent authenticated as itself, but Keycard refused to exchange that
    token for the dependency.

    The identity grant has already succeeded, so this is never a bad zone URL
    or client secret (those fail the client-credentials grant first). The
    subclasses name the refusals the zone reports, so the message can say what
    to fix. `code` is the OAuth error code the zone returned.
    """

    def __init__(self, dependency_url: str, code: str, description: str | None = None) -> None:
        self.dependency_url = dependency_url
        self.code = code
        self.description = description
        super().__init__(dependency_url, code)


class UnknownDependencyError(DependencyAccessError):
    """`invalid_target`: the zone has no resource at that URL, or the resource
    has no API key stored to hand out."""


class MissingDependencyError(DependencyAccessError):
    """`access_denied` with no policy named: no policy permits the exchange. The
    zone's default policies permit an agent acting as itself only on the
    resources it declares as dependencies, so in a starter zone this is the state
    before the dependency is added in the console. A zone with custom policies
    needs one that permits it instead."""


class PolicyDeniedError(DependencyAccessError):
    """`access_denied` naming the policy that denied it: the dependency exists,
    but a policy in the zone forbids this exchange."""


# Zones that predate the `reason` field: the zone names the deciding policy in
# `error_description` only when one explicitly denied the request:
# `Access to "R" is denied by Policy "P" ...`, or `... Determining policies: P.`
# when it has no friendlier reason. A denial with no policy named means no
# policy permitted it.
_POLICY_NAMED = re.compile(r"\bdenied by Polic(y|ies)\b|\bDetermining policies: ")


def _refusal(error: OAuthError, dependency_url: str) -> DependencyAccessError | None:
    """The exchange refusal `error` reports, or None if the zone didn't refuse it
    (an unreachable zone, a malformed response)."""
    if not isinstance(error, OAuthProtocolError):
        return None
    if error.error == "invalid_target":
        return UnknownDependencyError(dependency_url, error.error)
    if error.error == "access_denied":
        description = error.error_description or ""
        # The zone's machine-readable cause; older SDKs don't keep it.
        reason = getattr(error, "reason", None)
        if reason == "no_policy_matched":
            return MissingDependencyError(dependency_url, error.error)
        if reason == "policy_denied" or (reason is None and _POLICY_NAMED.search(description)):
            return PolicyDeniedError(dependency_url, error.error, " ".join(description.split()))
        return MissingDependencyError(dependency_url, error.error)
    return DependencyAccessError(dependency_url, error.error)


class WorkloadIdentityError(Exception):
    """The host did not hand the agent its identity token."""


class TokenClient(Protocol):
    async def client_credentials_grant(self, **kwargs) -> TokenResponse: ...

    async def exchange_token(self, **kwargs) -> TokenResponse: ...


def connect(settings: Settings) -> AsyncClient:
    """An OAuth client for the zone, authenticating as the agent's application.

    On Fly the client carries no credentials of its own: each request
    authenticates with the assertion `dependency_token` attaches.
    """
    auth = None if settings.on_fly else BasicAuth(settings.client_id, settings.client_secret)
    return AsyncClient(settings.zone_url, auth=auth)


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
    zone when it runs on Fly; empty when a client secret does.

    The zone resolves the credential by the assertion's issuer and subject, so
    no client id travels with it.
    """
    if settings.on_fly:
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
        # The grant above proved the agent's identity, so a refusal here is
        # about the dependency, not the zone URL or the client secret.
        if (refusal := _refusal(error, settings.dependency_url)) is None:
            raise
        raise refusal from error
    return exchanged.access_token
