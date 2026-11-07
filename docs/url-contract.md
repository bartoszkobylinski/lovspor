# URL contract for lovspor.no corpus pages

**Version 1, 2026-11-06** (#747). It writes down what ADR-0013 (lovspor-notebook) decided and
what the engine enforces. Where the two differ, this file says which one holds. A change to
any rule below bumps the version. A stronger promise than the one stated here needs an ADR first.

## 1. Four identities, kept apart

| Identity | What names it | Where it appears |
|---|---|---|
| **Document** | `canonical_id`, the manifest id (`nl-20241220-096`). Lovdata's own identity is `ref_id` (`lov/2024-12-20-96`), which the publicData API spec calls the FRBR "work". | JSON `canonical_id`, `ref_id` |
| **Provision** | The document plus `pid`, the section id in URL form (`35 a` → `35a`). The JSON spells it `<canonical_id>#paragraf-<pid>`. | provision JSON `canonical_id`, `heading_id` |
| **Current representation** | The URL: one page and its JSON twin. The text under a URL changes with the corpus, and `representation_hash` is the SHA-256 of the HTML bytes. | `canonical_url`, `<link rel="canonical">` |
| **Recorded version** | `source_revision`, the document's last lovverk commit. **It has no URL on the site.** | JSON `provenance.source_revision` |

A URL names the current representation of a document or provision. It never names a version,
and it never names a legal state at a date.

## 2. URL grammar

```text
https://lovspor.no/lov/{slug}/                       document (doc_type lov)
https://lovspor.no/forskrift/{slug}/                 document (doc_type forskrift)
https://lovspor.no/{route}/{slug}/paragraf/{pid}/    provision
{any page URL}index.json                             its JSON twin
https://lovspor.no/lov/   https://lovspor.no/forskrift/     browse indexes (no twin)
```

- URL identity is `(route, slug)`. A duplicate slug within one route fails the build. The same
  slug under both routes is legitimate (ADR-0013 Amendment 1).
- A slug is lowercase `[a-z0-9æøåü]` groups joined by single hyphens. The inventory refuses any
  other slug.
- A `pid` is the section id lowercased with all whitespace removed. The `§` sign never appears
  in a path.
- A document with a duplicate pid has **no provision URLs**. It gets its document page only,
  and `site-manifest.json` records it under `exclusions`. No disambiguation URL is invented.
- Directory URLs end in `/`. Host and scheme normalisation (HTTP → HTTPS, slash-less →
  slash) is left to Caddy's defaults. **This repo neither configures nor tests it**, and no
  `www` host is configured here. ADR-0013 says "301" for these cases. That claim is
  unverified, so this contract does not repeat it.
- Local regulations (`lokale-forskrifter/`) have no route and stay off the site
  (`scripts/quality/check_boundaries.py`).

## 3. Canonical ↔ JSON correspondence

For every page at path `P`:

- `<link rel="canonical">` is exactly `https://lovspor.no` + `P`.
- The twin at `P` + `index.json` has `canonical_url` equal to that same string, and its
  `representation_hash` equals the SHA-256 of the page's bytes. `publish-check` verifies the
  hash on every release.
- **A page and its twin share one fate.** Both are live, both answer 410, or both answer 301:
  `P` moves to `T` exactly when `P/index.json` moves to `T/index.json`.

## 4. Persistence: what happens to a URL a release served

Each page and twin that a release served gets one of three answers from every later release:

1. **Live**: still a page.
2. **301**: one hop to a live page that is the **same entity**. A document URL moves only to a
   document URL, and a provision URL only to the **same pid**. Neither moves to the parent
   document, and redirects never chain.
3. **410**: the URL is retired.

A 404 for such a URL is a defect. When a redirect source or a 410 prefix covers a URL the same
release serves, the build fails, because Caddy answers `redir`/`respond` before the file server.

| Event between releases | Answer |
|---|---|
| Content edit, same slug | live |
| Slug rename that git lineage proves (the rename walk) | 301 for the document, and 301 for each pid that **survives in the successor at the pinned snapshot** |
| Rename, but the pid is absent from the successor | 410, from the old slug's 410 namespace |
| A pid leaves a document that keeps its slug | 410 for that exact provision URL, when a collision-free version of the document's path in the pinned first-parent history carried the pid. A pid seen only in colliding versions was never servable and gets no entry |
| A document starts colliding on a pid | 410 for each pid of an earlier collision-free version. A pid the history never shows at a servable URL gets no entry |
| Document removed or repealed (`status != current`, or its file deleted) | 410 for the slug's whole namespace |
| A slug is reused by a different document | The new document owns the URL. The old document's pids that the new one lacks answer 410 |
| An ambiguous rename: no proven lineage, or only a similar title or slug | **No 301.** The old URL answers 410. Similarity is never identity |

**A 410 is a non-resolving citation, not an archive.** It says that this URL no longer serves
the entity. It does not promise that the retired text is available on the site or anywhere
else. The corpus history in lovverk git, and the MCP `get_law_at`, are a separate surface with
their own contract.

**Enforcement.**

- At build: `publish.redirects.validate_redirect_targets` checks that every target is emitted,
  that entity and pid are kept, and that no rule shadows a live URL.
- Across releases, in production (#820, fail-closed): `lovspor release commit` refuses to make a
  release live unless it answers every URL the **live** release serves. The check is
  `publish.check.check_continuity` over the two `corpus/` trees. It refuses a URL that would
  answer 404, a dangling or non-live 301, and a page and twin with different fates. That covers
  the live release's pages, its twins and its redirect sources. `reconcile --complete` applies
  the same check before it makes a staged fragment live.
  - **Which release is live** is the reconciled triple: R = D = M.active, read under the
    transaction lock. A directory listing never decides it. The live release's trees must
    still hash to its own `release_content_id`. A damaged, truncated or missing previous
    tree, or an unreadable marker, is a refusal. It is never a comparison against fewer URLs.
  - **The check runs after** the candidate is found complete and naming itself, and **before**
    anything is staged. The triple is then read again and must be unchanged, so the release
    that was compared is the one replaced. A refusal leaves the fragment, `.next`, Caddy's
    running configuration and the marker untouched.
  - **Concurrency.** `commit`, `rollback`, `reconcile` and `prune` hold an exclusive,
    non-blocking `flock(2)` on the releases root. A second one refuses at once, and `prune`
    can never remove a candidate that is being checked.
  - **Bootstrap.** Nothing is compared only when the triple proves nothing envelope-based was
    ever live: no marker, and R and D naming no release. A missing marker beside a release
    Caddy serves is the `reloaded_marker_missing` row, and it is refused.
- By hand: `lovspor publish-check <release> --previous <served release>` runs the same check
  on any two releases.
- Tests: `tests/unit/test_publish_url_continuity.py` holds the synthetic two-snapshot cases.

**Limits. These are stated, not guaranteed beyond them.**

- The evidence is the pinned lovverk history. The guarantee assumes that history is append-only.
  A rewritten history can remove the proof of a rename or of a removed pid.
- A 410 can cover a URL from corpus history before the site existed (2026-09), which no
  release ever served. That overclaims "was a URL", and it never hides a live page.
- Removed-pid evidence is read per Markdown path and applied to the document now published from
  that path. This relies on slug = file stem, which holds for every current record and which
  the rename walk relies on too.
- Rename proof relies on git rename detection, under the existing walk's limits.
- The check compares each release with the one it replaces. The guarantee is transitive only
  from the first release committed with the check (#820). Earlier transitions were not judged,
  and URLs served only before then are not protected.
- **`rollback` is not judged.** It restores the release served immediately before, so a URL
  that only the rolled-back release introduced answers 404 again. That lasts until a release
  that serves it is committed. This keeps rollback an emergency restore that never depends on
  the release it leaves.
- The first envelope (`release migrate`, and a `commit` on a host where nothing envelope-based
  was ever live) is not compared with the pre-envelope tree it replaces.
- The candidate is trusted to be the tree `release build` finalized and checked. The commit
  does not re-hash it, so a candidate edited after its build is judged as it now stands.
- A host where an operator hand-loads a configuration into Caddy is outside the transaction.
  The re-read before staging catches a change made during a commit, and `release live`
  refuses a configuration that does not match the disk.
- A URL no release ever served answers 404.

## 5. Dates and time axes

- **No URL carries a date today.** No version route exists.
- Any future date in a URL MUST name its axis in the path, never a bare date. A **recorded** date
  (when Lovspor recorded a state, from git history) must never read as **in force on**. **Corpus
  history is not legal validity history** (ADR-0002, `docs/mcp.md`).
- No version route is added without a verified version and retrieval contract. Recorded
  versions exist as git commits, but no site route serves them. Legal versions depend on the
  replay pilot (#744).
- The JSON `temporal` block carries `date_in_force` and `last_change_in_force` as the source
  states them for the current text, with the fixed note "Current corpus state; not an
  applicability-at-date determination."

## 6. Official identifiers

- A Lovspor URL is Lovspor's address for its derived representation. It is **not** an official
  identifier. Lovspor mints no ELI, and no path imitates an ELI form.
- `ref_id` is Lovdata's identity for the work, carried verbatim from the source.
- The JSON carries no official ELI link yet. The act-level mapping from `ref_id` exists
  (`lovspor.eli.act_eli`, #819) and its evidence and open questions are in
  [`docs/research/eli-assessment-2026-11-06.md`](research/eli-assessment-2026-11-06.md); emitting
  it is an owner decision. When emitted, it names the act, never a provision.
