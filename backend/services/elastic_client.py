"""
Elastic client — wraps both the direct elasticsearch-py SDK (for indexing / memory
layer write-back) and the Elastic Agent Builder MCP server (for hybrid search and
ES|QL tools).  The MCP path is preferred for reads; the direct SDK is always used
for writes so the memory layer is never blocked by MCP availability.

ELSER semantic search:
  blueprint_events.description and blueprint_cases.normalized_address use
  semantic_text field type with the .elser-2-elasticsearch inference endpoint
  (built-in on Elastic Cloud Serverless).  This enables true hybrid BM25 +
  dense-vector search via the Agent Builder MCP search tool.
  If the inference endpoint is unavailable (self-managed / trial without ML),
  _ensure_indices falls back to standard text fields automatically.

MCP transport: Elastic Agent Builder uses Streamable HTTP transport (not SSE, not stdio).
Connection uses mcp.client.streamable_http.streamablehttp_client, with auth headers
passed via a pre-configured httpx.AsyncClient.

Required API key privileges (Kibana application privilege, not just ES):
  - feature_agentBuilder.read
  - feature_actions.read
  - cluster: ["monitor_inference"]
  - index: read + view_index_metadata on your indices

Endpoint format: {KIBANA_URL}/api/agent_builder/mcp
Note: ELASTIC_URL is the Elasticsearch URL; ELASTIC_MCP_URL is the Kibana URL.
"""
import asyncio
import copy
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


class ElasticClient:
    def __init__(self):
        self._es: AsyncElasticsearch | None = None
        self._mcp_available = False

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
        except Exception as exc:
            logger.warning("[Elastic] connection failed: %s", exc)
            self._es = None

        # Check MCP availability
        if settings.ELASTIC_MCP_URL:
            self._mcp_available = await self._ping_mcp()
            if self._mcp_available:
                logger.info("[OK] Elastic Agent Builder MCP available")
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

    # ── Index setup ───────────────────────────────────────────────────────────

    async def _ensure_indices(self):
        if not self._es:
            return
        # description / normalized_address use semantic_text so the Agent Builder MCP
        # search tool gets true ELSER hybrid BM25 + dense-vector retrieval.
        # Falls back to plain text if the ELSER inference endpoint is not configured.
        _ELSER = ".elser-2-elasticsearch"  # Built-in on Elastic Cloud Serverless
        mappings = {
            IDX_CASES: {
                "mappings": {
                    "properties": {
                        "address_hash":       {"type": "keyword"},
                        "normalized_address": {"type": "semantic_text", "inference_id": _ELSER},
                        "display_address":    {"type": "text"},
                        "lat":                {"type": "float"},
                        "lng":                {"type": "float"},
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
                        "data_tier":           {"type": "keyword"},
                        "lat":                 {"type": "float"},
                        "lng":                 {"type": "float"},
                        "created_at":          {"type": "date"},
                        "timeline":            {"type": "object", "enabled": False},
                        "neighborhood":        {"type": "object", "enabled": False},
                        "climate_risk":        {"type": "object", "enabled": False},
                        "esql_insights":       {"type": "object", "enabled": False},
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
        }
        for idx, body in mappings.items():
            try:
                exists = await self._es.indices.exists(index=idx)
                if not exists:
                    await self._es.indices.create(index=idx, body=body)
                    logger.info("[Elastic] created index %s (ELSER semantic_text enabled)", idx)
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
        """Bulk-index a list of property events. Returns count indexed."""
        if not self._es or not events:
            return 0
        ops = []
        for ev in events:
            ops.append({"index": {"_index": IDX_EVENTS}})
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
        """
        Hybrid search over blueprint_events for a given property.
        Uses Elastic MCP if available (hybrid ELSER semantic+BM25+reranking).
        Falls back to direct SDK with text_similarity_reranker, then plain BM25.
        """
        if self._mcp_available:
            return await self._mcp_search(address_hash, query, size)
        return await self._direct_search(address_hash, size, query_text=query)

    async def _direct_search(self, address_hash: str, size: int, query_text: str = "") -> list[dict]:
        if not self._es:
            return []

        # ── Try semantic reranking first (Elastic built-in .rerank-v1-elasticsearch) ──
        # Reranking reorders BM25 hits by semantic similarity to the query text,
        # which surfaces the most risk-relevant events rather than just newest ones.
        if query_text:
            try:
                resp = await self._es.search(
                    index=IDX_EVENTS,
                    body={
                        "retriever": {
                            "text_similarity_reranker": {
                                "retriever": {
                                    "standard": {
                                        "query": {"term": {"address_hash": address_hash}}
                                    }
                                },
                                "field": "description",
                                "inference_id": ".rerank-v1-elasticsearch",
                                "inferenceText": query_text,
                                "rank_window_size": min(size * 2, 100),
                            }
                        },
                        "size": size,
                    }
                )
                results = [h["_source"] for h in resp["hits"]["hits"]]
                if results:
                    logger.info("[Elastic] Reranked search: %d docs ranked by semantic relevance", len(results))
                    return results
            except Exception as e:
                logger.debug("[Elastic] Reranking not available: %s — falling back to BM25", e)

        # ── Fallback: plain BM25 sorted by date ───────────────────────────────────
        try:
            resp = await self._es.search(
                index=IDX_EVENTS,
                query={"term": {"address_hash": address_hash}},
                sort=[{"event_date": {"order": "asc"}}],
                size=size,
            )
            return [h["_source"] for h in resp["hits"]["hits"]]
        except Exception as e:
            logger.warning("[Elastic] direct search failed: %s", e)
            return []

    async def index_stats(self) -> dict:
        """Return document counts for all Blueprint indices."""
        if not self._es:
            return {}
        stats = {}
        for idx in [IDX_CASES, IDX_EVENTS, IDX_REPORTS, IDX_SHARED, IDX_WATCHED]:
            try:
                resp = await self._es.count(index=idx)
                stats[idx] = resp.get("count", 0)
            except Exception:
                stats[idx] = -1
        return stats

    def _mcp_http_client(self) -> httpx.AsyncClient:
        """Return an httpx client pre-configured with the Elastic API key auth header."""
        return httpx.AsyncClient(
            headers={"Authorization": f"ApiKey {settings.ELASTIC_API_KEY}"},
            timeout=30.0,
        )

    async def _mcp_session(self):
        """Async context manager: opens a Streamable HTTP MCP session to Elastic Agent Builder."""
        http_client = self._mcp_http_client()
        ctx = streamablehttp_client(settings.ELASTIC_MCP_URL, http_client=http_client)
        streams = await ctx.__aenter__()
        # streamablehttp_client returns (read, write) or (read, write, get_session_id)
        read, write = streams[0], streams[1]
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        return ctx, session

    async def _mcp_search(self, address_hash: str, query: str, size: int) -> list[dict]:
        """
        Call Elastic Agent Builder MCP search tool via Streamable HTTP transport.
        Falls back to direct SDK on any failure.
        """
        try:
            http_client = self._mcp_http_client()
            async with streamablehttp_client(settings.ELASTIC_MCP_URL, http_client=http_client) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    # Discover the actual search tool name dynamically
                    tools_resp = await session.list_tools()
                    tool_names = [t.name for t in (tools_resp.tools or [])]
                    search_tool = _pick_search_tool(tool_names)
                    if not search_tool:
                        logger.warning("[Elastic MCP] no search tool found, tools: %s", tool_names)
                        return await self._direct_search(address_hash, size)
                    result = await session.call_tool(
                        search_tool,
                        arguments={
                            "query": query or f"property records {address_hash}",
                            "index": IDX_EVENTS,
                            "size": size,
                        },
                    )
                    raw = _parse_mcp_result(result)
                    hits = raw.get("hits", raw) if isinstance(raw, dict) else raw
                    if isinstance(hits, list):
                        return [h.get("_source", h) for h in hits]
        except Exception as e:
            logger.warning("[Elastic MCP] search failed: %s — falling back to direct SDK", e)
        return await self._direct_search(address_hash, size)

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
            http_client = self._mcp_http_client()
            async with streamablehttp_client(settings.ELASTIC_MCP_URL, http_client=http_client) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_resp = await session.list_tools()
                    tool_names = [t.name for t in (tools_resp.tools or [])]
                    esql_tool = _pick_esql_tool(tool_names)
                    if not esql_tool:
                        return None
                    result = await session.call_tool(esql_tool, arguments={"query": query})
                    raw = _parse_mcp_result(result)
                    if isinstance(raw, list):
                        return raw
                    return raw.get("rows", []) if isinstance(raw, dict) else None
        except Exception as e:
            logger.debug("[Elastic MCP] ES|QL failed: %s", e)
            return None

    async def list_mcp_tools(self) -> list[str]:
        """List all tools available on the Elastic Agent Builder MCP server."""
        if not settings.ELASTIC_MCP_URL or not settings.ELASTIC_API_KEY:
            return []
        try:
            http_client = self._mcp_http_client()
            async with streamablehttp_client(settings.ELASTIC_MCP_URL, http_client=http_client) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_resp = await session.list_tools()
                    return [t.name for t in (tools_resp.tools or [])]
        except Exception as e:
            logger.debug("[Elastic MCP] list_tools failed: %s", e)
            return []

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


def _pick_search_tool(tool_names: list[str]) -> str | None:
    """
    Find the search tool from whatever Elastic Agent Builder exposes.
    Elastic Serverless uses platform.core.* naming; classic Kibana uses shorter names.
    We rank known candidates and fall back to any name containing 'search'.
    """
    candidates = [
        # Elastic Serverless Agent Builder (platform.core.*)
        "platform.core.search",
        # Classic / older naming
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
    """Find the ES|QL tool from whatever Elastic Agent Builder exposes."""
    candidates = [
        "platform.core.esql",
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
