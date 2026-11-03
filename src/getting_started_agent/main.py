"""Entry point for `make dev`."""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    TokenClient,
    UnknownDependencyError,
    WorkloadIdentityError,
    anthropic_api_key,
    connect,
)

QUESTION = "What's open in PagerDuty right now?"
# Room for the one-line-per-incident summary at pagerduty.MAX_INCIDENTS.
MAX_TOKENS = 8192
# Each --watch cycle is a Keycard grant, an exchange and a model call, so keep
# it slow enough to read and don't let a typo make it a tight loop.
DEFAULT_INTERVAL = 15.0
MIN_INTERVAL = 5.0
# A --watch with no --for stops after this so an unattended run (left going overnight,
# say) can't quietly burn tokens forever. --forever opts out when you're there to watch it.
DEFAULT_DURATION = 3600.0
IDENTITY_WORKED = "Authenticated to Keycard and exchanged a token, so the identity flow worked."
AUTHENTICATED = "Authenticated to Keycard as the agent, but"


def explain(error: Exception) -> tuple[bool, str] | None:
    """(setup_incomplete, one friendly line) for a known failure; None for a real bug."""
    if isinstance(error, SettingsError):
        return True, str(error)
    if isinstance(error, MissingDependencyError):
        return True, (
            f"{AUTHENTICATED} it has no dependency on {error.dependency_url} yet, so Keycard "
            "refused the token. Add that resource as a dependency of the agent in the "
            "Keycard console, then try again."
        )
    if isinstance(error, UnknownDependencyError):
        return True, (
            f"{AUTHENTICATED} your zone has no resource at {error.dependency_url} with an "
            "API key stored on it. Add that resource as a dependency of the agent in the "
            f"Keycard console and store its API key, or fix `{error.variable}` if it points "
            "at the wrong resource, then try again."
        )
    if isinstance(error, PolicyDeniedError):
        return False, (
            f"{AUTHENTICATED} a policy in your zone denied the token for "
            f"{error.dependency_url}: {error.description} "
            "Check that policy in the Keycard console."
        )
    if isinstance(error, DependencyAccessError):
        return False, (
            f"{AUTHENTICATED} Keycard refused the token for {error.dependency_url} "
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
            "(on Fly the org, app and Machine name; on Vercel the team and project)."
        )
    if isinstance(error, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True, (
            "Keycard granted the Anthropic key, but Anthropic rejected it: check the key "
            "stored on the Anthropic resource in Keycard."
        )
    if isinstance(error, anthropic.APIError):
        return False, (
            "The model request failed. Check `ANTHROPIC_MODEL` in `.env` and the Anthropic "
            "key stored in Keycard."
        )
    return None


async def ask(graph: CompiledStateGraph) -> str:
    result = await graph.ainvoke({"messages": [HumanMessage(QUESTION)]})
    return result["messages"][-1].text


async def watch(graph: CompiledStateGraph, interval: float, duration: float | None = None) -> None:
    """Ask on a loop until Ctrl-C, or until `duration` seconds have elapsed when set, so a
    background --watch doesn't run forever. Each cycle is its own grant and exchange in the
    activity log; a cycle that fails for a known reason prints why and the next one tries again."""
    window = "" if duration is None else f" for {duration:g}s"
    print(f"Watching PagerDuty every {interval:g}s{window}. Ctrl-C to stop.", flush=True)
    loop = asyncio.get_running_loop()
    deadline = None if duration is None else loop.time() + duration
    while True:
        stamp = datetime.now().strftime("%H:%M:%S")
        try:
            print(f"[{stamp}] {await ask(graph)}", flush=True)
        except Exception as e:
            if (explained := explain(e)) is None:
                raise
            print(f"[{stamp}] {explained[1]}", flush=True)
        if deadline is not None and loop.time() >= deadline:
            return
        await asyncio.sleep(interval if deadline is None else min(interval, deadline - loop.time()))


async def preflight(keycard: TokenClient, settings: Settings) -> ChatAnthropic:
    """The chat model, keyed with the Anthropic key the zone grants the agent.

    The key is a prerequisite for the run: this runs before the graph is built,
    so a refused grant stops the run before any model or PagerDuty call. The
    key stays in this frame, as the PagerDuty key does in `graph.py`. A --watch
    run fetches it once, not per cycle. The notice goes to stderr so stdout
    stays the summary.
    """
    api_key = await anthropic_api_key(keycard, settings)
    print("Fetched the Anthropic key from Keycard as the agent.", file=sys.stderr, flush=True)
    return ChatAnthropic(model=settings.model, api_key=api_key, max_tokens=MAX_TOKENS)


@asynccontextmanager
async def agent(settings: Settings) -> AsyncIterator[CompiledStateGraph]:
    async with connect(settings) as keycard, httpx.AsyncClient(timeout=15.0) as http:
        model = await preflight(keycard, settings)
        yield build_graph(settings, keycard, http, model)


async def once(settings: Settings) -> str:
    """One cycle: what `make dev` prints, and what each deployed run does."""
    async with agent(settings) as graph:
        return await ask(graph)


async def main(
    settings: Settings, *, interval: float | None = None, duration: float | None = None
) -> None:
    """Ask once, or every `interval` seconds when watching, stopping after `duration` if set."""
    if interval is None:
        print(await once(settings))
        return
    async with agent(settings) as graph:
        await watch(graph, interval, duration)


def _interval(value: str) -> float:
    seconds = float(value)
    if not MIN_INTERVAL <= seconds < math.inf:
        raise argparse.ArgumentTypeError(f"must be at least {MIN_INTERVAL:g} seconds, got {value}")
    return seconds


def _duration(value: str) -> float:
    seconds = float(value)
    if not 0 < seconds < math.inf:
        raise argparse.ArgumentTypeError(f"must be a positive number of seconds, got {value}")
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
        "--forever",
        action="store_true",
        help="Watch with no time limit (overrides --for). Use only while you're watching it.",
    )
    parser.add_argument(
        "--interval",
        type=_interval,
        default=DEFAULT_INTERVAL,
        metavar="SECONDS",
        help=f"Seconds between checks with --watch (default: {DEFAULT_INTERVAL:g}).",
    )
    parser.add_argument(
        "--for",
        dest="duration",
        type=_duration,
        default=DEFAULT_DURATION,
        metavar="SECONDS",
        help=(
            f"Stop watching after this many seconds (default: {DEFAULT_DURATION:g}); "
            "pass --forever to watch with no limit."
        ),
    )
    args = parser.parse_args()
    load_dotenv()
    duration = None if args.forever else args.duration
    try:
        asyncio.run(
            main(
                Settings.from_env(),
                interval=args.interval if args.watch else None,
                duration=duration if args.watch else None,
            )
        )
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
