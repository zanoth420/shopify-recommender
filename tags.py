"""
tags.py — Tag-based content filtering

Recommends products based on overlap between
the customer's top tags/product types and
available products in the store.
"""

from shopify import ShopifyClient


async def get_tag_recommendations(
    top_product_types: list[str],
    top_tags: list[str],
    purchased_ids: list[int],
    shopify: ShopifyClient,
    shop_domain: str,
    limit: int = 4
) -> list:
    if not top_product_types:
        return []

    purchased = set(str(p) for p in purchased_ids)

    # Fetch products from the customer's top product type
    products = await shopify.get_products_by_type(
        product_type=top_product_types[0],
        limit=20
    )

    scored = []
    for p in products:
        if str(p["id"]) in purchased:
            continue  # skip already bought

        # Score by tag overlap
        product_tags = [
            t.strip()
            for t in (p.get("tags") or "").split(",")
            if t.strip()
        ]
        score = len(set(product_tags) & set(top_tags))

        scored.append({
            "id": p["id"],
            "title": p["title"],
            "url": f"https://{shop_domain}/products/{p['handle']}",
            "image": (p.get("images") or [{}])[0].get("src"),
            "price": (p.get("variants") or [{}])[0].get("price"),
            "source": "tags",
            "score": float(score)
        })

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]