"""PagerDuty REST API calls, authenticated with the key Keycard hands the agent.

The PagerDuty resource is backed by a vaulted API key, so the exchanged token
*is* that key, and PagerDuty expects it as `Token token=...`, not `Bearer`.
"""

from __future__ import annotations

import httpx

OPEN_STATUSES = ["triggered", "acknowledged"]
MAX_INCIDENTS = 25


async def open_incidents(http: httpx.AsyncClient, api_url: str, api_key: str) -> list[dict]:
    response = await http.get(
        f"{api_url.rstrip('/')}/incidents",
        params={"statuses[]": OPEN_STATUSES, "limit": MAX_INCIDENTS},
        headers={
            "Authorization": f"Token token={api_key}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        },
    )
    response.raise_for_status()
    return [
        {
            "number": incident["incident_number"],
            "title": incident["title"],
            "status": incident["status"],
            "urgency": incident["urgency"],
            "service": incident["service"]["summary"],
            "created_at": incident["created_at"],
            "url": incident["html_url"],
        }
        for incident in response.json()["incidents"]
    ]
