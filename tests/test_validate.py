"""Input validation tests — verify the server rejects bad inputs with 422."""
import pytest


# ── /api/analyze validation ───────────────────────────────────────────────────

def test_analyze_rejects_missing_address(client):
    r = client.post("/api/analyze", json={})
    assert r.status_code == 422


def test_analyze_rejects_short_address(client):
    r = client.post("/api/analyze", json={"address": "ab"})
    assert r.status_code == 422


def test_analyze_rejects_long_address(client):
    r = client.post("/api/analyze", json={"address": "A" * 301})
    assert r.status_code == 422


def test_analyze_stream_rejects_missing_address(client):
    r = client.get("/api/analyze/stream")
    assert r.status_code == 422


def test_analyze_stream_rejects_short_address(client):
    r = client.get("/api/analyze/stream", params={"address": "ab"})
    assert r.status_code == 422


def test_analyze_stream_rejects_long_address(client):
    r = client.get("/api/analyze/stream", params={"address": "A" * 301})
    assert r.status_code == 422


# ── /api/compare validation ───────────────────────────────────────────────────

def test_compare_rejects_missing_both_addresses(client):
    r = client.post("/api/compare", json={})
    assert r.status_code == 422


def test_compare_rejects_missing_address_b(client):
    r = client.post("/api/compare", json={"address_a": "363 Van Brunt St, Brooklyn, NY"})
    assert r.status_code == 422


def test_compare_rejects_short_address_a(client):
    r = client.post("/api/compare", json={"address_a": "ab", "address_b": "363 Van Brunt St, Brooklyn, NY"})
    assert r.status_code == 422


def test_compare_rejects_short_address_b(client):
    r = client.post("/api/compare", json={"address_a": "363 Van Brunt St, Brooklyn, NY", "address_b": "xy"})
    assert r.status_code == 422


# ── /api/ask validation ───────────────────────────────────────────────────────

def test_ask_rejects_missing_fields(client):
    r = client.post("/api/ask", json={})
    assert r.status_code in (400, 422)


def test_ask_rejects_empty_question(client):
    r = client.post("/api/ask", json={"address_hash": "abc123", "question": ""})
    assert r.status_code in (400, 422)


# ── /api/watch validation ─────────────────────────────────────────────────────

def test_watch_rejects_empty_payload(client):
    r = client.post("/api/watch", json={})
    assert r.status_code == 422


# ── /api/report not found ─────────────────────────────────────────────────────

def test_report_returns_404_for_unknown_hash(client):
    r = client.get("/api/report/nonexistent_hash_000000000000")
    assert r.status_code == 404


# ── /api/export not found ─────────────────────────────────────────────────────

def test_export_returns_404_for_unknown_hash(client):
    r = client.get("/api/export/nonexistent_hash_000000000000")
    assert r.status_code == 404


# ── /api/share not found ──────────────────────────────────────────────────────

def test_share_returns_404_for_unknown_hash(client):
    r = client.post("/api/share/nonexistent_hash_000000000000")
    assert r.status_code == 404
