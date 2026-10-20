"""The agent graph: authenticate as itself, then call the dependency directly."""

from __future__ import annotations

from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from .config import Settings
from .keycard import TokenClient, authenticate_as_self


class AgentState(TypedDict, total=False):
    dependency_token: str
    status_code: int
    body: str


def build_graph(settings: Settings, keycard: TokenClient, http: httpx.AsyncClient):
    async def authenticate(state: AgentState) -> AgentState:
        tokens = await authenticate_as_self(keycard, settings)
        return {"dependency_token": tokens.dependency.access_token}

    async def call_dependency(state: AgentState) -> AgentState:
        response = await http.get(
            settings.dependency_request_url,
            headers={"Authorization": f"Bearer {state['dependency_token']}"},
        )
        return {"status_code": response.status_code, "body": response.text}

    graph = StateGraph(AgentState)
    graph.add_node("authenticate", authenticate)
    graph.add_node("call_dependency", call_dependency)
    graph.add_edge(START, "authenticate")
    graph.add_edge("authenticate", "call_dependency")
    graph.add_edge("call_dependency", END)
    return graph.compile()
