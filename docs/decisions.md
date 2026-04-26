# Project Decisions Log — lovspor + lovverk

Single source of truth for why this project looks the way it does. Every non-obvious choice is captured here with rationale, so that a future Claude session (or human contributor) can pick up cold without context loss.

Update this file whenever a new decision lands.

Last updated: 2026-04-22

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
git commit (single commit per sync run; see §12a for per-doc deferred)
```

### Key invariants

- **Hash on normalized XML, never on rendered Markdown or HTML.** Rendering is deterministic, but changes in the renderer would otherwise trigger false-positive commits.
- **Renderer must be byte-identical deterministic.** Tested. Same XML in → same MD out, every time.
- **Sync is idempotent.** Running twice on same upstream state = 0 file changes = 0 commits.
- **Commit granularity:** as of Sprint 3 close, one commit per *sync run* (single mode), not per document. The `git_commit_mode='per-document'` configuration value is validated but not yet implemented; see §12a. When that lands, `git log <file>.md` will show amendment history per act; for now history is per-sync.

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
    <id>.md
  forskrifter/
    <id>.md
  manifest.json
  README.md, LICENSE
```

The engine pushes to the corpus via deploy key (to be configured in Sprint 3 when sync is functional).

Local layout:
- `~/Programming/Python/lovspor/` — engine repo + `.venv/` in project
- `~/Programming/Python/lovverk/` — corpus repo (no venv, pure data)

`~/Programming/Python/norsk_loven/` is an empty leftover directory. Can be deleted.

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

### Sprint 4 (potential, not committed)

Candidates if we keep going:
- Per-document commit mode for `git_commit_mode` (currently single only; field declared but unread — keep as forward declaration).
- Optional Storting open-data enrichment for parliamentary metadata.
- `lovspor render`, `lovspor validate`, `lovspor stats` CLI commands documented in PR #1's `docs/operations.md` planning.
- Status badge + workflow runtime stats reporting.

## 12a. git_commit_mode is now implemented (Sprint 4)

Originally decided 2026-04-26 to keep `Settings.git_commit_mode` as a forward declaration. Implemented 2026-04-26 in Sprint 4 PR #17. Three policies now wired:

- **`per-document` (default)**: one commit per add/update/rename/remove, with conventional-commit messages (`add(lov): skatteloven`, `update(forskrift): trafikkforskriften`, etc.), then a final `sync: update manifest and index` commit.
- **`single`**: one bulk commit per sync (`sync: N new, M changed, K renamed, L removed`).
- **Migration override**: when any rename has `prior.slug is None` (Sprint 3 manifest with no slug field), the orchestrator forces a single bulk commit (`migration: rename N documents to slug-based filenames`) regardless of `git_commit_mode`. This keeps the Sprint 3 → Sprint 4 transition as one auditable event in history rather than thousands of individual renames. User decision documented in conversation 2026-04-26 (option A — bulk migration commit).

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

## 13. Naming

Norwegian, deliberately:
- `lovspor` = "law trail" (track of changes in law)
- `lovverk` = "body of laws" (existing Norwegian legal term)

Both 7 letters, both start with `lov`, ship visibly as siblings.

## 14. Known open items

End of Sprint 3:

- **Dependabot PRs #2 / #3 / #4** still open. Need a review pass after Sprint 3 to merge or close. Updates are toolchain-only (actions/checkout v6, setup-uv v7, dev deps group).
- **`norsk_loven/` stale directory** on local disk — safe to delete; not in git.
- **Per-document commit mode** in orchestrator deferred (see §12a). `git_commit_mode='per-document'` validates but is treated as `'single'` in `_commit_staged`.
- **First real production sync** has not yet run. The workflow is scheduled but the very first scheduled run will be the integration smoke test. Watch the Actions tab the morning after merge.

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
