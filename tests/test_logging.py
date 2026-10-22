import json
import logging
from pathlib import Path

import pytest
from loguru import logger

from app.logging_setup import Sanitizer, mask_phone, setup_logging

SECRET = "sk-super-secret-key-123"
BOT_TOKEN = "1234567890:AAH" + "x" * 32


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("+7 (912) 345-12-34", "+7***1234"),
        ("89123451234", "+7***1234"),
        ("+79123451234", "+7***1234"),
        ("12", "***"),
    ],
)
def test_mask_phone(raw: str, masked: str) -> None:
    assert mask_phone(raw) == masked


def test_sanitizer_masks_secrets_tokens_and_phones() -> None:
    sanitize = Sanitizer([SECRET, "short"])

    text = sanitize(f"key={SECRET} url=/bot{BOT_TOKEN}/getMe Bearer abc.def tel +7 912 345 12 34")

    assert SECRET not in text
    assert BOT_TOKEN not in text
    assert "abc.def" not in text
    assert "+7***1234" in text


def test_sanitizer_keeps_ids_and_short_values() -> None:
    sanitize = Sanitizer(["short"])
    text = "update_id=123456789 tg_id=7123456789 short"

    assert sanitize(text) == text


def test_setup_logging_writes_sanitized_json(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    setup_logging("INFO", [SECRET], log_file=log_file)

    with logger.contextualize(tg_id=42):
        logger.info("calling api with {}", SECRET)
    logging.getLogger("aiogram.dispatcher").warning("stdlib phone 89123451234")
    logger.remove()  # flushes the enqueued file sink

    lines = log_file.read_text("utf-8").splitlines()
    records = [json.loads(line)["record"] for line in lines]
    messages = [r["message"] for r in records]
    assert "calling api with ***" in messages
    assert "stdlib phone +7***1234" in messages
    assert all(SECRET not in m for m in messages)
    assert any(r["extra"].get("tg_id") == 42 for r in records)
