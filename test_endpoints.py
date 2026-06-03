"""Endpoint-level tests.

The recommender is a pure collab ranker now: ``/recommend`` ranks from the
cached collab map and returns ids + scores only — no Shopify access, no
X-Shopify-Token. Only ``/build-map`` (which pulls orders to build the matrix)
still requires the token. These tests assert that contract without hitting Shopify.
"""

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    # Set the shared secret before (re)importing main so the auth check passes.
    monkeypatch.setenv("INTERNAL_API_KEY", "test-internal-key")
    import main

    importlib.reload(main)
    return TestClient(main.app), main


def _auth_headers(token: str | None = None) -> dict:
    headers = {"Authorization": "Bearer test-internal-key"}
    if token is not None:
        headers["X-Shopify-Token"] = token
    return headers


def test_recommend_works_without_shopify_token(client):
    test_client, main = client
    # Seed a co-occurrence map so collab has something to rank — proves the path
    # needs no Shopify call. Product 1 was purchased; 2 and 3 co-occur with it.
    import cache

    asyncio.run(cache.set("collab:demo.myshopify.com", {"1": {"2": 5, "3": 2}}, ttl_seconds=60))

    res = test_client.post(
        "/recommend",
        json={"shop_domain": "demo.myshopify.com", "purchased_product_ids": [1], "limit": 4},
        headers=_auth_headers(),  # NO X-Shopify-Token — must still succeed
    )

    assert res.status_code == 200
    recs = res.json()["recommendations"]
    # Ranked ids + scores only, no display fields, score-desc (2 outranks 3).
    assert [r["id"] for r in recs] == [2, 3]
    assert all(set(r.keys()) == {"id", "score", "source"} for r in recs)
    assert recs[0]["score"] >= recs[1]["score"]


def test_recommend_cold_cache_returns_empty(client):
    test_client, _ = client
    res = test_client.post(
        "/recommend",
        json={"shop_domain": "no-map.myshopify.com", "purchased_product_ids": [1]},
        headers=_auth_headers(),
    )
    assert res.status_code == 200
    assert res.json()["recommendations"] == []


def test_recommend_rejects_bad_internal_key(client):
    test_client, _ = client
    res = test_client.post(
        "/recommend",
        json={"shop_domain": "demo.myshopify.com"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert res.status_code == 401


def test_build_map_still_requires_shopify_token(client):
    test_client, _ = client
    res = test_client.post(
        "/build-map?shop_domain=demo.myshopify.com",
        headers=_auth_headers(),  # no X-Shopify-Token
    )
    assert res.status_code == 400
    assert "X-Shopify-Token" in res.json()["detail"]


def test_health_needs_no_auth(client):
    test_client, _ = client
    res = test_client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
