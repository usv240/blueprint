"""Tests for /api/watch — watchlist CRUD operations."""
import hashlib
import pytest

WATCH_NORMALIZED = "1600 Pennsylvania Ave NW, Washington, DC"
# Compute the same hash the server uses: md5(normalized.lower())[:12]
WATCH_HASH = hashlib.md5(WATCH_NORMALIZED.lower().encode()).hexdigest()[:12]
WATCH_PAYLOAD = {"address_hash": WATCH_HASH, "normalized_address": WATCH_NORMALIZED}


def test_watch_list_returns_list(client):
    r = client.get("/api/watch")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_watch_add(client):
    r = client.post("/api/watch", json=WATCH_PAYLOAD)
    assert r.status_code in (200, 201), f"Expected 200/201, got {r.status_code}: {r.text}"
    data = r.json()
    assert "address_hash" in data or "watched" in data


def test_watch_list_after_add(client):
    client.post("/api/watch", json=WATCH_PAYLOAD)
    items = client.get("/api/watch").json()
    addresses = [
        item.get("normalized_address", "").lower()
        for item in items
    ]
    assert any("pennsylvania" in a for a in addresses), (
        f"Added address not found in watchlist. Items: {items}"
    )


def test_watch_delete(client):
    client.post("/api/watch", json=WATCH_PAYLOAD)
    r = client.delete(f"/api/watch/{WATCH_HASH}")
    assert r.status_code in (200, 204)


def test_watch_delete_unknown_hash(client):
    r = client.delete("/api/watch/nonexistent_000000000")
    assert r.status_code in (404, 200)  # 200 acceptable for idempotent delete


def test_watch_rejects_missing_address_hash(client):
    r = client.post("/api/watch", json={"normalized_address": WATCH_NORMALIZED})
    assert r.status_code == 422


def test_watch_rejects_duplicate_gracefully(client):
    client.post("/api/watch", json=WATCH_PAYLOAD)
    r = client.post("/api/watch", json=WATCH_PAYLOAD)
    # Elasticsearch upsert — 200 on duplicate is correct
    assert r.status_code in (200, 201, 409)
