# LLHB Confirmatory Analysis Plan — claude-fable-5 replication (v1)

**Status: FROZEN at commit time (2026-08-15), before any `claude-fable-5`
model call.** This document is the preregistration for the confirmatory
replication mandated by DECISIONS.md ruling #30. Each arm's run metadata
MUST carry this file's blob SHA-256 as `analysis_plan_sha256`; a run whose
metadata does not reference this exact document is not governed by it and
cannot claim confirmatory status. Any edit to this file after the first
Fable call creates a new plan version, and results analysed under the new
version are diagnostic/post-hoc.

## 1. Experiment

* **Dataset:** LLHB v1 frozen set, 250 cases, canonical JSONL checksum
  `118eec543609442693643b3bdf29b3ec9aac30f0bee889afd6f47eefe07a4cc5`
  (`dataset/frozen/llhb-v1.lock.json`). No case is added, removed or
  edited. Errata, if any, follow FREEZE.md §5 and demote the run to
  LLHB v1.x with the delta stated.
* **Model:** requested `claude-fable-5` via the same Claude Code CLI
  harness as the Opus pair (TOOLING.md "Recorded harness caveat" applies
  verbatim). Provenance per ruling #30(h): requested identifier, returned
  identifier per case (from stream-json transcripts), CLI version, run
  timestamps.
* **Arms:** control = no tools; treatment = the pinned local lovspor MCP
  server at lovverk `6ec7059d`, surface per the committed apparatus
  document current at run time. The fairness gate
  (`check_fairness.py --frozen`) is mandatory before scoring.
* **Scorer:** the frozen semantic layer `llhb-score-v2` /
  `llhb-stance-v1` (DECISIONS.md ruling #30(b) — no cue-list, refusal,
  parsing or classification change may be motivated by these outputs).
  The aggregation layer implementing THIS plan's estimands and reason
  codes lands before the run and is pinned by the pair manifest's
  `scorer_commit`.

## 2. Primary estimand

Δ = P(H1 | control) − P(H1 | treatment)

where H1 is **answer-level**: an answer counts once if it contains at
least one asserted citation classified H1 (non-existent statutory basis),
regardless of how many. **Denominator: all 250 frozen cases per arm.**
This is the unconditional rate — NOT conditional on the answer containing
any citation. Positive Δ favours treatment.

## 3. Missing data and reason codes

Every scored case carries exactly one of:
`PASS | FAIL | UNRESOLVED_SEMANTIC | SCORER_ERROR | MODEL_ERROR`
(criterion-level detail beneath; the code is per case × arm).

* **MODEL_ERROR** — terminal transport/provider failure after the
  orchestrator's bounded retry. The affected **pair** (union across arms)
  drops from the primary computation.
* **SCORER_ERROR** — the frozen scorer crashed or could not process the
  output. Recorded, never patched-and-rescored in flight (ruling #30(b)).
* **UNRESOLVED_SEMANTIC** — the scorer ran and states it cannot classify.
  Not an error; stays in denominators as specified per metric.

**Eligibility gates (data integrity, checked BEFORE effect
interpretation):**

* MODEL_ERROR: ≤ 5 affected pairs / 250 (2%). More → the run is not
  eligible for a confirmatory verdict.
* SCORER_ERROR: ≤ 2 affected pairs / 250 (0.8%). More → not eligible.
* Both counts, per arm and as affected pairs, are reported in the run
  report regardless of outcome.

**Worst-case missingness bound:** with m ≥ 1 dropped pairs, the report
also states the primary result under the most unfavourable assignment of
all m missing pair-differences (each ∈ {−1, 0, +1}). The verdict
*confirmed* additionally requires that this worst-case assignment does
not reverse the sign of the effect.

## 4. Inference

* **Delta CI:** paired bootstrap; n = 10,000; seed = 42; unit = paired
  case; pairs resampled with replacement; statistic = Δ of §2; two-sided
  95% percentile interval. (n was raised from the 2,000 used in
  `llhb-metrics-v2` by ruling #30(e), before this freeze.)
* **Arm rates:** 95% Wilson score intervals.
* **Verdict (primary only):**
  * lower CI bound > 0 → **confirmed**
  * CI contains 0 → **inconclusive**
  * upper CI bound < 0 → **reversed / evidence of harm**

  No other vocabulary carries verdict weight. Discussion prose may say
  what it likes; the preregistered verdict is one of these three words.

## 5. Secondary metrics (no confirmatory labels)

Reported as point estimates with CIs; none receives a verdict:

1. Citation coverage per arm (answers with ≥ 1 asserted citation / 250).
2. Conditional citation hallucination rate (the v1 headline definition).
3. Citation accuracy over resolved citation instances.
4. Valid citation instances per answer; invalid citation instances per
   answer (absolute volumes — the 61 vs 63 class of finding is reported,
   never hidden).
5. Correct provision identification.
6. False-premise handling.
7. Quote fidelity over checkable quotes, with the unverifiable bucket
   beside it.
8. C8 three-way: PASS / FAIL / UNRESOLVED out of all 20 per arm
   (ruling #30(c)); a resolved-case ratio may appear only under that
   explicit label.
9. Post-direct-retrieval hallucination rate — renamed to match its
   implementation (a successful `get_section` of the expected pair);
   the broader "retrieved correct material" reading is out of scope.

## 6. Ceremony

analysis-plan (this file, hashed) → run control → freeze/hash control →
run treatment → freeze/hash treatment → pair manifest → score → report.

* **Pair manifest:** scoring executes only against a manifest whose
  fields (benchmark, `analysis_plan_sha256`, `dataset_sha256`,
  `scorer_commit`, `runner_commit`, `system_prompt_sha256`,
  `control_run_sha256`, `treatment_run_sha256`, model identifiers,
  corpus snapshot) all verify. Run hashes are SHA-256 over the exact
  UTF-8 bytes of the committed `records.jsonl` (LF, final newline
  included). "Aggregate scoring MUST NOT execute until a valid pair
  manifest exists and all referenced hashes verify."
* **Monitoring vs inspection (ruling #30(d), verbatim):** "During arm
  execution, operational monitoring MAY inspect completion status,
  latency, exit codes, MODEL_ERROR counts, transport errors, and other
  content-independent health signals. Response content MUST NOT be
  inspected before both arms are complete and cryptographically frozen.
  Any known violation MUST be disclosed in the run report."
* Re-running the scorer on identical inputs is a reproducibility test,
  not a violation.

## 7. Language pre-commitment

A confirming result is described as: *"the treatment effect replicated
across two model families on the same frozen challenge set."* It is
model generality, not dataset generality, and not "two independent
studies" — item difficulty is shared by design, which is what makes the
cross-model comparison clean. Contamination note: the requested model
generation predates the public release of LLHB v1 (2026-08-14); the
paper cites provider release documentation and states exactly what the
recorded identifiers prove, no more.

## 8. Out of scope of this plan

The paraphrase/held-out slice, OpenAI/Gemini runs (ruling #30(g): new
private set under public hash commitment), any scorer semantic change,
and any dataset change. Each is its own later, separately frozen step.
