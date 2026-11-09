"""At most one effective standing policy per source (#738, Amendment 1 point 1).

Sign refuses an overlapping unrevoked policy, records are never replaced, and a
second overlapping record placed by hand denies every evaluation. Revoking one
policy never brings another into effect. Synthetic evidence under ``tmp_path``.
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import release_commands
from lovspor.forarbeider.policy_models import (
    PolicyObservation,
    PolicyRevocation,
    RegisteredPolicy,
    StandingPolicy,
    policy_faults,
)
from lovspor.forarbeider.release_models import record_text
from tests.unit.cli_output import said
from tests.unit.forarbeider_policy_support import OWNER, PolicyDesk, standing_policy
from tests.unit.forarbeider_release_support import private

AT = "2100-05-01T00:00:00Z"
LATER = "2100-04-04T13:00:00Z"
REVOKED = "2100-04-05T00:00:00Z"
AFTER = "2100-04-05T00:00:00Z/2100-07-01T00:00:00Z"


@pytest.fixture
def desk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PolicyDesk:
    monkeypatch.setattr(release_commands, "_interactive", lambda: True)
    return PolicyDesk(tmp_path)


def _edit_evidence(desk: PolicyDesk) -> None:
    """Point terms_page at a newer file; the one the first policy bound stays intact."""
    path = desk.evidence["terms_page"]
    desk.evidence["terms_page"] = private(path.with_name("terms-2.txt"), b"updated terms")


def _stored(desk: PolicyDesk) -> list[str]:
    return sorted(path.stem for path in (desk.workspace / "policies").iterdir())


def _hand_placed(desk: PolicyDesk, sha: str, **changes: Any) -> str:
    """A second record written straight into the workspace, around sign."""
    policy = StandingPolicy.model_validate({**desk.record(sha), **changes})
    text = record_text(policy)
    placed = hashlib.sha256(text.encode()).hexdigest()
    private(desk.workspace / "policies" / f"{placed}.json", text.encode())
    return placed


def test_sign_refuses_while_an_overlapping_policy_is_unrevoked(desk: PolicyDesk) -> None:
    first = desk.signed()
    _edit_evidence(desk)
    result = desk.sign(decided=LATER)
    assert result.exit_code == 1, result.output
    assert said(f"refused: policy_overlap: {first}", result.output)
    assert "Type to confirm" not in result.output
    assert _stored(desk) == [first]


def test_revoking_frees_a_new_signature_and_revives_nothing(desk: PolicyDesk) -> None:
    first = desk.signed()
    _edit_evidence(desk)
    assert desk.sign(decided=LATER).exit_code == 1
    assert desk.revoke(first, REVOKED).exit_code == 0
    assert said("state faults policy_revoked", desk.show(first, AT).output)
    second = desk.signed(decided=REVOKED, valid=AFTER)
    assert said("state usable", desk.show(second, AT).output)
    assert said("state faults policy_revoked", desk.show(first, AT).output)
    assert _stored(desk) == sorted([first, second])


def test_sign_refuses_a_policy_decided_before_an_overlapping_revocation(
    desk: PolicyDesk,
) -> None:
    first = desk.signed()
    assert desk.revoke(first, "2100-04-06T00:00:00Z").exit_code == 0
    _edit_evidence(desk)
    result = desk.sign(decided=REVOKED, valid=AFTER)
    assert result.exit_code == 1, result.output
    assert said(f"refused: release_out_of_order: {first}", result.output)
    assert _stored(desk) == [first]


def test_signing_a_revoked_policy_again_is_refused(desk: PolicyDesk) -> None:
    first = desk.signed()
    assert desk.revoke(first, REVOKED).exit_code == 0
    result = desk.sign()
    assert result.exit_code == 1, result.output
    assert said(f"refused: release_already_revoked: {first}", result.output)


def test_a_window_that_only_touches_does_not_overlap(desk: PolicyDesk) -> None:
    first = desk.signed()
    _edit_evidence(desk)
    second = desk.signed(decided=LATER, valid="2100-07-01T00:00:00Z/2100-09-01T00:00:00Z")
    assert said("state usable", desk.show(first, AT).output)
    assert said("state usable", desk.show(second, "2100-08-01T00:00:00Z").output)


def test_two_unrevoked_overlapping_records_deny_every_evaluation(desk: PolicyDesk) -> None:
    first = desk.signed()
    second = _hand_placed(desk, first, decided_at=LATER)
    apart = _hand_placed(
        desk, first, valid_from="2100-08-01T00:00:00Z", valid_until="2100-09-01T00:00:00Z"
    )
    assert said("state faults policy_ambiguous", desk.show(first, AT).output)
    assert said("state faults policy_ambiguous", desk.show(second, AT).output)
    assert said("state faults policy_ambiguous", desk.show(apart, "2100-08-15T00:00:00Z").output)


@pytest.mark.parametrize("revoked", ["older", "newer"])
def test_revoking_one_of_two_hand_placed_records_revives_neither(
    desk: PolicyDesk, revoked: str
) -> None:
    older = desk.signed()
    newer = _hand_placed(desk, older, decided_at=LATER)
    gone, kept = (older, newer) if revoked == "older" else (newer, older)
    assert desk.revoke(gone, REVOKED).exit_code == 0
    assert said("state faults policy_overlaps_revoked", desk.show(kept, AT).output)
    assert said("state faults policy_revoked", desk.show(gone, AT).output)


def test_sign_refuses_while_two_hand_placed_records_overlap(desk: PolicyDesk) -> None:
    first = desk.signed()
    _hand_placed(desk, first, decided_at=LATER)
    _edit_evidence(desk)
    result = desk.sign(decided="2100-04-04T14:00:00Z")
    assert result.exit_code == 1
    assert said("refused: policy_overlap", result.output)


def test_a_record_not_named_by_a_digest_is_refused_as_corrupt(desk: PolicyDesk) -> None:
    first = desk.signed()
    private(desk.workspace / "policies" / "notes.json", b"{}")
    result = desk.show(first, AT)
    assert result.exit_code == 1, result.output
    assert said("refused: release_record_corrupt: policies", result.output)


def test_revoke_refuses_a_moment_before_the_policy(desk: PolicyDesk) -> None:
    first = desk.signed()
    result = desk.revoke(first, "2100-04-04T11:59:59Z")
    assert result.exit_code == 1, result.output
    assert said(f"refused: release_out_of_order: {first}", result.output)
    assert not (desk.workspace / "policy_revocations").exists()


def _policy(decided: str, start: str, end: str, types: tuple[str, ...]) -> StandingPolicy:
    return standing_policy(
        decided_at=decided, valid_from=start, valid_until=end, document_types=types
    )


def _entry(sha: str, policy: StandingPolicy, revoked_at: str | None = None) -> RegisteredPolicy:
    revocation = None
    if revoked_at is not None:
        revocation = PolicyRevocation(
            schema_version=1,
            record_type="forarbeider_standing_policy_revocation",
            policy_sha256=sha,
            owner=OWNER,
            revoked_at=datetime.fromisoformat(revoked_at),
        )
    return RegisteredPolicy(sha256=sha, policy=policy, revocation=revocation)


def _faults(entry: RegisteredPolicy, *register: RegisteredPolicy) -> tuple[str, ...]:
    evidence = {item.path: item.sha256 for item in entry.policy.evidence}
    observed = PolicyObservation(evidence=evidence, policies=(entry, *register))
    return policy_faults(entry, observed, datetime.fromisoformat(AT))


BOTH = ("prop-l", "prop-ls")


def _main() -> StandingPolicy:
    return _policy("2100-04-04T12:00:00Z", "2100-04-05T00:00:00Z", "2100-07-01T00:00:00Z", BOTH)


@pytest.mark.parametrize(
    ("other", "faults"),
    [
        (("2100-04-04T13:00:00Z", "2100-06-30T00:00:00Z", "2100-08-01T00:00:00Z", BOTH), 1),
        (("2100-04-04T13:00:00Z", "2100-06-30T00:00:00Z", "2100-08-01T00:00:00Z", ("prop-ls",)), 1),
        (("2100-04-04T13:00:00Z", "2100-07-01T00:00:00Z", "2100-08-01T00:00:00Z", BOTH), 0),
        (("2100-04-04T13:00:00Z", "2100-03-01T00:00:00Z", "2100-04-05T00:00:00Z", BOTH), 0),
        (("2100-04-04T13:00:00Z", "2100-04-01T00:00:00Z", "2100-08-01T00:00:00Z", ("prop-l",)), 1),
    ],
)
def test_overlap_needs_a_shared_type_and_a_shared_moment(
    other: tuple[str, str, str, tuple[str, ...]], faults: int
) -> None:
    peer = _entry("b" * 64, _policy(*other))
    expected = ("policy_ambiguous",) * faults
    assert _faults(_entry("a" * 64, _main()), peer) == expected


def test_disjoint_types_never_overlap() -> None:
    main = _policy(
        "2100-04-04T12:00:00Z", "2100-04-05T00:00:00Z", "2100-07-01T00:00:00Z", ("prop-l",)
    )
    peer = _policy(
        "2100-04-04T13:00:00Z", "2100-04-05T00:00:00Z", "2100-07-01T00:00:00Z", ("prop-ls",)
    )
    assert _faults(_entry("a" * 64, main), _entry("b" * 64, peer)) == ()


@pytest.mark.parametrize(
    ("revoked_at", "faults"),
    [
        ("2100-04-04T12:00:01Z", ("policy_overlaps_revoked",)),
        ("2100-04-04T12:00:00Z", ()),
        ("2100-04-04T11:00:00Z", ()),
    ],
)
def test_a_revocation_after_this_decision_suppresses_it(
    revoked_at: str, faults: tuple[str, ...]
) -> None:
    peer = _policy("2100-04-04T10:00:00Z", "2100-04-05T00:00:00Z", "2100-07-01T00:00:00Z", BOTH)
    assert _faults(_entry("a" * 64, _main()), _entry("b" * 64, peer, revoked_at)) == faults


def test_a_revoked_policy_does_not_count_toward_ambiguity() -> None:
    peer = _policy("2100-04-04T10:00:00Z", "2100-04-05T00:00:00Z", "2100-07-01T00:00:00Z", BOTH)
    entry = _entry("a" * 64, _main())
    assert _faults(entry, _entry("b" * 64, peer, "2100-04-04T11:00:00Z")) == ()
