# ELI assessment for Lovspor (#747), 2026-11-06

**Question.** Can Lovspor attach an official European Legislation Identifier (ELI) to a
document's JSON as a link to the act? The mapping must be exact and must come from the act's
official identity (type, date, number, jurisdiction). Nothing may be inferred from a slug or a
title, and no pseudo-ELI may be invented.

**Answer: not now.** No permitted source available to this session establishes the Norwegian
ELI template. So nothing is emitted: no `official_eli_url` field, and no ELI-shaped path.
The URL contract states the boundary ([`docs/url-contract.md`](../url-contract.md) §6).

## Source rule

`lovdata.no` pages are not read, and that includes `lovdata.no/eli/…`. The owner comment of
2026-10-31 on #747 records that `lovdata.no/robots.txt` has `Disallow: /eli/*`. The permitted
sources are `api.lovdata.no`, the EUR-Lex ELI register and Publications Office pages, and
Digdir/data.norge.no. No crawling.

## Evidence

| # | Source | Read on | What it shows | Status |
|---|---|---|---|---|
| E1 | `https://api.lovdata.no/swagger/api.json`, 115,896 bytes, sha256 `fbcaf3f97d310318c244bf508784abd22754b2011c2f14db238d1cfb14b07cec` | 2026-11-06 | Lovdata's identifiers are `refID`: FRBR "work" and `dokID`: FRBR "expression" (verbatim: ``refID`: FRBR \"work\"``). The spec contains no ELI. All 7 case-insensitive `eli` substrings fall inside `X-RateLimit-…` and `timeline`. | verified |
| E2 | Local publicData cache, `gjeldende-lover.tar.bz2` and `gjeldende-sentrale-forskrifter.tar.bz2` (mtime 2026-08-02) | 2026-11-06 | No Norwegian ELI in any document. The only `/eli/` URLs point at EU acts on `data.europa.eu` (106 occurrences, all in forskrifter footnotes, e.g. "ELI: http://data.europa.eu/eli/reg/2004/1935/oj"). The header has `RefID` `lov/1687-04-15`: **some acts carry no serial number.** | verified |
| E3 | `https://eur-lex.europa.eu/eli-register/norway.html`, `…/news_item_13.html` | 2026-11-06 | The pages returned an empty body to the fetch tool and HTTP 202 with 0 bytes to curl, so they could not be read. Search-engine summaries of them say the implementer is the Lovdata Foundation, the start was 2016 (beta) or 2017, and the metadata is RDFa in pages. One summary attributes a template of the form `/eli/{jurisdiction}/{type}/{year}/{month}/{day}/{natural_identifier}/…`. | **unverified**: a summary, not text that was read |
| E4 | `https://en.wikipedia.org/wiki/European_Legislation_Identifier` | 2026-11-06 | Secondary source. It lists Norway among implementers as of 2023-01-01. It says ELI components are optional and "do not have a pre-defined order", **so a national order cannot be derived from the generic standard.** | verified (secondary) |
| E5 | Digdir / DCAT-AP-NO | 2026-11-06 | No source found that links DCAT-AP-NO to ELI for Norwegian legislation. | not established |

## Findings

1. **Norway uses ELI, and Lovdata issues it.** This is likely (E3 summaries, E4), but the
   register page was not read verbatim.
2. **The Norwegian URI template is not established.** E3's template is a paraphrase from a
   summary. E4 shows that order is national. Building `…/eli/lov/2024/12/20/96` from
   `ref_id` would be a guess, which is the pseudo-ELI the issue forbids. A Lovspor form that
   differed from Lovdata's would be worse than none (owner, #747).
3. **Section level is not established.** No source read today shows whether a Norwegian
   subdivision ELI exists, or whether it resolves to the provision or to the whole act. Any
   link Lovspor emits must therefore stay at ACT level. A provision's JSON could carry it only
   as "the act this provision belongs to", never as the provision's identifier.
4. **Acts with no number exist** (E2: `lov/1687-04-15`). How ELI handles them is not
   established, so even a confirmed template might not cover every document.
5. **Lovspor URL ≠ ELI.** The Lovspor canonical URL names Lovspor's derived representation, and
   `ref_id` is Lovdata's work identity. An ELI would be a third, official identifier, linked
   to and never minted.

## What would unblock M4 (follow-up issue)

- A written statement of the Norwegian template from a permitted source. Two routes: the owner
  reads the EUR-Lex register page in a browser and records it verbatim with a date, or a
  written answer from `eli@lovdata.no` (address per E3 summary, unverified). Either should also
  cover regulations versus acts, acts without a number, language, and the subdivision level.
- Then: a pure `ref_id → ELI` function, tested against examples **quoted from that source**,
  that returns nothing for any shape the source does not cover.
- **Churn.** Adding a field to every companion changes every companion's bytes and so every
  `representation_hash`: a whole-corpus re-render. ADR-0013 Amendment 3 shows that the owner
  declines such churn for unrequested fields. So the field should ride along with the next
  change that re-renders every companion anyway, or come with an owner decision.
