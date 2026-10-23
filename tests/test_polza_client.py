import base64
import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from loguru import logger

from app.clients.base import HttpClient
from app.clients.polza import IMAGE_TIMEOUT_S, STT_TIMEOUT_S, PolzaImageClient, PolzaSpeechClient
from app.core.errors import ExternalServiceError
from tests.fakes import image_bytes

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


# --- image generation (Media API) ---

MEDIA_URL = f"{BASE_URL}/media"
RESULT_URL = "https://cdn.polza.test/f/1/result.jpg"
ROOM, REFERENCE, RESULT = b"room-jpeg", b"reference-jpeg", image_bytes("JPEG")


def make_image_client(provider: str | None = "mie") -> PolzaImageClient:
    http = HttpClient("polza", BASE_URL, headers={"Authorization": "Bearer pk"}, backoff_s=0)
    files = HttpClient("polza-cdn", "", backoff_s=0)
    return PolzaImageClient(http, files, "google/gemini-3.1-flash-lite-image", provider, 0)


def media(status: str, data: object = None, error: object = None) -> dict[str, object]:
    return {
        "id": "gen_1",
        "object": "media.generation",
        "status": status,
        "data": data,
        "error": error,
    }


@respx.mock
async def test_edit_sends_both_images_and_downloads_result() -> None:
    route = respx.post(MEDIA_URL).mock(
        return_value=httpx.Response(200, json=media("completed", [{"url": RESULT_URL}]))
    )
    cdn = respx.get(RESULT_URL).mock(return_value=httpx.Response(200, content=RESULT))
    client = make_image_client()

    image = await client.edit(ROOM, REFERENCE, "put it in")

    assert image == RESULT
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "google/gemini-3.1-flash-lite-image"
    assert body["provider"] == {"only": ["mie"]}
    assert body["input"]["prompt"] == "put it in"
    assert body["input"]["images"] == [
        {"type": "base64", "data": "data:image/jpeg;base64," + base64.b64encode(ROOM).decode()},
        {
            "type": "base64",
            "data": "data:image/jpeg;base64," + base64.b64encode(REFERENCE).decode(),
        },
    ]
    # The CDN link is downloaded without the API key.
    assert "Authorization" not in cdn.calls.last.request.headers
    await client.close()


@respx.mock
async def test_pending_generation_is_polled() -> None:
    respx.post(MEDIA_URL).mock(return_value=httpx.Response(200, json=media("pending")))
    poll = respx.get(f"{MEDIA_URL}/gen_1").mock(
        side_effect=[
            httpx.Response(200, json=media("processing")),
            httpx.Response(200, json=media("completed", {"url": RESULT_URL})),
        ]
    )
    respx.get(RESULT_URL).mock(return_value=httpx.Response(200, content=RESULT))
    client = make_image_client(provider=None)

    assert await client.edit(ROOM, REFERENCE, "p") == RESULT
    assert poll.call_count == 2
    await client.close()


@respx.mock
@pytest.mark.parametrize(
    "payload",
    [
        media("failed", error={"code": "BAD_GATEWAY", "message": "упс"}),
        media("completed", []),  # e.g. the model refused and answered with text only
        {"status": "completed"},  # no id: unexpected shape
    ],
)
async def test_failed_or_empty_generation_raises(payload: dict[str, object]) -> None:
    respx.post(MEDIA_URL).mock(return_value=httpx.Response(200, json=payload))
    client = make_image_client()

    with pytest.raises(ExternalServiceError):
        await client.edit(ROOM, REFERENCE, "p")
    await client.close()


@respx.mock
async def test_image_server_error_is_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpClient, "_sleep_before_retry", AsyncMock())  # no real backoff
    route = respx.post(MEDIA_URL).mock(return_value=httpx.Response(503))
    client = PolzaImageClient.create(BASE_URL, "pk", "model", "mie")

    with pytest.raises(ExternalServiceError):
        await client.edit(ROOM, REFERENCE, "p")

    assert route.call_count == 2
    assert route.calls.last.request.extensions["timeout"]["read"] == IMAGE_TIMEOUT_S
    await client.close()


@respx.mock
async def test_result_url_is_not_logged() -> None:
    respx.post(MEDIA_URL).mock(
        return_value=httpx.Response(200, json=media("completed", [{"url": RESULT_URL}]))
    )
    respx.get(RESULT_URL).mock(return_value=httpx.Response(200, content=RESULT))
    client = make_image_client()
    lines: list[str] = []
    sink_id = logger.add(lines.append, format="{message}")
    try:
        await client.edit(ROOM, REFERENCE, "p")
    finally:
        logger.remove(sink_id)

    assert any("<result url>" in line for line in lines)
    assert all(RESULT_URL not in line for line in lines)
    await client.close()


@respx.mock
async def test_image_read_timeout_is_not_retried() -> None:
    # The generation may still be running (and billed): a second POST would pay twice.
    route = respx.post(MEDIA_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    client = make_image_client()

    with pytest.raises(ExternalServiceError):
        await client.edit(ROOM, REFERENCE, "p")

    assert route.call_count == 1
    await client.close()


@respx.mock
@pytest.mark.parametrize(
    "content",
    [b"", b"<html>Error</html>", image_bytes("GIF"), image_bytes("JPEG")[:40]],
)
async def test_broken_result_is_rejected(content: bytes) -> None:
    respx.post(MEDIA_URL).mock(
        return_value=httpx.Response(200, json=media("completed", [{"url": RESULT_URL}]))
    )
    respx.get(RESULT_URL).mock(return_value=httpx.Response(200, content=content))
    client = make_image_client()

    with pytest.raises(ExternalServiceError):
        await client.edit(ROOM, REFERENCE, "p")
    await client.close()


@respx.mock
async def test_oversized_result_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.clients.polza.IMAGE_MAX_BYTES", len(RESULT) - 1)
    respx.post(MEDIA_URL).mock(
        return_value=httpx.Response(200, json=media("completed", [{"url": RESULT_URL}]))
    )
    respx.get(RESULT_URL).mock(return_value=httpx.Response(200, content=RESULT))
    client = make_image_client()

    with pytest.raises(ExternalServiceError):
        await client.edit(ROOM, REFERENCE, "p")
    await client.close()
