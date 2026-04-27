"""Property-based invariants for slug derivation.

Hypothesis explores the input space that hand-coded tests miss. The
2026-04-27 production crash (``OSError: File name too long`` on a
290-character forskrift title) was missed because all unit + integration
tests used synthetic titles ≤ 50 characters. Property tests now guard
the long-tail distribution.

Strategies cover Latin + Latin-1 Supplement + Latin Extended-A
(0x20-0x017F), which subsumes Norwegian ``æøå`` and the German
``äöü``. Lovdata never emits scripts outside that range for legal
text in either dataset, so this gives realistic coverage without
generating CJK or emoji that the slugify rules would just collapse
to hyphens anyway.
"""

from hypothesis import given
from hypothesis import strategies as st

from lovspor.rendering.slug import derive_slug, resolve_collisions

_TEXT_ALPHABET = st.characters(
    min_codepoint=0x20,
    max_codepoint=0x017F,
    blacklist_categories=("Cs",),  # exclude unpaired surrogates
)
_TITLE = st.text(alphabet=_TEXT_ALPHABET, min_size=0, max_size=500)
_DOC_ID = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=30,
)
_SLUG_LIKE = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-æøåäöü",
    min_size=1,
    max_size=50,
)


@given(short_title=st.one_of(st.none(), _TITLE), title=_TITLE, doc_id=_DOC_ID)
def test_slug_never_exceeds_200_utf8_bytes(
    short_title: str | None,
    title: str,
    doc_id: str,
) -> None:
    """The cap must hold for every input — this is the production
    invariant that prevents ``OSError: File name too long`` on Linux/macOS
    filesystems where NAME_MAX is 255 bytes."""
    slug = derive_slug(short_title, title, doc_id)
    assert len(slug.encode("utf-8")) <= 200


@given(short_title=st.one_of(st.none(), _TITLE), title=_TITLE, doc_id=_DOC_ID)
def test_slug_is_always_non_empty(
    short_title: str | None,
    title: str,
    doc_id: str,
) -> None:
    """An empty slug means an unusable filename. ``doc_id`` is the
    last-resort fallback so this should never happen."""
    slug = derive_slug(short_title, title, doc_id)
    assert len(slug) > 0


@given(short_title=st.one_of(st.none(), _TITLE), title=_TITLE, doc_id=_DOC_ID)
def test_slug_is_deterministic(
    short_title: str | None,
    title: str,
    doc_id: str,
) -> None:
    """Same inputs must always produce the same slug — change detection
    relies on the slug being a pure function of the metadata."""
    assert derive_slug(short_title, title, doc_id) == derive_slug(
        short_title,
        title,
        doc_id,
    )


@given(
    st.dictionaries(
        keys=_DOC_ID,
        values=_SLUG_LIKE,
        min_size=0,
        max_size=20,
    ),
)
def test_resolve_collisions_returns_unique_outputs(
    slugs_by_doc: dict[str, str],
) -> None:
    """Output must be a bijection over keys: every input ``doc_id``
    maps to a unique resolved slug, and no input is dropped."""
    resolved = resolve_collisions(slugs_by_doc)
    assert set(resolved.keys()) == set(slugs_by_doc.keys())
    assert len(set(resolved.values())) == len(resolved)


@given(
    st.dictionaries(
        keys=_DOC_ID,
        values=_SLUG_LIKE,
        min_size=0,
        max_size=20,
    ),
)
def test_resolve_collisions_is_input_order_independent(
    slugs_by_doc: dict[str, str],
) -> None:
    """Determinism: shuffling the input dict's iteration order must
    not change the output, since assignment is keyed on sorted doc_id."""
    items = list(slugs_by_doc.items())
    forward = dict(items)
    reverse = dict(reversed(items))
    assert resolve_collisions(forward) == resolve_collisions(reverse)
