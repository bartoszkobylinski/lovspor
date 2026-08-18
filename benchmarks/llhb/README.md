# LLHB — Lovspor Legal Hallucination Benchmark

LLHB measures whether giving a large language model access to the Lovspor
Norwegian-law MCP reduces legal hallucinations, compared with the same model
answering the same questions without it.

Status: **v1 executed on `claude-opus-5` (frozen pair, 250+250, published
2026-08-14) — a post-hoc diagnostic result per DECISIONS.md ruling #30; a
confirmatory `claude-fable-5` replication is preregistered in
`ANALYSIS-PLAN-fable5-v1.md`.** The frozen dataset lives in
`dataset/frozen/` (checksum in the lock file), scored reports in
`results/reports/`. See `DECISIONS.md` for the rulings and `PROPOSAL.md`
for the original design proposal.

## What LLHB is not

LLHB is conceptually separate from the persona-driven eval suite in `evals/`:

> **Existing evals:** "If the expected Lovspor tools are invoked, do the tools
> behave correctly?"
>
> **LLHB:** "When a real model is given access to Lovspor, does it hallucinate
> statutory citations less?"

The `evals/` suite is permanent CI infrastructure and is not modified by LLHB.
LLHB reuses parts of its infrastructure (the `claude -p` driver, trace parsing,
`CorpusReader` as a deterministic oracle) but is a self-contained study under
`benchmarks/`, following the `benchmarks/embedding_comparison/` precedent.

LLHB is also distinct from the planned Phase 3 staleness benchmark
(`docs/publication-plan.md`): Phase 3 measures how far behind Norwegian law a
model is, anchored on amendment dates; LLHB measures hallucination and grounding
with/without the MCP at one pinned corpus state. Neither replaces the other.

LLHB v1 does **not** measure legal-correctness of interpretation, does not use
an LLM judge, and must never be described as proving that Lovspor "eliminates
hallucinations" or guarantees legally correct answers.

## Documents

| File | Content |
|---|---|
| `METHODOLOGY.md` | Research questions, hallucination taxonomy H1–H6, category design, experimental conditions, fairness invariants, limitations |
| `SCORING.md` | Deterministic scoring rules: citation extraction, oracle mapping, metric definitions |
| `TOOLING.md` | Stage 2 tooling reference: extractor syntax, stance rules, resolver verdicts, quote refs, canonicalization (`src/lovspor/llhb/`) |
| `FREEZE.md` | Dataset freeze protocol, corpus pinning, checksum, errata and versioning policy |
| `DECISIONS.md` | Owner decisions of 2026-08-05 governing v1 |
| `PROPOSAL.md` | Original design proposal (historical; superseded where DECISIONS.md differs) |
| `schema/case.schema.json` | JSON Schema for one benchmark case |
| `schema/run_metadata.schema.json` | JSON Schema for run-level experiment metadata |
| `schema/result_record.schema.json` | JSON Schema for one per-case raw result record |

## Layout (as the project progresses)

```
benchmarks/llhb/
├── README.md, METHODOLOGY.md, SCORING.md, FREEZE.md, DECISIONS.md, PROPOSAL.md
├── schema/                  # JSON Schemas (this stage)
├── dataset/
│   ├── candidates/          # ~400 generated+validated candidates (Stage 3)
│   ├── frozen/              # llhb-v1.jsonl + llhb-v1.lock.json (Stage 4; private until results — FREEZE.md §6)
│   └── errata/              # post-freeze corrections (if ever needed)
├── generator/ validator/ runner/ scoring/   # tooling (Stages 2–7)
└── results/runs/<run-id>/   # raw per-case records + run metadata (Stage 9)
```

The frozen dataset contains **no statutory text** — quotes are stored as stable
corpus coordinates and materialized at evaluation time from the pinned lovverk
commit (`FREEZE.md`, `DECISIONS.md` §7). Lovdata source material is NLOD 2.0;
any materialized excerpt used in reports carries NLOD attribution.

Design reasoning, research log and the methodology ADR live in the private
`lovspor-notebook` repo (`docs/benchmark/`, `docs/adr/ADR-0007-*`). Everything a
third party needs to *reproduce* the benchmark lives here, in the public repo.
