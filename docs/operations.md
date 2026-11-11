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
uv run lovspor repair-embeddings  # diagnostic: flag under-embedded docs (see Maintenance)
uv run lovspor annotate-input-identity  # one-time ADR-0006 manifest annotation (see Maintenance)
uv run lovspor migrate-lspe-v2  # one-time ADR-0005 Stage 2 sidecar cutover (see Maintenance)
uv run lovspor sync --allow-mass-reembed  # explicit override for a large intended re-embed (see Maintenance)
uv run lovspor fetch-corpus    # clone/update the local lovverk corpus that `lovspor mcp` reads
uv run lovspor mcp             # serve the corpus to AI assistants over MCP (stdio)
uv run lovspor mcp-http        # serve the same tools over MCP Streamable HTTP (binds localhost — bearer auth + quotas; TLS terminated upstream, see deploy/digitalocean/)
```

`mcp-http` powers the optional operated (hosted) endpoint. It enforces **bearer-token authentication (revocable per-credential tokens) and per-credential rate limiting + quotas**. Optionally it also accepts **OAuth logins** via WorkOS AuthKit — set `LOVSPOR_AUTHKIT_DOMAIN` *and* `LOVSPOR_PUBLIC_URL` together (one without the other refuses to start; see [`mcp.md` § Authentication](mcp.md#authentication-two-modes)). The **app itself has no TLS** — the transport is plaintext, so a bearer token on an exposed port is sent in the clear. TLS terminates in a reverse proxy: Caddy with automatic Let's Encrypt, per the `deploy/digitalocean/` recipe, which **has been deployed since 2026-07-18** — the hosted instance is live at `https://lovspor.no/mcp`, accepting **both credential modes** — opaque `lsp_…` tokens and hosted OAuth via WorkOS AuthKit — *(owner decision 2026-09-30, verified live that day: RFC 9728 discovery answers 200 and Claude.ai and ChatGPT connectors log in over OAuth; supersedes the 2026-09-26 opaque-token-only decision of #343, which had itself superseded the 2026-08-02 statement that both modes were active)*. The WorkOS requirements (DCR on, the MCP resource defined and default) are in [`mcp.md` § Authentication](mcp.md#authentication-two-modes). If you run it yourself, keep it bound to localhost behind such a proxy and do not expose the app port to the internet. It serves `/mcp` plus unauthenticated `/healthz` and `/readyz` probes. See [`mcp.md` § Streamable HTTP transport](mcp.md#streamable-http-transport).

`seed` and `sync` are aliases at the engine level — both call the same orchestrator. Use `seed` semantically for the first run on an empty corpus, `sync` for repeated invocations. Settings are read from environment variables (or a `.env` file at the engine repo root). See `.env.example` for the required variables.

### Maintenance: embedding input identity and the mass-re-embed guard (ADR-0006)

Every keyed sync reconstructs each current document's embedding inputs locally and
compares their digest against the record's `embedding_input_hash`; an absent or
mismatching value selects the document for re-embedding. Before any provider call,
the complete repair selection is sized on two dimensions — document count/fraction
(`LOVSPOR_REEMBED_GUARD_MAX_FRACTION`, default 0.02) and input-token workload
(`LOVSPOR_REEMBED_GUARD_MAX_TOKENS`, default 1,000,000 ≈ $0.13) — and an
unexpectedly large scope **fails closed** with a message naming the counts and the
threshold that fired. Ordinary daily repairs pass automatically; a corpus-wide
selection (unannotated corpus, deliberate pipeline change, mass field-stripping)
requires the deliberate `lovspor sync --allow-mass-reembed`. The scheduled workflow
never passes the override, so a tripped guard fails the job before any spend.

The same guard and thresholds cover the change path (#790): before the first
provider call, the Sprint 9 backfill's included, a keyed sync renders every new, changed (byte-identical re-renders
excluded) and renamed document and sizes the run's embedding workload. When every
document hashes as changed — as on 2026-10-20/21, when Lovdata added an ELI row to
every header (#679) — the sync refuses with nothing written and no commit, instead
of re-embedding the corpus. The token count is the tokenizer's count of the
inputs to be sent, an estimate: a retried request can be billed again. The
override does not make a corpus-wide re-embed fit the sync job: one ran past the
120-minute `timeout-minutes` twice, and the paid vectors were lost with the
workspace.

`lovspor annotate-input-identity` is the one-time metadata migration for an
existing fully-embedded corpus: manifest-only, keyless, idempotent, and
drift-guarded (corpus HEAD re-verified and every digest recomputed immediately
before the manifest is written; any drift aborts with nothing written).

### Maintenance: `migrate-lspe-v2` (ADR-0005 Stage 2 cutover)

`lovspor migrate-lspe-v2` is the one coordinated corpus-wide LSPE version-2
cutover: every current sidecar is rewritten with its manifest ESI embedded in
the header, vectors preserved bit-for-bit and every written file re-read and
verified. Keyless, clean-worktree- and HEAD-drift-guarded, idempotent;
Markdown, manifest and history are untouched. It aborts on a record without a
recorded ESI and on an existing version-2 file that disagrees with the
manifest.

The ADR-0005 §3 ordering is **binding** — no step may be skipped:

1. The dual-reader engine release (reads v1 and v2) is merged and deployed to
   every consumer — including the hosted MCP droplet and any cached `uvx` MCP
   builds. A pre-Stage-2 reader meeting a v2 file skips it as "corrupt"
   silently; that is the failure the ordering exists to prevent.
2. A deliberate propagation window passes, decided by the owner. The corpus
   emits only version 1 throughout (`lspe_writer_version` stays 1).
3. The cutover runs against a pristine clone, is verified, and is pushed as
   one commit. Never a partial or incremental rollout.
4. The writer flips to version 2 (`LOVSPOR_LSPE_WRITER_VERSION=2` in the
   scheduled workflow, then a release changing the default) — only after the
   cutover has landed, and promptly after it, so a post-cutover document
   update does not reintroduce a version-1 file.

### Maintenance: `repair-embeddings` (diagnostic/recovery)

Superseded as the normal drift detector by the input-identity condition above —
retained during the rollout as an independent safety net. Originally a one-time
repair for a corpus whose embeddings were written before a section-parser fix. Flat (chapterless) acts render their sections at H2 (`## § N.`); acts synced before that shape was recognized produced **zero** embedding vectors and are invisible to `semantic_search`, yet carry `embedding_hash == xml_hash` — so the normal Sprint 9 staleness check never re-embeds them.

`repair-embeddings` checks, per section id, whether the stored `.bin` holds a vector for every section the current parser finds; it clears `embedding_hash` on any doc missing one and commits the manifest. A section long enough to be split into several chunks stores more vectors than sections and is **not** flagged — the comparison is per-id, not a raw count, so a fully-embedded doc is left alone. It does **not** call the OpenAI API itself:

```bash
uv run lovspor repair-embeddings          # flags docs, commits the manifest (no API cost)
OPENAI_API_KEY=sk-... uv run lovspor sync  # Sprint 9 backfill re-embeds exactly the flagged docs
```

It is idempotent — a no-op with no commit once every embedding matches its sections. When last run (2026-07-07) the affected set was ~2,333 acts (~$0.57 one-time at `text-embedding-3-large` pricing); that backfill has since completed. The churn is `.bin` rewrites only, markdown is untouched.

### Maintenance: the durable embedding cache (#802, off by default)

Design: `docs/proposals/embedding-cache-802.md`. The cache keeps vectors a
keyed sync has paid for, keyed by ESI and the ADR-0006 input hash, so a rerun
after a killed sync pays only for what it had not finished. It is **off**
unless `LOVSPOR_EMBEDDING_CACHE_DIR` is set, and `sync.yml` does not set it.

**Enable (a local or self-hosted sync only).** Choose an absolute directory
outside the `lovverk` checkout and outside the engine checkout; the sync
refuses one inside either. It is created `0700` if missing.

```bash
export LOVSPOR_EMBEDDING_CACHE_DIR=/var/lib/lovspor/embedding-cache
export LOVSPOR_EMBEDDING_CACHE_MAX_AGE_DAYS=30   # optional, default 30
uv run lovspor sync
```

During a keyed sync the cache prints one line every 50 documents to stderr:

```text
embedding-cache: documents=50 documents_cached=48 units_cached=0 units_paid=2 estimated_tokens_paid=3114
```

Counts only: no text, key or path. `estimated_tokens_paid` is the tiktoken
count of what was sent, not a bill; a retried request may be billed again.

**After a killed sync.** Rerun the same command with the same cache
directory. Documents and long-document requests that were checkpointed cost
nothing; the request in flight when the run died is paid again (no response
means nothing was stored). The ADR-0006 guards still count the whole
workload, so a resumed mass re-embed still needs `--allow-mass-reembed`.

**A sync stopped with `EmbeddingCacheError`.** An entry failed validation (or
the store cannot be written). The run did not pay over it. Look before
deleting anything:

```bash
uv run lovspor ops embedding-cache verify      # exit 1 lists each refused or expired entry
uv run lovspor ops embedding-cache invalidate --esi <esi> --input-hash <hash>
uv run lovspor ops embedding-cache invalidate --esi <esi>   # a whole space
```

An `EmbeddingCacheExpiredError` names an entry past the retention:

```bash
uv run lovspor ops embedding-cache prune
```

`prune` removes expired entries, unit checkpoints whose document entry
exists, and temporary files older than one hour. It never removes an entry it
refuses: that is evidence, and `invalidate` is the deliberate removal.
`status` prints entry counts and bytes per space.

**Roll back.** Unset `LOVSPOR_EMBEDDING_CACHE_DIR`. The corpus never depends
on the cache, so nothing else changes; deleting the directory loses only
money already spent, never corpus state.

**Remote store (Cloudflare R2), built but off.** GitHub-hosted runners discard
their disk with the job, so the local store alone does not protect the
scheduled sync.

* **Decided:** the owner accepted R2 Standard as the remote backend on
  2026-11-08, for architecture and offline development only (ADR-0022, G0 and
  D1–D3).
* **Built:** the adapter (`embeddings/r2_client.py`, `embeddings/cache_remote.py`)
  writes each paid unit to the bucket before the next request, remote first.
* **Off:** the sync uses it only when `LOVSPOR_EMBEDDING_CACHE_REMOTE=r2`, the
  local directory and the four `LOVSPOR_EMBEDDING_CACHE_R2_*` variables are
  all set. No workflow sets them.
* **Not approved:** bucket, token and secrets (G1), staging tests (G3) and
  activation (G4/G5).
* **Runbook:** every owner step, rotation and rollback are in
  [`docs/embedding-cache-r2.md`](embedding-cache-r2.md).

With the remote store on, two more refusals can stop a sync. Neither one pays
over anything:

* `EmbeddingCacheConflictError`: the local copy and the bucket hold different
  values for one key. Both copies are kept.
* `EmbeddingCacheStorageError`: the bucket stayed unreachable after three
  attempts, or refused a request.

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

Required for `lovspor sync` to write per-section embedding `.bin` files (Sprint 9), and for the MCP `semantic_search` tool to embed user queries at runtime. Without a key the engine still produces Markdown and runs the rest of the sync pipeline normally — the only casualty is that `.bin` files for documents added or changed in this run will not be written, and the next sync with a key set picks them up via the Sprint 9 backfill migration. The post-sync `lovspor audit` step reports each added document left without a sidecar as `missing_embedding` and fails, so a cleared or mistyped secret no longer passes as a green run (#344). Missing key in the MCP server disables only `semantic_search` and leaves the other seventeen tools working normally. Cost is fractions of a cent per query and ~$5-15/year for the production sync cadence — see [`docs/embeddings.md`](embeddings.md) for the model choice rationale.

## Load-testing the hosted endpoint (issue #480)

`scripts/load/mcp_load.py` drives N concurrent simulated clients against a `lovspor mcp-http` endpoint over Streamable HTTP, one concurrency level after another, and reports per level: calls, ok, refusals by reason (`capacity` — the instance ceiling; `in_flight`, `rate`, `quota` — the credential's own brakes), errors, latency p50/p95/p99 of the successful calls, and throughput. Every client opens its own MCP session and replays a research question — `search_laws` → `get_section` × k → `verify_quote` (a quote from the first section it read) → `validate_citation` — over a fixed list of real acts. `semantic_search` spends the operator's OpenAI money and runs only with `--include-paid`; leave it off. A refused call is counted, not retried.

It is repo tooling, not part of the MCP runtime, and **never runs in CI**. The token comes from `--token-file PATH` (one token per line, `-` for stdin; clients take the tokens round-robin) or `LOVSPOR_LOAD_TOKEN` — never from argv, and it is never printed. `--json-out FILE` writes the full report (per-tool counts, up to three distinct error texts per level) beside the Markdown table on stdout.

**What a local run can and cannot show.** The instance-wide ceiling (`ServiceLimits`, `LOVSPOR_SERVICE_MAX_IN_FLIGHT`, default 4) is applied **only in hosted-OAuth mode** (`_build_enforcer` in `src/lovspor/mcp.py`). A local `mcp-http` serving opaque tokens only never refuses `capacity`: it measures the tool bodies, the transport and the per-credential brakes. Local numbers describe the machine they ran on, never the 1-vCPU droplet.

### Locally first

```bash
# a throwaway credential store; run from the repo root with no .env, so no OpenAI key reaches the server
mkdir -p /tmp/lovspor-load && chmod 700 /tmp/lovspor-load
uv run lovspor tokens issue --label load-local --expires-in-days 1 \
  --credentials /tmp/lovspor-load/credentials.json
(umask 077 && pbpaste > /tmp/lovspor-load/token)   # copy the lsp_… line just printed first
LOVVERK_CORPUS_PATH=../lovverk uv run lovspor mcp-http --host 127.0.0.1 --port 18480 \
  --credentials /tmp/lovspor-load/credentials.json &
uv run python scripts/load/mcp_load.py --url http://127.0.0.1:18480/mcp \
  --token-file /tmp/lovspor-load/token --concurrency 1,2,4,8,16 --questions 3 --pause 20 \
  --json-out /tmp/lovspor-load/report.json
kill %1 && rm -rf /tmp/lovspor-load
```

One token for all clients shows the credential brakes (a hand-issued token's default burst of 30 is spent by the second level); one token per client in the file takes the per-credential brakes out of the picture. `--pause 20` lets a token's bucket refill between levels (120/min refills 30 in 15 s).

### Production (owner action)

Never automated, never from CI or an agent. Run it at a **quiet hour**: at 5+ clients the run fills the instance ceiling, and real users are refused `capacity` while it lasts. The run also spends from the server-wide daily ceiling (`LOVSPOR_SERVICE_DAILY_QUOTA`, default 20 000); the token's own `daily_quota` below bounds that at 600.

1. On the droplet, issue a dedicated one-day token and note its credential id (`beta-0NN` below):

```bash
sudo -u lovspor /opt/lovspor/app/.venv/bin/lovspor tokens issue --label load-test --expires-in-days 1 \
  --credentials /opt/lovspor/.config/lovspor/credentials.json
```

2. Give that record its limits. `daily_quota` is the low quota — the hard cap on calls the run can be served. `max_in_flight` and the rate are raised on purpose: at the hand-issued defaults (4 in flight, burst 30) the token's own brakes refuse first and hide the instance ceiling the run is there to measure. Replace `beta-0NN`; the server re-reads the store on change:

```bash
sudo -u lovspor /opt/lovspor/app/.venv/bin/python - beta-0NN <<'PY'
import sys
from pathlib import Path
from lovspor.access import Limits, load_credentials, write_credential_file
path = Path("/opt/lovspor/.config/lovspor/credentials.json")
target = sys.argv[1]
limits = Limits(max_in_flight=16, rate_per_minute=600, rate_burst=100, daily_quota=600, paid_daily_quota=1)
credentials = load_credentials(path)
if not any(c.credential_id == target for c in credentials):
    sys.exit(f"no credential {target}")
write_credential_file(path, [c.model_copy(update={"limits": limits}) if c.credential_id == target else c for c in credentials])
print(f"{target}: {limits}")
PY
```

3. From the Mac — the real path through the internet, Caddy and TLS — with the token copied to the clipboard:

```bash
cd ~/Programming/Python/lovspor && git pull
mkdir -p ~/.config/lovspor && (umask 077 && pbpaste > ~/.config/lovspor/load-token)
uv run python scripts/load/mcp_load.py --url https://lovspor.no/mcp \
  --token-file ~/.config/lovspor/load-token --concurrency 1,2,4,8,16 --questions 2 --pause 20 \
  --json-out ~/lovspor-load-$(date +%Y%m%d-%H%M).json
```

   About 31 clients × 2 questions × 5–6 calls ≈ 340 calls in total, under the token's 600. Server side of the same window, on the droplet:

```bash
sudo journalctl -u lovspor-mcp --since "-30 min" --no-pager | tail -200
```

4. Revoke the token on the droplet and delete the local copy on the Mac, whatever the outcome:

```bash
sudo -u lovspor /opt/lovspor/app/.venv/bin/lovspor tokens revoke beta-0NN \
  --credentials /opt/lovspor/.config/lovspor/credentials.json
```

```bash
rm ~/.config/lovspor/load-token
```

Latencies from the Mac include the network round trip: compare levels with each other, not with a local table.

## Publishing the site: the release envelope (ADR-0014 Decision 6)

The corpus site (ADR-0013) and the ADR-0014 site are released **together**, as
one envelope under `/var/www/lovspor-releases/<release_content_id>/` with two
trees, `corpus/` and `site/`, and two root files written once the id is
known: `release.json` (the record) and `release.caddy` (the per-release Caddy
fragment: `vars lovspor_release <id>`, both roots, the corpus's own
redirect map and a `handle_errors` block that answers every 404 with the
release's own `site/404.html`, status kept, every path absolute and
immutable). The cutover is one Caddy
configuration swap — the host's Caddyfile imports the active fragment — so a
request is served entirely by the old release or entirely by the new one, and
**no symlink exists**. What is live is never a pointer but a reconciled
triple: Caddy's running configuration read from its admin socket (R), the
configuration adapted from disk (D) and the marker `ACTIVE` (M) must name one
release with equal configuration hashes; otherwise every mutating command
refuses and prints the three, and an unreachable admin socket is its own
named refusal. The full contract — the seven-step build order, the
stage/commit/reload/marker transaction, the exhaustive crash table, the
`prune` invariant and rollback — is ADR-0014 Decision 6; the operator's
commands and the droplet procedure are in
[`deploy/digitalocean/README.md`](../deploy/digitalocean/README.md#operating-it).

```bash
uv run lovspor release live                      # the reconciled live id, or 'none'
uv run lovspor release build --corpus <clone> --live <id|none>   # probe + seven steps; prints the id
uv run lovspor release commit <id>               # URL continuity vs the live release (#820), then stage, validate, commit, reload, mark
uv run lovspor release reconcile [--complete|--abandon]          # name and resolve a crash state
uv run lovspor release rollback                  # the marker's previous, same transaction; not judged by URL continuity
uv run lovspor release prune                     # only when reconciled; never R, D, M or previous
uv run lovspor release rehearse <id> --unit <unit> …  # walk that migration on a SECOND instance first
uv run lovspor release rehearse-urls <id>        # …and dry-run what the two configurations ANSWER
uv run lovspor release migrate <id>              # the FIRST envelope: install the Caddyfile and cut over
uv run lovspor release migrate --check           # that migration's preflight alone; nothing moves
uv run lovspor release migrate --rollback        # back to the pre-envelope Caddyfile and TCP admin
uv run lovspor release migrate --rollback --offline  # the same, dialling nothing: files back, unit restarted
uv run lovspor release migrate --retire          # list the pre-envelope paths; removes nothing
uv run lovspor release migrate --retire --yes    # remove them; no way back after
uv run lovspor publish-check <envelope>          # the final check: both trees, cross-tree, ids
uv run lovspor publish-check <envelope> --previous <live envelope>  # + no URL the live release served would 404 (docs/url-contract.md)
```

Exit codes: `0` done (a `commit` of the live release included), `1` refused
or unreconciled with one stderr line, `2` usage, `3` the precondition *Caddy
admin reachable* unmet, with D and M printed. The commands take
`--releases`/`LOVSPOR_RELEASES_ROOT`, `--caddyfile`/`LOVSPOR_CADDYFILE`,
`--fragment`/`LOVSPOR_RELEASE_FRAGMENT` and `--admin`/`LOVSPOR_CADDY_ADMIN`.

`migrate` is the one command a host runs once: the first envelope's cutover,
where the configuration being installed is also what moves Caddy's admin
endpoint from `localhost:2019` onto the socket, so the load is delivered
explicitly to the old address rather than through `systemctl reload caddy`
(ADR-0014 Migration). It takes `--tcp-admin`/`LOVSPOR_CADDY_ADMIN_TCP`,
`--caddyfile-source`/`LOVSPOR_CADDYFILE_SOURCE`,
`--drop-in`/`LOVSPOR_CADDY_DROP_IN`, `--runtime-dir`/`LOVSPOR_CADDY_RUNTIME_DIR`
and `--release-group`/`LOVSPOR_RELEASE_GROUP` beside those four. `--check`,
`--rollback` and `--retire` exclude each other and the release id; `--retire`
deletes the only way back from the cutover — the drop-in's record and then,
last of all, the pre-envelope Caddyfile — so it is always a separate, later run,
and
it lists the paths and removes nothing until it is repeated with `--yes` (a
flag, never a prompt: an unattended run fails closed). No path this command
writes to or removes may be a symlink: `exists()` calls a dangling one absent
and a live one would hand root's write to whatever it points at, so every one
of those names is refused rather than followed, and every one is re-asked
immediately before the write rather than trusted from the preflight. What stood
at the drop-in's name is recorded under one of two names — `.pre-envelope` for
its bytes, `.pre-envelope.absent` for a name that held no file — because an
empty drop-in and an absent one are the same zero bytes and must not restore
alike. `--rollback --offline` is the
last resort for a box whose Caddy answers on neither address: it dials nothing,
restores the files and restarts the unit. The droplet procedure, step by step, is
[`deploy/digitalocean/README.md` § First migration](../deploy/digitalocean/README.md#first-migration-once-on-the-existing-droplet).

`rehearse` is what authorises that one run, and it is run **before** it
(ADR-0014 Validation (g)). The cutover's load moves the admin endpoint onto the
socket and the rollback's load is delivered to that socket, so neither can be
tried twice on the box that serves the site: both are walked first on a second
Caddy instance with its own unit, ports, runtime directory, drop-in directory
and releases root, every one of them an option. It drives the same
`first_migration` and `rollback_first_migration` as the real run, and resolves a
refused load through `reconcile --abandon` exactly as an operator would — the
procedure is never forked — and asserts, in the ADR's order: (i) TCP answering
the previous configuration with no socket; (ii) a load Caddy rejects leaving R
unmoved but answering over the rejected configuration's socket with TCP refusing
— Caddy v2.11.4's ordering, required exactly (ADR-0014 Amendment 1) — then
`reconcile --abandon` leaving TCP answering the previous configuration and
nothing of the attempt behind, then the cutover with the socket absent
immediately before it and answering immediately after, TCP refusing, R naming
the envelope and `ExecReload=` naming the explicit socket address; (iii) the rollback
reaching the socket; (iv) one steady-state `systemctl reload` through the
drop-in; (v) two restarts, each followed by the admin socket's four facts. Two
of its fixtures must **fail**: a plain `systemctl reload` of the previous
Caddyfile through the stock line while the socket is running (`caddy reload`
derives the address from the file it supplies — which is what makes the
rollback's explicit `--address` load-bearing), and a socket whose mode and
group were set once by hand, which a restart takes away. `--unit caddy` and
`--caddyfile /etc/caddy/Caddyfile` are refused by name, before anything is
read.

The rehearsal **cannot run in CI**: there is no `caddy` binary and no systemd
on the runner. What CI proves is that the sequence and every assertion are
sound against `FakeCaddy` (`tests/unit/test_release_rehearsal.py`, with a test
per negative fixture proving it really does fail). Whether this Caddy build
accepts the `|0660` suffix, whether its adapted JSON hashes equal to what `GET
/config/` returns and whether a restart recreates the socket `0660` in the
group are facts only the droplet reports — which is the reason the rehearsal
exists. The harness that starts the instance,
[`deploy/digitalocean/rehearse-migration.sh`](../deploy/digitalocean/rehearse-migration.sh),
carries no assertion of its own.

`rehearse-urls` is the other half, and the migration is authorised by **both**.
(g) covers the addresses, the loads and the socket and looks at no URL; this
covers routing, which
nothing else in the sequence does — a Caddyfile can hold its admin socket
correctly, pass every one of (i)–(v), and serve 404 on `/lov/…`. It starts
nothing: `caddy validate` and `caddy adapt` over the serving Caddyfile and the
new one, against a fully built envelope and the flat release still behind
`lovspor-current`, then a route-by-route comparison. It asserts that every
corpus URL the old configuration **answers** — the same handler, the same file
by root-relative name and by SHA-256, the same status and `Location`,
everything but the root, which moves on purpose — is answered the same by the
new one from `<release>/corpus` under the release's own map; that `/` and
`/observatory/` come from `<release>/site`, their bytes deliberately not
compared; that no serving path of the new configuration passes through a
symlink below `/var/www`, read off the adapted routes rather than the
Caddyfile's text; and that the previous Caddyfile, adapted again, answers
exactly as it did with the admin endpoint back on TCP and every file as it was
found. A URL the new configuration answers and the old one does not is no
obstacle — the comparison is one-directional.

It cannot run in CI either, and for the same reason. What CI proves is the
comparison against committed `caddy adapt` captures
(`tests/unit/fixtures/caddy_adapt/`, regenerated by
`scripts/capture_caddy_adapt.py` and only when a Caddyfile changes), with a
negative fixture per assertion: a configuration that drops a corpus URL, one
that answers it from the wrong tree, one that reaches the right tree through a
symlink, and a tree changed under the run. Its harness,
[`deploy/digitalocean/rehearse-urls.sh`](../deploy/digitalocean/rehearse-urls.sh),
carries no assertion of its own either.

## Observatory: surveying a host before registering it (issue #349)

Registration needs evidence, and `survey` is where it comes from. Three requests
per host at most — `robots.txt`, the conventional `/sitemap.xml`, the front page
— and none at all past a policy that refuses. Nothing is captured and nothing
enters the observation log: the question is whether a source *could* be
registered, not what it says.

```bash
export LOVSPOR_OBSERVATORY_ROOT=~/lovspor-observatory

uv run lovspor observatory survey --domain baerum.kommune.no
uv run lovspor observatory survey --from recon/kommuner.txt --run-id 2026-09-19-all
```

Every run writes `<root>/survey/<run-id>.jsonl`, one row per host. **That file is
the point.** The 2026-08-20 sweep over the municipalities produced the
figures still quoted in `observatory/commands.py` and persisted nothing, so its
population cannot be re-derived — which is issue #349, and the reason this is a
command rather than another script.

The survey log sits beside the observation log and is not part of it. An
observation records what an activated source served; a survey row records whether
a host could be activated at all. Keeping them apart preserves the invariant that
every authority id in the observation log is a registered one.

Six entries, and the distinctions carry weight:

| entry | means |
| --- | --- |
| `declared_sitemap` | `robots.txt` names a sitemap — the cheapest entry, so it wins over anything else the page also reveals |
| `conventional_sitemap` | nothing declared, but `/sitemap.xml` serves a document discovery can read. A 200 is not the test: the parser discovery itself uses decides, so a styled 404 does not count |
| `browser_assembled` | no sitemap, and the front page carries the API markers issue #194 found on 12 of 12 sitemap-less municipalities. Not a dead end — the index exists and a sitemap reader cannot see it |
| `no_machine_index` | no sitemap, no marker. The only entry that means a human must look, which is why the others must not drain into it |
| `robots_disallowed` | the site publishes a policy and it refuses the root. Its decision, not our finding |
| `robots_unreadable` | the policy could not be read. `fetch.py` treats that as a denial and so does this |

`--delay` defaults to 7 seconds, the spacing every registered source was cleared
with. A survey does not take the sweep's archive lock, so it can run while a
nightly sweep is in progress — different hosts do not share a politeness budget.
A bare domain whose root answers with a redirect to a host under its own name
(`kommune.no` → `www.kommune.no`) is probed there as well, robots first and with
the same spacing, and the row carries that host's findings under the name that was
asked; before #508 such a host read as `no_machine_index` because no probe request
follows redirects. A redirect to any other name, or a second redirect, is not
followed: the row says what was served.

Every observatory client — the survey, the robots read at registration, capture
and the sweep — connects under `observatory/tls.py` (#508): Python's default
cipher list first, then the two RSA-key-exchange AES-GCM suites as the last
resort, because five Romerike municipalities (Nes, Gjerdrum, Nannestad, Eidsvoll,
Hurdal; one server) serve TLS 1.2 with RSA key exchange only and reset the
connection on a ClientHello that offers nothing they accept, which read as
`robots_unreadable` and so as a denial. TLS 1.2 stays the minimum and the
certificate is verified as before; against such a server the session has no
forward secrecy, which for public pages the archive stores is the accepted
trade-off. The site and release probes keep httpx's defaults.

What this does **not** do: it does not resolve #194, it only sizes the population
that issue is about. And it does not register anything. Activation still needs a
named human's conclusion about the site's terms, which is the next section.

## Observatory: registering a capture source (ADR-0010)

Capture is refused until a named human has checked the source's `robots.txt`
and terms and recorded what they concluded. That is two commands, and the gap
between them is deliberate.

The registry lives under `LOVSPOR_OBSERVATORY_ROOT`, which must point outside
this repository and outside the `lovverk` corpus. There is no flag to override
it — a flag would be a one-word way to write access-policy records into a
published repository.

```bash
export LOVSPOR_OBSERVATORY_ROOT=~/lovspor-observatory

# 1. Eligible: an official municipal site. Nothing may fetch it yet.
uv run lovspor observatory register-source \
  --id 3201 --name "Bærum" --domain baerum.kommune.no --type kommune
```

Then read the source's `robots.txt` and its terms of use, and write down what
you concluded. The check is a document rather than a set of flags because it
has to answer "why was this activated?" months later:

```json
{
  "checked_at": "2026-08-18T17:00:00Z",
  "robots_txt_url": "https://www.baerum.kommune.no/robots.txt",
  "robots_allows": true,
  "terms_reviewed": true,
  "terms_permit_capture": true,
  "terms_url": "https://www.baerum.kommune.no/personvern/",
  "rate_limit_seconds": 7.0,
  "user_agent": "lovspor-observatory/0.1 (+https://lovspor.no/observatory)",
  "reviewed_by": "Your Name",
  "note": "What you actually found, including what you could not find."
}
```

`terms_reviewed` and `terms_permit_capture` are separate on purpose: the first
says someone read the terms, the second says what they concluded. Collapsing
them would let "I read the terms and they prohibit automated access" clear a
source for crawling. A document asserting the second without the first is
rejected.

```bash
# 2. Activated: capture is now permitted, under the recorded rate limit.
uv run lovspor observatory activate-source --id 3201 --check ./baerum-check.json

uv run lovspor observatory sources
```

Set `rate_limit_seconds` to at least the source's own `Crawl-delay` when it
declares one. `activate-source` enforces this (issue #449): it reads the live
`robots_txt_url` as the check's `user_agent`, takes the `Crawl-delay` of the
group that crawler falls under (its own product token, else `*`; consecutive
`User-agent` lines share one group, so Kongsvinger's `User-agent: *` directly
above `User-agent: MSNBot` / `Crawl-delay: 30` binds us to 30 s), and refuses a
lower rate. The read follows at most 5 redirects, each of which must stay
inside the source's cleared domain. A redirect off the domain, a sixth
redirect, a 5xx or an unreachable host refuses activation; a 4xx means the
site publishes no rules and sets no floor. Permission to fetch is still not permission to redistribute —
ADR-0010 §5 and §6 keep republication behind a separate per-source licensing
basis that no command here can satisfy.

## Observatory: discovering what a source publishes

Discovery reads the documents an authority publishes for exactly that purpose
— sitemaps, sitemap indexes, Atom and RSS — and reports the URLs worth
observing. It **proposes; it never captures a candidate**. That separation is
what keeps a sitemap of 40,000 entries from turning one command into a mass
download.

```bash
uv run lovspor observatory discover --id 3201
```

With no `--entry-point`, it starts from the sitemaps the source declares in
the very `robots.txt` the reviewer checked when the source was activated —
that URL is in the access-policy record, so nothing guesses a host. Pass
`--entry-point URL` (repeatable) to start somewhere else.

`robots.txt` is read with the engine's own matcher
(`src/lovspor/observatory/robots.py`), not the interpreter's: RFC 9309
semantics — the most specific matching rule decides (the rule with the most
octets, not the text a wildcard swallowed), `Allow` wins a tie, `*` and a
trailing `$` are wildcards, every group naming the crawler's product token is
combined into one rule set, with the `*` groups as the fallback. `urllib.robotparser` resolved a conflict by file
order up to Python 3.13 and by longest match from 3.14, so `Allow: /` followed
by narrower `Disallow` lines was honoured on one interpreter and ignored on
another (issue #351). What the crawler refuses is now a property of the code.

Every document discovery reads goes through the same gates as any other fetch
— activation, live `robots.txt`, the per-source rate limit — and is recorded
in the log. That is deliberate: what a municipality listed on a given day is
evidence, and it is what makes a later "this page appeared between these two
observations" claim checkable. Expect `verify` to report more records after a
discovery run.

Nothing is dropped silently. A link on another host, an unusable scheme, a
document past the depth or budget bound comes back under `skipped:` with its
reason, and the listing is never truncated — a partial list would read as
"this is what the source publishes", which is the one claim the observatory
must not make loosely.

### Reading a registered listing page (issues #151, #514)

A source with no sitemap or feed is read from an overview page a reviewer declared
as one of its `listing_entry_points`. Only a declared URL is read this way: an HTML
page served under a sitemap URL is still refused as `unparseable_document`. The page
decides how it is read, every time it is fetched — there is no per-page mode to set:

- **Dated list** — at least one entry carries a `<time datetime>`, as a hearings or
  kunngjøringer archive does. Each dated entry's link is proposed with that date as its
  `lastmod`; links without one are counted under `listing_entries_without_date: N`.
  A date belongs to one entry: a `<li>`, `<tr>` or `<article>` whose links lead to
  more than one URL is a container, and its `<time>` dates none of them. That is the
  CMS that wraps the whole body in one `<article>` with the page's "last updated"
  date, which used to stamp every link — share buttons included — with it.
- **Undated overview** — no dated entry at all, the shape of "Lokale forskrifter",
  "Reglement og vedtekter" and "Styrende dokumenter" pages. Every link in the page's
  content region is proposed, pages and linked documents alike (PDF, DOCX, ODT, and
  `/download/...` links with no suffix), with no `lastmod`. The region is `<main>`,
  else `<body>`, narrowed to the page's outermost `<article>` elements when it marks
  any that hold links. Dropped: `header`, `footer` and the `banner`/`contentinfo`/
  `search` landmarks; `nav` and `aside` only when the page has no `<main>` (inside
  one, a `nav` can be the overview itself); the page itself and its ancestors (the
  breadcrumb trail); fragments, `mailto:`, `tel:`; search, login, cookie and share
  links; images, CSS and scripts. The reading is always noted as
  `listing_undated_links_not_proposed: N`, so the log says which way a page was read.

An off-domain link (Lovdata, a delegation register, a share button) is proposed and
then refused by the same domain guard every candidate passes, as `off_source_host` —
the host is judged in one place only. A page where neither reading finds a link is
`unparseable_listing`: usually a page assembled in the browser, which the reader does
not run.

**Undated proposals are not re-fetched every night.** With no `lastmod` to decline
work with, freshness judges an undated URL on its own record: left alone for 24 hours
after it last served content, doubling for every byte-identical re-capture up to a
week (issues #209, #415); a URL that only ever fails backs off on its failures (#204).
The listing page itself is re-read on every discovery run — it is the entry point.

Measured offline on the 29 listing pages found on 2026-10-03 for #431: before this
reader, 20 of the 25 prepared for registration were refused and the other 5 parsed
only because one page-level date stamped every link. After it, all 29 are read as
undated overviews; 25 propose on-domain links, and 4 link only off-domain (Lovdata,
an external document system), which the guard refuses — a fact about those pages,
not about the reader. The one real dated archive in that crawl (a hearings page, 110
entries, 110 dates) is read exactly as before. The per-page counts are in the pull
request that fixed #514.

## Observatory: capturing what discovery found

```bash
uv run lovspor observatory capture --id 3201
uv run lovspor observatory capture --id 3201 --limit 50   # bound a first pass
```

Discovery runs first every time, so the candidate list is what the source
publishes now rather than one cached from an earlier day. Then each candidate
is fetched under the source's own rate limit — a full first pass over a
municipal site is hours, and the per-URL line is how you tell a slow run from
a stuck one.

**An observation belongs to the URL that was asked for.** When a redirect is
followed inside the cleared domain, the artifact is still recorded under the
requested URL, and the hops it went through are in
`provenance.redirect_chain`. Filing it under the destination instead — which
is what happened until issue #211 — meant the requested URL never appeared in
the log at all: freshness keys on it, so it was re-fetched on every pass
forever, and the archive answered "what did this URL serve?" with an
observation of somewhere else. One municipality had eight URLs redirecting to
its login page and 1,796 recorded fetches of that page, none of the eight.

**A candidate with a `lastmod` is skipped only when that stamp predates an
observation already in the log.** Everything less certain is fetched: an
unreadable stamp, a URL never seen, a timestamp that ties exactly.
Re-fetching costs one request; skipping wrongly costs an observation window
that cannot be recovered.

**A candidate the site says nothing about is skipped for 24 hours after it was
last seen** (`UNDATED_RECHECK`, issue #209). This is the one judgement the
freshness rule makes from our own record rather than the site's claim, and it
had to become one: discovery proposes plenty of undated candidates — a link
found inside a page carries no `lastmod` even when the sitemap it came from
stamps every entry — and each was fetched on every pass. A crawl could not
reach `captured: 0`, so it never finished. It also never got past them: the
`--limit` was spent on the same head of the candidate list every round, and
one municipality had 214 of its 274 URLs unseen for two days while 58 were
re-downloaded. The trade is that an undated page changing twice a day is
caught a day late.

**A URL that has never yielded content is judged on how it has been failing**
(`FAILED_RECHECK`, issue #204). Both rules above key on a sighting, so a URL
that only ever failed was proposed, fetched, failed and proposed again, every
pass, forever: 962 URLs accounted for 39,672 requests that came back with
nothing, and one municipality's own 404 page was asked for 154 times. Only
failures that describe the *URL* count — a 4xx, a redirect we will not follow,
a body over the cap, a path `robots.txt` denies. A timeout, a 5xx, a 429 and
anything unrecognised describe the *moment* and count for nothing, so a bad
night cannot hide a page. The wait starts at 24 hours, doubles with the run of
identical failures, and is capped at 48 hours; a `lastmod` later than the
failure overrides it outright, because the site saying the URL changed is a
stronger statement than our record of having been refused. Deferred candidates
are counted separately from unchanged ones in the run summary and in
`sweep-runs.jsonl` — a source whose candidate list has quietly become a list of
dead ends must not read as a clean pass that found nothing to do.

The category is deliberately not a range of status codes. Once issues #210 and
#212 had removed two unrelated re-fetch loops, a clean fleet sample (8 lanes,
41 minutes, engine `33a9fe4`) showed `redirect_not_followed` to be the largest
deterministic bucket by a factor of four — 100 URLs asked 268 times — and it
carries a 301 or a 302, which read as perfectly ordinary responses.

That rule is also what makes an interrupted run need no resuming. Each
observation is appended as it happens, so running the command again picks up
where it stopped — the pages already captured now fail the freshness test. And
a second pass over a site that has not changed costs two requests rather than
thousands.

A damaged log is refused before anything is fetched: appending thousands of
records would bury the damage. Run `observatory verify` first.

### Selecting what to capture by path (issue #348)

On a whole-site crawl of 201 municipalities, 95% of distinct URLs name no
regulation in their path, and that share is crawl budget: a source rate-limited
at 30 s a request spends most of a night on `/nyhetsarkiv/…` and
`/kultur-idrett-fritid/lag-og-foreninger/…`. Selection sits between discovery's
proposals and capture's fetch, and keeps only candidates whose percent-decoded,
lowercased path contains one of 48 stems (`REGULATION_PATH_STEMS` in
`src/lovspor/observatory/selection.py`), plus every linked document.

- **The stems.** The owner's set of 2026-09-19 (`forskrift`, `reglement`,
  `vedtekt`, the `kunngjør`/`kunngjering` and `høring`/`hoyring` spellings, and
  the rest), widened on 2026-10-03 (#507) after the classification study found
  it rejecting the URLs of 61 of 117 labelled enacted regulations and about 40 of
  ~153 distinct forskrifter. The 25 additions were read off those rejected URLs —
  school rules (`ordensregl`, `regler-for`), fees (`gebyr`, `betalingssats`),
  councillors' pay (`godtgjor`, `for-folkevalgte`), school permission, routes,
  districts and SFO (`permisjon`, `skolerute`, `skolekrets`,
  `skolefritidsordning`, `/sfo`), leash orders (`bandtvang`), alcohol sale hours
  — with their nynorsk forms; three narrow stems were widened to cover both
  languages (`ordensregl`, `retningslin`, `bestemmels`).
- **Every linked document** (owner decision 2026-10-03, #507): a path ending in
  `.pdf`, `.docx`, `.doc` or `.odt`, and any URL whose latest capture was a PDF
  or word-processor file, is selected whatever its path names. Linked PDFs are
  the richest regulation source the study found (85 of the ~153 forskrifter also
  exist as PDF/DOCX), and 2,732 of the archive's 4,365 PDF URLs carry no suffix,
  so the content type the capture state already holds is what finds them. A
  document never yet captured on a suffixless path naming no stem is not seen
  until it is fetched some other way.

Measured on 2026-10-03 against the 144,842 URLs in the archive (labels from
`~/lovspor-ops/classification-2026-10-03/`):

| | 2026-09-19 stems | widened (#507) |
|---|--:|--:|
| labelled enacted regulations selected | 56 of 117 | 79 of 117 |
| labelled adopted rule sets (§ 14 vedtak) | 70 of 84 | 80 of 84 |
| R1.1-positive URLs | 135 of 286 | 202 of 286 |
| municipalities with an R1.1 positive but none selected | 11 of 78 | 0 of 78 |
| selected share of all URLs | 6.6% | 12.4% |

The first selecting nightly (2026-10-03) selected 8,530 of 136,300 candidates
(6.3%); the widened rule should roughly double that. What is still rejected is
known and accepted:

- **Kongsvinger's CMS sidebar**: all 38 remaining enacted-regulation misses, 2 of
  the 4 adopted-rule misses and 81 of the 84 R1.1 misses. News and school pages
  under `/barnehage-skole-utdanning/` repeat the skoleregler forskrift, or the
  barnehage vedtekter, in a sidebar. The canonical pages
  (`…/hvilke-ordensregler-gjelder-for-grunnskolen-i-kongsvinger`,
  `…/vedtekter-for-de-kommunale-barnehagene-i-kongsvinger`) are selected.
- **Single pages on paths naming no regulation** — Fredrikstad's and Tingvoll's
  grant schemes (`tilskudd…`), Averøy's `planer-og-rapporter`, Inderøy's
  `brenning-av-avfall`, Steigen's `kommunal-bolig`. A stem broad enough to reach
  them (`tilskudd`, `plan`) would select a large share of every site.

- **Global, behind a flag, off by default** (owner decision 2026-09-26). It is on
  only when the process environment has `LOVSPOR_OBSERVATORY_CAPTURE_SELECTION=1`,
  for `capture`, `capture-all` and `nightly` alike; any other value is off. There is
  no per-source switch.
- **Off is a measurement.** Each pass prints what selection would keep, read off the
  same proposals, so one nightly run with the flag off sizes the cut before it is
  switched on (numbers here are illustrative, not measured):

  ```
  candidates: 1904
  selection off: 61 of 1904 candidates would be selected
  ```

  With the flag on, the same pass reads:

  ```
  candidates: 1904
  selected: 61 of 1904 candidates (1843 not selected: path names no regulation)
  ...
  captured: 3 | failed: 0 | unchanged since last seen: 58 | deferred after repeated failure: 0 | redirect hops: 0 | not selected by path: 1843
  ```

  The new counter is appended to the summary line, so a script matching its prefix
  is unaffected. A sweep with selection on also prints
  `candidates not selected by path: N (selection on)` at the end.
- **Discovery is untouched.** Every sitemap, sitemap index and listing is still read
  and archived, whatever its own URL says; only the pages capture would fetch are
  selected. That is what keeps "what did we decline to fetch" answerable: the
  proposals are in the archived discovery documents, the run record carries
  `capture_selection` and `unselected`, and `engine_commit` names the stem list.
- **A page a registered listing links to is always fetched**, whatever its path —
  a listing is a page a reviewer declared, so it is not a guess. That holds when a
  sitemap proposed the same page first.
- **Already-captured pages on unselected paths stop being observed.** Their records
  stay in the archive untouched; nothing is re-crawled or re-rendered. They are
  counted as not selected, never as unchanged or deferred.
- **Not a classifier.** ADR-0010's deferral holds: this scopes budget by what a path
  *names* and what a URL last served, never by what a document says. A regulation at
  `/tjenester/vann-og-avlop/` will not be fetched, a known, accepted miss, and a
  selected page can still be an empty JS shell (#332).
- **The freshness index rebuilds once.** Remembering what a URL last served bumped
  the index's derivation version to 4, so the first run after deploying #507 folds
  the whole log again instead of its tail.

To switch it on for the scheduled job, add the variable to the installed plist's
`EnvironmentVariables` and reload the agent (see "Installing the job"):

```xml
<key>LOVSPOR_OBSERVATORY_CAPTURE_SELECTION</key>
<string>1</string>
```

`observatory status` then shows `selection:  on — N candidates not selected by
path` for the last run.

### One domain, one authority

**Two activated sources on one domain make capture refuse, on both.** The archive's
claim is that specific bytes came from a named authority (ADR-0010 §3), and a register
that lists two authorities for a host cannot support it. `authorise_capture()` used to
resolve that by sorting on authority id and returning the first, silently.

It happened. `4202 Grimstad` carried `arendal.kommune.no` — Arendal's domain, not its
own — so the lower id won every request: **5,980 observations of Arendal's site were
filed under Grimstad, while Grimstad itself was never fetched once** and every pass over
it reported success (issue #215). The register said 201 registered, 201 active, and
nothing anywhere disagreed.

Three things now hold that shut:

- the gate refuses a contested host and names both claimants, so a sweep records the
  source as refused and degrades — audible, and it costs the other two hundred
  municipalities nothing. Both claimants refuse, not just the newcomer: refusing one
  would keep filing the domain under whichever row sorts first, which is the bug.
- `register-source` and `replace-source-domain` refuse a domain another source claims.
- `observatory status` names any contested domain, because nothing else reads the
  register as a whole and a sweep meets the state one source at a time.

The check is on the write paths and not on registry load, deliberately. A load-time
refusal would be stricter and would also be a trap: `replace-source-domain` is the only
supported way out of the state and it has to read the register first, so a register that
refuses to load is one nobody can repair through an interface this engine offers.

Repairing an existing collision is a review, not an edit — the clearance was obtained for
the wrong host:

```bash
uv run lovspor observatory replace-source-domain --id 4202 \
  --domain grimstad.kommune.no --reason "..." --by "<reviewer>"
uv run lovspor observatory activate-source --id 4202 --check <fresh-check>.json
```

Records already written under the wrong authority are a separate question: the log is
append-only and its history is not rewritten.

### A repair reaches the run that is already going

**The register is read again before every source, and every fetch is checked
against the file.** A sweep used to load it once and hold it for the whole pass.
That pass is not a moment: the run of 2026-09-03 took 141 hours.

It happened, and it is the tail of #215. The 4202 repair above was made at
07:20:44 on 2026-09-03, into a sweep that was already running —
`source-events.jsonl` carries the `source_domain_replaced`, and
`verdicts/4202-grimstad-check.json` sits beside it. The sweep never looked at
the file again: **669 further observations of `arendal.kommune.no` were filed
under authority 4202 after the repair**, taking the misattributed body from
5,980 to 7,872 (issue #221). `observatory status` showed no collision the whole
time, because by then there was none.

What holds it shut now:

- **Between sources**, the register is read again. A source deactivated while
  an earlier one was being swept is never asked; a source whose domain changed
  is swept under the domain it has now.
- **Inside a source**, each fetch is authorised against the file. If the row the
  lane bound itself to is no longer the row on disk, the lane is abandoned: the
  source is counted as refused, the sweep degrades, and the records already
  written stay. It fails closed rather than re-filing the rest of the lane
  under whatever the register says now — that would be a second guess about who
  publishes those pages, made by a process minutes after an operator made the
  first one by hand. A record not written can be captured again on the next
  pass; a misattributed one cannot be rewritten — nothing in this engine
  rewrites `authority_id`. It can only be corrected by appending, with
  `observatory reattribute` (ADR-0015, below).
- **The scope of a run** is the ids the register held when it began. A source
  activated mid-run waits for the next sweep.
- **A withdrawal is counted**, as `sources_withdrawn` in the run record and on
  the `status` report. It does not degrade the sweep — the operator asked for
  it — but a source that quietly stopped being swept would read as an archive
  with nothing missing.

Nothing about a binding is persisted, so a sweep killed mid-run needs no
resuming: the next invocation reads the register from scratch.

### Reading the archive's failure rate: `observatory composition`

**A followed redirect is recorded as `fetch_failure`, and it is not a
failure.** The hop returned no bytes, which is what the type means, but the
fetcher went straight on to ask the target and the document arrives under its
own record. On the archive as it stands that is 316,109 of 416,532
`fetch_failure` records — three quarters — so counting the `kind` field
reports a **49.6% failure rate against a real 12.3%** (issue #188).

The engine never read it that way. `Fetcher.capture()` returns only the
terminal result, so `failed:` in a round summary, `failed_fetches` in
`sweep-runs.jsonl` and everything `observatory status` prints have always
counted the honest number. What had no honest reading was the archive itself,
which is what an auditor — or a derived layer built on it later — actually
opens.

```
uv run lovspor observatory composition
```

One streamed pass over the log, reporting artifacts, redirect hops, lost
documents and tombstones, both rates side by side, and every failure outcome
by count. The overstated figure is printed next to the real one on purpose:
somebody has already quoted it, and a report that silently replaced it would
leave them unable to tell which number they had.

The rule is `outcomes.lost_the_document()` — everything except a followed
redirect — and it is code rather than a convention in this file precisely so
that no reader has to have read this paragraph. It applies to every record
already written: the alternative options for #188 were a new `kind` or a
renamed type, and because the log is append-only and its history is never
rewritten, either would have left the 316,109 existing hops under the old
vocabulary and split the archive at a commit boundary.

A round summary now also reports `redirect hops: N`. The hops were never
counted as failures there, but they were not mentioned either, and the pass
that stops calling them failures must not be the pass that stops mentioning
them.

### Which blobs carry a document: `observatory document-report`

An `artifact` record says bytes were retrievable; it does not say they hold a
document. On 2026-09-16 the regulation-shaped HTML blobs were opened for the
first time: the median `<main>` held 199 characters, 54% held under 300, and
25% contained a `§` — most were a JavaScript shell (issue #332).

```
uv run lovspor observatory document-report
```

Offline and read-only: one pass over the corrected log, reading each distinct
blob once per source. Per source it prints the HTML blob count, the median
visible `<main>` text length (whitespace collapsed; `<body>` when a page has no
`<main>`, counted under `no-main`), how many fall under 300 characters, how
many contain a `§`, and `docs/html` — blobs clearing both bars. PDFs are
counted and never measured: the engine ships no PDF text extractor. A blob
missing from disk is counted under `missing`. A damaged log is refused.

Both measures are proxies for "carries a document", not for "is a forskrift";
ADR-0010 defers classification. The observation schema is unchanged — recording
the same measurements on new captures is a separate, later step.

### Which sources share a server: `observatory addresses`

The politeness budget is a promise to whoever operates a machine, but the
fetcher enforces it per *host*. Two municipalities on one server therefore hold
two independent budgets and the sweep hits that machine at twice the rate it
believes it is holding to — `grimstad.kommune.no` and `arendal.kommune.no` are
one such pair (issue #277).

This command does not change the keying. It answers the question that comes
first: how much of the register actually shares an address today.

```bash
uv run lovspor observatory addresses
```

Read it before deciding what the budget should key on. Keying on the resolved
address is an improvement for a pair on one box and a disaster for a vendor
platform: Norwegian municipalities sit behind shared suppliers (#194 counts
twelve on ACOS alone), and one budget per address would collapse a whole
population into a single queue and turn a 20-hour sweep into a week of them.
The group sizes are the finding — a group of two and a group of twelve are the
same count and call for opposite decisions.

The hosts resolved for a source are the ones the register implies: the
canonical domain, its `www.` form, and the host of every declared listing entry
point. Discovery can still reach another subdomain inside the cleared domain
and this cannot see those — the register does not record them.

A host that does not resolve is reported with its reason rather than raised
past, so one retired domain does not end the pass. The command reports and
exits 0 either way: a shared address is a fact about the register, not a defect
in it.

### What a capture costs the machine it runs on

Before it fetches anything, `capture` reads the observation log once to learn
when each of this source's URLs was last seen. That reading is a stream and
the freshness map is narrowed to the source being captured, so the memory it
needs is the size of the answer rather than the size of the archive — 70 MB
against a 390 MB log holding 610,850 records (issue #199). The parse itself
still walks every line, because the completeness guarantee depends on it:
budget a few seconds per invocation, growing with the archive.

That per-invocation cost is what decides how many captures may run at once.
Bounding a pass with `--limit` and looping pays it again on every round, so a
lane that captures in batches of 100 re-reads the log every 100 fetches.
Before this was measured, twenty-four parallel lanes on a 16 GB machine asked
for roughly 51 GB between them and completed no rounds at all in thirty-four
minutes — every lane sat in swap instead of fetching. Prefer few lanes and
`ROUNDS_MAX=0`, and remember that a lane spends nearly all of its wall clock
waiting out `rate_limit_seconds`, not working.

## Observatory: after an interrupted run

The archive lives on storage that can go away mid-write — an external disk, a
machine that lost power. Three things can happen, and only one of them needs
you.

**A blob written but its record lost.** `verify_snapshot()` reports it as an
orphan blob. Harmless: a re-run fetches again and appends the record, and the
orphan is unreferenced bytes. No action.

**A record cut off mid-write.** Records are fsynced on append, so this needs
the disk or the machine to disappear inside a single write — but it is still
the failure this archive is exposed to. The audit reports it rather than
raising:

```bash
uv run lovspor observatory verify
```

It exits non-zero when the snapshot does not hold together, so a scheduled run
can act on it. "The final record was never finished" is the crash signature:
everything before the last line is intact, and the unfinished line is a fetch
that was never recorded. Recovery is to drop that one line:

```bash
uv run lovspor observatory repair            # reports what it would remove
uv run lovspor observatory repair --apply    # removes it, keeping the original
```

`repair` writes nothing without `--apply` — this edits evidence, so it takes
two deliberate steps, and a dry run has to be possible on a machine where the
answer turns out to be "do not touch this". With `--apply` it copies the log to
`observations.jsonl.bak` before truncating, and refuses if that backup already
exists rather than clobbering the evidence an earlier repair kept.

It also refuses any damage that is *not* an unfinished append. That is the
point of it: only that one kind is safe to fix by deleting.

**A corrupted line anywhere else** — the audit names the line numbers and says
`Do not truncate` — is not a crash. An interrupted append can only ever damage the last line, so damage
elsewhere means the storage itself is failing. Restore from backup and check
the disk; `repair` refuses this case for you, but the judgement is still
yours.

While the log cannot be read to the end, blob findings are suppressed rather
than reported — every blob the unread lines account for would otherwise show
up as an orphan, burying the real defect under invented ones. Re-run the audit
after recovery to get the full picture.

## Observatory: correcting a misattributed record (ADR-0015)

The log is append-only, so a record filed under the wrong authority is never
edited. It is **corrected by appending** two records per original: a
`refiled_observation` — the same observation, same `observed_at`, URL, hash
and retrieval provenance, under the right `authority_id`, with who, when and
why — and then a `record_tombstone` retracting the original by its key (the
SHA-256 of its line as stored). The original line and its blob stay exactly as
they are. Every fold of the log (capture state, the freshness index,
`composition`) reads the corrected view; `verify` reads the raw log and audits
the corrections.

Only `authority_id` is correctable, and only when the register already says
where the records belong: fix the register first (`replace-source-domain`,
`activate-source`), then correct the archive.

**Before the first correction**, move the nightly pin to an engine that reads
correction records. An older engine refuses a log holding one (the record
models forbid unknown kinds), so the first night after an `--apply` on an old
pin fails its preflight. The first run of the new engine also rebuilds the
freshness index once (its derivation version moved to 3).

The decision is a JSON document, so the dry run and the apply read the same
reviewed decision, and you keep it beside the output:

```bash
cat > correction-4202-4203.json <<'JSON'
{
  "from_authority": "4202",
  "to_authority": "4203",
  "host": "www.arendal.kommune.no",
  "reason": "Captured under Grimstad (4202) while its register row carried Arendal's domain, before replace-source-domain on 2026-09-03T07:20:44Z; the records belong to Arendal (4203). lovspor#221, lovspor#276, ADR-0015.",
  "corrected_by": "<your name>"
}
JSON
uv run lovspor observatory reattribute --correction correction-4202-4203.json          # dry run
uv run lovspor observatory reattribute --correction correction-4202-4203.json --apply  # appends
uv run lovspor observatory verify
uv run lovspor observatory composition
```

The dry run writes nothing — not the log, not the index, not the host lock —
and prints the selection (every `artifact` and `fetch_failure` filed under
`from_authority` whose URL host is `host`), by kind, with the first and last
`observed_at`, what is already corrected, what a crash left half-written, how
many lines `--apply` would append and the first line of each kind. The
correction id it prints is a preview; `--apply` mints its own.

It refuses — writing nothing — when `reason` or `corrected_by` is missing or
blank (neither is ever filled in by the engine), when `to_authority` is not
registered on a domain covering `host`, when `from_authority` is still
registered on one that does, when the log is damaged, and when any selected
record carries a half-written correction this decision did not write — a
record tombstone without its re-filed half, halves that disagree, a second
correction, or a move somewhere else. Those are not resumed: run
`observatory verify` and find out who wrote them. `--apply` also refuses if a
correction landed or the log got shorter between planning and appending, and
while a sweep holds the host's workload lock; run it between nights,
or after `observatory status` shows no sweep running.

**Idempotent and crash-safe.** Each record is appended through the log's one
write path (locked, fsynced), re-filed half first. A run interrupted between
the halves leaves the original standing — no fold sees the half — and `verify`
reports an incomplete correction, which keeps the nightly preflight red until
you run the same command again — the same decision file: it appends only the
missing tombstone, under the interrupted run's own attribution. A different
reason or author is a different decision and is refused. A finished correction re-run appends
nothing ("already corrected: N", "appended 0 lines").

A wrong correction is not undone. It is corrected in turn, by correcting its
re-filed record; `reattribute` itself only selects original observations.

## Promoting a local regulation (ADR-0016)

`lovspor promote` puts **one** archived artifact — an enacted local regulation
a human has read — into the `lokale-forskrifter/` dataset of a `lovverk`
checkout (ADR-0016 slice S3). It is the only writer of that directory. It never
touches the root `manifest.json`, `lover/` or `forskrifter/`, and it never
commits: committing is your step, made at promotion time.

Outside that directory it writes one thing: the generated corpus-status block
of `lovverk`'s `README.md`, between `<!-- lovspor:corpus-status:start -->` and
`<!-- lovspor:corpus-status:end -->` (#661). Every command that leaves
something to commit re-renders the block from both manifests — counts per
dataset, the local regulations per authority, each manifest's own
`generated_at` date — prints `wrote README.md` when it changed, and names
`README.md` in the `git add` it prints. `lovspor sync` does the same in its
final commit. The prose outside the markers is never touched; a README
without the block gets it before its first `##` heading, and a README whose
markers are broken is left alone with a warning on stderr. The README is
neither dataset, so the `lovverk` dataset-separation check is unaffected.

What you need:

* `LOVSPOR_OBSERVATORY_ROOT` — the archive. It is read through the
  observatory's own readers (the corrected view, ADR-0015) and never written,
  except for the decision log `promotions.jsonl`, which lives beside
  `observations.jsonl` there and nowhere in this repository (ADR-0010 §5).
* `--corpus` — an absolute path to a separate `lovverk` checkout that carries
  the S0 skeleton (`lokale-forskrifter/manifest.json`). A path inside this
  repository or inside the archive, a relative path, or a directory without
  `.git` or either manifest is refused.
* `--authority` — the KLASS code as the source register holds it; the
  authority's name and type are read from the register, never typed.
* `--klass-version` — the SSB KLASS vintage that code is read under. It is
  written into the document's `authority` block; the register does not carry
  it, so you state it.
* `--artifact` — the SHA-256 of the archived bytes, or the URL that served
  them. A hash served at two URLs, or a URL that served two texts, is refused
  with the candidates listed; name the other one.

### 1. Preview — what the reviewer reads

```bash
export LOVSPOR_OBSERVATORY_ROOT=/Volumes/T7/lovspor-observatory
uv run lovspor promote preview --authority 0301 --artifact <sha256-or-url> \
  --corpus /absolute/path/to/lovverk --klass-version <klass-vintage>
```

It runs the whole pipeline — extractor (S2), personal-data gate, identity
(S1), placement, renderer — and prints the id, version, path, content hash and
the full Markdown. It writes nothing and records nothing. A hold prints its
stage and reason and exits 3. (The ADR sketches this as `promote local
--dry-run`; it is its own command because a preview and a write are different
acts, and `local` already takes four options.)

### 2. Approve — the human decision

The owner reviews 100 % of the first promotion (owner decision on ADR-0016,
lovspor-notebook #141). The decision is a JSON document, so what was decided
stays re-readable:

```bash
cat > decision-0301.json <<'JSON'
{
  "decision": "approve",
  "decided_by": "<your name>",
  "reviewer_role": "project owner",
  "reason": "Enacted forskrift; text read against the source page in full.",
  "classifier": {"classifier_version": "<version>", "class_name": "forskrift", "evidence": ["<rule id or phrase>"]}
}
JSON
uv run lovspor promote approve --authority 0301 --artifact <sha256-or-url> --decision decision-0301.json
```

`decision` is `approve`, `reject` or `hold`; `classifier` is optional (leave
it out when no classifier output was reviewed). `decided_by` must be a person
— `classifier`, `lovspor` and similar names are refused.

`reviewer_role` is required: `lovverk` is public and its history is never
rewritten, so the audit record published there names the reviewer by **role**
(`reviewed_by_role`), never by name (owner decision on PR #517). The name in
`decided_by` is written only to `promotions.jsonl` in the archive root. A
document without a role is refused (`reviewer_role is required: the published
audit names the reviewer by role, never by name`), and so is a role that is a
machine name or carries personal data. Everything published — the role, the
`reason` and the classifier evidence — is refused when it carries personal
data (an e-mail address, a phone number) or any word of two letters or more
from `decided_by`.

An `approve` is bound to the text it was given for — its content hash and
extractor version — and a text the pipeline holds cannot be approved. The last decision on an artifact is the one that
stands; a later `reject` keeps it out.

The record appended to `promotions.jsonl`:

```json
{"kind": "decision", "artifact": {"authority_id": "0301", "sha256": "…", "source_url": "https://…"},
 "decision": "approve", "decided_by": "…", "reviewer_role": "project owner",
 "decided_at": "2026-10-03T09:00:00Z", "reason": "…",
 "content_hash": "…", "extractor_version": 1, "classifier": null}
```

### 3. Promote — write one version

```bash
uv run lovspor promote local --authority 0301 --artifact <sha256-or-url> \
  --corpus /absolute/path/to/lovverk --klass-version <klass-vintage>
```

It writes, under `lokale-forskrifter/` only:

* `<authority_id>/<slug>.md` — the rendering, with ADR-0016's front matter
  (`observed_at_first` is the first observation of this content; there is no
  `retrieved_at` and no `observed_at_last`; `vedtatt`, `ikraft` and
  `ikraft_text` are the evidence sidecar's resolved value, `null` when its
  statements disagree and the sidecar holds the date);
* `<authority_id>/observations/<slug>.json` — per version: content hash,
  first and last observation, observation count, primary and corroborating
  URLs, source blob, and `promotion`, the audit record: decision, the
  reviewer's role (`reviewed_by_role` — never the name), decision time,
  reason, `reviewed_in_sample`, classifier evidence (or `null`), extractor/renderer versions, source form, identity (scheme, id,
  ref-id, candidates), and the archive records it was read from; after a
  migration that carried the approval, also `approval_carried` (see
  "Migrating after an extractor bump");
* `<authority_id>/evidence/<slug>.json` — what this version's text states,
  verbatim (ADR-0016 S10): `relations` (`hjemmel`, `amends`, `repeals`,
  `amended_by`, `repealed_by`), each with the sentence that states it and
  either the one corpus document its target names (`target`: `doc_id`,
  `dataset`, `address`) or `target: null` with `unresolved` (`no_number`,
  `not_in_corpus`, `ambiguous_name`, `self_reference`); and `vedtatt` /
  `ikraft`, every statement of each date with its words and a `status` —
  `stated`, `text`, `absent`, or `held` (`conflicting_statements`,
  `placeholder_date`) with `value: null`. It is machine-read
  (`basis: "source_explicit"`, `reviewed: false`) and a sidecar: the Markdown,
  its `content_hash` and the manifest record do not depend on it, so no
  approval goes stale when it is written. Targets resolve against the corpus
  as it stands at promotion;
* `manifest.json` — the record keyed by id (`generated_at` is the decision
  time, not the clock).

The observations read are those **up to the decision time**, so a capture made
after the approval changes nothing. It then appends a `promoted` record (with
the same audit) to `promotions.jsonl` and prints the commit to make.

Refused, with nothing written: no decision recorded, a standing `reject` or
`hold`, an approval for another text, a withdrawn id or artifact (step 6), an id filed under
another authority, content first observed before the current version (the
backfill slice orders that), any output that would mention NLOD.

**Holds** — an extraction hold (unreadable, empty, garbled, a Lovdata print,
a placeholder date, no body or title), a personal-data hit, or an identity
hold (no `lf-` id and no vedtaksdato; an `lf-` id a central `sf-` record
already carries) — write nothing to the corpus, print the stage and reason,
append a `held` record to `promotions.jsonl` (personal data by kind and line,
never the value) and exit 3.

**Idempotent.** A rerun on a version the corpus already holds prints
`Unchanged`, writes nothing and appends nothing; a repeated hold is recorded
once.

### 4. Commit, at promotion time

```bash
git -C /absolute/path/to/lovverk add -- lokale-forskrifter README.md
git -C /absolute/path/to/lovverk commit -m 'promote(lokal-forskrift): 0301/<slug> v1'
```

Use the exact subject `promote local` printed. It is the history grammar of
ADR-0016 Decision 3: `promote(lokal-forskrift): <authority_id>/<slug> v<N>` is
`added` for v1 and `updated` after; `withdraw(lokal-forskrift): …` is
`removed`; `observe: …` is no event. Never backdate the commit to the
observation time: the commit date is the transaction axis (when the corpus
recorded it), `observed_at_first` is the observation axis, and the two are
never fused.

### 5. Derive history, and commit it

```bash
uv run lovspor promote history --corpus /absolute/path/to/lovverk
git -C /absolute/path/to/lovverk add -- lokale-forskrifter README.md
git -C /absolute/path/to/lovverk commit -m 'history(lokal-forskrift): derive history for 1 documents'
```

`history` reads the checkout's git log for every current local document and
writes `<authority_id>/history/<slug>.json` (JSON only — the central
`history/<slug>.md` carries Lovdata's licence in its front matter). It needs
the promotion commit to exist; before it, there is nothing to write. A rerun
on a committed history prints `History is current`.

### 6. Withdraw — undo a promotion forward (ADR-0016 4f, S9)

`lovverk` history is never rewritten, so a wrong promotion (misclassified,
wrong identity, personal data, a legal objection) is undone by a later
commit. The reviewer's decision is a JSON document, as for `approve`:

```bash
cat > withdrawal-0301.json <<'JSON'
{
  "decision": "withdraw",
  "removed_reason": "withdrawn_misclassified",
  "decided_by": "<your name>",
  "reviewer_role": "project owner",
  "reason": "The page is a høring, not an enacted forskrift."
}
JSON
uv run lovspor promote withdraw --authority 0301 --slug <slug> \
  --corpus /absolute/path/to/lovverk --decision withdrawal-0301.json
```

`removed_reason` is a closed set: `withdrawn_misclassified`,
`withdrawn_identity`, `withdrawn_personal_data`, `withdrawn_legal`. An archive
tombstone of a promoted source (ADR-0010 §7) is withdrawn under the reason the
tombstone states. The same rules as `approve` hold for `decided_by`,
`reviewer_role` and `reason`: a person, a role, and nothing published that
carries personal data or the reviewer's name.

The command first appends a `withdrawal` record to `promotions.jsonl` (the
document's id and slug, the reason, the reviewer's name and role, and every
archived artifact the document was promoted from), then, in the checkout:

* `manifest.json` — the record becomes `status: "removed"` with its
  `removed_reason` (`generated_at` is the decision time);
* `<authority_id>/<slug>.md` — deleted, so the MCP server no longer serves
  it (`get_law`, `get_section` and `search_laws` answer as for an unknown
  document);
* `<authority_id>/evidence/<slug>.json` — deleted with it: its spans are the
  same text;
* `<authority_id>/observations/<slug>.json` — kept, every version intact,
  with a `withdrawal` block (reason, `reviewed_by_role`, decision time — never
  the name);
* `<authority_id>/history/<slug>.json` — kept untouched.

It never commits; commit with the subject it prints,
`withdraw(lokal-forskrift): <authority_id>/<slug>` (a `removed` event in the
history grammar). A rerun on a withdrawn document prints `Unchanged`, writes
nothing and records nothing.

A withdrawal is permanent. `preview` and `local` refuse, before reading
anything, an artifact a withdrawal names or whose standing decision is
`reject`; and after extraction, any artifact whose id a withdrawal names — a
new approval does not bring it back, and neither does a fresh checkout.

Before pushing, run the corpus checks in the `lovverk` checkout:

```bash
python3 scripts/check_corpus_integrity.py
python3 scripts/check_dataset_separation.py origin/main HEAD
```

### Promoting a received document (ADR-0016 A1)

A text an authority sent in answer to an innsyn request (see "Innsyn requests
to every local authority") is promoted with `lovspor promote received`: the
same preview, approve and local steps, read from the innsyn workspace instead
of the archive. The decision log is still `promotions.jsonl` in the
observatory root; the authority block comes from the latest innsyn population
snapshot, so `--authority` is not asked for: the request names it.

```bash
export LOVSPOR_INNSYN_DIR=/absolute/path/to/innsyn-workspace
export LOVSPOR_OBSERVATORY_ROOT=/Volumes/T7/lovspor-observatory
uv run lovspor promote received preview --request 4646-20261005T074425Z --document <sha256> \
  --corpus /absolute/path/to/lovverk --klass-version <klass-vintage>
uv run lovspor promote received approve --request 4646-20261005T074425Z --document <sha256> \
  --decision decision-4646.json
uv run lovspor promote received local --request 4646-20261005T074425Z --document <sha256> \
  --corpus /absolute/path/to/lovverk --klass-version <klass-vintage>
```

`--document` is the SHA-256 `record-received` printed; the decision document
is the one step 2 describes. `local` writes the same four files as an
observed promotion, with `basis: "received"`, `received_at` and the request
in place of `observed_at_first` and `source_url`, and prints the commit to
make; a rerun prints `Unchanged`. The rules:

* Only a document classified `regulation_text` is read. A `vedtak`,
  `minutes`, `cover_letter` or `other` is innsyn material and is refused
  before anything is read or written.
* `--answers` is review evidence, kept in the innsyn block of the audit. It
  never names the document: the id is minted from the text alone.
* A scan without a text layer is held as `empty_text` (exit 3, recorded in the
  decision log) and cannot be approved. It is never OCR'd.
* A Lovdata print or a Norsk Lovtidend avd. II PDF is promoted with Lovdata's
  markup removed (owner decision A1-3): the metadata block, the contents list,
  amendment annotations, footnotes and print chrome; the id is minted from
  the `Dato` (or Lovtidend `Kunngjort`) line before removal. `preview` prints
  the form first (`received_form: lovdata_print | lovtidend |
  authority_original`) and the front matter records it. A print whose
  metadata block cannot be bounded, or with Lovdata's markup left after
  removal, is held as `lovdata_copy`. `vedtatt_av` is the issuer the text
  names (`Fastsatt ved kgl.res.`, `Fastsatt av Miljødirektoratet`), never
  assumed to be the kommunestyre (A1-4). The observed channel still holds
  every Lovdata copy.
* A Lovdata print or Lovtidend text is split by the print's own structure
  (owner decision 2026-10-28 on #636, `EXTRACTOR_VERSION` 13): its first
  published line is the title Lovdata states, whatever it opens with
  (`Renovasjonsforskrift, Røros kommune …`, `Verneplan for Skardsfjella og
  Hyllingsdalen. Vedlegg 1. Forskrift om …`), kept verbatim; and the body
  opens at `§ 1` / `Kapittel 1` as before, else at the first bare roman
  numeral (`I`, `II.`) or the first `1.` point, as older verneforskrifter and
  kgl.res. decisions are set, else right after the lines that state the
  enactment (`Hjemmel:`, `Fastsatt …`, `Fremmet av …`, with a line a wrap
  continues). A print whose text is those lines alone is still held as
  `no_body`. The id of such a text is minted from Lovdata's own lines, so the
  block needs no anchor to be safe. An `authority_original` file and the
  observed channel keep the shared anchor rules (`§ 1` / `Kapittel 1`, a
  title naming a `forskrift`): a website page vouches for nothing, a print
  the authority sent in answer to an innsynskrav does. On Røros's 45
  documents this moved 22 holds to extracted texts (16 `no_body`, 6
  `no_title`) and left the 23 already extracted byte-identical; two stubs
  that name a phone number are now held as `personal_data` instead. Run
  `promote migrate` after deploying (the bump is a migration, ADR-0016 4e).
* Three enactment forms a Lovdata print uses are read since `EXTRACTOR_VERSION`
  14 (#637): `Fastsatt ved kgl.res. av 21. desember 1990` dates the enactment
  (`vedtatt`) and names the instrument as written (`kgl.res.`,
  `Kronprinsreg.res.`, never mapped to "Kongen i statsråd" — observed, not
  asserted; owner decision 2026-10-28); a case number between the organ and
  the date (`Fastsatt av Røros kommunestyre, sak 78/24, 29. august 2024`) is
  stepped over; and the marker of a removed `Ikke kunngjort i Norsk Lovtidend`
  footnote is taken off the year the PDF glued it to (`19841` → `1984`). On
  Røros's 45 documents this fills `vedtatt` for 17 kgl.res. texts and the
  organ for one; a date with an abbreviated month (`2. okt. 1981`) is still
  not read (the three written forms stand). Texts already promoted whose
  front matter gains a date are a new version, not a migration (`promote
  migrate` refuses them; approve and `promote backfill`).
* A repealed regulation is published, and says so (owner decision on #639):
  when a Lovdata print's block carries the `OPPHEVET` stamp the front matter
  adds `status: "repealed"` right after `ikraft_text`, and when its
  `Ikrafttredelse` is a range (`15.02.2020 - 31.03.2020`) it adds
  `ikraft_end` with the range's last day. Both are read from the block before
  removal; a source that states neither leaves both keys out, so nothing is
  ever said to be in force. `ikraft` stays the text's own statement, never
  the range's start.
* No file name, recipient mailbox or person's name is published. The
  reviewer is named by role, as for an observed promotion.
* A document whose versions were observed is refused: switching the primary
  channel is a recorded decision no command makes yet (A4). A withdrawn or
  rejected received document is refused, and `promote withdraw` withdraws
  one the same way as an observed one.

#### Re-rendering an unchanged received version (#645)

A received version promoted before the renderer learned a front-matter field
(`status: "repealed"`, #639) keeps its old rendering: its `content_hash` has
not moved, so `local` prints `Unchanged`, and `promote migrate` never rewrites
the Markdown (#643). `promote received refresh` re-renders every current
`basis: "received"` document of the checkout from the source its front matter
names (`innsyn_request`, `source_sha256`), under the `klass_version` it was
published with, and rewrites the Markdown where the bytes differ:

```bash
export LOVSPOR_INNSYN_DIR=/absolute/path/to/innsyn-workspace
export LOVSPOR_OBSERVATORY_ROOT=/Volumes/T7/lovspor-observatory
uv run lovspor promote received refresh --corpus /absolute/path/to/lovverk --dry-run
uv run lovspor promote received refresh --corpus /absolute/path/to/lovverk
```

* It is a re-render, never a new version: `version`, `content_hash`,
  `received_at`, the request, the source and its form stay as they are. A
  re-rendering whose body or any of those fields would change is refused
  (`Refused: <id>: …`, exit 1, the others still refreshed); a new text is
  promoted with `local`. One exception: a case number the authority gave
  after publication (`innsyn record-case-number`) fills an
  `innsyn_saksnummer: null`; a number already published is never changed or
  emptied (#720).
* It needs the same standing approval as `local`: the approval of exactly
  this text at the running extractor. A held, rejected or withdrawn document
  is refused. Read the `--dry-run` diff before approving again: when the
  only thing missing is that approval — the owner approved exactly this text
  at an earlier extractor and `migrate` could not carry it because the
  rendering changes — the dry run prints
  `<id>: needs re-approval at v<N>: …` and the diff the re-approval covers,
  and exits 1 (#719). The write still refuses it until the approval is given
  again; a document that would also move its body or a version or provenance
  field, or whose standing decision is not an approval of this text, is
  refused in the dry run as well, with no diff.
* A document that re-renders byte-identically is reported as `unchanged` and
  not written. Only the Markdown is written: the manifest, the observations
  file and the evidence sidecar describe the same version and are left alone,
  and nothing is recorded in the decision log.
* It never commits. It prints the commit to make,
  `migration(lokal-forskrift): re-render <authority>/<slug> front matter` (or
  `… re-render front matter of N received documents`), which the history
  derivation reads as no event: the law did not change, its rendering did.

`promote refresh` (#595) is the same re-render for **every** current document of
the checkout, observed or received: an observed document (`basis: "observed"`)
is re-read from the archived capture its front matter names (`source_url`,
`source_sha256`) and rendered with the provenance it was published with; a
received one goes through the path above, so without `LOVSPOR_INNSYN_DIR` it is
refused, not skipped, and the observed ones are still refreshed. The terms are
the same: the running extractor must read the same id and text, the standing
approval of exactly that text at the running extractor must exist (`promote
approve` for an observed document; `--dry-run` shows the diff of one that awaits
only its re-approval, as above), the body and every version or provenance
field must be byte-identical, only the Markdown is written, nothing is recorded,
and the commit it prints is the `re-render` subject the history derivation reads
as no event. This is the step before `promote migrate` after an extractor bump
that changed a front-matter field: `migrate` refuses a rendering that would
change, `refresh` rewrites it, then `migrate` moves the version metadata.

```bash
uv run lovspor promote refresh --corpus /absolute/path/to/lovverk --dry-run
uv run lovspor promote refresh --corpus /absolute/path/to/lovverk
```

### Backfill — every observed version, one commit each (ADR-0016 S6)

`promote local` writes the one version you approved. `promote backfill`
writes **every** version observed at the artifact's URL since the first
capture (2026-08-19), in observation order, one per run. A version is a run of
one text (`content_hash` of the extracted regulation) at that URL; bytes that
change without the text changing (a date stamp, a menu) are one version, and
a text that comes back after another (A→B→A) is a new version: v1, v2, v3.

```bash
uv run lovspor promote backfill-preview --authority 0301 --artifact <sha256> \
  --corpus /absolute/path/to/lovverk --klass-version <klass-vintage>
```

The preview prints each version with its first and last observation and
count, its standing approval or its hold, the observations excluded from the
comparison (a tombstoned blob, ADR-0010 §7 — listed, never bridged), the last
outcome at the URL (a 404 is `source_status`, never a repeal), holds counted
by reason, and the version the next run would write. It writes and records
nothing.

Each version needs its own approval: `promote approve` on one of its blobs
(the preview names them by observation), bound to that text and the running
extractor, given after the version was first observed — an approval of A
given before A came back does not cover v3. Holds, by reason:
`extraction:<reason>`, `rejected`, `held_by_reviewer`, `approval_stale`,
`not_approved`, `identity:<reason>`, `identity_changed` (the text is named
as another document), and `after_earlier_hold` for every version behind the
first hold — versions are numbered by observation, so none is written past a
hold.

```bash
uv run lovspor promote backfill --authority 0301 --artifact <sha256> \
  --corpus /absolute/path/to/lovverk --klass-version <klass-vintage>
```

A run writes the **next** version the checkout does not hold (the same three
files as `promote local`, with every version's interval re-read from the log),
records it in `promotions.jsonl`, and prints the commit to make, then the
ordered `backfill` / `git add` / `git commit` lines for every further approved
version. Run them in that order; each commit is dated when you make it. A
rerun on a finished backfill writes nothing; a checkout whose versions the log
does not reproduce (say, `promote local` put a later text in as v1) is
refused, never patched. The observations read are those up to the latest
approval among the versions written, so later captures change nothing until
the refresh.

### Observation refresh — weekly

```bash
uv run lovspor promote observe --corpus /absolute/path/to/lovverk [--authority 0301]
git -C /absolute/path/to/lovverk add -- lokale-forskrifter README.md
git -C /absolute/path/to/lovverk commit -m 'observe: refresh observation intervals (<n> documents)'
```

`observe` re-reads every current local document's version intervals from the
log — last observation, count, blobs, byte-identical copies, `source_status`,
excluded observations — and rewrites only the `observations/<slug>.json`
files that changed. It adds no version and touches no document or manifest; a
new text at the URL ends the current version's interval where the new text
begins and waits for a backfill. A document the log does not reproduce (another
text, another first observation, another extractor version — that is a
migration, `promote migrate` below) is skipped with the reason. Use the subject it prints: `observe: …`
is no history event. Run it at most weekly (ADR-0016 Decision 3); scheduling
it is not automated yet.

### Migrating after an extractor bump (ADR-0016 4e)

When `EXTRACTOR_VERSION` moves, a promoted document whose text the new
extractor reads byte-identically is not a new version: it is a `migration:`
commit. Until it is made, the document still records the old extractor, so
`observe` skips it ("that is a migration, not a refresh"), `backfill` refuses
to append to it, and `local` stops at `Unchanged`. `promote migrate` writes
that commit's files:

* `manifest.json` — the record's `extractor_version`;
* `<authority_id>/observations/<slug>.json` — `promotion.extractor_version`
  of every promoted version (the extractor the published bytes are current
  for) and, where the approval is carried, `promotion.approval_carried`;
  nothing else;
* `<authority_id>/evidence/<slug>.json` — only when the document has none
  (promoted before the sidecar existed); an existing sidecar is not touched.

The Markdown is **never** written. The command re-extracts the source the
published front matter names (`source_sha256` at `source_url`) with the
running extractor, and refuses, writing nothing:

* when the text differs (another `content_hash`): that is a new version —
  approve it and run `promote backfill` (or `promote local`);
* when the text is the same but the rendering would change (a front-matter
  field the new extractor reads otherwise): a migration never rewrites the
  Markdown;
* when any promoted version is not reproduced by the log (another text,
  another first observation, another URL), or when the id the text is named
  by moves;
* without the owner's standing approval of **every** version's text (below);
* for a withdrawn document, or one whose standing decision is `reject` or
  `hold`.

**Approvals carry on byte-identical bytes (owner decision, 2026-10-08).** The
owner's standing `approve` of a version's text, given at an earlier
extractor, carries to the running extractor when that version's published
rendering is byte-identical under it: no new `promote approve` is needed. For
the current version the bytes are the checkout's Markdown; for an earlier
version of a backfilled document they are the Markdown its own commit
published, read from the checkout's git history (a shallow clone that does
not reach it carries nothing for that version). Any byte difference, a
standing `reject` or `hold`, a withdrawal, or no prior `approve` is refused as
before, and the re-approval at the running extractor is then the owner's act.
An approval already given at the running extractor wins: nothing is carried
for it.

A carry never reads as a review:

* `promotions.jsonl` gets one `carried` record per carried version: the
  artifact, `doc_id`, `version`, `content_hash`, `from_extractor_version` /
  `to_extractor_version`, `carried_at` (the run's time),
  `basis: "byte-identical rendering"`, and `approval`, naming the approval it
  rests on by `decided_at`, `extractor_version` and `reviewer_role` — never
  `decided_by`. It is not a `decision` record;
* in `observations/<slug>.json` the `promotion` block keeps the approval's own
  `decision`, `reviewed_by_role`, `decided_at` and `reason`;
  `extractor_version` is the running extractor (the one the published bytes
  are current for), and `approval_carried` says where the review happened:

  ```json
  "approval_carried": {
    "approved_at_extractor": 6,
    "basis": "byte-identical rendering",
    "carried_at": "2026-10-08T20:00:00Z",
    "carried_to_extractor": 7
  }
  ```

  A block without `approval_carried` was approved at its `extractor_version`.
  When the approval that stands is a fresh one (a re-approval at the running
  extractor), `migrate` writes that approval's `decision`, `reviewed_by_role`,
  `decided_at`, `reason` and `classifier` into the block, never the earlier
  decision's (#722); `observations` and `observations_through` are evidence
  and stay as published.
  `observe`, `backfill`, `local` and `batch` accept a carried approval exactly
  where they accept one given at the running extractor, and a version `backfill`,
  `local` or `batch` writes on a carried approval is published with
  `approval_carried` taken from the carry (its `carried_at`, its extractors).

`approval_carried` is a new key in the observations schema, and the readers
refuse keys they do not know: deploy the engine that reads it (the hosted MCP,
and `uvx --refresh` for local MCP clients) before pushing a carried migration
to `lovverk`.

The sequence, for every current document — no approval where the bytes are
identical:

```bash
export LOVSPOR_OBSERVATORY_ROOT=/Volumes/T7/lovspor-observatory
export LOVSPOR_INNSYN_DIR=/absolute/path/to/innsyn-workspace
uv run lovspor promote migrate --corpus /absolute/path/to/lovverk --dry-run
uv run lovspor promote migrate --corpus /absolute/path/to/lovverk
git -C /absolute/path/to/lovverk add -- lokale-forskrifter README.md
git -C /absolute/path/to/lovverk commit -m 'migration(lokal-forskrift): <n> documents to extractor v<new>'
```

Use the subject the run prints; for one document add `--authority <klass>
--slug <slug>` (subject `migration(lokal-forskrift): <klass>/<slug> extractor
v<old>→v<new>`). A document refused because its *rendering* would change though its text
does not (a front-matter field the bump resolves otherwise) is re-rendered
first with `promote refresh` (#595, below under "Refreshing a received
document"), then migrated. A document refused for a byte difference needs the owner's
re-approval first (`<sha256>` is the `source_sha256` in its front matter; for
a backfilled document, one blob of each refused version — a refusal names the
version and its blobs), then the same two `migrate` runs:

```bash
uv run lovspor promote approve --authority 3118 --artifact <sha256> --decision decision-3118.json
```

A document promoted from a received text (`basis: "received"`, #643) names
its source by `innsyn_request` and `source_sha256`, not by a URL, so
`migrate` re-reads it from the innsyn workspace `LOVSPOR_INNSYN_DIR` names —
the read `promote received preview` makes — and carries its approval on the
same terms: byte-identical rendering, under the `klass_version` it was
published with; an earlier received version, on the bytes its own commit
published. Without `LOVSPOR_INNSYN_DIR` each received document is refused
with that reason and observed documents still migrate. A received document
refused for a byte difference is re-approved with `promote received approve
--request <innsyn_request> --document <sha256>`.

`--dry-run` prints, per carried version, the approval it carries (time,
extractor, basis), then the unified diff the run would write; it writes and
records nothing. Without `--authority`/`--slug` it migrates every current
document of the checkout in one run; a refused document is reported on its
own line, the others are still written, and the exit status is 1. Each
migrated document is recorded in `promotions.jsonl` as a `migrated` record
(artifact, version, content hash, from and to extractor, the approval time,
the paths written), after its `carried` records. The commit touches no
Markdown, so `promote history` derives no event from it. A rerun after the
commit prints `already at extractor v<new>`, writes nothing and records
nothing.

### 6. A batch of classifier candidates (S8)

`lovspor promote batch` runs every enacted-regulation candidate the classifier
names for one authority (or a listed subset) through the same preparation as
`preview`, counts the holds by reason, draws the owner's spot-check sample and
gates the batch on it (ADR-0016 4g, 4h). Write a batch spec:

```json
{"batch_id": "0301-2026-10-06", "authority_id": "0301", "klass_version": "131-2024",
 "classifier_output": "/absolute/path/to/predictions_r11.jsonl",
 "classifier_version": "r1.1-2026-10-03", "sample_rate": "1"}
```

* `classifier_output` is the classifier's JSON Lines file (one row per
  artifact: `url`, `authority`, `sha`, `form`, `r1`, `r2` and the rule
  signals). Only `r1` rows (enacted regulation) are candidates. The file does
  not name its rule set, so `classifier_version` states it. A row that does
  not validate refuses the whole file.
* `sample_rate` is **required and has no default**: a decimal in `(0, 1]`.
  ADR-0016 4g recommends `"1"` (100 % review) for the first authority and for
  every new adapter family; a smaller rate is the owner's policy decision
  (Open Decision 2), so it is stated in every spec and printed in every report.
* `batch_id` seeds the sample: the same id draws the same items, in any input
  order. Optional `artifacts` lists SHA-256s to restrict the batch to.

```bash
uv run lovspor promote batch --spec batch-0301.json \
  --corpus /absolute/path/to/lovverk --report-dir /absolute/path/to/reports
```

This writes `batch-<id>.md` (for the owner: gate, counts, holds by reason,
each sampled item with its `promote preview` command) and `batch-<id>.json`
into `--report-dir`, which must be outside this repository and the corpus. It
records nothing and writes nothing to the corpus. Review each sampled item
and record the decision with `promote approve` (step 2). Exit status 4 means
the gate is blocked: **one rejected sampled item blocks the whole batch**, and
an unreviewed one blocks it until it is reviewed.

One regulation embedded on many pages (a CMS sidebar) is **one** candidate:
candidates that mint the same id from the same extracted text (`content_hash`)
are folded into one, with every page in its `sources` (the JSON lists them
all), and it never enters the sample as more than one item. It is assessed,
reviewed, approved and written from its **canonical page**, chosen
deterministically from the URLs (#566, owner decision 2026-10-08):

1. the page whose own slug (the last segment of the decoded URL path) shares
   the most words of the regulation's title — words of four letters or more,
   not counting the words of the site's host name (the kommune's name);
2. then the fewest path segments, then the shortest path;
3. then the URL, then the sha256.

The page's HTML title is not used: the batch does not carry it. The report's
sample entry names the canonical page and says how many pages carry the text;
approve that page's sha256 with `promote approve`, since an approval of another
page of the group does not count for it. `batch:needs_canonical_source` remains
only as a defensive hold and is not expected in a report.
The same id minted from **different** texts stays a real collision,
`batch:same_id_in_batch`, one hold per distinct text, shown as "1 regulation,
N pages" when one text sits on several pages.

Once the gate passes, add `--write`: it writes the **next** approved item
through the same code as `promote local` and prints its commit. Commit it,
then rerun the command for the next one — one commit per version. Only items
with their own standing approval are written; an unsampled item without one
is reported, never promoted on the classifier's word alone.

## Observatory: the 24-hour observation SLA (issue #167)

> **Every active source is observed at least once per 24 hours.**

That is the invariant, and it is a property of the data, not of the scheduler.
Which hour the job fires is deployment configuration and belongs in the launchd
plist; `OBSERVATION_SLA` and `SWEEP_DEADLINE` in
`src/lovspor/observatory/sweeps.py` are where the cadence itself is stated.

24h is a first SLA for local legal material, deliberately chosen to be measured
against rather than defended: the steady state of the register has never been
timed. Argue it down to 12h or 6h once there are sweep durations and delta
counts to argue from, not before.

### The sweep records itself

The observation log answers what the servers did. It cannot answer whether the
Observatory ran last night — a sweep that never started leaves no trace in it by
construction, and a machine that was off for three days looks exactly like three
quiet days at two hundred municipalities.

So `capture-all` appends one line per run to `sweep-runs.jsonl`, beside the
registry. Process telemetry, not an observation:

```json
{"run_id":"2026-08-25T01:00:00+00:00","started_at":"...","finished_at":"...",
 "active_sources":201,"sources_completed":198,"sources_refused":3,
 "sources_withdrawn":0,"captured":47,"failed_fetches":2,"unchanged":4218,
 "deferred":36,"capture_selection":false,"unselected":0,"status":"degraded"}
```

| status | meaning | who records it |
| --- | --- | --- |
| `success` | every active source was swept to the end of its sitemap | `capture-all` |
| `degraded` | the sweep ran, and at least one source was refused **or capped** (a source held under a verdict, or withdrawn from the register mid-run, does not degrade it — it is counted, not asked) | `capture-all` |
| `failed` | the sweep could not execute — archive not mounted, log damaged, or the host reserved for a benchmark (`deferred_exclusive_workload`) | the nightly wrapper |

`capture-all` still exits 1 on `degraded`. The `failed` state belongs to the
wrapper because the cases that produce it are the ones where `capture-all`
cannot run far enough to write anything — and an unmounted archive is exactly
the case where there is nowhere to write to.

### Running it: `observatory nightly`

`capture-all` sweeps. `nightly` checks the ground first, and it is what the scheduler
runs:

```bash
uv run lovspor observatory nightly
```

Preflight, in the order the failures happen — a missing archive is a different problem
from a damaged log, and answering "why is it red" with the wrong one sends you to the
wrong place:

| verdict | meaning |
| --- | --- |
| `storage_unavailable` | the archive directory is not there — T7 not mounted |
| `registry_missing` | the archive is there, the source registry is not |
| `observation_log_damaged` | the log does not scan clean; run `observatory verify` |
| `deferred_exclusive_workload` | the ground was fine, but the host is reserved: an LLHB benchmark arm holds the exclusive workload lock (issue #169). The sweep did not start; the next scheduled one picks up |
| `storage_write_failed` | mid-run, not preflight: the archive is still there, but a write under it failed (issue #534) |

Each writes a `failed` run carrying its reason — **except `storage_unavailable`**, which
by definition has nowhere to write.

The archive can also go away *during* a sweep (issue #534: T7 vanished at 09:36 on
2026-10-04). The engine never creates the archive root or anything above it — only
directories below an existing root — so a vanished volume ends the run with a typed
storage error and exit 1, not a traceback. The run is then `failed` with
`storage_unavailable` when the root is gone, and `storage_write_failed` when the root is
there but a write under it failed. The record is written only in the second case: a
vanished root gets no record, never one in a recreated or fallback directory that would
later read as the archive's history. Its reason still reaches stderr and the dead-man
switch's `/fail` body. Either way `--catch-up` treats the night as missed: a `failed` run
is not a sweep start. The deferral is checked *after* the ground checks for
the same reason: its record needs an archive to land in. It still pings the dead-man
switch's `/fail` URL — truthfully, the sweep did not run — so a deferred night shows up
as red, and whoever reserved the host is the one looking at it.

The lock is one file, `$XDG_STATE_HOME/lovspor/exclusive-workload.lock` (else
`~/.local/state/lovspor/…`; `LOVSPOR_EXCLUSIVE_LOCK_PATH` overrides), held with
`flock` for the whole sweep by `nightly` and `capture-all`, and for the whole arm by
`benchmarks/llhb/runner/run_arm.py --execute`. Neither side waits: the sweep defers, the
benchmark refuses. A crashed holder leaves nothing behind — the kernel releases the lock
with the process. Per-source `observatory capture` does not take it; it is an operator's
hand tool, not a workload. There the message and the exit code are the whole
output, and the remote dead-man switch is what turns the resulting silence into an alarm.

**There is no fallback.** If the archive is absent the sweep refuses. Quietly creating a
second observatory on the internal disk is the most damaging thing this command could do
while trying to be helpful: two archives, each partial, neither aware of the other.

### After the sweep: the LF first-seen ledger (issue #509)

The kommuner mostly link to Lovdata instead of hosting a local regulation's text. The
owner's rule is that an LF id first seen on a kommune's pages is requested from that
kommune under offentleglova the next day, so the archive keeps a ledger of when each
id was first seen. It is read from links in captured HTML only — nothing fetches
lovdata.no.

```bash
uv run lovspor observatory lf-ledger                 # bring it up to date (cheap: log tail only)
uv run lovspor observatory lf-ledger --rebuild       # read the whole log again (idempotent)
uv run lovspor observatory lf-first-seen --day 2026-10-02
uv run lovspor observatory lf-first-seen --since 2026-10-01
uv run lovspor observatory lf-last-seen --unlinked      # candidate repeals for review (#694)
```

- `lf-ledger.jsonl` sits beside the observation log (ADR-0010 §5). It is append-only,
  with one entry per (authority, kind, id): the earliest `ObservedAt` of a 2xx HTML page
  that linked it, plus that page's URL and blob hash. `kind` is `LF` or `LTII` for
  `lovdata.no/dokument/LF/forskrift/<id>` and Lovtidend avd. II links, and `forskrift`
  for the short `lovdata.no/forskrift/<id>` form. The short form does not say whether
  the regulation is local or central, so it stays `forskrift` and is resolved later.
- `lf-ledger-cursor.json` records how far into the log the ledger has read. It is
  trusted only when the log still begins with the bytes it was built from and no
  correction landed after it. Otherwise the next update reads the whole log, which
  costs time but never misses an id, because existing keys are not appended again.
- `nightly` updates the ledger after the sweep and the heartbeat. If the update fails,
  it says so on stderr (`lf-ledger not updated: …`) and leaves the sweep's exit code
  alone, and the next update catches up.
- A ledger line that does not parse is refused. The ledger is a function of the log, so
  delete the file and run `lf-ledger --rebuild`; don't edit it by hand.
- `lf-first-seen` prints tab-separated rows (Oslo day, first `ObservedAt` in UTC,
  authority, kind, id, page) to stdout and the count to stderr. Days are Oslo calendar
  days.
- `lf-last-seen` (#694) is the other half of the ledger, derived from the whole log on
  each run and persisted nowhere: for every (authority, kind, id) the latest 2xx HTML
  capture that linked it, and whether every page that ever linked it has since been
  read again without the link. Rows are tab-separated (authority, kind, id, first seen,
  last seen linked, `linked`/`unlinked`, unlinked since or `-`, the latest readable
  capture of those pages, pages that ever linked it, pages failing since their last
  readable capture, the page of the latest sighting); `--unlinked` keeps the candidates
  alone; the counts go to stderr. `unlinked` is a candidate repeal for a human to
  review, never a retraction: the ledger keeps its entry, the next wave still asks for
  the id, and a promoted text is not withdrawn. Only a later readable capture of the
  same page is evidence that a link went away — a fetch failure, an error page, a
  non-HTML answer or a capture whose blob is gone (tombstoned, or damage `verify`
  reports) leaves the id `linked` and counts the page as failing, so a kommune whose
  site is down does not read as a repeal. Verdicts follow `ObservedAt`, not log order.
  A damaged log is refused. Holding `unlinked` ids out of a wave, and tying an LTII
  amendment to its base regulation, are later slices of #694.

### Innsyn requests to every local authority (issue #509)

The LF ledger says which regulations a kommune links; the innsyn commands turn that, and
the general request for everything else, into one innsynskrav per kommune and
fylkeskommune. They live in a workspace of their own: `--dir` or `LOVSPOR_INNSYN_DIR`,
with no default. The workspace is refused inside the engine repo or the corpus. Keep it
off the archive volume too (for example `~/lovspor-ops/innsyn`); its parent must exist.

```bash
export LOVSPOR_INNSYN_DIR=~/lovspor-ops/innsyn
export LOVVERK_CORPUS_PATH=~/Programming/Python/lovverk   # `letters` holds what it publishes (#606)
I="uv run lovspor observatory innsyn"

# 1. The population: every kommune (KLASS 131) and fylkeskommune (KLASS 127) at a date.
$I fetch-population --date 2026-10-12     # writes population/population-<date>-read-<ts>.json
$I population                             # id, kind, KLASS code, name; sources + exclusions on stderr

# 2. Who signs, and where each request goes.
$I set-sender --name "…" --email kontakt@lovspor.no
$I recipient-candidates --id 4646         # proposals from the archive's captures; records nothing
$I set-recipient --id 4646 --address postmottak@fjaler.kommune.no \
   --evidence-url https://www.fjaler.kommune.no/ --read-at 2026-10-03T10:00:00Z
$I set-channel --id 1517 --channel edialog \
   --evidence-url https://svarut.ks.no/edialog/mottaker/964979278 --read-at <when>   # no e-mail: sent by hand
$I set-maalform --id 4646 --form nn --evidence-url <page read> --read-at <when>
$I authorities                            # recipient or UNRESOLVED, målform; counts on stderr

# 3. The pilot register, once.
$I import-pilot --register ~/lovspor-ops/innsyn-pilot-2026-10-03/sent.csv

# 4. A wave of letters. Writes files only; sends nothing.
$I letters --out ~/lovspor-ops/innsyn/wave-01 --wave-size 25 [--held held-ids.txt]
#    (or `$I --corpus /path/to/lovverk letters …` instead of LOVVERK_CORPUS_PATH)

# 5. Send it: a dry run first, then --send, a batch at a time (see "Sending a wave").
$I send --manifest ~/lovspor-ops/innsyn/wave-01/manifest.json --limit 25
$I send --manifest ~/lovspor-ops/innsyn/wave-01/manifest.json --limit 25 --send

# 6. A letter sent by hand, and the answers as they arrive.
$I record-sent --manifest ~/lovspor-ops/innsyn/wave-01/manifest.json --id 4646 --sent-at <UTC> [--message-id '<…@lovspor.no>']
$I record-sent-manual --manifest ~/lovspor-ops/innsyn/wave-01/manifest.json --id 1517 --sent-at <UTC> --evidence <receipt.pdf>
$I record-ack --request 4646-20261005T074425Z --at <UTC>
$I record-case-number --request 4646-20261005T074425Z --number 2026/2348
$I record-outcome --request 4646-20261005T074425Z --outcome partial --at <UTC> [--refusal-basis "…"]
$I record-interim --request 1515-20261019T235216Z --at <UTC> --promised-by 2026-11-22
$I record-narrowing-request --request 3825-20261019T064500Z --at <UTC> [--narrow-by 2026-11-10]
$I record-klage --request 4646-20261005T074425Z --at <UTC>
$I record-reminder --request 4646-20261005T074425Z --at <UTC> --letter <reminder.txt>
$I record-reply --request 1520-20261019T064500Z --at <UTC> --kind narrowing --letter <reply.txt>
$I record-follow-up --parent 0301-20261005T074343Z --letter <follow-up.txt> --sent-at <UTC> [--message-id '<…>']
$I record-bounce --request 4646-20261005T074425Z --at <UTC> --evidence <dsn.eml> --status 5.4.1
$I record-mailbox-closed --request 4646-20261005T074425Z --at <UTC> --evidence <auto-reply.eml>
$I requests                               # every request, with its § 32 mark
$I requests --waiting                     # the requests the authority asked us to narrow
$I due                                    # past the mark with no answer and no klage

# 6. The documents an answer carried (#509 point 5).
$I record-received --request 4646-20261005T074425Z --file <attachment.pdf> --at <UTC>
$I record-received-link --request 3207-20261021T160112Z --url <link as delivered> --at <UTC>
$I classify-received --request 4646-20261005T074425Z --document <sha256> \
   --kind regulation_text --answers 2009-02-16-306
$I received                               # every document; requested ids still without a text
```

- **Population.** The snapshot is the count. Nothing pins a number of kommuner, because
  the division changes. Codes that are not authorities are kept as `excluded` with the
  reason: SSB's "Uoppgitt" (`9999`, `9900`), and fylkeskommune `0300`, which is Oslo.
  Oslo is asked once, as kommune `0301`. A fylkeskommune is keyed by its fylkesnummer
  (`4600` becomes `46`), the same id the register and the LF ledger use.
- **Recipients.** An authority without a recorded recipient is **unresolved** and gets no
  letter. Each record carries the page the address was read on and when (a live visit,
  or a capture's `ObservedAt`). `recipient-candidates` scans the archive's captured HTML
  for `postmottak@`/`post@` addresses under `.no`, the way the pilot did, and prints
  the shortest citing URL. It proposes only addresses on the authority's own registered
  domain or its subdomains, so a business, a state agency or a neighbour named on the
  page is never proposed (owner decision 2026-10-12). An authority whose mailbox lives
  elsewhere, such as Oslo's `postmottak@oslobystyre.no`, is recorded by hand from the
  page that names it. The scan is read-only. The operator opens the page and records the
  choice.
- **Målform.** No machine-readable source of the språkvedtak was found reliable enough
  to read automatically, so the operator records it with evidence (`set-maalform`).
  Unset, the letter is in bokmål and the manifest says `maalform_recorded: false`. That
  default is the owner's decision (2026-10-12).
- **Letters.** A wave takes the next `--wave-size` resolved authorities. An authority
  a request already reached is not written to again unless its pages have since linked
  ids no request asked for (#696, #695): with nothing new it is listed as
  `skipped_open` while a request is open (open until the outcome is `full` or
  `refused`; a letter recorded undelivered reached nobody) and as `skipped_closed` once
  it closed. With new ids its letter asks for those ids alone, says it comes in
  addition to the earlier request, and does not put the general point again — a
  fulfilled request is never repeated, a refusal is answered on the klage track, not
  with a fresh general request, and an authority delivering in parts, holding, or
  pointing to Lovdata still gets every newly seen id as a short request of its own,
  under its own request id, with the open case neither reopened nor merged. The
  manifest entry then says `general_clause: false`, and `record-sent` records it so.
  Authorities with known ids still to ask for come first, then the rest, each group in
  id order (owner decision 2026-10-12). Point 1 lists the `LF`/`LTII` ids the LF ledger saw on the authority's
  pages, less the ids whose text is held and the ids an earlier request already asked
  for. An id is held when a current `lf-` regulation of the local dataset in the
  `lovverk` checkout carries it (`--corpus` before the command, or
  `LOVVERK_CORPUS_PATH`; read through the promotion reader, so a checkout that is
  missing, not `lovverk`, this repository, or whose `lokale-forskrifter/manifest.json`
  does not read refuses the wave instead of holding nothing, #606), or when the
  operator's `--held` file names it (one per line, `#` comments; for texts held outside
  the corpus). The manifest's `held_out` lists every known id left out this way with its
  authority and `held: corpus` or `held: file` (the corpus wins when both hold it), so a
  reviewer sees that nothing was dropped silently. A removed local record or an `lk-` id
  holds nothing. The short `forskrift` link form is left out. The next
  point asks for all other local regulations in force, with their amendments and an
  overview, as cases of one kind (offentleglova § 28 andre ledd). An authority with no
  known ids gets that point alone. Every letter asks for the authority's archive
  documents, not Lovdata texts, in electronic form (§ 30 første ledd). The output
  directory must be new or empty, and holds `<id>.txt` files (`Til:`, `Emne:`, body)
  and `manifest.json`. `--lf-ledger` names a copy of `lf-ledger.jsonl`; by default the
  observatory's own is read, so the volume must be mounted.
- **Ledger.** `requests.jsonl` is append-only, locked and fsynced. A torn or malformed
  line is refused, never read past. Each field has its own command. `record-sent`
  takes the ids, the general clause and the recipient from the manifest. It refuses a
  letter whose `Til:` line no longer matches, and records the SHA-256 of the letter as
  it stands. A transition on an unknown request, a second send of one request, and a
  time before the send are all refused. Outcome classes: `referred_to_lovdata`,
  `interim_reply_no_date`, `interim_reply_with_date`, `narrowing_requested`, `partial`,
  `full`, `refused`, `no_answer`. Only `referred_to_lovdata`, `partial`, `full` and
  `refused` are answers, so an interim reply leaves the request open and, once the § 32
  mark has passed, due.
- **Interim replies that name a date (#689).** An authority that says when it will
  answer ("innen 22.11.2026", "i løpet av uke 44") gets `record-interim` with `--at`
  and `--promised-by`, the Oslo day it promised, converted by the owner when it was
  given as a week (week 44 of 2026 ends 2026-11-01). It is not an answer. `due` leaves
  the request out until that day has passed, and lists it from the next day on, so a
  klage is never aimed at an authority still inside the date it named, and one that
  misses its own date is flagged. `record-outcome --outcome interim_reply_with_date`
  is refused: the day is mandatory. A promised day before the reply's own day is
  refused. A later outcome on the request replaces the promise: a `full` answer drops
  it, a second `record-interim` moves it, an `interim_reply_no_date` drops it so the
  § 32 mark alone decides. `requests` and `due` show the promised day in the column
  after the reply kind.
- **The authority asks us to narrow (#700).** An authority that answers "presiser
  kravet" under offentleglova § 28 andre ledd (Vinje, Råde, Stranda, Eidskog, 2026-10)
  gets `record-narrowing-request` with `--at` and, when it set a day, `--narrow-by`.
  The outcome class is `narrowing_requested`; `record-outcome` takes it too. It is not
  an answer and does not close the request, but it puts the request in the owner's
  hands: `due` leaves it out, whatever its § 32 mark, so a klage is never aimed at an
  authority waiting for our narrowing. `requests --waiting` lists exactly those
  requests, and the last two columns of `requests` and `due` show when the authority
  asked and the day it asked for. A `record-reply --kind narrowing` later than the
  request ends the wait and restarts the § 32 mark from the narrowing; a narrowing
  recorded before it does not answer it. A closing outcome (`full`, `refused`) ends the
  wait too; a `partial` answer does not, so an authority that answered point 1 and asked
  us to narrow point 2 still waits on us. A second request to narrow replaces the first
  by the later `--at`, whatever the order recorded. A deadline before the day the
  request arrived is refused.
- **Undelivered letters (#644).** `send` records every letter the SMTP server accepted,
  but acceptance is not delivery. A permanent bounce is recorded with `record-bounce`
  and its DSN status (`5.x.x` only: a transient `4.x.x` is not a failure yet). A mailbox
  that answers it takes no e-mail ("tek vi ikkje imot e-post på denne adressa") is
  recorded with `record-mailbox-closed`. Both take `--evidence`, the bounce or
  auto-reply saved from Gmail as `.eml`, and record its SHA-256, never its text: an
  auto-reply can name a case officer. Keep the saved file with the wave. An undelivered
  request started no § 32 clock, so `due` leaves it out, and it is not open, so it no
  longer holds the authority back. Its ids count as not yet asked for. The address it
  failed at is not used again: until `set-recipient` names another, the authority is
  `unresolved` and the next wave writes it no letter. Once it does, `letters` puts the
  authority in a later wave as usual, and `send` sends it there. An authority that
  takes requests only through a contact form or KS eDialog is given that channel with
  `set-channel` (see "Sent by hand").
- **Sent by hand: web forms and KS eDialog.** Some authorities take no e-mail (Hareid,
  Ulstein, Herøy: "tek vi ikkje imot e-post på denne adressa"). The owner sends those
  by hand (owner decision 2026-10-24). `set-channel --channel webform|edialog` records
  the route, with the form's or eDialog's own page as `--evidence-url`. A channel and a
  recipient fill the same slot: the one recorded last stands, so `set-recipient` later
  puts the authority back on e-mail. An authority with a channel is not unresolved
  (`authorities` shows `edialog <page>`). `letters` writes its letter as usual, with the
  page on the `Til:` line and `channel` in the manifest entry. `send` refuses it ("goes
  by edialog, not e-mail") and sends the others. To send it: 1. open the page and paste
  the letter's subject and body; 2. save the receipt (the confirmation page, the
  kvittering PDF, or a screenshot); 3. `record-sent-manual --manifest … --id …
  --sent-at <UTC> --evidence <receipt>`. The ledger keeps the channel, the page, the
  letter's SHA-256 and the receipt's SHA-256, never the receipt itself, so keep the file
  with the wave. From then on the request is like any other: its § 32 mark runs from
  `--sent-at`, `due` lists it (with `edialog` or `webform` in the channel column), and `record-reminder`,
  `record-outcome` and `record-klage` work on it. `record-sent` refuses a letter that
  goes by hand, and `record-sent-manual` refuses one that goes by e-mail.
- **Reminders (#625).** `record-reminder` records a påminning (offentleglova § 29 første
  ledd) with the SHA-256 of the letter as sent (`--letter`, the text saved as a file).
  A reminder is not a klage: the request stays on `due`, which shows the latest reminder
  in the column after `undelivered`, so a klage is not sent on a request reminded two
  days ago without the operator seeing it.
- **Replies to the authority (#686).** When the owner writes back in a request's thread,
  `record-reply` records it with the SHA-256 of the message as sent (`--letter`) and its
  kind: `narrowing`, when the authority asked for a nærare avgrensing under offentleglova
  § 28 andre ledd (Ørsta, Hareid, Sande, Ørland, 2026-10) and the owner narrowed the
  request; `answer`, to a question the authority put; or `other`. `requests` and `due`
  show the latest reply and its kind in the columns after the channel. A narrowing is the request
  as it now stands, so its § 32 mark runs from the latest narrowing, and a request
  narrowed today leaves `due` until five working days have passed. An answer or other
  reply moves nothing. The text is not kept: it can quote the case officer's mail.
- **Follow-up requests (#625).** After a referral, a new request to another body of the
  same authority (Oslo's Byrådet after `referred_to_lovdata`) is recorded with
  `record-follow-up --parent <request>`. It is a request of its own, with its own id,
  recipient, Message-ID and § 32 mark, linked to the one it follows (the last column of
  `requests`). Its recipient is the `Til:` line of `--letter`, the letter as sent saved
  in the wave format (`Til:`, `Emne:`, a blank line, the body), and it is not taken from
  the directory, which keeps the authority's main mailbox. It asks what the request it
  follows asked: the same ids and general clause. A parent not in the ledger, another
  authority's request, and a follow-up not sent after its parent are refused.
- **Answers in parts.** An authority that answers in several mails (Røros: "Her kjem
  første del") gets one `record-outcome` per part, each with its own `--at`. Every part
  stays in the ledger. The first answer is the request's `answered_at`, and the last
  part's class is its outcome, so the part that completes it is recorded `full`. The
  documents of each part are recorded with `record-received`.
- **Sending a wave.** `send` delivers the manifest's letters by SMTP submission with
  STARTTLS and certificate verification (port 587), in manifest order. A server that does
  not offer STARTTLS is refused before the password is sent. Without `--send` it is a dry
  run: one line per letter that would go (id, To, From, Subject, size in bytes), whether
  the SMTP settings are complete, and nothing sent or written. Right before each letter,
  after the pause, it reads the ledger, the directory and the file again and checks that
  the letter file still has the manifest's SHA-256, that the directory's recipient is
  still the manifest's, and that the authority has no open request from elsewhere; a
  letter that fails a check is refused with the reason and the others go. `--limit`
  counts letters sent, not letters refused. A letter whose
  authority already has a `sent` with this letter's SHA-256 is skipped, so a rerun never
  sends twice. Each accepted letter is recorded at once, through the same event as
  `record-sent`, with the acceptance time (UTC) as `sent_at` and its Message-ID (on the
  sender's domain), which `requests` shows after the klage time. The first letter the
  server does not accept stops the run: nothing is recorded for it, and the summary says
  `sent N, skipped (already sent) M, refused K, remaining R`. `--pause-seconds` (default
  20) is the gap between two letters, to stay inside Gmail's sending limits.
- **SMTP setup (Gmail).** In `.env` (see `.env.example`): `LOVSPOR_SMTP_HOST=smtp.gmail.com`,
  `LOVSPOR_SMTP_PORT=587` (the default), `LOVSPOR_SMTP_USER` = the Gmail address, and
  `LOVSPOR_SMTP_PASSWORD` = an app password (Google Account, Security, 2-Step Verification,
  App passwords), never the account password. The From header is the recorded sender
  (`set-sender`), so `kontakt@lovspor.no` must be a verified send-as address of that Gmail
  account (Settings, Accounts, "Send mail as"). A missing setting is refused by name. The
  password is never printed: a server reply or error that echoes it, plain, escaped or
  base64-encoded, is shown with `[redacted]` in its place.
- **Run procedure.** 1. Dry run, and read the lines: `send --manifest … --limit 25`.
  2. `send --manifest … --limit 25 --send`. 3. Check Gmail's Sent folder: 25 messages,
  From `kontakt@lovspor.no`, and `requests` lists them with their Message-IDs. 4. Repeat
  step 2 until `remaining 0`. If a run stops on an SMTP error, look for that letter in Sent
  before running again: when the server took it but the answer was lost, record it with
  `record-sent --message-id …` from the Sent copy so the rerun skips it. If the server took
  a letter and the ledger refused it, `send` prints the exact `record-sent` command.
- **The § 32 mark.** offentleglova § 32 andre ledd: no answer within five working days
  after receipt counts as a refusal that can be appealed. The mark is computed, never
  stored. It is the fifth working day after the Oslo day of `sent_at`, skipping
  weekends and Norwegian public holidays. `due` lists requests whose mark has passed:
  these are the klage candidates.
- **Received documents.** `record-received` holds the attachment in the workspace under
  `received/<sha256[:2]>/<sha256>` and records its SHA-256, media type and arrival time on
  the request. PDF, HTML and DOCX are taken: the formats the promotion extractor
  reads; TIFF, JPEG and PNG are taken too (#691), see the next bullet. The file name
  is not kept, because it can name a case officer or a party.
  Documents never go into a repository: a received PDF also carries cover letters,
  minutes pages and names, so only text extracted from it, after review, is published.
  `classify-received` records what the operator read the document to be:
  `regulation_text`, `vedtak`, `minutes`, `cover_letter` or `other`, and with
  `--answers` the requested LF id it is the text or vedtak of. Without `--answers` it is
  unrequested, such as a predecessor regulation the kommune sent along. A later
  classification replaces an earlier one. `received` lists every document and, per
  request, how many requested ids no received regulation text answers yet. A kommune
  that sent some texts and said nothing of the rest is not saying the rest do not exist.
- **Scanned documents and maps (#691).** Authorities deliver archive scans as images:
  Høyanger sent `Forskrift.TIF` and `Kunngjering.TIF`, the text as sent to Norsk
  Lovtidend, and Kragerø sent its friluftsområde boundary maps as JPEG. `record-received`
  and `record-received-link` hold TIFF, JPEG and PNG by content like any other document,
  by suffix (`.tif`, `.tiff`, `.jpg`, `.jpeg`, `.png`) or Content-Type. No text is read
  from an image: a scan classified `regulation_text` is held by the promotion extractor
  as an unsupported format and does not reach the corpus until its text is obtained
  another way (an OCR step is not built; a text copy asked from the authority is the
  honest route). A map is `other`. A hearing statement from a private person is `other`
  and is never published (personopplysningsloven); its bytes stay in the workspace only.
- **Documents delivered as links (#688).** An authority that answers with links (Nordre
  Follo: 47 opengov links; Akershus: eInnsyn links) delivers all the same.
  `record-received-link --url <link> --at <UTC>` reads the link once, holds the bytes as
  `record-received` would an attachment, and records the URL as delivered and when it
  was read (`fetched_at`), with `--at` still the arrival of the mail carrying the link.
  `received` shows the URL in its last column; an attachment's is empty. The format is
  the server's Content-Type, or the link's suffix when the server declares none or a
  generic `application/octet-stream`; a declared type the ledger does not hold (a GIF
  from a `.pdf` link) is believed, and the link refused; a link that is not answered
  200, comes back empty, or is in no format the ledger holds is refused and nothing is
  held. An HTML page a reader would see no text in — a JavaScript app shell whose only
  text is its title and a "enable JavaScript" notice (Drammen's Digdem links, #721), a
  blank body, page code alone — is refused the same way, naming the URL, before its
  blob or ledger line is written; a page with any text is held, however short. Fetch
  the document itself another way and hold that with `record-received`; never the
  shell. One command per link: a link the engine cannot read
  (a login page, a viewer that needs a browser) is a finding to file, not a file to
  record by hand.
- **Pilot import.** `import-pilot` reads `sent.csv` with the letters as sent
  (`innsyn-<id>.txt` beside it, or `--letters`). The ids come from the letter, and a row
  whose letter lists a different number of ids is refused. The outcome class is the
  cell's text before the first `;`. `notes` and `sent_from` are not imported, because the
  notes name case officers. A rerun imports nothing twice.

### The dead-man switch (issue #167, part 3)

Two failures look identical from inside this machine: *the sweep ran and found nothing
new* and *the machine was off for three days*. Both leave the observation log silent. So
the alarm is inverted — after every run the sweep reports out to a service that is **not
on this machine**, and that service alarms when the report fails to arrive. Nobody has to
detect the machine dying; it is enough that it stopped saying it is alive.

Remote is the whole point. A watchdog on this Mac cannot notice that this Mac is off, the
way a smoke detector cannot be powered from the burning room.

```bash
export LOVSPOR_OBSERVATORY_HEARTBEAT_URL="https://hc-ping.com/<uuid>"
```

Set the check's period to 24h and its grace to 12h, so it alarms at the same 36h the
engine already treats as the deadline (`SWEEP_DEADLINE`).

| run status | reports to | why |
| --- | --- | --- |
| `success` | the ping URL | |
| `degraded` | the ping URL | it **ran**, and liveness is what this guards |
| `failed` | the ping URL + `/fail` | it could not run |

**Degradation deliberately does not alarm here.** Ten sources already refuse on a normal
night; alarming on that would fire nightly, and a monitor that cries wolf gets muted —
taking the liveness signal with it. Degradation has its own channels: the exit code,
`observatory status`, and the run record. The full run travels in the ping body, so the
service's history still shows what kind of night it was.

**An undelivered heartbeat never fails a sweep, and is never silent.** The sweep is the
point; the telemetry is not. But a switch that quietly stopped reporting is
indistinguishable from a dead machine, so it says `heartbeat: NOT DELIVERED` on stderr —
better learned from the log than from a false alarm at 3am. With nothing configured it
says `no dead-man switch is armed` rather than passing quietly.

The limit worth knowing: this detects **this machine** going quiet, not the monitoring
service going quiet. If the hosted check dies you get silence instead of an alarm. No
number of watchers closes that; it moves.

### Installing the job

`deploy/launchd/no.lovspor.observatory.nightly.plist` is a template. Replace every
`__PLACEHOLDER__`, then:

```bash
cp deploy/launchd/no.lovspor.observatory.nightly.plist ~/Library/LaunchAgents/
# edit __LOVSPOR_BIN__, __OBSERVATORY_ROOT__, __LOG_DIR__, __HEARTBEAT_URL__
launchctl load ~/Library/LaunchAgents/no.lovspor.observatory.nightly.plist
launchctl list | grep lovspor          # confirm it is registered
launchctl start no.lovspor.observatory.nightly   # one manual run, to prove the wiring
```

`RunAtLoad` is true, guarded (issue #356): the job runs whenever the agent loads — at
login, and at `launchctl load`/`bootstrap` — with `--catch-up`, which exits 0 without
sweeping when a sweep started in the last 24 hours (`catch-up skipped: a sweep started
at …`). With no sweep in that window the load **does** start one, including the load
during setup: that is a missed night. `launchctl start`/`kickstart` runs the same guarded
command, so to force a sweep within the window run `lovspor observatory nightly` without
the flag.

Then prove the switch is armed from outside the log — `observatory status` prints a
`Dead-man switch` section that says `NOT ARMED` until `LOVSPOR_OBSERVATORY_HEARTBEAT_URL`
reaches the job's environment (issue #347: an unarmed switch said so on stderr every
night, into a 6 MB file nobody read, and an 8-day outage was found by a human).

The template also sets `LOVSPOR_OBSERVATORY_REQUIRE_PINNED_ENGINE=1`: the sweep then
refuses to run from an engine checkout that is on a branch or has local changes, the way
the bootstrap lane worker does, and records the refusal as `engine_not_pinned` (issue
#219 — the nightly worktree was found on a feature branch within an hour of being
installed). Every run record names the commit that produced it (`engine_commit`), and
`observatory status` shows it. Move the pin only to a merged `main` commit:

```bash
git -C <nightly worktree> fetch origin && git -C <nightly worktree> checkout --detach origin/main
```

`StartCalendarInterval`, not `StartInterval` or cron: if the machine is asleep at 03:00,
launchd runs the job on wake and coalesces missed triggers. cron loses them silently.

A machine that is **powered off** at 03:00 is different: launchd replays nothing at boot,
and the trigger is lost. `RunAtLoad` is what catches it up — the agent loads when the
owner's GUI session starts, so the catch-up happens at login, not at power-on; an
unattended reboot catches up only if the Mac logs in automatically. The guard reads
"a sweep started" from two traces, because a sweep killed by the shutdown never writes
its run record:

| trace | what it proves |
| --- | --- |
| `sweep-runs.jsonl` | a sweep that reached its end; a `failed` run (deferred, refused at preflight) observed nothing and does not count |
| the exclusive workload lock's advisory record | the start of the last sweep to take the lock; only a clean exit empties it, so a killed sweep's start survives until another workload takes the lock |

So a sweep killed mid-run by a shutdown counts as started: a reboot within 24 hours of
its start does not restart it (resuming a killed sweep is #218's checkpoint work, not
this guard's).

The 03:00 trigger itself is never guarded. launchd does not say which trigger started
the job, so a start between 03:00 and 03:15 Oslo time reads as the calendar and sweeps.
A boot catch-up at 01:00 therefore does not swallow that night's 03:00: in practice the
catch-up is still running then and launchd drops the trigger (which `observatory status`
reports, #218); if it has already finished, 03:00 sweeps again rather than becoming a
night that silently did not happen. A wake-replayed trigger arrives off the window and
is guarded like a load. A machine that stays off gets no run at all — the dead-man switch
is what notices that, and no scheduler can cover it from inside.

### A capped source is not a finished one (issue #172)

`--limit` stops a source's pass after that many fetches. The three counters cannot express
the difference between *the sitemap ran out* and *the pass was stopped*, and that
difference is the whole problem: a refused source yields nothing and says so loudly, while
a truncated one yields most of itself and reads as finished.

So the run record carries `sources_capped`, a capped source makes the sweep `degraded`, and
`capture-all` exits 1 — the exit code follows the recorded status, not the operator's
intent. A deliberate `--limit` run therefore exits non-zero: the bound was intentional, the
incompleteness is still real, and the next sweep should pick the source up rather than
count it as done. `freshness` makes that cheap — pages already captured fail the freshness
test and are skipped.

This was found in the bootstrap: seven municipalities, Bergen among them, stopped at the
lane's round cap and were recorded as complete.

### When an authority moves to another domain (issue #166)

`haugesund.no` officially redirects to `haugesund.kommune.no`. The redirect leaves the cleared
domain, so the fetcher refuses to follow it and the source yields nothing while still counting
as active. Editing `canonical_domain` is not the fix: the access-policy check answers about the
old host — the reviewer read *its* `robots.txt` and *its* terms — and leaving that check in
place would let a clearance obtained for one server authorise traffic to another.

```bash
uv run lovspor observatory replace-source-domain \
    --id 1106 \
    --domain haugesund.kommune.no \
    --reason "official haugesund.no redirects to haugesund.kommune.no" \
    --by "Bartosz Kobyliński"
```

One operation, because the parts are not independently safe. The domain changes, the
access-policy check is removed, the source is deactivated, and the declared listing entry
points and any capture verdict are dropped — all of them were obtained for a host that is no
longer this source's. Capture resumes only after a fresh review:

```bash
uv run lovspor observatory activate-source --id 1106 --check haugesund-kommune-no.json
```

Handing that command the *old* check is refused, not accepted: the check names the
`robots.txt` it was performed against, and a record whose clearance points outside its own
domain does not validate. That is enforced in the model, so a hand-edited `sources.json`
refuses to load rather than granting what the command would not.

The decision itself goes to `source-events.jsonl` beside the registry — append-only, one JSON
object per line, locked and fsynced like the observation log:

```json
{"event":"source_domain_replaced","authority_id":"1106",
 "from_domain":"haugesund.no","to_domain":"haugesund.kommune.no",
 "reason":"official haugesund.no redirects to haugesund.kommune.no",
 "changed_at":"2026-08-24T11:00:00Z","changed_by":"Bartosz Kobyliński",
 "previous_record_sha256":"…"}
```

`sources.json` is rewritten whole by every command, so it is current state and cannot answer
"what was withdrawn, and on whose word". The three artifacts split the job: `sources.json` what
is true now, `source-events.jsonl` what an operator decided, `observations.jsonl` what the
servers actually did. The fingerprint is the SHA-256 of the replaced record in the same
canonical JSON the registry is written in, so it can be recomputed from an archived
`sources.json` without this engine.

`--reason` and `--by` are mandatory and never synthesised. This is a human decision about
traffic to someone else's server, and an unattributed one is not evidence that anybody made it.

### A source found to publish nothing machine-reachable (issue #195)

A source that was activated, crawled, and found to publish nothing a machine can reach —
no sitemap, no feed, no server-rendered index — refuses loudly on every sweep, and the
conclusion that it always will lives nowhere. Twelve municipalities in the bootstrap ended
that way, with the evidence in a shell log. The registry can hold the conclusion instead:

```bash
uv run lovspor observatory record-verdict --id 1860 --verdict vestvagoy-verdict.json
```

```json
{
  "outcome": "no_machine_reachable_source",
  "routes_checked": [
    "sitemap.xml and sitemap index, declared and conventional",
    "Atom and RSS at conventional feed paths",
    "server-rendered listing page (zero <time>, zero datetime=)",
    "Lovdata publicData catalogue (avdeling I only)"
  ],
  "evidence": "issue #194: ACOS front end, listing assembled in the browser from /api/presentation/ behind a per-page token",
  "reached_at": "2026-08-26T18:00:00Z",
  "reviewed_by": "Bartosz Kobyliński",
  "recheck_after": "2026-11-26T00:00:00Z"
}
```

The verdict is the twin of the access-policy check and travels the same way: a document,
because it is the record of a human conclusion and has to be re-readable months later.
`outcome` is a closed vocabulary (`no_machine_reachable_source`, `access_blocked`) so that
verdicts can be counted; `routes_checked` and `recheck_after` are mandatory.

A held source is **not** deactivated — the re-check depends on it still being cleared to
fetch — and it does not vanish. `capture-all` skips it until `recheck_after`, prints
`held: <id> under <outcome> until <date>` and `sources held under a verdict: N of M`, and
the run record carries `sources_held`. `status` reports `held under a verdict` and
`due for re-check`. Once the date passes the source is swept again; if it refuses again,
it refuses as loudly as before, and a new verdict is a new decision.

### Is it working?

```bash
uv run lovspor observatory status
```

```
Sources
  registered: 201
  active:     198

Last sweep
  started:    2026-08-24T03:01:00+00:00
  finished:   2026-08-24T04:17:00+00:00
  duration:   1h16m
  completed:  196 / 198
  refused:    2
  capped:     0
  held:       0
  withdrawn:  0
  captured:   47 | unchanged: 4218 | deferred: 36
  selection:  off
  status:     DEGRADED

Cadence
  target:     24h00m
  age:        18h47m
  deadline:   36h00m
  state:      OK

Scheduled triggers
  schedule:   daily 03:00 Europe/Oslo
  running:    since 2026-09-26T05:15:40+00:00 (16h00m)
  dropped:    2 in the last 14 days
    2026-09-23T03:00+02:00  held by the sweep started 2026-09-22T05:10:23+00:00
    2026-09-25T03:00+02:00  held by the sweep started 2026-09-24T01:00:30+00:00
```

It exits 1 when no sweep has *begun* inside the deadline, so the same command
serves a monitor. Two details are deliberate:

- **Age is measured from the start of the last sweep, not its finish.** A sweep
  that began 35 hours ago and ran for two has still not begun a new observation
  in 35 hours; measuring from the finish would hide precisely the slow run the
  deadline exists to catch.
- **Never swept reads as OVERDUE, never as OK.** That is the machine-was-off
  case, and it is the one a naive check misses.

The deadline is 36h rather than 24h: room for sleep/wake and one long run,
without letting two whole days pass unnoticed.

**Scheduled triggers** closes the other silence (issue #218). launchd will not
start a second instance of a label that is still running, so a sweep that
outlives a day swallows the next 03:00 trigger and nothing records it. `status`
reconstructs them: every 03:00 Europe/Oslo instant of the last 14 days that fell
strictly inside a recorded run (start to finish) or inside the sweep running now
is listed with the start of the sweep that held the job. A sweep in progress has
no run record until it finishes; the host's exclusive workload lock (#169) names
it and its start, and is believed only while its pid is alive — so `running` is
this host's view, and a status read from another machine shows none. The
section is information, not an alarm: it does not change the exit code. The
schedule is a constant in `src/lovspor/observatory/triggers.py` that mirrors
`StartCalendarInterval` in the plist; a unit test fails when the two drift, so
moving the job's hour means changing both.

## Scheduled runs (production)

`.github/workflows/sync.yml` runs daily at **04:00 UTC (~05:00–06:00 CET)** — about 2.5 hours after Lovdata's nightly tarball drop at ~01:30 UTC. Manual reruns are available via the **Actions → Sync legal corpus → Run workflow** button on GitHub.

A run that fails or is cancelled files the issue **Nightly sync did not complete**, with the run link and the `Run lovspor sync` outcome; each repeat comments on the open issue instead of filing another (#790). The sync step stops at 105 minutes, inside the job's 120, so a long run fails its step and the alert still runs. Read the log before re-running: a keyed run that ran long may have spent embedding budget on work it then lost (#802). Close the issue once a run completes.

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

**Operational requirement: the attestation registry is part of corpus
acquisition.** `get_temporal_events` reads `reconciliation` from git
notes under `refs/notes/temporal-attestations`, written by the sync gate
(ADR-0012 point 2). A plain clone, fetch or pull does not transfer notes
refs — and `unattested` means "no proof was recorded for this state",
never "this checkout did not fetch the proof". So a checkout whose
registry was never synchronised **fails closed with a typed
`AttestationError`** rather than serving a false `unattested`.

`lovspor fetch-corpus` satisfies the contract on every supported clone
and update: it configures the notes refspec (glob form, so a pre-gate
origin without the ref does not break fetch/pull) and fetches it. A
checkout acquired some other way — the hosted droplet's refresh path
included — must be synchronised once by hand; every later fetch/pull
then carries the registry:

```bash
git -C <corpus-path> config --add remote.origin.fetch \
  '+refs/notes/temporal-attestations*:refs/notes/temporal-attestations*'
git -C <corpus-path> fetch origin
```

### The temporal gate epoch (ADR-0012 Amendment 1)

`unattested` is only served for a state that **predates the gate**. Each
temporal parser version has one immutable gate-epoch record in
`refs/notes/temporal-attestations-epoch` (`parser_version`, `epoch_at`,
`boundary_commit`, `recorded_at`, `source`, `evidence`), noted on the last
pre-epoch corpus state. The glob refspec above transports it — no refspec
change is needed, and none may be added: `fetch-corpus` removes any refspec
line that mentions the attestation namespace without transporting the
attestation ref itself. `get_temporal_events` then answers, in order:
channel failure → `AttestationError`; attested → `attested`; **no epoch
record for the serving parser version → `AttestationError`**; author date
before the epoch → `unattested`; at or after it → `UnattestedGateStateError`
(see `mcp.md`).

Two supported writers, both `lovspor temporal-epoch --corpus-path <clone>`.
Input is validated before anything is fetched, written or pushed (only the
read-only `--corpus-path` check runs git first), and a malformed value exits 2:
`--corpus-path` must be the top level of a lovverk clone (git + `manifest.json`),
`--sync-run` a positive decimal GitHub run id (it becomes the immutable
`evidence`), `--boundary-commit` a full 40-hex commit id, `--epoch-at` a UTC
instant (`Z` or `+00:00`). The parser version is always the engine's own
`TEMPORAL_PARSER_VERSION`, never an argument:

- `record-sync-run --sync-run <run id>` — run by the sync workflow at run
  start, before any corpus work, and pushed at once, so a failed gate still
  leaves the epoch on origin. It writes only on the **first run under a new
  `TEMPORAL_PARSER_VERSION`** (no record and no attestation under it). With
  attestations but no record it warns and writes nothing: that record is a
  backfill. It never blocks the sync (`continue-on-error`).
- `backfill --epoch-at <UTC instant> --boundary-commit <sha> --sync-run <run id> [--apply]`
  — the operator's record of an epoch that predates this mechanism, for the
  engine's own parser version. **Dry run by default**: it fetches the
  attestation and epoch refs from `origin`, validates the record exactly as
  the write would, prints it, and writes nothing. `--apply` writes the note
  and pushes the epoch ref, so it needs a clone with push rights to lovverk.
  It refuses a boundary commit whose author date is not before `epoch_at`,
  an `epoch_at` later than the first attested state under the version, and
  a different record for a version that already has one (records are
  immutable; an identical re-run is a no-op).

**Operator order at ship (parser version 2 backfill, #432)** — in this order:

1. Merge the implementation PR.
2. Backfill the version-2 record from a writable lovverk clone on the
   merged engine. Dry run first, read the printed record, then apply:

   ```bash
   uv run lovspor temporal-epoch --corpus-path ~/Programming/Python/lovverk backfill \
     --epoch-at 2026-09-04T08:45:36Z \
     --boundary-commit 3c4f1865e420135a12956e122a3ec42c96803986 \
     --sync-run 33854986231
   uv run lovspor temporal-epoch --corpus-path ~/Programming/Python/lovverk backfill \
     --epoch-at 2026-09-04T08:45:36Z \
     --boundary-commit 3c4f1865e420135a12956e122a3ec42c96803986 \
     --sync-run 33854986231 --apply
   ```

   (`epoch_at` is the start of sync run 33854986231, the first version-2
   gate run, which attested `964e4fe`; `3c4f186` is the last state before
   it.)
3. Refresh the droplet corpus clone, so it carries the record:

   ```bash
   ssh root@100.77.85.60 'systemctl start lovspor-fetch-corpus.service && journalctl -u lovspor-fetch-corpus --no-pager -n 10'
   ```
4. Deploy the engine to the droplet (`deploy/digitalocean/README.md`
   § Operating it).

With steps 3 and 4 reversed, `get_temporal_events` answers
`AttestationError` (no epoch record) until the refresh — it fails closed and
never serves a false `unattested`; the other tools are unaffected. Later
parser bumps need no backfill: the first sync run under the new version
records its own epoch, so trigger the sync right after merging, or deploy
after the scheduled run has refreshed the clone.

## Hosted MCP: usage metrics (issue #479)

`lovspor mcp-http` counts every tool call in memory and, once per UTC hour,
writes the finished hour as one line to stderr, which systemd puts in the
`lovspor-mcp` journal:

```
lovspor.metrics {"hour":"2026-09-30T13:00:00Z","calls":812,"ok":790,"errors":3,"refused":{"service_capacity":17,"rate":2},"p50_ms":41.0,"p95_ms":930.5,"by_tool":{"get_law":402,"semantic_search":88},"active_credentials":6}
```

- `calls` = `ok` + `errors` + the sum of `refused`. `errors` is a call that
  was admitted and failed (an unknown slug, a bad argument).
- `refused` is keyed by the brake that fired (`src/lovspor/quota.py`):
  `service_capacity` (the instance-wide `max_in_flight`, the resize signal),
  `service_daily`, `service_paid`, and per caller `in_flight`, `rate`,
  `daily`, `paid`, `unknown_credential`, `unidentified`.
- `p50_ms` / `p95_ms` are nearest-rank latencies over admitted calls only;
  `null` in an hour with none.
- `active_credentials` is the number of distinct callers in that hour, a
  count only. It cannot be summed across hours; the report shows the peak.

Aggregate only, as the privacy page states: no query text, no arguments, no
credential ids, no IP addresses. Hours with no calls write nothing. The line
is written within a minute after the hour turns, and on shutdown, so a
restart splits its hour into two lines (the report lists both and sums them).
The journal keeps them for 30 days (`deploy/digitalocean/journald-retention.conf`).

Today's numbers, from the owner's machine:

```bash
ssh root@100.77.85.60 'journalctl -u lovspor-mcp --since today -o cat | grep lovspor.metrics | /opt/lovspor/app/.venv/bin/lovspor ops usage'
```

`journalctl --since` reads the droplet's local time; the hours in the report
are UTC. `lovspor ops usage` also takes a saved file (`lovspor ops usage
journal.txt`) and `--since today|all|YYYY-MM-DD` to filter by UTC day. Metrics
are recorded only by the hosted server; `lovspor mcp` (stdio) records none.

## Forarbeider: fetch the listed pages (issue #738)

`lovspor ops forarbeider-fetch` fetches the regjeringen.no Prop. L / Prop. LS pages the owner lists, and nothing else. It replaces saving each page by hand (PR3B contract, Amendment 1, points 6 and 8). It publishes nothing, and the pages it stores are read by the offline commands below.

```bash
install -d -m 700 ~/forarbeider-staging
cat > ~/forarbeider-staging/urls.txt <<'URLS'
# Prop. 112 L (2025-2026)
https://www.regjeringen.no/no/dokumenter/prop.-112-l-20252026/id3172654/?ch=1
https://www.regjeringen.no/no/dokumenter/prop.-112-l-20252026/id3172654/?ch=10
URLS
uv run lovspor ops forarbeider-fetch --urls ~/forarbeider-staging/urls.txt --out ~/forarbeider-staging/pages \
  --terms-url "$TERMS_URL"   # the owner-named regjeringen.no terms page; omit to read no terms
```

- **Only the list, only the grammar.** Each line is a Prop. L / LS landing page or one `?ch=N` chapter page on `https://www.regjeringen.no/no/dokumenter/`. A tracking parameter, another document type, another host or plain HTTP refuses the whole list before any request, with `url_not_allowed`. Blank lines and `#` comments are skipped, and repeats are dropped. `--max` (default 50, at most 200) caps a list. Nothing is discovered: no links are followed and no sitemap is read.
- **robots.txt.** It is read live once per run from `https://www.regjeringen.no/robots.txt`, with no automatic redirect. A 301/302/303/307/308 is followed by hand for at most five hops, each to `https` on `www.regjeringen.no` and spaced like any request; a missing `Location`, another host, scheme, port or user, or a sixth hop refuses the run (`robots_unreadable: redirect_off_host` / `too_many_redirects`). A redirect body is never read as robots. A 403 or 429 refuses the run before any page request (`robots_refused: <status>`). Any other 4xx (the usual 404) means no rules are published: the run goes on, nothing is stored, and the record says `absent`. A 5xx, a timeout, a body over 512 KiB, a body that is not UTF-8 or any other status refuses the run (`robots_unreadable: <detail>`).
- **Website terms.** With `--terms-url`, one owner-named page on `https://www.regjeringen.no` (no query, fragment or dot segments; anything else refuses before any request with `terms_url_not_allowed`) is read once, after robots.txt, under its rules and spacing, following no redirect. A 200 stores it; a 403 or 429 stops the run, and every listed URL is recorded as `not_attempted`; any other 4xx records the terms as `absent`; a 3xx, 5xx, transport failure, oversized body or a robots.txt disallow records them as `unreadable`.
- **Politeness.** A disallowed URL is recorded as `robots_disallowed` and never requested. Requests are at least 5 s apart, longer if robots.txt sets a longer `Crawl-delay`. A 403 or 429 stops the run, and the remaining URLs are recorded as `not_attempted`. Every request carries `User-Agent: lovspor-forarbeider/1 (+https://lovspor.no; kontakt@lovspor.no)`.
- **Exact bytes as served.** Every request sends `Accept-Encoding: identity`. A body with no content coding is stored and hashed byte for byte as it came off the wire (HTTP/1.1 chunked framing is not content; no charset decoding or normalisation), so the same served content always has the same digest and the size cap bounds real bytes. A body that still arrives with a `Content-Encoding` other than `identity` is never decoded or stored: a page records `content_encoded`, robots.txt refuses the run (`robots_unreadable: content_encoded`), and the terms read as `unreadable`. A compressed body's digest would depend on the server's compressor, and inflating it is how a small response becomes an unbounded one.
- **What is stored.** Each page is stored as served, as `<sha256>.html`, owner-only (0600). The output directory must be absolute, owner-only (created 0700 if missing) and outside every Git checkout. Redirects are recorded with their `Location` and never followed. A body over 16 MiB is never stored, not even in part. A published robots.txt is stored as served as `robots-<sha256>.txt`, and terms as `terms-<sha256>.html`, both 0600 and written once: a second run that finds a stored copy whose bytes no longer match its name refuses (`evidence_mismatch`). Beside them, `fetch-run-<start>.json` (0600, schema 2) records the spacing, per URL the outcome, status, `retrieved_at` (the machine clock, with offset), digest and size, and for robots.txt and the terms page the requested URL, every redirect hop (URL, status, `Location`), the final status, the state (`published`, `absent`, `refused`, `unreadable`), the retrieval time, and for a stored body its SHA-256 and size. That is the current source proof a standing policy is compared against (Amendment 1 point 8). `check_source_proof` refuses a record no fetch run writes (`source_proof_inconsistent: <fault>`): a page under the wrong kind, robots.txt requested from any URL but `https://www.regjeringen.no/robots.txt`, a redirect chain that does not start there or does not chain hop to hop on `https://www.regjeringen.no`, more than five redirects, a final hop whose status differs from the record's, a terms page outside the `--terms-url` grammar or with more than one request, a state its status contradicts, times that run backwards, or another User-Agent. That is the record's integrity, not authentication: nothing proves regjeringen.no served these bytes, and anyone who can write the owner-only staging directory can write a consistent record. That directory is the trust boundary. An absent page is never evidence. Robots access does not grant reuse rights.
- **Output.** One line each for robots.txt and the terms page (`<kind> <state> <url> status <status> at <when>`, plus `file <path>` for a stored body: pass that path as policy evidence), one line per URL, then a summary. For a stored page the line gives what `forarbeider-metadata` and `forarbeider-chapter` need: `fetched <url> retrieved_at <when> file <path>`. Exit 0 means every URL was stored. Exit 2 means some were not (each named with its outcome). Exit 1 is a refusal with nothing fetched (`refused: <reason>: <detail>`).
- **Not here.** This command does not review, clear or publish anything. A fetched page is still raw source in private staging, under the retention rules below.

## Forarbeider: offline staging metadata (issue #738)

`lovspor ops forarbeider-metadata` reads one regjeringen.no Prop. L / Prop. LS chapter page the operator already saved, and writes one validated metadata record. It makes no HTTP request and publishes nothing. ADR-0018 is Accepted as architecture, not as clearance: live fetching, a `lovverk/forarbeider/` dataset and any publication stay gated (PR3).

```bash
uv run lovspor ops forarbeider-metadata \
  --html ~/forarbeider-staging/raw/prop-54-ls.html \
  --source-url 'https://www.regjeringen.no/no/dokumenter/prop.-54-ls-20252026/id3154578/?ch=1' \
  --retrieved-at 2026-10-31T09:00:00+01:00 \
  --output ~/forarbeider-staging/regjeringen-3154578.json
```

- **Input is a whole saved `?ch=N` page, at most 16 MiB.** The landing page has no chapter navigation, so a landing URL is refused with `chapter_page_required`. A missing, unreadable or larger file is refused with `input_unreadable` or `input_too_large`, a failed write with `output_write_failed`.
- **`N` must be the page's own.** Its `data-longdoc-prev` and `data-longdoc-next` links must point to the chapters either side of `N` (chapter 1 links back to the landing page, the last chapter has no next link), or it is refused with `page_chapter_unproved`: the same bytes never pass for two chapters. The check reads what the saved page says. It cannot prove who saved the file or when, so `--retrieved-at` and the bytes stay the operator's word, and a record is not a chapter citation. The last-chapter markup was read on Prop. 54 LS and Prop. 112 L: no next link, as checked here.
- **A page saved after the site's scripts ran is refused** with `browser_rendered_page`. regjeringen.no's long-document script cuts a subheader longer than 150 characters and wraps the rest in a `button[data-show-doctitle]`; the served page has the plain title. A browser's "Webpage, Complete" save keeps the rewritten tree, so the subheader no longer equals `DC.Title`. Save the page source instead ("Webpage, HTML Only" in Chrome, "Page Source" in Safari) from the URL without tracking parameters: `?ch=1&utm_source=…` is refused with `unexpected_query`, by design. Only this one script change is recognized. A short title is never rewritten, so a complete save of such a page can pass while other script changes (the collapsed navigation toggle, for one) are still in it, and then `source_sha256` covers the browser's serialization, not the served bytes. A subheader that just disagrees with `DC.Title` stays `conflicting_identity`.
- **A cut-off capture is refused** with `incomplete_page`: the source must reach `</body></html>`, close every list and item of the chapter navigation, and have the page footer after it. `chapters` is then the navigation of one complete saved page. It is not a claim about every chapter the proposition has. `source_url` in the record is the canonical document URL and `source_page_url` is the page the hash covers.
- **Output** must be an absolute `.json` path outside every Git repository: an ancestor holding `.git`, the engine repository and the default corpus path are refused with `unsafe_output_path` before anything is written.
- **The record is owner-only (0600)** whatever the umask, set on the staged file before it is renamed in. Rerunning replaces a record only if no one else could have read it: one at the path that others can reach (group or other permission bits, a second hard link, not a regular file, not yours) is refused with `observation_unsafe` and left exactly as it was, mode included. Nothing here runs `chmod` on it: check who could reach it first, then delete it or `chmod 600` it yourself. Records written before #783 are typically `0644`, so the first rerun over one is refused this way.
- **`--retrieved-at`** is required, with an offset, and stored in UTC. The command has no clock.
- **`published_at` is `null`.** The page states no publication date, and `DC.Date` matched the tilråding date in the samples, so it is not treated as one.
- **Rights:** `source_license` is always `null` and `rights_basis` records `åndsverkloven § 14`. That is a statutory qualification, not a licence, and never NLOD. The record is not legal clearance.
- **Chapters** are the source's own navigation, in order. Annexes (`Vedlegg`) are listed there too; `chapter_number` is the `ch` value, not the printed label.

Exit 1 prints `refused: <reason>: <detail>` and writes nothing. The reasons are in `src/lovspor/forarbeider/errors.py`. Keep saved pages out of Git: raw source HTML is never committed.

## Forarbeider: edition evidence from the landing page (issue #784)

The edition notice (corrected edition, original edition, rettebrev) appears only on the landing page, never on a `?ch=N` chapter page. `lovspor ops forarbeider-edition` reads one saved landing page as edition evidence only and writes a sidecar beside the v1 metadata record. The landing page never goes through the chapter reader, and the v1 record is read, never rewritten, so release preflight's reproduction check is unchanged. Contract: `docs/proposals/forarbeider-edition-evidence-contract.md`.

```bash
uv run lovspor ops forarbeider-edition \
  --landing-html ~/forarbeider-staging/raw/landing/prop-54-ls-landing.html \
  --landing-url 'https://www.regjeringen.no/no/dokumenter/prop.-54-ls-20252026/id3154578/' \
  --retrieved-at 2026-11-05T08:52:12+00:00 \
  --metadata ~/forarbeider-staging/regjeringen-3154578.json
```

- **Save the landing page as served:** the page source, from the URL without `?ch=` or tracking parameters. A chapter URL is refused with `landing_page_required`; any other query with `unexpected_query`. The page must state the same document in `og:url`, `DC.Identifier.URL`, the `h1`, `og:title` and `DC.Title`, or it is refused with `conflicting_identity`.
- **What counts as a correction:** a `data-doc-meta="corrected-edition-date"` notice in the article header reading `(Korrigert utgave per D.M.YYYY)`, together with the `h3.edition` block linking exactly one original edition PDF (`/no/opri/`) and at least one rettebrev PDF (`/no/rett/`). The stated date becomes `edition.corrected_on`. Both parts were read off the real Prop. 54 LS landing page on 2026-11-05.
- **The page must be whole.** It must close (`</body></html>`) and hold exactly one `footer.page-footer`, a direct child of `body` after `main`, as on the real page. A missing, duplicated, wrapped or misplaced footer is refused with `incomplete_page`, so a cut page never reads as `unknown`.
- **The edition list is read whole.** The block must be exactly `h3.edition` + `ul.link-list`, and every item exactly `li > a` with nothing else in it. Any other item, wrapper, text or second list is refused with `edition_unrecognized`, never skipped. Several rettebrev links are kept in source order.
- **`unknown` is the default.** A page with neither part records `unknown`, never `original`: no observed page states that it is the original edition, so the schema refuses `original` until one does. `DC.Date` is the statsråd date and is never read as a correction date.
- **Fail closed.** One part without the other, two notices, two blocks, two original editions, a link labelled against its target or a heading promising a rettebrev without one is refused with `edition_conflicting`. An unfamiliar notice, date, heading or link, and edition wording or a `/opri/` or `/rett/` link anywhere else on the page, is refused with `edition_unrecognized`: a notice we cannot read must not pass as "no notice".
- **Association:** `--metadata` must be a private v1 record (`observation_unsafe` otherwise) stating the same document id, type, session, number and canonical URL, or the command is refused with `edition_identity_mismatch`. The evidence names that record's `source_page_url`, `source_sha256` and `retrieved_at`.
- **`chapter_edition` is always `unknown`.** The landing page and the chapter page are separate captures, so nothing proves which edition the chapter bytes are, or which chapters a correction changed. Only a byte difference between two observations of the same chapter could show that.
- **Provenance:** `landing_retrieved_at` keeps the operator's offset, and `landing_sha256` covers the saved bytes. Both are the operator's word (`landing_provenance: operator_saved_offline`). The command has no clock and makes no request.
- **Output** is always beside the record, `<record>.edition.json` (here `~/forarbeider-staging/regjeringen-3154578.edition.json`), under the same path rules as the record: an absolute path outside every Git repository, the engine and the default corpus path, or `unsafe_output_path`. The evidence is an observation: written once, owner-only (0600) whatever the umask, never replaced. A rerun with identical inputs reports `unchanged`, and different evidence at the same path is refused with `observation_exists`. It lands beside the record, never in `chapters/`, where `review candidates` reads every `.json` as a chapter.
- **No release:** the evidence carries no review, release, licence or publication field, and nothing in release preflight reads it. A v1 reader refuses it with `schema_invalid`.

## Forarbeider: one held chapter in private staging (issue #738)

`lovspor ops forarbeider-chapter` extracts the one chapter a saved `?ch=N` page holds into a held staging result. It makes no HTTP request, publishes nothing and clears nothing: ADR-0018 PR2. Reviewer decisions are recorded separately, by `ops forarbeider-review` below.

```bash
uv run lovspor ops forarbeider-chapter \
  --html ~/forarbeider-staging/raw/prop-54-ls-ch9.html \
  --source-url 'https://www.regjeringen.no/no/dokumenter/prop.-54-ls-20252026/id3154578/?ch=9' \
  --retrieved-at 2026-10-31T09:00:00+01:00 \
  --output ~/forarbeider-staging/chapters/regjeringen-3154578-ch9-20261031.json
```

- **The page checks of `forarbeider-metadata` come first**, with the same refusals and 16 MiB limit. A wrong chapter, a cut-off file or a foreign URL gives exit 1 and no file.
- **Every result is `release_state: held`** and lists `review_required`. That is the only state schema version 1 can express; nothing in this command approves a chapter.
- **The body is all or nothing.** `blocks` holds headings, paragraphs, list items and quotations in source order, read from `div[data-longdoc-content]` only. The result has `blocks: null` and `text_sha256: null` when the chapter has an annex, image, table, figure or footnotes, an ambiguous boundary, unfamiliar markup (`damaged_dom`), text that was not all read (`incomplete_extraction`), content marked hidden, or a body the parser had to repair. A proved page always gives a result, so a withheld chapter is recorded rather than lost; exit 0 does not mean text was extracted. Read the printed reasons.
- **`unsupported_structure`** means valid markup that blocks cannot state without turning it into something else. A list item is one text block, optionally followed by one deeper list. A second paragraph in an item, an item with no text of its own, and two lists or two quotations with nothing between them are all withheld, because they would flatten into the same blocks and hash as a different structure.
- **`hidden_content`** means an element in or around the body says `hidden`, `aria-hidden="true"`, or an inline `display:none` or `visibility:hidden`. Classes and style sheets are not read, so content hidden that way is not detected.
- **`malformed_body`** means the tags of the body as saved differ from the tree lxml built, which is what a repair looks like. Leaving out `</p>` or `</li>` is valid HTML and still withholds: it cannot be told from damage, and all nine sampled pages close every element. Such a page gives a held result without a body, not a refusal.
- **Quotations and text candidates keep the body** for the reviewer: `quotation`, `third_party_work`, `hearing_participants`, `personal_data`, `sensitive_data`. The candidates are simple text patterns. Finding none does **not** mean a chapter is free of personal data or third-party work, and finding one is not a legal assessment.
- **`text_sha256`** covers the normalized text, one `kind level<TAB>text` line per block, so a wrapper change leaves it alone and a text or structure change moves it. Only HTML source whitespace is collapsed; no-break spaces and every other character stay as published.
- **An observation is never replaced.** The file is created owner-only (0600). Rerunning identical inputs reports `unchanged`. Different content at the same path is refused with `observation_exists`: give each observation its own path, such as one ending in the retrieval date. An existing file is only ever read. If it is not a private regular file with one name, owned by you, the run stops with `observation_unsafe` and changes nothing, even when the bytes match: its chapter text may already have been readable, so check who could reach it before running `chmod 600` on it.
- **Chapter text is sensitive staging data.** The result holds chapter text in clear, so keep it out of Git, logs, CI artifacts and issue or PR comments. The command prints only the id, counts, reasons and path.
- **Limits:** `source_license` is `null`, `rights_basis` records `åndsverkloven § 14`, `normative` is `false`, never NLOD. Markup was read on nine pages of two propositions of session 2025-2026. A chapter of an older shape is withheld, not approximated. `retrieved_at` and the bytes stay the operator's word.

## Forarbeider: private review decisions (issue #738)

`lovspor ops forarbeider-review` records what a reviewer decided about one staged chapter. It is ADR-0018 PR3A: offline, and it permits nothing. **An approval is not permission to publish or crawl.** No command here fetches, indexes, publishes or moves a chapter, the observation stays `release_state: held`, and every record says `grants_publication: false` and `grants_crawling: false` by type. Public release is PR3B, which needs its own architecture, privacy and legal gates and the owner's approval. § 14 is not NLOD, and the § 24 and personal-data questions for bulk or public use stay open.

```bash
install -d -m 700 ~/forarbeider-staging ~/forarbeider-staging/chapters
review() { uv run lovspor ops forarbeider-review --ledger ~/forarbeider-staging/review-ledger "$@"; }

# What is waiting, and the pin each decision needs
review candidates --staging ~/forarbeider-staging/chapters

# One observation: what it binds, its status, every revision
review show --observation ~/forarbeider-staging/chapters/regjeringen-3154578-ch9-20261031.json

# Hold it; --expect is copied from the line above
review --reviewer reviewer-1 --decided-at 2026-11-01T12:00:00+01:00 \
  hold --observation ~/forarbeider-staging/chapters/regjeringen-3154578-ch9-20261031.json \
  --expect <subject>@0 \
  --because awaiting_evidence=issue:738

# Approve, at a terminal only: needs all four attestations and a typed phrase
review --reviewer reviewer-1 --decided-at 2026-11-02T12:00:00+01:00 \
  approve --observation ~/forarbeider-staging/chapters/regjeringen-3154578-ch9-20261031.json \
  --expect <subject>@1 \
  --attest source_rights_policy=adr:0018 \
  --attest privacy_basis=sha256:<digest of a private note> \
  --attest third_party_checks=sha256:<digest of a private note> \
  --attest source_completeness=sha256:<digest of a private note>

# Check every chain and print each head digest
review verify
```

The ledger must be an absolute path to an owner-only directory outside every Git repository and outside the engine and corpus. Its parent must already exist.

**Status transitions.** A chapter with no decisions is `pending`.

| From | May become | Command |
| --- | --- | --- |
| `pending` | `held`, `rejected`, `approved` | `hold`, `reject`, `approve` |
| `held` | `pending`, `held`, `rejected`, `approved` | `reopen`, `hold`, `reject`, `approve` |
| `rejected` | `pending` | `reopen` |
| `approved` | `pending`, `held`, `rejected` | `reopen`, `hold`, `reject` |

A rejection cannot be approved in one step, and an approval is never refreshed in place: withdraw it, then decide again.

- **A decision binds one exact observation:** `source_page_url`, `source_sha256`, `chapter_number`, `text_sha256`, extractor and normalizer versions. Their digest is the subject in `--expect <subject>@<revision>`. Changed bytes or text are another subject, with no decisions, so an approval never carries over: the new observation is `pending`. Earlier subjects of a chapter stay in `verify`, listed by chapter id.
- **`--expect` is what the reviewer saw.** A different observation gives `review_subject_mismatch`. A revision that has moved on gives `review_conflict`: somebody else decided, so read `show` again. Rerunning the same decision with the same time prints `unchanged` and writes nothing.
- **An approval needs a person.** All four `--attest` kinds with evidence, a chapter that has a body, a real terminal on stdin, and the printed phrase typed back. Otherwise the run is refused (`review_attestation_missing`, `review_not_eligible`, `review_not_interactive`), nothing is written and the status stays what it was. A held result with a prompt or without one is treated alike: the detectors in `hold_reasons` are prompts, never clearance, and the attestations are the reviewer's own.
- **Records hold no free text.** Reasons are codes (`ReviewReason` in `src/lovspor/forarbeider/review_models.py`). Evidence is `adr:NNNN`, `issue:N`, `pr:N` or `sha256:<64 hex>`: keep a note in private storage and record its digest. `--reviewer` is a lowercase handle of at most 39 characters, so a name with a space or an email address is refused; use a role handle, not a personal name. A refused value is named by its field and never printed.
- **The ledger is append-only.** `<ledger>/<subject>/<revision>.json`, owner-only (0600 in 0700 directories), byte-identical for the same decisions. Each record states the digest of the one before it. Commands read the chain first and stop with `review_ledger_corrupt` on an edited, moved or missing earlier record, and with `observation_unsafe` or `review_ledger_unsafe` on a file or directory others can reach. Nothing here repairs a ledger or changes its modes.

**Retention and access for saved text.** Staged chapters hold source text in clear and may hold personal data, so:

- Keep them in owner-only directories on this machine, outside Git, shared drives, cloud-synchronised folders and CI. Encrypted disk is expected; the commands do not encrypt.
- Only the reviewer reads them. Do not paste chapter text into issues, PRs, logs or chat, and do not quote it in an evidence note kept outside private storage.
- Delete a staged chapter and its saved page when review ends, and at the latest 90 days after `retrieved_at`, unless an open review still needs it. A rejected chapter is deleted at once. Deleting them is a manual step: nothing here does it, and a later review needs a fresh observation.
- The ledger holds no chapter text and stays after the chapter is deleted, since it is the audit trail. It does hold reviewer handles and timestamps, so keep it private too and delete it with the pilot.
- Before any wider use, the owner reviews this period; it is an engineering default, not a legal assessment.

**Limits, unresolved.**

- The digests sit beside what they protect. Someone who can write as this user can cut a chain at its end, rebuild one whole, or edit the newest record until a later revision points at it. Copy the head digests from `verify` somewhere else to notice that; nothing here anchors them.
- `--decided-at`, `--reviewer` and the attestations are the operator's word. Times must not go backwards within a chain and are not checked against a clock. A handle is not an authenticated identity.
- The terminal check keeps cron, CI and pipes out. It cannot prove a person is there: a pseudo-terminal can pass it.
- `source_sha256` is copied from the observation, not recomputed: the saved page may be gone. `text_sha256` is recomputed from the blocks when the file is read.
- Owner-only modes protect against other local users, not against this user's own processes, root, backups or a stolen unencrypted disk.
- Retention is a written rule, not an enforced one.
- Nothing was reviewed for real here: the workflow is tested on synthetic chapters only.

## Forarbeider: private release preflight (issue #738)

`lovspor ops forarbeider-release` is ADR-0018 PR3B-1, the first slice of [the PR3B contract](proposals/forarbeider-pr3b-release-contract.md). It checks whether exact staged chapters *could* be released and writes a private report. **It publishes nothing.** No command here fetches, stages, renders or writes a `lovverk` file or any Git checkout, there is no `--force` or `--publish`, and every report says `publication_authorized: false`, `writes_corpus: false` and `asserts_complete_proposition: false`, whatever it found. A PR3A `approved` is one input among many: never sufficient alone.

```bash
install -d -m 700 ~/forarbeider-staging/release ~/forarbeider-staging/evidence
release() {
  uv run lovspor ops forarbeider-release \
    --workspace ~/forarbeider-staging/release \
    --ledger ~/forarbeider-staging/review-ledger "$@"
}
chapters() {
  release chapters \
    --metadata ~/forarbeider-staging/regjeringen-3154578.json \
    --observation ~/forarbeider-staging/chapters/regjeringen-3154578-ch9-20261031.json \
    --page ~/forarbeider-staging/raw/prop-54-ls-ch9.html "$@"
}

# 1. The case-specific dossier: one private file per gate, bound to chapters
chapters dossier --reviewed reviewer-1@2026-11-02T12:00:00+01:00 \
  --evidence publication_purpose@9=$HOME/forarbeider-staging/evidence/purpose.md \
  --evidence privacy_processing_basis@9=$HOME/forarbeider-staging/evidence/privacy.md \
  --evidence third_party_screening@9=$HOME/forarbeider-staging/evidence/third-party.md \
  --evidence source_rights@9=$HOME/forarbeider-staging/evidence/rights.md \
  --evidence source_completeness@9=$HOME/forarbeider-staging/evidence/completeness.md

# 2. The owner's authorization candidate, at a terminal; --dossier is the digest printed above
chapters authorize --dossier <dossier sha256> \
  --decided owner-1@2026-11-03T12:00:00+01:00 \
  --valid 2026-11-03T12:00:00+01:00/2026-12-01T00:00:00+01:00

# 3. The preflight; each --checkpoint is a `review verify` line kept OUTSIDE the ledger
chapters preflight --at 2026-11-04T12:00:00+01:00 \
  --authorization <authorization sha256> \
  --checkpoint <subject>@<revision>:<head>

# Inspect or withdraw
release show report <report sha256>
release revoke --authorization <authorization sha256> --decided owner-1@2026-11-05T12:00:00+01:00
```

Exit 0 is an `eligible_preview` report, exit 2 a `deny` report, exit 1 a typed refusal with nothing written. Records live in `<workspace>/{dossiers,authorizations,revocations,reports}/<sha256>.json`, owner-only, named by the digest of their bytes and never replaced: rerunning the same preflight prints the same lines and writes nothing new.

**What each chapter needs, or it is denied.**

- **A body proved by its page.** The saved page is parsed again and must reproduce the PR2 result byte for byte, so `source_sha256`, `text_sha256` and the extractor version are proved, not copied. The PR1 metadata must be reproduced by its own page too, list the chapter at its URL and title, and name the same document as every other input. A withheld body (annex, table, image, footnotes and the rest of `WITHHOLDING`) is denied, with its hold reasons in the report.
- **A current PR3A approval with an external anchor.** The chain must verify, end in `approved`, and match a `--checkpoint` you copied from `review verify` and kept elsewhere: a cut or rewritten tail otherwise passes. A later decision of any kind, even a new approval, makes the authorization's pin stale (`review_superseded`).
- **Evidence files for every gate.** `publication_purpose`, privacy (`privacy_processing_basis`, or a reviewed `privacy_no_personal_data` finding), `third_party_screening`, `source_rights` and `source_completeness`, each a private file bound to that chapter. Its bytes are read again and must match the digest recorded in the dossier. `adr:0018`, `issue:N`, `pr:N` and a bare `sha256:` are not files and are refused. A no-personal-data finding cannot stand alone on a chapter whose extractor flagged personal-data candidates.
- **The owner's authorization.** Scoped to exactly these chapter identities and review pins, this dossier, its purpose file and the one path `forarbeider/<session>/<prop-l|prop-ls>/<document id>.md`. It holds from `valid_from` up to, not including, `valid_until`, and not once revoked.
- **One authorized purpose, bound to the chapter.** Version 1 authorizes a single purpose. `authorize` stores the digest of the one intact `publication_purpose` file bound to *every* chapter of the dossier. It refuses with `release_not_eligible: publication_purpose` when no such file exists, and also when a second, different purpose is declared for any of the chapters, even one that covers only some of them, so it never takes whichever came first. Preflight checks each chapter again: every purpose bound to that chapter must be the authorized one, intact, or the chapter gets `purpose_not_authorized`. A purpose bound to chapter 2 never clears chapter 3. Separate purposes per chapter would need a reviewed schema change.
- **Decisions in order.** Every chapter's latest PR3A approval must come no later than the owner's decision, the owner's decision no later than `--at`, and the dossier review no later than the owner's decision. `authorize` refuses a decision out of that order with `release_out_of_order` (`dossier`, or the chapter id), and records nothing. Preflight denies a stored authorization that breaks the order: `authorized_before_dossier` for the whole document, and `authorized_before_approval` or `evaluated_before_approval` for the chapter. Equal instants are in order. Times are compared as instants, so `12:00+02:00` comes before `11:00Z`. Only an approval is ordered: a later hold or rejection already denies the chapter as `review_not_approved`. A re-approval after the owner's decision is both `review_superseded` and `authorized_before_approval`.

Only the chapters that pass are planned. The report is then a `partial_curated_excerpt`, or `every_listed_chapter` when the page's navigation lists nothing more; neither asserts that the proposition is complete.

**Printed and stored.** Ids, digests, reason codes and counts. Never chapter text, evidence content, file paths, file names or handles, in output or in reports. A refusal prints its reason and the role of the input it concerns, such as `refused: observation_unsafe: metadata`; the roles are `metadata`, `observation`, `page`, `ledger`, `workspace`, `evidence <gate>`, `actor`, `valid` and `at`, and a stored record is named by its digest or folder. Find the file from the role and the command line you ran. The dossier itself keeps evidence paths and the reviewer handle: keep the workspace private, under the retention rules above.

**Limits, unresolved.**

- Code checks that evidence exists, is bound to a chapter and has its declared bytes. Whether it is legally sufficient is the owner's judgement: `legal_sufficiency_decided_by_code: false`. §§ 14 and 24, personopplysningsloven and the source terms are not resolved by any test.
- The authorization is a private declaration. A handle, a typed phrase and a terminal are not authenticated identity (`identity_authenticated: false`), and a pseudo-terminal passes the terminal check.
- `--at` and every decision time are the operator's word; there is no clock. The order checks above catch an impossible chain, not a backdated one: anyone who can choose every timestamp can choose them in order. The PR3B-2 simulator below also ends an authorization by the machine clock, which is not a trusted clock either.
- Checkpoints are `operator_supplied_unauthenticated`: they detect a cut or rewritten chain only if they were kept where this user's processes cannot rewrite them too.
- A revocation is found by the authorization's digest in the same workspace; someone who can write there can delete it.
- Nothing was released for real: the workflow is tested on synthetic Prop. L and Prop. LS chapters only. PR3B-2 (promotion) and PR3B-3 (an opt-in reader) need separate approval.

## Forarbeider: synthetic promotion plan and simulator (issue #738)

ADR-0018 PR3B-2, scoped by the owner to code and **synthetic** tests only. It defines the isolated `forarbeider/` dataset (its own `manifest.json`, `INDEX.md` and `<session>/<prop-l|prop-ls>/<document id>.md`) and the engine that would write it, but **writes nothing real**: the only tree it can write is a disposable sandbox this command made, and only from the bundled synthetic Prop. 901 L and Prop. 902 LS. There is no `--publish` or `--force`, no command touches a `lovverk` clone, and nothing runs `git add`, `commit` or `push`. A real release needs its own owner, legal, privacy and source-terms gates first; the PR3B-3 reader below reads a synthetic sandbox only.

```bash
# The bundled synthetic propositions as staged files, owner-only, outside Git
release synthetic-fixtures --out ~/forarbeider-staging/synthetic
# ...then review, dossier, authorize and preflight them as in the section above.

# 1. The plan: re-runs the preflight the report came from, reads the corpus, writes no checkout
chapters plan --report <report sha256> --corpus ~/forarbeider-sandbox \
  --checkpoint <subject>@<revision>:<head>

# 2. A disposable sandbox: a new, empty Git checkout with a synthetic baseline, no remote
release sandbox-create --path ~/forarbeider-sandbox

# 3. Apply the plan to the sandbox, re-validated now
chapters simulate --plan <plan sha256> --sandbox ~/forarbeider-sandbox

# 4. Withdraw one document from the sandbox, forward only, at a terminal
release sandbox-withdraw --sandbox ~/forarbeider-sandbox \
  --document regjeringen-9000001 --decided owner-1@2100-04-12T12:00:00Z
```

Make the sandbox before the plan you mean to apply: a plan records the dataset state it was made against, and `simulate` refuses it against any other (`plan_stale: plan`).

- **`plan`** takes an `eligible_preview` report and the checkpoints it was evaluated against, and evaluates the preflight again from the private files as they are now, at the report's own `--at`. Anything different, even one chapter fewer, is a DENY (exit 2, `promotion_denied`): a held, rejected or reopened review, a revocation, a changed page, text, dossier or evidence file, a missing or other checkpoint. It then reads the corpus checkout's `forarbeider/` tree, which must be exactly what its manifest lists, and renders the manifest, index and document. Those bytes go to `<workspace>/previews/<sha256>.preview`, the plan to `<workspace>/plans/<sha256>.json`, both owner-only and outside Git. The checkout is never written, a real one included. A plan says `publication_authorized=false writes_corpus=false`.
- **`sandbox-create`** makes a new, owner-only directory outside every repository: the skeleton `git init` writes (unborn `main`, no remote, no commit), a marker inside `.git`, and placeholder `README.md`, `manifest.json`, `lover/`, `forskrifter/` and `lokale-forskrifter/` that every write checks unchanged. No Git command runs: this package never starts a subprocess.
- **`simulate`** refuses anything but a sandbox it made (a clone, a remote, an edited baseline, a symlink, a directory others can write), and a plan whose pages are not the bundled synthetic ones. It re-runs the preflight, ends the authorization by the machine clock against `valid_until`, and rebuilds the plan from the sandbox as it is now. Then it writes one change under an exclusive lock: the new bytes, and the previous bytes of every file replaced or removed, are staged inside `.git`, each staging file created and read through a no-follow descriptor (a symlink, FIFO or hard link there is refused, never followed), and a journal lists every operation and names its action and the plan behind it. Documents, index and manifest are each replaced whole, and the tree is read back and verified before the journal is removed. Every run re-checks the plan first, a retry included: an interrupted change of this plan is finished only while the plan still holds, and rolled back, every file put back as it was, once it is revoked, expired or otherwise denied. An applied plan retried after that is denied, not `UNCHANGED`; taking it down is a withdrawal. A journal of another plan or a withdrawal is refused and left alone. A journal can only name dataset files.
- **`sandbox-withdraw`** keeps the member, marked `withdrawn` with its `withdrawn_at`, removes its file and drops it from the index. It needs no grant: an interrupted release is rolled back first, whatever its plan's state, and an interrupted withdrawal finished; a journal naming no plan or intent in the workspace is refused. Before the sandbox changes it stores a withdrawal intent (owner, time, document, manifest digests before and after) in the workspace's `withdrawal_intents/`; the receipt in `withdrawals/` follows the change, under the intent's digest. If the receipt cannot be written the withdrawal still stands, and the next run of the same command writes that receipt from the original decision, once. A withdrawal that has its receipt is refused as `document_not_released`. It says, and the withdrawal record keeps (`history_erased: false`), that **Git history and every clone keep the earlier bytes; a revert does not erase them**. For a real corpus that is why the only safe choice for a doubtful chapter is to withhold it before release: taking down published bytes is the owner's decision and an escalation outside any command here.

**The dataset, version 1.** The manifest is the membership list, not a directory glob. Each member carries its identity (government document id, session, Prop. L or LS), `source_document_type: forarbeid`, `normative: false`, `source_provider: regjeringen.no`, `rights_basis: åndsverkloven § 14` and `source_license: null`, all fixed by type. It lists per-chapter receipts (page, `source_sha256`, `text_sha256`, extractor and normalizer versions, `retrieved_at`, `review_decided_at`), the coverage, and separate times: `source_published_at` only as the PR1 metadata stated it, the dossier review, the owner's decision, the release and the withdrawal, all `operator_supplied_unauthenticated`. The corpus records a change at its Git commit time, which the file never states. There is no `valid_at`. A duplicate, a path that is not the document's own, an unknown key, another schema version, bytes not in canonical form, a contradictory receipt, a dangling member or a stray file are refused.

**The rendering.** Markdown only from the pinned PR2 text blocks, in source order, each chapter under its page URL. Front matter and a banner say it is preparatory material, NOT enacted law, that it does not state whether the proposal was adopted, and, unless every listed chapter is released, that it is a partial curated excerpt and not the complete proposition. The source text is escaped so it cannot open a link, image, HTML or a block. A URL outside the regjeringen.no grammar, or a title or block holding a control, line separator or bidi override, is refused. Same inputs and renderer version, same bytes: there is no clock in the renderer.

**Limits, unresolved.** No rights, privacy or source-terms clearance is established by this code or its tests, and actor, times and checkpoints stay unauthenticated. The sandbox is the operator's own directory: someone who can write it can edit its journal or tree, which the next run then refuses rather than repairs.

## Forarbeider: opt-in synthetic reader (issue #738)

ADR-0018 PR3B-3, scoped by the owner to code and **synthetic** tests only. A separate, offline, read-only command reads the `forarbeider/` dataset of **one sandbox made by `sandbox-create`**, named on every call. Nothing else sees forarbeider: `get_law`, `corpus_status`, law and body search, MCP, the site, indexes, embeddings and benchmarks never import it and answer the same whether the dataset is absent, released, corrupt or unsafe. There is no default path, no environment switch and no discovery.

```bash
lovspor ops forarbeider-read --sandbox ~/forarbeider-sandbox list
lovspor ops forarbeider-read --sandbox ~/forarbeider-sandbox show \
  --document regjeringen-9000001 --session 2099-2100 --type prop-l
lovspor ops forarbeider-read --sandbox ~/forarbeider-sandbox chapter \
  --document regjeringen-9000001 --session 2099-2100 --type prop-l --chapter 2
lovspor ops forarbeider-read --sandbox ~/forarbeider-sandbox provenance \
  --document regjeringen-9000001 --session 2099-2100 --type prop-l
```

- **What it serves.** Only members the manifest lists as `released`; a `withdrawn` one never, even though it was released before (`document_not_released`). A document is named exactly by its government id, session and type: no law id, title or path lookup, and anything else is `document_unknown`. A chapter that is held, unlisted or not released is `chapter_not_released`.
- **What it proves first.** `require_sandbox` (the marker, no remote, the baseline, owner-only), no change in progress (a lock or journal is `corpus_locked`), and the whole `forarbeider/` tree exactly as its manifest lists it: a stray file, a dangling member, an edited index, other bytes than the manifest's digest, a symlink, hard link, FIFO, oversized file, wrong schema or duplicate member are refused. The manifest may be schema 1 or 2; schema 1 is read as schema 2 with each chapter `owner_review`, `provenance: manifest_v1` (see the batch plan section), and any other version is `manifest_incompatible`. Then each member is rendered again from the bundled synthetic pages: its manifest entry must restate that rendering (only release status, times and clearances are the entry's own word, re-validated) and its file must be byte-identical to it, or it is `input_not_synthetic`. That refuses a forged entry, an injected link or front matter line, malformed UTF-8, and any real document. The bytes printed are the ones whose digest was checked, read once.
- **What it prints.** One JSON record per call: `forarbeider_reader_list` (`schema_version: 1`), `_document`, `_chapter` or `_provenance` (`schema_version: 2`: each receipt carries its `clearance`, and the times carry `policy_decided_at`), each `synthetic: true` with the notice that a forarbeid is preparatory material, NOT enacted law. `show` gives identity, the labels (`forarbeid`, `normative: false`, `regjeringen.no`, `åndsverkloven § 14`, `source_license: null`, `asserts_complete_proposition: false`), coverage and `partial_curated_excerpt`, every released chapter's receipt and the separate time axes (source published, reviewed, owner decided, released; all `operator_supplied_unauthenticated`, the corpus time being Git's). `chapter` prints that one chapter's synthetic Markdown under its source URL. `provenance` gives the source receipts and the manifest and document digests it verified. No reviewer handle, dossier, evidence, authorization or raw page is ever printed. A refusal is `refused: <reason>: <detail>` on stderr, exit 1, a path shown as `input`.
- **Writes nothing.** No file, cache, index, HTTP, subprocess or background job; the reader's modules hold no write call at all (tested).

**Limits.** Reading a synthetic sandbox is **not** approval to publish or host real forarbeider text, and the reader refuses anything but the bundled synthetic propositions. Checksums prove which bytes these are, not whether they may be published: åndsverkloven § 14 and § 24, personopplysningsloven and the source's terms are decided by the owner, outside any command. A real pilot needs its own owner, legal, privacy and source-terms gates and a separately reviewed change.

## Forarbeider: private real-document pilot readiness (issue #738)

ADR-0018 PR4. The question here is narrow: can **one** Prop. L and **one** Prop. LS that the owner saved by hand go through the existing checks, offline and in private, and what stands between them and a release? Nothing in this section publishes, fetches, crawls or writes a Git checkout, and nothing in it is release clearance. Only the owner authorizes a real release, and this PR does not.

**Status of the real pilot: `REAL_INPUT_NOT_PROVIDED`.** No real page was supplied to this change, so every result below comes from the bundled synthetic Prop. 901 L and Prop. 902 LS. A real run is the owner's, with the steps below, and its outcome is recorded by the owner, never inferred from these tests.

### What the supported commands already carry

| Step | Supported command | Real saved pages | Missing in code | Needs a person |
| --- | --- | --- | --- | --- |
| Saved page to metadata | `ops forarbeider-metadata` | yes, offline, 16 MiB, page and chapter proved | nothing | choosing and saving the page; `--retrieved-at` is the operator's word |
| Page to held chapter | `ops forarbeider-chapter` | yes; withheld body on annex, table, image, footnotes, hidden or repaired markup | nothing; markup read on two propositions of 2025-2026 only | reading every hold reason |
| Review | `ops forarbeider-review` | yes, append-only private ledger | nothing | the reviewer's decision and attestations |
| Case-specific evidence | `forarbeider-release chapters dossier` | yes, private evidence files bound to chapters | nothing | writing each evidence note (checklist below) |
| Owner authorization candidate | `chapters authorize` | yes, at a terminal, typed phrase | nothing | the owner alone |
| Preflight | `chapters preflight` | yes; re-proves every page, text, chain, checkpoint and evidence digest | nothing | keeping checkpoints outside the ledger |
| **Per-chapter audit** | **`chapters pilot-audit`** (this PR) | yes | was missing: one PASS / HOLD / DENY per chapter with digests and URLs | reading it; a PASS is not clearance |
| Private plan | `chapters plan` | yes; renders into `<workspace>/previews/`, owner-only, writes no checkout | nothing | none; its preview holds chapter text, keep it private |
| Sandbox write | `chapters simulate` | **no**: synthetic pages only (`input_not_synthetic`) | by design | a future, separately approved PR |
| Reader | `ops forarbeider-read` | **no**: synthetic sandbox only | by design | a future, separately approved PR |

So the private real path ends at `plan`: a private preview in the workspace, no corpus. Every step after that refuses a real page, and this PR keeps it so.

### The pilot audit

```bash
chapters pilot-audit --at 2026-11-10T12:00:00+01:00 \
  --authorization <authorization sha256> \
  --checkpoint <subject>@<revision>:<head> --checkpoint <subject>@<revision>:<head>
```

That is the synthetic Prop. 901 L with only chapter 2 in the dossier and the grant, digests shown as `<sha256>`, exit 2: the owner left chapter 3 out, so it is DENY. It takes the inputs `preflight` takes, evaluates them with the same code, stores the same private report under `<workspace>/reports/<sha256>.json` (the digest printed is the preflight's own) and prints:

```text
PILOT_AUDIT regjeringen-9000001 prop-l 2099-2100 report <sha256> publication_authorized=false release_clearance=false
counts listed 4 audited 2 pass 1 hold 0 deny 1 coverage partial_curated_excerpt
chapter 2 PASS source <sha256> text <sha256> url https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=2 reasons -
chapter 3 DENY source <sha256> text <sha256> url https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=3 reasons dossier_chapter_missing,evidence_incomplete,missing:privacy,missing:publication_purpose,missing:source_completeness,missing:source_rights,missing:third_party_screening,not_authorized,purpose_not_authorized
technical audit only: PASS is not release clearance
```

- **PASS** is a chapter the preflight finds eligible.
- **HOLD** is a chapter whose every reason is something nobody has given yet: `review_missing`, `review_not_approved`, a missing evidence gate (`missing:<gate>`), `evidence_incomplete`, `checkpoint_missing`, `dossier_missing`, `dossier_chapter_missing`, `authorization_missing`, `authorization_not_yet_valid`, `purpose_evidence_missing`, `purpose_not_authorized`, `observation_missing`, and `body_withheld` (the extractor withholds the body, so it waits for a later extractor or stays out). Three of these also cover something given, and the audit reads the same verified inputs the preflight read to tell which: `review_not_approved` is HOLD for a pending or held review and DENY when the latest decision of the chapter's verified chain is a rejection; `purpose_not_authorized` is HOLD when the dossier declares no purpose for the chapter and DENY when it declares one that is not the owner's; `purpose_evidence_missing` is HOLD when the dossier declares no purpose at all and DENY when it holds one that is not the owner's.
- **DENY** is everything else: a page or text that changed, a forged or edited evidence file or record, a foreign chapter or document, a rejected or superseded review, a revoked or expired grant, a broken chain, a checkpoint that does not match. **A chapter the owner left out is DENY** (`not_authorized`, which only arises when an authorization exists and does not name the chapter): the owner excluded it, it is not waiting for consent. The chapters the owner did authorize still PASS, the report stays a `partial_curated_excerpt` with only those chapters planned, and nothing about the excluded chapter is authorized. Only a missing authorization altogether (`authorization_missing`) is HOLD. A reason code added later is DENY until it is classified (`tests/unit/test_forarbeider_pilot_verdicts.py`). This reading changes only what the audit prints: the stored report, its digest and what the preflight plans are the preflight's own (`tests/unit/test_forarbeider_pilot_triage.py`). **A missing saved page is DENY:** pages are found by the digest their observation recorded, so a page whose bytes drifted looks exactly like one that was never given.
- Exit 0 only when every audited chapter is PASS; otherwise exit 2 after the report is written; exit 1 is a typed refusal with nothing written. One call audits one document. The pilot set is two explicit calls, one per document; there is no selection, discovery or batch mode.
- Printed: ids, digests, counts, reason codes and the chapter's source URL. Never chapter text, evidence content, file paths or names, or handles (tested for both document types).

### Runbook: one Prop. L and one Prop. LS, in private

1. **Before saving anything,** the owner records the human checklist items 1 and 2 below. Unknown means stop.
2. **The owner picks** one recent Prop. L and one recent Prop. LS on regjeringen.no and, for each, the chapter pages (`…/id<document>/?ch=N`) to try. Nothing chooses them for you; no command lists, fetches or follows pages.
3. **Save each chosen page by hand, as served,** into `~/forarbeider-staging/raw/` (`install -d -m 700` first, outside Git, outside cloud-synchronised folders), one file per `?ch=N` page, and `chmod 600` it. Note the retrieval time with its offset. Do not edit a saved file: if `forarbeider-metadata` refuses it (`incomplete_page`, `page_chapter_unproved`), save it again.
4. **Per document:** `forarbeider-metadata` on one of its pages, then `forarbeider-chapter` on each chosen page, then review each chapter with `forarbeider-review` (sections above). Copy every `review verify` head to a place this user's processes cannot rewrite.
5. **Write the evidence notes** for the checklist below as private files under `~/forarbeider-staging/evidence/`, then `chapters dossier`, and, only if the owner so decides at a terminal, `chapters authorize`.
6. **Audit:** `chapters pilot-audit` once for the Prop. L and once for the Prop. LS. Keep only the printed lines; the report stays in the workspace.
7. **Optional:** `chapters plan` for a PASS document, against an empty sandbox or a corpus copy you do not care about, to see the private preview. Nothing is written to any checkout.
8. **Stop.** Record the outcome (PASS, HOLD or DENY per chapter, with the report digest) in the owner's private log. Delete the saved pages and staged chapters when the pilot ends, and within 90 days of retrieval at the latest (retention rules above).

**STOP gates.** Stop and decide as the owner, never route around, when: any checklist item is unknown (HOLD); `forarbeider-chapter` withholds the body; any chapter is DENY; the page's markup is older or different from what the extractor read; a page names private persons; or anything would put chapter text into Git, CI, an issue, a PR, a log or a chat. The audit itself never asks to proceed.

### Human-only checklist, per document, with dated evidence

Each item is a private, dated note, written and judged by a person. Code checks that a note exists, is bound to the chapter and has its recorded bytes, never whether it is right. Claude does not attest any of these and does not act as the owner. **Unknown = HOLD.**

1. **regjeringen.no terms and access policy** read on the date of saving: robots and terms for manual saving and for later redistribution, and who to contact (`source_rights` note).
2. **Rights:** åndsverkloven § 14 for the document type and § 24 for the database or catalogue, including whether a curated excerpt changes either (`source_rights` note).
3. **Third-party material:** annexes, quoted works, images, tables and media screened, each chapter (`third_party_screening` note).
4. **Personal data:** a purpose and basis under personopplysningsloven, or a reviewed finding that the chapter holds no personal data; a chapter the extractor flagged cannot rest on a no-personal-data finding (`privacy_processing_basis` or `privacy_no_personal_data` note).
5. **Source integrity:** the saved bytes are the page as served on that date, the chapter list matches, nothing hidden (`source_completeness` note).
6. **Takedown and contact, retention and Git permanence:** how a takedown request is received and handled, how long anything is kept, and that a release into Git cannot be erased from history or clones. No evidence gate checks this separately: keep it in the `publication_purpose` note, which the owner's authorization binds.
7. **Separate owner sign-off** for the release itself, after 1 to 6: `chapters authorize` records a candidate only; a real release needs a separate, reviewed change.

### What a future real-data PR needs

A separate PR, separately approved, would have to: allow a reviewed real document set through `simulate` into a staging checkout (today synthetic only); decide where a real dataset lives and who may push it; extend the reader beyond the bundled synthetic propositions with the same labels; add a takedown runbook that accepts Git history permanence; and carry the owner's dated sign-off for exactly those documents. Until then no real chapter body belongs in any Git repository or branch, including tests, fixtures, PR text and CI logs.

## Forarbeider: standing release policy (issue #738, contract Amendment 1)

[Amendment 1](proposals/forarbeider-pr3b-release-contract.md#amendment-1-2026-11-09-standing-policy-automatic-clearance-batch-release) replaces the per-chapter ceremony for chapters no detector flags with one owner record, signed once. This slice (A2a) only records, revokes and shows that policy. **Nothing here releases a chapter**: the batch plan that uses it (below) only plans, its apply is a later slice, and a flagged chapter keeps the per-chapter review, dossier and authorization path whatever the policy says.

```bash
uv run lovspor ops forarbeider-release --workspace <ws> --ledger <ledger> policy sign \
  --evidence source_rights=/abs/source-rights.md \
  --evidence publication_purpose=/abs/purpose.md \
  --evidence retention_deindexing=/abs/retention.md \
  --evidence git_permanence_accepted=/abs/git-permanence.md \
  --evidence privacy_basis_clean=/abs/privacy-clean.md \
  --evidence robots_txt=<fetch out>/robots-<sha256>.txt \
  --evidence terms_page=<fetch out>/terms-<sha256>.html \
  --evidence unflagged_unread_acceptance=/abs/unflagged-unread.md \
  --terms-url "$TERMS_URL" \
  --decided <handle>@2026-11-10T12:00:00+01:00 \
  --valid 2026-11-10T12:00:00+01:00/2027-02-07T00:00:00+01:00
```

- Every kind is required, once, and no file (by digest) may be bound for two kinds. Each file must be absolute, owner-only, outside every Git checkout and non-empty; its digest and size are read from the file. `publication_purpose` carries the takedown contact (`kontakt@lovspor.no`) and who answers it.
- `robots_txt` and `terms_page` are the exact files `forarbeider-fetch` stored (its report prints their paths), never a browser copy: only those bytes can match a later run's digests. `--terms-url` is the page that run read the terms from (same grammar as `forarbeider-fetch --terms-url`); the policy records it, sign prints it before the phrase, and it is inside the digest the phrase carries. Sign does not check that the terms file came from that URL: it makes no request, and that pairing is the owner's assertion. What it buys is the comparison below: a current proof must come from the same URL, so an unchanged copy of the old terms served elsewhere cannot stand in for the page the owner read.
- `privacy_basis_clean` is the owner-approved personal-data assessment: the processing basis under personopplysningsloven and the residual risk accepted for chapters no detector flags. Detector silence is not proof that a chapter holds no personal data.
- Every acceptance is the owner's own file, never a value set in code: `git_permanence_accepted` for Git-history permanence and `unflagged_unread_acceptance` for publishing a chapter no detector flags without a person reading it.
- The scope is fixed: `regjeringen.no`, `prop-l` and `prop-ls`. The record states only `clears: unflagged_chapters`, `legal_sufficiency_decided_by_code: false` and `identity_authenticated: false` of itself.
- Before the prompt, sign prints the source, the document types, the validity window, every bound kind with the first 12 hex of its SHA-256, and the first 12 hex of the SHA-256 of the exact record it will store (`policy <sha12> ...`). At a terminal only, the owner types `sign standing policy <sha12> regjeringen.no prop-l,prop-ls until <valid_until in UTC>`. Any changed byte in any bound file, or a different `--decided`, changes `<sha12>`; an old or wrong phrase writes nothing. The record is stored as `<ws>/policies/<sha256>.json`, whose digest starts with the `<sha12>` shown, and sign prints that digest.
- **At most one effective policy.** Sign refuses with `policy_overlap: <sha256>` while an unrevoked policy for the same source, sharing a document type and with an overlapping window, is stored: revoke it first. A window that only touches (one ends when the next starts) does not overlap. Sign never overwrites a record: re-signing a revoked policy unchanged is refused with `release_already_revoked`, and a new policy decided before an overlapping policy's revocation is refused with `release_out_of_order`.

```bash
... policy show <policy sha256> --at 2026-12-01T12:00:00+01:00 --source-proof <fetch out>/fetch-run-<start>.json
... policy revoke --policy <policy sha256> --decided <handle>@<ISO-8601>
```

`show` re-reads every evidence file and every stored policy, and prints `state usable` or `state faults <codes>`: `evaluated_before_policy`, `policy_not_yet_valid`, `policy_expired`, `policy_revoked`, `policy_evidence_changed`, `policy_evidence_unavailable`, `policy_ambiguous` (two unrevoked overlapping policies for the source are stored, which only a hand-placed file can cause; every evaluation for that source denies), `policy_overlaps_revoked` (an overlapping policy was revoked after this one was decided). Any fault and the policy clears nothing. Revocation is final and revokes exactly one record; a revocation cannot be dated before the policy's decision. A revoked or expired policy is never reinstated, and revoking one never brings another into effect: sign a new one, decided at or after the revocation. `state` judges the record alone: its local evidence, window and the register.

Then `show` judges the **current source proof** (Amendment 1 point 8). `--source-proof` names a `forarbeider-fetch` run record; show reads it (absolute, owner-only, outside every Git checkout), re-hashes the robots.txt and terms bytes stored beside it and refuses (`release_record_corrupt: source_proof <reason>`, or `release_invalid: source_proof` for a malformed record) if they changed. It also refuses a record no fetch run writes (`release_record_corrupt: source_proof source_proof_inconsistent: <fault>`, see `forarbeider-fetch`). These checks are integrity, not authentication: a consistent record whose bytes re-hash proves the record is intact and could have come from a fetch run, never that regjeringen.no served it. Anyone who can write the owner-only staging directory can write such a record, so that directory is the trust boundary. It makes no network request. It prints `source proof missing` or `source proof run <start> <kind> <state> <sha256> ...`, then `source matches` or `source faults <codes>`: `source_proof_missing`, `source_proof_stale` (the oldest page is more than 24 hours older than `--at`, or a page was read after `--at`), `robots_not_published` / `terms_not_published` (absent, refused, unreadable, or no terms page read), `robots_changed` / `terms_changed` (the digest differs from the signed evidence), `terms_url_changed` (the proof read the terms from another URL than the policy names, whatever their bytes). The last line is `automatic clearance allowed` only when the record has no fault and the source matches; otherwise `automatic clearance denied`. Intact local files never stand in for the current proof: a changed terms page denies until the owner fetches it and signs again.

## Forarbeider: private batch plan (issue #738, contract Amendment 1, A2b)

[Amendment 1](proposals/forarbeider-pr3b-release-contract.md#amendment-1-2026-11-09-standing-policy-automatic-clearance-batch-release) points 2–4 and 7. `batch plan` decides, for at most 25 propositions, which chapters a later batch release could carry and by which path. **It publishes, authorizes, confirms and applies nothing**: the plan states `publication_authorized: false`, `writes_corpus: false`, `applies: false` and `owner_confirmation: required_not_given` by type, and no command in this slice reads a plan to write anything. The batch confirmation and apply are slice B1.

```bash
uv run lovspor ops forarbeider-release --workspace <ws> --ledger <ledger> batch plan \
  --request /abs/private/batch.json \
  --policy <policy sha256> \
  --source-proof <fetch out>/fetch-run-<start>.json \
  --at 2026-12-01T12:00:00+01:00
uv run lovspor ops forarbeider-release --workspace <ws> --ledger <ledger> batch show <plan sha256>
```

The request is the owner's private JSON file (absolute, owner-only, outside every Git checkout; its paths are never copied into the plan or printed):

```json
{
  "schema_version": 1,
  "record_type": "forarbeider_batch_request",
  "target_branch": "forarbeider/batch-2026-12-01",
  "documents": [
    {"metadata": "/abs/staged/p1-metadata.json",
     "observations": ["/abs/staged/p1-ch2.json", "/abs/staged/p1-ch3.json"],
     "pages": ["/abs/pages/p1-ch2.html", "/abs/pages/p1-ch3.html"]},
    {"metadata": "/abs/staged/p2-metadata.json",
     "observations": ["/abs/staged/p2-ch2.json", "/abs/staged/p2-ch3.json"],
     "pages": ["/abs/pages/p2-ch2.html", "/abs/pages/p2-ch3.html"],
     "review": {"report": "<eligible preflight report sha256>",
                "checkpoints": ["<SUBJECT>@<REVISION>:<HEAD>"]}}
  ]
}
```

`review` is needed only for a document with flagged chapters: the report is the stored `eligible_preview` from `chapters preflight` over that document, and the checkpoints are the ones that preflight used (`forarbeider-review verify` prints them).

**Options.** `--request` the file above; `--policy` the standing policy's sha256 (`policy sign` printed it); `--source-proof` one `forarbeider-fetch` run record, the current source proof (point 8) and the proof of which pages were fetched; `--at` the moment evaluated, ISO 8601 with offset (no clock is read, so the same inputs give the same plan).

**What it checks, per chapter.** Each chapter is classified from its own hold reasons and body:
- `withheld` (no body, or annex, image, table, figure, footnotes, ambiguous boundary, damaged DOM, unsupported structure, hidden content, malformed body, incomplete extraction): always excluded, `withheld`.
- `flagged` (quotation, third-party work, hearing participants, personal data, sensitive data) and `unknown` (any hold reason the code has not classified): only the per-chapter path. The stored report must be `eligible_preview` and plan the chapter (`preflight_not_eligible`), be about the same document with the same chapter id, page and text digests (`preflight_changed`), and the preflight evaluated again at `--at`, under the report's authorization and the given checkpoints, must still find the chapter eligible: approval, dossier, evidence, checkpoint, an authorization valid and unrevoked at `--at` (its deny codes are listed). No report is `preflight_missing`. An approval alone never clears. The included chapter carries `cleared_by: owner_review` with the review pin, authorization and report digests.
- `clean` (a body and nothing but `review_required`): only the standing policy. Every preflight reason blocks it except those of the per-chapter path (dossier, authorization, evidence, review status, checkpoint), so a staged page that changed (`source_page_missing`), a result its page no longer reproduces, including another extractor or normalizer output (`observation_not_reproducible`), a changed metadata page, a chapter outside the inventory or a corrupt review chain each exclude it. The policy and the source proof must have no fault (`policy_expired`, `policy_revoked`, `policy_ambiguous`, `policy_evidence_changed`, `source_proof_stale`, `robots_changed`, `terms_changed`, `terms_url_changed`, …, as `policy show` lists them), the policy must name the document type (`policy_scope_mismatch`), the chapter's exact page URL and SHA-256 must be a `fetched` record of the source-proof run (`page_not_fetched`), and a person's latest held or rejected PR3A decision vetoes it (`review_veto`): a human no is never overridden. The included chapter carries `cleared_by: standing_policy` with the policy digest and the source-proof run's digest.
- **No fallback.** A clean chapter excluded by the policy is never cleared by a report covering it, and a flagged chapter is never cleared by the policy.

**Refusals** (exit 1, nothing written; `refused: <reason>: <detail>` on stderr, a path shown as `input`): `batch_empty`, `batch_too_large` (more than 25 documents, refused before any staged file is read), `target_branch_invalid` (not `forarbeider/[a-z0-9][a-z0-9-]{0,62}`; `main`, `master` and `HEAD` never match), `document_duplicate` (two entries for one document, source id or destination), `chapter_duplicate`, `chapter_foreign` (an observation of another document), `schema_invalid` (a request, metadata or result not its schema, extractor or normalizer version 2 and an edited id included), `input_unreadable` / `unsafe_input_path`, `release_record_missing` (policy or report not stored), `release_record_corrupt` (an edited policy, report or source proof whose stored robots/terms bytes no longer re-hash), `release_invalid` (a malformed digest, checkpoint or source-proof record).

**Output and exit.** Exit 0 when at least one chapter is included, exit 2 when none is; the plan is written either way. Printed: `BATCH PLAN <sha> documents <n> included <i> (standing_policy <p>, owner_review <r>) excluded <e>`, the target branch with `publication_authorized=false applies=false owner_confirmation=required_not_given`, the policy, source-proof and inputs digests, one line per document (id, destination, coverage) and per chapter (number, id, class, `included standing_policy policy <sha12>`, `included owner_review report <sha12> pin <key12>@<revision>` or `excluded <codes>`), then up to five `sample <chapter id> <source URL> (optional spot check, not a gate)` lines. Never chapter text, evidence, handles or a private path. `batch show <sha>` prints the same lines from the stored plan and refuses an edited one (`release_record_corrupt`).

**Storage and determinism.** The plan is `<ws>/batch_plans/<sha256>.json`, owner-only, named by the digest of its exact bytes and never replaced; planning the same request again writes nothing new. `inputs_sha256` is the digest of every staged byte and named record the plan read (metadata, results, pages, review chain heads, report, checkpoints, policy, source proof, `--at`, target branch), never a path; the request's document order does not matter. The C3 sample is the five policy-cleared chapters with the lowest `sha256(inputs_sha256 + "\n" + chapter_id)`, so the same inputs always give the same sample and any changed input reshuffles it. The plan's validator recomputes the sample, the counts and the coverage. Planning reads only private files, makes no request, starts no process and has no corpus or checkout argument: `lovverk` and the sync clone are never touched.

**Manifest schema 2** (`corpus_models.py`; nothing writes it yet, the batch apply in B1 will). Each chapter receipt carries `clearance`, one of:
- `cleared_by: standing_policy`, `policy_sha256`, `source_proof_sha256`, `individually_reviewed: false`; no review field is allowed;
- `cleared_by: owner_review`, `review_decided_at`, `review` (subject key, revision, head), `authorization_sha256`, `report_sha256`, `provenance`, `individually_reviewed: true`; no policy digest is allowed. `provenance: recorded` has all three references; `provenance: manifest_v1` has all three `null`.

Times add `policy_decided_at`, `null` exactly when no chapter was policy-cleared, as `reviewed_at` is `null` exactly when none was reviewed; both are at or before `owner_decided_at`. `renderer_version` moves to each document. The labels are unchanged and fixed by type: `forarbeid`, `normative: false`, `regjeringen.no`, `åndsverkloven § 14`, `source_license: null`, `asserts_complete_proposition: false`. A version 1 manifest is read as version 2: v1 was written only by the per-chapter path, so each chapter becomes `owner_review` with the review time v1 recorded and `provenance: manifest_v1`, and the pin, authorization and report v1 never recorded stay `null`, never filled in. The synthetic reader (`forarbeider-read`) reads both versions this way.

**Limits.** A plan is code checking evidence that exists and is bound to a chapter; legal sufficiency stays the owner's decision (`legal_sufficiency_decided_by_code: false`). Detector silence is not proof a chapter holds no personal data or third-party work; `hearing_participants` fires on any *høring*, so many real chapters will be flagged.

### Acceptance matrix (A2b)

| Requirement | Test |
|---|---|
| Every hold reason in exactly one class; withheld set is staging's | `tests/unit/test_forarbeider_batch_classify.py::test_every_hold_reason_is_in_exactly_one_set`, `::test_the_withheld_set_is_the_staging_withholding_set_itself` |
| All classifications, built results | `tests/unit/test_forarbeider_batch_classify.py::test_each_member_alone_after_review_required_has_its_class`, `::test_a_built_result_is_classified_from_its_own_reasons_and_body` |
| Mixed reasons, withholding precedence, missing body, unknown | `tests/unit/test_forarbeider_batch_classify.py::test_mixed_reasons_take_the_strictest_class` |
| Only flagged/unknown take the review path | `tests/unit/test_forarbeider_batch_classify.py::test_only_flagged_and_unknown_need_the_per_chapter_review` |
| Deny reasons partitioned; exclusion codes disjoint | `tests/unit/test_forarbeider_batch_clearance.py::test_every_deny_reason_is_in_exactly_one_set`, `tests/unit/test_forarbeider_batch_models.py::test_exclusion_reason_values_are_disjoint_across_their_four_sources` |
| Clean cleared by a valid policy alone | `tests/unit/test_forarbeider_batch_clearance.py::test_clean_chapters_are_cleared_by_a_valid_policy_alone`, `tests/unit/test_forarbeider_batch_cli.py::test_a_mixed_batch_clears_each_chapter_by_its_one_path` |
| Expired / revoked / ambiguous / not-yet-valid / evidence-changed policy; stale / changed source proof | `tests/unit/test_forarbeider_batch_clearance.py::test_any_policy_or_source_fault_excludes_every_clean_chapter`, `tests/unit/test_forarbeider_batch_cli.py::test_a_revoked_policy_clears_nothing_but_the_review_path_still_does`, `::test_nothing_included_writes_the_plan_and_exits_two` |
| Scope-mismatched policy | `tests/unit/test_forarbeider_batch_clearance.py::test_a_policy_not_naming_the_document_type_excludes_with_scope_mismatch` |
| Missing / corrupt source proof refused | `tests/unit/test_forarbeider_batch_cli.py::test_a_missing_or_edited_source_proof_is_refused`, `tests/unit/test_forarbeider_source_run.py::test_a_stored_source_page_edited_after_the_run_is_corrupt`, `::test_a_missing_or_malformed_run_record_is_refused` |
| Page not fetched in the proof run (Q3) | `tests/unit/test_forarbeider_source_run.py::test_only_fetched_pages_are_listed_by_url_and_digest`, `tests/unit/test_forarbeider_batch_clearance.py::test_a_page_the_proof_run_did_not_fetch_is_excluded`, `tests/unit/test_forarbeider_batch_cli.py::test_a_page_the_proof_run_did_not_fetch_is_excluded_by_name` |
| Held/rejected decision vetoes the policy (Q2) | `tests/unit/test_forarbeider_batch_clearance.py::test_a_held_or_rejected_decision_vetoes_the_policy`, `::test_approved_or_absent_history_does_not_veto_a_clean_chapter` |
| Withheld always denied | `tests/unit/test_forarbeider_batch_clearance.py::test_a_withheld_chapter_is_excluded_whatever_else_holds`, `tests/unit/test_forarbeider_batch_cli.py::test_withheld_and_flagged_chapters_without_a_report_are_excluded` |
| Unknown never policy-cleared | `tests/unit/test_forarbeider_batch_clearance.py::test_the_unknown_class_takes_the_review_path_and_never_the_policy` |
| No fallback, both ways | `tests/unit/test_forarbeider_batch_clearance.py::test_a_clean_chapter_with_a_faulted_policy_never_falls_back_to_a_report`, `::test_a_flagged_chapter_without_a_report_is_excluded_though_the_policy_is_valid` |
| Flagged: eligible report + fresh preflight clears; approval alone does not | `tests/unit/test_forarbeider_batch_clearance.py::test_a_flagged_chapter_with_an_eligible_report_is_cleared_by_review`, `::test_an_approved_flagged_chapter_without_dossier_or_checkpoint_is_excluded`, `::test_a_flagged_chapter_needs_its_checkpoint_at_plan_time`, `::test_a_review_after_the_report_supersedes_it`, `::test_an_authorization_expired_at_plan_time_excludes_the_flagged_chapter` |
| Report of another document / stale text hash | `tests/unit/test_forarbeider_batch_clearance.py::test_a_report_of_another_document_is_a_changed_preflight`, `::test_a_chapter_restaged_since_the_report_is_a_changed_preflight` |
| Stale source / text / metadata hashes, corrupt chain | `tests/unit/test_forarbeider_batch_clearance.py::test_a_page_changed_since_staging_blocks_a_clean_chapter`, `::test_a_result_its_page_does_not_reproduce_blocks_a_clean_chapter`, `::test_a_changed_metadata_page_blocks_every_clean_chapter`, `::test_a_corrupt_review_chain_blocks_a_clean_chapter` |
| Extractor/normalizer version mismatch, mismatched id | `tests/unit/test_forarbeider_batch_cli.py::test_a_result_of_another_code_version_or_identity_is_refused` |
| Cross-document mixing, duplicates | `tests/unit/test_forarbeider_batch_cli.py::test_a_chapter_of_another_document_is_refused`, `::test_the_same_document_twice_is_refused`, `::test_the_same_chapter_twice_is_refused`, `tests/unit/test_forarbeider_batch_models.py::test_a_document_planned_twice_or_out_of_order_is_invalid` |
| 25 ok / 26 refused / 0 refused | `tests/unit/test_forarbeider_batch_cli.py::test_twenty_five_documents_plan_and_twenty_six_are_refused_unread`, `::test_an_empty_batch_is_refused`, `tests/unit/test_forarbeider_batch_models.py::test_twenty_five_documents_are_a_plan_and_twenty_six_are_not` |
| Target branch grammar | `tests/unit/test_forarbeider_batch_models.py::test_a_branch_outside_the_grammar_is_refused`, `::test_a_branch_inside_the_grammar_is_accepted`, `tests/unit/test_forarbeider_batch_cli.py::test_a_target_branch_outside_the_grammar_is_refused` |
| Plan determinism (same → same bytes/digest; different → different) | `tests/unit/test_forarbeider_batch_clearance.py::test_the_same_inputs_give_the_same_plan_bytes_in_any_order`, `::test_a_changed_input_changes_the_inputs_digest_and_reseeds_the_sample`, `tests/unit/test_forarbeider_batch_cli.py::test_the_same_request_gives_the_same_plan_and_stores_it_once`, `::test_another_moment_is_another_plan` |
| Sample determinism; edited plan invalid | `tests/unit/test_forarbeider_batch_models.py::test_the_sample_is_the_lowest_keys_of_policy_cleared_chapters_only`, `::test_fewer_policy_cleared_chapters_than_the_sample_size_are_all_sampled`, `::test_an_edited_sample_or_count_is_invalid`, `tests/unit/test_forarbeider_batch_cli.py::test_show_prints_the_stored_plan_and_refuses_an_edited_one` |
| Plan authorizes and applies nothing | `tests/unit/test_forarbeider_batch_models.py::test_a_valid_plan_states_that_it_authorizes_and_applies_nothing`, `::test_a_plan_cannot_state_that_it_permits_anything`, `tests/unit/test_forarbeider_batch_cli.py::test_the_commands_state_their_options_and_that_they_authorize_nothing` |
| One clearance per included chapter, matching its class | `tests/unit/test_forarbeider_batch_models.py::test_an_included_chapter_has_exactly_the_one_clearance_its_class_allows`, `::test_a_flagged_chapter_may_carry_only_a_recorded_review_clearance` |
| Zero writes outside the workspace; no corpus, process or network | `tests/unit/test_forarbeider_batch_cli.py::test_planning_writes_only_its_plan_and_never_the_corpus`, `::test_the_batch_modules_hold_no_process_network_or_corpus_write` |
| No chapter text, evidence, handle or private path printed or stored | `tests/unit/test_forarbeider_batch_cli.py::test_no_chapter_text_evidence_handle_or_private_path_is_printed_or_stored`, `::test_a_malformed_request_is_refused_without_its_path` |
| Manifest v2 canonical bytes, determinism | `tests/unit/test_forarbeider_manifest_v2.py::test_the_v2_golden_round_trips_byte_exact`, `::test_the_serializer_gives_the_same_bytes_for_the_same_input_in_any_key_order`, `::test_a_different_input_gives_different_bytes` |
| Clearance variants: policy has digests and no review field; review has provenance and no policy digest | `tests/unit/test_forarbeider_manifest_v2.py::test_a_policy_clearance_carries_its_digests_and_no_review_field`, `::test_a_review_clearance_carries_review_provenance_and_no_policy_digest`, `::test_a_legacy_review_clearance_has_every_unrecorded_reference_null` |
| v2 labels and distinct time axes | `tests/unit/test_forarbeider_manifest_v2.py::test_a_label_that_is_fixed_by_type_cannot_change`, `::test_the_distinct_time_axes_survive_a_round_trip`, `::test_review_time_is_null_exactly_when_no_chapter_was_reviewed`, `::test_policy_time_is_null_exactly_when_no_chapter_was_policy_cleared` |
| v1 lift; `parse_manifest` stays v1-only | `tests/unit/test_forarbeider_manifest_v2.py::test_a_v1_manifest_is_lifted_with_owner_review_and_explicit_legacy_nulls`, `::test_parse_manifest_still_refuses_version_2`, `tests/unit/test_forarbeider_corpus_manifest.py::test_another_schema_version_is_incompatible` |
| Reader reads v1 and v2 | `tests/unit/test_forarbeider_reader_v2.py::test_a_v1_sandbox_is_served_with_lifted_owner_review_clearances`, `::test_a_v2_sandbox_serves_each_chapter_with_its_stated_clearance`, `::test_a_forged_v2_receipt_is_not_synthetic_whatever_clears_it` |

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
