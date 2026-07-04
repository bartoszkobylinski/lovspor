"""Deterministic XML canonicalization for change detection.

Two semantically equivalent XML inputs must produce byte-identical
canonical output, so that ``hash_normalized_xml`` returns the same hash
for both. This is the invariant change detection relies on: a hash diff
between two snapshots of the same Lovdata document means the document's
*content* changed, not just its formatting.

"Semantically equivalent" here means:
- same elements with same text and attribute values
- regardless of attribute order
- regardless of whitespace between elements (pretty-printed vs compact)
- regardless of comments (which may carry volatile upstream timestamps)
- regardless of self-closing vs open-close tag style

Whitespace inside element text content IS significant and is preserved.

Known gap: ``remove_blank_text=True`` also strips whitespace-only nodes
*between* elements, which the renderer (``remove_blank_text=False``, PR #79)
treats as significant inline spacing. A change to that inter-element
whitespace alone therefore does not move the hash and skips a re-render.
This is accepted, not a bug: making the hash whitespace-sensitive would
re-hash on every upstream indentation reflow and force a corpus-wide
re-render, against the conservative-churn posture. See decisions.md §14.

Implementation: lxml parser with ``remove_blank_text=True`` and
``remove_comments=True`` strips formatting noise at parse time, then
W3C XML Canonicalization (C14N) via ``etree.tostring(method="c14n")``
produces the stable byte form.

Security: parser refuses external entity resolution, network access,
and oversized trees. Blocks XXE and billion-laughs entity expansion.
Required by ``CLAUDE.md``.
"""

import hashlib
from io import BytesIO

from lxml import etree

from lovspor.errors import ParseError


def safe_parser(*, remove_blank_text: bool = True) -> etree.XMLParser:
    """Return the hardened lxml parser used across the pipeline.

    Security config (XXE / billion-laughs / no-network / bounded tree) is
    fixed here so every caller shares one source of truth.

    ``remove_blank_text`` (default ``True``) strips whitespace-only nodes
    at parse time. That is correct for *hashing* — formatting whitespace
    between elements must not change the content hash. It is WRONG for
    *rendering*: it also drops the significant whitespace-only tail
    between two inline elements (``<strong>a</strong> <em>b</em>``),
    fusing the words in the Markdown output. The renderer and metadata
    extractor pass ``remove_blank_text=False`` to keep that space.
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
        remove_blank_text=remove_blank_text,
        remove_comments=True,
    )


def canonicalize_xml(xml_bytes: bytes) -> bytes:
    """Return the C14N (W3C XML Canonicalization) byte form of ``xml_bytes``.

    Documents that define custom DTD entities (XXE / billion laughs
    attack payloads) cannot be canonicalized because the parser refuses
    to resolve them (``resolve_entities=False``) and the canonicalizer
    refuses to serialize unresolved entity references. Both failure
    modes surface as ``ParseError``. The attack is defeated either way:
    no external resource is fetched, no expansion bomb expands.

    Raises:
        ParseError: ``xml_bytes`` is not well-formed XML, or contains
            unresolved custom entities that C14N cannot serialize.
    """
    try:
        tree = etree.parse(BytesIO(xml_bytes), parser=safe_parser())
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"malformed XML: {exc}") from exc
    try:
        return etree.tostring(tree, method="c14n")
    except etree.C14NError as exc:
        raise ParseError(f"cannot canonicalize XML: {exc}") from exc


def hash_normalized_xml(xml_bytes: bytes) -> str:
    """SHA-256 hex digest (64 chars, no prefix) of canonicalized XML.

    Use this for change detection: identical hashes mean identical
    canonical content, regardless of formatting differences in the
    raw input.
    """
    return hashlib.sha256(canonicalize_xml(xml_bytes)).hexdigest()
