# Runbook: the remote embedding cache on Cloudflare R2 (#802)

* **Decision:** ADR-0022 (lovspor-notebook), accepted 2026-11-08 for **G0 and D1–D3 only**.
* **Design:** [`docs/proposals/embedding-cache-802.md`](proposals/embedding-cache-802.md) §8.1.
* **Code:** `embeddings/r2_client.py`, `embeddings/cache_remote.py`. Off by default.
* **Not approved:** G1 (bucket, token, secrets, billing), G3 (staging tests), G4 (one manual sync), G5 (scheduled sync). Every step below that creates, enables or spends something is an **owner action at a gate the owner has not yet passed**. Nothing here is done by an agent.

Never paste a secret into a chat, an issue, a PR, a commit or a log. Secrets go straight from the Cloudflare dashboard into `gh secret set`, which reads them from standard input.

## Gates

| Gate | What it allows | Who | State |
|---|---|---|---|
| G0 | Architecture, offline development | owner | **approved 2026-11-08** |
| G1 | Buckets, lifecycle, tokens, GitHub environments and secrets | owner | not approved |
| G2 | Adapter merged (off) | owner merge | PR open |
| G3 | Staging workflow: cross-runner crash and race tests | owner enables | not approved |
| G4 | One manual `sync` with the remote cache on | owner | not approved |
| G5 | Scheduled `sync` with the remote cache | owner | not approved |

## G1 — owner actions (only once G1 is approved)

There are two buckets with two tokens, so a staging test can never touch production entries.

| | Staging (G3) | Production (G4/G5) |
|---|---|---|
| Bucket | `lovspor-embedding-cache-staging` | `lovspor-embedding-cache` |
| GitHub environment | `embedding-cache-staging` | `embedding-cache` |
| Token | Object Read & Write, this bucket only, 90-day TTL | same, its own bucket only |

### 1. Billing and the account

1. Cloudflare dashboard → **R2 Object Storage**. Enabling R2 asks for a payment method even though usage is expected to stay inside the free tier.
2. Re-read the [pricing page](https://developers.cloudflare.com/r2/pricing/) on the day. The design's figures were read on 2026-11-07: Standard storage $0.015/GB-month, Class A $4.50/M, Class B $0.36/M; free each month 10 GB-month, 1M Class A, 10M Class B.
3. Note the **Account ID**, shown on the R2 overview page. It is a 32-character lowercase hex string.

### 2. Buckets, once per bucket

1. R2 → **Create bucket**. Name it from the table. Location: Automatic. Default storage class: **Standard**.
2. Bucket → **Settings** → Public access: leave the `r2.dev` URL **disabled**. Add no custom domain.
3. Bucket → **Settings** → **Object lifecycle rules** → Add rule:
   * name `expire-embedding-cache`;
   * prefix `lovspor-embedding-cache/`;
   * action: delete objects **30 days** after upload.

   The reader allows 32 days (`LOVSPOR_EMBEDDING_CACHE_MAX_AGE_DAYS=32`), so the bucket removes an entry before a reader would refuse it as expired.
4. Bucket lock: **not now**. It ships only after G3 shows how a 29-day lock and the 30-day lifecycle interact.

### 3. Tokens, one per bucket

1. R2 → **Manage R2 API Tokens** → **Create API token**.
2. Permissions: **Object Read & Write**. Never Admin Read & Write.
3. Specify bucket: **only** the one bucket from the table.
4. TTL: **90 days**. Write the expiry date in your calendar (see "Rotation").
5. Create. The dashboard shows the **Access Key ID** and **Secret Access Key** once. Keep the page open for step 4, and copy nothing anywhere else.

### 4. GitHub environments and secrets

On github.com → repository → **Settings** → **Environments**:

1. New environment `embedding-cache-staging`:
   * **Deployment branches and tags:** selected branches, `main` only;
   * **Required reviewers:** yourself.
2. New environment `embedding-cache`: same settings.

Then, from a terminal on your machine (each command prompts for the value; paste from the dashboard, press Enter, then Ctrl-D if asked):

```bash
gh secret set R2_ACCOUNT_ID        --env embedding-cache-staging --repo bartoszkobylinski/lovspor
gh secret set R2_ACCESS_KEY_ID     --env embedding-cache-staging --repo bartoszkobylinski/lovspor
gh secret set R2_SECRET_ACCESS_KEY --env embedding-cache-staging --repo bartoszkobylinski/lovspor
gh variable set R2_BUCKET --env embedding-cache-staging --body lovspor-embedding-cache-staging --repo bartoszkobylinski/lovspor
```

Repeat for `--env embedding-cache` with the production token and `--body lovspor-embedding-cache`. Check what was stored. Names only: GitHub never prints values.

```bash
gh secret list --env embedding-cache-staging --repo bartoszkobylinski/lovspor
gh variable list --env embedding-cache-staging --repo bartoszkobylinski/lovspor
```

Pull-request and fork workflows cannot read environment secrets. Only a job that names the environment, on `main`, after your approval, can read them.

## G3 — cross-runner tests on the staging bucket (only once G3 is approved)

The workflow `.github/workflows/embedding-cache-staging.yml` is **inert**. Every job is skipped unless both conditions hold:

* the repository variable `EMBEDDING_CACHE_STAGING_ENABLED` is `true`;
* the run was dispatched from `main` with the input `confirm` = `G3`.

It never calls a paid provider. A deterministic fake stands in, and its embedding-space descriptor names the run, so every run writes fresh keys.

1. Enable: `gh variable set EMBEDDING_CACHE_STAGING_ENABLED --body true --repo bartoszkobylinski/lovspor`.
2. Run: `gh workflow run embedding-cache-staging.yml --ref main -f confirm=G3 --repo bartoszkobylinski/lovspor`. GitHub then asks for the environment review of `crash`, `race-a` and `race-b` at once: approve all three **in one review**. The race writers wait for each other for at most 600 seconds (`LOVSPOR_G3_BARRIER_TIMEOUT_SECONDS`). `resume` and `race-check` ask for approval later, when their jobs start.
3. Watch: `gh run watch --repo bartoszkobylinski/lovspor`.
4. What passes:
   * **crash**: a child sync is SIGKILLed after 2 of 4 checkpoints;
   * **resume** (a fresh runner): pays exactly 2, then 0; the sidecar equals an uncached run's;
   * **race-a / race-b**: two runners race for the same new key, with providers that return different vectors (see "The race" below);
   * **race-check** (a fifth runner): the evidence proves the race; a third writer then pays 0 and writes the same sidecar.

### The race, and why it is a race

If two jobs only share `needs:`, they may run one after the other. The second writer's `GET` then finds the first writer's object and it never sends a competing `PUT`. Equal sidecars and a third runner paying 0 still hold. They prove eventual agreement, not the conditional-`PUT` race of design §8.1. The workflow therefore forces the race:

1. Both writers use the same run-specific keys: the fake provider's descriptor names the run. Each writer's provider returns different valid vectors.
2. The cache stores an entry only after the key's `GET` missed, the fake provider was paid and the entry was built. Each writer's **first cache-entry `PUT`** is held at a barrier (`src/lovspor/embeddings/g3_race.py`: staging only, imported by no sync, CLI or server path, and measured by the PR mutation gate). Before sending it, the writer:
   * writes its own readiness object, with a conditional `PUT`. The object names the run, the target key and the digest of the entry it is about to send;
   * `GET`s the peer's readiness object until it exists, is valid and names the same run and key.
3. Only then does either writer send its `PUT`. When it does, the other writer already holds a different candidate for the same key. Whoever commits second is answered `412` (or `409`), `GET`s the winner, validates it and adopts its bytes.

Readiness objects live at `lovspor-embedding-cache/g3-barrier/<run>/<key digest>/<writer>.json`. That is in the staging bucket, under the prefix the 30-day lifecycle rule expires, on a fresh path for every run attempt. The barrier uses only `GET` and conditional `PUT`: no LIST, no DELETE, no other credentials. A readiness object that is corrupt, names another run or key, or already exists stops the writer before its competing `PUT`.

**Evidence.** Each race job prints one `evidence` line, which is also the job output:

* `target`: the key both writers attempted;
* `candidate_sha256`: its own candidate entry;
* `peer_candidate_sha256`: the peer's candidate, as read at the barrier;
* `put_status`: what its `PUT` was answered;
* `remote_sha256`: the bucket's object for the key, read back afterwards;
* `local_sha256`: its own local copy of the key;
* `sidecar_sha256`: its sidecar;
* `paid`: requests it paid.

`race-check` refuses the run unless all of these hold:

* both writers name the same run and key, with different candidates;
* each saw the other's candidate at the barrier;
* exactly one `PUT` was answered `200` and the other `412`/`409`;
* the bucket holds the winner's candidate, and both local copies equal it;
* both sidecars are equal.

Only then does it start a third writer, which must pay 0 and write the same sidecar. A sequential schedule cannot pass: the first writer times out at the barrier.

**When a writer times out.** The job fails with `G3 race failed (writer a): writer b never reached the barrier within 600s; no competing PUT was sent`. `race-check` is then skipped, so the run is red. The usual cause is approving the two race jobs in separate reviews more than 600 seconds apart. Re-run the whole workflow (a new attempt gets new keys), and approve all pending jobs in one review.

**Examining a run.** Open each race job's last step log, or `gh run view <run-id> --log --repo bartoszkobylinski/lovspor`, and read the `evidence` lines. The `race-check` log names the winning writer, or lists every reason the evidence was refused. Readiness objects and entries stay in the staging bucket until the lifecycle rule removes them, so the target key can be inspected in the dashboard. No secret or endpoint is printed: the evidence holds keys and digests only.

5. Disable afterwards: `gh variable delete EMBEDDING_CACHE_STAGING_ENABLED --repo bartoszkobylinski/lovspor`.
6. Record the run URL on #802. #802 closes only on a green G3 run.

Also settle at G3, by hand on the staging bucket, and record each answer on #802. These are the design's unverified points:

* Does R2 reject a wrong `Content-MD5` (`400 BadDigest`)?
* Does a concurrent conditional `PUT` ever answer `409`? The adapter treats `409` like `412`.
* Can an Object Read & Write token delete? The design assumes it can.
* How does a 29-day bucket lock interact with the 30-day lifecycle?

## G4/G5 — the sync with the remote cache (each its own owner decision)

`sync.yml` sets none of the cache variables today. Turning it on is an edit to `sync.yml` in its own PR, reviewed like any other. The job gains `environment: embedding-cache` and these variables:

```yaml
LOVSPOR_EMBEDDING_CACHE_DIR: ${{ runner.temp }}/embedding-cache
LOVSPOR_EMBEDDING_CACHE_MAX_AGE_DAYS: "32"
LOVSPOR_EMBEDDING_CACHE_REMOTE: r2
LOVSPOR_EMBEDDING_CACHE_R2_BUCKET: ${{ vars.R2_BUCKET }}
LOVSPOR_EMBEDDING_CACHE_R2_ACCOUNT_ID: ${{ secrets.R2_ACCOUNT_ID }}
LOVSPOR_EMBEDDING_CACHE_R2_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}
LOVSPOR_EMBEDDING_CACHE_R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
```

A partial configuration is refused by variable name before any paid request. So is the remote variable without the local directory.

## Cost controls

* **Expected:** $0 a month. Nightly syncs write tens to hundreds of entries, and a full re-embed about 6,000–10,000.
* **Ceiling under abuse:** about $5 a month (design §8.1 "Cost").
* Every rerun reads first, one `GET` (Class B) per key, so it pays the provider only for true misses. A `PUT` answered `412` is billed as Class A.
* Monthly: dashboard → R2 → bucket → **Metrics** (operations, storage) and **Billing**.
* Cloudflare billing notifications, if your plan offers them: set one at a few dollars. **Unverified:** whether R2 usage can be capped rather than only alerted on. Assume it cannot.
* The 30-day lifecycle bounds storage without any job doing it.

## Rotation (every 90 days, before the token expires)

An expired token stops the sync with `EmbeddingCacheStorageError` before any further paid request. It never pays over it.

1. Create a new token exactly as in G1 step 3, for the same bucket.
2. `gh secret set R2_ACCESS_KEY_ID --env <environment>` and `gh secret set R2_SECRET_ACCESS_KEY --env <environment>` with the new values.
3. Staging only: run G3 once (if G3 is enabled), or dispatch one sync (if G4/G5 are on), and check that it is green.
4. Revoke the old token in **Manage R2 API Tokens**.

A leaked token: revoke it first, then do steps 1–3. A token is bucket-scoped, so at worst a leak exposes the cache entries. Those are vectors of published law text, not personal data, and every entry is validated on read, so a planted object is refused, not served.

## Rollback (at any gate)

1. Remove the cache variables from `sync.yml`, or delete the environment's secrets: `gh secret delete R2_SECRET_ACCESS_KEY --env embedding-cache`. The sync is then exactly today's uncached sync.
2. The corpus never depends on the cache. Nothing in `lovverk` changes.
3. Deleting a bucket loses only money already spent.

Rollback never resolves a refusal by rewriting a value. A conflict (`EmbeddingCacheConflictError`) or a refused remote object stops the run with both copies kept. Look first, then remove the copy you do not want:

* the local one, with `lovspor ops embedding-cache invalidate`;
* the remote one, from the dashboard, by its key named in the error.

## Every manual owner action, in order

| # | Action | Gate |
|---|---|---|
| 1 | Decide G1 | — |
| 2 | Enable R2 and a payment method; re-read pricing | G1 |
| 3 | Create both buckets, public access off | G1 |
| 4 | Add the 30-day lifecycle rule on `lovspor-embedding-cache/` to each bucket | G1 |
| 5 | Create two Object Read & Write tokens, one per bucket, 90-day TTL; note the expiry | G1 |
| 6 | Create environments `embedding-cache-staging` and `embedding-cache` (`main` only, you as reviewer) | G1 |
| 7 | `gh secret set` ×3 and `gh variable set R2_BUCKET` per environment | G1 |
| 8 | Decide G3; set `EMBEDDING_CACHE_STAGING_ENABLED=true`; dispatch with `confirm=G3`; approve `crash`, `race-a` and `race-b` in one review, later `resume` and `race-check` | G3 |
| 9 | Record the G3 run URL, both race `evidence` lines and the four unverified answers on #802; delete the variable | G3 |
| 10 | Decide G4: merge a `sync.yml` PR adding the environment and variables; dispatch one sync; review it | G4 |
| 11 | Decide G5: let the schedule run with it | G5 |
| 12 | Every 90 days: rotate both tokens | ongoing |
