"""
main.py — FastAPI recommendation service

Endpoints:
  POST /recommend  — returns ranked product recommendations
  POST /build-map  — rebuilds the collab map
  GET  /health     — health check

All endpoints except /health require INTERNAL_API_KEY auth.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from models import RecommendRequest, RecommendResponse
from collab import get_collab_recommendations, build_collab_map
from tags import get_tag_recommendations, detect_product_type
from browse import score_browse_intent
from shopify import ShopifyClient
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")
WORKER_URL = os.getenv("WORKER_URL", "")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[WORKER_URL] if WORKER_URL else ["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "Authorization"],
)


def verify_internal_key(request: Request):
    """Verify the request came from our Worker."""
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    if not INTERNAL_API_KEY or token != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/recommend")
async def recommend(body: RecommendRequest, request: Request):
    verify_internal_key(request)

    shopify = ShopifyClient(domain=body.shop_domain)

    query_type = detect_product_type(body.query) if body.query else None

    collab_recs, tag_recs = await asyncio.gather(
        get_collab_recommendations(
            shop_domain=body.shop_domain,
            purchased_ids=body.purchased_product_ids,
            shopify=shopify,
        ),
        get_tag_recommendations(
            top_product_types=body.top_product_types,
            top_tags=body.top_tags,
            purchased_ids=body.purchased_product_ids,
            shopify=shopify,
            shop_domain=body.shop_domain,
            query=body.query,
        ),
    )

    if query_type:
        collab_recs = [r for r in collab_recs if _matches_type(r, query_type)]

    browse_boost = score_browse_intent(body.browse_history)
    merged = merge_recommendations(
        collab_recs, tag_recs, browse_boost,
        query_type=query_type,
        limit=body.limit or 4,
    )

    return RecommendResponse(recommendations=merged)


@app.post("/build-map")
async def build_map(shop_domain: str, request: Request):
    verify_internal_key(request)
    shopify = ShopifyClient(domain=shop_domain)
    result = await build_collab_map(shop_domain=shop_domain, shopify=shopify)
    return {"success": True, **result}


def _matches_type(rec, query_type):
    pt = (rec.get("product_type") or "").lower()
    return query_type.lower() in pt


def merge_recommendations(collab, tags, browse_boost, query_type=None, limit=4):
    seen = set()
    merged = []

    if query_type:
        all_recs = tags + collab
    else:
        all_recs = collab + tags

    # Build new list with boosted scores — don't mutate originals
    boosted = []
    for rec in all_recs:
        boost = browse_boost.get(rec["title"], 0)
        boosted.append({
            **rec,
            "score": rec.get("score", 0) + (boost * 0.5),
            "browse_boost": boost,
        })

    boosted.sort(key=lambda x: x["score"], reverse=True)

    for rec in boosted:
        rid = str(rec["id"])
        if rid not in seen:
            seen.add(rid)
            merged.append(rec)
        if len(merged) >= limit:
            break

    return merged