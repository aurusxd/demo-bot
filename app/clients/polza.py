import base64

from loguru import logger
from pydantic import BaseModel, ValidationError

from app.clients.base import HttpClient
from app.core.errors import ExternalServiceError

SERVICE = "polza"
# Speech-to-text is served only by this OpenAI-compatible endpoint (not by /media).
TRANSCRIPTIONS_PATH = "/audio/transcriptions"
STT_TIMEOUT_S = 30.0
STT_MAX_RETRIES = 1
STT_LANGUAGE = "ru"

# TODO(polza): image generation (slice 6) goes to POST /media with model
# google/gemini-3.1-flash-lite-image and provider "mie".


class _TranscriptionResponse(BaseModel):
    text: str


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
