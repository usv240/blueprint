"""Tests for GET /api/about — educational content endpoint."""
import pytest


@pytest.fixture(scope="module")
def about(client):
    r = client.get("/api/about")
    assert r.status_code == 200
    return r.json()


# ── Score bands ───────────────────────────────────────────────────────────────

def test_about_score_bands_count(about):
    assert len(about["score_bands"]) == 4


def test_about_score_bands_labels(about):
    labels = {b["label"] for b in about["score_bands"]}
    assert labels == {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def test_about_score_bands_all_fields(about):
    for band in about["score_bands"]:
        for field in ("range", "label", "color", "headline", "meaning"):
            assert field in band, f"Missing field '{field}' in band {band.get('label')}"
        assert band["color"].startswith("#"), f"Color must be hex: {band['color']}"
        assert len(band["meaning"]) > 20, "Band meaning too short"


def test_about_score_band_ranges_cover_0_to_100(about):
    ranges = [b["range"] for b in about["score_bands"]]
    # Collect all start values
    starts = []
    for r in ranges:
        lo, hi = r.split("–")
        starts.append(int(lo))
    assert 0 in starts, "No band starts at 0"
    ends = []
    for r in ranges:
        lo, hi = r.split("–")
        ends.append(int(hi))
    assert 100 in ends, "No band ends at 100"


# ── Risk factors ──────────────────────────────────────────────────────────────

def test_about_risk_factors_count(about):
    assert len(about["risk_factors"]) == 7


def test_about_risk_factors_all_fields(about):
    for factor in about["risk_factors"]:
        for field in ("factor", "weight", "icon", "detail"):
            assert field in factor, f"Missing field '{field}' in factor {factor.get('factor')}"
        assert factor["weight"] in ("HIGH", "MEDIUM", "LOW")


def test_about_risk_factors_include_flood(about):
    factors = [f["factor"] for f in about["risk_factors"]]
    assert any("flood" in f.lower() for f in factors), "Missing flood risk factor"


def test_about_risk_factors_include_permits(about):
    factors = [f["factor"] for f in about["risk_factors"]]
    assert any("permit" in f.lower() for f in factors), "Missing permit risk factor"


# ── Glossary ──────────────────────────────────────────────────────────────────

def test_about_glossary_min_terms(about):
    assert len(about["glossary"]) >= 8


def test_about_glossary_all_fields(about):
    for term in about["glossary"]:
        for field in ("term", "short", "detail"):
            assert field in term, f"Missing field '{field}' in glossary entry {term.get('term')}"
        assert len(term["detail"]) > 30, "Glossary detail too short"


def test_about_glossary_includes_buyer_risk_score(about):
    terms = [t["term"].lower() for t in about["glossary"]]
    assert any("buyer risk score" in t or "risk score" in t for t in terms)


def test_about_glossary_includes_escape_plan(about):
    terms = [t["term"].lower() for t in about["glossary"]]
    assert any("escape" in t for t in terms)


# ── Data sources ──────────────────────────────────────────────────────────────

def test_about_data_sources_count(about):
    assert len(about["data_sources"]) >= 6


def test_about_data_sources_all_fields(about):
    for src in about["data_sources"]:
        for field in ("id", "name", "icon", "coverage", "what", "why_it_matters", "freshness"):
            assert field in src, f"Missing field '{field}' in source {src.get('id')}"


def test_about_data_sources_ids(about):
    ids = {s["id"] for s in about["data_sources"]}
    for required in ("fema", "usgs", "epa", "osm"):
        assert required in ids, f"Missing data source: {required}"


# ── Adversarial pipeline ──────────────────────────────────────────────────────

def test_about_pipeline_agents_count(about):
    agents = about["adversarial_pipeline"]["agents"]
    assert len(agents) == 7


def test_about_pipeline_agent_names(about):
    names = {a["name"] for a in about["adversarial_pipeline"]["agents"]}
    expected = {
        "GeocoderAgent", "DeedAgent", "PermitAgent",
        "ClimateAgent", "NeighborhoodAgent", "SynthesisAgent", "DebateAgent",
    }
    assert names == expected


def test_about_pipeline_all_fields(about):
    for agent in about["adversarial_pipeline"]["agents"]:
        for field in ("name", "icon", "role"):
            assert field in agent, f"Missing field '{field}' in agent {agent.get('name')}"
        assert len(agent["role"]) > 20


def test_about_pipeline_description(about):
    desc = about["adversarial_pipeline"]["description"]
    assert len(desc) > 50


# ── Validated examples ────────────────────────────────────────────────────────

def test_about_validated_examples_count(about):
    assert len(about["validated_examples"]) >= 3


def test_about_validated_examples_all_fields(about):
    for ex in about["validated_examples"]:
        for field in ("address", "label", "score", "level", "headline", "key_finding", "outcome"):
            assert field in ex, f"Missing field '{field}' in example {ex.get('label')}"
        assert 0 <= ex["score"] <= 100
        assert ex["level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
