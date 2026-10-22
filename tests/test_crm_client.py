import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from loguru import logger

from app.clients.base import HttpClient
from app.clients.crm import LogCrmClient, WebhookCrmClient
from app.core.errors import ExternalServiceError
from app.db.models import FurnitureType
from app.schemas.lead import LeadPayload

WEBHOOK_URL = "https://crm.test/rest/1/secret-hook/lead.add"


def make_lead(**overrides: object) -> LeadPayload:
    fields: dict[str, object] = {
        "lead_id": 7,
        "tg_id": 500,
        "tg_username": "ann",
        "created_at": datetime(2026, 10, 22, 12, 0, tzinfo=UTC),
        "name": "Анна",
        "furniture_type": FurnitureType.KITCHEN,
        "dimensions": "3 м",
        "length_m": 3.0,
        "price_class": None,
        "style": None,
        "budget": None,
        "city": "Казань",
        "deadline": None,
        "phone": "+79123451234",
        "comment": None,
        "business_role": None,
        "lead_sources": None,
        "estimate_min": 115000,
        "estimate_max": 155000,
        "currency": "₽",
        "photo_file_ids": ["p1"],
    }
    return LeadPayload.model_validate({**fields, **overrides})


async def test_log_client_logs_field_names_not_values() -> None:
    lines: list[str] = []
    sink_id = logger.add(lines.append, format="{message}")
    try:
        await LogCrmClient().send(make_lead())
    finally:
        logger.remove(sink_id)

    assert "lead_id=7" in lines[0]
    assert "phone" in lines[0]
    assert "+79123451234" not in lines[0]
    assert "Казань" not in lines[0]


@respx.mock
async def test_webhook_posts_lead_json() -> None:
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    client = WebhookCrmClient(HttpClient("crm", "", backoff_s=0), WEBHOOK_URL)

    await client.send(make_lead())

    body = json.loads(route.calls.last.request.content)
    assert body["lead_id"] == 7
    assert body["furniture_type"] == "kitchen"
    assert body["phone"] == "+79123451234"
    assert body["photo_file_ids"] == ["p1"]
    assert route.calls.last.request.url == WEBHOOK_URL  # no trailing slash added
    await client.close()


@respx.mock
@pytest.mark.parametrize(("status", "calls"), [(400, 1), (503, 2)])
async def test_webhook_errors_raise(
    monkeypatch: pytest.MonkeyPatch, status: int, calls: int
) -> None:
    monkeypatch.setattr(HttpClient, "_sleep_before_retry", AsyncMock())  # no real backoff
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(status))
    client = WebhookCrmClient.create(WEBHOOK_URL)

    with pytest.raises(ExternalServiceError):
        await client.send(make_lead())

    assert route.call_count == calls  # 4xx is final, 5xx is retried once
    await client.close()
