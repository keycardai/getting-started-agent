"""Entry point for `make dev`."""

from __future__ import annotations

import asyncio
import sys

import httpx
from dotenv import load_dotenv
from keycardai.oauth import OAuthError

from .config import MissingSettingsError, Settings
from .graph import build_graph
from .keycard import connect


async def main(settings: Settings) -> int:
    async with connect(settings) as keycard, httpx.AsyncClient(timeout=15.0) as http:
        graph = build_graph(settings, keycard, http)
        result = await graph.ainvoke({})
    print(f"GET {settings.dependency_request_url} -> {result['status_code']}")
    print(result["body"][:2000])
    return 0 if result["status_code"] < 400 else 1


def run() -> None:
    load_dotenv()
    try:
        settings = Settings.from_env()
        sys.exit(asyncio.run(main(settings)))
    except MissingSettingsError as e:
        sys.exit(str(e))
    except OAuthError as e:
        sys.exit(f"Keycard rejected the request: {e}")


if __name__ == "__main__":
    run()
