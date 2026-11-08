# Legislative crosswalk producer (ADR-0017, #610)

Status: implements the rulings the owner gave on 2026-11-06 on the proposal in
`docs/proposals/legislative-crosswalk-decisions-proposal-2026-11-02.md` (which
stays unchanged as the original proposal). They are recorded in lovspor-notebook
`docs/adr/ADR-0017-amendment-1-ruling.md` (#162) and in lovspor-notebook ADR-0019
(Accepted 2026-11-06), which refines ADR-0017. In short:

- D1–D11, R1 and A1 (signed approval, replay, key management) are approved.
  Under D6, history stays append-only with the A1.5 limits below.
- R2 is rejected for now: the licence statement made for the publicData endpoint
  is not accepted as confirmation for `lovtidend-avd1-2026.tar.bz2`. An approval
  must say `"R2": "rejected"`, and records resting on that archive stay
  `research_only`.
- A1.5 is in force in its interim, fail-closed form (B): a historical candidate
  gets no manifest, and a second promotion is unsupported (see "Manifest").
  Evidence for historical candidates is deferred to #818.
- On 2026-11-06, R3 (attribution wording) and R4 (regjeringen.no) stayed
  blocked. R3 is resolved by ADR-0021, below; R4 is still blocked.

This code also implements lovspor-notebook ADR-0021 (2026-11-07), which refines
ADR-0019 without changing it. The ADR holds its own status: this code is meant
to merge only after the owner has accepted it.

- **R2's historical rejection stands.** The endpoint-level statement was
  insufficient on 2026-11-06 and still is: `stated_for_endpoint` is accepted only
  when an approval says `"R2": "approved"`, and approvals say `"R2": "rejected"`.
- **A separate, dataset-level basis.** `lovtidend-avd1-2026.tar.bz2` is pinned
  `stated_for_dataset`: the current-year part of the NLOD 2.0 dataset "Norsk
  Lovtidend, Avdeling I". This rests on different evidence, not on the endpoint
  sentence:
  - Lovdata states NLOD 2.0 for that dataset;
  - its data.norge.no distribution is fetched through `/list` and `/get/` as one
    of two parts;
  - `/list` describes both avd. I files alike;
  - every member of the pinned file is under `lti/2026/` with an `LTI` dokid.
- **Fail closed around it.** No other file gets that basis: a future year, avd. II
  and `gjeldende-*` stay `inferred_same_dataset`. A cited member of the 2026
  archive must be a plain path under `lti/2026/`, with no `.`, `..` or empty
  segment.
- **R3, exact scope.** The manifest carries the attribution NLOD 2.0 §5 asks for,
  with the source name Lovdata brukervilkår 2.3 asks for: Lovdata named as
  source, the licensor, the dataset, source and licence links, a change notice,
  no endorsement and no other licence granted. This covers data derived from
  Lovtidend avd. I, in the crosswalk manifest only. It does not cover another
  dataset, Stortinget (R1, unchanged) or any publication.
- **R4 stays blocked.** Corrections are never promoted.

A ruling is not a release. Publication stays NO-GO. `approval-signers` names one
trusted signer, the owner's key (principal `lovspor-owner`, #840), and nothing here
publishes, tags, pushes, or writes into `lovverk`.
Publishing a dataset needs a separate owner decision.

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
| Release candidate | `lovspor ops crosswalk release-candidate --input FILE --approval FILE --cache DIR --output FILE` (signature in `FILE.sig`) | none | one new JSONL file |
| Manifest | `lovspor ops crosswalk manifest --input FILE --output FILE [--cache DIR [--approval-proof DIR]]` | none | one new manifest file |

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
  - `approved_by` is text and authenticates nobody (#792). The approval counts
    only with a detached SSH signature over its exact bytes, beside it as
    `<approval>.sig`, made in the namespace `lovspor-crosswalk-approval`:

    ```bash
    ssh-keygen -Y sign -f <owner key> -n lovspor-crosswalk-approval approval.json
    ```

    The signer must be listed in `src/lovspor/crosswalk/approval-signers`
    (OpenSSH allowed_signers format). It names exactly one signer: the owner's
    own `ssh-ed25519` public key, principal `lovspor-owner`, restricted to
    `namespaces="lovspor-crosswalk-approval"`, added by the owner in a change they
    made and merged (#840). `tests/unit/test_crosswalk_shipped_signers.py` pins
    its fingerprint, so another signer, a wider namespace or a changed key fails
    CI. A file with no signer line refuses every approval (`no trusted approval
    signer is configured: release candidates stay blocked`). No tool or agent
    creates, chooses or adds a key.
  - Promotes, as a new revision, only active attested records that the capture reproduces, raw bytes included, and whose cited archives have an accepted licence basis: `stated_for_file` and `stated_for_dataset` always, `stated_for_endpoint` only under `"R2": "approved"`, `inferred_same_dataset` never. A record citing a member of a `stated_for_dataset` archive outside that archive's Avdeling I year (`lti/2026/`) stays `research_only`.
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
  - A `release_candidate` file also needs `--approval-proof DIR` holding
    `approval.json`, `approval.json.sig` and `approved-input.jsonl` (#792). The
    signature is verified as above, then the gate is replayed:
    `promote(approved-input.jsonl, approval, capture)` must give the file byte for
    byte. A hand-edited `release_state`, a rewritten earlier revision, drift, a
    wrong pin or another approval's output gets no manifest. The manifest's
    `approval` block names the approved input, approval, signature and trusted
    signers file by SHA-256, plus the signer's principal. Research manifests carry
    no `approval` block and are unchanged.
  - The approved input must be `research_only` in every revision. A candidate
    revision it carried would pass through the gate untouched and look proven by
    an approval that never authorized it.
  - A `release_candidate` revision that is no longer current (a candidate later
    withdrawn or superseded, or a forged revision 1) still claims a past owner
    authorization. The file keeps it as written, append-only, and gets **no
    manifest** (ruling A1.5, interim variant B, fail-closed). Files with no
    candidate revision anywhere, withdrawals included, are unaffected.
  - Not supported for now, by that rule:
    - a second promotion of a file that already holds a candidate: its approved
      input carries the first candidate and is refused;
    - withdrawing or superseding a candidate in a file that is to be described:
      the file stays valid under `check` but gets no manifest.

    Evidence for past candidates is deferred to #818: proof chains, key rotation
    and revocation of past approvals, replay under the recorded
    `producer_commit`, and manifest versioning.
  - `approval.json`, its `.sig` and `approved-input.jsonl` must be regular files:
    a FIFO, device, directory or a symlink to one is refused before anything is
    read, so a proof directory cannot hang the gate. ssh-keygen verifies a
    private copy of the signature bytes that were read, so the manifest's
    `signature_sha256` is of exactly the signature that was verified.
  - Holds the counts, commits, archive pins, parser versions, source terms (with the date they were read and the statement relied on), the attribution (wording in nb and en, plus the structured Lovdata source) and the unresolved blockers (R4, and the separate publication step).
  - Has no wall clock and no `released` state.

The contract is `src/lovspor/crosswalk/legislative-crosswalk-1.0.0-rc.schema.json`
plus the producer invariants in `lovspor.crosswalk.contract`. The v0 schema
Politik #178 reads (`docs/research/schemas/legislative-crosswalk-v0.schema.json`)
is unchanged.

`stated_for_dataset` widened the `licence_basis` enum without a version change.
That is allowed only because `1.0.0-rc` is an unpublished pre-release: every
earlier record still validates, and no `1.0.0-rc` file has left the private
workspace. A strict `1.0.0-rc` reader written before the change rejects the new
value, fail-closed. Politik's open research reader (#185 there, a draft) took the
value on 2026-11-08: it accepts `stated_for_dataset` only on
`lovtidend-avd1-2026.tar.bz2`, binds each cited member to its archive's year, and
requires the manifest's NLOD 2.0 attribution for such a file. It still refuses
every `release_candidate`; an opt-in reader that verifies the owner's signed
approval itself is a separate draft (#186 there). Once a `1.0.0-rc` file is
published or a strict reader merges, a further enum change needs a version bump
(ADR-0021, "Contract version").
