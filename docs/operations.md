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
fragment: `vars lovspor_release <id>`, both roots and the corpus's own
redirect map, every path absolute and immutable). The cutover is one Caddy
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
uv run lovspor release commit <id>               # stage, validate, commit the fragment, reload, mark
uv run lovspor release reconcile [--complete|--abandon]          # name and resolve a crash state
uv run lovspor release rollback                  # the marker's previous, same transaction
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
  `retrieved_at` and no `observed_at_last`);
* `<authority_id>/observations/<slug>.json` — per version: content hash,
  first and last observation, observation count, primary and corroborating
  URLs, source blob, and `promotion`, the audit record: decision, the
  reviewer's role (`reviewed_by_role` — never the name), decision time,
  reason, `reviewed_in_sample`, classifier evidence (or `null`), extractor/renderer versions, source form, identity (scheme, id,
  ref-id, candidates), and the archive records it was read from;
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
git -C /absolute/path/to/lovverk add -- lokale-forskrifter
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
git -C /absolute/path/to/lovverk add -- lokale-forskrifter
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
git -C /absolute/path/to/lovverk add -- lokale-forskrifter
git -C /absolute/path/to/lovverk commit -m 'observe: refresh observation intervals (<n> documents)'
```

`observe` re-reads every current local document's version intervals from the
log — last observation, count, blobs, byte-identical copies, `source_status`,
excluded observations — and rewrites only the `observations/<slug>.json`
files that changed. It adds no version and touches no document or manifest; a
new text at the URL ends the current version's interval where the new text
begins and waits for a backfill. A document the log does not reproduce (another
text, another first observation, another extractor version — that is a
migration) is skipped with the reason. Use the subject it prints: `observe: …`
is no history event. Run it at most weekly (ADR-0016 Decision 3); scheduling
it is not automated yet.

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
are folded into one, with every page in its `sources`. The report shows it as
one line, "1 regulation, N pages", with the id, the `content_hash` and the
first pages; the JSON lists them all. It is held as
`batch:needs_canonical_source` until the owner settles which page is the
canonical source (#566), and it never enters the sample as more than one item.
The same id minted from **different** texts stays a real collision,
`batch:same_id_in_batch`, one hold per distinct text.

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
