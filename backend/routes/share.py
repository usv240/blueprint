"""
Share link routes — create and retrieve public property report URLs.

POST /api/share/{address_hash}  → creates a 90-day share link, returns {share_id, url}
GET  /api/share/{share_id}      → returns the full report JSON (public, no auth required)
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from backend.config import settings
from backend.services.elastic_client import IDX_REPORTS, elastic

logger = logging.getLogger(__name__)
router = APIRouter()

IDX_SHARED = "blueprint_shared"
_TTL_DAYS  = 90


@router.post("/share/{address_hash}")
async def create_share_link(address_hash: str):
    """Create a 90-day public share link for an existing property report."""
    if not elastic.available:
        raise HTTPException(status_code=503, detail="Elasticsearch not available")

    # Verify the report exists
    try:
        doc    = await elastic._es.get(index=IDX_REPORTS, id=address_hash)
        report = doc["_source"]
    except Exception:
        raise HTTPException(status_code=404, detail="Report not found — run an analysis first")

    share_id   = uuid.uuid4().hex[:16]
    now        = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=_TTL_DAYS)).isoformat()

    shared_doc = {
        "share_id":     share_id,
        "address_hash": address_hash,
        "report":       report,
        "created_at":   now.isoformat(),
        "expires_at":   expires_at,
    }
    await elastic.index_document(IDX_SHARED, share_id, shared_doc)

    share_url = f"{settings.APP_URL}/app.html?share={share_id}"
    logger.info("[Share] Created share link %s for %s", share_id, address_hash)
    return {
        "share_id":   share_id,
        "url":        share_url,
        "expires_at": expires_at,
    }


@router.get("/share/{share_id}")
async def get_shared_report(share_id: str):
    """Retrieve a publicly shared report by its share ID."""
    if not elastic.available:
        raise HTTPException(status_code=503, detail="Elasticsearch not available")
    try:
        doc    = await elastic._es.get(index=IDX_SHARED, id=share_id)
        shared = doc["_source"]
    except Exception:
        raise HTTPException(status_code=404, detail="Shared report not found or expired")

    expires_at = shared.get("expires_at")
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Share link has expired")
        except ValueError:
            pass  # malformed date — allow through

    return shared.get("report", {})
