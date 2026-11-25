# Forarbeider full corpus: architecture reset (PROPOSED)

**Status:** PROPOSED. Nothing here is Accepted, approved or authorized. It is a proposal for **ADR-0018 Amendment 2**, or a successor ADR if the owner prefers. It authorizes no fetch, signature, release or code change.
**Date:** 2026-11-12
**Tracking:** [#738](https://github.com/bartoszkobylinski/lovspor/issues/738)
**Builds on:** [ADR-0018](https://github.com/bartoszkobylinski/lovspor-notebook/blob/main/docs/adr/ADR-0018-government-forarbeider-corpus.md), [the PR3B contract and its Amendment 1](forarbeider-pr3b-release-contract.md), ADR-0017 and ADR-0019 (the crosswalk boundary), and the shipped slices A1, A2a, A2b, B1a (#875), B1b (#881) and B2 (#884).
**Owner decides; reviewer reviews; nothing here is legal advice.**

Each claim carries a tag: **[O]** observed in code, docs or Pilot-0 evidence; **[A]** assumption or inference; **[L]** needs legal review; **[D]** owner decision.

## 0. Why reset

The goal is a **comprehensive, historical, versioned corpus of Norwegian legislative preparatory works, with complete document texts**, provenance, and links to laws, amendments and parliamentary history. A1–B2 were built as a selective, curated-excerpt release path [O]:

- a chapter-level allowlist;
- whole-chapter withholding on any unsupported structure;
- `partial_curated_excerpt` coverage as the normal outcome;
- per-document confirmation phrases;
- a batch limit of 25.

Pilot-0 on Prop. 31 L (2025–2026) shows what that costs [O, owner-private Pilot-0 report, 2026-11-12]:

- 4 of 9 chapters were withheld, all of them consultation chapters;
- automatic clearance is unreachable today, because A2a requires a `terms_url` that regjeringen.no does not have for text;
- even with a terms page, the CSP nonce in every served page breaks the raw-digest comparison.

This proposal keeps every mandatory constraint: legal compliance, privacy, provenance, reliable withdrawal, and no automatic publication of unresolved material. It changes what the pipeline optimizes for: **completeness, with explicit, measured gaps**.

## 1. Architecture audit: what blocks a full corpus

Each barrier below is either a **legal** constraint (it stays, possibly reshaped) or a **code** constraint (our own choice, which can change).

| # | Barrier (where) | Kind | What it really protects | Proposal |
|---|---|---|---|---|
| B1 | Whole chapter withheld on `unsupported_structure`, e.g. adjacent `blockquote` (`chapter_body.py:_open`) [O] | **code** | rendering fidelity: two quote groups must not merge into one | Span-level blocks with explicit group boundaries; a chapter is complete when every span is represented (§4.3) |
| B2 | Any `quotation` makes a chapter `flagged` and routes it to per-chapter review (`batch_classify`) [O] | **legal + code** | åndsverkloven § 14 second paragraph: a work not made for the document and quoted in it keeps its own protection [O, statute] | Classify the quoted **span**, not the chapter; decide by source policy (§3.4) |
| B3 | Annex, table, image, figure and footnote withhold the whole chapter (`WITHHELD`) [O] | **code**, partly **legal** | tables and footnotes are usually government text [A]; images and annexes can be third-party works [O, § 14 second paragraph] | Tables and footnotes become supported structures; images and annexes become separate attachment records with their own rights state (§4.4) |
| B4 | `hearing_participants` fires on the bare substring `høring` (`chapter_body.py:62`) [O] | **code** | personal data of consultation participants | Pattern-specific detectors at span level, measured precision (§3.5) |
| B5 | `sensitive_data` fires on `biometrisk` [O, Pilot-0: the actuarial "biometrisk risiko"] | **code** | special-category data | Same as B4 |
| B6 | A2a requires a `terms_page` evidence file and a `terms_url` (`PolicyEvidenceKind`, `policy_source.py:100-104`) [O] | **code** (the law requires none) | that the owner read the site's terms | `terms_basis: page | none_identified` (§3.2) |
| B7 | Terms bytes compared raw, "no normalisation" (Amendment 1 point 8) [O] | **code** | detects a changed terms page | Nonce-normalized comparison with the raw digest kept (§3.3); robots.txt stays raw |
| B8 | Standing policy scoped to `prop-l` and `prop-ls` [O] | **code** + **[D]** | rights assessed per source and type | Source-level policy per document family (§3.1) |
| B9 | Per-batch typed phrase; batch of at most 25 documents (`MAX_DOCUMENTS`) [O] | **code** | owner act per release; bounded blast radius | Keep one owner act per release, sized by a release manifest rather than a document count (§5.3) |
| B10 | Manifest coverage is `partial_curated_excerpt` or `every_listed_chapter` [O] | **code** | never claim completeness wrongly | Document-level `completeness` record with explicit gaps (§2.3) |
| B11 | A document can be released once only; no re-release after withdrawal (`release_conflict`) [O] | **code** | no silent overwrite | Versioned documents: a new version is a new record, history kept (§2.2) |
| B12 | Fetch is an explicit list only; `--max` 200 per run [O] | **legal-adjacent** + **[D]** | Amendment 1 point 6, "not harvesting" | Discovery from official catalogues with a recorded inventory and owner-approved scope; requests stay polite and robots-bound (§4.1) |
| B13 | Private, owner-only staging; raw pages never in Git [O] | **legal** + good practice | rights in the raw HTML; privacy | Unchanged; a content-addressed cache with retention (§4.2) |
| B14 | `forarbeider/` lives inside the lovverk repo (Amendment 1 point 5) [O] | **code** + **[D]** | isolation from law readers | Separate repository, or LFS-free sharding, sized in §5.4 |
| B15 | Receipts, journal and reader keyed to whole-document renderings (`batch_records`, `release_reader`) [O] | **code** | integrity | Extend them to versioned documents and attachments; the transaction stays (§5.1) |
| B16 | Withdrawal removes a whole document file (B1b) [O] | **code** | takedown | Span- and document-level withdrawal with a tombstone (§3.6) |
| B17 | Git history is permanent [O] | **fact** | — | Unchanged; that is why gating comes before Git (§3.6) |
| B18 | The crosswalk (forarbeid → act) follows ADR-0017 evidence rules; votes stay in Politik (ADR-0019) [O] | **policy** | parliamentary data boundary | Unchanged; links are produced as crosswalk records, never as text inference (§2.5) |

**Genuine legal constraints** [L]:

- § 14 covers official proposals, reports and statements "given by public authority" [O, statute], but not quoted third-party works, annexes not made for the document, poetry, music or artistic works [O, § 14 second paragraph].
- § 24 database right, with § 14 applied "tilsvarende" [O]; its effect on regjeringen.no as a collection is unresolved [L].
- § 29 quotation right: "i samsvar med god skikk og i den utstrekning formålet betinger" [O]. Whether it covers republishing a whole proposition, quotes included, is unresolved [L].
- personopplysningsloven: a processing basis for any personal data republished [L].
- robots.txt and politeness govern access, never reuse [O, Amendment 1 point 8].

Everything else in the table is our own choice.

## 2. Target architecture

### 2.1 Document-first model

The unit is a **document version**, not a chapter excerpt.

- **Document** (stable identity): `family` (`prop-l`, `prop-ls`, `prop-s`, `ot-prp`, `nou`, `innst`, …), `source` (`regjeringen.no`, `stortinget.no`), the source's immutable numeric id, session, number, title, ministry or committee.
- **Version**: one observed, content-addressed state of the whole document, with the ordered list of its parts and attachments and each part's text digest. A changed source (a correction, an errata, a reissued PDF) is a new version; old versions stay.
- **Part**: a chapter or section, as an ordered list of **spans** (heading, paragraph, list item, quote, table, footnote). Each span carries its own text digest and **rights class** (§3.4) and its own **privacy state** (§3.5).
- **Attachment**: an annex, figure, image or PDF, with a digest, media type, rights state and a reason when absent.
- **Gap**: an explicit record of anything expected but not present: a chapter not fetched, an annex not supported, a span withheld for rights or privacy, an extraction failure. A gap has a code and a scope (document, part or span). It does **not** record the version it belongs to: the version manifest contains the gap, never the reverse (§2.2).

Today's `ForarbeidRecord`, `ChapterResult` and `TextBlock` map onto Document, Part and Span; the migration is in §5 [O].

### 2.2 Versioning and identity

- Identity is `<source>:<family>:<numeric id>` (today: `regjeringen-<id>`) [O]. It never changes across versions.
- **Version identity is content-only and acyclic.** `version_id = SHA-256(canonical version content)`, where the canonical content is, in a fixed order:
  - the document identity;
  - the raw SHA-256 of every source response the version was extracted from (§4.2);
  - the extractor and normalizer versions;
  - the ordered parts, each with its spans' text digests, rights class and privacy state;
  - the attachments by raw digest;
  - the gaps by `(code, scope locator)`.

  Nothing inside the hashed content may name a version id: no `since_version`, no `previous_version` and no release reference.
- **Lineage lives outside the hash.** A separate, append-only *version history* record per document lists the version ids in observed order, each with its retrieval time and the gaps that first appeared in it (a gap's "since" is derived by comparing consecutive versions, never stored inside one). Release, withdrawal and attestation records reference version ids; none is referenced by them.
- Determinism: the same raw responses and the same extractor versions give the same `version_id`. A rerun that changes nothing adds no version.
- Release history is the sequence of released versions per document, plus withdrawals. Git is the recording time, never the legal or publication time [O, today's time axes].

### 2.3 Two completeness axes, document by document

Completeness has **two independent axes**. Both are computed from records and never typed by a person. They are never merged into one flag.

**Source completeness** (ingestion and extraction): did we capture and faithfully extract everything the source publishes?

| Field | Meaning |
|---|---|
| `parts_expected` | from the source's own table of contents (chapter navigation, PDF bookmarks) |
| `parts_extracted` | parts whose every span was extracted |
| `attachments_expected`, `attachments_captured` | annexes and figures captured in the raw cache |
| `extraction_gaps` | gaps with an extraction code (`not_fetched`, `unsupported_structure`, `parse_failed`) |
| `extraction_fidelity` | measured against the source (§2.4) |
| `source_state` | `source_complete` only when parts and attachments are all extracted or captured and `extraction_gaps` is empty; otherwise `source_incomplete` |

**Publication completeness** (release eligibility): of what we extracted, how much may be published?

| Field | Meaning |
|---|---|
| `spans_publishable` | spans whose rights class is cleared by the signed policy and whose privacy state is clear |
| `release_gaps` | gaps with a release code (`rights_unresolved`, `privacy_unresolved`, `third_party_work`, `withdrawn`) |
| `attachments_publishable` | captured attachments whose rights class is cleared |
| `publication_state` | `publication_complete` only when `source_state` is `source_complete` **and** `release_gaps` is empty; otherwise `publication_incomplete` |

**Invariants.**

1. `publication_complete` implies `source_complete`; the converse never holds by default.
2. A document can be `source_complete` and `publication_incomplete`: everything extracted, with third-party consultation quotes unresolved. That is a normal, reportable state, not an error.
3. No reader, index, MCP tool or rendering may present a `publication_incomplete` version as complete. Every rendering states `publication_state` and lists its release gaps; the NOT-enacted-law label always appears [O, today's labels carry over].
4. Release gaps are never filled by shortening the expectation: `parts_expected` and `attachments_expected` come only from the source.

### 2.4 Corpus-level metrics, per axis

Published per release and per family. Each metric is computed from records, never estimated. A metric whose input is missing is reported as `unknown`, never as 0.

| Axis | Metric | Definition |
|---|---|---|
| source | `documents_discovered` | documents in the source's official catalogue for the scope (§4.1) |
| source | `documents_ingested` | at least one version staged |
| source | `documents_source_complete` | the latest version is `source_complete` |
| source | `parts_expected` / `parts_extracted` | summed over documents |
| source | `extraction_fidelity` | share of source text tokens present in spans, plus span-verbatim precision (Pilot-0 measured 100% and 100% on 5 clean chapters [O]) |
| source | `attachments_missing` | expected minus captured |
| source | `source_coverage` | sessions × families covered, with the oldest session reached |
| publication | `documents_released` | at least one version released |
| publication | `documents_publication_complete` | the latest released version is `publication_complete` |
| publication | `spans_rights_unresolved`, `spans_privacy_unresolved` | open release gaps by class |
| publication | `attachments_unpublishable` | captured but not cleared |

### 2.5 Links to laws and parliamentary history

- **Forarbeid → act:** crosswalk records (ADR-0017), from a structured source such as Stortinget's `sak` linkage or Lovtidend's commencement data, each with evidence. Never inferred from text similarity [O, ADR-0017 rules].
- **Votes and debates** stay in Politik (ADR-0019) [O]. The forarbeider corpus carries only the crosswalk keys (session, decision kind, number, `sak_id`).
- **Lovverk** keeps law text. Forarbeider stays a separate, opt-in dataset; no law reader changes [O].

### 2.6 Data flow

```mermaid
flowchart LR
  subgraph Discover["Discover (owner-approved scope)"]
    CAT[Official catalogue pages / APIs] --> INV[Inventory record\nper family + session]
  end
  subgraph Fetch["Fetch (private staging)"]
    INV --> F[Polite fetcher\nrobots, spacing, UA] --> CAS[(Raw cache keyed by\nraw SHA-256, owner-only)]
    CAS --> NIDX[Normalized-digest index\ncomparison only]
    F --> PROOF[Fetch-run record\nraw + normalized digests]
  end
  subgraph Extract["Extract (offline, deterministic)"]
    CAS --> X[Document parser\nparts, spans, attachments] --> VER[Version manifest\n+ completeness + gaps]
  end
  subgraph Classify["Classify (offline)"]
    VER --> R[Rights classes\nper span/attachment]
    VER --> P[Privacy detectors\nper span]
  end
  subgraph Clear["Clear (policy, exceptions)"]
    R --> POL{Source policy\nsigned by owner}
    P --> POL
    POL -- clears --> ELIG[Eligible spans]
    POL -- exception --> REV[Exception review\nper span/attachment]
    REV --> ELIG
    POL -- unresolved --> GAP[Explicit gap]
  end
  subgraph Release["Release (automatic under an owner-signed policy, §9)"]
    ELIG --> PLAN[Release manifest\nversions + gaps]
    GAP --> PLAN
    PLAN --> TXN[B1b transaction\nlock, journal, read-back] --> REPO[(forarbeider repo\nworking tree)]
    REPO --> MERGE[Commit + push to main\n= publication (§9)]
  end
  subgraph Read["Read"]
    MERGE --> ATT[Bot-signed publication\nattestation, §9.3] --> RDR[B2 reader / later MCP, search]
  end
  PROOF --> POL
```

## 3. Rights and privacy (PROPOSED)

### 3.1 Source-level authorization

One owner-signed policy per **source × family** (for example regjeringen.no × {`prop-l`, `prop-ls`, `nou`}), not per document. It binds:

- the rights assessment for that family (§ 14 coverage, the § 24 position for the method and volume, the § 29 position on quotations) [L];
- the personal-data assessment, its processing basis and the residual-risk acceptance [L];
- purpose, takedown contact, retention, deindexing and Git permanence (unchanged from Amendment 1);
- the **span rights classes** this policy clears (§3.4) and the ones it never clears;
- `terms_basis` (§3.2).

**Signature:** today's typed phrase plus the crosswalk's real anchor. The policy is signed with `ssh-keygen -Y sign`, and the signer must appear in a repository-pinned `allowed_signers` file the owner merged, as `src/lovspor/crosswalk/approval-signers` already works [O, crosswalk #840]. That gives authenticated identity where today the record says `identity_authenticated: false`. Under §9 (D11), the owner key signs policies, exceptions, withdrawals and stop/resume records only. Release manifests and publication attestations are signed by a separate **bot key**, never by the owner key [D].

**SSH signing trust boundary (PROPOSED).**

- **What a signature proves:** the signed bytes were signed by a private key whose public key is listed, for this namespace, in `allowed_signers` at the trusted commit. It proves nothing about who held the key, or about whether the owner understood what they signed.
- **Trust root:** the `allowed_signers` file in the engine repository, changed only by an owner-authored, owner-merged PR, pinned by SHA-256 in a test (as `test_crosswalk_shipped_signers.py` pins the crosswalk file [O]). Verification reads the file from the engine's own checked-out tree, never from a path a command argument names.
- **Namespaces:** one per purpose (`lovspor-forarbeider-policy`, `lovspor-forarbeider-release`, `lovspor-forarbeider-publication`), so a signature for one purpose never verifies for another.
- **Validity window:** each entry carries OpenSSH `valid-after` and `valid-before`. Verification uses the time the record states, which must also be no later than the machine clock. A record dated outside the key's window denies.
- **Rotation:** a new key is added by an owner-merged PR *before* it is used, with an overlap; the old key's `valid-before` is then set to the cut-over moment. Records signed by the old key inside its window stay valid.
- **Revocation:** a revoked key is moved to a pinned revocation list (`revoked_keys`, used with `ssh-keygen -Y verify -r`). Every verification consults it.
- **Compromise:**
  - revoke the key, with its `valid-before` set to the last moment it is trusted;
  - list every record signed after that moment as untrusted;
  - re-verify all records and treat any signed after the cut-over as unsigned;
  - withdraw affected releases forward-only (§3.6);
  - publish an incident note.

  Records signed before the cut-over stay valid only if the owner states that the key was safe until then. Otherwise the cut-over is the key's first use.
- **Residual risk:** the engine repository and the owner's GitHub account are the trust root. Whoever can merge to the engine can change `allowed_signers`; the engine's branch protection, with only the owner merging *engine* changes, is therefore part of the boundary. This concerns the engine repository, not forarbeider releases, which §9 automates.

### 3.2 Documented absence of website terms

As of 2026-11-12, regjeringen.no publishes **no text-reuse terms**. The only reuse page is for images and video (id2715005, updated 27.02.2026) [O, owner-private terms dossier, 2026-11-12].

- `terms_basis: none_identified` requires a signed `terms_search_record`: pages searched with URLs and read dates, the method, the finding, and an expiry no later than the policy's `valid_until` and **at most 30 days by default** (Pilot-1). A longer validity, up to 90 days, needs its own separate owner decision recorded in the policy; it is not implied by signing.
- The policy names a **terms locus** (`https://www.regjeringen.no/no/om-nettstedet/`) that every current proof reads. A changed locus denies automatic clearance (`terms_locus_changed`) until the owner searches and signs again.
- The absence of terms is **never** treated as a licence: the rights basis remains the statute, and the record says so.

### 3.3 Narrow CSP nonce normalization

Observed: two fetches 28 s apart differed only in `<meta name="csp-nonce" content="…">` and `<script nonce="…">` values [O, Pilot-0].

- **Rule:** the comparison digest is SHA-256 over the served bytes after replacing exactly two kinds of value:
  - the value of every `nonce="…"` attribute;
  - the `content` value of the one `<meta name="csp-nonce">` element.

  Each is replaced with the fixed token `NONCE`, by a byte-level grammar (ASCII attribute syntax, base64 alphabet plus `&#x2B;` and `=`). Nothing else is touched.
- The raw digest and the raw bytes are still recorded and stored, and the cache is keyed by the raw digest (§4.2). The normalized digest is a comparison value only. Readers see both digests.
- **Fail-closed:** a byte that differs anywhere else, or a nonce outside that grammar, means the normalized digests differ, and the evaluation denies.
- **Scope:** it applies to HTML evidence pages (a terms page or the locus) and to the source pages' `source_sha256` equality across runs. It never applies to robots.txt, which is compared raw.

### 3.4 Span-level rights classes

Each span or attachment gets exactly one class, assigned deterministically from structure and markup, never from free-text guessing:

| Class | Examples | Default |
|---|---|---|
| `official_text` | government prose, headings, the ministry's tables and footnotes | cleared by the source policy (§ 14 first paragraph) [L] |
| `statute_text` | quoted law and regulation text, draft law text (lovforslag) | cleared (§ 14 first paragraph: laws and regulations) [L] |
| `public_statement` | statements by public bodies quoted in a consultation (municipalities, directorates) | cleared if the policy says so (§ 14 first paragraph "uttalelser … avgitt av offentlig myndighet") [L] [D] |
| `third_party_quote` | consultation statements by private organisations or persons | **not** cleared by default. Either the policy clears it under § 29 at document scale [L] [D], or it stays an explicit gap |
| `third_party_work` | images, figures and annexes not made for the document | never cleared by the policy; an exception review, or a gap |
| `unknown` | anything not classified | gap; never cleared |

Detecting quote boundaries is reliable (`blockquote` is structural [O]). Detecting the *speaker* (public body vs private) is not reliable from markup alone [A]. Until a reliable signal exists, every quote is `third_party_quote`, and the policy decides the class as a whole.

### 3.5 Privacy

- Detectors run per span and are pattern-specific: e-mail, phone, national id, a named natural person in a consultation list, special-category terms with domain exemptions (for example `biometrisk risiko`). Each carries a measured precision on a labelled synthetic and real sample [A: to be built].
- A span with a privacy hit is `privacy_unresolved` (a gap) until an exception review clears it. It is never automatically published.
- Detector silence is not proof [O, Amendment 1]: the policy's residual-risk acceptance still applies.

### 3.6 Takedown, withdrawal and history

- Withdrawal targets a **version**, a **part**, a **span** or an **attachment**. It writes a forward-only change: the target is removed and a tombstone with a reason code is recorded in the version manifest.
- Readers never serve tombstoned content [O, B2 behaviour carried over]. Git history, forks and caches keep earlier bytes [O]. Gating therefore stays **before** Git: nothing unresolved is ever committed.

## 4. Ingestion at scale

### 4.1 Discovery [D]

- Inventory per family and session, from official catalogue pages or APIs. The inventory record lists each document's URL and id, with its own fetch-run proof.
- Fetching stays robots-bound and spaced, with one identifying User-Agent [O, A1 rules]. Discovery replaces the owner-typed list with an **owner-approved scope** (family × sessions), recorded and signed.
- This changes Amendment 1 point 6 ("not harvesting"). It is a policy decision **[D]**, not a code detail.

### 4.2 Resumable, deduplicated, deterministic

- **Raw cache:** keyed by the **raw SHA-256** of each response body, exactly as served, owner-only, outside Git. Every response is kept; two fetches of the same URL are two entries when their bytes differ (each has its own nonce). The raw bytes and the per-response identity (URL, retrieval time, status, raw digest) are never discarded or rewritten.
- **Normalized index:** a separate, derived index `normalized_sha256 → [raw_sha256, …]`, built by the §3.3 rule and recorded with the rule's version. It is used only to *compare*: is this response the same page as that one, nonce aside? It never names a stored object, never replaces a raw digest in a version, and never serves bytes.
- **Provenance:** every version, proof and comparison names the raw digests it used. A comparison result also records the normalized digests and the rule version that produced them, so it can be recomputed from the raw bytes.
- **Runs** are resumable: a run record lists done, pending and failed URLs; a restart skips done ones.
- **Extraction** is a pure function of (raw bytes, extractor version). Same inputs, same version manifest [O, today's determinism carries over].

### 4.3 Span-level extraction (replaces whole-chapter withholding)

- Adjacent quote groups become separate `quote` spans with an explicit group boundary; the renderer emits a separator so they never merge. This removes B1.
- Tables become `table` spans with a defined Markdown rendering, or an attachment when the shape is unsupported (a gap, not a withheld chapter).
- Footnotes become `footnote` spans linked from their anchor.
- A structure the parser does not support becomes one `unsupported_structure` gap at span scope; the rest of the part is kept.

### 4.4 Attachments

PDF annexes and figures are stored in the raw cache with their digests. They are released only when their rights class is cleared (§3.4); otherwise they appear as gaps with a source link.

## 5. Migration (reuse first)

### 5.1 Components

| Component | Fate |
|---|---|
| A1 fetch (`regjeringen_fetch`), robots, spacing, UA, raw storage, run records | **retain**; extend with the normalized digest (§3.3) and resumable runs |
| Metadata and edition (`page.py`, `edition*`) | **retain**; extend the families |
| Chapter extractor (`chapter_body.py`) | **extend**: spans, quote groups, tables, footnotes; deprecate whole-chapter `WITHHELD` for supported structures |
| Detectors (`_CANDIDATES`) | **replace** with span-level detectors that have measured precision |
| PR3A review ledger, dossier, authorization | **retain** for exceptions only |
| A2a standing policy | **extend**: families, `terms_basis`, SSH signature, span classes |
| A2b batch plan | **extend** to release manifests over versions and gaps; the 25-document cap becomes a size bound |
| B1a checkout guard | **retain**; extend the trusted roots to a per-repository table (FC9b) |
| B1b transaction, lock, journal, recovery | **retain**; extend operations to version files and attachments |
| B1b withdrawal | **extend** to version, part, span and attachment scope |
| B2 reader and publication attestation | **retain**; extend to versions and completeness; attestation signed with SSH (§3.1) |
| Manifest v2 | **retain** read support; add **manifest v3** (versions, completeness, gaps) |
| Renderer | **extend**: span types, gap notices, completeness banner |

### 5.2 Compatibility

- Readers read v2 and v3. A v2 member is lifted to a single-version v3 document with `source_state = source_incomplete` (v2 never recorded the full extraction) and `publication_state = publication_incomplete`, with release gaps for its unreleased chapters. It is never complete [O, the v1 → v2 lifting precedent].
- Existing B1b receipts, confirmations and plans stay valid for v2 releases. New releases write v3 only.
- Existing withdrawals stay. A v2 tombstone is carried as a document-scope tombstone in v3.

### 5.3 Release sizing

The owner's act covers one **release manifest**: versions, gaps, counts and a signed digest. Its size is bounded by bytes and file count, not by a document count, so the B1b transaction stays bounded.

### 5.4 Storage and repository growth [A, an order-of-magnitude estimate to measure]

- Pilot-0: 9 chapters, about 24 KB of extracted text across the 5 clean chapters [O]. A full proposition is roughly 50–500 KB of Markdown [A].
- About 300 relevant documents a year across families [A]. Over 20 sessions that is about 6,000 documents, roughly 0.3–3 GB of Markdown before history.
- That growth argues for a **separate repository** (`lovverk-forarbeider`) rather than `lovverk/forarbeider/` **[D]**, bootstrapped before the first public release (FC9b), because content published in lovverk could never leave its history. It keeps lovverk clones small and the Lovdata law readers untouched. Shard by `family/session/`. No Git LFS; attachments that are too large stay as source links with gaps.

### 5.5 Rollback

- Every slice is behind an explicit command or flag; nothing changes existing readers until they opt in.
- A bad release is withdrawn forward-only (§3.6), never rewritten.
- A bad code slice is reverted. Its records stay readable because v2 and v3 readers coexist.

## 6. Roadmap (dependency-ordered, bounded PRs)

Priority: **one legally cleared, COMPLETE Prop. L before mass ingestion.** Source completeness (FC1–FC6) is separate from publication completeness (FC7–FC10).

**Repository first (recommended, conditional on [D] 9).** If the owner accepts a separate `lovverk-forarbeider` repository, it is bootstrapped and trusted (FC9b) **before** any first public release (FC10). Nothing is ever published into `lovverk/forarbeider/` first and migrated later: migrating published content would leave it in lovverk's permanent history. If the owner declines the separate repository, FC9b is skipped and FC10 targets `lovverk/forarbeider/` under today's B1a guard.

| PR | Scope and interfaces | Migration | Red-first acceptance tests | Fail-closed | Completeness impact |
|---|---|---|---|---|---|
| **FC0** (this PR) | This document only | none | — | — | — |
| **FC1** Normalized comparison digest | `regjeringen_fetch`: `normalized_sha256` recorded beside the raw `sha256`, with the rule version; `policy_source` compares normalized for HTML evidence; storage stays keyed by raw digest | run records get one more field (schema 3) | a nonce-only change matches; any other byte change denies; robots.txt stays raw; stored files are still named by raw digest | grammar mismatch denies | unblocks every policy comparison (B7) |
| **FC2** `terms_basis: none_identified` | `policy_models`, `policy sign`, `policy_source`: `terms_search_record`, locus URL, expiry (30 days by default, longer only with an explicit recorded owner decision, at most 90); faults `terms_locus_changed`, `terms_search_expired` | policies stay `terms_basis: page` by default | sign refuses without a record; expiry denies; a 31-day validity without the extra decision refuses; a changed locus denies | yes | unblocks clean clearance on regjeringen.no (B6) |
| **FC3** Span model and quote groups | `chapter_body`: spans with group boundaries; renderer separator | `ChapterResult` schema 2 (spans); v1 results still read | adjacent quotes give separate spans; render round-trip; no merge | unknown structure becomes a span gap | Pilot-0: the 4 withheld chapters become representable (B1) |
| **FC4** Tables and footnotes | span types; Markdown rendering; attachment fallback | none | synthetic tables and footnotes render deterministically | an unsupported shape becomes a gap | removes B3 for government tables and footnotes |
| **FC5** Span rights classes and detectors | classifier per span; detectors with domain exemptions; precision report | `PlannedChapter` gets a span classification | every class has a fixture; `biometrisk risiko` exempt; real-personal-data fixtures still fire | `unknown` is a gap | removes B4 and B5 false positives |
| **FC6** Version manifest v3, both completeness axes | Document, Version, Part, Span, Attachment, Gap; acyclic `version_id` and separate version history (§2.2); source and publication completeness computed | readers read v2 and v3; v2 lifts to source- and publication-incomplete | `publication_complete` only with `source_complete` and zero release gaps; no version id inside hashed content; a lifted v2 is never complete | missing input becomes `unknown` | measured completeness on both axes (B10) |
| **FC7** Source policy v2 and SSH signatures | families, span classes, `ssh-keygen -Y verify` against the pinned `allowed_signers` and `revoked_keys`, per-purpose namespaces, validity windows (§3.1) | v1 policies keep working until expiry | an unsigned or untrusted signer denies; a signature over other bytes denies; another namespace denies; a revoked key denies; a record dated outside the key window denies | yes | scalable authorization (B8) |
| **FC8** Release manifest over versions | plan, confirm and apply on versions and gaps; size bound | B1b receipts v2 kept | an over-size manifest refuses; gaps recorded; idempotent retry | yes | removes B9 and B11 |
| **FC9** Withdrawal v2 | version, part, span and attachment tombstones | v2 withdrawals carried over | a tombstoned span is never served; history warning | yes | precise takedown (B16) |
| **FC9b** Repository bootstrap and trusted root (prerequisite of FC10 if [D] 9 is accepted) | the owner creates `lovverk-forarbeider` (empty, the rulesets of §9.7) and records its initial commit; the B1a guard gets a per-repository trusted-root table (lovverk root, forarbeider root); release-clone commands take the target repository explicitly | B1a `TRUSTED_ROOT_COMMITS` becomes per repository; lovverk's entry unchanged | a clone of the new repo with the pinned root passes; lovverk's root is refused for forarbeider writes and vice versa; an unknown root is refused | yes | removes B14 before anything is public |
| **FC10** First complete Prop. L | an operator run on one proposition: fetch, extract, classify, owner policy (FC7), release to a disposable clone, then to the release repository **only after owner authorization** | none | see the acceptance rule below the table | any unresolved span or attachment is a release gap | first `publication_complete` document, or a documented NO-GO |
| **FC11** Discovery and resumable ingestion | inventory per family and session; resumable runs | none | a resumed run skips done URLs; inventory digests recorded | robots refusal stops | `documents_discovered`, `source_coverage` |
| **FC12** More families | NOU, Ot.prp., Prop. S, Innst. (stortinget.no; own terms and policy [D]) | none | per-family extractor fixtures | an unknown family refuses | family coverage |
| **FC13** Repository growth controls | sharding by `family/session/`, size budgets per release, clone-size monitoring in `lovverk-forarbeider` (bootstrapped in FC9b) | none: there is no lovverk `forarbeider/` content to migrate, because nothing was ever released there | an over-budget release refuses; shard paths deterministic | yes | scale |
| **FC14** Search, MCP, API | opt-in reader tools over released versions only | none | tombstones never served; completeness banner always shown | yes | access |

**FC10 acceptance rule (unambiguous).** FC10 succeeds only if one Prop. L is released whose latest version is `source_complete` **and** `publication_complete`: every expected part and attachment is extracted and cleared, and there are zero release gaps. A partial release is **not** an FC10 success, whatever its share of chapters.

If unresolved rights, privacy or extraction gaps prevent that, FC10 reports **NO-GO**, with:
- the candidate;
- both completeness records;
- every gap by code;
- what would clear each gap.

The criterion is never redefined, and choosing a different candidate is a new FC10 attempt, reported as such.

## 7. Open owner and legal decisions

1. **[D]** Adopt this direction (full corpus, explicit gaps) as ADR-0018 Amendment 2, or as a successor ADR.
2. **[L]** § 14 first paragraph coverage of each family (Prop. L/LS/S, NOU, Ot.prp., Innst.).
3. **[L]** The § 24 position of regjeringen.no and stortinget.no as databases, for discovery-scale extraction, given § 24's "§ 14 gjelder tilsvarende".
4. **[L] [D]** § 29: may third-party consultation quotes be republished as part of a complete proposition? If not, they stay gaps.
5. **[L]** Personal data in consultation lists and quoted statements: the processing basis, and whether span-level redaction suffices.
6. **[D]** `terms_basis: none_identified` with a terms locus, for regjeringen.no; terms-search validity 30 days by default, with any longer validity (at most 90 days) a separate decision.
7. **[D]** Discovery from official catalogues instead of owner-typed lists (changes Amendment 1 point 6).
8. **[D]** SSH-signed policies, release manifests and publication attestations, with the trust boundary in §3.1 (namespaces, validity windows, rotation, revocation list, compromise procedure).
9. **[D]** A separate `lovverk-forarbeider` repository instead of `lovverk/forarbeider/`. **Recommended before any first public release** (FC9b before FC10).
10. **[D]** Stortinget documents (Innst.): source terms (stortinget.no or data.stortinget.no) and their own policy.

## 8. What this proposal does not change

- No chapter text, evidence bytes or personal data in this repository, CI, issues or PRs.
- `normative: false`, `source_license: null`, the § 14 basis (never NLOD), the NOT-enacted-law label, deterministic rendering.
- Nothing publishes without an owner-signed policy that authorizes it: under §9, the owner authorizes the policy, not each release.
- Law readers, MCP law tools, search and embeddings stay unchanged until FC14, and FC14 is opt-in.
- Votes and debates stay in Politik; forarbeid → act links follow ADR-0017.

## 9. Amendment A: policy-driven automatic publication (PROPOSED)

**Status:** PROPOSED. It records the owner's direction of 2026-11-12, stated in session: *manual approval of each document, batch, PR or merge is rejected; human approval is required for source-level policy, legal scope, security trust roots and material policy changes, not routine releases.* Formal approval is pending (decision D11 in [the owner decision gate](forarbeider-owner-decisions.md)).

This amendment replaces §5.3 and the owner-act steps of §2.6 ("Release (owner act)", "Owner commit + merge = publication"). Nothing else in §§1–8 changes. Rights, privacy, provenance and integrity gates are unchanged, and **unresolved content is never published**.

### 9.1 Authorities, kept apart

| Authority | Holder | May | May never |
|---|---|---|---|
| **Policy** | the owner's SSH key (namespace `lovspor-forarbeider-policy`) | sign source/family policies, including `automated_publication`, scope, budgets and legal-assessment references; sign exception clearances; sign withdrawals; sign stop and resume records | — |
| **Trust roots** | the owner, by owner-merged changes to the engine and by repository settings | change `allowed_signers`, `revoked_keys`, the bot's public keys, the trusted root commits, branch protection and repository visibility | be exercised by any automated process |
| **Execution** | the release runner: a dedicated macOS user account on the owner's Mac, run by launchd | fetch within the signed scope, extract, classify, plan, prepare and validate the proposed bytes, sign release manifests and publication attestations with the **bot key**, and publish with the **bot credential**: the App mutation on path A, or a bot-key-signed commit and fast-forward push on path B (§9.3) | sign a policy, clear an exception, change a trust root, publish anywhere but `main` of the one repository, force-push |
| **Repository write** | the bot credential: a GitHub App installation token with Contents: write on `lovverk-forarbeider` only (preferred, path A), or the tested alternative of §9.7 (path B) | update `main` of that one repository: through `createCommitOnBranch` with `expectedHeadOid` (A) or a fast-forward push (B) | administration, settings, other repositories, deleting branches or tags, force-push |

**Owner policy keys never reach the runner.**
- The owner key lives in the owner's macOS account Keychain, passphrase-protected. The runner is a separate macOS user with no access to that Keychain or agent.
- The runner receives only signed records: policies, stop, resume, clearances and withdrawals.
- AR7 also checks that the runner's ssh-agent and key files hold no key whose fingerprint is an owner key in `allowed_signers`; if one is found, the run refuses.

The bot key is listed in `allowed_signers` with **only** the namespaces `lovspor-forarbeider-release` and `lovspor-forarbeider-publication`. A bot signature in the policy namespace fails verification by construction. A compromised runner therefore cannot widen its own authority: the policy, trust roots and credential scope all live outside it.

### 9.2 Eligibility (unchanged gates, now automatic)

A document version is **eligible** for automatic publication only if:

1. an unrevoked, unexpired, owner-signed policy covers its source and family and states `automated_publication: true`, with a reference to a completed legal assessment (D10). No assessment reference, no automated publication;
2. a current source proof (fresh within the policy's window, nonce-normalized comparison per §3.3) matches the policy;
3. it is `source_complete` **and** `publication_complete` (§2.3). A version with any release gap is not eligible. Its eligible content is never published as a partial document under the automatic path; it waits in quarantine;
4. its integrity checks pass: raw digests re-hash, extraction reproduces, and the version id recomputes.

Everything else is **quarantined** (§9.6), never silently cleared and never silently dropped.

**Default, restated:** automatic release is `publication_complete` only.

**[D] (open):** may the automatic path ever publish `publication_incomplete` documents with explicit gap notices? This amendment says **no** by default. Changing that is a material policy change and needs owner approval.

### 9.3 The automated release transaction

One **release run** is a loop of bounded batches. Each batch is a transaction, and every step is idempotent and keyed by the batch's **release manifest digest**.

**Two publication paths.**
- **Path A, the GitHub App (primary):** GitHub creates the commit and moves `main` in one API call.
- **Path B, local commit and fast-forward `git push`:** an explicitly tested **fallback** (§9.7). It is never mixed with path A inside one batch.

The repository's configured path is part of the expected settings that AR7 verifies.

**Two validation phases, never confused:**

| Phase | What is validated | Where | What it can and cannot show |
|---|---|---|---|
| **Pre-publication** | the **proposed bytes**: the files the batch would add or remove, the manifest, the base commit | locally, in the release clone's working tree | that the proposal is correct and authorized as far as the runner can see. It cannot verify a GitHub commit, because none exists yet. On path A, B2 checks only the proposed tree, never "the commit" |
| **Post-publication** | the **actual commit** on `main` | read from GitHub after the publication event | that what GitHub published is exactly the proposal: parent, file set, manifest bytes, signature. It runs only after the commit exists |

#### Common steps

1. **Pre-checks.** Each step stops, with nothing written, when its condition fails:
   - the kill switch is off (§9.5);
   - the policy is valid now;
   - the daily budgets have room;
   - the release clone passes the B1a guard;
   - the local `main` equals the remote `main` (one read-only API read or `ls-remote`).
2. **Plan.** Select eligible versions not yet released, in deterministic order (family, session, id, version id), until a batch bound is reached: **100 files or 25 MB, whichever comes first** (recommended answer, pending). The manifest lists:
   - the versions with their digests;
   - the **base commit**, which becomes `expectedHeadOid` on path A;
   - every file to add or delete, with its digest;
   - the policy digest;
   - the source-proof digests;
   - the counts.

   Same inputs, same manifest, same digest.
3. **Prepare and validate locally (pre-publication).** The B1b transaction (lock, journal, staged bytes, documents then index then manifest, read-back) writes the proposed files into the release clone's working tree. Then the **proposed bytes** are validated:
   - every rights, privacy and completeness gate is evaluated again on exactly these bytes;
   - the dataset read-back equals the manifest's file set;
   - nothing outside the dataset changes;
   - the manifest file `releases/<digest>.json` is part of the proposal.

   **Any rejection here publishes nothing.** The batch is abandoned, the working tree is rolled back, and the documents responsible are quarantined (§9.4).
4. **Sign the manifest** with the bot key (namespace `lovspor-forarbeider-release`). The signed manifest is part of the proposed bytes.
5. **Authorize the publication, immediately before it.** The runner re-reads and re-checks, in this order:
   - the stop latch and the stop file;
   - an owner-signed `stop` record;
   - the signed policy: still unrevoked, unexpired, and the one the manifest names;
   - the revocation lists;
   - the daily budgets, counting this batch;
   - the source proof, still inside its window;
   - the integrity of the proposed bytes and of the manifest signature;
   - the remote `main` still equal to the manifest's base commit.

   It then writes a **publication attempt record**: manifest digest, base commit, path, time. These checks are local. They show that the runner *believed* the publication was authorized when it decided; they are **not cryptographic proof** of authorization at the moment GitHub accepted it (§9.3a).

   Any failure aborts without publishing. A stop or revocation **sets a persistent stop latch**; once set, no step initiates another publication until an owner-signed `resume` record clears it.

#### Path A: GitHub App (primary)

- **A6. Publish.** Call GraphQL `createCommitOnBranch` on `main`, at once after step 5, with:
  - `expectedHeadOid` = the manifest's base commit;
  - the proposed additions and deletions;
  - a message carrying the manifest digest.

  **The successful mutation is the publication event:** GitHub creates the commit and moves `main` to it atomically. If `main` is no longer the base, GitHub refuses the mutation (`expectedHeadOid` mismatch) and nothing is published.
- **A7. Verify the actual commit (post-publication).** Read, from GitHub, the commit the mutation returned (or, if the response was lost, see §9.3a). Check:
  - its parent is exactly the base commit;
  - its changed-file set is exactly the manifest's;
  - the manifest bytes in its tree are the signed manifest, and the bot signature verifies;
  - **GitHub's signature verifies**, and the commit is attributed to the expected App identity (the App's bot account, App id pinned in the expected settings; signature class `github_app`, §9.7);
  - `main` was at this commit when read (later commits are recovery's concern).
- **A8. Receipt, only after A7 passes:** manifest digest, commit, the time GitHub reported, App id, policy digest, bot key fingerprint. Then a **publication attestation**, signed by the bot key (namespace `lovspor-forarbeider-publication`).

  If A7 fails, the batch is treated as a published integrity anomaly: recorded as published, never republished, reported, latch set (§9.3a).

#### Path B: local commit and fast-forward push (tested fallback)

- **B6. Commit locally:** one commit by the bot identity, **signed with the bot key** (signature class `bot_key`, §9.7), with the message carrying the manifest digest.
- **B7. Validate the local commit (still pre-publication):** the B2 checks can run on this local commit, because it exists before publication. Every file is the manifest's, nothing outside the dataset changed, and the signature verifies.
- **B8. Re-run step 5** on the commit; the publication attempt record names the commit.
- **B9. Publish:** a fast-forward `git push` to `main`. **The accepted push is the publication event.** A rejected push publishes nothing and goes to recovery. Never force, never rebase.
- **B10. Verify post-publication:** the remote `main` equals the pushed commit, and the remote commit's content and bot signature are as validated.
- **B11. Receipt and attestation**, as in A8.

#### The authorization-to-publication race (both paths)

- A stop, revocation or policy expiry that lands between step 5 and the publication event (A6 or B9) cannot be seen by the runner. The window is short (one local check and one network round trip) but cannot be closed by client code.
- The authoritative emergency stop is **revoking the bot credential or uninstalling the App on GitHub**: GitHub then refuses the publication, whatever the runner does.
- A batch that slipped through the window is a completed publication. It is classified, reported, and withdrawn forward-only if the owner decides (§9.3a, §9.7).

#### Recovery, by path and last completed step

| Path | Last completed step | Recovery |
|---|---|---|
| both | before 3 | nothing to undo |
| both | 3 or 4 (proposal prepared, nothing sent) | roll back (B1b recover) or re-run from step 5 with the full authorization |
| A | 5 (attempt record written; mutation not sent, or **response unknown**) | **§9.3a first**: look on the remote for a commit whose parent is the base and whose message carries the manifest digest. Found: the batch **was published**; verify it as A7, receipt it, never re-send. Not found and `main` is still the base: nothing was published; re-run from step 5. Re-sending the same mutation is safe even if the first call had succeeded unseen, because `main` has then moved and `expectedHeadOid` makes GitHub refuse the duplicate |
| A | A6 succeeded, A7 or A8 not done (crash before receipt) | §9.3a: verify the commit as A7, write the receipt (`recovered_from_remote: true`) |
| B | B6 or B7 (local commit, not pushed) | re-run from B7 with the full step 5 authorization; if validation fails, reset the local branch to the remote and quarantine |
| B | B9 sent, **outcome unknown**, or later | §9.3a: recover from the remote history, never from local state alone |

A batch whose manifest digest is already on the remote, and verified, is `UNCHANGED`. **A verified remote batch is never republished**, on either path.

### 9.3a Recovery from the remote history

A runner that fails around a publication event may not know whether it happened. The remote is the record of what was published, so recovery reads it before anything else and before any new plan:

1. Read `main` from GitHub. Take every commit after the last commit with a receipt.
2. For each such commit, oldest first, establish **integrity** (step 2a) and, separately, **authorization** (step 2b).

**2a. Integrity: is this a well-formed pipeline commit?** Verify, do not assume:
   - it is a fast-forward descendant of the last receipted commit, and its parent is the base its manifest names;
   - **its signature has the class the configured path requires:**
     - path A, `github_app`: GitHub reports the signature verified and attributes the commit to the pinned App identity;
     - path B, `bot_key`: the signature verifies with `ssh-keygen -Y verify` against the bot key in `allowed_signers` (namespace `lovspor-forarbeider-release`), the key valid at the commit's stated time.

     A commit of the wrong class, of another App, or with a signature GitHub does not verify, fails integrity;
   - the tree holds exactly one new `releases/<digest>.json`, whose digest matches the commit message;
   - the manifest's own bot signature verifies. **A valid App commit carrying a manifest whose bot signature is invalid fails integrity**;
   - every file the commit changed is listed in the manifest, and no file outside the dataset changed;
   - the manifest names a policy that verifies against the owner key.

**2b. Authorization: did the policy still hold at the publication event?** This is a separate question. A policy valid at *planning* time proves nothing about the moment GitHub accepted the mutation or the push: a revocation, an expiry or a stop can land in between.

- **Commit author and committer timestamps are not authoritative:** they are client-set on path B, and on path A they are GitHub's statement, not something signed for us.
- **Evidence used, each with its limit:**
  - **(i) the runner's publication attempt record:** step 5 passed, at the runner's clock, for exactly this manifest digest and base. A local record, not proof.
  - **(ii) GitHub's own record:** on path A, the mutation's response and the commit GitHub created; on either path, the repository activity API entry for the ref update (actor, `before` and `after` commits, timestamp). Asserted by GitHub, not signed for us; retention limited [A, verify].
  - **(iii) the owner-signed times on revocation and stop records:** the owner's claim of when they took effect, signed but not timestamped by any third party.
- **Classification:**
  - `authorized`: (i) exists for this manifest, (ii) exists, and (ii)'s time is earlier than every revocation, expiry and stop time in (iii) that applies to the policy;
  - `unauthorized`: (ii)'s time is later than an applicable revocation, expiry or stop time, or the policy was not valid at planning time;
  - `authorization_unknown`: anything else. That includes a missing (i), a missing or expired (ii), and an ordering within the uncertainty of the clocks involved.
- **What cannot be proven:**
  - the exact moment GitHub performed a publication, by any cryptographic means available to us;
  - that an owner-signed revocation was signed at the time it states;
  - that no publication happened in the race window of §9.3.

  The classification is an evidence-based judgement, recorded with its inputs, **never a proof**.

3. **Outcome, by integrity and authorization:**
   - **integrity valid, `authorized`:** a completed publication. Recovery writes its receipt (`recovered_from_remote: true`) and counts its budget (§9.5). This is bookkeeping for something already public, **not a new publication authorization**.
   - **integrity valid, `authorization_unknown` or `unauthorized`:**
     - the commit is recorded as **published**, because it is public, and **never republished**;
     - it is counted against budgets once, and **reported** to the owner with the evidence;
     - the **stop latch is set**: **unknown authorization halts every subsequent release** until an owner-signed record resolves it (acknowledge, or withdraw forward-only).
4. Even when the policy is expired or revoked *now*:
   - nothing is republished, re-planned or re-sent on its authority;
   - a policy revoked with a withdrawal-relevant reason makes recovery **report** the affected commit for an owner-signed withdrawal decision (§9.7), which it never performs on its own.
5. A commit that fails an **integrity** check, or a commit not made by the pipeline's identity on the configured path (for example a manual push), **halts the pipeline**: the stop latch is set and the owner is notified as for a possible compromise. Nothing further is published until an owner-signed `resume` record clears the latch.
6. Only after recovery has receipted or halted on every new remote commit may a new batch be planned, against the recovered base.

**Crash boundaries and races (AR4 tests every one):**

*Both paths:*
- a crash before the proposal;
- **local changes rejected before publication** (a gate fails on the proposed bytes): nothing is sent, the tree is rolled back, the documents are quarantined;
- a crash during the receipt write;
- a remote advanced by a valid pipeline commit we did not record;
- a remote advanced by a foreign or unverifiable commit;
- **recovery after policy expiry or revocation:** a published batch is receipted and classified, never republished; nothing new is planned on that policy;
- **the policy revoked after planning but before publication:** step 5 refuses; in the race window, recovery classifies the commit, records it as published, reports it and sets the latch;
- **a valid remote commit with unknown authorization:** recorded as published, never republished, reported, latch set.

*Path A:*
- **the App mutation succeeded but its response was lost:** recovery finds the commit by base and manifest digest, verifies and receipts it, and re-sends nothing; a re-send is refused by `expectedHeadOid`;
- **an `expectedHeadOid` mismatch:** GitHub refuses; nothing is published; recovery runs before any re-plan;
- **the wrong App identity, or a GitHub signature that does not verify:** integrity fails, the pipeline halts;
- **a valid App commit with an invalid bot-signed manifest:** integrity fails, the commit is recorded as published, reported, and the pipeline halts;
- a crash after the mutation and before the receipt.

*Path B:*
- a crash after the local commit and before the push;
- a push sent with no answer;
- a push that landed, then a crash before the receipt;
- a remote advanced between step 5 and the push (rejected non-fast-forward).

### 9.4 Failure isolation

- A failure attributable to a document (a render failure, an integrity mismatch, a detector anomaly in its spans) quarantines **that document** with a reason code. The batch is re-planned without it, so unrelated eligible documents proceed.
- A failure not attributable to a document stops the run and trips the kill switch:
  - trust or policy (a signature, revocation or expiry);
  - repository (guard, push protection);
  - source (robots refused, a proof mismatch for the whole source).
- No retry loop republishes a quarantined document. Only an owner-signed exception clearance releases it from quarantine.

### 9.5 Kill switch, budgets, anomalies, notification

- **Kill switch, three independent levels.** Any one stops publication before the next push:
  1. a local stop file in the runner's private workspace, checked before every step;
  2. an owner-signed `stop` record (policy namespace). Revoking the policy has the same effect;
  3. revoking the bot credential on GitHub (immediate, outside the runner).

  Resuming needs an owner-signed `resume` record, or a new policy.
- **Budgets per day and per source:** documents, files, bytes, commits, fetch requests. Budgets are part of the signed policy. Reaching one ends the run cleanly, with no partial batch.
- **Each batch is counted exactly once.** The budget ledger is keyed by the release manifest digest. A batch is counted when its commit is confirmed on the remote: after post-publication verification (§9.3 A7 or B10), or by recovery (§9.3a). A retried, recovered or `UNCHANGED` batch never counts twice. A batch that never reached the remote never counts against publication budgets.
- Step 5 checks the ledger *plus* the batch about to be published, so a publication never exceeds a budget.
- **Anomaly detection.** Each item halts the run and notifies. Thresholds are in the policy:
  - the quarantine rate per run above a threshold;
  - a detector hit-rate shift;
  - extraction fidelity below a floor;
  - a sudden change in a source page's size or structure distribution;
  - a robots.txt or terms-locus change;
  - a policy nearing expiry;
  - a publication refused twice (an `expectedHeadOid` mismatch or a rejected push).
- **Notification** goes through the existing alert channel (`LOVSPOR_ALERT_WEBHOOK`, a secret ntfy topic; its value is never logged) plus a daily summary. The summary gives counts and codes only: no text, names or paths.

### 9.6 Quarantine

Quarantined documents **stay visible in the completeness metrics**:
- they count in `documents_ingested`, and in `documents_source_complete` when that holds;
- each reason is a release gap in `spans_rights_unresolved`, `spans_privacy_unresolved` or `attachments_unpublishable`;
- a separate `documents_quarantined` count is reported;
- nothing is dropped from the denominators.

A quarantine record holds the document or version id, the scope (document, part, span or attachment), the reason code, the first time seen and the evidence digests. It lives in the runner's private workspace, is never published and never expires silently.

Owner actions on quarantine are **exceptions**, by design rare:
- sign a clearance for a specific span class or span;
- sign a permanent exclusion with a reason;
- change the policy (a material change).

### 9.7 Git model and visibility

- **Repository:** `bartoszkobylinski/lovverk-forarbeider`, private during preparation (recommended answer, pending). Making it public is an owner act (trust root), not a release step.
- **Bot identity, preferred: a GitHub App** owned by the owner's account and installed on `lovverk-forarbeider` only:
  - its only repository permission is **Contents: write**, plus the read-only metadata permission every App has;
  - it uses short-lived installation tokens;
  - its identity is specific: a ruleset bypass actor of type `Integration` names this App by its id, so it bypasses for this App and no other actor [A, verify against current GitHub docs and on a disposable test repository before AR7];
  - it commits through the API (for example GraphQL `createCommitOnBranch`, whose `expectedHeadOid` gives GitHub-enforced compare-and-swap on the base commit). GitHub signs such commits as the App, which satisfies "require signed commits" without a machine user's key [A, verify: request size limits for 25 MB batches; if too small, the Git Data API with a non-forced ref update, verified the same way].
  - Our own provenance signature is the **bot key's signature on the release manifest and the publication attestation** (§9.1), independent of GitHub's commit signature.
- **Two commit signature classes, never mixed:**
  - `github_app` (path A): GitHub signs the commit it creates, and its verification API reports the signature valid and attributes the commit to the App's bot identity. We verify GitHub's statement and the pinned App identity; we hold no key for this signature.
  - `bot_key` (path B): the runner signs the commit with the bot key, and verification is `ssh-keygen -Y verify` against `allowed_signers`. GitHub's own "verified" badge then also requires that key to be registered on the bot's account.

  The expected class follows the configured path. A commit of the other class fails integrity (§9.3a). The bot key's signature on the **manifest and attestation** is present on both paths.
- **Alternatives, only if the App path fails the test:**
  - a **machine user** with write on this repository only and its SSH signing key registered, as a bypass actor by its user identity;
  - a **deploy key** with write access. **Caution:** a ruleset bypass actor of type `DeployKey` carries `actor_id: null`, which means **every** deploy key with write access on the repository bypasses, not one chosen key [A, verify]. If used:
    - exactly one write deploy key may exist;
    - AR7 refuses on any second write key;
    - no deploy key may sit on any other bypass list.

  Whichever path is used, it is documented with the test that proved its permissions.
- **Two rulesets on `main`.** GitHub enforces both:

  | Ruleset | Rules | Bypass actors |
  |---|---|---|
  | **R-integrity** | block force pushes; restrict deletions; require linear history; require signed commits | **none** (not the bot, not the owner, not admins) |
  | **R-writers** | restrict updates (only bypass actors may update `main`) | the bot (the App by id, or the tested alternative) and the repository admin (the owner) |

  - The bot's bypass applies **only** to R-writers. It never bypasses R-integrity.
  - No pull request rule is configured: releases are direct fast-forward updates, with no per-batch PR or merge.
- **Claims are limited to what GitHub enforces:**
  - who may update `main`;
  - no force-push or deletion;
  - linear history;
  - commits carrying a signature GitHub verifies;
  - the credential's repository and permission scope;
  - `expectedHeadOid` when the API path is used.

  Nothing else restricts the bot on GitHub's side. Policy validity, eligibility, budgets, stop state, manifest correctness, source proof and completeness are **client checks only**. The documentation never states that GitHub restricts the bot to "valid releases".
- **The pipeline stays disabled** unless AR7 can read and verify the actual settings:
  - rulesets and every bypass actor;
  - the App installation's permissions and repository selection;
  - deploy keys and their write flag;
  - collaborators and roles;
  - visibility.

  Unverifiable is treated as wrong.
- **Stolen-credential risk:** a stolen bot credential (and, for the manifest signature, the bot key) could push well-formed commits that skip every client check. That risk is mitigated by:
  - (a) revoking the credential or uninstalling the App: immediate, enforced by GitHub;
  - (b) the independent auditor below;
  - (c) withdrawal.
- **Independent auditor (read-only).** A job under the *owner's* account, not the runner's:
  - it re-verifies every new commit on `main` by the rules of §9.3a step 2 (bot signature, manifest, file set, historical policy binding);
  - any violation alerts the owner at once.

  It holds no write credential.
- **Releases become visible** at the publication event (the App mutation on path A, the accepted push on path B), when `main` moves, and publicly once the repository is public. Each release is one commit with a manifest, so `git log` is the release log.
- **Provenance in the commit:**
  - the bot identity and its signature;
  - the manifest digest in the message;
  - the manifest in the tree, naming the policy digest, the source-proof digests and every version digest.
- **Withdrawal** is a new commit by the same pipeline. It executes an **owner-signed** withdrawal record: the tombstone, the removal and the index update. It cannot erase history:
  - the earlier commit remains reachable in `main`'s history;
  - every clone, fork, mirror, cache and package made before the withdrawal keeps the bytes;
  - rewriting `main` would break every downstream clone, is forbidden by protection, and still would not reach existing copies.

  That is why every gate runs **before** the first publication event, and why unresolved content is quarantined, never published.

### 9.8 What changes in shipped and planned slices

| Slice | Change |
|---|---|
| **B1a** guard | the release clone belongs to the runner account; trusted root per repository (FC9b); `main` is the release branch of `lovverk-forarbeider` (today the guard refuses `main` only through the plan's `forarbeider/` branch grammar, which the automatic path replaces) |
| **B1b** apply/recover/withdraw | the typed-phrase confirmation is replaced, on the automatic path only, by "the plan is the signed policy's"; the manual mode stays for exceptions. The transaction, lock, journal and recovery are unchanged and now **prepare the proposed bytes** (§9.3 step 3); publication is added after them: the App mutation (path A) or a local commit and push (path B), with remote recovery (§9.3a) |
| **B2** reader and attestation | **pre-publication**, B2-style checks validate the *proposed bytes* (and, on path B only, the local commit). **Post-publication**, B2 verifies the *actual GitHub commit*: parent, file set, manifest and its bot signature, and the signature class (`github_app` or `bot_key`). On path A, B2 never claims to verify a commit before it exists. The owner attestation is replaced by the bot-signed publication attestation; the reader verifies policy → manifest → commit → attestation |
| **FC7** policy v2 | adds `automated_publication`, budgets, anomaly thresholds, the legal-assessment reference, stop and resume records |
| **FC8** release manifest | becomes the automatic planner and batch transaction (§9.3); the manifest is bot-signed and names the base commit used as `expectedHeadOid` |
| **FC9** withdrawal v2 | executed by the pipeline from an owner-signed withdrawal record |
| **FC9b** bootstrap | creates the repository, branch protection, the bot credential and pins the bot keys: all owner acts, documented and checked by a read-only verifier |
| **FC10** | the first complete Prop. L is published by the automatic path, once D10 is done and the policy enables it; the acceptance rule is unchanged |
| **FC11** discovery | feeds the automatic path; its scope is part of the signed policy |

### 9.9 Implementation PRs (bounded; each red-first, fail-closed)

| PR | Scope | Red-first tests | Fail-closed |
|---|---|---|---|
| **AR1** Policy v2 automation fields | `automated_publication`, budgets, thresholds, legal-assessment reference; stop and resume records; namespace checks (bot key never verifies as policy) | a bot-signed policy is refused; no legal reference means not automated; an expired or revoked policy denies | yes |
| **AR2** Deterministic automatic planner | eligibility (§9.2), ordering, batch bounds, manifest | same inputs give the same digest; an incomplete version is never planned; a 101-file batch splits | yes |
| **AR3** Quarantine and failure isolation | quarantine records, per-document attribution, re-plan without the failing document | one bad document does not block the others; quarantine is never auto-cleared | yes |
| **AR4** Publication transaction and remote recovery | §9.3 steps 3–5, path A (`createCommitOnBranch` with `expectedHeadOid`, against a **stubbed GraphQL endpoint** in tests) and path B (commit and fast-forward push against a **local bare remote**); post-publication verification; publication attempt records; §9.3a for both paths; stop latch | every crash boundary and race listed in §9.3a, including: **the App mutation succeeded but the response was lost** (found by base and manifest digest, receipted, nothing re-sent); **an `expectedHeadOid` mismatch** (refused, nothing published); **wrong App identity or an invalid GitHub signature** (halt); **a valid App commit with an invalid bot-signed manifest** (recorded as published, reported, halt); **local changes rejected before publication** (nothing sent, rollback, quarantine); **recovery after policy expiry or revocation** (receipted and classified, never republished); a policy revoked after planning but before publication; a valid remote commit with unknown authorization (published, reported, halt); a batch counted exactly once | yes |
| **AR5** Kill switch, budgets, anomalies, notification | three-level stop, budget accounting, anomaly checks, an alert sink | the stop file halts before the publication call; a budget hit ends the run cleanly; an anomaly halts and notifies (sink mocked; no secret in logs) | yes |
| **AR6** Publication verification, attestation and reader | post-publication verification of the actual commit by signature class (`github_app`: GitHub reports the signature verified and attributes the commit to the pinned App identity; `bot_key`: `ssh-keygen -Y verify` in the release namespace); the bot-signed publication attestation; the B2 reader verifying the chain | a commit of the wrong class for the configured path, another App or an unverified GitHub signature is refused; an attestation by an unlisted key or the wrong namespace is refused; a reader never treats proposed bytes as a published commit | yes |
| **AR7** Bootstrap and settings verifier (read-only) | before every run, reads the **actual** remote settings through the GitHub API (repository rulesets and their bypass actors, the effective rules on `main`, collaborators and roles, deploy keys and their write flag, visibility) and compares them with the expected configuration of §9.7 | a missing rule, bypass on R-integrity, a bot with admin or an unexpected visibility refuses the run; **an unexpected ruleset bypass actor** (any actor beyond the expected App id and the admin role, including a `DeployKey` actor with `actor_id: null` when the App path is used) refuses; **an unexpected write-enabled deploy key** refuses; App permissions wider than Contents: write, or installed on other repositories, refuse; the App id, slug and installation id must equal the pinned identity, and the configured publication path (A or B) must equal the expected settings; any API read that fails refuses; a fixture of API responses per case | yes |
| **AR8** Independent auditor | the owner-account, read-only re-verification of every new `main` commit by §9.3a step 2: signature class per configured path, App identity, parent and base, file set, manifest bot signature, historical policy binding | a forged, unsigned, out-of-manifest or wrongly policy-bound commit alerts; a commit of the wrong signature class or another App alerts; a valid App commit with an invalid bot-signed manifest alerts; a commit with valid integrity but `authorization_unknown` or `unauthorized` alerts; a valid, authorized one passes | alerts on failure |

**AR1 status (#947, AR1b).** AR1 shipped as release contracts and pure gates (#947). AR1b adds the rest of the AR1 row:
- owner-signed limits (batch caps, daily budgets) in the namespace `lovspor-forarbeider-limits`;
- the budget ledger's accounting records with their replay verifier and planner;
- owner-signed stop and resume records in the namespace `lovspor-forarbeider-control`;
- one resolved trust root for all release evidence;
- a sealed-provenance boundary, so a source label alone is never trusted.

The owner role may now hold the policy, limits and control namespaces; a bot key can hold none of them. Two hash chains still prove less than they appear to, so their gates stay failed until AR5 adds what is missing:
- **Budget ledger.** A local chain proves neither append-only storage nor concurrency safety. AR5 must provide:
  - an atomic append with compare-and-swap on the chain head;
  - durability before the publication call;
  - an external anchor of the head, outside the runner;
  - a single-writer lock;
  - reserve before publication, settle after verification;
  - reconciliation against the published history.
- **Control chain.** Whoever holds the chain can withhold its newest stop. AR5 must anchor the chain's head outside the runner.

The provenance seal is narrower than it looks, too. It assumes that all code running in the engine process is trusted:
- **`issue_provenance()` is an ordinary callable, not a runtime authorization boundary.** Any code in the same process can import it and seal a record it built itself, without knowing the HMAC key.
- **The AST import test is a review aid, not runtime security.** It limits which modules in this repository import the function today. It cannot stop code loaded at runtime.
- **Untrusted code must never reach the sealing capability.** Before AR4–AR8 integrate the gates into a production path, that path must guarantee that no untrusted code, plugin, extension or API input runs in, or can call into, the process that seals and judges evidence. Caller input reaches the engine only as raw material that a verifier checks, never as evidence.
- **An in-process HMAC seal is not auditable proof.** It shows only that this process's verifier produced the record. It proves nothing to an auditor, another process or a later run, and it is lost on serialization by design.
- **Persisted or externally verified evidence needs real signatures (AR6).** It must carry a signature that can be checked against the trust root, never a seal.

**AR2 status.** AR2 adds the expected-document inventory and the batch planner:
- **Owner-signed inventory** in the namespace `lovspor-forarbeider-inventory`. It binds the exact policy and scope, an inventory id and version, a validity window, the digests of the discovery evidence the owner reviewed, and every expected document with its stable upstream id, type and discovery URL. It attests `completeness: complete`. Two valid inventories conflict, and the owner replaces one by revoking it.
- **No inventory from a catalogue yet.** FC11 discovery has no verifier of a catalogue's authenticity, and D7 is open. Catalogue pages alone therefore stay `missing` (`catalogue_verifier_not_implemented`) and never become an inventory.
- **Deterministic planner.** FC8b rebuilds each offered document's selection, so only the newest verified version is used and a blocked head is never replaced. Every inventory document, and every document offered outside it, gets one visible disposition: `planned`, `blocked`, `excluded`, `missing` or `outside_inventory`.
- **Sizes counted from artifacts.** Each document's file count and byte size are computed from its bound artifact bytes. An artifact that is missing, or does not hash to its key, excludes the document. Planned documents are split, in id order, into the fewest batches that keep both the hard caps and the signed batch limits. A document larger than any batch is blocked.
- **Stable, binding identifiers.** Plan and batch ids are digests of the inventory, policy, limits, selection and artifacts, so any change makes a stored plan `stale`.
- **What the planner leaves to the gates.** It plans a document only when its sole blockers are the release-wide ones that FC8b gives every document today: the unanchored head, D10 and D11. The plan names those blockers, and the AR1 gates still fail on them. Any other blocker, such as FC7, FC7b, completeness, privacy or a foreign policy, keeps the document out of every batch.
- **Gate integration.** The inventory gate requires a sealed plan bound to the sealed inventory and the signed limits, of which the manifest is exactly one batch, selection by selection and binding by binding. Two observations can share a selection and a version yet rest on different FC6, FC5, FC7 or FC7b records or lineage heads, so each planned document names both digests, and a batch document whose binding differs refuses the release (`batch_artifact_binding_mismatch`). A document the plan did not plan blocks the release (`document_not_planned`). Planned documents grouped differently from the plan's batches refuse it (`batch_not_in_plan`). The constraints gate checks caps and budgets against the planner's count. The caller's batch size is only compared with that count; without a plan it is `batch_size_unverified`.

Limitations:
- Sizes use the bound private artifacts (`size_basis: bound_artifacts`). AR4 must recount them from the rendered publication bytes it proposes.
- Daily budgets are checked when a batch is reserved, by the ledger and the constraints gate, not when it is planned.
- Today no real document is plannable: publication completeness is never reached, so every selection carries a per-document blocker.

**AR3 status.** AR3 adds quarantine, failure attribution and re-planning (§9.4, §9.6):
- **Attribution by exact code, fail closed.** Every failure in the AR2 base plan, and every failure a later stage reports, is looked up in an explicit table. A failure is quarantined only when it is proven to belong to one document: extraction, rendering, artifact integrity, rights or privacy spans, attachments, source or publication completeness. Anything else is a global failure: the trust root or a signature, the policy (another policy, none evaluated, not applicable), FC7b authorization, a rewritten or forked lineage (the private store), a source-wide proof mismatch, the repository, publication authorization. An unknown code, a summary code without its details, or an FC7 decision that cannot be read or contradicts its counts is a global failure of category `unknown`. One global failure halts the run: nothing is merged and nothing is re-planned.
- **Pending decisions are not failures.** The release-wide blockers (the unanchored head, D10, D11) and FC7b's always-blocking codes (D6, D10, D11, the missing integration) neither quarantine nor halt; the AR1 gates judge them.
- **Quarantine records.** Each record is keyed by document, exact version, scope (document, part, span or attachment) and reason code. It keeps `first_seen`, the evidence digests and policy of its latest observation, and an append-only history (`observed`, `evidence_changed`, `cleared`, `reinstated`). Spans and parts are named by their FC5 ids and attachments by a digest: no text, URL or name. A retry gives the same bytes. A failure no longer reported, a new version (another record) or a policy change never clears a record.
- **The ledger** is private (`published: false`) and canonical; a successor that drops or rewrites anything is detected (`successor_findings`). Quarantine is separate from the runner's STOP: a halt is reported for the kill switch to act on, and no stop record is written.
- **Owner exceptions** are signed in the namespace `lovspor-forarbeider-quarantine`, which only an owner key may hold. They bind the exact policy, document, version and evidence digests. A clearance covers one finding, one reason class of a version, or one version; an exclusion is permanent and has a reason code. A forged exception, a bot or revoked key, another namespace or an unverifiable revocation halts the run. An expired, premature, revoked or foreign-policy exception is only rejected. A stored `cleared` is never trusted: clearances are re-verified on every run, and a record whose clearance is not offered, or whose evidence changed, is reinstated. A clearance lifts only the quarantine: the document's FC7, privacy, completeness and integrity blockers, and every AR1 gate, still apply.
- **Re-planning.** The planner accepts only a sealed quarantine assessment made from the same root, inventory and limits for exactly its base plan (`plan_quarantine_unverified`, `plan_quarantine_mismatch`, `plan_quarantine_stale`). A restricted document is `quarantined`: it is visible with its version, binding, artifacts and every reason, but never batched. The other documents keep their order and are partitioned again within the same caps. The basis names the quarantine set's digest, so changed membership changes the plan and batch ids. A plan made without a quarantine assessment blocks the inventory gate (`plan_quarantine_unassessed`).
- **Metrics:** `documents_expected`, `documents_ingested`, `documents_source_complete`, `documents_quarantined`, `documents_excluded_by_owner`, `documents_planned`, `spans_rights_unresolved`, `spans_privacy_unresolved`, `attachments_unpublishable` and release gaps by category. Quarantined documents stay in every denominator.

Limitations:
- The ledger is a local file. Like the budget ledger, it proves neither that it was not withheld nor that it was not replaced; atomic persistence, compare-and-swap and an external anchor of its digest are AR5. A lost ledger is not detectable before then.
- `collect_evidence` does not run quarantine yet. AR4 must pass the sealed quarantine assessment into the planning it does, and report the stage failures (rendering, publication) it finds.
- Under any trust root other than the engine-pinned one (every test and pilot root), every real run halts: each real document carries `fc7b:trust_root_not_engine_pinned`, which is global. In the offline pilot this was the only global failure; Prop. 31 L would otherwise get 416 span findings (rights) and one completeness finding.

**AR4a status.** AR4a builds the offline preparation half of AR4 (§9.3 step 3): the exact proposed bytes of one batch, prepared and validated in an isolated clone, with a crash-safe journal. Nothing is authorized, signed or published.
- **Transaction contract** (`publication_models`, version `publication-transaction/1`). A transaction binds the sealed plan, the batch, the quarantine set the plan honoured, each document's selection and binding digests, the base commit and the proposed files with digests, sizes and the digests they replace. The id is the digest of all of that. Paths A and B are separate record types, and the path is part of the content. So a transaction prepared for path A is never path B's: it gets another id and another release manifest.
- **Publication tree of `lovverk-forarbeider`.** Exactly four kinds of file exist: `documents/regjeringen-<id>.md`, `INDEX.md`, `catalogue.json` (the membership list) and one `releases/<sha256>.json` per batch, named by the digest of its own bytes. A proposal can touch no other path.
- **Isolated clone.** The local source repository is only read. Git clones it with `--no-local --no-hardlinks --template=` into a private work directory outside every repository, checks out the base detached and removes the clone's only remote. The source's `main` must be exactly the expected base. Git runs through `lovspor.git_prepare`, whose allowlist has no shape for `commit`, `push`, `fetch`, `add` or any ref update. Only `publication_clone` imports it.
- **Bindings first.** Each of these halts before anything is written: a plan without the planner's seal, a plan without a quarantine assessment, a quarantine other than the plan's, a batch the plan does not have, a missing row, a selection or binding digest other than the planned one, or a quarantined document in the batch.
- **Re-evaluation on the proposed bytes.** The bound FC6 manifest and raw pages must reproduce the extraction. The bound FC7 decision must be the selection's version and policy, with its rules satisfied and every rendered span `allow`. The selection must state source and publication completeness.
- **Rendering.** Only non-annex spans are rendered, escaped as in the manual renderer. Unsafe text is refused. Counts are recounted from the rendered files, and the plan's caps apply to them.
- **Write and read-back.** The B1b `flock` lock and descriptor-only staging inside `.git` are reused, with a new journal of preparation states: `staging`, `staged`, `documents_written`, `index_written`, `manifest_written`, `reading_back`, `prepared`, and `rolling_back`, which only goes back. The read-back requires all of the following:
  - `HEAD` is still the base;
  - Git reports exactly the proposal's paths: new files `??`, replaced files ` M`, nothing ignored, staged or elsewhere;
  - every file holds its digest.
- **Failure handling.**
  - A document-local failure writes nothing and is returned as an AR3 stage failure (`artifact_integrity_mismatch`, `render_failed`) with its evidence digests. AR3 then quarantines the document, and the re-planned batch of the other documents prepares.
  - A global or ambiguous failure halts, and is never downgraded however many local failures there are. Examples: an FC7 decision that is unavailable or contradicts the plan, another base, a version or release already published, a base tree whose documents are not exactly its catalogue's, or a read-back that finds an unexpected change.
  - A failed read-back rolls the proposal back. The halt carries a `PreparationHalt` record for AR5. No stop record is written.
- **Recovery.** A journal is accepted only if it is canonical and describes its own transaction; anything else is refused and nothing is touched. Recovery reconstructs a proposal only when every write landed, the caller expects exactly the journaled transaction, and the read-back proves it. Otherwise it rolls back. It never goes forward from `rolling_back`, never overwrites a file edited by hand, and never publishes.

Limitations:
- **No real document can be prepared today.** FC6 never computes `publication_complete` (`publication_constraints_absent`), so no real selection is planned. Under any trust root but the engine-pinned one, the AR3 run halts first. In the offline pilot, Prop. 31 L halted on `fc7b:trust_root_not_engine_pinned`, and its AR2 plan has no batch. AR4a was demonstrated on invented documents whose rows *state* completeness, as the AR2 and AR3 tests do.
- **AR4a is a library.** No CLI command reaches it yet, and `collect_evidence` does not call it.
- **Left to AR4b:** step 5's publication authorization and attempt record; paths A and B themselves (the GraphQL mutation and the local commit and push); the post-publication verification; §9.3a. **Left to AR5:** the stop latch, budget reservation and a durable halt record. **Left to AR6:** the bot signature on the release manifest (`signature: not_signed_ar6_pending`). **Left to AR7:** the remote settings, and comparing the source `main` with the remote. Each is listed in `unavailable_checks` and never reported as passed.
- The renderer is a first, deterministic format for spans only. Tables, footnotes and annexes are not rendered.

**AR4b-1 status.** AR4b-1 adds §9.3 step 5 and the publication call of both paths, executable **in simulation only**. Production execution is denied by construction.
- **Authorization evidence** (`publication_execution_models`, version `publication-authorization/1`). It binds:
  - the attempt id, which is derived from the transaction and its next sequence number, never chosen;
  - the transaction, plan, batch and release manifest;
  - every proposed file and its digest;
  - the path, the base commit and the remote `main` as read at that moment;
  - the policy, the trust root and the publisher's key;
  - the STOP state, the quarantine, the limits and the budget reservation;
  - the check time and every check.
- **Check results.** Each check is `passed`, `failed` or `unavailable`, and unavailable is never passed. The decision follows from the checks alone, and the verifier seals the evidence. Evidence built or edited by a caller is refused, and so is evidence for another attempt or older than 120 seconds.
- **`authorize` checks everything itself, at the moment of the attempt:**
  - the AR4a `prepared` journal and its read-back;
  - the sealed plan and quarantine;
  - the trust root;
  - the FC7b approvals and revocations;
  - the bot publisher key;
  - the owner-signed limits;
  - the STOP/RESUME chain and the persistent stop latch;
  - a ledger reservation for exactly this transaction that covers the batch and one commit;
  - the remote head against the base;
  - the configured path and App identity;
  - on path B, the local commit's parent, paths, blobs and bot signature in the release namespace.
- **What stays unavailable.** The following cannot pass yet:
  - the AR1 gate verdict, which has no passing state;
  - FC7b's pending blockers (D6, D10, D11, the missing integration, an unpinned trust root);
  - the AR5 anchors of the control chain and the ledger;
  - the AR6 manifest signature;
  - the AR7 settings.

  Production needs every check passed, so it is denied. A simulation may waive exactly those, each listed in `waived`.
- **Attempt records** (`publication_attempts`). They are append-only and hash-linked, with one folder per transaction. `prepared_to_send` is written and synced **before** the mutation; after it comes exactly one of `published`, `refused` (provably nothing published) or `outcome_unknown`. Retries follow the last record:
  - after `published`, a retry is `unchanged`;
  - after a `prepared_to_send` with nothing following, or after `outcome_unknown`, a retry is `recovery_required`, and nothing is sent blindly.

  A STOP, a revocation or an unknown outcome sets the persistent stop latch, which only an AR5 owner resume will clear.
- **Simulation adapters** (`publication_sandbox`). A simulation waives the checks above, so its label is no boundary. It sends only through these two adapters. Each is proved before authorization, again before `prepared_to_send` is recorded, and again right before the mutation:
  - **Path A** uses exactly the in-memory `SimulatedGitHub`, checked by type identity. It is a slotted, final class, so an instance cannot swap its methods and a subclass is refused. Any other transport, a real client included, is refused before it is read or posted to.
  - **Path B** uses a `DisposableRemote`. `create_disposable_remote` makes it fresh, with no template, as the only entry of a private, empty sandbox. Before every use it is re-verified on disk:
    - no symlink or `..` in its name;
    - exactly its fixed configuration (bare, `denyNonFastForwards`, `denyDeletes`);
    - no hooks and no alternates;
    - the marker holds its random token.

  Any other path, a pre-existing repository included, is refused.
- **Path A** (`publication_path_a`). It builds one `createCommitOnBranch` with `expectedHeadOid` = the base. The mutation binds the repository, `main`, a headline naming the manifest digest, a body naming the transaction, plan and batch, and exactly the proposed bytes. The response is judged as follows:
  - a stale-head refusal is `refused`;
  - a well-formed commit with `main` reading as it is `published`;
  - anything else, including a lost response, is `outcome_unknown`.

  The module holds no transport. The only implementation is the tests' in-memory stub.
- **Path B** (`publication_path_b`, `lovspor.git_publish`).
  - The commit object is built from a separate index, so the clone's index and the AR4a read-back are untouched. It is signed in the `gpgsig` header by the caller's signer, in the release namespace, and validated before authorization.
  - It is pushed `<commit>:refs/heads/main` to an **absolute local `.git` path** only. The allowlist has no force, delete or mirror shape, no `+` refspec and no URL.
  - The result is judged from the remote `main` afterwards.

Limitations:
- **Production is disabled.** No GitHub transport, network remote or production signer exists. Real Prop. 31 L remains blocked upstream: the AR3 run halts on the unpinned trust root.
- **Unknown outcomes stop at `recovery_required`.** Resolving them from the remote history (§9.3a), post-publication verification (A7/B10), receipts and attestations are AR4b-2 and AR6.
- **The attempt folder and the stop latch are local.** Like the AR3 ledger, they prove neither that they were not withheld nor that the newest record was not replaced by an equally canonical one. Anchoring is AR5.
- **The budget reservation is only read.** Writing it atomically, reserving before the call and settling after verification are AR5.

**AR4b-2 status.** AR4b-2 adds §9.3a recovery from the remote history and post-publication verification for both paths, in simulation only. Production stays disabled, and recovery holds no send, push or execute.
- **Reading the remote** (`publication_remote`). The history after the transaction's base is read oldest first from the remote itself, never from the attempt records, an API answer or the local clone:
  - path A reads the in-memory `SimulatedGitHub`, standing in for GitHub's commit and activity records: parent, message, changed files, whether GitHub verified the signature and for which App, and the ref-update time;
  - path B reads the `DisposableRemote` bare repository's own objects and its reflog. The disposable remote now logs ref updates, and a push records the simulation clock.

  `main` that does not descend from the base (a root, a merge, an unreadable commit, more than 32 commits) halts as `halted_history_unverifiable`.
- **Integrity** (`publication_integrity`, §9.3a 2a). Every check is `passed`, `failed` or `unavailable`:
  - `ancestry`: one parent, the base;
  - `signature_class`: on path A, GitHub verified the signature for the pinned App; on path B, the bot key the root lists, in the release namespace, at the commit's stated time. A key revoked since is judged as if not revoked here, because the revocation carries no time;
  - `message`: the transaction's headline and body;
  - `files`: exactly the proposed paths, each with its SHA-256;
  - `manifest`: exactly one release file, named by its digest, binding this transaction's plan, batch, path and policy, and listing every other changed file;
  - the manifest's bot signature stays `unavailable` (AR6) and is waived in simulation only.
- **Authorization** (§9.3a 2b) is classified separately, as a judgement from evidence and never a proof. The owner's revocation and control records are evaluated as of a moment, by keeping only those signed by then:
  - a key revoked in the trust root has no time, so the result is `authorization_unknown`;
  - the policy not holding at the runner's `prepared_to_send` time is `unauthorized`;
  - a missing attempt record or a missing remote event is `authorization_unknown`;
  - otherwise the policy is evaluated at the event and at ±300 seconds: holding at all three is `authorized`, at none is `unauthorized`, and anything mixed is `authorization_unknown`.

  The AR1 verdict, FC7b's pending blockers and the AR5/AR7 anchors are listed as waived and never passed.
- **Receipts** (`publication_recovery_models`, `publication_receipts`, version `publication-receipt/1`). They are canonical, append-only and hash-linked, and bind:
  - the transaction, attempt and manifest;
  - the commit, its parent and the path;
  - both verdicts with their checks and reasons;
  - the classification.

  They hold no time of their own, so recovering twice writes nothing. A receipt on record is re-derived, and one the remote contradicts is refused (`receipt_conflict`, latch set). Edited, reordered or truncated receipts are refused on read.
- **Outcomes** (`publication_remote_recovery`):
  - **nothing after the base:** `not_published`. An attempt left `prepared_to_send` or `outcome_unknown` is closed `refused`.
  - **integrity verified:** the commit is recorded as published, with a receipt and a `published` attempt record. It is never resent; `execute` returns `unchanged` whenever a published receipt exists, even with the attempt records gone. It is counted once, through an idempotent AR3 ledger `commit` settlement that recovery plans and the caller appends.
  - **any authorization but `authorized`:** the latch is set.
  - **integrity failure** (`halted_integrity_failed`, or `halted_foreign_commit` for a commit not naming the transaction), **or any commit after ours:** the latch is set.

  Recovery runs under STOP and under the latch, and clears neither.
- **Races.**
  - **A moving remote.** `main` is read before the history walk and again after it, an empty walk included. A `main` that moved meanwhile gives `halted_history_unverifiable` (`remote_head_moved_during_read`); one that cannot be read the second time gives `remote_main_unreadable`. Either way there is no receipt, no attempt is closed, and the latch is set.
  - **Concurrent runs on one workspace.** Every execution (from authorization to its last record) and every recovery (from its first remote read through receipts, attempt records and the latch) holds `<workspace>/workspace.lock` by `flock`. A second holder is refused at once with `corpus_locked: workspace`, never queued, so it cannot deadlock; retried later, it finds the receipts already written.
  - **Lock ordering.** The workspace lock is a leaf: nothing takes another lock while holding it, and the AR4a clone lock is never taken under it.

Limitations:
- **The two reads of `main` bound the walk; they are not an atomic snapshot.** `main` can still move after the final read and before the receipt is written, and GitHub offers no read that pins it. A pinned or conditional read, or matching the activity API, is AR7. Until then a move after the final read is caught by the next recovery, as a foreign commit after ours.
- **The workspace lock is advisory and local.** It serializes processes that use it on one machine and one filesystem. It is not a distributed lock, and it does not stop the owner, who can write the private workspace directly.
- **The ref-update times are simulated.** On path A they are the endpoint's `now`; on path B they are the reflog written with the simulation clock. Reading GitHub's activity API, with its retention limits [A, verify], belongs to the production transport (AR7 and later).
- **Revocations in the trust root carry no time.** Any revoked key makes authorization unknown. Owner-signed, timed key revocations are AR5.
- **Receipts and attempt records are local.** Anchoring them is AR5. The ledger settlement is planned, not written.

**AR5a status.** AR5a adds durable, independently anchored control-plane state. It changes no AR1–AR4 behavior: the AR5 gates (`ar5_control_anchor`, `ar5_ledger_anchor`) stay unavailable until AR5 wires this evidence in.
- **Record families covered:**
  - `publication_attempts`, per transaction;
  - `recovery_receipts` and the `stop_latch`, per workspace;
  - the AR1b `budget_ledger`, per ledger;
  - the owner's `control_history`, per policy;
  - the AR3 `quarantine_ledger`, one canonical document.

  The workspace families are read through the existing readers. The caller holds the bytes of the other three and passes them in. The records stay the source of truth.
- **Control entries bind their signatures.** Each control entry is anchored as the record bytes together with the exact detached signature bytes, length-framed; a missing signature is framed as absent. Removing or replacing a signature therefore changes the head, and the state is no longer `verified`. Whether a signature is the owner's stays `verify_control`'s to judge: an anchored entry is never an authorized one.
- **Anchor contract** (`control-plane-anchor/1`, `control_plane_anchor_models`). One canonical anchor binds:
  - the family and its record schema;
  - the repository, and the scope with its kind;
  - the digest of the chain head, the record count and the last sequence;
  - the anchor's own sequence and the digest of the anchor before it;
  - `verification: verified`.

  It is deterministic and holds no record content.
- **Trust model.** An anchor counts only if an **anchor witness** outside the runner signed it:
  - **Its own role.** The witness key holds a new trust-root role, `anchor_witness`, in the namespace `lovspor-forarbeider-anchor` only. A key holding that role together with the owner's or the bot's is refused, and neither an owner nor a bot key can sign an anchor.
  - **Compare-and-swap.** The witness keeps one append-only chain per family and scope. It accepts an anchor only if it names the current head and the next sequence. It never replaces or removes an anchor, and never lowers a record count.
  - **Verification.** The verifier checks every anchor against the trust root: signature, role, revocation and validity window. Hashes alone authenticate nothing.
  - **Independence.** A witness located inside the workspace it anchors, or holding it, is refused (`store_not_independent`). A second file in the same workspace is never an anchor.
  - **The offline witness** (`OfflineAnchorWitness`) has its own private directory and its own key, which never enter the workspace. It writes each anchor as a temporary file, flushes it, then links it to its final name. Writers are serialized by an `flock` on the witness's own lock file.
- **Verification** (`control_plane_anchor.verify`):
  - The local chain must have canonical bytes and intact hash links (`local_invalid`).
  - The witness chain must be canonical, belong to this family and scope, be unbroken, never lower its count, and be witness-signed (`anchor_invalid`). An unreadable witness is `store_unavailable`.
  - A caller's pin of the newest anchor it has seen must still be in the chain (`anchor_rolled_back`).
  - Then the state is one of:
    - `unanchored`: there is no anchor, even if the family is empty. A missing anchor is never a clean history; `initialize` anchors an empty family explicitly, once, and refuses existing records that have no anchor.
    - `local_rolled_back`: fewer records than anchored (truncation, deletion, an older copy, a deleted stop latch, a withheld newest stop).
    - `diverged`: an anchored head the records do not reproduce (substitution, a rewritten chain, a forked writer).
    - `verified`: exactly the anchored records. Only this state is exposed, as `verified_state`.
    - `pending_anchor`: exactly one record more than anchored.
    - `unanchored_tail`: more than one record more than anchored.
- **Crash safety** (`durable_append`, for the workspace families). It holds the workspace lock, verifies, appends through the existing writer, re-reads the chain, then anchors. An interruption:
  - **before the append** leaves the state verified and unchanged;
  - **after the append, before the anchor** leaves `pending_anchor`, which only a retry of the same record completes;
  - **during the anchor update** leaves no partial anchor, so the state is `pending_anchor`;
  - **after the anchor, before the acknowledgement** leaves the state verified, and a retry is `already_applied`.

  `anchor_pending` anchors exactly one pending record. More than one halts.
- **Lock ordering:** clone lock (AR4a, never under the workspace lock), then the workspace lock, then the witness lock, which is a leaf. AR4's execution and recovery never take the witness lock.

Limitations:
- **The offline witness is not a production authority.** Whoever holds its directory can roll it back. Then:
  - a newer workspace shows as `pending_anchor` or `unanchored_tail`, never as verified;
  - a consistent rollback of both the workspace and the witness is caught only by a caller's pin kept elsewhere.

  An append-only production witness (for example a transparency log, or a protected remote) is AR7, together with a production signer for the witness key.
- **Existing AR4 records have no anchors.** They are `unanchored`, and adopting them needs an owner decision (AR5).
- **An anchored quarantine ledger cannot be succeeded yet.**
  - Once a quarantine ledger is anchored, anchoring a different one is refused (`diverged`).
  - A witness chain in which a second document follows the first is `anchor_invalid` (`quarantine_successor_unverified`), for both documents.
  - Initializing an empty ledger and anchoring the first document still work.
  - **Later integration (AR5).** A successor may be verified only when all of these hold:
    - the predecessor's bytes reproduce the anchored head;
    - AR3's `successor_findings(previous, current)` is empty;
    - every clearance it relies on was re-verified under the owner's signed exceptions.

    The proof must travel with the new anchor, so the verifier can check it.
- **The ledger, control and quarantine writers are AR5.**
- **Not in this PR:** wiring `verified_state` into the STOP/RESUME and budget gates, owner resume, and budget reservation and settlement (AR5).

**AR5b status.** AR5b connects the AR5a anchors to STOP/RESUME and adds owner-authorized recovery from a stop. Simulation only. Production stays denied: in production `ar5_control_anchor` is still unavailable until AR7 provides a production witness, and `ar5_ledger_anchor` (AR5c), AR6 and AR7 are unchanged.
- **Verified control state before authorization** (`publication_anchors`, check `ar5_control_anchor`):
  - The verifier itself verifies three families against the witness: `control_history` for the plan's exact policy, the `stop_latch`, and the transaction's `publication_attempts`.
  - Any state other than `verified` denies: missing, stale, rolled back, diverged, unavailable, or not independent. No simulation may waive this check.
  - Nothing the caller passes counts as verified. The verifier also reads the latch from the workspace itself, not only from the adapter.
  - **A STOP cannot be bypassed by offering older valid control records.** Once the STOP is anchored, withholding it is `local_rolled_back`.
- **Sealed evidence never outlives a later STOP.** `publish_authorized` accepts evidence while it is fresh (120 s), but under the workspace lock and before `prepared_to_send` it re-reads the state the evidence was issued against:
  - the latch in force is refused (`stop_latch_set`);
  - a latch transition since the evidence is refused (`authorization_stale:stop_latch`); the evidence binds `stop_latch_head_sha256`;
  - the owner records are verified again at the send time: a stop in force or a lapsed resume lease is refused (`stop_control:…`), and a control head other than the evidence's is refused (`authorization_stale:control_history`);
  - the three anchored families are verified again against the witness (`anchor:…`).
  - The latch is read once more right before the send. A refusal sends nothing and makes no network call. `execute` is unaffected: it authorizes fresh under the same lock.
- **Appends are anchored.**
  - Execution and recovery anchor every attempt record, receipt and latch transition they write.
  - A denial is recorded only while the attempts verify. When the witness is down nothing unanchorable is written, and the denial still stands.
  - A `prepared_to_send` that cannot be anchored is never sent.
  - A recovery first completes any record a crash left unanchored.
- **The latch is an auditable chain** (`<workspace>/stop-latch/NNNNNN.json`, `LatchEvent`):
  - Transitions alternate `set` and `cleared`, are hash-linked and are never removed or replaced. A `cleared` names the owner RESUME it rests on, and the evidence that the cause was resolved.
  - The AR4b-1 single-file latch reads as set for good, and nothing clears it.
  - Execution, recovery and the bot never clear the latch.
- **STOP** (`record_stop`):
  - The newest owner record must be a STOP in force that `verify_control` accepts.
  - The latch is set at once, even while the witness is down. The STOP record and the latch transition are anchored when the witness can.
  - A STOP that is only offered also denies, through `stop_control`.
- **Owner RESUME** (`resume`) clears the latch only when all of these hold:
  - the newest owner record is a RESUME that `verify_control` accepts and that leaves the policy `not_stopped` (it names the stop it resumes, in that stop's scope, within its lease, with no later stop);
  - it extends the anchored control history exactly;
  - it was signed after the latch was set;
  - the cause is resolved, by anchored attempts and receipts:
    - no attempt is left at `prepared_to_send` or `outcome_unknown` (an unknown outcome needs AR4b-2 recovery first);
    - every receipt is `published_authorized`;
    - an unverifiable history or a receipt conflict is never resolved here;
  - the `cleared` transition is anchored.

  Refused: bot keys, forged signatures, another policy, a stale sequence, a replayed older history, a revoked owner key, a RESUME signed before the latch, a down witness.
- **Ordering and crash safety.**
  - STOP, RESUME, execution and recovery all hold the workspace lock, so they never interleave. The second of two concurrent calls is refused (`corpus_locked: workspace`) and decides nothing.
  - An execution that holds the lock first completes. A STOP recorded after it denies every later execution.
  - The only lock taken under the workspace lock is the witness's, which is a leaf.
  - Crashes:
    - after the STOP's latch is set, before its anchor: still stopped, and a retry anchors it;
    - before the clearing: still latched, and a retry resumes;
    - after the clearing, before its anchor: denied (`stop_latch:pending_anchor`) until a retry completes it.

Limitations:
- **Withdrawal after a halt is AR5c with the owner.** A foreign commit, a failed integrity check or an unverifiable history is never resumed here.
- **The budget ledger anchor, reservation and settlement are AR5c** (see the AR5c status below).
- **A STOP that is neither offered nor anchored cannot be seen.** Its effect starts once it is offered or recorded.

**AR5c status.** AR5c anchors the budget ledger and settles every verified publication exactly once. Simulation only. Production stays denied: in production `ar5_ledger_anchor` is unavailable until AR7 provides a production witness; AR6 and AR7 are unchanged.
- **Durable ledger** (`publication_ledger`, `<root>/<ledger_id>/NNNNNN.json`, outside every workspace, shared by them): the AR1b entries, created once, never replaced or removed, anchored as the `budget_ledger` family, scope `<ledger_id>`. A ledger several workspaces share names its own witness (`BudgetLedger.witness`); each workspace keeps its own witness for its workspace families.
- **Consistency model.** Only a ledger whose chain reproduces exactly the witnessed head is read or extended. Every write: under the ledger lock, verify against the witness, judge the operation with `plan_entry` on that chain (next entry, `already_applied` for an exact retry, or a refusal), create the entry `O_EXCL`, read it back, anchor it. A crash between write and anchor leaves `pending_anchor`, which blocks the ledger until the **same** operation is retried: the retry proves the pending bytes are exactly what it would append and completes the anchor. A pending entry anything else wrote is never anchored.
- **Before a simulation publishes:** `ar5_ledger_anchor` verifies the durable ledger against the witness, never waived. `budget` reads only the durable ledger: a reservation for exactly this transaction and batch, covering its usage, within the owner-signed limits and daily caps (AR1b). A missing ledger is never zero usage. Another release's open reservation, from any workspace, denies (`ledger_settlement_pending`). `publish_authorized` re-verifies, under the workspace and ledger locks it holds until the send has been recorded, the ledger anchor and this transaction's exact open reservation (not cancelled or settled, this batch, covering its usage, within the caps verified now, no other open reservation); sealed evidence never outlives its reservation (`authorization_stale:reservation:<why>`), and a cancel during the interval is refused (`corpus_locked: ledger`).
- **Ledger-wide invariant: at most one open reservation.** A reservation is open until the ledger settles (`commit`) or cancels it. It may have been sent, so its outcome is unresolved for the whole ledger: while it is open no other release, from any workspace, reserves (`ledger_reservation_open`) or is authorized (`ledger_settlement_pending`). The rule reads only the witnessed ledger, under the ledger lock, never a workspace's records or a caller's flag.
- **Reservation** (`reserve`): the transaction's exact usage for the day, anchored; a retry is `already_applied`; a conflicting reservation, another open reservation, an exceeded cap or unverified limits is refused and writes nothing.
- **Settlement** (`settle`, from AR4b-2 recovery only): the workspace's whole receipt chain is read once and verified against the witness; the receipt settled is the chain's receipt with exactly the offered receipt's canonical bytes, never the offered object. It must name this transaction, its release manifest, its path and base, a pipeline commit whose parent is that base, and be the transaction's only published receipt in the chain (else `receipt_not_in_anchored_chain`, `receipt_<field>_mismatch` or `receipt_ambiguous`). It settles the single reservation under the same batch; the `commit` entry carries the batch's exact usage, the verified remote commit and the receipt's digest, and is anchored. A second settlement naming another commit or receipt is refused (`ledger_conflicting_settlement`). An API answer or an attempt record never settles. `published_authorization_unknown` and integrity failures are withheld: the reservation stays open and the latch stops the workspace.
- **Locks.** Workspace lock, then the ledger lock (`<root>/<ledger_id>.lock`, across workspaces and processes), then the witness lock (a leaf). Nothing holding the ledger lock takes a workspace lock; the clone lock is never taken under either. A second holder is refused at once (`corpus_locked: ledger`).
- **Crashes** (each a test): before the reservation (nothing written, denied); after its append, before its anchor (denied until the same reservation is retried); after the anchor, before the send (recovery: `not_published`, one send later); after the mutation, before the response (recovery settles once); after the receipt, before the ledger commit (the next recovery settles); after the ledger commit, before its anchor (the next recovery completes it); after the anchor, before the acknowledgement (the next recovery is `already_settled`, nothing more is charged).

Limitations:
- **Withdrawal after a halt and adopting pre-existing unanchored records remain open; quarantine successors: AR5d-4, below.**
- **A withheld settlement is resolved by the owner** (AR5b RESUME does not settle it); its reservation keeps counting against the day.
- **Settlement under revoked owner limits is refused**: the publication stays unsettled and spending stays blocked until the owner signs limits again.
- **One open reservation per ledger serializes publication**: reserve, publish, settle, then the next batch. An abandoned reservation (reserved, never sent) blocks the ledger until it is cancelled; an owner-authorized cancel is not implemented here (AR5d-3 adds it, below).
- **Workspace families shared one scope per witness** (`stop_latch` and `recovery_receipts` were scoped `workspace`, #1013): workspaces could not share a witness store for them. It failed closed (a collision is a rollback or divergence), and a shared ledger therefore names its own witness. AR5d-1 removes the collision (below); the ledger's own witness stays valid.

**AR5d-1 status.** AR5d-1 gives each publication workspace an anchored identity and anchors the workspace families in its scope, so independent workspaces can share one witness store (#1013). Simulation only. Production stays denied; AR5c's ledger, settlement and reservation rules are unchanged.
- **Identity contract** (`workspace-identity/1`, `workspace_identity_models`). One canonical, immutable record, `<workspace>/workspace-identity.json`:
  - a random 256-bit `workspace_id`, drawn once;
  - the `repository` and the publication `domain` (`forarbeider-publication`);
  - the version and the creation time.

  The path is not part of it. The scope of the workspace families is `workspace:` followed by the SHA-256 of the record's exact bytes, so the repository, the domain and the random id are bound into every anchor key.
- **Trust model.** The record is a name, never a credential. It counts only once the witness has anchored it, as the one record of a new family, `workspace_identity`, in that same scope (`control_plane_anchor.workspace_scope`). Requests carry no identity: the verifier reads the file and asks the witness. Refused, with `workspace_identity:<why>` in `ar5_control_anchor`, and nothing sent:
  - a missing file (`missing`);
  - a link, a shared or hard-linked file, or a workspace that is not private (`unsafe`);
  - unreadable or non-canonical bytes, another repository or domain (`unreadable`, `not_canonical`, `repository_mismatch`);
  - a well-formed record the witness never anchored, such as a caller's claim (`unanchored`);
  - an identity whose anchor is pending or rolled back at the witness (`pending_anchor`, `local_rolled_back`, …), or an unreachable witness (`store_unavailable`).
- **Establishing** (`workspace_identity.establish`), under the workspace lock, with AR5a primitives only: anchor the empty family in the new scope, write the file once (a flushed temporary file hard-linked to the final name, never replaced), then anchor it as the family's one record. A crash before the write leaves no identity (the orphaned empty anchor names a random scope nobody holds); a crash after it leaves `pending_anchor`, which denies until a retry completes it; a retry after success is `already_established`. Leftover temporary files are removed under the lock first; a leftover hard link makes the identity `unsafe` until then. Only an **empty** workspace gets an identity (`workspace_not_empty`), and an identity file the witness does not hold is never registered again.
- **Anchor isolation.** `stop_latch` and `recovery_receipts` (through `durable_append`, `publication_anchors`, STOP/RESUME, execution, recovery and the AR5c receipt binding) are anchored and verified in the identity scope. A latch set in one workspace never stops another on the same witness (tested both ways); STOP and RESUME read and write only their own workspace's latch, in its scope. Two things stay shared on purpose:
  - `publication_attempts` stays per transaction. A transaction is content-addressed, so two workspaces attempting one transaction meet in one chain: that is the exactly-once guard, not a collision.
  - `control_history` stays per policy: an owner STOP binds every workspace of the policy. The `budget_ledger` stays per ledger.
- **Copies and relocation.**
  - A moved workspace keeps its identity and its scope; the path never mattered.
  - A copy, or the identity file copied into an empty workspace, is the **same identity**, and an identity has **one exclusive writer at a time** (below). The witness's compare-and-swap lets only one history continue: once either copy advances (a writer claim, an attempt, a receipt, a latch), the other is `local_rolled_back` or `diverged` and never re-anchored. Each case is a test, including the copy publishing first.
- **One writer per identity** (`workspace_writer`, `workspace_writer_models`; review blocker on PR #1020). A copy has its own workspace lock, and attempts are scoped per transaction, so before this two copies could send two **different** transactions, each with its own reserved ledger and its own remote. Now every send first claims the identity's writer slot at the witness:
  - a new family, `workspace_writer`, in the identity scope: an append-only chain of `claim` (random `claim_id`, transaction, attempt, time, lease end) and `release` records, anchored empty by `establish` before the identity file is written;
  - `claim` requires the chain to be exactly the anchored one, the newest claim released or its lease (300 s) over, and time not running backwards; it appends the claim and anchors it through the witness's compare-and-swap. The anchored head is the fencing token: of two copies, one claim is anchored; the other is `anchor_head_moved`, `local_rolled_back` or `diverged`, whatever its transaction or ledger;
  - right before the send, `require_current` proves the claim is still the anchored head with at least 60 s of lease left; a takeover is only possible after the lease, so a writer taken over never begins a send;
  - the claim is released after the outcome is recorded, or when a check refuses before the send.

  Writer exclusivity is told apart from, and tested separately from: same-transaction exactly-once (the attempt chain), budget serialization (a reservation per transaction), and remote expected-head protection (authorization's `remote_head` check and the remote's own `expectedHeadOid`).

  Crashes: a claim appended but not anchored is `pending_anchor` and is anchored by the next claim; a claim anchored and never released holds every writer of the identity off until its lease ends, then exactly one claim takes over and the old holder is fenced (`local_rolled_back`); a release the witness missed is completed by the next claim. A removed writer chain is `local_rolled_back`, never restarted. No identity is regenerated.
  - Another workspace's identity substituted into a workspace compares its chains with the other workspace's anchors, and is refused as soon as they differ.
- **Legacy (pre-AR5d) workspaces and anchors.**

  | Case | Behavior |
  |---|---|
  | Workspace with no identity | denied (`workspace_identity:missing`); nothing is written or anchored |
  | Establishing on a workspace holding records, a latch or receipts | refused (`workspace_not_empty`); records, latch and legacy anchors untouched; no STOP is reset |
  | Legacy anchors (scope `workspace`) | readable only through `legacy_verdicts`, read-only; never consulted for authorization |
  | Legacy anchors copied under an identity scope | `anchor_invalid` (`anchor_foreign`): an anchor names its scope and is never reinterpreted |
  | Nonempty family under a new identity | `unanchored`; `initialize` refuses existing records, so nothing is silently re-initialized |

  AR5d-2 (below) decided that no populated legacy workspace is migrated. No anchors exist outside tests.
- **Concurrency** (real processes): four processes establishing one workspace make exactly one identity (the others are refused `corpus_locked: workspace` or confirm it); two workspaces anchoring latches through one witness at once both verify; two processes publishing different transactions from two copies of one identity at once send exactly once, and the other is refused `workspace_writer:<why>`. Lock order is unchanged: workspace, ledger, witness (a leaf). Establishing takes no ledger lock.

Limitations:
- **A new workspace is a new writer.** An empty workspace with a fresh identity has empty families: a latch set in another workspace does not bind it. Workspace-wide halts belong to the owner's policy-scoped STOP; whether a halt in one workspace should block new identities for the repository is an owner decision (AR5d-2).
- **The lease is a time bound, not a remote-side fence.** A send that begins with at least 60 s of lease left and outlasts it could overlap a takeover; the simulated remotes cannot check a fencing token. The expected-head check still refuses a second commit on one remote. A production remote and witness are AR7.
- **The offline witness can be rolled back by whoever holds it** (AR5a). A truncated identity history shows as `pending_anchor`, which denies; `establish` then completes the same identity, never a new one.
- **Migrating legacy workspaces is AR5d-2**, with the owner. Decided below: none is migrated.

**AR5d-2 status.** AR5d-2 closes legacy workspace admission without a migration mechanism. Simulation only; production stays denied.
- **Inventory.** No durable publication workspace, witness store or ledger exists outside tests:
  - no CLI or deployment reaches `publication_execute`, `publication_remote_recovery`, `publication_stop_resume`, `publication_ledger` or `workspace_identity`;
  - a search of the owner's machine (home directory, the external volume) found no `workspace-identity.json`, `stop-latch.json`, `stop-latch/`, `workspace-writer/` or `workspace.lock` outside pytest temporary folders;
  - the hosted droplet runs the MCP server only.

  Every legacy state (a workspace without identity, anchors under the literal scope `workspace`, historical attempts and receipts, the AR4b-1 `stop-latch.json`, pending anchors, quarantine and budget histories) is therefore a test fixture. **No migration framework is built.** If real legacy state appears, it stays denied until the owner decides.
- **Policy.**
  - A populated legacy workspace is never migrated, and its evidence is kept read-only.
  - New identities come only from `establish`, only for an empty workspace.
  - Historical records are never anchored under a new identity.
  - STOP, pending outcomes and open reservations are never cleared silently.
  - An ambiguous state stays denied.
- **`establish` completes the identity.** Step 1 now anchors every family of the new scope empty (`stop_latch`, `recovery_receipts`, `workspace_writer`; `workspace_identity.SCOPED`), while the workspace is locked and provably empty. Before this, a workspace given its identity by `establish` alone was denied `stop_latch:unanchored`: the families were anchored only by the test rig, through the raw `initialize` primitive.
- **Legacy evidence is never anchored as current** (`control_plane_anchor.legacy_evidence`, checked in `anchor_pending`, the one path that completes a pending record). Suppose an identity file is copied by hand next to legacy state. The legacy latch or receipt is then one record ahead of the new scope's empty anchor, so it reads as `pending_anchor`, just like a crash's record. Before this check, recovery or a durable append would anchor it: the copy took over the identity's latch, and the identity's own workspace became `local_rolled_back`. Now a record that the witness holds as a legacy head, under the literal scope, is refused (`anchor:legacy_evidence`) and stays pending, so the copy is denied. A legacy history that cannot be proved (unreadable, unsigned, unavailable) is never taken to mean there is none.
- **A workspace without an anchored identity is read-only** (`publication_anchors.identified`), whenever a witness is given:
  - a denied execution writes no attempt record and sets no latch. Before this, it appended a `denied` attempt and anchored it in the transaction's chain, which every workspace shares;
  - recovery is refused (`workspace_identity:<why>`) before it completes a pending record, writes a receipt or attempt, or anchors anything.

  An owner STOP is the one exception: `record_stop` still sets the latch locally (fail-safe; it can never be anchored there), and RESUME stays refused (`workspace_identity:missing`). Requests without a witness are AR4's: they anchor nothing.
- **Admission matrix.**

  | State | establish | execute / publish | recovery | STOP / RESUME | writer claim | ledger |
  |---|---|---|---|---|---|---|
  | Empty workspace | establishes; all families anchored empty | admitted | admitted | admitted | admitted | per ledger |
  | Populated, no identity (legacy) | `workspace_not_empty`; nothing touched | denied (`workspace_identity:missing`); nothing written | refused; nothing written | STOP sets the local latch; RESUME refused | refused | cannot settle (no anchored receipts) |
  | AR4b-1 `stop-latch.json` | `workspace_not_empty` | denied (`stop_latch_legacy_file`) | refused | refused | refused | n/a |
  | Identity copied by hand next to legacy records | not called | denied (pending or tail at the witness); the identity's own workspace still publishes | runs; the legacy record is never anchored | latch stays pending | `local_rolled_back` once the original claims | n/a |
  | Legacy anchors copied into an identity scope | n/a | `anchor_invalid` (`anchor_foreign`) | refused | refused | n/a | n/a |
  | Missing, corrupt, forged or unanchored identity | refused with its reason | denied | refused | RESUME refused | refused | n/a |
  | Another workspace's open reservation or attempts for the transaction | n/a | denied (`publication_attempts:local_rolled_back`); the ledger is unchanged | per remote evidence only | n/a | n/a | never cleared |

- **Not covered** (an owner decision, below): a legacy record that the witness **never** anchored is indistinguishable from a crash's record, if someone copies an identity file next to it by hand. Records carry no identity, so only a schema change could tell them apart. The effect is bounded:
  - a single such record can be completed under the copied identity;
  - from then on the identity's own workspace is `local_rolled_back`, and is therefore denied;
  - a latch so adopted only halts;
  - a receipt so adopted is re-derived from the remote by every recovery, and halts on conflict.

Owner decisions left open:
- whether to bind workspace records to their identity (a new receipt and latch schema), so that even legacy records the witness never anchored can be refused;
- what to do if a real legacy workspace is ever found: it stays denied until then;
- a repository-level halt for new identities (from AR5d-1).

**AR5d-3 status.** AR5d-3 lets the owner cancel an abandoned reservation, only on proof that nothing was ever sent, and keeps every withheld settlement withheld. Simulation only; production stays denied. Routine settlement stays automatic (AR4b-2 recovery, AR5c): nothing here asks the owner about a normal publication.

- **One reservation's states** (the ledger and the transaction's anchored attempts together):

  | From | Event | To | Who |
  |---|---|---|---|
  | none | `reserve` | open, never sent | automatic |
  | open, never sent | `prepared_to_send` anchored (under the workspace and ledger locks) | open, sent | automatic |
  | open, never sent | owner instruction and the proof below | **cancelled** | owner, AR5d-3 |
  | open, sent | recovery: `published_authorized` | settled | automatic (AR5c) |
  | open, sent | recovery: `not_published` | open, sent; a retried send is safe (the remote's expected head refuses a duplicate) | automatic |
  | open, sent | recovery: authorization unknown, integrity failed, revoked limits, receipt conflict | open, withheld; the latch is set | automatic halt |
  | open, withheld | an owner decision | not implemented: design below | owner |
  | cancelled, settled | anything | terminal; never reserved again (AR1b) | — |

  **"Open, sent" never becomes "cancelled".** After a send begins, an unchanged remote proves nothing: the request may still be in flight, its response may be lost, or its process may have died after the mutation. A timeout, a lapsed lease or a missing answer is never evidence of nonpublication. Such a reservation is resolved by recovery, and a retried send if need be.
- **Trust boundaries.**
  - Trusted: the owner key (control namespace, owner role, trust root) and the witness (AR5a).
  - Verified here, never trusted: everything the caller passes, every workspace record (attempts, receipts, writer claims, identity), the ledger and the remote.
  - **The authoritative attempt history** (review fix): a transaction reserved in a ledger has one attempt history, anchored on the ledger's witness, the witness that anchors its reservation (`publication_anchors.attempts_witness`). A workspace that keeps its own witness for its own families (identity, latch, receipts, writer, control history; AR5c) still anchors the transaction's attempts there. A send begins only once its `prepared_to_send` is anchored on that witness (AR4b-1), so a writer that cannot anchor there, because the witness is unavailable or because it names another one, never sends. The cancellation reads the same history: nothing a caller names about witnesses is compared, and a substituted ledger witness holds no anchored ledger, so step 7 refuses.
- **The proof** (`publication_cancel.cancel_reservation`), all read under the workspace lock, then the ledger lock, then the witness (a leaf), and held until the last write:
  1. the workspace identity is the anchored one (AR5d-1);
  2. the instruction verifies (below) and binds this repository, ledger, transaction, batch, path, base and identity scope;
  3. the transaction's attempts are read on their authoritative witness, the ledger's (above), where every sender of the transaction, on whatever witness its own families are kept, must anchor its `prepared_to_send` before sending;
  4. the transaction's attempts verify against that witness and hold no `prepared_to_send`, `outcome_unknown` or `published` (`cancel:send_initiated:<state>`); their head is the one the owner signed. A copied workspace, or another workspace, on any witness, that began a send, is `local_rolled_back` or `diverged` there (`cancel:anchor:publication_attempts:<status>`);
  5. no receipt for the transaction is in the anchored receipt chain (`cancel:receipt_on_record`);
  6. no writer of the identity can be live: the writer chain verifies, and its newest claim is released or lapsed (`cancel:workspace_writer:claim_held`). The lapse only shows that the writer can no longer begin a send; step 4 shows that none began;
  7. the ledger verifies against its witness; its head and the reservation's bytes are the ones the owner signed; the reservation is open and names this batch (`instruction_stale:ledger_head`, `instruction_reservation_mismatch`, `reservation_not_open`);
  8. the remote `main` is the base, read before and after the walk (`cancel:remote_advanced`, `cancel:remote:<why>`).
- **Writes.**
  1. A `refused` attempt record carrying `reservation_cancelled_by_owner` and the instruction's digest, anchored for the transaction. It is the durable decision and a fence: `execute` returns `refused`, and `publish_authorized` refuses (`reservation_cancelled`), without sending.
  2. The AR1b `cancel` entry, appended and anchored through the ledger's own write path.

  `publication_ledger.record` now refuses a `cancel` (`ledger:cancel_needs_owner_instruction`). The cancellation clears no latch and no STOP, settles nothing and permits no publication.
- **Owner instruction** (`ReservationCancellation`, namespace `lovspor-forarbeider-control`, its own record type and domain):
  - Binds: the operation (`cancel_abandoned_reservation`), repository, ledger id, transaction, batch, path, base commit, the reservation entry's SHA-256, the ledger head's SHA-256, the attempts head's SHA-256 (or none), the identity scope, a reason, an instruction id, the signer, `signed_at` and `valid_until` (at most 24 hours later). There is no wildcard.
  - Refused: an unlisted key, a bot key (it cannot hold the namespace), another namespace, a STOP or other record offered in its place, expired, signed after the check time, valid for more than a day, or bound to anything else.
  - Stale: evidence that changed after signing (ledger head, attempts head).
  - Replay: the same instruction completes or finds its own cancellation (`already_cancelled`). Another instruction for a cancelled reservation is refused. An instruction cannot apply elsewhere: it binds one reservation, and a cancelled release is never reserved again.
- **Races.**
  - Send against cancellation, in-process and across processes, from this workspace, from another on the same witness, or from one on its own witness sharing the ledger: both hold the workspace lock (same workspace) or the ledger lock (shared ledger) from their first check to their last write. The second is refused (`corpus_locked`); then it finds the first one's records (a cancellation fences the send; a begun send refuses the cancellation).
  - Sealed evidence issued before a cancellation never sends (the fence, then AR5c's reservation recheck).
  - Copied identities: step 4 above, and AR5d-1 writer fencing.
  - **Remote mutation between the check and the cancel entry.** Only a send of this transaction could publish it. Every send anchors `prepared_to_send` first, under the ledger lock this cancellation holds, and step 4 proved that none exists. So no request for it exists anywhere, and none can start once the reservation is cancelled. A foreign commit in that interval is not this transaction's: the next recovery halts on it. No atomic remote read is needed, because no sender exists.
- **Crash matrix** (each a test, both paths). In every case nothing is sent before the retry, the retry ends `cancelled` or `already_cancelled`, and there is exactly one decision record and one anchored `cancel` entry.

  | Crash point | State left | Retry |
  |---|---|---|
  | during the remote verification | nothing written | proves again, cancels |
  | after the decision record, before its anchor | attempts `pending_anchor`; a send is denied | completes the anchor, cancels |
  | after the anchor, before the ledger entry | the reservation is still open, and the fence stops every send | appends the entry |
  | after the ledger entry, before its anchor | ledger `pending_anchor` | completes it (the same operation) |
  | after the anchor, before the acknowledgement | done | `already_cancelled` |

- **Withheld settlement: unchanged, and never cancelled.** A transaction that was published, or whose outcome is unknown, is refused by step 4 or 5. Its reservation stays open, so it keeps counting against the day, and the latch keeps the workspace stopped:
  - `published_authorization_unknown`;
  - an integrity failure;
  - revoked owner limits (nothing can be appended under unverified limits, a cancel included);
  - a conflicting receipt;
  - an unknown remote outcome.

  The four facts stay apart:
  - A, what happened remotely: the receipt;
  - B, whether it was authorized: the receipt's classification, never rewritten;
  - C, budget: the open reservation;
  - D, further publication: the latch and STOP.

  No owner signature relabels B.
- **Why exceptional accounting is a design, not code.** Closing a withheld reservation means recording budget consumed by a publication that was not proved authorized. The v1 ledger can say this only as a `commit`, which `verify_ledger` reads as a routine settlement (`settled`). A reader of the ledger alone could not tell an owner exception from a verified, authorized publication. The entry also cannot name the owner instruction: every field is in the canonical bytes, so adding one changes every entry. Revoked limits are worse: each entry binds the limits' digest, so new limits make the whole ledger fail replay (`ledger_limits_mismatch`). **Bounded proposal (ledger schema v2, owner decision):**
  - a `consume` operation, distinct from `commit`, naming the receipt's digest, its classification as recorded, the owner instruction's digest and the basis `owner_exception`. It closes the reservation at the batch's exact usage, and `verify_ledger` reports it apart from settled usage;
  - an owner instruction `account_withheld`, bound like the cancellation to one receipt and one reservation, which neither relabels authorization nor clears a latch;
  - RESUME unchanged: an unknown or unauthorized receipt still blocks it until a separate owner decision (acknowledge, or withdraw forward-only);
  - ledger succession under new limits: a new ledger whose first entry names the predecessor's head and carries its open reservations over;
  - (no longer needed: the review fix anchors every reserved transaction's attempts on the ledger's witness, which makes every begun send of a shared ledger visible wherever it is cancelled.)

Limitations and remaining blockers:
- **Closed by the review fix: a sender on another witness.** Before it, AR5c let a workspace anchor a reserved transaction's attempts on its own witness, so a cancellation on the ledger's witness could not see that sender's `prepared_to_send` (reproduced red: an in-flight send from such a workspace was cancelled). The attempts now live on the ledger's witness; the earlier mitigation (`cancel:ledger_witness_not_workspace_witness`, an owner confirmation of the topology) is removed. Residual: a sender that bypasses `execute` altogether and talks to the remote directly is outside every AR5 control; its publication after a cancellation is never settled and sets the latch (tested).
- **Trusted, not proved:** the witness. One that drops or forks a family's anchors (AR5a: whoever holds the offline store can roll it back) could hide a `prepared_to_send`; a production witness is AR7.
- Withheld settlements, revoked limits and ledger succession stay blocked until the owner decides on the v2 design.
- The decision record lives in the canceller's workspace. The ledger's `cancel` entry does not name the instruction (v1 schema).
- Not in this PR: a global halt, owner cancellation of a sent reservation, quarantine successors, AR6 and AR7.

**AR5d-4 status.** AR5d-4 adds anchored, independently re-provable successors of the AR3 quarantine ledger. Simulation only; production stays denied; nothing clears a quarantine automatically.

- **Before.** AR5a anchors the ledger as one document (`quarantine_ledger`). A changed document is `diverged` and can never be anchored; a signed anchor over another document is `anchor_invalid` (`quarantine_successor_unverified`). That family is unchanged: an old anchor never authorizes a changed document.
- **New family** `quarantine_succession` (`forarbeider_quarantine_succession/1`, scope = ledger id), anchor schema unchanged (`control-plane-anchor/1`). Its records are `QuarantineSuccession` entries in `<root>/<ledger_id>/NNNNNN.json`, created once (AR5c storage primitives), linked by the digest of the entry before. Each entry carries:
  - the predecessor's digest (and, for a first entry continuing a `quarantine_ledger` document, that document);
  - the successor document and its digest;
  - the exact signed owner exceptions and revocations it relies on (record bytes and detached signature), the policy digest they were judged under, and the judgement time.
- **The proof of one transition** (`quarantine_succession.transition_reasons`), replayed by every reader and by the writer before it writes, with the real AR3 engine:
  1. the predecessor's digest is the one named; both documents are canonical AR3 ledgers;
  2. `successor_findings` is empty: no record dropped, no history rewritten, no exclusion dropped (AR3 has no justified removal, so none is accepted);
  3. the named policy is one the reader holds, by digest (`policy_unavailable`);
  4. `verify_exceptions` re-verifies every carried exception and revocation against the **reader's** trust root at the judgement time; a signature failure fails the proof (`exception:quarantine_exception_untrusted`), so a key revoked later fails every later read;
  5. every new `cleared` event names a carried, verified clearance that `clears` the record as it then stood (policy, version, evidence, target); every new exclusion names a verified exclusion of that document (`unjustified_clearance`, `unjustified_exclusion`);
  6. the successor is a fixed point of `settle` under exactly those exceptions (`not_settled_under_the_verified_exceptions`): a stored `cleared` without a verified clearance is never accepted;
  7. time does not run backwards (`clock_regressed`, `event_after_the_judgement`).
- **Genesis.** A first entry either starts from the empty ledger, which requires that `quarantine_ledger` holds no document for the scope (verified empty or unanchored), or continues the document anchored there, which must be `verified` with exactly that document. A document anchored there after the chain began breaks the chain (`genesis:quarantine_ledger:local_rolled_back`): it is never silently adopted, and anchored history is never discarded.
- **Reading** (`read_quarantine`): the chain must be `verified` against the witness (a pending entry is untrusted), the genesis must agree, every transition is replayed. Anything else refuses (`quarantine_succession:<why>`).
- **Writing** (`append_successor`), under the store lock (a leaf above the witness lock; no workspace or budget-ledger lock is taken):
  1. read and verify the chain; anchor an unanchored, empty chain empty first;
  2. the caller's expected predecessor must be the verified head (`predecessor_not_the_anchored_head`): one successor per anchored predecessor; the same successor again is `already_applied`;
  3. prove, create (`O_EXCL`), anchor; the witness's compare-and-set refuses a competing anchor.
- **Crash and race matrix** (each a test):

  | Case | Result |
  |---|---|
  | crash before the create | nothing written; retry `appended` |
  | crash after the create, before the anchor | `pending_anchor`: readers refuse; a different successor or another time is `pending_entry_is_another_successor`; the exact retry `completed`, then `already_applied` |
  | two processes, two successors of one predecessor | exactly one `appended`; the other `corpus_locked: ledger` or `predecessor_not_the_anchored_head` |
  | a copied store adds a competing successor | `anchor:local_rolled_back` |
  | witness unavailable | `anchor:store_unavailable`, nothing written; readers refuse |
  | witness rolled back | readers refuse (`anchor:pending_anchor`) |
  | local entry deleted, edited or truncated | `anchor:local_rolled_back`, `anchor:local_invalid`, `store:ledger` |
  | an entry the witness signed but whose proof fails | `entry_<n>:<reason>` on every read |
  | a fresh process with only the files, the witness and the trust root | proves the same head |

Limitations:
- The witness is trusted, not proved (AR5a): one that drops or forks anchors could hide a successor; a production witness is AR7.
- The writer needs every policy the chain's entries name, to replay them (matched by digest, never trusted).
- No AR3 path removes a quarantine record, so no justified removal exists; an owner decision would be needed to add one.
- `run_quarantine` still takes the previous ledger's bytes from its caller, for independent AR3 use; the publishing pipeline reads quarantine only through `read_quarantine` since the AR5 integration gate (below).
- A global halt, AR6 and AR7 are not in this PR.

**AR5 integration gate status.** The publishing pipeline no longer relies on a caller-supplied quarantine, and every anchor is verified under the request's one trust root. Simulation only; production stays denied; no flag is enabled.

- **Before.** The planner, AR4a preparation and authorization accepted any `QuarantineEvidence` carrying the in-process `quarantine_assessment` seal. `run_quarantine` issues that seal over whatever ledger bytes its caller offers, so neither the AR5d-4 chain nor its witness was ever required. Reproduced on `7fbfedae`:
  - a document the anchored chain quarantines was sent (paths A and B);
  - a pending successor was ignored;
  - evidence issued before a successor was spent after it;
  - a witness authority under a second trust root was accepted.
- **Adapter** `publication_quarantine` (no change to AR3, the planner or the metrics):
  - `witnessed_quarantine` reads the head only through `read_quarantine` (the chain is `verified`, its genesis agrees, and every transition is replayed). It then verifies the head entry's carried owner exceptions **now**: `clock_regressed`, `exception:<halt>` (e.g. a signing key whose window has since closed), and `clearance_not_current` (`settle` is not a fixed point now: lapsed or revoked).
  - `plan_witnessed` refuses a caller ledger (`caller_ledger_refused`). It runs AR3 on the anchored head with the head's exceptions and those offered. It plans only when the run leaves the head unchanged; otherwise it returns `successor_required` with the successor and the material for `append_successor`. A lapsed clearance is reinstated through that successor, never honoured.
  - `prepare_witnessed` runs AR4a only after that binding holds now.
  - `quarantine_reasons` binds the sealed assessment's and the sealed plan's quarantine digests to the anchored head's set, and the head's policy digest to the plan's policy.
- **Authorization.**
  - New check `ar5_quarantine_anchor`: never waivable in simulation; in production `unavailable` (`quarantine_anchor_production_witness_ar7_pending`).
  - It reads the chain on the ledger's witness, when the ledger names one, as the attempts are (AR5d-3).
  - `trust_root` also requires the witness authority's root to be the request's (`anchor_trust_root_not_the_requests`).
- **Before the send** (`_still_current`, under the locks):
  - the trust roots are the evidence's (`authorization_stale:trust_root`);
  - the anchored head is still the evidence's (`authorization_stale:quarantine:<why>`).
  - The chain's lock is held from the authorization to the outcome record. Lock order: workspace, budget ledger, quarantine chain, witness. `append_successor` takes only the last two, so no successor is anchored between the final check and the send, and no send starts while a successor is written (`corpus_locked: ledger`).
- **Forged control records** (found by the gate's audit, reproduced on `7fbfedae`):
  - **#1027.** `publication_anchors.settle` anchors a pending latch `cleared` event only on proof (`clearing_reasons`):
    - it names the newest record of the request's owner control history;
    - that history verifies at the clearing's time as a RESUME that leaves the policy not stopped;
    - that history is itself anchored;
    - the RESUME was signed after the `set` it clears.
    A hand-written clearing stays `pending_anchor`, so recovery and `resume` never adopt it and the send is denied.
  - **#1028.** The cancellation resume path accepts its decision record only when it directly follows the attempts head the owner signed, with no `prepared_to_send`, `outcome_unknown` or `published` before it (`cancellation_record_not_the_owners`).
  - **#1029.** The owner's control history is verified and anchored on its authoritative witness: the ledger's, when the ledger names one (`control_authority_of`), as the attempts are (AR5d-3). A STOP recorded from any workspace sharing the ledger binds every other one, and a workspace on another witness that withholds the head is denied (`control_history:local_rolled_back`).
    - Default chosen for the owner to confirm or reject in review. The alternative, one pinned control witness per repository, needs AR7's pin.
- **Bootstrap.** Each operation is explicit and runs once; none adopts or resets a history.
  - `initialize_quarantine` anchors one ledger's empty chain, explicitly and once.
  - It refuses entries on disk, an anchored chain (`already_anchored`) and a document anchored under `quarantine_ledger` (`genesis:quarantine_ledger:<status>`); such a document is continued by the first `append_successor`.
  - Without it, readers refuse `anchor:unanchored`: a fresh deployment cannot publish until the owner bootstraps.
  - `publication_anchors.initialize_attempts` anchors one transaction's empty attempts on their authoritative witness (the ledger's, AR5d-3). Records on disk or an anchored chain refuse it.
  - `publication_ledger.initialize_ledger` anchors one budget ledger's empty chain on its witness. Entries or an anchored ledger refuse it.
  - `publication_anchors.initialize_control` anchors one policy's empty control history on its authoritative witness. Any anchored chain refuses it. `sync_control` then anchors the owner's records.
  - The test rig now bootstraps through these operations.

| Component | Authority | Verified at | Failure mode | Tests | Status |
|---|---|---|---|---|---|
| Quarantine membership | AR5d-4 chain on the ledger's witness, owner exceptions re-verified now | `plan_witnessed`; `authorize` (`ar5_quarantine_anchor`); `_still_current` | refuse or deny; `successor_required` | `test_forarbeider_quarantine_integration` | fixed here |
| Planning and batch preparation | the anchored quarantine head | `plan_witnessed`, `prepare_witnessed` | `successor_required`; refuse, nothing staged | `test_forarbeider_quarantine_integration` | fixed here |
| Anchor trust root | the request's trust root | `authorize` (`trust_root`); `_still_current` | deny; `authorization_stale:trust_root` | same | fixed here |
| Plan, policy approvals, limits | owner signatures, trust root | `authorize` | deny | `test_forarbeider_publication_execute` | existing |
| STOP/RESUME, latch | owner records anchored on the ledger's witness; latch anchored, a clearing only on an anchored owner RESUME | `authorize`, `_still_current`, `settle` | deny; latch; clearing `pending_anchor` | `test_forarbeider_publication_stop_resume`, `_stale_authorization`, `_forged_control_records` | #1027 and #1029 fixed here |
| Workspace identity, writer | anchored identity and writer chain | `authorize`, `claim`, `require_current` | deny; refuse | `test_forarbeider_workspace_identity`, `_workspace_writer` | existing |
| Attempts, receipts | anchored on the ledger's witness (AR5d-3) | `execute`, recovery | `recovery_required`; latch | `test_forarbeider_publication_remote_recovery`, `_recovery_races` | existing |
| Budget ledger | durable, anchored | `authorize`, `_still_current` | deny | `test_forarbeider_publication_ledger` | existing; ledger identity unbound to limits: ledger v2 |
| Cancellation | owner instruction and proof; a resumed decision bound to the signed head | `cancel_reservation` | refuse | `test_forarbeider_reservation_cancellation`, `_cross_witness_cancellation`, `_forged_control_records` | #1028 fixed here |
| Bootstrap | the owner's explicit operation | `initialize_quarantine`, `initialize_attempts`, `initialize_ledger`, `initialize_control` | refuse any existing history | `_quarantine_integration`, `_forged_control_records` | fixed here (quarantine, attempts, ledger, control history) |

Remaining gaps, classified:
- **Fixed here:** #1027, #1028 and #1029 (above), and the bootstrap of every family: quarantine, `publication_attempts`, `budget_ledger` and `control_history`.
- **Owner confirmation:** #1029's witness choice (the ledger's) is a default, to be confirmed or rejected in review.
- **Production-only (AR7):** the witness pin, a production witness and an unpinned trust root (FC7b). `AuthorizationInputs.ledger` is read only without a durable ledger, which simulation denies.
- **Ledger v2 / exceptional accounting:** ledger identity bound to the owner-signed limits; anchored policy and limits revocations.
- **AR4a at the publishing boundary.** `prepare_witnessed` chooses a batch's membership only once the sealed assessment and the sealed plan are proved to be the anchored head's quarantine now (`quarantine_witness:prepare:<why>`, nothing cloned or staged). The raw AR2 planner (`plan_release`) and AR4a `prepare_publication` stay as they are for independent use, as `run_quarantine` does for AR3. The publishing pipeline enters through `plan_witnessed` and `prepare_witnessed`, and nothing prepared from another quarantine can be authorized or sent (`ar5_quarantine_anchor`, the final check).

**Migration:** the manual B1b path stays for exceptions and for the period before AR1–AR8 land. No release has happened yet [O], so nothing published needs migrating.

**Rollback:** the stop file, then revoking the policy, then revoking the credential. Published commits are withdrawn forward only (§9.7).
