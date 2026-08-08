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

## Addendum — Stage 1 review (2026-08-05)

14. **ADR-0007 clarification and acceptance.** Decision 2's "no ADR is created"
    referred specifically to an ADR permitting LLM-as-a-judge. Notebook
    governance requires an ADR for benchmark methodology, so ADR-0007 for the
    deterministic LLHB v1 methodology is appropriate — **Accepted 2026-08-05**,
    with an explicit non-authorization section: no LLM-as-a-judge, no jurist
    scoring, no general legal-correctness benchmark, no H5/H6 semantic
    interpretation in the v1 headline score.
15. **Stage 2 phrasing strategy:** template-first; LLM-assisted phrasing only
    where templates are insufficient; deterministic ground truth throughout.

## Addendum — Stage 3.6 remediation rulings (2026-08-05)

16. **Quarantine policy:** an objective, versioned rule match from a
    review-confirmed defect class → automatic quarantine (exclusion from the
    eligible pool), never automatic drop. Borderline classification, new
    replacements, and all new C5/C8 material → owner review.
17. **C5 duplicate semantics:** ground truth encodes ALL deterministically
    valid occurrences (`valid_occurrences`, oracle-computed after
    document-layer classification — never a curated subset) with
    `expected_behaviour: must_disambiguate`. Scoring passes any behaviour that
    surfaces the ambiguity; failure = silently presenting one occurrence as
    unambiguous. Schema amendment applied pre-freeze.
18. **Veileder vs vedlegg:** a normative vedlegg with its own § numbering can
    constitute real ambiguity; an embedded veileder/commentary heading is not
    a statutory section. The RC3 parser defect gets its own production
    issue/PR, mandatory BEFORE any new C5 population is generated.
19. **C5 target remains 15.** Feasibility under corrected semantics is
    unknown until the post-parser-fix corpus re-scan; any change is an
    explicit pre-freeze methodology amendment with cases moved to another
    category — never a silent target cut.
20. **Tombstone scoring:** the `repealed` oracle verdict maps to
    out-of-current-corpus (unresolved-class), never to a hallucination; the
    H1 subcode `repealed-as-current` is withdrawn.

## Addendum — Stage 3.6-G feasibility ruling (2026-08-06)

21. **C5 per-act category cap raised to 3; frozen target stays 15.** The
    layer-aware rescan fixed the C5 population at 42 real duplicate ids in 7
    documents, 3 of which carry a single id — so the default per-act cap of 2
    makes 11 the structural maximum and the frozen target of 15 unreachable
    (evidence: `dataset/candidates/remediation/replacement-supply.json`).
    Per ruling 19 the resolution must be explicit: the owner raises the C5
    per-act category cap to 3 (`PoolConfig.per_act_category_caps`), which
    makes the structural maximum exactly 15. Accepted trade-off: up to three
    C5 cases each from the three id-rich forskrifter, always on distinct
    provisions (the per-provision cap of FREEZE.md §2.3 is untouched). The
    alternative — moving 4 cases to another category — was rejected because
    the population supports the original target.

## Addendum — Stage 3.6-F review-assist ruling (2026-08-06)

22. **Model-assisted review annotations (Stage 3.6-F).** An LLM may
    pre-screen review packets and produce per-case ADVISORY annotations: a
    recommended decision, a rationale, and language-naturalness flags. The
    model never fills the decisions file, never defines ground truth, and its
    output is not a score. The owner makes every final decision in the review
    CLI; `reviewer` remains the owner. Notes derived from model annotations
    are marked `(model-assisted)`. Publication discloses: "owner review with
    model-assisted annotations." Consistent with decisions 8/15 (bounded LLM
    roles); decisions 2/14 (no LLM-as-a-judge in scoring) untouched.

## Addendum — Stage 4 rulings (2026-08-08)

23. **Semantic genericity: drop over redesign; 100% owner review of C2
    and C4.** The genericity defect class (a topic corpus-unique as a
    heading yet too broad to identify one provision) is not
    deterministically filterable — census counts occurrences, not
    semantic breadth — and context-anchoring the questions would change
    the C2/C4 category designs pre-freeze. Ruling: affected cases are
    DROPPED (`ambiguous-ground-truth`), never patched; C2 and C4 join
    C5/C8 as 100%-owner-reviewed categories for selection eligibility
    (v5 queue + the two full-review slices); shortfalls are met by
    fresh-seed category-scoped top-up pools, never by pre-F2 material.
    Heuristic filters (token document frequency, self-retrieval) are
    explicitly deferred to LLHB v2, where this round's ~120 labeled
    decisions can calibrate them.
24. **Selection rule.** Selection of the frozen 250 follows SELECTION.md
    at its governing commit: sources are exactly regen-v5 and topup-c4;
    eligibility per ruling #23; per category ascending case_id with the
    ≤2-per-provision cap; fail-closed on any shortfall. The rule is
    fixed before any benchmark model call and is independent of
    anticipated model performance (FREEZE.md §2.6). Owner acceptance of
    SELECTION.md authorizes the tool run; the freeze artifacts and the
    `llhb-v1-freeze` tag remain separate owner acts.

## Addendum — Stage 5 ruling (2026-08-08)

25. **Runner: vendor-native CLI agents on subscription billing.** The
    v1 runner executes each provider's own agent CLI — Claude Code
    (`claude -p`), OpenAI Codex CLI, Gemini CLI — against the same
    local stdio MCP server, instead of the raw-API function-calling
    bridge of decision #11. Motivation: per-token API cost for the
    full matrix is not affordable for v1; CLI subscriptions/free tiers
    remove it, and the CLI surface is how these models are actually
    deployed (ecological validity). PROPOSAL §11's cross-provider
    invariant is narrowed accordingly: within one provider both
    conditions run the identical harness, prompt and settings — the
    only difference is MCP availability — and the control−treatment
    delta per provider is the primary cross-provider comparison;
    absolute rates across providers are reported with a documented
    harness caveat. Control arms run with the CLI's built-in tools
    hard-disabled (sandbox / no-network flags); a control run showing
    any tool activity is invalid. CLI name and version, harness
    settings and payload traces are recorded in run metadata. The
    function-bridge design of decision #11 is deferred to a possible
    v2 rerun on the same frozen dataset.

Stage 1 scope granted: documentation structure, methodology/specification
documents, dataset schema, freeze/versioning protocol, deterministic scoring
rules, experiment metadata format, matching notebook research-log structure,
and recording of these decisions. Explicitly not yet authorized: candidate
generation, model runs, publication claims, architecture changes, embeddings
changes, semantic/LLM judging.
