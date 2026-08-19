# LLHB Confirmatory Analysis Template — v1

**Status: normative template (DECISIONS.md ruling #30).** Every
provider-specific confirmatory Analysis Plan instantiates this template
and states any deviation explicitly. **The template MUST NOT change
between providers of one replication family:** a change creates
template v2, and results analysed under different template versions are
not presented as one replication family. This is what forecloses the
objection that each provider got a different test.

A provider plan adds ONLY provider bindings — model identifier, driver
and its commit, tool transport (MCP config / adapter), tool-surface
hash, provider-specific fairness checks — never different estimands,
gates, inference parameters or verdict rules.

## Inherited invariants (identical in every provider plan)

1. **Primary estimand.** Δ = P(H1 | control) − P(H1 | treatment),
   answer-level asserted-H1, denominator = all frozen cases per arm,
   unconditional on citing behaviour.
2. **Reason codes.** Every scored case carries exactly one of
   `PASS | FAIL | UNRESOLVED_SEMANTIC | SCORER_ERROR | MODEL_ERROR`.
3. **Eligibility gates** (data integrity, checked before any effect
   interpretation): MODEL_ERROR ≤ 5 affected pairs / 250 (union across
   arms, pairwise drop from the primary); SCORER_ERROR ≤ 2 affected
   pairs / 250. Exceeding either → no confirmatory verdict. Counts
   reported regardless of outcome.
4. **Worst-case missingness bound.** With m ≥ 1 dropped pairs, the
   report states the primary result under the most unfavourable
   assignment of all m missing pair-differences; *confirmed*
   additionally requires that this assignment does not reverse the
   effect sign.
5. **Inference.** Paired bootstrap: n = 10,000, seed = 42, unit =
   paired case, pairs resampled with replacement, statistic = Δ,
   two-sided 95% percentile CI. Arm rates: 95% Wilson score intervals.
6. **Verdict vocabulary (primary only).** Lower CI bound > 0 →
   **confirmed**; CI contains 0 → **inconclusive**; upper bound < 0 →
   **reversed / evidence of harm**. No other word carries verdict
   weight. Secondary metrics receive point estimates and CIs, never
   confirmatory labels.
7. **Scorer freeze.** The semantic scoring layer is frozen per ruling
   #30(b); confirmatory outputs never extend cue lists, refusal
   patterns, parsing or classification. A scorer modification motivated
   by confirmatory outputs creates a new scorer version, and results
   rescored with it are diagnostic/post-hoc.
8. **Ceremony.** Plan frozen and hashed before the first model call
   (`analysis_plan_sha256` in each arm's run metadata) → run control →
   freeze/hash → run treatment → freeze/hash → pair manifest → score →
   report. Aggregate scoring is manifest-gated and fail-closed
   (`score_run.py --manifest`). Monitoring-vs-inspection norm of ruling
   #30(d) applies verbatim.
9. **Provenance.** Requested model identifier, returned identifier per
   case, driver/CLI version, timestamps. Claims state exactly what
   these prove.
10. **Cross-provider comparison norm.** Providers run under separate
    harnesses, so absolute scores are never compared across providers:
    *"Under separate provider-specific harnesses, we compare the change
    induced by MCP access within each provider."* The replication claim
    is Δ_provider1 ≈ Δ_provider2 ≈ …, never "provider X beats provider
    Y because its score is higher" — that comparison would mix model,
    tokenizer, tool policy, inference stack and harness in one number.
11. **Public-dataset labelling.** Runs on the public LLHB v1 set are
    described as *evaluation on the public LLHB v1 benchmark*, with the
    publication date stated and contamination not excluded for models
    trained after it. "Unseen" claims are reserved for sets under
    pre-run hash commitment (ruling #30(g)).

## Instantiations

| Plan | Provider binding | Template |
|---|---|---|
| `ANALYSIS-PLAN-fable5-v1.md` | `claude-fable-5`, Claude Code CLI driver, native MCP | v1 |
