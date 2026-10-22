import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.clients.base import ChatMessage, HttpClient
from app.clients.deepseek import TIMEOUT_S, DeepSeekClient
from app.core.errors import ExternalServiceError

BASE_URL = "https://api.deepseek.test"
URL = f"{BASE_URL}/chat/completions"
MESSAGES = [ChatMessage("system", "sys"), ChatMessage("user", "хочу кухню")]


def make_client() -> DeepSeekClient:
    http = HttpClient(
        "deepseek", BASE_URL, headers={"Authorization": "Bearer k"}, max_retries=1, backoff_s=0
    )
    return DeepSeekClient(http, "deepseek-chat")


def completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@respx.mock
async def test_returns_content_and_requests_json_mode() -> None:
    route = respx.post(URL).mock(return_value=completion('{"reply": "ok"}'))
    client = make_client()

    content = await client.complete_json(MESSAGES)

    assert content == '{"reply": "ok"}'
    request = route.calls.last.request
    body = json.loads(request.content)
    assert body["model"] == "deepseek-chat"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][1] == {"role": "user", "content": "хочу кухню"}
    assert request.headers["Authorization"] == "Bearer k"
    await client.close()


@respx.mock
async def test_server_error_raises_after_retries() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(503))
    client = make_client()

    with pytest.raises(ExternalServiceError):
        await client.complete_json(MESSAGES)

    assert route.call_count == 2
    await client.close()


@respx.mock
@pytest.mark.parametrize("payload", [{"choices": []}, {"error": "x"}, {"choices": [{}]}])
async def test_unexpected_shape_raises(payload: object) -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json=payload))
    client = make_client()

    with pytest.raises(ExternalServiceError):
        await client.complete_json(MESSAGES)
    await client.close()


@respx.mock
async def test_production_client_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review #2: 30 s timeout and a single retry instead of 60 s x 3 attempts."""
    monkeypatch.setattr(HttpClient, "_sleep_before_retry", AsyncMock())  # no real backoff
    route = respx.post(URL).mock(return_value=httpx.Response(503))
    client = DeepSeekClient.create(BASE_URL, "k", "deepseek-chat")

    with pytest.raises(ExternalServiceError):
        await client.complete_json(MESSAGES)

    assert route.call_count == 2
    assert route.calls.last.request.extensions["timeout"]["read"] == TIMEOUT_S == 30.0
    await client.close()
