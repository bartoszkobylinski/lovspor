"""Placing a new document in its authority directory: the slug rule (ADR-0016 3)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lovspor.errors import PromotionRefusedError
from lovspor.promotion.corpus import LocalManifest, LocalRecord
from lovspor.promotion.decisions import ArtifactKey, Decision, HumanDecision
from lovspor.promotion.extract import EXTRACTOR_VERSION, extract_regulation
from lovspor.promotion.identity import mint_identity
from lovspor.promotion.models import Authority, ExtractedDocument, MintedIdentity
from lovspor.promotion.plan import Prepared, _free_slug, require_approval
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


def test_a_title_that_slugs_to_nothing_falls_back_to_the_id() -> None:
    identity, extracted = _minted()
    fields = extracted.fields.model_copy(update={"title": "[Forskrift om renovasjon]"})
    untitled = extracted.model_copy(update={"fields": fields})

    assert _free_slug(_manifest(), identity, untitled) == identity.doc_id


def test_without_a_vedtaksdato_a_taken_slug_moves_straight_to_the_id() -> None:
    identity, extracted = _minted()
    regulation = extracted.regulation.model_copy(update={"vedtaksdato": None})
    undated = extracted.model_copy(update={"regulation": regulation})

    assert _free_slug(_manifest(BASE), identity, undated) == f"{BASE}-{identity.doc_id}"


DECIDED_AT = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)


def _prepared() -> Prepared:
    identity, extracted = _minted()
    return Prepared(identity=identity, extracted=extracted, slug=BASE, version=1, unchanged=False)


def _decision(decision: Decision, content_hash: str | None, extractor: int | None) -> HumanDecision:
    return HumanDecision(
        artifact=ArtifactKey(authority_id="0301", sha256="a" * 64, source_url="https://x.invalid"),
        decision=decision,
        decided_by="Kari Gjennomgang",
        reviewer_role="project owner",
        decided_at=DECIDED_AT,
        reason="Lest.",
        content_hash=content_hash,
        extractor_version=extractor,
    )


def test_an_approval_of_exactly_this_text_stands() -> None:
    prepared = _prepared()
    approval = _decision(Decision.APPROVE, prepared.identity.content_hash, EXTRACTOR_VERSION)

    assert require_approval(approval, prepared, {}) is approval


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (
            None,
            "no human decision is recorded for this artifact; run `lovspor promote approve`",
        ),
        (
            _decision(Decision.HOLD, None, None),
            "the standing decision is hold (Kari Gjennomgang, 2026-08-20T10:00:00Z)",
        ),
    ],
)
def test_no_standing_approval_is_refused_saying_what_stands(
    decision: HumanDecision | None, expected: str
) -> None:
    with pytest.raises(PromotionRefusedError) as refused:
        require_approval(decision, _prepared(), {})

    assert str(refused.value) == expected


@pytest.mark.parametrize(("other_text", "other_extractor"), [(True, False), (False, True)])
def test_an_approval_of_another_text_or_extractor_is_refused(
    other_text: bool, other_extractor: bool
) -> None:
    prepared = _prepared()
    content_hash = "0" * 64 if other_text else prepared.identity.content_hash
    extractor = EXTRACTOR_VERSION + 1 if other_extractor else EXTRACTOR_VERSION

    with pytest.raises(PromotionRefusedError) as refused:
        require_approval(_decision(Decision.APPROVE, content_hash, extractor), prepared, {})

    assert str(refused.value) == (
        "the approval was given for another text or extractor; preview and approve again"
    )
