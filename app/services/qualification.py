import json
from dataclasses import dataclass, field

from loguru import logger
from pydantic import ValidationError

from app.clients.base import ChatMessage, LLMClient
from app.core.errors import ExternalServiceError, InvalidInputError
from app.db.models import Lead, LeadStatus, Message, MessageKind, MessageRole, User
from app.db.repositories import LeadRepository, MessageRepository
from app.prompts.qualification import CURRENT_SLOTS_TEMPLATE, INVALID_JSON_HINT, SYSTEM_PROMPT
from app.schemas.qualification import LLMReply

HISTORY_LIMIT = 12
MAX_USER_TEXT_LEN = 1000
SLOT_FIELDS = (
    "name",
    "furniture_type",
    "dimensions",
    "style",
    "budget",
    "city",
    "deadline",
    "phone",
    "comment",
    "business_role",
    "lead_sources",
)
REQUIRED_SLOTS = ("furniture_type", "dimensions", "phone")


@dataclass(frozen=True, slots=True)
class TurnResult:
    reply: str | None  # None -> LLM failed, the bot answers with a fallback text
    qualified: bool = False
    slots: dict[str, str] = field(default_factory=dict[str, str])


class QualificationService:
    """Lead qualification dialog: the LLM proposes, the code validates and applies."""

    def __init__(self, llm: LLMClient, leads: LeadRepository, messages: MessageRepository) -> None:
        self._llm = llm
        self._leads = leads
        self._messages = messages

    async def start(self, user: User, greeting: str) -> None:
        """Fresh dialog for /start: a new draft lead whose history opens with the greeting."""
        lead = await self._create_draft(user)
        await self._save_message(lead, MessageRole.ASSISTANT, greeting)
        logger.info("qualification started lead_id={}", lead.id)

    async def handle_message(self, user: User, text: str) -> TurnResult:
        text = text.strip()
        if not text or len(text) > MAX_USER_TEXT_LEN:
            logger.info("qualification input rejected text_len={}", len(text))
            raise InvalidInputError(f"text length {len(text)}")

        lead = await self._get_or_create_draft(user)
        with logger.contextualize(lead_id=lead.id):
            await self._save_message(lead, MessageRole.USER, text)
            reply = await self._ask_llm(lead)
            if reply is None:
                return TurnResult(reply=None, slots=_lead_slots(lead))

            updated = await self._apply_slots(lead, reply)
            await self._save_message(lead, MessageRole.ASSISTANT, reply.reply)
            qualified = reply.ready and _required_filled(lead)
            if qualified:
                await self._set_status(lead, LeadStatus.QUALIFIED)
            logger.info(
                "qualification turn done updated_slots={} llm_ready={} qualified={}",
                updated,
                reply.ready,
                qualified,
            )
            return TurnResult(reply=reply.reply, qualified=qualified, slots=_lead_slots(lead))

    async def _get_or_create_draft(self, user: User) -> Lead:
        lead = await self._leads.get_latest_draft(user.id)
        return lead if lead is not None else await self._create_draft(user)

    async def _create_draft(self, user: User) -> Lead:
        lead = await self._leads.add(Lead(user_id=user.id, status=LeadStatus.DRAFT))
        logger.info("lead created lead_id={} status={}", lead.id, lead.status)
        return lead

    async def _ask_llm(self, lead: Lead) -> LLMReply | None:
        history = await self._messages.get_last(lead.id, HISTORY_LIMIT)
        prompt = [
            ChatMessage("system", SYSTEM_PROMPT),
            ChatMessage("system", CURRENT_SLOTS_TEMPLATE.format(slots=_slots_json(lead))),
            *(_to_chat_message(m) for m in history),
        ]
        for attempt in (1, 2):
            try:
                raw = await self._llm.complete_json(prompt)
            except ExternalServiceError:
                logger.exception("llm call failed attempt={}", attempt)
                return None
            try:
                return LLMReply.model_validate_json(raw)
            except ValidationError as exc:
                logger.warning(
                    "llm reply invalid attempt={} errors={} raw_len={}",
                    attempt,
                    exc.error_count(),
                    len(raw),
                )
                prompt = [*prompt, ChatMessage("system", INVALID_JSON_HINT)]
        logger.warning("llm reply invalid after retry, using fallback")
        return None

    async def _apply_slots(self, lead: Lead, reply: LLMReply) -> list[str]:
        patch = reply.slots.model_dump(exclude_none=True)
        changed = [name for name, value in patch.items() if getattr(lead, name) != value]
        for name in changed:
            setattr(lead, name, patch[name])
        if changed:
            await self._leads.save(lead)
        return changed

    async def _set_status(self, lead: Lead, status: LeadStatus) -> None:
        logger.info("lead status {} -> {} lead_id={}", lead.status, status, lead.id)
        lead.status = status
        await self._leads.save(lead)

    async def _save_message(self, lead: Lead, role: MessageRole, content: str) -> None:
        await self._messages.add(
            Message(lead_id=lead.id, role=role, content=content, kind=MessageKind.TEXT)
        )


_EMPTY_SLOTS: dict[str, None] = dict.fromkeys(SLOT_FIELDS)


def _to_chat_message(message: Message) -> ChatMessage:
    if message.role is MessageRole.ASSISTANT:
        # Past answers are replayed in the full JSON shape the model must return. With
        # plain-text history DeepSeek's JSON mode often returns empty content; with a
        # reply-only shape it starts omitting "slots". Known slot values are sent separately.
        payload = {"reply": message.content, "slots": _EMPTY_SLOTS, "ready": False}
        return ChatMessage("assistant", json.dumps(payload, ensure_ascii=False))
    return ChatMessage("user", message.content)


def _lead_slots(lead: Lead) -> dict[str, str]:
    slots: dict[str, str] = {}
    for name in SLOT_FIELDS:
        value: object = getattr(lead, name)
        if value is not None:
            slots[name] = str(value)
    return slots


def _slots_json(lead: Lead) -> str:
    return json.dumps(_lead_slots(lead), ensure_ascii=False)


def _required_filled(lead: Lead) -> bool:
    return all(getattr(lead, name) is not None for name in REQUIRED_SLOTS)
