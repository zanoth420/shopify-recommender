"""
shopify.py — Shopify data client via Worker proxy

Instead of calling Shopify Admin API directly (which requires
the store's token), this client calls the Worker's /internal/products
endpoint. The Worker holds Shopify tokens in KV and proxies requests.

Env vars:
  WORKER_URL       — e.g. https://shopify-bot.helm-trial.workers.dev
  INTERNAL_API_KEY — shared secret matching the Worker's secret
"""

import httpx
import asyncio
import os

WORKER_URL = os.getenv("WORKER_URL", "")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")

_transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2)


async def _post_with_retry(client, url, headers, json_body, max_retries=3):
    for attempt in range(max_retries):
        try:
            res = await client.post(url, headers=headers, json=json_body)
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
        """Fetch first page of orders via proxy."""
        try:
            data = await self.get(
                "orders.json?status=any&limit=250&fields=id,customer,line_items"
            )
            return data.get("orders", [])
        except Exception as e:
            print(f"[shopify] get_all_orders failed: {e}")
            return []

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