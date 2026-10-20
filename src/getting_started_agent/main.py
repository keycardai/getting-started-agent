"""Entry point for `make dev`."""

from __future__ import annotations

import asyncio
import sys

import httpx
from dotenv import load_dotenv
from keycardai.oauth import OAuthError

from .config import Settings, SettingsError
from .graph import build_graph
from .keycard import connect

MAX_BODY_CHARS = 2000


async def main(settings: Settings) -> int:
    async with connect(settings) as keycard, httpx.AsyncClient(timeout=15.0) as http:
        result = await build_graph(settings, keycard, http).ainvoke({})
    print(f"GET {settings.dependency_request_url} -> {result['status_code']}")
    print(result["body"][:MAX_BODY_CHARS])
    return 0 if result["status_code"] < 400 else 1


def run() -> None:
    load_dotenv()
    try:
        sys.exit(asyncio.run(main(Settings.from_env())))
    except SettingsError as e:
        sys.exit(str(e))
    except OAuthError as e:
        sys.exit(f"Keycard request failed: {e}")
    except httpx.HTTPError as e:
        sys.exit(f"Dependency request failed: {e}")


if __name__ == "__main__":
    run()
