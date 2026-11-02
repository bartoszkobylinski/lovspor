# Legislative crosswalk: proposed decisions after the E4 pilot

**Status: PROPOSED — OWNER APPROVAL REQUIRED.** Nothing here is accepted. It
authorises no public artifact, no new MCP tool, no Lovverk migration, no
production change and no Politik change.
**Date:** 2026-11-02
**Authority:** accepted [ADR-0017](https://github.com/bartoszkobylinski/lovspor-notebook/blob/main/docs/adr/ADR-0017-source-backed-legislative-crosswalk.md)
(ownership and evidence rules only). Private counterpart:
`lovspor-notebook/docs/adr/ADR-0017-amendment-1-proposal.md`. This public file
is the specification; the notebook is not a dependency of it (ADR-0017 gate 3).
**Tracking:** [#610](https://github.com/bartoszkobylinski/lovspor/issues/610),
[#618](https://github.com/bartoszkobylinski/lovspor/issues/618),
[Politik #177](https://github.com/bartoszkobylinski/norwegian_parilamentary/issues/177).
**Input:** the nine owner decisions listed in
[`docs/research/pilot-e4-2026-11-01.md`](../research/pilot-e4-2026-11-01.md)
("Owner decisions needed before publication"), from merged PR #756.
**Draft schema:** [`docs/research/schemas/legislative-crosswalk-v0.2-proposal.schema.json`](../research/schemas/legislative-crosswalk-v0.2-proposal.schema.json),
tested by `tests/unit/test_legislative_crosswalk_v0_2_proposal.py`. The v0 schema
is unchanged.

Labels: **Fact** is read from a cited file or URL. **Inferred** is reasoning, not
stated by a source. **BLOCKED** means a precondition is not established and no
recommendation below assumes it.

## Evidence base (read 2026-11-02)

- `GET https://api.lovdata.no/v1/publicData/list` at 2026-11-02T09:08Z lists six
  archives with `filename`, `description`, `sizeBytes`, `lastModified` and **no
  licence field**. Among them `lovtidend-avd1-2001-2025.tar.bz2` (lastModified
  2026-10-29T02:31:00Z, the same as the pilot capture) and
  `lovtidend-avd1-2026.tar.bz2` (2026-10-31T02:31:00Z), both described as "Norsk
  Lovtidend avd. I - lover og sentrale forskrifter". Avd. II is listed too; that
  is [#736](https://github.com/bartoszkobylinski/lovspor/issues/736), out of scope here.
- Fact, `https://api.lovdata.no/om-api-tjenesten/`: "Datasettet til Norsk Lovtiden
  avd. 1 finner du på Data.norge.no . Det kan fritt benyttes til alle formål uten
  vederlag. Dataene er lisensiert under NLOD 2.0."
- Fact, the linked data.norge.no entry ("Norsk Lovtidend, Avdeling I", publisher
  Stiftelsen Lovdata): licence NLOD 2.0; its distribution names only
  `lovtidend-avd1-2001-2025.tar.bz2`; "Rettigheter for bruk: Ikke oppgitt".
- Fact: api.lovdata.no links no terms-of-use page; the swagger has no
  `termsOfService` or `license`. The FAQ asks users to credit Lovdata
  ("Hvorfor må vi kreditere Lovdata ved bruk?") and gives no wording. The 2026
  archive contains no licence file.
- Fact, NLOD 2.0 §5 (`https://data.norge.no/nlod/en/2.0`): attribute "as specified
  by the licensor"; otherwise «Contains data under the Norwegian licence for Open
  Government data (NLOD) distributed by [name of licensor]», with links to the
  licence and the source.
- Fact, `https://data.stortinget.no/om-datatjenesten/bruksvilkar/`: terms follow
  "til enhver tid gjeldende Norsk lisens for offentlige data" (no fixed NLOD
  version); "Stortinget skal oppgis som kilde"; data must not be presented
  misleadingly; 100 API calls per minute.
- Fact, pilot E4: law → decision `attested` from a Lovtidend avd. I header;
  decision → case `attested` from `eksport/sak`; Stortinget responses embed
  `respons_dato_tid` and came from a cache; cache expiry not measured. From 2013
  on 1,658 of 1,663 law documents cite exactly one Lovvedtak in the header; before
  2013, 279 cite one and 1,074 cite none. LOV-2009-06-19-107 (Besl. O. 87,
  2008-2009) is `unresolved`. For #618 neither header states which act corrects
  which.
- Fact, `https://data.stortinget.no/eksport/publikasjoner?publikasjontype=lovvedtak&sesjonid=2008-2009`
  returned 155 entries titled "Besl. O. nr. N (2008-2009)" with ids `beso-200809-NNN`.
  Not verified: whether pre-2009 sak records carry typed references to `beso-` ids.
- Fact, ADR-0018 (Accepted 2026-11-01) put forarbeider in an opt-in Lovverk
  namespace with its own manifest (its A2) and kept a separate repository (A3)
  only "if future rights/retention requirements make a common repository
  impossible".
- Fact: Politik PR #178 (merged 2026-10-20) reads contract v0 (`0.1.0-research`)
  as a research-only fixture reader.

## a. Lovtidend avd. I via publicData as the source of the law → decision edge

**Recommendation.** Accept the Lovtidend avd. I header citation, read only from
`https://api.lovdata.no/v1/publicData/`, as the primary source of the
law → decision edge, and as evidence input only: no Lovtidend text enters
`lovverk`, no new corpus dataset is created, raw XML stays out of every repository.
What a record publishes from it is limited to identities (law id, `dokid`,
cited `(session, number)`), digests, archive pins and the parser version.

Rights: NLOD 2.0 is **stated** for the dataset "Norsk Lovtidend avd. 1" by Lovdata
and by data.norge.no for `lovtidend-avd1-2001-2025.tar.bz2`. Attribution, for any
release: name Stiftelsen Lovdata as licensor, link NLOD 2.0 and the source URL,
mark the record as derived.

- **BLOCKED — rights for `lovtidend-avd1-2026.tar.bz2`.** No source names this file
  with a licence; coverage is inferred from the shared description. 15 of the 52
  study acts are 2026 acts. Until Lovdata (`api@lovdata.no`) or data.norge.no
  states it, records depending on the 2026 archive stay `research_only`. The draft
  schema encodes this: `licence_basis: inferred_same_dataset` cannot appear in a
  `release_candidate`.
- **BLOCKED — exact attribution wording.** Lovdata requires credit but specifies no
  wording. The NLOD §5 default would apply only if the licensor specified none;
  that reading is inferred. Decide the wording, or ask Lovdata, before any release.

**Alternatives considered.** (1) regjeringen.no sanction notices: ADR-0017 allows
them, and they cover acts with no header citation, but no reuse-terms review exists
for regjeringen.no in this repository. Keep them as a second source, BLOCKED for
automated capture until reviewed. (2) lovdata.no HTML: forbidden by `CLAUDE.md`. (3)
Stortinget sak pages that print "Sanksjonert … (lovNN)": HTML pages, and the
`eksport/sak` XML carries no law number (pilot). (4) Status quo, `docs/decisions.md`
§3 "optional later": leaves the only measured machine source unsanctioned.

## b. Evidence retention and access

**Recommendation.**
- **Stortinget:** keep committing raw `eksport/sak` bodies only as a small, named
  regression set under `tests/fixtures/stortinget/`, with the existing README that
  names Stortinget as source. Study and production captures go to the operator
  cache (`data/cache/`, gitignored, write-once via `archive_sak`). A release may
  publish digests and parsed identities, not bodies. Publishing bodies alongside a
  release is a separate owner choice: NLOD permits it with attribution, but nothing
  needs it.
- **Lovdata:** no raw XML in any repository, including test fixtures (they stay
  synthetic). Evidence is the archive pin (filename, URL, SHA-256, size,
  `lastModified`, retrieval time), member path, member SHA-256, parsed identity and
  parser version. Replay re-downloads the archive. If upstream has rebuilt it, replay
  compares parsed identities (item g) and reports the new archive digest; it never
  claims the old bytes.
- Any retained copy of a Lovdata archive lives only in an access-controlled operator
  cache, never in Git or a public bucket.

**Alternatives considered.** Committing Lovdata members as fixtures (rejected by
`docs/decisions.md` §4). Publishing all Stortinget bodies (larger, gains nothing over
digests plus a replay recipe, and widens what must stay attributed). Cache only, no
committed Stortinget fixtures (loses the CI replay `test_legislative_chain.py` has
now).

## c. Artifact location, schema and versioning

**Recommendation: a separately versioned derived dataset outside `lovverk`**, produced
by Lovspor, with its own manifest and version tags. Lovspor docs and ADR-0004 use
"sidecar" for a per-document derived binary (`embeddings/<slug>.bin`); the crosswalk
is a table, so this proposal says *separately versioned dataset* to avoid overloading
that term. The concrete host (a small dedicated Git repository, or release assets of
`lovspor`) is an owner choice. A dedicated Git repository is preferred: it keeps Git
as the version store (ADR-0003), and Politik can read it as a sibling checkout the
way it reads `lovverk` today. Nothing is created before GO.

Why not `lovverk/enrichment/legislative-links/` (ADR-0017's candidate):
- `lovverk` states two licence classes for its content (NLOD 2.0 for Lovdata text,
  § 14 for local regulations) plus CC0 for structure. The crosswalk mixes Lovdata
  NLOD 2.0 identities with Stortinget data under an unversioned NLOD — a third
  rights statement inside the legal corpus.
- Crosswalk records have their own revision and withdrawal history (ADR-0017 "own
  observation/version history"). In `lovverk` every correction becomes a corpus
  commit, and the corpus history becomes the crosswalk's history.
- Schema versions would move with corpus commits, while Politik needs to pin a
  schema version independently.

Evidence the other way, stated so the owner can weigh it: ADR-0018 chose an opt-in
Lovverk namespace for forarbeider and named reconciliation with ADR-0017 as a cost of
a separate repository. One `lovverk` commit would also pin corpus and crosswalk
together. Revisit if the owner prefers that symmetry; the schema below works in either
place.

**Versioning policy (proposed).**
- `schema_version` is semver. `0.x` is research. `1.0.0` is the first owner-approved
  release. A minor bump adds optional fields only. A major bump removes, renames or
  re-types a field, adds a required field, adds an enum value a strict consumer must
  handle, or changes a status's meaning.
- `release_state`: `research_only` → `release_candidate` → `released`. The draft
  admits only the first two; `released` needs owner approval and a version bump.
- Each release has a manifest: schema version, record count by status, source pins
  (item d), producer commit, and the SHA-256 of every file.
- **Compatibility with Politik's v0 reader:** `legislative-crosswalk-v0.schema.json`
  and the 52-record v0 research fixture stay unchanged while #178 reads them. The
  v0.2 draft is a parallel file with a different `schema_version`, and a test shows a
  v0 validator rejects a v0.2 record, so no silent reinterpretation occurs. Politik
  moves only through a reader change in Politik, after a release exists.

The draft schema adds to v0: `record_revision`, `record_state`, `supersedes_revision`,
`withdrawal`; `edge_status` per edge; `reason_code`; `source_pins`;
`parsed_identity_sha256` on raw evidence; `unclassified_sanction`; a required
`correction` evidence item for any `corrective_resanction`.

**Alternatives considered.** In-`lovverk` namespace (above). Data under
`lovspor/docs/research/data/` (today's practice; acceptable for research, but mixes
engine history with dataset history). Accepting v0 as `1.0.0` (v0 has no source pins,
revisions or parsed identity; see `legislative-crosswalk-contract-v0-2026-10-14.md`
"Owner gates left open").

## d. Source and corpus pins

**Recommendation.** Every record and every release manifest pins:
- `lovverk_commit` (40-hex) of the corpus the amending-law ids were read from;
- `producer_commit` of the `lovspor` engine that produced the record;
- per Lovdata archive: `filename`, `source_url`, `sha256`, `size_bytes`,
  `last_modified` as served by `/list`, `retrieved_at`, `licence`, `licence_basis`;
- per raw evidence item: `upstream_id` (`<archive>!<member>` or `sak/<id>`),
  `body_sha256`, `parsed_identity_sha256`, `retrieved_at`, `parser_version`;
- parser versions (`lovtidend-identity-1`, `sak-xml-2` at present).

A parser-version change re-derives every record and is visible as new revisions; a
record never mixes parser versions within one edge.

**Alternatives considered.** Pinning only the corpus commit (cannot replay sources);
pinning raw digests only (item g).

## e. Revocation and corrections of crosswalk records

**Recommendation.** Records are append-only per `amending_lov_id`. A changed outcome
is a new `record_revision` with `supersedes_revision`; the earlier revision is marked
`superseded` in the next release and stays in history. An edge Lovspor itself got
wrong is a new revision with `record_state: withdrawn` and a `withdrawal` reason and
date. Nothing is deleted from a released file set. This crosswalk-level retraction is
distinct from a legal correction event (item f), which is a fact about the sources and
is recorded as data.

**Alternatives considered.** Deleting a wrong record (fails ADR-0017's own-history
rule and #610 slice B "must not rewrite earlier observations"). Reusing the
observatory tombstone model (ADR-0015 is accepted for the observation log only;
the shape is analogous, not inherited).

## f. #618 corrective re-sanction events

**Recommendation.**
- One decision may have several sanction events, each linked to its own law id. The
  `(session, kind, number) → LOV` mapping is never single-valued.
- `sanction_event.kind`: `initial_sanction`, `corrective_resanction`, or
  `unclassified_sanction`. The last is the default when a permitted source proves the
  sanction but no source states its role.
- `corrective_resanction` with `corrects_law_id` may be emitted only with a
  `correction` evidence item from a source that states the correction explicitly:
  an official government correction record, or a Lovdata publicData document whose
  content states it. A Lovtidend header citing the same Lovvedtak does not (pilot:
  "neither header states a correction"). A Stortinget page mapping Lovvedtak 41 to
  law 67 shows the current pairing, not a correction.
- Status: the regjeringen.no notices cited in #618 are the candidate source, and
  their reuse terms are unreviewed (item a). Until reviewed, both #618 acts are
  `unclassified_sanction` in machine output. The v0 research fixture keeps its
  human-reviewed `corrective_resanction` as it is.
- No event implies a date of legal effect.

**Alternatives considered.** Inferring correction from order of dates (an inference,
forbidden by ADR-0017 "no silent inference"); dropping the earlier act (hides a real
event, rejected in #618).

## g. Raw digest vs parsed identity

**Recommendation.** Keep both. `body_sha256` is provenance: which bytes this run read.
`parsed_identity_sha256` is the comparison key: SHA-256 of the canonical JSON
(sorted keys, no whitespace, UTF-8) of what the named parser extracted. A repeat
capture with a new body digest and the same parsed identity is "no change"; a
changed parsed identity is a new record revision. Comparisons across parser versions
are not made.

Not measured, and to be reported by the study: whether Stortinget bodies change
after the cache expires (they embed `respons_dato_tid`), and whether a Lovdata
archive rebuild changes member digests.

**Alternatives considered.** Raw digest as the comparison key (spurious changes from
server timestamps); parsed identity only (loses proof of which bytes were read).

## h. Besl. O. / Odelsting scope

**Recommendation.** Keep `besl_o` typed (v0 already has it) and emit it as
`unsupported` with `reason_code: decision_kind_not_supported`, not `unresolved`, until
measured: it is a scope boundary of the reader, not a failed lookup. The draft schema
enforces this. Separately, acts without a header citation (most pre-2013 acts) are
`unresolved` with `no_decision_citation_in_source`.

Extension plan, when the owner wants it: (1) measure whether 2008-2009 and earlier
sak records carry typed references to `beso-` ids; (2) measure how far back
`eksport/publikasjoner` and `eksport/saker` return data; (3) extend the reader with a
new parser version only if both hold; (4) Besl. L. not yet seen anywhere.

**Alternatives considered.** Dropping pre-2009 acts from scope silently (hides a
coverage boundary); extending the reader now (unmeasured source shape).

## i. Reconciliation with `docs/decisions.md` §2 and §3 (ADR-0017 gate 5)

**Recommendation.** Append notes; rewrite nothing (`docs/decisions.md`: "don't delete
the old entry — append a new one that supersedes it"). This PR does not edit
`docs/decisions.md` or `docs/roadmap.md`; the text below is proposed for a follow-up
PR after approval.

Proposed note appended to §2, after the 2026-10-14 clarification:

> **Roadmap reconciliation proposed 2026-11-02, approved \<date\> (ADR-0017 gate 5):**
> `docs/roadmap.md` D-DIRECT-4 ("which parties voted how on a given act") is not a
> Lovspor deliverable: ADR-0017 assigns divisions, votes and speeches to Politik,
> and Lovspor publishes only the source-backed crosswalk
> `LOV ↔ (session, decision kind, number) ↔ sak_id`. D-DIRECT-3 (forarbeider) is
> governed by ADR-0018 as a separate opt-in dataset; any forarbeid → act link follows
> ADR-0017's evidence rules. "Full parliamentary enrichment" above remains out of
> scope. This note records the reconciliation; it does not rewrite the original entry.

Proposed note appended to §3:

> **Crosswalk evidence sources, proposed 2026-11-02, approved \<date\>:** For the
> ADR-0017 crosswalk only, `lovtidend-avd1-{2001-2025,2026}.tar.bz2` from the same
> publicData API is read as **evidence** for the law → decision edge. It is not
> ingested into `lovverk` and raw XML stays out of every repository. Stortinget
> `eksport/sak` (and `eksport/publikasjoner`/`eksport/saker` for discovery) is read as
> evidence for the decision → case edge, under Stortinget's NLOD terms, naming
> Stortinget as source, at ≤100 calls per minute. Neither is a primary source of law
> text. Rights status per archive is recorded in each release (see
> `docs/proposals/legislative-crosswalk-decisions-proposal-2026-11-02.md`). The
> "Optional later" and "NOT used as a primary source" lines above stand as written.

Proposed annotation on `docs/roadmap.md` D-DIRECT-4: "Superseded in scope by ADR-0017
(votes stay in Politik); see `docs/decisions.md` §2."

Related but separate: `docs/roadmap.md` "serves exactly four archives" is stale against
the live `/list` (six archives). That is #736's decision, not this one.

## j. Next measurement: the 52-act study (Workstream B)

The study is being built in parallel on branch `feat/610-study-52`. It has no results
yet and this document states none.

**Design requirements (proposed).**
- Input is the 52 amending-law ids of the pinned ledger only. The manual columns
  (`sak_id`, `session_id`, `decision_number`, `proceeding_kind`, URLs) are evaluation
  labels and are never read by the pipeline.
- Both archives are pinned (`-2001-2025` and `-2026`); the 2026 rows are reported with
  `licence_basis: inferred_same_dataset` (item a).
- Report two results separately, never as one number:
  1. **Label-free discovery recall:** of 52, how many produce an `attested` chain
     from the law id alone (Lovtidend header → `(session, number)` → sak found through
     Stortinget listings and confirmed by a typed `publikasjon_referanse`), and of
     those, how many equal the label.
  2. **Conditional verification:** given each labelled `(decision, sak)`, how many the
     reader attests, contradicts, or leaves ambiguous/unresolved/unsupported, with
     reason codes.
- Strata reported separately: pre-2013 vs 2013 on; 2026 archive vs 2001-2025;
  Lovvedtak vs Besl. O. n = 52 is a regression set, not a population estimate.

**GO** (to draft a `release_candidate` for the measured scope) requires all of:
zero `attested` edges that disagree with a label, unless the disagreement is an
already documented source disagreement recorded as data; every non-attested outcome
carries a `reason_code`; a second run reproduces every parsed identity; no `henvisning`,
title or date was used to attest an edge. Recall has no pass mark: it is reported
with its denominator and decides scope, not GO.

**NO-GO** if any of: an unexplained wrong `attested` edge; non-reproducible parsed
identities; an attestation that relied on free text; the rights items in (a) remain
BLOCKED for records that would be released (those records stay `research_only`
regardless).

**Alternatives considered.** A single headline "coverage" number (mixes discovery with
verification); fixed recall thresholds (no baseline exists to set them).

## Owner decisions requested

- [ ] Form of the ruling: Amendment 1 section in ADR-0017, or a new ADR (see the
      notebook counterpart; repository docs disagree).
- [ ] (a) Accept Lovtidend avd. I via publicData as the law → decision source, evidence only.
- [ ] (a) BLOCKED: settle rights for `lovtidend-avd1-2026.tar.bz2` and the attribution wording.
- [ ] (b) Retention: Stortinget regression fixtures only; Lovdata pins, never bytes.
- [ ] (c) Location: separately versioned dataset outside `lovverk`, and its host.
- [ ] (c) Versioning policy and the v0.2 draft as the basis of `1.0.0`.
- [ ] (d) Pin set.
- [ ] (e) Append-only revisions and withdrawals.
- [ ] (f) #618 event model; correction only from an explicit source; regjeringen.no terms review.
- [ ] (g) Parsed identity for comparison, raw digest for provenance.
- [ ] (h) Besl. O. as `unsupported` until measured.
- [ ] (i) The §2, §3 and roadmap notes above, in a follow-up PR.
- [ ] (j) The study's GO / NO-GO criteria.
