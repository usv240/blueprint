# ADR-0003: Seven Specialised Agents Over One General Agent

**Status:** Accepted  
**Date:** 2026-05-08

---

## Context

The property analysis task requires gathering data from six independent public APIs, synthesising it into a risk score, and then running an adversarial review. This could be implemented as:

| Approach | Problem |
|---|---|
| One general agent with all tools | Context window blooms; hard to reason about; one API failure can corrupt the whole run |
| Two agents (data + synthesis) | Better, but data stage is still too broad; no separation between data types |
| **Seven specialised agents in sequence (chosen)** | Each agent has one job, one tool, one output key; failures are isolated |

---

## Decision

Seven `LlmAgent` instances in a Google Cloud ADK `SequentialAgent`:

```
GeocoderAgent  → normalise address + geocode + open Elastic case file
DeedAgent      → deed/sale history from Socrata county APIs
PermitAgent    → building permits from 50+ city open-data portals
ClimateAgent   → FEMA flood zone + USGS earthquake catalog
NeighborhoodAgent → EPA EJSCREEN + OpenStreetMap amenities
SynthesisAgent → Elastic hybrid search + ES|QL → Buyer Risk Score
DebateAgent    → OptimistAgent vs PessimistAgent → final verdict
```

Each agent writes its findings to a shared `ContextVar` session state and to Elasticsearch. Downstream agents read upstream state but do not call upstream APIs again.

---

## Consequences

**Positive:**
- Failures are isolated: if PermitAgent fails, ClimateAgent still runs
- Each agent's system prompt is narrow and specific — less hallucination surface
- Progress is observable: SSE stream emits per-agent status events in real time
- Agents can run with different tool configurations (PermitAgent uses FunctionTool; SynthesisAgent uses MCPToolset + FunctionTool)
- The pipeline is explainable: the buyer's report shows which agent found what

**Negative:**
- Sequential execution adds latency vs parallel data collection
- Seven LlmAgent instantiations at startup vs one

**Accepted trade-off:** Sequential isolation is worth the latency for data integrity. The user sees real-time agent progress via SSE, so the wait feels active, not passive.
