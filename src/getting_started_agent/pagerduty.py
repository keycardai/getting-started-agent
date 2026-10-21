"""PagerDuty REST API calls, authenticated with the key Keycard hands the agent.

The PagerDuty resource is backed by a vaulted API key, so the exchanged token
*is* that key, and PagerDuty expects it as `Token token=...`, not `Bearer`.
"""

from __future__ import annotations

import httpx

OPEN_STATUSES = ["triggered", "acknowledged"]
MAX_INCIDENTS = 25


async def open_incidents(http: httpx.AsyncClient, api_url: str, api_key: str) -> dict:
    """The first page of open incidents; `more` tells the model the list is truncated."""
    response = await http.get(
        f"{api_url.rstrip('/')}/incidents",
        params={"statuses[]": OPEN_STATUSES, "limit": MAX_INCIDENTS},
        headers={
            "Authorization": f"Token token={api_key}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        },
    )
    response.raise_for_status()
    page = response.json()
    incidents = [
        {
            "number": incident["incident_number"],
            "title": incident["title"],
            "status": incident["status"],
            "urgency": incident["urgency"],
            "service": incident["service"]["summary"],
            "created_at": incident["created_at"],
            "url": incident["html_url"],
        }
        for incident in page["incidents"]
    ]
    return {"incidents": incidents, "more": page.get("more", False)}
