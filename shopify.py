"""
shopify.py — Shopify data client via Worker proxy

Instead of calling Shopify Admin API directly (which requires
the store's token), this client calls the Worker's /internal/products
endpoint. The Worker holds Shopify tokens in KV and proxies requests.

The Worker now returns Shopify's JSON body plus a "_pagination" object
with next/previous page_info cursors (parsed from Shopify's Link header),
so get_all_orders can page through the full order history.

Env vars:
  WORKER_URL       — e.g. https://shopify-bot.helm-trial.workers.dev
  INTERNAL_API_KEY — shared secret matching the Worker's secret
"""

import httpx
import asyncio
import os
import logging

logger = logging.getLogger(__name__)

WORKER_URL = os.getenv("WORKER_URL", "")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")

# Safety cap so a huge store (or a pagination bug) can't loop forever.
# 40 pages * 250 = 10,000 orders — plenty for a co-occurrence map.
MAX_ORDER_PAGES = 40

_transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2)


class RateLimitedError(Exception):
    """Worker proxy returned 429 (Shopify rate limit) and retries were exhausted."""


async def _post_with_retry(client, url, headers, json_body, max_retries=3):
    """POST to the Worker proxy with retry on network errors AND 429s.

    429 is distinct from a network error: the Worker forwards Shopify's
    rate-limit status and Retry-After. We honor that delay rather than the
    fixed backoff used for transient network failures.
    """
    for attempt in range(max_retries):
        try:
            res = await client.post(url, headers=headers, json=json_body)

            if res.status_code == 429:
                # Worker returns { "error": "rate_limited", "retry_after": N }
                if attempt == max_retries - 1:
                    raise RateLimitedError("Shopify rate limit, retries exhausted")
                try:
                    retry_after = res.json().get("retry_after", 2)
                except Exception:
                    retry_after = 2
                await asyncio.sleep(float(retry_after))
                continue

            res.raise_for_status()
            return res

        except (httpx.ReadError, httpx.ConnectError, httpx.TimeoutException):
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))

    return None


class ShopifyClient:
    """Fetches Shopify data through the Worker's /internal/products proxy."""

    def __init__(self, domain: str):
        self.domain = domain
        self.proxy_url = f"{WORKER_URL}/internal/products"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {INTERNAL_API_KEY}",
        }

    async def get(self, path: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0, transport=_transport) as client:
            res = await _post_with_retry(
                client, self.proxy_url, self.headers,
                {"shop_domain": self.domain, "path": path}
            )
            return res.json()

    async def get_all_orders(self) -> list:
        """Fetch ALL orders by following Shopify's page_info cursor.

        Stops when there's no next cursor or MAX_ORDER_PAGES is hit.
        On rate-limit-after-retries, returns whatever was collected so far
        (partial data) rather than crashing — but logs it loudly so a
        persistently rate-limited store is visible.
        """
        all_orders = []
        # First page: full query with filters. Subsequent pages: page_info only.
        # Shopify rejects mixing page_info with other filters, so once we have
        # a cursor we send ONLY limit + page_info.
        path = "orders.json?status=any&limit=250&fields=id,customer,line_items"
        pages = 0

        while path and pages < MAX_ORDER_PAGES:
            try:
                data = await self.get(path)
            except RateLimitedError:
                logger.warning(
                    "[shopify] rate limited after %d pages (%d orders so far) for %s — "
                    "returning partial data",
                    pages, len(all_orders), self.domain,
                )
                break
            except Exception as e:
                logger.warning(
                    "[shopify] get_all_orders failed on page %d for %s: %s",
                    pages, self.domain, e,
                )
                break

            all_orders.extend(data.get("orders", []))
            pages += 1

            next_cursor = (data.get("_pagination") or {}).get("next")
            if not next_cursor:
                break
            # page_info is incompatible with status/fields filters — send it alone.
            path = f"orders.json?limit=250&page_info={next_cursor}"

        if pages >= MAX_ORDER_PAGES:
            logger.warning(
                "[shopify] hit MAX_ORDER_PAGES (%d) for %s — order history may be truncated",
                MAX_ORDER_PAGES, self.domain,
            )

        logger.info("[shopify] fetched %d orders across %d pages for %s",
                    len(all_orders), pages, self.domain)
        return all_orders

    async def get_products_by_ids(self, ids: list) -> list:
        if not ids:
            return []
        res = await self.get(
            f"products.json?ids={','.join(str(i) for i in ids)}"
            f"&fields=id,title,handle,images,variants,product_type"
        )
        return res.get("products", [])

    async def get_products_by_type(self, product_type: str, limit: int = 20) -> list:
        from urllib.parse import quote
        res = await self.get(
            f"products.json?product_type={quote(product_type)}&limit={limit}"
            f"&fields=id,title,handle,images,variants,tags,product_type"
        )
        return res.get("products", [])