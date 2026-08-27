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

`mcp-http` powers the optional operated (hosted) endpoint. It enforces **bearer-token authentication (revocable per-credential tokens) and per-credential rate limiting + quotas**. Optionally it also accepts **OAuth logins** via WorkOS AuthKit — set `LOVSPOR_AUTHKIT_DOMAIN` *and* `LOVSPOR_PUBLIC_URL` together (one without the other refuses to start; see [`mcp.md` § Authentication](mcp.md#authentication-two-modes)). The **app itself has no TLS** — the transport is plaintext, so a bearer token on an exposed port is sent in the clear. TLS terminates in a reverse proxy: Caddy with automatic Let's Encrypt, per the `deploy/digitalocean/` recipe, which **has been deployed since 2026-07-18** — the hosted instance is live at `https://lovspor.no/mcp`, running with **both credential modes active** (opaque tokens + WorkOS AuthKit OAuth; the AuthKit pair is configured there — verified against production 2026-08-02). If you run it yourself, keep it bound to localhost behind such a proxy and do not expose the app port to the internet. It serves `/mcp` plus unauthenticated `/healthz` and `/readyz` probes. See [`mcp.md` § Streamable HTTP transport](mcp.md#streamable-http-transport).

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

Required for `lovspor sync` to write per-section embedding `.bin` files (Sprint 9), and for the MCP `semantic_search` tool to embed user queries at runtime. Without a key the engine still produces Markdown and runs the rest of the sync pipeline normally — the only casualty is that `.bin` files for documents added or changed in this run will not be written, and the next sync with a key set picks them up via the Sprint 9 backfill migration. Missing key in the MCP server disables only `semantic_search` and leaves the other fifteen tools working normally. Cost is fractions of a cent per query and ~$5-15/year for the production sync cadence — see [`docs/embeddings.md`](embeddings.md) for the model choice rationale.

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
declares one. Permission to fetch is still not permission to redistribute —
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

**A candidate is skipped only when the site's `lastmod` predates an
observation already in the log.** Everything less certain than that is
fetched: no `lastmod`, an unreadable one, a URL never seen, a timestamp that
ties exactly. Re-fetching costs one request; skipping wrongly costs an
observation window that cannot be recovered.

That rule is also what makes an interrupted run need no resuming. Each
observation is appended as it happens, so running the command again picks up
where it stopped — the pages already captured now fail the freshness test. And
a second pass over a site that has not changed costs two requests rather than
thousands.

A damaged log is refused before anything is fetched: appending thousands of
records would bury the damage. Run `observatory verify` first.

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
 "captured":47,"failed_fetches":2,"unchanged":4218,"status":"degraded"}
```

| status | meaning | who records it |
| --- | --- | --- |
| `success` | every active source was swept to the end of its sitemap | `capture-all` |
| `degraded` | the sweep ran, and at least one source was refused **or capped** (a source held under a verdict does not degrade it — it is counted, not asked) | `capture-all` |
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

Each writes a `failed` run carrying its reason — **except `storage_unavailable`**, which
by definition has nowhere to write. The deferral is checked *after* the ground checks for
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
# edit __LOVSPOR_BIN__, __OBSERVATORY_ROOT__, __LOG_DIR__
launchctl load ~/Library/LaunchAgents/no.lovspor.observatory.nightly.plist
launchctl list | grep lovspor          # confirm it is registered
launchctl start no.lovspor.observatory.nightly   # one manual run, to prove the wiring
```

`RunAtLoad` is false on purpose: loading the job during setup must not start a sweep
against two hundred municipal servers as a side effect.

`StartCalendarInterval`, not `StartInterval` or cron: if the machine is asleep at 03:00,
launchd runs the job on wake and coalesces missed triggers. cron loses them silently.
A machine that is powered off gets no run at all — that is precisely the case the
dead-man switch exists for, and no scheduler can cover it from inside.

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
  captured:   47 | unchanged: 4218
  status:     DEGRADED

Cadence
  target:     24h00m
  age:        18h47m
  deadline:   36h00m
  state:      OK
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
