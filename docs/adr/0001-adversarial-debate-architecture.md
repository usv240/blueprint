# ADR-0001: Adversarial Debate Before Every Verdict

**Status:** Accepted  
**Date:** 2026-05-10

---

## Context

Every AI-generated risk score is a single point estimate from a model that has already committed to a narrative. Anchoring bias — the tendency to weight early evidence too heavily — is a known failure mode in LLM reasoning chains. A single Gemini call producing "Risk score: 42, NEGOTIATE" gives the buyer no way to understand how confident that number is, or what the strongest counter-arguments are.

Property purchase decisions involve hundreds of thousands of dollars. A false negative (underestimating risk) or false positive (overestimating risk) both cause real harm.

Options considered:

| Approach | Problem |
|---|---|
| Single synthesis call | Anchors on early data; no confidence signal; opaque to buyer |
| Ensemble average (3 calls, average scores) | Reduces variance but all calls share same framing; no adversarial signal |
| External fact-checker pass | Adds latency; still same model, same anchoring |
| **Adversarial debate (chosen)** | Forces both sides to be argued; produces confidence interval; score movement is explainable |

---

## Decision

After SynthesisAgent produces an initial score, DebateAgent runs two opposing Gemini sub-agents in sequence:

- **OptimistAgent** — instructed to argue the score is too *high*; must cite specific positive signals
- **PessimistAgent** — instructed to argue the score is too *low*; must cite specific risk factors

A VerdictAgent adjudicates and outputs a confidence-adjusted final score. The buyer sees all three positions — not just the verdict.

---

## Consequences

**Positive:**
- The score is explainable: "Synthesis said 50. Optimist argued 28. Pessimist argued 72. Final verdict 45 at MEDIUM confidence."
- Catches hallucinations: if Optimist and Pessimist both converge close together, confidence is HIGH; wide divergence → MEDIUM/LOW
- Unique product differentiator — no comparable property tool exposes its reasoning this way
- Buyers make better decisions with the debate transcript than with a bare number

**Negative:**
- Two extra Gemini calls per analysis (~10–15s additional latency)
- Scores are non-deterministic across runs — the same property can score slightly differently

**Accepted trade-off:** The latency is acceptable given the stakes of the decision. Non-determinism is mitigated by showing the debate range, not just the point estimate.
