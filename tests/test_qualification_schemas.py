import pytest
from pydantic import ValidationError

from app.db.models import FurnitureType
from app.schemas.qualification import LLMReply, normalize_phone
from tests.fakes import llm_json


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8 (912) 345-12-34", "+79123451234"),
        ("+7 912 345 12 34", "+79123451234"),
        ("9123451234", "+79123451234"),
        ("+380 44 123 45 67", "+380441234567"),
        ("12345", None),
        ("позвоните мне", None),
        (None, None),
    ],
)
def test_normalize_phone(raw: str | None, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


def test_valid_reply_is_parsed() -> None:
    reply = LLMReply.model_validate_json(
        llm_json("Какие размеры?", furniture_type="kitchen", city=" Казань ", phone=None)
    )

    assert reply.reply == "Какие размеры?"
    assert reply.slots.furniture_type is FurnitureType.KITCHEN
    assert reply.slots.city == "Казань"
    assert reply.slots.filled() == {"furniture_type": "kitchen", "city": "Казань"}
    assert reply.ready is False


def test_unknown_furniture_type_and_extra_keys_are_ignored() -> None:
    reply = LLMReply.model_validate_json(llm_json("Ок", furniture_type="sofa", hacked="yes"))

    assert reply.slots.furniture_type is None
    assert reply.slots.filled() == {}


def test_long_values_are_truncated() -> None:
    reply = LLMReply.model_validate_json(llm_json("x" * 5000, style="y" * 500))

    assert len(reply.reply) == 2000
    assert reply.slots.style is not None
    assert len(reply.slots.style) == 128


@pytest.mark.parametrize(
    "raw",
    [
        "not a json",
        '{"slots": {}, "ready": true}',
        '{"reply": "   ", "slots": {}}',
        '{"reply": "ok", "ready": "maybe"}',
    ],
)
def test_invalid_replies_fail_validation(raw: str) -> None:
    with pytest.raises(ValidationError):
        LLMReply.model_validate_json(raw)
