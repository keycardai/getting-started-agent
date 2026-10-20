import json

import httpx
import pytest
from keycardai.oauth import TokenResponse, TokenType
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from getting_started_agent.config import (
    DEFAULT_MODEL,
    PAGERDUTY_URL,
    REQUIRED,
    Settings,
    SettingsError,
)
from getting_started_agent.graph import build_graph
from getting_started_agent.keycard import dependency_token

ENV = {
    "KEYCARD_ZONE_URL": "https://zone.keycard.test",
    "KEYCARD_CLIENT_ID": "agent",
    "KEYCARD_CLIENT_SECRET": "client-secret",
    "KEYCARD_RESOURCE_URL": "https://agent.example.com",
    "KEYCARD_DEPENDENCY_URL": "https://api.pagerduty.test",
    "ANTHROPIC_API_KEY": "model-key",
}

INCIDENT = {
    "incident_number": 42,
    "title": "Checkout latency",
    "status": "triggered",
    "urgency": "high",
    "service": {"summary": "checkout"},
    "created_at": "2026-10-20T12:00:00Z",
    "html_url": "https://acme.pagerduty.test/incidents/P42",
    "description": "dropped by the tool",
}


class FakeKeycard:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def client_credentials_grant(self, **kwargs) -> TokenResponse:
        self.calls.append(("client_credentials", kwargs))
        return TokenResponse(access_token="own-token")

    async def exchange_token(self, **kwargs) -> TokenResponse:
        self.calls.append(("token_exchange", kwargs))
        return TokenResponse(access_token="pagerduty-key")


class ScriptedModel(GenericFakeChatModel):
    """Replays fixed replies; ignores the tools it is bound to."""

    def bind_tools(self, tools, **kwargs):
        return self


def scripted_model() -> ScriptedModel:
    call = {"name": "list_open_incidents", "args": {}, "id": "call-1"}
    return ScriptedModel(
        messages=iter([AIMessage("", tool_calls=[call]), AIMessage("#42 Checkout latency")])
    )


async def run_graph(handler) -> dict:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        graph = build_graph(Settings.from_env(ENV), FakeKeycard(), http, scripted_model())
        return await graph.ainvoke({"messages": [HumanMessage("What's open?")]})


def test_missing_settings_are_all_reported():
    with pytest.raises(SettingsError) as e:
        Settings.from_env({})
    assert all(var in str(e.value) for var in REQUIRED.values())


def test_optional_settings_have_defaults():
    defaults = Settings.from_env({k: v for k, v in ENV.items() if k in REQUIRED.values()})
    assert (defaults.dependency_url, defaults.model) == (PAGERDUTY_URL, DEFAULT_MODEL)
    assert Settings.from_env({**ENV, "ANTHROPIC_MODEL": "claude-x"}).model == "claude-x"


def test_secrets_are_not_in_repr():
    settings = repr(Settings.from_env(ENV))
    assert ENV["KEYCARD_CLIENT_SECRET"] not in settings
    assert ENV["ANTHROPIC_API_KEY"] not in settings


@pytest.mark.parametrize(
    "overrides",
    [
        {"KEYCARD_ZONE_URL": "http://zone.keycard.test"},
        {"KEYCARD_DEPENDENCY_URL": "http://api.pagerduty.test"},
    ],
    ids=["plaintext-zone", "plaintext-dependency"],
)
def test_rejects_settings_that_could_leak_credentials(overrides):
    with pytest.raises(SettingsError):
        Settings.from_env({**ENV, **overrides})


def test_allows_plain_http_to_loopback():
    local = {
        "KEYCARD_ZONE_URL": "http://localhost:8080",
        "KEYCARD_DEPENDENCY_URL": "http://127.0.0.1:9000",
    }
    assert Settings.from_env({**ENV, **local}).dependency_url == "http://127.0.0.1:9000"


async def test_authenticates_as_self_then_exchanges_for_dependency():
    keycard = FakeKeycard()

    assert await dependency_token(keycard, Settings.from_env(ENV)) == "pagerduty-key"
    assert keycard.calls == [
        ("client_credentials", {"resource": ENV["KEYCARD_RESOURCE_URL"]}),
        (
            "token_exchange",
            {
                "subject_token": "own-token",
                "subject_token_type": TokenType.ACCESS_TOKEN,
                "resource": ENV["KEYCARD_DEPENDENCY_URL"],
            },
        ),
    ]


async def test_graph_reads_open_incidents_with_the_exchanged_key():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"incidents": [INCIDENT]})

    result = await run_graph(handler)

    [request] = seen
    assert request.url.path == "/incidents"
    assert request.url.params.get_list("statuses[]") == ["triggered", "acknowledged"]
    assert request.headers["Authorization"] == "Token token=pagerduty-key"

    [tool_result] = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert json.loads(tool_result.content) == [
        {
            "number": 42,
            "title": "Checkout latency",
            "status": "triggered",
            "urgency": "high",
            "service": "checkout",
            "created_at": "2026-10-20T12:00:00Z",
            "url": "https://acme.pagerduty.test/incidents/P42",
        }
    ]
    assert result["messages"][-1].text == "#42 Checkout latency"
    assert not any("pagerduty-key" in str(m.content) for m in result["messages"])


async def test_pagerduty_errors_end_the_run():
    with pytest.raises(httpx.HTTPStatusError):
        await run_graph(lambda request: httpx.Response(401, json={"error": "unauthorized"}))
