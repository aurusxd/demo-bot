import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ExternalServiceError, InvalidInputError
from app.db.models import FurnitureType, Lead, LeadStatus, Message, MessageRole, User
from app.db.repositories import LeadRepository, MessageRepository, UserRepository
from app.prompts.qualification import INVALID_JSON_HINT
from app.services.qualification import HISTORY_LIMIT, SLOT_FIELDS, QualificationService
from tests.fakes import FakeLLMClient, llm_json


async def make_user(session: AsyncSession) -> User:
    return await UserRepository(session).upsert(1, "u")


def make_service(session: AsyncSession, llm: FakeLLMClient) -> QualificationService:
    return QualificationService(llm, LeadRepository(session), MessageRepository(session))


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


async def test_ready_without_required_slots_is_not_qualified(session: AsyncSession) -> None:
    user = await make_user(session)
    service = make_service(session, FakeLLMClient(llm_json("Готово", ready=True, city="Москва")))

    result = await service.handle_message(user, "Москва")

    assert result.qualified is False


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
    assert set(assistant[0]["slots"]) == set(SLOT_FIELDS)
    assert all(v is None for v in assistant[0]["slots"].values())
