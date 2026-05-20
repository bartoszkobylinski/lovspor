# Operations

## Local runs

```bash
./scripts/bootstrap.sh         # one-time setup (uv sync + pre-commit install + checks)
uv run lovspor --help          # CLI usage
uv run lovspor --version       # show version
uv run lovspor info            # project info
uv run lovspor seed            # initial corpus population (first sync)
uv run lovspor sync            # incremental update against latest tarballs
```

`seed` and `sync` are aliases at the engine level — both call the same orchestrator. Use `seed` semantically for the first run on an empty corpus, `sync` for repeated invocations. Settings are read from environment variables (or a `.env` file at the engine repo root). See `.env.example` for the required variables.

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

Required for `lovspor sync` to write per-section embedding `.bin` files (Sprint 9), and for the MCP `semantic_search` tool to embed user queries at runtime. Without a key the engine still produces Markdown and runs the rest of the sync pipeline normally — the only casualty is that `.bin` files for documents added or changed in this run will not be written, and the next sync with a key set picks them up via the Sprint 9 backfill migration. Missing key in the MCP server disables only `semantic_search` and leaves the other thirteen tools working normally. Cost is fractions of a cent per query and ~$5-15/year for the production sync cadence — see [`docs/embeddings.md`](embeddings.md) for the model choice rationale.

## Scheduled runs (production)

`.github/workflows/sync.yml` runs daily at **04:00 UTC (~05:00–06:00 CET)** — about 2.5 hours after Lovdata's nightly tarball drop at ~01:30 UTC. Manual reruns are available via the **Actions → Sync legal corpus → Run workflow** button on GitHub.

The workflow:

1. Checks out the engine.
2. Installs `uv` and engine dependencies.
3. Configures SSH using the `LOVVERK_DEPLOY_KEY` secret.
4. Clones `lovverk` to a sibling directory.
5. Runs `lovspor sync`.
6. Pushes corpus changes only if HEAD is ahead of `origin/main`.

Concurrency is set to a single `sync` group: a second invocation queues until the active one finishes, never races.

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

### 4. Optionally remove the local copies

```bash
rm ~/.ssh/lovverk_deploy_key ~/.ssh/lovverk_deploy_key.pub
```

The keys live in GitHub now; you don't need them locally unless you want to debug SSH config off-line.

## Idempotency

`lovspor sync` is idempotent: running twice on the same upstream state produces **zero file changes and zero git commits**. The orchestrator early-returns before manifest write/commit when the change detector reports no `new` / `changed` / `removed` documents. The integration test `test_run_sync_is_idempotent_on_unchanged_state` enforces this by asserting commit-count parity.

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
