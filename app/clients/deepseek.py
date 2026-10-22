from collections.abc import Sequence

from loguru import logger
from pydantic import BaseModel, ValidationError

from app.clients.base import ChatMessage, HttpClient
from app.core.errors import ExternalServiceError

SERVICE = "deepseek"
CHAT_PATH = "/chat/completions"
TIMEOUT_S = 60.0
TEMPERATURE = 0.3
MAX_TOKENS = 800


class _ResponseMessage(BaseModel):
    content: str


class _Choice(BaseModel):
    message: _ResponseMessage


class _ChatResponse(BaseModel):
    choices: list[_Choice]


class DeepSeekClient:
    """LLMClient over DeepSeek's OpenAI-compatible chat API in JSON mode."""

    def __init__(self, http: HttpClient, model: str) -> None:
        self._http = http
        self._model = model

    @classmethod
    def create(cls, base_url: str, api_key: str, model: str) -> "DeepSeekClient":
        http = HttpClient(
            SERVICE,
            base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout_s=TIMEOUT_S,
        )
        return cls(http, model)

    async def complete_json(self, messages: Sequence[ChatMessage]) -> str:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "response_format": {"type": "json_object"},
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
        }
        response = await self._http.request("POST", CHAT_PATH, json=payload)
        try:
            parsed = _ChatResponse.model_validate_json(response.content)
        except ValidationError as exc:
            logger.warning("deepseek unexpected response shape errors={}", exc.error_count())
            raise ExternalServiceError(SERVICE, "unexpected response shape") from exc
        if not parsed.choices:
            raise ExternalServiceError(SERVICE, "empty choices")
        return parsed.choices[0].message.content

    async def close(self) -> None:
        await self._http.close()
