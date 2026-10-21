"""The agent graph: one tool that reads PagerDuty as the agent itself."""

from __future__ import annotations

import json

import httpx
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from . import pagerduty
from .config import Settings
from .keycard import TokenClient, dependency_token

SYSTEM_PROMPT = (
    "You are an on-call assistant. Call list_open_incidents once, then summarize what is "
    "open in PagerDuty, most urgent first, one line per incident. If `more` is true, say "
    "the list is truncated. If nothing is open, say so."
)


def build_graph(
    settings: Settings, keycard: TokenClient, http: httpx.AsyncClient, model: BaseChatModel
) -> CompiledStateGraph:
    @tool
    async def list_open_incidents() -> str:
        """List the PagerDuty incidents that are triggered or acknowledged."""
        # The key is fetched per call and stays in this frame: graph state is
        # checkpointed and traced, so only the incidents go back to the model.
        api_key = await dependency_token(keycard, settings)
        return json.dumps(await pagerduty.open_incidents(http, settings.dependency_url, api_key))

    return create_agent(model, tools=[list_open_incidents], system_prompt=SYSTEM_PROMPT)
