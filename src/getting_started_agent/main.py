"""Entry point for `make dev`."""

from __future__ import annotations

import asyncio
import sys

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
# PagerDuty answering these after the exchange means the identity flow worked
# but nothing real sits behind the dependency yet, as in localdev.
DEPENDENCY_NOT_WIRED = {401, 403, 404}


async def main(settings: Settings) -> None:
    model = ChatAnthropic(model=settings.model, api_key=settings.anthropic_api_key, max_tokens=1024)
    async with connect(settings) as keycard, httpx.AsyncClient(timeout=15.0) as http:
        result = await build_graph(settings, keycard, http, model).ainvoke(
            {"messages": [HumanMessage(QUESTION)]}
        )
    print(result["messages"][-1].text)


def run() -> None:
    """Setup-incomplete states exit 0 with a nudge; other known failures exit 1 with one line."""
    load_dotenv()
    try:
        asyncio.run(main(Settings.from_env()))
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
