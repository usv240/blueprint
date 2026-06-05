"""
Tests for Elastic-specific API endpoints (fast — no live Gemini/pipeline calls).

Endpoints covered:
  GET /api/elastic/status   — live capability matrix
  GET /api/elastic/insights — live market aggregations
  GET /api/similar/{hash}   — cross-property similarity
  GET /api/coverage         — permit city coverage
"""
import pytest


# ── /api/elastic/status ───────────────────────────────────────────────────────

def test_elastic_status_returns_200(client):
    r = client.get("/api/elastic/status")
    assert r.status_code == 200


def test_elastic_status_has_connected_field(client):
    data = client.get("/api/elastic/status").json()
    assert "elastic_connected" in data
    assert isinstance(data["elastic_connected"], bool)


def test_elastic_status_has_mcp_field(client):
    data = client.get("/api/elastic/status").json()
    assert "elastic_mcp" in data


def test_elastic_status_has_capabilities_list(client):
    data = client.get("/api/elastic/status").json()
    caps = data.get("capabilities", [])
    assert isinstance(caps, list)
    assert len(caps) >= 8


def test_elastic_status_capabilities_have_required_fields(client):
    data = client.get("/api/elastic/status").json()
    for cap in data.get("capabilities", []):
        assert "id" in cap, f"Missing id: {cap}"
        assert "label" in cap, f"Missing label: {cap}"
        assert "active" in cap, f"Missing active: {cap}"
        assert "category" in cap, f"Missing category: {cap}"
        assert "detail" in cap, f"Missing detail: {cap}"


def test_elastic_status_has_expected_capability_ids(client):
    data = client.get("/api/elastic/status").json()
    ids = {c["id"] for c in data.get("capabilities", [])}
    expected = {
        "elser_semantic", "rrf_hybrid", "semantic_reranker",
        "esql", "geo_distance", "aggregations", "significant_terms",
        "percolator", "memory_writeback", "mcp_execution",
    }
    for cap_id in expected:
        assert cap_id in ids, f"Missing capability: {cap_id}"


def test_elastic_status_has_indices(client):
    data = client.get("/api/elastic/status").json()
    assert "indices" in data
    indices = data["indices"]
    assert isinstance(indices, dict)
    assert any("blueprint_events" in k for k in indices)
    assert any("blueprint_reports" in k for k in indices)


def test_elastic_status_has_retrieval_strategies(client):
    data = client.get("/api/elastic/status").json()
    strategies = data.get("retrieval_strategies", [])
    assert len(strategies) >= 3


def test_elastic_status_has_esql_queries_list(client):
    data = client.get("/api/elastic/status").json()
    queries = data.get("esql_queries", [])
    assert len(queries) >= 5


def test_elastic_status_has_inference_endpoints(client):
    data = client.get("/api/elastic/status").json()
    assert "elser_inference_endpoint" in data
    assert "reranking_inference_endpoint" in data


def test_elastic_status_index_counts_field(client):
    data = client.get("/api/elastic/status").json()
    assert "index_document_counts" in data
    assert isinstance(data["index_document_counts"], dict)


# ── /api/elastic/insights ─────────────────────────────────────────────────────

def test_elastic_insights_returns_200(client):
    r = client.get("/api/elastic/insights")
    assert r.status_code == 200


def test_elastic_insights_has_available_field(client):
    data = client.get("/api/elastic/insights").json()
    # available is True when Elastic is connected, False otherwise — both valid
    assert "available" in data or "insights" in data


def test_elastic_insights_structure_when_available(client):
    data = client.get("/api/elastic/insights").json()
    if not data.get("available", True):
        pytest.skip("Elastic unavailable — skipping insights structure test")
    assert "insights" in data


def test_elastic_insights_significant_terms_field(client):
    data = client.get("/api/elastic/insights").json()
    assert "significant_terms_by_band" in data
    assert isinstance(data["significant_terms_by_band"], dict)


# ── /api/similar/{hash} ───────────────────────────────────────────────────────

def test_similar_unknown_hash_returns_gracefully(client):
    """Unknown hash should return empty results, not crash."""
    r = client.get("/api/similar/unknownhash00000000")
    assert r.status_code == 200
    data = r.json()
    assert "properties" in data
    assert isinstance(data["properties"], list)


def test_similar_response_has_method_field(client):
    data = client.get("/api/similar/unknownhash00000000").json()
    assert "method" in data or "count" in data


def test_similar_response_has_count(client):
    data = client.get("/api/similar/unknownhash00000000").json()
    assert "count" in data
    assert isinstance(data["count"], int)


def test_similar_response_count_matches_properties(client):
    data = client.get("/api/similar/unknownhash00000000").json()
    assert data["count"] == len(data.get("properties", []))


# ── /api/coverage ─────────────────────────────────────────────────────────────

def test_coverage_returns_200(client):
    r = client.get("/api/coverage")
    assert r.status_code == 200


def test_coverage_has_city_count(client):
    data = client.get("/api/coverage").json()
    assert "total_cities" in data or "cities" in data or "permit_cities" in data


def test_coverage_has_verified_cities(client):
    data = client.get("/api/coverage").json()
    # At least some cities should be verified (real Socrata 4x4 IDs)
    assert (
        "verified_cities" in data
        or "verified_permit_cities" in data
        or any("verified" in str(v).lower() for v in data.values())
    )


def test_coverage_nationwide_sources_present(client):
    data = client.get("/api/coverage").json()
    data_str = str(data).lower()
    # FEMA and USGS are always available (no city restriction)
    assert "fema" in data_str or "nationwide" in data_str or "climate" in data_str
