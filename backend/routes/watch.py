"""
Watchlist routes — add/remove/list properties monitored for 24 h re-analysis.

POST   /api/watch              → add a property to the watchlist
DELETE /api/watch/{hash}       → remove a property from the watchlist
GET    /api/watch              → list all watched properties
GET    /api/watch/{hash}       → check whether a property is currently watched

The background re-analysis loop lives in main.py (runs every 24 h at startup).
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.elastic_client import elastic

logger = logging.getLogger(__name__)
router = APIRouter()

IDX_WATCHED = "blueprint_watched"


class WatchRequest(BaseModel):
    address_hash: str
    normalized_address: str


@router.post("/watch")
async def add_watch(req: WatchRequest):
    """Add a property to the watchlist for periodic re-analysis."""
    if not elastic.available:
        raise HTTPException(status_code=503, detail="Elasticsearch not available")

    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "address_hash":       req.address_hash,
        "normalized_address": req.normalized_address,
        "watched_at":         now,
        "last_checked":       now,
    }
    await elastic.index_document(IDX_WATCHED, req.address_hash, doc)
    logger.info("[Watch] Added: %s", req.normalized_address)
    return {"watched": True, "address_hash": req.address_hash}


@router.delete("/watch/{address_hash}")
async def remove_watch(address_hash: str):
    """Remove a property from the watchlist."""
    if not elastic.available:
        raise HTTPException(status_code=503, detail="Elasticsearch not available")
    try:
        await elastic._es.delete(index=IDX_WATCHED, id=address_hash)
        logger.info("[Watch] Removed: %s", address_hash)
        return {"removed": True, "address_hash": address_hash}
    except Exception:
        raise HTTPException(status_code=404, detail="Property not on watchlist")


@router.get("/watch/{address_hash}")
async def check_watch(address_hash: str):
    """Check whether a specific property is currently on the watchlist."""
    if not elastic.available:
        return {"watched": False}
    try:
        await elastic._es.get(index=IDX_WATCHED, id=address_hash)
        return {"watched": True, "address_hash": address_hash}
    except Exception:
        return {"watched": False, "address_hash": address_hash}


@router.get("/watch")
async def list_watched():
    """List all properties currently on the watchlist, newest first."""
    if not elastic.available:
        return []
    try:
        resp = await elastic._es.search(
            index=IDX_WATCHED,
            sort=[{"watched_at": {"order": "desc"}}],
            size=50,
        )
        return [h["_source"] for h in resp["hits"]["hits"]]
    except Exception:
        return []
