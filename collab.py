"""
collab.py — Collaborative filtering using SVD matrix factorization

Builds a customer-product matrix from order history,
factorizes it with SVD, and uses the latent factors
to find products similar customers bought.
"""

import numpy as np
from sklearn.decomposition import TruncatedSVD
from collections import defaultdict
from itertools import permutations
import cache

COLLAB_TTL = 6 * 3600  # 6 hours

async def build_collab_map(shop_domain: str, shopify):
    all_orders = await shopify.get_all_orders()

    product_map = defaultdict(lambda: defaultdict(int))
    all_product_ids = set()
    customer_products = defaultdict(set)

    for order in all_orders:
        product_ids = [
            str(item["product_id"])
            for item in order.get("line_items", [])
            if item.get("product_id")
        ]
        customer_id = str(order.get("customer", {}).get("id", ""))

        all_product_ids.update(product_ids)
        if customer_id:
            customer_products[customer_id].update(product_ids)

        for a, b in permutations(product_ids, 2):
            product_map[a][b] += 1

    # Try SVD if enough data
    if len(customer_products) >= 5 and len(all_product_ids) >= 5:
        product_list = sorted(all_product_ids)
        customer_list = sorted(customer_products.keys())

        product_idx = {p: i for i, p in enumerate(product_list)}
        customer_idx = {c: i for i, c in enumerate(customer_list)}

        matrix = np.zeros((len(customer_list), len(product_list)))
        for customer_id, products in customer_products.items():
            for product_id in products:
                if customer_id in customer_idx and product_id in product_idx:
                    matrix[customer_idx[customer_id]][product_idx[product_id]] = 1

        n_components = min(20, len(customer_list) - 1, len(product_list) - 1)
        if n_components >= 2:
            svd = TruncatedSVD(n_components=n_components)
            customer_factors = svd.fit_transform(matrix)
            product_factors = svd.components_.T

            cache.set(f"svd:{shop_domain}", {
                "customer_factors": customer_factors.tolist(),
                "product_factors": product_factors.tolist(),
                "product_list": product_list,
                "customer_list": customer_list,
            }, ttl_seconds=COLLAB_TTL)

    # Store co-occurrence map
    cache.set(
        f"collab:{shop_domain}",
        {k: dict(v) for k, v in product_map.items()},
        ttl_seconds=COLLAB_TTL
    )

    return {"products": len(all_product_ids), "customers": len(customer_products)}


async def get_collab_recommendations(
    shop_domain: str,
    purchased_ids: list,
    shopify,
    limit: int = 4
) -> list:
    purchased = set(str(p) for p in purchased_ids)

    # Try SVD first
    svd_data = cache.get(f"svd:{shop_domain}")
    if svd_data:
        recs = _svd_recommend(svd_data, purchased, limit)
        if recs:
            return await _fetch_product_details(recs, purchased, shopify, shop_domain, "svd")

    # Fall back to co-occurrence
    product_map = cache.get(f"collab:{shop_domain}")
    if not product_map:
        # No cache yet — build it now
        await build_collab_map(shop_domain, shopify)
        product_map = cache.get(f"collab:{shop_domain}")

    if not product_map:
        return []

    scores = defaultdict(float)
    for product_id in purchased:
        related = product_map.get(str(product_id), {})
        for related_id, count in related.items():
            if related_id not in purchased:
                scores[related_id] += count

    top_ids = sorted(scores, key=scores.get, reverse=True)[:limit]
    return await _fetch_product_details(top_ids, purchased, shopify, shop_domain, "collab")


def _svd_recommend(svd_data: dict, purchased: set, limit: int) -> list:
    product_list = svd_data["product_list"]
    product_factors = np.array(svd_data["product_factors"])

    purchased_indices = [
        i for i, p in enumerate(product_list) if p in purchased
    ]
    if not purchased_indices:
        return []

    user_vector = np.mean(product_factors[purchased_indices], axis=0)
    scores = product_factors @ user_vector

    for idx in purchased_indices:
        scores[idx] = -999

    top_indices = np.argsort(scores)[::-1][:limit]
    return [product_list[i] for i in top_indices]


async def _fetch_product_details(
    product_ids: list,
    purchased: set,
    shopify,
    shop_domain: str,
    source: str
) -> list:
    if not product_ids:
        return []
    products = await shopify.get_products_by_ids(product_ids)
    return [
        {
            "id": p["id"],
            "title": p["title"],
            "url": f"https://{shop_domain}/products/{p['handle']}",
            "image": (p.get("images") or [{}])[0].get("src"),
            "price": (p.get("variants") or [{}])[0].get("price"),
            "product_type": p.get("product_type", ""),
            "source": source,
            "score": 0.0
        }
        for p in products
        if str(p["id"]) not in purchased
    ]
