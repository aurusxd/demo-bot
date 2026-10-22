from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ExternalServiceError
from app.db.models import FurnitureType, Lead, LeadMedia, LeadStatus, MediaKind, User
from app.db.repositories import (
    LeadMediaRepository,
    LeadRepository,
    UnitOfWork,
    UserRepository,
)
from app.services.estimate import EstimateService, load_pricing
from app.services.leads import LeadService
from tests.fakes import FakeCrmClient, FakeNotifier

ESTIMATOR = EstimateService(load_pricing(Path("config/pricing.yaml")))


async def make_lead(session: AsyncSession, **fields: object) -> tuple[User, Lead]:
    user = await UserRepository(session).upsert(500, "ann")
    defaults: dict[str, object] = {
        "status": LeadStatus.QUALIFIED,
        "name": "Анна",
        "furniture_type": FurnitureType.KITCHEN,
        "dimensions": "3 м",
        "length_m": 3.0,
        "phone": "+79123451234",
    }
    lead = await LeadRepository(session).add(Lead(user_id=user.id, **{**defaults, **fields}))
    return user, lead


def make_service(session: AsyncSession, crm: FakeCrmClient, notifier: FakeNotifier) -> LeadService:
    return LeadService(
        ESTIMATOR,
        crm,
        notifier,
        LeadRepository(session),
        LeadMediaRepository(session),
        UnitOfWork(session),
    )


async def test_estimate_is_stored_on_lead(session: AsyncSession) -> None:
    _, lead = await make_lead(session)
    service = make_service(session, FakeCrmClient(), FakeNotifier())

    result = await service.estimate(lead.id)

    assert result is not None
    assert (lead.estimate_min, lead.estimate_max) == (115000, 155000)


async def test_no_length_means_no_estimate(session: AsyncSession) -> None:
    _, lead = await make_lead(session, length_m=None)
    service = make_service(session, FakeCrmClient(), FakeNotifier())

    assert await service.estimate(lead.id) is None
    assert lead.estimate_min is None


async def test_submit_sends_to_crm_and_managers_with_photos(session: AsyncSession) -> None:
    user, lead = await make_lead(session, estimate_min=115000, estimate_max=155000)
    media = LeadMediaRepository(session)
    for file_id in ("p1", "p2"):
        await media.add(LeadMedia(lead_id=lead.id, tg_file_id=file_id, kind=MediaKind.PHOTO))
    crm, notifier = FakeCrmClient(), FakeNotifier()

    delivered = await make_service(session, crm, notifier).submit(user, lead.id)

    assert delivered is True
    assert lead.status is LeadStatus.SENT
    assert lead.sent_at is not None
    assert crm.sent[0].photo_file_ids == ["p1", "p2"]
    assert crm.sent[0].tg_username == "ann"
    card, photos = notifier.cards[0]
    assert photos == ["p1", "p2"]
    assert "+79123451234" in card  # managers need the full phone
    assert "Кухня" in card
    assert "115 000 ₽" in card
    assert "Фото:</b> 2" in card


async def test_card_escapes_user_values(session: AsyncSession) -> None:
    user, lead = await make_lead(session, name="<script>Анна</script>", length_m=None)
    notifier = FakeNotifier()

    await make_service(session, FakeCrmClient(), notifier).submit(user, lead.id)

    card = notifier.cards[0][0]
    assert "<script>" not in card
    assert "&lt;script&gt;" in card
    assert "после замера" in card


async def test_crm_failure_keeps_lead_qualified_but_notifies_managers(
    session: AsyncSession,
) -> None:
    user, lead = await make_lead(session)
    notifier = FakeNotifier()
    crm = FakeCrmClient(ExternalServiceError("crm", "HTTP 500", 500))

    delivered = await make_service(session, crm, notifier).submit(user, lead.id)

    assert delivered is False
    assert lead.status is LeadStatus.QUALIFIED
    assert lead.sent_at is None
    assert len(notifier.cards) == 1


async def test_notifier_failure_does_not_block_crm(session: AsyncSession) -> None:
    user, lead = await make_lead(session)
    crm = FakeCrmClient()
    notifier = FakeNotifier(ExternalServiceError("telegram", "send_lead_card failed"))

    delivered = await make_service(session, crm, notifier).submit(user, lead.id)

    assert delivered is True
    assert lead.status is LeadStatus.SENT
    assert len(crm.sent) == 1
