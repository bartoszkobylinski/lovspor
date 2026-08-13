# LLHB v1 frozen pair — scorer v1 → v2 metric changelog

Pair: control `llhb-v1-run-20260812-frozen2` vs treatment
`llhb-v1-run-20260812-treatfrozen4` (250 cases per arm, `claude-opus-5`
via Claude Code CLI 2.1.228, runner commit `981dbdb`, corpus pin
`6ec7059d`, fairness gate PASS). The runs were executed once and never
edited; both reports below score the same committed records. Scoring is
a deterministic post-hoc layer, re-run after the 2026-08-13 audit found
four scoring-layer defects (issues #84–#87, ruling #29 in DECISIONS.md,
fixed in PR #88 as `llhb-score-v2` + `llhb-metrics-v2`).

Reports beside this file:

- v1: `llhb-v1-run-20260812-frozen2-vs-llhb-v1-run-20260812-treatfrozen4.json`
- v2: `llhb-v1-run-20260812-frozen2-vs-treatfrozen4-scorev2.json`

## Numbers (control → treatment; Δ = control − treatment; `*` = 95% bootstrap CI excludes 0)

| Metric | scorer v1 | scorer v2 | What changed and why |
|---|---|---|---|
| citation_hallucination_rate | 21.4% → 17.3% (ns) | **19.5% → 12.0%** `*` | #85 removed phantom ids («§ 8 første» → `8f`) from both numerators; #84 stopped counting refuted citations as asserted. The corpus effect emerges as significant. |
| citation_accuracy | 65.6% → 83.4% `*` | **67.7% → 87.2%** `*` | Phantom invalid citations left both denominators (#85). Direction unchanged, magnitude up. |
| correct_provision_identification | 8.9% → 31.1% `*` | **8.9% → 33.3%** `*` | Downstream of the same fixes. |
| misattribution_rate | 13.3% → 50.0% `*` (treatment worse) | **0.0% → 0.0%** | The v1 number was a measurement artifact: all 19 flagged answers opened with an explicit denial of the planted attribution (#84). No real misattribution exists in either arm. |
| false_premise_rejection_rate | 0.0% → 12.5% `*` | **0.0% → 22.9%** `*` | Refute-then-explain C6 answers now score as the rejections they are (#84). |
| quote_fidelity | 0.4% → 2.7% `*` | **11.1% → 20.8%** (ns) | Denominator corrected to checkable quotes (#86); unverifiable mass (517/638) reported as its own bucket. |
| no_invention_rate | 1/1 vs 0/5 (degenerate) | **7/7 vs 7/11** `*` (control better) | Typed C8 refusals quoting non-statute «» material no longer drop out as UNRESOLVED (#87). An honest negative: with tools the model overreaches on out-of-corpus questions more often than the tool-less control, which abstains cleanly. |
| post_retrieval_hallucination_rate | — → 19.4% | **— → 8.7%** | Treatment-only; halved once phantom ids and refuted citations left the numerator. |

## Reading guard

Values are properties of one model+harness pair on this corpus and
dataset (METHODOLOGY); the stability subset (ruling #26, 30×5) has not
yet run, so single-run sampling noise is not yet quantified. The v1
report is retained, not retracted: publishing the defective numbers
never happened, and the diff above is the audit trail.
