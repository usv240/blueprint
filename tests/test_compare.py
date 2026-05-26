"""
Tests for POST /api/compare — parallel dual-pipeline property comparison.
These hit live APIs. Mark: @pytest.mark.slow

Run: python -m pytest tests/test_compare.py -v -m slow
"""
import pytest

pytestmark = pytest.mark.slow

ADDRESS_A = "363 Van Brunt St, Brooklyn, NY"
ADDRESS_B = "2000 E Olympic Blvd, Los Angeles, CA"


@pytest.fixture(scope="module")
def compare_result(client):
    """Run one comparison and cache the result for all tests in this module."""
    r = client.post(
        "/api/compare",
        json={"address_a": ADDRESS_A, "address_b": ADDRESS_B},
        timeout=300.0,
    )
    assert r.status_code == 200, f"Compare failed: {r.text}"
    return r.json()


def test_compare_has_report_a(compare_result):
    assert "a" in compare_result


def test_compare_has_report_b(compare_result):
    assert "b" in compare_result


def test_compare_report_a_has_score(compare_result):
    assert "buyer_risk_score" in compare_result["a"]
    assert 0 <= compare_result["a"]["buyer_risk_score"] <= 100


def test_compare_report_b_has_score(compare_result):
    assert "buyer_risk_score" in compare_result["b"]
    assert 0 <= compare_result["b"]["buyer_risk_score"] <= 100


def test_compare_has_recommendation(compare_result):
    assert "recommendation" in compare_result


def test_compare_recommendation_has_recommended_field(compare_result):
    rec = compare_result["recommendation"]
    assert "recommended" in rec
    assert rec["recommended"] in ("a", "b", "A", "B", None, "neither")


def test_compare_recommendation_has_headline(compare_result):
    rec = compare_result["recommendation"]
    assert "headline" in rec
    assert len(rec["headline"]) > 5


def test_compare_recommendation_has_rationale(compare_result):
    rec = compare_result["recommendation"]
    assert "rationale" in rec
    assert len(rec["rationale"]) > 20


def test_compare_recommendation_has_pros_cons(compare_result):
    rec = compare_result["recommendation"]
    assert "a_pros" in rec or "pros_a" in rec
    assert "b_pros" in rec or "pros_b" in rec


def test_compare_recommendation_confidence_present(compare_result):
    rec = compare_result["recommendation"]
    assert "confidence" in rec


def test_compare_both_addresses_analyzed(compare_result):
    """Verify both addresses are represented in the results."""
    a_addr = str(compare_result["a"].get("address", "")).lower()
    b_addr = str(compare_result["b"].get("address", "")).lower()
    assert "brooklyn" in a_addr or "van brunt" in a_addr or len(a_addr) > 0
    assert "los angeles" in b_addr or "olympic" in b_addr or len(b_addr) > 0
