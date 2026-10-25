import asyncio
import json
from dataclasses import dataclass, field

from loguru import logger
from pydantic import ValidationError

from app.clients.base import ChatMessage, LLMClient
from app.core.errors import ExternalServiceError, InvalidInputError
from app.db.models import (
    CatalogCategory,
    Lead,
    LeadMedia,
    LeadStatus,
    MediaKind,
    Message,
    MessageKind,
    MessageRole,
    User,
)
from app.db.repositories import LeadMediaRepository, LeadRepository, MessageRepository, UnitOfWork
from app.prompts.qualification import (
    CURRENT_SLOTS_TEMPLATE,
    ESTIMATE_SHOWN_TEMPLATE,
    INVALID_JSON_HINT,
    PHOTO_MARKER,
    SYSTEM_PROMPT,
)
from app.schemas.qualification import LLMReply
from app.services.estimate import ESTIMATE_INPUTS

HISTORY_LIMIT = 12
# Hard cap for one dialog turn (all LLM attempts): the user must not wait for minutes.
LLM_TURN_BUDGET_S = 45.0
MAX_USER_TEXT_LEN = 1000
SLOT_FIELDS = (
    "name",
    "furniture_type",
    "dimensions",
    "length_m",
    "price_class",
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
    lead_id: int | None = None
    changed_slots: tuple[str, ...] = ()  # slots whose value changed on this turn
    qualified: bool = False  # True only on the turn the lead became qualified
    slots: dict[str, str] = field(default_factory=dict[str, str])
    catalog: CatalogCategory | None = None  # show this catalog category after the reply
    # Show the client the price range (if computable): its inputs changed or it was asked.
    estimate_due: bool = False


@dataclass(frozen=True, slots=True)
class PhotoAttached:
    lead_id: int
    after_delivery: bool  # managers already got the card: forward this photo separately


class QualificationService:
    """Lead qualification dialog: the LLM proposes, the code validates and applies."""

    def __init__(
        self,
        llm: LLMClient,
        leads: LeadRepository,
        messages: MessageRepository,
        media: LeadMediaRepository,
        uow: UnitOfWork,
        llm_budget_s: float = LLM_TURN_BUDGET_S,
    ) -> None:
        self._llm = llm
        self._leads = leads
        self._messages = messages
        self._media = media
        self._uow = uow
        self._llm_budget_s = llm_budget_s

    async def start(self, user: User, greeting: str) -> None:
        """Fresh dialog for /start: a new draft lead whose history opens with the greeting."""
        lead = await self._create_draft(user)
        await self._save_message(lead, MessageRole.ASSISTANT, greeting)
        logger.info("qualification started lead_id={}", lead.id)

    async def handle_message(
        self, user: User, text: str, kind: MessageKind = MessageKind.TEXT
    ) -> TurnResult:
        """One dialog turn. `kind` tells where the text came from (typed or voice transcript)."""
        text = text.strip()
        if not text or len(text) > MAX_USER_TEXT_LEN:
            logger.info("qualification input rejected text_len={}", len(text))
            raise InvalidInputError(f"text length {len(text)}")

        lead = await self._get_or_create_lead(user)
        with logger.contextualize(lead_id=lead.id):
            await self._save_message(lead, MessageRole.USER, text, kind)
            history = await self._messages.get_last(lead.id, HISTORY_LIMIT)
            await self._uow.commit()  # release the SQLite write lock before the slow LLM call

            reply = await self._ask_llm(lead, history)
            if reply is None:
                return TurnResult(reply=None, lead_id=lead.id, slots=_lead_slots(lead))

            updated = await self._apply_slots(lead, reply)
            await self._save_message(lead, MessageRole.ASSISTANT, reply.reply)
            qualified = lead.status is LeadStatus.DRAFT and reply.ready and _required_filled(lead)
            if qualified:
                await self._set_status(lead, LeadStatus.QUALIFIED)
            await self._uow.commit()
            estimate_due = reply.price_asked or bool(ESTIMATE_INPUTS.intersection(updated))
            logger.info(
                "qualification turn done updated_slots={} llm_ready={} qualified={} catalog={} "
                "price_asked={} estimate_due={}",
                updated,
                reply.ready,
                qualified,
                reply.catalog,
                reply.price_asked,
                estimate_due,
            )
            return TurnResult(
                reply=reply.reply,
                lead_id=lead.id,
                qualified=qualified,
                changed_slots=tuple(updated),
                slots=_lead_slots(lead),
                catalog=reply.catalog,
                estimate_due=estimate_due,
            )

    async def attach_photo(self, user: User, tg_file_id: str, ack: str | None) -> PhotoAttached:
        """Attach a validated photo to the current lead (no analysis, no LLM call).

        A photo sent after delivery is reported, so the caller forwards it to managers.

        The history gets a marker so the LLM knows a photo was sent, plus the bot's
        acknowledgement `ack` if one is sent to the user.
        """
        lead = await self._get_or_create_lead(user)
        with logger.contextualize(lead_id=lead.id):
            await self._media.add(
                LeadMedia(lead_id=lead.id, tg_file_id=tg_file_id, kind=MediaKind.PHOTO)
            )
            await self._save_message(lead, MessageRole.USER, PHOTO_MARKER, MessageKind.PHOTO)
            if ack is not None:
                await self._save_message(lead, MessageRole.ASSISTANT, ack)
            await self._uow.commit()
            logger.info("photo attached to lead lead_id={}", lead.id)
            return PhotoAttached(lead.id, after_delivery=lead.manager_notified_at is not None)

    async def _get_or_create_lead(self, user: User) -> Lead:
        # The dialog continues on the latest lead even after it is qualified, so the bot keeps
        # the name and the context. Only /start begins a new lead.
        lead = await self._leads.get_latest(user.id)
        return lead if lead is not None else await self._create_draft(user)

    async def _create_draft(self, user: User) -> Lead:
        lead = await self._leads.add(Lead(user_id=user.id, status=LeadStatus.DRAFT))
        logger.info("lead created lead_id={} status={}", lead.id, lead.status)
        return lead

    async def _ask_llm(self, lead: Lead, history: list[Message]) -> LLMReply | None:
        prompt = [
            ChatMessage("system", SYSTEM_PROMPT),
            ChatMessage("system", _lead_context(lead)),
            *(_to_chat_message(m) for m in history),
        ]
        try:
            async with asyncio.timeout(self._llm_budget_s):
                return await self._ask_with_retry(prompt)
        except TimeoutError:
            logger.warning("llm turn budget exceeded budget_s={}", self._llm_budget_s)
            return None

    async def _ask_with_retry(self, prompt: list[ChatMessage]) -> LLMReply | None:
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

    async def _save_message(
        self, lead: Lead, role: MessageRole, content: str, kind: MessageKind = MessageKind.TEXT
    ) -> None:
        await self._messages.add(Message(lead_id=lead.id, role=role, content=content, kind=kind))


_EMPTY_SLOTS: dict[str, None] = dict.fromkeys(SLOT_FIELDS)


def _to_chat_message(message: Message) -> ChatMessage:
    if message.role is MessageRole.ASSISTANT:
        # Past answers are replayed in the full JSON shape the model must return. With
        # plain-text history DeepSeek's JSON mode often returns empty content; with a
        # reply-only shape it starts omitting "slots". Known slot values are sent separately.
        payload = {
            "reply": message.content,
            "slots": _EMPTY_SLOTS,
            "ready": False,
            "catalog": None,
            "price_asked": False,
        }
        return ChatMessage("assistant", json.dumps(payload, ensure_ascii=False))
    return ChatMessage("user", message.content)


def _lead_slots(lead: Lead) -> dict[str, str]:
    slots: dict[str, str] = {}
    for name in SLOT_FIELDS:
        value: object = getattr(lead, name)
        if value is not None:
            slots[name] = str(value)
    return slots


def _lead_context(lead: Lead) -> str:
    """Known slots plus the range the client has already seen (the bot shows it once stored)."""
    context = CURRENT_SLOTS_TEMPLATE.format(slots=json.dumps(_lead_slots(lead), ensure_ascii=False))
    if lead.estimate_min is None or lead.estimate_max is None:
        return context
    shown = ESTIMATE_SHOWN_TEMPLATE.format(min_price=lead.estimate_min, max_price=lead.estimate_max)
    return f"{context}\n{shown}"


def _required_filled(lead: Lead) -> bool:
    return all(getattr(lead, name) is not None for name in REQUIRED_SLOTS)
