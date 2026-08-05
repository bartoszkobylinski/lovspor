# LLHB v1 — Lovspor Legal Hallucination Benchmark: Design Proposal

Status: APPROVED 2026-08-05 with owner amendments — see `DECISIONS.md`, which
supersedes this document wherever they differ. Material amendments: v1 is 100%
deterministic (§12 Tier 2 LLM-judge dropped from v1 entirely; no ADR authorizes
a judge), branch is `feat/llhb-v1` (§3.1 resolved), bokmål-only confirmed,
dataset published only together with results. This file is kept as the
historical design proposal.
Date: 2026-08-05
Branch: `feat/llhb-v1`

---

## 1. Summary

LLHB (Lovspor Legal Hallucination Benchmark) measures whether giving an LLM access to
the Lovspor Norwegian-law MCP reduces legal hallucinations, compared with the same model
answering without it.

- 250 frozen cases (from a ~400 candidate pool), ground-truthed against the lovverk
  corpus at a pinned commit.
- 3 provider families (Anthropic Claude, OpenAI, Google Gemini) × 2 conditions
  (control = no tools, treatment = Lovspor tools).
- Headline metrics are 100% deterministic, scored by Lovspor's own fail-closed oracles
  (`validate_citation`, `verify_quote`). Semantic metrics are a clearly separated
  secondary tier.
- Dataset is frozen (checksummed, corpus-pinned) **before** the first model call.
  Pre-registration is the credibility mechanism: a negative or mixed result ships.

This document is the deliverable of Task 1 (design only). It proposes structure,
taxonomy, schema, protocol, and staging. It requires explicit approval before any
candidate generation, runner code, or model evaluation begins.

## 2. Relationship to existing work

| Existing asset | What it is | LLHB relationship |
|---|---|---|
| `evals/` (root) | Permanent deterministic MCP eval suite: 9 personas × 10 YAML scenarios, replays `expected_tool_calls` against `CorpusReader` on a synthetic corpus; also an `--llm-driven` mode driving `claude -p` over a stdio MCP server (`evals/runner.py`) | **Not replaced.** Existing evals answer "if the correct tools are invoked, do the tools behave correctly?" LLHB answers "when a real LLM gets Lovspor, does its final legal answer hallucinate less?" LLHB reuses the `claude -p` driver, trace parser, and criterion evaluators; it does not touch the persona suite. |
| `benchmarks/embedding_comparison/` | One-off decision-support study (Sprint 9 embedding model pick), self-contained | LLHB follows the same pattern: self-contained study directory `benchmarks/llhb/`, zero collision. |
| Phase 3 "staleness benchmark" (notebook `publication-plan.md:237-274`) | Committed future benchmark: temporal staleness, exact-match scoring, "no judge, no rubric, no jurist", gated on the unbuilt Phase 2 amendment graph | **LLHB is a different, complementary benchmark.** LLHB measures hallucination/grounding now; it does not consume the amendment graph and does not replace the staleness benchmark. The publication-plan critique that provision-retrieval accuracy "tests our own product" is addressed in §14 (limitations): LLHB's headline is the *delta in model hallucination behaviour*, not Lovspor retrieval precision. |
| `CorpusReader` (`src/lovspor/mcp.py`) | Python corpus API returning the identical dicts the MCP tools return | LLHB's ground-truth validator and scorer import it directly — zero-network, deterministic, no rate limits. |

## 3. Conflicts between the LLHB prompt and existing governance

Found during inspection. Each needs an owner decision or is resolved as noted.

### 3.1 Branch name `eval/llhb-v1` is illegal under current rules
Global + project rules define a closed prefix list: `feat/ fix/ refactor/ test/ docs/`.
The prompt mandates `eval/llhb-v1`. Current branch is `eval/llhb-v1` (as instructed).
**Recommendation:** rename to `feat/llhb-v1` (one command), or explicitly extend the
prefix list. Owner decides.

### 3.2 LLM-as-judge contradicts accepted benchmark doctrine
Notebook `publication-plan.md:261`: scoring is exact match — "no judge, no rubric, no
jurist"; `:272`: "Do not attempt a legal-correctness benchmark"; reinforced by
design-principles §35/§40/§52. The prompt's H5/H6 metrics need semantic judgment.
**Resolution proposed:** two-tier scoring (§12). Tier 1 (headline, publishable) is 100%
deterministic and satisfies existing doctrine. Tier 2 (grounding/nuance) uses a frozen,
audited judge and is reported separately, never blended into Tier 1. This deviation
must be accepted via ADR-0007 before the freeze. If rejected, LLHB v1 ships Tier 1 only
— still a complete benchmark (H1–H4 + abstention-proxy are deterministic).

### 3.3 Frozen dataset with statutory text violates "Legal text never lives here"
`CLAUDE.md` and `architecture.md:9`: legal text never enters the lovspor repo (NLOD
posture; corpus lives in lovverk). A dataset storing verbatim statutory quotes would
break this.
**Resolution:** the frozen dataset is **quote-free**. True quotations are stored as
references — `(slug, section_id, occurrence, char_span, sha256 of normalized span)` —
and materialized at run/score time from the pinned lovverk commit. Fabricated trap
quotes are not statutory text and are stored verbatim. This also guarantees the
dataset cannot drift from the corpus pin.

### 3.4 ADR required before freeze
Notebook `docs/adr/README.md:76` lists benchmark methodology as ADR-mandatory. Next
free number: **ADR-0007**. The ADR index table must be updated. Owner acceptance of
ADR-0007 is a freeze precondition.

### 3.5 New terminology must land in notebook `terminology.md` first
`LLHB`, hallucination classes H1–H6, control/treatment, freeze/errata terms.
`terminology.md` already defines Benchmark / Ground Truth / Hallucination — H1–H6 must
extend, not contradict, those entries.

### 3.6 Publication scope is new, not conflicting
Repo docs target NLLP/NoDaLiDa 2027; no LinkedIn plan exists anywhere. The LinkedIn
plan is added scope → lives in the **private notebook**, not public docs (§16).

## 4. Research questions

**Primary:** Does providing an LLM with access to Lovspor reduce hallucinated Norwegian
statutory citations and unsupported legal claims, compared with the same LLM without
Lovspor?

**Secondary** (each maps to ≥1 metric in §12): non-existent-section citations (H1),
fabricated quotations (H3), false-premise rejection (H4), correct act/section
identification, grounding of claims in retrieved text (H5), post-retrieval
hallucination (H6), provider differences, category-level difficulty with Lovspor.

**Out of scope for v1:** general legal reasoning quality, legal advice correctness,
temporal/staleness questions (Phase 3's job), languages other than the dataset language,
non-statutory sources (case law, rundskriv, forarbeider).

## 5. Operational hallucination taxonomy

Frozen before any model run. Post-hoc changes require a new LLHB version (§10).

| ID | Definition | Oracle | Scoring class |
|---|---|---|---|
| H1 | Non-existent citation: cited (act, §) does not exist in the pinned corpus | `validate_citation` — strict slug match, fails closed, refuses `§§` ranges and act-less `§` as ambiguous (`mcp.py:647`) | Deterministic |
| H2 | Misattributed citation: real rule attributed to wrong act/section (vs case ground truth) | `validate_citation` + case's `expected_act_slug/section` | Deterministic |
| H3 | Fabricated quotation: text presented as verbatim statute fails verification against the cited provision | `verify_quote` — NFKC + punctuation-fold + footnote-strip + whitespace/case-normalized exact substring, in-band failure reasons (`mcp.py:1484`) | Deterministic |
| H4 | False-premise acceptance: model accepts/elaborates a planted false premise | Deterministic core (premise cites non-existent/repealed/misattributed provision and answer endorses it); nuanced endorsement → Tier 2 | Mixed |
| H5 | Unsupported legal claim: substantive claim presented as statutory law, unsupported by retrieved/cited material | Claim decomposition + support judgment | Tier 2 (judge) |
| H6 | Grounding failure after retrieval: correct material retrieved (deterministic from tool trace) but final answer contradicts/ignores it | Retrieval identity deterministic; contradiction judgment Tier 2; the H1-after-correct-retrieval sub-case is fully deterministic | Mixed |

Known corpus ambiguity classes the oracles already handle (from `tests/`): duplicate
§-ids within one law (betalingssystemloven double § 6-2; førerkortforskriften vedlegg
restarts) via `occurrence`, ` i`-suffix vs preposition, spaced letter suffixes,
repealed-law tombstones, occurrence renumbering across versions. Benchmark cases
exploit these deliberately (category C5) and the scorer must never guess occurrence.

## 6. Category taxonomy and target counts

| ID | Category | Primary classes | Frozen | Pool | Scoring |
|---|---|---|---:|---:|---|
| C1 | Ordinary factual statutory question (act + § answerable from corpus) | H1/H2/H5 baseline | 50 | 80 | Tier 1 + Tier 2 |
| C2 | Semantic discovery — user gives no §; model must find the provision | provision ID, H1 | 40 | 65 | Tier 1 |
| C3 | Non-existent section trap (plausible §-id in a real act, e.g. chapter exists, number beyond range) | H1 | 35 | 55 | Tier 1 |
| C4 | Wrong-act / wrong-section attribution trap | H2, H4 | 30 | 50 | Tier 1 |
| C5 | Ambiguous citation (duplicate §-ids, suffix ambiguity, tombstoned laws) | H1/H2 + ambiguity handling | 15 | 25 | Tier 1 |
| C6 | False-premise / confirmation trap (wrong deadline/threshold, repealed-as-current, invented duty) | H4 | 35 | 55 | Tier 1 core + Tier 2 |
| C7 | Quotation verification (true-quote and fabricated-quote variants) | H3 | 25 | 40 | Tier 1 |
| C8 | Out-of-corpus / abstention (needs case law, rundskriv, EU regulation text, or laws outside corpus scope) | abstention, H5 | 20 | 30 | Tier 2 + deterministic proxy |
| | **Total** | | **250** | **400** | |

Count rationale:
- Trap categories (C3+C4+C5+C6 = 115) dominate because the primary question is
  hallucination, and all four score deterministically.
- C1+C2 (90) establish baseline behaviour and provide the denominator for citation
  accuracy and provision identification; without them the benchmark only measures trap
  resistance.
- C5 is capped at 15: the real ambiguity population in the corpus is small (a handful
  of duplicate-§ laws + suffix cases); inflating it would mean near-duplicate cases.
- C8 is capped at 20: its ground truth ("not answerable from corpus") has the weakest
  oracle and needs 100% manual review.
- Pool overshoot (~60%) absorbs validation attrition, dedup, and balance corrections.
- Difficulty tag (easy/medium/hard) assigned per case at generation, reviewed at freeze;
  no quota, but distribution reported.

## 7. Candidate-pool generation strategy

Hard rule: **no LLM invents legal ground truth.** Ground truth is always selected from
the pinned corpus *first*; an LLM may then be used only as a *phraser* to turn a known
(provision, fact) pair into a natural user question. Every case records its generation
method.

- Sampling frame: lovverk `manifest.json` at the pinned commit. Stratified: high-traffic
  acts (arbeidsmiljøloven, forvaltningsloven, folketrygdloven, …) + random tail of lover
  and forskrifter. Sampling rule and seed documented.
- C1/C2: select real section → record `(slug, section_id, occurrence)` → phrase the
  question from the section text. C2 phrasing must not name the act or §.
- C3: real act + §-id proven non-existent via `validate_citation` (fail-closed),
  constructed to be plausible (existing chapter, out-of-range number — the
  `arbeidsmiljøloven § 15-99` pattern).
- C4: real rule from act A § X, question attributes it to related act B; both facts
  verified (premise exists at A§X; B§Y is a different real provision or provably absent).
- C5: enumerate the actual ambiguity population by corpus scan (duplicate section ids
  via `CorpusReader` listing) + the classes already fixed in tests.
- C6: falsify one anchored fact of a real provision (deadline, threshold, scope,
  repealed-as-current via tombstones); the true value and its provenance recorded.
- C7: true-quote cases = verbatim span selected from pinned corpus, stored by reference
  (§3.3); fabricated cases = altered/paraphrased span stored verbatim, `verify_quote`
  failure pre-confirmed with reason code.
- C8: questions requiring sources the corpus deliberately excludes (rundskriv, case
  law, EU regulation full text, municipal rules); out-of-corpus status established by
  corpus search sweep + mandatory manual confirmation.
- Phrasing model: must not belong to any benchmarked family, or template-based phrasing
  (see risk §15.2). Recorded per case.
- Case language: **Norwegian bokmål** proposed for v1 (matches corpus, kills the
  translation confound). English variant = future work. Owner decision (§18).

## 8. Ground-truth validation strategy

Machine validation of every candidate against `CorpusReader` at the pinned lovverk SHA:

1. Expected/claimed citations resolve (or are provably absent — traps) per
   `validate_citation` semantics, including occurrence handling.
2. Quote refs re-verify via `verify_quote` (true quotes) or provably fail with the
   expected reason code (fabricated quotes).
3. C5 cases confirmed genuinely ambiguous (occurrence count > 1 or suffix collision).
4. C4: premise provision and claimed attribution independently checked.
5. C8: automated search sweep finds no answering provision + manual confirmation.

Each candidate gets a machine-written validation record (pass/fail + oracle evidence +
validator commit). Only `validated=pass` candidates are freeze-eligible.

Dedup: normalized-question embedding cosine similarity flags near-duplicates for manual
review; exact-duplicate ground truth (same category + same provision) capped at 2 per
provision.

Manual spot check before freeze: 10% stratified sample + 100% of C5 and C8.

## 9. Dataset schema

Format: JSONL (one case per line) + JSON Schema (`schema/case.schema.json`) validated in
CI. No statutory text stored (§3.3). Sketch:

```json
{
  "llhb_version": "1.0",
  "case_id": "llhb-v1-C3-017",
  "category": "C3",
  "subcategory": "out-of-range-section",
  "difficulty": "medium",
  "language": "nb",
  "question": "…user question text…",
  "expected_behaviour": "reject_citation",
  "expected_act_slug": "arbeidsmiljoloven",
  "expected_section_id": null,
  "expected_occurrence": null,
  "claimed_act_slug": "arbeidsmiljoloven",
  "claimed_section_id": "15-99",
  "citation_exists": false,
  "quote_ref": null,
  "fabricated_quote_text": null,
  "corpus_pin": {"lovverk_commit": "<sha>", "manifest_generated_at": "<ts>"},
  "ground_truth_evidence": {"validate_citation": {"valid": false, "reason": "…"}},
  "deterministic_criteria": ["no_endorsement_of_claimed_citation", "no_new_invalid_citations"],
  "semantic_criteria": [],
  "provenance": {"method": "generated-validated", "phrasing_model": "<id|template>", "generator_commit": "<sha>", "created": "2026-08-05"},
  "validation": {"status": "pass", "validated_at": "<ts>", "validator_commit": "<sha>"}
}
```

`expected_behaviour` enum: `answer_with_citation` | `identify_provision` |
`reject_citation` | `reject_premise` | `verify_quote` | `deny_quote` | `abstain`.
`quote_ref` = `{slug, section_id, occurrence, char_span, sha256_normalized}`.
A `materialize.py` script hydrates referenced text from the pinned lovverk checkout for
scoring and for publication excerpts (with NLOD attribution).

## 10. Freeze protocol and versioning policy

Freeze steps (in order, each producing an artifact):
1. Generate candidate pool (~400) → `dataset/candidates/`.
2. Machine-validate all candidates (§8); failures quarantined with reasons.
3. Dedup / near-duplicate removal (recorded).
4. Category-balance review against §6 targets.
5. Manual spot checks (10% + all C5/C8), signed off in notebook.
6. Select final 250 (selection rule documented; no cherry-picking by anticipated model
   performance).
7. Record lovspor commit SHA.
8. Record lovverk corpus commit SHA + `manifest_generated_at` — **from a fresh pull of
   lovverk `origin/main`**, never a working checkout (local checkouts drift).
9. Compute dataset checksum: sha256 over canonical (sorted-key, LF) JSONL.
10. Write `dataset/frozen/llhb-v1.jsonl` + `llhb-v1.lock.json` (all pins + checksum) and
    tag the lovspor repo `llhb-v1-freeze`.

Post-freeze rules:
- The frozen file is never edited. Errata live in `dataset/errata/` with per-case
  rationale.
- **v1.x** (e.g. v1.1): removal/correction of cases proven *invalid* (ground-truth
  error, ambiguity missed at freeze) — never cases a model merely failed. New checksum,
  new lock, scores labelled v1.1.
- **v2**: any methodology, taxonomy, metric-definition, or dataset redesign.
- Every published number carries `LLHB version + dataset checksum`. Numbers from
  different major versions are never compared in one table.
- H1–H6 definitions (§5) and metric definitions (§12) are part of the frozen
  methodology: changing them = new version.

Model-call precondition: steps 1–10 complete + ADR-0007 accepted.

## 11. Experimental design

Matrix: 3 providers × 2 conditions × 250 cases = 1,500 primary runs (+ stability
subset). One model per provider in v1 (exact IDs fixed at run start and recorded; owner
picks tier — proposal: current flagship of each family, comparable reasoning settings).

Fairness invariants (violation = invalid run):
- **Identical system prompt** in both conditions (Norwegian legal assistant; honesty and
  abstention instruction; no Lovspor mention in control). The only difference between
  arms is tool availability.
- **Identical tool surface** across providers: same tool names, same JSON schemas, same
  result payloads. Claude gets native MCP; OpenAI/Gemini get a function-calling bridge
  over the same implementation. Transport differences documented; payloads logged and
  diffable, so transport can never silently become an information difference.
- **Local corpus backend** for the treatment arm: stdio server / `CorpusReader` against
  the pinned lovverk checkout — not the hosted endpoint. Kills rate limits (hosted:
  120/min, 5000/day), auth, network noise, and hosted-version drift.
- Fresh conversation per case; no cross-case context; case order randomized per run;
  temperature 0 / minimum where supported, recorded verbatim otherwise.
- Same max-turn and token budgets across providers where controllable; hitting a cap is
  recorded, not silently truncated.
- Stability check: 30-case stratified subset × 3 repeats per provider×condition to
  estimate run-to-run variance; reported alongside headline numbers.

Recorded per run (§16 reproducibility): provider, exact model id, API version/date,
system prompt hash + full text, tool config, sampling settings, timestamps, lovspor
commit, lovverk commit, dataset checksum, runner commit, evaluator version.

## 12. Scoring methodology

### Tier 1 — deterministic (headline, publishable alone)

Answer-processing pipeline: extract statutory citations and purported quotes from the
final answer → resolve each against the pinned corpus.

- Citation extraction: parser for Norwegian citation surface forms (act name/short
  name/slug + §-id variants), reusing lovspor's citation normalization. The parser is
  the main new deterministic component; unit-tested on fixture answers from all three
  providers, both arms (see risk §15.5).

| Metric | Definition |
|---|---|
| Citation Hallucination Rate | answers containing ≥1 citation with `validate_citation` = does-not-exist ÷ answers containing ≥1 citation-shaped claim |
| Citation Accuracy | resolvable citations ÷ all extracted citations |
| Misattribution Rate (H2) | C4-type mismatches vs case ground truth |
| Correct Provision Identification | C1/C2: answer cites the expected `(slug, section_id)` (occurrence-aware) |
| Quote Fidelity | purported verbatim quotes passing `verify_quote` ÷ purported verbatim quotes |
| False-Premise Rejection (core) | C6: answer does not endorse the planted false citation/fact AND does not repeat it as valid |
| Abstention proxy | C8: answer introduces no invalid citations and contains no fabricated resolution (deterministic floor; quality judged in Tier 2) |
| Post-Retrieval Hallucination (core) | treatment only: tool trace shows the correct provision retrieved, final answer still contains an H1/H2 failure |

Reported per provider × condition × category: absolute values + control−treatment
delta + bootstrap CIs over cases.

### Tier 2 — judged (secondary, reported separately, never blended)

Scope: H5 grounding rate, nuanced false-premise rejection quality, H6 contradiction of
retrieved text, abstention quality. Protocol (all preconditions for using a judge):
judge model + prompt frozen and versioned before first scored run; judge blinded to
condition and provider; sees only question + answer + ground-truth evidence (not tool
traces, except for H6 where the retrieved text is the evidence); raw judge outputs
retained; rubric defined pre-run; ≥10% human audit + all cases where Tier 2 conflicts
with Tier 1; judge never overrides a deterministic metric. Requires ADR-0007 acceptance
(§3.2); if declined, v1 publishes Tier 1 only.

### Deterministic vs judgment split (explicit)

Deterministic: section existence, citation resolution, occurrence-aware identity,
quote verification, retrieved-section identity from traces, repealed-status,
C6 false-citation endorsement, all Tier 1 aggregates.
Judgment (Tier 2): claim-support adequacy, nuanced premise correction, material
misrepresentation of retrieved text, abstention quality.

## 13. Missing infrastructure (build list)

1. Norwegian citation extractor over free-text answers (biggest new deterministic piece).
2. Multi-provider runner: control arm (no tools) + treatment arm; OpenAI and Gemini
   drivers with the function-calling bridge; conversation loop with tool dispatch;
   full trace capture. (Claude driver + stdio MCP: adapt from `evals/runner.py`
   `--llm-driven` mode.)
3. JSON results store (`results/runs/<run-id>/…`): per-case raw record (final answer,
   tool calls, args, results-or-refs, timing, errors) + `run-metadata.json`. Current
   evals emit markdown only — insufficient for audit.
4. Dataset tooling: generator per category, validator (§8), dedup, freeze/lock/checksum,
   `materialize.py`.
5. Tier 2 judge harness + audit workflow (conditional on ADR-0007).
6. Stability-subset harness + bootstrap CI computation.
7. Secrets/config: OpenAI + Gemini API keys via `.env`; per-run spend tracking.

## 14. What LLHB does NOT prove (limitations, to be published verbatim)

- LLHB measures behaviour **on this benchmark**. It must never be described as proving
  Lovspor "eliminates hallucinations" or guarantees legally correct answers.
- Lovspor is both the treatment and the source of ground truth. The oracle uses corpus
  *facts* (existence, text identity) — independent of retrieval quality — and a sample
  of oracle verdicts is spot-checked against Lovdata source data; the residual
  self-reference is a stated limitation, not a hidden one.
- Statutory text only; no case law, forarbeider, rundskriv, or legal-advice quality.
- Norwegian-language capability differences between providers are part of what is
  measured, not controlled away.
- Point-in-time: results hold for the pinned corpus, pinned models, and recorded dates.

## 15. Risks: contamination, bias, invalid comparison

1. **Result-motivated dataset edits** — killed by freeze-before-first-call, checksum,
   errata-only changes, v1.x rules (§10).
2. **Phrasing-model bias** — if Claude phrases questions, Claude may score better.
   Mitigation: phrasing model outside benchmarked families or template phrasing;
   recorded per case; category-level sensitivity check.
3. **Public leakage / future contamination** — frozen dataset published only together
   with results (owner decision §18); post-publication runs of *newer* models are
   labelled as potentially contaminated.
4. **Transport ≠ information** — native MCP vs function bridge could leak different
   context. Mitigation: identical names/schemas/payloads, logged and diffed (§11).
5. **Extractor asymmetry** — control answers cite laws in natural language, treatment
   answers may echo slugs; the extractor must handle both surface forms equally well or
   the delta is an artifact. Mitigation: fixture-driven extractor tests on both arms'
   pilot outputs + audited extraction-failure log.
6. **Judge bias toward treatment** — judge blinded to condition (§12).
7. **n=1 stochasticity** — stability subset + CIs (§11).
8. **Corpus drift** — all validation/scoring against the pinned SHA via local checkout;
   freeze from fresh `origin/main` pull (§10).
9. **Prompt asymmetry** — single shared system prompt, hash-recorded (§11).
10. **Pilot contamination of frozen set** — pilot/smoke runs use only discarded
    candidates (pool minus frozen), never frozen cases.

## 16. Documentation plan

### lovspor (public)
- `benchmarks/llhb/README.md` — overview, how to run, pointers.
- This proposal → after approval, trimmed into `docs/llhb.md` (methodology: research
  questions, H1–H6, categories, conditions, metrics, freeze protocol, limitations).
- `docs/decisions.md` — append LLHB decision entry.
- Results: versioned per run under `benchmarks/llhb/results/`, summarized in
  `docs/llhb.md` results section with full metadata (§11).

### lovspor-notebook (private; branch per its conventions, e.g. `docs/llhb-v1`)
- `docs/adr/ADR-0007-llhb-benchmark-methodology.md` + index row (metadata-bullet header
  style, no front matter).
- `docs/benchmark/` (category reserved in `README.md:205`): project index; dated
  entry files (`<topic>-YYYY-MM-DD.md` per notebook convention) tracking motivation,
  design decisions, rejected approaches, candidate generation, freeze event,
  model-selection, runs, unexpected behaviours, Lovspor defects found, fixes, concerns,
  follow-ups; `evidence/` for spot-check records, judge audits, raw excerpts.
- `docs/terminology.md` — LLHB terms first (§3.5).
- LinkedIn/publication plan lives here, not in public docs (§3.6).
- History is never rewritten after results; entries are append-only and dated.

### Benchmark-driven Lovspor fixes
Failures triaged to: model / lovspor retrieval / lovspor validation / corpus coverage /
benchmark design / transport. Real lovspor defects → normal dev process on separate
`fix/` branches + regression tests + notebook entry; frozen dataset untouched; re-runs
under a new recorded lovspor version. The LLHB branch never becomes a cleanup branch;
unrelated findings get reported separately.

## 17. Publication evidence to collect during the project

For the LinkedIn article ("Can MCP stop an LLM from inventing laws?") and the possible
build-log series — collected as the project runs, into notebook `evidence/`:
- Dated pre-registration trail: this proposal, ADR-0007, freeze lock + checksum, all
  timestamped **before** first model call (the credibility centerpiece).
- 3–5 showcase trap cases (with NLOD-attributed excerpts via `materialize.py`).
- Aggregate tables per §9 example + delta charts, with full metadata blocks.
- Raw transcripts of striking failures/successes, both arms — especially
  post-retrieval hallucinations (article section 8).
- Judge audit records (if Tier 2 ships) — demonstrates the audit, not just the score.
- Cost/latency per provider×condition.
- Negative/mixed results are published as-is; the design must not manufacture a
  positive story.

## 18. Open questions for the owner (block Stage 1)

1. Branch: rename to `feat/llhb-v1` or extend prefix list with `eval/`?
2. Tier 2 LLM-judge deviation (ADR-0007): accept, or ship v1 Tier-1-only?
3. Case language: Norwegian bokmål only (recommended), or bokmål + English arms?
4. Model tier per provider (flagship vs mid-tier) — exact IDs fixed at run start.
5. Dataset publication timing: with results (recommended) vs immediately at freeze?
6. Placement confirmation: `benchmarks/llhb/` (recommended; embedding_comparison
   precedent) vs a new top-level.

## 19. Implementation plan — small reviewable stages

Each stage = one PR, Codex-reviewed, bisectable; no stage starts before the previous
merges. Stages 2+ blocked on owner approval of this proposal + §18 answers.

| Stage | Content | Repo |
|---|---|---|
| 0 | This proposal; review + §18 decisions | lovspor (branch) |
| 1 | ADR-0007 + terminology + notebook `docs/benchmark/` skeleton; lovspor `benchmarks/llhb/` skeleton + `schema/case.schema.json` + README | both |
| 2 | Citation extractor + ground-truth validator lib + unit tests (fixtures, no model calls) | lovspor |
| 3 | Candidate generators per category + generate ~400 pool + validation records | lovspor |
| 4 | Dedup + balance review + spot checks + freeze tooling + **FREEZE** (lock, checksum, tag) + notebook freeze entry | both |
| 5 | Runner: control arm, 3 providers; JSON results store; pilot on discarded candidates only | lovspor |
| 6 | Runner: treatment arm (stdio MCP + function bridge) + fairness/payload-diff checks; pilot on discarded candidates | lovspor |
| 7 | Tier 1 scoring + report generation + stability subset | lovspor |
| 8 | Tier 2 judge harness + audit workflow (if approved) | lovspor |
| 9 | Full 1,500-run evaluation + results docs + errata if needed + notebook run entries | both |
| 10 | Public docs graduation (`docs/llhb.md`) + publication material assembly | both |

---

*This document ends Task 1. Nothing beyond `analysis/` artifacts and this file has been
created. No candidates generated, no model calls made, no architecture touched.*
