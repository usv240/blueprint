# ADR-0002: Elastic as the Intelligence Layer, Not Just Storage

**Status:** Accepted  
**Date:** 2026-05-12

---

## Context

The pipeline needs a place to store property events, cross-reference them across analyses, and surface patterns that a single-property analysis cannot see. The obvious choices were:

| Option | What it provides | What it lacks |
|---|---|---|
| PostgreSQL / SQLite | Reliable storage, SQL queries | No semantic search; no vector embeddings; no percolator |
| Pinecone / Weaviate (pure vector DB) | Dense vector similarity | No BM25 hybrid; no ES\|QL aggregations; no reverse-search; no geo |
| MongoDB Atlas | Document storage + vector search | No ELSER; no significant_terms; no percolator |
| **Elastic Cloud Serverless (chosen)** | Full AI stack: ELSER + BM25 + reranker + ES\|QL + percolator + geo + aggregations | More operational surface area |

The hackathon is specifically the **Elastic track**, requiring meaningful MCP integration. But beyond compliance, Elastic uniquely offers capabilities that directly match BLUEPRINT's needs.

---

## Decision

Use Elastic as the **intelligence layer**, not just a data store. Every Elastic capability maps to a concrete product feature:

| Elastic capability | BLUEPRINT feature |
|---|---|
| ELSER sparse-vector semantic search | Retrieves semantically relevant events even when keywords don't match |
| RRF hybrid retriever (BM25 + ELSER) | Best-of-both: exact permit/address matches + semantic context |
| `text_similarity_reranker` | Reorders retrieved events by actual risk relevance before synthesis |
| ES\|QL cross-reference | Flip-fraud detection: correlates permit filing dates vs deed transfer dates |
| Percolator (reverse search) | Proactive: every new property is matched against saved risk profiles |
| `geo_distance` query | Cross-property intelligence: surfaces similar-risk properties within 50km |
| `significant_terms` aggregation | Identifies which risk flags are statistically over-represented in each risk band |
| Memory write-back (6 indices) | Each analysis compounds: the system gets smarter with every property analysed |
| Agent Builder MCP | Gemini agents call Elastic tools natively over Streamable HTTP |

---

## Consequences

**Positive:**
- The system has genuine cross-property intelligence — not just isolated analyses
- Percolator enables proactive alerting without polling
- Significant terms gives corpus-level insight no single-property analysis can produce
- The Elastic integration is deep enough to be the architectural centrepiece, not a checkbox

**Negative:**
- Requires an Elastic Cloud Serverless project (free trial available)
- All Elastic calls are wrapped in try/except so the pipeline degrades gracefully if unavailable

**Accepted trade-off:** The operational dependency on Elastic is justified by the unique capabilities it unlocks. Every feature degrades gracefully to a no-Elastic fallback.
