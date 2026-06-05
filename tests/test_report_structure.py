"""
Deep report structure tests — verify every field the UI depends on is present.
These hit live APIs (Gemini + Elastic). Mark: @pytest.mark.slow

Covers:
  - elastic_provenance (retrieval strategy, ES|QL queries, MCP active)
  - debate result (final_score, optimist, pessimist, buy_recommendation, confidence)
  - timeline structure (date, event_type, description, source, flags)
  - neighborhood data (pm25, superfund, schools, parks, transit)
  - share link URL format (/app.html?share=)
  - similar properties response after analysis
"""
import pytest

pytestmark = pytest.mark.slow

NYC_ADDRESS = "363 Van Brunt St, Brooklyn, NY"
HOUSTON_ADDRESS = "2121 Airline Dr, Houston, TX"


@pytest.fixture(scope="module")
def nyc_report(client):
    """Run analysis once and share the result across all tests in this module."""
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    assert r.status_code == 200, f"Analysis failed: {r.text}"
    return r.json()


# ── Core report fields ────────────────────────────────────────────────────────

def test_report_has_address_hash(nyc_report):
    assert "address_hash" in nyc_report
    assert len(nyc_report["address_hash"]) >= 8


def test_report_has_normalized_address(nyc_report):
    assert "normalized_address" in nyc_report
    assert len(nyc_report["normalized_address"]) > 5


def test_report_has_coordinates(nyc_report):
    assert "lat" in nyc_report
    assert "lng" in nyc_report
    assert isinstance(nyc_report["lat"], (int, float))
    assert isinstance(nyc_report["lng"], (int, float))


def test_report_risk_score_in_range(nyc_report):
    score = nyc_report.get("buyer_risk_score")
    assert score is not None
    assert 0 <= score <= 100


def test_report_risk_level_valid(nyc_report):
    level = nyc_report.get("risk_level")
    assert level in ("LOW", "MEDIUM", "HIGH", "CRITICAL"), f"Invalid level: {level}"


def test_report_risk_level_matches_score(nyc_report):
    """score and risk_level must be consistent (scoreToLevel logic)."""
    score = nyc_report["buyer_risk_score"]
    level = nyc_report["risk_level"]
    if score <= 30:
        assert level == "LOW"
    elif score <= 60:
        assert level == "MEDIUM"
    elif score <= 80:
        assert level == "HIGH"
    else:
        assert level == "CRITICAL"


def test_report_has_summary(nyc_report):
    assert "summary" in nyc_report
    assert len(nyc_report["summary"]) > 20


def test_report_has_flags_list(nyc_report):
    assert "flags" in nyc_report
    assert isinstance(nyc_report["flags"], list)


def test_report_has_escape_plan(nyc_report):
    assert "escape_plan" in nyc_report
    assert isinstance(nyc_report["escape_plan"], list)
    assert len(nyc_report["escape_plan"]) >= 1


def test_report_has_diligence_questions(nyc_report):
    assert "diligence_questions" in nyc_report
    assert isinstance(nyc_report["diligence_questions"], list)
    assert len(nyc_report["diligence_questions"]) >= 1


def test_report_has_positive_signals(nyc_report):
    assert "positive_signals" in nyc_report
    assert isinstance(nyc_report["positive_signals"], list)


def test_report_has_data_sources(nyc_report):
    assert "data_sources" in nyc_report
    assert len(nyc_report["data_sources"]) >= 1


# ── Debate result structure ───────────────────────────────────────────────────

def test_report_has_debate(nyc_report):
    assert "debate" in nyc_report, "Debate result missing from report"


def test_debate_has_final_score(nyc_report):
    debate = nyc_report.get("debate", {})
    assert "final_score" in debate
    assert 0 <= debate["final_score"] <= 100


def test_debate_has_buy_recommendation(nyc_report):
    debate = nyc_report.get("debate", {})
    assert "buy_recommendation" in debate
    assert debate["buy_recommendation"] in ("BUY", "NEGOTIATE", "AVOID")


def test_debate_has_confidence(nyc_report):
    debate = nyc_report.get("debate", {})
    assert "confidence" in debate
    assert debate["confidence"] in ("LOW", "MEDIUM", "HIGH")


def test_debate_has_optimist(nyc_report):
    debate = nyc_report.get("debate", {})
    assert "optimist" in debate
    opt = debate["optimist"]
    assert "argument" in opt or "adjusted_score" in opt


def test_debate_has_pessimist(nyc_report):
    debate = nyc_report.get("debate", {})
    assert "pessimist" in debate
    pes = debate["pessimist"]
    assert "argument" in pes or "adjusted_score" in pes


def test_debate_has_verdict_text(nyc_report):
    debate = nyc_report.get("debate", {})
    assert "verdict_text" in debate
    assert len(debate["verdict_text"]) > 10


def test_debate_final_score_between_agents(nyc_report):
    """Final score should be between or near the two agent scores."""
    debate = nyc_report.get("debate", {})
    final = debate.get("final_score")
    opt = (debate.get("optimist") or {}).get("adjusted_score")
    pes = (debate.get("pessimist") or {}).get("adjusted_score")
    if final is None or opt is None or pes is None:
        pytest.skip("Missing debate scores")
    low, high = min(opt, pes), max(opt, pes)
    # Allow some tolerance — confidence adjustment can push outside range
    assert (low - 10) <= final <= (high + 10), (
        f"Final score {final} far outside agent range [{low},{high}]"
    )


# ── Elastic provenance structure ──────────────────────────────────────────────

def test_report_has_elastic_provenance(nyc_report):
    assert "elastic_provenance" in nyc_report, "elastic_provenance missing from report"


def test_provenance_has_retrieval_strategy(nyc_report):
    prov = nyc_report.get("elastic_provenance", {})
    assert "retrieval_strategy" in prov
    assert prov["retrieval_strategy"] in (
        "elastic_mcp_hybrid", "rrf_bm25_elser",
        "semantic_reranker", "bm25", "unavailable",
    )


def test_provenance_has_events_retrieved(nyc_report):
    prov = nyc_report.get("elastic_provenance", {})
    assert "events_retrieved" in prov
    assert isinstance(prov["events_retrieved"], int)


def test_provenance_has_esql_queries(nyc_report):
    prov = nyc_report.get("elastic_provenance", {})
    assert "esql_queries" in prov
    assert isinstance(prov["esql_queries"], list)
    assert len(prov["esql_queries"]) >= 3


def test_provenance_esql_queries_have_name_and_rowcount(nyc_report):
    prov = nyc_report.get("elastic_provenance", {})
    for q in prov.get("esql_queries", []):
        assert "name" in q, f"Missing name in ES|QL query: {q}"
        assert "row_count" in q, f"Missing row_count in ES|QL query: {q}"
        assert "query" in q, f"Missing query text: {q}"


def test_provenance_has_mcp_active_flag(nyc_report):
    prov = nyc_report.get("elastic_provenance", {})
    assert "mcp_active" in prov
    assert isinstance(prov["mcp_active"], bool)


def test_provenance_has_geo_nearby_count(nyc_report):
    prov = nyc_report.get("elastic_provenance", {})
    assert "geo_nearby_count" in prov
    assert isinstance(prov["geo_nearby_count"], int)


# ── Timeline structure ────────────────────────────────────────────────────────

def test_report_has_timeline(nyc_report):
    assert "timeline" in nyc_report
    assert isinstance(nyc_report["timeline"], list)


def test_timeline_items_have_required_fields(nyc_report):
    for item in nyc_report.get("timeline", []):
        assert "date" in item, f"Timeline item missing date: {item}"
        assert "event_type" in item, f"Timeline item missing event_type: {item}"
        assert "description" in item, f"Timeline item missing description: {item}"
        assert "source" in item, f"Timeline item missing source: {item}"


def test_timeline_dates_are_valid_format(nyc_report):
    import re
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for item in nyc_report.get("timeline", []):
        assert date_pattern.match(item["date"]), (
            f"Invalid date format: {item['date']}"
        )


def test_timeline_event_types_are_known(nyc_report):
    known_types = {
        "sale", "permit", "climate_flood", "climate_earthquake",
        "neighborhood_env", "neighborhood_amenities", "unknown",
    }
    for item in nyc_report.get("timeline", []):
        assert item["event_type"] in known_types, (
            f"Unknown event_type: {item['event_type']}"
        )


def test_timeline_flags_are_lists(nyc_report):
    for item in nyc_report.get("timeline", []):
        assert isinstance(item.get("flags", []), list)


# ── Neighbourhood data ────────────────────────────────────────────────────────

def test_report_has_neighborhood(nyc_report):
    assert "neighborhood" in nyc_report


def test_neighborhood_has_score(nyc_report):
    nbhd = nyc_report.get("neighborhood", {})
    assert "neighborhood_score" in nbhd
    score = nbhd["neighborhood_score"]
    assert 0 <= score <= 100


def test_neighborhood_has_amenity_counts(nyc_report):
    nbhd = nyc_report.get("neighborhood", {})
    assert "schools_nearby" in nbhd
    assert "parks_nearby" in nbhd
    assert "transit_nearby" in nbhd


def test_neighborhood_amenity_counts_non_negative(nyc_report):
    nbhd = nyc_report.get("neighborhood", {})
    assert nbhd.get("schools_nearby", 0) >= 0
    assert nbhd.get("parks_nearby", 0) >= 0
    assert nbhd.get("transit_nearby", 0) >= 0


def test_neighborhood_has_env_fields(nyc_report):
    nbhd = nyc_report.get("neighborhood", {})
    assert "pm25" in nbhd
    assert "superfund_proximity" in nbhd
    assert "traffic_proximity" in nbhd


# ── Share link URL format ─────────────────────────────────────────────────────

def test_share_link_url_format(client):
    """Share URL must point to /app.html?share= not /share/ (raw API path)."""
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    addr_hash = r.json().get("address_hash")
    if not addr_hash:
        pytest.skip("No address_hash")

    r2 = client.post(f"/api/share/{addr_hash}")
    assert r2.status_code == 200
    share = r2.json()
    url = share.get("url") or share.get("share_url") or ""
    assert "/app.html?share=" in url, (
        f"Share URL should contain /app.html?share=, got: {url}"
    )


def test_share_link_is_retrievable(client):
    """Share ID must be retrievable via GET /api/share/{id}."""
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    addr_hash = r.json().get("address_hash")
    if not addr_hash:
        pytest.skip("No address_hash")

    r2 = client.post(f"/api/share/{addr_hash}")
    url = r2.json().get("url", "")
    share_id = url.split("?share=")[-1] if "?share=" in url else None
    if not share_id:
        pytest.skip("Could not extract share_id from URL")

    r3 = client.get(f"/api/share/{share_id}")
    assert r3.status_code == 200
    report = r3.json()
    assert "buyer_risk_score" in report


# ── Similar properties after analysis ────────────────────────────────────────

def test_similar_properties_after_analysis(client):
    """After analysis, /api/similar/{hash} should return structured response."""
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    addr_hash = r.json().get("address_hash")
    if not addr_hash:
        pytest.skip("No address_hash")

    r2 = client.get(f"/api/similar/{addr_hash}")
    assert r2.status_code == 200
    data = r2.json()
    assert "count" in data
    assert "properties" in data
    assert isinstance(data["properties"], list)
    assert "method" in data


def test_similar_properties_method_is_known(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    addr_hash = r.json().get("address_hash")
    if not addr_hash:
        pytest.skip("No address_hash")

    data = client.get(f"/api/similar/{addr_hash}").json()
    method = data.get("method", "")
    assert method in ("geo_distance", "risk_level_esql", "unavailable", "not_found"), (
        f"Unknown similarity method: {method}"
    )


# ── Houston: verify higher-risk property ─────────────────────────────────────

def test_houston_has_permit_or_climate_data(client):
    """Houston refinery zone should have climate/environmental data."""
    r = client.post("/api/analyze", json={"address": HOUSTON_ADDRESS}, timeout=180.0)
    assert r.status_code == 200
    data = r.json()
    # Should have flood zone or env flags given location
    assert (
        "flood_zone" in data
        or "climate" in str(data).lower()
        or "refinery" in str(data).lower()
        or any("flood" in f.lower() or "permit" in f.lower() for f in data.get("flags", []))
    )


def test_houston_neighborhood_data_present(client):
    r = client.post("/api/analyze", json={"address": HOUSTON_ADDRESS}, timeout=180.0)
    data = r.json()
    nbhd = data.get("neighborhood", {})
    assert "pm25" in nbhd
    assert "neighborhood_score" in nbhd
