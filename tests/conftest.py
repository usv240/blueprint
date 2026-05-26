"""
Shared pytest fixtures for Blueprint API tests.
Requires a running server at http://localhost:8080 (uvicorn backend.main:app --port 8080).
Run with: cd blueprint && python -m pytest tests/ -v
Slow pipeline tests: python -m pytest tests/ -v -m slow
"""
import pytest
import httpx

BASE_URL = "http://localhost:8081"


@pytest.fixture(scope="session")
def client():
    """Synchronous httpx client for the live Blueprint server."""
    with httpx.Client(base_url=BASE_URL, timeout=120.0) as c:
        yield c


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


# ── Demo addresses used across tests ─────────────────────────────────────────

DEMO_ADDRESSES = [
    "363 Van Brunt St, Brooklyn, NY",
    "2121 Airline Dr, Houston, TX",
    "2000 E Olympic Blvd, Los Angeles, CA",
]

NYC_ADDRESS = "363 Van Brunt St, Brooklyn, NY"
AUSTIN_ADDRESS = "1010 Lavaca St, Austin, TX"
SHORT_ADDRESS = "ab"       # too short — should fail validation
LONG_ADDRESS = "A" * 301   # too long — should fail validation
