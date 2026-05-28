"""
main.py — FastAPI recommendation service

Endpoints:
  POST /recommend        — returns ranked product recommendations
  POST /recommend/debug  — same + full scoring breakdown
  POST /build-map        — rebuilds the collab map
  GET  /health           — health check
  GET  /debug/cache      — inspect cached collab/SVD data
  GET  /logs             — view recent recommendation logs (JSON)
  GET  /logs/view        — visual dashboard for recommendation logs

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
from tags import get_tag_recommendations, detect_product_type
from browse import score_browse_intent
from shopify import ShopifyClient
from db import log_recommendation, get_recent_logs
import asyncio
import json
import os
import cache
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
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    if not INTERNAL_API_KEY or token != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def verify_key_param(key: str):
    if not INTERNAL_API_KEY or key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
async def health():
    return {"status": "ok"}


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
.query{font-size:14px;font-weight:500}
.query.none{color:#6b7080;font-style:italic}
.pills{display:flex;gap:6px;margin-top:4px}
.pill{font-size:10px;padding:2px 8px;border-radius:4px;font-weight:500}
.pill-t{background:#1a2940;color:#5b9cf5}
.pill-c{background:#251e40;color:#9b8cff}
.pill-q{background:#2a2210;color:#d4a944}
.pill-b{background:#0f2a22;color:#4cc99a}
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
.boost{font-size:12px;padding:3px 8px;background:#0f2a22;color:#4cc99a;border-radius:4px;font-family:monospace;display:inline-block;margin:2px}
h2{font-size:16px;font-weight:600;margin-bottom:16px}
h2 span{color:#6c72ff}
</style></head><body>
<h2><span>helm</span> recommendation logs</h2>
<div id="c"></div>
<script>
var logs=__LOGS_DATA__;
function P(v){if(!v)return null;if(typeof v==='object')return v;try{return JSON.parse(v)}catch{return null}}
function F(v){if(typeof v!=='number')return'\u2014';return v===0?'0':Math.abs(v)>=1?v.toFixed(2):v.toFixed(4)}
function T(iso){if(!iso)return'';var d=new Date(iso),p=function(n){return String(n).padStart(2,'0')};return['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()]+' '+d.getDate()+' '+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds())}
function R(cs,label){if(!cs||!cs.length)return'';var h='<div class="sec">'+label+' ('+cs.length+')</div><table><tr><th>Product</th><th>Source</th><th class="n">Raw</th><th class="n">Boost</th><th class="n">Final</th></tr>';cs.forEach(function(c){var r=c.raw_score!==undefined?c.raw_score:(c.score||0);var b=c.browse_boost||0;var f=c.final_score!==undefined?c.final_score:r;h+='<tr><td>'+c.title+'</td><td>'+(c.source||'?')+'</td><td class="n">'+F(r)+'</td><td class="n">'+(b>0?'+'+F(b):'\u2014')+'</td><td class="n" style="color:#e2e4e9;font-weight:500">'+F(f)+'</td></tr>'});return h+'</table>'}
var html='';logs.forEach(function(l,i){var d=P(l.full_debug);var cc=d?(d.collab_candidates||[]):(P(l.collab_candidates)||[]);var tc=d?(d.tag_candidates||[]):(P(l.tag_candidates)||[]);var bm=d?(d.browse_boost_map||{}):(P(l.browse_boost_map)||{});var fp=d?(d.final_picks||[]):(P(l.final_picks)||[]);var be=Object.entries(bm);var all=[].concat(tc,cc);var pills='<span class="pill '+(l.merge_order==='tags_first'?'pill-t':'pill-c')+'">'+(l.merge_order||'?')+'</span>';if(l.query_type)pills+='<span class="pill pill-q">'+l.query_type+'</span>';if(be.length)pills+='<span class="pill pill-b">browse</span>';var bh='';if(be.length){bh='<div class="sec">Browse boosts</div>';be.forEach(function(e){bh+='<span class="boost">'+e[0]+': +'+F(e[1])+'</span> '})}var ph='';if(fp.length){ph='<div class="sec">Final ranking</div><div class="rank">';fp.forEach(function(id,j){var f=all.find(function(c){return c.id===id});ph+='<span class="rk"><b>#'+(j+1)+'</b>'+(f?f.title:id)+'</span>'});ph+='</div>'}html+='<div class="card" id="l'+i+'"><div class="summary" onclick="document.getElementById(\'l'+i+'\').classList.toggle(\'open\')"><div><div class="query '+(l.query?'':'none')+'">'+(l.query||'No query')+'</div><div class="pills">'+pills+'</div></div><div style="text-align:right"><div class="time">'+T(l.created_at)+'</div><div class="counts">collab <b>'+l.collab_count+'</b> · tags <b>'+l.tag_count+'</b> · final <b>'+l.final_count+'</b></div></div></div><div class="detail">'+R(cc,'Collab candidates')+R(tc,'Tag candidates')+bh+ph+'</div></div>'});
document.getElementById('c').innerHTML=html||'<div style="text-align:center;padding:60px;color:#6b7080">No logs</div>';
</script></body></html>""".replace("__LOGS_DATA__", logs_json)
    return HTMLResponse(content=html)


async def _get_recommendations(body: RecommendRequest):
    """
    Core recommendation logic. Returns (merged, collab_recs, tag_recs, browse_boost, query_type).
    Used by both /recommend and /recommend/debug.
    """
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
        for rec in collab_recs:
            if not _matches_type(rec, query_type):
                rec["score"] = rec.get("score", 0) * 0.3

    browse_boost = score_browse_intent(body.browse_history)
    merged = merge_recommendations(
        collab_recs, tag_recs, browse_boost,
        query_type=query_type,
        limit=body.limit or 4,
    )

    return merged, collab_recs, tag_recs, browse_boost, query_type


def _log(body, merged, collab_recs, tag_recs, browse_boost, query_type, debug_info=None):
    try:
        log_recommendation(
            shop_domain=body.shop_domain,
            query=body.query,
            query_type=query_type,
            merge_order="tags_first" if query_type else "collab_first",
            purchased_ids=body.purchased_product_ids,
            collab_recs=collab_recs,
            tag_recs=tag_recs,
            browse_boost=browse_boost,
            merged=merged,
            debug_info=debug_info,
        )
    except Exception:
        pass


@app.post("/recommend")
async def recommend(body: RecommendRequest, request: Request):
    verify_internal_key(request)
    merged, collab_recs, tag_recs, browse_boost, query_type = await _get_recommendations(body)

    _log(body, merged, collab_recs, tag_recs, browse_boost, query_type)

    return RecommendResponse(recommendations=merged)


@app.post("/recommend/debug")
async def recommend_debug(body: RecommendRequest, request: Request):
    verify_internal_key(request)
    merged, collab_recs, tag_recs, browse_boost, query_type = await _get_recommendations(body)

    merge_order = "tags_first" if query_type else "collab_first"

    collab_breakdowns = [
        ScoringBreakdown(
            id=r["id"],
            title=r["title"],
            source=r.get("source", "collab"),
            raw_score=r.get("score", 0),
            browse_boost=browse_boost.get(r["title"], 0),
            final_score=r.get("score", 0) + browse_boost.get(r["title"], 0) * 0.5,
        )
        for r in collab_recs
    ]

    tag_breakdowns = [
        ScoringBreakdown(
            id=r["id"],
            title=r["title"],
            source="tags",
            raw_score=r.get("score", 0),
            browse_boost=browse_boost.get(r["title"], 0),
            final_score=r.get("score", 0) + browse_boost.get(r["title"], 0) * 0.5,
        )
        for r in tag_recs
    ]

    debug_dict = {
        "query_type_detected": query_type,
        "merge_order": merge_order,
        "collab_candidates": [b.model_dump() for b in collab_breakdowns],
        "tag_candidates": [b.model_dump() for b in tag_breakdowns],
        "browse_boost_map": browse_boost,
        "final_picks": [r["id"] for r in merged],
    }

    debug = DebugInfo(
        query_type_detected=query_type,
        merge_order=merge_order,
        collab_candidates=collab_breakdowns,
        tag_candidates=tag_breakdowns,
        browse_boost_map=browse_boost,
        final_picks=[r["id"] for r in merged],
    )

    _log(body, merged, collab_recs, tag_recs, browse_boost, query_type, debug_info=debug_dict)

    return DebugRecommendResponse(recommendations=merged, debug=debug)


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