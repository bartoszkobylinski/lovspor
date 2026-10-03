"""Placing a new document in its authority directory: the slug rule (ADR-0016 3)."""

from __future__ import annotations

import pytest

from lovspor.errors import PromotionRefusedError
from lovspor.promotion.corpus import LocalManifest, LocalRecord
from lovspor.promotion.extract import extract_regulation
from lovspor.promotion.identity import mint_identity
from lovspor.promotion.models import Authority, ExtractedDocument, MintedIdentity
from lovspor.promotion.plan import _free_slug
from tests.unit.promotion_fixtures import html_page

BASE = "forskrift-om-renovasjon-og-slam-eksempel-kommune"


def _minted(authority_id: str = "0301") -> tuple[MintedIdentity, ExtractedDocument]:
    extracted = extract_regulation(html_page(), "text/html")
    assert isinstance(extracted, ExtractedDocument)
    authority = Authority(id=authority_id, type="kommune", name="Eksempel", klass_version="k")
    identity = mint_identity(extracted.regulation, authority)
    assert isinstance(identity, MintedIdentity)
    return identity, extracted


def _manifest(*slugs: str, authority_id: str = "0301") -> LocalManifest:
    records = {
        f"lk-{authority_id}-{index:012x}": LocalRecord(
            status="removed" if index % 2 else "current",
            slug=slug,
            title="t",
            markdown_path=f"lokale-forskrifter/{authority_id}/{slug}.md",
            renderer_version=1,
            last_seen="2026-08-19T15:17:23Z",
            authority_id=authority_id,
            authority_type="kommune",
            content_hash="0" * 64,
            version=1,
            extractor_version=1,
        )
        for index, slug in enumerate(slugs)
    }
    return LocalManifest(documents=records)


@pytest.mark.parametrize(
    ("taken", "expected"),
    [
        ((), BASE),
        ((BASE,), f"{BASE}-2019-12-12"),
        ((BASE, f"{BASE}-2019-12-12"), f"{BASE}-lk-0301-aa8ae774921d"),
    ],
)
def test_a_taken_slug_moves_to_the_vedtaksdato_then_the_id(
    taken: tuple[str, ...], expected: str
) -> None:
    identity, extracted = _minted()

    assert _free_slug(_manifest(*taken), identity, extracted) == expected


def test_a_slug_another_authority_holds_is_free_here() -> None:
    identity, extracted = _minted()

    assert _free_slug(_manifest(BASE, authority_id="4601"), identity, extracted) == BASE


def test_every_slug_taken_is_refused() -> None:
    identity, extracted = _minted()
    taken = (BASE, f"{BASE}-2019-12-12", f"{BASE}-{identity.doc_id}")

    with pytest.raises(PromotionRefusedError, match="taken"):
        _free_slug(_manifest(*taken), identity, extracted)
