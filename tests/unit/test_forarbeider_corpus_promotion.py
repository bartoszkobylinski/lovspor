"""Plan, simulate and withdraw, through the CLI (#738, ADR-0018 PR3B-2, M2-M4).

A plan re-runs the preflight and writes no checkout. Only the simulator
writes, only to a sandbox this package made, only from the bundled synthetic
pages, and only after re-validating everything at that moment.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lovspor.forarbeider import corpus_commands, corpus_transaction
from lovspor.forarbeider.corpus_models import parse_manifest
from lovspor.forarbeider.corpus_transaction import JOURNAL, LOCK, STAGING
from lovspor.forarbeider.corpus_tree import BASELINE, read_dataset
from lovspor.forarbeider.review_models import ReviewStatus
from tests.unit.cli_output import said, unwrapped
from tests.unit.forarbeider_promotion_support import (
    WITHDRAWN,
    eligible,
    plan,
    planned,
    release_options,
    sandbox,
    simulate,
    tree,
    withdraw,
)
from tests.unit.forarbeider_release_support import (
    OWNER,
    PRIVATE_MARKER,
    REVIEWER,
    Desk,
    at_a_terminal,  # noqa: F401
    said_digest,
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")

DOCUMENT = "forarbeider/2099-2100/prop-l/9000001.md"
_GIT = shutil.which("git")


@pytest.fixture
def desk(tmp_path: Path) -> Desk:
    return Desk(tmp_path / "desk")


@pytest.fixture
def box(desk: Desk, tmp_path: Path) -> Path:
    path = tmp_path / "sandbox"
    result = sandbox(desk, path)
    assert result.exit_code == 0, result.output
    return path


def _assert_private_free(desk: Desk, output: str) -> None:
    printed = unwrapped(output)
    for private in (PRIVATE_MARKER, str(desk.root), REVIEWER, OWNER, "evidence/"):
        assert unwrapped(private) not in printed, private


def _outside_dataset(root: Path) -> dict[str, bytes]:
    return {
        path: payload
        for path, payload in tree(root).items()
        if not path.startswith(("forarbeider/", ".git/lovspor-forarbeider"))
    }


def test_a_plan_writes_no_checkout_and_keeps_its_files_privately(desk: Desk, box: Path) -> None:
    before = tree(box)
    report, checkpoints = eligible(desk)

    result = plan(desk, report, checkpoints, box)

    assert result.exit_code == 0, result.output
    assert said("action release publication_authorized=false", result.output)
    assert said("base forarbeider/manifest.json absent", result.output)
    for path in ("forarbeider/INDEX.md", "forarbeider/manifest.json", DOCUMENT):
        assert f"write {path} " in result.output
    assert "synthetic_inputs=true writes_corpus=false" in result.output
    assert tree(box) == before
    previews = sorted((desk.workspace / "previews").iterdir())
    assert len(previews) == 3
    assert all(oct(path.stat().st_mode & 0o777) == "0o600" for path in previews)
    _assert_private_free(desk, result.output)


def test_a_plan_of_the_same_inputs_is_the_same_plan(desk: Desk, box: Path) -> None:
    report, checkpoints = eligible(desk)

    first = plan(desk, report, checkpoints, box)
    second = plan(desk, report, checkpoints, box)

    assert first.exit_code == second.exit_code == 0
    assert said_digest(first.output, "plan") == said_digest(second.output, "plan")
    assert first.output == second.output


def test_a_plan_reads_a_real_looking_checkout_and_leaves_it_byte_identical(
    desk: Desk, tmp_path: Path
) -> None:
    checkout = tmp_path / "lovverk"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "manifest.json").write_text('{"documents": []}\n')
    (checkout / "lover").mkdir()
    (checkout / "lover" / "lov-1.md").write_text("# lov\n")
    before = tree(checkout)
    report, checkpoints = eligible(desk)

    result = plan(desk, report, checkpoints, checkout)

    assert result.exit_code == 0, result.output
    assert tree(checkout) == before
    assert not (checkout / "forarbeider").exists()


def _deny_after(desk: Desk, box: Path, change: str) -> None:
    report, checkpoints = eligible(desk)
    if change == "held":
        desk.decide(3, ReviewStatus.HELD, at="2100-04-09T00:00:00Z")
    elif change == "revoked":
        revoke = ["revoke", "--authorization", _authorization(desk, report)]
        desk.invoke([*release_options(desk), *revoke, "--decided", f"{OWNER}@2100-04-09T00:00:00Z"])
    elif change == "page":
        desk.pages[3].write_bytes(desk.pages[3].read_bytes() + b"<p>endret</p>")
    elif change == "evidence":
        desk.evidence["source_rights"].write_bytes(b"endret bevis")
    elif change == "dossier":
        for path in (desk.workspace / "dossiers").iterdir():
            path.unlink()
    before = tree(box)

    result = plan(desk, report, checkpoints, box)

    assert result.exit_code == 2, (change, result.output)
    assert said("refused: promotion_denied", result.output), result.output
    assert tree(box) == before


def _authorization(desk: Desk, report: str) -> str:
    stored = json.loads((desk.workspace / "reports" / f"{report}.json").read_text())
    return str(stored["authorization_sha256"])


@pytest.mark.parametrize("change", ["held", "revoked", "page", "evidence", "dossier"])
def test_a_plan_denies_once_anything_changed_since_the_preflight(
    desk: Desk, box: Path, change: str
) -> None:
    _deny_after(desk, box, change)


def test_a_plan_with_another_checkpoint_is_denied(desk: Desk, box: Path) -> None:
    report, checkpoints = eligible(desk)

    result = plan(desk, report, checkpoints[:1], box)

    assert result.exit_code == 2, result.output


def test_a_plan_from_a_deny_report_is_denied(desk: Desk, box: Path) -> None:
    desk.approve_all()
    _, authorization = desk.released()
    denied = desk.preflight(authorization, ())
    assert denied.exit_code == 2

    result = plan(desk, said_digest(denied.output, "report"), [], box)

    assert result.exit_code == 2
    assert said("refused: promotion_denied", result.output)


@pytest.mark.parametrize("report", ["0" * 64, "not-a-digest"])
def test_a_plan_from_an_unknown_report_is_refused(desk: Desk, box: Path, report: str) -> None:
    result = plan(desk, report, [], box)

    assert result.exit_code == 1
    assert said("refused: release_", result.output)


def test_simulate_writes_only_the_dataset_and_verifies_it(desk: Desk, box: Path) -> None:
    sha = planned(desk, box)
    outside = _outside_dataset(box)

    result = simulate(desk, sha, box)

    assert result.exit_code == 0, result.output
    assert said(f"APPLIED regjeringen-9000001 plan {sha}", result.output)
    assert "no git add, commit or push" in result.output
    assert _outside_dataset(box) == outside
    assert {name: (box / name).read_text() for name in BASELINE} == BASELINE
    dataset = read_dataset(box)
    member = dataset.manifest.documents[0]
    assert (member.path, member.coverage, member.release_status) == (
        DOCUMENT,
        "partial_curated_excerpt",
        "released",
    )
    assert [item.chapter_number for item in member.chapters] == [2, 3]
    previews = {path.read_bytes() for path in (desk.workspace / "previews").iterdir()}
    assert (box / DOCUMENT).read_bytes() in previews
    assert not (box / JOURNAL).exists() and not (box / LOCK).exists()
    _assert_private_free(desk, result.output)


def test_a_retried_simulation_changes_nothing(desk: Desk, box: Path) -> None:
    sha = planned(desk, box)
    assert simulate(desk, sha, box).exit_code == 0
    after = tree(box)

    again = simulate(desk, sha, box)

    assert again.exit_code == 0, again.output
    assert said("UNCHANGED", again.output)
    assert tree(box) == after


@pytest.mark.skipif(_GIT is None, reason="git is not installed")
def test_simulate_stages_commits_and_pushes_nothing(desk: Desk, box: Path) -> None:
    assert simulate(desk, planned(desk, box), box).exit_code == 0

    def git(*arguments: str) -> str:
        assert _GIT is not None
        done = subprocess.run(
            [_GIT, *arguments], cwd=box, capture_output=True, text=True, check=False
        )
        return done.stdout

    assert git("rev-parse", "--verify", "-q", "HEAD") == ""
    assert not (box / ".git" / "index").exists()
    assert git("remote") == ""
    assert "?? forarbeider/" in git("status", "--porcelain")


def test_simulate_refuses_a_checkout_it_did_not_make(desk: Desk, tmp_path: Path) -> None:
    clone = tmp_path / "lovverk"
    (clone / ".git").mkdir(parents=True)
    (clone / "manifest.json").write_text('{"documents": []}\n')
    report, checkpoints = eligible(desk)
    sha = said_digest(plan(desk, report, checkpoints, clone).output, "plan")
    before = tree(clone)

    result = simulate(desk, sha, clone)

    assert result.exit_code == 1
    assert said("refused: corpus_not_sandbox: sandbox", result.output)
    assert tree(clone) == before


def test_simulate_refuses_inputs_that_are_not_the_bundled_synthetic_pages(
    tmp_path: Path,
) -> None:
    desk = Desk(tmp_path / "desk", extras={3: "<p>Ikke en del av pakken.</p>"})
    path = tmp_path / "sandbox"
    assert sandbox(desk, path).exit_code == 0
    report, checkpoints = eligible(desk)
    result = plan(desk, report, checkpoints, path)
    assert "synthetic_inputs=false" in result.output
    before = tree(path)

    simulated = simulate(desk, said_digest(result.output, "plan"), path)

    assert simulated.exit_code == 1
    assert said("refused: input_not_synthetic: plan", simulated.output)
    assert tree(path) == before


def test_simulate_denies_a_revocation_made_after_the_plan(desk: Desk, box: Path) -> None:
    report, checkpoints = eligible(desk)
    sha = said_digest(plan(desk, report, checkpoints, box).output, "plan")
    revoke = ["revoke", "--authorization", _authorization(desk, report)]
    desk.invoke([*release_options(desk), *revoke, "--decided", f"{OWNER}@2100-04-09T00:00:00Z"])
    before = tree(box)

    result = simulate(desk, sha, box)

    assert result.exit_code == 2, result.output
    assert said("refused: promotion_denied", result.output)
    assert tree(box) == before


def test_simulate_denies_an_authorization_the_machine_clock_has_ended(
    desk: Desk, box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = planned(desk, box)
    monkeypatch.setattr(
        corpus_commands,
        "_now",
        lambda: corpus_commands.datetime(2101, 1, 1, tzinfo=corpus_commands.UTC),
    )
    before = tree(box)

    result = simulate(desk, sha, box)

    assert result.exit_code == 2
    assert said("refused: promotion_denied: authorization_expired", result.output)
    assert tree(box) == before


def test_simulate_refuses_a_plan_made_against_another_dataset_state(
    tmp_path: Path,
) -> None:
    first = Desk(tmp_path / "l")
    second = Desk(tmp_path / "ls", kind="ls")
    path = tmp_path / "sandbox"
    assert sandbox(first, path).exit_code == 0
    stale = planned(first, path)
    other = planned(second, path)
    assert simulate(second, other, path).exit_code == 0
    before = tree(path)

    result = simulate(first, stale, path)

    assert result.exit_code == 1
    assert said("refused: plan_stale: plan", result.output)
    assert tree(path) == before


def test_two_propositions_share_one_manifest_in_path_order(tmp_path: Path) -> None:
    first = Desk(tmp_path / "l")
    second = Desk(tmp_path / "ls", kind="ls")
    path = tmp_path / "sandbox"
    assert sandbox(first, path).exit_code == 0
    assert simulate(second, planned(second, path), path).exit_code == 0

    assert simulate(first, planned(first, path), path).exit_code == 0

    documents = read_dataset(path).manifest.documents
    assert [item.document.document_type for item in documents] == ["prop-l", "prop-ls"]
    index = (path / "forarbeider" / "INDEX.md").read_text()
    assert index.index("prop-l/9000001.md") < index.index("prop-ls/9000003.md")


def test_a_held_lock_refuses_a_concurrent_change(desk: Desk, box: Path) -> None:
    sha = planned(desk, box)
    (box / LOCK).write_text("")
    before = tree(box)

    result = simulate(desk, sha, box)

    assert result.exit_code == 1
    assert said("refused: corpus_locked: sandbox", result.output)
    assert tree(box) == before


def test_an_interrupted_change_is_finished_by_the_next_run(
    desk: Desk, box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = planned(desk, box)
    real = corpus_transaction.replace
    calls: list[Path] = []

    def crash_after_the_document(source: Path, target: Path) -> None:
        calls.append(target)
        if target.name == "INDEX.md":
            raise OSError("simulated crash")
        real(source, target)

    monkeypatch.setattr(corpus_transaction, "replace", crash_after_the_document)
    crashed = simulate(desk, sha, box)
    assert crashed.exit_code == 1
    assert said("refused: output_write_failed: forarbeider", crashed.output)
    assert (box / JOURNAL).exists() and not (box / LOCK).exists()
    monkeypatch.setattr(corpus_transaction, "replace", real)

    recovered = simulate(desk, sha, box)

    assert recovered.exit_code == 0, recovered.output
    assert "recovered_interrupted_change" in recovered.output
    assert read_dataset(box).manifest.documents[0].path == DOCUMENT
    assert not (box / JOURNAL).exists()


def test_a_journal_that_was_tampered_with_is_refused(desk: Desk, box: Path) -> None:
    sha = planned(desk, box)
    (box / JOURNAL).write_text('{"operations": []}')

    result = simulate(desk, sha, box)

    assert result.exit_code == 1
    assert said("refused: corpus_tree_mismatch: journal", result.output)


@pytest.mark.parametrize("action", ["release", "withdraw"])
@pytest.mark.parametrize(
    "stop_after", [DOCUMENT, "forarbeider/INDEX.md", "forarbeider/manifest.json"]
)
def test_recovery_finishes_each_completed_operation_without_rewriting_it(
    desk: Desk, box: Path, monkeypatch: pytest.MonkeyPatch, action: str, stop_after: str
) -> None:
    """The journal survives even a failure after the manifest commit point (#738)."""
    sha = planned(desk, box)
    if action == "withdraw":
        assert simulate(desk, sha, box).exit_code == 0
    outside = _outside_dataset(box)
    real_perform = corpus_transaction._perform

    def interrupt_after_operation(root: Path, operation: corpus_transaction.Operation) -> None:
        real_perform(root, operation)
        if operation.path == stop_after:
            raise OSError("interrupted after completing an operation")

    monkeypatch.setattr(corpus_transaction, "_perform", interrupt_after_operation)
    result = (
        simulate(desk, sha, box)
        if action == "release"
        else withdraw(desk, box, "regjeringen-9000001")
    )
    assert result.exit_code == 1, result.output
    assert said("refused: output_write_failed: forarbeider", result.output)
    assert (box / JOURNAL).exists()
    assert not (box / LOCK).exists()
    journal = corpus_transaction.Journal.model_validate_json((box / JOURNAL).read_bytes())
    completed = {
        operation.path: (box / operation.path).stat().st_mtime_ns
        for operation in journal.operations[
            : next(
                i for i, operation in enumerate(journal.operations) if operation.path == stop_after
            )
            + 1
        ]
        if operation.sha256 is not None
    }
    monkeypatch.setattr(corpus_transaction, "_perform", real_perform)

    with corpus_transaction.locked(box):
        assert corpus_transaction.recover(box) is True
        assert corpus_transaction.recover(box) is False

    assert {path: (box / path).stat().st_mtime_ns for path in completed} == completed
    dataset = read_dataset(box)
    assert dataset.manifest_sha256 == journal.target_manifest_sha256
    assert dataset.manifest.documents[0].release_status == (
        "released" if action == "release" else "withdrawn"
    )
    assert (box / DOCUMENT).exists() == (action == "release")
    assert _outside_dataset(box) == outside
    assert not (box / JOURNAL).exists()
    assert not (box / STAGING).exists()
    assert not (box / LOCK).exists()


def test_withdrawal_removes_the_member_and_keeps_its_record(desk: Desk, box: Path) -> None:
    assert simulate(desk, planned(desk, box), box).exit_code == 0

    result = withdraw(desk, box, "regjeringen-9000001")

    assert result.exit_code == 0, result.output
    assert said(f"removed {DOCUMENT}", result.output)
    assert "a revert does not erase them" in result.output
    assert not (box / DOCUMENT).exists()
    manifest = parse_manifest((box / "forarbeider" / "manifest.json").read_bytes())
    member = manifest.documents[0]
    assert (member.release_status, member.markdown_sha256) == ("withdrawn", None)
    assert member.times.withdrawn_at is not None
    assert "9000001" not in (box / "forarbeider" / "INDEX.md").read_text()
    stored = list((desk.workspace / "withdrawals").iterdir())
    assert len(stored) == 1
    assert json.loads(stored[0].read_text())["history_erased"] is False
    _assert_private_free(desk, result.output.replace(OWNER, ""))


def test_a_withdrawn_document_cannot_be_withdrawn_or_released_again(desk: Desk, box: Path) -> None:
    sha = planned(desk, box)
    assert simulate(desk, sha, box).exit_code == 0
    assert withdraw(desk, box, "regjeringen-9000001").exit_code == 0
    after = tree(box)

    again = withdraw(desk, box, "regjeringen-9000001")
    resimulated = simulate(desk, sha, box)

    assert again.exit_code == 1
    assert said("refused: document_not_released: document", again.output)
    assert resimulated.exit_code == 1
    assert tree(box) == after


def test_withdrawal_is_refused_away_from_a_terminal(
    desk: Desk, box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert simulate(desk, planned(desk, box), box).exit_code == 0
    monkeypatch.setattr("lovspor.forarbeider.release_commands._interactive", lambda: False)
    before = tree(box)

    result = withdraw(desk, box, "regjeringen-9000001")

    assert result.exit_code == 1
    assert said("refused: review_not_interactive", result.output)
    assert tree(box) == before


@pytest.mark.parametrize(
    ("document", "at", "reason"),
    [
        ("regjeringen-9000002", WITHDRAWN, "document_not_released: document"),
        ("../../lover/x", WITHDRAWN, "document_not_released: document"),
        ("regjeringen-9000001", "2100-04-01T00:00:00Z", "manifest_invalid: withdrawn_at"),
    ],
)
def test_a_withdrawal_of_nothing_released_or_before_release_is_refused(
    desk: Desk, box: Path, document: str, at: str, reason: str
) -> None:
    assert simulate(desk, planned(desk, box), box).exit_code == 0
    before = tree(box)

    result = withdraw(desk, box, document, at)

    assert result.exit_code == 1
    assert said(f"refused: {reason}", result.output), result.output
    assert tree(box) == before


def test_withdrawal_refuses_a_checkout_it_did_not_make(desk: Desk, tmp_path: Path) -> None:
    clone = tmp_path / "lovverk"
    (clone / ".git").mkdir(parents=True)

    result = withdraw(desk, clone, "regjeringen-9000001")

    assert result.exit_code == 1
    assert said("refused: corpus_not_sandbox", result.output)


def test_the_synthetic_fixtures_are_written_privately_and_once(desk: Desk, tmp_path: Path) -> None:
    out = tmp_path / "fixtures"
    arguments = [*release_options(desk), "synthetic-fixtures", "--out", str(out)]

    first = desk.invoke(arguments)
    second = desk.invoke(arguments)

    assert first.exit_code == second.exit_code == 0, first.output
    names = sorted(path.name for path in out.iterdir())
    assert names == sorted(
        f"{kind}-{suffix}"
        for kind in ("prop-l", "prop-ls")
        for suffix in ("metadata.json", "ch2.html", "ch3.html", "ch2.json", "ch3.json")
    )
    assert all(oct(path.stat().st_mode & 0o777) == "0o600" for path in out.iterdir())
    assert oct(os.lstat(out).st_mode & 0o777) == "0o700"
    assert (out / "prop-l-ch2.html").read_bytes() == desk.pages[2].read_bytes()


def test_the_synthetic_fixtures_are_never_written_into_a_repository(
    desk: Desk, tmp_path: Path
) -> None:
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    arguments = [
        *release_options(desk),
        "synthetic-fixtures",
        "--out",
        str(tmp_path / "repo" / "fixtures"),
    ]

    result = desk.invoke(arguments)

    assert result.exit_code == 1
    assert said("refused: unsafe_output_path: fixtures", result.output)


def test_a_tampered_fixture_is_refused_not_overwritten(desk: Desk, tmp_path: Path) -> None:
    out = tmp_path / "fixtures"
    arguments = [*release_options(desk), "synthetic-fixtures", "--out", str(out)]
    assert desk.invoke(arguments).exit_code == 0
    before = {path.name: path.read_bytes() for path in out.iterdir()}
    tampered = out / "prop-l-ch2.html"
    tampered.write_bytes(b"endret innhold")

    result = desk.invoke(arguments)

    assert result.exit_code == 1
    assert said("refused: observation_exists: fixtures", result.output)
    assert tampered.read_bytes() == b"endret innhold"
    after = {path.name: path.read_bytes() for path in out.iterdir()}
    assert {**before, "prop-l-ch2.html": b"endret innhold"} == after


def test_a_held_chapter_is_left_out_and_the_document_marked_partial(desk: Desk, box: Path) -> None:
    desk.decide(2, ReviewStatus.APPROVED)
    desk.decide(3, ReviewStatus.HELD)
    _, authorization = desk.released(chapters=(2,))
    checkpoints = [desk.checkpoint(2)]
    preflight = desk.preflight(authorization, checkpoints, chapters=(2, 3))
    assert preflight.exit_code == 0, preflight.output
    report = said_digest(preflight.output, "report")

    planned_sha = said_digest(plan(desk, report, checkpoints, box).output, "plan")
    assert simulate(desk, planned_sha, box).exit_code == 0

    member = read_dataset(box).manifest.documents[0]
    assert [item.chapter_number for item in member.chapters] == [2]
    markdown = (box / DOCUMENT).read_text()
    assert "Partial curated excerpt" in markdown
    assert desk.results[3].source_page_url not in markdown
