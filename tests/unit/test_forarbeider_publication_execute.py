"""AR4b-1: fail-closed publication authorization, attempt records and simulated execution (#738).

Paths A and B run only against test adapters: a stub of the GraphQL endpoint
held in memory, and a disposable bare remote in pytest's directory, with
throwaway keys. Production execution is denied by construction. Nothing here
reaches the network, a real repository or a production key.
"""

import ast
import base64
import json
import subprocess
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

import lovspor
from lovspor import git_publish
from lovspor.forarbeider import publication_attempts, publication_path_a, publication_path_b
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_trust import RELEASE_NAMESPACE, SignedBytes, check_signature
from lovspor.forarbeider.publication_authorize import (
    AdapterFacts,
    AuthorizationContext,
    authorize,
)
from lovspor.forarbeider.publication_execute import (
    Production,
    Simulation,
    execute,
    next_attempt_id,
    publish_authorized,
)
from lovspor.forarbeider.publication_execution_models import (
    AR5_TO_AR7_CHECKS,
    FRESH_SECONDS,
    WAIVABLE_IN_SIMULATION,
    AttemptRecord,
    AuthorizationCheck,
    decide,
)
from lovspor.forarbeider.publication_prepare import recover_preparation
from lovspor.forarbeider.release_contract_models import BudgetUsage
from lovspor.forarbeider.release_limits import LimitsRequest, verify_limits
from lovspor.forarbeider.version_models import canonical_text
from tests.unit.forarbeider_authorization_support import full_request, signer_line, trust_root
from tests.unit.forarbeider_execution_support import (
    APP,
    NOW,
    OWNER_KEYS,
    Crash,
    KeySigner,
    Rig,
    StubGitHub,
    TransportLostError,
    batch_usage,
    control_history,
    make_rig,
    reserve,
    settings_for,
)
from tests.unit.forarbeider_publication_support import commit_files, git
from tests.unit.forarbeider_release_control_support import BOT, OWNER, policy, policy_bytes


@pytest.fixture
def rig_a(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "a", "A")


@pytest.fixture
def rig_b(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "b", "B")


def stub(rig: Rig) -> StubGitHub:
    return StubGitHub(head_oid=rig.base)


def simulation_a(rig: Rig, github: StubGitHub) -> Simulation:
    return Simulation(clock=lambda: NOW, transport=github)


def simulation_b(rig: Rig, signer: KeySigner | None = None) -> Simulation:
    chosen = signer or KeySigner(BOT, rig.bot)
    return Simulation(clock=lambda: NOW, remote=rig.bare, signer=chosen)


def raw_git(repository: Path, *argv: str) -> bytes:
    done = subprocess.run(
        ["git", "-C", str(repository), *argv],
        capture_output=True,
        check=True,
    )
    return done.stdout


def states(rig: Rig) -> list[str]:
    return [
        item.state
        for item in publication_attempts.records(rig.workspace, rig.transaction.transaction_id)
    ]


def failed(outcome: Any) -> set[str]:
    assert outcome.evidence is not None
    return {check.name for check in outcome.evidence.checks if check.status == "failed"}


def with_inputs(rig: Rig, **changes: Any) -> Any:
    return rig.request(inputs=rig.inputs._replace(**changes))


# --- the authorization contract ----------------------------------------------------------------


def test_a_simulated_authorization_passes_every_check_it_can_make(rig_a: Rig) -> None:
    github = stub(rig_a)
    outcome = execute(rig_a.request(), simulation_a(rig_a, github))

    evidence = outcome.evidence
    assert evidence is not None
    assert evidence.decision == "simulation_only"
    assert evidence.production_authorized is False
    assert {c.name for c in evidence.checks if c.status == "failed"} == set()
    unavailable = {c.name for c in evidence.checks if c.status == "unavailable"}
    assert unavailable <= WAIVABLE_IN_SIMULATION
    assert set(AR5_TO_AR7_CHECKS) <= unavailable
    assert evidence.waived == tuple(sorted(unavailable))
    content = rig_a.transaction.content
    assert (evidence.transaction_id, evidence.plan_id, evidence.batch_id) == (
        rig_a.transaction.transaction_id,
        content.plan_id,
        content.batch_id,
    )
    assert evidence.release_manifest_sha256 == content.release_manifest_sha256
    assert [(f.path, f.sha256) for f in evidence.files] == [
        (f.path, f.sha256) for f in content.files
    ]
    assert (evidence.path, evidence.base_commit, evidence.remote_head) == (
        "A",
        rig_a.base,
        rig_a.base,
    )
    assert evidence.trust_root_sha256 == rig_a.root.sha256
    assert evidence.checked_at == NOW


def test_production_is_denied_whatever_else_passes(rig_a: Rig) -> None:
    outcome = execute(rig_a.request(), Production())

    assert outcome.status == "denied"
    evidence = outcome.evidence
    assert evidence is not None and evidence.decision == "denied"
    assert set(AR5_TO_AR7_CHECKS) <= set(evidence.denied_by)
    assert states(rig_a) == ["denied"]


def _authorized(rig: Rig, github: StubGitHub, at: datetime, attempt: str | None = None) -> Any:
    """Evidence for the very next attempt, as the executor would make it at ``at``."""
    facts = AdapterFacts(app=github.identity)
    chosen = attempt or next_attempt_id(rig.request())
    context = AuthorizationContext(
        rig.inputs, rig.root, rig.clone, github_head(github), at, chosen, "simulation", facts
    )
    evidence = authorize(context)
    assert evidence.decision == "simulation_only", evidence.denied_by
    return evidence


def test_genuine_evidence_for_the_next_attempt_publishes(rig_a: Rig) -> None:
    github = stub(rig_a)
    outcome = publish_authorized(
        _authorized(rig_a, github, NOW), rig_a.request(), simulation_a(rig_a, github)
    )

    assert outcome.status == "published"


def test_a_forged_or_edited_authorization_is_never_accepted(rig_a: Rig) -> None:
    github = stub(rig_a)
    evidence = _authorized(rig_a, github, NOW)
    unsealed = evidence.model_copy(update={"provenance": None})
    edited = evidence.model_copy(update={"remote_head": "7" * 40})

    for item in (unsealed, edited):
        with pytest.raises(ForarbeiderRefusedError) as raised:
            publish_authorized(item, rig_a.request(), simulation_a(rig_a, github))
        assert raised.value.detail == "authorization_not_authentic"
    assert github.calls == []
    assert states(rig_a) == []


def github_head(github: StubGitHub) -> Any:
    return lambda: github.head(github.repository, "main")


def test_an_authorization_for_another_attempt_is_refused(rig_a: Rig) -> None:
    github = stub(rig_a)
    other = _authorized(rig_a, github, NOW, attempt="b" * 64)

    with pytest.raises(ForarbeiderRefusedError) as raised:
        publish_authorized(other, rig_a.request(), simulation_a(rig_a, github))
    assert raised.value.detail == "authorization_not_this_attempt"
    assert github.calls == []


@pytest.mark.parametrize("age", [timedelta(seconds=FRESH_SECONDS + 1), timedelta(seconds=-1)])
def test_an_authorization_that_is_not_fresh_is_refused(rig_a: Rig, age: timedelta) -> None:
    github = stub(rig_a)
    evidence = _authorized(rig_a, github, NOW - age)

    with pytest.raises(ForarbeiderRefusedError) as raised:
        publish_authorized(evidence, rig_a.request(), simulation_a(rig_a, github))
    assert raised.value.detail == "authorization_not_fresh"
    assert github.calls == []


def test_a_simulation_never_waives_an_unavailable_check_outside_its_list() -> None:
    checks = (
        AuthorizationCheck(name="remote_head", status="unavailable", reasons=("unread",)),
        AuthorizationCheck(name="ar7_repository_settings", status="unavailable", reasons=("x",)),
    )

    assert decide(checks, "simulation") == ("denied", ("remote_head",), ())
    assert decide(checks[1:], "simulation") == ("simulation_only", (), ("ar7_repository_settings",))
    assert decide(checks[1:], "production")[0] == "denied"


def test_an_expired_policy_approval_denies(rig_a: Rig) -> None:
    github = stub(rig_a)
    late = Simulation(clock=lambda: NOW.replace(year=NOW.year + 3), transport=github)
    outcome = execute(rig_a.request(), late)

    assert outcome.status == "denied"
    assert "policy" in failed(outcome)
    assert github.calls == []


def test_a_revoked_owner_key_denies_and_latches(rig_a: Rig) -> None:
    lines = [
        signer_line(OWNER, rig_a.owner, OWNER_KEYS),
        signer_line(BOT, rig_a.bot, "lovspor-forarbeider-release,lovspor-forarbeider-publication"),
    ]
    revoked = trust_root(rig_a.tmp / "revoked", lines, (rig_a.owner,))
    github = stub(rig_a)
    outcome = execute(rig_a.request(root=revoked), simulation_a(rig_a, github))

    assert outcome.status == "denied"
    assert {"policy", "trust_root"} <= failed(outcome)
    assert publication_attempts.latched(rig_a.workspace)
    assert github.calls == []


def test_an_active_stop_denies_and_latches(rig_a: Rig) -> None:
    github = stub(rig_a)
    outcome = execute(
        with_inputs(rig_a, control=control_history(rig_a.owner, stopped=True)),
        simulation_a(rig_a, github),
    )

    assert outcome.status == "denied"
    assert "stop_control" in failed(outcome)
    assert publication_attempts.latched(rig_a.workspace)
    again = execute(rig_a.request(), simulation_a(rig_a, github))
    assert "stop_latch" in failed(again)
    assert github.calls == []


def test_a_missing_budget_reservation_denies(rig_a: Rig) -> None:
    github = stub(rig_a)
    outcome = execute(with_inputs(rig_a, ledger=()), simulation_a(rig_a, github))

    assert "budget" in failed(outcome)
    assert github.calls == []


def test_a_reservation_smaller_than_the_batch_denies(rig_a: Rig) -> None:
    evidence = verify_limits(LimitsRequest(policy_bytes(), rig_a.inputs.limits, NOW), rig_a.root)
    usage = batch_usage(rig_a.transaction)
    small = BudgetUsage(
        documents=usage.documents, files=usage.files, bytes=usage.bytes - 1, commits=1
    )
    ledger = reserve(evidence, rig_a.transaction, small)
    github = stub(rig_a)
    outcome = execute(with_inputs(rig_a, ledger=ledger), simulation_a(rig_a, github))

    assert "budget" in failed(outcome)
    assert github.calls == []


def test_a_remote_head_other_than_the_base_denies(rig_a: Rig) -> None:
    github = stub(rig_a)
    github.head_oid = "9" * 40
    outcome = execute(rig_a.request(), simulation_a(rig_a, github))

    assert "remote_head" in failed(outcome)
    assert github.calls == []


def test_a_configured_path_other_than_the_transactions_denies(rig_a: Rig) -> None:
    github = stub(rig_a)
    outcome = execute(with_inputs(rig_a, settings=settings_for("B")), simulation_a(rig_a, github))

    assert "path_identity" in failed(outcome)
    assert github.calls == []


def test_another_app_identity_denies(rig_a: Rig) -> None:
    github = stub(rig_a)
    github.identity = APP.model_copy(update={"app_id": 1})
    outcome = execute(rig_a.request(), simulation_a(rig_a, github))

    assert "path_identity" in failed(outcome)
    assert github.calls == []


def test_changed_proposed_bytes_deny(rig_a: Rig) -> None:
    (rig_a.clone / "documents" / "regjeringen-9000001.md").write_bytes(b"changed\n")
    github = stub(rig_a)
    outcome = execute(rig_a.request(), simulation_a(rig_a, github))

    assert "proposal" in failed(outcome)
    assert github.calls == []


def test_a_policy_other_than_the_plans_denies(rig_a: Rig) -> None:
    other = policy().model_copy(update={"policy_version": 2})
    github = stub(rig_a)
    outcome = execute(
        with_inputs(rig_a, policy=full_request(other, rig_a.owner)), simulation_a(rig_a, github)
    )

    assert "policy" in failed(outcome)


def test_a_caller_cannot_claim_authorized(rig_a: Rig) -> None:
    with pytest.raises((ValueError, TypeError), match="unexpected field"):
        rig_a.inputs._replace(authorized=True)  # type: ignore[call-arg]


# --- path A: createCommitOnBranch against a stub -----------------------------------------------


def test_path_a_sends_exactly_the_proposal_and_records_before_and_after(rig_a: Rig) -> None:
    github = stub(rig_a)
    outcome = execute(rig_a.request(), simulation_a(rig_a, github))

    assert outcome.status == "published"
    [payload] = github.calls
    given = payload["variables"]["input"]
    assert given["branch"] == {
        "repositoryNameWithOwner": "bartoszkobylinski/lovverk-forarbeider",
        "branchName": "main",
    }
    assert given["expectedHeadOid"] == rig_a.base
    manifest = rig_a.transaction.content.release_manifest_sha256
    assert given["message"]["headline"] == f"forarbeider release {manifest}"
    sent = {
        item["path"]: base64.b64decode(item["contents"])
        for item in given["fileChanges"]["additions"]
    }
    for item in rig_a.transaction.content.files:
        assert sha256(sent[item.path]).hexdigest() == item.sha256
    assert set(sent) == {item.path for item in rig_a.transaction.content.files}
    assert given["fileChanges"].get("deletions", []) == []
    assert "createCommitOnBranch" in payload["query"]
    assert outcome.commit == github.head_oid
    records = publication_attempts.records(rig_a.workspace, rig_a.transaction.transaction_id)
    assert [r.state for r in records] == ["prepared_to_send", "published"]
    assert records[0].authorization_sha256 == outcome.evidence_sha256
    assert records[0].base_commit == rig_a.base and records[0].path == "A"
    assert records[1].commit == github.head_oid


def test_a_duplicate_retry_never_publishes_twice(rig_a: Rig) -> None:
    github = stub(rig_a)
    first = execute(rig_a.request(), simulation_a(rig_a, github))
    again = execute(rig_a.request(), simulation_a(rig_a, github))

    assert (first.status, again.status) == ("published", "unchanged")
    assert again.commit == first.commit
    assert len(github.calls) == 1
    assert states(rig_a) == ["prepared_to_send", "published"]


def test_prepared_record_exists_before_graphql_post(
    rig_a: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = stub(rig_a)
    original = github.post

    def post(payload: dict[str, Any]) -> Any:
        assert states(rig_a) == ["prepared_to_send"]
        return original(payload)

    monkeypatch.setattr(github, "post", post)
    assert execute(rig_a.request(), simulation_a(rig_a, github)).status == "published"
    assert states(rig_a) == ["prepared_to_send", "published"]


@pytest.mark.parametrize(
    "answer,head",
    [
        (
            {
                "errors": [
                    {"type": "STALE_DATA", "message": "Expected branch to point to base"},
                    {"type": "INTERNAL", "message": "unknown error"},
                ]
            },
            None,
        ),
        ({"data": {"createCommitOnBranch": {"commit": {"oid": "a" * 40}}}}, "b" * 40),
        ({"data": {"createCommitOnBranch": {"commit": {"oid": "a" * 40}}}}, None),
    ],
)
def test_ambiguous_graphql_answer_or_head_is_unknown(answer: object, head: str | None) -> None:
    assert publication_path_a.judged(answer, head).status == "outcome_unknown"


def test_a_head_mismatch_at_send_publishes_nothing(rig_a: Rig) -> None:
    github = stub(rig_a)
    github.reported_head = rig_a.base
    github.head_oid = "8" * 40
    outcome = execute(rig_a.request(), simulation_a(rig_a, github))

    assert outcome.status == "refused"
    assert github.head_oid == "8" * 40
    assert states(rig_a) == ["prepared_to_send", "refused"]


def test_a_lost_response_requires_recovery_never_a_blind_retry(rig_a: Rig) -> None:
    github = stub(rig_a)

    def lose() -> None:
        raise TransportLostError

    github.after = lose
    outcome = execute(rig_a.request(), simulation_a(rig_a, github))
    github.after = None
    again = execute(rig_a.request(), simulation_a(rig_a, github))

    assert outcome.status == "outcome_unknown"
    assert again.status == "recovery_required"
    assert len(github.calls) == 1
    assert states(rig_a) == ["prepared_to_send", "outcome_unknown"]
    assert publication_attempts.latched(rig_a.workspace)


@pytest.mark.parametrize(
    "response",
    [
        {"data": {"createCommitOnBranch": {"commit": {"oid": "not-an-oid"}}}},
        {"data": {}},
        ["unexpected"],
        {"errors": [{"type": "INTERNAL", "message": "something"}]},
    ],
)
def test_a_malformed_or_ambiguous_response_is_an_unknown_outcome(rig_a: Rig, response: Any) -> None:
    github = stub(rig_a)
    github.response = response
    outcome = execute(rig_a.request(), simulation_a(rig_a, github))

    assert outcome.status == "outcome_unknown"
    assert states(rig_a) == ["prepared_to_send", "outcome_unknown"]


def test_a_crash_after_sending_requires_recovery(rig_a: Rig) -> None:
    github = stub(rig_a)

    def die() -> None:
        raise Crash

    github.after = die
    with pytest.raises(Crash):
        execute(rig_a.request(), simulation_a(rig_a, github))
    github.after = None
    again = execute(rig_a.request(), simulation_a(rig_a, github))

    assert states(rig_a) == ["prepared_to_send"]
    assert again.status == "recovery_required"
    assert len(github.calls) == 1


def test_a_crash_before_the_attempt_is_recorded_sends_nothing_and_retries_once(
    rig_a: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = stub(rig_a)
    original = publication_attempts.append

    def die(*args: Any) -> Any:
        raise Crash

    monkeypatch.setattr(publication_attempts, "append", die)
    with pytest.raises(Crash):
        execute(rig_a.request(), simulation_a(rig_a, github))
    monkeypatch.setattr(publication_attempts, "append", original)
    assert github.calls == [] and states(rig_a) == []

    outcome = execute(rig_a.request(), simulation_a(rig_a, github))
    assert outcome.status == "published"
    assert len(github.calls) == 1


def test_a_forged_attempt_record_is_refused(rig_a: Rig) -> None:
    github = stub(rig_a)
    execute(rig_a.request(), simulation_a(rig_a, github))
    folder = rig_a.workspace / rig_a.transaction.transaction_id
    last = sorted(folder.iterdir())[-1]
    raw = json.loads(last.read_text())
    raw["state"] = "refused"
    last.chmod(0o600)
    last.write_text(json.dumps(raw))

    with pytest.raises(ForarbeiderRefusedError):
        execute(rig_a.request(), simulation_a(rig_a, github))
    assert len(github.calls) == 1


def test_a_canonically_rewritten_earlier_attempt_breaks_the_chain(rig_a: Rig) -> None:
    github = stub(rig_a)
    execute(rig_a.request(), simulation_a(rig_a, github))
    folder = rig_a.workspace / rig_a.transaction.transaction_id
    first = sorted(folder.iterdir())[0]
    record = AttemptRecord.model_validate_json(first.read_bytes())
    first.chmod(0o600)
    first.write_text(canonical_text(record.model_copy(update={"state": "refused"})))

    with pytest.raises(ForarbeiderRefusedError):
        execute(rig_a.request(), simulation_a(rig_a, github))
    assert len(github.calls) == 1


def test_a_missing_earlier_attempt_refuses_retry(rig_a: Rig) -> None:
    github = stub(rig_a)
    assert execute(rig_a.request(), simulation_a(rig_a, github)).status == "published"
    folder = rig_a.workspace / rig_a.transaction.transaction_id
    (folder / "000001.json").unlink()

    with pytest.raises(ForarbeiderRefusedError, match="attempts"):
        execute(rig_a.request(), simulation_a(rig_a, github))
    assert len(github.calls) == 1


# --- path B: a bot-signed commit and a fast-forward push to a bare remote ----------------------


def test_path_b_pushes_a_bot_signed_commit_of_exactly_the_proposal(rig_b: Rig) -> None:
    outcome = execute(rig_b.request(), simulation_b(rig_b))

    assert outcome.status == "published"
    commit = outcome.commit
    assert commit is not None
    assert git(rig_b.bare, "rev-parse", "main") == commit
    assert git(rig_b.bare, "rev-parse", f"{commit}^") == rig_b.base
    assert git(rig_b.bare, "rev-list", "--count", "main") == "2"
    changed = git(
        rig_b.bare, "diff-tree", "--no-commit-id", "--name-only", "-r", commit
    ).splitlines()
    assert sorted(changed) == sorted(item.path for item in rig_b.transaction.content.files)
    for item in rig_b.transaction.content.files:
        blob = raw_git(rig_b.bare, "cat-file", "blob", f"{commit}:{item.path}")
        assert sha256(blob).hexdigest() == item.sha256
    payload, signature = publication_path_b.split_signature(
        raw_git(rig_b.bare, "cat-file", "commit", commit)
    )
    check = check_signature(rig_b.root, SignedBytes(payload, signature), RELEASE_NAMESPACE, NOW)
    assert check.verified and check.entry is not None and check.entry.principal == BOT
    assert states(rig_b) == ["prepared_to_send", "published"]
    records = publication_attempts.records(rig_b.workspace, rig_b.transaction.transaction_id)
    assert records[0].commit == commit and records[1].commit == commit


def test_path_b_with_a_key_the_root_does_not_list_for_the_bot_pushes_nothing(rig_b: Rig) -> None:
    for signer in (KeySigner(BOT, rig_b.stranger), KeySigner(BOT, rig_b.owner)):
        outcome = execute(rig_b.request(), simulation_b(rig_b, signer))
        assert outcome.status == "denied"
        assert "bot_signature" in failed(outcome)
    assert git(rig_b.bare, "rev-parse", "main") == rig_b.base


def test_path_b_with_an_unrelated_change_pushes_nothing(rig_b: Rig) -> None:
    (rig_b.clone / "README.md").write_bytes(b"unrelated\n")
    outcome = execute(rig_b.request(), simulation_b(rig_b))

    assert outcome.status == "denied"
    assert "proposal" in failed(outcome)
    assert git(rig_b.bare, "rev-parse", "main") == rig_b.base


def test_path_b_a_remote_that_moved_after_authorization_rejects_the_push(
    rig_b: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = rig_b.tmp / "other"
    git(rig_b.tmp, "clone", "--quiet", str(rig_b.bare), str(other))
    moved = commit_files(other, {"README.md": b"someone else\n"}, "foreign")
    real = publication_path_b.remote_head
    monkeypatch.setattr(publication_path_b, "remote_head", lambda _bare: rig_b.base)
    git(other, "push", "--quiet", "origin", "HEAD:main")
    outcome = execute(rig_b.request(), simulation_b(rig_b))
    monkeypatch.setattr(publication_path_b, "remote_head", real)

    assert outcome.status == "refused"
    assert git(rig_b.bare, "rev-parse", "main") == moved
    assert states(rig_b) == ["prepared_to_send", "refused"]


def test_path_b_a_duplicate_retry_never_pushes_again(rig_b: Rig) -> None:
    first = execute(rig_b.request(), simulation_b(rig_b))
    again = execute(rig_b.request(), simulation_b(rig_b))

    assert (first.status, again.status) == ("published", "unchanged")
    assert git(rig_b.bare, "rev-list", "--count", "main") == "2"


def test_path_b_never_touches_the_source_repository(rig_b: Rig) -> None:
    before = git(rig_b.source, "rev-parse", "main")
    execute(rig_b.request(), simulation_b(rig_b))

    assert git(rig_b.source, "rev-parse", "main") == before == rig_b.base


def test_a_clone_without_a_prepared_proposal_is_denied(rig_b: Rig) -> None:
    recover_preparation(rig_b.clone, None)
    outcome = execute(rig_b.request(), simulation_b(rig_b))

    assert outcome.status == "denied"
    assert "proposal" in failed(outcome)


# --- the Git boundary --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("push", "--force", "/var/empty/x.git", "a" * 40 + ":refs/heads/main"),
        ("push", "/var/empty/x.git", "+" + "a" * 40 + ":refs/heads/main"),
        (
            "push",
            "https://github.com/bartoszkobylinski/lovverk-forarbeider",
            "a" * 40 + ":refs/heads/main",
        ),
        (
            "push",
            "git@github.com:bartoszkobylinski/lovverk-forarbeider.git",
            "a" * 40 + ":refs/heads/main",
        ),
        ("push", "/var/empty/x.git", "a" * 40 + ":refs/heads/other"),
        ("push", "--delete", "/var/empty/x.git", "main"),
        ("reset", "--hard"),
        ("add", "--all"),
        ("add", "--", "../outside.md"),
    ],
)
def test_the_publication_git_refuses_force_urls_and_anything_else(
    tmp_path: Path, argv: tuple[str, ...]
) -> None:
    with pytest.raises(git_publish.GitPublishError):
        git_publish.run_git(tmp_path, argv)


def test_the_publication_git_has_no_force_shape() -> None:
    literals = {part for shape in git_publish.COMMANDS for part in shape if isinstance(part, str)}

    assert not literals & {"--force", "-f", "--force-with-lease", "--delete", "--mirror", "--all"}
    assert {shape[0] for shape in git_publish.COMMANDS} <= {
        "read-tree",
        "add",
        "write-tree",
        "hash-object",
        "cat-file",
        "diff-tree",
        "ls-tree",
        "ls-remote",
        "push",
    }


@pytest.mark.structural
def test_only_path_b_imports_the_publication_git() -> None:
    source = Path(lovspor.__file__).parent
    importers = set()
    for path in source.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            names += [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            if any(name.split(".")[:2] == ["lovspor", "git_publish"] for name in names):
                importers.add(path.relative_to(source).as_posix())
    assert importers == {"forarbeider/publication_path_b.py"}
