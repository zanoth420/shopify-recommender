"""
main.py — FastAPI recommendation service

Endpoints:
  POST /recommend        — returns ranked product recommendations
  POST /recommend/debug  — same + scoring breakdown
  POST /build-map        — rebuilds the collab map
  GET  /health           — health check
  GET  /debug/cache      — inspect cached collab/SVD data
  GET  /logs             — view recent recommendation logs (JSON)
  GET  /logs/view        — visual dashboard for recommendation logs

The recommender is a pure collaborative-filtering ranker: it ranks from the
cached collab map (SVD → co-occurrence) and returns product ids + scores.
Content matching (semantic search) and card building live in the host app
(Helm), which resolves these ids against its own catalog.

All endpoints except /health require INTERNAL_API_KEY auth.
"""

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from models import (
    RecommendRequest, RecommendResponse,
    DebugRecommendResponse, DebugInfo, ScoringBreakdown,
)
from collab import get_collab_recommendations, build_collab_map
from shopify import ShopifyClient
from db import log_recommendation, get_recent_logs
from contextlib import asynccontextmanager
import json
import logging
import os
import cache
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")
WORKER_URL = os.getenv("WORKER_URL", "")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # This service is tokenless — it can't fetch Shopify on its own at startup,
    # so there is no startup collab warm. The collab map is (re)built only when
    # Helm calls POST /build-map with the store's X-Shopify-Token. A cold cache
    # returns no collab recs until the first build; Helm falls back to its own
    # semantic search in the meantime.
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[WORKER_URL] if WORKER_URL else ["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "Authorization"],
)


def verify_internal_key(request: Request):
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    if not INTERNAL_API_KEY or token != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_shopify_token(request: Request) -> str:
    """The per-store Shopify access token Helm passes for this call.

    This service is tokenless: it holds no Shopify credentials and uses this
    token only for the duration of the request. Should be read-only scoped
    (products + orders). Never logged.
    """
    token = request.headers.get("X-Shopify-Token", "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing X-Shopify-Token")
    return token


def verify_key_param(key: str):
    if not INTERNAL_API_KEY or key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
async def health():
    return {"status": "ok",
            "cache_backend": cache.backend_name(),
            "cache_reachable": await cache.ping(),
            }


@app.get("/debug/cache")
async def debug_cache(shop_domain: str, request: Request):
    verify_internal_key(request)

    svd_data = await cache.get(f"svd:{shop_domain}")
    collab_data = await cache.get(f"collab:{shop_domain}")

    return {
        "svd_exists": svd_data is not None,
        "svd_product_count": len(svd_data["product_list"]) if svd_data else 0,
        "svd_sample_ids": svd_data["product_list"][:10] if svd_data else [],
        "collab_exists": collab_data is not None,
        "collab_product_count": len(collab_data) if collab_data else 0,
        "collab_sample_ids": list(collab_data.keys())[:10] if collab_data else [],
    }

@app.get("/debug/map")
async def debug_map(shop_domain: str, request: Request):
    verify_internal_key(request)

    svd_data = await cache.get(f"svd:{shop_domain}")
    collab_data = await cache.get(f"collab:{shop_domain}")

    result = {
        "shop_domain": shop_domain,
        "svd": None,
        "collab": None,
    }

    if svd_data:
        import numpy as np
        product_list = svd_data["product_list"]
        product_factors = np.array(svd_data["product_factors"])

        result["svd"] = {
            "product_count": len(product_list),
            "customer_count": len(svd_data["customer_list"]),
            "n_components": product_factors.shape[1] if len(product_factors.shape) > 1 else 0,
            "products": product_list,
            "customers": svd_data["customer_list"],
        }

    if collab_data:
        result["collab"] = {
            "product_count": len(collab_data),
            "co_occurrences": collab_data,
        }

    return result


@app.get("/logs")
async def view_logs(request: Request, shop_domain: str = None, limit: int = 20):
    verify_internal_key(request)
    logs = get_recent_logs(shop_domain=shop_domain, limit=limit)
    return {"logs": logs, "count": len(logs)}


@app.get("/logs/view", response_class=HTMLResponse)
async def logs_dashboard(key: str = Query(...)):
    verify_key_param(key)
    logs = get_recent_logs(limit=50)
    logs_json = json.dumps(logs)
    html = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Helm Logs</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui;background:#0f1117;color:#e2e4e9;padding:24px}
.card{background:#161820;border:1px solid #2a2d38;border-radius:8px;margin-bottom:8px;overflow:hidden}
.card:hover{border-color:#3a3f50}
.card.open{border-color:#6c72ff}
.summary{padding:12px 16px;cursor:pointer;display:flex;justify-content:space-between}
.shop{font-size:14px;font-weight:500}
.time{font-size:12px;color:#6b7080;font-family:monospace}
.counts{font-size:11px;color:#6b7080;margin-top:4px}
.counts b{color:#e2e4e9}
.detail{display:none;border-top:1px solid #2a2d38;padding:12px 16px}
.card.open .detail{display:block}
table{width:100%;border-collapse:collapse;margin:8px 0}
th{font-size:10px;color:#6b7080;text-align:left;padding:4px 8px;border-bottom:1px solid #2a2d38}
th.n{text-align:right}
td{font-size:13px;padding:5px 8px;border-bottom:1px solid #1e2130}
td.n{text-align:right;font-family:monospace;font-size:12px}
.sec{font-size:11px;color:#6b7080;margin:12px 0 4px;font-weight:500}
.rank{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.rk{font-size:12px;padding:3px 8px;background:#1e2130;border:1px solid #2a2d38;border-radius:4px}
.rk b{color:#6c72ff;margin-right:4px}
h2{font-size:16px;font-weight:600;margin-bottom:16px}
h2 span{color:#6c72ff}
</style></head><body>
<h2><span>helm</span> recommendation logs</h2>
<div id="c"></div>
<script>
var logs=__LOGS_DATA__;
function P(v){if(!v)return null;if(typeof v==='object')return v;try{return JSON.parse(v)}catch{return null}}
function F(v){if(typeof v!=='number')return'—';return v===0?'0':Math.abs(v)>=1?v.toFixed(2):v.toFixed(4)}
function T(iso){if(!iso)return'';var d=new Date(iso),p=function(n){return String(n).padStart(2,'0')};return['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()]+' '+d.getDate()+' '+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds())}
function R(cs,label){if(!cs||!cs.length)return'';var h='<div class="sec">'+label+' ('+cs.length+')</div><table><tr><th>Product</th><th>Source</th><th class="n">Score</th></tr>';cs.forEach(function(c){h+='<tr><td>'+c.id+'</td><td>'+(c.source||'?')+'</td><td class="n">'+F(c.score||0)+'</td></tr>'});return h+'</table>'}
var html='';logs.forEach(function(l,i){var cc=P(l.collab_candidates)||[];var fp=P(l.final_picks)||[];var ph='';if(fp.length){ph='<div class="sec">Final ranking</div><div class="rank">';fp.forEach(function(id,j){ph+='<span class="rk"><b>#'+(j+1)+'</b>'+id+'</span>'});ph+='</div>'}html+='<div class="card" id="l'+i+'"><div class="summary" onclick="document.getElementById(\'l'+i+'\').classList.toggle(\'open\')"><div><div class="shop">'+l.shop_domain+'</div><div class="counts">collab <b>'+l.collab_count+'</b> · final <b>'+l.final_count+'</b></div></div><div style="text-align:right"><div class="time">'+T(l.created_at)+'</div></div></div><div class="detail">'+R(cc,'Collab candidates')+ph+'</div></div>'});
document.getElementById('c').innerHTML=html||'<div style="text-align:center;padding:60px;color:#6b7080">No logs</div>';
</script></body></html>""".replace("__LOGS_DATA__", logs_json)
    return HTMLResponse(content=html)


async def _get_recommendations(body: RecommendRequest) -> list:
    """Collab-only ranking. Returns ranked [{id, score, source}].

    The recommender is a pure ranker now: it ranks from the cached collab map
    (SVD → co-occurrence) and returns ids + scores. Content matching (semantic
    search) and card building live in the host app (Helm), which resolves these
    ids against its own product catalog. No Shopify access here.
    """
    return await get_collab_recommendations(
        shop_domain=body.shop_domain,
        purchased_ids=body.purchased_product_ids,
        limit=body.limit or 4,
    )


def _log(body, merged):
    try:
        log_recommendation(
            shop_domain=body.shop_domain,
            purchased_ids=body.purchased_product_ids,
            collab_recs=merged,
            merged=merged,
        )
    except Exception:
        logger.warning("failed to log recommendation for %s", body.shop_domain, exc_info=True)


@app.post("/recommend")
async def recommend(body: RecommendRequest, request: Request):
    verify_internal_key(request)
    merged = await _get_recommendations(body)
    _log(body, merged)
    return RecommendResponse(recommendations=merged)


@app.post("/recommend/debug")
async def recommend_debug(body: RecommendRequest, request: Request):
    verify_internal_key(request)
    merged = await _get_recommendations(body)

    debug = DebugInfo(
        source="collab",
        collab_candidates=[ScoringBreakdown(id=r["id"], source=r.get("source", "collab"), score=r["score"]) for r in merged],
        final_picks=[r["id"] for r in merged],
    )

    _log(body, merged)
    return DebugRecommendResponse(recommendations=merged, debug=debug)


@app.post("/build-map")
async def build_map(shop_domain: str, request: Request):
    verify_internal_key(request)
    access_token = require_shopify_token(request)
    shopify = ShopifyClient(domain=shop_domain, access_token=access_token)
    result = await build_collab_map(shop_domain=shop_domain, shopify=shopify)
    return {"success": True, **result}
