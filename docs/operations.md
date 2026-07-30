# Operations

## Local runs

```bash
./scripts/bootstrap.sh         # one-time setup (uv sync + pre-commit install + checks)
uv run lovspor --help          # CLI usage
uv run lovspor --version       # show version
uv run lovspor info            # project info
uv run lovspor seed            # initial corpus population (first sync)
uv run lovspor sync            # incremental update against latest tarballs
uv run lovspor sync --force-rerender  # re-render every doc, to land a renderer fix (see Maintenance)
uv run lovspor repair-embeddings  # flag under-embedded docs for re-embed (see Maintenance)
uv run lovspor fetch-corpus    # clone/update the local lovverk corpus that `lovspor mcp` reads
uv run lovspor mcp             # serve the corpus to AI assistants over MCP (stdio)
uv run lovspor mcp-http        # serve the same tools over MCP Streamable HTTP (binds localhost — bearer auth + quotas; TLS terminated upstream, see deploy/digitalocean/)
```

`mcp-http` is the hosted-service foundation (Sprint 12 item 1). It now enforces **bearer-token authentication (revocable per-credential tokens) and per-credential rate limiting + quotas**. Optionally it also accepts **self-service OAuth logins** via WorkOS AuthKit — set `LOVSPOR_AUTHKIT_DOMAIN` *and* `LOVSPOR_PUBLIC_URL` together (one without the other refuses to start; see [`mcp.md` § Authentication](mcp.md#authentication-two-modes)). The **app itself has no TLS** — the transport is plaintext, so a bearer token on an exposed port is sent in the clear. TLS terminates in a reverse proxy: Caddy with automatic Let's Encrypt, per the `deploy/digitalocean/` recipe, which **has been deployed since 2026-07-18** — the hosted instance is live at `https://lovspor.bartoszkobylinski.com/mcp` (opaque-token mode; OAuth is available in the code but not enabled there). If you run it yourself, keep it bound to localhost behind such a proxy and do not expose the app port to the internet. It serves `/mcp` plus unauthenticated `/healthz` and `/readyz` probes. See [`mcp.md` § Streamable HTTP transport](mcp.md#streamable-http-transport).

`seed` and `sync` are aliases at the engine level — both call the same orchestrator. Use `seed` semantically for the first run on an empty corpus, `sync` for repeated invocations. Settings are read from environment variables (or a `.env` file at the engine repo root). See `.env.example` for the required variables.

### Maintenance: `repair-embeddings`

A one-time repair for a corpus whose embeddings were written before a section-parser fix. Flat (chapterless) acts render their sections at H2 (`## § N.`); acts synced before that shape was recognized produced **zero** embedding vectors and are invisible to `semantic_search`, yet carry `embedding_hash == xml_hash` — so the normal Sprint 9 staleness check never re-embeds them.

`repair-embeddings` checks, per section id, whether the stored `.bin` holds a vector for every section the current parser finds; it clears `embedding_hash` on any doc missing one and commits the manifest. A section long enough to be split into several chunks stores more vectors than sections and is **not** flagged — the comparison is per-id, not a raw count, so a fully-embedded doc is left alone. It does **not** call the OpenAI API itself:

```bash
uv run lovspor repair-embeddings          # flags docs, commits the manifest (no API cost)
OPENAI_API_KEY=sk-... uv run lovspor sync  # Sprint 9 backfill re-embeds exactly the flagged docs
```

It is idempotent — a no-op with no commit once every embedding matches its sections. When last run (2026-07-07) the affected set was ~2,333 acts (~$0.57 one-time at `text-embedding-3-large` pricing); that backfill has since completed. The churn is `.bin` rewrites only, markdown is untouched.

### Renderer versioning and self-healing re-renders

Change detection is driven entirely by the upstream XML hash. A **renderer** fix changes no XML, so every document stays `unchanged` and the corpus keeps serving whatever the renderer produced when it last wrote each file.

Every rendered record carries a `renderer_version` stamp (`ManifestRecord.renderer_version`, set from `RENDERER_VERSION` in `rendering/markdown_renderer.py`). When you change what the renderer emits, **bump `RENDERER_VERSION` in the same commit** — `tests/unit/test_rendering_golden.py` pins the output bytes to the version and fails if you forget. The scheduled sync then compares each current document's stamp against the code's version and re-renders any that are stale, so a renderer fix reaches the frozen backlog **on the next ordinary nightly run — no manual step**.

The re-rendered documents are committed as one `migration: re-render N documents (renderer vK)` commit, whose subject the history classifier ignores (a re-render is not a legal change), so healing thousands of documents adds **no** phantom "Content updated" events and leaves every `last_changed` untouched. A document whose re-render is byte-identical (a bump whose fix did not touch it) is not committed; only its stamp is refreshed in the manifest so it is not re-promoted every run. `rerendered_count` in the sync report is separate from `changed_count`.

Cost: re-rendered documents are re-embedded (embeddings derive from Markdown). A corpus-wide heal is a one-time cost the first sync after the bumped release ships — run it keyed, or let the Sprint 9 backfill re-embed on the next keyed sync.

### Maintenance: `sync --force-rerender`

`sync --force-rerender` re-renders **every** current document regardless of stamp or hash — the manual escape hatch for when you want to force the whole corpus through the current renderer without bumping the version (e.g. verifying a fix, or after an aborted heal):

```bash
uv run lovspor sync --force-rerender
```

It is **self-limiting**. A document whose re-render is byte-identical is skipped outright — not written, not embedded, not committed — because `retrieved_at` is carried over from the prior manifest record instead of being restamped. Only genuinely different output reaches git. Like the automatic heal, forced re-renders land under the `migration: re-render …` subject and report as `rerendered_count`, not `changed_count`.

Two things to know before running it:

- **Cost.** Embeddings are computed inside `_write_one`, so every document that *does* change is re-embedded. Budget accordingly, or run without `OPENAI_API_KEY` and let the Sprint 9 backfill re-embed on the next keyed sync.
- **Commit volume.** The re-rendered documents land in one bulk `migration: re-render …` commit followed by the manifest/index/history commit; use `LOVSPOR_GIT_COMMIT_MODE=single` to keep any accompanying real changes to one commit too.

It is a CLI flag and a `run_sync` parameter, never a `Settings`/env field — a stray environment variable must not be able to rewrite the whole corpus from the scheduled workflow.

### Required environment

```bash
LOVSPOR_DATA_DIR=./data
LOVSPOR_OUTPUT_REPO_PATH=../lovverk
```

The `LOVSPOR_OUTPUT_REPO_PATH` must point at a clone of [`lovverk`](https://github.com/bartoszkobylinski/lovverk) that has push permission to its remote (your own SSH key, or a deploy key if running in CI).

### Optional environment

```bash
OPENAI_API_KEY=sk-...        # also accepts OPENAI_APIKEY for legacy configs
```

Required for `lovspor sync` to write per-section embedding `.bin` files (Sprint 9), and for the MCP `semantic_search` tool to embed user queries at runtime. Without a key the engine still produces Markdown and runs the rest of the sync pipeline normally — the only casualty is that `.bin` files for documents added or changed in this run will not be written, and the next sync with a key set picks them up via the Sprint 9 backfill migration. Missing key in the MCP server disables only `semantic_search` and leaves the other fifteen tools working normally. Cost is fractions of a cent per query and ~$5-15/year for the production sync cadence — see [`docs/embeddings.md`](embeddings.md) for the model choice rationale.

## Scheduled runs (production)

`.github/workflows/sync.yml` runs daily at **04:00 UTC (~05:00–06:00 CET)** — about 2.5 hours after Lovdata's nightly tarball drop at ~01:30 UTC. Manual reruns are available via the **Actions → Sync legal corpus → Run workflow** button on GitHub.

The workflow:

1. Checks out the engine.
2. Installs `uv` and engine dependencies.
3. Configures SSH using the `LOVVERK_DEPLOY_KEY` secret.
4. Clones `lovverk` to a sibling directory.
5. Runs `lovspor sync` with the `OPENAI_API_KEY` secret in the
   environment, so changed docs get their embedding `.bin` sidecars
   written in the same run. (Production ran keyless from Sprint 9
   until 2026-06-10 — every sync silently skipped embeddings and
   `semantic_search` had no data to search. If the secret is ever
   removed, syncs keep working but embeddings stop updating until
   the next keyed run triggers the backfill migration.)
6. Pushes corpus changes only if HEAD is ahead of `origin/main`.

Concurrency is set to a single `sync` group: a second invocation queues until the active one finishes, never races.

### Keepalive — preventing 60-day auto-disable

GitHub automatically **disables a repository's scheduled workflows after 60 days with no repository activity**. This matters here because the daily sync commits to `lovverk`, not to `lovspor` — so the engine repo can sit 60 days without a commit and silently lose its `sync.yml` cron. The corpus quietly stops updating, and the only downstream signal is client-side (`corpus_status` reports `is_stale` after 7 days).

`.github/workflows/keepalive.yml` prevents this: it runs weekly (Mondays 03:17 UTC) and, when `HEAD` is older than 45 days, pushes an empty commit to reset the activity clock with margin before the 60-day cutoff. During active development the age guard skips, so it only touches history when the repo is genuinely idle. If you ever see a `chore: keepalive` commit, that is the mechanism working as intended.

## Deploy key setup (one-time)

The workflow needs **write access** to `lovverk` via an SSH deploy key. The engine repo holds the **private** key as a secret; the corpus repo holds the **public** key as a deploy key.

### 1. Generate the key locally

```bash
ssh-keygen -t ed25519 -C "lovspor-sync@github-actions" -f ~/.ssh/lovverk_deploy_key -N ""
```

Two files: `~/.ssh/lovverk_deploy_key` (private) and `~/.ssh/lovverk_deploy_key.pub` (public).

### 2. Public key → `lovverk` Deploy Keys

- Open: <https://github.com/bartoszkobylinski/lovverk/settings/keys/new>
- **Title:** `lovspor sync workflow`
- **Key:** paste the contents of `~/.ssh/lovverk_deploy_key.pub`
- **Allow write access:** ✅ enable (workflow must push)
- Click **Add key**

### 3. Private key → `lovspor` Actions Secrets

- Open: <https://github.com/bartoszkobylinski/lovspor/settings/secrets/actions/new>
- **Name:** `LOVVERK_DEPLOY_KEY`
- **Value:** paste the contents of `~/.ssh/lovverk_deploy_key` (the file **without** `.pub`)
- Click **Add secret**

## OpenAI key setup (one-time)

The sync workflow also needs `OPENAI_API_KEY` as an Actions secret to
write the embedding sidecars that power `semantic_search`:

```bash
gh secret set OPENAI_API_KEY   # paste the key when prompted
```

The first keyed sync after a key-less period auto-runs the Sprint 9
backfill migration (embeds every doc missing a `.bin`; ~30-60 min and
a few dollars of OpenAI usage for the full corpus, seconds and
fractions of a cent on routine daily runs).

### 4. Optionally remove the local copies

```bash
rm ~/.ssh/lovverk_deploy_key ~/.ssh/lovverk_deploy_key.pub
```

The keys live in GitHub now; you don't need them locally unless you want to debug SSH config off-line.

## Temporal tools require full corpus git history

The git-log-based time-machine tools (`get_law_at`, `diff_law_versions`) can
only reach as far back as the local checkout's git history. `lovspor
fetch-corpus` clones **shallow by default** (`--depth 1`, small download); on
such a clone the tools serve only dates after the clone was made, and a date
beyond the boundary raises a dedicated incomplete-history error
(`ShallowHistoryError`) — never a claim that the law was absent from the
corpus (ADR-0003).

**Operational requirement: any deployment exposing the temporal tools must
use complete git history.**

- New checkouts: `lovspor fetch-corpus --full-history` (the hosted deployment
  units pass this flag).
- Existing shallow checkouts, deepened in place (additive, no history
  rewrite):

```bash
git -C <corpus-path> fetch --unshallow
```

Shallow clones remain supported only where the reduced temporal reach is
explicitly acceptable (e.g. local current-law lookup, keyword search, CI
smoke tests). `list_law_versions` and `get_law_history` read committed
`history/<slug>.json` files and are unaffected by clone depth.

## Idempotency

`lovspor sync` is idempotent: running twice on the same upstream state produces **zero file changes and zero git commits**. The orchestrator early-returns before manifest write/commit when the change detector reports no `new` / `changed` / `removed` documents. The integration test `test_run_sync_is_idempotent_on_unchanged_state` enforces this by asserting commit-count parity.

`sync --force-rerender` preserves that property when the renderer has not changed: every document re-renders byte-identically, is skipped, and reports as `unchanged` — no commits. `test_force_rerender_is_a_noop_when_render_output_is_unchanged` asserts commit-count parity and a clean working tree.

## Recovery from a bad sync

If a sync produces an incorrect commit on `lovverk` (e.g., a renderer bug ships, gets fixed, but the bad commit is on `main`):

1. **Identify the bad commits** on `lovverk` `main`.
2. **Revert** them on `lovverk`:
   ```bash
   cd ~/Programming/Python/lovverk
   git pull origin main
   git revert <bad-commit-sha>      # produces a new commit that undoes the change
   git push origin main
   ```
3. **Do not** force-push or rewrite history. Consumers may have pulled.
4. The next sync run will re-classify the affected documents (now diff vs the reverted state) and write fresh commits with the correct content.

If the sync workflow itself is failing, check **Actions** → most recent run → **sync** job log. Common failures:

- **Deploy key auth**: `Permission denied (publickey)`. Re-check that `LOVVERK_DEPLOY_KEY` secret exists and matches the public key on `lovverk`.
- **Lovdata 5xx**: transient. Re-run via **Run workflow** button or wait for tomorrow's scheduled run.
- **Schema drift**: `ParseError: invalid manifest schema`. Engine version mismatch with on-disk manifest; bump manifest version and migrate.
- **Non-fast-forward push**: `failed to push some refs to ...`. The workflow's concurrency group prevents two workflow runs from racing each other, but a human commit pushed to `lovverk/main` *during* an active sync will cause the final `git push origin main` to fail. Recovery: wait for the run to fail, then trigger a fresh **Run workflow** — the next run starts from the latest `lovverk/main` (including your push) and proceeds normally.
- **Sync silently stopped / no runs for weeks**: GitHub disabled the scheduled workflow after 60 days of engine-repo inactivity (see *Keepalive* above). Confirm on **Actions → Sync legal corpus** — a disabled workflow shows a banner and no recent scheduled runs. Re-enable via that banner (**Enable workflow**) or `gh workflow enable sync.yml`, then trigger a fresh **Run workflow** to catch up. The `keepalive.yml` workflow is designed to prevent this, but a manual re-enable is the recovery if it ever slips through (e.g. it too was disabled in the same idle window).
