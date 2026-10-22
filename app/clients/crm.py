from loguru import logger

from app.clients.base import HttpClient
from app.schemas.lead import LeadPayload

SERVICE = "crm"
WEBHOOK_TIMEOUT_S = 15.0
WEBHOOK_MAX_RETRIES = 1


class LogCrmClient:
    """Demo CrmClient: the lead only goes to the log (fields names, never values)."""

    async def send(self, lead: LeadPayload) -> None:
        logger.info(
            "crm lead logged service=log lead_id={} fields={}", lead.lead_id, lead.filled_fields()
        )

    async def close(self) -> None:
        return None


class WebhookCrmClient:
    """CrmClient that POSTs the lead as JSON to CRM_WEBHOOK_URL.

    The URL is used as an absolute path so it is logged verbatim and therefore fully
    masked by the log sanitizer (it is registered as a secret: webhooks often embed a key).
    """

    def __init__(self, http: HttpClient, url: str) -> None:
        self._http = http
        self._url = url

    @classmethod
    def create(cls, url: str) -> "WebhookCrmClient":
        http = HttpClient(SERVICE, "", timeout_s=WEBHOOK_TIMEOUT_S, max_retries=WEBHOOK_MAX_RETRIES)
        return cls(http, url)

    async def send(self, lead: LeadPayload) -> None:
        await self._http.request("POST", self._url, json=lead.model_dump(mode="json"))
        logger.info("crm lead sent service=webhook lead_id={}", lead.lead_id)

    async def close(self) -> None:
        await self._http.close()
