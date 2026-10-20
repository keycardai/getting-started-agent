"""The agent graph: call the dependency directly, as the agent itself."""

from __future__ import annotations

from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .config import Settings
from .keycard import TokenClient, dependency_token


class AgentState(TypedDict, total=False):
    # Tokens stay out of graph state: state is checkpointed and traced.
    status_code: int
    body: str


def build_graph(
    settings: Settings, keycard: TokenClient, http: httpx.AsyncClient
) -> CompiledStateGraph:
    async def call_dependency(state: AgentState) -> AgentState:
        token = await dependency_token(keycard, settings)
        response = await http.get(
            settings.dependency_request_url,
            headers={"Authorization": f"Bearer {token}"},
        )
        return {"status_code": response.status_code, "body": response.text}

    graph = StateGraph(AgentState)
    graph.add_node("call_dependency", call_dependency)
    graph.add_edge(START, "call_dependency")
    graph.add_edge("call_dependency", END)
    return graph.compile()
