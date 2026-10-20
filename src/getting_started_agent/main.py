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


async def main(settings: Settings) -> None:
    model = ChatAnthropic(model=settings.model, api_key=settings.anthropic_api_key, max_tokens=1024)
    async with connect(settings) as keycard, httpx.AsyncClient(timeout=15.0) as http:
        result = await build_graph(settings, keycard, http, model).ainvoke(
            {"messages": [HumanMessage(QUESTION)]}
        )
    print(result["messages"][-1].text)


def run() -> None:
    load_dotenv()
    try:
        asyncio.run(main(Settings.from_env()))
    except SettingsError as e:
        sys.exit(str(e))
    except OAuthError as e:
        sys.exit(f"Keycard request failed: {e}")
    except httpx.HTTPError as e:
        sys.exit(f"PagerDuty request failed: {e}")
    except anthropic.APIError as e:
        sys.exit(f"Model request failed: {e}")


if __name__ == "__main__":
    run()
