"""
main.py — FastAPI recommendation service

Endpoints:
  POST /recommend  — returns ranked product recommendations
  POST /build-map  — rebuilds the collab map (run periodically)
  GET  /health     — health check
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from models import RecommendRequest, RecommendResponse
from collab import get_collab_recommendations, build_collab_map
from tags import get_tag_recommendations
from browse import score_browse_intent
from shopify import ShopifyClient
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/recommend")
async def recommend(body: RecommendRequest):
    shopify = ShopifyClient(
        domain=body.shop_domain,
        token=body.shopify_token
    )

    collab_recs, tag_recs = await asyncio.gather(
        get_collab_recommendations(
            shop_domain=body.shop_domain,
            purchased_ids=body.purchased_product_ids,
            shopify=shopify
        ),
        get_tag_recommendations(
            top_product_types=body.top_product_types,
            top_tags=body.top_tags,
            purchased_ids=body.purchased_product_ids,
            shopify=shopify,
            shop_domain=body.shop_domain
        )
    )

    browse_boost = score_browse_intent(body.browse_history)
    merged = merge_recommendations(collab_recs, tag_recs, browse_boost, limit=body.limit or 4)

    return RecommendResponse(recommendations=merged)

@app.post("/build-map")
async def build_map(shop_domain: str, shopify_token: str, admin_key: str):
    if admin_key != os.getenv("ADMIN_KEY"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    shopify = ShopifyClient(domain=shop_domain, token=shopify_token)
    result = await build_collab_map(shop_domain=shop_domain, shopify=shopify)
    return {"success": True, **result}

def merge_recommendations(collab, tags, browse_boost, limit=4):
    seen = set()
    merged = []

    all_recs = collab + tags
    for rec in all_recs:
        boost = browse_boost.get(rec["title"], 0)
        rec["score"] = rec.get("score", 0) + (boost * 0.5)

    all_recs.sort(key=lambda x: x.get("score", 0), reverse=True)

    for rec in all_recs:
        rid = str(rec["id"])
        if rid not in seen:
            seen.add(rid)
            merged.append(rec)
        if len(merged) >= limit:
            break

    return merged