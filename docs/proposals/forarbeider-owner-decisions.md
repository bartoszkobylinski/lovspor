# Forarbeider full corpus: owner decision gate (PROPOSED / PENDING)

**Status:** every decision below is **PROPOSED / PENDING** until the owner explicitly approves it. This document authorizes no fetch, signature, repository, release or code.
**Date:** 2026-11-12
**Tracking:** [#738](https://github.com/bartoszkobylinski/lovspor/issues/738)
**Architecture:** [full-corpus architecture](forarbeider-full-corpus-architecture.md) (#892, merged as the proposed direction), the [PR3B contract and Amendment 1](forarbeider-pr3b-release-contract.md), ADR-0018, ADR-0017, ADR-0019.
**Scope:** this document resolves implementation choices and lists what needs owner approval. It does not redesign the architecture and gives no legal advice.

Tags: **[O]** observed in code, docs or evidence; **[A]** assumption; **[L]** needs independent legal assessment.

## 1. Decision table

Kind: **T** technical architecture, **S** operational or security, **L** legal (assessment, never decided here).

| ID | Decision | Kind | Recommendation | Owner approval needed | Slices |
|---|---|---|---|---|---|
| D1 | Full-corpus direction | T | **ACCEPT** | now, before FC3 | all |
| D2 | Successor ADR rather than Amendment 2 | T | **ACCEPT** | now, together with D1 | documentation only |
| D3 | Separate `lovverk-forarbeider` repository before the first release; private during preparation; the bot (preferably a GitHub App) updates protected `main` (D11) | T, S | **ACCEPT** | before FC9b; not needed for FC1–FC8 | FC9b, AR7, FC10, FC13 |
| D4 | `terms_basis: none_identified`, terms locus, 30-day default | T, L | **ACCEPT**, rule only | before FC2 merges | FC1b, FC2, FC7 |
| D5 | Discovery from official catalogues | S, L | **DEFER** | before FC11 | FC11, FC12 |
| D6 | SSH signatures and trust boundary (revised for D11: separate owner and bot keys) | S | **ACCEPT** | before FC7 and AR1 merge | FC7, AR1, AR4, AR6 |
| D7 | Release strategy (owner-merged manifests) | S | **SUPERSEDED by D11** (owner direction 2026-11-12) | — | — |
| D11 | Policy-driven automatic publication | S, T | **ACCEPT** (as the owner directed); formal approval pending | before AR1 merges | AR1–AR8, FC7–FC11, B1a, B1b, B2 |
| D8 | Two completeness axes, `unknown` and never a false zero | T | **ACCEPT** | now, together with D1 | FC6, FC10, FC14 |
| D9 | Staged document families | T, L | **MODIFY** | Prop. L and LS now; later families each at their own gate | FC10, FC12 |
| D10 | Legal assessment (§§ 14, 24, 29; privacy; quotes; attachments) | L | **record unresolved; commission an assessment** | the assessment must come before FC10's release step | FC5, FC7, FC10 |

## 2. Decisions in detail

### D1: full-corpus direction (ACCEPT)

- **Recommended:**
  - the product is complete source documents, versioned, with explicit gaps;
  - two completeness axes: source and publication (§2.3 of the architecture);
  - curated excerpts are never the target, only an interim state with recorded gaps.
- **Alternatives:**
  - keep the curated-excerpt pipeline (A1–B2 as shipped);
  - a hybrid with no completeness accounting.
- **Rationale:** it is the stated product goal. Pilot-0 showed the excerpt model withholds 4 of 9 chapters of an ordinary proposition [O] and cannot tell a gap from a design choice.
- **Consequences:**
  - FC3–FC9 extend A1–B2; nothing shipped is discarded;
  - release volume per document grows, so D11 and D10 matter more.
- **Reversibility:** high until FC10. Every slice before FC10 is offline or additive.
- **Approve now:** yes, because it decides what FC3 and FC6 build.

### D2: successor ADR rather than Amendment 2 (ACCEPT)

- **Recommended:** write a successor ADR, "Forarbeider full corpus", in `lovspor-notebook`, under **ADR-0025**. Checked 2026-11-12: ADR-0020, 0021, 0023 and 0024 exist in `lovspor-notebook/docs/adr`, and ADR-0022 is taken by open lovspor-notebook PR #167. It supersedes ADR-0018's excerpt-release model, keeps ADR-0018's isolation and labelling rules by reference, and links the PR3B contract and Amendment 1 as historical.
- **Alternative:** Amendment 2 to ADR-0018.
- **Rationale:** the change concerns the product (complete documents, versions, two axes, a new repository), not one release rule. Stacking a second amendment on a contract whose base text is about excerpts makes the "amendment wins" reading fragile.
- **Consequences:** one ADR to write. The PR3B contract gets a "superseded in scope by the successor ADR" note, and nothing in it is deleted.
- **Reversibility:** documentation only.
- **Approve now:** yes, with D1.

### D3: separate `lovverk-forarbeider` repository; the engine stays in lovspor (ACCEPT)

- **Recommended:**
  - a new repository, created by the owner, holding only released forarbeider;
  - the GitHub rulesets of architecture §9.7: R-integrity with no bypass, and R-writers with only the bot (preferably a GitHub App, bypass by App id) and the admin as bypass actors;
  - releases are published by the bot under D11, with no manual merge. Owner-only merging applies to engine and trust-root changes, not to releases;
  - its initial commit is pinned in the engine (FC9b);
  - the engine and every command stay in lovspor;
  - lovverk keeps law text only.
- **Alternatives:**
  - `lovverk/forarbeider/`, as built by B1b;
  - a branch or orphan history inside lovverk.
- **Rationale:**
  - published bytes can never leave a repository's history [O]. Releasing into lovverk first and migrating later would leave forarbeider in lovverk's history for good;
  - growth is estimated at 0.3–3 GB of Markdown over 20 sessions [A], which would bloat every lovverk clone.
- **Consequences:**
  - FC9b adds per-repository trusted roots to the B1a guard;
  - the B1b and B2 commands take the target repository explicitly;
  - nothing has been released into lovverk, so there is nothing to migrate [O].
- **Reversibility:** high before FC10. After the first release, only forward.
- **Migration cost:** small (FC9b).
- **Approve:** before FC9b. FC1–FC8 do not depend on it.

### D4: source terms (ACCEPT the rule; the legal reading stays with D10)

- **Recommended:**
  - `terms_basis: none_identified`, backed by a signed `terms_search_record`: pages searched with URL and read date, the method, the finding;
  - a terms locus (`https://www.regjeringen.no/no/om-nettstedet/`) that every current proof reads;
  - validity of **30 days by default**; longer (at most 90) only by a separate recorded owner decision;
  - the comparison uses the narrow CSP nonce normalization (FC1);
  - absence of terms is recorded as absence, **never as a licence**.
- **Alternatives:**
  - wait until regjeringen.no publishes text terms. Pilot-0 found none on 2026-11-12 [O];
  - name an unrelated page as `terms_url`. **Rejected:** it substitutes a page for terms.
- **Rationale:** A2a cannot clear anything today without a terms page that does not exist [O], and a raw-byte comparison of regjeringen.no HTML never matches, because of the per-response nonce [O].
- **Consequences:**
  - FC2 adds the mode;
  - FC1b changes Amendment 1 point 8 ("no normalisation") for HTML evidence; robots.txt stays raw;
  - re-searching every 30 days is an operational cost.
- **Reversibility:** high. `terms_basis: page` stays available.
- **Approve:** before FC1b and FC2 merge. FC1a needs no approval (§4).

### D5: discovery from official catalogues (DEFER)

- **Recommended when taken up:**
  - discovery from official catalogue pages or APIs, inside an owner-approved and signed scope (family × sessions);
  - robots.txt live per run; at least 5 s between requests; one identifying User-Agent;
  - a per-run request cap and a per-day cap;
  - resumable runs with an inventory record.
- **Alternative:** keep owner-typed explicit lists (Amendment 1 point 6) indefinitely.
- **Why defer:**
  - the first complete Prop. L needs no discovery: an explicit list is enough [O, Pilot-0];
  - discovery changes Amendment 1's "not harvesting" point and touches the § 24 volume question [L].
- **Consequences of deferring:** none for FC1–FC10.
- **Approve:** before FC11.

### D6: SSH signatures and trust boundary (ACCEPT)

- **Recommended:** the trust boundary of architecture §3.1:
  - an engine-pinned `allowed_signers` and `revoked_keys`;
  - one namespace per purpose (policy, release, publication);
  - `valid-after` and `valid-before` windows; rotation with overlap; a compromise procedure;
  - reusing the crosswalk mechanism (#840) [O].
- **Revised for D11:**
  - two keys: the owner key (namespace `lovspor-forarbeider-policy`) and the **bot key** (namespaces `lovspor-forarbeider-release` and `lovspor-forarbeider-publication` only);
  - the bot key can never verify as a policy signature;
  - the bot key is rotated and revoked through the same `allowed_signers` and `revoked_keys`, by owner-merged changes.
- **No false identity claim:**
  - a signature proves only that a listed key signed the bytes;
  - records state `signature_verified: true` and the key fingerprint, never "the owner did X";
  - `identity_authenticated` becomes true only in the narrow sense "verified against the pinned key", with that meaning written into the record.
- **Alternatives:**
  - typed phrases only (today): not authenticated [O];
  - GPG: a second toolchain.
- **Consequences:**
  - one owner key ceremony;
  - policy, release manifests and publication attestations gain signatures;
  - B2 attestations become verifiable.
- **Reversibility:** medium. Records signed under a key stay tied to it.
- **Approve:** before FC7 merges. Code can be built and tested with test keys earlier.

### D7: release strategy (SUPERSEDED by D11)

The owner rejected manual approval of each document, batch, PR or merge (2026-11-12). The owner-merged, per-release manifest model described here earlier is withdrawn. D11 replaces it.

### D11: policy-driven automatic publication (ACCEPT, as directed; formal approval pending)

- **Owner direction (2026-11-12, stated in session):** manual approval of each document, batch, PR or merge is rejected. Human approval is required for source-level policy, legal scope, security trust roots and material policy changes, not routine releases.
- **Recommended model:** [architecture §9](forarbeider-full-corpus-architecture.md#9-amendment-a-policy-driven-automatic-publication-proposed):
  - an owner-signed source/family policy with `automated_publication: true`, budgets, anomaly thresholds and a reference to a completed legal assessment (D10);
  - eligibility only for `source_complete` and `publication_complete` versions; everything else quarantined;
  - bounded batches;
  - a bot-signed release manifest. Publication, preferably by a GitHub App through `createCommitOnBranch` with `expectedHeadOid` (GitHub creates and signs the commit and moves `main` atomically); a bot-key-signed commit and fast-forward push only as a tested fallback;
  - the proposed bytes validated before publication, the actual GitHub commit verified after it, and a receipt only then;
  - immutable receipts;
  - a three-level kill switch;
  - per-document failure isolation;
  - authorization re-checked immediately before every publication call, with a persistent stop latch;
  - recovery from the remote history after a crash (architecture §9.3a);
  - GitHub-enforced rulesets (with claims limited to what GitHub enforces), a preferred GitHub App bot, AR7 refusing any unexpected bypass actor or write deploy key, and the pipeline disabled while settings cannot be verified (§9.7);
  - recovery that separates remote integrity from publication authorization; uncertain authorization is recorded as published, never republished, reported, and halts further releases (§9.3a);
  - an independent read-only auditor (§9.7);
  - each batch counted against the budgets exactly once;
  - quarantined documents kept visible in the metrics;
  - owner keys never on the runner.
- **Authorities separated:**
  - the owner holds policy and trust roots;
  - the runner (a dedicated macOS user on the owner's Mac, run by launchd) holds execution;
  - the bot credential (preferably the App, Contents: write) can update `main` of one repository only.
- **Alternatives:**
  - the owner merges each release (D7, rejected);
  - CI publishes. Rejected: it adds a trust root outside the owner's machine, and recommended answer 8 asks for no CI publishing.
- **Consequences:**
  - AR1–AR8 replace the manual release steps;
  - FC10 becomes an automatic release once D10 and the policy allow it;
  - the manual B1b path stays for exceptions.
- **Reversibility:** stop file, then policy revocation, then credential revocation. Published commits are withdrawn forward only and cannot be erased from history (architecture §9.7).
- **Open [D]:** may the automatic path ever publish `publication_incomplete` documents with gap notices? The recommended answer is no. Changing it is a material policy change.

### D8: two completeness axes; `unknown`, never a false zero (ACCEPT)

- **Recommended:**
  - `source_complete` and `publication_complete` are computed independently from records;
  - `publication_complete` implies `source_complete`;
  - a metric with a missing input is `unknown`;
  - expected counts come only from the source;
  - every rendering shows its publication state and its gaps.
- **Alternative:** a single `complete` flag. **Rejected:** it would hide third-party gaps.
- **Consequences:** FC6 builds both axes, and readers (B2, FC14) show them.
- **Reversibility:** costly after the first release, because records carry the fields. **Approve now**, with D1.

### D9: staged document families (MODIFY)

- **Recommended stages:**
  1. Prop. L and Prop. LS on regjeringen.no (the existing extractor and Pilot-0);
  2. NOU and Prop. S on regjeringen.no;
  3. historical Ot.prp. and Ot.prp.-era documents;
  4. Stortinget documents (Innst., Dokument-serien), with their own source terms and policy.
- **Change from the brief:** each family enters by its **own gate**: an extractor fixture set, the § 14 coverage question for that family [L], and a policy covering it. It is never added implicitly.
- **No permanent exclusion without a recorded reason:** a family left out carries a reason code and a review date in the scope record.
- **Rationale:** a family's markup, attachments and rights status differ. The NOU (committee reports) and Innst. (Storting) authorship differs from ministry propositions [A] [L].
- **Reversibility:** high.
- **Approve now:** stage 1. Later stages at their gates.

### D10: legal assessment (record unresolved; commission an assessment)

- **Recommended:** a written, independent assessment of the questions in §3, commissioned by the owner before FC10's release step.
- Until then:
  - every affected span or attachment is a **release gap**;
  - nothing is cleared "provisionally";
  - nothing in code or documentation states a legal conclusion.
- **Alternative:** the owner's own assessment, signed into the policy evidence. Amendment 1 allows that, but this document recommends an independent reading for the § 24 and § 29 questions, given full-document volume.
- **Approve now:** the decision to commission it. The answers are not decided here.

## 3. Questions for the owner (answer each)

1. **D1/D2:** do you approve the full-corpus direction and a successor ADR in `lovspor-notebook` (ADR-0025)?
2. **D3:** do you approve a separate `lovverk-forarbeider` repository, created by you, with the §9.7 rulesets (R-integrity with no bypass; R-writers with bypass for the bot and the admin only), before any release? Do you accept a GitHub App as the bot (Contents: write on this repository only), with a machine user or a single write deploy key only as a tested fallback?
3. **D4:** do you approve `terms_basis: none_identified` with the locus page, a 30-day default validity, and the narrow nonce normalization replacing the raw comparison for HTML evidence?
4. **D6:** will you generate a dedicated signing key for forarbeider (separate from the crosswalk key, or the same key under new namespaces)? Where is it held, and what is the rotation interval?
5. **D11:** what batch bound (files and bytes), daily budgets and anomaly thresholds?
6. **D9:** do you approve stage 1 (Prop. L and LS) now, with each later family at its own gate?
7. **D10:** who performs the legal assessment, and by when? Do you accept that FC10's release step waits for it?
8. **Operations:** which machine and account run the release clone and hold the private workspace? Today that is the owner's Mac [O].

## 4. Implementation readiness

| Slice | Starts before legal clearance? | Needs architectural owner approval | Needs legal assessment or publication authorization | Notes |
|---|---|---|---|---|
| **FC1a** normalized digest recorded (additive) | **yes** | no | no | offline; no behaviour change (§6) |
| FC1b compare normalized for HTML evidence | yes (code) | **D4** before merge | no | changes Amendment 1 point 8 |
| FC2 `terms_basis: none_identified` | yes (code) | **D4** before merge | no | |
| FC3 spans and quote groups | **yes** | D1 | no | extraction only |
| FC4 tables and footnotes | **yes** | D1 | no | extraction only |
| FC5 span rights classes and detectors | yes (code) | D1 | classifies only; clears nothing | which classes the policy clears is a D10 matter |
| FC6 version manifest v3, two axes | **yes** | D1, D8 | no | |
| FC7 source policy v2 and SSH | yes (code, test keys) | **D6** before merge | signing a real policy needs D10 | |
| FC8 release manifest (becomes AR2–AR4) | yes (code) | **D11** before merge | no | tests against a local bare remote |
| FC9 withdrawal v2 (executed by the pipeline from an owner-signed record) | yes | D11 | no | |
| AR1 policy v2 automation fields | yes (code, test keys) | **D11**, D6 | a real automated policy needs D10 | |
| AR5 kill switch, budgets, anomalies | **yes** | D11 | no | |
| AR7 bootstrap verifier (read-only) | after D3 | D3, D11 | no | owner sets repository settings |
| FC9b repository bootstrap | after D3 | **D3** | no | the owner creates the repository |
| **FC10** first complete Prop. L | source part yes; release part no | D1–D6, D8, D11 | **D10**; a signed policy with `automated_publication` | no per-release owner act |
| FC11 discovery | no | **D5** | § 24 volume [L] | |
| FC12 more families | per gate | D9 | § 14 per family [L] | |
| FC13 growth controls | after FC9b | D3 | no | |
| FC14 search, MCP, API | after FC10 | separate | publication done | |

**Can FC1 start independently?** **FC1a: yes**, it is purely additive and offline. **FC1b: code yes, merge after D4**, because it changes what A2a compares.

**No approval gate is created for offline engineering.** FC1a, FC3, FC4 and FC6 read only staged bytes and write only private records or test fixtures. They need D1 as a direction, not as a per-PR approval.

### Critical paths

**A. First `source_complete` Prop. L.** Offline, no legal step, no new fetch: Pilot-0's staged pages for Prop. 31 L (2025–2026) can be reused [O, private staging].

FC1a → FC3 → (FC4 if the candidate has tables or footnotes; Prop. 31 L has none [O]) → FC6 → an offline run on the staged pages: `source_state` and gaps, reported.

**B. First `publication_complete` release.**

A → FC1b + FC2 (D4) → FC5 → FC7 + AR1 (D6, D11) → AR2–AR5 → FC9 → FC9b + AR7 (D3) → AR6 → D10 assessment → the owner signs a policy enabling automated publication → the automatic run publishes FC10.

FC10 acceptance stays as merged: one Prop. L that is both `source_complete` and `publication_complete` with zero release gaps, or a NO-GO with every gap. If third-party consultation quotes remain unresolved, FC10 is NO-GO by definition [O, architecture §6].

## 5. Legal questions kept unresolved [L]

None of these is answered here, and no slice may assume an answer:

1. **§ 14 first paragraph:** does it cover, for each family, the whole published document (Prop. L/LS/S, NOU, Ot.prp., Innst.)? Does it cover statements by public bodies quoted in a consultation chapter?
2. **§ 14 second paragraph:** which quoted material and annexes in a proposition are third-party works?
3. **§ 24:** is regjeringen.no or stortinget.no a protected database? Does "§ 14 gjelder tilsvarende" remove protection for a database of § 14 material? What extraction volume would be "substantial" or "systematic"?
4. **§ 29:** may third-party consultation quotes be republished inside a complete proposition "i samsvar med god skikk og i den utstrekning formålet betinger"?
5. **Personal data:** what is the processing basis under personopplysningsloven for republishing names and other personal data in consultation chapters and lists? Is span-level redaction sufficient?
6. **Attachments:** what are the rights in annexes, figures and images that were not made for the document (§ 14 second paragraph)?
7. **Absence of terms:** does the absence of published text-reuse terms on regjeringen.no have any legal significance beyond the statute?

## 6. Recommended immediate next PR: FC1a

**Title:** `feat(forarbeider): record a nonce-normalized comparison digest beside every raw digest (#738, FC1a)`

**Scope:**

- Add `normalize_regjeringen_html(raw: bytes) -> bytes` in `regjeringen_fetch`. It replaces exactly two kinds of value with the fixed token `NONCE`:
  - the value of each `nonce="…"` attribute;
  - the `content` of the one `<meta name="csp-nonce">`.

  The grammar is byte-level: ASCII attribute syntax, base64 alphabet plus `&#x2B;` and `=`. Anything outside that grammar is left untouched.
- Fetch run records gain `normalized_sha256` and `normalization_rule` (a version string) for every stored HTML page, beside the unchanged raw `sha256`. Schema 3, additive.
- **Storage stays keyed by the raw digest.** Files are still named `<raw sha256>.html`, and every response is still stored.
- **No comparison changes.** A2a, A2b, B1 and B2 keep comparing raw digests. FC1b, after D4, switches the HTML evidence comparison.

**Acceptance criteria (red first):**

1. Two pages differing only in nonce values give different raw digests and an equal `normalized_sha256`.
2. A page differing in one other byte gives a different `normalized_sha256`.
3. A nonce value outside the grammar is left untouched, so a nonce change in it changes `normalized_sha256` (fail closed).
4. robots.txt records carry no `normalized_sha256`.
5. Stored file names are still the raw digest, and the raw bytes are unchanged.
6. Schema 2 run records are still read.
7. Normalization is deterministic and idempotent: normalizing twice gives the same bytes.
8. No chapter text in the fixtures: synthetic pages only.
9. The existing A2a, A2b, B1 and B2 suites pass unchanged, which shows the change alters no behaviour.

**Not in FC1a:** any change to `policy_source`, policy signing or the release path.

## 7. Answers received 2026-11-12 (recommended; PENDING owner confirmation)

These answers arrived in session under the headings "Rekomendacja" and "Rekomendowana odpowiedź". They are recorded as **recommended answers**, not as approvals. They become the owner's decisions when the owner confirms them, for example by merging this PR or by a comment on #738.

| # | Recommended answer | Effect if confirmed |
|---|---|---|
| D1 / Q1 | full corpus; new ADR, numbered after checking the register | D1, D2; ADR-0025 (§D2) |
| D3 / Q2 | `bartoszkobylinski/lovverk-forarbeider`, private during preparation, public only when ready to release | D3; making it public is an owner act (trust root) |
| D4 / Q3 | approve none_identified, 30 days, nonce rule; absence of terms grants no right to publish | D4 |
| D5 | defer to FC11 | D5 |
| D6 / Q4 | a separate Ed25519 key for forarbeider on the owner's Mac, passphrase-protected, in the system Keychain; rotation every 12 months and immediately on suspicion | D6 owner key; the bot key (D11) is a second key on the runner account |
| D7 / Q5 | 100 files or 25 MB per batch, whichever comes first; no required gap between apply and merge; mandatory re-validation before merge | under D11: the batch bound of architecture §9.3; "re-validation before merge" becomes pre-publication validation of the proposed bytes and the authorization gate immediately before the publication call (§9.3 steps 3–5) |
| D8 | accept | D8 |
| D9 / Q6 | stage 1 Prop. L/LS; later NOU, Ot.prp., Innst. and the rest | D9 |
| D10 / Q7 | commission an independent assessment before full documents go public; do not block FC1–FC6; no artificial deadline; start before FC10 | D10 |
| Q8 | the owner's Mac, the owner's user account; private staging and keys outside the repository; no CI publishing | under D11: a **dedicated** runner user on the owner's Mac (§9.1), so the bot credential is not in the owner's own account; still no CI publishing |

## 8. What stays unchanged until approved

- Nothing is fetched, signed, created, released or merged by this document.
- A1–B2 behaviour, labels (`normative: false`, `source_license: null`, § 14, NOT enacted law) and private staging are unchanged until the AR slices land. Releases are authorized by owner-signed policy (D11), never by a per-release owner act. Owner-only merging still governs *engine* changes, including trust roots.
- Every decision above stays **PROPOSED / PENDING** until the owner approves it in writing (a comment on #738, the merge of this PR, or the successor ADR merge).
