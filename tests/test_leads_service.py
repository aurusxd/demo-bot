import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.lead_retry import LeadDeliveryRetrier
from app.core.errors import ExternalServiceError
from app.core.locks import UserLocks
from app.db.models import FurnitureType, Lead, LeadMedia, LeadStatus, MediaKind, User
from app.db.repositories import LeadMediaRepository, LeadRepository, UserRepository
from app.schemas.lead import LeadEvent
from app.services.leads import LeadService
from tests.fakes import FakeCrmClient, FakeNotifier, make_lead_services

CRM_DOWN = ExternalServiceError("crm", "HTTP 500", 500)


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


def make_service(
    session: AsyncSession, crm: FakeCrmClient | None = None, notifier: FakeNotifier | None = None
) -> LeadService:
    return make_lead_services(crm or FakeCrmClient(), notifier or FakeNotifier()).build(session)


async def add_photo(session: AsyncSession, lead: Lead, file_id: str) -> None:
    media = LeadMedia(lead_id=lead.id, tg_file_id=file_id, kind=MediaKind.PHOTO)
    await LeadMediaRepository(session).add(media)


async def test_estimate_is_stored_on_lead(session: AsyncSession) -> None:
    _, lead = await make_lead(session)

    result = await make_service(session).estimate(lead.id)

    assert result is not None
    assert (lead.estimate_min, lead.estimate_max) == (115000, 155000)


async def test_no_length_means_no_estimate(session: AsyncSession) -> None:
    _, lead = await make_lead(session, length_m=None)

    assert await make_service(session).estimate(lead.id) is None
    assert lead.estimate_min is None


async def test_submit_sends_to_crm_and_managers_with_photos(session: AsyncSession) -> None:
    _, lead = await make_lead(session, estimate_min=115000, estimate_max=155000)
    for file_id in ("p1", "p2"):
        await add_photo(session, lead, file_id)
    crm, notifier = FakeCrmClient(), FakeNotifier()

    delivered = await make_service(session, crm, notifier).submit(lead.id)

    assert delivered is True
    assert lead.status is LeadStatus.SENT
    assert lead.sent_at is not None
    assert lead.manager_notified_at is not None
    assert crm.sent[0].event is LeadEvent.CREATED
    assert crm.sent[0].photo_file_ids == ["p1", "p2"]
    assert crm.sent[0].tg_username == "ann"
    card, photos = notifier.cards[0]
    assert photos == ["p1", "p2"]
    assert "+79123451234" in card  # managers need the full phone
    assert "Кухня" in card
    assert "115 000 ₽" in card
    assert "Фото:</b> 2" in card


async def test_card_escapes_user_values(session: AsyncSession) -> None:
    _, lead = await make_lead(session, name="<script>Анна</script>", length_m=None)
    notifier = FakeNotifier()

    await make_service(session, notifier=notifier).submit(lead.id)

    card = notifier.cards[0][0]
    assert "<script>" not in card
    assert "&lt;script&gt;" in card
    assert "после замера" in card


async def test_crm_failure_keeps_lead_qualified_but_notifies_managers(
    session: AsyncSession,
) -> None:
    _, lead = await make_lead(session)
    notifier = FakeNotifier()

    delivered = await make_service(session, FakeCrmClient(CRM_DOWN), notifier).submit(lead.id)

    assert delivered is False
    assert lead.status is LeadStatus.QUALIFIED
    assert lead.sent_at is None
    assert lead.manager_notified_at is not None
    assert len(notifier.cards) == 1


async def test_notifier_failure_does_not_block_crm(session: AsyncSession) -> None:
    _, lead = await make_lead(session)
    crm = FakeCrmClient()
    notifier = FakeNotifier(ExternalServiceError("telegram", "send_lead_card failed"))

    delivered = await make_service(session, crm, notifier).submit(lead.id)

    assert delivered is True
    assert lead.status is LeadStatus.SENT
    assert lead.manager_notified_at is None  # the retry job will send the card
    assert len(crm.sent) == 1


async def test_submit_again_only_does_what_is_missing(session: AsyncSession) -> None:
    """Review #2: a retry after a CRM failure must not send the managers' card twice."""
    _, lead = await make_lead(session)
    notifier = FakeNotifier()
    await make_service(session, FakeCrmClient(CRM_DOWN), notifier).submit(lead.id)
    crm = FakeCrmClient()

    delivered = await make_service(session, crm, notifier).submit(lead.id)
    again = await make_service(session, crm, notifier).submit(lead.id)

    assert delivered is True and again is True
    assert lead.status is LeadStatus.SENT
    assert len(crm.sent) == 1
    assert len(notifier.cards) == 1


async def test_submit_computes_missing_estimate(session: AsyncSession) -> None:
    """The bot may stop between qualification and the estimate step."""
    _, lead = await make_lead(session)
    crm = FakeCrmClient()

    await make_service(session, crm).submit(lead.id)

    assert crm.sent[0].estimate_min == 115000


async def test_submit_recomputes_stale_estimate(session: AsyncSession) -> None:
    """The stored range may predate the last length change if the bot stopped in between."""
    _, lead = await make_lead(session, length_m=4.0, estimate_min=115000, estimate_max=155000)
    crm = FakeCrmClient()

    await make_service(session, crm).submit(lead.id)

    assert (crm.sent[0].estimate_min, crm.sent[0].estimate_max) == (153000, 207000)


async def test_draft_is_never_submitted(session: AsyncSession) -> None:
    _, lead = await make_lead(session, status=LeadStatus.DRAFT)
    crm, notifier = FakeCrmClient(), FakeNotifier()

    assert await make_service(session, crm, notifier).submit(lead.id) is False
    assert crm.sent == [] and notifier.cards == []


async def test_update_after_delivery_goes_to_crm_and_managers(session: AsyncSession) -> None:
    """Review #1: data the client adds after the card must reach the managers."""
    now = datetime.now(UTC)
    _, lead = await make_lead(
        session,
        status=LeadStatus.SENT,
        manager_notified_at=now,
        estimate_min=115000,
        estimate_max=155000,
        city="Москва",
        length_m=4.0,
    )
    crm, notifier = FakeCrmClient(), FakeNotifier()

    await make_service(session, crm, notifier).send_update(lead.id, ["city", "length_m"])

    assert crm.sent[0].event is LeadEvent.UPDATED
    assert (lead.estimate_min, lead.estimate_max) == (153000, 207000)  # recomputed for 4 m
    card = notifier.cards[0][0]
    assert "Дополнение к заявке" in card
    assert "Москва" in card
    assert "153 000 ₽" in card
    assert "Телефон" not in card  # only what changed


async def test_update_without_estimate_inputs_keeps_estimate_line_out(
    session: AsyncSession,
) -> None:
    _, lead = await make_lead(
        session, status=LeadStatus.SENT, manager_notified_at=datetime.now(UTC), city="Тула"
    )
    notifier = FakeNotifier()

    await make_service(session, notifier=notifier).send_update(lead.id, ["city"])

    assert "Предварительно" not in notifier.cards[0][0]


async def test_photo_after_delivery_is_forwarded(session: AsyncSession) -> None:
    _, lead = await make_lead(
        session, status=LeadStatus.SENT, manager_notified_at=datetime.now(UTC)
    )
    await add_photo(session, lead, "late")
    crm, notifier = FakeCrmClient(), FakeNotifier()

    await make_service(session, crm, notifier).send_update(lead.id, photo_file_id="late")

    assert notifier.photos == [(f"📎 <b>Дополнение к заявке #{lead.id}:</b> фото", "late")]
    assert notifier.cards == []
    assert crm.sent[0].photo_file_ids == ["late"]


async def test_no_update_before_managers_got_the_card(session: AsyncSession) -> None:
    _, lead = await make_lead(session)  # qualified, card not sent yet
    crm, notifier = FakeCrmClient(), FakeNotifier()

    await make_service(session, crm, notifier).send_update(lead.id, ["city"], "p1")

    assert crm.sent == [] and notifier.cards == [] and notifier.photos == []


async def test_update_of_lead_not_accepted_by_crm_goes_to_managers_only(
    session: AsyncSession,
) -> None:
    """The CRM gets the latest data on the retry; managers already have the card."""
    _, lead = await make_lead(session, manager_notified_at=datetime.now(UTC), city="Тула")
    crm, notifier = FakeCrmClient(), FakeNotifier()

    await make_service(session, crm, notifier).send_update(lead.id, ["city"])

    assert crm.sent == []
    assert len(notifier.cards) == 1


async def test_retrier_delivers_stuck_leads(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        _, stuck = await make_lead(session)
        await LeadRepository(session).add(
            Lead(user_id=stuck.user_id, status=LeadStatus.SENT, manager_notified_at=None)
        )
        await LeadRepository(session).add(
            Lead(
                user_id=stuck.user_id,
                status=LeadStatus.SENT,
                manager_notified_at=datetime.now(UTC),
            )
        )
        await LeadRepository(session).add(Lead(user_id=stuck.user_id))  # draft
        await session.commit()
    crm, notifier = FakeCrmClient(), FakeNotifier()
    retrier = LeadDeliveryRetrier(
        session_factory, make_lead_services(crm, notifier), UserLocks(), interval_s=60
    )

    delivered = await retrier.run_once()
    second_pass = await retrier.run_once()

    assert delivered == 2  # the qualified one is accepted now, the sent one already was
    assert [lead.lead_id for lead in crm.sent] == [stuck.id]  # sent leads are not re-sent
    assert len(notifier.cards) == 2  # both leads without a card get one
    assert second_pass == 0
    async with session_factory() as session:
        assert await LeadRepository(session).list_undelivered() == []


async def test_retrier_waits_for_the_users_lock(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A handler delivering the lead holds the user's lock: the retry must not race it."""
    async with session_factory() as session:
        _, lead = await make_lead(session)
        await session.commit()
    locks = UserLocks()
    crm, notifier = FakeCrmClient(), FakeNotifier()
    retrier = LeadDeliveryRetrier(
        session_factory, make_lead_services(crm, notifier), locks, interval_s=60
    )

    async with locks.get(500):  # the user's update is being handled
        retry = asyncio.create_task(retrier.run_once())
        await asyncio.sleep(0.05)
        assert not retry.done()  # it found the lead and waits for the lock
        async with session_factory() as session:
            await make_service(session, crm, notifier).submit(lead.id)

    assert await retry == 1  # ran after the handler, saw the lead delivered
    assert len(crm.sent) == 1
    assert len(notifier.cards) == 1
