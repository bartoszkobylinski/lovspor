# Legislative crosswalk producer (ADR-0017, #610)

Status: implementation prepared against the PROPOSED rulings in
`docs/proposals/legislative-crosswalk-decisions-proposal-2026-11-02.md`; nothing
here is in force until the owner records them (lovspor-notebook
`docs/adr/ADR-0017-amendment-1-ruling.md`). Nothing here publishes, tags, pushes,
or writes into `lovverk`.

All commands sit on the `ops` group (`lovspor.cli` is at its size ratchet).

| Step | Command | Network | Writes |
|---|---|---|---|
| Capture | `lovspor ops crosswalk capture --cache DIR --laws FILE --lovverk-commit SHA` | Lovdata publicData, `data.stortinget.no/eksport` only | a new, gitignored cache directory (write-once) |
| Build | `lovspor ops crosswalk build --cache DIR --output FILE [--previous FILE]` | none | one new canonical JSONL file |
| Check | `lovspor ops crosswalk check --input FILE [--previous FILE] [--cache DIR]` | none | nothing; exit 1 on any problem |
| Release candidate | `lovspor ops crosswalk release-candidate --input FILE --approval FILE --cache DIR --output FILE` | none | one new JSONL file |
| Manifest | `lovspor ops crosswalk manifest --input FILE --output FILE` | none | one new manifest file |

- **Capture** (`lovspor.crosswalk.capture`):
  - Reads every Lovtidend avd. I archive the catalogue lists, the decision sessions' listings and every case a listing hint proposes.
  - Paces Stortinget under 100 calls a minute.
  - Retries at most three times, and only on a 429, a 5xx or a transport failure.
  - Refuses redirects.
  - Records every refusal as that refusal.
  - Stores each object once under its digest.
  - regjeringen.no is never fetched while R4 is open.
- **Build** (`lovspor.crosswalk.build`):
  - Re-hashes every cached object against the manifest.
  - Reads only the `discovery` stage, never a label.
  - Keeps every act in scope, attested or not.
  - Writes `research_only` records only.
  - Is append-only against `--previous`.
  - The same inputs give the same bytes.
- **Check** (`lovspor.crosswalk.check`) fails on:
  - anything that is not canonical;
  - any contract or history problem;
  - any previous revision that is missing or rewritten;
  - evidence a rebuild from the cache does not reproduce.
- **Release candidate** (`lovspor.crosswalk.release`):
  - Needs an approval file that the owner writes for the input's SHA-256:

    ```json
    {"input_sha256": "<64 hex>", "approved_by": "...", "approved_on": "YYYY-MM-DD", "R1": "approved", "R2": "approved"}
    ```

    `R2` is `approved` or `rejected`. The tool never writes this file.
  - Promotes, as a new revision, only active attested records that the capture reproduces and whose cited archives have an accepted licence basis.
  - Corrections stay out while R4 is blocked.
- **Manifest** (`lovspor.crosswalk.manifest`):
  - Is derived only from the checked file.
  - Holds the counts, commits, archive pins, parser versions, source terms (with the date they were read), the provisional attribution and the unresolved rights blockers.
  - Has no wall clock and no `released` state.

The contract is `src/lovspor/crosswalk/legislative-crosswalk-1.0.0-rc.schema.json`
plus the producer invariants in `lovspor.crosswalk.contract`. The v0 schema
Politik #178 reads (`docs/research/schemas/legislative-crosswalk-v0.schema.json`)
is unchanged.
