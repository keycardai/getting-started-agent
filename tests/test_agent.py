import json

import anthropic
import httpx
import pytest
from keycardai.oauth import OAuthError, TokenResponse, TokenType
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from getting_started_agent import main
from getting_started_agent.config import (
    DEFAULT_MODEL,
    PAGERDUTY_URL,
    REQUIRED,
    Settings,
    SettingsError,
)
from getting_started_agent.graph import build_graph
from getting_started_agent.keycard import dependency_token
from getting_started_agent.pagerduty import MAX_INCIDENTS, PAGE_SIZE

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


def status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.pagerduty.test/incidents?statuses[]=triggered")
    response = httpx.Response(status, request=request)
    message = f"Client error '{status}' for url '{request.url}'"
    return httpx.HTTPStatusError(message, request=request, response=response)


def test_missing_settings_are_all_reported():
    with pytest.raises(SettingsError) as e:
        Settings.from_env({})
    assert all(var in str(e.value) for var in REQUIRED.values())


def test_optional_settings_have_defaults():
    defaults = Settings.from_env({k: v for k, v in ENV.items() if k in REQUIRED.values()})
    assert (defaults.dependency_url, defaults.model) == (PAGERDUTY_URL, DEFAULT_MODEL)
    assert Settings.from_env({**ENV, "ANTHROPIC_MODEL": "claude-x"}).model == "claude-x"


def test_dependency_url_echoing_the_agent_resource_falls_back_to_pagerduty():
    # `keycard new agent` can write the agent's own resource as the dependency;
    # that is not the PagerDuty resource, so treat it as unset and fall back.
    echoed = {**ENV, "KEYCARD_DEPENDENCY_URL": ENV["KEYCARD_RESOURCE_URL"]}
    assert Settings.from_env(echoed).dependency_url == PAGERDUTY_URL


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
        return httpx.Response(200, json={"incidents": [INCIDENT], "more": False})

    result = await run_graph(handler)

    [request] = seen
    assert request.url.path == "/incidents"
    assert request.url.params.get_list("statuses[]") == ["triggered", "acknowledged"]
    assert request.headers["Authorization"] == "Token token=pagerduty-key"

    [tool_result] = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert json.loads(tool_result.content) == {
        "incidents": [
            {
                "number": 42,
                "title": "Checkout latency",
                "status": "triggered",
                "urgency": "high",
                "service": "checkout",
                "created_at": "2026-10-20T12:00:00Z",
                "url": "https://acme.pagerduty.test/incidents/P42",
            }
        ],
        "more": False,
    }
    assert result["messages"][-1].text == "#42 Checkout latency"
    assert not any("pagerduty-key" in str(m.content) for m in result["messages"])


async def test_pagerduty_errors_end_the_run():
    with pytest.raises(httpx.HTTPStatusError):
        await run_graph(lambda request: httpx.Response(401, json={"error": "unauthorized"}))


async def test_follows_pages_until_pagerduty_has_no_more():
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offsets.append(int(request.url.params["offset"]))
        return httpx.Response(200, json={"incidents": [INCIDENT], "more": len(offsets) < 3})

    result = await run_graph(handler)

    assert offsets == [0, 1, 2]
    [tool_result] = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert json.loads(tool_result.content)["more"] is False
    assert len(json.loads(tool_result.content)["incidents"]) == 3


async def test_stops_paging_at_the_cap_and_says_there_is_more():
    requested: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        requested.append((int(params["offset"]), int(params["limit"])))
        page = [INCIDENT] * int(params["limit"])
        return httpx.Response(200, json={"incidents": page, "more": True})

    result = await run_graph(handler)

    assert requested == [(offset, PAGE_SIZE) for offset in range(0, MAX_INCIDENTS, PAGE_SIZE)]
    [tool_result] = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    content = json.loads(tool_result.content)
    assert (len(content["incidents"]), content["more"]) == (MAX_INCIDENTS, True)


@pytest.fixture
def run_failing(monkeypatch):
    """Runs `make dev`'s entry point with `main` raising `error`."""
    monkeypatch.setattr(main, "load_dotenv", lambda: None)
    for var, value in ENV.items():
        monkeypatch.setenv(var, value)

    def run_with(error: Exception) -> None:
        async def fail(settings, **kwargs):
            raise error

        # `run()` now parses argv; keep pytest's own flags out of argparse.
        monkeypatch.setattr("sys.argv", ["getting-started-agent"])
        monkeypatch.setattr(main, "main", fail)
        main.run()

    return run_with


@pytest.mark.parametrize("status", [401, 403, 404])
def test_unwired_dependency_is_a_clean_exit(run_failing, capsys, status):
    run_failing(status_error(status))

    out = capsys.readouterr().out
    assert "the identity flow worked" in out
    assert f"returned {status}" in out


def test_missing_setting_is_a_clean_exit(run_failing, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    run_failing(AssertionError("main must not run"))

    assert "`ANTHROPIC_API_KEY` isn't set. Add it to `.env`" in capsys.readouterr().out


@pytest.mark.parametrize(
    "error",
    [
        status_error(500),
        httpx.ConnectError("boom", request=httpx.Request("GET", "https://api.pagerduty.test/x?y")),
        OAuthError("invalid_client: raw detail"),
        anthropic.APIError(
            "raw detail", httpx.Request("POST", "https://api.anthropic.test"), body=None
        ),
    ],
    ids=["dependency-5xx", "unreachable", "keycard", "model"],
)
def test_other_known_failures_exit_1_with_one_friendly_line(run_failing, error):
    with pytest.raises(SystemExit) as e:
        run_failing(error)

    message = str(e.value.code)
    assert "\n" not in message
    assert not any(raw in message for raw in ("raw detail", "statuses[]", "?y", "mozilla"))


def test_unexpected_errors_still_raise(run_failing):
    with pytest.raises(ValueError):
        run_failing(ValueError("a real bug"))


@pytest.mark.parametrize("interval", ["0", "-1", "4.9", "inf", "nan"])
def test_watch_rejects_intervals_that_would_flood_or_stall(monkeypatch, capsys, interval):
    monkeypatch.setattr("sys.argv", ["getting-started-agent", "--watch", "--interval", interval])
    with pytest.raises(SystemExit) as exit_info:
        main.run()
    assert exit_info.value.code == 2
    assert "--interval: must be at least 5 seconds" in capsys.readouterr().err
