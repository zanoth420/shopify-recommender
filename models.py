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


class Product(BaseModel):
    id: int
    title: str
    url: str
    image: Optional[str] = None
    price: Optional[str] = None
    source: str
    score: float = 0


class RecommendResponse(BaseModel):
    recommendations: list[Product]


# ─── Debug models ────────────────────────────────────────

class ScoringBreakdown(BaseModel):
    id: int
    title: str
    source: str
    raw_score: float = 0.0
    browse_boost: float = 0.0
    final_score: float = 0.0


class DebugInfo(BaseModel):
    query_type_detected: Optional[str] = None
    merge_order: str
    collab_candidates: list[ScoringBreakdown] = Field(default_factory=list)
    tag_candidates: list[ScoringBreakdown] = Field(default_factory=list)
    browse_boost_map: dict[str, float] = Field(default_factory=dict)
    final_picks: list[int] = Field(default_factory=list)


class DebugRecommendResponse(BaseModel):
    recommendations: list[Product]
    debug: DebugInfo