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
git commit per changed document
```

### Key invariants

- **Hash on normalized XML, never on rendered Markdown or HTML.** Rendering is deterministic, but changes in the renderer would otherwise trigger false-positive commits.
- **Renderer must be byte-identical deterministic.** Tested. Same XML in → same MD out, every time.
- **Sync is idempotent.** Running twice on same upstream state = 0 file changes = 0 commits.
- **One commit per changed document** in `lovverk`. `git log <file>.md` shows amendment history.

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

### Sprint 2 (next)

Two PRs planned:
- **Part A:** `LovdataClient.download(filename, dest)` — streaming + `.part` atomic rename + sha256 verification.
- **Part B:** `src/lovspor/extraction/tarball.py` — `safe_extract_tarball()` using `tarfile.data_filter` (CVE-2007-4559). Plus a malicious fixture testing path-traversal rejection.

## 13. Naming

Norwegian, deliberately:
- `lovspor` = "law trail" (track of changes in law)
- `lovverk` = "body of laws" (existing Norwegian legal term)

Both 7 letters, both start with `lov`, ship visibly as siblings.

## 14. Known open items

- Duplicate commit `2def5a6` on `feat/lovdata-source-client` (cosmetic; will squash-merge away).
- Dependabot PRs #2/3/4 open.
- `norsk_loven/` stale directory on disk — safe to delete.
- Deploy key from `lovspor` to `lovverk` not yet configured (needed before first real sync push).
- `sync.yml` GitHub Actions workflow not yet added (comes in Sprint 3, after sync CLI is functional).

---

## How to use this document

- **Before starting a session**: read this + `CLAUDE.md`. Those two together are the full context.
- **Making a new non-obvious decision**: add an entry here. Date-stamp it.
- **Reversing a previous decision**: don't delete the old entry — append a new one that supersedes it with rationale. History matters.
- **On Claude crash recovery**: this file + git log on both repos should let any new session resume without re-asking the user.
