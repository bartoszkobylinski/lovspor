# Project Decisions Log — lovspor + lovverk

Single source of truth for why this project looks the way it does. Every non-obvious choice is captured here with rationale, so that a future Claude session (or human contributor) can pick up cold without context loss.

Update this file whenever a new decision lands.

Last updated: 2026-07-12

---

## 1. Project identity

- **`lovspor`** — the **engine**. Python package. This repo.
- **`lovverk`** — the **corpus**. Markdown-only data repo. Separate repo.
- Both public on GitHub under `bartoszkobylinski/`.

Two repos because two audiences: engine repo is for contributors and portfolio, corpus repo is for consumers (AI/RAG, researchers) who want clean data without code noise.

## 2. Purpose

Build a versioned Markdown corpus of current Norwegian law, auto-updated from Lovdata's public-data API, with every change landing as an individual Git commit. Primary use case: AI/RAG ingestion with auditable provenance.

**Out of scope (deliberately):**
- Point-in-time reconstruction from *before* the corpus's git history began (the Sprint 10 time-machine tools — `get_law_at`, `diff_law_versions` — cover dates within the tracked window; pre-corpus reconstruction stays out of scope, see `historiske-lover` in the roadmap)
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
| Hooks | `pre-commit` | Wired for gitleaks, ruff, format, mypy, pytest unit |
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

### Sprint 6 — MCP server stdio (PR #26 → #27, MERGED 2026-04-27)

Closed Sprint 6. Stdio MCP server exposing the `lovverk` corpus to AI consumers (Claude Desktop, Claude Code) is live, plus full adoption documentation. Distribution mode is **stdio + good README** (each user runs their own copy locally) rather than VPS-hosted — dominant MCP-ecosystem pattern, avoids public-API maintenance commitment.

- **PR #26** `feat(mcp): stdio MCP server for the lovverk corpus` — new `src/lovspor/mcp.py` with four read-only tools, FastMCP via Anthropic's `mcp` SDK, stdio transport, `CorpusReader` with path-traversal defense (`_safe_join`), CLI entry `lovspor mcp --corpus-path PATH`. Three Codex passes; first found 1 HIGH (path traversal in `markdown_path`/`slug` joins), 1 MEDIUM (since-date validation didn't normalize alternate ISO forms), 2 LOW (negative limit slicing, stale `docs/mcp.md` reference); all fixed.
- **PR #27** `docs(mcp): README + full docs/mcp.md adoption guide` — new `docs/mcp.md` (317 lines), README MCP section with copy-paste Claude Desktop / Claude Code config, four-tool reference with samples, troubleshooting, NLOD 2.0 attribution. Replaced the outdated "Early scaffold" Status block with the production reality. One Codex pass found a §12 Sprint 6 status drift; fixed.

Tools shipped:

- `get_law(slug)` — returns the rendered Markdown body + frontmatter for a doc.
- `get_law_history(slug)` — returns the structured event list from `history/<slug>.json` (Sprint 5 deliverable directly enables this).
- `list_recent_changes(dataset?, since?, limit?)` — sorts manifest by `last_changed` (Sprint 5 metadata field directly enables this).
- `search_laws(query, dataset?)` — substring match on slug + title from manifest; body-text search deferred to a future sprint.

Other Sprint-6 candidates not committed (still open for later sprints if user demand surfaces):
- **Section / § addressing** (`get_section(slug, "5-12")`) — needs a Markdown-section parser; high value for AI but bigger scope.
- **JSONL chunked export for RAG** — explicit chunking format for embedding pipelines; complementary to MCP rather than blocking.
- **Status badge + workflow runtime stats** — quick wins, do alongside if scope permits.
- **Lovtidend feed integration** — second data source giving "why a law changed"; deserves its own sprint.

### Sprint 7 — Corpus freshness signal for MCP consumers (PR #28 → #29, MERGED 2026-04-27 → 2026-04-28)

Closed Sprint 7. Validated end-to-end with real Claude Code: a stale or schema-incompatible corpus now produces a clear AI-driven "run this command to refresh" diagnosis in one tool call instead of 5+ minutes of guessing.

Trigger: the Sprint 6 manual integration test surfaced a silent-empty-results failure mode — a stale local `lovverk` clone makes every MCP search/get return `[]`, indistinguishable from a missing law. The Sprint 7 fix gives the AI a fifth tool to diagnose this without manifest spelunking.

- **PR #28** `feat(mcp): corpus_status() tool for proactive freshness signal` — new `corpus_status()` returning manifest age + git HEAD info + `is_stale` (date-based, 7-day threshold) + `refresh_command` (shlex-quoted) + `notice` (human-readable). Tool docstring nudges proactive use. Three Codex passes; first found 1 MEDIUM (refresh_command shell-unsafe for paths with spaces) + 1 LOW (negative `manifest_age_days` on clock skew); fixed.
- **PR #29** `fix(mcp): schema-staleness detection in corpus_status (recovery)` — second-half of Sprint 7 that **almost shipped invisible**. After the manual integration test in Claude Code revealed that the date-based `is_stale` signal misses pre-Sprint-4 manifests (records have `slug=None` so every search returns empty even with a "fresh" manifest), `corpus_status` was extended with `schema_compatible` boolean and a dedicated notice variant. The follow-up commits were Codex-reviewed clean **but never merged** — PR #28's squash had captured the pre-extension state, and the post-merge push for the schema commits created a new orphan branch with the same name that nobody re-merged. Discovered when the second manual test still hit the original failure mode; cherry-picked onto a fresh branch as PR #29 plus a notice-priority fix Codex caught (schema-stale must win over clock-skew in the notice slot to avoid the "is_stale=true but notice says 'Treating as fresh'" contradiction).

**Architecture decision: tell, do not do.** Server stays read-only and no-network. `refresh_command` is a suggestion the AI relays; user runs `git pull` manually. Rejected `refresh_corpus()` tool that would shell out to `git pull` (would invalidate read-only contract; merge-conflict / local-commit edge cases on user repos; mismatches the inert-read-only MCP-ecosystem norm).

End-of-sprint validation in Claude Code (2026-04-28): user prompt *"Use the lovverk MCP to find a Norwegian law about boligkjøp"* on a pre-Sprint-4 checkout produces a single `search_laws` → empty → single `corpus_status` → AI quotes the schema-stale notice and gives the exact `git -C <path> pull` command. Same prompt on a fresh checkout produces a single `search_laws` → hit → structured answer with no `corpus_status` overhead. AI uses the diagnostic tool only when the data path looks broken — exactly the design intent.

**Lesson recorded for §14:** PRs that get follow-up commits after Codex's "No findings" need a verification that the follow-up actually went to the same branch ref on origin. After a squash-merge, GitHub deletes the source branch; subsequent pushes silently create a new branch with the same name and re-trigger Codex without ever connecting back to a PR. See PR #29 origin story.

### Sprint 8 — Tool surface deepening (PR #31 → #34, MERGED 2026-04-28 → 2026-04-29)

User decision 2026-04-28, after surveying the polish-law-mcp ecosystem (Ansvar Systems' polish-law-mcp + numikel's law-scrapper-mcp + janisz's sejm-mcp). Those projects offer ~13 tools each vs. our six; concrete gaps worth closing:

1. **Full-text body search** — `search_laws` matches manifest only (slug + title). Polish equivalents do BM25 / FTS5 across full body text; users searching for "kryptovaluta" or "kunstig intelligens" hit nothing in our search even though the term appears in body text.
2. **Section / § addressing** — `get_law` returns the entire act. Polish equivalents support `get_provision(act_id, "Art. 5")` for surgical access. Norwegian `§` numbering is structurally equivalent.
3. **Citation validation** — Ansvar's "zero-hallucination" feature: confirm a cited reference (e.g. "§ 5-12 skatteloven") actually exists before letting the AI use it.
4. **EU cross-references** — Lovdata XML carries EU directive references (`<dd class="ref-eu">`) for laws implementing EU acts (GDPR, NIS2, eIDAS, AI Act). We currently extract the doc's own metadata but not these outbound EU links.

**Sprint 8 PR breakdown** (decided 2026-04-28; ordered low-risk-first):

- **PR-A** `feat(mcp): search_body for full-text body search` (MERGED 2026-04-28 as PR #31) — MCP-only, lazy in-memory index of all current Markdown bodies on first call (~45 MB resident, ~3-5 s cold load), substring case-insensitive scan, returns slug / doc_id / title / dataset / `match_count` / `snippet` (~100-char window around first match). Sorted by match_count descending. Two Codex passes; first found 1 MEDIUM (frontmatter / H1 leaking into body search — body-only contract violated) + 1 LOW (docs drift); both fixed.
- **PR-B** `feat(mcp): get_section for §-level addressing` (MERGED 2026-04-28 as PR #32, with follow-up recovery PR for round 2 Codex fixes — see §14 'PR-merge follow-up branch detection') — MCP-only, parses Markdown body for `### § N-M.` headings, returns `{section_id, heading, parent_chapter, body}`. Reuses the cached frontmatter / H1 stripped body index from PR-A. Five tactical decisions (per chat 2026-04-28): strict section_id format (Q1=A, no `§` prefix), result includes `parent_chapter` for context (Q2), parse-on-each-call without per-slug section cache (Q3=A), `CorpusNotFoundError` lists available sections in natural order on miss (Q4=A), tool name `get_section(slug, section_id)` (Q5). Two Codex passes; first found 1 MEDIUM (untitled `### § N` headings invisible) + 1 MEDIUM (natural-sort `TypeError` on mixed `5-12` / `5-12a` siblings) + 1 LOW (docs drift); fixes shipped in the recovery PR after PR #32's squash-merge orphaned the round 2 commits — same failure mode as PR #29.
- **PR-C** (this PR, in flight 2026-04-29) `feat(mcp): validate_citation for zero-hallucination references` — MCP-only, parses citation strings (e.g. "§ 5-12 skatteloven-sktl"), checks slug exists + section exists. Reuses PR-B parser via delegation to `get_section`. Five tactical decisions (per chat 2026-04-29): permissive parser accepting Norwegian variants like reverse order or filler "i" (Q1=A), strict slug match (no fuzzy fallback, Q2=A), structured result `{valid, slug, section_id, heading, reason}` (Q3), edge cases handled (slug-only valid, §-only ambiguous, unparseable invalid; Q4), tool name `validate_citation(citation: str) -> dict` (Q5).
- **PR-D** (this PR, in flight 2026-04-29) `feat(sync): extract EU cross-references` — **engine + corpus migration**. `LegalDocumentFrontMatter.eu_basis: list[str]` captures CELEX identifiers from Lovdata's `<dd class="eeaReferences">` block; `ManifestRecord.eu_basis: list[str] | None = None` so pre-Sprint-8 manifests still load. Sprint 8 backfill migration in `run_sync` fires once on the first sync after PR-D ships when any current record has `eu_basis is None` AND `slug is not None` — re-renders every current doc and emits one `migration: backfill eu_basis for N documents` commit BEFORE the regular sync flow. Slug-less records (legacy Sprint-3 manifest) are deferred to the Sprint 4 rename migration in the normal sync flow because Sprint 8 can't safely rename and would orphan the legacy file. Two new MCP tools: `get_eu_basis(slug) -> {slug, doc_id, title, dataset, eu_basis}` and `search_eu_implementations(eu_doc_id) -> [{slug, doc_id, title, dataset}, ...]`. Five tactical decisions (per chat 2026-04-29): CELEX uppercase normalization (Q1=A, EU canonical form); flat list of strings, not objects (Q2=A — small list, type letter is recoverable from CELEX position 5); empty list when no EEA references or only the EØS-avtalen annex link (Q3=A); auto-detect bulk migration like Sprint 4/5 (Q4=A); two MCP tools only — no third `validate_eu_implementation` (Q5=A — `get_eu_basis` + `search_eu_implementations` already cover both directions; AI consumers can compose them).

**Architecture decisions for Sprint 8:**
- **In-memory body index for search_body** (Q1=A 2026-04-28). Rejected: SQLite FTS5 index committed to lovverk (would add ~50 MB git blob per sync; complex pipeline change); SQLite at MCP startup (extra deps, marginal speedup). 4500 docs × ~10 KB each = 45 MB RAM is acceptable for a long-lived stdio process.
- **Substring case-insensitive matching** (Q2=substring) for the MVP. No tokenization, no stemming. Word-based / stemmed indexing is a follow-up if real use shows it matters; documented as a limitation in `docs/mcp.md`.
- **Two separate tools** (`search_laws` vs `search_body`, Q4=dwa-toole) rather than one with a `body: bool` flag. AI tool selection is clearer when names encode intent.
- **Limit-only, no offset** (Q5=limit-only). AI consumers rarely paginate; they refine queries instead.

### Sprint 9 — Semantic search + anti-hallucination stack (PR #41 → #50, MERGED 2026-04-30 → 2026-05-06)

User decision 2026-04-29, after Sprint 8 closed the keyword-search gap: substring matching cannot relate semantically equivalent phrasings (*"renter rights"* ↔ *manglende vedlikehold*). Sprint 9 adds embeddings plus the matching anti-hallucination grounding layer that makes the new fuzzy retrieval safe for AI consumers.

**Model pivot (PR #41 benchmark → PR #42 implementation).** Empirical benchmark (`benchmarks/embedding_comparison/results-2026-04-30.md`) over 8 personas × 47 realistic queries compared `text-embedding-3-large`, `text-embedding-3-small`, `nb-sbert-v2-large`, and `nb-sbert-v2-base`. `text-embedding-3-large` won by **+24% Recall@5** over the best Norwegian-tuned alternative. Counter-intuitive — generalist beating domain-tuned — but the benchmark was strict and queries were realistic AI-consumer phrasings. Trade-off accepted: paid API + network dependency vs ~$5-15/year cost on a hobby project where Norwegian law text is publicly available under NLOD 2.0 (no privacy concern), in exchange for the Recall@5 gain.

**Sprint 9 PR breakdown:**

- **PR-A** (PR #42, MERGED 2026-04-30) — embeddings model layer: `OpenAIEmbedder` with `tiktoken`-aware truncation, retry on `TransportError`/429/5xx, and index-aligned response extraction (the API can return embeddings in different order than input; aligning by the `index` field prevents silent section-vector misalignment at storage time).
- **PR-B1 / PR-B2** (PR #44, MERGED 2026-04-30) — orchestrator wire-in via `_write_one`. Sprint 9 backfill migration that re-renders every current doc to populate `.bin` files on first sync after PR-B1, similar in shape to the Sprint 4 slug rename and Sprint 5 history backfills.
- **Path-cascade hotfix train** (PR #43, PR #44 rounds 2-3, PR #45, PR #46) — production CI sync crashed three times during the embedding migration with `pathspec did not match any files`. Class of bugs progressively closed: rename + rename collision → atomic rename phase (#43); changed + rename and cross-loop interleaving (#44 rounds); universal within-sync collision detector (#45); cross-sync manifest-vs-tree drift orphan-path filter so a stale manifest entry no longer crashes `git add` (#46). The class is now structurally closed across both within-sync (any action-type combo) and cross-sync (orphan paths from prior crashes) failure modes.
- **PR-B3** (PR #48, MERGED 2026-05-05) — `semantic_search` and `verify_quote` MCP tools (10 → 12). Eager OpenAIEmbedder construction at server start when `OPENAI_API_KEY` is set so a malformed key fails fast; missing key warns to stderr and disables only `semantic_search`, keeping the other 11 tools alive (graceful degradation — original eager fail-fast plan reverted because crashing the whole server over one optional dependency is user-hostile). Codex round-1 caught a real correctness bug: `_load_embedding_index` accepted `.bin` files of any dim, then `top_k_cosine` raised `shapes not aligned` when a stale-dim file met the current-dim query vector. Fix added per-file dim filtering with operator-visible stderr log; round-2 added a distinct error message for the all-stale-corpus state (post-migration) vs the no-bin-files state (cold bootstrap).
- **PR-B3.5** (PR #50, MERGED 2026-05-06) — `cross_references` field on `get_section` response. Every `§ N-M` reference in the body is parsed once, validated against the manifest, deduplicated by target. Closes the cross-citation hallucination vector PR-B3 left open: AI sees broken internal refs inline rather than needing a follow-up `validate_citation` call. Codex round-1 caught a slug-leak bug in the bounded-window resolution where the next `§`'s slug-before-`§` owner could bleed backward into the current `§`'s window; fix added `_compute_match_owner_starts` to detect known-slug tokens immediately preceding the next match (whitespace-only between token and `§`) and trim the previous match's AFTER-window at the token's start. Restricted to known slugs so non-slug fillers like *samt* / *også* don't over-trim.

**Anti-hallucination layered story** (the design intent across Sprint 9 PR-B3 and PR-B3.5):

1. `semantic_search` returns candidates with `score` and `citation_hint`.
2. `get_section` returns verbatim text plus `cross_references` so the AI sees broken internal refs inline.
3. `verify_quote` confirms verbatim text matches the cited section before the AI quotes anything.
4. `validate_citation` is the off-ramp for ambiguous citations the other three can't resolve.

None of the four prevent paraphrase hallucination — that requires the AI client itself to ground in `get_section` and quote the original Norwegian. The stack covers what the tooling layer can cover.

**Architecture decisions for Sprint 9:**
- **Per-doc binary sharding** (Q1=A 2026-04-29). Rejected: monolithic `embeddings.parquet` or `lovverk/embeddings.sqlite` (would rewrite end-to-end on any sync, producing 200+ MB git blobs and burying real legal-text changes under embedding churn). Per-doc `.bin` files preserve git diff per-section semantics; ~200 MB total at 3072-dim int8 across 4500 docs is acceptable for a long-lived stdio process.
- **int8 quantization with per-batch scale** (Q2=int8). ~99% similarity preserved at 1/4 storage cost vs float32. Per-file scale (not corpus-wide) because section vectors within one act share a tighter magnitude distribution.
- **Brute-force `top_k_cosine`** (Q3=brute-force). Rejected: ANN index (faiss, hnswlib). At corpus scale a brute-force scan is ~50 ms per query — well within an interactive budget — and avoids the determinism risk plus extra ops complexity ANN indexes introduce.
- **Graceful key-missing degradation** (Q4=warn-and-degrade). Rejected: original eager fail-fast at server start. Only 1 of 12 tools needs the key; refusing to start the whole server over one optional dependency is user-hostile.
- **B-tier scope for `cross_references`** (Q5=B). Canonical slugs only. Descriptive name resolution (*"i lov om X"* → looking up `skatteloven-sktl` from a free-text title) deferred as a possible C-tier follow-up; `validate_citation` remains the off-ramp.
- **Bounded-window slug resolution with owner-trim** (Q6=bounded+owner-trim, decided 2026-05-06 after Codex round-1 on PR #50). Each `§` match's slug-resolution window is bounded by surrounding `§` matches AND further trimmed when the next `§` has a slug-before-`§` owner. Without owner-trim, a body like *"Se § 5-13. Etter annen-lov § 9-3."* resolves § 5-13 to `annen-lov` because that slug falls inside § 5-13's AFTER-window even though it clearly attaches to the next § 9-3.

**Sprint 9 eval retrofit closeout (PR #55 → #57, MERGED 2026-05-07 → 2026-05-08).** A three-PR sequence covered the eval-suite side of the Sprint 9 work:

- **Phase 1** (PR #55) added `semantic_search` and `verify_quote` dispatch to the deterministic eval runner, retrofitted 7 of the 8 `reveals_gap: no semantic search` scenarios to call `semantic_search` (one — `kari_009` — kept a different gap, retargeted to "no local-regulation or cadastral coverage"), and routed the synthetic corpus through the real `OpenAIEmbedder` so per-doc `.bin` files are written byte-identical to production.
- **Phase 2 lite** (PR #56) added an opt-in `--llm-driven` runner that spawns `claude -p` per scenario, reads the `stream-json` event stream, and translates `tool_use` / `tool_result` events back into `ToolCallResult` so existing success criteria apply unchanged. Codex round-1 caught two correctness bugs (nonzero claude exit silently passing once a tool call was parsed, and unexpected MCP `is_error: true` not failing the scenario like the deterministic runner does) — both closed before merge.
- **Phase 3** (PR #57) added the **Frida** persona — a senior legal-affairs journalist whose editor refuses AI citations — with 10 anti-hallucination scenarios that exercise `verify_quote`, `cross_references`, and `validate_citation` end-to-end. `EXPECTED_PERSONAS` bumped 8 → 9. Synthetic skatteloven § 6-1 body extended to reference one valid (§ 5-1) and one deliberately broken (§ 999-99) cross-reference so `frida_009` has a real fixture target.

**First real LLM-driven Opus baseline (2026-05-08, ~$8 of subscription quota).** The first manual `--llm-driven --llm-model opus` run on the Frida suite produced **5 pass / 3 partial / 2 fail / 0 gap-revealed** (full report at `evals/results/2026-05-08-frida-opus-baseline.md`). Two findings worth keeping:

1. **The anti-hallucination stack caught a real Opus hallucination.** On `frida_010` Opus tried to read husleieloven `§ 9-6`, which does not exist in the synthetic act — and would have silently pasted that section id into the final answer. `get_section` raised `section '9-6' not found in 'husleieloven'; available: § 9-5, § 9-7, § 9-8`, the runner reported `unexpected get_section error` and the scenario failed the way it should. Without Sprint 9's tooling layer the user would have received a confidently fabricated citation. This is the production-fidelity signal the LLM-driven path was built for.
2. **Several scenarios over-specify the tool chain.** `frida_001` and `frida_007` went partial because Opus skipped `get_section` when it already knew the slug + section_id and went straight to `verify_quote`; the scenarios required `tool_called: get_section` mechanically. `frida_004` failed because Opus refused to call `validate_citation` on a slug-less `"§ 5-12"` and instead pushed back to the user for clarification — desirable production behaviour, undesirable test design.

The scenario-specs-too-rigid issue is **deferred to a separate "eval refinement" sprint** rather than bundled into Sprint 9. Sprint 9 is closed; eval refinement defines what "tool-chain tolerance" means before changing the success criteria so the next refinement does not just chase the current Opus snapshot.


Originally decided 2026-04-26 to keep `Settings.git_commit_mode` as a forward declaration. Implemented 2026-04-26 in Sprint 4 PR #17 (three modes wired); Sprint 5 PR #24 added per-act history bundling on top — see §12d for the chicken-and-egg with `git log --follow` that drives the post-Sprint-5 commit topology.

- **`per-document` (default)**: one commit per add / update / rename / remove with conventional-commit messages (`add(lov): skatteloven`, `update(forskrift): trafikkforskriften`, etc.), then a final commit bundling manifest + INDEX + per-act history (`sync: update manifest, index, and history`). Sprint 5 added the `, and history` suffix; the per-doc commits land first so history extraction can see them.
- **`single`**: one bulk docs+meta commit (`sync: N new, M changed, K renamed, L removed`) followed by a `sync: update history for N documents` follow-up commit. Two commits required because history extraction can only run after the docs commit lands (chicken-and-egg). Sprint 5 added the follow-up; pre-Sprint-5 single mode was a single commit only.
- **Migration override**: when any rename has `prior.slug is None` (Sprint 3 manifest with no slug field), the orchestrator forces a single bulk commit (`migration: rename N documents to slug-based filenames`) regardless of `git_commit_mode`, plus a `sync: update history for N documents` follow-up (same chicken-and-egg as `single` mode). This keeps the Sprint 3 → Sprint 4 transition as one auditable event in history rather than thousands of individual renames. User decision documented in conversation 2026-04-26 (option A — bulk migration commit); Sprint 5 added the history follow-up.

Sprint 5 also introduced a separate **Sprint 5 history migration** branch in `run_sync` that fires once on the first sync after PR #24 ships and emits a standalone `migration: generate history for N documents` commit before any regular sync work. Triggered when the corpus has prior current docs but no `<dataset>/history/` dirs. See §12d.

### Sprint 10 — Time-machine tools + PyPI distribution (2026-06 → 2026-07)

Three threads: git-history time-travel tools, consumer distribution, and maintenance tooling.

**Time-machine MCP tools (git-history "as of date").** `timetravel.py` (`git log --follow` + `git show <sha>:<path>`) backs three read-only tools no other corpus-MCP can match, since none version their corpus through git:
- `get_law_at(slug, date)` — an act's full Markdown as of a past date (B1).
- `list_law_versions(slug)` — dates of distinct content versions, oldest-first (B1).
- `diff_law_versions(slug, date_a, date_b)` — section-by-section diff between two dates (B2).
Tool count rose to **16**.

**Maintenance: `repair-embeddings` (PR #109, MERGED).** Flags docs whose stored vector count under-counts their current sections (flat acts rendering `§` at H2 produced zero vectors before a parser fix) by clearing `embedding_hash`; the next keyed `sync` re-embeds them via the Sprint 9 backfill. A one-time backfill of ~2,336 docs ran 2026-07-07.

**Consumer distribution — PyPI (PRs #110–#114).** lovspor became a PyPI-published tool:
- `lovspor fetch-corpus` (PR #112) — one command shallow-clones the corpus to `~/.cache/lovverk`; `lovspor mcp` auto-discovers that cache, so `--corpus-path` is now optional.
- PyPI-publishable packaging + OIDC Trusted-Publishing release workflow (PR #111); refreshed README consumer quickstart (PR #110).
- Released 0.2.0, then **0.2.1** (PR #114) after 0.2.0 shipped reporting itself as 0.1.0 — `__version__` now derives from `importlib.metadata` (single source of truth), guarded by a drift test.

**MCP tool-surface batch (PRs #62–#67, MERGED 2026-06-10 → 2026-06-11).** Logged retroactively during the 2026-07-12 currency pass; it shipped inside the Sprint 10 window but was never written down. Vectorized semantic search + O(1) point lookups (#62); grounded `semantic_search` results with a `min_score` floor (#63); input tolerance + error recovery for AI consumers (#64); the `list_sections` TOC tool (#65, tool count 14 → 15); >8k-token sections chunked instead of silently truncated (#66); production-dead delete/push helpers removed (#67).

### Sprint 11 — Code-review remediation wave (PRs #71 → #133, MERGED 2026-07-03 → 2026-07-12)

A four-part code review (pipeline, rendering, ops, embeddings/MCP) was run over the whole engine on 2026-07-03; its findings were tracked in an internal fix backlog and closed over ~60 PRs in ten days. This was a **hardening sprint, not a feature sprint** — no new MCP tools, tool count still 16. This entry records the decisions, not every fix; the PR list is the changelog.

**Renderer version stamp + self-healing re-renders (PRs #121, #122, #123, #125).** `RENDERER_VERSION` (currently 3) is stamped into every manifest record. A document whose stored `renderer_version` is older than the current one is re-rendered on sync **even when its XML hash is unchanged**, so a renderer bug fix heals the corpus without anyone hand-writing a migration. Three constraints fell out of §4's conservative-churn posture:
- The backfill is **self-limiting** (PR #121) — a version bump does not rewrite ~5,900 documents in one commit.
- Re-render migrations are **exempt from legal history** (PR #122). A re-render is an engine event, not a change in the law; letting it emit history events would pollute `history/<slug>.json` with non-legal churn.
- The manifest reader **tolerates unknown fields** (PR #125), so a corpus written by a newer engine never breaks an older MCP reader. The `renderer_version` rollout itself broke every older reader before this landed — the lesson is that additive manifest keys are only safe if readers are permissive first.

**Renderer correctness (PRs #72, #79, #82, #103, #104, #107, #108, #120, #126).** The block walk now fails loudly when it drops text (#72) rather than silently emitting a short document. Words no longer fuse across inline element boundaries (#79); Markdown-significant characters in law text are escaped (#82); tables render as GFM instead of flattening to mush (#103); footnotes and mixed articles render (#104); `§` headings at H2 in flat, chapterless laws are parsed and embedded (#107, #108); not-in-force markup (`futuretitle`, `futureLegalArticle`) is elided rather than published as if it were in force (#120); heading-div and bare-`<p>` blocks render, healing 34 previously-skipped forskrifter (#126).

**Pipeline durability (PR #128).** Manifest, document and history writes are atomic, so a crashed sync can no longer leave a half-written corpus. A per-member size cap on tarball extraction closes the decompression-bomb gap that the CVE-2007-4559 path-traversal guard did not cover.

**Upstream drift — tolerate and notify, not fail-loud (PRs #129, #130).** Decided 2026-07-11, user is the solo dev and there is no on-call.
- The `LovdataArchive` model used `extra="forbid"`, so **any additive field Lovdata shipped would take the nightly sync fully offline until a code deploy**. Switched to `extra="allow"`: unknown fields are kept in `model_extra`, surfaced through the parse path, and the nightly sync auto-opens (or updates) a GitHub issue — "Lovdata schema drift: unknown field X" — using the built-in `GITHUB_TOKEN`. Idempotent, so it cannot spam nightly. The pre-existing test that locked in reject-behaviour was **inverted deliberately**: this is a design reversal, not a bug fix.
- Documents Lovdata serves as an **error notice** rather than legal text are withheld from the corpus (PR #130) and tombstoned with a `removed_reason`, instead of being published as placeholder prose. Detection keys on the structural signal (a `class="errorMessage"` block in `<main>`), never on the notice's wording. Filtering happens at the top of the sync, before change detection, so every downstream mechanism behaves correctly without special-casing. Self-healing: if Lovdata later publishes the real text, the document re-enters the normal flow as `new`.

**Supply chain and CI (PRs #80, #91, #97, #99, #101, #105, #119, #132, #133).** Actions pinned to commit SHAs (#80); transitive deps bumped to clear 7 CVEs (#91); gitleaks wired into pre-commit (#97); CI matrix across Python 3.12 / 3.13 / 3.14 (#99); a keepalive workflow so GitHub stops auto-disabling the sync cron on repo inactivity (#101). The sync job holds two live secrets (`LOVVERK_DEPLOY_KEY`, `OPENAI_API_KEY`), so it now pins github.com's host key instead of harvesting it with `ssh-keyscan` — a scan trusts whatever answers — warns if GitHub rotates that key out of its published set (#132, #133), and installs **runtime deps only**: `--no-dev` on *both* `uv sync` and `uv run`, because `uv run` re-syncs and would otherwise put the dev group straight back (44 packages instead of 76). mutmut, ruff, pytest and pre-commit have no business sitting next to live secrets.

**Packaging (PRs #81, #124, #131, #132).** `pyyaml` declared as a runtime dependency (#81); `evals` no longer ships as a top-level wheel package (#131) — it collided with OpenAI's `evals` on PyPI and is repo-only tooling anyway, run via `python -m evals.runner`; the embeddings benchmark extra declared so the documented benchmark is reproducible (#132); released **0.3.0** (#124).

### Sprint 12 — Hosted MCP foundation: Streamable HTTP transport (2026-07-16)

First item of the [commercial pivot](roadmap.md): expose the existing sixteen read-only tools over the MCP Streamable HTTP transport. No tool logic changed. stdio stays the development path.

**The SDK dispatch finding that shaped everything.** mcp 1.27.0 calls a **synchronous** tool handler *inline on its single event-loop thread* (`func_metadata.call_fn_with_arg_validation` → `fn(**args)`; the only `anyio.to_thread` in the fastmcp package is in the resource path, never tools). The lowlevel server does start each request as its own task, but a blocking sync body never yields, so concurrent tool calls **serialize**. Consequences, both counter-intuitive:
- On stdio this is harmless and the reader needed no locking, which is why `CorpusReader` was correct as written despite documenting itself as single-session.
- On HTTP it means one slow call (cold `search_body`, a `semantic_search` embedding round-trip, a `git` subprocess) stalls *every* client. Verified before writing any lock — a lock alone would have guarded a race that could not yet occur.

**Offload tool bodies to worker threads.** In hosted mode each tool is registered as an async wrapper that runs the sync body via `asyncio.to_thread`. `functools.wraps` preserves `__wrapped__`, and FastMCP derives the argument schema through `inspect.signature` (which follows it) while deciding await-vs-inline from the callable itself — so the wrapper keeps the exact tool schema while becoming awaitable. Confirmed empirically against the SDK before adopting it; a bare `**kwargs` wrapper would have silently produced empty schemas for all sixteen tools.

**Thread-safety became necessary only because of the offload.** Once bodies run on threads (and once item 2 refreshes the corpus on a background thread), two callers can tear `_refresh_if_stale`'s cache reset — it stamps the new mtime *before* nulling the six caches, so a second thread sees the fresh mtime, skips the refresh, and reads a half-reset set — or each pay to build the same ~45 MB / ~200 MB index. Added a **reentrant** lock (the loaders re-enter it via `self.manifest`) guarding invalidation and every lazy build with double-checked locking. stdio behaviour is unchanged: one uncontended acquire per call.

**Locking the four index caches was not enough — the per-doc caches needed an epoch guard (Codex, PR #139).** `_doc_bodies` and `_section_ids_cache` were left lock-free on the argument that their races were benign: single dict ops are atomic under the GIL and nothing iterates them. That reasoning was wrong, because the hazard is not corruption but **staleness across a refresh**. A reader that starts `_read_stripped_body` before a refresh, and finishes after it, writes pre-refresh text into the *post*-refresh cache; every later caller is then served the superseded legal text until the next refresh. Reproduced deterministically (pause the read, refresh, resume → `_doc_bodies["skatteloven"] == "OLD"`), and pinned by a regression test.

The fix is a monotonic `_epoch`, bumped inside the same locked block that drops the caches. Anything computed off-lock snapshots the epoch first and writes back only if it has not moved; a mismatch discards the value and costs one re-read. The alternative — holding the lock across the read — was rejected: an act can be ~1 MB, so stripping it under the lock would stall every other tool call and undo the offload the transport exists for. The lesson generalises: for a cache behind an invalidation boundary, "is this write atomic?" is the wrong question; the question is "does this value still describe the corpus the cache now represents?"

**Warm the indices at startup — the lock created a new stall.** Holding the cache lock for the whole cold build means a trivial call racing a first `search_body` waits for the entire build. Measured against the production corpus: `corpus_status` took **1.60 s** (vs ~0.27 s warm) while a cold `search_body` held the lock. Hosted mode therefore builds the indices *before* accepting traffic; the same call then took **0.27 s** on a freshly booted server. Trade-off accepted: slower start, higher memory floor. Embeddings warm only when an embedder is configured (`semantic_search` is disabled without one, so ~200 MB would be dead weight). **stdio stays lazy** — a metadata-only client should not pay a 3-5 s startup for an index it may never touch.

**`HttpConfig` selects hosted mode.** Passing it to `build_server` implies offload + warm and carries the bind address (FastMCP configures its transport-security allowlist from the constructor host, so it cannot be set after the fact). Bundling them also keeps `build_server` within the four-parameter limit (§ code rules).

**Security posture — deliberately incomplete.** There is **no authentication, authorization, TLS, rate limiting, quota, or metering**. `mcp-http` binds `127.0.0.1` by default and must stay behind an authenticating, TLS-terminating reverse proxy until access control lands (Sprint 12 item 3). `/healthz` and `/readyz` are unauthenticated by design (FastMCP `custom_route` bypasses auth) and deliberately cheap — readiness stats `manifest.json` rather than parsing it or shelling out to git, so a probe loop cannot stall the loop. Richer freshness stays behind `corpus_status`.

**Packaging.** `starlette` and `uvicorn` were already unconditional `mcp` requirements; declared explicitly since the HTTP entry point imports starlette and runs uvicorn directly — same "declare what you import" reasoning as `pyyaml` in PR #81.

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

Open items (current):

- **Embeddings-backfill partial-failure recovery** — `_run_sprint9_embeddings_migration` writes every `.bin` sidecar in a loop and commits once at the end. A crash mid-loop leaves written-but-uncommitted sidecars in the corpus worktree, and the sync aborts on a dirty corpus tree by design (PR #73), so recovery is manual cleanup. Same shape as the Sprint 5 partial-failure item below. Note the *detection* side is sound: `_embedding_is_stale` compares the recorded `embedding_hash` against the doc's `xml_hash` rather than merely testing that the `.bin` exists (PR #75), so once the tree is clean the re-embed is correctly re-queued.
- **The `eu_basis` migration would absorb a same-day upstream content change** — `_run_sprint8_eu_basis_migration` re-renders each current doc from the *upstream* document unconditionally (that is the point: the frontmatter gains a field even when `xml_hash` is unchanged). If a doc's upstream XML *also* changed on the day the migration fires, that real legal change is written inside the bulk `migration: backfill eu_basis for N documents` commit instead of surfacing as its own `update(...)` commit — and re-render migrations are exempt from legal history (PR #122), so the change could go unrecorded in `history/<slug>.json`. **Latent, not live**: the migration is gated on `eu_basis is None` and has already fired in production. It matters only as a design constraint on the *next* migration of this shape — a hash compare against the prior record would let a same-day content change fall through to the normal flow.
- **Mutation baseline still pending an authoritative full rerun.** Codex's PR #23–#29 reviews ran fresh mutmut snapshots but stopped them mid-flight to keep the working tree clean (mutmut mutates files in-place). Latest non-final snapshot from PR #29: 851 / 1299 killed, 357 survived, 91 untested. The §9a revisit trigger of 250 survivors is still exceeded but the count remains non-authoritative until a clean full run completes. Next Codex pass on a Sprint 8+ PR should include a cache reset and a fresh authoritative score before any §9a re-evaluation.
- **Bracket-stripping in `short_title`** — `derive_slug` strips bracketed content from `title` (e.g. `(skatteloven)`) but not from `short_title`. Lovdata's short_title for some acts includes parenthesized abbreviations like `Skatteloven (sktl)`, so the slug becomes `skatteloven-sktl` rather than `skatteloven`. Acceptable but slightly verbose. Changing this would force another slug migration on the corpus, so only worth doing if researchers ask.
- **Sprint 5 partial-failure recovery** — `_needs_sprint5_history_migration` only checks for the presence of `<dataset>/history/`, not that every current doc has a populated history file. A migration that crashes mid-bulk-write would not auto-retry on the next sync. Acceptable for a one-time event; recovery is manual rerun or a strengthened detector. See §12d.
- **Sprint-5 mixed-bulk-commit ambiguity** — `_classify_bulk_sync` cannot distinguish a deleted file from an in-place shrunken update inside the same bulk commit using `--numstat` alone. Deletes mixed with updates are classified as updates. Bounded to legacy bulk-mode commits (post-Sprint-4 default is per-doc, never goes through this branch). Full fix needs `--name-status` parsing; deferred unless real lovverk history shows the misclassification mattering.
- **Orchestrator branch coverage at 97%** — Sprint 5 PR-B added several new branches (commit-mode dispatch, history follow-up, Sprint 5 migration trigger) without proportional integration coverage. Codex flagged but did not classify as a bug.
- **Stale `uvx` cache after lovspor pushes (operational gotcha)** — **Superseded 2026-07-14:** the commercial pivot withdrew the PyPI releases and took the engine private, so the *prefer `uvx lovspor` / `pip install lovspor`* advice no longer holds — there is no PyPI package to prefer. The current path is a local checkout run via `uv run --project /path/to/lovspor lovspor …` (see [`roadmap.md`](roadmap.md)), so the git-source cache gotcha is again the relevant failure mode. *Original note (historical): Largely obsolete since the 0.2.0 PyPI release: prefer `uvx lovspor` / `pip install lovspor`, which are versioned and immutable. The gotcha below applies only to legacy `--from git+...` installs.* Adopters who configured the MCP server via `uvx --from "git+https://github.com/.../lovspor.git" lovspor mcp ...` may continue to see an older lovspor build after the upstream main moves, because `uvx`'s git-source cache does not refresh aggressively. Diagnosis: call `corpus_status()` and check whether the `schema_compatible` field is present. If absent → cached pre-PR-#29 build is in play. Fix: `uvx --refresh --from "git+..." ...` once (or `uv cache clean lovspor`) and restart the MCP client. Worth a one-line note in `docs/mcp.md` Troubleshooting if a real adopter reports it.
- **PR-merge follow-up branch detection** — discovered during PR #29: when a PR has Codex-reviewed follow-up commits after the initial "No findings", a squash-merge of the PR deletes the source branch on origin; any subsequent push to the same branch name silently creates a new orphan branch and re-triggers Codex without ever connecting back to a PR. We almost shipped Sprint 7 with the schema-detection invisible because of this. Mitigation: after a squash-merge, **always** verify a follow-up branch's existence via `git ls-remote origin <branch>` before assuming a re-Codex-pass means the work is on main. Worth automating into the PR-merge skill flow.
- **Change-detection is blind to inter-element whitespace the renderer treats as significant** — `hash_normalized_xml` parses with `remove_blank_text=True`, so an upstream change that alters ONLY whitespace-only nodes between elements (`<strong>a</strong><em>b</em>` → `<strong>a</strong> <em>b</em>`) yields the same hash and triggers no re-render. The renderer parses with `remove_blank_text=False` (PR #79) and DOES treat that inline space as significant, so the rendered corpus can drift from upstream for such a change. **Deliberately not fixed** (decided 2026-07-05): making the hash whitespace-sensitive flips it on every block-level indentation reflow Lovdata emits (`<root><a>` vs `<root>\n  <a>`), forcing a corpus-wide re-render — a direct violation of the conservative-churn posture (§4). A precise fix would have to replicate the renderer's inline-vs-block whitespace model inside the hash path (fragile, could still churn). The gap is bounded and rare — real legal amendments change text or structure, never inline spacing alone — and it heals forward on the doc's next content change. Verified empirically: strip=True collapses both the inline-space diff and the indentation reflow; strip=False separates them but re-hashes on indentation. Revisit only if a real upstream diff shows a whitespace-only rendering change being dropped.

Resolved during Sprint 11:
- ~~Dependabot PRs #2 / #3 / #4 followups~~ — every workflow action is now pinned to a commit SHA (PR #80); `sync.yml` carries `actions/checkout` v7.0.0 and `astral-sh/setup-uv` v8.3.2 (PRs #105, #119).
- ~~Not-in-force markup published as if it were in force~~ — `futuretitle` / `futureLegalArticle` blocks are elided rather than skipped or published (PR #120).
- ~~An additive Lovdata API field takes the nightly sync offline~~ — `extra="allow"` + auto-filed GitHub issue on drift (PR #129). See the Sprint 11 entry.

Resolved during Sprint 7:
- ~~Stale-corpus failure surfaces as silent empty results~~ — `corpus_status()` now flags both age-staleness and schema-staleness with a copy-pasteable refresh command. Validated end-to-end in Claude Code 2026-04-28.

Resolved during Sprint 6:
- ~~MCP server planned but not implemented~~ — shipped in PR #26 + #27. Validated in Claude Code with all four (later five) tools.
- ~~README "Early scaffold. Not functional yet."~~ — Status block rewritten to production reality in PR #27.

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
