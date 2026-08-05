# LLHB v1 — Owner Decisions (2026-08-05)

Recorded verbatim in substance from the owner's approval of the LLHB v1 design
proposal. These decisions govern v1; deviations require a new owner decision.
Labels follow the notebook convention: every item below is [OWNER-DECISION].

1. **Branch:** `feat/llhb-v1`. Branch-prefix governance unchanged (no `eval/`
   prefix added).
2. **100% deterministic v1.** No LLM-as-a-judge in v1; no ADR is created to
   authorize one. The accepted doctrine stands: no judge, no rubric, no jurist;
   LLHB v1 is not a general legal-correctness benchmark. H5/H6-style semantic
   or interpretive evaluation may be documented as future work but is not part
   of any v1 score.
3. **Language:** Norwegian bokmål only. English/multilingual out of scope for
   v1; possible v2.
4. **Models:** one current flagship/general-purpose model per provider
   (Anthropic, OpenAI, Google). Exact model IDs selected and recorded at
   benchmark-run time. No mini/cheap-model comparisons in the primary v1
   matrix.
5. **Dataset publication:** freeze before benchmark execution with lovverk
   commit SHA, lovspor commit SHA, manifest metadata, canonical JSONL SHA-256
   and a freeze tag. Frozen cases are NOT publicly released before execution;
   the dataset is published together with the results.
6. **Home:** `benchmarks/llhb/`. LLHB is conceptually separate from the
   persona-driven `evals/` suite; reuse infrastructure where useful without
   blurring the distinction.
7. **Quote handling:** quote-free dataset accepted. Statutory source text is
   never copied into the lovspor repo. Authoritative quotes are stored as
   stable coordinates (slug, section_id, occurrence where applicable, character
   span where applicable, source hash) and materialized at evaluation time from
   the pinned lovverk state. Adversarial fabricated quotes may be stored
   directly (not statutory source text).
8. **Candidate generation:** ground truth selected and validated from the
   corpus first; an LLM may only phrase or transform already-grounded cases and
   never defines legal ground truth. The phrasing model belonging to a
   benchmarked family is not itself a blocker provided: corpus-derived ground
   truth, deterministic candidate validation, dedup/near-dedup, and final
   selection independent of benchmark outcomes.
9. **Taxonomy accepted as working design:** C1 50, C2 40, C3 35, C4 30, C5 15,
   C6 35, C7 25, C8 20 — total 250; ~400 candidates for attrition.
10. **Validation accepted:** deterministic CorpusReader/MCP-equivalent
    validation for every candidate; 10% manual spot-check overall; 100% manual
    review for C5 and C8.
11. **Runner:** local corpus / stdio path, not the hosted production endpoint;
    the benchmark must not be constrained or distorted by hosted quotas. Claude
    may use native MCP. OpenAI/Gemini may use an adapter/function-calling
    bridge only if tool schemas are equivalent, information available to the
    models is equivalent, payloads are logged, and transport differences are
    documented.
12. **Existing evals unchanged.** LLHB must explicitly document the
    distinction: existing evals — "If the expected Lovspor tools are invoked,
    do the tools behave correctly?"; LLHB — "When a real model is given access
    to Lovspor, does it hallucinate statutory citations less?"
13. **Security finding out of scope:** `cryptography 49.0.0` /
    PYSEC-2026-3552 is NOT fixed on the LLHB branch; recorded as an unrelated
    finding for a separate `fix/` branch.

Stage 1 scope granted: documentation structure, methodology/specification
documents, dataset schema, freeze/versioning protocol, deterministic scoring
rules, experiment metadata format, matching notebook research-log structure,
and recording of these decisions. Explicitly not yet authorized: candidate
generation, model runs, publication claims, architecture changes, embeddings
changes, semantic/LLM judging.
