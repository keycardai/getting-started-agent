"""Entry point for `make dev`."""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from datetime import datetime

import anthropic
import httpx
from dotenv import load_dotenv
from keycardai.oauth import AuthorizationServerDiscoveryError, NetworkError, OAuthError
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from .config import PAGERDUTY_URL, Settings, SettingsError
from .graph import build_graph
from .keycard import (
    DependencyAccessError,
    MissingDependencyError,
    PolicyDeniedError,
    UnknownDependencyError,
    WorkloadIdentityError,
    connect,
)

QUESTION = "What's open in PagerDuty right now?"
# Room for the one-line-per-incident summary at pagerduty.MAX_INCIDENTS.
MAX_TOKENS = 8192
# Each --watch cycle is a Keycard grant, an exchange and a model call on the
# user's key, so keep it slow enough to read and don't let a typo make it a tight loop.
DEFAULT_INTERVAL = 15.0
MIN_INTERVAL = 5.0
IDENTITY_WORKED = "Authenticated to Keycard and exchanged a token, so the identity flow worked."
AUTHENTICATED = "Authenticated to Keycard as the agent, but"


def explain(error: Exception) -> tuple[bool, str] | None:
    """(setup_incomplete, one friendly line) for a known failure; None for a real bug."""
    if isinstance(error, SettingsError):
        return True, str(error)
    if isinstance(error, MissingDependencyError):
        return True, (
            f"{AUTHENTICATED} it has no dependency on {error.dependency_url} yet, so Keycard "
            "denied the exchange. Add that resource as a dependency of the agent in the "
            "Keycard console, then try again."
        )
    if isinstance(error, UnknownDependencyError):
        return True, (
            f"{AUTHENTICATED} your zone has no resource at {error.dependency_url} with an "
            "API key to hand out. Add PagerDuty as a dependency of the agent in the Keycard "
            "console and store its API key, or fix `KEYCARD_DEPENDENCY_URL` if it points at "
            "the wrong resource, then try again."
        )
    if isinstance(error, PolicyDeniedError):
        return False, (
            f"{AUTHENTICATED} a policy in your zone denied the exchange for "
            f"{error.dependency_url}: {error.description} "
            "Check that policy in the Keycard console."
        )
    if isinstance(error, DependencyAccessError):
        return False, (
            f"{AUTHENTICATED} Keycard refused the exchange for {error.dependency_url} "
            f"(`{error.code}`)."
        )
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status == 404:
            return False, (
                "PagerDuty returned 404 for the incidents list, so `KEYCARD_DEPENDENCY_URL` "
                f"doesn't point at PagerDuty's REST API ({PAGERDUTY_URL}). Fix it, and the "
                "PagerDuty resource's URL in Keycard if that differs, then try again."
            )
        if status in (401, 403):
            return True, (
                f"{IDENTITY_WORKED} PagerDuty rejected the key with {status}: check the "
                "PagerDuty API key stored on that resource in Keycard."
            )
        return False, f"PagerDuty returned {status}. Try again in a minute."
    if isinstance(error, httpx.RequestError):
        host = error.request.url.host
        return False, f"Couldn't reach {host}. Check `KEYCARD_DEPENDENCY_URL`."
    if isinstance(error, WorkloadIdentityError):
        return False, f"{error} The agent can't prove who it is without it."
    # Before OAuthError, which both subclass: an unreachable zone is not a
    # rejected credential, and saying so is the difference between checking
    # the URL and checking the secret.
    if isinstance(error, (NetworkError, AuthorizationServerDiscoveryError)):
        return False, (
            f"Couldn't reach Keycard at `KEYCARD_ZONE_URL` ({error}). Check the URL "
            "and your network connection."
        )
    if isinstance(error, OAuthError):
        return False, (
            "Keycard didn't issue a token. Locally, check `KEYCARD_ZONE_URL`, "
            "`KEYCARD_CLIENT_ID` and `KEYCARD_CLIENT_SECRET` in `.env`. Deployed, check "
            "that the Deploy step in the console registered this host's identity "
            "(the org, app and Machine name must match)."
        )
    if isinstance(error, anthropic.APIError):
        return False, (
            "The model request failed. Check `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` in `.env`."
        )
    return None


async def ask(graph: CompiledStateGraph) -> str:
    result = await graph.ainvoke({"messages": [HumanMessage(QUESTION)]})
    return result["messages"][-1].text


async def watch(graph: CompiledStateGraph, interval: float) -> None:
    """Ask on a loop until Ctrl-C. Each cycle is its own grant and exchange in the activity log;
    a cycle that fails for a known reason prints why and the next one tries again."""
    print(f"Watching PagerDuty every {interval:g}s. Ctrl-C to stop.", flush=True)
    while True:
        stamp = datetime.now().strftime("%H:%M:%S")
        try:
            print(f"[{stamp}] {await ask(graph)}", flush=True)
        except Exception as e:
            if (explained := explain(e)) is None:
                raise
            print(f"[{stamp}] {explained[1]}", flush=True)
        await asyncio.sleep(interval)


async def main(settings: Settings, *, interval: float | None = None) -> None:
    """Ask once, or every `interval` seconds when watching."""
    model = ChatAnthropic(
        model=settings.model, api_key=settings.anthropic_api_key, max_tokens=MAX_TOKENS
    )
    async with connect(settings) as keycard, httpx.AsyncClient(timeout=15.0) as http:
        graph = build_graph(settings, keycard, http, model)
        if interval is None:
            print(await ask(graph))
        else:
            await watch(graph, interval)


def _interval(value: str) -> float:
    seconds = float(value)
    if not MIN_INTERVAL <= seconds < math.inf:
        raise argparse.ArgumentTypeError(f"must be at least {MIN_INTERVAL:g} seconds, got {value}")
    return seconds


def run() -> None:
    """Setup-incomplete states exit 0 with a nudge; other known failures exit 1 with one line."""
    parser = argparse.ArgumentParser(
        prog="getting-started-agent",
        description="Ask the agent what's open in PagerDuty, once or on a loop with --watch.",
    )
    parser.add_argument(
        "--watch", action="store_true", help="Keep re-checking until Ctrl-C instead of once."
    )
    parser.add_argument(
        "--interval",
        type=_interval,
        default=DEFAULT_INTERVAL,
        metavar="SECONDS",
        help=f"Seconds between checks with --watch (default: {DEFAULT_INTERVAL:g}).",
    )
    args = parser.parse_args()
    load_dotenv()
    try:
        asyncio.run(main(Settings.from_env(), interval=args.interval if args.watch else None))
    except KeyboardInterrupt:
        pass  # Ctrl-C out of --watch is a clean stop.
    except Exception as e:
        if (explained := explain(e)) is None:
            raise
        setup_incomplete, message = explained
        if not setup_incomplete:
            sys.exit(message)
        print(message)


if __name__ == "__main__":
    run()
