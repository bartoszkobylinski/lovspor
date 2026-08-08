# LLHB Stage 2 — Deterministic Tooling Reference

Code: `src/lovspor/llhb/` (shipped-package location so the strict
`mypy src/`, coverage and unit-test gates apply; `jsonschema` stays a
dev-group dependency via lazy import, so the wheel gains no runtime
dependency). Tests: `tests/unit/test_llhb_*.py` on synthetic corpora
(`tests/unit/llhb_fixtures.py`) — no statutory text anywhere.

Stage 2 provides primitives only. It does not generate candidates,
freeze datasets, call models, or score runs.

## Module map

| Module | Contract |
|---|---|
| `abbreviations.py` | Frozen table `ABBREVIATIONS` (version `llhb-abbrev-v1`), exact casefolded token match, golden-tested. Maps abbreviation → act *name* (never slug), so entries survive corpus changes and resolve through the name index. |
| `names.py` | `ActNameIndex`: prose act name → slug(s). Keys per manifest record: slug, normalized title, single-token `-loven/-lova/-forskriften/-forskrifta` parentheticals in the title. Exact normalized lookup (NFKC + casefold + whitespace collapse); leftmost-longest word-bounded scanner with hyphen-aware boundaries. |
| `citations.py` | `extract_citations(answer, index)` → citations + unresolved bucket. Syntax and binding precedence documented below. |
| `stances.py` | `classify_stances` (version `llhb-stance-v1`): asserted / denied / corrected / unresolved via frozen cue lists + sentence-local windows. |
| `resolver.py` | `CitationResolver`: typed verdicts; existence delegated to production `validate_citation`; failure classification via the reader's own typed exceptions. |
| `quotes.py` | `QuoteRef` materialization + fail-closed hash verification using the production `verify_quote` normalization (imported, not copied). |
| `corpus_pin.py` | `CorpusPin` (full SHA + manifest `generated_at`), `verify_pin` (fail closed on wrong HEAD or dirty tree), per-document freeze fields. |
| `schema.py` | JSONL load, JSON-Schema validation (deterministic pathed messages), canonical JSONL + SHA-256 checksum. |
| `validation.py` | `CandidateValidator`: schema layer then per-category C1-C8 deterministic checks; dataset-level duplicate-id and provision-cap checks. |
| `results.py` | Stage 5 `ResultsStore`: validated, append-only run storage (`run-metadata.json` + `records.jsonl` under `results/runs/<run-id>/`). Contract below. |

## Extractor syntax (closed contract)

Recognized:

* `<act-name> § <id>` and `<act-name> §<id>` — binding `before`;
* `§ <id> <act-name>`, `§ <id> i <act-name>`, `§ <id> etter <act-name>` — binding `after`;
* `<abbrev.> § <id>` for frozen-table abbreviations — binding `abbreviation`;
* bare `§ <id>` — nearest act mention at-or-before in the sentence
  (binding `sentence`), else nearest mention in the paragraph
  (binding `paragraph`), else no act (missing-act residue);
* `§§ a og b`, `§§ a, b` — split into individual citations;
  `§§ a til b` — the two endpoints, `from_range: true`, interior never
  assumed; any other `§§` shape → unresolved bucket;
* section ids in the corpus grammar (`lovspor.headings.SECTION_ID`),
  canonicalized via `canonical_section_id`.

Binding precedence is exactly: adjacent-before (incl. abbreviation) →
adjacent-after → sentence at-or-before → paragraph nearest → none.

Not recognized (lands in residue or is out of scope by design):
single-`§` conjunctions («§ 4 og 5» extracts only § 4 — the second
number carries no `§` of its own), `ledd`/`bokstav` sub-references,
chapter citations, short-title inflections not present as index keys.
A `§` character that no rule consumes always becomes an
`UnresolvedClaim` — the invariant is golden-tested adversarially.

Known deliberate ambiguity: `§ 12 i skatteloven` extracts raw id
`12 i` (the corpus contains genuine ` i`-suffixed sections); the
resolver applies the production longest-read + tail-strip fallback, so
extractor+resolver agree with `validate_citation` — parity is tested.

## Stance rules (frozen `llhb-stance-v1`)

Cue lists: see `DENIAL_CUES` / `CORRECTION_CUES` in `stances.py`.
Window rules per citation within its sentence: denial cue in the
after-window → DENIED; else correction cue in the before-window →
CORRECTED; else an unconsumed denial cue anywhere in the sentence →
UNRESOLVED; else ASSERTED. Sentence boundary: `[.!?]` + whitespace +
uppercase, or newline (abbreviation dots do not split).
«testloven § 15-99 finnes ikke» is DENIED, never a hallucination.

## Resolver verdicts

`valid` · `nonexistent-section` · `unknown-act` · `ambiguous-act` ·
`repealed-act` · `missing-act` · `ambiguous-occurrence` · `unresolved`.

The existence verdict for a resolved (slug, §) pair is production
`validate_citation` output, verbatim semantics. Classification of an
invalid verdict uses a `get_section` probe catching
`CorpusAmbiguousSectionError` / `CorpusNotFoundError` — no
reason-string parsing, no parallel legal resolver. Any disagreement
between the production verdict and the probe returns `unresolved`
(refuse to score) rather than either answer.

## Quote references

`QuoteRef = (slug, section_id, occurrence?, char_span?, sha256_normalized)`.
`char_span` is `[start, end)` over the *normalized* section text
(production `verify_quote` normalization); span omitted = the whole
normalized section body. Materialization fails closed: not-found /
ambiguous / span-invalid / hash-mismatch; coordinates are never
adjusted. Drift vs invalid-case labeling requires the corpus-pin check
(`drift_or_invalid(pin_matches)`).

## Canonical JSONL + checksum (freeze contract)

Lines sorted by `case_id` (order-independent input), each line JSON
with sorted keys, compact separators, `ensure_ascii=False`, LF, one
trailing LF; duplicate `case_id` refused. Checksum = SHA-256 over the
file bytes, locked by a byte-level golden test.

## Candidate validator (C1-C8)

Schema first (short-circuits), then: C1/C2 expected provision exists
(occurrence-aware); C2 question must not leak the act slug or a `§`;
C3 claimed act current + claimed section provably absent (ambiguity is
NOT absence); C4 expected exists + claimed trap verified per
`citation_exists` + trap ≠ ground truth; C5 cited pair genuinely
ambiguous under production semantics, or the act a tombstone; C6 like
C4 with the claimed pair optional; C7 true-quote refs must materialize
AND pass production `verify_quote`, fabricated quotes must exist-check
their target and must NOT verify; C8 structural only (citation fields
null) + a WARNING until `spot_checked` — Stage 2 cannot prove
"not in corpus", and does not pretend to. Dataset level: duplicate
ids, per-provision cap (max 2 per category per provision).

## Stage 3.6 amendments (2026-08-05, owner-approved)

Driven by the Stage 3.5 human audit (see
`dataset/candidates/remediation/taxonomy.md`):

* **Templates `llhb-templates-v2`**: C6 nonexistent-support frames anchor
  a TRUE substantive claim to the fabricated section (the citation is the
  sole trap); C5 tombstone frames deleted with the subcategory (RC1); C8
  frames name their referent (act / named municipality).
* **Topic filter `llhb-topic-filter-v2`** (`is_usable_topic`): meta/
  structural heading topics never anchor C2/C8 discovery, C4/C6
  premises or C7 fabrications; strict mode also rejects one/two-word
  topics. C1 stays unfiltered by owner ruling. (C4 joined in F4 —
  it was the only premise builder without the filter, so 30 of 50 v4
  C4 cases anchored 'virkeområde'-class topics.) v2 (F3, C2-746): generic 'om'-phrase
  heading openers (Generelt/Nærmere/Særlig om) are stripped from topics
  before the length rule — frames supply their own 'om'.
* **C5 v2**: `expected_behaviour: must_disambiguate` +
  `valid_occurrences` (oracle-computed, layer-filtered, never curated);
  validator enforces exact match against the oracle
  (`valid-occurrences-mismatch`). The oracle is `oracle_occurrences`:
  veileder-layer echoes never count, normative vedlegg rows do (RC3
  parser fix, lovspor #26). Rescan evidence:
  `dataset/candidates/remediation/c5-rescan.json`.
* **Quarantine ledger** (`remediation/apply_quarantine.py`, DECISIONS.md
  #16): objective rule match → automatic quarantine, never automatic
  drop; owner drop/needs_fix carried from the immutable Stage 3.5
  snapshot; rc4-borderline stays with the owner; a kept case matching an
  objective rule is quarantined fail-closed with `owner_conflict: true`.
  Full per-case disposition:
  `dataset/candidates/remediation/quarantine.jsonl`.
* **Regenerated pool (Stage 3.6-E)** under `dataset/candidates/regen/`:
  the v2 generator run against the same corpus pin with
  `PoolConfig.id_offset=500`, so generation-2 ids (`C*-501+`) are
  disjoint from Stage 3 ids and a Stage 3.5 decision can never point at
  regenerated content. The Stage 3 pool and its artifacts stay frozen as
  evidence. Per-category supply vs frozen targets (and the open C5
  cap-vs-target decision, ruling #19):
  `dataset/candidates/remediation/replacement-supply.json`.
* **Regenerated pool v3 (Stage 3.6-F2)** under
  `dataset/candidates/regen-v3/`: the F2-fixed generator
  (`llhb-templates-v3`, review-F structural rules) run against the same
  corpus pin and seed as v2 with `PoolConfig.id_offset=700`, so
  generation-3 ids (`C*-701+`) are disjoint from both earlier pools.
  Same seed as v2 on purpose: the v2/v3 diff isolates the effect of the
  F2 fixes. The v2 pool and its review decisions stay frozen as
  evidence; replacements for v2 drop/needs_fix cases are drawn from v3
  after owner review of its queue.
* **Regenerated pool v4 (Stage 3.6-F3)** under
  `dataset/candidates/regen-v4/`: the F3-fixed generator (title-final
  sentence periods stripped from display names, `llhb-topic-filter-v2`,
  source-cased C7 quote presentation) run against the same corpus pin
  and seed with `PoolConfig.id_offset=900` — generation-4 ids
  (`C*-901+`) disjoint from all earlier pools; the v3 pool and its
  review decisions stay frozen as evidence.
* **Regenerated pool v5 (Stage 3.6-F4)** under
  `dataset/candidates/regen-v5/`: the F4-fixed generator (C4 premises
  filter meta topics, C6-parity) run against the same corpus pin and
  seed with `PoolConfig.id_offset=100` — generation-5 ids (`C*-101+`)
  take the unused range between Stage 3 (`0xx`) and Stage 3.6-E
  (`5xx`), because the case-id schema fixes ids at three digits. Only
  C4 differs from v4 (39 of 50 cases); the v4 pool and its review
  decisions stay frozen as evidence.
* **C4 top-up pool (Stage 4 plan B)** under
  `dataset/candidates/topup-c4/`: the owner's C2/C4 genericity
  full-review round cut C4 eligible supply to 23 (< frozen target 30),
  so a category-scoped pool (`--target C4=50`, fresh seed 20260808 —
  new sampler shuffle, new acts; one expected-provision pair with v5,
  C4-225 ↔ C4-110, whose v5 side is owner-DROPPED, so eligible-supply
  overlap is zero and the ≤2-per-provision freeze cap holds either way;
  no claimed-side or question overlap) with
  `PoolConfig.id_offset=200` supplies replacements. Its whole C4
  population is owner-reviewed via the full-category slice
  (`review-full/`, `build_c2c4_slice.py --include-queued`); the pool's
  own 5-row stratified queue is superseded by that slice so decisions
  live in one file.
* **Trap sibling guard** (`trap_has_sibling`): a claimed § with an
  existing `-x`/letter sibling is never a non-existence trap (RC7).
* **C7 quote material**: spans end at sentence boundaries; mutations
  respect a 15-char tail guard so a modified quote stays plausible (RC6).
  F3 (C7-710/716/731/737): `quote_ref` coordinates stay in the
  casefolded verify domain, but presentation uses the source-cased
  counterpart (`display_span_text`, token-aligned, fail-closed) — and a
  span whose source text starts lowercase (mid-sentence material the
  casefolded domain cannot see) is no quote material at all. Modified
  quotes mutate the display text.
* **Scoring semantics**: the `repealed` oracle verdict is
  out-of-current-corpus, never a hallucination
  (`resolver.REPEALED_ACT_SCORING_NOTE`); C8 abstention never penalizes
  correct statements about the statutory text itself.

## Stage 4 selection and freeze (2026-08-08)

* **Selection rule**: SELECTION.md (rulings #23/#24) — sources are
  exactly `regen-v5` + `topup-c4`; C2/C4 join C5/C8 as 100%-reviewed
  categories; per category ascending case_id under the
  ≤2-per-provision cap (C8 exempt: no ground-truth provision);
  fail-closed on shortfall. Implementation:
  `lovspor.llhb.selection` (unit-tested), orchestrated by
  `generator/select_freeze.py` with hard gates (all review surfaces
  final, pool pins match the corpus, pin re-verified as an ancestor of
  lovverk `origin/main` after a fresh fetch).
* **Freeze artifacts**: `lovspor.llhb.freeze.build_lock` captures per
  cited document `xml_hash` / `renderer_version` / `embedding_space_id`
  / `embedding_hash` from the pinned manifest, plus the dataset SHA-256
  over canonical bytes (FREEZE.md §4). `select_freeze.py` is a dry run
  by default; `--write` emits `dataset/frozen/` artifacts. The freeze
  commit, the notebook sign-off (FREEZE.md §2.5) and the
  `llhb-v1-freeze` tag remain owner acts.

## Stage 5 results store (2026-08-08)

* **Module**: `lovspor.llhb.results` (unit-tested). Validated,
  append-only storage under `results/runs/<run-id>/`:
  `run-metadata.json` (run_metadata.schema.json) and `records.jsonl`
  (result_record.schema.json), one canonical single-line JSON document
  per record.
* **Fail-closed contract**: every document validates against the
  committed schema before any byte reaches disk; `open_run` never
  reuses an existing run directory; a record must match its run's
  `run_id`/`provider`/`model_id`/`condition`; one
  (`case_id`, `repeat_index`) pair per run — dedup state is reseeded
  from disk, so it survives process restarts; `finalize_run` may touch
  completion fields only (`finished_at`, `cases_total`,
  `cases_completed`, `errors_total`, `notes`, `evaluator_version`).
* Records are never edited after capture; scoring reads them as-is.

## What Stage 2 deliberately does not solve

* Answer-level quote *detection* (finding purported quotes in model
  answers) — scoring-stage work; only reference verification exists.
* C8 out-of-corpus proof — manual review stays mandatory.
* Coverage of every Norwegian citation surface form — unresolved
  residue is measured and published instead (SCORING.md §2).
* Freezing (`llhb-v1.lock.json`), candidate generation, runners,
  scoring, provider integrations — later stages.
