# Project Decisions Log — lovspor + lovverk

Single source of truth for why this project looks the way it does. Every non-obvious choice is captured here with rationale, so that a future Claude session (or human contributor) can pick up cold without context loss.

Update this file whenever a new decision lands.

Last updated: 2026-04-27

---

## 1. Project identity

- **`lovspor`** — the **engine**. Python package. This repo.
- **`lovverk`** — the **corpus**. Markdown-only data repo. Separate repo.
- Both public on GitHub under `bartoszkobylinski/`.

Two repos because two audiences: engine repo is for contributors and portfolio, corpus repo is for consumers (AI/RAG, researchers) who want clean data without code noise.

## 2. Purpose

Build a versioned Markdown corpus of current Norwegian law, auto-updated from Lovdata's public-data API, with every change landing as an individual Git commit. Primary use case: AI/RAG ingestion with auditable provenance.

**Out of scope (deliberately):**
- Historical point-in-time reconstruction of laws
- Local/regional regulations (only `sentrale forskrifter`)
- Full parliamentary enrichment from Stortinget
- Interpretation or legal advice

## 3. Data sources

Single ingestion channel: **Lovdata public-data API** (`https://api.lovdata.no/v1/publicData/`), announced 2025-11-03 under NLOD 2.0.

| Endpoint | Purpose |
|---|---|
| `GET /v1/publicData/list` | Catalogue of available archives (JSON) |
| `GET /v1/publicData/get/{filename}` | Download archive (tar.bz2) |

Tracked archives:
- `gjeldende-lover.tar.bz2` — current Norwegian laws (~5.8 MB)
- `gjeldende-sentrale-forskrifter.tar.bz2` — current central regulations (~21 MB)

Optional later:
- `lovtidend-avd1-{year}.tar.bz2` — change announcements (if we want a per-act changelog)

**Stortinget open data** (`data.stortinget.no`) — **NOT used as a primary source**. It publishes parliamentary metadata (saker, voteringer, publikasjoner), not consolidated law text. Possible enrichment in a later phase. Rate limit: 100 req/min.

## 4. Legal posture — Option A (conservative)

Decided 2026-04-22.

**Raw XML from Lovdata is never committed to any repo** (not `lovspor`, not `lovverk`). Only the rendered Markdown derivative and a manifest of XML hashes are published.

Rationale: NLOD 2.0 literally permits redistribution, but Stiftelsen Lovdata has historically asserted rights over their editorial consolidation/markup. The prior-art project `cloveras/lovdata2` took this same conservative stance in its `LEGAL.md`. By publishing only our derivative (Markdown), we:
- sidestep any argument about editorial markup
- still allow anyone to verify by re-running the engine against the same tarball
- remain in the spirit of NLOD 2.0 while minimizing legal surface

Implementation:
- `data/cache/` in `lovspor` is gitignored (holds downloaded tarballs + extracted XML)
- every rendered Markdown carries NLOD 2.0 attribution in YAML front matter
- `lovverk/LICENSE` and README state attribution + CC0 on structure
- `docs/legal-and-sources.md` documents the stance

**Forbidden:**
- Scraping `lovdata.no` HTML (Lovdata's regular brukervilkår forbid this for AI; NLOD does not cover it)
- Redistributing Lovdata's raw XML from this project

## 5. Architecture

### High-level flow

```
Lovdata public API
        ↓
download tar.bz2  →  data/cache/  (gitignored)
        ↓
extract + normalize XML
        ↓
SHA256 per document (on normalized XML)
        ↓
diff against manifest.json
        ↓
render changed docs → Markdown
        ↓
write to lovverk repo (sibling clone)
        ↓
git commit (per-document by default since Sprint 4; see §12a for all three modes)
```

### Key invariants

- **Hash on normalized XML, never on rendered Markdown or HTML.** Rendering is deterministic, but changes in the renderer would otherwise trigger false-positive commits.
- **Renderer must be byte-identical deterministic.** Tested. Same XML in → same MD out, every time.
- **Sync is idempotent.** Running twice on same upstream state = 0 file changes = 0 commits.
- **Commit granularity:** as of Sprint 4 PR #17, the default is `per-document` — one commit per add / update / rename / remove with conventional-commit messages, plus a final `sync: update manifest, index, and history` commit (the "and history" suffix added in Sprint 5 PR-B; `single` and Sprint 4 migration modes split history into a follow-up commit because of the chicken-and-egg with `git log --follow`). See §12a for commit modes and §12d for the history flow. `git log lovverk/<dataset>/<slug>.md` now shows the amendment history per act, and `lovverk/<dataset>/history/<slug>.json` provides the structured per-act event list (§12d).

### Update cadence

Lovdata updates tarballs nightly around **01:30 UTC**. Polling more often is wasted work. Target: GitHub Actions cron daily at ~06:00 CET (≈04:00 UTC), 2–3 hours after Lovdata's drop.

## 6. Two-repo layout

```
github.com/bartoszkobylinski/lovspor/   ← engine (Python)
  src/lovspor/
  tests/
  docs/
  CLAUDE.md, AGENTS.md, pyproject.toml, ...

github.com/bartoszkobylinski/lovverk/   ← corpus (data)
  lover/
    <slug>.md      ← Sprint 4: human-readable slug, e.g. skatteloven.md
    INDEX.md       ← Sprint 4: alphabetical discovery list
  forskrifter/
    <slug>.md
    INDEX.md
  manifest.json
  README.md, LICENSE
```

The engine pushes to the corpus via the `LOVVERK_DEPLOY_KEY` deploy key (configured Sprint 3 PR #16; runbook in `docs/operations.md` §Deploy key setup).

Local layout:
- `~/Programming/Python/lovspor/` — engine repo + `.venv/` in project
- `~/Programming/Python/lovverk/` — corpus repo (no venv, pure data)

## 7. Toolchain

| Area | Choice | Why |
|---|---|---|
| Runtime | Python 3.12 | Stable, PEP 695 available, `typing.Self` available |
| Env/deps | `uv` | Fast, lockfile, dev groups. `.venv/` in project |
| Lint + format | `ruff` | Single tool replaces black + flake8 + isort |
| Security lint | ruff `S` rules + `bandit` | Overlap is fine; bandit runs via `uvx` |
| Types | `mypy` strict mode | Wired into CI + pre-commit |
| Tests | `pytest` + `pytest-httpx` + `pytest-cov` | Transport mocked only; logic never mocked |
| Mutation | `mutmut == 2.5.1` | See §9 |
| Hooks | `pre-commit` | Wired for ruff, format, mypy, pytest unit |
| Build | `hatchling` | Default modern backend |
| HTTP | `httpx` (sync) | Simple and enough for sequential downloads |
| XML | `lxml` with `resolve_entities=False, huge_tree=False` (when added) | XXE / billion-laughs mitigation |
| Models | `pydantic` 2.x | Frozen, `extra='forbid'`, alias support |
| CLI | `typer` | Multi-command app |
| Env vars | `python-dotenv` | Loads `.env` |
| Secrets scan | `gitleaks` (brew install) | Pre-commit + skill |

## 8. Security posture

Global Claude skill at `~/.claude/skills/security-check/SKILL.md`, invokable as `/security-check`. Runs 6 checks:

1. `bandit -r src/` (static analysis)
2. `pip-audit` against resolved deps (vulnerability scan)
3. `gitleaks detect` (secret scan)
4. `tarfile.extractall` / `extract` must use `filter='data'` (CVE-2007-4559)
5. `lxml` parsers must set `resolve_entities=False`, `huge_tree=False`
6. No `subprocess.*(..., shell=True)`

Baseline on scaffold (2026-04-22): all six clean.

## 9. mutmut pinned to 2.x — no PEP 695

mutmut 2.5.1 pinned in `pyproject.toml`. mutmut 3.x has open bugs:
- #486: breaks editable installs (we use them via `uv sync`)
- #480: `@dataclass` methods produce zero mutants (we use Pydantic dataclasses)
- #485: crashes on initial test run
- #490: race condition in dict iteration

**Consequence:** no PEP 695 generic syntax. mutmut 2.x parser predates PEP 695 and crashes on `def foo[T](...)`. Use classic `TypeVar` from `typing` instead. Ruff rule `UP047` is globally disabled to prevent accidental reintroduction.

When mutmut 3.x stabilizes (track their issue tracker), revisit this constraint.

## 9a. Mutation testing baseline expectations

Decided 2026-04-23 after Sprint 3 PR #11; baseline numbers updated 2026-04-26 after PR #16. Codex runs mutmut on every PR review (see CLAUDE.md, §Testing strategy). Current state at Sprint 3 close: **~70% kill rate** with **~200 surviving mutants** (specifically 465/663 killed, 198 survived as of PR #16 review). The number grew through Sprint 3 as the codebase grew (orchestrator, settings, document_io, git_commit added ~140 mutants of which a meaningful fraction is equivalent per the categorization below). This remains the expected baseline, **not a quality emergency**.

**Why so many survivors?** Categorization based on Codex reports across PR #5 → #11:

| Category | Approx share | Why it survives | Action |
|---|---|---|---|
| String content (`"GET {url}"` → `"XXGET {url}XX"` etc.) | ~50% | Tests check structural behavior (exception type, response shape), not exact text. Mutations are semantically equivalent. | Accept. Killing requires brittle exact-text assertions. |
| Default arg values (`timeout=120.0` → `121.0`) | ~15% | Tests pass explicit values; defaults often unexercised. | Accept unless the default is load-bearing. |
| CLI metadata (Typer `help="..."`) | ~10% | Pure documentation strings. | Accept — these are not behavior. |
| TypeVar names (`TypeVar("T")` → `TypeVar("XXT")`) | ~2% | Name is a label only. | Accept — equivalent. |
| **lxml `no_network` flag** | ~1% | lxml does not expose this flag; `resolve_entities=False` already short-circuits any code path that would attempt network access, making it dead-code from a testability standpoint. | Accept — register, document, move on. |
| **lxml `huge_tree` flag** | ~1% | **Killable.** `huge_tree=False` enforces a 256-level nesting cap that `huge_tree=True` removes. Test `test_canonicalize_rejects_excessively_deep_xml` (PR #13) parses a 300-level payload and asserts ParseError, killing the mutation. Don't lump this with `no_network`. | **Fix** — added in PR #13 after Codex reviewer caught the misclassification. |
| **Real critical-path gaps** | ~5–10% | Genuine missing test coverage that lets a real bug pass. | **Fix promptly** — Codex flags these per PR. |

**Policy:**

- Codex flags critical-path survivors per PR; we fix those on the same branch.
- Equivalent survivors are **registered, not chased**. We do not configure mutmut to filter them out — we want the raw signal so a future regression that adds new equivalent mutants is visible.
- Survivor count and kill rate are tracked in PR descriptions (Codex always reports them).

**Revisit triggers** (when this decision should be reopened):

1. Survivor count exceeds **250** (originally 150; bumped 2026-04-26 once we observed end-of-Sprint-3 baseline of ~200).
2. Kill rate drops below **65%** (originally 70%; bumped 2026-04-26 to leave headroom over current ~70% baseline).
3. A real bug ships that mutation testing should have caught (signals our equivalence-class judgment is wrong).
4. mutmut 3 ships a stable filter API that lets us suppress equivalents cleanly without configuration drift.

## 9b. Property-based testing with Hypothesis

Added 2026-04-27 after the first scheduled migration sync crashed in production with `OSError: File name too long`. Root cause was a 290-character forskrift title slugifying past POSIX NAME_MAX (see §12b). Codex's PR #17 review missed it; mutation testing missed it; both because every unit + integration test used synthetic titles ≤ 50 characters.

**Lesson:** synthetic test data does not exercise the long-tail distribution of real Lovdata content. Hand-coded edge cases (`""`, `"x" * 1000`) catch some of this but not all — they presuppose the writer thought of the failure mode. Property testing flips it: declare an invariant, let Hypothesis search for counterexamples.

**Where:** `tests/property/test_slug_properties.py`. Five invariants on `derive_slug` and `resolve_collisions`:

- slug ≤ 200 UTF-8 bytes (the production guard)
- slug is non-empty (filename usability)
- slug is a pure function of inputs (change-detection contract)
- `resolve_collisions` is a bijection (no document silently dropped)
- `resolve_collisions` is input-order independent (determinism)

**Strategies:** Latin + Latin-1 Supplement + Latin Extended-A (`0x20`–`0x017F`), titles up to 500 characters, dictionaries up to 20 entries. Lovdata never emits scripts outside that range for legal text, so this gives realistic coverage without generating CJK or emoji that the slugify rules would just collapse to hyphens.

**Where to add property tests next** (when motivation strikes, not as urgent work):
- `extraction/tarball.py` — member-name validation invariants (no path traversal escapes for any input).
- `parsing/xml_normalizer.py` — canonicalization round-trip (parse + canonicalize + parse should be a fixed point).
- `storage/manifest.py` — JSON round-trip determinism for arbitrary valid manifests.

**Cost:** `hypothesis` added to dev dependencies (`pyproject.toml`). Default 100 examples per test × 5 tests ≈ < 1 s in local CI. Negligible.

## 10. Workflow — how Claude works here

Full contract in `CLAUDE.md`. Key points:

1. **Small chunks** — 1 commit = 1 logical change. Every commit independently green and bisectable.
2. **TDD per chunk** — failing unit test first, then minimal code to green.
3. **Pre-commit mandatory** — ruff + format + mypy + pytest + `/security-check`.
4. **Feature branches only** — `feat/`, `fix/`, `refactor/`, `test/`, `docs/`. Never commit to `main` except the single bootstrap commit.
5. **PR → Codex → merge** — Claude opens PR with prepared Codex prompt, STOPS, user runs Codex, Claude fixes any bugs on the same branch, **only the user merges**.
6. **No AI attribution** in commit messages, PR descriptions, or code comments.

## 11. AGENTS (Codex) contract

`AGENTS.md` in repo root. Codex **writes tests and finds bugs only** — never refactors, never changes features. Each PR description carries a standardized Codex prompt (see `.github/PULL_REQUEST_TEMPLATE.md`).

Codex focus areas for lovspor:
- Determinism of rendering
- Hash stability
- Change detection correctness (new / removed / changed / unchanged)
- XML parser safety
- Tar extraction safety
- Manifest round-trip
- Edge cases (empty tarball, malformed UTF-8, large files, timeouts)

## 12. Sprint log

### Sprint 0 — Scaffold (PR #1, MERGED 2026-04-22)

Squashed to single commit on `main`. Established: pyproject.toml, ruff/mypy/pytest config, pre-commit, GitHub Actions test workflow, dependabot, CLAUDE.md, AGENTS.md, docs/, minimal Typer CLI.

Codex found: broken entry point (fixed), docs/operations.md showing unimplemented commands (fixed).

### Sprint 1 — Lovdata client: list endpoint (PR #5, open)

Branch: `feat/lovdata-source-client`

Landed on branch:
- `feat(errors)`: `LovsporError`, `NetworkError`, `ParseError`, `ExtractionError`
- `feat(retry)`: `retry_with_backoff` with exponential backoff (TypeVar, not PEP 695)
- `feat(sources)`: `LovdataClient.list_datasets()` + `LovdataArchive` Pydantic model
- `test(sources)`: cover `httpx.RequestError` retry path

Codex round 1 found: PEP 695 syntax crashed mutmut 2.x. User fixed before Claude could (commits `70d3c0f`, `39069dc`, `99d1457`). Claude session had a crash, came back and made a near-duplicate revert commit `2def5a6` — cosmetic noise, will be squashed on merge.

### Dependabot PRs #2 / #3 / #4

Auto-opened after PR #1 merge (dependabot config kicked in). Ignored until after Sprint 2; will review together:
- `actions/checkout` → v6
- `astral-sh/setup-uv` → v7
- pip dev dependencies group

### Sprint 2 Part A — Lovdata download (PR #6, MERGED 2026-04-22)

Squashed to `feat: LovdataClient.download with streaming, integrity, and path traversal defense`. Added:

- `DownloadResult` Pydantic model (frozen)
- `LovdataClient.download(archive, dest_dir)` — streaming 64KB chunks, `.part` + atomic rename, sha256 + size verification, retry on transient failures
- Two-layer path-traversal defense: `LovdataArchive.filename` Pydantic field_validator at the model boundary, `dest.parent == dest_dir` check at download

Codex rounds: 2 (first found HIGH severity path traversal via `archive.filename`; fixed with two-layer defense).

### Sprint 2 Part B — Safe tarball iteration (PR #7, in review)

Branch `feat/safe-tarball-extraction`. Adds:

- `src/lovspor/extraction/tarball.py` with `iter_tarball_xml(path) -> Iterator[TarballMember]`
- **Memory posture:** members read fully into memory via `fh.read()`. Lovdata XML files are KB to a few MB each; streaming per-member would add complexity without benefit at this scale. Revisit if members ever grow to hundreds of MB.
- Never calls `TarFile.extractall()` or `extract()` — uses read-only `extractfile()` so CVE-2007-4559 class is sidestepped entirely. No filesystem writes occur from member-provided paths.
- Member-name validation rejects null bytes, absolute paths, parent references (POSIX + Windows separators).

### Sprint 3 — Sync pipeline (PR #8 → #16, MERGED 2026-04-23 → 2026-04-26)

Closed Sprint 3. End-to-end working pipeline.

- **PR #8** `feat(parsing): canonicalize_xml + hash_normalized_xml` — C14N normalization with safe XML parser (XXE / billion-laughs / huge_tree).
- **PR #9** `feat(rendering): renderer + frontmatter` — deterministic XML→MD with YAML frontmatter.
- **PR #10** `feat(storage): manifest read/write` — Pydantic frozen models, deterministic JSON.
- **PR #11** `feat(sync): change_detector` — pure function classifying upstream against prior manifest.
- **PR #12** `docs(claude): clarify mutmut is Codex's job` — small docs alignment.
- **PR #13** `test+docs: harden tar Windows path + xml deep-nesting + mutation policy` — mutation policy doc + 2 real-gap fixes.
- **PR #14** `feat(sync): git command wrappers` — subprocess+git CLI primitives, no GitPython.
- **PR #15** `feat: sync orchestrator + CLI` — full pipeline end-to-end. seed and sync commands.
- **PR #16** `feat(workflow): scheduled daily sync to lovverk` — production runner with deploy key.

End-of-sprint state:
- 23 production source files, ~1500 LOC, 100% coverage.
- ~273 tests across unit + integration.
- Mutation kill rate ~75–80% with most survivors equivalent (per §9a).
- Daily cron at 04:00 UTC pushes corpus updates to `lovverk` automatically.
- Idempotency contract enforced by tests: no upstream changes ⇒ no commits.
- Conservative legal posture preserved: only NLOD 2.0 tarballs, never raw XML in git.

### Sprint 4 — Corpus UX polish (PR #17 → #18, MERGED 2026-04-26 → 2026-04-27)

Closed Sprint 4. Filenames are now human-readable, the corpus is browsable via INDEX, per-document commit history is wired, and a slug length cap protects against NAME_MAX overflow on EU implementation forskrifter.

- **PR #17** `feat(corpus): slug-based filenames, INDEX.md, per-document commits` — slug derivation (§12b) + collision resolution + rename detection + three commit policies (§12a) + per-dataset INDEX generation (§12c). Codex round 2 found cross-dataset slug collision scoping bug and tombstone slug/title loss; both fixed.
- **PR #18** `fix(rendering): cap slug at 200 UTF-8 bytes (production hotfix)` — slug length cap after the first scheduled migration sync crashed with `OSError: File name too long` on a 290-character EU forskrift title. Adds `tests/property/` with 5 Hypothesis invariants on slug derivation (§9b). Codex round 2 found a docs/behavior mismatch on the no-hyphen edge case; fixed by narrowing the documented contract instead of changing runtime behavior.

End-of-sprint state:
- 23 production source files, ~2200 LOC, 99% coverage.
- 321 tests across unit + integration + property.
- First scheduled cron ran 2026-04-27 and executed an atomic migration commit (4522 renames + manifest + INDEX in one commit) producing the human-readable corpus on `lovverk/main`.
- Property-testing infrastructure in place; see §9b for next-target modules.

### Sprint 5 — Per-act change history layer (PR #23 → #24, MERGED 2026-04-27)

Closed Sprint 5. Per-act history is now generated for every changed doc on every sync, with JSON as source of truth and Markdown as derived view (see §12d for the design and §12b for the directory rationale). The Sprint 4 slug + INDEX work supplied the discovery layer; Sprint 5 supplies the history layer.

- **PR #23** `feat(history): per-act change history extractor + writers` — pure module `lovspor.history` with `HistoryEvent` / `HistoryRecord` Pydantic models, `extract_history()` (walks `git log --follow --numstat`), `render_history_markdown()` (deterministic MD view), `write_history()` (writes `<dataset>/history/<slug>.{json,md}` deterministically). Slug carries a path-traversal validator. 39 unit tests + 4 Hypothesis property invariants. Three Codex passes; first found bulk-sync delete misclassification + slug path-traversal + docs/code mismatch on output location, second found bulk-sync rename misclassification, third clean.
- **PR #24** `feat(sync): generate per-act history during sync + Sprint 5 migration` — wires PR-A into the orchestrator. ManifestRecord gains `total_changes` + `last_changed`. `_commit_with_history` is mode-aware (per-doc bundles into final commit; single + Sprint 4 migration emit a history follow-up). Sprint 5 standalone migration: triggers once on the first sync after PR-B, when the corpus has prior current docs but no `<dataset>/history/` dirs. Three Codex passes; only finding addressed was a mixed-bulk-commit delete-vs-update ambiguity in the bulk-sync heuristic (numstat-only is fundamentally limited; documented trade-off, all post-Sprint-4 commits are per-doc and unambiguous).

End-of-sprint state:
- 24 production source files, ~2700 LOC, 99 % coverage.
- 388 tests across unit + integration + property.
- First production sync after PR #24 ran 2026-04-27 and executed the Sprint 5 migration commit `0c40d0bf` on `lovverk/main` — one atomic commit producing 1562 lover/history files (781 docs × 2 formats) + 7480 forskrifter/history files (3740 × 2) + manifest with `total_changes` / `last_changed` populated for all current docs.
- MCP-ready: `history/<slug>.json` is queryable structured data; future Sprint 6 MCP server can answer `get_law_history(slug)`, `list_recent_changes()`, and similar tools with a plain JSON read.

### Sprint 6 (in progress) — MCP server

User decision 2026-04-27: this sprint stands up an MCP server exposing the `lovverk` corpus to AI consumers (Claude Code, Claude Desktop) via the Model Context Protocol. Distribution mode is **stdio + good README** (each user runs their own copy locally) rather than VPS-hosted — that's the dominant pattern in the MCP ecosystem today and avoids the public-API maintenance commitment. See conversation 2026-04-27.

Sprint progress:

- **PR-A** (this PR, in flight 2026-04-27) — `src/lovspor/mcp.py` with the four tools below, FastMCP via Anthropic's `mcp` SDK, stdio transport, CLI entry `lovspor mcp --corpus-path PATH`. CorpusReader is read-only and validates path containment to refuse manifest-driven escapes.
- **PR-B** (this PR, in flight 2026-04-27) — root `README.md` section + new `docs/mcp.md` (full adoption guide). Covers prerequisites, two-client quickstart with copy-paste config, every tool documented with sample input + sample response, discovery flow, limitations, troubleshooting walkthrough, and NLOD 2.0 attribution. Also refreshes the README's "Status" block from "Early scaffold" to the current production state.

Tools shipped in PR-A:

- `get_law(slug)` — returns the rendered Markdown body + frontmatter for a doc.
- `get_law_history(slug)` — returns the structured event list from `history/<slug>.json` (Sprint 5 deliverable directly enables this).
- `list_recent_changes(dataset?, since?, limit?)` — sorts manifest by `last_changed` (Sprint 5 metadata field directly enables this).
- `search_laws(query, dataset?)` — substring match on slug + title from manifest; body-text search deferred to a future sprint.

Other Sprint-6 candidates not yet committed (let priorities settle once MCP minimum lands):
- **Section / § addressing** (`get_section(slug, "5-12")`) — needs a Markdown-section parser; high value for AI but bigger scope.
- **JSONL chunked export for RAG** — explicit chunking format for embedding pipelines; complementary to MCP rather than blocking.
- **Status badge + workflow runtime stats** — quick wins, do alongside if scope permits.
- **Lovtidend feed integration** — second data source giving "why a law changed"; deserves its own sprint.

## 12a. git_commit_mode is now implemented (Sprint 4 + Sprint 5 history bundling)


Originally decided 2026-04-26 to keep `Settings.git_commit_mode` as a forward declaration. Implemented 2026-04-26 in Sprint 4 PR #17 (three modes wired); Sprint 5 PR #24 added per-act history bundling on top — see §12d for the chicken-and-egg with `git log --follow` that drives the post-Sprint-5 commit topology.

- **`per-document` (default)**: one commit per add / update / rename / remove with conventional-commit messages (`add(lov): skatteloven`, `update(forskrift): trafikkforskriften`, etc.), then a final commit bundling manifest + INDEX + per-act history (`sync: update manifest, index, and history`). Sprint 5 added the `, and history` suffix; the per-doc commits land first so history extraction can see them.
- **`single`**: one bulk docs+meta commit (`sync: N new, M changed, K renamed, L removed`) followed by a `sync: update history for N documents` follow-up commit. Two commits required because history extraction can only run after the docs commit lands (chicken-and-egg). Sprint 5 added the follow-up; pre-Sprint-5 single mode was a single commit only.
- **Migration override**: when any rename has `prior.slug is None` (Sprint 3 manifest with no slug field), the orchestrator forces a single bulk commit (`migration: rename N documents to slug-based filenames`) regardless of `git_commit_mode`, plus a `sync: update history for N documents` follow-up (same chicken-and-egg as `single` mode). This keeps the Sprint 3 → Sprint 4 transition as one auditable event in history rather than thousands of individual renames. User decision documented in conversation 2026-04-26 (option A — bulk migration commit); Sprint 5 added the history follow-up.

Sprint 5 also introduced a separate **Sprint 5 history migration** branch in `run_sync` that fires once on the first sync after PR #24 ships and emits a standalone `migration: generate history for N documents` commit before any regular sync work. Triggered when the corpus has prior current docs but no `<dataset>/history/` dirs. See §12d.

## 12b. Slug-based filenames (Sprint 4)

Decided 2026-04-26. Markdown filenames in `lovverk/lover/` and `lovverk/forskrifter/` use a human-readable slug derived from the law's `short_title` (Lovdata's official kortform), not the opaque `nl-YYYYMMDD-NNN` doc_id from the source XML.

Slug derivation: `short_title` → strip-bracketed `title` → `doc_id` (last-resort fallback). Lowercase, hyphenated, Norwegian Unicode (`æøå`) preserved. Collisions resolved deterministically by `resolve_collisions` (sort by doc_id, append `-2`, `-3`, …).

The Lovdata stable id stays in the manifest as the dict key and in the rendered file's frontmatter as the `id` field. Cross-reference is preserved.

Why slug not full title:
- Length: full titles are 60–90 chars; slugs are 10–25 chars.
- Norwegian convention: laws are referred to by their kortform (`Skatteloven`, `Opplæringslova`) not the full descriptive title.
- Filesystem and URL ergonomics: shorter is better in directory listings, terminal prompts, and URL bars.

Why preserve Norwegian Unicode (`æøå`) instead of ASCII transliteration:
- Native Norwegian readers expect the real letters; `opplæringslova` is the law's name, not `opplaeringslova`.
- GitHub UI renders Unicode correctly; modern URL handling supports it.
- AI ingestion (RAG) handles Unicode without issue.

Length cap (added 2026-04-27 after first scheduled migration sync crashed):
- Slugs are capped at **200 UTF-8 bytes**. POSIX NAME_MAX is 255 bytes; we reserve 55 bytes of headroom for `.md` (3) + collision suffix `-99` (3) + future filename conventions.
- ~1009 of 4522 documents (22%) have `short_title: null` and fall through to the `title`. ~96 of those titles exceed 200 bytes (longest 428 chars), mostly EU implementation forskrifter with names like `Forskrift om gjennomføring av kommisjonsforordning (EU) …`. Without the cap, slug+`.md` overflows NAME_MAX and `pathlib.write_text` raises `OSError: File name too long`.
- Truncation prefers a hyphen boundary when one exists in the byte-truncated prefix. For the theoretical case of a single token longer than 200 bytes with no internal hyphen (not observed in real Lovdata data — every legal title has spaces that become hyphens during slugify), the raw byte-truncated form is returned. Behavior is well-defined and tested.
- Collisions induced by truncation are resolved by the existing `resolve_collisions` (`-2`, `-3`, …).
- Codex did not catch this in PR #17 review because all unit + integration tests used synthetic titles ≤50 chars. Lesson captured in §9b (property-based testing now in place).

## 12c. INDEX.md per dataset (Sprint 4)

Decided 2026-04-26. Each dataset subdirectory in `lovverk` now has an auto-generated `INDEX.md` listing every `current` (non-tombstoned) document sorted alphabetically by slug:

```
# Lover

_4521 current documents_

- [polititjenestepliktloven](polititjenestepliktloven.md) — Lov om tjenesteplikt i politiet [polititjenestepliktloven]
- [skatteloven](skatteloven.md) — Lov om skatt av formue og inntekt (skatteloven)
- ...
```

The INDEX adds discovery on top of the slug-based filenames: a human or AI can browse one file to see the entire corpus rather than scrolling 4500 entries in the GitHub directory listing. Updated on every non-noop sync (committed alongside the manifest in per-document mode, or bundled in the bulk commit in single/migration mode).

## 12d. History generation in sync (Sprint 5)

Implemented in PR-B 2026-04-27. The orchestrator generates per-act history (`history/<slug>.json` source-of-truth + `history/<slug>.md` derived view, see §12b for the directory rationale) for every doc that changed in the current sync. Manifest records gain `total_changes` and `last_changed` so future MCP-style queries can sort and filter without loading every history.json.

**Commit topology** (chicken-and-egg constraint: history reads `git log --follow`, which can only see commits that already exist):

- `per-document` mode (default): per-doc commits land first, then history is generated, then one final commit bundles manifest + INDEX + history (`sync: update manifest, index, and history`). Single final commit, matching user decision Q1 = same-commit (2026-04-27 chat).
- `single` mode: docs+meta commit first (the "single" semantic, unchanged), then a follow-up commit `sync: update history for N documents`. Two commits required.
- Sprint 4 migration override: rename + meta commit first, then history follow-up. Same two-commit pattern as single mode.
- Sprint 5 standalone migration: when the corpus has prior current docs but no `<dataset>/history/` dir, `run_sync` first emits `migration: generate history for N documents` BEFORE any regular sync work. Triggers once on the first sync after PR-B; subsequent syncs see populated dirs and skip.

**Frontmatter is intentionally untouched.** Sprint 5 metadata lives only in the manifest and in `history/`, never in `<slug>.md`. Adding `last_change_commit:` to per-doc frontmatter would force a one-time re-render of all 4522 docs (false-positive change). User decision Q2 = 2b (2026-04-27 chat).

**Tombstones are skipped.** Docs with `status: removed` are not given a `history/` file in this sprint — `git log --follow` on a deleted file returns empty without `--all`, and reconstructing via `--all` would expand scope. User decision Q3 = A (2026-04-27 chat). Documented gap; revisit when researchers ask for "what happened to this law before it was deleted".

**Performance.** Sprint 5 migration is sequential: one `git log` subprocess per current doc (~50 ms each × 4522 docs ≈ 4 minutes). Within the GitHub Actions 30-minute workflow timeout. User decision Q4 = sequential (2026-04-27 chat). If this becomes a bottleneck, a follow-up PR can batch via `git log --all --name-only` over the whole repo and bucket by file.

**Partial-failure risk on Sprint 5 migration.** If the migration crashes mid-bulk-write, the dir exists but only some docs have history.json. The detector (`_needs_sprint5_history_migration`) only checks dir presence, so the migration will not auto-retry on the next sync. Acceptable for a one-time event; if it ever fires in production and fails midway, manual rerun or a strengthened detector that checks per-doc presence is the recovery path.

## 13. Naming

Norwegian, deliberately:
- `lovspor` = "law trail" (track of changes in law)
- `lovverk` = "body of laws" (existing Norwegian legal term)

Both 7 letters, both start with `lov`, ship visibly as siblings.

## 14. Known open items

End of Sprint 5:

- **Dependabot PRs #2 / #3 / #4 followups** — `actions/checkout` v4 and `setup-uv` v4 are still pinned in `.github/workflows/sync.yml` (PR #2 / #3 only bumped `test.yml`, since `sync.yml` was added later in PR #16). Dependabot's next weekly run will propose new PRs against `sync.yml`; merge those as a batch. No functional risk.
- **Mutation baseline still pending an authoritative full rerun.** Codex's PR #23 / #24 reviews ran fresh mutmut snapshots but stopped them mid-flight to keep the working tree clean (mutmut mutates files in-place). Latest non-final snapshot: 714 / 1084 killed, 315 survived, 55 untested. The §9a revisit trigger of 250 survivors is still exceeded but the count remains non-authoritative until a clean full run completes. Next Codex pass on a Sprint 6 PR should include a cache reset and a fresh authoritative score before any §9a re-evaluation.
- **Bracket-stripping in `short_title`** — `derive_slug` strips bracketed content from `title` (e.g. `(skatteloven)`) but not from `short_title`. Lovdata's short_title for some acts includes parenthesized abbreviations like `Skatteloven (sktl)`, so the slug becomes `skatteloven-sktl` rather than `skatteloven`. Acceptable but slightly verbose. Changing this would force another slug migration on the corpus, so only worth doing if researchers ask.
- **Sprint 5 partial-failure recovery** — `_needs_sprint5_history_migration` only checks for the presence of `<dataset>/history/`, not that every current doc has a populated history file. A migration that crashes mid-bulk-write would not auto-retry on the next sync. Acceptable for a one-time event; recovery is manual rerun or a strengthened detector. See §12d.
- **Sprint-5 mixed-bulk-commit ambiguity** — `_classify_bulk_sync` cannot distinguish a deleted file from an in-place shrunken update inside the same bulk commit using `--numstat` alone. Deletes mixed with updates are classified as updates. Bounded to legacy bulk-mode commits (post-Sprint-4 default is per-doc, never goes through this branch). Full fix needs `--name-status` parsing; deferred unless real lovverk history shows the misclassification mattering.
- **Orchestrator branch coverage at 97%** — Sprint 5 PR-B added several new branches (commit-mode dispatch, history follow-up, Sprint 5 migration trigger) without proportional integration coverage. Codex flagged but did not classify as a bug.

Resolved during Sprint 5:
- ~~Sprint 5 history layer planned but not implemented~~ — shipped in PR #23 + #24 (§12d). Production migration commit `0c40d0bf` populated history for all 4522 current docs.
- ~~Stale mutmut cache from Sprint 4~~ — Codex re-ran fresh snapshots in PR #23 / #24 reviews; current numbers reflect Sprint 5 surface.
- ~~Dependabot mutmut major bump~~ — `chore(dependabot)` in PR #20 added an `ignore` rule for `mutmut` semver-major updates (decisions.md §9 reasoning).

Resolved during Sprint 4:
- ~~Per-document commit mode deferred~~ — implemented in PR #17 (§12a).
- ~~First real production sync has not yet run~~ — ran 2026-04-26 (manual seed, 4522 docs) and 2026-04-27 (scheduled migration commit, atomic 4522 renames + manifest + INDEX).
- ~~`norsk_loven/` stale directory~~ — deleted from local disk 2026-04-27.
- ~~Slug NAME_MAX overflow on monster forskrift titles~~ — fixed in PR #18 (§12b, §9b).

Resolved during Sprint 3:
- ~~Duplicate commit `2def5a6`~~ — squashed away by PR #5 merge.
- ~~Deploy key not configured~~ — operator runbook in `docs/operations.md` §Deploy key setup; user confirmed the key is in place 2026-04-26.
- ~~`sync.yml` not added~~ — added in PR #16.

---

## How to use this document

- **Before starting a session**: read this + `CLAUDE.md`. Those two together are the full context.
- **Making a new non-obvious decision**: add an entry here. Date-stamp it.
- **Reversing a previous decision**: don't delete the old entry — append a new one that supersedes it with rationale. History matters.
- **On Claude crash recovery**: this file + git log on both repos should let any new session resume without re-asking the user.
