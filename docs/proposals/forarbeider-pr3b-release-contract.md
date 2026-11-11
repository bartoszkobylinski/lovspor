# Forarbeider PR3B: evidence-backed release and opt-in corpus contract

**Status:** Proposed implementation contract — owner review required; **not** publication clearance  
**Date:** 2026-11-01  
**Authority:** Accepted [ADR-0018](https://github.com/bartoszkobylinski/lovspor-notebook/blob/main/docs/adr/ADR-0018-government-forarbeider-corpus.md), [ADR-0017](https://github.com/bartoszkobylinski/lovspor-notebook/blob/main/docs/adr/ADR-0017-source-backed-legislative-crosswalk.md)  
**Tracking:** [#738](https://github.com/bartoszkobylinski/lovspor/issues/738); [PR1 #741](https://github.com/bartoszkobylinski/lovspor/pull/741), [PR2 #752](https://github.com/bartoszkobylinski/lovspor/pull/752), [PR3A #754](https://github.com/bartoszkobylinski/lovspor/pull/754)  
**Division of responsibility:** Owner chooses release policy, authorizes each live pilot and merges. Architecture reviewer owns this contract and independent code review. Coding agent implements bounded PRs.  
**Amended:** 2026-11-09 by owner decision. See [Amendment 1](#amendment-1-2026-11-09-standing-policy-automatic-clearance-batch-release). Where it conflicts with the text below, the amendment wins.

## Amendment 1 (2026-11-09): standing policy, automatic clearance, batch release

**Why.** The real-document pilot ran on 2026-11-09 ([#738 comment](https://github.com/bartoszkobylinski/lovspor/issues/738#issuecomment-6739553217)). It covered Prop. 112 L and Prop. 111 LS, six chapters in all. It proved that the extractor and every source proof work on real pages, nynorsk included. It also proved that the per-chapter ceremony cannot scale to a corpus. That ceremony is evidence notes, a dossier and a typed phrase for every chapter. The owner decided to replace it. Forarbeider are built to be published, and the release process must make that routine. It must not turn each chapter into a legal ritual.

**The model.**

1. **Standing policy, signed once.** One owner record covers one source and the document types it names, today regjeringen.no `prop-l` and `prop-ls`, for one validity window. It binds, by SHA-256 of private owner-only files, each kind exactly once and no file for two kinds:
   - the source-rights assessment: website terms, åndsverkloven § 14 and § 24 (database right), for the intended method and volume;
   - the personal-data assessment: the owner-approved processing basis under personopplysningsloven for publishing these chapters, and the owner's assessment of the personal data a chapter can still hold when no detector fires. **Detector silence is not proof that a chapter holds no personal data.** This assessment is what accepts that residual risk for clean chapters; without it there is no clearance;
   - the publication purpose, with the takedown and correction contact (`kontakt@lovspor.no`) and who answers it; the contact may live in the purpose file;
   - retention and deindexing;
   - the owner's acceptance of Git-history permanence;
   - the owner's explicit acceptance that a chapter the detectors do not flag is published without a person reading it, as **its own file**. A value set in code is not this acceptance;
   - the robots.txt and website-terms pages the owner read, as the exact bytes `forarbeider-fetch` stored (point 8), never a browser copy, together with the terms page's URL. The URL is part of the signed record: a current proof must read the terms from the same URL, so an unchanged copy of old terms served at another address cannot stand in for the page the owner read. Sign makes no request and cannot verify that the terms file came from that URL; the pairing is the owner's assertion.

   Signing happens at a terminal and makes no network request. Before the prompt the command prints the source, the document types, the validity window, every bound file's kind and SHA-256 prefix, and the first 12 hex digits of the SHA-256 of the canonical policy record. The typed phrase contains that digest prefix, so any change to any bound byte changes the phrase, and a wrong phrase writes nothing. A typed phrase at a terminal is the owner's act, not cryptographic identity; the record claims no more.

   **At most one effective policy.** Two policies for the same source whose document types intersect and whose windows overlap never both count. Sign refuses while such an unrevoked policy exists; the owner revokes it first. Records are immutable: sign never overwrites or replaces one. Revocation is final and revokes exactly one record; because no second overlapping record can be signed, revoking a policy never brings another back into effect. If two unrevoked overlapping records are nevertheless found (a hand-placed file), every evaluation for that source DENYs.

   A policy must be signed again when it is revoked, when it expires, and when the current source proof (point 8) no longer matches it.
2. **Automatic per document.** Every existing source proof stays, unchanged:
   - identity, session, chapter URL and ordering;
   - page re-parse and byte reproduction;
   - source and text hashes;
   - extractor and normalizer versions;
   - collision and cross-document checks.

   Each chapter is then classified from its extractor `hold_reasons`:
   - **clean**: no reason except `review_required`;
   - **withheld**: any body-withholding reason (annex, table, image, footnotes, hidden or repaired markup, and the rest of that set). Never published;
   - **flagged**: a body exists but a detector fired (quotation, third-party work, hearing participants, personal data, sensitive data);
   - **unknown**: any reason not classified in code is treated as flagged.
3. **Owner only on exceptions.**
   - A clean chapter is cleared by the standing policy, and only while the policy is effective and a current source proof matches it (point 8).
   - A flagged chapter is cleared only through the existing PR3A review, dossier and authorization path, ending in an eligible preflight report: individual human review of exactly that chapter. That path is kept intact for exactly this purpose.
   - A withheld chapter is never cleared.
4. **One confirmation per batch.** A batch of at most 25 documents is planned privately, writing nothing but the private plan record in the owner-only workspace. It is then applied after one typed owner phrase that names the counts and the target branch. The stored confirmation is the owner's dated, written sign-off for exactly those documents and chapters. The plan also lists a small deterministic sample of clean chapters with their source links. ADR-0018 C3 requires sampling, and this sample serves it as an optional owner spot check, not a gate.
5. **Apply writes a working tree, never history.** Apply writes only `forarbeider/` in a dedicated lovverk release clone, on a non-`main` branch. It never writes the sync clone (`~/.cache/lovverk`) or the engine. It runs no `git add`, commit or push. The owner commits, pushes and opens the lovverk PR, and **merging that PR is the publication**. Forward-only withdrawal ships with or before apply.
6. **Explicit-list fetch.** A supported command, `lovspor ops forarbeider-fetch`, fetches an owner-supplied list of Prop. L / LS landing and `?ch=N` chapter URLs on `https://www.regjeringen.no` into private staging:
   - it refuses the whole list before any request if one URL is outside that grammar;
   - it never follows links, walks a sitemap or discovers pages, and caps each run;
   - it reads robots.txt live at the start of every run (point 8) and never requests a URL robots.txt disallows;
   - it spaces requests at least 5 s apart, longer when robots.txt sets a longer `Crawl-delay`;
   - a 403 or 429 on any request stops the run; later URLs are recorded as not attempted;
   - it follows no redirect for a document page, and stores no body over its size cap;
   - it sends one identifying User-Agent with a contact address;
   - it stores pages owner-only under their SHA-256, in a directory outside every Git checkout;
   - **exact bytes as served** means the response content with no content coding: every request sends `Accept-Encoding: identity`, and an uncoded body is stored and hashed byte for byte as it arrived (HTTP/1.1 chunked framing is not content; no charset decoding or normalisation). A body that still carries a `Content-Encoding` other than `identity` is never decoded and never stored: its digest would depend on the server's compressor, and inflating it defeats the size cap. A page records `content_encoded`, robots.txt makes the run unreadable, the terms read as unreadable. This is the "raw-response SHA-256" of ADR-0018.

   This is not harvesting. Each URL is named by the owner.
7. **Manifest records how a chapter was cleared.** Each chapter carries `cleared_by: standing_policy | owner_review` and the policy digest, in manifest schema 2. No reader may present a policy-cleared chapter as individually human-reviewed.
8. **Current source proof.** Automatic clearance needs proof of what the source publishes *now*, not of what the owner once read. A local evidence file that still hashes to its signed digest proves only what was signed.
   - **robots.txt** is read by every `forarbeider-fetch` run from `https://www.regjeringen.no/robots.txt`, without automatic redirects. A 3xx is followed by hand for at most 5 hops, each to an `https` URL on `www.regjeringen.no`, each spaced like any request; a missing `Location`, any other host or scheme, or a sixth hop makes robots.txt unreadable. The body of a redirect is never parsed. Every hop's URL and status are recorded. The answer is then one of:
     - **published** (200): the exact body as served, at most 512 KiB, is stored owner-only under its SHA-256;
     - **refused** (403, 429): the run stops before any document request;
     - **absent** (any other 4xx, the usual 404): no rules are published (RFC 9309), so the fetch may proceed; nothing is stored, and an absent robots.txt is never evidence for a policy;
     - **unreadable** (5xx, transport failure, bad redirect, oversized body): the run stops before any document request.
   - **Website terms** are fetched in the same run from one owner-named `https://www.regjeringen.no` page (`--terms-url`), under the same robots.txt and spacing, following no redirect. A 200 stores the exact body; a 403 or 429 stops the run; any other 4xx records the terms as absent; a 3xx, 5xx, transport failure or oversized body records them as unreadable. The bytes are hashed raw, as served, with no normalisation: a cosmetic change to the page reads as changed terms and denies until the owner re-signs. That cost is the price of failing closed.
   - The run record carries, for robots.txt and for the terms page: the requested URL, every redirect hop, the final status, the state above, the retrieval time, and for a stored body its SHA-256 and size. A reader recomputes the SHA-256 of the stored bytes and refuses a mismatch.
   - **Comparison.** An evaluation for automatic clearance consumes one validated current source proof: a fetch run record whose stored robots.txt and terms bytes re-hash to their recorded digests, retrieved at most 24 hours before the evaluation. It clears only if robots.txt is published and its digest equals the policy's robots evidence, and the terms are published and their digest equals the policy's terms evidence. A missing or stale proof, a robots.txt or terms page that is absent, refused or unreadable, a digest that differs, or stored bytes that do not match their record each **DENY automatic clearance**. The flagged-chapter path is unaffected.
   - The proof must also be a record a fetch run writes: robots.txt under its kind and canonical URL, a redirect chain that starts there and chains hop to hop on `https://www.regjeringen.no` (at most five redirects), terms from a URL inside the `--terms-url` grammar with at most one request, a final status that matches the recorded state, times that do not run backwards and the fetcher's own User-Agent. Anything else DENYs by refusal, and so does a terms URL that differs from the policy's.
   - **Integrity, not authentication.** These checks prove a proof is intact and internally consistent. Nothing in it is signed by regjeringen.no, so it never proves the server sent those bytes; anyone who can write the owner-only staging directory can write a consistent record. The trust boundary is that directory and the owner running the fetch.
   - `policy sign` and `policy show` make no network request; current proof comes only from a fetch run.
   - **Robots access never grants reuse rights.** The rights basis is the signed assessment under åndsverkloven § 14 and § 24; robots.txt only governs whether the fetcher may request a page.

**Superseded by this amendment:**
- PR3B-1 points 3–5 as a precondition for *clean* chapters: the per-chapter PR3A approval, the chapter-specific dossier and the per-chapter owner decision. These still apply to *flagged* chapters.
- PR3B-1's "NO ... automatic approval" and *Deliberately deferred* "auto-approval": clearing a clean chapter under an effective standing policy with a matching current source proof is the owner-approved automatic approval. Every other form of automatic approval stays forbidden.
- The sentence "legal sufficiency requires an owner/human decision, not keyword detection." The owner has made that decision once, at the policy level, and accepts detector-based clearance for unflagged chapters.
- "A no-personal-data pilot still requires an explicit written case-specific review" (PR3B-1 point 4 and *Release controls*). For clean chapters it is replaced by the policy's personal-data assessment (point 1); flagged chapters keep the case-specific path.
- PR3B-2's "No scheduler, crawler, CI deployment or unreviewed batch operation may trigger it": a batch is reviewed by its one owner confirmation (point 4). Schedulers, crawlers and CI still may not trigger a release.
- *Release controls not delegated to automated code*: the owner records those items once, in the standing policy, instead of per pilot. "A bounded pilot population" becomes the per-batch cap, and the "separate written release authorization" is the batch confirmation for exactly its documents.
- In *Deliberately deferred*, "automated harvesting" and, in PR3B-2, "no live government fetch": the explicit-list fetch in point 6 is carved out. Sitemap walks, discovery and crawling stay deferred.
- Acceptance-matrix rows "Missing PR3A decision", "Dossier lacks …" and "No scoped owner release decision": they now read DENY *for a flagged chapter*. For a clean chapter the equivalent DENY is a missing, revoked, expired or ambiguous standing policy, or a missing, stale or non-matching current source proof. Row "PR3B-1 against any real checkout: zero writes" still holds for preflight and plan. Apply writes only after the batch confirmation.

**Unchanged:**
- the isolated `forarbeider/` dataset;
- `normative: false`, `source_license: null`, åndsverkloven § 14 (not NLOD), and the § 24 database-right limit;
- the NOT-enacted-law label;
- the partial-curated-excerpt marking;
- deterministic rendering;
- forward-only withdrawal with the history warning;
- no chapter text in Git outside the released dataset, in CI, issues or PRs; no robots, terms or evidence bytes in Git or CI either;
- the owner merges every lovverk release, and nothing publishes without that merge.

MCP and site exposure stay a separate slice.

**Implementation slices:**
- A1: fetch, including the current source proof of point 8.
- A2: standing policy, classifier, batch plan, manifest schema 2. A2 ships as A2a (the policy record and its evaluation against a current source proof; publishes nothing) and A2b (classifier, batch plan, manifest schema 2).
- B1: release-checkout guard, batch apply, real withdrawal, takedown runbook.
- B2: receipt-verified reader of a real release checkout.

Each slice is one PR, or the named sub-slices above, TDD, synthetic fixtures only.

## Decision to preserve

Forarbeider are official **preparatory material, not enacted law**. Use an isolated, explicitly opt-in dataset within Lovverk:

\`\`\`text
lovverk/
  manifest.json                       # Lovdata: NEVER add forarbeider here
  forarbeider/
    manifest.json                     # first created by a separately authorized release
    INDEX.md
    <session>/<prop-l|prop-ls>/<government-document-id>.md
\`\`\`

Only an explicit dataset reader may see the new manifest. The older law readers, MCP tools, law search, embeddings and static default indexes must remain byte/behavior-compatible. The source's immutable numeric document ID and session/type define identity; no synthetic law ID, no \`valid_at\`, and never classify a proposition as current law.

\`source_document_type=forarbeid\`, \`normative=false\`, \`source_provider=regjeringen.no\`, \`rights_basis=åndsverkloven § 14\`, and \`source_license=null\` are mandatory. **§14 is a statutory qualification, not NLOD, CC0, or a content licence.** Keep \`published_at\` nullable when not explicitly evidenced; distinguish upstream publication time, source retrieval time, owner release decision time and Git recording time.

## Critical PR3A limitation

PR3A records \`approved\` when a reviewer declares four attestations. Its \`EvidenceReference\` permits e.g. \`adr:0018\` as the sole evidence for **all four**. That ADR establishes policy but does not prove a GDPR processing basis, rights, third-party screening or completeness for any *individual* chapter. PR3A's self-reported reviewer handle, time, terminal confirmation and locally linked hash chain are also not authenticated or externally anchored. A hash reference establishes identity of bytes, **not** their legal adequacy.

**Therefore \`review.status=approved\` is a necessary but NEVER a sufficient release condition.** No publisher may implement \`if approved: publish\`.

## Boundary: three separately reviewed slices

### PR3B-1 — PRIVATE preflight and publication candidate (first code PR)

Implement an offline, read-only-to-corpus preflight using PR1/PR2 staged observations and PR3A audit chains. All inputs, reports, rendered previews and evidence remain in private owner-only storage **outside every Git checkout and CI artifact**. The operator invokes a supported Typer CLI. The preflight does not stage, create or write any \`lovverk\` file and has NO \`--force\`, \`--publish\`, hidden environment override or automatic approval.

Preflight must:
1. Validate the exact source document type, government ID, session, official reference, chapter URL and ordering; forbid collisions and cross-document chapter mixing.
2. Recompute or verify the source/text hashes from actual private staged artifacts, not merely copied strings; match extractor/normalizer versions, observation to review subject and the latest unbroken PR3A review chain. Require an externally kept chain-head reference when release evidence is evaluated; a standalone local ledger cannot prove its tail was not removed.
3. Evaluate **every candidate chapter** separately. Any missing body, annex, footnote/table/image/ambiguous or malformed content, withheld/rejected/pending or stale/superseded decision prevents publication of that chapter. A previous approval may not survive source/text changes or a later \`held\`/\`rejected\` decision.
4. Require a **chapter-specific, independently inspectable private release dossier** with actual evidence artifacts and checked SHA-256, not just an \`adr:\`/\`issue:\`/\`pr:\` pointer or an unattested digest. The dossier must include the identified publication purpose, applicable personal-data processing basis (or a reasoned no-personal-data assessment), minimization/classification decision, third-party rights screening, exact original-page/source integrity proof and reviewer/date. \`adr:0018\` alone cannot satisfy any case-specific gate. A machine can validate existence, binding, format and hashes; legal sufficiency requires an owner/human decision, not keyword detection.
5. Require a separate **owner release decision**, scoped to exact document/chapter IDs, source/text hashes, intended publication destination, rights/privacy review version, purpose and expiry/revocation conditions. The operator's PR3A \`approved\` flag cannot substitute for this. The contract for ownership/authenticity must be explicit; a typed handle/TTY is not cryptographic identity verification.
6. Produce a deterministic, structured report with eligibility outcomes/reasons and proposed (not published) manifest/rendering shape. Never print body text, private names, evidence-note contents or unsafe absolute paths to public logs. Default \`DENY\` on absent or ambiguous evidence.

**A successful PR3B-1 report still has \`publication_authorized=false\` and performs zero public writes.** No real-world text from source pages or private dossiers in code fixtures, issue/PR comments or CI.

### PR3B-2 — optional manual promotion, separate approval and code PR

Only after the owner approves the **PR3B-2 implementation scope** and independently establishes pre-publication legal/privacy/source-terms gates, implement a dedicated command able to promote a narrowly scoped, already-authorized, fully preflighted candidate. It remains disabled by default. No scheduler, crawler, CI deployment or unreviewed batch operation may trigger it.

Required release semantics:
- Re-validate against the current private dossier, latest review status, owner decision and immutable content hashes **at the moment of promotion**. Detect revocation or a changed source, and fail closed.
- Verify a clean, correct Lovverk checkout and an explicit scoped destination. Prepare a bounded, human-inspectable plan before writing. Never silently push, merge or make a release commit.
- Write only the dedicated \`forarbeider/\` tree; never touch root \`manifest.json\`, \`lover/\`, \`forskrifter/\`, \`lokale-forskrifter/\`, law histories, generic renderer defaults or public indexes. No raw HTML/PDF, complete private review notes, private reviewer IDs, third-party media or unapproved chapters in Git.
- The **independent versioned** \`forarbeider/manifest.json\` is the authoritative membership list, not an implicit directory glob. Schema must fail closed on duplicates, mismatched paths/hashes, dangling members, incompatible version or non-allowlisted document type. Empty/uninitialized dataset remains invisible to old readers.
- A document rendering includes only specifically released chapters in explicit source order. If any chapter is not released, clearly mark the result a **partial, curated excerpt**, list accurate source locators and do not assert proposition-wide completeness. Do not imply that a proposition was adopted.
- Rendering bytes are deterministic for identical source snapshot + released chapter set + renderer version + owner decision; hash normalized chapter text separately from Markdown. Encode links defensively; prevent Markdown/front-matter injection or navigation outside the dataset.
- Separate \`release_status\` (e.g. released/withdrawn) from a proposition's parliamentary or legal status. Preserve source and Git time axes; never invent \`valid_at\` or a publication date.
- Include a supported owner-only **withdraw/takedown workflow**: block future serving/indexing, identify affected manifest entries and generated artifacts, make a forward-only Git correction, and provide a documented escalation path for history exposure. **Git revert does not erase old public commits or clones**; if the exposure is unacceptable, the correct pre-publication decision is to withhold it.
- Source rights/website terms/§24 assessment, GDPR processing basis, privacy notice/contact channel, response ownership, retention/deindexing plan and independent human approval must be recorded **before the first real public bytes enter any Git branch or PR**, not merely before merging them.
- First tests use only synthetic fixtures, isolated disposable Git repos and mocked/stubbed private authorization records. No live government fetch or public dataset migration.

### PR3B-3 — explicit opt-in read path, separately approved

Only after PR3B-2 is reviewed and a limited release is individually approved: a dedicated, opt-in reader with dataset-specific labels and source citations. Default MCP, website search, \`get_law\`, law identity, default indexes, benchmark behavior and embeddings stay unchanged. No crosswalk or enacted-law linkage in this slice; that is governed later by ADR-0017. A missing, withdrawn or corrupt forarbeider manifest is not allowed to disrupt existing law service.

## Release controls not delegated to automated code

Before any real publication or crawl scale-up the owner must record:
- Specific source/website terms and §24 database-rights assessment for intended method/volume; robots access does **not** grant reuse rights.
- A documented lawful basis and purpose for any personal-data processing; third-party and special-category content addressed in a human-reviewed dossier. Unresolved high-risk cases stay private or receive metadata-and-official-link only.
- A public correction/takedown contact, deindexing/retention policy and a realistic plan for harmful Git-history disclosure (including inability to retract third-party clones).
- A bounded pilot population and **separate written release authorization**. Review of a code PR, green tests, ADR acceptance or a PR3A decision is NOT that authorization.

A no-personal-data pilot still requires an explicit written case-specific review of that conclusion and rights/terms. No agent may invent one.

## Acceptance matrix (red-first, negative tests mandatory)

| Scenario | Required result |
| --- | --- |
| Missing PR3A decision, latest held/rejected/pending | DENY; no candidate cleared |
| \`approved\` with four \`adr:0018\` references only | DENY; policy citation is not case evidence |
| Dossier hash points to missing/different private bytes | DENY |
| Dossier lacks explicit GDPR/purpose/third-party/source review | DENY |
| No scoped owner release decision, or expired/revoked/different target | DENY |
| Changed page bytes, chapter text or extractor version since approval | DENY |
| Ledger modified, truncated/empty without external checkpoint | DENY |
| Missing/inconsistent chapter inventory, duplicate source ID, invalid URL | DENY |
| Annex/media/footnote/table, malformed/partial body, suspect private citation | Hold relevant chapter; never partial-text-as-complete |
| One approved chapter, another held | Only exact approved subset may be eligible; document marked partial |
| Invalid/ambiguous public corpus path or symlink escape | DENY before writing |
| Root manifest, \`get_law\`, MCP, site/index absent/disabled | Byte/behavior identical |
| Non-Lovdata document with NLOD or implied effective date | Validation failure |
| Same frozen candidate and release inputs twice | Byte-identical plan/render; no unexpected new commit |
| Withdrawal after release | No future read/index; explicit public-history warning and recovery procedure |
| PR3B-1 against any real checkout | **Zero corpus writes**, even with all decisions \`approved\` |
| CI/tests/docs include real page body or private note | Gate failure |

Test both \`Prop. L\` and \`Prop. LS\`; chapter-by-chapter selectivity, repeated and conflicting decisions, source/version drift, missing evidence, malicious Markdown/URLs, crash partial-write cleanup, symlink and unsafe paths, and idempotent retries. A configured session/review/release option that the operator cannot reach via supported CLI is not shipped (CLAUDE.md). PR3B-2 must have separate integration tests for public corpus validation and withdrawal, **before** any real data.

## Deliberately deferred

No automated harvesting, sitemap walk, PDF/OCR, background cron, auto-approval, default MCP publishing, embeddings, crosswalk by title/year, Stortinget Innstillinger, Rundskriv/NAV, NOU or Ot.prp. No general refactor of \`promotion/\` solely to reuse its licence or renderer. No weakening existing CI/mutation/security checks or ratchets. Existing pypdf vulnerabilities [#740](https://github.com/bartoszkobylinski/lovspor/issues/740) stay tracked separately.

## Handoff and review

1. Owner reviews and merges this **documentation-only** contract PR; it grants no publication.
2. Claude implements **PR3B-1 only**, in one code PR from latest \`main\`, TDD and no real public writes. Run \`verify-fast\`, \`verify-deep\`, \`/security-check\`, existing integration + CI Codex/mutation. Owner alone merges.
3. Independent reviewer checks candidate/dossier/proof threat model, release denial tests, code, outputs and compatibility.
4. Owner then decides whether to authorize the **PR3B-2 code scope**. This decision is distinct from authorization to release any specific real chapter; that remains explicitly gated.

This contract translates ADR-0018 into reviewable implementation boundaries. It is not legal advice, a licence, evidence of GDPR compliance, or permission to publish.

## Appendix: B1 implementation architecture (non-normative)

**Status:** implementation notes for slice B1, written with B1a (2026-11-11, [#738](https://github.com/bartoszkobylinski/lovspor/issues/738)). This is not an amendment. Where it differs from Amendment 1 or the text above, those win. B1a ships the release-checkout guard and the read-only `batch preflight` ([operations](../operations.md#forarbeider-read-only-apply-preflight-issue-738-contract-amendment-1-b1a)). B1b (2026-11-11, same issue) implements the rest as `batch release-clone apply|recover|withdraw` ([operations](../operations.md#forarbeider-batch-apply-recovery-and-withdrawal-issue-738-contract-amendment-1-b1b)), with four deliberate differences from the notes below, each stricter or simpler, none weakening Amendment 1:

- **Confirm and apply are one command** (point 2). The phrase is typed inside `apply`, under the clone's lock, after a first preflight; a second preflight then runs immediately before staging and must give the confirmed binding and the confirmed files. No confirmation from an earlier process is ever applied.
- **Recovery never completes a release** (point 6). A release journal left by a dead run is always rolled back, and the owner runs `apply` again, which re-checks everything and asks the phrase again. Completing it would need the guard's clean-tree precondition on a half-written tree; rolling back costs one more confirmation. A withdrawal journal is finished, as point 7 says. The relaxed clean-tree case of point 6 exists only for a finished batch (status lists exactly its confirmation's files, or the owner committed them): that run prints `UNCHANGED`.
- **The lock is `flock`-held** (point 4): created exclusively, held by an advisory `flock`, and taken over only by `recover` when no process holds it. The guard accepts that one lock, by device and inode, and nothing else.
- **Withdrawal removes a whole document** (point 7). The owner names the chapters that triggered it, but a document's one file holds every released chapter, so all are withdrawn and the phrase names them all; a withdrawn document is never released again by a batch.

1. **What B1b may write.**
   - Only `forarbeider/**` in the guarded release clone: rendered documents at their `DOCUMENT_PATH`, `INDEX.md`, `manifest.json` (schema 2).
   - Its own lock, journal and staging inside that clone's `.git/`.
   - In the private workspace, the confirmation and receipt records.
   - Never the root `manifest.json`, `lover/`, `forskrifter/`, `lokale-forskrifter/` or any other path, the sync clone, the engine, or a Git ref. No `git add`, commit or push: the owner commits, pushes and opens the lovverk PR, and merging it is the publication.
2. **One gate, called twice.** `apply_preflight(scope, request, now)` (`forarbeider/apply_preflight.py`) is the only way to obtain an apply verdict. `batch confirm` runs it and shows the binding. `batch apply` runs it again **inside the clone's lock, immediately before staging**, and requires the same binding digest the confirmation names. No verdict object or digest from an earlier process is accepted, so nothing changed between confirmation and write (a re-staged page, a new review decision, an expired authorization or proof, a moved HEAD, a changed base manifest) can slip through.
3. **Owner confirmation binding (B1b).** One typed phrase at a terminal (`require_a_person`), containing the plan digest prefix, the binding digest prefix, the included count and the target branch. The binding digest covers the plan digest, target branch, HEAD commit, base manifest digest, remote, every included chapter id with its text digest, and the counts by path. The stored `BatchConfirmation` records those values, the document ids, the owner handle and time, and `identity_authenticated: false`. A typed phrase is the owner's act, not cryptographic identity.
4. **Atomic write and rollback.** Reuse `corpus_transaction`: journal before the first operation, every new file's bytes and every replaced file's previous bytes staged inside `.git`, documents then index then manifest, forward `finish` or backward `roll_back`, and a read-back check before the journal is cleared.
   - One journal per batch, `Binding("batch_release", <confirmation sha>)`, so a batch is all or nothing.
   - Its `_verify` is today bound to the synthetic sandbox (`require_sandbox`). B1b passes the release-checkout guard's read-back instead, and changes nothing else in the transaction.
5. **Post-write check.** After apply, `git status --porcelain=v2` (read-only, through `lovspor.git_read`) must list exactly the planned `forarbeider/` paths and nothing else, and the dataset read back must be the target manifest. Otherwise roll back and refuse.
6. **Crash recovery and idempotency.**
   - A journal left by a dead run is settled before anything else. A release journal whose confirmation still reproduces (same binding) is finished; otherwise it is rolled back, never completed. This is the rule `simulate` already follows.
   - The clean-tree precondition is relaxed for one case only: exactly this confirmation's own paths already hold their planned bytes. That run reports `UNCHANGED` and writes nothing.
   - After the owner has committed the batch, the tree is clean again and the manifest already holds it: `UNCHANGED`.
7. **Forward-only withdrawal (B1b, ships with or before apply).**
   - Against the guarded clone, at a terminal, with no release grant needed.
   - The intent is stored first (named by the resulting manifest digest), then a `withdraw_draft` runs through the same transaction, then the receipt is stored, as `sandbox-withdraw` does.
   - It prints the Git-history warning. The owner commits the removal; history is never rewritten.
8. **Not in B1:** MCP or site exposure; the receipt-verified reader of a real clone (B2).
