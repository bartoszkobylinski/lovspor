# ELI assessment for Lovspor (#747, #819)

**Question.** Can Lovspor link a document to the act's official European Legislation Identifier
(ELI)? The mapping must be exact and must come from the act's official identity (type, date,
number). Nothing may be inferred from a slug or a title, and no pseudo-ELI may be invented.

**Answer, updated for #819.** Yes, at act level, for one attested shape:

```text
ref_id  {lov|forskrift}/{yyyy}-{mm}-{dd}-{n}
ELI     https://lovdata.no/eli/{lov|forskrift}/{yyyy}/{mm}/{dd}/{n}
```

`lovspor.eli.act_eli` implements this and returns `None` with a named reason for everything
else. **Nothing is emitted yet.** Site JSON and URLs are unchanged. Activation is a later
owner decision (§ Planned integration).

The first version of this file (#747, PR #821) answered "not now". The EUR-Lex register could
not be read then, and no Norwegian official example had been found. Both gaps are closed below.

## Source rule

`lovdata.no` is not read, `lovdata.no/eli/…` included (`robots.txt`: `Disallow: /eli/*`, owner
comment on #747, 2026-10-31). No request in this research went to `lovdata.no`. The permitted
sources are:

- `api.lovdata.no`;
- the EUR-Lex ELI register, read through the Internet Archive's stored copies, because EUR-Lex
  answers automated clients with an HTTP 202 challenge;
- Norwegian government specifications on GitHub;
- the Internet Archive's capture index (CDX). It returns URL, status and date metadata only. It
  was queried for a handful of exact URLs, and no archived `lovdata.no` page body was fetched.

## Evidence

All entries were read on 2026-11-06. Raw files were kept outside the repository; their hashes are given.

| # | Source | What it shows | Proves |
|---|---|---|---|
| E1 | `https://api.lovdata.no/swagger/api.json`, sha256 `fbcaf3f97d310318c244bf508784abd22754b2011c2f14db238d1cfb14b07cec` | ``refID`: FRBR \"work\"``. The spec contains no ELI. | `ref_id` is Lovdata's work identity, the same level an act ELI names. |
| E2 | Local publicData cache (`gjeldende-lover`, `gjeldende-sentrale-forskrifter`, mtime 2026-08-02) | No Norwegian ELI in any document. Only EU-act ELIs on `data.europa.eu` (106 occurrences). `RefID` `lov/1687-04-15` has no number. | Source data carries no ELI; numberless acts exist. |
| E3 | EUR-Lex ELI register, Norway: archive copy `https://web.archive.org/web/20260830220236id_/https://eur-lex.europa.eu/eli-register/norway.html`, sha256 `35b6b42794aebb2bdeb498c9f079679316c904fba70a31660e2f2228351a510d`. The page says "Last update: 06/10/2025". | "Organisation/state agency responsible for the ELI implementation: The Lovdata Foundation" · "Level of implementation: Pillar 3" · "Norwegian legislation is published using the following URI template: /eli/{jurisdiction}/{type}/{year}/{month}/{day}/{natural_identifier}/{level…}/{version}/{point in time}/{language}/{format}" · "ELI URIs are implemented for all consolidated legislation, both statutes and regulations." The "Examples" section is empty, and was already empty in the 2017 and 2018 copies. | Lovdata issues ELI for all consolidated acts and regulations. The component *order* is official. The template does not show how `{jurisdiction}` is written, or whether it is written at all. |
| E4 | Digdir DCAT-AP-NO, `Informasjonsforvaltning/dcat-ap-no@9f4d765c`. `docs/Klasse_Datasett.adoc:299–311` (sha256 prefix `440710ee984a86f4`) and three sibling class files at the same commit. | "For references to Lovdata, ELI-URI should be used" · `dcatap:applicableLegislation <https://lovdata.no/eli/forskrift/2016/12/15/1600>` | The national metadata standard recommends Lovdata ELIs. It attests the forskrift form with no jurisdiction segment. |
| E5 | Digdir CPSV-AP-NO, `Informasjonsforvaltning/cpsv-ap-no@d2c87346`, `docs/TemaKnytteTilRegelverk.adoc:23` | `rdfs:seeAlso <https://lovdata.no/eli/lov/1989/06/02/27/>` | Attests the lov form, a **zero-padded** month and day, and an unpadded number. A trailing slash appears here only. |
| E6 | Digdir SKOS-AP-NO, `Informasjonsforvaltning/skos-ap-no-begrep@d5f3c1b0`, `docs/To-måter-definisjon.adoc:30` | `dct:source <https://lovdata.no/eli/lov/1997/02/28/19/chapter/14>` | A zero-padded date again. A subdivision written `/chapter/14`. |
| E7 | DFØ eForms SDK (Norwegian public procurement), `anskaffelser/eforms-sdk-nor@29e1fd27`, `src/fields/national-e.yaml:155–171` | `https://lovdata.no/eli/forskrift/2016/8/12/974`, also `/975` and `/976`, as `schemeName = 'ELI'` | A 3-digit forskrift number is **unpadded**. The month is **unpadded** here, which conflicts with E5 and E6. |
| E8 | Internet Archive CDX index (metadata only) | HTTP 200 captures of `https://lovdata.no/eli/forskrift/2016/12/15/1600` (2026-03-20), `…/eli/lov/2005/05/20/28` (2021-10-02), `…/eli/lov/2005/05/20/28/nor` (2021-10-09) and `…/eli/lov/2016/05/27/14/nor/section/3-3` (2022-01-22). No capture under `/eli/no/`. No capture of either padding variant of E7's URI. | The archive observed the jurisdiction-less, **zero-padded** form returning 200. It also shows a language segment `nor`, and a subdivision placed after the language. |
| E9 | Corpus check, lovverk `a185a1dc` | Every act in E4–E8 is in the corpus with the matching `ref_id`: `forskrift/2016-12-15-1600`, `lov/1989-06-02-27`, `lov/1997-02-28-19`, `lov/2005-05-20-28`, and also `lov/1998-07-17-56`. | ELI date and number equal the `ref_id`'s date and number. |

## Classification

| Aspect | Status | Basis |
|---|---|---|
| Host and prefix `https://lovdata.no/eli/` | **verified** | E3, E4–E8 |
| Type segments `lov`, `forskrift` | **verified** | E4, E5, E7, E8 |
| No jurisdiction segment | **verified in practice** | Every attested URI (E4–E8) omits it, and the archive saw such URIs answer 200. The E3 template names `{jurisdiction}` without saying how it is written. This is recorded as a discrepancy, not resolved. |
| Date written `yyyy/mm/dd`, zero-padded | **verified** (emitted form) | E5, E6, E8 (HTTP 200 for `2005/05/20`) |
| Date written unpadded (`2016/8/12`) | **also attested, canonical status unknown** | E7. Which form Lovdata treats as canonical is not established from a permitted source. |
| Number written as unpadded decimal | **verified** | 27, 19, 28, 974, 1600 (E5–E8) |
| ELI number = `ref_id` number | **verified for the attested acts, inferred for the rest** | E9. E3 says every consolidated act has an ELI, and `ref_id` is Lovdata's only work identity (E1). |
| Old regulations with Lovdata-assigned numbers (29 pre-1980 forskrifter numbered ≥ 9000, e.g. `forskrift/1896-06-26-9805`) | **inferred** | The same rule applies, but no example is attested. |
| Act with no serial number (26 refs, e.g. `lov/1687-04-15`, Grunnloven `lov/1814-05-17`) | **unknown, returns `None`** | No source shows their ELI. |
| Subdivision (provision, chapter) | **unknown, not emitted** | E6 has `/chapter/14` with no language. E8 has `/nor/section/3-3` with the language first. They conflict, and the E3 template puts the level before the language. What a subdivision resolves to is not established. |
| Language (`/nor`; nynorsk), version, point in time | **unknown, not emitted** | E8 shows `nor` exists. Nothing establishes nynorsk, version or date segments. |
| Lovtidend (official journal) ELIs | **out of scope** | E3 says they exist. Lovspor publishes consolidated text only. |

## What the function emits

`act_eli(ref_id)` in `src/lovspor/eli.py`:

- A `ref_id` that fully matches `(lov|forskrift)/YYYY-MM-DD-N` maps to
  `https://lovdata.no/eli/{type}/{YYYY}/{MM}/{DD}/{N}`. The date must be a real calendar date,
  and N is an ASCII decimal without a leading zero. The date keeps the `ref_id`'s own zero
  padding, which is the E5, E6 and E8 form.
- Anything else gives `uri=None` with one of these reasons:
  - `no_serial_number`;
  - `invalid_date`;
  - `unsupported_shape`: other types, Lovtidend or local ids, subdivisions, languages,
    fragments, whitespace, non-ASCII digits, leading-zero numbers, extra segments.
- The URI is the whole act. Its path has exactly five segments after `/eli/`, and it never
  identifies a provision. A provision companion may carry it only as *the act this provision
  belongs to*.
- A returned URI is **constructed, not resolved**. No request is made.

## Corpus audit

`lovspor ops eli-audit --corpus <lovverk> --ref a185a1dc84accf7714f5c09ebb879efa445f5b0c`,
run read-only on 2026-11-06:

| | count |
|---|---|
| published documents | 5,890 (5,889 distinct `ref_id`s: Grunnloven nb and nn share one) |
| mapped | 5,864 |
| `no_serial_number` | 26 |
| `invalid_date`, `unsupported_shape` | 0 |
| mapped with a zero-padded month or day | 4,241 |

The last row is the exposure to the open padding question. If Lovdata's canonical form is
E7's unpadded one, these 4,241 URIs would differ from it; they are still the form E8 observed
answering 200.

Mapped `ref_id`s by era:

| | `< 1950` | `1950–1979` | `1980–1999` | `≥ 2000` |
|---|---|---|---|---|
| lov | 58 | 91 | 134 | 445 |
| forskrift | 30 | 235 | 885 | 3,986 |

## Planned integration (not done; needs an owner decision)

- **Field.** Optional `official_eli_url` in document companions. In provision companions it
  would name the parent act, labelled as the act. It is absent when `act_eli` returns `None`.
- **Churn**, measured on a scratch build of lovverk `a185a1dc` (5,890 documents, 93,022 pages):
  - **JSON-only field:** 5,864 document companions and 86,625 provision companions change
    bytes. `representation_hash` is the SHA-256 of the HTML page, so it does **not** change.
    Pages, sitemaps and redirects are untouched. (The PR #821 version of this file said the
    hash would change; that was wrong for a JSON-only field.)
  - **Link also shown on the page's source block:** all 93,022 HTML pages change, and so does
    every `representation_hash`. That is a whole-corpus re-render, which the owner has declined
    for unrequested fields before (ADR-0013 Amendment 3).
- **Schema.** A new optional field leaves `schema_version` at `"1"` only if consumers tolerate
  unknown keys. `tests/unit/test_publish_invariants.py`'s `COMPANION_ENVELOPE` would have to
  admit it. Whether to bump `schema_version` is part of the decision.
- **Decisions this needs:**
  1. Accept the zero-padded form despite E7, or wait for Lovdata's canonical form.
  2. JSON-only, or JSON plus page.
  3. When: alone, or riding the next change that re-renders every companion anyway.
