#!/usr/bin/env python
"""Byte-level and content-level audit of the rendered lovverk corpus.

Read-only. Never writes to the corpus, never calls OpenAI, never commits.

Motivation
----------
``lovspor audit`` is *stamp*-based: it confirms every document carries a
``renderer_version`` equal to the current renderer. That is a provenance
claim, not a correctness claim. In July 2026 a renderer bug left 413/781
laws silently missing ~6.3M characters while every stamp looked healthy.
This tool measures the two things the stamp cannot.

Check A — REPRODUCIBILITY
    Re-render each document from its source Lovdata XML with the current
    renderer and byte-compare against the Markdown committed in lovverk.
    Catches corruption, partial writes, and stamps applied without an
    actual re-render.

    Blind spot: if the renderer itself is wrong, re-rendering reproduces
    the same wrong bytes and check A passes. That is why check B exists.

Check B — CONTENT COVERAGE (the load-bearing check)
    Independently of the renderer, pull the text content out of the source
    XML body and verify the rendered Markdown preserves it. TWO metrics,
    because there are two distinct failure modes and conflating them
    misdiagnoses both.

    B1. CHAR COVERAGE — "is the text still there at all?"
        Both sides are reduced to a pure alphanumeric character stream
        (lowercased, every non-alphanumeric character removed, whitespace
        included). Coverage is measured over overlapping character
        ``CHAR_NGRAM``-grams:

            char_coverage = sum_g min(n_xml[g], n_md[g]) / sum_g n_xml[g]

        Stripping punctuation and whitespace on *both* sides makes this
        immune to how the renderer spaces things — it answers only
        "did the characters survive". This is the metric that would have
        caught the July 2026 disaster.

    B2. TOKEN COVERAGE — "are word boundaries intact?"
        The same one-directional multiset comparison, but over ``\\w+``
        word tokens weighted by token length:

            token_coverage = sum_t min(n_xml[t], n_md[t]) * len(t)
                           / sum_t n_xml[t] * len(t)

        A document can score ~100% char coverage and poor token coverage.
        That means the characters survived but ran together — e.g. a table
        flattened by inline rendering emits ``ArtAvlsfjørfeProduksjonsfjørfe``
        where the XML had three separate cells. The legal text is not
        *missing*, but it is no longer searchable or quotable, so it is
        reported as its own defect class rather than hidden inside a
        single blended score.

    Both are one-directional on purpose: content the renderer *adds*
    (link URLs, table pipes, heading marks) cannot raise either score,
    and content it *drops* lowers them proportionally.

    Two variants of the XML side are reported:
      raw       — every text node in <main>.
      adjusted  — with the renderer's two DOCUMENTED drops elided first:
                  not-in-force subtrees (``futuretitle``,
                  ``futureLegalArticle``, ``futureLegalArticleHeader``), and
                  the footnote marker a ``legalArticleHeader`` carries but
                  does not render. Each is defended in a renderer docstring;
                  the audit subtracts what the renderer declares, never what
                  it merely happens to do.
    Flagging uses ``adjusted``; ``raw`` is reported so the size of the
    intentional drop stays visible.

    Word boundaries come from the markup, not from every element seam: inline
    markup runs through words (``CO<sub>2</sub>`` is one word), so joining
    ``itertext()`` with a space would report the renderer's correct output as
    invented text. See :func:`_all_text`.

    Structural counters, also renderer-independent: count of "§" in the
    XML body text vs in the Markdown, and count of XML ``<tr>`` elements
    vs Markdown table data rows.

Vintage skew
------------
The corpus is rendered from a nightly Lovdata snapshot. If the tarball
this tool reads is a different vintage than the one that produced the
committed Markdown, unchanged-looking documents show false mismatches.
Skew is detected *exactly*, per document, by recomputing
``hash_normalized_xml`` and comparing it to the manifest's ``xml_hash``
— not by date heuristics. Documents whose hash differs are bucketed
``VINTAGE_SKEW``, annotated with ``last_changed``, and excluded from the
drift verdict while still being counted and reported.

Unparseable / unrenderable documents are counted as UNKNOWN and reported
explicitly. They are never silently dropped — silent drops are precisely
how the July bug hid.

Usage
-----
    python scripts/audit_render_bytes.py --sample 100
    python scripts/audit_render_bytes.py --full --json out.json
    python scripts/audit_render_bytes.py --docs sf-20220407-0637,...

Both inputs default to the same environment the sync itself runs on
(``.env`` included, via :func:`lovspor.settings.load_env`): the corpus from
``LOVSPOR_OUTPUT_REPO_PATH`` and the tarballs from
``$LOVSPOR_DATA_DIR/cache/archives`` — the directory
``sync.orchestrator`` downloads them into. Either can be overridden on the
command line; neither is guessed if the environment is silent.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tarfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path

from lxml import etree

from lovspor.errors import ParseError, RenderError
from lovspor.parsing.xml_normalizer import hash_normalized_xml, safe_parser
from lovspor.rendering.document import FrontmatterContext
from lovspor.rendering.markdown_renderer import RENDERER_VERSION
from lovspor.settings import load_env
from lovspor.storage.manifest import read_manifest
from lovspor.sync.document_io import doc_type_for_dataset, render_full_document

# Mirrors markdown_renderer._NOT_IN_FORCE_CLASSES. Duplicated rather than
# imported so that check B stays independent of the renderer module's
# internals: if the renderer silently changes what it elides, this tool
# must NOT follow it automatically — the divergence is a finding.
NOT_IN_FORCE_CLASSES = frozenset(
    {"futuretitle", "futureLegalArticle", "futureLegalArticleHeader"},
)

# The other documented renderer drop: a legalArticleHeader renders from these
# two spans only. Duplicated, not imported, for the reason above.
LEGAL_ARTICLE_HEADER_CLASS = "legalArticleHeader"
LEGAL_ARTICLE_VALUE_CLASS = "legalArticleValue"
LEGAL_ARTICLE_TITLE_CLASS = "legalArticleTitle"
# h1/h2 keep everything (_render_heading walks them inline); only h3-h6 reach
# _render_legal_article_header.
SUB_HEADING_TAGS = frozenset({"h3", "h4", "h5", "h6"})

# Which XML elements put a word boundary between two text nodes. Block-level by
# HTML semantics: crossing one starts a new line of prose, so the words on
# either side are separate words. Everything else (<sub>, <sup>, <a>, <i>,
# <span>, a footnote reference) is inline markup INSIDE a word: "CO<sub>2</sub>"
# is one word, "km<sup>2</sup>" is one word, and a footnote marker sits flush
# against the word it annotates.
#
# Duplicated from HTML rather than imported from the renderer, for the same
# reason as NOT_IN_FORCE_CLASSES: the audit must be able to disagree with the
# renderer. Importing markdown_renderer._BLOCK_TAGS would make every renderer
# flattening bug invisible here by construction.
BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "caption",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    },
)
# Inline tags that are word boundaries all the same, because Markdown has no
# way to emit them without a delimiter: emphasis becomes *…*, a link [ …](…).
# "E<i>ff</i>EXT" can only come out as "E*ff*EXT", which reads as three tokens
# whatever the renderer does, so counting the source as one would report a
# fabrication that no rendering could avoid. <sub>, <sup> and <span> carry no
# such delimiter and stay glue.
MARKED_INLINE_TAGS = frozenset({"a", "em", "i", "strong"})
# <sup class="footnotereference"> joined them at renderer 5, which emits "[^1]"
# instead of a bare "1". Class-scoped: a plain <sup> is still glue.
FOOTNOTE_REFERENCE_CLASS = "footnotereference"

DATASETS = ("gjeldende-lover", "gjeldende-sentrale-forskrifter")
ARCHIVE_NAME = "{dataset}.tar.bz2"

WORD_RE = re.compile(r"\w+", re.UNICODE)
# Character n-gram width for the char-coverage metric. Long enough that
# incidental collisions between unrelated legal boilerplate are negligible,
# short enough that a single lost cell boundary perturbs only ~k n-grams.
CHAR_NGRAM = 12
# Renderer escapes these in inline text (_INLINE_ESCAPE, plus the "(" of a "]("
# pair since renderer 6) and at line starts (_escape_line_leading: # > | - + and
# the dot/paren of an ordered marker).
MD_ESCAPE_ANY_RE = re.compile(r"\\([\\`*#>|\-+.()])")
# [text](destination) -> the destination is an XML attribute, not text.
MD_LINK_DEST_RE = re.compile(r"\]\([^)\s]*\)")
# Synthesized list markers at line start (unescaped forms only). Repeated,
# because a nested list whose first item shares the outer item's line opens
# with two of them ("9. 1. Landbruksdepartementet..."); stripping only the
# first leaves a bare "1" that reads as a word the renderer invented.
MD_LIST_MARKER_RE = re.compile(r"^([ \t]*)(?:(?:\d{1,9}[.)]|[-+])[ \t]+)+", re.MULTILINE)
# Synthesized ATX heading / blockquote marks at line start.
MD_BLOCK_MARK_RE = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?)", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
RETRIEVED_AT_RE = re.compile(r'^retrieved_at:\s*"([^"]+)"', re.MULTILINE)
# A Markdown table data row: starts with |, and is not the |---|---| rule.
MD_TABLE_ROW_RE = re.compile(r"^\s*\|(?!\s*:?-+:?\s*\|).*\|\s*$", re.MULTILINE)


# --------------------------------------------------------------------------
# text extraction
# --------------------------------------------------------------------------


def _classes(elem: etree._Element) -> list[str]:
    return (elem.get("class") or "").split()


def _remove_keeping_tail(elem: etree._Element) -> None:
    """Drop ``elem`` and its subtree, but keep its tail.

    The tail is the parent's text that follows the element, not part of what
    is being elided; dropping it with the subtree would understate the XML
    side and hide real loss.
    """
    parent = elem.getparent()
    if parent is None:
        return
    tail = elem.tail
    prev = elem.getprevious()
    parent.remove(elem)
    if tail and tail.strip():
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail


def _elide_not_in_force(root: etree._Element) -> None:
    """Drop not-in-force subtrees in place (documented renderer behaviour)."""
    for elem in list(root.iter()):
        if elem.getparent() is not None and any(c in NOT_IN_FORCE_CLASSES for c in _classes(elem)):
            _remove_keeping_tail(elem)


def _elide_dropped_heading_marks(root: etree._Element) -> None:
    """Drop what a ``legalArticleHeader`` renders without (documented drop).

    ``_render_legal_article_header`` builds "### {value}. {title}" from the two
    spans alone and emits nothing else the header carries — in this corpus
    always a footnote-reference marker, 424 of them in 83 documents. Its
    docstring states why: 206 of the markers sit on the § number, so emitting
    them gives "### § 5.1", which reads as a citation of subsection 5.1, and
    the footnote's own text renders in the § anyway.

    Elided here for the same reason ``NOT_IN_FORCE_CLASSES`` is: a documented,
    intentional drop counted as loss would keep the metric permanently short of
    zero and train the reader to ignore it. It stays visible in the *raw*
    variant, which elides nothing.

    Only h3-h6: ``_render_heading`` sends h1/h2 through the full inline walk,
    which keeps every marker, so eliding there would hide a real loss.
    """
    for header in root.iter():
        if header.tag not in SUB_HEADING_TAGS or LEGAL_ARTICLE_HEADER_CLASS not in _classes(header):
            continue
        value = header.find(f".//span[@class='{LEGAL_ARTICLE_VALUE_CLASS}']")
        if value is None:
            continue  # no value span: the renderer falls back to _inline, keeping everything
        title = header.find(f".//span[@class='{LEGAL_ARTICLE_TITLE_CLASS}']")
        spans = [span for span in (value, title) if span is not None]
        for child in list(header):
            # Only text is dropped. A childless <br/> between the two spans
            # carries none, and removing it would delete a word boundary the
            # renderer replaces with its synthesized ". " — turning
            # "Artikkel 34 Overgangsbestemmelser" into one token.
            if not "".join(child.itertext()).strip():
                continue
            # A child that CONTAINS a rendered span is a wrapper, not a drop.
            if any(any(node is span for node in child.iter()) for span in spans):
                continue
            _remove_keeping_tail(child)


def _body_root(xml_bytes: bytes) -> etree._Element:
    """Return the ``<main class="documentBody">`` element.

    Document metadata lives in ``<header>`` and is rendered into YAML
    frontmatter (or deliberately not rendered at all, e.g. legalArea,
    table-of-contents). Only ``<main>`` is body content, so only ``<main>``
    is a fair denominator for body coverage.
    """
    tree = etree.parse(BytesIO(xml_bytes), parser=safe_parser(remove_blank_text=False))
    root = tree.getroot()
    main = root.find(".//main")
    if main is None:
        raise ParseError("no <main> element in document")
    return main


def _all_text(elem: etree._Element) -> str:
    """Subtree text with a space only where the markup means a word boundary.

    ``" ".join(itertext())`` puts one at every seam, which splits every word
    that inline markup runs through — ``CO<sub>2</sub>`` became "co" + "2",
    ``km<sup>2</sup>`` "km" + "2". The renderer joins them, correctly, and the
    joined form then counted as a word the renderer had invented: 13,499 of the
    13,501 fabricated words this tool reported on 2026-07-27 were this artefact,
    not a defect.

    A seam is a boundary when a block element is opened or closed between the
    two text nodes — including one that carries no text of its own, such as the
    ``<br/>`` between two lines of a table cell, which is why the crossed
    elements are accumulated during the walk rather than derived from the two
    text nodes' ancestries.
    """
    parts: list[str] = []
    crossed: list[etree._Element] = []

    def walk(node: etree._Element) -> None:
        nonlocal crossed
        crossed.append(node)
        if node.text:
            _append(parts, crossed, node.text)
            crossed = []
        for child in node:
            walk(child)
            crossed.append(child)
            if child.tail:
                _append(parts, crossed, child.tail)
                crossed = []

    walk(elem)
    return "".join(parts)


def _separates_words(elem: etree._Element) -> bool:
    """Whether crossing ``elem`` puts a word boundary between two text nodes."""
    if elem.tag in BLOCK_TAGS or elem.tag in MARKED_INLINE_TAGS:
        return True
    # A footnote reference renders as "[^1]" since renderer 5, so the marker no
    # longer runs into the word it annotates and the two are separate tokens on
    # the Markdown side. A plain <sup> — "km<sup>2</sup>" — carries no delimiter
    # and stays glue, which is why this is decided per class, not per tag.
    return elem.tag == "sup" and FOOTNOTE_REFERENCE_CLASS in _classes(elem)


def _append(parts: list[str], crossed: list[etree._Element], text: str) -> None:
    if parts and any(_separates_words(elem) for elem in crossed):
        parts.append(" ")
    parts.append(text)


def _words(text: str) -> Counter[str]:
    return Counter(WORD_RE.findall(text.lower()))


def _alnum_stream(text: str) -> str:
    """Lowercased alphanumeric-only character stream.

    Whitespace and punctuation are removed on BOTH sides, so the char
    metric measures survival of the characters themselves and is immune
    to how the renderer spaces or punctuates its output.
    """
    return "".join(c for c in text.lower() if c.isalnum())


def _ngrams(stream: str, k: int = CHAR_NGRAM) -> Counter[str]:
    if len(stream) < k:
        return Counter([stream]) if stream else Counter()
    return Counter(stream[i : i + k] for i in range(len(stream) - k + 1))


def _weighted_total(counts: Counter[str]) -> int:
    return sum(n * len(t) for t, n in counts.items())


def _char_coverage(xml_stream: str, md_stream: str) -> float:
    """Fraction of the XML's character n-grams present in the Markdown."""
    xg = _ngrams(xml_stream)
    total = sum(xg.values())
    if total == 0:
        return 1.0
    mg = _ngrams(md_stream)
    return sum(min(n, mg.get(g, 0)) for g, n in xg.items()) / total


def _coverage(xml_counts: Counter[str], md_counts: Counter[str]) -> float:
    total = _weighted_total(xml_counts)
    if total == 0:
        return 1.0
    matched = sum(min(n, md_counts.get(t, 0)) * len(t) for t, n in xml_counts.items())
    return matched / total


def _missing_words(xml_counts: Counter[str], md_counts: Counter[str], limit: int = 12) -> list[str]:
    """Highest-weight word tokens present in the XML but short in the MD."""
    deficits = []
    for token, n in xml_counts.items():
        gap = n - md_counts.get(token, 0)
        if gap > 0:
            deficits.append((gap * len(token), token, gap))
    deficits.sort(reverse=True)
    return [f"{tok}(x{gap})" for _, tok, gap in deficits[:limit]]


def _md_body(md_text: str) -> str:
    """Markdown body reduced to the text the renderer took FROM the XML.

    Strips constructs the renderer *synthesizes* and that therefore have no
    counterpart in the source text. Leaving them in silently deflates the
    char metric, because they splice alphanumerics into the middle of the
    stream and break every n-gram spanning the splice:

      * link destinations — ``[text](forskrift/2003-11-06-1318)``: the href
        is an XML *attribute*, never a text node.
      * ``<br>`` inside table cells — ``_render_table_cell`` substitutes it
        for a newline that would otherwise break the GFM row.
      * ordered/bullet list markers — ``_render_list_lines`` prefixes
        ``"{index}. "`` / ``"- "``, numbering the XML never spelled out.
        Body text that genuinely starts that way is backslash-escaped by
        ``_escape_line_leading``, so matching the *unescaped* form only
        removes synthesized markers — and matching a *run* of them is safe
        for the same reason, which is what a nested list sharing its parent's
        line ("9. 1. ") needs.
      * ATX heading marks and blockquote marks, for the same reason.

    Escapes are undone last, so an escaped ``1\\.`` is not mistaken for a
    synthesized list marker while the markers are being removed.
    """
    body = FRONTMATTER_RE.sub("", md_text, count=1)
    body = MD_LINK_DEST_RE.sub("]", body)
    body = body.replace("<br>", " ")
    body = MD_LIST_MARKER_RE.sub(r"\1", body)
    body = MD_BLOCK_MARK_RE.sub("", body)
    return MD_ESCAPE_ANY_RE.sub(r"\1", body)


# --------------------------------------------------------------------------
# result model
# --------------------------------------------------------------------------


@dataclass
class DocResult:
    doc_id: str
    slug: str = ""
    dataset: str = ""
    status: str = ""  # OK | FLAG | VINTAGE_SKEW | UNKNOWN
    reasons: list[str] = field(default_factory=list)
    vintage_match: bool | None = None
    last_changed: str | None = None
    reproducible: bool | None = None
    byte_delta: int | None = None
    char_coverage_raw: float | None = None
    char_coverage_adjusted: float | None = None
    coverage_raw: float | None = None
    coverage_adjusted: float | None = None
    xml_body_chars: int | None = None
    md_body_chars: int | None = None
    xml_section_marks: int | None = None
    md_section_marks: int | None = None
    xml_table_rows: int | None = None
    md_table_rows: int | None = None
    missing_sample: list[str] = field(default_factory=list)
    novel_tokens: int | None = None
    novel_sample: list[str] = field(default_factory=list)
    error: str | None = None


def audit_one(  # noqa: PLR0912, PLR0915
    doc_id: str,
    record: dict,
    xml_bytes: bytes,
    corpus_root: Path,
    coverage_threshold: float,
    char_threshold: float,
) -> DocResult:
    res = DocResult(
        doc_id=doc_id,
        slug=record["slug"],
        dataset=record["source_dataset"],
        last_changed=record.get("last_changed"),
    )

    # --- vintage ---------------------------------------------------------
    try:
        computed = hash_normalized_xml(xml_bytes)
    except Exception as exc:  # must never silently drop a document
        res.status = "UNKNOWN"
        res.error = f"hash failed: {type(exc).__name__}: {exc}"
        res.reasons.append("hash_error")
        return res
    res.vintage_match = computed == record["xml_hash"]

    md_path = corpus_root / record["markdown_path"]
    if not md_path.exists():
        res.status = "FLAG"
        res.reasons.append("missing_markdown_file")
        return res
    md_text = md_path.read_text(encoding="utf-8")

    # --- check A: reproducibility ---------------------------------------
    if res.vintage_match:
        # ``retrieved_at`` is provenance bookkeeping, not content: a forced
        # re-render preserves the *prior* value in the file
        # (orchestrator._rerender_upstream) while the manifest's ``last_seen``
        # advances to the sync's ``now``, so the two legitimately disagree.
        # Seed the context from the file's own frontmatter, so check A
        # compares rendered *content* rather than re-deriving a timestamp we
        # already know cannot be reconstructed. A missing/unparseable
        # retrieved_at falls back to the manifest value and is reported.
        m = RETRIEVED_AT_RE.search(md_text[:4000])
        if m:
            retrieved_at = m.group(1)
        else:
            retrieved_at = record["last_seen"]
            res.reasons.append("no_retrieved_at_in_frontmatter")
        ctx = FrontmatterContext(
            doc_id=doc_id,
            slug=record["slug"],
            doc_type=doc_type_for_dataset(record["source_dataset"]),
            xml_hash=computed,
            source_dataset=record["source_dataset"],
            retrieved_at=retrieved_at,
        )
        try:
            rendered = render_full_document(xml_bytes, ctx)
        except (RenderError, ParseError) as exc:
            res.reproducible = False
            res.reasons.append("render_error")
            res.error = f"{type(exc).__name__}: {exc}"
        else:
            res.reproducible = rendered == md_text
            if not res.reproducible:
                res.byte_delta = len(md_text) - len(rendered)
                res.reasons.append("byte_mismatch")

    # --- check B: content coverage (renderer-independent) ----------------
    try:
        main_raw = _body_root(xml_bytes)
        xml_text_raw = _all_text(main_raw)
        main_adj = _body_root(xml_bytes)
        _elide_not_in_force(main_adj)
        _elide_dropped_heading_marks(main_adj)
        xml_text_adj = _all_text(main_adj)
        xml_tr = len(main_adj.findall(".//tr"))
    except (ParseError, etree.XMLSyntaxError) as exc:
        res.status = "UNKNOWN"
        res.error = f"xml parse failed: {type(exc).__name__}: {exc}"
        res.reasons.append("xml_parse_error")
        return res

    md_body = _md_body(md_text)
    md_counts = _words(md_body)
    raw_counts = _words(xml_text_raw)
    adj_counts = _words(xml_text_adj)
    md_stream = _alnum_stream(md_body)

    res.char_coverage_raw = _char_coverage(_alnum_stream(xml_text_raw), md_stream)
    res.char_coverage_adjusted = _char_coverage(_alnum_stream(xml_text_adj), md_stream)
    res.coverage_raw = _coverage(raw_counts, md_counts)
    res.coverage_adjusted = _coverage(adj_counts, md_counts)
    res.xml_body_chars = _weighted_total(adj_counts)
    res.md_body_chars = _weighted_total(md_counts)
    res.xml_section_marks = xml_text_adj.count("§")
    res.md_section_marks = md_body.count("§")
    res.xml_table_rows = xml_tr
    res.md_table_rows = len(MD_TABLE_ROW_RE.findall(md_body))
    res.missing_sample = _missing_words(adj_counts, md_counts)

    # Words that exist in the Markdown but in NO text node of the source XML.
    # After the synthesized-markup normalisation in _md_body, a faithful
    # render cannot invent a word, so any hit is a fabricated token — almost
    # always two adjacent blocks fused without a separator
    # ("inneholde" + "opplysninger" -> "inneholdeopplysninger"). This is the
    # most legible evidence of boundary corruption, so it is counted, not
    # merely inferred from the token ratio.
    novel = {t: n for t, n in md_counts.items() if t not in adj_counts}
    res.novel_tokens = sum(novel.values())
    res.novel_sample = sorted(novel, key=len, reverse=True)[:8]

    # Missing content (severe) and boundary corruption (distinct, less
    # severe) are flagged separately so neither hides inside the other.
    if res.char_coverage_adjusted < char_threshold:
        res.reasons.append(f"char_coverage<{char_threshold}")
    if res.coverage_adjusted < coverage_threshold:
        res.reasons.append(f"token_coverage<{coverage_threshold}")

    # --- classify --------------------------------------------------------
    if not res.vintage_match:
        # Real upstream change between snapshot and corpus HEAD: byte and
        # coverage deltas here are expected, not drift. Counted, reported,
        # excluded from the verdict.
        res.status = "VINTAGE_SKEW"
        res.reasons.append("xml_hash_differs_from_manifest")
    elif res.reasons:
        res.status = "FLAG"
    else:
        res.status = "OK"
    return res


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

_XML_CACHE: dict[str, bytes] = {}
_CORPUS_ROOT: Path | None = None
_THRESHOLD = 0.0
_CHAR_THRESHOLD = 0.0
_RECORDS: dict[str, dict] = {}


def _load_archives(archive_dir: Path, wanted: set[str]) -> dict[str, bytes]:
    """doc_id -> raw XML bytes, for the wanted doc_ids only."""
    out: dict[str, bytes] = {}
    for dataset in DATASETS:
        path = archive_dir / ARCHIVE_NAME.format(dataset=dataset)
        if not path.exists():
            raise SystemExit(f"missing archive: {path}")
        with tarfile.open(path, mode="r:bz2") as tar:
            for member in tar:
                if not member.isfile() or not member.name.endswith(".xml"):
                    continue
                doc_id = Path(member.name).stem
                if doc_id not in wanted:
                    continue
                fh = tar.extractfile(member)
                if fh is None:  # pragma: no cover
                    continue
                with fh:
                    out[doc_id] = fh.read()
    return out


def _init_worker(
    corpus_root: str,
    threshold: float,
    char_threshold: float,
    records: dict,
    xml: dict,
) -> None:
    global _CORPUS_ROOT, _THRESHOLD, _CHAR_THRESHOLD, _RECORDS, _XML_CACHE  # noqa: PLW0603
    _CORPUS_ROOT = Path(corpus_root)
    _THRESHOLD = threshold
    _CHAR_THRESHOLD = char_threshold
    _RECORDS = records
    _XML_CACHE = xml


def _work(doc_id: str) -> DocResult:
    if _CORPUS_ROOT is None:  # pragma: no cover - worker init always runs first
        raise RuntimeError("worker not initialised")
    return audit_one(
        doc_id,
        _RECORDS[doc_id],
        _XML_CACHE[doc_id],
        _CORPUS_ROOT,
        _THRESHOLD,
        _CHAR_THRESHOLD,
    )


def self_test(
    corpus_path: Path,
    archive_dir: Path,
    doc_id: str,
) -> int:
    """Sensitivity check: excise known fractions of a document's Markdown
    and confirm the char metric registers the loss proportionally.

    A verification tool that cannot detect injected damage manufactures
    false confidence, so this is run as evidence, not decoration. It
    mutates only an in-memory copy — the corpus is never touched.
    """
    manifest = read_manifest(corpus_path / "manifest.json")
    rec = manifest.documents[doc_id]
    xml = _load_archives(archive_dir, {doc_id})[doc_id]
    md = (corpus_path / rec.markdown_path).read_text(encoding="utf-8")

    main_adj = _body_root(xml)
    _elide_not_in_force(main_adj)
    _elide_dropped_heading_marks(main_adj)
    xml_stream = _alnum_stream(_all_text(main_adj))
    body = _md_body(md)

    print(f"SELF-TEST on {doc_id} ({rec.slug})")
    clean_baseline = 0.9999
    min_detectable = 0.01
    print(f"  XML body alnum chars: {len(xml_stream)}")
    print(f"  {'excised':>9}  {'char cov':>10}  {'token cov':>10}")
    ok = True
    for frac in (0.0, 0.001, 0.01, 0.05, 0.25, 0.50):
        cut = int(len(body) * frac)
        start = (len(body) - cut) // 2
        mutated = body[:start] + body[start + cut :]
        cc = _char_coverage(xml_stream, _alnum_stream(mutated))
        tc = _coverage(_words(_all_text(main_adj)), _words(mutated))
        print(f"  {frac * 100:>8.1f}%  {_pct(cc):>10}  {_pct(tc):>10}")
        if frac == 0.0 and cc < clean_baseline:
            print("  ! baseline is not clean — pick a doc with 100% coverage")
            ok = False
        if frac >= min_detectable and cc > 1.0 - frac / 2:
            print(f"  ! FAILED to register a {frac * 100:.1f}% excision")
            ok = False
    print("  RESULT:", "PASS — excisions are detected proportionally" if ok else "FAIL")
    return 0 if ok else 1


def _env_corpus_path() -> Path | None:
    """The lovverk clone the sync writes to, or None if the env is silent.

    Returned as None rather than a guess: an audit pointed at the wrong
    corpus reports drift that is really a path mistake, and a default
    naming one developer's home directory is a wrong answer everywhere else.
    """
    raw = os.environ.get("LOVSPOR_OUTPUT_REPO_PATH")
    return Path(raw).expanduser() if raw else None


def _env_archive_dir() -> Path:
    """Where ``sync.orchestrator`` caches the downloaded tarballs."""
    data_dir = os.environ.get("LOVSPOR_DATA_DIR", "./data")
    return Path(data_dir).expanduser() / "cache" / "archives"


def _select_docs(args: argparse.Namespace, records: dict[str, dict]) -> list[str]:
    """The doc_ids this run audits, per the mutually exclusive mode flags.

    A ``--docs`` id absent from the manifest is reported and dropped rather
    than audited as UNKNOWN: it was never in the corpus, so it is a typo in
    the invocation, not a finding about the corpus.
    """
    if args.docs:
        asked = [d.strip() for d in args.docs.split(",") if d.strip()]
        unknown = [d for d in asked if d not in records]
        if unknown:
            print(f"! not current records in manifest: {unknown}", file=sys.stderr)
        return [d for d in asked if d in records]
    if args.sample:
        rng = random.Random(args.seed)  # noqa: S311 - sampling, not crypto
        return rng.sample(sorted(records), min(args.sample, len(records)))
    return sorted(records)


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(
        description="Byte-level + content-level audit of the rendered lovverk corpus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[-1],
    )
    ap.add_argument(
        "--corpus-path",
        type=Path,
        default=_env_corpus_path(),
        help="lovverk clone containing manifest.json "
        "(default: $LOVSPOR_OUTPUT_REPO_PATH = %(default)s)",
    )
    ap.add_argument(
        "--archive-dir",
        type=Path,
        default=_env_archive_dir(),
        help="directory holding the two Lovdata .tar.bz2 files "
        "(default: $LOVSPOR_DATA_DIR/cache/archives = %(default)s)",
    )
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--sample", type=int, metavar="N", help="audit a random sample of N docs")
    grp.add_argument("--full", action="store_true", help="audit every current document")
    grp.add_argument("--docs", type=str, help="comma-separated doc_ids to audit")
    grp.add_argument(
        "--self-test",
        type=str,
        nargs="?",
        const="nl-19990326-014",
        metavar="DOC_ID",
        help="sensitivity check: excise slices of one doc's Markdown and confirm "
        "the metric registers the loss (default doc: %(const)s)",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=1729,
        help="RNG seed for --sample (default: %(default)s)",
    )
    ap.add_argument(
        "--coverage-threshold",
        type=float,
        default=0.995,
        help="flag documents below this adjusted TOKEN coverage (default: %(default)s)",
    )
    ap.add_argument(
        "--char-threshold",
        type=float,
        default=0.9999,
        help="flag documents whose adjusted CHAR coverage falls below this (default: %(default)s)",
    )
    ap.add_argument("--jobs", type=int, default=8, help="worker processes (default: %(default)s)")
    ap.add_argument("--json", type=Path, help="write full per-document results as JSON")
    ap.add_argument("--top", type=int, default=25, help="how many worst offenders to print")
    args = ap.parse_args()

    if args.corpus_path is None:
        raise SystemExit(
            "no corpus path: set LOVSPOR_OUTPUT_REPO_PATH (in the environment or "
            ".env) or pass --corpus-path /path/to/lovverk",
        )

    if args.self_test:
        return self_test(args.corpus_path, args.archive_dir, args.self_test)

    manifest = read_manifest(args.corpus_path / "manifest.json")
    records = {
        doc_id: {
            "slug": r.slug,
            "source_dataset": r.source_dataset,
            "markdown_path": r.markdown_path,
            "xml_hash": r.xml_hash,
            "last_seen": r.last_seen,
            "last_changed": str(r.last_changed) if r.last_changed else None,
        }
        for doc_id, r in manifest.documents.items()
        if r.status == "current"
    }

    wanted = _select_docs(args, records)

    print(
        f"renderer v{RENDERER_VERSION} | manifest {args.corpus_path / 'manifest.json'} | "
        f"{len(records)} current records | auditing {len(wanted)}",
        file=sys.stderr,
    )

    t0 = time.time()
    xml = _load_archives(args.archive_dir, set(wanted))
    print(f"loaded {len(xml)} XML members in {time.time() - t0:.1f}s", file=sys.stderr)

    not_in_upstream = [d for d in wanted if d not in xml]
    todo = [d for d in wanted if d in xml]

    results: list[DocResult] = []
    t1 = time.time()
    if args.jobs > 1:
        with ProcessPoolExecutor(
            max_workers=args.jobs,
            initializer=_init_worker,
            initargs=(
                str(args.corpus_path),
                args.coverage_threshold,
                args.char_threshold,
                records,
                xml,
            ),
        ) as pool:
            for i, r in enumerate(pool.map(_work, todo, chunksize=16), 1):
                results.append(r)
                if i % 250 == 0:
                    print(f"  {i}/{len(todo)} ({time.time() - t1:.0f}s)", file=sys.stderr)
    else:
        _init_worker(
            str(args.corpus_path),
            args.coverage_threshold,
            args.char_threshold,
            records,
            xml,
        )
        for d in todo:
            results.append(_work(d))

    for d in not_in_upstream:
        results.append(
            DocResult(
                doc_id=d,
                slug=records[d]["slug"],
                dataset=records[d]["source_dataset"],
                status="UNKNOWN",
                reasons=["not_in_upstream_tarball"],
                last_changed=records[d]["last_changed"],
            ),
        )

    _report(results, args, time.time() - t1)

    if args.json:
        args.json.write_text(
            json.dumps([asdict(r) for r in results], indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}", file=sys.stderr)

    flagged = [r for r in results if r.status == "FLAG"]
    return 1 if flagged else 0


def _pct(x: float) -> str:
    return f"{x * 100:.4f}%"


def _report(  # noqa: PLR0912, PLR0915
    results: list[DocResult],
    args: argparse.Namespace,
    elapsed: float,
) -> None:
    buckets = Counter(r.status for r in results)
    print("\n" + "=" * 78)
    print(f"AUDITED {len(results)} documents in {elapsed:.1f}s")
    print("=" * 78)
    for k in ("OK", "FLAG", "VINTAGE_SKEW", "UNKNOWN"):
        print(f"  {k:<14} {buckets.get(k, 0)}")

    comparable = [
        r for r in results if r.status in ("OK", "FLAG") and r.coverage_adjusted is not None
    ]

    # --- check A ---------------------------------------------------------
    repro = [r for r in comparable if r.reproducible is True]
    nonrepro = [r for r in comparable if r.reproducible is False]
    print(f"\nCHECK A — reproducibility (vintage-matched docs only, n={len(comparable)})")
    print(f"  byte-identical re-render : {len(repro)}")
    print(f"  NOT reproducible         : {len(nonrepro)}")
    for r in sorted(nonrepro, key=lambda x: abs(x.byte_delta or 0), reverse=True)[: args.top]:
        why = r.error.split("\n")[0][:90] if r.error else f"delta {r.byte_delta:+d} bytes"
        print(f"    {r.slug[:56]:<56} {why}")

    # --- check B ---------------------------------------------------------
    if comparable:
        covs = sorted(r.coverage_adjusted for r in comparable)
        n = len(covs)

        def q(p: float) -> float:
            return covs[min(n - 1, int(p * n))]

        chars = sorted(r.char_coverage_adjusted for r in comparable)

        def qc(p: float) -> float:
            return chars[min(n - 1, int(p * n))]

        print(f"\nCHECK B — coverage distribution, adjusted (n={n})")
        print(f"  {'quantile':<10} {'CHAR (absence)':>16} {'TOKEN (boundaries)':>20}")
        for label, p in (
            ("min", 0.0),
            ("p01", 0.01),
            ("p05", 0.05),
            ("p25", 0.25),
            ("median", 0.50),
            ("p75", 0.75),
            ("p95", 0.95),
        ):
            cv = chars[0] if p == 0.0 else qc(p)
            tv = covs[0] if p == 0.0 else q(p)
            print(f"  {label:<10} {_pct(cv):>16} {_pct(tv):>20}")
        print(f"  {'max':<10} {_pct(chars[-1]):>16} {_pct(covs[-1]):>20}")

        nov = [r for r in comparable if (r.novel_tokens or 0) > 0]
        total_novel = sum(r.novel_tokens or 0 for r in comparable)
        print("\n  FABRICATED TOKENS — words in the Markdown absent from the source XML")
        print(f"    documents with >=1 : {len(nov)} / {n}")
        print(f"    total occurrences  : {total_novel}")
        for r in sorted(nov, key=lambda x: x.novel_tokens or 0, reverse=True)[:10]:
            ex = " ".join(r.novel_sample[:3])
            print(f"    {r.novel_tokens:>6}  {r.slug[:44]:<44} {ex[:70]}")

        print("\n  documents below each threshold:")
        print(f"  {'threshold':<12} {'CHAR':>8} {'TOKEN':>8}")
        for t in (1.0, 0.9999, 0.999, 0.99, 0.95, 0.90, 0.50):
            bc = sum(1 for c in chars if c < t)
            bt = sum(1 for c in covs if c < t)
            print(f"  <{t * 100:>10.4f}% {bc:>8} {bt:>8}")

        worst = sorted(comparable, key=lambda r: (r.char_coverage_adjusted, r.coverage_adjusted))
        worst = worst[: args.top]
        print(f"\nWORST {len(worst)} BY ADJUSTED CHAR COVERAGE (content absence)")
        print(f"  {'char':>10} {'token':>10}  {'§xml/§md':>12}  {'tr x/m':>10}  slug")
        for r in worst:
            secs = f"{r.xml_section_marks}/{r.md_section_marks}"
            trs = f"{r.xml_table_rows}/{r.md_table_rows}"
            print(
                f"  {_pct(r.char_coverage_adjusted):>10} {_pct(r.coverage_adjusted):>10}  "
                f"{secs:>12}  {trs:>10}  {r.slug[:48]}",
            )

        tworst = sorted(comparable, key=lambda r: r.coverage_adjusted)[: args.top]
        print(f"\nWORST {len(tworst)} BY ADJUSTED TOKEN COVERAGE (boundary corruption)")
        print(f"  {'char':>10} {'token':>10}  {'§xml/§md':>12}  {'tr x/m':>10}  slug")
        for r in tworst:
            secs = f"{r.xml_section_marks}/{r.md_section_marks}"
            trs = f"{r.xml_table_rows}/{r.md_table_rows}"
            print(
                f"  {_pct(r.char_coverage_adjusted):>10} {_pct(r.coverage_adjusted):>10}  "
                f"{secs:>12}  {trs:>10}  {r.slug[:48]}",
            )

    flagged = [r for r in results if r.status == "FLAG"]
    if flagged:
        print(f"\nFLAGGED ({len(flagged)}) — worst first")
        for r in sorted(
            flagged,
            key=lambda x: x.char_coverage_adjusted if x.char_coverage_adjusted is not None else -1,
        ):
            cov = _pct(r.char_coverage_adjusted) if r.char_coverage_adjusted is not None else "n/a"
            tok = _pct(r.coverage_adjusted) if r.coverage_adjusted is not None else "n/a"
            print(
                f"  char={cov:>10} tok={tok:>10}  {r.doc_id:<20} "
                f"{r.slug[:40]:<40} {','.join(r.reasons)}",
            )
            if r.missing_sample:
                print(f"              missing: {' '.join(r.missing_sample[:10])}")

    unknown = [r for r in results if r.status == "UNKNOWN"]
    if unknown:
        print(f"\nUNKNOWN ({len(unknown)}) — could not be checked, NOT counted as clean")
        for r in unknown[:40]:
            print(f"  {r.doc_id:<22} {r.slug[:40]:<40} {','.join(r.reasons)} {r.error or ''}")

    skew = [r for r in results if r.status == "VINTAGE_SKEW"]
    if skew:
        print(
            f"\nVINTAGE_SKEW ({len(skew)}) — upstream XML differs from the "
            "snapshot that rendered the corpus",
        )
        for r in skew[:40]:
            print(f"  {r.doc_id:<22} {r.slug[:44]:<44} last_changed={r.last_changed}")


if __name__ == "__main__":
    sys.exit(main())
