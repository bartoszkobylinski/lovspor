# LLHB v1 — Freeze Protocol and Versioning Policy

Status: Stage 1 specification, approved (owner decisions 5, and PROPOSAL §10).

## 1. Why freeze

The frozen dataset is the pre-registration artifact: it is fixed, checksummed
and corpus-pinned **before the first model call**, so no case can be added,
removed or edited to improve a result. A negative or mixed benchmark result is
published as-is.

## 2. Freeze preconditions

1. Candidate pool (~400) generated under `dataset/candidates/`.
2. Every candidate machine-validated against `CorpusReader` at the pinned
   lovverk commit; failures quarantined with reasons. Validation records
   retained per candidate.
3. Duplicates and near-duplicates removed (recorded); at most 2 cases per
   category may share the same ground-truth provision.
4. Category balance reviewed against METHODOLOGY §3 targets.
5. Manual spot checks: 10% stratified overall, 100% of C5 and C8; sign-off
   recorded in the notebook research log.
6. Final 250 selected by a documented selection rule, independent of any
   anticipated model performance.
7. ADR-0007 (notebook) accepted by the owner.

## 3. Corpus pin

Cut from a **fresh pull of lovverk `origin/main`** — never a working checkout
(local checkouts drift; verified 2026-08-05). The pin records:

1. Full lovverk commit SHA (git history is the corpus version store, ADR-0003;
   `git show <sha>:<path>` reproduces any pinned file exactly).
2. `manifest.json` `generated_at` at that commit (cross-check + human-readable
   timestamp).
3. Per cited document, captured from the pinned manifest into the lock file:
   `xml_hash`, `renderer_version`, and — because the treatment arm uses
   `semantic_search` — `embedding_space_id` and `embedding_hash`.
4. The lovspor engine commit SHA used for validation and (later) scoring.

Temporal caveat (ADR-0002): the pin is corpus-retrieval time, not
legal-validity time. Recorded in the dataset lock and the dataset card.

## 4. Freeze artifacts

- `dataset/frozen/llhb-v1.jsonl` — the 250 cases, canonical form.
- `dataset/frozen/llhb-v1.lock.json` — all pins of §3 + dataset checksum +
  freeze timestamp + selection-rule reference.
- Git tag `llhb-v1-freeze` on the lovspor commit containing both.

### Canonical JSONL and checksum

One case per line; each line is JSON with lexicographically sorted keys,
compact separators (`,` / `:`), UTF-8, no escaping of non-ASCII (`ensure_ascii`
false), LF line endings, trailing LF at end of file. The dataset checksum is
SHA-256 over the file bytes. The checksum is recomputed and asserted by tooling
before every scored run.

## 5. Post-freeze rules

- The frozen file is never edited. Any correction goes through errata.
- Errata (`dataset/errata/`): one file per erratum — case ID, defect, evidence,
  disposition. Only cases proven **invalid** (ground-truth error, ambiguity
  missed at freeze) may be corrected or excluded — never cases a model merely
  failed.
- Errata produce **LLHB v1.x**: new canonical JSONL, new checksum, new lock,
  new tag (`llhb-v1.1-freeze`, …). Scores are labelled with the exact version;
  v1 and v1.x numbers may be compared only with the errata delta stated.
- **LLHB v2**: any change to methodology, taxonomy, H1–H6 definitions, metric
  definitions, category design or dataset redesign. Numbers across major
  versions are never compared in one table.
- Every published number carries: LLHB version + dataset checksum + the full
  run metadata chain (`schema/run_metadata.schema.json`).

## 6. Publication gating (owner decision 5)

The frozen cases are **not publicly released before benchmark execution**; the
dataset is published together with the results.

Coordination constraint: the lovspor repo is currently private, but
re-publication of the engine is planned (`docs/publication-plan.md`,
decisions.md §15). **The repo must not be flipped public while an unpublished
frozen dataset sits in `dataset/frozen/`.** If the flip is needed before
results exist, the frozen JSONL + lock move to the private notebook
(`docs/benchmark/evidence/`) and only the checksum + lock metadata stay here;
the dataset returns to this repo at results publication. This rule exists to
prevent silent benchmark contamination of future model training data before
the experiment has run.

## 7. What "frozen" covers

Frozen together with the dataset: METHODOLOGY.md (incl. H1–H6), SCORING.md
(incl. cue lists and the abbreviation table shipped with the evaluator),
case schema. Tooling bug-fixes that do not change definitions are allowed and
recorded; anything that changes a definition or a criterion is a new version.
