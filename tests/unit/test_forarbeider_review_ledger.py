"""The append-only review ledger (#738, ADR-0018 PR3A)."""

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import review_ledger, staging
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.review_ledger import (
    DecisionRequest,
    current_status,
    load_history,
    record_decision,
    verify_ledger,
)
from lovspor.forarbeider.review_models import (
    Attestation,
    AttestationKind,
    ReviewReason,
    ReviewStatus,
    subject_key,
    subject_of,
)
from tests.unit.forarbeider_review_support import chapter, with_changed_text


def request(**changes: Any) -> DecisionRequest:
    base: dict[str, Any] = {
        "subject": subject_of(chapter()),
        "reviewer": "reviewer-1",
        "decided_at": "2100-04-02T12:00:00Z",
        "status": ReviewStatus.HELD,
        "reason_codes": (ReviewReason.AWAITING_EVIDENCE,),
        "evidence": ("issue:738",),
        "attestations": (),
        "expected_revision": 0,
    }
    return DecisionRequest(**(base | changes))


def approval(**changes: Any) -> DecisionRequest:
    attestations = tuple(
        Attestation(kind=kind, attested=True, evidence=("adr:0018",)) for kind in AttestationKind
    )
    return request(
        status=ReviewStatus.APPROVED,
        reason_codes=(ReviewReason.ATTESTATIONS_COMPLETE,),
        evidence=("adr:0018",),
        attestations=attestations,
        **changes,
    )


def refusal(ledger: Path, entry: DecisionRequest) -> RefusalReason:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        record_decision(ledger, entry)
    return caught.value.reason


def chapter_directory(ledger: Path) -> Path:
    return ledger / subject_key(subject_of(chapter()))


def history(ledger: Path) -> tuple[Any, ...]:
    return load_history(ledger, subject_of(chapter()))


def later(revision: int, moment: str = "2100-04-03T12:00:00Z", **changes: Any) -> DecisionRequest:
    return request(expected_revision=revision, decided_at=moment, **changes)


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    path = tmp_path / "ledger"
    path.mkdir(mode=0o700)
    return path


def test_no_history_means_pending(ledger: Path) -> None:
    assert history(ledger) == ()
    assert current_status(()) is ReviewStatus.PENDING


def test_a_ledger_never_created_means_pending(tmp_path: Path) -> None:
    assert load_history(tmp_path / "gone", subject_of(chapter())) == ()


def test_first_decision_is_revision_one(ledger: Path) -> None:
    outcome = record_decision(ledger, request())
    assert outcome.written is True
    assert outcome.stored.decision.revision == 1
    assert outcome.stored.decision.previous_decision_sha256 is None
    assert current_status(history(ledger)) is ReviewStatus.HELD


def test_records_and_directories_are_owner_only(ledger: Path) -> None:
    record_decision(ledger, request())
    directory = chapter_directory(ledger)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "000001.json").stat().st_mode) == 0o600
    assert [path.name for path in directory.iterdir()] == ["000001.json"]


def test_a_later_decision_links_to_the_one_before(ledger: Path) -> None:
    first = record_decision(ledger, request())
    second = record_decision(ledger, later(1, status=ReviewStatus.REJECTED))
    assert second.stored.decision.revision == 2
    assert second.stored.decision.previous_decision_sha256 == first.stored.sha256
    assert [item.decision.status for item in history(ledger)] == [
        ReviewStatus.HELD,
        ReviewStatus.REJECTED,
    ]


def test_history_is_never_rewritten(ledger: Path) -> None:
    record_decision(ledger, request())
    before = (chapter_directory(ledger) / "000001.json").read_bytes()
    record_decision(ledger, later(1, status=ReviewStatus.REJECTED))
    assert (chapter_directory(ledger) / "000001.json").read_bytes() == before


def test_a_retry_of_the_same_decision_changes_nothing(ledger: Path) -> None:
    first = record_decision(ledger, request())
    again = record_decision(ledger, request())
    assert again.written is False
    assert again.stored == first.stored
    assert len(history(ledger)) == 1


def test_a_retry_of_a_later_decision_changes_nothing(ledger: Path) -> None:
    record_decision(ledger, request())
    record_decision(ledger, later(1, status=ReviewStatus.REJECTED))
    assert record_decision(ledger, later(1, status=ReviewStatus.REJECTED)).written is False


def test_same_records_from_same_decisions_are_byte_identical(tmp_path: Path) -> None:
    payloads = []
    for name in ("one", "two"):
        ledger = tmp_path / name
        ledger.mkdir(mode=0o700)
        record_decision(ledger, request())
        payloads.append((chapter_directory(ledger) / "000001.json").read_bytes())
    assert payloads[0] == payloads[1]


def test_a_stale_reviewer_is_a_conflict(ledger: Path) -> None:
    record_decision(ledger, request())
    entry = request(reviewer="reviewer-2", status=ReviewStatus.REJECTED)
    assert refusal(ledger, entry) is RefusalReason.REVIEW_CONFLICT
    assert len(history(ledger)) == 1


def test_a_future_revision_is_a_conflict(ledger: Path) -> None:
    assert refusal(ledger, request(expected_revision=1)) is RefusalReason.REVIEW_CONFLICT
    assert history(ledger) == ()


def test_a_decision_racing_to_the_same_revision_is_a_conflict(
    ledger: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rival = request(reviewer="reviewer-2")
    real = review_ledger.write_observation

    def competing(path: Path, text: str) -> bool:
        monkeypatch.setattr(review_ledger, "write_observation", real)
        record_decision(ledger, rival)
        return real(path, text)

    monkeypatch.setattr(review_ledger, "write_observation", competing)
    assert refusal(ledger, request()) is RefusalReason.REVIEW_CONFLICT
    assert history(ledger)[0].decision.reviewer == "reviewer-2"


def test_identical_decisions_racing_to_the_same_revision_write_once(
    ledger: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An identical winner is an idempotent retry, not a conflicting reviewer."""
    real = review_ledger.write_observation
    winners = []

    def competing(path: Path, text: str) -> bool:
        monkeypatch.setattr(review_ledger, "write_observation", real)
        winners.append(record_decision(ledger, request()))
        return real(path, text)

    monkeypatch.setattr(review_ledger, "write_observation", competing)
    outcome = record_decision(ledger, request())
    assert winners[0].written is True
    assert outcome.written is False
    assert outcome.stored == winners[0].stored
    assert history(ledger) == (outcome.stored,)
    assert sorted(path.name for path in chapter_directory(ledger).iterdir()) == ["000001.json"]


def test_a_record_with_invalid_utf8_is_corrupt(ledger: Path) -> None:
    record_decision(ledger, request())
    path = chapter_directory(ledger) / "000001.json"
    path.write_bytes(path.read_bytes().replace(b"reviewer-1", b"reviewer-\xff"))
    before = path.read_bytes()
    assert refusal(ledger, later(1)) is RefusalReason.REVIEW_LEDGER_CORRUPT
    assert path.read_bytes() == before
    assert sorted(item.name for item in path.parent.iterdir()) == ["000001.json"]


@pytest.mark.parametrize("extra", [0, 1])
def test_record_size_limit_is_inclusive_and_never_extends_on_refusal(
    ledger: Path, monkeypatch: pytest.MonkeyPatch, extra: int
) -> None:
    original = record_decision(ledger, request())
    path = chapter_directory(ledger) / "000001.json"
    before = path.read_bytes()
    monkeypatch.setattr(review_ledger, "MAX_RECORD_BYTES", len(before) - extra)
    if extra:
        assert refusal(ledger, later(1)) is RefusalReason.INPUT_TOO_LARGE
        assert path.read_bytes() == before
        assert sorted(item.name for item in path.parent.iterdir()) == ["000001.json"]
    else:
        assert history(ledger) == (original.stored,)


def test_a_forbidden_transition_is_refused(ledger: Path) -> None:
    record_decision(ledger, request(status=ReviewStatus.REJECTED))
    assert refusal(ledger, approval(expected_revision=1)) is RefusalReason.REVIEW_TRANSITION


def test_pending_cannot_follow_nothing(ledger: Path) -> None:
    entry = request(status=ReviewStatus.PENDING, reason_codes=(ReviewReason.REOPENED,))
    assert refusal(ledger, entry) is RefusalReason.REVIEW_TRANSITION


def test_a_decision_earlier_than_the_last_is_refused(ledger: Path) -> None:
    record_decision(ledger, request())
    entry = later(1, "2100-04-01T12:00:00Z", status=ReviewStatus.REJECTED)
    assert refusal(ledger, entry) is RefusalReason.REVIEW_TRANSITION


def test_a_decision_at_the_same_moment_is_kept(ledger: Path) -> None:
    record_decision(ledger, request())
    assert record_decision(ledger, later(1, "2100-04-02T12:00:00Z")).written is True


@pytest.mark.parametrize(
    ("moment", "allowed"),
    [
        ("2100-04-02T13:00:00+01:00", True),
        ("2100-04-02T11:30:00-01:00", True),
        ("2100-04-02T13:00:00+02:00", False),
    ],
)
def test_chronology_compares_instants_across_offsets(
    ledger: Path, moment: str, allowed: bool
) -> None:
    first = record_decision(ledger, request()).stored
    path = chapter_directory(ledger) / "000001.json"
    before = path.read_bytes()
    following = later(1, moment)
    if allowed:
        second = record_decision(ledger, following).stored
        assert second.decision.revision == 2
        assert second.decision.previous_decision_sha256 == first.sha256
        assert load_history(ledger, following.subject) == (first, second)
        assert verify_ledger(ledger) == ((first, second),)
    else:
        assert refusal(ledger, following) is RefusalReason.REVIEW_TRANSITION
        assert history(ledger) == (first,)
        assert sorted(item.name for item in path.parent.iterdir()) == ["000001.json"]
    assert path.read_bytes() == before


def test_invalid_fields_are_named_never_echoed(ledger: Path) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        record_decision(ledger, request(reviewer="Ola Nordmann", evidence=("note:Ola",)))
    assert caught.value.reason is RefusalReason.REVIEW_INVALID
    assert caught.value.detail == "evidence,reviewer"
    assert "Ola" not in str(caught.value)


def test_missing_reviewer_and_time_are_invalid(ledger: Path) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        record_decision(ledger, request(reviewer=None, decided_at=None))
    assert caught.value.detail == "decided_at,reviewer"


def test_a_failed_write_leaves_no_record_and_no_temporary_file(
    ledger: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_source: Path, _target: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(staging.os, "link", fail)
    assert refusal(ledger, request()) is RefusalReason.OUTPUT_WRITE_FAILED
    monkeypatch.undo()
    assert history(ledger) == ()
    assert list(chapter_directory(ledger).iterdir()) == []


def test_a_temporary_file_from_a_crash_is_ignored(ledger: Path) -> None:
    record_decision(ledger, request())
    leftover = chapter_directory(ledger) / ".000002.json.abc123"
    leftover.write_text('{"cut', encoding="utf-8")
    leftover.chmod(0o600)
    assert len(history(ledger)) == 1
    assert len(verify_ledger(ledger)) == 1


def test_an_unsafe_ledger_is_refused_before_writing(ledger: Path) -> None:
    ledger.chmod(0o750)
    assert refusal(ledger, request()) is RefusalReason.REVIEW_LEDGER_UNSAFE


def test_a_symlinked_chapter_directory_is_refused(ledger: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    chapter_directory(ledger).symlink_to(elsewhere)
    assert refusal(ledger, request()) is RefusalReason.REVIEW_LEDGER_UNSAFE
    assert list(elsewhere.iterdir()) == []


def test_a_record_others_can_read_is_refused(ledger: Path) -> None:
    record_decision(ledger, request())
    (chapter_directory(ledger) / "000001.json").chmod(0o644)
    assert refusal(ledger, later(1)) is RefusalReason.OBSERVATION_UNSAFE


def tamper(ledger: Path, name: str, **changes: Any) -> None:
    path = chapter_directory(ledger) / name
    payload = json.loads(path.read_text(encoding="utf-8")) | changes
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def two_decisions(ledger: Path) -> None:
    record_decision(ledger, request())
    record_decision(ledger, later(1))


@pytest.mark.parametrize(
    "change",
    [{"revision": 3}, {"previous_decision_sha256": "a" * 64}],
)
def test_an_edited_link_breaks_the_chain(ledger: Path, change: dict[str, Any]) -> None:
    two_decisions(ledger)
    tamper(ledger, "000002.json", **change)
    assert refusal(ledger, later(2)) is RefusalReason.REVIEW_LEDGER_CORRUPT


def test_an_edit_of_the_newest_record_is_seen_only_through_its_digest(ledger: Path) -> None:
    """The known limit: nothing points at the head yet, so an operator anchors its digest."""
    two_decisions(ledger)
    anchored = history(ledger)[-1].sha256
    tamper(ledger, "000002.json", reviewer="someone-else")
    assert history(ledger)[-1].sha256 != anchored


def test_an_edit_to_an_earlier_record_breaks_the_chain(ledger: Path) -> None:
    two_decisions(ledger)
    tamper(ledger, "000001.json", reviewer="someone-else")
    assert refusal(ledger, later(2)) is RefusalReason.REVIEW_LEDGER_CORRUPT


def test_a_record_reformatted_by_hand_is_corrupt(ledger: Path) -> None:
    record_decision(ledger, request())
    path = chapter_directory(ledger) / "000001.json"
    path.write_bytes(path.read_bytes() + b"\n")
    assert refusal(ledger, later(1)) is RefusalReason.REVIEW_LEDGER_CORRUPT


def test_a_record_that_is_not_json_is_corrupt(ledger: Path) -> None:
    record_decision(ledger, request())
    (chapter_directory(ledger) / "000001.json").write_text("{", encoding="utf-8")
    assert refusal(ledger, later(1)) is RefusalReason.REVIEW_LEDGER_CORRUPT


def test_a_removed_middle_record_is_corrupt(ledger: Path) -> None:
    two_decisions(ledger)
    record_decision(ledger, later(2, "2100-04-04T12:00:00Z"))
    (chapter_directory(ledger) / "000002.json").unlink()
    assert refusal(ledger, later(3)) is RefusalReason.REVIEW_LEDGER_CORRUPT


def test_a_record_moved_to_another_subject_is_corrupt(ledger: Path) -> None:
    record_decision(ledger, request())
    other = subject_of(with_changed_text(chapter()))
    target = ledger / subject_key(other)
    chapter_directory(ledger).rename(target)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        load_history(ledger, other)
    assert caught.value.reason is RefusalReason.REVIEW_LEDGER_CORRUPT


def test_a_stored_forbidden_transition_is_corrupt(ledger: Path) -> None:
    record_decision(ledger, request(status=ReviewStatus.REJECTED))
    first = history(ledger)[0]
    forged = approval(expected_revision=1, decided_at="2100-04-03T12:00:00Z")
    forgery = review_ledger._decision(forged, (first,))
    path = chapter_directory(ledger) / "000002.json"
    path.write_text(review_ledger.review_text(forgery), encoding="utf-8")
    path.chmod(0o600)
    assert refusal(ledger, later(2)) is RefusalReason.REVIEW_LEDGER_CORRUPT


def test_a_stored_decision_earlier_than_the_last_is_corrupt(ledger: Path) -> None:
    record_decision(ledger, request())
    first = history(ledger)[0]
    forged = later(1, "2100-04-01T12:00:00Z")
    path = chapter_directory(ledger) / "000002.json"
    path.write_text(
        review_ledger.review_text(review_ledger._decision(forged, (first,))), encoding="utf-8"
    )
    path.chmod(0o600)
    assert refusal(ledger, later(2)) is RefusalReason.REVIEW_LEDGER_CORRUPT


def test_an_approval_does_not_carry_to_changed_text(ledger: Path) -> None:
    record_decision(ledger, approval())
    other = subject_of(with_changed_text(chapter()))
    assert current_status(history(ledger)) is ReviewStatus.APPROVED
    assert current_status(load_history(ledger, other)) is ReviewStatus.PENDING


def test_verify_reports_every_chain_in_a_stable_order(ledger: Path) -> None:
    record_decision(ledger, request())
    other = subject_of(with_changed_text(chapter()))
    record_decision(ledger, request(subject=other, status=ReviewStatus.REJECTED))
    (ledger / ("0" * 64)).mkdir(mode=0o700)
    chains = verify_ledger(ledger)
    assert [len(chain) for chain in chains] == [1, 1]
    keys = [subject_key(chain[-1].decision.subject) for chain in chains]
    assert keys == sorted(keys)


def test_verify_of_a_ledger_never_created_is_empty(tmp_path: Path) -> None:
    assert verify_ledger(tmp_path / "gone") == ()


def test_verify_finds_a_broken_chain(ledger: Path) -> None:
    two_decisions(ledger)
    tamper(ledger, "000001.json", reviewer="someone-else")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        verify_ledger(ledger)
    assert caught.value.reason is RefusalReason.REVIEW_LEDGER_CORRUPT


@pytest.mark.parametrize("name", ["notes", "A" * 64, "a" * 63])
def test_verify_refuses_what_is_not_a_chain(ledger: Path, name: str) -> None:
    (ledger / name).mkdir(mode=0o700)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        verify_ledger(ledger)
    assert caught.value.reason is RefusalReason.REVIEW_LEDGER_CORRUPT


def test_verify_refuses_a_symlink_in_the_ledger(ledger: Path, tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    (ledger / ("a" * 64)).symlink_to(real)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        verify_ledger(ledger)
    assert caught.value.reason is RefusalReason.REVIEW_LEDGER_UNSAFE
