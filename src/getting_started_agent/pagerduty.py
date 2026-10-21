"""PagerDuty REST API calls, authenticated with the key Keycard hands the agent.

The PagerDuty resource is backed by a vaulted API key, so the exchanged token
*is* that key, and PagerDuty expects it as `Token token=...`, not `Bearer`.
"""

from __future__ import annotations

import httpx

OPEN_STATUSES = ["triggered", "acknowledged"]
PAGE_SIZE = 100
MAX_INCIDENTS = 200


def _summary(incident: dict) -> dict:
    return {
        "number": incident["incident_number"],
        "title": incident["title"],
        "status": incident["status"],
        "urgency": incident["urgency"],
        "service": incident["service"]["summary"],
        "created_at": incident["created_at"],
        "url": incident["html_url"],
    }


async def open_incidents(http: httpx.AsyncClient, api_url: str, api_key: str) -> dict:
    """Every open incident, up to MAX_INCIDENTS; `more` means some were left out."""
    incidents: list[dict] = []
    more = True
    while more and len(incidents) < MAX_INCIDENTS:
        response = await http.get(
            f"{api_url.rstrip('/')}/incidents",
            params={
                "statuses[]": OPEN_STATUSES,
                "limit": min(PAGE_SIZE, MAX_INCIDENTS - len(incidents)),
                "offset": len(incidents),
            },
            headers={
                # Vaulted keys can carry stray whitespace from a copy-paste; strip it
                # so the header is a legal value (httpx rejects a trailing space).
                "Authorization": f"Token token={api_key.strip()}",
                "Accept": "application/vnd.pagerduty+json;version=2",
            },
        )
        response.raise_for_status()
        page = response.json()
        incidents += [_summary(incident) for incident in page["incidents"]]
        more = bool(page.get("more")) and bool(page["incidents"])
    return {"incidents": incidents, "more": more}
