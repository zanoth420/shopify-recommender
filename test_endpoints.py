"""Endpoint-level tests for the token guard.

The recommender is tokenless: protected endpoints require both the internal
auth bearer (Helm↔recommender shared secret) AND the per-store X-Shopify-Token
header. These tests assert the guard, without hitting Shopify.
"""

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


def test_recommend_rejects_missing_shopify_token(client):
    test_client, _ = client
    res = test_client.post(
        "/recommend",
        json={"shop_domain": "demo.myshopify.com"},
        headers=_auth_headers(),  # no X-Shopify-Token
    )
    assert res.status_code == 400
    assert "X-Shopify-Token" in res.json()["detail"]


def test_recommend_rejects_bad_internal_key(client):
    test_client, _ = client
    res = test_client.post(
        "/recommend",
        json={"shop_domain": "demo.myshopify.com"},
        headers={"Authorization": "Bearer wrong", "X-Shopify-Token": "shpat_x"},
    )
    assert res.status_code == 401


def test_build_map_rejects_missing_shopify_token(client):
    test_client, _ = client
    res = test_client.post(
        "/build-map?shop_domain=demo.myshopify.com",
        headers=_auth_headers(),
    )
    assert res.status_code == 400


def test_health_needs_no_auth(client):
    test_client, _ = client
    res = test_client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
