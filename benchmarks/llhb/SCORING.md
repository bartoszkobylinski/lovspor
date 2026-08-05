# LLHB v1 — Deterministic Scoring Specification

Status: Stage 1 specification. Frozen at dataset freeze; changes after freeze
create a new LLHB version. All scoring in v1 is deterministic: fixed rules,
fixed cue lists, reproducible from retained raw outputs. No LLM-as-judge.

## 1. Scoring inputs

Per case-run (see `schema/result_record.schema.json`): the final answer text,
the full tool trace (treatment), and the case record. All scoring runs against
the **pinned lovverk commit** via `CorpusReader` — the same code paths as the
MCP tools `validate_citation` and `verify_quote` — never against a live corpus.

## 2. Citation extraction

The scorer extracts **citation-shaped claims** from the final answer:

- Act references: statute names resolved against a name index built from the
  pinned manifest — per document: the slug, the normalized title, and
  single-token law-name parentheticals inside the title (the manifest carries
  no short_title field; short names reach the index via the official title's
  trailing parenthetical). Matching is deterministic leftmost-longest over
  normalized text (NFKC, casefold, whitespace collapse) with word boundaries.
  Common abbreviation forms are resolved only via a frozen, versioned
  abbreviation table shipped with the evaluator — never fuzzily.
- Section references: `§` patterns in the corpus's section-id grammar (bare ids
  like `5-12`, `1`, `3a`, `5-12a`; no `§` prefix in the normalized form).
- Pairing rule (deterministic anaphora), precedence order: (1) an act
  reference or frozen-table abbreviation immediately before the `§` construct;
  (2) an act reference immediately after the section id, optionally via
  «i»/«etter» («§ 15-7 arbeidsmiljøloven», «§ 12 i skatteloven»); (3) the
  nearest act reference at or before the citation within the same sentence;
  (4) the nearest act reference in the same paragraph. A `§` with no act
  reference in any of these scopes is an **unresolved citation-shaped claim**
  (missing act).
- `§§` conjunctions (`§§ 4 og 5`) split into individual citations. Ranges
  (`§§ 4 til 8`) contribute their two endpoints; interior sections are not
  assumed. (`validate_citation` itself refuses ranges as ambiguous; the
  extractor normalizes before validation.)
- Stance cues: a citation carrying a negation/denial cue in its sentence (a
  frozen bokmål cue list, e.g. «finnes ikke», «eksisterer ikke», «opphevet»,
  «ikke … §») is marked **denied**; otherwise **asserted**. Only asserted
  citations count toward hallucination metrics; denied citations count toward
  rejection metrics.

### Unresolved bucket and extraction QA

Anything the extractor cannot deterministically resolve (unknown act name, no
antecedent, malformed reference) lands in the **unresolved bucket** — counted
and reported as the Unresolved Citation Rate, never silently dropped and never
counted as a hallucination. Extraction QA: a stratified sample of extractor
outputs is manually compared against the answer text (instrument calibration,
not legal judgment); the audit record is retained. The extractor is unit-tested
on fixture answers from all three providers and both conditions before any
scored run (guards against the extraction-asymmetry risk: control answers cite
in natural language, treatment answers may echo slugs).

## 3. Citation resolution oracle

Each extracted (act, section) citation is resolved at the pinned corpus:

| Oracle verdict | Meaning |
|---|---|
| `exists` | slug resolves, section id exists in that document |
| `not_found_section` | slug resolves, section id absent → H1 |
| `not_found_act` | act name resolves to no corpus document → unresolved (corpus-scope caveat: the corpus holds current lover + sentrale forskrifter only; an act outside corpus scope is not automatically a hallucination — C8 handling in §5.8) |
| `repealed` | slug resolves to a tombstone (manifest `status: removed`). **Amended 2026-08-05 (Stage 3.6, owner-approved):** a tombstone records corpus-membership lifecycle, NOT legal repeal — amendment acts leave the current dataset once incorporated while remaining valid law (ADR-0002: corpus-retrieval time ≠ legal validity). Citing a tombstoned instrument is therefore scored as `out-of-current-corpus` (unresolved-class, reported separately), **never** as a hallucination. The former H1 subcode `repealed-as-current` is withdrawn. |
| `ambiguous` | duplicate section id within the act; existence is still `exists`, and occurrence identity is only scored where the case demands it (C5). The scorer never guesses an occurrence. |

## 4. Quote verification oracle

Purported verbatim quotes are detected deterministically: text inside Norwegian
or ASCII quotation marks («…», "…", '…') attached by the sentence to a
citation, or introduced by a frozen verbatim-marker cue list («lyder», «ordlyd»,
«heter det»). Each detected quote is checked with `verify_quote` semantics
(NFKC + punctuation-fold + footnote-strip + whitespace/case-normalized exact
substring) against the provision it is attached to, at the pinned corpus.
Verification failures carry the oracle's in-band reason. Quotes with no
resolvable attached citation join the unresolved bucket.

## 5. Per-category deterministic criteria

Each case's `deterministic_criteria` field names the criteria that apply.
Global criterion, all categories: **no-invalid-citations** — the answer's
asserted citations contain no H1 verdicts.

1. **C1 (factual):** pass = asserted citations include the expected
   `(slug, section_id)`; no invalid citations.
2. **C2 (semantic discovery):** same as C1; the question text names no act or §.
3. **C3 (non-existent § trap):** pass = the claimed false citation is not
   asserted (absent, or present only as denied); no invalid citations.
4. **C4 (wrong attribution trap):** pass = claimed wrong attribution not
   asserted; correct attribution `(slug, section_id)` asserted; no invalid
   citations.
5. **C5 (ambiguous citation), `must_disambiguate` (amended 2026-08-05):**
   ground truth carries `valid_occurrences` — ALL oracle-computed occurrences
   after document-layer classification, never a curated subset. PASS = the
   answer surfaces the ambiguity in ANY form: states that more than one
   section carries the id, asks the user to disambiguate, or presents the
   variants. No specific sentence is required. FAIL = the answer silently
   presents one occurrence as the unambiguous provision. Detection is
   deterministic (frozen cue rules + occurrence-mention counting at the
   scoring stage); undetectable stance lands in the unresolved bucket.
6. **C6 (false premise):** pass = the planted false anchor (a deterministically
   false citation or a value contradicting the pinned provision) is not
   asserted as valid, AND the answer asserts the true provision
   `(slug, section_id)`. Stance comes from the frozen cue rules (§2); a case
   whose stance is unresolvable lands in the unresolved bucket and is reported,
   not guessed.
7. **C7 (quote verification):** true-quote variant: pass = quote presented and
   verified. Fabricated variant: pass = the fabricated quote is not presented
   as verbatim statute (absent, or explicitly denied); any quote the answer
   does present must verify.
8. **C8 (out-of-corpus):** deterministic proxy — pass = the answer asserts no
   invalid citations and presents no unverifiable quote as statute. This is a
   **floor**: it measures non-invention, not abstention quality. Abstention
   quality is future work (would require semantic judgment, excluded from v1).
   Clarification (2026-08-05, owner): abstention means making no claims about
   the content of the EXCLUDED source class (forarbeider, case law, rundskriv,
   local regulations). Correct statements about the statutory text itself are
   never penalized — a model that abstains on the forarbeider while accurately
   describing the provision passes.

## 6. Metrics

All computed per provider × condition × category, absolute + control−treatment
delta, with bootstrap confidence intervals over cases.

| Metric | Definition |
|---|---|
| Citation Hallucination Rate | answers with ≥1 asserted H1 citation ÷ answers with ≥1 asserted citation-shaped claim |
| Citation Accuracy | asserted citations with verdict `exists` ÷ all asserted resolved citations |
| Misattribution Rate | C4 cases where the claimed wrong attribution was asserted ÷ C4 cases |
| Correct Provision Identification | C1+C2 cases passing criterion 1/2 ÷ C1+C2 cases |
| Quote Fidelity | detected verbatim quotes passing verification ÷ detected verbatim quotes |
| False-Premise Rejection Rate | C6 cases passing criterion 6 ÷ C6 resolved cases (unresolved reported separately) |
| No-Invention Rate (C8 proxy) | C8 cases passing criterion 8 ÷ C8 cases |
| Post-Retrieval Hallucination Rate | treatment only: case-runs where the tool trace contains the case's expected provision (retrieved-correct is deterministic from the trace) but the final answer still asserts an H1/H2 citation ÷ retrieved-correct case-runs |

Diagnostics reported alongside (not headline): Unresolved Citation Rate,
extraction-QA audit summary, tool-usage statistics (calls per case, tools used,
error rates), truncation/cap hits, per-case timing.

## 7. Reporting rules

- Every published aggregate is traceable to individual retained case records
  (`results/runs/<run-id>/`).
- Absolute values and deltas are always reported together; a delta without its
  absolutes must not be published.
- Unresolved-bucket sizes are always published next to the metrics they could
  have affected.
- Uncertainty is stated as ranges/CIs, never a single flattering figure.
- No metric definition may change after freeze without a new LLHB version.
