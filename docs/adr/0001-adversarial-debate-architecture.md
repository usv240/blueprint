# ADR-0001: Adversarial debate before every verdict

**Status:** Accepted  
**Date:** 2026-05-10

---

The first version of BLUEPRINT had SynthesisAgent produce a score and that was the answer. It worked, but something felt off. Run the same address twice and you'd get different scores. More importantly, there was no way to tell whether a score of 42 was a confident 42 or a Gemini guess dressed up as a number.

The problem is anchoring. Once a language model commits to a framing early in a reasoning chain, it tends to stick with it even when later evidence contradicts it. A permit backlog found in step 3 will colour how the model interprets the flood zone data in step 4. There's no neutral observer.

Three other approaches were considered. Averaging three independent synthesis calls reduces variance but all calls see the same data in the same order: you are averaging over noise, not over genuine disagreement. Adding a fact-checker pass is just another LLM pass with the same anchoring problem. Neither gives the buyer any signal about *how confident* the system is.

The adversarial debate does something different. After SynthesisAgent produces an initial score, DebateAgent runs two sub-agents in sequence with opposing mandates:

- **OptimistAgent** must argue the score is too high and cite specific reasons why
- **PessimistAgent** must argue the score is too low and cite specific reasons why

A VerdictAgent reads both arguments and produces the final confidence-adjusted score. The buyer sees all three positions: not just the number at the end.

The practical result: when OptimistAgent says 28 and PessimistAgent says 72, the system reports MEDIUM confidence and the buyer knows to dig deeper. When they both land near 40, that's a high-confidence score. The debate transcript also catches hallucinations: if Optimist is citing "zero environmental risk" but the PessimistAgent correctly notes the EPA data returned zeros (a known data gap), that tension surfaces before the buyer acts on it.

Two downsides worth being honest about. This adds roughly 10–15 seconds of latency per analysis: two extra Gemini calls. And scores are non-deterministic: the same address on two separate runs can score a few points differently. Neither is a dealbreaker. The latency is acceptable given the decision being made. The non-determinism is mitigated by surfacing the debate range rather than presenting a bare number as ground truth.

No comparable property intelligence tool shows the reasoning that produced the score. That's the differentiator.
