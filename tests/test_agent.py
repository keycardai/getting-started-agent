import json

import anthropic
import httpx
import pytest
from keycardai.oauth import (
    AuthorizationServerDiscoveryError,
    NetworkError,
    OAuthError,
    OAuthProtocolError,
    TokenResponse,
    TokenType,
)
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from getting_started_agent import keycard as keycard_module
from getting_started_agent import main, vercel
from getting_started_agent.config import (
    CLIENT_CREDENTIALS,
    DEFAULT_MODEL,
    PAGERDUTY_URL,
    REQUIRED,
    Settings,
    SettingsError,
)
from getting_started_agent.graph import build_graph
from getting_started_agent.keycard import (
    JWT_BEARER,
    DependencyAccessError,
    MissingDependencyError,
    PolicyDeniedError,
    UnknownDependencyError,
    WorkloadIdentityError,
    dependency_token,
)
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
    needed = {**REQUIRED, **CLIENT_CREDENTIALS}.values()
    defaults = Settings.from_env({k: v for k, v in ENV.items() if k in needed})
    assert (defaults.dependency_url, defaults.model) == (PAGERDUTY_URL, DEFAULT_MODEL)
    assert Settings.from_env({**ENV, "ANTHROPIC_MODEL": "claude-x"}).model == "claude-x"


def test_dependency_url_echoing_the_agent_resource_falls_back_to_pagerduty(capsys):
    # `keycard new agent` can write the agent's own resource as the dependency;
    # that is not the PagerDuty resource, so treat it as unset and fall back.
    for echo in (ENV["KEYCARD_RESOURCE_URL"], ENV["KEYCARD_RESOURCE_URL"] + "/"):
        echoed = {**ENV, "KEYCARD_DEPENDENCY_URL": echo}
        assert Settings.from_env(echoed).dependency_url == PAGERDUTY_URL
        assert "is the agent's own resource" in capsys.readouterr().err


def test_dependency_url_is_used_exactly_as_registered():
    registered = {**ENV, "KEYCARD_DEPENDENCY_URL": "https://api.pagerduty.com/"}
    assert Settings.from_env(registered).dependency_url == "https://api.pagerduty.com/"


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


# Deployed to Fly: the Machine's identity stands in for the client secret.
FLY_ENV = {
    k: v for k, v in ENV.items() if k not in ("KEYCARD_CLIENT_ID", "KEYCARD_CLIENT_SECRET")
} | {"FLY_APP_NAME": "agent-x"}


def test_on_fly_the_client_secret_is_not_required():
    settings = Settings.from_env(FLY_ENV)
    assert settings.host == "fly"
    assert (settings.client_id, settings.client_secret) == (None, None)


def test_on_fly_a_shipped_client_secret_is_ignored():
    # A .env that leaked into the image must not turn into Basic auth.
    settings = Settings.from_env({**FLY_ENV, **ENV})
    assert settings.host == "fly"
    assert settings.client_secret is None


def test_on_fly_missing_settings_point_at_fly_secrets():
    with pytest.raises(SettingsError) as e:
        Settings.from_env({"FLY_APP_NAME": "agent-x", "KEYCARD_ZONE_URL": ENV["KEYCARD_ZONE_URL"]})
    message = str(e.value)
    assert "fly secrets set" in message
    assert "make dev" not in message
    assert "KEYCARD_CLIENT_SECRET" not in message


def test_locally_the_client_secret_is_still_required():
    with pytest.raises(SettingsError) as e:
        Settings.from_env({k: v for k, v in ENV.items() if k != "KEYCARD_CLIENT_SECRET"})
    assert "KEYCARD_CLIENT_SECRET" in str(e.value)


async def test_on_fly_both_requests_carry_the_machine_token(monkeypatch):
    asked_for: list[str] = []

    async def fake_fly_token(audience: str) -> str:
        asked_for.append(audience)
        return "fly-oidc-jwt"

    monkeypatch.setattr(keycard_module, "fly_identity_token", fake_fly_token)
    keycard = FakeKeycard()

    assert await dependency_token(keycard, Settings.from_env(FLY_ENV)) == "pagerduty-key"
    # One token per run, minted for the zone, presented on both requests.
    assert asked_for == [ENV["KEYCARD_ZONE_URL"]]
    proof = {"client_assertion": "fly-oidc-jwt", "client_assertion_type": JWT_BEARER}
    assert keycard.calls == [
        ("client_credentials", {"resource": ENV["KEYCARD_RESOURCE_URL"], **proof}),
        (
            "token_exchange",
            {
                "subject_token": "own-token",
                "subject_token_type": TokenType.ACCESS_TOKEN,
                "resource": ENV["KEYCARD_DEPENDENCY_URL"],
                **proof,
            },
        ),
    ]
    # And no client id went over the wire: the zone resolves it from the token.
    assert not any("client_id" in kwargs for _, kwargs in keycard.calls)


async def test_fly_token_fetch_failure_is_a_workload_identity_error(monkeypatch):
    class NoSocket(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("no such file", request=request)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", NoSocket)
    with pytest.raises(WorkloadIdentityError):
        await keycard_module.fly_identity_token("https://zone.keycard.test")


# Deployed to Vercel: each request's OIDC header stands in for the client secret.
VERCEL_ENV = {
    k: v for k, v in ENV.items() if k not in ("KEYCARD_CLIENT_ID", "KEYCARD_CLIENT_SECRET")
} | {"VERCEL": "1", "VERCEL_OIDC_TOKEN": "vercel-oidc-jwt"}


def test_on_vercel_a_shipped_client_secret_is_ignored():
    settings = Settings.from_env({**VERCEL_ENV, **ENV})
    assert settings.host == "vercel"
    assert settings.client_secret is None
    assert "vercel-oidc-jwt" not in repr(settings)


def test_on_vercel_missing_settings_point_at_env_push():
    with pytest.raises(SettingsError) as e:
        Settings.from_env({"VERCEL": "1", "KEYCARD_ZONE_URL": ENV["KEYCARD_ZONE_URL"]})
    assert "make env-push" in str(e.value)
    assert "fly secrets" not in str(e.value)


async def test_on_vercel_both_requests_carry_the_request_token():
    keycard = FakeKeycard()
    assert await dependency_token(keycard, Settings.from_env(VERCEL_ENV)) == "pagerduty-key"
    proof = {"client_assertion": "vercel-oidc-jwt", "client_assertion_type": JWT_BEARER}
    assert all(proof.items() <= kwargs.items() for _, kwargs in keycard.calls)


async def test_on_vercel_a_missing_token_is_a_workload_identity_error():
    settings = Settings.from_env({k: v for k, v in VERCEL_ENV.items() if k != "VERCEL_OIDC_TOKEN"})
    with pytest.raises(WorkloadIdentityError):
        await dependency_token(FakeKeycard(), settings)


@pytest.fixture
def call_vercel(monkeypatch):
    """Calls the Vercel entrypoint with `once` replaced by `cycle`."""
    for var, value in VERCEL_ENV.items():
        if var != "VERCEL_OIDC_TOKEN":
            monkeypatch.setenv(var, value)
    monkeypatch.delenv("CRON_SECRET", raising=False)

    def call(cycle, path="/api/run", **headers) -> tuple[str, str]:
        async def fake_once(settings):
            return cycle(settings)

        monkeypatch.setattr(vercel, "once", fake_once)
        started: list[str] = []
        body = vercel.app({"PATH_INFO": path, **headers}, lambda s, h: started.append(s))
        return started[0], b"".join(body).decode()

    return call


def test_vercel_run_uses_the_request_token_and_keeps_the_summary_in_the_logs(call_vercel, capsys):
    seen: list[Settings] = []
    status, body = call_vercel(
        lambda s: seen.append(s) or "2 open incidents", HTTP_X_VERCEL_OIDC_TOKEN="jwt"
    )
    assert status == "200 OK"
    assert seen[0].vercel_oidc_token == "jwt"
    assert "2 open incidents" not in body
    assert "2 open incidents" in capsys.readouterr().out


def test_vercel_only_serves_the_run_path(call_vercel):
    assert call_vercel(lambda s: "", path="/")[0] == "404 Not Found"


def test_vercel_run_requires_the_cron_secret_when_set(call_vercel, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    assert call_vercel(lambda s: "")[0] == "401 Unauthorized"
    assert call_vercel(lambda s: "", HTTP_AUTHORIZATION="Bearer s3cret")[0] == "200 OK"


def test_vercel_run_explains_a_known_failure(call_vercel):
    def fail(settings):
        raise MissingDependencyError(PAGERDUTY_URL, "access_denied")

    status, body = call_vercel(fail)
    assert status == "200 OK"
    assert "no dependency" in body


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


def refused(error: str, description: str | None = None) -> OAuthProtocolError:
    return OAuthProtocolError(error, description, operation="POST /token (exchange)")


# The refusals svc-sts returns for an exchange, with the descriptions it sends.
# A policy denial names the deciding policy; a denial with none named means no
# policy permitted it, and for an agent acting as itself the zone's built-in
# policy permits only its declared dependencies.
NO_DEPENDENCY = 'Application "agent" is not allowed to access "PagerDuty".'
FORBIDDEN = 'Access to "PagerDuty" is denied by Policy "no-weekend-pages".'
FORBIDDEN_FALLBACK = (
    "Access denied by policy. Policy set: ps-1. Policy set version: psv-1. "
    "Determining policies: no-weekend-pages."
)
UNKNOWN_RESOURCE = 'Requested authorization for unknown resource "https://api.pagerduty.test".'
NO_SECRET = "No secrets found for resource 'https://api.pagerduty.test'."


@pytest.mark.parametrize(
    ("error", "refusal"),
    [
        (refused("access_denied", NO_DEPENDENCY), MissingDependencyError),
        (refused("access_denied"), MissingDependencyError),
        (refused("access_denied", FORBIDDEN), PolicyDeniedError),
        (refused("access_denied", FORBIDDEN_FALLBACK), PolicyDeniedError),
        (refused("invalid_target", UNKNOWN_RESOURCE), UnknownDependencyError),
        (refused("invalid_target", NO_SECRET), UnknownDependencyError),
        (refused("insufficient_authorization", "raw detail"), DependencyAccessError),
    ],
    ids=[
        "no-dependency",
        "no-description",
        "policy",
        "policy-fallback",
        "unknown-resource",
        "no-api-key",
        "other-code",
    ],
)
async def test_refused_exchange_is_classified_by_error_code(error, refusal):
    # The identity grant succeeds; only the exchange is refused.
    class Refusing(FakeKeycard):
        async def exchange_token(self, **kwargs) -> TokenResponse:
            self.calls.append(("token_exchange", kwargs))
            raise error

    keycard = Refusing()
    with pytest.raises(DependencyAccessError) as e:
        await dependency_token(keycard, Settings.from_env(ENV))

    assert type(e.value) is refusal
    assert e.value.dependency_url == ENV["KEYCARD_DEPENDENCY_URL"]
    assert e.value.code == error.error
    # It got as far as the exchange, so the client-credentials grant ran first.
    assert [name for name, _ in keycard.calls] == ["client_credentials", "token_exchange"]


async def test_unreachable_zone_during_the_exchange_is_not_a_refusal():
    class Unreachable(FakeKeycard):
        async def exchange_token(self, **kwargs) -> TokenResponse:
            raise NetworkError(httpx.ConnectError("Name or service not known"))

    with pytest.raises(NetworkError):
        await dependency_token(Unreachable(), Settings.from_env(ENV))


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


def test_pagerduty_404_is_an_error_that_points_at_the_url(run_failing, capsys):
    with pytest.raises(SystemExit) as e:
        run_failing(status_error(404))

    message = str(e.value.code)
    assert "PagerDuty returned 404" in message
    assert "`KEYCARD_DEPENDENCY_URL`" in message
    assert PAGERDUTY_URL in message
    assert "identity flow worked" not in message
    assert capsys.readouterr().out == ""


def test_missing_dependency_is_a_clean_exit_that_points_at_adding_it(run_failing, capsys):
    run_failing(MissingDependencyError(ENV["KEYCARD_DEPENDENCY_URL"], "access_denied"))

    out = capsys.readouterr().out
    assert "Authenticated to Keycard as the agent" in out
    assert "no dependency" in out
    assert "Add that resource as a dependency" in out
    assert ENV["KEYCARD_DEPENDENCY_URL"] in out


def test_unknown_dependency_is_a_clean_exit_that_points_at_adding_it(run_failing, capsys):
    run_failing(UnknownDependencyError(ENV["KEYCARD_DEPENDENCY_URL"], "invalid_target"))

    out = capsys.readouterr().out
    assert "no resource at " + ENV["KEYCARD_DEPENDENCY_URL"] in out
    assert "store its API key" in out


def test_policy_denial_says_a_policy_denied_it_not_to_add_a_dependency(run_failing):
    denial = PolicyDeniedError(ENV["KEYCARD_DEPENDENCY_URL"], "access_denied", FORBIDDEN)
    with pytest.raises(SystemExit) as e:
        run_failing(denial)

    message = str(e.value.code)
    assert "a policy in your zone denied the exchange" in message
    assert '"no-weekend-pages"' in message
    assert "dependency" not in message


def test_other_refusals_exit_1_with_the_error_code(run_failing):
    refusal = DependencyAccessError(ENV["KEYCARD_DEPENDENCY_URL"], "insufficient_authorization")
    with pytest.raises(SystemExit) as e:
        run_failing(refusal)

    message = str(e.value.code)
    assert "`insufficient_authorization`" in message
    assert "dependency" not in message


@pytest.mark.parametrize(
    "error",
    [
        NetworkError(httpx.ConnectError("Name or service not known")),
        AuthorizationServerDiscoveryError(ENV["KEYCARD_ZONE_URL"]),
    ],
    ids=["network", "discovery"],
)
def test_unreachable_zone_says_so_instead_of_blaming_the_secret(run_failing, error):
    with pytest.raises(SystemExit) as e:
        run_failing(error)
    message = str(e.value.code)
    assert "Couldn't reach Keycard" in message
    assert "KEYCARD_CLIENT_SECRET" not in message


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_key_is_a_clean_exit_that_points_at_the_key(run_failing, capsys, status):
    run_failing(status_error(status))

    out = capsys.readouterr().out
    assert "the identity flow worked" in out
    assert f"PagerDuty rejected the key with {status}" in out
    assert "expected" not in out


def test_ctrl_c_is_a_clean_stop(run_failing):
    run_failing(KeyboardInterrupt())


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


async def test_watch_survives_a_bad_cycle_with_a_friendly_line(monkeypatch, capsys):
    answers = iter([status_error(500), {"messages": [AIMessage("All quiet.")]}])

    class Graph:
        async def ainvoke(self, _):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

    class Stop(Exception):
        pass

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise Stop

    monkeypatch.setattr(main.asyncio, "sleep", sleep)
    with pytest.raises(Stop):
        await main.watch(Graph(), 10)

    out = capsys.readouterr().out
    assert sleeps == [10, 10]
    assert "PagerDuty returned 500" in out
    assert "All quiet." in out
    assert "mozilla" not in out
