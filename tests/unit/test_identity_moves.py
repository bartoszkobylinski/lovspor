"""Boundary cases for the base-blob move contract introduced in #910."""

import hashlib

import pytest

from lovspor.identity.migration import Rename
from lovspor.identity.moves import plan_rename_moves

RENAME = Rename(doc_id="sf-20260601-0002", directory="forskrifter", from_slug="x", to_slug="x-2026")
TEXT = '---\nid: "sf-20260601-0002"\nslug: "x"\n---\n\nNorsk: øæå\nslug: "x"\n'


def test_moves_preserve_body_and_hash_exact_utf8_and_empty_sidecar_bytes() -> None:
    planned = plan_rename_moves(RENAME, TEXT.encode("utf-8"), b"")
    expected = TEXT.replace('slug: "x"', 'slug: "x-2026"', 1)

    assert planned.problems == []
    assert planned.markdown == {"forskrifter/x-2026.md": expected}
    assert planned.moves[0].sha256 == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert len(planned.sidecars) == 1
    assert planned.sidecars[0].sha256 == hashlib.sha256(b"").hexdigest()
    assert planned.sidecars[0].source == "forskrifter/embeddings/x.bin"
    assert planned.sidecars[0].target == "forskrifter/embeddings/x-2026.bin"
    assert plan_rename_moves(RENAME, TEXT.encode("utf-8"), b"") == planned


@pytest.mark.parametrize(
    "blob",
    [
        None,
        b"\xff",
        TEXT.replace('slug: "x"\n---', 'slug: "x"\nslug: "x"\n---').encode(),
        TEXT.replace(
            'id: "sf-20260601-0002"', 'id: "sf-20260601-0002"\nid: "sf-20260601-0002"'
        ).encode(),
        b'body only\nslug: "x"\nid: "sf-20260601-0002"\n',
    ],
    ids=["missing", "invalid-utf8", "duplicate-slug", "duplicate-id", "body-fields"],
)
def test_unrewritable_markdown_has_no_planned_text_or_digest(blob: bytes | None) -> None:
    planned = plan_rename_moves(RENAME, blob, None)

    assert len(planned.problems) == 1
    assert planned.markdown == {}
    assert len(planned.moves) == 1
    assert planned.moves[0].sha256 is None
    assert planned.sidecars == []
