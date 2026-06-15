"""
models.py — Pydantic models for the recommendation service

The recommender is a pure collaborative-filtering ranker: it takes a customer's
purchase history and returns ranked product ids + scores. Content matching
(semantic search) and card building live in the host app (Helm), so there are
no tag/browse/query inputs and no display fields here.
"""

from pydantic import BaseModel, Field
from typing import Optional


class RecommendRequest(BaseModel):
    shop_domain: str
    purchased_product_ids: list[int] = Field(default_factory=list)
    limit: Optional[int] = 4


class RankedProduct(BaseModel):
    """A ranked product id + score from collaborative filtering.

    The recommender is a pure ranker: it returns ids and scores only. The host
    app (Helm) resolves these ids against its own product catalog to build the
    customer-facing cards, so no display fields (title/url/image/price) and no
    Shopify access are needed here.
    """

    id: int
    score: float = 0
    source: str = "collab"


class RecommendResponse(BaseModel):
    recommendations: list[RankedProduct]


# ─── Debug models ────────────────────────────────────────
# Collab-only: the recommender ranks from its cached SVD/co-occurrence map and
# returns ids + scores. /recommend/debug echoes that ranking back for inspection.

class ScoringBreakdown(BaseModel):
    id: int
    source: str = "collab"
    score: float = 0.0


class DebugInfo(BaseModel):
    source: str = "collab"
    collab_candidates: list[ScoringBreakdown] = Field(default_factory=list)
    final_picks: list[int] = Field(default_factory=list)


class DebugRecommendResponse(BaseModel):
    recommendations: list[RankedProduct]
    debug: DebugInfo
