"""AR6a: a release manifest publishes only under the bot's genuine signature (#738; §9.3, §9.3a).

Every case runs on paths A and B with real ``ssh-keygen`` signatures, never a
mocked verdict. The signature is detached: the exact bytes of
``releases/<sha256>.json`` in the prepared clone, signed by the bot key the trust
root lists, in ``lovspor-forarbeider-release``, and kept beside the preparation
journal (``.git/lovspor-release-manifest.sig``), never in the published tree.

Before the send, ``ar6_release_manifest_signature`` is a real check that no
simulation may waive: a missing, malformed, foreign, owner-signed, wrongly
namespaced, revoked or expired signature, or one over other bytes, denies with
nothing recorded as ``prepared_to_send`` and nothing sent. Evidence issued on a
signature that is gone by the send is stale. After a publication, recovery
verifies the published manifest bytes against the held signature; a published
commit whose manifest signature does not verify is never ``integrity_verified``,
and it stays a published anomaly, never a never-published transaction.
"""

from datetime import timedelta
from functools import partial
from pathlib import Path

import pytest

from lovspor.forarbeider import publication_attempts, publication_receipts
from lovspor.forarbeider.policy_trust import (
    ANCHOR_NAMESPACE,
    BOT_NAMESPACES,
    PUBLICATION_NAMESPACE,
    RELEASE_NAMESPACE,
    TrustRoot,
)
from lovspor.forarbeider.publication_authorize import AuthorizationInputs
from lovspor.forarbeider.publication_execute import (
    ExecutionRequest,
    Production,
    execute,
    publish_authorized,
)
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash
from tests.unit.forarbeider_authorization_support import Key, sign, signer_line, trust_root
from tests.unit.forarbeider_execution_support import (
    NOW,
    OWNER_KEYS,
    WITNESS,
    Rig,
    make_rig,
    retried_after_the_crashed_claim,
    settings_for,
)
from tests.unit.forarbeider_release_control_support import BOT, OWNER
from tests.unit.test_forarbeider_publication_stale_authorization import (
    Adapters,
    authorized,
    refused,
)

SIGNATURE = ".git/lovspor-release-manifest.sig"
CHECK = "ar6_release_manifest_signature"
T2 = NOW + timedelta(seconds=30)


@pytest.fixture(params=["A", "B"])
def rig(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / request.param.lower(), request.param)


# --- helpers --------------------------------------------------------------------------------------


def manifest_of(rig: Rig) -> bytes:
    return (rig.clone / rig.transaction.content.release_manifest_path).read_bytes()


def hold(rig: Rig, signature: bytes) -> None:
    """Put ``signature`` where the runner keeps the manifest's detached signature."""
    path = rig.clone / SIGNATURE
    path.unlink(missing_ok=True)
    path.write_bytes(signature)
    path.chmod(0o600)


def signed(rig: Rig, key: Key | None = None, namespace: str = RELEASE_NAMESPACE) -> bytes:
    return sign(key or rig.bot, manifest_of(rig), namespace)


def genuine(rig: Rig) -> None:
    hold(rig, signed(rig))


def check_of(evidence: object, name: str = CHECK) -> tuple[str, tuple[str, ...]]:
    (found,) = [c for c in evidence.checks if c.name == name]  # type: ignore[attr-defined]
    return found.status, tuple(found.reasons)


def states(rig: Rig) -> list[str]:
    return [
        r.state for r in publication_attempts.records(rig.workspace, rig.transaction.transaction_id)
    ]


def nothing_sent(rig: Rig, adapters: Adapters) -> None:
    assert not adapters.sent()
    assert "prepared_to_send" not in states(rig)
    assert "published" not in states(rig)


def root_with(
    rig: Rig, bot_window: tuple[str, str] | None = None, revoked: bool = False
) -> TrustRoot:
    lines = [
        signer_line(OWNER, rig.owner, OWNER_KEYS),
        signer_line(BOT, rig.bot, ",".join(sorted(BOT_NAMESPACES)), bot_window),
        signer_line(WITNESS, rig.witness_key, ANCHOR_NAMESPACE),
    ]
    return trust_root(rig.tmp / "other-root", lines, (rig.bot,) if revoked else ())


# --- a genuine signature ------------------------------------------------------------------------


def test_a_genuine_bot_signature_is_verified_and_publishes(rig: Rig) -> None:
    genuine(rig)
    adapters = Adapters(rig)

    outcome = execute(rig.request(), adapters.at(NOW))

    assert outcome.status == "published"
    assert outcome.evidence is not None
    assert check_of(outcome.evidence) == ("passed", ())
    assert CHECK not in outcome.evidence.waived


def test_a_restart_rebuilds_every_input_and_still_verifies(rig: Rig) -> None:
    """A new process: the inputs, root and request rebuilt from what is on disk, nothing reused."""
    genuine(rig)
    inputs = AuthorizationInputs(
        transaction=type(rig.transaction).model_validate_json(rig.transaction.model_dump_json()),
        plan=rig.inputs.plan,
        quarantine=rig.inputs.quarantine,
        policy=rig.inputs.policy,
        limits=rig.inputs.limits,
        control=rig.inputs.control,
        ledger=rig.inputs.ledger,
        settings=settings_for(rig.path),
    )
    from lovspor.forarbeider.policy_trust import load_trust_root  # noqa: PLC0415

    root = load_trust_root(rig.tmp / "root")
    request = ExecutionRequest(
        inputs=inputs,
        root=root,
        clone=Path(str(rig.clone)),
        workspace=Path(str(rig.workspace)),
        anchoring=rig.anchoring(root=root),
        ledger=rig.ledger,
        quarantine=rig.quarantine,
    )

    outcome = execute(request, Adapters(rig).at(NOW))

    assert outcome.status == "published"
    assert outcome.evidence is not None
    assert check_of(outcome.evidence) == ("passed", ())


# --- refused before anything is recorded or sent -------------------------------------------------


def test_a_missing_signature_denies_before_anything_is_sent(rig: Rig) -> None:
    (rig.clone / SIGNATURE).unlink(missing_ok=True)
    adapters = Adapters(rig)

    outcome = execute(rig.request(), adapters.at(NOW))

    assert outcome.status == "denied"
    assert check_of(outcome.evidence) == ("failed", ("manifest_signature_missing",))
    nothing_sent(rig, adapters)


FORGED = {
    "modified_manifest": "manifest_signature:signature_invalid",
    "unlisted_key": "manifest_signature:signer_not_in_trust_root",
    "owner_key": "manifest_signature:signer_role_not_permitted",
    "publication_namespace": "manifest_signature:signature_namespace_mismatch",
    "malformed": "manifest_signature:signature_malformed",
    "commit_payload": "manifest_signature:signature_invalid",
}


def forged(rig: Rig, case: str) -> bytes:
    manifest = manifest_of(rig)
    if case == "modified_manifest":
        changed = manifest.replace(
            b"forarbeider_publication_release", b"forarbeider_publication_relEase"
        )
        assert changed != manifest
        return sign(rig.bot, changed, RELEASE_NAMESPACE)
    if case == "unlisted_key":
        return signed(rig, rig.stranger)
    if case == "owner_key":
        return signed(rig, rig.owner)
    if case == "publication_namespace":
        return signed(rig, namespace=PUBLICATION_NAMESPACE)
    if case == "malformed":
        return b"-----BEGIN SSH SIGNATURE-----\nnot a signature\n-----END SSH SIGNATURE-----\n"
    return sign(rig.bot, b"tree " + b"0" * 40 + b"\n", RELEASE_NAMESPACE)


@pytest.mark.parametrize("case", sorted(FORGED))
def test_a_signature_that_is_not_the_bots_over_these_bytes_denies(rig: Rig, case: str) -> None:
    hold(rig, forged(rig, case))
    adapters = Adapters(rig)

    outcome = execute(rig.request(), adapters.at(NOW))

    assert outcome.status == "denied"
    assert check_of(outcome.evidence) == ("failed", (FORGED[case],))
    nothing_sent(rig, adapters)


def test_a_revoked_bot_key_denies(rig: Rig) -> None:
    genuine(rig)
    root = root_with(rig, revoked=True)
    adapters = Adapters(rig)

    outcome = execute(rig.request(root=root, anchoring=rig.anchoring(root=root)), adapters.at(NOW))

    assert outcome.status == "denied"
    assert check_of(outcome.evidence) == ("failed", ("manifest_signature:signer_key_revoked",))
    nothing_sent(rig, adapters)


def test_an_expired_bot_key_denies(rig: Rig) -> None:
    genuine(rig)
    root = root_with(rig, bot_window=("20260101000000Z", "20261101000000Z"))
    adapters = Adapters(rig)

    outcome = execute(rig.request(root=root, anchoring=rig.anchoring(root=root)), adapters.at(NOW))

    assert outcome.status == "denied"
    assert check_of(outcome.evidence) == (
        "failed",
        ("manifest_signature:signer_key_outside_validity",),
    )
    nothing_sent(rig, adapters)


def test_the_signature_check_is_never_waived_and_production_stays_denied(rig: Rig) -> None:
    genuine(rig)
    from lovspor.forarbeider.publication_execution_models import (  # noqa: PLC0415
        WAIVABLE_IN_SIMULATION,
    )

    assert CHECK not in WAIVABLE_IN_SIMULATION
    outcome = execute(rig.request(), Production())
    assert outcome.status == "denied"
    assert outcome.evidence is not None
    assert check_of(outcome.evidence) == ("passed", ())


# --- stale evidence, crash and retry ------------------------------------------------------------


@pytest.mark.parametrize("change", ["removed", "replaced"])
def test_evidence_issued_on_a_signature_that_changed_is_stale(rig: Rig, change: str) -> None:
    genuine(rig)
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))
    if change == "removed":
        (rig.clone / SIGNATURE).unlink()
    else:
        hold(rig, signed(rig, rig.stranger))

    detail = refused(partial(publish_authorized, evidence, rig.request(), adapters.at(T2)))

    assert "manifest_signature" in detail
    nothing_sent(rig, adapters)


def test_a_denied_attempt_retried_once_signed_publishes_exactly_once(rig: Rig) -> None:
    (rig.clone / SIGNATURE).unlink(missing_ok=True)
    adapters = Adapters(rig)
    assert execute(rig.request(), adapters.at(NOW)).status == "denied"
    genuine(rig)

    assert execute(rig.request(), adapters.at(NOW)).status == "published"
    assert execute(rig.request(), adapters.at(NOW)).status == "unchanged"
    assert states(rig).count("published") == 1


def test_a_crash_after_prepared_to_send_recovers_on_the_held_signature(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    genuine(rig)
    adapters = Adapters(rig)
    original = publication_attempts.append

    def die(workspace: Path, record: object) -> None:
        original(workspace, record)  # type: ignore[arg-type]
        if record.state == "prepared_to_send":  # type: ignore[attr-defined]
            raise SimulatedCrash

    monkeypatch.setattr(publication_attempts, "append", die)
    with pytest.raises(SimulatedCrash):
        execute(rig.request(), adapters.at(NOW))
    monkeypatch.setattr(publication_attempts, "append", original)

    outcome = recover(rig.request(), adapters.at(NOW))

    assert outcome.classification == "not_published"
    assert retried_after_the_crashed_claim(rig.request(), adapters.at(NOW)).status == "published"


# --- the recovery boundary ----------------------------------------------------------------------


def published(rig: Rig) -> Adapters:
    genuine(rig)
    adapters = Adapters(rig)
    assert execute(rig.request(), adapters.at(NOW)).status == "published"
    return adapters


def test_a_published_commit_on_a_genuine_signature_is_integrity_verified(rig: Rig) -> None:
    adapters = published(rig)

    outcome = recover(rig.request(), adapters.at(NOW))

    receipt = outcome.receipt
    assert receipt is not None
    assert receipt.integrity == "integrity_verified"
    assert receipt.waived == ()
    (found,) = [c for c in receipt.integrity_checks if c.name == CHECK]
    assert found.status == "passed"


@pytest.mark.parametrize("change", ["removed", "foreign"])
def test_a_published_commit_whose_manifest_signature_fails_is_never_integrity_valid(
    rig: Rig, change: str
) -> None:
    adapters = published(rig)
    if change == "removed":
        (rig.clone / SIGNATURE).unlink()
    else:
        hold(rig, signed(rig, rig.stranger))

    outcome = recover(rig.request(), adapters.at(NOW))

    receipt = outcome.receipt
    assert receipt is not None
    assert receipt.integrity == "integrity_failed"
    assert receipt.classification == "halted_integrity_failed"
    assert receipt.ours
    assert outcome.classification != "not_published"
    assert publication_attempts.latched(rig.workspace)
    assert states(rig)[-1] == "published"
    assert not any(r.published for r in publication_receipts.receipts(rig.workspace))


def test_a_bot_key_revoked_after_publication_never_verifies_the_manifest(rig: Rig) -> None:
    """Fail-closed: the published anomaly is receipted and halts; it is never never-published."""
    adapters = published(rig)
    root = root_with(rig, revoked=True)

    outcome = recover(rig.request(root=root, anchoring=rig.anchoring(root=root)), adapters.at(NOW))

    receipt = outcome.receipt
    assert receipt is not None
    (found,) = [c for c in receipt.integrity_checks if c.name == CHECK]
    assert (found.status, found.reasons) == ("failed", ("manifest_signature:signer_key_revoked",))
    assert receipt.classification == "halted_integrity_failed"
    assert outcome.classification != "not_published"
    assert publication_attempts.latched(rig.workspace)
