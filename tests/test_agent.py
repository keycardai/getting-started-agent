import httpx
import pytest
from keycardai.oauth import TokenResponse, TokenType

from getting_started_agent.config import REQUIRED, Settings, SettingsError
from getting_started_agent.graph import build_graph
from getting_started_agent.keycard import dependency_token

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


def test_missing_settings_are_all_reported():
    with pytest.raises(SettingsError) as e:
        Settings.from_env({})
    assert all(var in str(e.value) for var in REQUIRED.values())


def test_request_url_defaults_to_dependency():
    assert Settings.from_env(ENV).dependency_request_url == ENV["KEYCARD_DEPENDENCY_URL"]


def test_client_secret_is_not_in_repr():
    assert "secret" not in repr(Settings.from_env(ENV)).replace("client_secret", "")


@pytest.mark.parametrize(
    "overrides",
    [
        {"KEYCARD_ZONE_URL": "http://zone.keycard.test"},
        {"KEYCARD_DEPENDENCY_URL": "http://api.dependency.test"},
        {"DEPENDENCY_REQUEST_URL": "https://elsewhere.test/me"},
    ],
    ids=["plaintext-zone", "plaintext-dependency", "foreign-request-origin"],
)
def test_rejects_settings_that_could_leak_credentials(overrides):
    with pytest.raises(SettingsError):
        Settings.from_env({**ENV, **overrides})


def test_allows_plain_http_to_loopback():
    local = {
        "KEYCARD_ZONE_URL": "http://localhost:8080",
        "KEYCARD_DEPENDENCY_URL": "http://127.0.0.1:9000",
        "DEPENDENCY_REQUEST_URL": "http://127.0.0.1:9000/me",
    }
    assert Settings.from_env({**ENV, **local}).dependency_request_url == "http://127.0.0.1:9000/me"


async def test_authenticates_as_self_then_exchanges_for_dependency():
    keycard = FakeKeycard()

    assert await dependency_token(keycard, Settings.from_env(ENV)) == "dependency-token"
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


async def test_graph_calls_dependency_with_exchanged_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="hello agent")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await build_graph(Settings.from_env(ENV), FakeKeycard(), http).ainvoke({})

    assert result == {"status_code": 200, "body": "hello agent"}
    assert str(seen[0].url) == ENV["KEYCARD_DEPENDENCY_URL"]
    assert seen[0].headers["Authorization"] == "Bearer dependency-token"
