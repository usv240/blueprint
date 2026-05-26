"""
Slack alert service — posts a formatted property risk alert to an Incoming Webhook.

Fires automatically when buyer_risk_score >= SLACK_ALERT_THRESHOLD (default 60).
Configure SLACK_WEBHOOK_URL in .env to enable. If the env var is empty the call
is silently skipped so the rest of the pipeline is unaffected.
"""
import logging

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

_LEVEL_COLOR = {
    "LOW":      "#22c55e",
    "MEDIUM":   "#eab308",
    "HIGH":     "#f97316",
    "CRITICAL": "#ef4444",
}


async def post_alert(report: dict) -> bool:
    """
    Post a Buyer Risk Score alert to Slack.
    Returns True if the webhook responded 200, False otherwise.
    Never raises — failures are logged as warnings.
    """
    if not settings.SLACK_WEBHOOK_URL:
        return False

    score   = report.get("buyer_risk_score", 0)
    level   = report.get("risk_level", "UNKNOWN")
    address = report.get("normalized_address", "Unknown address")
    flags   = report.get("flags", [])
    summary = report.get("summary", "")
    hash_id = report.get("address_hash", "")

    color      = _LEVEL_COLOR.get(level, "#94a3b8")
    flags_text = "\n".join(f"• {f}" for f in flags[:5]) if flags else "None detected"
    report_url = f"{settings.APP_URL}/api/report/{hash_id}" if hash_id else ""

    payload: dict = {
        "attachments": [
            {
                "color": color,
                "title": f"BLUEPRINT Property Alert — {level} RISK",
                "title_link": report_url,
                "text": f"*{address}*",
                "mrkdwn_in": ["text", "fields"],
                "fields": [
                    {
                        "title": "Buyer Risk Score",
                        "value": f"*{score}/100*",
                        "short": True,
                    },
                    {
                        "title": "Risk Level",
                        "value": level,
                        "short": True,
                    },
                    {
                        "title": "Risk Flags",
                        "value": flags_text,
                        "short": False,
                    },
                    {
                        "title": "Summary",
                        "value": summary[:300] + ("…" if len(summary) > 300 else ""),
                        "short": False,
                    },
                ],
                "footer": "BLUEPRINT · AI Due Diligence for Homes",
                "footer_icon": "https://cdn.jsdelivr.net/npm/emoji-datasource-apple/img/apple/64/1f3e0.png",
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.SLACK_WEBHOOK_URL, json=payload)
        if resp.status_code == 200:
            logger.info("[Slack] Alert sent for %s (score=%s, level=%s)", address, score, level)
            return True
        logger.warning("[Slack] Webhook returned HTTP %s", resp.status_code)
        return False
    except Exception as exc:
        logger.warning("[Slack] Alert failed: %s", exc)
        return False
