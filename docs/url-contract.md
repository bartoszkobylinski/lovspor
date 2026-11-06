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
| A pid leaves a document that keeps its slug | 410 for that exact provision URL. The evidence is a removed `### §` heading in the pinned first-parent history |
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
- Across releases: `lovspor publish-check <release> --previous <served release>` refuses a
  release that would answer 404, a dangling or non-live 301, or a page and twin with different
  fates for any URL the served release answered. That covers its pages, its twins and its
  redirect sources.
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
- `publish-check --previous` compares two releases. **It is not yet called by `release build`
  or `publish-release.sh`.** Until it is, the cross-release guarantee is checked only when an
  operator runs it, and in tests. Wiring it into the release transaction is an owner decision
  (#747 follow-up).
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
- The JSON carries no official ELI link. The mapping could not be verified from a permitted
  source: [`docs/research/eli-assessment-2026-11-06.md`](research/eli-assessment-2026-11-06.md).
