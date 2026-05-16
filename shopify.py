"""
shopify.py — Async Shopify Admin API client
"""

import httpx
import re

# Force IPv4 to avoid async DNS issues on Windows
_transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")


class ShopifyClient:
    def __init__(self, domain: str, token: str, api_version: str = "2026-04"):
        self.base = f"https://{domain}/admin/api/{api_version}"
        self.headers = {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json"
        }

    async def get(self, path: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0, transport=_transport) as client:
            res = await client.get(f"{self.base}/{path}", headers=self.headers)
            res.raise_for_status()
            return res.json()

    async def get_all_orders(self) -> list:
        all_orders = []
        url = f"{self.base}/orders.json?status=any&limit=250&fields=id,customer,line_items"
        async with httpx.AsyncClient(timeout=30.0, transport=_transport) as client:
            while url:
                res = await client.get(url, headers=self.headers)
                data = res.json()
                all_orders.extend(data.get("orders", []))
                link = res.headers.get("Link", "")
                next_match = re.search(r'<([^>]+)>;\s*rel="next"', link)
                url = next_match.group(1) if next_match else None
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