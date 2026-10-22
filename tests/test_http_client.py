import httpx
import pytest
import respx

from app.clients.base import HttpClient
from app.core.errors import ExternalServiceError

BASE_URL = "https://api.example.test"


def make_client(max_retries: int = 2) -> HttpClient:
    return HttpClient("example", BASE_URL, max_retries=max_retries, backoff_s=0)


@respx.mock
async def test_success() -> None:
    route = respx.post(f"{BASE_URL}/v1/run").mock(return_value=httpx.Response(200, json={"ok": 1}))
    client = make_client()

    response = await client.request("POST", "/v1/run", json={"a": 1})

    assert response.json() == {"ok": 1}
    assert route.call_count == 1
    await client.close()


@respx.mock
async def test_retries_on_5xx_then_succeeds() -> None:
    route = respx.get(f"{BASE_URL}/x").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={})]
    )
    client = make_client()

    response = await client.request("GET", "/x")

    assert response.status_code == 200
    assert route.call_count == 2
    await client.close()


@respx.mock
async def test_4xx_is_not_retried() -> None:
    route = respx.get(f"{BASE_URL}/x").mock(return_value=httpx.Response(401))
    client = make_client()

    with pytest.raises(ExternalServiceError) as exc_info:
        await client.request("GET", "/x")

    assert exc_info.value.status_code == 401
    assert route.call_count == 1
    await client.close()


@respx.mock
async def test_5xx_exhausts_retries() -> None:
    route = respx.get(f"{BASE_URL}/x").mock(return_value=httpx.Response(500))
    client = make_client(max_retries=2)

    with pytest.raises(ExternalServiceError) as exc_info:
        await client.request("GET", "/x")

    assert exc_info.value.status_code == 500
    assert route.call_count == 3
    await client.close()


@respx.mock
async def test_timeout_retried_then_raises() -> None:
    route = respx.get(f"{BASE_URL}/x").mock(side_effect=httpx.ReadTimeout("slow"))
    client = make_client(max_retries=1)

    with pytest.raises(ExternalServiceError, match="ReadTimeout"):
        await client.request("GET", "/x")

    assert route.call_count == 2
    await client.close()
