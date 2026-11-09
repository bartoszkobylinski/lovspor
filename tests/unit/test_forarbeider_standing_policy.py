"""The standing release policy: signed once at a terminal, revocable (#738, Amendment 1).

Synthetic evidence only, written owner-only under ``tmp_path``.
"""

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider import release_commands
from lovspor.forarbeider.policy_commands import policy_show_impl
from lovspor.forarbeider.policy_models import (
    PolicyObservation,
    PolicyRevocation,
    RegisteredPolicy,
    StandingPolicy,
    policy_faults,
)
from lovspor.forarbeider.release_models import record_text
from tests.unit.cli_output import said
from tests.unit.forarbeider_policy_support import (
    DECIDED,
    KINDS,
    OWNER,
    UNTIL,
    VALID,
    PolicyDesk,
    shown_phrase,
)
from tests.unit.forarbeider_policy_support import evidence_options as _evidence_options
from tests.unit.forarbeider_policy_support import (
    standing_policy as _policy,
)
from tests.unit.forarbeider_release_support import PRIVATE_MARKER, private


@pytest.fixture
def desk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PolicyDesk:
    monkeypatch.setattr(release_commands, "_interactive", lambda: True)
    return PolicyDesk(tmp_path)


def test_sign_stores_the_policy_under_its_digest(desk: PolicyDesk) -> None:
    sha = desk.signed()
    record = desk.record(sha)
    assert record["record_type"] == "forarbeider_standing_policy"
    assert record["source_provider"] == "regjeringen.no"
    assert record["document_types"] == ["prop-l", "prop-ls"]
    assert [item["kind"] for item in record["evidence"]] == list(KINDS)
    assert record["owner"] == OWNER
    assert record["clears"] == "unflagged_chapters"
    assert "unflagged_published_unread" not in record
    assert "git_permanence_accepted" not in record
    assert record["legal_sufficiency_decided_by_code"] is False
    assert record["identity_authenticated"] is False


def test_sign_binds_each_file_by_its_digest_and_size(desk: PolicyDesk) -> None:
    record = desk.record(desk.signed())
    for item in record["evidence"]:
        payload = desk.evidence[item["kind"]].read_bytes()
        assert item["sha256"] == hashlib.sha256(payload).hexdigest()
        assert item["size_bytes"] == len(payload)
        assert item["path"] == str(desk.evidence[item["kind"]])


def test_sign_prints_no_path_handle_or_evidence_text(desk: PolicyDesk) -> None:
    result = desk.sign()
    for secret in (PRIVATE_MARKER, OWNER, str(desk.root)):
        assert secret not in result.output, secret
    assert said("not permission to publish a flagged chapter", result.output)


@pytest.mark.parametrize("missing", KINDS)
def test_sign_refuses_a_policy_missing_any_kind(desk: PolicyDesk, missing: str) -> None:
    evidence = [f"{kind}={path}" for kind, path in desk.evidence.items() if kind != missing]
    result = desk.sign(options=_evidence_options(evidence))
    assert result.exit_code == 1
    assert said("refused: release_invalid: evidence", result.output)
    assert not (desk.workspace / "policies").exists()


def test_sign_refuses_a_kind_given_twice(desk: PolicyDesk) -> None:
    evidence = [f"{kind}={path}" for kind, path in desk.evidence.items()]
    extra = private(desk.root / "evidence" / "again.txt", b"another note")
    evidence.append(f"source_rights={extra}")
    result = desk.sign(options=_evidence_options(evidence))
    assert result.exit_code == 1
    assert said("refused: release_invalid: evidence", result.output)


@pytest.mark.parametrize(
    "bad", ["unknown_kind=/tmp/x", "source_rights", "source_rights=relative/path", "=/tmp/x"]
)
def test_sign_refuses_a_malformed_evidence_option(desk: PolicyDesk, bad: str) -> None:
    evidence = [f"{kind}={path}" for kind, path in desk.evidence.items() if kind != "source_rights"]
    result = desk.sign(options=_evidence_options([*evidence, bad]))
    assert result.exit_code == 1
    assert said("refused: release_invalid: evidence", result.output)


def test_sign_refuses_an_empty_or_missing_evidence_file(desk: PolicyDesk) -> None:
    desk.evidence["source_rights"].unlink()
    assert desk.sign().exit_code == 1
    private(desk.evidence["source_rights"], b"")
    result = desk.sign()
    assert result.exit_code == 1
    assert said("refused: input_unreadable: evidence", result.output)
    assert not (desk.workspace / "policies").exists()


def test_sign_needs_a_person_at_a_terminal(
    desk: PolicyDesk, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_commands, "_interactive", lambda: False)
    result = desk.sign()
    assert result.exit_code == 1
    assert said("refused: review_not_interactive: policy", result.output)
    assert not (desk.workspace / "policies").exists()


def test_sign_refuses_a_wrong_phrase(desk: PolicyDesk) -> None:
    result = desk.sign(phrase="yes\n")
    assert result.exit_code == 1
    assert said("refused: review_not_interactive: policy", result.output)
    assert not (desk.workspace / "policies").exists()


def test_sign_refuses_a_window_that_ends_before_it_starts(desk: PolicyDesk) -> None:
    result = desk.sign(valid="2100-07-01T00:00:00Z/2100-04-04T12:00:00Z")
    assert result.exit_code == 1
    assert said("refused: release_invalid", result.output)


def test_show_reports_a_policy_in_force_as_usable(desk: PolicyDesk) -> None:
    sha = desk.signed()
    result = desk.show(sha, "2100-05-01T00:00:00Z")
    assert result.exit_code == 0, result.output
    assert said(f"policy {sha} regjeringen.no prop-l,prop-ls", result.output)
    assert said("state usable", result.output)
    for secret in (PRIVATE_MARKER, OWNER, str(desk.root)):
        assert secret not in result.output, secret


@pytest.mark.parametrize(
    ("at", "fault"),
    [
        ("2100-07-01T00:00:00Z", "policy_expired"),
        ("2100-04-04T11:59:59Z", "evaluated_before_policy"),
    ],
)
def test_show_reports_a_moment_outside_the_window(desk: PolicyDesk, at: str, fault: str) -> None:
    result = desk.show(desk.signed(), at)
    assert result.exit_code == 0
    assert said(f"state faults {fault}", result.output)


def test_show_reports_changed_and_missing_evidence(desk: PolicyDesk) -> None:
    sha = desk.signed()
    desk.evidence["source_rights"].write_bytes(b"edited after signing")
    desk.evidence["terms_page"].unlink()
    result = desk.show(sha, "2100-05-01T00:00:00Z")
    assert said("state faults policy_evidence_changed,policy_evidence_unavailable", result.output)


def test_revoke_withdraws_the_policy_and_show_says_so(desk: PolicyDesk) -> None:
    sha = desk.signed()
    revoked = desk.invoke(["revoke", "--policy", sha, "--decided", f"{OWNER}@2100-04-05T00:00:00Z"])
    assert revoked.exit_code == 0, revoked.output
    assert said(f"revoked policy {sha}", revoked.output)
    assert said("state faults policy_revoked", desk.show(sha, "2100-05-01T00:00:00Z").output)


def test_revoke_twice_or_unknown_is_refused(desk: PolicyDesk) -> None:
    sha = desk.signed()
    decided = ["--decided", f"{OWNER}@2100-04-05T00:00:00Z"]
    assert desk.invoke(["revoke", "--policy", sha, *decided]).exit_code == 0
    again = desk.invoke(["revoke", "--policy", sha, *decided])
    assert again.exit_code == 1
    assert said("refused: release_already_revoked", again.output)
    unknown = desk.invoke(["revoke", "--policy", "0" * 64, *decided])
    assert said("refused: release_record_missing", unknown.output)


def test_show_refuses_an_edited_policy_record(desk: PolicyDesk) -> None:
    sha = desk.signed()
    path = desk.workspace / "policies" / f"{sha}.json"
    path.write_text(path.read_text().replace(OWNER, "owner-c"))
    result = desk.show(sha, "2100-05-01T00:00:00Z")
    assert result.exit_code == 1
    assert said("refused: release_record_corrupt", result.output)


def _intact(policy: StandingPolicy) -> dict[str, str | None]:
    return {item.path: item.sha256 for item in policy.evidence}


def _revocation(at: str, sha: str = "f" * 64) -> PolicyRevocation:
    return PolicyRevocation(
        schema_version=1,
        record_type="forarbeider_standing_policy_revocation",
        policy_sha256=sha,
        owner=OWNER,
        revoked_at=datetime.fromisoformat(at),
    )


def _entry(
    policy: StandingPolicy, revocation: PolicyRevocation | None = None, sha: str = "f" * 64
) -> RegisteredPolicy:
    return RegisteredPolicy(sha256=sha, policy=policy, revocation=revocation)


def _observed(policy: StandingPolicy, *register: RegisteredPolicy) -> PolicyObservation:
    return PolicyObservation(evidence=_intact(policy), policies=register)


@pytest.mark.parametrize(
    ("at", "faults"),
    [
        ("2100-04-04T11:59:59Z", ("evaluated_before_policy", "policy_not_yet_valid")),
        ("2100-04-04T12:00:00Z", ("policy_not_yet_valid",)),
        ("2100-04-05T00:00:00Z", ()),
        ("2100-06-30T23:59:59Z", ()),
        ("2100-07-01T00:00:00Z", ("policy_expired",)),
    ],
)
def test_policy_faults_at_the_window_edges(at: str, faults: tuple[str, ...]) -> None:
    policy = _policy()
    found = policy_faults(_entry(policy), _observed(policy), datetime.fromisoformat(at))
    assert found == faults


def test_policy_faults_name_revocation_and_each_evidence_fault_once() -> None:
    policy = _policy()
    evidence: dict[str, str | None] = _intact(policy)
    first, second, third = (item.path for item in policy.evidence[:3])
    evidence[first], evidence[second] = None, "b" * 64
    del evidence[third]
    at = datetime.fromisoformat("2100-05-01T00:00:00Z")
    revocation = _revocation("2100-04-05T00:00:00Z")
    observed = PolicyObservation(evidence=evidence, policies=())
    assert policy_faults(_entry(policy, revocation), observed, at) == (
        "policy_evidence_changed",
        "policy_evidence_unavailable",
        "policy_revoked",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"document_types": ("prop-ls", "prop-l")},
        {"document_types": ("prop-l", "prop-l")},
        {"document_types": ()},
        {"valid_until": "2100-04-05T00:00:00Z"},
        {"unflagged_published_unread": True},
        {"git_permanence_accepted": True},
        {"identity_authenticated": True},
        {"source_provider": "lovdata.no"},
    ],
)
def test_the_policy_model_refuses_an_out_of_scope_record(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _policy(**changes)


def test_the_policy_model_refuses_evidence_out_of_order() -> None:
    evidence = tuple(reversed(_policy().evidence))
    with pytest.raises(ValidationError, match="every kind, once each"):
        _policy(evidence=evidence)


def test_the_policy_is_stored_in_utc() -> None:
    policy = _policy(decided_at="2100-04-04T14:00:00+02:00")
    assert policy.decided_at.utcoffset() == timedelta(0)
    assert policy.decided_at == datetime.fromisoformat("2100-04-04T12:00:00Z")


def test_sign_reordered_evidence_is_the_same_record_and_never_a_second(desk: PolicyDesk) -> None:
    evidence = [f"{kind}={path}" for kind, path in reversed(desk.evidence.items())]
    reordered = desk.invoke(desk.sign_args(_evidence_options(evidence), DECIDED, VALID), "\n")
    assert shown_phrase(reordered.output) == shown_phrase(desk.shown().output)
    sha = desk.signed()
    path = desk.workspace / "policies" / f"{sha}.json"
    original = path.read_bytes()
    result = desk.sign(options=_evidence_options(evidence))
    assert result.exit_code == 1, result.output
    assert said(f"refused: policy_overlap: {sha}", result.output)
    assert path.read_bytes() == original
    assert hashlib.sha256(original).hexdigest() == sha
    assert list((desk.workspace / "policies").iterdir()) == [path]
    assert not desk.ledger.exists()


@pytest.mark.parametrize(
    ("unsafe", "reason"),
    [
        ("shared", "observation_unsafe"),
        ("symlink", "unsafe_input_path"),
        ("checkout", "unsafe_output_path"),
    ],
)
def test_sign_refuses_unsafe_evidence_without_disclosing_its_path(
    desk: PolicyDesk, unsafe: str, reason: str
) -> None:
    path = desk.evidence["source_rights"]
    if unsafe == "shared":
        path.chmod(0o644)
    elif unsafe == "symlink":
        target = private(path.with_name("private-target.txt"), b"private note")
        path.unlink()
        path.symlink_to(target)
    else:
        checkout = desk.root / "checkout"
        desk.evidence["source_rights"] = private(checkout / "note.txt", b"private note")
        (checkout / ".git").mkdir()
    result = desk.sign()
    assert result.exit_code == 1, result.output
    assert said(f"refused: {reason}: evidence source_rights", result.output)
    assert str(desk.root) not in result.output
    assert PRIVATE_MARKER not in result.output
    assert not (desk.workspace / "policies").exists()


def test_show_refuses_a_revocation_that_names_another_policy(desk: PolicyDesk) -> None:
    sha = desk.signed()
    revocation = PolicyRevocation(
        schema_version=1,
        record_type="forarbeider_standing_policy_revocation",
        policy_sha256="0" * 64,
        owner=OWNER,
        revoked_at="2100-04-05T00:00:00Z",
    )
    private(
        desk.workspace / "policy_revocations" / f"{sha}.json",
        record_text(revocation).encode(),
    )
    result = desk.show(sha, "2100-05-01T00:00:00Z")
    assert result.exit_code == 1, result.output
    assert said("refused: release_record_corrupt", result.output)
    assert str(desk.root) not in result.output


@pytest.mark.parametrize("command", ["show", "revoke"])
def test_policy_commands_name_the_invalid_digest_field(desk: PolicyDesk, command: str) -> None:
    if command == "show":
        result = desk.show("private-invalid-digest", DECIDED)
    else:
        result = desk.invoke(
            ["revoke", "--policy", "private-invalid-digest", "--decided", f"{OWNER}@{DECIDED}"]
        )
    assert result.exit_code == 1, result.output
    assert said("refused: release_invalid: policy", result.output)
    assert "private-invalid-digest" not in result.output
    assert not (desk.workspace / "policies").exists()
    assert not (desk.workspace / "policy_revocations").exists()


@pytest.mark.parametrize("at", ["private-invalid-moment", "2100-05-01T00:00:00"])
def test_show_names_the_invalid_moment_field(desk: PolicyDesk, at: str) -> None:
    sha = desk.signed()
    before = (desk.workspace / "policies" / f"{sha}.json").read_bytes()
    result = desk.show(sha, at)
    assert result.exit_code == 1, result.output
    assert said("refused: invalid_timestamp: at", result.output)
    assert at not in result.output
    assert (desk.workspace / "policies" / f"{sha}.json").read_bytes() == before
    assert not (desk.workspace / "policy_revocations").exists()


def test_show_names_evidence_that_became_unsafe(desk: PolicyDesk) -> None:
    sha = desk.signed()
    desk.evidence["source_rights"].chmod(0o644)
    result = desk.show(sha, DECIDED)
    assert result.exit_code == 1, result.output
    assert said("refused: observation_unsafe: evidence source_rights", result.output)
    assert str(desk.root) not in result.output
    assert PRIVATE_MARKER not in result.output
    assert not (desk.workspace / "policy_revocations").exists()


def test_show_returns_the_complete_policy_summary(desk: PolicyDesk) -> None:
    sha = desk.signed()
    record = desk.record(sha)
    summary = policy_show_impl(
        release_commands.ReleaseScope(workspace=desk.workspace, ledger=desk.ledger),
        sha,
        "2100-05-01T00:00:00Z",
        None,
    )
    expected = [
        f"policy {sha} regjeringen.no prop-l,prop-ls valid "
        "2100-04-04T12:00:00+00:00/2100-07-01T00:00:00+00:00",
        *(f"evidence {item['kind']} {item['sha256']}" for item in record["evidence"]),
        "state usable; identity_authenticated=false",
        "source proof missing",
        "source faults source_proof_missing",
        "automatic clearance denied",
    ]
    assert summary == "\n".join(expected)


def _sha12(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def test_sign_shows_every_bound_digest_and_the_record_digest_before_the_prompt(
    desk: PolicyDesk,
) -> None:
    shown = desk.shown()
    assert shown.exit_code == 1
    phrase = shown_phrase(shown.output)
    prefix = phrase.split()[3]
    assert phrase == f"sign standing policy {prefix} regjeringen.no prop-l,prop-ls until {UNTIL}"
    assert said(f"policy {prefix} source regjeringen.no types prop-l,prop-ls", shown.output)
    assert said(f"valid 2100-04-04T12:00:00+00:00/{UNTIL}", shown.output)
    for kind, path in desk.evidence.items():
        assert said(f"evidence {kind} {_sha12(path)}", shown.output), kind
    assert not (desk.workspace / "policies").exists()
    sha = desk.signed()
    assert sha.startswith(prefix)
    stored = desk.workspace / "policies" / f"{sha}.json"
    assert hashlib.sha256(stored.read_bytes()).hexdigest().startswith(prefix)


@pytest.mark.parametrize("kind", KINDS)
def test_the_phrase_changes_with_any_evidence_byte(desk: PolicyDesk, kind: str) -> None:
    before = shown_phrase(desk.shown().output)
    path = desk.evidence[kind]
    path.write_bytes(path.read_bytes()[:-1] + b"X")
    after = shown_phrase(desk.shown().output)
    assert before != after
    assert after.startswith("sign standing policy ")
    result = desk.sign(phrase=f"{before}\n")
    assert result.exit_code == 1
    assert said("refused: review_not_interactive: policy", result.output)
    assert not (desk.workspace / "policies").exists()


def test_the_phrase_changes_with_the_decision_moment(desk: PolicyDesk) -> None:
    first = shown_phrase(desk.shown().output)
    later = shown_phrase(desk.shown(decided="2100-04-04T12:00:01Z").output)
    assert first.split()[3] != later.split()[3]


def test_sign_requires_its_own_acceptance_file_for_unread_publication(desk: PolicyDesk) -> None:
    assert "unflagged_unread_acceptance" in KINDS
    sha = desk.signed()
    record = desk.record(sha)
    kinds = [item["kind"] for item in record["evidence"]]
    assert "unflagged_unread_acceptance" in kinds
    acceptance = desk.evidence["unflagged_unread_acceptance"]
    bound = next(i for i in record["evidence"] if i["kind"] == "unflagged_unread_acceptance")
    assert bound["sha256"] == hashlib.sha256(acceptance.read_bytes()).hexdigest()


@pytest.mark.parametrize("same", ["path", "bytes"])
def test_sign_refuses_one_file_bound_for_two_kinds(desk: PolicyDesk, same: str) -> None:
    rights = desk.evidence["source_rights"]
    if same == "path":
        desk.evidence["publication_purpose"] = rights
    else:
        desk.evidence["publication_purpose"].unlink()
        private(desk.evidence["publication_purpose"], rights.read_bytes())
    result = desk.sign()
    assert result.exit_code == 1, result.output
    assert said("refused: release_invalid: evidence", result.output)
    assert not (desk.workspace / "policies").exists()


def test_the_policy_model_refuses_one_digest_for_two_kinds() -> None:
    evidence = list(_policy().evidence)
    evidence[1] = evidence[1].model_copy(update={"sha256": evidence[0].sha256})
    with pytest.raises(ValidationError, match="one file for two kinds"):
        _policy(evidence=tuple(evidence))
