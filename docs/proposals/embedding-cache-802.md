# Durable embedding cache outside the published corpus (#802) — design

* **Status:** Proposed (design for owner approval). Not an accepted ADR: once
  approved, it is filed in `lovspor-notebook/docs/adr/` under the next free
  number, and this file points there.
* **Date:** 2026-11-06
* **Issue:** #802 (follow-up of #790, Task 1 "do not lose paid work")
* **Owner decision it implements:** #802, 2026-11-06: *option 3, a durable
  embedding cache outside the published corpus*. No partial corpus. Design
  first. `actions/cache` saved in a post-step is not assumed to survive a job
  timeout.
* **Binding ADRs:** ADR-0005 (embedding space identity, ESI), ADR-0006
  (embedding input identity), ADR-0012 (attestation of the sync head).
* **What this document authorises:** nothing in production. It asks for two
  decisions (§9). Code built from it stays off by default, and nothing in it
  uploads, spends or enables anything.

---

## 1. Problem, measured

A keyed `lovspor sync` writes every document's Markdown and LSPE sidecar into
the working tree, commits once at the end, and the workflow pushes in a later
step (`.github/workflows/sync.yml`, `Push corpus changes if any`). A run killed
before that step loses every vector it paid for. This happened twice:
run 39399830386 (2026-10-20) and run 39575627491 (2026-10-21), about $4.94
each (#790).

What has changed since #790 was filed:

* #803 refuses a change-path workload above the ADR-0006 §7 thresholds before
  the first provider call, unless `--allow-mass-reembed` is given.
* #815 refuses an unattestable keyed sync (a changed document that cannot
  render) before the first provider call.
* `Run lovspor sync` has `timeout-minutes: 105`, under the job's 120, so a
  step timeout still lets the alert step run.

What has not changed: a run that is allowed to spend, and then dies, still
loses everything it spent. That is the subject of this design.

## 2. Map of every paid embedding call

Found by `grep -rn "\.encode(" src/` on `main` `3b6b4288`. Line numbers are
from that commit.

| # | Path | Call site | Trigger | Paid unit |
|---|---|---|---|---|
| P1 | Sync change path: new, changed, renamed documents | `run_sync` → `_try_write_one` → `_write_one` → `_write_embeddings_for_doc` → `embedder.encode(...)` (`sync/orchestrator.py:1191`) | Scheduled and dispatched `lovspor sync` with a key | Every document that renders and is not a no-op re-render |
| P2 | Sprint 9 backfill (staleness repair) | `run_sync` → `_run_sprint9_embeddings_migration` → `_write_embeddings_for_doc` (`:1981`) → same `encode` at `:1191` | Any keyed sync whose `_select_embedding_backfill` finds a stale record (ADR-0005 §5, ADR-0006 §3) | Every stale current document |
| P3 | Repair | `lovspor repair-embeddings` (`mark_undersized_embeddings_stale`) clears `embedding_hash`; it calls no provider. The spend happens in P2 of the sync that follows | `repair_embeddings` dispatch input in `sync.yml` | As P2 |
| Q1 | Query embedding | `embeddings/query.py:180`, `mcp.py:1348` (`semantic_search`) | Each hosted or local query | One short string. Not a corpus write, and out of scope here (#790 audit) |

P1 and P2 meet in one function, `_write_embeddings_for_doc`. That function is
the single place where corpus vectors are paid for, and it is the single
boundary this design changes (§6).

### 2.1 What one paid unit is today

* `build_embedding_inputs(rendered)` yields the ordered `(section_id, text)`
  records. A section longer than 8,000 tokens becomes several chunks under the
  same `section_id` (`split_to_token_chunks`).
* `hash_embedding_inputs(inputs)` is the ADR-0006 identity of that whole
  ordered stream (`EMPTY_INPUT_HASH` for no inputs).
* `OpenAIEmbedder.encode(texts)` splits the texts into HTTP requests of at most
  128 inputs and at most 250,000 summed tokens (`_batches`). Each request gets
  up to 3 attempts, a 180 s timeout and a backoff of 2 s, then 4 s. Only
  429/500/502/503/504 and transport errors are retried. Then every row is
  L2-normalised.
* `quantize_int8(matrix)` computes **one scale for the whole document** (max
  absolute value / 127), and `write_embeddings` writes LSPE v2 with the ESI
  digest in the header (`settings.lspe_writer_version`, default 2).

So the unit the provider bills is one HTTP request. The smallest unit that can
be **published** is one document, because its scale depends on all of its
rows.

### 2.2 Identity facts the cache must respect

From ADR-0005 and ADR-0006, quoted where they bind:

1. ESI equality is the only compatibility test. There is no cross-ESI
   relation, and "Unknown is never optimistically treated as comparable"
   (ADR-0005 §Decision.4). Dimension equality is a shape check, never evidence
   of space.
2. ESI excludes the credential, quantization, chunking and the format version
   (ADR-0005 §Decision.1).
3. "ESI has never promised bit-identical provider output and does not now"
   (ADR-0005 Amendment 1). Batch composition moves the vectors. So a cached
   vector **cannot be validated by re-embedding**. It can only be validated
   against the identity it was stored under and the integrity of its bytes.
4. `embedding_input_hash` is stamped "at generation time, from the inputs it
   actually embedded" (ADR-0006 §Decision.2). With a cache, the inputs are
   recomputed from the current Markdown on every run, and a cached entry is
   used only when it was stored under that same hash. The stamp stays true.
5. A mixed LSPE v1/v2 corpus is forbidden (ADR-0005 §Decision.3). The cache
   stores vectors, not sidecars, and the writer still decides the format.

## 3. Design summary

1. **Key:** `(ESI, embedding_input_hash)` for a whole document. A long
   document also has **unit checkpoints** keyed by
   `(ESI, embedding_input_hash, unit index, unit count, unit input hash)`, where
   a unit is exactly one provider request of the embedder's own batching plan,
   and the unit input hash is `hash_embedding_inputs` over that unit's slice of
   the inputs. No new hash function: only `build_embedding_inputs` and
   `hash_embedding_inputs` (ADR-0006 §2, one canonical path).
2. **Read first.** A document entry that validates costs zero provider calls.
   Otherwise each unit checkpoint that validates is reused, and only the
   missing units are paid for.
3. **Persist each completed paid unit before the next request.** The order
   inside one document is: request, response, validate, write the checkpoint
   durably, then the next request. A crash between two units loses at most
   the request in flight (§7).
4. **Every read is validated.** A cached entry that fails any check is a typed
   refusal. The sync stops, and nothing is paid to replace the bad entry
   silently (§5.4).
5. **Off by default.** No cache directory configured means today's behaviour,
   byte for byte, with no new file touched.
6. **Outside both repositories.** The local store refuses a root inside the
   `lovverk` checkout or the engine checkout, and nothing in it is ever
   committed or pushed to `lovverk`.
7. **No partial corpus.** The cache changes where vectors come from, never
   what gets committed. The commit, the audit, the attestation and the push
   are unchanged, and a run that dies still publishes nothing.

## 4. Entry format (schema 1)

One file per entry:

```text
magic           4 bytes   b"LSEC"
meta length     4 bytes   uint32 big-endian
meta            N bytes   canonical JSON (sorted keys, no whitespace, UTF-8)
payload         M bytes   row-major vectors
digest         32 bytes   SHA-256 over every preceding byte
```

`meta` fields:

| field | doc entry | unit entry |
|---|---|---|
| `schema` | `1` | `1` |
| `kind` | `"document"` | `"unit"` |
| `esi` | 32 lowercase hex | same |
| `input_hash` | 64 lowercase hex, the document's ADR-0006 hash | same |
| `dim` | embedder dimension | same |
| `rows` | number of inputs (= chunks) | rows in this unit |
| `dtype` | `"int8"` | `"float32"` (little-endian) |
| `scale` | the document's quantization scale, a finite float > 0 | absent |
| `unit` | absent | `{"index": i, "count": n, "input_hash": h}` |
| `created_at` | UTC ISO-8601, seconds | same |

Why two dtypes:

* A **document entry** stores exactly what the sidecar is made of: the int8
  rows and the one scale. A sidecar assembled from it is byte-identical to the
  one the run that paid for it would have written. At 3,072 dimensions it costs
  about 3 KiB per chunk, the same as the published sidecar.
* A **unit entry** must store float32. The document's scale depends on rows
  not yet fetched, so quantizing a unit early would give a different sidecar
  than the uncached path. Unit entries are transient: once the document entry
  exists, prune removes them.

The digest at the end makes a torn write detectable, even if the rename
promises (§5.2) fail on some filesystem. The meta binds every identity field,
so a file renamed under another key does not validate.

## 5. Local store (M1)

### 5.1 Layout and path safety

```text
<root>/                         0700, owned by the running user
  schema-1/                     0700
    <esi>/                      0700, 32 hex
      documents/<input_hash>.lsec          0600
      units/<input_hash>/<i>-<n>-<unit_hash>.lsec   0600
```

* `<root>` comes only from `LOVSPOR_EMBEDDING_CACHE_DIR`. Unset means off.
* The root must be absolute. It must not be a symlink, and no component under
  it is followed if it is one: files are opened with `O_NOFOLLOW`, and
  directories are checked with `lstat`.
* The root must lie outside the `lovverk` checkout (`lovverk_repo_path`) and
  outside the engine's own git work tree. Inside either one is refused at
  startup, so a vector can never be staged by `git add`.
* Every path component comes from validated hex or integers. No name is built
  from document text, slug or user input, so traversal has nothing to inject.
* A root or directory with group or other permission bits, or owned by another
  user, is refused. The store does not chmod someone else's directory into
  shape.

### 5.2 Atomic, immutable, idempotent writes

1. Write the full entry to a unique temporary file in the target's own
   directory (`O_CREAT | O_EXCL | O_NOFOLLOW`, mode 0600), then `fsync` it.
2. `os.link(temporary, final)`. A link never replaces an existing name, so an
   entry, once published, is immutable.
3. Unlink the temporary file and `fsync` the directory.
4. If `final` already exists (a concurrent writer, or a rerun): read it and
   validate it. A valid existing entry wins. The caller receives the stored
   vectors, not the ones it just fetched, so every reader of a key sees one
   value. An invalid existing entry is a typed refusal, not an overwrite.

`os.replace` is not used, because it would let a second writer silently
replace a first one. `src/lovspor/atomic_io.py` is not reused, because it does
not `fsync` and it replaces.

A crash leaves either no entry, a whole entry, or a stray `*.tmp.*` file
that no reader opens. Prune removes stray temporary files older than one hour.

### 5.3 What a read checks

In this order. Any failure raises `EmbeddingCacheError` (a `LovsporError`)
that names the key and the failed check. It never includes vector values,
document text or credentials.

1. Regular file, opened with `O_NOFOLLOW`, owned by the current user, mode
   0600.
2. Length is at least the fixed parts, the magic matches, the meta length
   fits, and the trailing SHA-256 matches.
3. The meta parses, `schema` is known, and `kind` is the one asked for.
4. `esi`, `input_hash`, `dim` and (for units) `unit` equal the values the
   caller derived from the current document and embedder. A file stored under
   one key with another key's meta is refused.
5. `rows` equals the expected count, and the payload length equals
   `rows × dim × itemsize`.
6. float32: every value is finite. int8: `scale` is finite and > 0.
7. `created_at` is not in the future (with 5 minutes of tolerance) and not
   older than the retention (`LOVSPOR_EMBEDDING_CACHE_MAX_AGE_DAYS`, default
   30). An expired entry is refused, not ignored. Prune is the supported way
   to remove it.

Order of rows is bound by construction: the entry is keyed by the hash of the
ordered inputs, and rows are stored in input order. A reordered document has a
different `input_hash` and therefore a different key.

### 5.4 Refusal means stop, not pay

A refused entry stops the run with exit code 1 and the failing check. It does
not fall back to a paid call. The reasons:

* A corrupt cache is evidence that something writes where it should not. Paying
  over it hides that evidence.
* Paying silently on corruption turns a cache defect into an unbounded spend,
  which is the #790 failure mode.

Recovery is an operator action: `lovspor embedding-cache invalidate` for the
key named in the error, or for the whole ESI (§8).

### 5.5 What the cache refuses to start with

* An embedder whose ESI is unknown (`space_id_of` returns `unknown`), or that
  declares no descriptor. Unknown is never comparable (ADR-0005 §Decision.4).
* `lspe_writer_version = 1`. Version 1 carries no ESI and is the legacy
  format. Writing it from a cache would make it easier to reach the forbidden
  mixed corpus (ADR-0005 §Decision.3).
* No credential. A keyless sync has no embedder, so the cache is never built.
  It is not read either: keyless runs write no sidecar, by design.

### 5.6 Behaviour that does not change

* The #803 change-path guard and the ADR-0006 §7 backfill guard still count
  the **whole** workload in tokens, cache hits included, before the first
  provider call. A cache never relaxes a guard. Making the guard count only
  misses would be a separate decision (§9, D3).
* #815 still refuses an unattestable run before any spend.
* Quantization, the LSPE writer, the manifest stamps (`embedding_hash`,
  `embedding_space`, `embedding_space_id`, `embedding_input_hash`), the
  commit, the audit, the attestation and the push are untouched.

## 6. Integration (M2)

One boundary, used by both P1 and P2: `_write_embeddings_for_doc` stops
calling `embedder.encode` and quantizing. It calls one function,
`quantized_document_vectors(embedder, inputs)`, which returns
`(int8 rows, scale)`.

* Without a cache, the function is `quantize_int8(embedder.encode(texts))`:
  today's code, moved.
* With a cache, `_load_embedder` returns a `CachingEmbedder`. It is the same
  adapter, wrapped, with the same `space_id`, `descriptor` and
  `get_dimension`. Its document method looks up the document entry. On a miss
  it plans the units with the embedder's own batching rule, reuses the valid
  unit entries, and pays for each missing unit with one `encode` call. After
  each call it writes the unit entry before the next request. Then it
  quantizes the assembled float32 matrix and writes the document entry.

The batching plan moves from `OpenAIEmbedder._batches` to a module function
used by both, so a cached run sends exactly the requests an uncached run sends.
That keeps vectors comparable with the uncached path under ADR-0005
Amendment 1.

A document of one unit writes only the document entry, with no unit entry.

### 6.1 Progress (M5)

The wrapper counts documents, documents served from cache, units hit, units
paid, and estimated tokens paid (with `count_input_tokens`). The sync prints
one line every 50 documents and one at the end, to stderr. Only counts are
printed, never text, keys or the cache path.

## 7. Crash semantics

| Moment of death | What survives | Cost on rerun |
|---|---|---|
| Before a request is sent | All entries written so far | That unit is paid once |
| **A request is in flight** (sent, no response) | The provider may have billed it. No response means no vectors, so nothing can be stored | **Paid again. A paid in-flight request with no response is not recoverable**, by any design |
| Response received, entry not yet durable | No entry, or a stray temp file | Paid again |
| Entry linked and directory fsynced | The entry | 0 |
| Document entry written, sidecar not committed | The document entry | 0 for this document |
| Commit made, push not run | Every entry in the run | 0 for every document |

The loss bound is therefore at most one request in flight per process: at most
128 chunks or 250,000 tokens. That bound holds only **if the store survives
the process**. On a local disk, a rerun on the same machine finds the entries.
A GitHub-hosted runner's disk is discarded with the job, so locally it bounds
nothing across jobs. That is §8.

## 8. Remote durability: options

The local store survives a process. It does not survive the job, because
GitHub-hosted runners are ephemeral. Proving cross-job recovery needs a store
that is written **during** the sync step, while the process runs, and read at
the start of the next run. Writing at exit does not count: the job timeout,
a cancellation or a lost runner skips the code that would have written it. That
is the owner's constraint on `actions/cache`, and it holds for any post-step.

| | A. Actions artifacts | B. Non-published git ref | C. Object storage |
|---|---|---|---|
| Written during the step? | Not directly. `actions/upload-artifact` is an action and runs as its own step. A `run:` step does not receive `ACTIONS_RUNTIME_TOKEN` (to verify). In-step upload would need an undocumented internal API or a third-party action that exposes the runtime token | Yes. `git push` of a dedicated ref from inside the step, with the deploy key the job already holds | Yes, through an HTTP adapter |
| Alternative without in-step writes | Split the work: the sync stops itself at a deadline well before the step timeout, without committing. A normal next step uploads the local store, and the next run downloads it. This covers the timeout (the incident) but not a cancellation or a lost runner | — | — |
| New credentials | None (`actions: read` added to the workflow to download another run's artifact) | None new, but the `lovverk` deploy key would write a second ref | **Yes**: an access key in repository secrets |
| New service or cost | None expected for a public repository — **unverified**: the owner confirms on the account's billing page before D1 | None, but the bytes stay in `lovverk`'s history for good: GitHub's GC is not under our control, and a full re-embed is about 320 MiB of int8 (5,890 sidecars measured 2026-11-06 in a local `lovverk` clone, 322.7 MiB) | **Yes**: a new provider account or bucket, monthly cost |
| Retention | 1–90 days, set per upload | Forever, unless the ref is deleted, and even then not reliably | Lifecycle rules |
| Atomic keys | Whole artifact per upload, immutable | Commit per checkpoint, fast-forward only, `concurrency: sync` serializes writers | Conditional put (`If-None-Match: *`) where supported |
| Visibility | Readable by anyone with read access to the repository's Actions | Public: anyone can fetch the ref of a public repository. Vectors of published text are not secret, but this puts a store **inside** `lovverk`, which the owner ruled out ("outside published lovverk") | Private |
| Fit with "outside published `lovverk`" | Yes | **No**, or only by a reading the owner did not give | Yes |

**Recommendation:** A, deadline form. It needs no new service or credential,
it covers the failure that actually happened twice (the timeout), and its
remaining gap (cancellation, a lost runner) is named here rather than hidden.
B is rejected: it puts the store inside `lovverk`. C is the only option that
is durable against every kind of death, and it needs a new account, a secret
and a monthly bill. That is the owner's call (D1).

Whatever the backend, it is an adapter behind the same entry format and the
same validated reads. Downloaded entries are validated like local ones: a
store that comes from somewhere else is untrusted input.

## 9. Decisions requested from the owner

* **D1 — remote backend (blocks M4).** A (deadline + artifact), C (object
  storage, which provider, and who holds the key), or none for now (local store
  only, which helps self-hosted reruns and tests but not GitHub-hosted runs).
  Until D1 is decided, #802 stays open: closing it needs verified cross-job
  recovery, and a local restart is not proof of that.
* **D2 — refusal policy.** This design stops the sync on any invalid entry
  (§5.4). The alternative is quarantining the entry and paying for it again,
  with an alert. The recommendation is to stop.
* **D3 — guard accounting.** Today the guards count the whole workload. With a
  cache, a resumed mass re-embed still needs `--allow-mass-reembed`, even if
  every vector is cached. Counting only misses would make a resumed run cheaper
  to authorise, and it would also make the guard depend on cache contents. The
  recommendation is to keep counting everything.

## 10. Operations (M5)

All commands work on the local store and never call the provider.

* `lovspor embedding-cache status` — entry counts and bytes per ESI, stray temp
  files, expired entries. Counts only.
* `lovspor embedding-cache verify` — validates every entry (§5.3) and lists the
  keys that fail.
* `lovspor embedding-cache invalidate --esi <esi> [--input-hash <hash>]` —
  removes entries: one key, or a whole space.
* `lovspor embedding-cache prune` — removes expired entries, unit entries whose
  document entry exists, and stray temp files older than one hour.

Rollback is turning the cache off: unset `LOVSPOR_EMBEDDING_CACHE_DIR`. The
corpus never depended on it, so nothing else changes. Deleting the directory
loses only money already spent, never corpus state.

## 11. Delivery plan

| PR | Scope | State |
|---|---|---|
| M0 | This document | Proposed |
| M1 | `embeddings/cache.py`: format, local store, validation, typed errors. Not wired, off | After M0 is reviewed |
| M2 | `quantized_document_vectors` boundary, `CachingEmbedder`, `LOVSPOR_EMBEDDING_CACHE_DIR` setting, shared batching plan. Off by default | After M1 |
| M3 | Crash and security regressions with a fake provider: SIGKILL after a checkpoint and a restart in a new process and workspace, torn, swapped and expired entries, concurrent writers, symlinks | With M1 and M2 (red first) |
| M4 | Remote adapter | **Owner-blocked on D1** |
| M5 | Progress line, `embedding-cache` commands, runbook section | After M2 |

No PR in this plan enables the cache in `sync.yml`, uploads anything, or runs a
paid or live sync.
