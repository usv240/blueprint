"""Provision Kibana data views for BLUEPRINT indices (idempotent).

Run: python kibana_setup.py
Creates one data view per Blueprint index so the data is explorable in Discover,
Lens, and dashboards. Safe to re-run — skips data views that already exist.
"""
import asyncio

import httpx

from backend.config import settings

BASE = settings.ELASTIC_MCP_URL.split("/api/agent_builder/mcp")[0].rstrip("/")
H = {"Authorization": f"ApiKey {settings.ELASTIC_API_KEY}", "kbn-xsrf": "true",
     "Content-Type": "application/json"}

# (index title, friendly name, time field)  — stable id = "blueprint-<index>"
DATA_VIEWS = [
    ("blueprint_reports", "BLUEPRINT Reports", "created_at"),
    ("blueprint_events",  "BLUEPRINT Events",  "event_date"),
    ("blueprint_cases",   "BLUEPRINT Cases",   "created_at"),
]


async def existing_titles(c: httpx.AsyncClient) -> set[str]:
    r = await c.get(f"{BASE}/api/data_views", headers=H)
    return {dv.get("title") for dv in r.json().get("data_view", [])}


async def main():
    async with httpx.AsyncClient(timeout=30.0) as c:
        have = await existing_titles(c)
        for title, name, time_field in DATA_VIEWS:
            if title in have:
                print(f"[skip] data view '{title}' already exists")
                continue
            body = {"data_view": {"title": title, "name": name,
                                  "timeFieldName": time_field,
                                  "id": f"blueprint-{title}"}}
            r = await c.post(f"{BASE}/api/data_views/data_view", headers=H, json=body)
            if r.status_code in (200, 201):
                print(f"[ok]   created data view '{name}' ({title}) "
                      f"id=blueprint-{title} time={time_field}")
            else:
                print(f"[fail] {title}: {r.status_code} {r.text[:300]}")


if __name__ == "__main__":
    asyncio.run(main())
