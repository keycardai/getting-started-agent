"""The Vercel entrypoint: `vercel.json` has Cron call `/api/run` once a day,
and each call runs one cycle, like `make dev`.

Vercel sends each request an OIDC token in the `x-vercel-oidc-token` header.
The agent presents it as its client assertion (`keycard.py`), so no client
secret is deployed.

Anyone who finds the URL can call it, and each call is a model request on the
zone's key. Set `CRON_SECRET` on the project to stop that: Vercel's Cron sends it
as a bearer token, and calls without it get a 401. The summary goes to the
function logs (`vercel logs`), not the response, so the URL never shows your
incidents.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from collections.abc import Callable, Iterable

from .config import VERCEL_OIDC_TOKEN, Settings
from .main import explain, once

RUN_PATH = "/api/run"
StartResponse = Callable[[str, list[tuple[str, str]]], object]


def _respond(start_response: StartResponse, status: str, text: str) -> Iterable[bytes]:
    start_response(status, [("Content-Type", "text/plain; charset=utf-8")])
    return [f"{text}\n".encode()]


def _authorized(environ: dict) -> bool:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        return True
    return hmac.compare_digest(environ.get("HTTP_AUTHORIZATION", ""), f"Bearer {secret}")


def app(environ: dict, start_response: StartResponse) -> Iterable[bytes]:
    if environ.get("PATH_INFO") != RUN_PATH:
        return _respond(start_response, "404 Not Found", f"The agent runs at {RUN_PATH}.")
    if not _authorized(environ):
        return _respond(start_response, "401 Unauthorized", "Missing or wrong CRON_SECRET.")
    # Only this request's token counts, never a stale one in the project's env.
    env = {**os.environ, VERCEL_OIDC_TOKEN: environ.get("HTTP_X_VERCEL_OIDC_TOKEN", "")}
    try:
        print(asyncio.run(once(Settings.from_env(env))), flush=True)
    except Exception as e:
        if (explained := explain(e)) is None:
            raise
        setup_incomplete, message = explained
        print(message, flush=True)
        status = "200 OK" if setup_incomplete else "500 Internal Server Error"
        return _respond(start_response, status, message)
    return _respond(start_response, "200 OK", "Ran one cycle. The summary is in `vercel logs`.")
