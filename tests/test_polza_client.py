import base64
import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.clients.base import HttpClient
from app.clients.polza import STT_TIMEOUT_S, PolzaSpeechClient
from app.core.errors import ExternalServiceError

BASE_URL = "https://polza.test/api/v1"
URL = f"{BASE_URL}/audio/transcriptions"
AUDIO = b"OggS-fake-voice"


def make_client(provider: str | None = "openai") -> PolzaSpeechClient:
    http = HttpClient("polza", BASE_URL, headers={"Authorization": "Bearer pk"}, backoff_s=0)
    return PolzaSpeechClient(http, "openai/whisper-1", provider)


@respx.mock
async def test_transcribe_sends_base64_audio_and_returns_text() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json={"text": "Хочу кухню", "duration": 2.5})
    )
    client = make_client()

    text = await client.transcribe(AUDIO, "audio/ogg")

    assert text == "Хочу кухню"
    request = route.calls.last.request
    body = json.loads(request.content)
    assert body["model"] == "openai/whisper-1"
    assert body["file"] == "data:audio/ogg;base64," + base64.b64encode(AUDIO).decode()
    assert body["language"] == "ru"
    assert body["provider"] == {"only": ["openai"]}
    assert request.headers["Authorization"] == "Bearer pk"
    await client.close()


@respx.mock
async def test_provider_is_omitted_when_not_configured() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"text": "ок"}))
    client = make_client(provider=None)

    await client.transcribe(AUDIO, "audio/ogg")

    assert "provider" not in json.loads(route.calls.last.request.content)
    await client.close()


@respx.mock
@pytest.mark.parametrize("payload", [{"error": "x"}, {"text": None}, []])
async def test_unexpected_shape_raises(payload: object) -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json=payload))
    client = make_client()

    with pytest.raises(ExternalServiceError):
        await client.transcribe(AUDIO, "audio/ogg")
    await client.close()


@respx.mock
async def test_client_error_is_not_retried() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(400, json={"error": "bad"}))
    client = make_client()

    with pytest.raises(ExternalServiceError) as exc_info:
        await client.transcribe(AUDIO, "audio/ogg")

    assert exc_info.value.status_code == 400
    assert route.call_count == 1
    await client.close()


@respx.mock
async def test_production_client_retries_once_with_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(HttpClient, "_sleep_before_retry", AsyncMock())  # no real backoff
    route = respx.post(URL).mock(return_value=httpx.Response(502))
    client = PolzaSpeechClient.create(BASE_URL, "pk", "openai/whisper-1", "openai")

    with pytest.raises(ExternalServiceError):
        await client.transcribe(AUDIO, "audio/ogg")

    assert route.call_count == 2
    assert route.calls.last.request.extensions["timeout"]["read"] == STT_TIMEOUT_S
    await client.close()
