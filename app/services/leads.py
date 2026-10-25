import html
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.clients.base import CrmClient, LeadNotifier
from app.core.errors import ExternalServiceError
from app.db.models import Lead, LeadStatus, MediaKind
from app.db.repositories import LeadMediaRepository, LeadRepository, UnitOfWork, UserRepository
from app.schemas.estimate import EstimateResult
from app.schemas.lead import LeadEvent, LeadPayload
from app.services.estimate import ESTIMATE_INPUTS, EstimateService


class LeadService:
    """A lead's price estimate (from the first turn it is computable), delivery to the CRM
    and the managers' chat once qualified, updates.

    Delivery has two independent marks: `status = sent` (the CRM accepted the lead) and
    `manager_notified_at` (the card went out). `submit` only does what is still missing,
    so the retry job can call it again safely.
    """

    def __init__(
        self,
        estimator: EstimateService,
        crm: CrmClient,
        notifier: LeadNotifier,
        leads: LeadRepository,
        users: UserRepository,
        media: LeadMediaRepository,
        uow: UnitOfWork,
    ) -> None:
        self._estimator = estimator
        self._crm = crm
        self._notifier = notifier
        self._leads = leads
        self._users = users
        self._media = media
        self._uow = uow

    async def estimate(self, lead_id: int) -> EstimateResult | None:
        """Compute and store the price range; None if there is not enough data."""
        lead = await self._get(lead_id)
        with logger.contextualize(lead_id=lead.id):
            result = self._apply_estimate(lead)
            await self._leads.save(lead)
            await self._uow.commit()
            return result

    async def submit(self, lead_id: int) -> bool:
        """Deliver what is still missing: the lead to the CRM, the card to managers.

        Returns True once the CRM has accepted the lead. Failures are logged and never
        reach the client: the lead stays undelivered and the retry job picks it up.
        """
        lead = await self._get(lead_id)
        with logger.contextualize(lead_id=lead.id):
            crm_pending = lead.status is LeadStatus.QUALIFIED
            card_pending = lead.manager_notified_at is None
            if lead.status is LeadStatus.DRAFT or not (crm_pending or card_pending):
                return lead.status is LeadStatus.SENT
            if crm_pending:
                # Always recompute: a stored range may predate the last slot change if the
                # bot stopped between saving the slots and the estimate step.
                self._apply_estimate(lead)
            payload = await self._payload(lead, LeadEvent.CREATED)
            await self._uow.commit()  # nothing is held in the DB during external calls

            if crm_pending and await self._send_to_crm(payload):
                logger.info("lead status {} -> sent lead_id={}", lead.status, lead.id)
                lead.status = LeadStatus.SENT
                lead.sent_at = datetime.now(UTC)
            card = render_lead_card(payload)
            if card_pending and await self._notify(card, payload.photo_file_ids):
                lead.manager_notified_at = datetime.now(UTC)
            await self._leads.save(lead)
            await self._uow.commit()
            return lead.status is LeadStatus.SENT

    async def send_update(
        self,
        lead_id: int,
        changed_slots: Sequence[str] = (),
        photo_file_id: str | None = None,
    ) -> None:
        """Forward what the client added after the managers got the card.

        Before that nothing is sent: the first card (or its retry) carries the latest data.
        """
        lead = await self._get(lead_id)
        with logger.contextualize(lead_id=lead.id):
            if lead.manager_notified_at is None or not (changed_slots or photo_file_id):
                return
            # The dialog turn may have stored the new estimate already, so a price input
            # change (not a before/after diff) decides whether the card shows the range.
            estimate_changed = bool(ESTIMATE_INPUTS.intersection(changed_slots))
            if estimate_changed:
                self._apply_estimate(lead)
                await self._leads.save(lead)
            payload = await self._payload(lead, LeadEvent.UPDATED)
            await self._uow.commit()

            if lead.status is LeadStatus.SENT:
                await self._send_to_crm(payload)  # a failed update is logged, not retried
            if photo_file_id is not None:
                caption = texts.LEAD_UPDATE_PHOTO.format(lead_id=lead.id)
                await self._notify_photo(caption, photo_file_id)
            if changed_slots:
                await self._notify(render_update_card(payload, changed_slots, estimate_changed), [])
            logger.info(
                "lead update forwarded lead_id={} slots={} photo={}",
                lead.id,
                list(changed_slots),
                photo_file_id is not None,
            )

    def _apply_estimate(self, lead: Lead) -> EstimateResult | None:
        result = self._estimator.calculate(lead.furniture_type, lead.length_m, lead.price_class)
        lead.estimate_min = result.min_price if result else None
        lead.estimate_max = result.max_price if result else None
        return result

    async def _get(self, lead_id: int) -> Lead:
        lead = await self._leads.get(lead_id)
        if lead is None:
            raise LookupError(f"lead {lead_id} not found")
        return lead

    async def _payload(self, lead: Lead, event: LeadEvent) -> LeadPayload:
        user = await self._users.get(lead.user_id)
        if user is None:
            raise LookupError(f"user {lead.user_id} not found")
        photos = await self._media.list_file_ids(lead.id, MediaKind.PHOTO)
        return LeadPayload(
            event=event,
            lead_id=lead.id,
            tg_id=user.tg_id,
            tg_username=user.username,
            created_at=lead.created_at,
            name=lead.name,
            furniture_type=lead.furniture_type,
            dimensions=lead.dimensions,
            length_m=lead.length_m,
            price_class=lead.price_class,
            style=lead.style,
            budget=lead.budget,
            city=lead.city,
            deadline=lead.deadline,
            phone=lead.phone,
            comment=lead.comment,
            business_role=lead.business_role,
            lead_sources=lead.lead_sources,
            estimate_min=lead.estimate_min,
            estimate_max=lead.estimate_max,
            currency=self._estimator.currency,
            photo_file_ids=photos,
        )

    async def _send_to_crm(self, payload: LeadPayload) -> bool:
        try:
            await self._crm.send(payload)
        except ExternalServiceError:
            logger.exception(
                "crm delivery failed lead_id={} event={}", payload.lead_id, payload.event
            )
            return False
        return True

    async def _notify(self, card_html: str, photo_file_ids: Sequence[str]) -> bool:
        try:
            await self._notifier.notify(card_html, photo_file_ids)
        except ExternalServiceError:
            logger.exception("manager notification failed")
            return False
        logger.info("manager notified photos={}", len(photo_file_ids))
        return True

    async def _notify_photo(self, caption_html: str, file_id: str) -> bool:
        try:
            await self._notifier.notify_photo(caption_html, file_id)
        except ExternalServiceError:
            logger.exception("manager photo notification failed")
            return False
        return True


@dataclass(frozen=True, slots=True)
class LeadServiceFactory:
    """Shared clients for LeadService; builds one per DB session (update or retry pass)."""

    estimator: EstimateService
    crm: CrmClient
    notifier: LeadNotifier

    def build(self, session: AsyncSession) -> LeadService:
        return LeadService(
            self.estimator,
            self.crm,
            self.notifier,
            LeadRepository(session),
            UserRepository(session),
            LeadMediaRepository(session),
            UnitOfWork(session),
        )


def render_lead_card(lead: LeadPayload) -> str:
    """HTML card for managers. Every user-provided value is escaped."""
    values = lead.model_dump()
    lines = [texts.LEAD_CARD_TITLE.format(lead_id=lead.lead_id), ""]
    lines += _field_lines(values, texts.LEAD_CARD_FIELDS)
    contact = f"@{lead.tg_username}" if lead.tg_username else f"id {lead.tg_id}"
    lines.append(texts.LEAD_CARD_CONTACT.format(contact=html.escape(contact)))
    lines += ["", _estimate_line(lead)]
    if lead.photo_file_ids:
        lines.append(texts.LEAD_CARD_PHOTOS.format(count=len(lead.photo_file_ids)))
    b2b = _field_lines(values, texts.LEAD_CARD_B2B_FIELDS)
    if b2b:
        lines += [texts.LEAD_CARD_B2B_TITLE, *b2b]
    return "\n".join(lines)


def render_update_card(
    lead: LeadPayload, changed_slots: Sequence[str], estimate_changed: bool
) -> str:
    """Short HTML card with only the fields the client changed after delivery."""
    changed = set(changed_slots)
    fields = tuple(
        field
        for field in (*texts.LEAD_CARD_FIELDS, *texts.LEAD_CARD_B2B_FIELDS)
        if field[0] in changed
    )
    lines = [texts.LEAD_UPDATE_TITLE.format(lead_id=lead.lead_id), ""]
    lines += _field_lines(lead.model_dump(), fields)
    if estimate_changed:
        lines.append(_estimate_line(lead))
    return "\n".join(lines)


def _estimate_line(lead: LeadPayload) -> str:
    if lead.estimate_min is None or lead.estimate_max is None:
        return texts.LEAD_CARD_NO_ESTIMATE
    return texts.LEAD_CARD_ESTIMATE.format(
        min_price=texts.format_price(lead.estimate_min, lead.currency),
        max_price=texts.format_price(lead.estimate_max, lead.currency),
    )


def _field_lines(values: dict[str, object], fields: tuple[tuple[str, str], ...]) -> list[str]:
    lines: list[str] = []
    for name, label in fields:
        value = values.get(name)
        if value is None:
            continue
        raw = f"{value:g}" if isinstance(value, float) else str(value)  # 3.0 -> "3"
        shown = texts.VALUE_LABELS.get(raw, raw)
        lines.append(texts.LEAD_CARD_FIELD.format(label=label, value=html.escape(shown)))
    return lines
