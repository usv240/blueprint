# ADR-0002: Elastic as the intelligence layer, not just storage

**Status:** Accepted  
**Date:** 2026-05-12

---

BLUEPRINT needs somewhere to store property events, cross-reference them across analyses, and find patterns that no single-property analysis can see. The obvious candidates were PostgreSQL, a dedicated vector DB like Pinecone, or MongoDB Atlas.

Postgres handles the storage and SQL part fine, but semantic search over heterogeneous property records requires something else. Pinecone gives you dense vector similarity, but nothing else: no BM25 hybrid, no aggregations, no reverse-search, no geo queries. MongoDB added vector search relatively recently and it works, but significant_terms, percolator, and ES|QL are not in its vocabulary.

Elastic has all of it. More importantly, each capability maps to something the product actually needs:

ELSER over the events index means SynthesisAgent can find semantically relevant events even when the exact keywords don't match. A permit description that says "structural modification" and a deed note that says "major renovation" should surface together. BM25 alone misses that.

RRF hybrid retrieval blends the lexical and semantic rankings instead of picking one. In practice this works better than either alone for property records, which mix structured data (dates, addresses, permit types) with unstructured descriptions.

ES|QL makes flip-fraud detection possible. The query that detects a permit opened in the 90 days before or after a sale is a straightforward STATS query over two event types. Doing the same in application code after fetching all events is possible but fragile.

The percolator inverts the usual search direction. Instead of querying Elastic for matches to a new document, it matches saved risk-profile queries against every new property as it completes analysis. That is how "2 saved risk profiles matched" shows up in the report without polling.

geo_distance and significant_terms are the memory layer features. As BLUEPRINT analyses more properties, geo_distance surfaces genuinely nearby comparable properties rather than falling back to same-state heuristics. significant_terms identifies which risk flags are statistically over-represented in each risk band across the entire corpus, which is something that emerges only after enough data accumulates.

The operational trade-off is a real dependency on an Elastic Cloud Serverless project. Every Elastic call is wrapped in a try/except so the pipeline degrades gracefully when it is unavailable, but the cross-property intelligence features do not work without it.
