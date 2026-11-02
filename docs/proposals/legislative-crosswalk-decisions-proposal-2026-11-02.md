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
  Stiftelsen Lovdata, `https://data.norge.no/api/datasets/c0c6a87c-f597-3735-965f-650be23426a0`):
  distribution licence NLOD 2.0; its description says "Datasettet er delt i to: *
  Data fra 2001 til og med sist årsskifte * Data fra inneværeende år."; the
  distribution's `downloadURL` names only `lovtidend-avd1-2001-2025.tar.bz2`.
- Fact (re-checked 2026-11-02, corrects an earlier line of this file): the OpenAPI
  document `https://api.lovdata.no/swagger/api.json` has no licence in `info`, but
  its tag "Public data" reads "Public data released under the NLOD 2.0 license. No
  account or authentication required." and links `https://data.norge.no/nlod/no/2.0`.
  Both `/v1/publicData/list` and `/v1/publicData/get/{filename}` carry that tag, so
  the statement covers every file the endpoint serves, `lovtidend-avd1-2026.tar.bz2`
  included. SHA-256 of the document as read: `fbcaf3f97d310318c244bf508784abd22754b2011c2f14db238d1cfb14b07cec`.
- Fact: api.lovdata.no links no terms-of-use page. The FAQ asks users to credit
  Lovdata ("Hvorfor må vi kreditere Lovdata ved bruk?") and gives no wording. The
  2026 archive contains no licence file, and responses carry no licence header.
- Fact, regjeringen.no: the only reuse page under "Om nettstedet" covers images and
  video (`https://www.regjeringen.no/no/om-nettstedet/bruk-av-bilder-og-videomateriale/id2715005/`);
  no licence for text or PDF was found. Fact, åndsverkloven § 14 (read from the
  Lovverk corpus): "Lover, forskrifter, rettsavgjørelser og andre vedtak av offentlig
  myndighet er uten vern etter denne loven", and the same holds for proposals and
  statements "avgitt av offentlig myndighet".
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
  2008-2009) is `unresolved` in the pilot and `unsupported` in the 52-act study
  (item h). For #618 neither header states which act corrects which.
- Fact, 52-act study (merged PR #768): 52/52 law identities, 51/51 label-free
  discovery matches, 51/51 conditional verification, 1 Besl. O. `unsupported`, no
  observed failure in this sample (item j).
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

Rights, per archive (status as read 2026-11-02):

| Archive | Status | Basis | `licence_basis` |
|---|---|---|---|
| `lovtidend-avd1-2001-2025.tar.bz2` | **VERIFIED** | data.norge.no distribution names the file under NLOD 2.0; the OpenAPI "Public data" tag states NLOD 2.0 | `stated_for_file` |
| `lovtidend-avd1-2026.tar.bz2` | **VERIFIED** (endpoint-level) | the OpenAPI "Public data" tag states NLOD 2.0 for `/get/{filename}`; data.norge.no describes the current-year part under the same NLOD 2.0 distribution. No statement names this filename. | `stated_for_endpoint` |

The earlier version of this file called the 2026 rights BLOCKED and "inferred from
the shared description". That was wrong: it missed the tag-level statement. The
2026 file is not assumed to inherit the older archive's licence; it is covered by
its own endpoint statement. Whether an endpoint-level statement is enough for a
release is the owner's call in item k; the draft schema keeps it distinct from
`stated_for_file` so the choice stays visible, and refuses `inferred_same_dataset`
in a `release_candidate`.

- **Attribution wording: partly BLOCKED.** NLOD 2.0 §5 (VERIFIED): attribute the
  licensor "as specified by the licensor"; if none is specified, «Inneholder data
  under Norsk lisens for offentlige data (NLOD) tilgjengeliggjort av [lisensgiver]»,
  link the licence and the source where practical, and state that changes were
  made. api.lovdata.no specifies no wording (VERIFIED absence). Whether a lovdata.no
  page specifies one was not checked, because this project never fetches lovdata.no
  HTML: BLOCKED until the owner reads it or asks `api@lovdata.no`. The proposed
  wording, if nothing more specific exists, is in item k.

**Alternatives considered.** (1) regjeringen.no sanction notices: ADR-0017 allows
them, and they cover acts with no header citation. Reviewed 2026-11-02: the site
publishes reuse terms for images and video only, none for text. The candidate basis
is åndsverkloven § 14 (decisions and statements of public authority are not
protected), which lovspor already relies on for forarbeider. Whether a given
sanction or correction notice is such a decision or statement is a per-document-type
reading (INFERRED, not verified), and no attribution requirement is stated
(BLOCKED: owner policy, or ask DIO). Keep them as a second source, not captured
automatically until the owner rules on the § 14 basis for that document type. (2) lovdata.no HTML: forbidden by `CLAUDE.md`. (3)
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
  v0.2 draft is a parallel file with a different `schema_version`. Politik's reader
  (`src/analysis/lovspor_crosswalk.py` on Politik `main`) accepts only
  `schema_version: "0.1.0-research"` and `release_state: "research_only"`, forbids
  extra fields and marks the whole artifact invalid on any failure, so a v0.2 file
  fails closed there. A lovspor test shows the v0 JSON Schema rejects a v0.2 record
  too. The Politik model, not the v0 JSON Schema, is the effective consumer contract:
  it also checks that the amending law is in the sanction history, that no sanction
  is duplicated, that a correction's predecessor exists and that the case URL names
  the `sak_id`. v0.2 keeps these as producer invariants (item l). A future Politik
  reader must select the highest `record_revision` per law before applying its
  "more than one record means ambiguous" rule. Politik moves only through a reader
  change in Politik, after a release exists.

The draft schema adds to v0: `record_revision`, `record_state` (`active` |
`withdrawn`), `withdrawal`; `edge_status` per edge (`law_identity`, `law_decision`,
`decision_case`); `reason_code`; `source_pins`; `parsed_identity_sha256` on raw
evidence; `unclassified_sanction` as the only role besides `corrective_resanction`;
a `decision_publication` evidence role; a required `correction` evidence item for
any `corrective_resanction`. It drops v0's `reviewed_web_only` status, whose
human-reviewed fixture stays in v0. Item l lists the review that shaped it.

**Alternatives considered.** In-`lovverk` namespace (above). Data under
`lovspor/docs/research/data/` (today's practice; acceptable for research, but mixes
engine history with dataset history). Accepting v0 as `1.0.0` (v0 has no source pins,
revisions or parsed identity; see `legislative-crosswalk-contract-v0-2026-10-14.md`
"Owner gates left open").

## d. Source and corpus pins

**Recommendation.** Every record and every release manifest pins:
- `lovverk_commit` (40-hex) of the corpus the amending-law ids were read from;
- `producer_commit` of the `lovspor` engine that produced the record;
- per Lovdata archive: `source_url` (which names the file), `sha256`, `size_bytes`,
  `last_modified` as served by `/list`, `retrieved_at` (UTC), `licence`,
  `licence_basis`;
- per raw evidence item: `upstream_id` (`<archive>!<member>`, `sak/<id>` or
  `publikasjoner/<session>`), `body_sha256`, `parsed_identity_sha256`,
  `retrieved_at` (UTC), `parser_version`. The parser version is pinned here, per
  item, and nowhere else: the study already uses four parsers
  (`lovtidend-identity-1`, `besl-o-header-1`, `sak-xml-2`,
  `lovvedtak-discovery-2`), and a fixed per-record parser map could not hold them.

A parser-version change re-derives every record and is visible as new revisions; a
record never mixes parser versions within one edge.

**Alternatives considered.** Pinning only the corpus commit (cannot replay sources);
pinning raw digests only (item g).

## e. Revocation and corrections of crosswalk records

**Recommendation.** Records are append-only per `amending_lov_id`. A changed outcome
is a new `record_revision` (revisions run 1..n without gaps); the highest revision is
current, and supersession is derived from that, never written back into the earlier
revision, whose bytes stay frozen once released. A re-capture whose parsed
identities are unchanged emits no new revision. An edge Lovspor itself got wrong is
a new revision (≥ 2) with `record_state: withdrawn`, a non-attested status,
`reason_code: withdrawn_by_producer` and a `withdrawal` reason and date. Nothing is
deleted from a released file set: the release check verifies that every
`(amending_lov_id, record_revision)` of the previous release is present and
byte-identical. This crosswalk-level retraction is
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
- `sanction_event.kind`: `unclassified_sanction` or `corrective_resanction`. The first
  is the default whenever a permitted source proves the sanction but no source states
  its role, which is every case measured so far. The earlier draft also had
  `initial_sanction`; it is dropped, because a Lovtidend header citing a Lovvedtak
  states an "initial" role no more than a "corrective" one, and the pilot fixture's
  `initial_sanction` was exactly such an unproven claim.
- The 52-act study reports the #618 relation as `corrective_relation: unsupported`
  (no permitted source states it); in a record the same fact is two
  `unclassified_sanction` events. Same meaning, record vocabulary wins.
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
(sorted keys, no whitespace, UTF-8) of what the named parser extracted. The fields
are fixed per parser version: for a Lovtidend member `{law_id, lovvedtak}` (the cited
decisions); for a Stortinget case `{sak_id, sak_sesjon, lovvedtak}` (the typed
decisions); for a publication list, proposed: the sorted Lovvedtak ids of the session
(the pilot test digests the first two; the third is not implemented). A change to that field
list is a new parser version. A repeat
capture with a new body digest and the same parsed identity is "no change"; a
changed parsed identity is a new record revision. Comparisons across parser versions
are not made.

Measured by the study (item j): pilot vs study, 2 saker are `drift_in_bytes_only`
(new digest, same parsed identity) and 3 `identical`; discovery vs verification pass,
51/51 `identical`. The 2001–2025 archive digest and the shared members' digests were
unchanged since the pilot. Still not measured: body behaviour after the Stortinget
cache expires over a longer interval (56 of 133 responses were cache-served), and a
Lovdata archive rebuild that changes member digests.

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

## j. The 52-act study: results and GO / NO-GO

The study is complete and merged (lovspor PR #768, merge commit `4288bbee`):
[`docs/research/study-52-2026-11-02.md`](../research/study-52-2026-11-02.md), with
derived results in `docs/research/data/study-52-2026-11-02.json` (`release_state:
research_only`). Facts below are read from that file's `summary`.

| Measure | Result |
|---|---|
| Lovdata law identity found (`law_identity`) | **52/52** `attested` |
| Header citation | 51 Lovvedtak, 1 Besl. O. |
| Label-free chain (`chain_status_label_free`) | 51 `attested`, 1 `unsupported` (LOV-2009-06-19-107, Besl. O. 87, 2008-2009) |
| Label-free discovery recall | **51/51** match the #617 label (denominator: the 51 Lovvedtak acts; the Besl. O. act is `not_measured`) |
| Conditional verification | **51/51** `attested` |
| Cases whose sak session precedes the decision session (S−1) | 10 |
| Cases carrying several typed Lovvedtak | 5 |
| Candidates per decision | 1 for 50 decisions, 3 for 1 |
| Header decision vs label | 52/52 agree |
| Failures (HTTP, parse, `incomplete`, `unlisted`, `contradicted`) | none observed: 133/133 Stortinget requests answered 200, all 16 sak listings and 12 publication lists parsed |
| Discovery pass vs verification pass | 51/51 saker `identical` parsed identity; pilot vs study: 2 `drift_in_bytes_only`, 3 `identical` |

Scope of the claim: the 52 acts are the #617 frame, a deduplicated regression set
skewed to 2025–2026 (34 of 52; 3 predate 2013). The result supports the method on
this frame. It is **not** a coverage estimate for amending acts in general, for acts
whose Lovtidend header cites no decision (most pre-2013 acts), or for regulations.
15 of the 51 attested chains depend on `lovtidend-avd1-2026.tar.bz2` (item a:
rights VERIFIED at endpoint level only).

**GO / NO-GO applied to the result (proposed criteria, as drafted before the run).**
- Zero `attested` edges disagreeing with a label: met (51 match, 0 disagree).
- A reason on every non-attested outcome: met (the one `unsupported` record carries
  "header cites Besl. O.; the reader types Lovvedtak only").
- Reproducible parsed identities: met within the run (51/51 `identical`), with the
  study's own caveat that 56 of 133 Stortinget responses came from the server
  cache, so this does not prove the source stable.
- No free-text attestation: met. `henvisning` only proposes candidates; each edge is
  attested by a typed `publikasjon_referanse` naming the decision by link text and
  `eksport_id`, plus the decision's presence in the session's Lovvedtak list.
- Rights: the 15 records from the 2026 archive rest on the endpoint-level NLOD 2.0
  statement (item a); their release eligibility is the owner's call under item k.

Reading: **GO for the method as a research pipeline**, and GO to build an offline
`release_candidate` for the measured scope once the owner decisions below are taken.
**NO-GO for publication**: every record stays `research_only` until the owner
approves the release policy (item k) and the rights items it depends on are
VERIFIED. Next measurement, recommended by the study: a declared harder sample
weighted to 2007–2015, including acts whose headers cite nothing.

**Design requirements the study was built to (kept for the record).**
- Input is the 52 amending-law ids of the pinned ledger only. The manual columns
  (`sak_id`, `session_id`, `decision_number`, `proceeding_kind`, URLs) are evaluation
  labels and are never read by the pipeline.
- Both archives are pinned (`-2001-2025` and `-2026`), and 2026 rows are reported as
  their own stratum (item a).
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

**GO** (to draft a `release_candidate` for the measured scope) required all of:
zero `attested` edges that disagree with a label, unless the disagreement is an
already documented source disagreement recorded as data; every non-attested outcome
carries a `reason_code`; a second run reproduces every parsed identity; no `henvisning`,
title or date was used to attest an edge. Recall has no pass mark: it is reported
with its denominator and decides scope, not GO.

**NO-GO** if any of: an unexplained wrong `attested` edge; non-reproducible parsed
identities; an attestation that relied on free text; the rights items in (a) remain
BLOCKED for records that would be released (those records stay `research_only`
regardless). *(Criteria kept as drafted before the run. Since then the 2026 archive's
rights were found stated at endpoint level, item a; which records may be released is
item k.)*

**Alternatives considered.** A single headline "coverage" number (mixes discovery with
verification); fixed recall thresholds (no baseline exists to set them).

## k. Release eligibility and attribution

**Recommendation: a record may leave `research_only` only if every source its
attested edges rest on has VERIFIED rights. Anything else stays `research_only`.**

| Source | Rights status (2026-11-02) | Eligible for `release_candidate` |
|---|---|---|
| Lovtidend avd. I 2001–2025 archive | VERIFIED, `stated_for_file` | yes |
| Lovtidend avd. I 2026 archive | VERIFIED, `stated_for_endpoint` | yes, **if the owner accepts the endpoint-level statement**; otherwise those records (15 of 51 in the study) stay `research_only` |
| Any archive with `inferred_same_dataset` | INFERRED | no (enforced by the draft schema) |
| data.stortinget.no `eksport/*` | VERIFIED (current NLOD, "Stortinget skal oppgis som kilde") | yes, with Stortinget attribution |
| regjeringen.no notices (correction evidence) | INFERRED (åndsverkloven § 14, per document type); attribution BLOCKED | no, until the owner rules; so no `corrective_resanction` can be released |
| lovdata.no HTML | forbidden source | never |

The rule is applied mechanically by the release-candidate gate (item m, P4): it reads
each record's archive pins and evidence sources and forces `research_only` on any
record that touches a source not marked eligible. It never upgrades a record.
A future year's archive (for example `lovtidend-avd1-2027.tar.bz2`) is eligible only
after its own licence basis is recorded; it never inherits one.

**Attribution block (proposed; the release manifest and the dataset README carry it):**

> Inneholder data under Norsk lisens for offentlige data (NLOD) tilgjengeliggjort av
> Stiftelsen Lovdata (https://api.lovdata.no/v1/publicData/) og Stortinget
> (https://data.stortinget.no/). Lisens: https://data.norge.no/nlod/no/2.0.
> Dataene er bearbeidet av Lovspor: koblingene er utledet fra kildene og gjengir ikke
> kildetekstene.
>
> Contains data under the Norwegian Licence for Open Government Data (NLOD) made
> available by Stiftelsen Lovdata and Stortinget. Changed by Lovspor: the links are
> derived from the sources and do not reproduce their texts.

This is the NLOD §5 default sentence plus the change notice §5 requires. It stays
provisional until the owner confirms that no lovdata.no page specifies other wording
(BLOCKED: not readable under this project's rules; ask `api@lovdata.no`).
Stortinget's terms name no NLOD version; the block links 2.0, the version current on
2026-11-02, and the manifest records the retrieval date of the terms page.

**Alternatives considered.** Releasing everything under the dataset-level licence
statement (would let an unverified future archive through by inheritance). Keeping
the whole dataset `research_only` until every source is verified (blocks the 36
records whose rights are stated for the file).

## l. Review of the v0.2 draft schema (2026-11-02)

An independent review ran the draft against the pilot record and a set of targeted mutations of it.
It found the earlier draft accepted invalid records. The draft in this PR fixes them;
`tests/unit/test_legislative_crosswalk_v0_2_proposal.py` has 72 cases, 20 of which
fail on the earlier draft.

| Criterion | Finding in the earlier draft | Fix in this draft |
|---|---|---|
| Typed identities, statuses | Per-edge `attested` was an unchecked claim; top-level `status` could contradict the edges; `reviewed_web_only` survived without its v0 rule; `reason_code: other`. | Each attested edge requires its identity and its own raw evidence; `status` is attested exactly when all three edges are; `reviewed_web_only` and `other` removed; `law_identity` is an edge. |
| Evidence and pins | `date-time` unchecked (`jsonschema` skips it without an optional package): `retrieved_at: "not-a-timestamp"` passed. Raw evidence could come from lovdata.no HTML. Archive URL was a prefix. Parser map allowed 2 of the study's 4 parsers. | `utc_timestamp` pattern; raw evidence only from `api.lovdata.no/v1/publicData/` or `data.stortinget.no/eksport/`; exact archive URL; typed `upstream_id` and `parser_version`; parser pinned per evidence item. |
| Deterministic serialization | Not specified. | Specified below; enforced by the producer (P3). |
| Append-only | `superseded` meant editing the earlier revision; `supersedes_revision` could point anywhere; withdrawal at revision 1 passed. | `record_state` is `active` or `withdrawn`; supersession derived; withdrawal only at revision ≥ 2 and never attested. |
| #618 | `initial_sanction` asserted without a source; duplicates passed. | `initial_sanction` removed; `uniqueItems`; a two-event, one-decision record validates with no inferred role. |
| No inferred correction / `valid_at` | `valid_at` refused, untested. Correction evidence could be a Stortinget page. | Tested; correction evidence only from regjeringen.no or publicData. |
| Politik boundary | Fails closed, as intended. | Unchanged; see item c. |

**Canonical serialization (proposed for P3).** One JSON object per line (JSONL),
UTF-8, `json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`,
`\n` line ends. Records sorted by `(amending_lov_id, record_revision)`; `evidence` by
`(role, upstream_id, source_url)`; `sanction_events` by `law_id`; `lovdata_archives`
by `source_url`. Integers are emitted as integers (`89016`, never `89016.0`). No wall
clock enters a record except the capture times of its evidence.

**Producer invariants (the semantic contract; JSON Schema cannot express them).**
1. `amending_lov_id` appears in `sanction_events` when the law → decision edge is attested.
2. No two sanction events share a `law_id`; a `corrects_law_id` names another event in the same record.
3. A raw `decision_case` URL names the record's `sak_id`.
4. Every archive named in an `upstream_id` is pinned in `source_pins.lovdata_archives`.
5. One edge never mixes parser versions.
6. Revisions of a law are 1..n without gaps; `(amending_lov_id, record_revision)` is unique.
7. A release contains every revision of the previous release, byte-identical.

Items 1–3 are the checks Politik's v0 reader already makes; keeping them in the
producer means no consumer has to discover their absence.

**Open, for the owner.** The study makes the session's Lovvedtak publication list
mandatory for an attested decision → case edge (study policy "mandatory"). The draft
adds the `decision_publication` role but does not yet require it, because the pilot
record that anchors the tests has no such capture. Requiring it is recommended for
`1.0.0-rc` (P1), with a fresh capture as the test anchor.

## m. Producer implementation: proposed PR breakdown (not started)

Nothing below is implemented by this PR. Each PR is gated on the decisions named in
its row; none publishes, none touches `lovverk`, Politik runtime or MCP. Each runs the
normal pipeline (fast-ci, Test matrix, codex-tests, mutation).

| # | PR | Content | Gated on |
|---|---|---|---|
| P0 | `docs/decisions.md` §2/§3 notes | The appended notes of item i, and the roadmap D-DIRECT-4 annotation. Text only. | i |
| P1 | Contract `1.0.0-rc` | Promote the v0.2 draft to `legislative-crosswalk-1.0.0-rc.schema.json` with the item l corrections; a strict validator in `src/lovspor/` (Pydantic model + JSON Schema check) and the negative cases of item l. v0 stays as is. | c, d, e, f, g, h |
| P2 | Capture | Move the study's network capture into `src/lovspor/` as `lovspor crosswalk capture`: write-once operator cache, capture manifest (archive pins, per-response digest, `retrieved_at`, HTTP status, parse error). Network only here. | a, b, d |
| P3 | Offline deterministic build | `lovspor crosswalk build --cache <dir>`: no network; reads only the capture manifest and cache; writes one JSONL file in canonical form (sorted keys, `,`/`:` separators, UTF-8, records sorted by `amending_lov_id`, `record_revision`). Test: two builds from the same cache are byte-identical; a build with the network disabled succeeds. | P1, P2 |
| P4 | Validation and release-candidate gate | `lovspor crosswalk check`: schema validation of every record; append-only check against the previous release (no revision removed or rewritten, every change a new revision); rights eligibility (item k): any record with an unverified source is forced to `research_only`; a rebuild reproduces the parsed-identity digests. Exit non-zero on any failure. | P3, k |
| P5 | Manifest | Release manifest: schema version, record counts by status and `release_state`, source pins, producer commit, Lovverk commit, SHA-256 of each file, attribution block (item k). Written by the tool, never by hand. | P4, c |
| P6 | Dataset repository | After the owner creates the dataset repository (item c): a dry-run export into a local checkout. Publishing (push, tag) stays a manual owner step. | owner GO on a release |
| P7 | Politik reader `1.0` | In Politik, after a release exists: a reader that pins the dataset tag and schema major version, keeping the v0 research reader. | P6 |

**Alternatives considered.** One large producer PR (unreviewable, and mixes network
capture with the offline build that must be deterministic). Building in the dataset
repository (moves engine code out of Lovspor, against ADR-0017 "Lovspor produces").

## Owner decision checklist

Each line: what is recommended, why in one clause, and what it unlocks. Ticking a
line approves the recommendation; anything else is a different decision. Nothing
here is approved by this PR.

**Form**
- [ ] **F1. Form of the ruling:** Amendment 1 appended to ADR-0017 (house pattern;
      the rulings fill items ADR-0017 left open and change none of its rules). The
      alternative is a new ADR. Unlocks: recording the rulings below.

**Decisions that permit implementation (P0–P5 in item m)**
- [ ] **D1 (a). Source:** Lovtidend avd. I header via publicData is the law → decision
      evidence, never ingested; it is the only measured machine source. Unlocks P2.
- [ ] **D2 (b). Retention and replay:** Stortinget raw bodies only as named regression
      fixtures; study and production captures in the gitignored write-once operator
      cache; Lovdata pins, never bytes; replay re-downloads and compares parsed
      identities. Unlocks P2.
- [ ] **D3 (c). Placement:** a separately versioned dataset repository outside
      `lovverk`, with its own manifest and tags (keeps `lovverk`'s licence classes and
      history clean, lets Politik pin the dataset independently). The `lovverk`
      namespace alternative, matching ADR-0018, is weighed in item c. Unlocks P5, P6.
- [ ] **D4 (c). Versioning:** semver `schema_version`, `0.x` research, `1.0.0` the
      first approved release; `release_state` `research_only` → `release_candidate` →
      `released`; the v0.2 draft (item l) as the basis of `1.0.0-rc`; v0 unchanged
      while Politik reads it. Unlocks P1.
- [ ] **D5 (d). Pins:** Lovverk commit, producer commit, each archive pin, per-evidence
      upstream id, body digest, parsed-identity digest, UTC time and parser version.
      Unlocks P1, P2.
- [ ] **D6 (e). Revisions:** append-only, supersession derived, withdrawal as a later
      non-attested revision, release must contain the previous release byte-identical.
      Unlocks P1, P4.
- [ ] **D7 (f). #618 and corrections:** several sanction events per decision;
      `unclassified_sanction` by default; `corrective_resanction` only with correction
      evidence from a source that states it; no `initial_sanction`; no event implies
      `valid_at`. Unlocks P1.
- [ ] **D8 (g). Identity:** parsed identity for comparison, raw digest for provenance,
      field lists fixed per parser version. Unlocks P1, P3.
- [ ] **D9 (h). Besl. O.:** `unsupported` with `decision_kind_not_supported` until a
      measured reader extension; acts with no header citation `unresolved`. Unlocks P1.
- [ ] **D10 (i). Gate 5:** the appended §2/§3 notes and the D-DIRECT-4 annotation.
      Unlocks P0.
- [ ] **D11 (l). Producer invariants and canonical form** as listed in item l, and
      whether `decision_publication` evidence becomes mandatory in `1.0.0-rc`
      (recommended: yes). Unlocks P1, P3.

**Decisions that permit a release candidate (P4) — none permits publication**
- [ ] **R1 (k). Eligibility rule:** only records whose every source has VERIFIED rights
      leave `research_only`; the gate never upgrades a record.
- [ ] **R2 (a, k). 2026 archive:** accept the endpoint-level NLOD 2.0 statement
      (`stated_for_endpoint`) as VERIFIED for release. If not, the 15 records from the
      2026 archive stay `research_only`, or ask Lovdata to name the file.
- [ ] **R3 (k). Attribution block** as drafted, after checking whether lovdata.no
      specifies other wording (BLOCKED for this project: lovdata.no HTML is not read;
      owner reads it or asks `api@lovdata.no`).
- [ ] **R4 (f, k). regjeringen.no:** whether sanction/correction notices fall under
      åndsverkloven § 14 and what attribution to give. Until decided: no
      `corrective_resanction` is released and no such notice is captured automatically.

**Still blocked after all of the above**
- Exact Lovdata attribution wording (R3) and regjeringen.no basis (R4): owner reading
  or a question to the source.
- Coverage outside the #617 frame: the study measured 52 acts skewed to 2025–2026;
  the next sample (2007–2015, acts with no header citation) is needed before any
  coverage statement.
- Publication itself: creating the dataset repository, tagging and pushing a release,
  and any Politik reader change are separate owner GOs (P6, P7).
