# Corpus data quality, issue #624: the three open questions re-measured

Measured 2026-10-28 on `lovverk` `main` at `e60b33c15` (5,888 current central
documents, every one at `renderer_version: 8`) with
`scripts/measure_corpus_quality.py`, against the Lovdata tarballs cached on
2026-08-02 in `data/cache/` for the upstream comparison. Reproduce:

```bash
uv run python scripts/measure_corpus_quality.py /abs/path/to/lovverk \
    --xml-cache data/cache --json corpus-quality.json
```

Every count below is a [FACT] from that run unless marked otherwise. The
historical counts in `docs/publication-plan.md` (9, 230, ~4,800) were taken
on an older corpus and are not comparable one to one.

## 1. `last_change_in_force` before `date_in_force`: 32 documents, upstream

5 laws and 27 regulations (the JSON lists them). For every one of the 32 the
cached XML header carries the same two values the front matter does:
`<dd class="dateInForce">` and `<dd class="lastChangeInForce">` are mapped
field for field by `rendering/document.py` (`_find_dd`, `_parse_date`), so
this is a property of the source, not a mapping error.

Two shapes:

- **27 single-valued.** The regulation was amended before it entered into
  force: `sf-19890623-0413` is in force from 1992-04-03 and its last change
  from 1992-03-18; `sf-20160318-0264` from 2017-01-01 and 2016-12-13;
  `nl-20250620-102` (viltressursloven) from 2026-07-01 and 2026-06-19. Lovdata
  dates the amendment's own commencement, which can precede the base act's.
- **5 multi-valued.** Lovdata lists several commencement dates in one field,
  not always in chronological order: `nl-20111216-059` carries
  `2015-01-01, 2012-02-01`, `sf-20240126-0111` `2025-01-01, 2024-01-26`,
  `sf-20201211-2714` `2021-04-26, 2021-05-31, 2021-06-14`, `sf-20240214-0257`
  `2024-03-15, 2027-03-15`. The renderer keeps the leading date only
  (`_DATE_ONLY` matches at the start of the field), so the front matter says
  2015-01-01 for a law partly in force since 2012-02-01, and the "reversal"
  against a 2014-12-19 change is an artefact of that choice.

**Finding:** no defect in the mapping. The multi-valued field is a real
simplification: `date_in_force` is documented as descriptive metadata
(`mcp.py`), and the leading-date rule drops the other dates without saying
so. Whether to carry the whole list (a front-matter change for at least the
5 documents here plus every multi-valued field that happens not to reverse —
not counted) is an owner decision; nothing to fix for the reversal itself.

## 2. No in-force date at all: 228 documents, three causes

41 laws and 187 regulations have neither `date_in_force` nor
`last_change_in_force`. Their cached XML headers fall into three classes:

| class | laws | regulations | what the header says |
|---|---|---|---|
| no `dateInForce` field | 4 | 73 | nothing; very old instruments (`nl-17410217-000` Forbud paa Vimpel-Føring, `nl-17750608-000`, `nl-18450607-000`, `nl-19090323-000`; resolutions from 1929 onwards) |
| text only | 27 | 114 | `Kongen bestemmer` (25) and `Kongen fastset`, `Budsjettåret 2026` (25), `Inntektsåret <year>` (one per tax year), `Når endringer i overenskomst trer i kraft` |
| text first, dates after it | 10 | 0 | `Kongen bestemmer, 2004-01-01, 2005-01-01` and the like |

- **77 upstream.** The field is absent in the source. Nothing to map.
- **141 lossy but faithful.** The source states a condition, not a date.
  `_parse_date` keeps a leading ISO date and nothing else, so the front
  matter cannot tell "in force when the King decides" or "the budget year
  2026" from "unknown". The condition is lost, not misrepresented.
- **10 lost dates, ours.** All amending acts (`Lov om endringer i …`,
  `nl-20030620-044` … `nl-20160617-045`): the source states a condition
  *and* the dates its parts entered into force (`Kongen bestemmer,
  2004-01-01, 2005-01-01`), and the leading-date rule keeps none of them.
  This is the one class where the renderer drops dates the source has.

**Finding:** the absence is upstream for 77 documents and a renderer
simplification for 151, of which 10 lose real dates. A fix means a
front-matter contract change (keep the condition text, or the date list, or
both) and a re-render of the affected documents under a renderer bump;
`date_in_force` is read by `publish/inventory.py` and the site pages, so the
shape is an owner decision. Not changed here.

## 3. Footnote markers glued to words: resolved in renderer 8; retrieval covered

The glued form (`Amtskasserere1`) no longer exists in the corpus: renderer
commit `96998d8e` ("delimit footnote markers so they stop fusing into
words") renders every in-text marker as `[^n]`, and all 5,888 current
documents are at renderer 8. Measured now:

- `[^n]` markers in bodies: **11,763**, of which **4,457** sit flush against
  the word they annotate (`Amtskasserere[^1]`) and the rest follow
  whitespace or punctuation.
- Word+digit tokens of the old glued shape (a lower-case word of five or
  more letters followed by one digit): **254 in 73 documents**, and the top
  ones are source text, not markers: `vedlegg1` (73), `vedlegg2` (55),
  `vedlegg4` (15), `vedlegg3` (13), `CHOHCH3` (12), `NaHCO3` (6), `MenaQ7`
  (5), `CaHPO4` (5) — annex references written without a space, and chemical
  formulae.
- The one deliberate drop stands: markers on `legalArticleHeader` are not
  emitted (documented in `_render_legal_article_header`; 0 `## §` lines
  carry a marker, per `renderer-v8-heading-migration-2026-08-02.md`).

The retrieval question the plan left open is answered by code that landed
with the renderer change: `search_body` compiles the query with
`_marker_tolerant_query`, which lets a `[^n]` interrupt the phrase anywhere,
and `verify_quote` normalises markers away. `build_embedding_inputs`
(`embeddings/inputs.py`) does not strip them, so an embedded chunk carries
`[^1]` tokens beside intact words; the word itself is spellable, which was
the concern. Untested and not measured here: whether those tokens move
`semantic_search` rankings.

**Finding:** closed as a corpus defect; the remaining question is a minor
embedding-noise question, not a data-quality one.

## Owner decisions this leaves

1. Multi-valued and conditional `dateInForce`: keep the leading date (status
   quo, documented here), or extend the front matter to carry the list and
   the condition. The 10 amending acts that lose real dates are the concrete
   case for extending it.
2. Nothing for the 32 reversed pairs: they state what Lovdata states.
3. Nothing for footnote markers.
