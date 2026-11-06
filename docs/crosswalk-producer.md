# Legislative crosswalk producer (ADR-0017, #610)

Status: implementation prepared against the PROPOSED rulings in
`docs/proposals/legislative-crosswalk-decisions-proposal-2026-11-02.md`; nothing
here is in force until the owner records them (lovspor-notebook
`docs/adr/ADR-0017-amendment-1-ruling.md`). Nothing here publishes, tags, pushes,
or writes into `lovverk`.

All commands sit on the `ops` group (`lovspor.cli` is at its size ratchet).

Every write (the capture cache, its staging directory, every output file) is
checked on the fully resolved path before anything is created
(`lovspor.crosswalk.paths`). It is refused inside a Git clone, worktree or
submodule (gitignored directories included), inside the Lovverk corpus
(`LOVVERK_CORPUS_PATH`, or a `manifest.json` + `lover/` root), through a symlinked
cache or staging directory, or into a non-empty cache. A refusal creates no file,
so raw Lovdata archives never land in a checkout. `scripts/study_52.py capture`
defaults to `data/cache/study-52` inside this repository and is therefore refused
now; pass `--cache` outside the checkout.

| Step | Command | Network | Writes |
|---|---|---|---|
| Capture | `lovspor ops crosswalk capture --cache DIR --laws FILE --lovverk-commit SHA` | Lovdata publicData, `data.stortinget.no/eksport` only | a new, empty cache directory outside every Git checkout and Lovverk (write-once) |
| Build | `lovspor ops crosswalk build --cache DIR --output FILE [--previous FILE]` | none | one new canonical JSONL file |
| Check | `lovspor ops crosswalk check --input FILE [--previous FILE] [--cache DIR [--strict]]` | none | nothing; exit 1 on any problem |
| Release candidate | `lovspor ops crosswalk release-candidate --input FILE --approval FILE --cache DIR --output FILE` | none | one new JSONL file |
| Manifest | `lovspor ops crosswalk manifest --input FILE --output FILE [--cache DIR]` | none | one new manifest file |

- **Capture** (`lovspor.crosswalk.capture`):
  - Reads every Lovtidend avd. I archive the catalogue lists, the decision sessions' listings and every case a listing hint proposes.
  - Paces Stortinget under 100 calls a minute.
  - Retries at most three times, and only on a 429, a 5xx or a transport failure.
  - Refuses redirects.
  - Records every refusal as that refusal.
  - Stores each object once under its digest.
  - regjeringen.no is never fetched while R4 is open.
- **Build** (`lovspor.crosswalk.build`):
  - Reads the scope (law ids, Lovverk commit) the capture recorded. A capture
    made before scopes were recorded (the 2026-11-02 study cache) is refused
    with `this capture records no scope`: recapture it with `capture --laws
    --lovverk-commit` (#793). The library still accepts a scope the caller states
    in `BuildOptions`; no scope is ever inferred from the cached bytes.
  - Re-hashes every cached object against the manifest.
  - Reads only the `discovery` stage, never a label.
  - Keeps every act in scope, attested or not.
  - Writes `research_only` records only.
  - Is append-only against `--previous`: an act whose content changed gets a new
    `research_only` revision; an unchanged one gets none, so a promoted
    `release_candidate` revision is kept, never demoted by a rebuild.
  - The same inputs give the same bytes.
- **Check** (`lovspor.crosswalk.check`) fails on:
  - anything that is not canonical;
  - any contract or history problem;
  - any previous revision that is missing or rewritten;
  - evidence identities a rebuild from the cache does not reproduce.

  It reports two weaker outcomes apart: acts the capture does not cover
  (`not reproduced from a capture`), and acts whose identities are reproduced but
  whose claimed raw bytes (an evidence `body_sha256` or an archive pin) are not
  this capture's (`with raw bytes not in this capture`). The second is byte-only
  source drift or a made-up digest; it never fails the file, and it is never
  certified or promoted.

  `--strict` (only with `--cache`) turns both weaker outcomes into failures: exit
  1 and one `problem:` line per act. Use it wherever the file must rest on exactly
  this capture's bytes; the default stays a report so a research file built from
  an earlier capture can still be checked against a newer one.
- **Release candidate** (`lovspor.crosswalk.release`):
  - Needs an approval file that the owner writes for the input's SHA-256:

    ```json
    {"input_sha256": "<64 hex>", "approved_by": "...", "approved_on": "YYYY-MM-DD", "R1": "approved", "R2": "approved"}
    ```

    `R2` is `approved` or `rejected`. The tool never writes this file.
  - Promotes, as a new revision, only active attested records that the capture reproduces, raw bytes included, and whose cited archives have an accepted licence basis.
  - Corrections stay out while R4 is blocked.
- **Manifest** (`lovspor.crosswalk.manifest`):
  - Is derived only from the checked file.
  - A file whose current records include a `release_candidate` gets a manifest
    only with `--cache`, and only when every current candidate is reproduced
    from that capture, raw bytes included; its `provenance` block names the
    capture manifest's SHA-256. A research manifest written without `--cache`
    carries `"provenance": {"verified": false, ...}` saying so.
  - `provenance.verified` means checked against the named capture with every
    current `release_candidate` reproduced from it. `provenance.fully_reproduced`
    says whether every current record was, raw bytes included: a research file
    with drift has `verified: true`, `fully_reproduced: false` and names the acts
    in `not_reproduced` / `raw_bytes_not_in_capture`. Without `--cache` both are
    `false`.
  - Holds the counts, commits, archive pins, parser versions, source terms (with the date they were read), the provisional attribution and the unresolved rights blockers.
  - Has no wall clock and no `released` state.

The contract is `src/lovspor/crosswalk/legislative-crosswalk-1.0.0-rc.schema.json`
plus the producer invariants in `lovspor.crosswalk.contract`. The v0 schema
Politik #178 reads (`docs/research/schemas/legislative-crosswalk-v0.schema.json`)
is unchanged.
