# Publication plan — from fixed corpus to a 2027 paper

**Written 2026-07-24. Phase 0 closed 2026-07-28.** Sequenced plan
with gates. Complements
[`roadmap.md`](roadmap.md) (menu of strategic options) and
[`decisions.md`](decisions.md) (decisions actually made). This document is the
*order of work*, not another option catalogue.

**End state:** a peer-reviewed workshop paper in 2027, with the corpus, the
amendment graph and the benchmark as its evidence.

---

## Governing rule

> **Nothing in the critical path may depend on someone else's yes.**

Phases 0–3 below are complete on their own: a fixed corpus, a public repo, an
amendment graph and a published benchmark are a finished artefact whether or
not any reviewer, co-author or institution ever responds. Phase 4 and the
parallel track need other people, and are therefore explicitly allowed to fail
without stopping anything.

This rule exists because the failure mode is known and repeated: work finished,
then parked pending an external decision that never arrives.

---

## Phase 0 — Fix the renderer, then prove it at scale

**Blocks everything. Nothing else starts.**

**Gate:** fabricated-word count at or near zero, measured, corpus-wide.

Why the gate was strict: the audit of 2026-07-22 measured **74 622 fabricated
words across 2 690 of 5 916 documents (45.5%)** — adjacent blocks fused without
a separator (`enngelatin`, `oppfyllerkravene`,
`Høyesterettlagmannsrettenetingrettene` in `domstolloven-dl § 1`). No legal text
was missing; the defect was boundary loss, which makes affected passages
unsearchable and unquotable.

### Gate met, 2026-07-27 — 0 fabricated words

Measured over **5 916 documents** (one, `sf-20200309-0720`, does not render at
all — see below). Six documents sit below 100% token coverage, lowest 99.9568%.

The defect was three classes of the same bug, not one — and once the first two
were fixed, all but 140 of the 13 631 words still being reported were the
audit's own measurement error, not the renderer's:

| | fabricated words | documents |
|---|---:|---:|
| audit of 2026-07-22 | 74 622 | 2 690 |
| after fusion classes 1–2 (PR #158, `8aa6b13`) | 13 631 | 846 |
| **of which real** (instrument corrected) | **140** | **49** |
| after class 3 — block wrappers (PR #158, `67cac18`) | **0** | **0** |

Two figures circulate for that 140 and both are right. **140** is the *stock* of
real fabrications standing before `67cac18`; **138** is the number the fix
*eliminates*. The two that survive it are the audit's own list-marker artefact
(`9. 1. ` stripped once, leaving a stray `1`), removed later in PR #157 — which
is why the end state is 0 rather than 2. Quote which one you mean.

**Class 3, found 2026-07-27:** `_is_block_element` recognised only the tags the
renderer had an explicit branch for, so every other block-level wrapper —
`<footer class="footnotes">` (2 063 sites / 772 docs), `<div class="indent">`
(144 / ~50), a stray `<li>` (171 / 27), `<blockquote>`, `<figure>` — went
through the inline path, which concatenates a whole subtree with no separator.
Worst case: `sjøloven` Art 4bis emitted an entire annex, articles and list items
included, as one run-on paragraph.

**The instrument was wrong too, and by more than the renderer.** The audit put a
word boundary at every XML element seam, including inline markup that runs
*through* a word: `CO<sub>2</sub>`, `km<sup>2</sup>`, a footnote marker, the
`E<i>ff</i>EXT™` trademark. **13 499 of the 13 631 were this artefact** — they
hid the 140 that were real. Fixed in PR #157 (`2817193`), which also subtracts
the footnote marker a `§` heading carries but never renders (424 markers in 83
documents) as a *documented* drop, the way not-in-force elision already is, with
the reason written into the renderer (`0d661a0`): 206 of those markers sit on
the § number, so emitting them yields `### § 5.1` — a form that reads as a
citation of subsection 5.1, which is worse than a missing pointer for a corpus
queried through `validate_citation`.

Cost of class 3, measured against the class-1–2 tree: **217 documents change
bytes**, 0 documents lose token coverage, 22 gain it. Classes 1–2 rewrote far
more (222 751 articles in 3 514 documents, 184 344 list items in 3 478).
`RENDERER_VERSION` 3 → 4 covers all three classes in a single re-render, which
then rewrote **3 788 documents and 9 871 files** in the published corpus.

Reviewed 2026-07-27, both PRs **PASS**. The review reproduced the byte-churn,
coverage and zero-fabrication figures independently, confirmed the
`RENDERER_VERSION` reasoning against `change_detector.py` and `orchestrator.py`,
and verified the audit still detects injected damage after its correction —
the one thing that would have made a smaller number worthless. Mutation run on
the renderer: 7 survivors, none in the new dispatch; the two that marked a real
test gap (a nested `h1`/`h2`) are closed in `211f04d`.

### Verified on the corpus, 2026-07-27

The re-render ran (`85177318e`, 3 788 documents, renderer v4) and
`scripts/audit_render_bytes.py --full` then measured the **published** corpus,
not a render held in memory:

| | |
|---|---:|
| documents audited | 5 914 |
| OK | 5 906 |
| `VINTAGE_SKEW` | 6 |
| `FLAG` | 2 |

On the 5 908 documents where the comparison is valid — local tarballs matching
the snapshot that rendered the corpus:

> **fabricated words 0 · token coverage ≥ 99.9568% · char coverage ≥ 99.9325%
> · byte-identical re-render 5 908 / 5 908**

Every fabricated word the audit reported at all (39) sits inside the 6
vintage-skewed documents, confirmed by set intersection, and is the instrument
comparing a five-day-old XML snapshot against today's Markdown. The 2 `FLAG`s
are `narkotikaforskriften` and `tilsetningsstofforskriften` at 99.93% and
99.99% char coverage; the shortfall is chemical names dense with `<sub>`/`<sup>`
(`tetrametylsyklopropyl`, `naftalenyl`, `disulfo`), not lost text.

**`sf-20200309-0720` is not a gap.** It is a Lovdata placeholder ("Vi klarer
dessverre ikke vise hele dokumentet"), detected by `is_content_placeholder`,
withheld with a logged warning, tombstoned — and self-healing: if Lovdata ever
publishes the real text it re-enters as `new`. Deliberate, and already
implemented.

### Phase 0 closed, 2026-07-28 — renderer 5 on the corpus and in production

A second defect surfaced after the v4 gate, and it was the one that mattered
most: a footnote marker rendered as bare text fused into the word it annotates
(`Amtskasserere1`), 7 940 times across 664 documents. Not cosmetic — measured
against the **live** hosted server, `verify_quote` rejected the faithful quote
of `§ 8` of `lov-om-omordning-af-det-civile-embedsverk` as *"paraphrased rather
than verbatim, or hallucinated"* and accepted only the variant carrying the
digit. The anti-hallucination guard called the statute's own words a
hallucination, and an AI trusting that verdict corrects itself INTO text that
does not exist.

Fixed in three places (PR #162), because one rendering rule was not enough:
body text, heading title spans, and `verify_quote`'s matcher — without the
last, the fix only moves the false negative from `Amtskasserere1` to `[^1]`.
Markers now render as GFM `[^n]`, left orphaned because labels are not
document-unique (they repeat inside 383 of the 1 092 documents that carry
references). The same PR drops the dead `_PARAGRAPH_CLASSES` branch: proven to
change 0 of 5 917 documents.

Verified on the published corpus and on production:

| | |
|---|---:|
| documents on `renderer_version: 5` | 5 914 |
| re-rendered | 3 786 |
| under-embedded sidecars repaired | 26 |
| **fabricated words** (5 905 documents with a valid comparison) | **0** |
| min token coverage | 99.9568% |
| byte-identical re-render | 5 905 / 5 905 |

> `verify_quote("Amtskasserere og Politimestre samt Amtskassereres Betjente")`
> → **`verified: true`**, while an invented quote of the same § still returns
> `false`. The guard stopped lying without being loosened.

**Operational note, learned the hard way.** The corpus reaches the hosted MCP
through the daily `lovspor-fetch-corpus` timer, but the ENGINE does not. PR
#162 changed `src/lovspor/mcp.py`, so until the droplet pulled and restarted,
the corpus carried `[^1]` while the server had no idea to normalise it — half
the fix, and the symptom looked identical to no fix at all. A change touching
`mcp.py` needs `git pull` + `uv sync` + `systemctl restart lovspor-mcp`;
a renderer-only change does not.

---

## Phase 1 — Open the repository

**A decision, not a task. Costs an afternoon.**

The distribution note of 2026-07-14 made the engine private for a commercial
pivot. The decision of 2026-07-22 (`analysis/llm-infra/06-decision-free.md` —
private working notes, not in this repo) reversed the commercial part — but
*free* and *open source* are not the same thing. *(Update 2026-08-02: the
README, `docs/mcp.md`, `docs/roadmap.md` and `docs/decisions.md` §15 now
carry the open-infrastructure framing; the stale "engine is private" claims
are gone. The repository itself is still PRIVATE pending the rest of this
phase.)* *(Completed 2026-08-03: this phase executed — the repository is
PUBLIC (history rewritten into the canonical `bartoszkobylinski/lovspor`,
old repo archived privately) and `0.4.0` is live on PyPI via Trusted
Publishing. Everything below in this phase is historical plan, not pending
work.)*

Everything after this phase is invisible while the engine stays closed.

Deliverables:
- engine repository public
- README rewritten to lead with the problem, not the architecture *(landed
  2026-08-06, after the phase closed: the 2026-08-03 pass fixed the framing but
  the page still opened on engine internals; the rewrite moved distribution and
  sprint history out to `releasing.md` / `decisions.md`)*
- `analysis/llm-infra/10-byte-audit-results.md` promoted out of the analysis
  directory — the audit is an exhibit, not a working note
- explicit scope statement in the README, mirroring `corpus_status`: acts and
  central regulations only; no agency circulars, court practice, preparatory
  works or municipal regulations

Do this **after** Phase 0 so what opens is clean, and **before** Phase 2 so
publication is not deferred behind one more feature.

---

## Phase 2 — Amendment graph (parse the `Endret ved …` chains)

Turns the amendment footnotes already present in the corpus into queryable
structure. Feasibility measured 2026-07-24 (§ *Measurements* below): the format
is highly regular, residue **0.19%**.

Work:
1. Parse footnote lines into `(target_section, amending_act, passed_date, in_force_date)`.
2. Resolve the **49.1%** of references that carry no markdown link. The
   text → `ref_id` transformation is mechanical (Norwegian month name → number).
3. **Validate every constructed reference against the manifest — never trust a
   derived id.** Reuse the `validate_citation` pattern.
4. Expect most constructed ids to fail validation: they point at spent amending
   acts absent from the `gjeldende` datasets. That is correct behaviour, not an
   error. Model resolution three-valued, as `cross_references` already does:
   resolved / provably absent / outside corpus.
5. Model treaty-based amendments (`traktat/`, `avtale`) as a **second instrument
   class**, not as failed act references — that is what the 80-line residue is.

**Why this precedes the benchmark:** without it, the benchmark can only sample
sections at random and report an accuracy figure anyone with Lovdata could
produce. With it, test items can be *selected by when the provision changed*,
which turns a single number into a curve — accuracy as a function of amendment
date. The git history reaches back to 2026-04-26 only; the footnotes reach
**1905**. The graph is what makes a temporal claim defensible.

---

## Phase 3 — Staleness benchmark

**The novel contribution. Everything before this exists to make it credible.**

The question no one else can ask with a current-law corpus alone:

> By how much is a given model behind Norwegian law?

This requires *dated* ground truth — `get_law_at` (Sprint 10) plus the Phase 2
amendment dates.

### The one design decision that decides whether this works

**What counts as a correct answer.** Three options; only one is buildable
without a jurist:

| variant | measures | solo? |
|---|---|---|
| verbatim recall of § text | memorisation, not usefulness | yes, but weak |
| did the model *retrieve* the right § | this MCP server, not the model | yes — tests our own product |
| is the answer legally correct | reasoning | **no** — needs a jurist to author ground truth |

**Chosen design — closed-form questions anchored on changed values.** Not
"what does § 21 say", but *how much is X* / *from when does Y apply* / *is Z
permitted* — where the answer is a number, a date or yes/no, **and that value
changed at a known date**. Scoring is exact match: no judge, no rubric, no
jurist. Phase 2 is what makes such items findable.

Second axis, cheap and probably unreported anywhere: `ikr.` markers run to
**2030**, so the corpus contains changes already enacted but not yet in force.
Testing whether models know about those is forward-looking rather than
backward-looking.

Deliverables: benchmark harness, question set, results across several models,
published with the method.

**Do not attempt a legal-correctness benchmark.** `analysis/llm-infra/README.md`
already lists "who authors the reference answer in the benchmark" among the
things the source discussion never addressed. It is still unaddressed.

---

## Phase 4 — Write-up and submission

**Target: 2027.** NLLP 2026 closes **2026-08-11** — 18 days from writing, which
is not achievable alongside Phases 0–3. These deadlines recur annually.

Candidate venues:
- **NLLP** (Natural Legal Language Processing) — workshop co-located with EMNLP.
  2026 cycle: submissions 11 Aug, notification 15 Sep, camera-ready 22 Sep,
  workshop 28–29 Oct. Assume a comparable 2027 cycle; **not verified**.
- **NoDaLiDa 2027** — 26th Nordic Conference on Computational Linguistics,
  Copenhagen. **Dates not yet announced.** Regional, smaller, natural fit.

Two contributions, one paper:
1. **Resource** — a version-controlled corpus of national legislation with
   per-document change history and an unusually clean licence story (NLOD 2.0,
   redistribution and commercial use both permitted).
2. **Method** — version-controlled law as temporal ground truth for measuring
   model staleness. This is the transferable part: any jurisdiction with an open
   law API can replicate it, which is what makes it a paper rather than a
   dataset card.

**Not a pretraining corpus.** Legal text is over-represented in pretraining
mixes and gets downweighted (see the `v0.3.0-preview` notes in the author's
Polish DynaWord work). The contribution here is retrieval and verification
ground truth, not tokens.

**Language constraint, stated plainly:** the paper must be in English at a level
the author does not currently write unaided. This converts a co-author from
*desirable* to *structural*. It does not block Phases 0–3.

---

## Parallel track — allowed to fail

Runs alongside, blocks nothing:

- Contact the National Library AI-lab / Språkbanken / the UiO language
  technology group **after Phase 1**, with a link to a working, public,
  audited artefact rather than a proposal.
- Assume no reply is the default outcome. A reply is a bonus, never a
  precondition.
- If no academic co-author materialises, Phases 0–3 still stand as a complete,
  citable technical artefact, and a technical report needs nobody's approval.

---

## Measurements established 2026-07-24

Sweeps run read-only over the corpus; scripts reproducible.

**Amendment-footnote regularity**

| metric | value |
|---|---|
| documents in corpus | 5 916 |
| documents carrying amendment footnotes | 2 873 (48.6%) |
| footnote lines | 41 976 |
| act references total | 220 441 |
| — with markdown link (machine-resolvable) | 112 168 (50.9%) |
| — bare text, no link | 108 273 (49.1%) |
| `ikr.` in-force markers | 55 582 |
| reference year range | **1905 – 2026** |
| `ikr.` year range | **1971 – 2030** |
| residue (footnote yielding no reference) | **80 lines (0.19%)** |

Residue is almost entirely EEA/EFTA material (`eøs-loven`, `eric-lova`) amended
by treaty or agreement rather than by act — a second instrument class, not
parser failure.

**Why 51.4% carry no footnote**

| category | total | % | lover | forskrifter |
|---|---:|---:|---:|---:|
| has footnotes | 2 873 | 48.6% | 577 | 2 296 |
| never amended | 2 668 | 45.1% | 132 | 2 536 |
| **amended but no footnote** | **136** | **2.3%** | 10 | 126 |
| no dates — cannot classify | 230 | 3.9% | 42 | 188 |
| `last_change_in_force` < `date_in_force` (anomaly) | 9 | 0.2% | 2 | 7 |

The absence is overwhelmingly legitimate: 45.1% were never amended, mostly
regulations. **The real gap is 136 documents.**

Therefore the honest coverage claim is not "48.6% of the corpus" but:

> **Amendment history for ~95.5% of documents that were ever amended**
> (`lover` 98.3%, `forskrifter` 94.8%).

Uncertainty: 230 documents cannot be classified. If all of them turned out to be
amended without footnotes, coverage would fall to 88.7%. State the range
**88.7–95.5%**, not a single figure.

**Amending acts present in the corpus:** 112 in `lover`, 10 in `forskrifter`.
Some spent amending acts do survive in the `gjeldende` datasets (they carry
still-operative transitional provisions). 122 is a residue, not a basis for
reconstructing historical text.

**Legal status of distributing the tool** — `advokatloven § 66` first paragraph:
*"Enhver kan yte rettslig bistand, med mindre annet er fastsatt i lov …"*.
Note that `domstolloven § 218`, still cited by older regulations, no longer
governs this — its content was replaced when `advokatloven` entered into force
on 2025-01-01. A live example of why derived cross-references must be validated
rather than followed.

---

## Open questions

- ~~Size of Phase 0 — unknown until the corpus-wide audit runs.~~ **Answered
  2026-07-27:** three fusion classes in the renderer, one measurement error in
  the audit; 0 fabricated words, 217 documents re-rendered. See Phase 0.
- Is a `sup.footnotereference` glued to the word it annotates (`amtskasserere1`,
  ~4 800 word occurrences) acceptable in a corpus meant to be searched? The renderer
  is faithful there and the audit no longer counts it, but a BM25 or embedding
  index still sees a word that no query will spell. Not a fabrication — a
  retrieval question, and untested.
- The 9 documents where `last_change_in_force` precedes `date_in_force`:
  source error or field-mapping error? Not investigated.
- The 230 documents without dates: not classified.
- `semantic_search` calls OpenAI per query. A public, free, open server means
  unbounded third-party spend. Existing quotas are a runaway-loop brake, not a
  budget control. Needs a hard cap, or degradation to `search_body` when
  exhausted, before Phase 1.
  *(Classification 2026-08-02: making the source repository public adds no
  spend surface by itself — the hosted endpoint requires a credential on every
  request and self-hosters bring their own OpenAI key. The exposure scales
  with the hosted credential population: bounded while tokens are
  operator-issued, unbounded only if AuthKit self-registration is left open.
  So the cap/degradation is recommended operational hardening for the hosted
  service — it gates expanding hosted access, not repo visibility. Whether
  the original "before Phase 1" binding stands or is waived is an owner
  decision.)*
- NLLP 2027 and NoDaLiDa 2027 dates — not announced at time of writing.
