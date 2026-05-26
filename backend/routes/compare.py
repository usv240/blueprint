"""
/api/compare — run two property analyses in parallel and deliver a
"Which should I buy?" recommendation powered by Gemini 3.

POST /api/compare
Body: { "address_a": "...", "address_b": "..." }
Response: { "a": <report>, "b": <report>, "recommendation": { ... } }
"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from backend.services import gemini
from backend.services.adk_runner import run_analysis

logger = logging.getLogger(__name__)
router = APIRouter()


class CompareRequest(BaseModel):
    address_a: str
    address_b: str

    @field_validator("address_a", "address_b")
    @classmethod
    def validate_address(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 5:
            raise ValueError("Address too short (min 5 characters)")
        if len(v) > 300:
            raise ValueError("Address too long (max 300 characters)")
        return v


@router.post("/compare")
async def compare_properties(req: CompareRequest):
    """
    Run full 7-agent analyses on two properties in parallel, then use Gemini
    to produce a head-to-head 'Which should I buy?' recommendation.
    """
    logger.info("[Compare] Starting parallel analysis: %r vs %r", req.address_a, req.address_b)

    report_a, report_b = await asyncio.gather(
        run_analysis(req.address_a),
        run_analysis(req.address_b),
        return_exceptions=True,
    )

    if isinstance(report_a, Exception):
        logger.error("[Compare] Analysis A failed: %s", report_a)
        raise HTTPException(status_code=500, detail=f"Analysis failed for address A: {report_a}")
    if isinstance(report_b, Exception):
        logger.error("[Compare] Analysis B failed: %s", report_b)
        raise HTTPException(status_code=500, detail=f"Analysis failed for address B: {report_b}")

    recommendation = await _compare_with_gemini(report_a, report_b)

    return {
        "a": report_a,
        "b": report_b,
        "recommendation": recommendation,
    }


async def _compare_with_gemini(a: dict, b: dict) -> dict:
    score_a = a.get("buyer_risk_score", 50)
    score_b = b.get("buyer_risk_score", 50)

    prompt = f"""You are BLUEPRINT, an AI property intelligence advisor. Compare two properties and tell a buyer which to buy.

PROPERTY A — {a.get("normalized_address", "Address A")}
Risk Score: {score_a}/100 ({a.get("risk_level", "?")})
Summary: {a.get("summary", "")}
Top risk flags: {json.dumps((a.get("flags") or [])[:4], ensure_ascii=False)}
Debate verdict: {(a.get("debate") or {}).get("buy_recommendation", "N/A")} ({(a.get("debate") or {}).get("confidence", "?")})
Escape plan steps: {len(a.get("escape_plan") or [])}
Neighbourhood score: {(a.get("neighborhood") or {}).get("neighborhood_score", "N/A")}

PROPERTY B — {b.get("normalized_address", "Address B")}
Risk Score: {score_b}/100 ({b.get("risk_level", "?")})
Summary: {b.get("summary", "")}
Top risk flags: {json.dumps((b.get("flags") or [])[:4], ensure_ascii=False)}
Debate verdict: {(b.get("debate") or {}).get("buy_recommendation", "N/A")} ({(b.get("debate") or {}).get("confidence", "?")})
Escape plan steps: {len(b.get("escape_plan") or [])}
Neighbourhood score: {(b.get("neighborhood") or {}).get("neighborhood_score", "N/A")}

Respond with ONLY valid JSON — no markdown, no prose outside the JSON:
{{
  "recommended": "A" or "B" or "NEITHER",
  "confidence": "HIGH" or "MEDIUM" or "LOW",
  "headline": "One punchy sentence: which to buy and the #1 reason",
  "rationale": "2-3 sentence explanation citing specific data points",
  "a_pros": ["up to 3 key advantages of A"],
  "a_cons": ["up to 3 key disadvantages of A"],
  "b_pros": ["up to 3 key advantages of B"],
  "b_cons": ["up to 3 key disadvantages of B"],
  "deal_breaker": "The single biggest red flag across both properties (or empty string)"
}}"""

    text = await gemini.generate(prompt, temperature=0.3)
    text = (text or "").strip()

    # Strip markdown fences
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            if part.startswith("json"):
                part = part[4:]
            stripped = part.strip()
            if stripped.startswith("{"):
                text = stripped
                break

    try:
        return json.loads(text)
    except Exception:
        # Fallback: derive from raw scores
        if score_a != score_b:
            rec = "A" if score_a < score_b else "B"
        else:
            rec = "NEITHER"
        return {
            "recommended": rec,
            "confidence": "LOW",
            "headline": f"Property {rec} has the lower risk score ({min(score_a, score_b)}/100).",
            "rationale": "Recommendation derived from risk scores only — Gemini comparison unavailable.",
            "a_pros": [], "a_cons": [],
            "b_pros": [], "b_cons": [],
            "deal_breaker": "",
        }
