"""
Full 7-agent pipeline tests — these hit live APIs (Gemini, Elastic, FEMA, USGS, EPA).
Mark: @pytest.mark.slow

Run only fast tests: python -m pytest tests/ -v -m "not slow"
Run slow tests:      python -m pytest tests/ -v -m slow
Run everything:      python -m pytest tests/ -v
"""
import json
import pytest

pytestmark = pytest.mark.slow

NYC_ADDRESS = "363 Van Brunt St, Brooklyn, NY"
LA_ADDRESS = "2000 E Olympic Blvd, Los Angeles, CA"


# ── /api/analyze (one-shot JSON) ──────────────────────────────────────────────

def test_analyze_nyc_returns_200(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    assert r.status_code == 200


def test_analyze_nyc_has_address(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    assert "address" in data or "normalized_address" in data


def test_analyze_nyc_has_risk_score(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    assert "buyer_risk_score" in data
    assert isinstance(data["buyer_risk_score"], (int, float))
    assert 0 <= data["buyer_risk_score"] <= 100


def test_analyze_nyc_has_risk_level(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    assert "risk_level" in data
    assert data["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def test_analyze_nyc_has_verdict(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    verdict = data.get("verdict", "")
    assert any(v in verdict.upper() for v in ("BUY", "NEGOTIATE", "AVOID", "CAUTION")), (
        f"Unexpected verdict: {verdict}"
    )


def test_analyze_nyc_has_flood_zone(client):
    """NYC Van Brunt St is in FEMA flood zone — should have climate data."""
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    # Either flood_zone key or climate_summary should be present
    assert (
        "flood_zone" in data
        or "climate_summary" in data
        or "climate" in str(data).lower()
    )


def test_analyze_nyc_has_debate_result(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    assert "debate" in data or "optimist" in str(data).lower() or "pessimist" in str(data).lower()


def test_analyze_nyc_has_escape_plan(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    assert "escape_plan" in data or "escape" in str(data).lower()


def test_analyze_nyc_has_data_sources(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    assert "data_sources" in data
    assert isinstance(data["data_sources"], list)
    assert len(data["data_sources"]) > 0


def test_analyze_nyc_has_report_hash(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    assert "address_hash" in data or "report_hash" in data or "hash" in data


def test_analyze_la_returns_200(client):
    r = client.post("/api/analyze", json={"address": LA_ADDRESS}, timeout=180.0)
    assert r.status_code == 200


def test_analyze_la_has_risk_score(client):
    r = client.post("/api/analyze", json={"address": LA_ADDRESS}, timeout=180.0)
    data = r.json()
    assert "buyer_risk_score" in data
    assert 0 <= data["buyer_risk_score"] <= 100


def test_analyze_la_has_earthquake_data(client):
    """LA area should surface earthquake data from USGS."""
    r = client.post("/api/analyze", json={"address": LA_ADDRESS}, timeout=180.0)
    data = r.json()
    assert "earthquake" in str(data).lower() or "seismic" in str(data).lower()


# ── /api/analyze/stream (SSE) ─────────────────────────────────────────────────

def test_analyze_stream_content_type(client):
    with client.stream(
        "GET",
        "/api/analyze/stream",
        params={"address": NYC_ADDRESS},
        timeout=180.0,
    ) as r:
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "text/event-stream" in ct


def test_analyze_stream_emits_events(client):
    events = []
    with client.stream(
        "GET",
        "/api/analyze/stream",
        params={"address": NYC_ADDRESS},
        timeout=180.0,
    ) as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(line[5:].strip())
                if len(events) >= 3:
                    break

    assert len(events) >= 1, "No SSE events emitted"


def test_analyze_stream_emits_complete_event(client):
    complete_data = None
    with client.stream(
        "GET",
        "/api/analyze/stream",
        params={"address": NYC_ADDRESS},
        timeout=180.0,
    ) as r:
        event_type = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                if event_type == "complete":
                    complete_data = line[5:].strip()
                    break

    assert complete_data is not None, "No 'complete' SSE event received"
    parsed = json.loads(complete_data)
    assert "buyer_risk_score" in parsed


def test_analyze_stream_agent_progress_events(client):
    """Verify we receive named agent progress events before completion."""
    agent_events = []
    with client.stream(
        "GET",
        "/api/analyze/stream",
        params={"address": NYC_ADDRESS},
        timeout=180.0,
    ) as r:
        for line in r.iter_lines():
            if line.startswith("event:") and "complete" not in line:
                agent_events.append(line[6:].strip())
            elif line.startswith("event:complete") or (
                line.startswith("event:") and line.strip().endswith("complete")
            ):
                break

    assert len(agent_events) >= 1, "Expected at least one agent progress event before completion"


# ── Report retrieval after analysis ──────────────────────────────────────────

def test_report_retrievable_after_analyze(client):
    """Analyze → get the hash → retrieve from /api/report/{hash}."""
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    addr_hash = data.get("address_hash") or data.get("hash")
    if not addr_hash:
        pytest.skip("No address_hash in analyze response — Elastic may be unavailable")

    r2 = client.get(f"/api/report/{addr_hash}")
    assert r2.status_code == 200
    report = r2.json()
    assert "buyer_risk_score" in report


# ── Share after analysis ──────────────────────────────────────────────────────

def test_share_after_analyze(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    addr_hash = data.get("address_hash") or data.get("hash")
    if not addr_hash:
        pytest.skip("No address_hash in analyze response")

    r2 = client.post(f"/api/share/{addr_hash}")
    assert r2.status_code == 200
    share_data = r2.json()
    assert "share_url" in share_data or "url" in share_data or "link" in share_data


# ── Export after analysis ─────────────────────────────────────────────────────

def test_export_after_analyze(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    addr_hash = data.get("address_hash") or data.get("hash")
    if not addr_hash:
        pytest.skip("No address_hash in analyze response")

    r2 = client.get(f"/api/export/{addr_hash}", timeout=120.0)
    assert r2.status_code == 200
    ct = r2.headers.get("content-type", "")
    assert "html" in ct
    assert "<html" in r2.text.lower() or "<!doctype" in r2.text.lower()


# ── Q&A chat after analysis ───────────────────────────────────────────────────

def test_ask_after_analyze(client):
    r = client.post("/api/analyze", json={"address": NYC_ADDRESS}, timeout=180.0)
    data = r.json()
    addr_hash = data.get("address_hash") or data.get("hash")
    if not addr_hash:
        pytest.skip("No address_hash in analyze response")

    r2 = client.post(
        "/api/ask",
        json={"address_hash": addr_hash, "question": "What is the flood risk?"},
        timeout=60.0,
    )
    assert r2.status_code == 200
    answer = r2.json()
    assert "answer" in answer or "response" in answer or "text" in answer
