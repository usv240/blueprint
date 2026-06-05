# ADR-0003: Seven specialised agents over one general agent

**Status:** Accepted  
**Date:** 2026-05-08

---

The obvious first design was one general agent with access to all the tools. Give Gemini the address and a list of functions: geocode it, fetch permits, check flood zones, query EPA, call Elastic. Let the model decide what to call and in what order.

That works for a demo. It breaks in production.

Context blooms quickly when one agent is responsible for everything. A permit dataset for an active NYC address can return 30+ rows. The FEMA response includes polygon geometry. EPA EJSCREEN returns dozens of indicator fields. By the time the model reaches synthesis, it is working with a context that has accumulated noise from six different API calls, and the quality of the reasoning visibly degrades.

More practically, when one API fails, the whole analysis fails. If the Socrata permit endpoint times out, you lose the entire result, not just the permit section. There is no clean way to recover.

Seven specialised agents, each with one job and one tool, fixes both problems. Each agent has a narrow system prompt and a bounded context. If PermitAgent fails, ClimateAgent still runs with no knowledge of or dependency on what happened upstream. The failure is isolated.

The shared state is simple: each agent writes its findings to a ContextVar session dict and to Elasticsearch. Downstream agents read the upstream output but do not re-call upstream APIs. The final report reflects exactly which agents succeeded and which did not.

There is one real cost: sequential execution. Five data agents running in series takes longer than if they ran in parallel. The SSE stream mitigates this perceptually. The user watches each agent card update as the pipeline progresses, which makes 45 seconds feel more like watching a process than waiting. But the latency is real and worth acknowledging.

The pipeline is also more explainable this way. The buyer's report shows which agent found what. "30 open permits flagged by PermitAgent" is attributable. "Gemini found some permits" is not.
