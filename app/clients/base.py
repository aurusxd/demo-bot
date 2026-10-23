import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx
from loguru import logger

from app.core.errors import ExternalServiceError
from app.schemas.lead import LeadPayload

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_S = 1.0
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# Multipart file: (filename, content, content_type).
FileField = tuple[str, bytes, str]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


# Interfaces. Signatures are refined in the slice that implements each client.


class LLMClient(Protocol):
    async def complete_json(self, messages: Sequence[ChatMessage]) -> str:
        """Return the raw JSON string produced by the model."""
        ...


class ImageGenClient(Protocol):
    async def edit(self, room_photo: bytes, reference_photo: bytes, prompt: str) -> bytes:
        """Generate one image from two JPEGs (room + catalog reference); return its bytes."""
        ...


class SpeechClient(Protocol):
    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        """Return the recognized text (may be empty for silence)."""
        ...


class FileDownloader(Protocol):
    async def download(self, file_id: str) -> bytes:
        """Download a Telegram file into memory (never to disk)."""
        ...


class CrmClient(Protocol):
    async def send(self, lead: LeadPayload) -> None:
        """Deliver a qualified lead; raise ExternalServiceError on failure."""
        ...


class LeadNotifier(Protocol):
    async def notify(self, card_html: str, photo_file_ids: Sequence[str]) -> None:
        """Send the lead card (and its photos) to the managers' chat."""
        ...

    async def notify_photo(self, caption_html: str, file_id: str) -> None:
        """Send one photo with a caption to the managers' chat."""
        ...


class HttpClient:
    """Shared async HTTP client: timeouts, retries with backoff, one log line per attempt."""

    def __init__(
        self,
        service: str,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_s: float = DEFAULT_BACKOFF_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._service = service
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=dict(headers or {}),
            timeout=timeout_s,
            transport=transport,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, FileField] | None = None,
        timeout_s: float | None = None,
        log_path: str | None = None,
    ) -> httpx.Response:
        """`log_path` replaces `path` in logs: for URLs that grant access (signed, file links)."""
        attempts = self._max_retries + 1
        logged_path = log_path or path
        timeout = timeout_s if timeout_s is not None else httpx.USE_CLIENT_DEFAULT
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                response = await self._client.request(
                    method, path, json=json, data=data, files=files, timeout=timeout
                )
            except httpx.TransportError as exc:
                self._log_failure(method, logged_path, attempt, started, type(exc).__name__)
                if attempt == attempts:
                    raise ExternalServiceError(self._service, type(exc).__name__) from exc
                await self._sleep_before_retry(attempt)
                continue

            self._log_response(method, logged_path, attempt, started, response)
            if response.status_code in RETRYABLE_STATUSES and attempt < attempts:
                await self._sleep_before_retry(attempt)
                continue
            if response.is_error:
                raise ExternalServiceError(
                    self._service, f"HTTP {response.status_code}", response.status_code
                )
            return response

        raise AssertionError("unreachable")  # loop always returns or raises

    async def close(self) -> None:
        await self._client.aclose()

    async def _sleep_before_retry(self, attempt: int) -> None:
        delay = self._backoff_s * attempt
        logger.warning("http retry service={} attempt={} delay_s={}", self._service, attempt, delay)
        await asyncio.sleep(delay)

    def _log_response(
        self, method: str, path: str, attempt: int, started: float, response: httpx.Response
    ) -> None:
        logger.info(
            "http call service={} {} {} status={} latency_ms={} attempt={} "
            "req_bytes={} resp_bytes={}",
            self._service,
            method,
            path,
            response.status_code,
            _elapsed_ms(started),
            attempt,
            response.request.headers.get("content-length", "0"),
            len(response.content),
        )

    def _log_failure(
        self, method: str, path: str, attempt: int, started: float, error: str
    ) -> None:
        logger.warning(
            "http call service={} {} {} failed error={} latency_ms={} attempt={}",
            self._service,
            method,
            path,
            error,
            _elapsed_ms(started),
            attempt,
        )


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
