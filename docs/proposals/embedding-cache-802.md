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

Hard links are not refused as such. `_publish` links the temporary into place
before it unlinks it, so every fresh entry has two links for a moment, and a
reader refusing `st_nlink > 1` would stop a concurrent sync on a healthy entry.
A hard link grants nothing the owner-only mode does not: bytes rewritten
through another name fail check 2, and a valid entry linked under another key
fails check 4. Both are pinned by tests.

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

One `run_sync` builds this adapter once, and the backfill (P2/P3) and the change
loop (P1) both use it. So a run has one cache wrapper, one progress count and,
with #790's meter beneath it, one token budget. Building it per phase would
restart the budget between them (GPT-6 review on #828).

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

### 8.1 Amendment: option C on Cloudflare R2 Standard (proposed, not approved)

The owner's preferred direction for C is Cloudflare R2 Standard, a private
bucket, about 30 days of retention. This section is the design that direction
would need. **It is not an approval.** No bucket, token, secret, account or
workflow change exists or is made by this PR, and no production adapter is
written until the owner approves this section and its cost (D1). Every
provider claim below was read from the provider's own documentation on
2026-11-07, re-read the same day, and is cited. Where the docs do not settle a
question it is marked **unverified** and becomes a staging check (G3), not an
assumption.

#### Verified provider facts this design relies on

| Fact | Source |
|---|---|
| `PutObject` honours `If-None-Match: *` and evaluates it atomically against committed state: of concurrent creators of an absent key, at most one succeeds, the rest get `412 PreconditionFailed` | [R2 S3 API](https://developers.cloudflare.com/r2/api/s3/api/), [S3 extensions](https://developers.cloudflare.com/r2/api/s3/extensions/), [error codes](https://developers.cloudflare.com/r2/api/error-codes/) (10031) |
| Read-after-write, list-after-write and delete are strongly consistent over the S3 API; a `404` "is not final" with respect to an earlier `PutObject` whose outcome is unknown | [Consistency](https://developers.cloudflare.com/r2/reference/consistency/) |
| Get, Head, Put, ListObjectsV2, Delete are implemented; endpoint `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`, region `auto`; `Content-MD5` is accepted on `PutObject`; `BadDigest` (400) exists | [R2 S3 API](https://developers.cloudflare.com/r2/api/s3/api/), [error codes](https://developers.cloudflare.com/r2/api/error-codes/) |
| Lifecycle rules expire objects by prefix after N days (`Expiration: { Days: N }`); removal is "typically within 24 hours" of the `x-amz-expiration` value. The page does not say in words whether the age counts from upload; entries here are written once and never modified, so upload and last modification are the same instant | [Object lifecycles](https://developers.cloudflare.com/r2/buckets/object-lifecycles/) |
| Object Read & Write tokens can be limited to one bucket; tokens can carry an expiration date; there is no "write but not delete" preset | [API tokens](https://developers.cloudflare.com/r2/api/tokens/) |
| Bucket locks prevent deletion and overwrite for a period, by prefix, and take precedence over lifecycle rules | [Bucket locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/) |
| Standard: storage $0.015/GB-month, Class A $4.50/M, Class B $0.36/M; free each month 10 GB-month, 1M Class A, 10M Class B; egress and `DeleteObject` free; a `PutObject` answered `412` **is billed as Class A** | [Pricing](https://developers.cloudflare.com/r2/pricing/) |
| Single PUT up to 4.995 GiB (5 GiB less 5 MiB, per the limits footnote); keys up to 1,024 bytes; **1 write per second per key** (429 above) | [Limits](https://developers.cloudflare.com/r2/platform/limits/) |
| Repository/environment secrets reach the job; a workflow triggered from a fork gets none | [GitHub: using secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets) |

**Unverified, so not relied on:** whether R2 verifies `Content-MD5` server-side;
whether `x-amz-checksum-sha256` is honoured on a single `PutObject`; whether
R2 ever answers a conditional race with `409` as AWS can; whether an Object
Read & Write token can delete (assume it can); how a bucket lock interacts with
a lifecycle rule of the same age; whether operations are billed rounded up to
whole millions. **Not available:** GitHub OIDC federation to R2. Neither
Cloudflare nor GitHub documents it, so the design assumes a stored secret.

#### Keys

One prefix per schema, the local layout unchanged beneath it, so
`entry_location` (§5.1) names both stores:

```text
lovspor-embedding-cache/schema-1/<esi:32 hex>/documents/<input_hash:64 hex>.lsec
lovspor-embedding-cache/schema-1/<esi>/units/<input_hash>/<i>-<n>-<unit_hash:64 hex>.lsec
```

At most about 210 bytes, inside the 1,024-byte limit. The key is derived only
from validated identity (32/64 lowercase hex and integers), never from text,
paths or titles, so a key cannot traverse or collide by construction.

#### Authority: one winner per key, and it is the remote object

ESI has never promised bit-identical provider output (ADR-0005 Amendment 1),
so two writers of one key can hold two different, equally valid vectors.
Under the same ESI, input hash and request plan, a retried request, an
overlapping manual run, or a run on another runner can each get different
floats. A conditional `PUT` answered `412` therefore proves only that **an**
object exists under the key. It does not prove that the object holds **this**
writer's bytes.

With the remote store on, one rule settles every case below: **the first
object committed under a key in the bucket is that key's only value.** Every
writer that loses adopts it, and nothing is overwritten to hide a race, local
or remote. The local store is the runner's copy of remote winners. It never
holds a value the bucket does not hold or would refuse. A key whose local and
remote values differ, or whose remote value cannot be validated, stops the sync
with a typed refusal, before any further paid request and without paying for
that key again (§5.4).

"Same value" is compared on the decoded entry, not on the file bytes, because
`created_at` differs between writers. Two entries are the same value when their
identity (ESI, input hash, unit, dim, rows, dtype), their vector payload bytes
and their scale are equal. A winner's `created_at` is kept as written.

#### Write protocol, during the step

The remote write sits at the same seam as the local one (§6): after a paid
request returns and its vectors pass `_check_response`, and **before the next
paid request is sent**. Nothing is uploaded at exit or in a later step (§8).
For each entry, unit checkpoint or document, in this order:

1. Encode the entry once (§4).
2. `PUT` the key with `If-None-Match: *` and `Content-MD5` (a transport check,
   not the integrity guarantee). **The remote write comes first**, so the local
   store only ever receives a value the bucket has accepted as the winner.
3. `200`: this writer won. Publish the same bytes locally (§5.2) and return its
   own vectors.
4. `412`: someone holds the key, possibly this writer after an ambiguous
   attempt (5). `GET` the key and run the full read checks (next section)
   against the key this writer expected.
   * Valid: that object is the winner. Publish **its** bytes locally and return
     **its** vectors, not the ones just paid for. That spend is lost (one
     request at most, the in-flight bound of §7), but the run continues on the
     single winning value.
   * Invalid (corrupt, torn, another identity, another dimension, expired):
     stop with `EmbeddingCacheError` naming the key's label. Nothing is written
     locally or remotely, and the corrupt object is never overwritten (the
     conditional `PUT` could not overwrite it anyway). The owner investigates it
     and removes it on purpose (§10 tooling, extended to the remote store).
     There is no paid fallback: a rerun refuses the same key the same way.
   * `404` (the object was removed between the `412` and the `GET`, by the
     lifecycle rule or by the owner): repeat from step 2, once. A second `404`
     stops the sync with `EmbeddingCacheStorageError`.
5. `429`, `5xx`, a transport error or a timeout: retry the **same** conditional
   `PUT` with backoff, at most 3 attempts in all. A timeout is ambiguous: the
   first `PUT` may have committed. A later `200` means it did not and this
   attempt won. A later `412` is handled exactly as in step 4, and that `GET`
   tells whether the winner is this writer's value or another writer's.
   Either way the remote object is adopted, so the outcome does not depend on
   knowing which. A `HEAD` or `GET` returning `404` after an ambiguous `PUT` is
   not proof that it failed (consistency caveat above), so a `404` is never
   read as "safe to pay again".
6. Retries exhausted: stop with `EmbeddingCacheStorageError` before the next
   paid request. The vectors just paid for are **not** written locally, because
   the bucket might still hold a different winner. This is a loss of at most
   one request, the same bound as a request in flight (§7). Continuing to pay
   while the bucket cannot be written would make every further response
   unprotected spend.
7. Remote `200` or adopted, then the local publish fails (disk full, I/O
   error): stop with `EmbeddingCacheStorageError` before the next paid request,
   as §5.2 does today. The bucket holds the winner, so a rerun on any runner
   reads it and pays 0 for that key.

A document entry is built from the unit values this process actually returned,
winners included, quantized with one document scale (§6). It then goes through
steps 1–7 like any entry. The sidecar is written only from the document value
step 3 or 4 returned. When another writer's document entry won, the sidecar
carries **its** vectors and scale, never a local assembly that differs from it,
so the sidecar, the remote entry and the local copy hold one value.

```text
writer                     R2 bucket                   local store
  |-- encode (paid) ---------->                            |
  |-- PUT If-None-Match:* ---->|                           |
  |<-------- 200 --------------|                           |
  |-- publish same bytes -------------------------------->|   own value
  |
  |-- PUT If-None-Match:* ---->|   (key already held)      |
  |<-------- 412 --------------|                           |
  |-- GET -------------------->|                           |
  |<-------- winner bytes -----|                           |
  |-- decode_entry against expected key                    |
  |     valid:   publish winner bytes ------------------->|   winner's value
  |     invalid: stop, typed refusal, write nothing        |
  |
  |-- PUT ... timeout          |   (may have committed)    |
  |-- PUT If-None-Match:* ---->|                           |
  |<-------- 412 or 200 -------|   then as above           |
```

#### Read protocol and integrity

A downloaded entry is untrusted input. It goes through `decode_entry` exactly
like a local one (§5.3): magic, schema, meta identity against the expected key
(ESI, input hash, unit, dim, rows, dtype), payload length, SHA-256 trailer,
finiteness, scale, `created_at`. Any failure is a typed refusal that stops the
sync without paying (§5.4, D2). It is never a silent re-download or a paid
fallback. R2's own checksums are not trusted for this: the entry carries its
own digest.

With the remote store on, a lookup asks the bucket, the authority, and checks
the local copy against it:

| Remote | Local | Result | Paid |
|---|---|---|---|
| valid | absent | publish locally, use the remote value | 0 |
| valid | valid, same value | use it | 0 |
| valid | valid, **different value** | stop: `EmbeddingCacheConflictError` (a typed `EmbeddingCacheError`) naming the key's label. Neither copy is changed | 0 |
| valid | invalid | stop: the local refusal of §5.3, local entry kept as evidence | 0 |
| invalid | any | stop: typed refusal, remote object kept as evidence | 0 |
| absent | valid | upload the local bytes (write protocol, steps 2–6, no provider call); if another writer's value wins the `412`, stop as in the conflict row | 0 |
| absent | absent | miss: pay, then the write protocol | 1 request |
| absent | invalid | stop: the local refusal | 0 |

The conflict row can only come from a local entry written outside this
protocol: one written while the remote store was off, or one copied in by hand.
Resolving it is the owner's choice of which value to keep, made with the admin
tooling (`invalidate` on one store), never by the sync. Lookups stay one
`GET` (Class B) per key. A resumed run therefore spends Class B, not Class A,
on what it already has, and pays the provider only for true misses.

#### Failure matrix

| Case | Outcome | Provider spend on rerun |
|---|---|---|
| Two writers, same key, different valid vectors | First commit wins. The loser `GET`s and validates it, adopts it locally and in its sidecar. Its own response is discarded | 0 for the key |
| `PUT` timeout, retry answered `412` | `GET` + validate. The remote object is adopted whether it is this writer's or not | 0 |
| Remote winner differs from an existing local entry | Typed conflict refusal. Both kept, owner decides | 0 (no pay while refused) |
| Remote object corrupt or of another identity | Typed refusal. Kept as evidence. Never overwritten, never re-paid by the sync | 0 until the owner removes it, then 1 request |
| Remote write succeeds, local publish fails | Storage error before the next paid request. The bucket holds the winner | 0 for the key |
| Remote unwritable after 3 attempts | Storage error before the next paid request. Nothing stored locally | ≤ 1 request (that response) |
| Killed between remote `200` and local publish | The bucket holds the winner, the runner's disk is gone | 0 |

Concurrency: `sync.yml`'s `concurrency: sync` already serialises scheduled
runs. A manual overlap or a retried job is the case above: one winner per key,
adopted by every other writer, and the winner is what every later reader
validates and uses. Each key is written once per run, far under the 1 write/s
per key limit. A writer that adopts a winner pays one extra `GET` (Class B),
which the cost table already covers.

#### Retention, and why the reader's limit is longer than the bucket's

A lifecycle rule expires `lovspor-embedding-cache/` 30 days after upload. The
reader's `LOVSPOR_EMBEDDING_CACHE_MAX_AGE_DAYS` is set to **32** for the
remote store. Age is counted from the one PUT of a write-once entry, not last use, and removal happens up
to about a day after expiry. With equal limits, an entry 30–31 days old would
still be served and refused as expired, stopping the sync. With the bucket
shorter than the reader, the bucket removes an entry before the reader would
refuse it, and the next run treats it as a miss. An expired entry that is still
served is then a provider anomaly, and stopping on it is correct.

Optional, staging-verified only: a bucket lock of 29 days on the same prefix
would make entries non-overwritable and non-deletable even by the CI token,
without blocking the 30-day expiry. The docs say locks take precedence over
lifecycle; the behaviour at those ages is unverified, so the lock ships only
after G3 demonstrates it.

#### Permissions and credentials, the smallest set

* One private bucket, no public access, no `r2.dev` URL, no custom domain.
* One **Object Read & Write** token scoped to that bucket only, with an
  expiration date (proposed: 90 days, rotated by the owner), never an Admin
  token. Lifecycle and lock rules are set once by the owner with their own
  account. The CI token cannot change them.
* Stored as secrets of a GitHub **environment** (`embedding-cache`) that only
  the `sync.yml` job on `main` may use: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
  `R2_SECRET_ACCESS_KEY`. PR and fork workflows never see them.
* The engine never logs the endpoint, key id, secret or a full URL with a
  signature. Progress lines stay counts only (§6.1).

#### Cost, worst case and expected

Measured corpus: 5,890 documents, 322.7 MiB of int8 sidecars (§8 table). A full
re-embed writes about 5,890 document entries, plus unit checkpoints for the
long documents, removed by prune once their document entry exists.

| Case | Class A (PUT) | Class B (GET) | Storage | Monthly cost |
|---|---|---|---|---|
| Nightly syncs, a few changed laws each (normal) | tens to hundreds | same | < 1 GB | $0 (free tier) |
| One full re-embed, then reruns that read first | ~6,000–10,000 | ~10,000 per rerun | ~0.4 GB | $0 (free tier) |
| Ceiling: 30 full re-embeds in a month, each into a new ESI, nothing expired yet | ≤ 300,000 | ≤ 300,000 | ≤ 12 GB-month | ≤ $0.03 (storage over 10 GB) |
| Generic stress bound from the research (60,000 × 20 KB entries, 60,000 PUT incl. 412 + 60,000 GET per run, 30 runs) | 1.8 M | 1.8 M | ≤ 36 GB-month | ≈ $3.61–$4.90 |

So the realistic cost is $0, and the provable ceiling under abuse is a few
dollars a month. The free tier and prices can change: the owner re-reads the
pricing page and their account's billing before approving (G0). Reading first
on reruns matters: without it, every rerun pays one billed `412` per entry.

#### Test strategy

* **Offline (M4, the adapter PR, after G0):** the adapter speaks HTTP through
  `httpx`, mocked with `pytest-httpx` only (CLAUDE.md). Red first, one test
  per row of the failure matrix and the lookup table:
  * `200`: the writer's own value is published locally and used.
  * `412` with a **different valid** winner: one `GET`, the winner's vectors
    are returned, published locally and carried by the sidecar; the writer's
    own response is discarded; no second provider call; the local file's bytes
    equal the remote object's.
  * `412` with a winner equal to the writer's value: adopted, same result.
  * Timeout, then `412` (both for "own bytes committed" and "another writer
    committed"): `GET` + validate decides, the remote value is adopted, and a
    `404` after the timeout never leads to a paid retry.
  * `412`, then a corrupt, torn, swapped-identity, other-dimension or expired
    `GET`: typed refusal, nothing written locally, no `PUT` that could overwrite,
    no provider call on the rerun.
  * `412`, then `404`: one more conditional `PUT`, then a storage error.
  * `429`/`5xx`/transport retry then succeed; retries exhausted stop before the
    next paid request with nothing stored locally.
  * Remote `200`, local publish fails: storage error before the next paid
    request; a fresh store resumes from the bucket and pays 0.
  * Local valid, remote different: `EmbeddingCacheConflictError`, both files
    unchanged, 0 provider calls. Local valid, remote absent: uploaded, no
    provider call.
  * A document entry assembled from adopted units, and a document entry lost to
    another writer: the sidecar equals the remote document value.
  * The key is derived only from identity; no secret appears in logs or errors;
    cache off and unset credentials behave exactly as today.
* **Cross-runner crash test (G3, staging bucket, no paid API):** a
  `workflow_dispatch` job on a GitHub-hosted runner runs a sync on a synthetic
  corpus with the fake provider, and kills itself with SIGKILL after k of n
  checkpoints. A second job, on a fresh runner with an empty disk, runs the same
  sync and must pay exactly n − k requests, then 0 on a third run, with the
  sidecars byte-identical to an uncached run. This test, not a local restart, is
  the evidence #802 needs to close.
* **Cross-runner race (G3, staging bucket, no paid API):** two jobs start the
  same synthetic sync together, with fake providers that return **different**
  valid vectors for the same inputs. Pass: every key has one remote object;
  each job's local copies and sidecars equal the remote values; no job
  overwrote an object; a third job pays 0. A variant injects a timeout on one
  `PUT` that the bucket did commit, and must end the same way.
* Staging also settles the unverified points above: whether a token deletes,
  lock vs lifecycle, `Content-MD5` rejection, `409`. A `409` on a conditional
  race is then handled like `412` (`GET` + validate + adopt), never as success.

#### Rollout gates and rollback

| Gate | What has to be true | Who |
|---|---|---|
| G0 | This section and its cost are approved | owner |
| G1 | Bucket, lifecycle rule (30 d), token (bucket-scoped, expiring) and environment secrets created | owner |
| G2 | Adapter PR merged, off by default, offline tests green, full PR pipeline | engineering, then owner merge |
| G3 | Cross-runner crash test green on the staging bucket; unverified points settled | engineering |
| G4 | One manual `workflow_dispatch` sync with the remote cache on, reviewed | owner |
| G5 | Scheduled sync uses the remote cache | owner |

Rollback at any gate: remove the environment secrets or unset
`LOVSPOR_EMBEDDING_CACHE_*` in `sync.yml`. The sync is then exactly today's
uncached sync. The corpus never depends on the cache, so nothing in `lovverk`
changes. Deleting the bucket loses only money already spent. Revoking the
token stops every credential derived from it.

Rollback never resolves a refusal by rewriting a value. A conflict or a
corrupt remote object that stops a run is investigated first. Turning the
remote store off then returns the sync to the local-only behaviour of §5,
where the local entries are values the bucket accepted or ones written before
it existed. Turning it back on re-checks every key against the bucket (the
lookup table above), so a value that diverged while it was off is refused, not
silently uploaded over another.

## 9. Decisions requested from the owner

* **D1 — remote backend (blocks M4).** A (deadline + artifact), C (object
  storage, which provider, and who holds the key), or none for now (local store
  only, which helps self-hosted reruns and tests but not GitHub-hosted runs).
  The owner's stated preference is C on Cloudflare R2 Standard. §8.1 is that
  design, with verified provider facts, cost and gates G0–G5. Approving it (G0)
  is the decision still open; creating the bucket and token (G1) is the
  owner's.
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
