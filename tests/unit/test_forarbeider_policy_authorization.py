"""FC7b: signed policy authorization, verified fail-closed and never sufficient (#738).

Synthetic policies, records and throwaway SSH keys only. Every test that
reaches a verdict also checks that nothing is authorized or eligible: a
valid signature, a JSON field or a bot action never authorizes release.
"""

import json
import subprocess
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_authorization import (
    ALWAYS_BLOCKING,
    AuthorizationRequest,
    SignedRecord,
    authorization_gate,
    authorization_text,
    parse_authorization,
    verify_authorization,
    verify_authorization_record,
)
from lovspor.forarbeider.policy_authorization_models import (
    AuthorizationGate,
    PolicyAuthorization,
    RecordCheck,
)
from lovspor.forarbeider.policy_evaluation import decision_text, evaluate_policy
from lovspor.forarbeider.policy_trust import (
    ENGINE_TRUST_ROOT_SHA256,
    RELEASE_NAMESPACE,
    REVOKED_FILE,
    SIGNERS_FILE,
    TRUST_DIRECTORY,
    TrustRoot,
    load_trust_root,
    parse_trust_root,
)
from lovspor.forarbeider.publication_policy import policy_text
from tests.unit.forarbeider_authorization_support import (
    BOT_NAMESPACES,
    CHECKED_AT,
    OWNER_NAMESPACES,
    SIGNED_AT,
    VALID_FROM,
    Key,
    approval,
    automated,
    full_request,
    new_key,
    policy,
    raw,
    revocation,
    sign,
    signed,
    signer_line,
    trust_root,
)
from tests.unit.forarbeider_version_support import evidence


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Key]:
    directory = tmp_path_factory.mktemp("throwaway-keys")
    return {name: new_key(directory, name) for name in ("owner", "bot", "stranger", "other")}


@pytest.fixture(scope="module")
def root(keys: dict[str, Key], tmp_path_factory: pytest.TempPathFactory) -> TrustRoot:
    lines = [
        signer_line("test-owner", keys["owner"], OWNER_NAMESPACES),
        signer_line("test-bot", keys["bot"], BOT_NAMESPACES),
    ]
    return trust_root(tmp_path_factory.mktemp("root"), lines)


def verify(request: AuthorizationRequest, chosen_root: TrustRoot) -> PolicyAuthorization:
    verdict = verify_authorization(request, chosen_root)
    assert verdict.authorized is False
    assert verdict.release_eligible is False
    assert set(ALWAYS_BLOCKING) <= set(verdict.blockers)
    return verdict


def reasons(check: RecordCheck) -> set[str]:
    return set(check.reasons)


# --- valid signatures: verified, never authorized ------------------------------------


def test_valid_owner_signatures_verify_but_never_authorize(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    verdict = verify(full_request(policy(), keys["owner"]), root)

    assert verdict.status == "signatures_verified"
    assert verdict.policy_approval.status == "verified"
    assert verdict.automated_publication.status == "verified"
    signature = verdict.policy_approval.signature
    assert signature is not None
    assert signature.signature_verified is True
    assert (signature.principal, signature.role) == ("test-owner", "owner_policy")
    assert signature.key_fingerprint is not None
    assert signature.key_fingerprint.startswith("SHA256:")
    assert verdict.trust_root_origin == "unpinned"
    assert "trust_root_not_engine_pinned" in verdict.blockers
    assert verdict.legal_assessment == "not_verifiable"
    assert verdict.legal_assessment_reference_stated == "synthetic-reference-not-an-assessment"
    assert verdict.governance.d6_signatures == "proposed_not_accepted"
    assert verdict.governance.d11_automated_publication == "proposed_not_accepted"


def test_the_verdict_binds_the_exact_policy_and_trust_root(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    chosen = policy()
    verdict = verify(full_request(chosen, keys["owner"]), root)

    assert verdict.policy_sha256 == sha256(policy_text(chosen).encode()).hexdigest()
    assert (verdict.policy_id, verdict.policy_version) == ("synthetic-policy", 1)
    assert verdict.trust_root_sha256 == root.sha256
    assert verdict.trust_root_signers == 2
    assert verdict.checked_at == CHECKED_AT


# --- missing is not invalid ----------------------------------------------------------


def test_nothing_offered_is_missing_evidence_not_invalid(root: TrustRoot) -> None:
    request = AuthorizationRequest(policy_text(policy()).encode(), CHECKED_AT)
    verdict = verify(request, root)

    assert verdict.status == "missing_evidence"
    assert verdict.policy_approval.status == "missing"
    assert reasons(verdict.policy_approval) == {"record_missing"}
    assert "policy_approval_missing" in verdict.blockers
    assert "automated_publication_approval_missing" in verdict.blockers


def test_a_record_without_its_signature_is_missing(keys: dict[str, Key], root: TrustRoot) -> None:
    approved = SignedRecord(raw(approval(policy())), None)
    request = full_request(policy(), keys["owner"], policy_approval=approved)
    verdict = verify(request, root)

    assert verdict.policy_approval.status == "missing"
    assert reasons(verdict.policy_approval) == {"signature_missing"}
    assert verdict.policy_approval.record_id == "synthetic-approval-1"
    assert verdict.status == "invalid"  # the D11 approval now binds an unverified approval


def test_a_policy_approval_alone_leaves_automated_publication_missing(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    request = full_request(policy(), keys["owner"], automated_publication=None)
    verdict = verify(request, root)

    assert verdict.policy_approval.status == "verified"
    assert verdict.status == "missing_evidence"
    assert "automated_publication_approval_missing" in verdict.blockers


def test_an_automated_approval_without_its_policy_approval_is_missing(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    request = full_request(policy(), keys["owner"], policy_approval=None)
    verdict = verify(request, root)

    assert verdict.automated_publication.status == "missing"
    assert reasons(verdict.automated_publication) == {"bound_policy_approval_missing"}
    assert verdict.status == "missing_evidence"


# --- wrong key, role or trust root ---------------------------------------------------


def test_a_key_outside_the_trust_root_is_invalid(keys: dict[str, Key], root: TrustRoot) -> None:
    request = full_request(policy(), keys["stranger"])
    verdict = verify(request, root)

    assert verdict.status == "invalid"
    assert reasons(verdict.policy_approval) == {"signer_not_in_trust_root"}
    signature = verdict.policy_approval.signature
    assert signature is not None
    assert (signature.signature_verified, signature.principal) == (False, None)


def test_another_trust_root_does_not_trust_the_owner_key(
    keys: dict[str, Key], tmp_path: Path
) -> None:
    other = trust_root(tmp_path, [signer_line("test-other", keys["other"], OWNER_NAMESPACES)])
    verdict = verify(full_request(policy(), keys["owner"]), other)

    assert reasons(verdict.policy_approval) == {"signer_not_in_trust_root"}
    assert verdict.status == "invalid"


def test_the_bot_key_cannot_sign_a_policy_approval(keys: dict[str, Key], root: TrustRoot) -> None:
    approved = signed(approval(policy()), keys["bot"])
    verdict = verify(full_request(policy(), keys["owner"], policy_approval=approved), root)

    assert reasons(verdict.policy_approval) == {"signer_role_not_permitted"}
    signature = verdict.policy_approval.signature
    assert signature is not None
    assert signature.role == "bot_execution"
    assert verdict.status == "invalid"


def test_the_bot_cannot_approve_its_own_automated_publication(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    chosen = policy()
    approved = signed(approval(chosen), keys["owner"])
    by_bot = signed(automated(chosen, approved.record), keys["bot"])
    request = full_request(chosen, keys["owner"], policy_approval=approved)
    verdict = verify(replace(request, automated_publication=by_bot), root)

    assert reasons(verdict.automated_publication) == {"signer_role_not_permitted"}
    assert verdict.status == "invalid"


def test_a_bot_signature_in_its_own_namespace_never_verifies_as_policy(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    approved = signed(approval(policy()), keys["bot"], RELEASE_NAMESPACE)
    verdict = verify(full_request(policy(), keys["owner"], policy_approval=approved), root)

    assert reasons(verdict.policy_approval) == {"signature_namespace_mismatch"}
    signature = verdict.policy_approval.signature
    assert signature is not None
    assert signature.namespace == RELEASE_NAMESPACE


def test_the_shipped_trust_root_is_pinned_and_lists_no_key() -> None:
    shipped = load_trust_root()
    signers = (TRUST_DIRECTORY / SIGNERS_FILE).read_text("ascii").splitlines()
    revoked = (TRUST_DIRECTORY / REVOKED_FILE).read_text("ascii").splitlines()

    assert shipped.sha256 == ENGINE_TRUST_ROOT_SHA256
    assert shipped.origin == "engine_pinned"
    assert shipped.entries == ()
    assert all(not line.strip() or line.startswith("#") for line in signers + revoked)


def test_the_shipped_trust_root_refuses_every_signature(keys: dict[str, Key]) -> None:
    verdict = verify(full_request(policy(), keys["owner"]), load_trust_root())

    assert reasons(verdict.policy_approval) == {"trust_root_has_no_signer"}
    assert verdict.trust_root_origin == "engine_pinned"
    assert "trust_root_not_engine_pinned" not in verdict.blockers
    assert verdict.status == "invalid"


# --- altered bytes and digests -------------------------------------------------------


def test_an_approval_of_another_policy_body_does_not_bind(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    approved = signed(approval(policy()), keys["owner"])
    changed = policy(attachment_rules=({"kind": "attachment"},))
    request = full_request(changed, keys["owner"], policy_approval=approved)
    verdict = verify(request, root)

    assert reasons(verdict.policy_approval) == {"policy_digest_mismatch"}
    assert verdict.status == "invalid"


def test_record_bytes_altered_after_signing_are_invalid(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    original = signed(approval(policy()), keys["owner"])
    altered = raw(approval(policy(), valid_until=VALID_FROM.replace(year=2099)))
    request = full_request(
        policy(), keys["owner"], policy_approval=SignedRecord(altered, original.signature)
    )
    verdict = verify(request, root)

    assert reasons(verdict.policy_approval) == {"signature_invalid"}


def test_a_garbled_signature_is_malformed(keys: dict[str, Key], root: TrustRoot) -> None:
    approved = SignedRecord(raw(approval(policy())), b"-----BEGIN SSH SIGNATURE-----\nAAAA\n")
    verdict = verify(full_request(policy(), keys["owner"], policy_approval=approved), root)

    assert reasons(verdict.policy_approval) == {"signature_malformed"}
    assert verdict.policy_approval.status == "invalid"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda b: b.replace(b"\n", b"\r\n"), "record_noncanonical"),
        (lambda b: json.dumps(json.loads(b)).encode(), "record_noncanonical"),
        (lambda b: b"not json", "record_malformed"),
        (lambda b: b[:-3], "record_malformed"),
    ],
)
def test_malformed_and_noncanonical_records_are_invalid(
    keys: dict[str, Key], root: TrustRoot, mutate: Any, reason: str
) -> None:
    data = mutate(raw(approval(policy())))
    request = full_request(policy(), keys["owner"], policy_approval=signed(data, keys["owner"]))
    verdict = verify(request, root)

    assert reasons(verdict.policy_approval) == {reason}
    assert verdict.status == "invalid"


@pytest.mark.parametrize(
    "forged",
    [
        {"accepted": True},
        {"owner_approved": True},
        {"legal_assessment": "completed"},
        {"authorized": True},
        {"release_eligible": True},
        {"status": "accepted"},
        {"d11": "accepted"},
    ],
)
def test_forged_acceptance_metadata_is_refused_even_when_signed(
    keys: dict[str, Key], root: TrustRoot, forged: dict[str, Any]
) -> None:
    body = {**json.loads(raw(approval(policy()))), **forged}
    data = (json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    request = full_request(policy(), keys["owner"], policy_approval=signed(data, keys["owner"]))
    verdict = verify(request, root)

    assert reasons(verdict.policy_approval) == {"record_malformed"}
    assert verdict.status == "invalid"


def test_a_policy_claiming_approval_is_refused_before_any_signature(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    body = {**json.loads(policy_text(policy())), "approved_by_owner": True}
    data = (json.dumps(body, indent=2, sort_keys=True) + "\n").encode()
    request = replace(full_request(policy(), keys["owner"]), policy=data)

    with pytest.raises(ForarbeiderRefusedError, match="schema_invalid"):
        verify_authorization(request, root)


# --- time: expiry, windows, revocation ----------------------------------------------


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"valid_until": CHECKED_AT}, "approval_expired"),
        (
            {"valid_from": CHECKED_AT.replace(hour=13), "signed_at": SIGNED_AT},
            "approval_not_yet_valid",
        ),
        ({"signed_at": CHECKED_AT.replace(hour=13)}, "signed_after_check_time"),
    ],
)
def test_approvals_outside_their_time_are_invalid(
    keys: dict[str, Key], root: TrustRoot, changes: dict[str, Any], reason: str
) -> None:
    approved = signed(approval(policy(), **changes), keys["owner"])
    verdict = verify(full_request(policy(), keys["owner"], policy_approval=approved), root)

    assert reasons(verdict.policy_approval) == {reason}
    assert verdict.status == "invalid"


def test_a_key_used_outside_its_window_is_invalid(keys: dict[str, Key], tmp_path: Path) -> None:
    window = ("20260101000000Z", "20261101000000Z")
    expired = trust_root(
        tmp_path, [signer_line("test-owner", keys["owner"], OWNER_NAMESPACES, window)]
    )
    verdict = verify(full_request(policy(), keys["owner"]), expired)

    assert reasons(verdict.policy_approval) == {"signer_key_outside_validity"}


def test_a_revoked_key_is_invalid(keys: dict[str, Key], tmp_path: Path) -> None:
    lines = [signer_line("test-owner", keys["owner"], OWNER_NAMESPACES)]
    revoked = trust_root(tmp_path, lines, revoked=(keys["owner"],))
    verdict = verify(full_request(policy(), keys["owner"]), revoked)

    assert reasons(verdict.policy_approval) == {"signer_key_revoked"}
    assert verdict.status == "invalid"


def test_a_verified_revocation_withdraws_the_approval(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    withdrawn = signed(revocation("synthetic-approval-1"), keys["owner"])
    verdict = verify(full_request(policy(), keys["owner"], revocations=(withdrawn,)), root)

    assert reasons(verdict.policy_approval) == {"approval_revoked"}
    assert verdict.revocations[0].status == "verified"
    assert verdict.status == "invalid"


def test_an_unverified_revocation_fails_closed(keys: dict[str, Key], root: TrustRoot) -> None:
    by_bot = signed(revocation("synthetic-other-approval"), keys["bot"])
    verdict = verify(full_request(policy(), keys["owner"], revocations=(by_bot,)), root)

    assert verdict.revocations[0].status == "invalid"
    assert "revocation_not_verified" in verdict.blockers
    assert verdict.status == "invalid"


# --- replay across scope -------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"policy_id": "another-policy"}, {"policy_id_mismatch", "policy_digest_mismatch"}),
        ({"policy_version": 2}, {"policy_version_mismatch", "policy_digest_mismatch"}),
        (
            {"document_types": ("prop-l", "prop-ls")},
            {"policy_scope_mismatch", "policy_digest_mismatch"},
        ),
    ],
)
def test_an_approval_replayed_on_another_policy_is_invalid(
    keys: dict[str, Key], root: TrustRoot, changes: dict[str, Any], expected: set[str]
) -> None:
    approved = signed(approval(policy()), keys["owner"])
    other = policy(**changes)
    verdict = verify(full_request(other, keys["owner"], policy_approval=approved), root)

    assert reasons(verdict.policy_approval) == expected


def test_an_automated_approval_bound_to_another_policy_approval_is_invalid(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    chosen = policy()
    first = signed(approval(chosen), keys["owner"])
    second = signed(approval(chosen, approval_id="synthetic-approval-2"), keys["owner"])
    request = full_request(chosen, keys["owner"], policy_approval=first)
    replayed = signed(automated(chosen, second.record), keys["owner"])
    verdict = verify(replace(request, automated_publication=replayed), root)

    assert reasons(verdict.automated_publication) == {"policy_approval_binding_mismatch"}


def test_one_record_kind_never_verifies_as_another(keys: dict[str, Key], root: TrustRoot) -> None:
    approved = signed(approval(policy()), keys["owner"])
    request = full_request(policy(), keys["owner"], automated_publication=approved)
    verdict = verify(request, root)

    assert reasons(verdict.automated_publication) == {"record_malformed"}


def test_a_signature_moved_to_another_record_is_invalid(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    request = full_request(policy(), keys["owner"])
    assert request.policy_approval is not None
    assert request.automated_publication is not None
    moved = SignedRecord(request.automated_publication.record, request.policy_approval.signature)
    verdict = verify(replace(request, automated_publication=moved), root)

    assert reasons(verdict.automated_publication) == {"signature_invalid"}


# --- D10 and D11 ---------------------------------------------------------------------


def test_automated_approval_without_a_legal_reference_is_blocked(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    chosen = policy()
    approved = signed(approval(chosen), keys["owner"])
    bare = signed(
        automated(chosen, approved.record, legal_assessment_reference=None), keys["owner"]
    )
    request = full_request(chosen, keys["owner"], policy_approval=approved)
    verdict = verify(replace(request, automated_publication=bare), root)

    assert verdict.status == "signatures_verified"
    assert "legal_assessment_reference_missing" in verdict.blockers
    assert verdict.legal_assessment_reference_stated is None


def test_a_stated_legal_reference_is_never_a_completed_assessment(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    verdict = verify(full_request(policy(), keys["owner"]), root)

    assert verdict.legal_assessment == "not_verifiable"
    assert "d10_legal_assessment_not_verifiable" in verdict.blockers
    assert verdict.governance.d10_legal_assessment == "unresolved_not_verifiable"


@pytest.mark.parametrize(
    "claim",
    [
        {"authorized": True},
        {"release_eligible": True},
        {"governance": {"d6_signatures": "accepted"}},
        {"governance": {"d11_automated_publication": "accepted"}},
        {"governance": {"d10_legal_assessment": "completed"}},
        {"legal_assessment": "verified"},
        {"policy_status": "approved"},
        {"blockers": []},
    ],
)
def test_no_record_may_claim_authorization_or_acceptance(
    keys: dict[str, Key], root: TrustRoot, claim: dict[str, Any]
) -> None:
    verdict = verify(full_request(policy(), keys["owner"]), root)
    body = {**json.loads(authorization_text(verdict)), **claim}
    data = (json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()

    with pytest.raises(ForarbeiderRefusedError, match="schema_invalid"):
        parse_authorization(data)


@pytest.mark.parametrize("model", [PolicyAuthorization, AuthorizationGate])
def test_the_models_have_no_true_eligibility(model: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"release_eligible": True})
    assert model.model_fields["release_eligible"].default is False


# --- determinism and integrity -------------------------------------------------------


def test_verification_is_deterministic_and_reproducible(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    request = full_request(policy(), keys["owner"])
    first = authorization_text(verify(request, root)).encode()

    assert authorization_text(verify(request, root)).encode() == first
    assert parse_authorization(first) == verify_authorization(request, root)
    assert verify_authorization_record(first, request, root).status == "signatures_verified"


def test_a_hand_edited_verdict_parses_but_never_verifies(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    request = full_request(policy(), keys["owner"])
    missing = verify(replace(request, automated_publication=None), root)
    forged = authorization_text(
        missing.model_copy(update={"status": "signatures_verified"})
    ).encode()

    parse_authorization(forged)
    with pytest.raises(ForarbeiderRefusedError, match="not reproduced"):
        verify_authorization_record(forged, replace(request, automated_publication=None), root)


def test_a_verdict_does_not_verify_against_another_trust_root(
    keys: dict[str, Key], root: TrustRoot, tmp_path: Path
) -> None:
    request = full_request(policy(), keys["owner"])
    first = authorization_text(verify(request, root)).encode()
    lines = [signer_line("test-owner", keys["owner"], OWNER_NAMESPACES)]

    with pytest.raises(ForarbeiderRefusedError, match="not reproduced"):
        verify_authorization_record(first, request, trust_root(tmp_path, lines))


def test_a_check_time_without_utc_is_refused(keys: dict[str, Key], root: TrustRoot) -> None:
    request = replace(
        full_request(policy(), keys["owner"]), checked_at=CHECKED_AT.replace(tzinfo=None)
    )

    with pytest.raises(ForarbeiderRefusedError, match="invalid_timestamp"):
        verify_authorization(request, root)


def test_ssh_keygen_unavailable_is_unverifiable_not_verified(
    keys: dict[str, Key], root: TrustRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = full_request(policy(), keys["owner"])

    def unavailable(*_: object, **__: object) -> None:
        raise FileNotFoundError("ssh-keygen")

    monkeypatch.setattr(subprocess, "run", unavailable)
    verdict = verify(request, root)

    assert reasons(verdict.policy_approval) == {"signature_unverifiable"}
    assert verdict.status == "invalid"


# --- the trust root's own format ------------------------------------------------------


def _line(keys: dict[str, Key], options: str, principal: str = "test-owner") -> str:
    return f"{principal} {options} {keys['owner'].public}"


_WINDOW = 'valid-after="20260101000000Z",valid-before="20300101000000Z"'


@pytest.mark.parametrize(
    "options",
    [
        f'namespaces="lovspor-forarbeider-policy,lovspor-forarbeider-release",{_WINDOW}',
        'namespaces="lovspor-forarbeider-policy"',
        f'namespaces="lovspor-crosswalk-approval",{_WINDOW}',
        f'namespaces="lovspor-forarbeider-policy",{_WINDOW},cert-authority=""',
        'namespaces="lovspor-forarbeider-policy",valid-after="20260101",valid-before="20300101"',
        'namespaces="lovspor-forarbeider-policy",valid-after="20300101000000Z",valid-before="20260101000000Z"',
    ],
)
def test_a_trust_root_entry_outside_the_strict_format_is_refused(
    keys: dict[str, Key], options: str
) -> None:
    with pytest.raises(ForarbeiderRefusedError, match="trust root"):
        parse_trust_root(_line(keys, options).encode(), b"")


@pytest.mark.parametrize("principal", ["*", "test-owner,test-bot", "Test Owner"])
def test_a_wildcard_or_list_principal_is_refused(keys: dict[str, Key], principal: str) -> None:
    line = _line(keys, f'namespaces="lovspor-forarbeider-policy",{_WINDOW}', principal)
    with pytest.raises(ForarbeiderRefusedError, match="trust root"):
        parse_trust_root(line.encode(), b"")


def test_one_key_cannot_be_both_owner_and_bot(keys: dict[str, Key]) -> None:
    owner = signer_line("test-owner", keys["owner"], OWNER_NAMESPACES)
    bot = signer_line("test-bot", keys["owner"], BOT_NAMESPACES)

    with pytest.raises(ForarbeiderRefusedError, match="listed twice"):
        parse_trust_root(f"{owner}\n{bot}\n".encode(), b"")


def test_a_trust_root_file_that_is_a_symlink_is_refused(
    keys: dict[str, Key], tmp_path: Path
) -> None:
    real = tmp_path / "real"
    real.write_text(signer_line("test-owner", keys["owner"], OWNER_NAMESPACES) + "\n", "ascii")
    (tmp_path / SIGNERS_FILE).symlink_to(real)
    (tmp_path / REVOKED_FILE).write_text("", "ascii")

    with pytest.raises(ForarbeiderRefusedError, match="not a regular file"):
        load_trust_root(tmp_path)


def test_a_signature_names_only_what_the_root_lists(keys: dict[str, Key], root: TrustRoot) -> None:
    """The caller's key is never an anchor: signing with any key changes nothing."""
    chosen = policy()
    data = raw(approval(chosen))
    for name in ("stranger", "other"):
        offered = SignedRecord(data, sign(keys[name], data))
        verdict = verify(full_request(chosen, keys["owner"], policy_approval=offered), root)
        assert verdict.policy_approval.status == "invalid"


# --- FC7 integration -----------------------------------------------------------------


def test_the_gate_sets_the_fc7_decision_beside_the_verdict_and_blocks_release(
    keys: dict[str, Key], root: TrustRoot
) -> None:
    chosen = policy()
    request = full_request(chosen, keys["owner"])
    gate = authorization_gate(evidence(), request, root)
    decision = evaluate_policy(evidence(), chosen)
    verdict = verify(request, root)

    assert gate.release_eligible is False
    assert gate.published is False
    assert gate.version_id == decision.version_id
    assert gate.policy_decision_sha256 == sha256(decision_text(decision).encode()).hexdigest()
    assert gate.authorization_sha256 == sha256(authorization_text(verdict).encode()).hexdigest()
    assert gate.policy_blockers == decision.document.blockers
    assert gate.authorization_status == "signatures_verified"
    assert decision.document.eligible is False
    assert decision.document.authorization == "pending_no_verifiable_mechanism"


def test_the_gate_takes_no_caller_eligibility(keys: dict[str, Key], root: TrustRoot) -> None:
    gate = authorization_gate(evidence(), full_request(policy(), keys["owner"]), root)
    body = {**gate.model_dump(mode="json"), "release_eligible": True}

    with pytest.raises(ValidationError):
        AuthorizationGate.model_validate(body)
    assert set(ALWAYS_BLOCKING) <= set(gate.authorization_blockers)


def test_signed_at_is_the_key_window_time(keys: dict[str, Key], tmp_path: Path) -> None:
    """The key window is judged at the stated signing time, the approval window at the check."""
    window = ("20261114090000Z", "20261114110000Z")
    lines = [signer_line("test-owner", keys["owner"], OWNER_NAMESPACES, window)]
    verdict = verify(full_request(policy(), keys["owner"]), trust_root(tmp_path, lines))

    assert verdict.policy_approval.status == "verified"
    assert SIGNED_AT < CHECKED_AT


def test_only_the_trust_root_runs_ssh_keygen() -> None:
    source = Path(__file__).resolve().parents[2] / "src" / "lovspor"
    importers = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if "lovspor.ssh_signature" in path.read_text(encoding="utf-8")
    }
    assert importers == {"forarbeider/policy_trust.py"}
