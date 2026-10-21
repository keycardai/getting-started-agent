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
from keycardai.oauth import OAuthError
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage

from .config import Settings, SettingsError
from .graph import build_graph
from .keycard import connect

QUESTION = "What's open in PagerDuty right now?"
# Room for the one-line-per-incident summary at pagerduty.MAX_INCIDENTS.
MAX_TOKENS = 8192
# PagerDuty answering these after the exchange means the identity flow worked
# but nothing real sits behind the dependency yet, as in localdev.
DEPENDENCY_NOT_WIRED = {401, 403, 404}

# Errors worth surviving in --watch mode: one bad cycle (a transient PagerDuty,
# model, or token hiccup) should print and let the next cycle try again, rather
# than tearing down the whole watcher. A one-shot run lets them propagate so the
# exit code reflects the failure.
CYCLE_ERRORS = (OAuthError, httpx.HTTPError, anthropic.APIError)
# Each --watch cycle is a Keycard grant, an exchange and a model call on the
# user's key, so don't let a typo turn it into a tight loop.
MIN_INTERVAL = 5.0


def _interval(value: str) -> float:
    seconds = float(value)
    if not MIN_INTERVAL <= seconds < math.inf:
        raise argparse.ArgumentTypeError(f"must be at least {MIN_INTERVAL:g} seconds, got {value}")
    return seconds


async def _ask(graph) -> str:
    """One wake-act-settle cycle: ask the agent the question, return its answer."""
    result = await graph.ainvoke({"messages": [HumanMessage(QUESTION)]})
    return result["messages"][-1].text


async def main(settings: Settings, *, watch: bool = False, interval: float = 60.0) -> None:
    model = ChatAnthropic(
        model=settings.model, api_key=settings.anthropic_api_key, max_tokens=MAX_TOKENS
    )
    async with connect(settings) as keycard, httpx.AsyncClient(timeout=15.0) as http:
        graph = build_graph(settings, keycard, http, model)
        if not watch:
            # One-shot: run once and exit; errors propagate to `run`.
            print(await _ask(graph))
            return
        # Watch: wake, read, settle, wait — on a loop until Ctrl-C. Each cycle
        # re-runs the client-credentials grant and token exchange, so every read
        # is freshly authorized and lands in the zone's activity log on its own.
        print(f"Watching PagerDuty every {interval:g}s. Ctrl-C to stop.", flush=True)
        while True:
            stamp = datetime.now().strftime("%H:%M:%S")
            try:
                print(f"[{stamp}] {await _ask(graph)}", flush=True)
            except CYCLE_ERRORS as e:
                print(f"[{stamp}] check failed: {e}", flush=True)
            await asyncio.sleep(interval)


def run() -> None:
    """Setup-incomplete states exit 0 with a nudge; other known failures exit 1 with one line."""
    parser = argparse.ArgumentParser(
        prog="getting-started-agent",
        description="Ask the agent what's open in PagerDuty — once, or on a loop with --watch.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep running: re-check on an interval instead of once (Ctrl-C to stop).",
    )
    parser.add_argument(
        "--interval",
        type=_interval,
        default=60.0,
        metavar="SECONDS",
        help="Seconds between checks in --watch mode (default: 60).",
    )
    args = parser.parse_args()
    load_dotenv()
    try:
        asyncio.run(main(Settings.from_env(), watch=args.watch, interval=args.interval))
    except KeyboardInterrupt:
        pass  # Ctrl-C out of --watch is a clean stop, not an error.
    except SettingsError as e:
        print(e)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status not in DEPENDENCY_NOT_WIRED:
            sys.exit(f"PagerDuty returned {status}. Try `make dev` again in a minute.")
        print(
            "Authenticated to Keycard and exchanged a token — the identity flow worked. "
            f"The dependency returned {status}, so there were no incidents to read. "
            "In localdev there's no PagerDuty wired behind `KEYCARD_DEPENDENCY_URL` yet, "
            "so this is expected — nothing's broken."
        )
    except httpx.RequestError as e:
        sys.exit(f"Couldn't reach {e.request.url.host}. Check `KEYCARD_DEPENDENCY_URL` in `.env`.")
    except OAuthError:
        sys.exit(
            "Keycard didn't issue a token. Check `KEYCARD_ZONE_URL`, `KEYCARD_CLIENT_ID` "
            "and `KEYCARD_CLIENT_SECRET` in `.env`."
        )
    except anthropic.APIError:
        sys.exit(
            "The model request failed. Check `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` in `.env`."
        )


if __name__ == "__main__":
    run()
