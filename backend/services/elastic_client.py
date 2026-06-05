"""
Elastic client — wraps both the direct elasticsearch-py SDK (for indexing / memory
layer write-back) and the Elastic Agent Builder MCP server (for hybrid search and
ES|QL tools).  The MCP path is preferred for reads; the direct SDK is always used
for writes so the memory layer is never blocked by MCP availability.

This client deliberately exercises the *full* Elastic Search-AI surface:

  • ELSER sparse-vector semantic search   (semantic_text + .elser-2-elasticsearch)
  • RRF hybrid retrieval                   (BM25 ⊕ ELSER via the rrf retriever)
  • text_similarity_reranker               (.rerank-v1-elasticsearch)
  • ES|QL                                  (aggregation, RERANK, flip detection)
  • geo_point + geo_distance               (nearby-property cross-referencing)
  • aggregations                           (terms, stats, percentiles, date_histogram,
                                            cardinality, significant_terms)
  • percolator                             (saved risk-profile queries → proactive alerts)
  • Agent Builder MCP (Streamable HTTP)    (platform.core.search / platform.core.esql)
  • memory-layer write-back                (every finding persisted + searchable)

Every capability is wrapped so that a missing cluster feature degrades gracefully
to the next-best path and never breaks the analysis pipeline.

ELSER semantic search:
  blueprint_events.description and blueprint_cases.normalized_address use
  semantic_text field type with the .elser-2-elasticsearch inference endpoint
  (built-in on Elastic Cloud Serverless).  If the inference endpoint is unavailable
  (self-managed / trial without ML), _ensure_indices falls back to standard text.

MCP transport: Elastic Agent Builder uses Streamable HTTP transport (not SSE, not stdio).
Endpoint format: {KIBANA_URL}/api/agent_builder/mcp
Note: ELASTIC_URL is the Elasticsearch URL; ELASTIC_MCP_URL is the Kibana URL.
"""
import asyncio
import copy
import hashlib
import json
import logging
from typing import Any

import httpx
from elasticsearch import AsyncElasticsearch
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from backend.config import settings

logger = logging.getLogger(__name__)

# ── Index names ───────────────────────────────────────────────────────────────
IDX_CASES   = "blueprint_cases"
IDX_EVENTS  = "blueprint_events"
IDX_REPORTS = "blueprint_reports"
IDX_SHARED  = "blueprint_shared"
IDX_WATCHED = "blueprint_watched"
IDX_ALERTS  = "blueprint_alerts"   # percolator: saved risk-profile queries

# Built-in Elastic Cloud Serverless inference endpoints
ELSER_INFERENCE_ID   = ".elser-2-elasticsearch"
RERANK_INFERENCE_ID  = ".rerank-v1-elasticsearch"


class ElasticClient:
    def __init__(self):
        self._es: AsyncElasticsearch | None = None
        self._mcp_available = False
        self._mcp_tool_names: list[str] | None = None   # cache — avoid re-handshaking
        self._percolator_ready = False
        self._agent_builder_tools: list[str] = []       # custom tools we provisioned
        # Aggregatable field names for blueprint_reports, resolved at connect time.
        # On a fresh cluster these are true `keyword` fields; on a cluster where the
        # index predates the explicit mappings they were dynamically mapped as `text`
        # with a `.keyword` sub-field — so we aggregate on `<field>.keyword` instead.
        self._agg_fields: dict[str, str] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self):
        if not settings.ELASTIC_URL or not settings.ELASTIC_API_KEY:
            logger.warning("[Elastic] credentials not set — client offline")
            return
        self._es = AsyncElasticsearch(
            hosts=[settings.ELASTIC_URL],
            api_key=settings.ELASTIC_API_KEY,
            retry_on_timeout=True,
            max_retries=3,
        )
        try:
            await self._es.ping()
            logger.info("[OK] Elasticsearch connected")
            await self._ensure_indices()
            await self._backfill_geo_mapping()
            await self._resolve_agg_fields()
            await self.register_default_alerts()
        except Exception as exc:
            logger.warning("[Elastic] connection failed: %s", exc)
            self._es = None

        # Check MCP availability
        if settings.ELASTIC_MCP_URL:
            self._mcp_available = await self._ping_mcp()
            if self._mcp_available:
                logger.info("[OK] Elastic Agent Builder MCP available — tools: %s",
                            ", ".join(self._mcp_tool_names or []))
                await self.provision_agent_builder_tools()
            else:
                logger.warning("[Elastic MCP] unreachable — falling back to direct SDK for search")

    async def close(self):
        if self._es:
            await self._es.close()

    @property
    def available(self) -> bool:
        return self._es is not None

    @property
    def mcp_available(self) -> bool:
        return self._mcp_available

    @property
    def percolator_ready(self) -> bool:
        return self._percolator_ready

    @property
    def agent_builder_tools(self) -> list[str]:
        return self._agent_builder_tools

    # ── Index setup ───────────────────────────────────────────────────────────

    def _index_mappings(self) -> dict:
        """The canonical create-time mappings for every Blueprint index.

        Single source of truth — used by _ensure_indices at startup and by the
        reindex migration so a repaired index always matches the documented schema.
        description / normalized_address use semantic_text so the Agent Builder MCP
        search tool gets true ELSER hybrid BM25 + dense-vector retrieval; falls back
        to plain text via _downgrade_to_text if the ELSER endpoint isn't configured."""
        _ELSER = ELSER_INFERENCE_ID
        return {
            IDX_CASES: {
                "mappings": {
                    "properties": {
                        "address_hash":       {"type": "keyword"},
                        "normalized_address": {"type": "semantic_text", "inference_id": _ELSER},
                        "display_address":    {"type": "text"},
                        "lat":                {"type": "float"},
                        "lng":                {"type": "float"},
                        "location":           {"type": "geo_point"},
                        "city":               {"type": "keyword"},
                        "county":             {"type": "keyword"},
                        "state":              {"type": "keyword"},
                        "data_tier":          {"type": "keyword"},
                        "created_at":         {"type": "date"},
                        "status":             {"type": "keyword"},
                    }
                }
            },
            IDX_EVENTS: {
                "mappings": {
                    "properties": {
                        "address_hash": {"type": "keyword"},
                        "event_type":   {"type": "keyword"},
                        "event_date":   {"type": "date", "format": "yyyy-MM-dd||yyyy||epoch_millis"},
                        # semantic_text enables ELSER hybrid search via MCP search tool
                        "description":  {"type": "semantic_text", "inference_id": _ELSER},
                        "amount":       {"type": "double"},
                        "source":       {"type": "keyword"},
                        "source_url":   {"type": "keyword"},
                        "confidence":   {"type": "float"},
                        "flags":        {"type": "keyword"},
                        "raw_data":     {"type": "object", "enabled": False},
                    }
                }
            },
            IDX_REPORTS: {
                "mappings": {
                    "properties": {
                        "case_id":             {"type": "keyword"},
                        "address_hash":        {"type": "keyword"},
                        "normalized_address":  {"type": "text"},
                        "buyer_risk_score":    {"type": "integer"},
                        "risk_level":          {"type": "keyword"},
                        "buy_recommendation":  {"type": "keyword"},
                        "confidence":          {"type": "keyword"},
                        "county":              {"type": "keyword"},
                        "state":               {"type": "keyword"},
                        "flood_zone":          {"type": "keyword"},
                        "data_tier":           {"type": "keyword"},
                        "lat":                 {"type": "float"},
                        "lng":                 {"type": "float"},
                        "location":            {"type": "geo_point"},
                        "created_at":          {"type": "date"},
                        "timeline":            {"type": "object", "enabled": False},
                        "neighborhood":        {"type": "object", "enabled": False},
                        "climate_risk":        {"type": "object", "enabled": False},
                        "esql_insights":       {"type": "object", "enabled": False},
                        "elastic_provenance":  {"type": "object", "enabled": False},
                        "diligence_questions": {"type": "semantic_text", "inference_id": _ELSER},
                        "summary":             {"type": "text"},
                        "flags":               {"type": "keyword"},
                        "escape_plan":         {"type": "object", "enabled": False},
                        "positive_signals":    {"type": "object", "enabled": False},
                    }
                }
            },
            IDX_SHARED: {
                "mappings": {
                    "properties": {
                        "share_id":     {"type": "keyword"},
                        "address_hash": {"type": "keyword"},
                        "report":       {"type": "object", "enabled": False},
                        "created_at":   {"type": "date"},
                        "expires_at":   {"type": "date"},
                    }
                }
            },
            IDX_WATCHED: {
                "mappings": {
                    "properties": {
                        "address_hash":       {"type": "keyword"},
                        "normalized_address": {"type": "text"},
                        "watched_at":         {"type": "date"},
                        "last_checked":       {"type": "date"},
                    }
                }
            },
            # Percolator: each document IS a saved query. We percolate every new
            # property report against these to fire proactive "risk profile matched"
            # alerts — a reverse-search pattern unique to Elasticsearch.
            IDX_ALERTS: {
                "mappings": {
                    "properties": {
                        "query":             {"type": "percolator"},
                        "alert_name":        {"type": "keyword"},
                        "alert_description": {"type": "text"},
                        "severity":          {"type": "keyword"},
                        # fields the saved queries reference (mirror the percolated doc)
                        "buyer_risk_score":  {"type": "integer"},
                        "risk_level":        {"type": "keyword"},
                        "flood_zone":        {"type": "keyword"},
                        "state":             {"type": "keyword"},
                        "flags":             {"type": "keyword"},
                    }
                }
            },
        }

    async def _ensure_indices(self):
        if not self._es:
            return
        mappings = self._index_mappings()
        for idx, body in mappings.items():
            try:
                exists = await self._es.indices.exists(index=idx)
                if not exists:
                    await self._es.indices.create(index=idx, body=body)
                    logger.info("[Elastic] created index %s", idx)
            except Exception as e:
                err_str = str(e).lower()
                if any(k in err_str for k in ("semantic_text", "inference", "illegal_argument", "unknown field")):
                    # ELSER inference endpoint not configured — fall back to BM25 text fields
                    fallback = _downgrade_to_text(body)
                    try:
                        if not await self._es.indices.exists(index=idx):
                            await self._es.indices.create(index=idx, body=fallback)
                            logger.warning(
                                "[Elastic] index %s created with BM25 text fields "
                                "(ELSER inference endpoint not configured — semantic search disabled)", idx
                            )
                    except Exception as e2:
                        logger.debug("Index %s BM25 fallback setup: %s", idx, e2)
                else:
                    logger.debug("Index %s setup: %s", idx, e)

    async def _backfill_geo_mapping(self):
        """Best-effort: add the geo_point `location` field to pre-existing indices.

        geo_point is one of the few field types that can be added to an existing
        mapping via put_mapping, so older deployments gain geo support without a
        reindex.  Failures are non-fatal."""
        if not self._es:
            return
        for idx in (IDX_CASES, IDX_REPORTS):
            try:
                await self._es.indices.put_mapping(
                    index=idx,
                    body={"properties": {"location": {"type": "geo_point"},
                                          "flood_zone": {"type": "keyword"}}},
                )
            except Exception as e:
                logger.debug("[Elastic] geo backfill on %s skipped: %s", idx, e)

    async def _resolve_agg_fields(self):
        """Resolve the aggregatable field name for each keyword field on
        blueprint_reports. `text` fields can't be aggregated directly (fielddata is
        disabled), but dynamic mapping gives them a `.keyword` sub-field — so we point
        terms/cardinality/significant_terms aggs at `<field>.keyword` when needed.
        On a correctly-mapped (fresh) cluster the field is already `keyword`."""
        self._agg_fields = {f: f for f in ("flags", "state", "county")}
        if not self._es:
            return
        try:
            mp = await self._es.indices.get_mapping(index=IDX_REPORTS)
            props = mp[IDX_REPORTS]["mappings"]["properties"]
            for field in ("flags", "state", "county"):
                conf = props.get(field, {})
                if conf.get("type") == "text" and "keyword" in (conf.get("fields") or {}):
                    self._agg_fields[field] = f"{field}.keyword"
            non_default = {k: v for k, v in self._agg_fields.items() if k != v}
            if non_default:
                logger.info("[Elastic] aggregating drifted text fields via sub-fields: %s",
                            non_default)
        except Exception as e:
            logger.debug("[Elastic] agg-field resolution skipped: %s", e)

    def _agg_field(self, name: str) -> str:
        """Aggregatable field name for `name` (handles text→.keyword drift)."""
        return self._agg_fields.get(name, name)

    # ── Write operations (direct SDK — memory layer write-back) ───────────────

    async def index_document(self, index: str, doc_id: str, doc: dict) -> bool:
        if not self._es:
            return False
        try:
            await self._es.index(index=index, id=doc_id, document=doc)
            return True
        except Exception as e:
            logger.warning("[Elastic] index_document failed: %s", e)
            return False

    async def index_events_bulk(self, events: list[dict]) -> int:
        """Bulk-index a list of property events. Returns count indexed.

        Uses a content-hash document ID so re-running the same analysis
        overwrites existing events instead of appending duplicates.
        """
        if not self._es or not events:
            return 0
        ops = []
        for ev in events:
            # Stable ID: hash of (address_hash, event_type, event_date, source).
            # Identical events across pipeline reruns silently upsert — no duplicates.
            raw_id = (
                f"{ev.get('address_hash', '')}"
                f"{ev.get('event_type', '')}"
                f"{str(ev.get('event_date', ''))[:10]}"
                f"{ev.get('source', '')}"
                f"{ev.get('description', '')[:40]}"
            )
            doc_id = hashlib.md5(raw_id.encode()).hexdigest()[:16]
            ops.append({"index": {"_index": IDX_EVENTS, "_id": doc_id}})
            ops.append(ev)
        try:
            resp = await self._es.bulk(operations=ops)
            errors = [i for i in resp.get("items", []) if "error" in i.get("index", {})]
            return len(events) - len(errors)
        except Exception as e:
            logger.warning("[Elastic] bulk index failed: %s", e)
            return 0

    # ── Read operations ────────────────────────────────────────────────────────

    async def search_events(self, address_hash: str, query: str = "", size: int = 50) -> list[dict]:
        """Back-compat wrapper — returns just the event list.
        New callers should prefer hybrid_search_events() for retrieval provenance."""
        result = await self.hybrid_search_events(address_hash, query, size)
        return result.get("events", [])

    async def hybrid_search_events(self, address_hash: str, query: str = "", size: int = 50) -> dict:
        """
        Retrieve a property's events using the strongest available strategy and
        report which one ran (for UI provenance).

        Strategy ladder (first that yields results wins):
          1. Elastic Agent Builder MCP hybrid search   → "elastic_mcp_hybrid"
          2. RRF hybrid retriever  (BM25 ⊕ ELSER)       → "rrf_bm25_elser"
          3. text_similarity_reranker over BM25         → "semantic_reranker"
          4. plain BM25 sorted by date                  → "bm25"
        """
        # 1 — MCP hybrid (preferred when Agent Builder is reachable).
        # platform_core_search requires a natural-language query, so only attempt it
        # when we have one; with no query the date-sorted BM25 path (4) is correct.
        if self._mcp_available and query:
            events = await self._mcp_search(address_hash, query, size)
            if events:
                return {"events": events, "strategy": "elastic_mcp_hybrid",
                        "count": len(events), "reranked": True}

        if not self._es:
            return {"events": [], "strategy": "unavailable", "count": 0, "reranked": False}

        # 2 — RRF hybrid retriever (Reciprocal Rank Fusion: lexical ⊕ semantic)
        if query:
            rrf = await self._rrf_search(address_hash, query, size)
            if rrf:
                return {"events": rrf, "strategy": "rrf_bm25_elser",
                        "count": len(rrf), "reranked": False}

        # 3 — semantic reranker over a term-filtered base
        if query:
            reranked = await self._reranker_search(address_hash, query, size)
            if reranked:
                return {"events": reranked, "strategy": "semantic_reranker",
                        "count": len(reranked), "reranked": True}

        # 4 — plain BM25 by date
        bm25 = await self._bm25_search(address_hash, size)
        return {"events": bm25, "strategy": "bm25", "count": len(bm25), "reranked": False}

    async def _rrf_search(self, address_hash: str, query_text: str, size: int) -> list[dict]:
        """RRF retriever combining a lexical (match) and a semantic (ELSER) retriever.
        Returns [] on any failure so the caller can fall through."""
        if not self._es:
            return []
        hash_filter = [{"term": {"address_hash": address_hash}}]
        try:
            resp = await self._es.search(
                index=IDX_EVENTS,
                size=size,
                retriever={
                    "rrf": {
                        "retrievers": [
                            {"standard": {"query": {"bool": {
                                "must": [{"match": {"description": query_text}}],
                                "filter": hash_filter}}}},
                            {"standard": {"query": {"bool": {
                                "must": [{"semantic": {"field": "description", "query": query_text}}],
                                "filter": hash_filter}}}},
                        ],
                        "rank_window_size": min(size * 2, 100),
                        "rank_constant": 20,
                    }
                },
            )
            results = [h["_source"] for h in resp["hits"]["hits"]]
            if results:
                logger.info("[Elastic] RRF hybrid: %d docs (BM25 ⊕ ELSER)", len(results))
            return results
        except Exception as e:
            logger.debug("[Elastic] RRF unavailable: %s", e)
            return []

    async def _reranker_search(self, address_hash: str, query_text: str, size: int) -> list[dict]:
        """text_similarity_reranker over a term-filtered base retriever."""
        if not self._es:
            return []
        try:
            resp = await self._es.search(
                index=IDX_EVENTS,
                size=size,
                retriever={
                    "text_similarity_reranker": {
                        "retriever": {"standard": {"query": {"term": {"address_hash": address_hash}}}},
                        "field": "description",
                        "inference_id": RERANK_INFERENCE_ID,
                        "inferenceText": query_text,
                        "rank_window_size": min(size * 2, 100),
                    }
                },
            )
            results = [h["_source"] for h in resp["hits"]["hits"]]
            if results:
                logger.info("[Elastic] reranked search: %d docs by semantic relevance", len(results))
            return results
        except Exception as e:
            logger.debug("[Elastic] reranking unavailable: %s — falling back to BM25", e)
            return []

    async def _bm25_search(self, address_hash: str, size: int) -> list[dict]:
        if not self._es:
            return []
        try:
            resp = await self._es.search(
                index=IDX_EVENTS,
                query={"term": {"address_hash": address_hash}},
                sort=[{"event_date": {"order": "asc"}}],
                size=size,
                source=True,
            )
            return [h["_source"] for h in resp["hits"]["hits"]]
        except Exception as e:
            logger.warning("[Elastic] BM25 search failed: %s", e)
            return []

    # Kept for any external callers / parity with previous public surface.
    async def _direct_search(self, address_hash: str, size: int, query_text: str = "") -> list[dict]:
        if query_text:
            rr = await self._reranker_search(address_hash, query_text, size)
            if rr:
                return rr
        return await self._bm25_search(address_hash, size)

    # ── Geospatial cross-property intelligence ─────────────────────────────────

    async def geo_search_nearby(self, lat: float, lng: float, radius_km: float = 50.0,
                                exclude_hash: str = "", size: int = 6) -> list[dict]:
        """Find previously-analysed properties within radius_km, nearest first.

        Uses a geo_distance filter + _geo_distance sort — genuine geographic
        cross-referencing across BLUEPRINT's accumulated memory layer."""
        if not self._es or lat is None or lng is None:
            return []
        origin = {"lat": lat, "lon": lng}
        try:
            resp = await self._es.search(
                index=IDX_REPORTS,
                size=size,
                query={"bool": {
                    "filter": [{"geo_distance": {"distance": f"{radius_km}km", "location": origin}}],
                    "must_not": [{"term": {"address_hash": exclude_hash}}] if exclude_hash else [],
                }},
                sort=[{"_geo_distance": {"location": origin, "order": "asc", "unit": "km"}}],
                source=["address_hash", "normalized_address", "buyer_risk_score",
                        "risk_level", "flags", "county", "state", "lat", "lng", "flood_zone"],
            )
            out = []
            for h in resp["hits"]["hits"]:
                src = h["_source"]
                dist = None
                if h.get("sort"):
                    try:
                        dist = round(float(h["sort"][0]), 1)
                    except (TypeError, ValueError):
                        dist = None
                src["distance_km"] = dist
                out.append(src)
            return out
        except Exception as e:
            logger.debug("[Elastic] geo_distance search unavailable: %s", e)
            return []

    # ── Aggregation analytics (market intelligence) ────────────────────────────

    async def market_insights(self) -> dict:
        """Cross-property aggregations — each run as a separate async request so
        individual failures are isolated and surfaced without blocking the others."""
        if not self._es:
            return {}

        async def _agg(name: str, agg_def: dict) -> tuple[str, dict]:
            try:
                r = await self._es.search(
                    index=IDX_REPORTS, size=0, aggregations={name: agg_def}
                )
                return name, (r.get("aggregations") or {}).get(name) or {}
            except Exception as exc:
                logger.warning("[Elastic] agg '%s' failed: %s", name, exc)
                return name, {}

        results = await asyncio.gather(
            _agg("risk_levels", {"terms": {"field": "risk_level", "size": 5}}),
            _agg("score_stats", {"stats": {"field": "buyer_risk_score"}}),
            _agg("score_pct",   {"percentiles": {"field": "buyer_risk_score",
                                                 "percents": [25, 50, 75, 90]}}),
            _agg("top_flags",   {"terms": {"field": self._agg_field("flags"), "size": 8}}),
            _agg("top_states",  {"terms": {"field": self._agg_field("state"), "size": 6}}),
            _agg("unique_counties", {"cardinality": {"field": self._agg_field("county")}}),
            _agg("over_time",   {"date_histogram": {"field": "created_at",
                                                    "calendar_interval": "day",
                                                    "min_doc_count": 1}}),
        )
        aggs = dict(results)

        def _buckets(name: str) -> list:
            return [{"key": b["key"], "count": b["doc_count"]}
                    for b in (aggs.get(name) or {}).get("buckets", [])]

        over_time = [{"date": b.get("key_as_string", "")[:10], "count": b["doc_count"]}
                     for b in (aggs.get("over_time") or {}).get("buckets", [])]

        return {
            "risk_level_distribution": _buckets("risk_levels"),
            "top_flags":               _buckets("top_flags"),
            "top_states":              _buckets("top_states"),
            "analyses_over_time":      over_time,
            "score_stats":             aggs.get("score_stats") or {},
            "score_percentiles":       (aggs.get("score_pct") or {}).get("values", {}),
            "unique_counties":         (aggs.get("unique_counties") or {}).get("value", 0),
        }

    async def significant_flags(self, risk_level: str, size: int = 6) -> list[dict]:
        """significant_terms: which risk flags are statistically over-represented
        in this risk band vs. the whole corpus.  Empty until enough data exists."""
        if not self._es or not risk_level:
            return []
        try:
            resp = await self._es.search(
                index=IDX_REPORTS,
                size=0,
                query={"term": {"risk_level": risk_level}},
                aggregations={"sig": {"significant_terms": {"field": self._agg_field("flags"), "size": size}}},
            )
            return [{"flag": b["key"], "score": round(b.get("score", 0), 3),
                     "in_band": b["doc_count"], "in_corpus": b.get("bg_count", 0)}
                    for b in resp.get("aggregations", {}).get("sig", {}).get("buckets", [])]
        except Exception as e:
            logger.warning("[Elastic] significant_terms failed: %s", e)
            return []

    async def index_stats(self) -> dict:
        """Return document counts for all Blueprint indices."""
        if not self._es:
            return {}
        stats = {}
        for idx in [IDX_CASES, IDX_EVENTS, IDX_REPORTS, IDX_SHARED, IDX_WATCHED, IDX_ALERTS]:
            try:
                resp = await self._es.count(index=idx)
                stats[idx] = resp.get("count", 0)
            except Exception:
                stats[idx] = -1
        return stats

    # ── Percolator: proactive risk-profile alerting ────────────────────────────

    def _default_alert_specs(self) -> list[dict]:
        """Saved risk-profile queries. Each is a percolator document whose `query`
        matches the property report doc we percolate at analysis time."""
        return [
            {
                "id": "severe_buyer_risk",
                "alert_name": "Severe buyer risk",
                "severity": "CRITICAL",
                "alert_description": "Composite buyer risk score is 80 or above: specialist review required before any offer.",
                "query": {"range": {"buyer_risk_score": {"gte": 80}}},
            },
            {
                "id": "high_flood_exposure",
                "alert_name": "High flood-zone exposure",
                "severity": "HIGH",
                "alert_description": "Property sits in a FEMA Special Flood Hazard Area (Zone A/AE/AO/VE): mandatory flood insurance likely.",
                "query": {"terms": {"flood_zone": ["A", "AE", "AO", "VE", "AH", "AR"]}},
            },
            {
                "id": "elevated_risk_band",
                "alert_name": "Elevated risk band",
                "severity": "MEDIUM",
                "alert_description": "Risk level is HIGH or CRITICAL: negotiate hard and budget for mitigation.",
                "query": {"terms": {"risk_level": ["HIGH", "CRITICAL"]}},
            },
        ]

    async def register_default_alerts(self):
        """Idempotently index the default percolator queries."""
        if not self._es:
            return
        try:
            for spec in self._default_alert_specs():
                doc = {k: v for k, v in spec.items() if k != "id"}
                await self._es.index(index=IDX_ALERTS, id=spec["id"], document=doc)
            self._percolator_ready = True
            logger.info("[Elastic] percolator alerts registered (%d profiles)",
                        len(self._default_alert_specs()))
        except Exception as e:
            logger.debug("[Elastic] percolator registration skipped: %s", e)
            self._percolator_ready = False

    async def percolate_property(self, report: dict) -> list[dict]:
        """Percolate a finished report against saved risk profiles → matched alerts.
        Reverse search: the document is the query target, the stored queries match it."""
        if not self._es or not self._percolator_ready:
            return []
        doc = {
            "buyer_risk_score": int(report.get("buyer_risk_score", 0) or 0),
            "risk_level":       report.get("risk_level", "UNKNOWN"),
            "flood_zone":       (report.get("climate_risk", {}) or {}).get("flood_zone", "")
                                or report.get("flood_zone", ""),
            "state":            report.get("state", ""),
            "flags":            report.get("flags", []),
        }
        try:
            resp = await self._es.search(
                index=IDX_ALERTS,
                query={"percolate": {"field": "query", "document": doc}},
            )
            return [{"alert_name": h["_source"].get("alert_name"),
                     "severity":   h["_source"].get("severity"),
                     "description": h["_source"].get("alert_description")}
                    for h in resp["hits"]["hits"]]
        except Exception as e:
            logger.debug("[Elastic] percolate failed: %s", e)
            return []

    # ── MCP transport ──────────────────────────────────────────────────────────

    def _mcp_headers(self) -> dict[str, str]:
        """Auth + Kibana headers for the Agent Builder MCP endpoint.

        streamablehttp_client takes a `headers` dict (not a pre-built httpx client);
        Kibana requires the ApiKey auth header plus kbn-xsrf and the api-version pin."""
        return {
            "Authorization": f"ApiKey {settings.ELASTIC_API_KEY}",
            "kbn-xsrf": "true",
            "elastic-api-version": "2023-10-31",
        }

    async def _mcp_search(self, address_hash: str, query: str, size: int) -> list[dict]:
        """
        Call the Elastic Agent Builder `platform_core_search` tool over Streamable HTTP.

        The tool does natural-language semantic retrieval and returns a *resource list*
        of `{reference:{id,index}, content:{snippets}}` — references, not full _source,
        and it cannot be term-filtered.  We therefore hydrate the returned event ids to
        full documents via the direct SDK (mget) and hard-filter to this property so
        semantic recall never leaks events from other addresses.

        Returns [] (not a BM25 result) so hybrid_search_events can fall through to the
        RRF/reranker strategies when MCP surfaces nothing for this address.
        """
        try:
            async with streamablehttp_client(settings.ELASTIC_MCP_URL, headers=self._mcp_headers()) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    search_tool = _pick_search_tool(await self._tool_names(session))
                    if not search_tool:
                        logger.warning("[Elastic MCP] no search tool found")
                        return []
                    result = await session.call_tool(
                        search_tool,
                        arguments={"query": query, "index": IDX_EVENTS},
                    )
                    raw = _parse_mcp_result(result)
                    refs = _doc_refs_from_envelope(raw, IDX_EVENTS)
                    if not refs:
                        return []
                    docs = await self._mget_sources(refs[: max(size * 2, size)])
                    # Hard-filter to this property regardless of what the semantic
                    # tool surfaced across the corpus.
                    filtered = [d for d in docs if not address_hash
                                or d.get("address_hash") == address_hash]
                    if filtered:
                        logger.info("[Elastic MCP] hybrid search: %d docs (hydrated from %d refs)",
                                    len(filtered), len(refs))
                    return filtered[:size]
        except Exception as e:
            logger.warning("[Elastic MCP] search failed: %s — falling back to direct SDK", e)
        return []

    async def _mget_sources(self, refs: list[tuple[str, str]]) -> list[dict]:
        """Hydrate (index, id) references to full _source docs via direct SDK mget."""
        if not self._es or not refs:
            return []
        try:
            resp = await self._es.mget(body={"docs": [{"_index": idx, "_id": _id}
                                                       for idx, _id in refs]})
            return [d["_source"] for d in resp.get("docs", [])
                    if d.get("found") and d.get("_source")]
        except Exception as e:
            logger.debug("[Elastic] mget hydrate failed: %s", e)
            return []

    async def esql(self, query: str) -> list[dict]:
        """Run an ES|QL query via Elastic MCP (preferred) or direct SDK."""
        if self._mcp_available:
            result = await self._mcp_esql(query)
            if result is not None:
                return result
        return await self._direct_esql(query)

    async def _direct_esql(self, query: str) -> list[dict]:
        if not self._es:
            return []
        try:
            resp = await self._es.esql.query(body={"query": query}, format="json")
            cols = [c["name"] for c in resp.get("columns", [])]
            return [dict(zip(cols, row)) for row in resp.get("values", [])]
        except Exception as e:
            logger.warning("[Elastic] ES|QL failed: %s", e)
            return []

    async def _mcp_esql(self, query: str) -> list[dict] | None:
        """Call Elastic Agent Builder MCP ES|QL tool via Streamable HTTP."""
        try:
            async with streamablehttp_client(settings.ELASTIC_MCP_URL, headers=self._mcp_headers()) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    esql_tool = _pick_esql_tool(await self._tool_names(session))
                    if not esql_tool:
                        return None
                    result = await session.call_tool(esql_tool, arguments={"query": query})
                    raw = _parse_mcp_result(result)
                    # Agent Builder envelope: {"results":[..,{"type":"esql_results",
                    #   "data":{"columns":[{name}],"values":[[..]]}}]}
                    table = _esql_table_from_envelope(raw)
                    if table is not None:
                        return table
                    # Legacy / simpler shapes kept for forward-compat.
                    if isinstance(raw, list):
                        return raw
                    if isinstance(raw, dict):
                        if isinstance(raw.get("rows"), list):
                            return raw["rows"]
                        if "columns" in raw and "values" in raw:
                            cols = [c.get("name") for c in raw["columns"]]
                            return [dict(zip(cols, row)) for row in raw["values"]]
                    return None
        except Exception as e:
            logger.debug("[Elastic MCP] ES|QL failed: %s", e)
            return None

    async def _tool_names(self, session: ClientSession) -> list[str]:
        """Tool names for an open session, using the cached list when available."""
        if self._mcp_tool_names is not None:
            return self._mcp_tool_names
        tools_resp = await session.list_tools()
        names = [t.name for t in (tools_resp.tools or [])]
        self._mcp_tool_names = names
        return names

    async def list_mcp_tools(self) -> list[str]:
        """List all tools on the Elastic Agent Builder MCP server (cached)."""
        if self._mcp_tool_names is not None:
            return self._mcp_tool_names
        if not settings.ELASTIC_MCP_URL or not settings.ELASTIC_API_KEY:
            return []
        try:
            async with streamablehttp_client(settings.ELASTIC_MCP_URL, headers=self._mcp_headers()) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_resp = await session.list_tools()
                    self._mcp_tool_names = [t.name for t in (tools_resp.tools or [])]
                    return self._mcp_tool_names
        except Exception as e:
            logger.warning("[Elastic MCP] list_tools failed: %s — type: %s", e, type(e).__name__)
            return []

    # ── Agent Builder custom-tool provisioning ─────────────────────────────────

    def _kibana_base(self) -> str:
        """Derive the Kibana base URL from the MCP endpoint."""
        url = settings.ELASTIC_MCP_URL or ""
        return url.split("/api/agent_builder/mcp")[0].rstrip("/")

    def _agent_builder_tool_specs(self) -> list[dict]:
        """Custom ES|QL tools to expose in Agent Builder so a Gemini agent can call
        them by name over MCP. These wrap BLUEPRINT's signature cross-references."""
        return [
            {
                "id": "blueprint_flip_fraud",
                "type": "esql",
                "description": "Detect rapid repeated deed transfers (flip-fraud / distress-sale pattern) for a property by address_hash.",
                "configuration": {
                    "query": (
                        f"FROM {IDX_EVENTS} | WHERE address_hash == ?address_hash AND event_type == \"sale\" "
                        f"| STATS sale_count = COUNT(*), first_sale = MIN(event_date), last_sale = MAX(event_date) "
                        f"| WHERE sale_count >= 2"
                    ),
                    "params": {"address_hash": {"type": "string",
                               "description": "Stable hash identifying the property to analyse."}},
                },
            },
            {
                "id": "blueprint_permit_sale_timing",
                "type": "esql",
                "description": "Cross-reference permit vs. sale timing to surface undisclosed-construction risk for a property.",
                "configuration": {
                    "query": (
                        f"FROM {IDX_EVENTS} | WHERE address_hash == ?address_hash AND event_type IN (\"permit\", \"sale\") "
                        f"| STATS earliest = MIN(event_date), latest = MAX(event_date), n = COUNT(*) BY event_type"
                    ),
                    "params": {"address_hash": {"type": "string",
                               "description": "Stable hash identifying the property to analyse."}},
                },
            },
            {
                "id": "blueprint_top_risk_events",
                "type": "esql",
                "description": "Return a property's highest-risk events, semantically reranked by Elastic's built-in reranker.",
                "configuration": {
                    "query": (
                        f"FROM {IDX_EVENTS} | WHERE address_hash == ?address_hash "
                        f"| RERANK \"undisclosed construction flood earthquake permit violation hazard\" ON description "
                        f"WITH {{\"inference_id\": \"{RERANK_INFERENCE_ID}\"}} "
                        f"| KEEP event_type, event_date, description, confidence, flags | LIMIT 5"
                    ),
                    "params": {"address_hash": {"type": "string",
                               "description": "Stable hash identifying the property to analyse."}},
                },
            },
        ]

    async def provision_agent_builder_tools(self) -> list[str]:
        """Best-effort: create custom ES|QL tools in Agent Builder via the Kibana API
        so they appear in the MCP tool list and a Gemini agent can call them by name.

        Fully optional — guarded so a missing/locked-down API never breaks startup."""
        base = self._kibana_base()
        if not base or not settings.ELASTIC_API_KEY:
            return []
        created: list[str] = []
        url = f"{base}/api/agent_builder/tools"
        headers = {
            "Authorization": f"ApiKey {settings.ELASTIC_API_KEY}",
            "kbn-xsrf": "true",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                for spec in self._agent_builder_tool_specs():
                    try:
                        resp = await client.post(url, headers=headers, json=spec)
                        # Kibana returns 400 "already exists" (not 409) on re-runs —
                        # that still means the tool is provisioned and callable.
                        already = (resp.status_code == 400
                                   and "already exists" in resp.text.lower())
                        if resp.status_code in (200, 201, 409) or already:
                            created.append(spec["id"])
                        else:
                            logger.debug("[Elastic] provision tool %s → %s: %s",
                                         spec["id"], resp.status_code, resp.text[:200])
                    except Exception as e:
                        logger.debug("[Elastic] provision tool %s skipped: %s", spec["id"], e)
        except Exception as e:
            logger.debug("[Elastic] Agent Builder tool provisioning skipped: %s", e)
        if created:
            logger.info("[Elastic] Agent Builder custom tools available: %s", ", ".join(created))
        self._agent_builder_tools = created
        # Tool list changed — drop the cache so it's rediscovered.
        self._mcp_tool_names = None
        return created

    # ── MCP ping ──────────────────────────────────────────────────────────────

    async def _ping_mcp(self) -> bool:
        """Ping Elastic MCP by listing tools via Streamable HTTP transport."""
        if not settings.ELASTIC_MCP_URL or not settings.ELASTIC_API_KEY:
            return False
        try:
            tools = await self.list_mcp_tools()
            return len(tools) > 0
        except Exception:
            return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_mcp_result(result) -> Any:
    """Extract usable data from an MCP tool call result (handles text/JSON content)."""
    if result is None:
        return {}
    content = getattr(result, "content", None)
    if not content:
        return {}
    for item in content:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {"text": text}
    return {}


def _esql_table_from_envelope(raw: Any) -> list[dict] | None:
    """Parse the Agent Builder execute_esql result envelope into row dicts.

    Shape: {"results":[.., {"type":"esql_results","data":{"columns":[{name}],
            "values":[[..]]}}]}.  Returns None if the envelope isn't recognised."""
    if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
        return None
    for item in raw["results"]:
        if isinstance(item, dict) and item.get("type") == "esql_results":
            data = item.get("data") or {}
            cols = [c.get("name") for c in data.get("columns", [])]
            if cols:
                return [dict(zip(cols, row)) for row in data.get("values", [])]
    return None


def _doc_refs_from_envelope(raw: Any, index: str | None = None) -> list[tuple[str, str]]:
    """Extract (index, id) document references from a platform_core_search envelope.

    Shape: {"results":[{"type":"resource_list","data":{"resources":[
            {"reference":{"id":..,"index":..}}]}}]}.  Filters to `index` when given."""
    out: list[tuple[str, str]] = []
    if not isinstance(raw, dict):
        return out
    for item in raw.get("results", []) or []:
        if not isinstance(item, dict):
            continue
        for res in (item.get("data") or {}).get("resources", []) or []:
            ref = (res or {}).get("reference") or {}
            _id, idx = ref.get("id"), ref.get("index")
            if _id and (index is None or idx == index):
                out.append((idx, _id))
    return out


def _pick_search_tool(tool_names: list[str]) -> str | None:
    """
    Find the search tool from whatever Elastic Agent Builder exposes.
    Elastic Serverless uses platform.core.* naming; classic Kibana uses shorter names.
    """
    candidates = [
        "platform_core_search",          # Agent Builder library name (confirmed, underscores)
        "platform.core.search",          # legacy/dotted alias
        "search", "semantic_search", "hybrid_search", "elasticsearch_search",
        "search_documents", "retrieval", "query",
    ]
    for c in candidates:
        if c in tool_names:
            return c
    for name in tool_names:
        if "search" in name.lower():
            return name
    return None


def _pick_esql_tool(tool_names: list[str]) -> str | None:
    """Find the ES|QL tool from whatever Elastic Agent Builder exposes.

    The Agent Builder library uses 'platform.core.execute_esql' (confirmed in Kibana
    tool library). Older naming 'platform.core.esql' kept as fallback.
    """
    candidates = [
        "platform_core_execute_esql",   # Agent Builder library name (confirmed, underscores)
        "platform.core.execute_esql",   # legacy/dotted alias
        "platform.core.esql",           # legacy/alternative name
        "esql", "esql_query", "run_esql", "elasticsearch_esql", "query_esql",
    ]
    for c in candidates:
        if c in tool_names:
            return c
    for name in tool_names:
        if "esql" in name.lower() or "sql" in name.lower():
            return name
    return None


def _downgrade_to_text(body: dict) -> dict:
    """Replace semantic_text fields with plain text for ELSER-unavailable environments."""
    result = copy.deepcopy(body)
    props = result.get("mappings", {}).get("properties", {})
    for field, conf in props.items():
        if conf.get("type") == "semantic_text":
            props[field] = {"type": "text"}
    return result


# ── Module singleton ──────────────────────────────────────────────────────────
elastic = ElasticClient()
