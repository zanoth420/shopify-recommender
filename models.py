"""
models.py — Pydantic models for the recommendation service
"""

from pydantic import BaseModel, Field
from typing import Optional


class BrowseEvent(BaseModel):
    event: str
    data: dict
    timestamp: str


class RecommendRequest(BaseModel):
    shop_domain: str
    purchased_product_ids: list[int] = Field(default_factory=list)
    top_product_types: list[str] = Field(default_factory=list)
    top_tags: list[str] = Field(default_factory=list)
    browse_history: list[BrowseEvent] = Field(default_factory=list)
    limit: Optional[int] = 4
    query: Optional[str] = None


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
# Collab-only: tag/browse scoring + display fields now live in the host app (Helm).

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