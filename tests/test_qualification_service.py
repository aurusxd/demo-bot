import asyncio
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.clients.base import LLMClient
from app.core.errors import ExternalServiceError, InvalidInputError
from app.db.models import (
    FurnitureType,
    Lead,
    LeadMedia,
    LeadStatus,
    MediaKind,
    Message,
    MessageKind,
    MessageRole,
    User,
)
from app.db.repositories import (
    LeadMediaRepository,
    LeadRepository,
    MessageRepository,
    UnitOfWork,
    UserRepository,
)
from app.prompts.qualification import (
    ESTIMATE_SHOWN_TEMPLATE,
    INVALID_JSON_HINT,
    NAME_ALREADY_ASKED,
    PHOTO_MARKER,
    SYSTEM_PROMPT,
)
from app.services.qualification import (
    HISTORY_LIMIT,
    LLM_TURN_BUDGET_S,
    SLOT_FIELDS,
    QualificationService,
)
from tests.fakes import FakeLLMClient, SlowLLMClient, llm_json


async def make_user(session: AsyncSession) -> User:
    return await UserRepository(session).upsert(1, "u")


def make_service(
    session: AsyncSession, llm: LLMClient, llm_budget_s: float = LLM_TURN_BUDGET_S
) -> QualificationService:
    return QualificationService(
        llm,
        LeadRepository(session),
        MessageRepository(session),
        LeadMediaRepository(session),
        UnitOfWork(session),
        llm_budget_s,
    )


async def get_messages(session: AsyncSession) -> list[Message]:
    return list((await session.scalars(select(Message).order_by(Message.id))).all())


async def test_turn_merges_slots_and_stores_history(session: AsyncSession) -> None:
    user = await make_user(session)
    llm = FakeLLMClient(llm_json("Какие размеры?", furniture_type="kitchen", city="Казань"))
    service = make_service(session, llm)

    result = await service.handle_message(user, "Хочу кухню в Казани")

    assert result.reply == "Какие размеры?"
    assert result.qualified is False
    assert result.slots == {"furniture_type": "kitchen", "city": "Казань"}
    lead = (await session.scalars(select(Lead))).one()
    assert lead.furniture_type is FurnitureType.KITCHEN
    assert lead.status is LeadStatus.DRAFT
    roles = [m.role for m in await get_messages(session)]
    assert roles == [MessageRole.USER, MessageRole.ASSISTANT]


async def test_lead_qualified_when_ready_and_required_slots_filled(session: AsyncSession) -> None:
    user = await make_user(session)
    llm = FakeLLMClient(
        llm_json("Размеры?", furniture_type="wardrobe"),
        llm_json("Спасибо!", ready=True, dimensions="2 x 2.4 м", phone="8 912 345-12-34"),
    )
    service = make_service(session, llm)

    await service.handle_message(user, "Нужен шкаф")
    result = await service.handle_message(user, "2 на 2.4, телефон 89123451234")

    assert result.qualified is True
    assert result.slots["phone"] == "+79123451234"
    lead = (await session.scalars(select(Lead))).one()
    assert lead.status is LeadStatus.QUALIFIED


async def test_ready_with_invalid_phone_asks_for_it_instead_of_thanking(
    session: AsyncSession,
) -> None:
    user = await make_user(session)
    llm = FakeLLMClient(
        llm_json(
            "Спасибо! Заявку передал менеджеру",
            ready=True,
            furniture_type="kitchen",
            dimensions="3 м",
            phone="+7999999",  # 7 digits: rejected by the code
        )
    )
    service = make_service(session, llm)

    result = await service.handle_message(user, "Кухня 3 м, +7999999")

    assert result.qualified is False
    assert result.reply == texts.PHONE_NEEDED
    stored = [m.content for m in await get_messages(session) if m.role is MessageRole.ASSISTANT]
    assert stored == [texts.PHONE_NEEDED]  # the LLM sees what the client actually saw


async def test_ready_without_required_slots_is_not_qualified(session: AsyncSession) -> None:
    user = await make_user(session)
    service = make_service(session, FakeLLMClient(llm_json("Готово", ready=True, city="Москва")))

    result = await service.handle_message(user, "Москва")

    assert result.qualified is False


async def test_estimate_due_on_price_input_change_or_price_question(
    session: AsyncSession,
) -> None:
    user = await make_user(session)
    llm = FakeLLMClient(
        llm_json("Город?", city="Казань"),
        llm_json("Размеры?", furniture_type="kitchen"),
        llm_json("Размеры?", furniture_type="kitchen"),  # same value: nothing changed
        llm_json("Посчитаю", price_asked=True),
    )
    service = make_service(session, llm)

    results = [await service.handle_message(user, text) for text in ("а", "б", "в", "г")]

    assert [r.estimate_due for r in results] == [False, True, False, True]


async def test_shown_estimate_is_in_llm_context(session: AsyncSession) -> None:
    user = await make_user(session)
    llm = FakeLLMClient(llm_json("Размеры?"), llm_json("Дешевле — ЛДСП"))
    service = make_service(session, llm)

    await service.handle_message(user, "кухня")
    assert "расчёт" not in llm.calls[0][1].content  # nothing shown yet
    lead = (await session.scalars(select(Lead))).one()
    lead.estimate_min, lead.estimate_max = 115000, 155000
    await service.handle_message(user, "А почему так дорого?")

    context = llm.calls[1][1].content
    assert ESTIMATE_SHOWN_TEMPLATE.format(min_price=115000, max_price=155000) in context


async def test_name_is_not_asked_again_after_the_first_reply(session: AsyncSession) -> None:
    user = await make_user(session)
    llm = FakeLLMClient(
        llm_json("Как вас зовут? Какие размеры?"),
        llm_json("Стиль?"),
        llm_json("Анна, телефон?", name="Анна"),
        llm_json("Спасибо"),
    )
    service = make_service(session, llm)

    for text in ("кухня", "3 метра", "Анна", "+79123451234"):
        await service.handle_message(user, text)

    contexts = [call[1].content for call in llm.calls]
    # First reply may ask the name; later ones get the stop until the name is known.
    assert [NAME_ALREADY_ASKED in c for c in contexts] == [False, True, True, False]


async def test_null_slots_do_not_erase_known_values(session: AsyncSession) -> None:
    user = await make_user(session)
    llm = FakeLLMClient(llm_json("Ок", city="Казань"), llm_json("Ок", city=None, budget="300 тыс"))
    service = make_service(session, llm)

    await service.handle_message(user, "Казань")
    result = await service.handle_message(user, "до 300")

    assert result.slots == {"city": "Казань", "budget": "300 тыс"}


async def test_invalid_json_is_retried_with_hint(session: AsyncSession) -> None:
    user = await make_user(session)
    llm = FakeLLMClient("oops", llm_json("Уточните размеры", furniture_type="kitchen"))
    service = make_service(session, llm)

    result = await service.handle_message(user, "кухня")

    assert result.reply == "Уточните размеры"
    assert len(llm.calls) == 2
    assert llm.calls[1][-1].content == INVALID_JSON_HINT


async def test_invalid_json_twice_returns_fallback(session: AsyncSession) -> None:
    user = await make_user(session)
    service = make_service(session, FakeLLMClient("oops", '{"reply": ""}'))

    result = await service.handle_message(user, "кухня")

    assert result.reply is None
    assert result.qualified is False
    roles = [m.role for m in await get_messages(session)]
    assert roles == [MessageRole.USER]


async def test_llm_api_error_returns_fallback(session: AsyncSession) -> None:
    user = await make_user(session)
    llm = FakeLLMClient(ExternalServiceError("deepseek", "HTTP 503", 503))
    service = make_service(session, llm)

    result = await service.handle_message(user, "кухня")

    assert result.reply is None
    assert len(llm.calls) == 1


@pytest.mark.parametrize("text", ["", "   ", "x" * 1001])
async def test_invalid_input_is_rejected_before_llm(session: AsyncSession, text: str) -> None:
    user = await make_user(session)
    llm = FakeLLMClient()

    with pytest.raises(InvalidInputError):
        await make_service(session, llm).handle_message(user, text)

    assert llm.calls == []


async def test_prompt_contains_system_slots_and_limited_history(session: AsyncSession) -> None:
    user = await make_user(session)
    turns = HISTORY_LIMIT  # each turn stores 2 messages -> history must be cut
    llm = FakeLLMClient(*(llm_json(f"ответ {i}") for i in range(turns)))
    service = make_service(session, llm)
    await service.start(user, "Привет!")

    for i in range(turns):
        await service.handle_message(user, f"сообщение {i}")

    last_prompt = llm.calls[-1]
    assert [m.role for m in last_prompt[:2]] == ["system", "system"]
    history = last_prompt[2:]
    assert len(history) == HISTORY_LIMIT
    assert history[-1].content == f"сообщение {turns - 1}"


async def test_start_begins_new_lead_and_dialog_continues_it(session: AsyncSession) -> None:
    user = await make_user(session)
    llm = FakeLLMClient(llm_json("Приятно познакомиться!", name="Влад"))
    service = make_service(session, llm)

    await service.start(user, "Как вас зовут?")
    await service.start(user, "Как вас зовут?")
    result = await service.handle_message(user, "Влад")

    leads = (await session.scalars(select(Lead).order_by(Lead.id))).all()
    assert len(leads) == 2
    assert leads[0].name is None
    assert leads[1].name == "Влад"
    assert result.slots == {"name": "Влад"}


async def test_b2b_slots_are_stored(session: AsyncSession) -> None:
    user = await make_user(session)
    llm = FakeLLMClient(
        llm_json("Откуда заявки?", business_role="руководитель"),
        llm_json("Понял!", lead_sources="Авито, сарафан"),
    )
    service = make_service(session, llm)

    await service.handle_message(user, "Руководитель")
    await service.handle_message(user, "С Авито и по сарафану")

    lead = (await session.scalars(select(Lead))).one()
    assert (lead.business_role, lead.lead_sources) == ("руководитель", "Авито, сарафан")


async def test_assistant_history_is_sent_as_json(session: AsyncSession) -> None:
    user = await make_user(session)
    llm = FakeLLMClient(llm_json("Размеры?"), llm_json("Стиль?"))
    service = make_service(session, llm)

    await service.handle_message(user, "кухня")
    await service.handle_message(user, "3 метра")

    history = llm.calls[1][2:]
    assert [(m.role, m.content) for m in history if m.role == "user"] == [
        ("user", "кухня"),
        ("user", "3 метра"),
    ]
    assistant = [json.loads(m.content) for m in history if m.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["reply"] == "Размеры?"
    assert assistant[0]["ready"] is False
    assert assistant[0]["price_asked"] is False
    assert set(assistant[0]["slots"]) == set(SLOT_FIELDS)
    assert all(v is None for v in assistant[0]["slots"].values())


async def test_dialog_continues_on_lead_after_qualification(session: AsyncSession) -> None:
    """Review #4: after "заявка собрана" the bot must not forget the client."""
    user = await make_user(session)
    llm = FakeLLMClient(
        llm_json(
            "Спасибо, Влад!",
            ready=True,
            name="Влад",
            furniture_type="kitchen",
            dimensions="3 м",
            phone="89123451234",
        ),
        llm_json("Точную цену назовёт менеджер, Влад", ready=True),
    )
    service = make_service(session, llm)
    await service.start(user, "Как вас зовут?")

    first = await service.handle_message(user, "Влад, кухня 3 м, 89123451234")
    second = await service.handle_message(user, "Спасибо! А сколько будет стоить?")

    assert first.qualified is True
    assert second.qualified is False  # already qualified: no second transition
    leads = (await session.scalars(select(Lead))).all()
    assert len(leads) == 1
    assert leads[0].status is LeadStatus.QUALIFIED
    prompt = llm.calls[1]
    assert '"name": "Влад"' in prompt[1].content  # known slots are still sent
    history = [m.content for m in prompt[2:] if m.role == "user"]
    assert history == ["Влад, кухня 3 м, 89123451234", "Спасибо! А сколько будет стоить?"]


async def test_llm_turn_budget_returns_fallback(session: AsyncSession) -> None:
    """Review #2: a hanging LLM must not keep the user waiting for minutes."""
    user = await make_user(session)
    service = make_service(session, SlowLLMClient(delay_s=5), llm_budget_s=0.05)

    started = asyncio.get_running_loop().time()
    result = await service.handle_message(user, "кухня")

    assert result.reply is None
    assert asyncio.get_running_loop().time() - started < 1
    roles = [m.role for m in await get_messages(session)]
    assert roles == [MessageRole.USER]  # the user's message is kept, no assistant reply


async def test_voice_transcript_is_stored_as_voice_message(session: AsyncSession) -> None:
    user = await make_user(session)
    service = make_service(session, FakeLLMClient(llm_json("Какие размеры?")))

    await service.handle_message(user, "Хочу кухню", MessageKind.VOICE)

    kinds = [(m.role, m.kind) for m in await get_messages(session)]
    assert kinds == [
        (MessageRole.USER, MessageKind.VOICE),
        (MessageRole.ASSISTANT, MessageKind.TEXT),
    ]


async def test_photo_is_attached_to_current_lead_without_llm(session: AsyncSession) -> None:
    user = await make_user(session)
    llm = FakeLLMClient(llm_json("Ок"))
    service = make_service(session, llm)
    await service.handle_message(user, "Нужен шкаф")

    await service.attach_photo(user, "photo-1", ack="Фото приложил")
    await service.attach_photo(user, "photo-2", ack=None)

    lead = (await session.scalars(select(Lead))).one()
    media = (await session.scalars(select(LeadMedia).order_by(LeadMedia.id))).all()
    assert [(m.lead_id, m.tg_file_id, m.kind) for m in media] == [
        (lead.id, "photo-1", MediaKind.PHOTO),
        (lead.id, "photo-2", MediaKind.PHOTO),
    ]
    tail = [(m.role, m.content, m.kind) for m in (await get_messages(session))[2:]]
    assert tail == [
        (MessageRole.USER, PHOTO_MARKER, MessageKind.PHOTO),
        (MessageRole.ASSISTANT, "Фото приложил", MessageKind.TEXT),
        (MessageRole.USER, PHOTO_MARKER, MessageKind.PHOTO),
    ]
    assert len(llm.calls) == 1  # photos never go to the LLM


async def test_photo_without_dialog_creates_draft_lead(session: AsyncSession) -> None:
    user = await make_user(session)
    service = make_service(session, FakeLLMClient())

    await service.attach_photo(user, "photo-1", ack=None)

    lead = (await session.scalars(select(Lead))).one()
    assert lead.status is LeadStatus.DRAFT
    assert (await session.scalars(select(LeadMedia))).one().lead_id == lead.id


def test_system_prompt_explains_photo_marker() -> None:
    assert PHOTO_MARKER in SYSTEM_PROMPT
