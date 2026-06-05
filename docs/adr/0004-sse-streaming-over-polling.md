# ADR-0004: SSE streaming over polling or WebSocket

**Status:** Accepted  
**Date:** 2026-05-09

---

A full analysis takes 30 to 90 seconds depending on API response times and Gemini latency. Users need to see something happening during that time, and they need to see the right something: not a spinner, but the actual work.

Polling on a status endpoint was the first instinct. Every 2 seconds, the browser asks "are we done yet?" This works but has annoying characteristics: 2-second lag between events, unnecessary requests when nothing has changed, and the UI only knows "done" or "not done" rather than "PermitAgent just flagged 30 open permits."

WebSockets seemed like the natural upgrade, but they are bidirectional and BLUEPRINT does not need that. The client never needs to send mid-stream messages. WebSockets also add proxying complexity on Cloud Run that SSE avoids entirely.

Server-Sent Events fit exactly. The FastAPI route for `/api/analyze/stream` opens a `text/event-stream` response and an asyncio Queue is bound to the request via a ContextVar. Each tool call in the ADK pipeline emits a typed event onto that queue. The response generator reads from it and flushes to the browser. The browser's native EventSource API handles reconnection automatically.

Five event types come through the stream:

- `step` fires when an agent starts a tool call, with a message and optional detail
- `finding` fires when an agent completes, with a summary of what it found
- `complete` carries the full synthesis report JSON
- `debate_complete` carries the debate result separately, after synthesis, because the debate adds 10+ seconds
- `error` closes the stream if something goes wrong

The separate `debate_complete` event is what makes the "debate in progress" UX possible. The report appears immediately after synthesis with a pulsing amber indicator. When the debate finishes, the score and verdict update in place. That two-stage reveal is only possible because synthesis and debate arrive as separate events rather than one combined response.

Cloud Run handles long-lived SSE connections natively up to the 3600 second timeout, which is more than enough. The `[DONE]` sentinel closes the connection cleanly.
