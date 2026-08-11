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
| `claude_cli.py` | Claude Code CLI driver for both conditions: exact `claude -p` argv, stream-json transcript parsing (tool trace + harness evidence), schema-valid record assembly. Contract below. |
| `orchestrator.py` | Run orchestrator for both conditions: hermetic whitelist env (API key banned, HOME sandbox) and sandbox working directory, seeded case order, per-case CLI execution with timeout-as-result, raw transcript retention, tool-payload spill, ResultsStore integration. Contract below. |
| `mcp_surface.py` | Stage 6 treatment surface: `--mcp-config` document for the pinned lovverk stdio server, tool names + tool-schema SHA-256 read from the server itself, run-metadata `tool_config`. Contract below. |
| `tool-surface-v1.json` | Frozen LLHB v1 apparatus surface: the tool names + schema hash the server serves, committed as the expectation `check_fairness.py` compares a run's declared `tool_config` against. A unit test re-derives the names from the code on every interpreter and the hash on the apparatus interpreter (3.12, the CI leg pinned for it), so it cannot drift; a deliberate change to the served surface means regenerating it as an explicit apparatus decision. |
| `fairness.py` | Stage 6 fairness checks over committed artifacts: metadata diff against an explicit may-differ list, paired case sets, per-record control/treatment violations. Contract below. |

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

## Claude CLI driver, both arms (2026-08-08, extended 2026-08-09, ruling #25)

* **Module**: `lovspor.llhb.claude_cli` (unit-tested, no subprocess —
  executing the argv belongs to the run orchestrator).
* **Shared argv**: `claude -p <question> --output-format stream-json
  --verbose --model <id> --system-prompt <text> --tools ""
  --strict-mcp-config --mcp-config <config>`. Built-in tools are
  disabled in both arms and MCP is confined to what `--mcp-config`
  declares, so the arms differ in exactly one argument: control passes
  `{"mcpServers":{}}` and nothing else, treatment passes the lovverk
  server plus `--allowedTools mcp__lovverk__*` (last, because the flag
  is variadic). `build_argv` fails closed when the condition and the
  presence of tool access disagree.
* **Why stream-json in both arms**: the single-JSON format reports only
  a final answer, so a control record could only *assert*
  `"tool_calls": []`. The transcript carries the tool list the model
  was offered, every call it made and anything the harness denied — the
  evidence ruling #25 needs to call a control run with tool activity
  invalid. Pilots 1–3 predate this and ran on `--output-format json`.
* **Parsing**: `parse_stream_json` never raises — a non-zero exit,
  unreadable NDJSON, a missing result event, `is_error` or a
  non-`success` subtype become a `ParsedCliResult(ok=False, error=...)`.
  It is fail-closed on the trace: a transcript with no `system init`
  event, a `tool_use` block without a readable name/input/id, or a
  `tool_result` without a `tool_use_id` becomes an error record rather
  than a case that silently reports less tool use than it had. Results
  are matched to calls by `tool_use_id`, never by position, and a
  result matching no call is itself a failure — it is evidence of a
  call the parser did not see. A repeated `tool_use_id` fails on either
  side: two results sharing an id would silently discard one payload,
  two calls sharing one would split a single payload arbitrarily
  between them. A transcript carrying two `system init` events fails
  too: which tool environment applied is not decidable, and taking the
  first would let a case that gained MCP part-way through be recorded
  as toolless.
  `build_result_record` turns either outcome into a schema-valid record
  (`errors[].stage: "request"`, `completed: false`, `final_answer: null`
  on failure) and carries the `harness` block: `exposed_tools`,
  `mcp_servers` (name + connection status), `permission_denials`.
* **Orchestrator** (`lovspor.llhb.orchestrator`): the child environment
  is whitelist-built (`HOME` = per-run sandbox, `PATH`, `TERM`), never
  inherited — user-level settings, hooks and MCP config cannot leak
  into the benchmark conversation. `ANTHROPIC_API_KEY` is banned
  outright: in `-p` mode a present key silently outranks subscription
  OAuth and would move the run onto per-token billing. The child also
  **runs in that sandbox** (`cwd`), because CLAUDE.md discovery walks up
  from the working directory and a sandboxed HOME does not stop it —
  see the contamination finding below. Case order is a seeded shuffle
  over sorted ids (`case_order_seed` in run metadata), fail-closed on
  duplicate ids. A CLI timeout or crash becomes an error record, never
  an aborted run. The raw transcript/stderr/exit of every invocation is
  retained at `raw/<case_id>.json` and referenced via
  `raw_response_ref`; every tool payload is written to
  `tools/<case_id>-<index>.json` and referenced via `result_ref` +
  `result_sha256`, never inlined into the record.
* **Retention split** (owner ruling #27): what a run leaves behind is
  versioned by whether it can be regenerated, not by what it contains.
  A tool payload is regenerable — the freeze pins lovverk, so
  (tool, arguments, pin) reproduces the bytes and `records.jsonl` keeps
  each payload's SHA-256 to check them against — so `tools/` is
  gitignored; a copy in git would be a duplicate with no evidentiary
  value. Model output is not regenerable, being non-deterministic, so
  every final answer and the full tool trace stay in `records.jsonl`.
  Statutory quotes inside those answers stay too: redacting them would
  remove the citation fidelity LLHB exists to measure. `raw/` is
  excluded because a treatment transcript embeds the payloads inline,
  which would put regenerable corpus material back in the repo; what it
  holds beyond `records.jsonl` — thinking blocks and stderr — is not
  scored, and the ordering that would carry evidentiary weight is kept,
  since `tool_calls` is ordered as issued. `tool_calls[].result` is
  schema-constrained to null, so the rule is enforced at the gate
  rather than by whichever writer runs next; the schema also binds
  `result_ref` to `result_sha256` and constrains both it and
  `raw_response_ref` to paths inside the run directory, because a
  reference with no hash cannot be checked against the regenerated
  payload and one that escapes the run does not point at that run's
  evidence.
* **Tool-call reconciliation**: after each case the orchestrator counts
  `"type": "tool_use"` in the transcript text, without walking events,
  and stops the whole run if that disagrees with the parsed trace.
  Undercounting tool calls is the one error this pipeline cannot make
  and has made three times, each in a different part of the parser (an
  aborted generator, a discarded partial trace, a non-zero exit
  short-circuit). A count taken by different means turns a fourth
  occurrence into a failed run rather than a number in a published
  result. It errs toward stopping: a literal `"type": "tool_use"`
  inside a payload would trip it, and a failed run is cheaper than a
  wrong one.
* **Run setup** (`lovspor.llhb.run_setup` + `runner/run_arm.py`):
  `pilot_cases` selects drops only — every frozen case_id is excluded,
  and a limit the drop pool cannot satisfy fails closed.
  `compose_run_metadata` builds the run_metadata document from a typed
  spec and refuses a control run carrying a `tool_config` or a
  treatment run without one; `dataset_checksum` is always computed over
  the canonical bytes of the case set actually being run (for the
  pilot: discarded candidates, stated in `notes`, never the frozen
  250). `runner/run_arm.py` runs either condition (`--condition
  control|lovspor`) and is dry-run by default (prints metadata +
  first-case argv, zero disk writes); `--execute` spawns the CLI and
  writes `results/runs/<run-id>/`. One driver on purpose: two scripts
  would be two places for the arms to drift apart. The system prompt
  lives at `runner/system-prompt-v1.txt` (bokmål, honesty + abstention,
  no Lovspor mention) and must stay byte-identical across both
  conditions and all providers of one evaluation; the CLI version is
  recorded in metadata `notes`. The per-run sandbox HOME under
  `results/runs/.sandbox/` is gitignored.
* **Recorded harness caveat**: `--system-prompt` does not replace the
  CLI's own preamble. Asked what it had received, a run under the
  benchmark prompt reported the Agent SDK preamble ahead of the
  Norwegian instructions. `system_prompt_sha256` therefore covers the
  benchmark's own prompt bytes, not the complete system context; the
  preamble is identical in both arms, so the comparison holds.
* **Pilot findings (2026-08-09)**: macOS Keychain auth does NOT
  survive the HOME sandbox — pilot1 (`llhb-v1-run-20260809-pilot1`)
  failed 10/10 with "Not logged in" (records retained as evidence).
  Resolution: a long-lived subscription token from `claude
  setup-token`, stored as `LLHB_CLAUDE_CODE_OAUTH_TOKEN` in the
  gitignored `.env` and passed to the child as
  `CLAUDE_CODE_OAUTH_TOKEN`; `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`
  stay banned (per-token billing). Pilot2
  (`llhb-v1-run-20260809-pilot2`, 10 drops, control, claude-opus-5):
  10/10 completed, 0 errors, 0 tool calls, 1 turn per case, avg
  34.5 s/case (~2.4 h projected for a 250-case control arm), ~78k
  in / ~20k out tokens per 10 cases, subscription-billed.
* **CLAUDE.md contamination (2026-08-09)**: the Stage 5 pilots spawned
  the CLI with the repository as its working directory. Asked directly
  whether it had received project instructions, a run in that
  configuration answered JA and named both `~/.claude/CLAUDE.md` and
  the project file; the same question from a sandbox working directory
  answered that it had received only the system prompt. The sandboxed
  HOME did not prevent it — on macOS the CLI resolves the user
  instruction file from the real home regardless of `$HOME`. Pilots 1–3
  therefore ran with development instructions in context. The
  contamination was identical across pilot2 and pilot3, so ruling #26's
  run-to-run variance evidence is unaffected; no answer content from
  those runs is comparable to a fixed run. Fixed by running the child
  in the sandbox directory.

## Stage 6 treatment arm (2026-08-09, ruling #25)

* **Backend**: a local stdio `lovspor mcp` server bound to the pinned
  lovverk checkout — never the hosted production endpoint, whose corpus
  moves (METHODOLOGY §5). `runner/run_arm.py --condition lovspor`
  requires `--corpus-path` and calls `verify_pin` against the frozen
  lock's `corpus_pin` before composing anything: wrong HEAD, dirty tree
  or a mismatched manifest timestamp stops the run.
* **Tool surface** (`lovspor.llhb.mcp_surface`): the tool list and
  `tool_schema_sha256` come from `build_server(...).list_tools()` —
  the client-facing view — so the recorded surface cannot drift from
  the served one. The hash covers name, description and input/output
  schema of all 16 tools and is independent of whether
  `OPENAI_API_KEY` is set (tested), because a hash that moved with the
  ambient environment would describe the machine, not the run. Both
  paths in the `--mcp-config` document must be absolute: the CLI runs
  from a sandbox where a relative path resolves elsewhere.
  `verify_server_command` pins `--server-command` to exactly one legal
  path — this environment's `lovspor` entry point — since the surface is
  read in-process and any other executable, including another one in the
  same directory, may serve a different tool set. The anchor is
  `sysconfig.get_path("scripts")`, not `sys.executable`: resolving the
  interpreter follows a venv's `python` symlink out of the venv.
* **Credentials**: the treatment child additionally receives
  `OPENAI_API_KEY`, without which `semantic_search` is served but fails
  on every call — a treatment arm quietly weaker than the one
  METHODOLOGY §5 describes. The runner fails closed if it is missing.
  Query embedding remains an external call at run time; retrieval
  results are captured in full in the tool trace (recorded limitation).
* **Fairness checks** (`lovspor.llhb.fairness` +
  `runner/check_fairness.py`): reads two committed runs and reports
  every way the pair fails to be a control-treatment comparison — a
  metadata field that should have matched and did not (the may-differ
  list is explicit: `run_id`, `condition`, `tool_config`, timestamps,
  notes, per-run counts, `evaluator_version`; everything else is
  compared, so a field added later is checked unless deliberately
  exempted), a case only one arm ran, a case that completed in one arm
  only or errored in both (either way it compares nothing), a control
  case that issued or was offered a tool, a treatment case whose
  offered surface is not exactly `tool_config.tools` or whose MCP
  server did not connect, and any tool call the harness denied. The
  server is checked by name, read out of the run's own
  `mcp__<server>__<tool>` entries: "some server connected" would pass a
  case where lovverk failed and something unrelated came up, which is a
  case with no treatment in it. Because the expectation is read out of
  the declared names, the declared surface is checked before it is
  believed, in the module and in the schema both: a tool with no
  `mcp__<server>__` prefix names no server, so the check would expect
  nothing and pass a run with nothing connected; a tool naming a server
  other than `lovverk` passes as soon as that other server connects,
  which is a run with no lovspor treatment in it. `lovverk` is a
  constant in the fairness module (duplicated from
  `mcp_surface.SERVER_NAME`, which the module cannot import without
  pulling in the whole MCP server) and a literal in the schema pattern;
  tests tie all three together, and one more asserts every tool
  `mcp_surface` actually serves satisfies that pattern. The declared
  surface itself is compared against a fact the run had no hand in:
  `runner/tool-surface-v1.json` freezes the apparatus surface (the 16
  namespaced tool names and the tool-schema SHA-256 the pinned server
  serves), `check_pair` requires it, and a treatment declaration that
  is not exactly that surface — or records a different schema hash —
  is a finding. The surface is a function of lovspor code and the
  interpreter, and of nothing else. Corpus-independent: the schemas
  come from `build_server`, not the documents, so a unit test
  regenerates the document from the code on every run — against two
  content-disjoint corpora, since a single fixture could not
  distinguish corpus-independence from coincidence; it cannot go
  stale, and changing it is an explicit apparatus decision.
  Interpreter-dependent, found the hard way (CI matrix, 2026-08-11):
  tool descriptions come from docstrings, and CPython 3.13 dedents
  docstrings at compile time, so 3.12 and 3.13 serve genuinely
  different description bytes and hash differently. The anchor
  therefore records the apparatus interpreter (`python: "3.12"`); the
  regeneration test checks the names on every interpreter and the
  hash only on the apparatus one, and a run made on a different
  interpreter records a different hash and fails the gate on its own
  — fail-closed, which is the point. Sampling corpora cannot rule out
  deliberately corpus-conditional registration, and does not have to:
  a run's declared `tool_config` is computed from the pinned corpus
  itself (`run_arm.py`), so a surface that diverged on the pinned
  corpus would disagree with the anchor and fail the gate — the test
  is the early warning, the gate is the enforcement. Without that
  anchor, every per-record check read
  its expectation out of the run's own `tool_config`, and a run
  declaring a subset of the real surface — transcripts agreeing with
  the subset — agreed only with itself and passed. Tool calls are
  checked against the declaration too: a record that called a tool the
  run never declared is a finding, whichever of the two documents is
  lying. What the module still cannot check from artifacts alone is
  that the executable *behind* the name served that surface at run
  time; `verify_server_command` fails closed on that when the run is
  made, not when it is judged. A
  completed case with no `harness` block is itself a finding, and so is
  a treatment run declaring no surface at all. The surface comparison is
  by name, not by count: a run that offered a different tool of the same
  arity is a different experiment. Which arm a record belongs to is
  always the caller's claim, never inferred from the declared surface
  being empty — otherwise a treatment run with an empty `tool_config`
  would be graded as a control run and pass. Exits non-zero, so it can
  gate a report. **Known limit, deliberate until Stage 7:** the dataset
  itself has no frozen anchor in this gate. `dataset_checksum` is
  compared cross-arm and verified against the lock at run time
  (`verify_frozen_against_lock`), but `check_fairness.py` does not
  compare it to `dataset/frozen/llhb-v1.lock.json`, because pilots run
  discarded candidates by design and would fail such a gate. Before
  any published number, Stage 7 reporting must add a frozen mode that
  anchors the pair externally: metadata `dataset_checksum` against the
  lock's `dataset_sha256`, the exact record case-id set against the
  frozen JSONL (grammar and count are checked today, identity is not),
  and `lovverk_commit` against the lock's pin — plus whatever other
  preregistered values the publication claims (prompt hash, model,
  seed), which today are checked only for cross-arm equality.
* **First treatment pilot (2026-08-10)**: `llhb-v1-run-20260810-pilot6`
  (control) and `llhb-v1-run-20260810-treat3` (lovspor), same 10
  discarded candidates, same seed, same prompt hash, same
  `claude-opus-5`, both at lovspor `1b06ba6`. Both 10/10 completed, 0
  errors. Control: 0 tools offered, 0 tool calls, 1 turn per case, mean
  32.6 s/case. Treatment: 16 tools offered and the server connected on
  10/10 cases, 58 tool calls (4–9 per case), mean 30.1 s/case, 0 denied
  calls, 0 truncations. Tools used: `get_section` 19, `search_laws` 12,
  `verify_quote` 10, `list_sections` 8, `search_body` 4,
  `corpus_status` 3, `get_law` 1, `semantic_search` 1 — that last one
  is the single errored call of the run, since the tool is served but
  has no key behind it (F4); the model tried it once and moved on.
  `check_fairness.py` reports the pair clean. Answers ran shorter under
  treatment (mean 1809 → 1584 chars) — a length observation, not a
  quality one. All figures are regenerable with `runner/pilot_summary.py`,
  which reads only the versioned records. **Nothing here is scored**:
  whether tool access changes hallucination rates is Stage 7's question,
  and this pilot ran on discarded candidates, never the frozen 250.
  An earlier pair (`-pilot5`/`-treat2`, 62 tool calls) was replaced
  because its metadata carried the absolute path of the corpus checkout.

## What Stage 2 deliberately does not solve

* Answer-level quote *detection* (finding purported quotes in model
  answers) — scoring-stage work; only reference verification exists.
* C8 out-of-corpus proof — manual review stays mandatory.
* Coverage of every Norwegian citation surface form — unresolved
  residue is measured and published instead (SCORING.md §2).
* Freezing (`llhb-v1.lock.json`), candidate generation, runners,
  scoring, provider integrations — later stages.
