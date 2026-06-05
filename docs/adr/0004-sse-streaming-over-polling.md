# ADR-0004: SSE Streaming Over Polling or WebSocket

**Status:** Accepted  
**Date:** 2026-05-09

---

## Context

A full 7-agent analysis takes 30–90 seconds. Users need feedback during this time. Three options:

| Approach | Problem |
|---|---|
| Polling (`GET /api/status/{id}` every 2s) | Client hammers server; 2s delay between events; stale UI |
| WebSocket (bidirectional) | Overhead for a one-directional stream; harder to proxy on Cloud Run |
| **Server-Sent Events / SSE (chosen)** | Unidirectional server→client push; HTTP/1.1 compatible; Cloud Run native; browser EventSource API |

---

## Decision

`GET /api/analyze/stream?address=...` returns `Content-Type: text/event-stream`.

Each tool call in the ADK pipeline emits a typed event onto an `asyncio.Queue` bound to the request via a `ContextVar`. The FastAPI response generator reads from the queue and streams events to the browser:

```
event: step      → agent started, message + detail
event: finding   → agent completed, summary
event: complete  → full report JSON
event: debate_complete → debate result JSON
event: error     → pipeline error
```

The browser renders each event in real time: agent cards update status, the log drawer fills, and the verdict banner appears the moment synthesis completes — before the debate result arrives.

---

## Consequences

**Positive:**
- Zero polling overhead; events arrive within milliseconds of being emitted
- The debate-pending UI state (amber pill) is only possible because synthesis and debate arrive as separate events
- Cloud Run handles long-lived SSE connections natively (up to 3600s timeout)
- `[DONE]` sentinel closes the connection cleanly; browser `EventSource` auto-reconnects on network errors

**Negative:**
- SSE is unidirectional — user cannot send mid-stream instructions (not needed here)
- Each streaming connection holds a Cloud Run instance alive for the analysis duration

**Accepted trade-off:** The real-time agent progress is central to the product experience — watching 7 agents run in sequence is part of what makes the pipeline legible to a non-technical buyer. The Cloud Run cost is justified.
