"""
/api/analyze — one-shot JSON analysis
/api/analyze/stream — SSE real-time streaming analysis
/api/report/{address_hash} — retrieve a stored report from Elasticsearch
/api/health — service health check
"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from backend.services.adk_runner import run_analysis
from backend.services.elastic_client import IDX_CASES, IDX_REPORTS, elastic
from backend.services import gemini

logger = logging.getLogger(__name__)
router = APIRouter()


class AnalyzeRequest(BaseModel):
    address: str

    @field_validator("address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 5:
            raise ValueError("Address too short")
        if len(v) > 300:
            raise ValueError("Address too long")
        return v


# ── One-shot JSON analysis ─────────────────────────────────────────────────────

@router.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """Run the full 7-agent pipeline and return the final report as JSON."""
    report = await run_analysis(req.address)
    if "error" in report and len(report) == 1:
        raise HTTPException(status_code=400, detail=report["error"])
    return report


# ── SSE streaming analysis ────────────────────────────────────────────────────

@router.get("/analyze/stream")
async def analyze_stream(address: str = Query(..., min_length=5, max_length=300)):
    """
    Stream real-time agent progress as Server-Sent Events.
    The final event is type="complete" and carries the full report.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    async def run():
        try:
            await run_analysis(address, stream_queue=queue)
        finally:
            await queue.put(None)  # sentinel: pipeline done

    asyncio.create_task(run())

    async def event_generator():
        try:
            while True:
                item = await asyncio.wait_for(queue.get(), timeout=120)
                if item is None:
                    yield "data: [DONE]\n\n"
                    break
                yield f"data: {json.dumps(item)}\n\n"
        except asyncio.TimeoutError:
            yield f'data: {json.dumps({"type": "error", "message": "Pipeline timed out"})}\n\n'
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Report retrieval ──────────────────────────────────────────────────────────

@router.get("/report/{address_hash}")
async def get_report(address_hash: str):
    """Retrieve a previously generated report from Elasticsearch by address hash."""
    if not elastic.available:
        raise HTTPException(status_code=503, detail="Elasticsearch not available")
    try:
        doc = await elastic._es.get(index=IDX_REPORTS, id=address_hash)
        return doc["_source"]
    except Exception:
        raise HTTPException(status_code=404, detail="Report not found")


@router.get("/case/{address_hash}")
async def get_case(address_hash: str):
    """Retrieve the case file for a property by address hash."""
    if not elastic.available:
        raise HTTPException(status_code=503, detail="Elasticsearch not available")
    try:
        doc = await elastic._es.get(index=IDX_CASES, id=address_hash)
        return doc["_source"]
    except Exception:
        raise HTTPException(status_code=404, detail="Case not found")


# ── Recent reports list ───────────────────────────────────────────────────────

@router.post("/ask")
async def ask_question(req: dict):
    """
    Conversational Q&A about a property report.
    Body: { address_hash: str, question: str }
    """
    address_hash = (req.get("address_hash") or "").strip()
    question     = (req.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    # Load report context from Elastic
    report_context = ""
    if address_hash and elastic.available:
        try:
            doc = await elastic._es.get(index=IDX_REPORTS, id=address_hash)
            r = doc["_source"]
            report_context = f"""
Property: {r.get('normalized_address', 'Unknown')}
Buyer Risk Score: {r.get('buyer_risk_score', '?')}/100 ({r.get('risk_level', '?')})
Summary: {r.get('summary', '')}
Risk Flags: {r.get('flags', [])}
Escape Plan: {r.get('escape_plan', [])}
Buy Recommendation: {r.get('buy_recommendation', 'N/A')}
Debate Verdict: {r.get('debate', {}).get('verdict_text', 'N/A')}
Diligence Questions: {r.get('diligence_questions', [])}
"""
        except Exception:
            pass

    prompt = f"""You are BLUEPRINT, an AI property due-diligence advisor. A buyer is asking you a question about a property analysis.

{report_context if report_context else "No report context available — answer based on general real estate knowledge."}

BUYER'S QUESTION: {question}

Answer in 2-4 sentences. Be specific, practical, and cite the data above where relevant. If you don't have enough data to answer confidently, say so and suggest what they should verify."""

    answer = await gemini.generate(prompt, temperature=0.4)
    return {"answer": answer or "I don't have enough information to answer that question."}


@router.get("/recent")
async def recent_reports(limit: int = Query(default=10, ge=1, le=50)):
    """List the most recent property analyses stored in Elasticsearch."""
    if not elastic.available:
        return []
    try:
        resp = await elastic._es.search(
            index=IDX_REPORTS,
            sort=[{"created_at": {"order": "desc"}}],
            size=limit,
            source=["normalized_address", "buyer_risk_score", "risk_level", "created_at", "address_hash"],
        )
        return [h["_source"] for h in resp["hits"]["hits"]]
    except Exception:
        return []
