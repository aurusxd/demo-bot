import asyncio
import base64
import io
import time

from loguru import logger
from PIL import Image
from pydantic import BaseModel, ValidationError

from app.clients.base import HttpClient
from app.core.errors import ExternalServiceError

SERVICE = "polza"
# Speech-to-text is served only by this OpenAI-compatible endpoint (not by /media).
TRANSCRIPTIONS_PATH = "/audio/transcriptions"
STT_TIMEOUT_S = 30.0
STT_MAX_RETRIES = 1
STT_LANGUAGE = "ru"

# Image generation (Nano Banana) goes through the Media API. Checked on the real API
# (2026-10-23): without "async" the POST waits ~30 s and answers "completed" with
# data = [{"url": ...}]; a "pending"/"processing" answer is polled via GET /media/{id}.
MEDIA_PATH = "/media"
IMAGE_TIMEOUT_S = 90.0
IMAGE_MAX_RETRIES = 1
IMAGE_POLL_INTERVAL_S = 3.0  # docs: poll images every 3-5 s
IMAGE_POLL_MAX_S = 120.0  # safety cap; the caller's budget is normally shorter
IMAGE_DOWNLOAD_TIMEOUT_S = 30.0
IMAGE_MIME = "image/jpeg"
IMAGE_OUTPUT_FORMAT = "jpeg"
IMAGE_MAX_BYTES = 10 * 1024 * 1024  # Telegram's limit for sendPhoto
RESULT_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
PENDING_STATUSES = frozenset({"pending", "processing"})
COMPLETED_STATUS = "completed"


class _TranscriptionResponse(BaseModel):
    text: str


class _MediaFile(BaseModel):
    url: str


class _MediaError(BaseModel):
    code: str | None = None
    message: str | None = None


class _MediaResponse(BaseModel):
    id: str
    status: str
    # Seen as a list; the docs' example shows an object. Accept both.
    data: list[_MediaFile] | _MediaFile | None = None
    error: _MediaError | None = None

    def first_url(self) -> str | None:
        files = self.data if isinstance(self.data, list) else [self.data] if self.data else []
        return files[0].url if files else None


class PolzaSpeechClient:
    """SpeechClient over Polza's /audio/transcriptions: JSON body, audio as a base64 data URL."""

    def __init__(self, http: HttpClient, model: str, provider: str | None = None) -> None:
        self._http = http
        self._model = model
        self._provider = provider

    @classmethod
    def create(
        cls, base_url: str, api_key: str, model: str, provider: str | None = None
    ) -> "PolzaSpeechClient":
        http = HttpClient(
            SERVICE,
            base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout_s=STT_TIMEOUT_S,
            max_retries=STT_MAX_RETRIES,
        )
        return cls(http, model, provider)

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        encoded = base64.b64encode(audio).decode("ascii")
        payload: dict[str, object] = {
            "model": self._model,
            "file": f"data:{mime_type};base64,{encoded}",
            "language": STT_LANGUAGE,
            "response_format": "json",
        }
        if self._provider:
            payload["provider"] = {"only": [self._provider]}
        response = await self._http.request("POST", TRANSCRIPTIONS_PATH, json=payload)
        try:
            parsed = _TranscriptionResponse.model_validate_json(response.content)
        except ValidationError as exc:
            logger.warning("polza stt unexpected response shape errors={}", exc.error_count())
            raise ExternalServiceError(SERVICE, "unexpected response shape") from exc
        return parsed.text

    async def close(self) -> None:
        await self._http.close()


class PolzaImageClient:
    """ImageGenClient over Polza's Media API: images as base64 data URLs, result by URL.

    `files` downloads the result from Polza's CDN: a separate client without the API key.
    """

    def __init__(
        self,
        http: HttpClient,
        files: HttpClient,
        model: str,
        provider: str | None = None,
        poll_interval_s: float = IMAGE_POLL_INTERVAL_S,
    ) -> None:
        self._http = http
        self._files = files
        self._model = model
        self._provider = provider
        self._poll_interval_s = poll_interval_s

    @classmethod
    def create(
        cls, base_url: str, api_key: str, model: str, provider: str | None = None
    ) -> "PolzaImageClient":
        http = HttpClient(
            SERVICE,
            base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout_s=IMAGE_TIMEOUT_S,
            max_retries=IMAGE_MAX_RETRIES,
        )
        files = HttpClient(
            f"{SERVICE}-cdn", "", timeout_s=IMAGE_DOWNLOAD_TIMEOUT_S, max_retries=IMAGE_MAX_RETRIES
        )
        return cls(http, files, model, provider)

    async def edit(self, room_photo: bytes, reference_photo: bytes, prompt: str) -> bytes:
        started = time.perf_counter()
        generation = await self._create(room_photo, reference_photo, prompt)
        generation = await self._wait(generation, started)
        url = generation.first_url()
        if generation.status != COMPLETED_STATUS or url is None:
            error = generation.error or _MediaError()
            logger.warning(
                "polza image failed generation_id={} status={} code={}",
                generation.id,
                generation.status,
                error.code,
            )
            raise ExternalServiceError(SERVICE, f"image {generation.status} code={error.code}")
        # The URL opens the client's photo: never logged.
        response = await self._files.request("GET", url, log_path="<result url>")
        image = response.content
        # A CDN error page with 200 would fail only at Telegram, with the generation counted.
        image_format = await asyncio.to_thread(_check_result, image)
        logger.info(
            "polza image done generation_id={} latency_ms={} bytes={} format={}",
            generation.id,
            round((time.perf_counter() - started) * 1000),
            len(image),
            image_format,
        )
        return image

    async def close(self) -> None:
        await self._http.close()
        await self._files.close()

    async def _create(
        self, room_photo: bytes, reference_photo: bytes, prompt: str
    ) -> _MediaResponse:
        payload: dict[str, object] = {
            "model": self._model,
            "input": {
                "prompt": prompt,
                "images": [_image_item(room_photo), _image_item(reference_photo)],
                "output_format": IMAGE_OUTPUT_FORMAT,
            },
        }
        if self._provider:
            payload["provider"] = {"only": [self._provider]}
        # Paid and not idempotent: after a read timeout the generation may still be running.
        response = await self._http.request("POST", MEDIA_PATH, json=payload, idempotent=False)
        generation = _parse_media(response.content)
        logger.info(
            "polza image created generation_id={} status={}", generation.id, generation.status
        )
        return generation

    async def _wait(self, generation: _MediaResponse, started: float) -> _MediaResponse:
        while generation.status in PENDING_STATUSES:
            if time.perf_counter() - started > IMAGE_POLL_MAX_S:
                raise ExternalServiceError(SERVICE, "image generation timed out")
            await asyncio.sleep(self._poll_interval_s)
            response = await self._http.request("GET", f"{MEDIA_PATH}/{generation.id}")
            generation = _parse_media(response.content)
        return generation


def _image_item(image: bytes) -> dict[str, str]:
    encoded = base64.b64encode(image).decode("ascii")
    return {"type": "base64", "data": f"data:{IMAGE_MIME};base64,{encoded}"}


def _check_result(image: bytes) -> str:
    """Blocking (Pillow): run in a worker thread. The result must be a sendable photo."""
    if not image or len(image) > IMAGE_MAX_BYTES:
        logger.warning("polza image rejected bytes={}", len(image))
        raise ExternalServiceError(SERVICE, f"bad image size {len(image)}")
    try:
        with Image.open(io.BytesIO(image)) as opened:
            image_format = opened.format
            opened.verify()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        logger.warning("polza image rejected error={}", type(exc).__name__)
        raise ExternalServiceError(SERVICE, f"bad image {type(exc).__name__}") from exc
    if image_format not in RESULT_FORMATS:
        logger.warning("polza image rejected format={}", image_format)
        raise ExternalServiceError(SERVICE, f"bad image format {image_format}")
    return image_format


def _parse_media(content: bytes) -> _MediaResponse:
    try:
        return _MediaResponse.model_validate_json(content)
    except ValidationError as exc:
        logger.warning("polza media unexpected response shape errors={}", exc.error_count())
        raise ExternalServiceError(SERVICE, "unexpected response shape") from exc
