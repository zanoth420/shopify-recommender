"""
tags.py — Tag-based content filtering

Recommends products based on overlap between
the customer's top tags/product types and
available products in the store.

If a query is provided, detects intent (shoes, pants, snowboards, etc.)
and filters by that product type instead of the customer's top type.
"""

from shopify import ShopifyClient

# Map keywords in customer messages to product types
QUERY_TYPE_MAP = {
    "snowboard": "snowboard",
    "winter": "snowboard",
    "ski": "snowboard",
    "wax": "accessory",
    "bindings": "accessory",
    "helmet": "accessory",
    "goggles": "accessory",
    "shoe": "shoes",
    "sneaker": "shoes",
    "running": "shoes",
    "footwear": "shoes",
    "pant": "clothing",
    "jean": "clothing",
    "shirt": "clothing",
    "t-shirt": "clothing",
    "jacket": "clothing",
    "short": "clothing",
    "clothing": "clothing",
    "apparel": "clothing",
    "swim": "swimwear",
    "beach": "swimwear",
    "surf": "swimwear",
}


def detect_product_type(query: str) -> str | None:
    """Detects which product type the customer is asking about."""
    if not query:
        return None
    q = query.lower()
    for keyword, product_type in QUERY_TYPE_MAP.items():
        if keyword in q:
            return product_type
    return None


async def get_tag_recommendations(
    top_product_types: list[str],
    top_tags: list[str],
    purchased_ids: list[int],
    shopify: ShopifyClient,
    shop_domain: str,
    query: str = None,
    limit: int = 4
) -> list:
    # Use query intent if available, otherwise customer's top product type
    query_type = detect_product_type(query)
    product_type = query_type or (top_product_types[0] if top_product_types else None)

    if not product_type:
        return []

    purchased = set(str(p) for p in purchased_ids)

    products = await shopify.get_products_by_type(
        product_type=product_type,
        limit=20
    )

    scored = []
    for p in products:
        if str(p["id"]) in purchased:
            continue

        product_tags = [
            t.strip()
            for t in (p.get("tags") or "").split(",")
            if t.strip()
        ]
        # If query was provided, score on broader matching (not just top_tags)
        if query_type:
            score = 1.0  # baseline for being right category
            # bonus if tags overlap with customer's preferences
            score += len(set(product_tags) & set(top_tags)) * 0.5
        else:
            score = float(len(set(product_tags) & set(top_tags)))

        scored.append({
            "id": p["id"],
            "title": p["title"],
            "url": f"https://{shop_domain}/products/{p['handle']}",
            "image": (p.get("images") or [{}])[0].get("src"),
            "price": (p.get("variants") or [{}])[0].get("price"),
            "product_type": product_type,
            "source": "tags",
            "score": score
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]