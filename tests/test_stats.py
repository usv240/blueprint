"""Tests for GET /api/stats and GET /api/recent."""
import pytest


# ── /api/stats ────────────────────────────────────────────────────────────────

def test_stats_status_ok(client):
    r = client.get("/api/stats")
    assert r.status_code == 200


def test_stats_fields_present(client):
    data = client.get("/api/stats").json()
    for field in ("total_analyses", "analyses_24h", "elastic_connected", "elastic_mcp_active"):
        assert field in data, f"Missing field: {field}"


def test_stats_total_is_non_negative(client):
    data = client.get("/api/stats").json()
    assert data["total_analyses"] >= 0


def test_stats_24h_is_non_negative(client):
    data = client.get("/api/stats").json()
    assert data["analyses_24h"] >= 0


def test_stats_elastic_connected_is_bool(client):
    data = client.get("/api/stats").json()
    assert isinstance(data["elastic_connected"], bool)


# ── /api/recent ───────────────────────────────────────────────────────────────

def test_recent_status_ok(client):
    r = client.get("/api/recent")
    assert r.status_code == 200


def test_recent_returns_list(client):
    data = client.get("/api/recent").json()
    assert isinstance(data, list)


def test_recent_items_have_required_fields(client):
    data = client.get("/api/recent").json()
    for item in data[:5]:
        assert "address" in item or "normalized_address" in item
