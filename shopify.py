"""
shopify.py — Shopify Admin API client (direct)

Calls the Shopify Admin REST API directly using the store's access token,
which the caller (Helm) passes per request. This service is *tokenless* — it
holds no Shopify credentials and never persists the token; it uses the token
only for the duration of the call.

The token should be scoped read-only to products + orders. Even if this
service is compromised, a read-only token bounds the blast radius to reading
catalog/order data (no mutations).

Pagination follows Shopify's `Link` response header (rel="next"), the standard
cursor mechanism — previously the Cloudflare Worker proxy parsed this for us.

Env vars:
  SHOPIFY_API_VERSION — Admin API version (default 2025-04)
"""

import asyncio
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2025-04")

# Safety cap so a huge store (or a pagination bug) can't loop forever.
# 40 pages * 250 = 10,000 orders — plenty for a co-occurrence map.
MAX_ORDER_PAGES = 40

_transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2)

# Shopify Link header: <https://shop/admin/api/2025-04/orders.json?...&page_info=XXX>; rel="next"
_NEXT_PAGE_INFO = re.compile(r'page_info=([^&>]+)[^>]*>;\s*rel="next"')


class RateLimitedError(Exception):
    """Shopify returned 429 (rate limit) and retries were exhausted."""


def _extract_next_page_info(link_header: str | None) -> str | None:
    """Pull the next-page cursor out of Shopify's Link header, if present."""
    if not link_header:
        return None
    match = _NEXT_PAGE_INFO.search(link_header)
    return match.group(1) if match else None


async def _get_with_retry(client, url, headers, max_retries=3):
    """GET the Shopify Admin API with retry on network errors AND 429s.

    429 is distinct from a network error: Shopify sends a rate-limit status with
    a Retry-After header. We honor that delay rather than the fixed backoff used
    for transient network failures.
    """
    for attempt in range(max_retries):
        try:
            res = await client.get(url, headers=headers)

            if res.status_code == 429:
                if attempt == max_retries - 1:
                    raise RateLimitedError("Shopify rate limit, retries exhausted")
                retry_after = res.headers.get("Retry-After", "2")
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = 2.0
                await asyncio.sleep(delay)
                continue

            res.raise_for_status()
            return res

        except (httpx.ReadError, httpx.ConnectError, httpx.TimeoutException):
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))

    return None


class ShopifyClient:
    """Fetches Shopify data directly from the Admin API.

    Constructed per request with the store domain and the store's access token
    (passed by Helm). The token is never stored or logged.
    """

    def __init__(self, domain: str, access_token: str):
        self.domain = domain
        self.access_token = access_token
        self.base_url = f"https://{domain}/admin/api/{SHOPIFY_API_VERSION}"
        self.headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": access_token,
        }

    async def _get_raw(self, client, path: str):
        return await _get_with_retry(client, f"{self.base_url}/{path}", self.headers)

    async def get(self, path: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0, transport=_transport) as client:
            res = await self._get_raw(client, path)
            return res.json()

    async def get_all_orders(self) -> list:
        """Fetch ALL orders by following Shopify's Link-header cursor.

        Stops when there's no next cursor or MAX_ORDER_PAGES is hit. On
        rate-limit-after-retries, returns whatever was collected so far (partial
        data) rather than crashing — but logs it loudly so a persistently
        rate-limited store is visible.
        """
        all_orders = []
        # First page: full query with filters. Subsequent pages: page_info only.
        # Shopify rejects mixing page_info with other filters, so once we have a
        # cursor we send ONLY limit + page_info.
        path = "orders.json?status=any&limit=250&fields=id,customer,line_items"
        pages = 0

        async with httpx.AsyncClient(timeout=30.0, transport=_transport) as client:
            while path and pages < MAX_ORDER_PAGES:
                try:
                    res = await self._get_raw(client, path)
                except RateLimitedError:
                    logger.warning(
                        "[shopify] rate limited after %d pages (%d orders so far) for %s — "
                        "returning partial data",
                        pages,
                        len(all_orders),
                        self.domain,
                    )
                    break
                except Exception as e:
                    logger.warning(
                        "[shopify] get_all_orders failed on page %d for %s: %s",
                        pages,
                        self.domain,
                        e,
                    )
                    break

                all_orders.extend(res.json().get("orders", []))
                pages += 1

                next_cursor = _extract_next_page_info(res.headers.get("Link"))
                if not next_cursor:
                    break
                # page_info is incompatible with status/fields filters — send it alone.
                path = f"orders.json?limit=250&page_info={next_cursor}"

        if pages >= MAX_ORDER_PAGES:
            logger.warning(
                "[shopify] hit MAX_ORDER_PAGES (%d) for %s — order history may be truncated",
                MAX_ORDER_PAGES,
                self.domain,
            )

        logger.info("[shopify] fetched %d orders across %d pages for %s", len(all_orders), pages, self.domain)
        return all_orders

    async def get_products_by_ids(self, ids: list) -> list:
        if not ids:
            return []
        res = await self.get(
            f"products.json?ids={','.join(str(i) for i in ids)}&fields=id,title,handle,images,variants,product_type"
        )
        return res.get("products", [])

    async def get_products_by_type(self, product_type: str, limit: int = 20) -> list:
        from urllib.parse import quote

        res = await self.get(
            f"products.json?product_type={quote(product_type)}&limit={limit}"
            f"&fields=id,title,handle,images,variants,tags,product_type"
        )
        return res.get("products", [])
