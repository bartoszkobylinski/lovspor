"""The files a batch release and a withdrawal write, manifest schema 2 (#738, B1b).

Pure drafts from synthetic staged chapters: no checkout, no clock. They are
what the batch apply writes, so their labels, clearances, times, conflicts
and determinism are pinned here, apart from the transaction.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lovspor.forarbeider.batch_models import MAX_DOCUMENTS
from lovspor.forarbeider.batch_release import (
    ChapterRelease,
    Decision,
    DocumentRelease,
    release_batch_draft,
    released_digests,
    withdraw_v2_draft,
)
from lovspor.forarbeider.corpus_models import (
    EMPTY_MANIFEST,
    INDEX_PATH,
    MANIFEST_PATH,
    PolicyClearance,
    ReviewClearance,
    lift_v1,
    parse_any_manifest,
)
from lovspor.forarbeider.corpus_render import NOT_LAW
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.models import ForarbeidRecord
from lovspor.forarbeider.release_models import ReviewPin
from tests.unit.forarbeider_batch_support import BatchDesk

POLICY = PolicyClearance(
    cleared_by="standing_policy",
    policy_sha256="b" * 64,
    source_proof_sha256="c" * 64,
    individually_reviewed=False,
)
SIGNED = datetime(2100, 4, 1, 12, 0, tzinfo=UTC)
CONFIRMED = datetime(2100, 4, 10, 13, 0, tzinfo=UTC)
DECISION = Decision(policy_decided_at=SIGNED, owner_decided_at=CONFIRMED)
EMPTY = lift_v1(EMPTY_MANIFEST)


def _review(at: datetime = datetime(2100, 4, 9, 12, 0, tzinfo=UTC)) -> ReviewClearance:
    return ReviewClearance(
        cleared_by="owner_review",
        review_decided_at=at,
        review=ReviewPin(subject_key="d" * 64, revision=1, head_sha256="e" * 64),
        authorization_sha256="f" * 64,
        report_sha256="1" * 64,
        provenance="recorded",
        individually_reviewed=True,
    )


@pytest.fixture
def batch(tmp_path: Path) -> BatchDesk:
    return BatchDesk(tmp_path)


def _release(batch: BatchDesk, number: int, chapters: dict[int, object]) -> DocumentRelease:
    desk = batch.desks.get(number) or batch.add(number)
    metadata = ForarbeidRecord.model_validate_json(desk.metadata.read_bytes())
    included = tuple(
        ChapterRelease(desk.results[chapter], clearance)  # type: ignore[arg-type]
        for chapter, clearance in sorted(chapters.items())
    )
    return DocumentRelease(metadata, included)


def test_a_policy_cleared_document_is_written_with_every_fixed_label(batch: BatchDesk) -> None:
    draft = release_batch_draft(EMPTY, [_release(batch, 1, {2: POLICY, 3: POLICY})], DECISION)

    path = "forarbeider/2099-2100/prop-l/9000101.md"
    assert sorted(draft.writes) == sorted([path, INDEX_PATH, MANIFEST_PATH])
    assert draft.removes == ()
    manifest = parse_any_manifest(draft.writes[MANIFEST_PATH])
    assert manifest.schema_version == 2
    (member,) = manifest.documents
    assert (member.normative, member.source_license, member.rights_basis) == (
        False,
        None,
        "åndsverkloven § 14",
    )
    assert member.source_document_type == "forarbeid"
    assert member.asserts_complete_proposition is False
    assert [item.clearance for item in member.chapters] == [POLICY, POLICY]
    assert member.times.policy_decided_at == SIGNED
    assert member.times.reviewed_at is None
    assert member.times.owner_decided_at == member.times.released_at == CONFIRMED
    markdown = draft.writes[path].decode()
    assert NOT_LAW in markdown
    assert "normative: false" in markdown
    assert "source_license: null" in markdown
    assert "NLOD" not in markdown
    assert path.removeprefix("forarbeider/") in draft.writes[INDEX_PATH].decode()


def test_a_mixed_document_keeps_each_chapters_own_path_and_review_time(batch: BatchDesk) -> None:
    review = _review()
    draft = release_batch_draft(EMPTY, [_release(batch, 1, {2: POLICY, 3: review})], DECISION)

    (member,) = parse_any_manifest(draft.writes[MANIFEST_PATH]).documents
    assert [item.clearance for item in member.chapters] == [POLICY, review]
    assert member.times.reviewed_at == review.review_decided_at
    assert member.times.policy_decided_at == SIGNED


def test_only_the_chapters_handed_in_are_rendered_and_marked_partial(batch: BatchDesk) -> None:
    draft = release_batch_draft(EMPTY, [_release(batch, 1, {2: POLICY})], DECISION)

    (member,) = parse_any_manifest(draft.writes[MANIFEST_PATH]).documents
    assert [item.chapter_number for item in member.chapters] == [2]
    assert member.coverage == "partial_curated_excerpt"
    markdown = draft.writes[member.path].decode()
    assert "released_chapters: [2]" in markdown
    assert batch.desks[1].results[3].source_page_url not in markdown


def test_the_same_batch_gives_the_same_bytes(batch: BatchDesk) -> None:
    releases = [_release(batch, 1, {2: POLICY}), _release(batch, 2, {2: POLICY, 3: _review()})]

    first = release_batch_draft(EMPTY, releases, DECISION)
    again = release_batch_draft(EMPTY, list(reversed(releases)), DECISION)

    assert first == again
    assert released_digests(first) == released_digests(again)


@pytest.mark.parametrize("status", ["released", "withdrawn"])
def test_a_document_the_manifest_already_lists_is_a_conflicting_release(
    batch: BatchDesk, status: str
) -> None:
    first = release_batch_draft(EMPTY, [_release(batch, 1, {2: POLICY})], DECISION)
    base = parse_any_manifest(first.writes[MANIFEST_PATH])
    if status == "withdrawn":
        base = parse_any_manifest(
            withdraw_v2_draft(base, base.documents[0].document.id, CONFIRMED).writes[MANIFEST_PATH]
        )

    with pytest.raises(ForarbeiderRefusedError) as caught:
        release_batch_draft(
            base, [_release(batch, 2, {2: POLICY}), _release(batch, 1, {3: POLICY})], DECISION
        )

    assert (caught.value.reason.value, caught.value.detail) == (
        "release_conflict",
        base.documents[0].document.id,
    )


def test_a_confirmation_before_the_policy_or_a_review_is_invalid(batch: BatchDesk) -> None:
    early = Decision(policy_decided_at=SIGNED, owner_decided_at=SIGNED - timedelta(seconds=1))
    with pytest.raises(ForarbeiderRefusedError) as caught:
        release_batch_draft(EMPTY, [_release(batch, 1, {2: POLICY})], early)
    assert (caught.value.reason.value, caught.value.detail) == ("release_invalid", "decided")

    late = _review(CONFIRMED + timedelta(seconds=1))
    with pytest.raises(ForarbeiderRefusedError) as caught:
        release_batch_draft(EMPTY, [_release(batch, 1, {2: late})], DECISION)
    assert caught.value.detail == "decided"


def test_twenty_five_documents_are_one_draft(batch: BatchDesk) -> None:
    releases = [_release(batch, number, {2: POLICY}) for number in range(1, MAX_DOCUMENTS + 1)]

    draft = release_batch_draft(EMPTY, releases, DECISION)

    manifest = parse_any_manifest(draft.writes[MANIFEST_PATH])
    assert len(manifest.documents) == MAX_DOCUMENTS
    assert [item.path for item in manifest.documents] == sorted(
        item.path for item in manifest.documents
    )
    assert len(draft.writes) == MAX_DOCUMENTS + 2


def test_a_withdrawal_keeps_provenance_and_removes_only_the_file(batch: BatchDesk) -> None:
    released = release_batch_draft(
        EMPTY,
        [_release(batch, 1, {2: POLICY, 3: _review()}), _release(batch, 2, {2: POLICY})],
        DECISION,
    )
    base = parse_any_manifest(released.writes[MANIFEST_PATH])
    target = base.documents[0]
    later = CONFIRMED + timedelta(days=1)

    draft = withdraw_v2_draft(base, target.document.id, later)

    assert draft.removes == (target.path,)
    assert sorted(draft.writes) == [INDEX_PATH, MANIFEST_PATH]
    after = parse_any_manifest(draft.writes[MANIFEST_PATH])
    withdrawn = next(item for item in after.documents if item.document.id == target.document.id)
    assert withdrawn.release_status == "withdrawn"
    assert withdrawn.markdown_sha256 is None
    assert withdrawn.times.withdrawn_at == later
    assert withdrawn.chapters == target.chapters
    index = draft.writes[INDEX_PATH].decode()
    assert target.path.removeprefix("forarbeider/") not in index
    assert base.documents[1].path.removeprefix("forarbeider/") in index


def test_a_withdrawal_refuses_an_unknown_a_withdrawn_or_a_too_early_document(
    batch: BatchDesk,
) -> None:
    released = release_batch_draft(EMPTY, [_release(batch, 1, {2: POLICY})], DECISION)
    base = parse_any_manifest(released.writes[MANIFEST_PATH])
    document = base.documents[0].document.id

    with pytest.raises(ForarbeiderRefusedError) as caught:
        withdraw_v2_draft(base, "regjeringen-1", CONFIRMED)
    assert caught.value.reason.value == "document_not_released"
    with pytest.raises(ForarbeiderRefusedError) as caught:
        withdraw_v2_draft(base, document, CONFIRMED - timedelta(seconds=1))
    assert (caught.value.reason.value, caught.value.detail) == ("manifest_invalid", "withdrawn_at")
    gone = parse_any_manifest(withdraw_v2_draft(base, document, CONFIRMED).writes[MANIFEST_PATH])
    with pytest.raises(ForarbeiderRefusedError) as caught:
        withdraw_v2_draft(gone, document, CONFIRMED)
    assert caught.value.reason.value == "document_not_released"
