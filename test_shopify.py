"""Tests for the direct Shopify Admin API client.

Uses httpx.MockTransport (no network, no new deps) by monkeypatching the
module-level transport the client uses. Async tests run on anyio's pytest
plugin (ships with anyio, a FastAPI dependency) — no pytest-asyncio needed.
"""

import httpx
import pytest

import shopify
from shopify import ShopifyClient, _extract_next_page_info

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    # Run async tests on asyncio only (avoid requiring trio).
    return "asyncio"


def _install_mock(monkeypatch, handler):
    """Point the client's transport at a MockTransport using `handler`."""
    monkeypatch.setattr(shopify, "_transport", httpx.MockTransport(handler))


# ── Link header parsing ───────────────────────────────────────────────

def test_extract_next_page_info_finds_next_cursor():
    link = (
        '<https://s.myshopify.com/admin/api/2025-04/orders.json?limit=250&page_info=ABC123>; rel="next"'
    )
    assert _extract_next_page_info(link) == "ABC123"


def test_extract_next_page_info_ignores_previous_only():
    link = '<https://s/admin/api/2025-04/orders.json?page_info=PREV>; rel="previous"'
    assert _extract_next_page_info(link) is None


def test_extract_next_page_info_handles_missing_header():
    assert _extract_next_page_info(None) is None
    assert _extract_next_page_info("") is None


# ── Direct calls use the token + correct URL ──────────────────────────

async def test_get_products_by_ids_calls_shopify_directly_with_token(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["token"] = request.headers.get("X-Shopify-Access-Token")
        return httpx.Response(200, json={"products": [{"id": 1, "title": "Shoe"}]})

    _install_mock(monkeypatch, handler)
    client = ShopifyClient(domain="demo.myshopify.com", access_token="shpat_secret")

    products = await client.get_products_by_ids([1, 2])

    assert products == [{"id": 1, "title": "Shoe"}]
    assert seen["url"].startswith("https://demo.myshopify.com/admin/api/")
    assert "products.json?ids=1,2" in seen["url"]
    assert seen["token"] == "shpat_secret"


async def test_get_products_by_ids_short_circuits_on_empty(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not call Shopify for empty id list")

    _install_mock(monkeypatch, handler)
    client = ShopifyClient(domain="demo.myshopify.com", access_token="t")
    assert await client.get_products_by_ids([]) == []


# ── Order pagination follows the Link header ──────────────────────────

async def test_get_all_orders_pages_via_link_header(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "page_info=" not in str(request.url):
            # First page → return one order + a next cursor.
            return httpx.Response(
                200,
                json={"orders": [{"id": 1, "line_items": []}]},
                headers={
                    "Link": '<https://demo.myshopify.com/admin/api/2025-04/orders.json'
                    '?limit=250&page_info=NEXT1>; rel="next"'
                },
            )
        # Second page → one more order, no Link header → stop.
        return httpx.Response(200, json={"orders": [{"id": 2, "line_items": []}]})

    _install_mock(monkeypatch, handler)
    client = ShopifyClient(domain="demo.myshopify.com", access_token="t")

    orders = await client.get_all_orders()

    assert [o["id"] for o in orders] == [1, 2]
    assert len(calls) == 2
    assert "page_info=NEXT1" in calls[1]


async def test_get_all_orders_stops_without_next_cursor(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"orders": [{"id": 1, "line_items": []}]})

    _install_mock(monkeypatch, handler)
    client = ShopifyClient(domain="demo.myshopify.com", access_token="t")

    orders = await client.get_all_orders()

    assert [o["id"] for o in orders] == [1]
    assert len(calls) == 1
