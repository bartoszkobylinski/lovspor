"""AR4b-2: recovery from the remote history and post-publication verification (#738; §9.3a).

Every scenario runs against the AR4b-1 sandbox only: the in-memory
SimulatedGitHub for path A and a DisposableRemote for path B, with throwaway
keys. Recovery reads the remote, never the local journal or an API answer
alone; it never sends, never force-pushes and never clears the stop latch.
"""

import ast
import json
import os
import shutil
import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import (
    publication_attempts,
    publication_path_b,
    publication_receipts,
    publication_remote_recovery,
    publication_stage,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_authorization_models import PolicyApproval
from lovspor.forarbeider.policy_trust import CONTROL_NAMESPACE, RELEASE_NAMESPACE
from lovspor.forarbeider.publication_execute import Simulation, execute
from lovspor.forarbeider.publication_execution_models import AppIdentity
from lovspor.forarbeider.publication_integrity import integrity_checks
from lovspor.forarbeider.publication_path_a import body, headline
from lovspor.forarbeider.publication_recovery_models import integrity_of
from lovspor.forarbeider.publication_remote import RemoteCommit
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash, SimulatedGitHub
from lovspor.forarbeider.release_ledger import entry_bytes
from lovspor.forarbeider.version_models import canonical_text
from tests.unit.forarbeider_authorization_support import (
    revocation,
    signed,
    signer_line,
    trust_root,
)
from tests.unit.forarbeider_execution_support import (
    APP,
    NOW,
    OWNER_KEYS,
    KeySigner,
    Rig,
    identified_workspace,
    make_rig,
    retried_after_the_crashed_claim,
)
from tests.unit.forarbeider_publication_support import commit_files, git
from tests.unit.forarbeider_release_control_support import BOT, OWNER, control, signed_by

OTHER_APP = AppIdentity(app_id=1, slug="someone-else", installation_id=2)
BOT_NAMESPACES = "lovspor-forarbeider-release,lovspor-forarbeider-publication"


@pytest.fixture
def rig_a(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "a", "A")


@pytest.fixture
def rig_b(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "b", "B")


def endpoint(rig: Rig, **changes: Any) -> SimulatedGitHub:
    fields: dict[str, Any] = {"head_oid": rig.base, "identity": APP, "now": NOW}
    return SimulatedGitHub(**(fields | changes))


def sim_a(github: SimulatedGitHub) -> Simulation:
    return Simulation(clock=lambda: NOW, transport=github)


def sim_b(rig: Rig, signer: KeySigner | None = None) -> Simulation:
    chosen = signer or KeySigner(BOT, rig.bot)
    return Simulation(clock=lambda: NOW, remote=rig.remote, signer=chosen)


def states(rig: Rig, workspace: Path | None = None) -> list[str]:
    return [
        item.state
        for item in publication_attempts.records(
            workspace or rig.workspace, rig.transaction.transaction_id
        )
    ]


def receipts(rig: Rig, workspace: Path | None = None) -> list[Any]:
    return publication_receipts.receipts(workspace or rig.workspace)


def proposal(rig: Rig) -> dict[str, bytes]:
    return {
        item.path: publication_stage.read_file(rig.clone, item.path)
        for item in rig.transaction.content.files
    }


def fresh_workspace(rig: Rig, name: str = "elsewhere") -> Path:
    """An empty workspace with its own anchored identity (AR5d-2)."""
    return identified_workspace(rig, name)


def approval_id(rig: Rig) -> str:
    offered = rig.inputs.policy.policy_approval
    assert offered is not None
    return PolicyApproval.model_validate_json(offered.record).approval_id


def with_revocation(rig: Rig, at: timedelta) -> Any:
    revoked = signed(revocation(approval_id(rig), signed_at=NOW + at), rig.owner)
    return rig.inputs._replace(policy=replace(rig.inputs.policy, revocations=(revoked,)))


def with_stop(rig: Rig, at: timedelta) -> Any:
    resume = signed_by(control("resume", 1, None), rig.owner, CONTROL_NAMESPACE)
    stop = signed_by(control("stop", 2, resume, signed_at=NOW + at), rig.owner, CONTROL_NAMESPACE)
    return rig.inputs._replace(control=(resume, stop))


def crash_after_prepared(monkeypatch: pytest.MonkeyPatch) -> None:
    original = publication_attempts.append

    def die(workspace: Path, record: Any) -> None:
        original(workspace, record)
        if record.state == "prepared_to_send":
            raise SimulatedCrash

    monkeypatch.setattr(publication_attempts, "append", die)


# --- path A -----------------------------------------------------------------------------------


def test_a_lost_response_is_recovered_from_the_remote_without_a_resend(rig_a: Rig) -> None:
    github = endpoint(rig_a, fault="lost")
    assert execute(rig_a.request(), sim_a(github)).status == "outcome_unknown"
    github.fault = None

    outcome = recover(rig_a.request(), sim_a(github))

    assert outcome.classification == "published_authorized"
    receipt = outcome.receipt
    assert receipt is not None
    assert receipt.commit == github.head_oid and receipt.parent == rig_a.base
    assert receipt.integrity == "integrity_verified"
    assert receipt.authorization == "authorized"
    assert receipt.recovered_from_remote is True
    assert receipt.release_manifest_sha256 == rig_a.transaction.content.release_manifest_sha256
    assert len(github.calls) == 1
    assert states(rig_a) == ["prepared_to_send", "outcome_unknown", "published"]
    assert publication_attempts.latched(rig_a.workspace)
    assert execute(rig_a.request(), sim_a(github)).status == "unchanged"
    assert len(github.calls) == 1


def test_repeated_recovery_writes_one_receipt_and_the_same_bytes(rig_a: Rig) -> None:
    github = endpoint(rig_a, fault="lost")
    execute(rig_a.request(), sim_a(github))
    github.fault = None

    first = recover(rig_a.request(), sim_a(github))
    folder = rig_a.workspace / "receipts"
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    again = recover(rig_a.request(), sim_a(github))

    assert again.classification == "already_receipted"
    assert again.receipt == first.receipt
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    assert len(receipts(rig_a)) == 1
    assert states(rig_a).count("published") == 1


def test_a_crash_after_the_mutation_is_recovered_once(rig_a: Rig) -> None:
    github = endpoint(rig_a, fault="crash")
    with pytest.raises(SimulatedCrash):
        execute(rig_a.request(), sim_a(github))
    github.fault = None
    assert execute(rig_a.request(), sim_a(github)).status == "recovery_required"

    outcome = recover(rig_a.request(), sim_a(github))

    assert outcome.classification == "published_authorized"
    assert states(rig_a) == ["prepared_to_send", "published"]
    assert execute(rig_a.request(), sim_a(github)).status == "unchanged"
    assert len(github.calls) == 1


def test_a_normal_publication_gets_a_receipt_that_is_not_recovered(rig_a: Rig) -> None:
    github = endpoint(rig_a)
    assert execute(rig_a.request(), sim_a(github)).status == "published"

    outcome = recover(rig_a.request(), sim_a(github))

    assert outcome.classification == "published_authorized"
    assert outcome.receipt is not None and outcome.receipt.recovered_from_remote is False
    assert not publication_attempts.latched(rig_a.workspace)


def test_an_expected_head_mismatch_then_foreign_history_halts(rig_a: Rig) -> None:
    github = endpoint(rig_a)
    github.advance({"README.md": b"someone else\n"}, ("manual", ""), (None, False))
    github.reported_head = rig_a.base
    assert execute(rig_a.request(), sim_a(github)).status == "refused"
    github.reported_head = None

    outcome = recover(rig_a.request(), sim_a(github))

    assert outcome.classification == "halted_foreign_commit"
    assert outcome.receipt is not None and outcome.receipt.integrity == "integrity_failed"
    assert publication_attempts.latched(rig_a.workspace)
    assert "published" not in states(rig_a)
    assert len(github.calls) == 1


def test_a_remote_advanced_by_another_app_commit_halts(rig_a: Rig) -> None:
    github = endpoint(rig_a)
    github.advance({"INDEX.md": b"other\n"}, ("forarbeider release x", "other"), (APP, True))

    outcome = recover(rig_a.request(), sim_a(github))

    assert outcome.classification == "halted_foreign_commit"
    assert publication_attempts.latched(rig_a.workspace)
    assert github.calls == []


@pytest.mark.parametrize(("app", "verified"), [(OTHER_APP, True), (APP, False), (None, False)])
def test_our_files_under_the_wrong_identity_or_signature_fail_integrity(
    rig_a: Rig, app: AppIdentity | None, verified: bool
) -> None:
    github = endpoint(rig_a)
    transaction = rig_a.transaction
    files = proposal(rig_a)
    github.advance(files, (headline(transaction), body(transaction)), (app, verified))

    outcome = recover(rig_a.request(), sim_a(github))

    assert outcome.classification == "halted_integrity_failed"
    receipt = outcome.receipt
    assert receipt is not None and receipt.integrity == "integrity_failed"
    assert "signature_class" in {c.name for c in receipt.integrity_checks if c.status == "failed"}
    assert publication_attempts.latched(rig_a.workspace)


@pytest.mark.parametrize("change", ["manifest_bytes", "manifest_missing", "extra_file"])
def test_an_invalid_manifest_or_file_set_fails_integrity(rig_a: Rig, change: str) -> None:
    github = endpoint(rig_a)
    files = proposal(rig_a)
    manifest = rig_a.transaction.content.release_manifest_path
    if change == "manifest_bytes":
        files[manifest] = files[manifest].replace(b"{", b"{ ", 1)
    elif change == "manifest_missing":
        del files[manifest]
    else:
        files["README.md"] = b"unrelated\n"
    transaction = rig_a.transaction
    github.advance(files, (headline(transaction), body(transaction)), (APP, True))

    outcome = recover(rig_a.request(), sim_a(github))

    assert outcome.classification == "halted_integrity_failed"
    assert publication_attempts.latched(rig_a.workspace)


def test_a_valid_commit_without_an_attempt_record_has_unknown_authorization(rig_a: Rig) -> None:
    github = endpoint(rig_a)
    assert execute(rig_a.request(), sim_a(github)).status == "published"
    elsewhere = fresh_workspace(rig_a)

    outcome = recover(rig_a.request(workspace=elsewhere), sim_a(github))

    assert outcome.classification == "published_authorization_unknown"
    receipt = outcome.receipt
    assert receipt is not None
    assert (receipt.integrity, receipt.authorization) == (
        "integrity_verified",
        "authorization_unknown",
    )
    assert "attempt_record_missing" in receipt.authorization_reasons
    assert publication_attempts.latched(elsewhere)
    assert states(rig_a, elsewhere) == ["published"]
    assert execute(rig_a.request(workspace=elsewhere), sim_a(github)).status == "unchanged"
    assert len(github.calls) == 1


def test_a_missing_remote_event_leaves_authorization_unknown(rig_a: Rig) -> None:
    github = endpoint(rig_a, now=None)
    execute(rig_a.request(), sim_a(github))

    outcome = recover(rig_a.request(), sim_a(github))

    assert outcome.classification == "published_authorization_unknown"
    assert outcome.receipt is not None
    assert "remote_event_missing" in outcome.receipt.authorization_reasons
    assert publication_attempts.latched(rig_a.workspace)


def test_a_key_revoked_after_publication_leaves_authorization_unknown(rig_a: Rig) -> None:
    github = endpoint(rig_a)
    execute(rig_a.request(), sim_a(github))
    lines = [
        signer_line(OWNER, rig_a.owner, OWNER_KEYS),
        signer_line(BOT, rig_a.bot, BOT_NAMESPACES),
    ]
    revoked = trust_root(rig_a.tmp / "revoked", lines, (rig_a.owner,))

    outcome = recover(rig_a.request(root=revoked), sim_a(github))

    receipt = outcome.receipt
    assert receipt is not None
    assert (receipt.integrity, receipt.authorization) == (
        "integrity_verified",
        "authorization_unknown",
    )
    assert "untimed_key_revocation" in receipt.authorization_reasons
    assert publication_attempts.latched(rig_a.workspace)
    assert len(github.calls) == 1


def test_an_approval_revoked_after_publication_still_classifies_authorized(rig_a: Rig) -> None:
    github = endpoint(rig_a)
    execute(rig_a.request(), sim_a(github))
    later = with_revocation(rig_a, timedelta(hours=1))

    outcome = recover(rig_a.request(inputs=later), sim_a(github))

    assert outcome.classification == "published_authorized"
    assert len(github.calls) == 1


def test_an_approval_revoked_before_the_attempt_is_unauthorized(rig_a: Rig) -> None:
    github = endpoint(rig_a)
    execute(rig_a.request(), sim_a(github))
    earlier = with_revocation(rig_a, -timedelta(minutes=30))

    outcome = recover(rig_a.request(inputs=earlier), sim_a(github))

    assert outcome.classification == "published_unauthorized"
    assert outcome.receipt is not None and outcome.receipt.integrity == "integrity_verified"
    assert outcome.receipt.authorization_reasons == ("policy_not_valid_at_attempt",)
    assert publication_attempts.latched(rig_a.workspace)
    assert execute(rig_a.request(), sim_a(github)).status == "unchanged"
    assert len(github.calls) == 1


def test_a_stop_within_the_clock_uncertainty_leaves_authorization_unknown(rig_a: Rig) -> None:
    github = endpoint(rig_a)
    execute(rig_a.request(), sim_a(github))

    outcome = recover(rig_a.request(inputs=with_stop(rig_a, timedelta(minutes=1))), sim_a(github))

    assert outcome.classification == "published_authorization_unknown"
    assert outcome.receipt is not None
    assert "within_clock_uncertainty" in outcome.receipt.authorization_reasons


def test_recovery_classifies_while_stopped_and_never_sends(rig_a: Rig) -> None:
    github = endpoint(rig_a, fault="lost")
    execute(rig_a.request(), sim_a(github))
    github.fault = None
    stopped = with_stop(rig_a, timedelta(hours=1))

    outcome = recover(rig_a.request(inputs=stopped), sim_a(github))

    assert outcome.classification == "published_authorized"
    assert len(github.calls) == 1


def test_recovery_with_nothing_on_the_remote_lets_a_clean_retry_run(
    rig_a: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig_a)
    crash_after_prepared(monkeypatch)
    with pytest.raises(SimulatedCrash):
        execute(rig_a.request(), sim_a(github))
    monkeypatch.undo()
    assert github.calls == []

    outcome = recover(rig_a.request(), sim_a(github))

    assert outcome.classification == "not_published"
    assert outcome.receipt is None
    assert states(rig_a) == ["prepared_to_send", "refused"]
    assert not publication_attempts.latched(rig_a.workspace)
    assert retried_after_the_crashed_claim(rig_a.request(), sim_a(github)).status == "published"
    assert len(github.calls) == 1


def test_recovery_refuses_a_foreign_transport(rig_a: Rig) -> None:
    class Foreign:
        identity = APP

        def head(self, *_: Any) -> str:
            raise AssertionError("read")

    with pytest.raises(ForarbeiderRefusedError) as raised:
        recover(rig_a.request(), Simulation(clock=lambda: NOW, transport=Foreign()))  # type: ignore[arg-type]
    assert raised.value.detail == "simulation_transport_not_sandboxed"


# --- integrity, check by check -----------------------------------------------------------------


def _remote_commit(rig: Rig, **changes: Any) -> RemoteCommit:
    transaction = rig.transaction
    fields: dict[str, Any] = {
        "oid": "1" * 40,
        "parents": (rig.base,),
        "headline": headline(transaction),
        "body": body(transaction),
        "files": dict(proposal(rig)),
        "signature_reasons": (),
        "event_at": NOW,
    }
    return RemoteCommit(**(fields | changes))


def _failed(rig: Rig, commit: RemoteCommit) -> dict[str, tuple[str, ...]]:
    checks = integrity_checks(commit, rig.inputs)
    return {c.name: c.reasons for c in checks if c.status == "failed"}


def test_the_exact_proposal_passes_every_integrity_check(rig_a: Rig) -> None:
    checks = integrity_checks(_remote_commit(rig_a), rig_a.inputs)

    assert integrity_of(checks) == ("integrity_verified", ("ar6_release_manifest_signature",))


def test_integrity_fails_each_check_for_its_own_fault(rig_a: Rig) -> None:
    files = proposal(rig_a)
    manifest = rig_a.transaction.content.release_manifest_path
    document = next(p for p in files if p.startswith("documents/"))

    assert _failed(rig_a, _remote_commit(rig_a, parents=("7" * 40,))) == {
        "ancestry": ("parent_not_the_base",)
    }
    assert _failed(rig_a, _remote_commit(rig_a, files=files | {document: b"other\n"})) == {
        "files": ("file_bytes_not_the_proposal",)
    }
    extra = _failed(rig_a, _remote_commit(rig_a, files=files | {"README.md": b"x\n"}))
    assert extra["files"] == ("files_not_the_proposal",)
    assert extra["manifest"] == ("changed_files_not_listed_in_manifest",)
    edited = files | {manifest: files[manifest].replace(b"{", b"{ ", 1)}
    assert _failed(rig_a, _remote_commit(rig_a, files=edited))["manifest"] == (
        "manifest_digest_mismatch",
    )


# --- path B -----------------------------------------------------------------------------------


def test_b_a_crash_before_the_local_commit_leaves_nothing_to_recover(
    rig_b: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def die(*_: Any) -> Any:
        raise SimulatedCrash

    monkeypatch.setattr(publication_path_b, "build_commit", die)
    with pytest.raises(SimulatedCrash):
        execute(rig_b.request(), sim_b(rig_b))
    monkeypatch.undo()

    outcome = recover(rig_b.request(), sim_b(rig_b))

    assert outcome.classification == "not_published"
    assert states(rig_b) == [] and receipts(rig_b) == []


def test_b_a_local_commit_never_pushed_is_not_published(
    rig_b: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def die(*_: Any) -> Any:
        raise SimulatedCrash

    monkeypatch.setattr(publication_path_b, "push", die)
    with pytest.raises(SimulatedCrash):
        execute(rig_b.request(), sim_b(rig_b))
    monkeypatch.undo()

    outcome = recover(rig_b.request(), sim_b(rig_b))

    assert outcome.classification == "not_published"
    assert states(rig_b) == ["prepared_to_send", "refused"]
    assert git(rig_b.bare, "rev-parse", "main") == rig_b.base
    assert retried_after_the_crashed_claim(rig_b.request(), sim_b(rig_b)).status == "published"


@pytest.mark.parametrize("after_push", ["lost", "crash"])
def test_b_a_push_that_landed_is_recovered_from_the_remote(
    rig_b: Rig, monkeypatch: pytest.MonkeyPatch, after_push: str
) -> None:
    real = publication_path_b.push

    def landed(*args: Any) -> Any:
        sent = real(*args)
        if after_push == "crash":
            raise SimulatedCrash
        return publication_path_b.Sent("outcome_unknown", sent.commit, ("response_lost",))

    monkeypatch.setattr(publication_path_b, "push", landed)
    if after_push == "crash":
        with pytest.raises(SimulatedCrash):
            execute(rig_b.request(), sim_b(rig_b))
    else:
        assert execute(rig_b.request(), sim_b(rig_b)).status == "outcome_unknown"
    monkeypatch.undo()

    outcome = recover(rig_b.request(), sim_b(rig_b))

    assert outcome.classification == "published_authorized"
    receipt = outcome.receipt
    assert receipt is not None
    assert receipt.commit == git(rig_b.bare, "rev-parse", "main")
    assert receipt.parent == rig_b.base
    assert states(rig_b)[-1] == "published"
    assert execute(rig_b.request(), sim_b(rig_b)).status == "unchanged"
    assert git(rig_b.bare, "rev-list", "--count", "main") == "2"


def test_b_recovery_reads_the_remote_not_the_local_clone(rig_b: Rig) -> None:
    assert execute(rig_b.request(), sim_b(rig_b)).status == "published"
    for item in rig_b.transaction.content.files:
        target = rig_b.clone / item.path
        target.chmod(0o600)
        target.write_bytes(b"changed locally\n")

    outcome = recover(rig_b.request(), sim_b(rig_b))

    assert outcome.classification == "published_authorized"


def _foreign_push(rig: Rig, files: dict[str, bytes], message: str = "foreign") -> str:
    other = rig.tmp / f"other-{message}"
    git(rig.tmp, "clone", "--quiet", str(rig.bare), str(other))
    commit = commit_files(other, files, message)
    git(other, "push", "--quiet", "origin", "HEAD:main")
    return commit


def test_b_a_remote_moved_before_the_push_halts_on_the_foreign_commit(rig_b: Rig) -> None:
    _foreign_push(rig_b, {"README.md": b"someone else\n"})

    outcome = recover(rig_b.request(), sim_b(rig_b))

    assert outcome.classification == "halted_foreign_commit"
    assert publication_attempts.latched(rig_b.workspace)


def _git_env(rig: Rig) -> dict[str, str]:
    return {**os.environ, "GIT_INDEX_FILE": str(rig.clone / ".git" / publication_path_b.INDEX)}


def _push_signed(rig: Rig, signer: KeySigner, extra: dict[str, bytes] | None = None) -> str:
    """Our transaction's commit, signed by ``signer``, optionally with extra files, pushed."""
    commit = publication_path_b.build_commit(rig.clone, rig.transaction, signer, NOW)
    if extra:
        for path, data in extra.items():
            (rig.clone / path).write_bytes(data)
        cwd = ["git", "-C", str(rig.clone)]
        subprocess.run([*cwd, "add", "--", *extra], check=True, env=_git_env(rig))
        tree = subprocess.run(
            [*cwd, "write-tree"], check=True, capture_output=True, text=True, env=_git_env(rig)
        ).stdout.strip()
        payload = publication_path_b.commit_payload(tree, rig.transaction, signer, NOW)
        raw = publication_path_b.signed_object(payload, signer.sign(payload, RELEASE_NAMESPACE))
        done = subprocess.run(
            [*cwd, "hash-object", "-t", "commit", "-w", "--stdin"],
            input=raw,
            check=True,
            capture_output=True,
        )
        commit = done.stdout.decode().strip()
    git(rig.clone, "push", "--quiet", str(rig.bare), f"{commit}:refs/heads/main")
    return commit


@pytest.mark.parametrize("kind", ["stranger_key", "owner_key", "extra_file"])
def test_b_a_wrong_signature_or_file_set_fails_integrity(rig_b: Rig, kind: str) -> None:
    if kind == "stranger_key":
        _push_signed(rig_b, KeySigner(BOT, rig_b.stranger))
    elif kind == "owner_key":
        _push_signed(rig_b, KeySigner(BOT, rig_b.owner))
    else:
        _push_signed(rig_b, KeySigner(BOT, rig_b.bot), {"README.md": b"unrelated\n"})

    outcome = recover(rig_b.request(), sim_b(rig_b))

    assert outcome.classification == "halted_integrity_failed"
    assert publication_attempts.latched(rig_b.workspace)


def test_b_a_commit_after_ours_halts_after_our_receipt(rig_b: Rig) -> None:
    assert execute(rig_b.request(), sim_b(rig_b)).status == "published"
    _foreign_push(rig_b, {"README.md": b"after us\n"})

    outcome = recover(rig_b.request(), sim_b(rig_b))

    assert outcome.classification == "halted_foreign_commit"
    assert [r.classification for r in receipts(rig_b)] == [
        "published_authorized",
        "halted_foreign_commit",
    ]
    assert publication_attempts.latched(rig_b.workspace)
    assert execute(rig_b.request(), sim_b(rig_b)).status == "unchanged"


def test_b_a_rewritten_history_is_unverifiable_and_halts(rig_b: Rig) -> None:
    lonely = rig_b.tmp / "lonely"
    subprocess.run(["git", "init", "--quiet", str(lonely)], check=True)
    root = commit_files(lonely, {"README.md": b"rewritten\n"}, "root")
    git(lonely, "push", "--quiet", str(rig_b.bare), f"{root}:refs/heads/rewritten")
    git(rig_b.bare, "update-ref", "refs/heads/main", root)

    outcome = recover(rig_b.request(), sim_b(rig_b))

    assert outcome.classification == "halted_history_unverifiable"
    assert outcome.receipt is None
    assert publication_attempts.latched(rig_b.workspace)


def test_b_recovery_counts_the_budget_once(rig_b: Rig) -> None:
    assert execute(rig_b.request(), sim_b(rig_b)).status == "published"

    first = recover(rig_b.request(), sim_b(rig_b))
    assert first.ledger is not None and first.ledger.outcome == "append"
    assert first.ledger.entry is not None
    settled = rig_b.inputs._replace(ledger=(*rig_b.inputs.ledger, entry_bytes(first.ledger.entry)))
    again = recover(rig_b.request(inputs=settled), sim_b(rig_b))

    assert again.classification == "already_receipted"
    assert again.ledger is not None and again.ledger.outcome == "already_applied"


def test_a_receipted_publication_is_never_resent_even_without_its_attempt_records(
    rig_a: Rig,
) -> None:
    github = endpoint(rig_a, fault="lost")
    execute(rig_a.request(), sim_a(github))
    github.fault = None
    recover(rig_a.request(), sim_a(github))
    shutil.rmtree(rig_a.workspace / rig_a.transaction.transaction_id)
    shutil.rmtree(rig_a.workspace / "stop-latch")

    assert execute(rig_a.request(), sim_a(github)).status == "unchanged"
    assert len(github.calls) == 1


# --- receipts ---------------------------------------------------------------------------------


def _published_receipt(rig: Rig) -> tuple[Path, SimulatedGitHub]:
    github = endpoint(rig)
    execute(rig.request(), sim_a(github))
    recover(rig.request(), sim_a(github))
    return rig.workspace / "receipts" / "000001.json", github


def test_an_edited_receipt_is_refused(rig_a: Rig) -> None:
    path, _ = _published_receipt(rig_a)
    raw = json.loads(path.read_text())
    raw["classification"] = "published_unauthorized"
    path.chmod(0o600)
    path.write_text(json.dumps(raw))

    with pytest.raises(ForarbeiderRefusedError):
        publication_receipts.receipts(rig_a.workspace)


def test_a_truncated_receipt_chain_is_refused(rig_b: Rig) -> None:
    assert execute(rig_b.request(), sim_b(rig_b)).status == "published"
    _foreign_push(rig_b, {"README.md": b"after us\n"})
    recover(rig_b.request(), sim_b(rig_b))
    (rig_b.workspace / "receipts" / "000001.json").unlink()

    with pytest.raises(ForarbeiderRefusedError):
        publication_receipts.receipts(rig_b.workspace)


def test_a_canonical_forged_receipt_that_the_remote_contradicts_halts(rig_a: Rig) -> None:
    path, github = _published_receipt(rig_a)
    record = publication_receipts.receipts(rig_a.workspace)[0]
    forged = record.model_copy(
        update={
            "authorization": "unauthorized",
            "authorization_reasons": ("policy_not_valid_at_attempt",),
            "classification": "published_unauthorized",
        }
    )
    path.chmod(0o600)
    path.write_text(canonical_text(forged))

    with pytest.raises(ForarbeiderRefusedError) as raised:
        recover(rig_a.request(), sim_a(github))
    assert raised.value.detail == "receipt_conflict"
    assert publication_attempts.latched(rig_a.workspace)


@pytest.mark.structural
def test_recovery_can_send_nothing() -> None:
    tree = ast.parse(Path(publication_remote_recovery.__file__).read_text(encoding="utf-8"))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}

    assert not names & {"post", "push", "send", "publish_authorized", "execute", "run_git"}
