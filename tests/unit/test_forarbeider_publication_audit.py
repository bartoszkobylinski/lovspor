"""AR6c: the independent, read-only publication history auditor (#738; §9.7 auditor, AR8).

Every history is real: the first release goes through the pipeline, later ones
are genuine bot-signed releases on the path's own remote (the in-memory GitHub
endpoint for A, a disposable bare repository for B), and every signature is
checked with real ``ssh-keygen``. The auditor is given the remote, the trust
root and the expected settings; nothing it approves is mocked.
"""

import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from lovspor.forarbeider.publication_audit import AuditRequest, audit, audit_remote
from lovspor.forarbeider.publication_audit_models import AuditReport
from lovspor.forarbeider.publication_sandbox import SimulatedCrash
from lovspor.forarbeider.version_models import canonical_text
from tests.unit.forarbeider_audit_support import (
    History,
    add_foreign,
    add_release,
    attest_commit,
    attestation_files,
    commit_files,
    expected,
    first_release,
    head,
    manifest_at,
    next_release,
    push_object,
    reader,
    release_files,
    run,
    work_clone,
)
from tests.unit.forarbeider_authorization_support import sign
from tests.unit.forarbeider_execution_support import NOW, Rig, _root, make_rig
from tests.unit.forarbeider_publication_support import git
from tests.unit.test_forarbeider_publication_stale_authorization import Adapters

RELEASE_NS = "lovspor-forarbeider-release"


@pytest.fixture(params=["A", "B"])
def rig(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "rig", request.param)


@pytest.fixture
def rig_b(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "rig", "B")


def releases(rig: Rig, count: int) -> History:
    history = first_release(rig)
    for _ in range(count - 1):
        add_release(rig, history)
    return history


def classes(report: AuditReport) -> list[tuple[str, str]]:
    return [(c.classification, c.attestation) for c in report.commits]


def remote_state(rig: Rig, adapters: Adapters) -> object:
    if rig.path == "A":
        github = adapters.github
        return (
            github.head_oid,
            dict(github.commits),
            dict(github.refs),
            dict(github.objects),
            len(github.calls),
            len(github.ref_calls),
        )
    objects = sorted(p.relative_to(rig.bare) for p in (rig.bare / "objects").rglob("*"))
    return git(rig.bare, "for-each-ref"), objects


# --- a valid history --------------------------------------------------------------------------


def test_a_valid_multi_release_history_is_a_verified_window(rig: Rig) -> None:
    history = releases(rig, 3)

    report = run(rig, history.adapters, rig.base)

    assert (report.verdict, report.anomalies, report.gaps) == ("verified_window", (), ())
    assert [c.commit for c in report.commits] == list(reversed(history.commits))
    assert classes(report) == [("release", "attested")] * 3
    assert (report.counts.examined, report.counts.releases, report.counts.attested) == (3, 3, 3)
    assert report.observed_head == report.final_head == history.commits[-1]
    assert report.boundary.commit == rig.base
    assert (report.boundary.status, report.boundary.trusted) == ("reached", False)
    assert report.boundary.source == "caller_supplied"
    assert report.scope.anchoring == "unanchored"
    assert report.scope.trust_root_sha256 == rig.root.sha256
    assert report.scope.path == rig.path
    assert report.verdict_scope == "this_window_only"
    for limit in (
        "rollback_resistance",
        "ar7_independent_checkpoint",
        "authorization_at_publication",
    ):
        assert limit in report.not_verified
    assert [c.batch_sequence for c in report.commits] == [3, 2, 1]


def test_the_audit_reads_without_the_runner_clone_or_workspace(rig: Rig) -> None:
    history = releases(rig, 2)
    shutil.rmtree(rig.clone)
    shutil.rmtree(rig.workspace)
    if (rig.tmp / "audit-work").exists():
        shutil.rmtree(rig.tmp / "audit-work")

    report = run(rig, history.adapters, rig.base)

    assert report.verdict == "verified_window", (report.anomalies, report.gaps)
    assert report.counts.attested == 2


def test_the_audit_writes_nothing(rig: Rig) -> None:
    history = releases(rig, 2)
    add_release(rig, history, attested=False)
    before = remote_state(rig, history.adapters)
    workspace = sorted(p.relative_to(rig.workspace) for p in rig.workspace.rglob("*"))

    run(rig, history.adapters, rig.base)

    assert remote_state(rig, history.adapters) == before
    assert sorted(p.relative_to(rig.workspace) for p in rig.workspace.rglob("*")) == workspace


def test_the_report_holds_no_content_and_keeps_claims_as_claims(rig: Rig) -> None:
    history = releases(rig, 2)

    report = run(rig, history.adapters, rig.base)
    raw = canonical_text(report).encode()

    assert b"release 2\\n" not in raw and b"INDEX" not in raw
    newest = report.commits[0]
    assert newest.claimed_transaction_id == sha256(b"transaction 2").hexdigest()
    assert "claims" not in report.model_dump()
    assert report.commits[1].claimed_transaction_id == rig.transaction.transaction_id


# --- anomalies --------------------------------------------------------------------------------


def test_a_foreign_commit_between_releases_is_an_anomaly(rig: Rig) -> None:
    history = first_release(rig)
    foreign = add_foreign(rig, history)
    add_release(rig, history)

    report = run(rig, history.adapters, rig.base)

    assert report.verdict == "anomalies_found"
    assert classes(report) == [
        ("release", "attested"),
        ("foreign", "not_applicable"),
        ("release", "attested"),
    ]
    assert report.commits[1].commit == foreign
    assert f"foreign_commit:{foreign}" in report.anomalies
    assert (report.counts.foreign, report.counts.releases) == (1, 2)
    assert report.gaps == ()


def test_a_missing_attestation_is_never_attested(rig: Rig) -> None:
    history = first_release(rig)
    missing = add_release(rig, history, attested=False)

    report = run(rig, history.adapters, rig.base)

    assert report.verdict == "anomalies_found"
    assert classes(report)[0] == ("release", "missing")
    assert f"attestation_missing:{missing}" in report.anomalies
    assert (report.counts.attested, report.counts.missing_attestations) == (1, 1)
    assert report.commits[0].claimed_transaction_id is None


def test_a_replaced_attestation_is_invalid(rig: Rig) -> None:
    history = first_release(rig)
    second = add_release(rig, history, attested=False)
    copied = attestation_files(rig, history.adapters, history.commits[0], 1)
    attest_commit(rig, history.adapters, second, copied)

    report = run(rig, history.adapters, rig.base)

    assert report.verdict == "anomalies_found"
    assert classes(report)[0] == ("release", "invalid")
    assert "attestation_not_this_commit" in report.commits[0].reasons
    assert f"attestation_invalid:{second}" in report.anomalies
    assert report.counts.invalid_attestations == 1


def test_two_attestations_claiming_one_transaction_conflict(rig: Rig) -> None:
    history = releases(rig, 2)
    third = add_release(rig, history, attested=False)
    attest_commit(rig, history.adapters, third, attestation_files(rig, history.adapters, third, 2))

    report = run(rig, history.adapters, rig.base)

    claimed = sha256(b"transaction 2").hexdigest()
    assert classes(report) == [("release", "attested")] * 3
    assert report.verdict == "anomalies_found"
    assert f"conflicting_claims:transaction:{claimed}" in report.anomalies


def test_two_releases_of_one_batch_are_duplicate_facts(rig: Rig) -> None:
    history = releases(rig, 2)
    adapters = history.adapters
    template = manifest_at(rig, adapters, history.commits[0])
    found = next_release(rig, template, head(rig, adapters), 3, batch=2)
    signature = sign(rig.bot, found.manifest, RELEASE_NS)
    third = commit_files(rig, adapters, release_files(found, signature))
    attest_commit(rig, adapters, third, attestation_files(rig, adapters, third, 3))

    report = run(rig, adapters, rig.base)

    batch = report.commits[0].batch_id
    assert classes(report) == [("release", "attested")] * 3
    assert report.verdict == "anomalies_found"
    assert f"duplicate_release_facts:batch_id:{batch}" in report.anomalies
    assert any(a.startswith("duplicate_release_facts:batch_sequence:") for a in report.anomalies)


def bad_release(rig: Rig, history: History, signature: bytes | None, key: object = None) -> str:
    adapters = history.adapters
    template = manifest_at(rig, adapters, history.commits[0])
    found = next_release(rig, template, head(rig, adapters), len(history.commits) + 1)
    made = signature if signature != b"genuine" else sign(rig.bot, found.manifest, RELEASE_NS)
    return commit_files(rig, adapters, release_files(found, made), key)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("signature", "reason"),
    [
        (None, "manifest_signature_not_published"),
        ("other_bytes", "manifest_signature:signature_invalid"),
        ("stranger", "manifest_signature:signer_not_in_trust_root"),
    ],
)
def test_an_invalid_manifest_signature_is_an_invalid_release(
    rig: Rig, signature: str | None, reason: str
) -> None:
    history = first_release(rig)
    made = {
        "other_bytes": lambda: sign(rig.bot, b"other bytes", RELEASE_NS),
        "stranger": lambda: sign(rig.stranger, b"x", RELEASE_NS),
    }
    commit = bad_release(rig, history, made[signature]() if signature else None)

    report = run(rig, history.adapters, rig.base)

    newest = report.commits[0]
    assert (newest.commit, newest.classification) == (commit, "release_invalid")
    assert newest.attestation == "not_applicable"
    assert reason in newest.reasons, newest.reasons
    assert f"release_invalid:{commit}" in report.anomalies
    assert report.verdict == "anomalies_found"
    assert report.counts.invalid_releases == 1


def test_a_release_of_the_wrong_signature_class_is_invalid(rig: Rig) -> None:
    history = first_release(rig)
    commit = bad_release(rig, history, b"genuine", rig.stranger)

    report = run(rig, history.adapters, rig.base)

    newest = report.commits[0]
    assert (newest.commit, newest.classification) == (commit, "release_invalid")
    assert any(r.startswith("commit_signature:") for r in newest.reasons), newest.reasons
    assert report.verdict == "anomalies_found"


def test_a_merge_on_main_is_never_clean(rig_b: Rig) -> None:
    history = releases(rig_b, 2)
    work = work_clone(rig_b)
    tree = git(work, "rev-parse", "HEAD^{tree}").strip().encode()
    parents = (history.commits[-1].encode(), history.commits[0].encode())
    merge = push_object(rig_b, work, (tree, parents), ("a merge", rig_b.bot))

    report = run(rig_b, history.adapters, rig_b.base)

    assert report.commits[0].commit == merge
    assert classes(report) == [("merge", "not_applicable")]
    assert f"merge_commit:{merge}" in report.anomalies
    assert f"traversal_stopped_at_merge:{merge}" in report.gaps
    assert report.verdict == "anomalies_found"
    assert report.boundary.status == "not_reached"


# --- incomplete coverage ----------------------------------------------------------------------


def drop_object(rig: Rig, adapters: Adapters, commit: str) -> None:
    if rig.path == "A":
        del adapters.github.commits[commit]
        return
    (rig.bare / "objects" / commit[:2] / commit[2:]).unlink()


def test_an_unreadable_commit_is_incomplete(rig: Rig) -> None:
    history = releases(rig, 3)
    drop_object(rig, history.adapters, history.commits[1])

    report = run(rig, history.adapters, rig.base)

    assert report.verdict == "incomplete"
    assert any(g.startswith("commit_unreadable:") for g in report.gaps), report.gaps
    assert report.boundary.status == "not_reached"
    assert all(c.commit != history.commits[0] for c in report.commits)


def test_a_head_moving_during_the_audit_is_incomplete(rig: Rig) -> None:
    history = releases(rig, 2)
    source = reader(rig, history.adapters)
    moved: list[str] = []

    def commit_then_move(oid: str):  # type: ignore[no-untyped-def]
        if not moved:
            moved.append(add_foreign(rig, history))
        return source.commit(oid)

    report = audit(
        source._replace(commit=commit_then_move), AuditRequest(expected(rig), rig.base), NOW
    )

    assert report.verdict == "incomplete"
    assert "remote_head_moved_during_audit" in report.gaps
    assert report.final_head == moved[0] != report.observed_head
    assert [c.commit for c in report.commits] == list(reversed(history.commits))


def test_an_exceeded_traversal_limit_is_incomplete(rig: Rig) -> None:
    history = releases(rig, 3)

    report = run(rig, history.adapters, rig.base, limit=2)

    assert report.verdict == "incomplete"
    assert "traversal_limit_reached" in report.gaps
    assert report.counts.examined == 2
    assert report.scope.traversal_limit == 2
    assert report.boundary.status == "not_reached"


def test_a_boundary_off_main_is_never_clean(rig: Rig) -> None:
    history = releases(rig, 2)

    report = run(rig, history.adapters, "f" * 40)

    assert report.verdict != "verified_window"
    assert "boundary_not_reached" in report.gaps
    assert report.boundary.status == "not_reached"


def test_no_boundary_is_a_bounded_diagnostic_only(rig: Rig) -> None:
    history = releases(rig, 2)

    report = run(rig, history.adapters, None)

    assert report.verdict != "verified_window"
    assert "no_boundary_given" in report.gaps
    assert (report.boundary.source, report.boundary.status) == ("none", "not_given")


def test_an_empty_window_proves_nothing(rig: Rig) -> None:
    history = releases(rig, 1)

    report = run(rig, history.adapters, history.commits[0])

    assert (report.verdict, report.gaps) == ("incomplete", ("window_empty",))
    assert report.commits == ()


def test_an_unreadable_attestation_store_is_unverifiable(rig: Rig) -> None:
    history = releases(rig, 2)
    if rig.path == "A":
        history.adapters.github.objects.clear()
    else:
        tag = git(rig.bare, "rev-parse", f"refs/tags/forarbeider-attestation-{history.commits[1]}")
        oid = tag.strip()
        (rig.bare / "objects" / oid[:2] / oid[2:]).unlink()

    report = run(rig, history.adapters, rig.base)

    assert report.verdict == "incomplete"
    assert classes(report)[0] == ("release", "unverifiable")
    assert f"attestation_unverifiable:{history.commits[1]}" in report.gaps
    assert report.counts.unverifiable_attestations >= 1


def make_unavailable(rig: Rig, adapters: Adapters) -> None:
    if rig.path == "A":
        adapters.github.unavailable = True
        return
    rig.bare.rename(rig.bare.with_name("moved"))


def test_an_unavailable_remote_is_incomplete(rig: Rig) -> None:
    history = releases(rig, 1)
    make_unavailable(rig, history.adapters)

    report = audit_remote(history.adapters.at(NOW), AuditRequest(expected(rig), rig.base), NOW)

    assert report.verdict == "incomplete"
    assert "remote_unavailable" in report.gaps
    assert report.observed_head is None and report.commits == ()


# --- boundaries from a prior audit ------------------------------------------------------------


def prior(rig: Rig, history: History) -> AuditReport:
    report = run(rig, history.adapters, rig.base)
    assert report.verdict == "verified_window"
    return report


def test_a_prior_verified_audit_bounds_the_next_one(rig: Rig) -> None:
    history = releases(rig, 2)
    earlier = prior(rig, history)
    third = add_release(rig, history)
    raw = canonical_text(earlier).encode()

    report = run(rig, history.adapters, earlier.observed_head, prior=raw)

    assert report.verdict == "verified_window", (report.anomalies, report.gaps)
    assert [c.commit for c in report.commits] == [third]
    assert report.boundary.source == "prior_audit"
    assert report.boundary.prior_report_sha256 == sha256(raw).hexdigest()
    assert report.boundary.trusted is False


def substituted(report: AuditReport, case: str) -> bytes:
    changes = {
        "other_scope": {"scope": report.scope.model_copy(update={"trust_root_sha256": "0" * 64})},
        "not_verified": {"verdict": "incomplete"},
        "later": {
            "scope": report.scope.model_copy(update={"audited_at": NOW.replace(year=NOW.year + 1)})
        },
    }
    if case == "garbage":
        return b"{not a report"
    return canonical_text(report.model_copy(update=changes[case])).encode()


@pytest.mark.parametrize(
    ("case", "gap"),
    [
        ("other_scope", "boundary_report_other_scope"),
        ("not_verified", "boundary_report_not_verified"),
        ("later", "boundary_report_after_audit_time"),
        ("garbage", "boundary_report_unreadable"),
    ],
)
def test_a_stale_or_substituted_prior_report_never_bounds_a_clean_audit(
    rig: Rig, case: str, gap: str
) -> None:
    history = releases(rig, 2)
    earlier = prior(rig, history)
    add_release(rig, history)

    report = run(rig, history.adapters, earlier.observed_head, prior=substituted(earlier, case))

    assert report.verdict == "incomplete"
    assert gap in report.gaps, report.gaps


def test_a_boundary_other_than_the_prior_reports_head_is_refused(rig: Rig) -> None:
    history = releases(rig, 2)
    earlier = prior(rig, history)

    report = run(rig, history.adapters, history.commits[0], prior=canonical_text(earlier).encode())

    assert report.verdict == "incomplete"
    assert "boundary_not_the_reports_head" in report.gaps


def test_a_prior_audited_head_gone_from_main_is_a_rewrite(rig_b: Rig) -> None:
    history = releases(rig_b, 2)
    earlier = prior(rig_b, history).model_copy(update={"observed_head": "e" * 40})

    report = run(rig_b, history.adapters, "e" * 40, prior=canonical_text(earlier).encode())

    assert report.verdict == "anomalies_found"
    assert "history_rewritten_since_prior_audit" in report.anomalies
    assert "boundary_not_reached" in report.gaps


# --- repeat and restart -----------------------------------------------------------------------


def test_repeated_audits_are_identical_and_a_crash_leaves_nothing(rig: Rig) -> None:
    history = releases(rig, 2)
    add_foreign(rig, history)
    first = canonical_text(run(rig, history.adapters, rig.base)).encode()
    before = remote_state(rig, history.adapters)
    source = reader(rig, history.adapters)
    calls: list[str] = []

    def crash_on_second(oid: str):  # type: ignore[no-untyped-def]
        calls.append(oid)
        if len(calls) == 2:
            raise SimulatedCrash
        return source.commit(oid)

    with pytest.raises(SimulatedCrash):
        audit(source._replace(commit=crash_on_second), AuditRequest(expected(rig), rig.base), NOW)

    assert remote_state(rig, history.adapters) == before
    assert canonical_text(run(rig, history.adapters, rig.base)).encode() == first


def test_a_revoked_bot_key_invalidates_the_window(rig: Rig) -> None:
    history = releases(rig, 2)
    revoked = _root(rig.tmp / "revoked", rig.owner, rig.bot, rig.witness_key, (rig.bot,))
    request = AuditRequest(expected(rig)._replace(root=revoked), rig.base)

    report = audit(reader(rig, history.adapters), request, NOW)

    assert report.verdict == "anomalies_found"
    assert report.scope.trust_root_sha256 == revoked.sha256
    assert all(c.classification == "release_invalid" for c in report.commits)
