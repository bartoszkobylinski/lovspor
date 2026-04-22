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


def safe_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
        remove_blank_text=True,
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
