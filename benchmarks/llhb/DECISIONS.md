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

## Addendum — Stage 5 pilot ruling (2026-08-09)

26. **One pass over the frozen 250; stability subset raised to 30×5.**
    The primary matrix runs a single pass per case (R=1), and the
    stability subset moves from 30 cases × 3 repeats (PROPOSAL §11) to
    30 cases × **5**. Rationale, from two successful control-arm runs
    over the same 10 discarded candidates —
    `llhb-v1-run-20260809-pilot2` and `-pilot3`, both 10/10 completed,
    artifacts committed under `results/runs/`: the deterministic layer
    reproduced exactly (identical dataset checksum, prompt hash, seeded
    case order and completion rate), while **0 of 10** answers were
    byte-identical across the two runs — an exact figure that needs no
    pattern to compute. An abstention proxy was also run under three
    explicitly written phrase patterns
    (`runner/stability_proxy.py`, regenerable): narrow 3→6 with 7 of 10
    cases flipping, medium 8→8 with 4 flipping, broad 8→9 with 3
    flipping. The counts are pattern-sensitive by construction and no
    single one of them is quotable on its own; what survives every
    pattern is that **individual cases change between runs while the
    aggregate moves less** — aggregate stability without per-case
    stability. The CLI exposes no temperature control (§11 records
    settings verbatim instead). Stage 7's deterministic scorer replaces
    this diagnostic entirely; nothing here is a publishable metric.
    Statistically, repeats shrink sampling noise only as 1/√R while
    costing wall-clock linearly, and 250 cases already supply 250 draws
    for the headline; the subset's job is to *measure* per-case
    variance, not to suppress it, so the extra repeats are spent there.
    Adding repeats to the primary pass stays available as an
    evidence-driven decision once a real delta and its confidence
    interval exist. Supersedes the 30×3 figure in PROPOSAL §11.

## Open for owner decision — Stage 6 harness findings (2026-08-09)

Not rulings. Two facts measured while building the treatment arm, each
with a decision attached that is the owner's to make.

**F1 — the Stage 5 pilots ran with development instructions in
context.** The CLI discovers `CLAUDE.md` upward from its working
directory, and the sandboxed `HOME` does not stop it (on macOS the
user-level file is resolved from the real home regardless of `$HOME`).
Asked directly, a run spawned in the repository answered JA and named
both `~/.claude/CLAUDE.md` and the project file; the same question from
an empty sandbox directory answered that it had received only the
system prompt. Fixed for all future runs by spawning the child inside
the per-run sandbox. Ruling #26's evidence is about run-to-run variance
between two runs contaminated identically, so it is unaffected; no
*answer content* from pilots 1–3 is comparable to a fixed run.
*Decision needed:* whether the pilot control arm is re-run under the
fixed harness before the treatment comparison is reported.

**F2 — both arms now use `--output-format stream-json`.** Ruling #25
says the two conditions run the identical harness with MCP availability
as the only difference; the previous single-JSON format left the
control arm's `"tool_calls": []` as an assertion rather than an
observation. The streaming transcript carries the offered tool list,
every call and every permission denial, which is what makes "a control
run showing any tool activity is invalid" checkable. Pilots 1–3 were
captured under the old format and keep it. *Decision needed:* whether
this is accepted as the recorded harness for v1, or whether the owner
wants the format difference itself recorded as a superseding ruling.

**F3 — treatment tool payloads are statutory text, and this repo holds
none.** Every lovverk tool answers with corpus text, so a committed
treatment run would move rendered legal text into the engine repo,
against `CLAUDE.md`. Applied for now: payloads are never inlined into
`records.jsonl`; each is written to `tools/<case_id>-<index>.json` and
referenced by `result_ref` + `result_sha256`, and `tools/` and `raw/`
are gitignored. What stays versioned is the tool name, the exact
arguments and the payload hash — and because the corpus is pinned,
(tool, arguments, pin) regenerates the bytes the hash was taken over,
so the evidence is checkable without duplicating the corpus. The rule
separates corpus material from model output: a tool payload is
lovverk's text copied verbatim and is excluded, while a model answer is
the measurement itself and is versioned even though it quotes
provisions — a benchmark that dropped the answers would have nothing to
score.
*Resolved by ruling #27:* ratified with the rationale changed from
licensing to regenerability. *Superseded question:* whether treatment
transcripts are
benchmark evidence rather than corpus material and may be versioned
here (which also decides roughly 80–120 MB of artifacts for the full
matrix, extrapolated from the pilot at ~876 KB per 10 treatment cases).

**F4 — `semantic_search` was unavailable for the first treatment
pilot.** No `OPENAI_API_KEY` exists on the build machine (both `.env`
files carry an empty value), so the MCP server serves the tool but
every call would fail. The runner refuses a treatment run in that state
unless `--without-semantic-search` is passed, which records the weaker
surface in `notes`. The pilot ran with 15 of 16 tools usable and the
model never invoked the sixteenth. *Decision needed:* whether the full
matrix requires the key (METHODOLOGY §5 assumes embedding-based
retrieval is available and already records it as an external
dependency), or whether v1 runs on the deterministic 15.

## Addendum — Stage 6 retention ruling (2026-08-10)

27. **Artifacts are kept by regenerability, not by content.** The
    earlier framing — "legal text does not live in this repo" — is not
    defensible as stated: lovverk is public NLOD 2.0 material, and read
    literally the rule would deny the corpus repo's existence. The
    constraint in `CLAUDE.md` concerns Lovdata's raw XML and its
    editorial markup, not statutory text as such. The test that governs
    LLHB artifacts is instead:
    **regenerable from `corpus_pin` → not stored; not regenerable →
    must be stored.** A tool payload is regenerable, since the freeze
    pins lovverk at `6ec7059d` and (tool, arguments, pin) reproduces
    the bytes; keeping it in the repo is a duplicate with no
    evidentiary value. Model output is non-deterministic, so an answer
    nobody kept is an answer nobody — including us — can re-score
    later, which would defeat the pre-registration this stage exists to
    support. Statutory quotes inside model answers therefore stay:
    redacting them would remove the citation-fidelity measurement,
    which is one of the things LLHB measures. Applied: `tools/` and
    `raw/` gitignored, `records.jsonl` and `run-metadata.json`
    versioned, and the 30 previously tracked `raw/` files from pilots
    1-3 removed from the index so the rule and the repository agree. The
    schema gates the rule rather than the writer: `tool_calls[].result`
    is constrained to null, so no future writer can inline a payload
    into a versioned record. It is constrained rather than deleted
    because records already written carry an explicit null and stay
    valid; deleting the field would have required re-running the pilot
    a third time to satisfy a stricter shape of the same rule.

28. **The measuring apparatus is frozen until the v1 runs finish.**
    After the parser hardening, no further change to the runner,
    driver, orchestrator or fairness gate until the v1 matrix has run.
    Same discipline as the dataset freeze and for the same reason:
    anything that changes what is being measured, between the freeze
    and the measurement, invalidates the description of the experiment.
    Defects that would make a run produce a wrong number are the only
    admissible exception, and each is an owner call rather than a
    judgement made while fixing something else. Improvements that only
    make the apparatus nicer wait for v2 — including the transcript
    rewrite considered and declined under #27, which if it ever happens
    is runner-native at write time, never post-hoc, and lands on the
    v1→v2 boundary.

## Addendum — post-run scoring audit ruling (2026-08-13)

29. **Scorer v2: the four audited scoring-layer defects are the #28
    exception, and the fix is a re-score, never a re-run.** The
    2026-08-13 audit of the completed frozen pair (frozen2 /
    treatfrozen4) found four defect classes, each producing a wrong
    published number: #84 — the sentence-window stance rules read a
    refute-then-explain answer (denial up front, the claimed provision
    cited later inside a heading to explain what it actually says) as
    asserting the claim: all 19 C4 "misattribution" fails opened with
    an explicit denial, and the same layer contaminated C6 and the H1
    numerator; #85 — the extractor swallowed the first letter of
    æ/ø/å-second-letter words («første», «følger», «hører») into the
    section id, planting 83 (control) / 214 (treatment) phantom
    citations; #86 — quote_fidelity divided by unverifiable quotes
    against the scorer's own None semantics (true fidelity 2/17 and
    20/99, published as 2/535 and 20/739); #87 — correct C8 refusals
    that quote non-statute material in «» landed UNRESOLVED and
    degenerated no_invention_rate to denominators of 1 and 5. The
    runs, dataset, fairness gate and harness evidence are untouched by
    all four — scoring is a deterministic post-hoc layer, separated
    for exactly this contingency. Ruling: fix all four as scorer v2
    (`llhb-score-v2`, `llhb-metrics-v2`; the stance window rules
    themselves stay `llhb-stance-v1` — the premise-denial and
    source-refusal cue lists are criterion-level and frozen with the
    scorer), apply the same v2 to BOTH arms, publish the v1→v2 metric
    diff as part of the changelog, and never edit or re-execute the
    runs. Accepted, documented limitation of the #87 escape: an answer
    that both refuses in its opening and presents a fabricated but
    unattachable quote passes `no-fabricated-resolution`; an attached
    quote failing verification still fails it regardless. Audit
    evidence is reproducible from the committed artifacts at `981dbdb`;
    the audit note is `analysis/llhb-scoring-audit-2026-08-13.md`
    (gitignored analysis/, referenced for provenance).

## Addendum — confirmatory ceremony ruling (2026-08-15)

Recorded from the owner's adversarial-review discussion of 2026-08-14/15.
The review's verdict ("major revision, potentially strong") and the owner's
decisions below are binding on all further LLHB work.

30. **Epistemic status of the Opus frozen pair; scorer v2 frozen; the
    confirmatory ceremony for every future run.**

    **(a) Status of the completed Opus pair.** The scorer-v2 cue-list
    extensions of ruling #29 (#84 premise-denial cues, #87 source-refusal
    rules) were informed by inspection of the frozen-pair answers. The
    mechanical defect fixes (#85 extractor, #86 denominator) are
    direction-neutral; the cue extensions are calibration on evaluation
    data. Ruling: the Opus frozen pair scored with scorer v2 is a
    **post-hoc diagnostic result, not a confirmatory one**. Its published
    lineage keeps three layers, none deleted, none rewritten: (1) the
    original preregistered metrics as scored at freeze, (2) the scorer-v2
    corrected diagnostic results, (3) a clearly-labelled post-hoc
    supplementary re-analysis (answer-level unconditional H1 rate over all
    250 cases, citation coverage per arm, valid and invalid citation
    instances per answer). LLHB v1 metric definitions are NOT retroactively
    edited (FREEZE.md §5): the re-analysis is a separate, labelled artifact.

    **(b) Scorer v2 is frozen, verbatim norm:** "Outputs that cannot be
    classified by the frozen scorer are recorded as UNRESOLVED. Confirmatory
    outputs MUST NOT be used to extend cue lists, refusal patterns, parsing
    rules, or other semantic classification logic. Any scorer modification
    motivated by inspection of confirmatory outputs creates a new scorer
    version; results rescored with that version are diagnostic/post-hoc and
    cannot retain confirmatory status." Reporting-layer changes mandated by
    this ruling (C8 three-way aggregation, the reason-code taxonomy, the
    answer-level metrics of the analysis plan) land BEFORE the confirmatory
    run and are pinned by the pair manifest's scorer commit; the semantic
    classification layer (`llhb-score-v2`, `llhb-stance-v1`, cue lists,
    parsing) does not change.

    **(c) C8 reporting semantics.** SCORING.md defines No-Invention Rate
    over all C8 cases (n=20); the v2 aggregator silently dropped
    `passed is None` from the denominator — a spec/implementation
    contradiction and a complete-case analysis nobody chose. Ruling: C8 is
    reported **three-way — PASS / FAIL / UNRESOLVED out of all 20 per arm**.
    The existing 7/7 vs 7/11 figures are retained only under the explicit
    label *resolved-case analysis*. Committed artifacts carry the full
    five-way reason code per case: `PASS | FAIL | UNRESOLVED_SEMANTIC |
    SCORER_ERROR | MODEL_ERROR` — one `None` meaning three different things
    is exactly the defect being closed. Papers may collapse to the
    three-way form; artifacts never do.

    **(d) Confirmatory ceremony (first application: the `claude-fable-5`
    pair).** Order, each step gated on the previous: analysis plan frozen
    and hashed → run control → freeze/hash control → run treatment →
    freeze/hash treatment → pair manifest → score → report. A timestamped
    Confirmatory Analysis Plan is committed BEFORE the first model call;
    each arm's run metadata carries `analysis_plan_sha256`. Aggregate
    scoring is mechanically gated: **"Aggregate scoring MUST NOT execute
    until a valid pair manifest exists and all referenced hashes verify."**
    Re-running the scorer on identical inputs (same raw outputs, scorer
    commit, config, plan) is a reproducibility test and is allowed; what is
    forbidden is aggregate scoring or content inspection before both arms
    are complete and frozen. Operational monitoring norm, verbatim:
    "During arm execution, operational monitoring MAY inspect completion
    status, latency, exit codes, MODEL_ERROR counts, transport errors, and
    other content-independent health signals. Response content MUST NOT be
    inspected before both arms are complete and cryptographically frozen.
    Any known violation MUST be disclosed in the run report."

    **(e) Frozen analysis numbers** (data-integrity eligibility gates,
    checked before any effect interpretation — thresholds are not a licence
    for errors):

    | Element | Decision |
    |---|---|
    | Primary estimand | Δ = P(H1&#124;control) − P(H1&#124;treatment), answer-level asserted-H1, denominator = all 250 frozen cases |
    | MODEL_ERROR gate | ≤ 5 affected pairs / 250 (2%); union of pairs with a terminal MODEL_ERROR in either arm; affected pairs drop pairwise from the primary; > 5 → no confirmatory verdict |
    | SCORER_ERROR gate | ≤ 2 affected pairs / 250 (0.8%); > 2 → no confirmatory verdict (the instrument, not the provider, is broken) |
    | Missingness bound | with m ≥ 1 dropped pairs, report the worst-case assignment of all m; *confirmed* additionally requires that the worst-case assignment does not reverse the effect sign |
    | Bootstrap | n = 10,000; seed = 42; unit = paired case; pairs resampled with replacement; statistic = Δ; two-sided 95% percentile CI |
    | Arm-rate CIs | 95% Wilson score intervals |
    | Primary verdict | lower CI bound > 0 → **confirmed**; CI contains 0 → **inconclusive**; upper bound < 0 → **reversed / evidence of harm**. No other vocabulary ("trend", "directionally consistent") carries verdict weight |
    | Secondary metrics | point estimates + CIs only; no confirmatory labels of any kind |

    The bootstrap change 2,000 → 10,000 is made HERE, before the plan
    freeze — a post-run change of inference parameters would itself be a
    researcher degree of freedom.

    **(f) Language pre-commitment.** A confirming Fable result is described
    as *"the treatment effect replicated across two model families on the
    same frozen challenge set"* — model generality, NOT dataset generality
    and NOT "two independent studies": item difficulty is shared, which is
    precisely what makes the cross-model comparison clean. Dataset
    generality requires the future held-out/paraphrase set.

    **(g) Later providers (OpenAI/Gemini).** LLHB v1 has been public since
    2026-08-14; the procedural "unseen before run" guarantee is gone for
    models trained after that date. Future providers run on a NEW private
    held-out/paraphrase set under hash commitment: publish, before any run,
    the SHA-256 over the exact UTF-8 bytes of the frozen JSONL (LF line
    endings, final newline included) plus timestamp/commit and case count;
    disclose the file after all runs. This proves the set existed before
    the runs and was not changed after seeing results.

    **(h) Model identity provenance.** Run metadata records the requested
    model identifier, the returned model identifier (lifted from the
    stream-json transcripts, per case), the CLI/provider version, and run
    timestamps. Published claims state exactly what these prove and no
    more; an alias with a mutable backend proves less than an immutable
    snapshot id, and the paper says which one it has.

Stage 1 scope granted: documentation structure, methodology/specification
documents, dataset schema, freeze/versioning protocol, deterministic scoring
rules, experiment metadata format, matching notebook research-log structure,
and recording of these decisions. Explicitly not yet authorized: candidate
generation, model runs, publication claims, architecture changes, embeddings
changes, semantic/LLM judging.
