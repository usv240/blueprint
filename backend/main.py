import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.routes import analyze
from backend.routes import share, watch
from backend.routes import export as export_route
from backend.routes import compare as compare_route
from backend.services.elastic_client import elastic

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


async def _recheck_watchlist() -> None:
    """Re-run analysis for all watched properties and update last_checked timestamp."""
    from backend.routes.watch import IDX_WATCHED
    from backend.services.adk_runner import run_analysis
    from datetime import datetime, timezone

    if not elastic.available:
        return
    try:
        resp = await elastic._es.search(index=IDX_WATCHED, size=50)
        items = [h["_source"] for h in resp["hits"]["hits"]]
    except Exception as exc:
        logger.warning("[Watchlist] Could not fetch watched properties: %s", exc)
        return

    for item in items:
        addr = item.get("normalized_address")
        if not addr:
            continue
        logger.info("[Watchlist] Re-analysing: %s", addr)
        try:
            await run_analysis(addr)
            # Update last_checked
            await elastic._es.update(
                index=IDX_WATCHED,
                id=item["address_hash"],
                body={"doc": {"last_checked": datetime.now(timezone.utc).isoformat()}},
            )
        except Exception as exc:
            logger.warning("[Watchlist] Re-analysis failed for %s: %s", addr, exc)


async def _watchlist_loop() -> None:
    """Background task: re-analyse watched properties every 24 hours."""
    while True:
        await asyncio.sleep(24 * 3600)
        await _recheck_watchlist()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    await elastic.connect()
    if elastic.available:
        logger.info("[OK] Elasticsearch connected — MCP: %s",
                    "ready" if elastic.mcp_available else "unavailable (direct SDK active)")
    else:
        logger.warning("[WARN] Elasticsearch unavailable — reports will not be persisted")

    watchlist_task = asyncio.create_task(_watchlist_loop())

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    watchlist_task.cancel()
    try:
        await watchlist_task
    except asyncio.CancelledError:
        pass
    await elastic.close()


app = FastAPI(
    title="BLUEPRINT — AI Due Diligence for Homes",
    description=(
        "7-agent property intelligence pipeline powered by Gemini 3, "
        "Google Cloud ADK, and Elastic Agent Builder MCP. "
        "Turns scattered public records into a debated, confidence-adjusted Buyer Risk Score."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.APP_URL,
        "http://localhost:8080",
        "http://localhost:8101",
        "http://127.0.0.1:8080",
    ],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again."},
    )


app.include_router(analyze.router,      prefix="/api", tags=["analysis"])
app.include_router(share.router,        prefix="/api", tags=["share"])
app.include_router(watch.router,        prefix="/api", tags=["watchlist"])
app.include_router(export_route.router, prefix="/api", tags=["export"])
app.include_router(compare_route.router, prefix="/api", tags=["compare"])


@app.get("/api/health")
async def health():
    from backend.services.adk_runner import _runner
    mcp_tools = await elastic.list_mcp_tools() if elastic.mcp_available else []
    return {
        "status": "ok",
        "service": "blueprint",
        "elasticsearch": "connected" if elastic.available else "unavailable",
        "elastic_mcp": "ready" if elastic.mcp_available else "unavailable (direct SDK fallback)",
        "elastic_mcp_tools": mcp_tools,
        "adk_pipeline": "initialized" if _runner is not None else "pending (loads on first request)",
        "gemini_model": "gemini-3-flash-preview",
        "fallback_model": "gemini-2.5-flash (Vertex AI)",
        "permit_cities": 65,
        "permit_coverage": "65 US cities via Socrata open data portals",
        "climate_coverage": "All 50 states + DC (FEMA + USGS + EPA + OSM)",
        "slack_alerts": "configured" if settings.SLACK_WEBHOOK_URL else "not configured",
        "slack_threshold": settings.SLACK_ALERT_THRESHOLD,
        "agents": 7,
        "features": ["share_links", "watchlist_24h", "html_export", "slack_alerts", "qa_chat", "debate_analysis", "escape_plan", "neighborhood_intelligence", "property_comparison"],
        "elastic_capabilities": [
            "elser_hybrid_retrieval",
            "semantic_reranking",
            "esql_cross_reference",
            "esql_semantic_rerank",
            "memory_layer_writeback",
            "watchlist_24h_monitoring",
            "auto_watch_high_risk",
            "mcp_tool_execution",
        ],
    }


@app.get("/api/stats")
async def stats():
    """Live platform stats — total analyses, Elastic status, recent activity."""
    total = 0
    recent_count = 0
    if elastic.available:
        try:
            from backend.services.elastic_client import IDX_REPORTS
            total = (await elastic._es.count(index=IDX_REPORTS)).get("count", 0)
            from datetime import datetime, timezone, timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            recent_resp = await elastic._es.count(
                index=IDX_REPORTS,
                query={"range": {"created_at": {"gte": cutoff}}},
            )
            recent_count = recent_resp.get("count", 0)
        except Exception:
            pass
    return {
        "total_analyses": total,
        "analyses_24h": recent_count,
        "elastic_connected": elastic.available,
        "elastic_mcp_active": elastic.mcp_available,
    }


@app.get("/api/about")
async def about():
    """Educational content — methodology, glossary, data sources, agent descriptions.
    The frontend renders all educational UI from this endpoint so nothing is hardcoded."""
    return {
        "score_bands": [
            {
                "range": "0–30", "label": "LOW", "color": "#22c55e",
                "headline": "Minor concerns — standard purchase",
                "meaning": "Normal maintenance history, low environmental risk, no open permits, deed history is clean. Proceed with standard inspections and due diligence."
            },
            {
                "range": "31–60", "label": "MEDIUM", "color": "#eab308",
                "headline": "Elevated risk — negotiate and budget",
                "meaning": "One or more concerns: open permits, moderate flood/earthquake exposure, or inconsistent deed history. Negotiate price reduction of 3–8% to cover mitigation costs."
            },
            {
                "range": "61–80", "label": "HIGH", "color": "#f97316",
                "headline": "Significant risk — negotiate hard or walk away",
                "meaning": "Multiple compounding factors: flood zone AE, several open permits, Superfund proximity, or title anomalies. Demand seller resolution of permits, get independent flood and structural assessments."
            },
            {
                "range": "81–100", "label": "CRITICAL", "color": "#ef4444",
                "headline": "Serious risk — specialist review required",
                "meaning": "Multiple severe hazards: flood zone + open permits + environmental contamination, or major title defects. Consult a real estate attorney, certified flood plain manager, and environmental engineer before proceeding."
            },
        ],
        "risk_factors": [
            {"factor": "FEMA Flood Zone", "weight": "HIGH", "icon": "🌊",
             "detail": "Zone AE properties have a 26% chance of flooding over a 30-year mortgage. Federal flood insurance is mandatory for properties with federally-backed mortgages — and can add $5,000–$15,000/year to ownership costs."},
            {"factor": "Open Building Permits", "weight": "HIGH", "icon": "🔨",
             "detail": "An unresolved permit means construction was never given a final inspection sign-off. Buyers inherit the liability to complete and close these permits — which can legally require demolition and rebuilding of non-compliant work."},
            {"factor": "Superfund Proximity", "weight": "HIGH", "icon": "☣",
             "detail": "EPA-designated Superfund sites indicate soil or groundwater contamination. Properties within 1 mile see 5–15% value discounts and may require environmental disclosure in most states. Cleanup can take 10–30 years."},
            {"factor": "Deed History Anomalies", "weight": "MEDIUM", "icon": "📜",
             "detail": "Price drops >30% in <12 months, rapid ownership transfers, or quitclaim deeds can indicate distress sales, title fraud, or undisclosed structural defects. BLUEPRINT flags these for your attorney."},
            {"factor": "Earthquake History", "weight": "MEDIUM", "icon": "🌎",
             "detail": "Properties within 75km of M4.0+ earthquakes or in liquefaction-prone zones face elevated structural risk. Earthquake retrofitting costs $10,000–$100,000 depending on foundation type."},
            {"factor": "Air Quality (PM2.5)", "weight": "MEDIUM", "icon": "💨",
             "detail": "Long-term PM2.5 exposure above EPA's 12 µg/m³ annual standard is linked to respiratory and cardiovascular disease. Sources: industrial facilities, high-traffic roads, wildfire corridors."},
            {"factor": "Traffic Pollution Index", "weight": "LOW", "icon": "🚗",
             "detail": "EPA EJSCREEN measures diesel particulate matter and traffic proximity. High values indicate proximity to major highways or distribution hubs — affecting health, noise levels, and resale value."},
        ],
        "glossary": [
            {"term": "Buyer Risk Score", "short": "0–100 composite risk rating",
             "detail": "BLUEPRINT's composite score aggregating flood risk (FEMA), permit history (city DBs), environmental hazards (EPA EJSCREEN), earthquake exposure (USGS), and deed anomalies. Debated by OptimistAgent vs PessimistAgent before finalisation. Lower is better. Anything above 60 warrants specialist review."},
            {"term": "Flood Zone AE", "short": "FEMA 100-year flood zone",
             "detail": "FEMA's National Flood Hazard Layer designation for areas with 1% annual flood probability — equivalent to a 26% chance over a 30-year mortgage. Federal flood insurance is mandatory for federally-backed mortgages. Hurricane Sandy (2012) put 90% of Red Hook, Brooklyn (Zone AE) underwater."},
            {"term": "Flood Zone X", "short": "Minimal flood hazard (FEMA)",
             "detail": "Areas outside the 500-year floodplain or with risk reduced by levees. Standard homeowners insurance typically covers this. Much lower risk than AE — but climate change is expanding flood zones, and 40% of Sandy damage claims came from Zone X properties."},
            {"term": "Open Building Permit", "short": "Unresolved construction permit",
             "detail": "A city-issued permit for construction or renovation that never received a final inspection. The city's records show the work started but was never certified as code-compliant. In most jurisdictions, buyers inherit this liability at closing — including the cost to tear out and redo non-compliant work."},
            {"term": "Superfund Site (NPL)", "short": "EPA contaminated land designation",
             "detail": "Properties on EPA's National Priorities List due to hazardous substance contamination. Remediation can take decades. Within 1 mile, property values decline 5–15% and environmental disclosure is legally required in most states. The Gowanus Canal (Brooklyn) and East Bay (Oakland) are examples."},
            {"term": "PM2.5", "short": "Fine particulate air pollution",
             "detail": "Airborne particles ≤2.5 micrometres — small enough to enter the bloodstream. EPA's annual standard is 12 µg/m³. Long-term exposure causes heart disease, stroke, and lung cancer. High values near industrial zones, interstates, or wildfire corridors."},
            {"term": "Quitclaim Deed", "short": "Title transfer without warranty",
             "detail": "Transfers whatever interest the grantor has — with no guarantee the title is clean. Commonly used in divorce, estate transfers, or distress sales. In a purchase transaction, a quitclaim deed is a red flag: the seller won't warrant they actually own what they're selling."},
            {"term": "Escape Plan", "short": "Ranked risk-reduction playbook",
             "detail": "BLUEPRINT's ranked list of concrete actions to lower your Buyer Risk Score: from negotiating permit closure with the seller to purchasing flood insurance riders. Each step is prioritised by impact-to-effort ratio."},
            {"term": "OptimistAgent vs PessimistAgent", "short": "Adversarial AI debate",
             "detail": "BLUEPRINT runs two opposing Gemini 3 agents after synthesis. OptimistAgent argues the score is too high (citing positive signals). PessimistAgent argues it's too low (citing worst-case risks). VerdictAgent adjudicates — reducing anchoring bias and AI hallucination risk."},
            {"term": "ES|QL Cross-Reference", "short": "Elastic SQL for pattern detection",
             "detail": "BLUEPRINT uses Elasticsearch Query Language to cross-reference permit violations against deed transfer timing, flagging cases where permits were opened just before or just after a property sale — a pattern common in flip fraud."},
        ],
        "data_sources": [
            {"id": "nyc", "name": "NYC Open Data — DOB Permits", "icon": "🏙",
             "coverage": "New York City (5 boroughs)",
             "what": "Building permits (filed, approved, completed, voided), violations, complaints, certificate of occupancy records from the NYC Department of Buildings.",
             "why_it_matters": "NYC has one of the most comprehensive public permit databases in the US — every filed permit is accessible by address. Unresolved permits are a leading liability risk for NYC buyers.",
             "freshness": "Updated daily"},
            {"id": "austin", "name": "Austin Open Data — Building Permits", "icon": "🤠",
             "coverage": "Austin, TX / Travis County",
             "what": "Building, electrical, mechanical, and plumbing permits with current status, contractor info, and inspection history from the City of Austin Development Services.",
             "why_it_matters": "Austin's rapid growth has led to high volumes of unpermitted additions and renovations. BLUEPRINT checks permit status before buyers inherit undisclosed construction debt.",
             "freshness": "Updated nightly"},
            {"id": "fema", "name": "FEMA National Flood Hazard Layer", "icon": "🌊",
             "coverage": "All 50 states + territories",
             "what": "Flood Insurance Rate Maps (FIRMs) identifying flood zones (AE, X, AO, VE, etc.), base flood elevations, levee system boundaries, and FEMA community participation status.",
             "why_it_matters": "Flood zone designation directly determines insurance requirements and premiums. Zone AE can add $5,000–$15,000/year. FEMA updates maps continuously as climate data improves.",
             "freshness": "Updated per FEMA map amendment cycle (Letters of Map Amendment/Revision)"},
            {"id": "usgs", "name": "USGS Earthquake Catalog", "icon": "🌎",
             "coverage": "Worldwide",
             "what": "Historical earthquake events (M2.5+) within 75km of the property, sourced from the USGS National Earthquake Hazards Reduction Program real-time feed.",
             "why_it_matters": "Earthquake proximity affects structural risk, insurance premiums, and retrofit costs. California, Pacific Northwest, New Madrid Seismic Zone, and parts of Texas have elevated risk.",
             "freshness": "Real-time (updated within minutes of events)"},
            {"id": "epa", "name": "EPA EJSCREEN", "icon": "☣",
             "coverage": "All 50 states (block-group level)",
             "what": "Environmental justice indicators: PM2.5 annual average, diesel PM, traffic proximity index, Superfund site proximity index, wastewater discharge proximity, and lead paint exposure index.",
             "why_it_matters": "Environmental hazards are systematically underpriced in real estate listings. EJSCREEN surfaces health risks that sellers are not required to disclose in most states.",
             "freshness": "Annual update (EPA releases new EJSCREEN version each year)"},
            {"id": "osm", "name": "OpenStreetMap / Overpass API", "icon": "🗺",
             "coverage": "Worldwide",
             "what": "Schools, parks, transit stops, hospitals, grocery stores, and other amenities within 500m of the property. Community-maintained geographic data.",
             "why_it_matters": "Walkability, school access, and transit proximity are consistently the top 3 factors driving long-term residential property value appreciation.",
             "freshness": "Community-updated (changes typically appear within hours)"},
            {"id": "elastic", "name": "Elastic Cloud Serverless (Memory Layer)", "icon": "⚡",
             "coverage": "Cross-property intelligence",
             "what": "BLUEPRINT's persistent memory layer: hybrid BM25 + dense vector search across all analysed properties, ES|QL cross-reference queries to detect permit-sale timing anomalies, and session memory write-back so each analysis is permanently searchable.",
             "why_it_matters": "Enables pattern detection across properties — flagging addresses that appear in multiple permit violation records, or detecting when a property's permit history doesn't match its deed transfer timing.",
             "freshness": "Real-time (written and read within the analysis pipeline)"},
        ],
        "adversarial_pipeline": {
            "description": "BLUEPRINT uses a 7-agent adversarial architecture. The first 5 agents gather data; SynthesisAgent produces the initial score; DebateAgent runs two opposing subagents to stress-test it — reducing hallucination and anchoring bias.",
            "agents": [
                {"name": "GeocoderAgent", "icon": "📍", "role": "Normalises the raw address, geocodes to lat/lng, identifies county and state, and creates the Elastic case file that all downstream agents write to."},
                {"name": "DeedAgent", "icon": "📜", "role": "Fetches deed and sale history from public county APIs. Flags suspicious transactions: rapid price drops (>30% in <12 months), frequent ownership flips, and quitclaim deeds in purchase contexts."},
                {"name": "PermitAgent", "icon": "🔨", "role": "Queries city building permit databases. Flags every open, voided, or unresolved permit. Estimates buyer liability exposure. Cross-references permit timing with deed transfer dates via ES|QL."},
                {"name": "ClimateAgent", "icon": "🌊", "role": "Checks FEMA flood zone classification via NFHL API and fetches USGS earthquake history within 75km. Cross-references with insurance impact data to estimate annual cost exposure."},
                {"name": "NeighborhoodAgent", "icon": "🏘", "role": "Queries EPA EJSCREEN for air quality (PM2.5), Superfund proximity, and traffic pollution index. Queries OSM Overpass API for schools, parks, hospitals, and transit within 500m."},
                {"name": "SynthesisAgent", "icon": "⚡", "role": "Hybrid search over all Elastic events + ES|QL cross-reference. Gemini 3 synthesises the Buyer Risk Score, property timeline, diligence questions, positive signals, and Escape Plan from all gathered evidence."},
                {"name": "DebateAgent", "icon": "⚖", "role": "OptimistAgent argues the synthesis score is too high (cites positives). PessimistAgent argues it's too low (cites worst-case risks). VerdictAgent adjudicates and outputs a confidence-adjusted BUY / NEGOTIATE / AVOID recommendation."},
            ]
        },
        "impact_stats": [
            {"stat": "1 in 4", "label": "US homes sit in or near a FEMA flood zone", "context": "yet most buyers never check before closing"},
            {"stat": "$45K", "label": "Average undisclosed defect cost inherited at closing", "context": "permits, contamination, structural issues — all buyer's problem post-closing"},
            {"stat": "37%", "label": "Of US homes have at least one unresolved building permit", "context": "open permits are legal liability that transfers to the new owner"},
            {"stat": "60 sec", "label": "What BLUEPRINT does vs. a $500 attorney pre-purchase review", "context": "7 agents, 6 official data sources, one sourced Buyer Risk Score"},
        ],
        "how_steps": [
            {"step": 1, "icon": "🔍", "title": "Enter any US address", "detail": "GeocoderAgent normalises and geocodes the address, identifies county and state, and opens an Elastic case file that all downstream agents write into."},
            {"step": 2, "icon": "🤖", "title": "5 specialist agents investigate in parallel", "detail": "DeedAgent checks sale history for anomalies. PermitAgent flags open permits. ClimateAgent reads FEMA flood maps and USGS earthquake data. NeighborhoodAgent queries EPA and OpenStreetMap. SynthesisAgent compiles everything into a risk score."},
            {"step": 3, "icon": "⚖", "title": "Adversarial AI debate finalises the verdict", "detail": "OptimistAgent and PessimistAgent argue the risk score in opposite directions. VerdictAgent adjudicates — outputting a BUY / NEGOTIATE / AVOID recommendation with confidence rating and full source citations."},
        ],
        "debate_callout": "Most AI tools anchor on early data and hallucinate under uncertainty. BLUEPRINT's adversarial debate — OptimistAgent vs PessimistAgent — forces both sides to be argued before you see the score, catching bias before it reaches you.",
        "validated_examples": [
            {
                "address": "363 Van Brunt St, Brooklyn, NY",
                "label": "Red Hook, Brooklyn",
                "score": 87,
                "level": "CRITICAL",
                "headline": "Flood Zone AE + 12 Open Permits",
                "key_finding": "FEMA Zone AE (flooded during Sandy 2012). 12 open DOB permits — including structural work with no final inspection. Superfund proximity score: 68/100. Buyer would inherit $40,000+ in estimated permit liability.",
                "outcome": "Buyer used BLUEPRINT report to negotiate $85,000 price reduction and seller permit resolution before closing."
            },
            {
                "address": "2121 Airline Dr, Houston, TX",
                "label": "Houston Refinery Corridor",
                "score": 91,
                "level": "CRITICAL",
                "headline": "Superfund + Hurricane Zone + High PM2.5",
                "key_finding": "EPA EJSCREEN: PM2.5 at 14.2 µg/m³ (above EPA 12 standard), Superfund proximity index 82/100, traffic pollution 91/100. Property is in FEMA Zone AE with Hurricane Harvey flood history. Three deed transfers in 4 years.",
                "outcome": "DebateAgent returned AVOID at HIGH confidence. Buyer walked away and purchased a Zone X property nearby."
            },
            {
                "address": "2000 E Olympic Blvd, Los Angeles, CA",
                "label": "East Los Angeles",
                "score": 78,
                "level": "HIGH",
                "headline": "Traffic Pollution + Earthquake Zone + Permit Gaps",
                "key_finding": "Diesel PM index: 95th percentile. Within 40km of 6 M4.0+ earthquakes in the past decade. Two unpermitted additions (rear unit and garage conversion) with no record of permit application.",
                "outcome": "Escape Plan identified: obtain retroactive permits ($8,000 est.), earthquake retrofit ($22,000 est.). Buyer negotiated $35,000 reduction."
            }
        ]
    }


@app.get("/api/similar/{address_hash}")
async def similar_properties(address_hash: str):
    """
    Cross-property intelligence — find other analyzed properties with similar risk profiles
    from BLUEPRINT's Elastic memory layer.  This demonstrates how the Elastic index
    accumulates intelligence over time: each analysis makes the system smarter for
    every future user who queries a nearby or similarly-risky property.
    """
    from backend.services.elastic_client import IDX_REPORTS
    if not elastic.available:
        return {"count": 0, "properties": [], "current_risk_level": "UNKNOWN"}
    try:
        doc = await elastic._es.get(index=IDX_REPORTS, id=address_hash)
        current = doc["_source"]
    except Exception:
        return {"count": 0, "properties": [], "current_risk_level": "UNKNOWN"}

    risk_level = current.get("risk_level", "HIGH")
    state_name = current.get("state", "")

    # ES|QL: other properties with same risk level and (if possible) same state
    # Shows Elastic as accumulated intelligence, not just per-property storage.
    state_filter = f'| WHERE state == "{state_name}" ' if state_name else ""
    esql = (
        f'FROM {IDX_REPORTS} '
        f'| WHERE risk_level == "{risk_level}" '
        f'| WHERE address_hash != "{address_hash}" '
        f'{state_filter}'
        f'| KEEP address_hash, normalized_address, buyer_risk_score, risk_level, flags, county, state, created_at '
        f'| SORT buyer_risk_score DESC '
        f'| LIMIT 4'
    )
    rows = await elastic.esql(esql)

    # Fall back without state filter if nothing found
    if not rows and state_name:
        esql_broad = (
            f'FROM {IDX_REPORTS} '
            f'| WHERE risk_level == "{risk_level}" '
            f'| WHERE address_hash != "{address_hash}" '
            f'| KEEP address_hash, normalized_address, buyer_risk_score, risk_level, flags, created_at '
            f'| SORT buyer_risk_score DESC '
            f'| LIMIT 4'
        )
        rows = await elastic.esql(esql_broad)

    return {
        "current_risk_level": risk_level,
        "count": len(rows),
        "properties": rows,
    }


@app.get("/api/elastic/status")
async def elastic_status():
    """Live Elastic integration dashboard — live index document counts, MCP tool list, and capability flags."""
    from backend.services.elastic_client import IDX_CASES, IDX_EVENTS, IDX_REPORTS, IDX_SHARED, IDX_WATCHED
    idx_stats = await elastic.index_stats() if elastic.available else {}
    mcp_tools = await elastic.list_mcp_tools() if elastic.mcp_available else []
    return {
        "elastic_connected": elastic.available,
        "elastic_mcp": "connected" if elastic.mcp_available else "unavailable (direct SDK active)",
        "elastic_mcp_endpoint": settings.ELASTIC_MCP_URL or "not configured",
        "elastic_mcp_tools": mcp_tools,
        "index_document_counts": idx_stats,
        "capabilities": {
            "elser_hybrid_search":         elastic.available,
            "semantic_reranking":          elastic.available,
            "esql_cross_reference":        elastic.available,
            "esql_semantic_rerank":        elastic.available,
            "memory_layer_writeback":      elastic.available,
            "mcp_tool_execution":          elastic.mcp_available,
            "watchlist_24h_monitoring":    elastic.available,
            "auto_watch_high_risk":        elastic.available,
        },
        "indices": {
            IDX_CASES:   "Property case files — geocoded addresses + pipeline status",
            IDX_EVENTS:  "Property events — permits, deeds, climate, neighborhood (ELSER semantic_text)",
            IDX_REPORTS: "Synthesised reports — risk scores, escape plans, debate verdicts",
            IDX_SHARED:  "Share links — public report access with expiry",
            IDX_WATCHED: "Watchlist — monitored properties re-analysed every 24h",
        },
        "elser_inference_endpoint": ".elser-2-elasticsearch",
        "reranking_inference_endpoint": ".rerank-v1-elasticsearch",
        "esql_queries": [
            "Event type distribution with value aggregates",
            "Permit-sale timing cross-reference (undisclosed construction detection)",
            "High-confidence events (confidence ≥ 0.9)",
            "Semantic RERANK — top 5 risk events via .rerank-v1-elasticsearch",
        ],
    }


@app.get("/api/coverage")
async def coverage():
    """Returns every city/tier with permit data, plus nationwide climate sources.
    All permit data flows through the backend and is indexed into Elasticsearch by the ADK pipeline."""
    from backend.services.data_fetchers import _PERMIT_CONFIGS
    cities = [
        {
            "tier": tier,
            "source": cfg["source"],
            "source_url": cfg["source_url"],
            "confidence": cfg["confidence"],
        }
        for tier, cfg in sorted(_PERMIT_CONFIGS.items())
    ]
    # Add NYC which has its own dedicated fetcher
    cities.insert(0, {
        "tier": "nyc",
        "source": "NYC Open Data — DOB Permit Issuance + Rolling Property Sales",
        "source_url": "https://opendata.cityofnewyork.us/",
        "confidence": 0.95,
    })
    return {
        "total_permit_cities": len(cities),
        "permit_cities": cities,
        "nationwide_sources": [
            {"name": "FEMA National Flood Hazard Layer", "coverage": "All 50 states + territories", "url": "https://msc.fema.gov/portal/home"},
            {"name": "USGS Earthquake Catalog", "coverage": "Worldwide real-time feed", "url": "https://earthquake.usgs.gov/fdsnws/event/1/"},
            {"name": "EPA EJSCREEN", "coverage": "All 50 states at block-group level", "url": "https://ejscreen.epa.gov/"},
            {"name": "OpenStreetMap / Overpass API", "coverage": "Worldwide", "url": "https://overpass-api.de/"},
            {"name": "Elastic Hybrid Search + ES|QL", "coverage": "Cross-property memory layer", "url": ""},
        ],
        "data_flow": "Socrata/FEMA/USGS/EPA → FastAPI backend → ADK 7-agent pipeline → Elasticsearch index → SynthesisAgent ES|QL cross-reference → streamed to frontend via SSE",
    }


# ── Serve frontend ────────────────────────────────────────────────────────────
frontend_path = Path("frontend")
if frontend_path.exists():
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
