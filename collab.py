"""
collab.py — Collaborative filtering using SVD matrix factorization

Builds a customer-product matrix from order history,
factorizes it with SVD, and uses the latent factors
to find products similar customers bought.
"""

import logging
import numpy as np
from sklearn.decomposition import TruncatedSVD
from collections import defaultdict
from itertools import permutations
import cache

logger = logging.getLogger(__name__)

COLLAB_TTL = 43200  # 12 hours


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

            await cache.set(f"svd:{shop_domain}", {
                "customer_factors": customer_factors.tolist(),
                "product_factors": product_factors.tolist(),
                "product_list": product_list,
                "customer_list": customer_list,
            }, ttl_seconds=COLLAB_TTL)

    # Store co-occurrence map
    await cache.set(
        f"collab:{shop_domain}",
        {k: dict(v) for k, v in product_map.items()},
        ttl_seconds=COLLAB_TTL
    )

    return {"products": len(all_product_ids), "customers": len(customer_products)}


async def get_collab_recommendations(
    shop_domain: str,
    purchased_ids: list,
    limit: int = 4,
) -> list:
    """Rank products purely from the cached collab map (SVD → co-occurrence).

    Returns ranked ``[{"id": int, "score": float, "source": str}]`` — ids and
    scores only. Helm resolves these ids against its own ProductCatalog to build
    cards, so this never fetches Shopify (no token, no live product read).
    """
    purchased = set(str(p) for p in purchased_ids)

    # Try SVD first
    svd_data = await cache.get(f"svd:{shop_domain}")
    if svd_data:
        scored = _svd_recommend(svd_data, purchased, limit)
        if scored:
            return _to_ranked(scored, "svd")

    # Fall back to co-occurrence
    product_map = await cache.get(f"collab:{shop_domain}")
    if not product_map:
        # Cold cache: return nothing and let Helm fall back to its own semantic
        # search. Never rebuild inline — it blocks/times out.
        logger.warning("Collab cache cold for %s — returning no collab recs.", shop_domain)
        return []

    scores = defaultdict(float)
    for product_id in purchased:
        related = product_map.get(str(product_id), {})
        for related_id, count in related.items():
            if related_id not in purchased:
                scores[related_id] += count

    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
    scored = {pid: score for pid, score in top}

    return _to_ranked(scored, "collab")


def _to_ranked(scored: dict, source: str) -> list:
    """Turn {product_id: score} into ranked [{id, score, source}], score-desc."""
    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    out = []
    for pid, score in ranked:
        try:
            product_id = int(pid)
        except (TypeError, ValueError):
            continue
        out.append({"id": product_id, "score": float(score), "source": source})
    return out


def _svd_recommend(svd_data: dict, purchased: set, limit: int) -> dict | None:
    """Returns {product_id: score} or None if no data."""
    product_list = svd_data["product_list"]
    product_factors = np.array(svd_data["product_factors"])

    purchased_indices = [
        i for i, p in enumerate(product_list) if p in purchased
    ]
    if not purchased_indices:
        return None

    user_vector = np.mean(product_factors[purchased_indices], axis=0)
    raw_scores = product_factors @ user_vector

    for idx in purchased_indices:
        raw_scores[idx] = -999

    top_indices = np.argsort(raw_scores)[::-1][:limit]
    return {
        product_list[i]: float(raw_scores[i])
        for i in top_indices
        if raw_scores[i] > -999
    }