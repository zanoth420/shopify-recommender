# Recommender Service — Integration Contract

**Version:** 1.0  
**Last updated:** May 2026  
**Base URL:** `https://web-production-b50e.up.railway.app`  
**Deployed on:** Railway  
**Auth:** All endpoints except `/health` require `Authorization: Bearer {INTERNAL_API_KEY}`

---

## Overview

A standalone FastAPI service that returns ranked product recommendations for a given Shopify store and customer. It runs independently of Helm — Helm calls it as an internal API via `RecommenderGateway`.

**What it does:**
- Collaborative filtering (SVD + co-occurrence) based on order history
- Tag/product-type filtering based on customer purchase profile
- Browse intent scoring with recency decay
- Merges and ranks all signals into a final recommendation list

**What it does NOT do:**
- Hold Shopify credentials — it calls Shopify through the Cloudflare Worker proxy (`/internal/products`)
- Manage sessions or conversation history — that's Helm's job
- Know anything about the chat context — Helm passes the customer message as `query`

---

## Authentication

Every request (except `/health`) must include:

```
Authorization: Bearer {INTERNAL_API_KEY}
```

The `INTERNAL_API_KEY` is a shared secret between Helm and the recommender. Store it in Helm's Secrets Manager alongside other credentials. The recommender and the Worker share the same key.

---

## Endpoints

### `GET /health`
No auth required. Returns service status and cache backend info.

**Response:**
```json
{
  "status": "ok",
  "cache_backend": "redis",
  "cache_reachable": true
}
```

`cache_backend` is either `"redis"` or `"memory"`. If it's `"memory"` in production, something is wrong — `REDIS_URL` is not set or Redis is unreachable.

---

### `POST /recommend`
**The main endpoint.** Returns ranked product recommendations.

**Request body:**
```json
{
  "shop_domain": "store.myshopify.com",
  "purchased_product_ids": [123, 456],
  "top_product_types": ["snowboard", "clothing"],
  "top_tags": ["sale", "winter", "mens"],
  "browse_history": [
    {
      "event": "product_viewed",
      "data": { "productTitle": "Some Product" },
      "timestamp": "2026-05-28T10:00:00Z"
    }
  ],
  "limit": 4,
  "query": "looking for something warm"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `shop_domain` | string | ✅ | Must be registered in the Worker's KV store |
| `purchased_product_ids` | int[] | No | Customer's full purchase history |
| `top_product_types` | string[] | No | Customer's top product types by frequency |
| `top_tags` | string[] | No | Customer's top tags by frequency |
| `browse_history` | BrowseEvent[] | No | From the Widget's tracker — pass as-is |
| `limit` | int | No | Default 4, max sensible value ~10 |
| `query` | string | No | The customer's message text — used for intent detection |

**All array fields default to empty — the service degrades gracefully when data is missing.** A request with only `shop_domain` returns featured/popular products with no personalization.

**Response:**
```json
{
  "recommendations": [
    {
      "id": 789,
      "title": "Product Name",
      "url": "https://store.myshopify.com/products/handle",
      "image": "https://cdn.shopify.com/...",
      "price": "49.99",
      "source": "svd",
      "score": 1.24
    }
  ]
}
```

`source` tells you which signal produced the recommendation: `"svd"`, `"collab"`, `"tags"`, `"featured"`, or `"fallback"`. Useful for debugging.

---

### `POST /recommend/debug`
Same as `/recommend` but includes full scoring breakdown. Use this during development and for debugging bad recommendations. Don't call it in production request paths.

**Response adds:**
```json
{
  "recommendations": [...],
  "debug": {
    "query_type_detected": "clothing",
    "merge_order": "tags_first",
    "collab_candidates": [
      {
        "id": 789,
        "title": "Product Name",
        "source": "svd",
        "raw_score": 0.92,
        "browse_boost": 0.3,
        "final_score": 1.07
      }
    ],
    "tag_candidates": [...],
    "browse_boost_map": { "Product Name": 0.3 },
    "final_picks": [789, 101, 202, 303]
  }
}
```

---

### `POST /build-map?shop_domain={domain}`
Triggers a full rebuild of the collaborative filtering map for the given store. Fetches all orders from Shopify via the Worker proxy, runs SVD, stores results in Redis.

**When Helm should call this:**
- On Shopify `orders/create` webhook — invalidate + schedule a rebuild
- On store first connection — warm the cache immediately
- Not on every request — it's expensive

**Response:**
```json
{
  "success": true,
  "products": 120,
  "customers": 340
}
```

---



Add to Helm's env vars and settings:
```
RECOMMENDER_URL=https://web-production-b50e.up.railway.app
INTERNAL_API_KEY=                    # same key the Worker uses
```

---

## Data the recommender needs from Helm

For a `ShopifyChannel` conversation, Helm needs to pass:

| Data | Where Helm gets it | Notes |
|---|---|---|
| `shop_domain` | `ChannelConnection` for the Shopify channel | Stored on install |
| `purchased_product_ids` | Shopify API — customer's order history | Cache in `ChannelConnection` or a profile table |
| `top_product_types` | Derived from order history | Same source as above |
| `top_tags` | Derived from order history | Same source as above |
| `browse_history` | Forwarded by the Worker from the widget tracker | Worker stores in KV, forwards on `/chat` |
| `query` | The customer's message from `DMPipeline` | Pass as-is |

---

## Degradation behavior

The service is designed to never error on missing data. Here's what happens when things are absent or broken:

| Condition | Behavior |
|---|---|
| Collab cache cold (first deploy, restart) | Returns tag-based recommendations only, logs warning |
| No purchase history | Returns tag-based or featured products |
| No tags or product types | Returns collab-based only |
| `query` is None or empty | Skips intent detection, uses collab-first merge |
| Recommender unreachable | Helm should catch the exception and continue without recommendations — don't fail the chat response |
| Redis down | Falls back to in-memory cache automatically, logs warning |

---

## Architecture notes for the Helm integrator

**What stays:**
- This recommender service — runs as a separate ECS/Railway service
- The Widget frontend (`widget-client.js`, `ui.js`, `tracker.js`, `identity.js`) — reusable as-is
- The Worker as a thin proxy — serves the widget JS, forwards chat to Helm, receives browse events

**What gets replaced when Helm integrates:**
- Worker's Gemini AI backend 
- Worker's KV session storage
- Worker's D1 knowledge base (FAQs/policies)
- Worker's customer profile building → Helm fetches from Shopify directly using stored token

**Widget integration point:**
The widget's `botUrl` config  currently points at the Worker's `/chat` endpoint. When Helm takes over, that URL changes to Helm's new `/api/shopify/chat/` endpoint. The Worker becomes a thin proxy that forwards to Helm. The widget JS itself does not change.

---

## Environment variables

| Variable | Where | Notes |
|---|---|---|
| `INTERNAL_API_KEY` | Recommender + Worker + Helm | Shared secret — rotate independently |
| `REDIS_URL` | Recommender | Optional — falls back to in-memory without it |
| `WORKER_URL` | Recommender | The Cloudflare Worker base URL |
| `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` | Recommender | For LLM intent detection (replacing hardcoded QUERY_TYPE_MAP) |
| `RECOMMENDER_URL` | Helm | Points at this service |

---

## Known gaps (to be addressed post-handoff)

1. **`QUERY_TYPE_MAP` is hardcoded** for snowboards/shoes/clothing. LLM intent detection is planned to replace it — tracked, not yet implemented.
2. **No Shopify webhook integration yet** — `build_collab_map` is rebuilt on a schedule (every 6 hours) rather than triggered by new orders. When Helm adds `orders/create` webhook support, it should call `/build-map` to keep the model fresh.
3. **Single-tenant Worker setup** — the Worker currently uses a manual token registration flow (`/admin/register`). This becomes the Shopify OAuth flow when the full app is built.
4. **Cart-aware recommendations** — designed and documented, not yet implemented. The `RecommendRequest` model needs a `cart_product_ids` field and the collab scoring needs to treat cart items as high-intent seeds.
