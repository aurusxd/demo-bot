import html
from datetime import UTC, datetime

from loguru import logger

from app.bot import texts
from app.clients.base import CrmClient, LeadNotifier
from app.core.errors import ExternalServiceError
from app.db.models import Lead, LeadStatus, MediaKind, User
from app.db.repositories import LeadMediaRepository, LeadRepository, UnitOfWork
from app.schemas.estimate import EstimateResult
from app.schemas.lead import LeadPayload
from app.services.estimate import EstimateService


class LeadService:
    """A qualified lead: price estimate, then delivery to the CRM and the managers' chat."""

    def __init__(
        self,
        estimator: EstimateService,
        crm: CrmClient,
        notifier: LeadNotifier,
        leads: LeadRepository,
        media: LeadMediaRepository,
        uow: UnitOfWork,
    ) -> None:
        self._estimator = estimator
        self._crm = crm
        self._notifier = notifier
        self._leads = leads
        self._media = media
        self._uow = uow

    async def estimate(self, lead_id: int) -> EstimateResult | None:
        """Compute and store the price range; None if there is not enough data."""
        lead = await self._get(lead_id)
        with logger.contextualize(lead_id=lead.id):
            result = self._estimator.calculate(lead.furniture_type, lead.length_m, lead.price_class)
            if result is not None:
                lead.estimate_min, lead.estimate_max = result.min_price, result.max_price
                await self._leads.save(lead)
                await self._uow.commit()
            return result

    async def submit(self, user: User, lead_id: int) -> bool:
        """Send the lead to the CRM and the card to managers. True if the CRM accepted it.

        Failures are logged and never reach the client: the lead stays `qualified`.
        """
        lead = await self._get(lead_id)
        with logger.contextualize(lead_id=lead.id):
            photos = await self._media.list_file_ids(lead.id, MediaKind.PHOTO)
            payload = _to_payload(lead, user, photos, self._estimator.currency)
            await self._uow.commit()  # nothing is held in the DB during external calls

            delivered = await self._send_to_crm(payload)
            await self._notify_managers(payload)
            if delivered:
                logger.info(
                    "lead status {} -> {} lead_id={}", lead.status, LeadStatus.SENT, lead.id
                )
                lead.status = LeadStatus.SENT
                lead.sent_at = datetime.now(UTC)
                await self._leads.save(lead)
                await self._uow.commit()
            return delivered

    async def _get(self, lead_id: int) -> Lead:
        lead = await self._leads.get(lead_id)
        if lead is None:
            raise LookupError(f"lead {lead_id} not found")
        return lead

    async def _send_to_crm(self, payload: LeadPayload) -> bool:
        try:
            await self._crm.send(payload)
        except ExternalServiceError:
            logger.exception("crm delivery failed lead_id={}", payload.lead_id)
            return False
        return True

    async def _notify_managers(self, payload: LeadPayload) -> None:
        try:
            await self._notifier.notify(render_lead_card(payload), payload.photo_file_ids)
        except ExternalServiceError:
            logger.exception("manager notification failed lead_id={}", payload.lead_id)
            return
        logger.info(
            "manager notified lead_id={} photos={}", payload.lead_id, len(payload.photo_file_ids)
        )


def render_lead_card(lead: LeadPayload) -> str:
    """HTML card for managers. Every user-provided value is escaped."""
    values = lead.model_dump()
    lines = [texts.LEAD_CARD_TITLE.format(lead_id=lead.lead_id), ""]
    lines += _field_lines(values, texts.LEAD_CARD_FIELDS)
    contact = f"@{lead.tg_username}" if lead.tg_username else f"id {lead.tg_id}"
    lines.append(texts.LEAD_CARD_CONTACT.format(contact=html.escape(contact)))
    lines.append("")
    if lead.estimate_min is not None and lead.estimate_max is not None:
        lines.append(
            texts.LEAD_CARD_ESTIMATE.format(
                min_price=texts.format_price(lead.estimate_min, lead.currency),
                max_price=texts.format_price(lead.estimate_max, lead.currency),
            )
        )
    else:
        lines.append(texts.LEAD_CARD_NO_ESTIMATE)
    if lead.photo_file_ids:
        lines.append(texts.LEAD_CARD_PHOTOS.format(count=len(lead.photo_file_ids)))
    b2b = _field_lines(values, texts.LEAD_CARD_B2B_FIELDS)
    if b2b:
        lines += [texts.LEAD_CARD_B2B_TITLE, *b2b]
    return "\n".join(lines)


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


def _to_payload(lead: Lead, user: User, photo_file_ids: list[str], currency: str) -> LeadPayload:
    return LeadPayload(
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
        currency=currency,
        photo_file_ids=photo_file_ids,
    )
