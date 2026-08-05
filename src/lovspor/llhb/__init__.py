"""LLHB (Lovspor Legal Hallucination Benchmark) deterministic tooling.

Stage 2 primitives only: citation extraction, act-name indexing, stance
classification, citation resolution against a corpus, quote-reference
materialization, corpus pinning, schema/canonicalization utilities and
candidate validation. No model calls, no candidate generation, no scoring
runs — those are later LLHB stages.

Everything here is deterministic and rule-based by design (ADR-0007): what
cannot be resolved by a frozen rule lands in an explicit unresolved bucket,
never in a guess. See ``benchmarks/llhb/TOOLING.md`` for the documented
contract of each module.
"""
