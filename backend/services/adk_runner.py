"""
BLUEPRINT ADK pipeline: 7-agent SequentialAgent that analyses a property address.

Pipeline:
  GeocoderAgent    → normalise address + geocode to lat/lng + initialise Elastic case file
  DeedAgent        → fetch deed/sale records + write to Elastic memory layer
  PermitAgent      → fetch building permits + write to Elastic memory layer
  ClimateAgent     → fetch FEMA/USGS climate risk + write to Elastic memory layer
  NeighborhoodAgent→ EPA EJSCREEN + OSM amenities + write to Elastic memory layer
  SynthesisAgent   → hybrid ELSER search over Elastic + ES|QL cross-reference → final report
  DebateAgent      → OptimistAgent vs PessimistAgent → VerdictAgent BUY/NEGOTIATE/AVOID

SSE streaming: each tool call pushes events onto an asyncio.Queue bound to the current
request via a ContextVar, identical to the pattern used in ORACLE.
"""
import asyncio
import hashlib
import json
import logging
import os
from contextvars import ContextVar

# google-genai SDK references aiohttp.ClientConnectorDNSError which was added
# in aiohttp 3.11. Patch it to the equivalent class for older installs.
import aiohttp as _aiohttp
if not hasattr(_aiohttp, "ClientConnectorDNSError"):
    _aiohttp.ClientConnectorDNSError = _aiohttp.ClientConnectorError
from datetime import datetime, timezone
from typing import Any

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.adk.tools.mcp_tool import MCPToolset, StreamableHTTPConnectionParams
from google.genai import types as genai_types

from backend.config import settings

# ADK uses GOOGLE_API_KEY to authenticate with the Gemini API.
os.environ.setdefault("GOOGLE_API_KEY", settings.GEMINI_API_KEY)

from backend.services import gemini
from backend.services.data_fetchers import (
    fetch_climate_risk,
    fetch_deed_records,
    fetch_neighborhood_data,
    fetch_permit_records,
)
from backend.services.elastic_client import IDX_CASES, IDX_EVENTS, IDX_REPORTS, elastic
from backend.services.geocoder import geocode
from backend.services.slack import post_alert

logger = logging.getLogger(__name__)

# ── Per-request SSE queue and shared state ─────────────────────────────────────
_stream_queue_var: ContextVar[asyncio.Queue | None] = ContextVar("stream_queue", default=None)
_session_state_var: ContextVar[dict] = ContextVar("session_state", default={})


def _emit(event: dict) -> None:
    """Push an SSE event onto the current request's queue. Never raises."""
    q = _stream_queue_var.get()
    if q is not None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def _step(agent: str, message: str, detail: str = "") -> None:
    _emit({"type": "step", "agent": agent, "message": message, "detail": detail})


def _finding(agent: str, count: int, summary: str) -> None:
    _emit({"type": "finding", "agent": agent, "count": count, "summary": summary})


# ── ADK tool functions ─────────────────────────────────────────────────────────
# Each tool accesses shared session state via _session_state_var (ContextVar).
# Data written here is visible to all subsequent agents in the pipeline.


async def tool_geocode_address(address: str) -> dict:
    """Geocode the property address, identify county/state, and initialise the case file in Elasticsearch."""
    _step("GeocoderAgent", f"Geocoding address: {address}")
    state = _session_state_var.get()
    try:
        geo = await geocode(address)
        state.update(geo)
        _step("GeocoderAgent", f"Located in {geo['county']}, {geo['state']}", f"Coordinates: {geo['lat']:.4f}, {geo['lng']:.4f}")
        _step("GeocoderAgent", f"Data tier: {geo['data_tier'].upper()}, initialising Elastic case file")

        # Write case file to Elastic (memory layer)
        case_doc = {
            **geo,
            "location": {"lat": geo["lat"], "lon": geo["lng"]},  # geo_point for geo_distance
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "analyzing",
        }
        await elastic.index_document(IDX_CASES, geo["address_hash"], case_doc)
        _step("GeocoderAgent", "Case file created in Elasticsearch")
        return geo
    except ValueError as e:
        _step("GeocoderAgent", f"Geocoding failed: {e}")
        return {"error": str(e)}


async def tool_fetch_deed_records(address: str = "", county: str = "", state_name: str = "", **_: str) -> dict:
    """Fetch property deed/sale records from public county APIs and write them to the Elastic memory layer."""
    state = _session_state_var.get()
    if "error" in state:
        return {"skipped": True, "reason": "geocoding failed"}
    _step("DeedAgent", f"Fetching deed & sale records for {state.get('normalized_address', 'property')}")

    geo = {k: state.get(k) for k in [
        "address_hash", "normalized_address", "lat", "lng",
        "county", "state", "data_tier", "house_number", "road", "borough"
    ]}
    events = await fetch_deed_records(geo)

    if events:
        indexed = await elastic.index_events_bulk(events)
        _finding("DeedAgent", len(events), f"{len(events)} deed/sale records indexed ({indexed} written to Elastic)")
    else:
        _finding("DeedAgent", 0, "No deed records found in public APIs for this address")

    state["deed_events"] = events
    return {"deed_record_count": len(events), "source_tier": state.get("data_tier")}


async def tool_fetch_permit_records(address: str = "", county: str = "", state_name: str = "", **_: str) -> dict:
    """Fetch building permit records from public city/county APIs and write them to the Elastic memory layer."""
    state = _session_state_var.get()
    if "error" in state:
        return {"skipped": True, "reason": "geocoding failed"}
    _step("PermitAgent", f"Fetching building permits for {state.get('normalized_address', 'property')}")

    geo = {k: state.get(k) for k in [
        "address_hash", "normalized_address", "lat", "lng",
        "county", "state", "data_tier", "house_number", "road", "borough"
    ]}
    events = await fetch_permit_records(geo)

    open_count = sum(1 for e in events if "open_permit" in e.get("flags", []))
    if events:
        indexed = await elastic.index_events_bulk(events)
        _finding("PermitAgent", len(events),
                 f"{len(events)} permits found · {open_count} still open · {indexed} written to Elastic")
        if open_count > 0:
            _step("PermitAgent", f"[WARN] {open_count} open/unresolved permit(s) flagged", "Verify status before closing")
    else:
        _finding("PermitAgent", 0, "No permit records found for this address")

    state["permit_events"] = events
    state["open_permit_count"] = open_count
    return {"permit_count": len(events), "open_permits": open_count}


async def tool_fetch_climate_risk(address: str = "", lat: float = 0.0, lng: float = 0.0, county: str = "", state_name: str = "", **_: str) -> dict:
    """Fetch FEMA flood zone and USGS earthquake data for this property's coordinates."""
    state = _session_state_var.get()
    if "error" in state:
        return {"skipped": True, "reason": "geocoding failed"}
    lat = state.get("lat")
    lng = state.get("lng")
    _step("ClimateAgent", f"Assessing climate risk at {lat:.4f}, {lng:.4f}")
    _step("ClimateAgent", "Querying FEMA National Flood Hazard Layer (NFHL)")

    geo = {
        "lat": lat,
        "lng": lng,
        "address_hash": state.get("address_hash"),
    }
    risk = await fetch_climate_risk(geo)

    # Write climate events to Elastic memory layer
    climate_events = risk.pop("events", [])
    if climate_events:
        await elastic.index_events_bulk(climate_events)

    fz = risk.get("flood_zone", "UNKNOWN")
    eq_count = risk.get("earthquake_count_nearby", 0)
    max_eq = risk.get("max_earthquake_magnitude", 0)

    _step("ClimateAgent", f"FEMA Flood Zone: {fz} · Risk level: {risk.get('flood_risk', 'UNKNOWN')}")
    _step("ClimateAgent", f"USGS: {eq_count} earthquakes ≥M3.5 within 75km (max M{max_eq})")
    _finding("ClimateAgent", len(climate_events),
             f"Flood zone {fz} · {eq_count} nearby earthquakes · {len(climate_events)} climate events written to Elastic")

    state["climate_risk"] = risk
    return risk


async def tool_fetch_neighborhood(address: str = "", lat: float = 0.0, lng: float = 0.0, county: str = "", state_name: str = "", **_: str) -> dict:
    """Fetch neighborhood intelligence: EPA environmental hazards, air quality, Superfund proximity, and nearby schools/parks/transit via OpenStreetMap."""
    state = _session_state_var.get()
    if "error" in state:
        return {"skipped": True, "reason": "geocoding failed"}

    _step("NeighborhoodAgent", f"Scanning neighbourhood environment for {state.get('normalized_address', 'property')}")
    _step("NeighborhoodAgent", "Querying EPA EJSCREEN, air quality, Superfund sites, toxic facilities")

    geo = {k: state.get(k) for k in ["address_hash", "normalized_address", "lat", "lng", "county", "state"]}
    data = await fetch_neighborhood_data(geo)

    neighborhood_events = data.pop("events", [])
    if neighborhood_events:
        await elastic.index_events_bulk(neighborhood_events)

    pm25      = data.get("pm25", 0)
    superfund = data.get("superfund_proximity", 0)
    schools   = data.get("schools_nearby", 0)
    parks     = data.get("parks_nearby", 0)
    transit   = data.get("transit_nearby", 0)
    n_score   = data.get("neighborhood_score", 60)
    env_flags = data.get("env_flags", [])

    _step("NeighborhoodAgent",
          f"EPA: PM2.5 {pm25:.1f} µg/m³ · Superfund proximity {superfund:.0f}/100",
          "superfund_site_nearby" in env_flags and "[WARN] Superfund site detected within 0.5 miles" or "")
    _step("NeighborhoodAgent",
          f"OSM: {schools} school(s), {parks} park(s), {transit} transit stop(s) nearby")
    _finding("NeighborhoodAgent", len(neighborhood_events),
             f"Neighbourhood score: {n_score}/100 · {len(env_flags)} environmental flag(s) · {len(neighborhood_events)} events written to Elastic")

    state["neighborhood"] = data
    return data


async def tool_debate_analysis(address: str = "", **_: str) -> dict:
    """
    Run a two-sided debate on the property report:
    OptimistAgent argues the risk is overstated; PessimistAgent argues worst-case.
    VerdictAgent weighs both and produces a final confidence-adjusted recommendation.
    """
    state = _session_state_var.get()
    report = state.get("final_report", {})
    if not report:
        return {"skipped": True, "reason": "no report to debate"}

    _step("DebateAgent", "Initiating two-sided risk debate, OptimistAgent vs PessimistAgent")

    score        = report.get("buyer_risk_score", 50)
    summary      = report.get("summary", "")
    flags        = report.get("flags", [])
    positives    = report.get("positive_signals", [])
    neighborhood = state.get("neighborhood", {})
    address_text = report.get("normalized_address", "this property")

    context = f"""Property: {address_text}
Risk Score: {score}/100 ({report.get('risk_level', 'UNKNOWN')})
Summary: {summary}
Risk Flags: {flags}
Positive Signals: {positives}
Neighbourhood Score: {neighborhood.get('neighborhood_score', 'N/A')}/100
PM2.5 Air Quality: {neighborhood.get('pm25', 'N/A')} µg/m³
Schools Nearby: {neighborhood.get('schools_nearby', 'N/A')}
Diligence Questions: {report.get('diligence_questions', [])}"""

    opt_floor = max(0,   score - 25)   # Optimist can argue at most 25 points below synthesis
    pes_ceil  = min(100, score + 25)   # Pessimist can argue at most 25 points above synthesis

    _step("DebateAgent", "OptimistAgent: building the case that risk is overstated")
    optimist_prompt = f"""You are OptimistAgent, a real estate optimist attorney reviewing this property report.
Your job: argue that the risk score of {score}/100 is TOO HIGH. Find every positive signal and explain why each flag may be manageable.

{context}

Your adjusted_score must be between {opt_floor} and {score - 1} (lower than the original, but no more than 25 points below it).

Respond with JSON:
{{"argument": "<2-3 sentence optimistic case>", "adjusted_score": <integer between {opt_floor} and {score - 1}>, "key_positives": ["<3 specific points in favour>"]}}"""

    _step("DebateAgent", "PessimistAgent: building the worst-case scenario")
    pessimist_prompt = f"""You are PessimistAgent, a risk-averse real estate attorney reviewing this property report.
Your job: argue that the risk score of {score}/100 is TOO LOW. Identify worst-case scenarios and hidden liabilities.

{context}

Your adjusted_score must be between {score + 1} and {pes_ceil} (higher than the original, but no more than 25 points above it).

Respond with JSON:
{{"argument": "<2-3 sentence pessimistic case>", "adjusted_score": <integer between {score + 1} and {pes_ceil}>, "key_risks": ["<3 specific worst-case risks>"]}}"""

    optimist_result, pessimist_result = await asyncio.gather(
        gemini.generate_json(optimist_prompt),
        gemini.generate_json(pessimist_prompt),
    )

    opt_score = max(opt_floor, min(score - 1, int(optimist_result.get("adjusted_score", score - 10))))
    pes_score = min(pes_ceil,  max(score + 1, int(pessimist_result.get("adjusted_score", score + 10))))

    _step("DebateAgent",
          f"OptimistAgent scores it {opt_score} · PessimistAgent scores it {pes_score}",
          "VerdictAgent adjudicating…")

    verdict_prompt = f"""You are VerdictAgent, an impartial senior real estate analyst.
Two analysts have debated this property. Weigh their arguments and deliver a final verdict.

ORIGINAL SCORE: {score}/100
OPTIMIST (score: {opt_score}): {optimist_result.get('argument', '')}
PESSIMIST (score: {pes_score}): {pessimist_result.get('argument', '')}

Deliver a final verdict. Respond with JSON:
{{"final_score": <integer 0-100, your considered final score>, "confidence": <"LOW"|"MEDIUM"|"HIGH">, "verdict": "<2-sentence final recommendation to the buyer>", "buy_recommendation": <"BUY"|"NEGOTIATE"|"AVOID">}}"""

    verdict = await gemini.generate_json(verdict_prompt)

    final_score      = verdict.get("final_score", score)
    confidence       = verdict.get("confidence", "MEDIUM")
    buy_rec          = verdict.get("buy_recommendation", "NEGOTIATE")
    verdict_text     = verdict.get("verdict", "")

    _step("DebateAgent",
          f"Verdict: {buy_rec} · Final score {final_score}/100 · Confidence {confidence}",
          verdict_text[:120])
    _finding("DebateAgent", 3,
             f"Debate complete, {buy_rec} recommendation · Confidence: {confidence} · Final score: {final_score}/100")

    debate_result = {
        "optimist": optimist_result,
        "pessimist": pessimist_result,
        "verdict": verdict,
        "final_score": final_score,
        "confidence": confidence,
        "buy_recommendation": buy_rec,
        "verdict_text": verdict_text,
    }

    # Patch the report stored in Elastic with debate results
    report["debate"] = debate_result
    report["buyer_risk_score"] = final_score
    report["buy_recommendation"] = buy_rec
    report["confidence"] = confidence
    report["risk_level"] = _risk_band(final_score)  # keep band consistent with debated score
    address_hash = report.get("address_hash", "")

    # ── Proactive Elastic layer (on the debate-adjusted final score) ──────────
    # Percolator reverse-search: which saved risk profiles does this property match?
    matched_alerts = await elastic.percolate_property(report)
    report.setdefault("elastic_provenance", {})["percolator_matches"] = matched_alerts
    debate_result["percolator_matches"] = matched_alerts  # surfaced live via debate_complete
    await elastic.index_document(IDX_REPORTS, address_hash, report)
    if matched_alerts:
        names = ", ".join(a.get("alert_name", "") for a in matched_alerts)
        _step("DebateAgent", f"Percolator: {len(matched_alerts)} saved risk profile(s) matched", names)

    # Auto-watch high-risk properties in the Elastic memory layer
    if final_score >= 75 and address_hash:
        from backend.routes.watch import IDX_WATCHED
        await elastic.index_document(IDX_WATCHED, address_hash, {
            "address_hash": address_hash,
            "normalized_address": report.get("normalized_address", ""),
            "watched_at": datetime.now(timezone.utc).isoformat(),
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "auto_watched": True,
            "trigger_score": final_score,
        })
        _step("DebateAgent",
              f"Auto-added to Elastic watchlist, final score {final_score} ≥ 75 → 24h monitoring")

    # Fire Slack alert on the debated final score
    if settings.SLACK_WEBHOOK_URL and final_score >= settings.SLACK_ALERT_THRESHOLD:
        await post_alert(report)
        _step("DebateAgent",
              f"Slack alert sent, final score {final_score} ≥ threshold {settings.SLACK_ALERT_THRESHOLD}")

    _emit({"type": "debate_complete", "debate": debate_result})
    state["debate"] = debate_result
    state["final_report"] = report
    return debate_result


async def tool_synthesize_report(address: str = "", **_: str) -> dict:
    """
    Read all property events from Elastic via hybrid search + ES|QL,
    then generate the final Buyer Risk Score, sourced timeline, and diligence questions.
    """
    state = _session_state_var.get()
    address_hash = state.get("address_hash", "")
    normalized_address = state.get("normalized_address", "unknown address")

    _step("SynthesisAgent", "Querying Elastic, hybrid search over all property events")

    # Hybrid retrieval, RRF (BM25 ⊕ ELSER) / MCP / reranker, strongest available.
    # The provenance dict records exactly which Elastic capabilities ran, for the UI.
    query_text = f"property risk permits deed flood {normalized_address}"
    retrieval = await elastic.hybrid_search_events(address_hash, query=query_text)
    all_events = retrieval.get("events", [])
    retrieval_strategy = retrieval.get("strategy", "bm25")
    provenance: dict = {
        "retrieval_strategy": retrieval_strategy,
        "events_retrieved": len(all_events),
        "reranked": retrieval.get("reranked", False),
        "esql_queries": [],          # filled below as each query runs
        "mcp_active": elastic.mcp_available,
    }
    _STRATEGY_LABEL = {
        "elastic_mcp_hybrid": "Elastic Agent Builder MCP hybrid search",
        "rrf_bm25_elser":     "RRF hybrid retriever (BM25 ⊕ ELSER)",
        "semantic_reranker":  "ELSER + semantic reranker",
        "bm25":               "BM25 keyword",
        "unavailable":        "Elastic unavailable",
    }
    _step("SynthesisAgent",
          f"Retrieved {len(all_events)} events via {_STRATEGY_LABEL.get(retrieval_strategy, retrieval_strategy)}")

    def _record_esql(name: str, query: str, rows: list) -> None:
        provenance["esql_queries"].append({
            "name": name,
            "query": " ".join(query.split()),
            "row_count": len(rows) if isinstance(rows, list) else 0,
        })

    # ── ES|QL Query 1: Event type distribution with value aggregates ───────────
    esql_distribution = (
        f'FROM {IDX_EVENTS} '
        f'| WHERE address_hash == "{address_hash}" '
        f'| STATS count = COUNT(*), total_value = SUM(amount), avg_confidence = AVG(confidence) '
        f'BY event_type '
        f'| SORT count DESC'
    )
    type_distribution = await elastic.esql(esql_distribution)
    _record_esql("Event type distribution", esql_distribution, type_distribution)
    _step("SynthesisAgent", f"ES|QL distribution: {len(type_distribution)} event categories found")

    # ── ES|QL Query 2: Permit-sale timing cross-reference ─────────────────────
    # Detects permits that were active during or before a sale (undisclosed construction)
    esql_timing = (
        f'FROM {IDX_EVENTS} '
        f'| WHERE address_hash == "{address_hash}" '
        f'| WHERE event_type IN ("permit", "sale") '
        f'| STATS earliest = MIN(event_date), latest = MAX(event_date), n = COUNT(*) '
        f'BY event_type '
        f'| SORT event_type ASC'
    )
    timing_rows = await elastic.esql(esql_timing)
    _record_esql("Permit–sale timing cross-reference", esql_timing, timing_rows)

    # Parse timing, flag permits that predate or overlap with sales
    permit_timing = next((r for r in timing_rows if r.get("event_type") == "permit"), {})
    sale_timing   = next((r for r in timing_rows if r.get("event_type") == "sale"), {})
    timing_insight = ""
    if permit_timing and sale_timing:
        p_earliest = str(permit_timing.get("earliest", ""))[:10]
        s_latest   = str(sale_timing.get("latest",   ""))[:10]
        p_n = permit_timing.get("n", 0)
        s_n = sale_timing.get("n", 0)
        if p_earliest and s_latest:
            if p_earliest <= s_latest:
                timing_insight = (
                    f"ES|QL PERMIT-SALE CROSS-REFERENCE: {p_n} permit(s) first recorded "
                    f"{p_earliest}; {s_n} sale(s) with latest on {s_latest}. "
                    f"Permits predate or overlap latest sale, verify all permits were "
                    f"closed before deed transfer (a common undisclosed-construction red flag)."
                )
            else:
                timing_insight = (
                    f"ES|QL PERMIT-SALE CROSS-REFERENCE: {p_n} permit(s) first recorded "
                    f"{p_earliest} (after latest sale {s_latest}). "
                    f"Post-sale construction activity detected, may indicate renovation "
                    f"or unpermitted additions by current owner."
                )
    if timing_insight:
        _step("SynthesisAgent", "Permit-sale timing anomaly detected via ES|QL", timing_insight[:120])

    # ── ES|QL Query 3: High-confidence risk events ─────────────────────────────
    esql_high_risk = (
        f'FROM {IDX_EVENTS} '
        f'| WHERE address_hash == "{address_hash}" AND confidence >= 0.9 '
        f'| STATS verified_events = COUNT(*), max_value = MAX(amount) BY event_type '
        f'| SORT verified_events DESC'
    )
    high_confidence = await elastic.esql(esql_high_risk)
    _record_esql("High-confidence events (≥0.9)", esql_high_risk, high_confidence)

    # ── ES|QL Query 4: Semantic RERANK, top risk events via Elastic built-in reranker ──
    # RERANK reorders events by semantic similarity to the risk query, surfacing
    # the most contextually relevant findings for Gemini synthesis.
    top_risk_events: list[dict] = []
    esql_rerank = (
        f'FROM {IDX_EVENTS} '
        f'| WHERE address_hash == "{address_hash}" '
        f'| RERANK "undisclosed construction flood earthquake permit violation hazard code" ON description '
        f'  WITH {{"inference_id": ".rerank-v1-elasticsearch"}} '
        f'| KEEP event_type, event_date, description, confidence, flags '
        f'| LIMIT 5'
    )
    try:
        top_risk_events = await elastic.esql(esql_rerank)
        _record_esql("Semantic RERANK top-risk events", esql_rerank, top_risk_events)
        if top_risk_events:
            _step("SynthesisAgent",
                  f"ES|QL RERANK: {len(top_risk_events)} highest-risk events surfaced via semantic reranking")
    except Exception:
        pass  # RERANK requires Elastic 9.3+ / Serverless, graceful fallback

    _step("SynthesisAgent",
          f"ES|QL cross-reference complete, {len(type_distribution)} categories · "
          f"timing analysis · {len(high_confidence)} high-confidence types · "
          f"{len(top_risk_events)} semantically reranked top-risk events")

    # ── ES|QL Query 5: Flip fraud pattern detection ───────────────────────────
    # Detects rapid repeated sales, a hallmark of renovation fraud, distress
    # flips, and title fraud schemes. Unique intelligence Elastic enables.
    esql_flips = (
        f'FROM {IDX_EVENTS} '
        f'| WHERE address_hash == "{address_hash}" AND event_type == "sale" '
        f'| STATS sale_count = COUNT(*), '
        f'  first_sale = MIN(event_date), last_sale = MAX(event_date) '
        f'| WHERE sale_count >= 2'
    )
    flip_flag = ""
    try:
        flip_rows = await elastic.esql(esql_flips)
        _record_esql("Flip-fraud detection", esql_flips, flip_rows)
        if flip_rows:
            row = flip_rows[0]
            sale_count = row.get("sale_count", 0)
            first_sale = str(row.get("first_sale", ""))[:10]
            last_sale  = str(row.get("last_sale", ""))[:10]
            if sale_count >= 3:
                flip_flag = (
                    f"{sale_count} deed transfers recorded between {first_sale} and {last_sale}: "
                    f"rapid flip pattern, possible distress sale or renovation fraud"
                )
                _step("SynthesisAgent",
                      f"ES|QL FLIP DETECTION: {sale_count} sales in records, pattern flagged")
            elif sale_count == 2:
                flip_flag = f"2 deed transfers between {first_sale} and {last_sale}, verify legitimacy"
    except Exception:
        pass

    # Compute flags from accumulated state
    flags = []
    open_permits = state.get("open_permit_count", 0)
    if open_permits > 0:
        flags.append(f"{open_permits} open/unresolved building permit(s)")
    climate = state.get("climate_risk", {})
    if climate.get("sfha"):
        flags.append(f"FEMA Special Flood Hazard Area (Zone {climate.get('flood_zone')})")
    if climate.get("max_earthquake_magnitude", 0) >= 5.0:
        flags.append(f"M{climate['max_earthquake_magnitude']} earthquake recorded within 75km")
    deed_events = [e for e in all_events if e.get("event_type") == "sale"]
    arm_sales = [e for e in deed_events if "arm_sale" in e.get("flags", [])]
    if arm_sales:
        flags.append(f"{len(arm_sales)} arms-length sale anomaly/anomalies detected")
    if flip_flag:
        flags.append(flip_flag)

    _step("SynthesisAgent", f"Risk flags identified: {len(flags)}")

    # Build timeline entries (sorted by date, most recent first)
    timeline = _build_timeline(all_events)
    _step("SynthesisAgent", f"Timeline built: {len(timeline)} dated events")

    _step("SynthesisAgent", "Calling Gemini to generate Buyer Risk Score and diligence questions")

    # Gemini synthesis
    report = await _gemini_synthesize(
        normalized_address=normalized_address,
        geo=state,
        all_events=all_events,
        flags=flags,
        timeline=timeline,
        type_distribution=type_distribution,
        timing_insight=timing_insight,
        high_confidence=high_confidence,
        top_risk_events=top_risk_events,
    )

    # ── Finalise Elastic provenance: geo cross-reference + significant terms ──
    risk_level     = report.get("risk_level", "UNKNOWN")
    flood_zone_top = (state.get("climate_risk", {}) or {}).get("flood_zone", "") or ""
    lat, lng = state.get("lat"), state.get("lng")
    nearby   = await elastic.geo_search_nearby(lat, lng, radius_km=50, exclude_hash=address_hash, size=6)
    sig_flags = await elastic.significant_flags(risk_level)
    provenance["geo_nearby_count"]        = len(nearby)
    provenance["geo_nearby"]              = nearby
    provenance["significant_terms_count"] = len(sig_flags)
    provenance["significant_terms"]       = sig_flags
    provenance["indices_written"]         = [IDX_CASES, IDX_EVENTS, IDX_REPORTS]
    if nearby:
        _step("SynthesisAgent",
              f"geo_distance: {len(nearby)} previously-analysed propert(y/ies) within 50 km cross-referenced")

    # Write final report to Elastic
    # NOTE: geo + neighborhood + climate fields are included so the SSE "complete"
    # event has everything the frontend needs in one payload, no second API call.
    report_doc = {
        "case_id": address_hash,
        "address_hash": address_hash,
        "normalized_address": normalized_address,
        "display_address": state.get("display_address", normalized_address),
        "lat": lat,
        "lng": lng,
        "location": ({"lat": lat, "lon": lng} if lat is not None and lng is not None else None),
        "county": state.get("county", ""),
        "state": state.get("state", ""),
        "flood_zone": flood_zone_top,
        "data_tier": state.get("data_tier", "generic"),
        "buyer_risk_score": report.get("buyer_risk_score", 50),
        "risk_level": report.get("risk_level", "UNKNOWN"),
        "flags": flags,
        "timeline": timeline,
        "diligence_questions": report.get("diligence_questions", []),
        "summary": report.get("summary", ""),
        "data_sources": report.get("data_sources", []),
        "positive_signals": report.get("positive_signals", []),
        "escape_plan": report.get("escape_plan", []),
        "neighborhood": state.get("neighborhood", {}),
        "climate_risk": state.get("climate_risk", {}),
        "esql_insights": {
            "type_distribution": type_distribution,
            "timing_insight": timing_insight,
            "high_confidence_events": len(high_confidence),
            "top_risk_events": top_risk_events,
        },
        "elastic_provenance": provenance,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await elastic.index_document(IDX_REPORTS, address_hash, report_doc)
    _step("SynthesisAgent", "Final report written to Elasticsearch")

    # NOTE: auto-watch + Slack alerting are deferred to DebateAgent so they fire on
    # the debate-adjusted final score (not the pre-debate synthesis score).

    _emit({"type": "complete", "report": report_doc})

    state["final_report"] = report_doc
    return report_doc


def _risk_band(score: int) -> str:
    """Map a 0-100 score to the user-facing band (matches /api/about score_bands)."""
    if score <= 30:
        return "LOW"
    if score <= 60:
        return "MEDIUM"
    if score <= 80:
        return "HIGH"
    return "CRITICAL"


def _build_timeline(events: list[dict]) -> list[dict]:
    """Sort and format events into a timeline suitable for the frontend."""
    clean = []
    for e in events:
        date = e.get("event_date")
        if not date:
            continue
        clean.append({
            "date": str(date)[:10],
            "event_type": e.get("event_type", "unknown"),
            "description": e.get("description", ""),
            "amount": e.get("amount"),
            "source": e.get("source", ""),
            "confidence": e.get("confidence", 0.8),
            "flags": e.get("flags", []),
        })
    clean.sort(key=lambda x: x["date"], reverse=True)
    return clean


async def _gemini_synthesize(
    normalized_address: str,
    geo: dict,
    all_events: list[dict],
    flags: list[str],
    timeline: list[dict],
    type_distribution: list[dict],
    timing_insight: str = "",
    high_confidence: list[dict] | None = None,
    top_risk_events: list[dict] | None = None,
) -> dict:
    """Call Gemini to produce a structured synthesis report."""
    events_summary = json.dumps(
        [{"date": e["date"], "type": e["event_type"], "desc": e["description"], "flags": e["flags"]}
         for e in timeline[:20]],
        indent=2,
    )
    flags_text = "\n".join(f"  • {f}" for f in flags) if flags else "  None detected"
    county = geo.get("county", "")
    state_name = geo.get("state", "")
    flood_zone = geo.get("climate_risk", {}).get("flood_zone", "UNKNOWN")
    esql_section = f"\nES|QL PERMIT-SALE TIMING CROSS-REFERENCE:\n{timing_insight}" if timing_insight else ""
    hc_section = f"\nHIGH-CONFIDENCE EVENTS (ES|QL):\n{json.dumps(high_confidence or [])}" if high_confidence else ""
    rerank_section = (
        f"\nSEMANTICALLY RERANKED TOP-RISK EVENTS (Elastic .rerank-v1-elasticsearch):\n"
        f"{json.dumps(top_risk_events or [])}"
    ) if top_risk_events else ""

    prompt = f"""You are a property due-diligence analyst. A buyer is considering purchasing property at:

{normalized_address} ({county}, {state_name})

EVENTS FROM PUBLIC RECORDS, retrieved via Elastic ELSER hybrid search (most recent first):
{events_summary}

RISK FLAGS:
{flags_text}

FEMA FLOOD ZONE: {flood_zone}
EVENT TYPE DISTRIBUTION (ES|QL): {json.dumps(type_distribution)}{esql_section}{hc_section}{rerank_section}

Your task: produce a JSON report with this exact schema:
{{
  "buyer_risk_score": <integer 0-100, where 100 = maximum risk>,
  "risk_level": <"LOW" | "MEDIUM" | "HIGH" | "CRITICAL">,
  "summary": <2-3 sentence plain-English summary of the key findings>,
  "diligence_questions": [<3 specific questions this buyer should ask before closing>],
  "data_sources": [<list of source names used>],
  "positive_signals": [<any positive findings, e.g. long ownership history, renovated permits resolved>],
  "escape_plan": [<list of 3-5 specific, actionable steps ranked by risk-reduction impact, each as a string like "Resolve 30 open permits → estimated -25 risk points (HIGH → MEDIUM)">]
}}

Scoring guide — use these bands exactly, they must match risk_level:
  0-30   = LOW:      clean records, minor items only
  31-60  = MEDIUM:   one or two open items worth verifying; no major hazards
  61-80  = HIGH:     multiple compounding flags or significant unresolved liability
  81-100 = CRITICAL: severe or multiple hazards; major legal/financial exposure

Anchor rules — apply these FIRST before adjusting for context:
  - Each open/unresolved building permit: +2 points (e.g. 30 permits = minimum score 60, start HIGH)
  - FEMA Zone AE (Special Flood Hazard Area): add 30 points floor
  - Superfund site within 1 mile: add 25 points floor
  - Quitclaim deed or price drop >30% in <12 months: add 15 points
  - FEMA Zone X with no other flags: base score 10-20 (LOW)
  - No flags at all: score 5-15

Apply anchor rules first, then adjust up or down by max 15 points for mitigating or compounding context.

Be specific, factual, and source-referenced. Use "public records show..." and "FEMA data indicates..." rather than vague language.
"""
    result = await gemini.generate_json(prompt)
    if not isinstance(result, dict) or "buyer_risk_score" not in result:
        # Build a safe fallback
        score = min(100, max(0, len(flags) * 20))
        return {
            "buyer_risk_score": score,
            "risk_level": "HIGH" if score > 50 else "MEDIUM" if score > 25 else "LOW",
            "summary": f"Analysis complete for {normalized_address}. {len(all_events)} public records retrieved.",
            "diligence_questions": [
                "Request all building permit completion certificates from the city.",
                "Obtain a current flood elevation certificate from a licensed surveyor.",
                "Ask the seller for full disclosure of any known structural or legal issues.",
            ],
            "data_sources": list({e.get("source", "") for e in all_events if e.get("source")}),
            "positive_signals": [],
            "escape_plan": [
                "Resolve any open building permits with the city permit office",
                "Obtain flood elevation certificate from a licensed surveyor",
                "Commission a full structural inspection before closing",
            ],
        }
    return result


# ── ADK agent definitions ──────────────────────────────────────────────────────

_GEOCODER_INSTRUCTION = """You are the GeocoderAgent for BLUEPRINT, an AI property due-diligence system.

Your job is to geocode the input address, identify its county and state, and initialise the property case file.

Call tool_geocode_address with the address provided by the user. Confirm the result and report the county, state, and data tier identified.

The user provided this address: {input_address}
"""

_DEED_INSTRUCTION = """You are the DeedAgent for BLUEPRINT.

The property has been geocoded:
{geocoder_result}

Your job is to fetch all available deed and sale records for this property from public county APIs, then write the findings to the Elastic memory layer.

Call tool_fetch_deed_records. Report how many records were found and note any anomalies like very low sale prices.
"""

_PERMIT_INSTRUCTION = """You are the PermitAgent for BLUEPRINT.

The property has been geocoded:
{geocoder_result}

Your job is to fetch all building permit records for this property, then write them to the Elastic memory layer.

Call tool_fetch_permit_records. Report how many permits were found, specifically flag any permits that are still open or unresolved.
"""

_CLIMATE_INSTRUCTION = """You are the ClimateAgent for BLUEPRINT.

The property has been geocoded:
{geocoder_result}

Your job is to assess climate and natural disaster risk at this property's coordinates using FEMA and USGS data, then write the findings to the Elastic memory layer.

Call tool_fetch_climate_risk. Report the FEMA flood zone designation and any notable earthquake history.
"""

_NEIGHBORHOOD_INSTRUCTION = """You are the NeighborhoodAgent for BLUEPRINT.

The property has been geocoded:
{geocoder_result}

Your job is to assess the surrounding environment using EPA and OpenStreetMap data:
- EPA EJSCREEN: air quality (PM2.5), proximity to Superfund toxic sites, traffic pollution
- OpenStreetMap: schools, parks, and transit stops within walking distance

Call tool_fetch_neighborhood. Report the neighbourhood score, any environmental hazards found, and the quality of nearby amenities.
"""

_DEBATE_INSTRUCTION = """You are the DebateAgent for BLUEPRINT, the final quality-control agent.

Previous agents have completed a full property analysis:
- Geocoding: {geocoder_result}
- Deed records: {deed_result}
- Permit records: {permit_result}
- Climate risk: {climate_result}
- Neighbourhood: {neighborhood_result}
- Synthesis report: {synthesis_result}

Your job is to run a rigorous two-sided debate on the risk assessment:
1. OptimistAgent argues the risk score is too high (finds positive signals)
2. PessimistAgent argues the risk score is too low (finds worst-case scenarios)
3. VerdictAgent weighs both and delivers a final buy/negotiate/avoid recommendation

Call tool_debate_analysis. This produces the most trustworthy, defensible final output.
"""

_SYNTHESIS_INSTRUCTION_MCP = """You are the SynthesisAgent for BLUEPRINT.

Previous agents have completed:
- Geocoding: {geocoder_result}
- Deed records: {deed_result}
- Permit records: {permit_result}
- Climate risk: {climate_result}
- Neighbourhood intelligence: {neighborhood_result}

You have access to two categories of tools:

1. ELASTIC AGENT BUILDER TOOLS via MCP (call these first):
   - platform.core.search         , hybrid ELSER semantic + BM25 search over property events
   - platform.core.execute_esql   , execute ES|QL queries against Elasticsearch
   - platform.core.generate_esql  , generate ES|QL from natural language, then execute
   - blueprint_flip_fraud         , detect rapid deed transfers (flip-fraud pattern)
   - blueprint_permit_sale_timing , cross-reference permit vs. sale timing
   - blueprint_top_risk_events    , semantically reranked top risk events

   Use platform.core.search to retrieve property events for this address_hash.
   Use platform.core.execute_esql for cross-references.
   Call blueprint_* tools with the address_hash from the geocoding result.

2. PIPELINE SYNTHESIS TOOL:
   - tool_synthesize_report, reads all findings from Elastic, runs ES|QL cross-reference
     analysis, and generates the Buyer Risk Score, timeline, diligence questions, Escape Plan.

Workflow: call the Elastic Agent Builder tools first, then call tool_synthesize_report.
"""

# Fallback instruction used when Elastic Agent Builder MCP is not reachable.
# All ES|QL cross-referencing still runs inside tool_synthesize_report via the direct SDK.
_SYNTHESIS_INSTRUCTION_NO_MCP = """You are the SynthesisAgent for BLUEPRINT.

Previous agents have completed:
- Geocoding: {geocoder_result}
- Deed records: {deed_result}
- Permit records: {permit_result}
- Climate risk: {climate_result}
- Neighbourhood intelligence: {neighborhood_result}

Your job is to query the Elastic memory layer for all findings, perform ES|QL cross-reference
analysis (RRF hybrid retrieval, permit-sale timing, flip-fraud detection, semantic RERANK),
and generate the final Buyer Risk Score, property intelligence report, and Escape Plan.

Call tool_synthesize_report. This runs the full ES|QL pipeline and produces the complete
report including risk score, timeline, diligence questions, and the Escape Plan.
"""

_MODEL = settings.GEMINI_MODEL

# The three custom Agent Builder tools BLUEPRINT provisioned into Elastic Agent Builder.
# Gemini will choose to call them by name over MCP once the toolset is in its tool list.
_ELASTIC_MCP_TOOLS = [
    # Agent Builder platform tools (confirmed in Kibana tool library)
    "platform.core.search",          # hybrid ELSER + BM25 search over any index
    "platform.core.execute_esql",    # execute ES|QL queries
    "platform.core.generate_esql",   # natural-language → ES|QL query generation
    # Custom BLUEPRINT tools (added manually in Kibana Agent Builder Tools)
    "blueprint_flip_fraud",
    "blueprint_permit_sale_timing",
    "blueprint_top_risk_events",
]


def _elastic_mcp_toolset() -> MCPToolset | None:
    """Build an MCPToolset pointing at Elastic Agent Builder.

    Only attached when MCP was successfully reachable at startup.
    Returns None otherwise so the pipeline falls back cleanly to FunctionTool-only.
    The toolset is scoped to the three custom BLUEPRINT ES|QL tools.
    Compatible with google-adk >= 1.3.0 (local dev) and 2.0.0 (pinned deploy).
    """
    if not settings.ELASTIC_MCP_URL or not settings.ELASTIC_API_KEY:
        return None
    # Guard: don't attach if MCP ping failed at startup, attaching an unreachable
    # MCPToolset causes the ADK Runner to throw when it initialises the agent.
    if not elastic.mcp_available:
        return None
    try:
        return MCPToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=settings.ELASTIC_MCP_URL,
                headers={"Authorization": f"ApiKey {settings.ELASTIC_API_KEY}"},
                timeout=30.0,
                sse_read_timeout=120.0,
            ),
            tool_filter=_ELASTIC_MCP_TOOLS,
        )
    except Exception as exc:
        logger.warning("[ADK] Could not build Elastic MCPToolset: %s, MCP tools unavailable", exc)
        return None


def _build_agents() -> SequentialAgent:
    # Build the Elastic Agent Builder MCPToolset once (or None if not configured).
    # When present, SynthesisAgent sees the custom ES|QL tools alongside its
    # FunctionTool, and Gemini can call them by name over MCP.
    elastic_toolset = _elastic_mcp_toolset()
    if elastic_toolset:
        logger.info("[ADK] Elastic Agent Builder MCPToolset attached to SynthesisAgent "
                    "(tools: %s)", ", ".join(_ELASTIC_MCP_TOOLS))

    geocoder = LlmAgent(
        name="GeocoderAgent",
        model=_MODEL,
        instruction=_GEOCODER_INSTRUCTION,
        tools=[FunctionTool(tool_geocode_address)],
        output_key="geocoder_result",
    )
    deed = LlmAgent(
        name="DeedAgent",
        model=_MODEL,
        instruction=_DEED_INSTRUCTION,
        tools=[FunctionTool(tool_fetch_deed_records)],
        output_key="deed_result",
    )
    permit = LlmAgent(
        name="PermitAgent",
        model=_MODEL,
        instruction=_PERMIT_INSTRUCTION,
        tools=[FunctionTool(tool_fetch_permit_records)],
        output_key="permit_result",
    )
    climate = LlmAgent(
        name="ClimateAgent",
        model=_MODEL,
        instruction=_CLIMATE_INSTRUCTION,
        tools=[FunctionTool(tool_fetch_climate_risk)],
        output_key="climate_result",
    )
    neighborhood = LlmAgent(
        name="NeighborhoodAgent",
        model=_MODEL,
        instruction=_NEIGHBORHOOD_INSTRUCTION,
        tools=[FunctionTool(tool_fetch_neighborhood)],
        output_key="neighborhood_result",
    )
    # SynthesisAgent: FunctionTool always present; Elastic MCPToolset added when
    # Agent Builder MCP is reachable so Gemini can call the custom ES|QL tools by name.
    # The instruction is switched accordingly, Gemini must not reference tool names
    # that aren't registered in tools_dict or ADK will raise ValueError at runtime.
    synthesis_tools = [FunctionTool(tool_synthesize_report)]
    if elastic_toolset is not None:
        synthesis_tools.append(elastic_toolset)
        synthesis_instruction = _SYNTHESIS_INSTRUCTION_MCP
    else:
        synthesis_instruction = _SYNTHESIS_INSTRUCTION_NO_MCP

    synthesis = LlmAgent(
        name="SynthesisAgent",
        model=_MODEL,
        instruction=synthesis_instruction,
        tools=synthesis_tools,
        output_key="synthesis_result",
    )
    debate = LlmAgent(
        name="DebateAgent",
        model=_MODEL,
        instruction=_DEBATE_INSTRUCTION,
        tools=[FunctionTool(tool_debate_analysis)],
        output_key="final_report",
    )
    return SequentialAgent(
        name="BlueprintPipeline",
        sub_agents=[geocoder, deed, permit, climate, neighborhood, synthesis, debate],
    )


# ── Public runner API ──────────────────────────────────────────────────────────

_session_service = InMemorySessionService()
_runner: Runner | None = None


def _get_runner() -> Runner:
    global _runner
    if _runner is None:
        pipeline = _build_agents()
        _runner = Runner(
            agent=pipeline,
            app_name="blueprint",
            session_service=_session_service,
        )
    return _runner


async def run_analysis(address: str, stream_queue: asyncio.Queue | None = None) -> dict:
    """
    Run the full 7-agent BLUEPRINT pipeline for the given address.
    If stream_queue is provided, SSE events are pushed to it in real-time.
    Returns the final report dict (or an error dict).
    """
    session_id = hashlib.md5(f"{address}{datetime.now().isoformat()}".encode()).hexdigest()[:16]
    shared_state: dict = {}

    # Bind SSE queue and shared state to this request's context
    queue_token = _stream_queue_var.set(stream_queue)
    state_token = _session_state_var.set(shared_state)

    try:
        _emit({"type": "start", "address": address, "session_id": session_id})
        runner = _get_runner()

        session = await _session_service.create_session(
            app_name="blueprint",
            user_id="blueprint-user",
            session_id=session_id,
            state={"input_address": address},
        )

        user_message = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=address)],
        )

        async for _event in runner.run_async(
            user_id="blueprint-user",
            session_id=session_id,
            new_message=user_message,
        ):
            pass  # Events are emitted from tool functions via _emit()

        final = shared_state.get("final_report", {})
        if not final:
            final = {"error": "Pipeline completed but no report was generated."}
        return final

    except Exception as e:
        logger.exception("Blueprint pipeline failed for address %r", address)
        _emit({"type": "error", "message": str(e)})
        return {"error": str(e)}
    finally:
        _stream_queue_var.reset(queue_token)
        _session_state_var.reset(state_token)
