import httpx
import pytest
from keycardai.oauth import TokenResponse, TokenType

from getting_started_agent.config import MissingSettingsError, Settings
from getting_started_agent.graph import build_graph
from getting_started_agent.keycard import authenticate_as_self

ENV = {
    "KEYCARD_ZONE_URL": "https://zone.keycard.test",
    "KEYCARD_CLIENT_ID": "agent",
    "KEYCARD_CLIENT_SECRET": "secret",
    "KEYCARD_RESOURCE_URL": "https://agent.example.com",
    "KEYCARD_DEPENDENCY_URL": "https://api.dependency.test",
}


class FakeKeycard:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def client_credentials_grant(self, **kwargs) -> TokenResponse:
        self.calls.append(("client_credentials", kwargs))
        return TokenResponse(access_token="own-token")

    async def exchange_token(self, **kwargs) -> TokenResponse:
        self.calls.append(("token_exchange", kwargs))
        return TokenResponse(access_token="dependency-token")


def test_settings_report_every_missing_key():
    with pytest.raises(MissingSettingsError) as e:
        Settings.from_env({"KEYCARD_ZONE_URL": "https://zone.keycard.test"})
    assert e.value.missing == [
        "KEYCARD_CLIENT_ID",
        "KEYCARD_CLIENT_SECRET",
        "KEYCARD_RESOURCE_URL",
        "KEYCARD_DEPENDENCY_URL",
    ]


def test_request_url_defaults_to_dependency():
    assert Settings.from_env(ENV).dependency_request_url == "https://api.dependency.test"
    custom = Settings.from_env({**ENV, "DEPENDENCY_REQUEST_URL": "https://api.dependency.test/me"})
    assert custom.dependency_request_url == "https://api.dependency.test/me"


async def test_authenticates_as_self_then_exchanges_for_dependency():
    keycard = FakeKeycard()
    tokens = await authenticate_as_self(keycard, Settings.from_env(ENV))

    assert keycard.calls == [
        ("client_credentials", {"resource": "https://agent.example.com"}),
        (
            "token_exchange",
            {
                "subject_token": "own-token",
                "subject_token_type": TokenType.ACCESS_TOKEN,
                "resource": "https://api.dependency.test",
            },
        ),
    ]
    assert tokens.dependency.access_token == "dependency-token"


async def test_graph_calls_dependency_with_exchanged_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="hello agent")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        graph = build_graph(Settings.from_env(ENV), FakeKeycard(), http)
        result = await graph.ainvoke({})

    assert result["status_code"] == 200
    assert result["body"] == "hello agent"
    assert str(seen[0].url) == "https://api.dependency.test"
    assert seen[0].headers["Authorization"] == "Bearer dependency-token"
