"""
HTML export route — professional buyer brief as a downloadable HTML file.

GET /api/export/{address_hash}

Retrieves the stored report from Elasticsearch and uses Gemini to render a
complete, self-contained HTML buyer brief (inline CSS, no external deps).
The response is streamed as an attachment so the browser triggers a download.
"""
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from backend.services import gemini
from backend.services.elastic_client import IDX_REPORTS, elastic

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/export/{address_hash}")
async def export_report(address_hash: str):
    """Generate and return a downloadable HTML buyer brief for a property."""
    if not elastic.available:
        raise HTTPException(status_code=503, detail="Elasticsearch not available")

    try:
        doc    = await elastic._es.get(index=IDX_REPORTS, id=address_hash)
        report = doc["_source"]
    except Exception:
        raise HTTPException(status_code=404, detail="Report not found — run an analysis first")

    html     = await _generate_html(report)
    filename = f"blueprint-report-{address_hash[:8]}.html"
    return HTMLResponse(
        content=html,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _generate_html(report: dict) -> str:
    score     = report.get("buyer_risk_score", "N/A")
    level     = report.get("risk_level", "UNKNOWN")
    address   = report.get("normalized_address", "Unknown address")
    summary   = report.get("summary", "")
    flags     = report.get("flags", [])
    questions = report.get("diligence_questions", [])
    positives = report.get("positive_signals", [])
    timeline  = report.get("timeline", [])[:15]
    created   = str(report.get("created_at", ""))[:10]
    escape    = report.get("escape_plan", [])
    debate    = report.get("debate", {})
    nbhd      = report.get("neighborhood", {})

    colors      = {"LOW": "#22c55e", "MEDIUM": "#eab308", "HIGH": "#f97316", "CRITICAL": "#ef4444"}
    # Prefer debate-adjusted score/level if available
    if debate.get("final_score") is not None:
        score = debate["final_score"]
        level = report.get("risk_level", level)  # already updated by debate write-back
    score_color = colors.get(level, "#64748b")

    debate_verdict  = debate.get("buy_recommendation", "")
    debate_text     = debate.get("verdict_text", "")
    debate_conf     = debate.get("confidence", "")
    debate_final    = debate.get("final_score", "")
    optimist_arg    = (debate.get("optimist") or {}).get("argument", "")
    pessimist_arg   = (debate.get("pessimist") or {}).get("argument", "")
    optimist_score  = (debate.get("optimist") or {}).get("adjusted_score", "")
    pessimist_score = (debate.get("pessimist") or {}).get("adjusted_score", "")

    nbhd_items = []
    if nbhd:
        if nbhd.get("pm25") is not None:
            nbhd_items.append({"label": "Air Quality (PM2.5)", "value": f"{nbhd['pm25']:.1f} µg/m³", "ok": nbhd["pm25"] <= 12})
        if nbhd.get("superfund_proximity") is not None:
            nbhd_items.append({"label": "Superfund Proximity", "value": f"{nbhd['superfund_proximity']:.0f}/100 index", "ok": nbhd["superfund_proximity"] < 40})
        if nbhd.get("traffic_proximity") is not None:
            nbhd_items.append({"label": "Traffic Pollution", "value": f"{nbhd['traffic_proximity']:.0f}/100 index", "ok": nbhd["traffic_proximity"] < 60})
        if nbhd.get("schools_nearby") is not None:
            nbhd_items.append({"label": "Schools (500m)", "value": str(nbhd["schools_nearby"]), "ok": nbhd["schools_nearby"] > 0})
        if nbhd.get("parks_nearby") is not None:
            nbhd_items.append({"label": "Parks (500m)", "value": str(nbhd["parks_nearby"]), "ok": nbhd["parks_nearby"] > 0})
        if nbhd.get("transit_nearby") is not None:
            nbhd_items.append({"label": "Transit Stops (500m)", "value": str(nbhd["transit_nearby"]), "ok": nbhd["transit_nearby"] > 1})
        if nbhd.get("neighborhood_score") is not None:
            nbhd_items.append({"label": "Neighbourhood Score", "value": f"{nbhd['neighborhood_score']}/100", "ok": nbhd["neighborhood_score"] >= 60})

    prompt = f"""You are generating a professional property buyer intelligence report as a standalone HTML file.
Output ONLY raw HTML — no markdown fences, no explanatory text, nothing before <!DOCTYPE html>.
This should look like a premium $500 real estate due-diligence report.

=== DATA ===
Property: {address}
Report Date: {created}
Buyer Risk Score: {score}/100  |  Risk Level: {level}
Score color accent: {score_color}

Executive Summary:
{summary}

Risk Flags (JSON): {json.dumps(flags, ensure_ascii=False)}

Escape Plan — ranked steps to reduce risk (JSON): {json.dumps(escape, ensure_ascii=False)}

Due Diligence Questions (JSON): {json.dumps(questions, ensure_ascii=False)}

Positive Signals (JSON): {json.dumps(positives, ensure_ascii=False)}

AI Debate Results:
  Recommendation: {debate_verdict}  |  Confidence: {debate_conf}  |  Final Score: {debate_final}/100
  OptimistAgent argument: {optimist_arg}  (suggested score: {optimist_score}/100)
  PessimistAgent argument: {pessimist_arg}  (suggested score: {pessimist_score}/100)
  Verdict: {debate_text}

Neighbourhood Intelligence (JSON): {json.dumps(nbhd_items, ensure_ascii=False)}

Property Timeline — up to 15 events (JSON array, each has date/event_type/description/amount/source):
{json.dumps(timeline, ensure_ascii=False)}

=== DESIGN REQUIREMENTS ===
- Complete standalone HTML, ALL CSS inline in <style> block, zero external dependencies
- Professional, clean design; max-width 900px centered; font: system-ui, -apple-system, sans-serif
- Dark navy header bar with property address, score badge, and BLUEPRINT logo
- Risk score shown as a large number circle with {score_color} ring and "{level}" label
- Section order: 1) Cover header  2) AI Debate Verdict  3) Executive Summary  4) Risk Flags  5) Escape Plan  6) Neighbourhood Intelligence  7) Due Diligence Questions  8) Property Timeline  9) Positive Signals
- AI Debate: two side-by-side cards — green "Optimist" with its argument and score, red "Pessimist" with its argument and score. Below them a verdict block with {debate_verdict} badge.
- Risk Flags: red/orange warning cards with a bold "!" indicator, each flag as a card
- Escape Plan: numbered ordered list with blue accent, each step prominent
- Neighbourhood: 2-column grid of metric cards, green if ok=true, red/amber if ok=false
- Timeline: clean table with columns Date | Type | Description | Amount | Source; alternate row shading
- Positive Signals: green check cards
- Print-friendly (no dark backgrounds for content, avoid fixed heights)
- Professional footer: "BLUEPRINT Property Intelligence Report · {created} · Buyer Risk Score: {score}/100 · All data sourced from public records (NYC Open Data, Austin Open Data, FEMA NFHL, USGS, EPA EJSCREEN, OpenStreetMap) · This report is for informational purposes only and does not constitute professional real estate, legal, or financial advice. Always verify findings with licensed professionals."
- Do NOT include placeholder text — only render sections that have actual data
"""

    html_text = await gemini.generate(prompt, temperature=0.2)
    html_text = (html_text or "").strip()

    # Strip any markdown fence that might slip through
    if "```" in html_text:
        lines   = html_text.split("\n")
        cleaned = []
        in_fence = False
        for line in lines:
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if not in_fence:
                cleaned.append(line)
        html_text = "\n".join(cleaned).strip()

    if "<!DOCTYPE" not in html_text and "<html" not in html_text:
        logger.warning("[Export] Gemini returned non-HTML — using minimal fallback")
        html_text = _minimal_fallback(address, score, level, summary, score_color, created)

    return html_text


def _minimal_fallback(address: str, score, level: str, summary: str, color: str, created: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"/>
<title>BLUEPRINT · {address}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:900px;margin:0 auto;padding:2rem;color:#1e293b;}}
.header{{background:#0f172a;color:white;padding:2rem;border-radius:8px;margin-bottom:2rem;}}
.score{{font-size:3rem;font-weight:700;color:{color};}}
.badge{{background:{color};color:white;padding:.25rem .75rem;border-radius:20px;font-size:.85rem;font-weight:600;}}
p{{line-height:1.7;}}
footer{{margin-top:3rem;font-size:.75rem;color:#94a3b8;border-top:1px solid #e2e8f0;padding-top:1rem;}}
</style></head><body>
<div class="header">
  <h1>BLUEPRINT Property Intelligence</h1>
  <p>{address}</p>
  <div class="score">{score}</div>
  <span class="badge">{level} RISK</span>
</div>
<p>{summary}</p>
<footer>BLUEPRINT Property Intelligence Report · {created} · All data sourced from public records · Not professional advice</footer>
</body></html>"""
