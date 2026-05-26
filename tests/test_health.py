"""Tests for GET /api/health"""
import pytest


def test_health_status_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"


def test_health_service_name(client):
    data = client.get("/api/health").json()
    assert data["service"] == "blueprint"


def test_health_agent_count(client):
    data = client.get("/api/health").json()
    assert data["agents"] == 7


def test_health_gemini_model(client):
    data = client.get("/api/health").json()
    assert "gemini" in data["gemini_model"].lower()


def test_health_fallback_model(client):
    data = client.get("/api/health").json()
    assert "gemini" in data["fallback_model"].lower()
    assert "vertex" in data["fallback_model"].lower() or "2.5" in data["fallback_model"].lower()


def test_health_elasticsearch_field_present(client):
    data = client.get("/api/health").json()
    assert "elasticsearch" in data
    assert data["elasticsearch"] in ("connected", "unavailable")


def test_health_elastic_mcp_field_present(client):
    data = client.get("/api/health").json()
    assert "elastic_mcp" in data


def test_health_slack_field_present(client):
    data = client.get("/api/health").json()
    assert "slack_alerts" in data
    assert data["slack_alerts"] in ("configured", "not configured")


def test_health_features_list(client):
    data = client.get("/api/health").json()
    features = data.get("features", [])
    expected = {
        "share_links", "watchlist_24h", "html_export",
        "slack_alerts", "qa_chat", "debate_analysis",
        "escape_plan", "neighborhood_intelligence", "property_comparison",
    }
    for feat in expected:
        assert feat in features, f"Missing feature: {feat}"


def test_health_elastic_capabilities(client):
    data = client.get("/api/health").json()
    caps = data.get("elastic_capabilities", [])
    for cap in ("hybrid_retrieval", "memory_layer_writeback", "esql_cross_reference"):
        assert cap in caps, f"Missing capability: {cap}"


def test_health_data_tiers(client):
    data = client.get("/api/health").json()
    tiers = data.get("data_tiers", [])
    assert any("nyc" in t for t in tiers)
    assert any("austin" in t for t in tiers)
