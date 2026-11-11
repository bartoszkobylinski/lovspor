"""``lovspor ops forarbeider-release-read``: the receipt-verified real-release reader (#738, B2).

Every state is reached through supported commands: a synthetic batch planned,
applied by ``batch release-clone apply`` to a scratch release clone and
committed, then fast-forwarded onto ``main`` with the remote-tracking
``origin/main`` set as a fetch would. Each read must leave every byte below
``tmp_path`` (clone, ``.git``, workspace) as it found it, and no refusal may
carry chapter text, a private note, a handle or a path.
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest
import typer
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider import publication, release_reader
from lovspor.forarbeider.corpus_models import (
    EMPTY_MANIFEST,
    INDEX_PATH,
    MANIFEST_PATH,
    manifest_text,
    manifest_v2_text,
    parse_any_manifest,
)
from lovspor.forarbeider.corpus_render import render_index
from lovspor.forarbeider.corpus_transaction import JOURNAL, LOCK
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from tests.unit.cli_output import said
from tests.unit.forarbeider_apply_support import (
    DOC1,
    DOC2,
    NOW,
    PATH1,
    QUOTE,
    TABLE,
    ApplyBench,
)
from tests.unit.forarbeider_batch_support import BRANCH, CHAPTER_MARKER
from tests.unit.forarbeider_checkout_support import commit_all, git, snapshot
from tests.unit.forarbeider_release_support import (
    OWNER,
    PRIVATE_MARKER,
    at_a_terminal,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApplyBench:
    return ApplyBench(tmp_path, monkeypatch)


ATTESTED = "2100-04-10T12:50:00Z"


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publication, "_now", lambda: NOW)


def attest(bench: ApplyBench, commit: str = "", typed: str | None = None) -> Any:
    """The owner attests the commit they saw on lovverk main, at a terminal."""
    sha = commit or git(bench.clone, "rev-parse", "HEAD").strip()
    head = ["ops", "forarbeider-release", "--workspace", str(bench.batch.workspace)]
    head += ["--ledger", str(bench.batch.ledger), "publication", "attest"]
    arguments = [
        "--checkout",
        str(bench.clone),
        "--commit",
        sha,
        "--decided",
        f"{OWNER}@{ATTESTED}",
    ]
    with pytest.MonkeyPatch.context() as typist:
        typist.setattr(
            typer, "prompt", lambda text, **_: typed or text.removeprefix("Type to confirm: ")
        )
        return CliRunner().invoke(app, [*head, *arguments])


def publish(bench: ApplyBench, message: str = "owner: release batch") -> None:
    """The owner commits the batch branch, merges it to main and attests the merged commit."""
    commit_all(bench.clone, message)
    git(bench.clone, "switch", "--quiet", "main")
    git(bench.clone, "merge", "--quiet", "--ff-only", BRANCH)
    result = attest(bench)
    assert result.exit_code == 0, result.output


def back_to_batch_branch(bench: ApplyBench) -> None:
    git(bench.clone, "switch", "--quiet", BRANCH)
    git(bench.clone, "merge", "--quiet", "--ff-only", "main")


@pytest.fixture
def released(bench: ApplyBench, httpx_mock: HTTPXMock, at_a_terminal: None) -> ApplyBench:  # noqa: F811
    """Doc 1: chapters 2, 3 by the policy. Doc 2: 2 by the policy, 3 by owner review."""
    request = bench.mixed(httpx_mock)
    assert bench.apply(bench.planned(request), request).exit_code == 0
    publish(bench)
    return bench


def read(bench: ApplyBench, *arguments: str, workspace: Path | None = None) -> Any:
    """One reader call; it must change nothing anywhere and leak nothing private."""
    space = workspace or bench.batch.workspace
    head = ["ops", "forarbeider-release-read", "--workspace", str(space)]
    before = snapshot(bench.root)
    result = CliRunner().invoke(app, [*head, "--checkout", str(bench.clone), *arguments])
    assert snapshot(bench.root) == before
    for secret in (PRIVATE_MARKER, OWNER, str(bench.root)):
        assert secret not in result.output
    if result.exit_code != 0:
        assert CHAPTER_MARKER not in result.output
    return result


def loaded(result: Any) -> dict[str, Any]:
    assert result.exit_code == 0, result.output
    record: dict[str, Any] = json.loads(result.output)
    return record


def refused(result: Any, reason: str, detail: str) -> None:
    assert result.exit_code == 1, result.output
    assert said(f"refused: {reason}: {detail}", result.output), result.output
    assert "{" not in result.output


def edit_committed(bench: ApplyBench, path: str, payload: bytes) -> None:
    (bench.clone / path).write_bytes(payload)
    commit_all(bench.clone, "hand edit")
    attest(bench)  # refused when the guard already refuses the tree; the reader refuses too


# --- the pass --------------------------------------------------------------------------------


def test_a_published_release_is_listed_with_its_labels(released: ApplyBench) -> None:
    record = loaded(read(released, "list"))

    assert record["record_type"] == "forarbeider_release_list"
    assert record["synthetic"] is False
    assert record["publication"] == "owner_attested_commit"
    assert record["remote_verified"] is False
    assert record["attested_commit"] == record["head_commit"]
    assert "NOT enacted law" in record["notice"]
    assert "not identity" in record["trust_notice"]
    assert record["head_commit"] == git(released.clone, "rev-parse", "HEAD").strip()
    assert [item["document"]["id"] for item in record["documents"]] == [DOC1, DOC2]
    assert [item["released_chapters"] for item in record["documents"]] == [[2, 3], [2, 3]]


def test_show_states_labels_coverage_times_and_both_clearance_paths(released: ApplyBench) -> None:
    first = loaded(read(released, "show", "--document", DOC1))
    second = loaded(read(released, "show", "--document", DOC2))

    assert first["labels"] == {
        "source_document_type": "forarbeid",
        "normative": False,
        "source_provider": "regjeringen.no",
        "rights_basis": "åndsverkloven § 14",
        "source_license": None,
        "asserts_complete_proposition": False,
    }
    assert first["partial_curated_excerpt"] is True
    assert first["partial_notice"].startswith("Partial curated excerpt")
    policy = first["chapters"][0]["clearance"]
    assert policy["cleared_by"] == "standing_policy"
    assert policy["individually_reviewed"] is False
    assert policy["policy_sha256"] == released.batch.policy_sha
    review = second["chapters"][1]["clearance"]
    assert review["cleared_by"] == "owner_review"
    assert review["individually_reviewed"] is True
    assert review["provenance"] == "recorded"
    assert review["review"]["revision"] >= 1
    assert first["times"]["owner_decided_at"] == "2100-04-10T12:30:00Z"


def test_a_chapter_is_served_alone_under_its_source_line(released: ApplyBench) -> None:
    record = loaded(read(released, "chapter", "--document", DOC1, "--chapter", "3"))

    desk = released.batch.desks[1]
    markdown = record["markdown"]
    assert markdown.startswith(f"Source: <{desk.results[3].source_page_url}>")
    assert desk.results[2].source_page_url not in markdown
    assert record["chapter"]["chapter_number"] == 3
    assert record["labels"]["normative"] is False
    assert markdown in (released.clone / PATH1).read_text()


def test_provenance_names_the_verified_owner_records(released: ApplyBench) -> None:
    record = loaded(read(released, "provenance", "--document", DOC2))

    receipts = record["receipts"]
    workspace = released.batch.workspace
    assert (workspace / "batch_releases" / f"{receipts['confirmation_sha256']}.json").exists()
    assert (workspace / "batch_plans" / f"{receipts['plan_sha256']}.json").exists()
    assert receipts["verification"] == "owner_records_integrity_unauthenticated"
    assert (
        record["markdown_sha256"]
        == json.loads((released.clone / MANIFEST_PATH).read_text())["documents"][1][
            "markdown_sha256"
        ]
    )


def test_a_checkout_without_forarbeider_lists_nothing(bench: ApplyBench) -> None:
    bench.batch.workspace.mkdir(mode=0o700)
    git(bench.clone, "switch", "--quiet", "main")
    assert attest(bench).exit_code == 0

    record = loaded(read(bench, "list"))

    assert record["documents"] == []
    assert record["manifest_sha256"] is None


def test_the_command_states_it_reads_a_published_main_and_writes_nothing() -> None:
    from lovspor.forarbeider import release_reader_cli  # noqa: PLC0415

    assert "Read-only" in str(release_reader_cli.release_read_app.info.help)
    assert "Offline and read-only" in str(release_reader_cli.read_scope.__doc__)
    result = CliRunner().invoke(app, ["ops", "forarbeider-release-read", "--help"])
    assert result.exit_code == 0


# --- not published ---------------------------------------------------------------------------


def test_a_batch_in_the_working_tree_is_never_served(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    assert bench.apply(bench.planned(request), request).exit_code == 0

    refused(read(bench, "list"), "checkout_branch_mismatch", "branch")
    for path in sorted(bench.status()):
        target = bench.root / "copy" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((bench.clone / path).read_bytes())
    git(bench.clone, "stash", "--include-untracked", "--quiet")
    git(bench.clone, "switch", "--quiet", "main")
    for path in (p for p in (bench.root / "copy").rglob("*") if p.is_file()):
        target = bench.clone / path.relative_to(bench.root / "copy")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    refused(read(bench, "list"), "checkout_dirty", "untracked")


def test_a_local_commit_with_origin_main_moved_to_it_is_not_published(
    released: ApplyBench,
) -> None:
    """The finding: a remote-tracking ref moved offline must never stand in for publication."""
    git(released.clone, "commit", "--quiet", "--allow-empty", "-m", "local only")
    git(released.clone, "update-ref", "refs/remotes/origin/main", "HEAD")

    refused(read(released, "list"), "release_not_published", "attestation")


def test_without_an_attestation_for_head_nothing_is_published(released: ApplyBench) -> None:
    (stored,) = released.records("publications")
    stored.rename(released.root / "moved-attestation.json")

    refused(read(released, "list"), "release_not_published", "attestation")


def test_a_corrupt_or_mismatched_attestation_is_refused(released: ApplyBench) -> None:
    (stored,) = released.records("publications")
    saved = stored.read_text()
    stored.write_text(saved.replace('"commit"', '"commit" ', 1))
    refused(read(released, "list"), "release_record_corrupt", stored.stem)
    payload = json.loads(saved)
    payload["manifest_sha256"] = "0" * 64
    stored.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    refused(read(released, "list"), "release_not_published", "attestation_mismatch")


def test_moving_origin_main_during_a_read_changes_nothing_it_is_not_trusted(
    released: ApplyBench, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = release_reader.read_any_dataset_files
    root = git(released.clone, "rev-list", "--max-parents=0", "HEAD").strip()

    def read_then_move_the_ref(checkout: Path) -> Any:
        found = real(checkout)
        git(released.clone, "update-ref", "refs/remotes/origin/main", root)
        return found

    monkeypatch.setattr(release_reader, "read_any_dataset_files", read_then_move_the_ref)
    head = ["ops", "forarbeider-release-read", "--workspace", str(released.batch.workspace)]
    result = CliRunner().invoke(app, [*head, "--checkout", str(released.clone), "list"])

    record = loaded(result)
    assert record["remote_verified"] is False
    assert record["attested_commit"] == git(released.clone, "rev-parse", "HEAD").strip()


@pytest.mark.parametrize("change", ["head", "attestation"])
def test_a_publication_change_while_reading_is_refused(
    released: ApplyBench, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    real = release_reader.read_any_dataset_files

    def read_then_change(checkout: Path) -> Any:
        found = real(checkout)
        if change == "head":
            git(released.clone, "commit", "--quiet", "--allow-empty", "-m", "moved while reading")
            assert attest(released).exit_code == 0
        else:
            (stored,) = released.records("publications")
            stored.rename(released.root / "gone.json")
        return found

    monkeypatch.setattr(release_reader, "read_any_dataset_files", read_then_change)
    head = ["ops", "forarbeider-release-read", "--workspace", str(released.batch.workspace)]
    result = CliRunner().invoke(app, [*head, "--checkout", str(released.clone), "list"])

    assert result.exit_code == 1, result.output
    assert said("refused: corpus_tree_mismatch: concurrent", result.output) or said(
        "refused: release_not_published: attestation", result.output
    )
    assert "{" not in result.output


# --- the attestation command -----------------------------------------------------------------


def test_an_attestation_records_the_owners_word_and_writes_only_the_workspace(
    released: ApplyBench,
) -> None:
    (stored,) = released.records("publications")
    record = json.loads(stored.read_text())
    head = git(released.clone, "rev-parse", "HEAD").strip()

    assert stored.name == f"{head}.json"
    assert record["commit"] == head
    assert record["branch"] == "main"
    assert record["repository"] == "github.com/bartoszkobylinski/lovverk"
    assert record["basis"] == "owner_asserted_unverified"
    assert record["remote_verified"] is False
    assert record["identity_authenticated"] is False
    assert record["manifest_sha256"] == json.loads(
        json.dumps(_sha((released.clone / MANIFEST_PATH).read_bytes()))
    )
    before = snapshot(released.root)
    assert attest(released).exit_code == 0, "the same attestation again is a no-op"
    assert snapshot(released.root) == before


def test_an_attestation_names_head_exactly_from_a_clean_main(released: ApplyBench) -> None:
    git(released.clone, "commit", "--quiet", "--allow-empty", "-m", "not yet attested")
    before = snapshot(released.root)
    other = git(released.clone, "rev-list", "--max-parents=0", "HEAD").strip()
    refused(attest(released, commit=other), "release_not_published", "commit")
    refused(attest(released, commit="not-a-commit"), "release_invalid", "commit")
    refused(
        attest(released, typed="attest something else"), "review_not_interactive", "publication"
    )
    assert snapshot(released.root) == before
    git(released.clone, "switch", "--quiet", BRANCH)
    refused(attest(released), "checkout_branch_mismatch", "branch")
    git(released.clone, "switch", "--quiet", "main")
    (released.clone / "stray.txt").write_text("x\n")
    refused(attest(released), "checkout_dirty", "untracked")


def test_a_wrong_branch_repository_or_path_is_refused(released: ApplyBench) -> None:
    git(released.clone, "switch", "--quiet", BRANCH)
    refused(read(released, "list"), "checkout_branch_mismatch", "branch")
    git(released.clone, "switch", "--quiet", "main")
    git(released.clone, "remote", "set-url", "origin", "https://github.com/someone/lovverk.git")
    refused(read(released, "list"), "checkout_remote_untrusted", "origin")
    git(
        released.clone,
        "remote",
        "set-url",
        "origin",
        "https://github.com/bartoszkobylinski/lovverk.git",
    )
    link = released.root / "linked-clone"
    link.symlink_to(released.clone)
    head = ["ops", "forarbeider-release-read", "--workspace", str(released.batch.workspace)]
    result = CliRunner().invoke(app, [*head, "--checkout", str(link), "list"])
    refused(result, "checkout_unsafe", "path")


def test_an_interrupted_transaction_is_never_read_through(released: ApplyBench) -> None:
    for name, detail in ((LOCK, "lock"), (JOURNAL, "journal")):
        (released.clone / name).write_text("{}")
        refused(read(released, "list"), "checkout_pending_change", detail)
        (released.clone / name).unlink()


def test_a_change_while_reading_is_refused(
    released: ApplyBench, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = release_reader.read_any_dataset_files

    def read_then_move(checkout: Path) -> Any:
        found = real(checkout)
        git(released.clone, "commit", "--quiet", "--allow-empty", "-m", "moved while reading")
        assert attest(released).exit_code == 0
        return found

    monkeypatch.setattr(release_reader, "read_any_dataset_files", read_then_move)
    head = ["ops", "forarbeider-release-read", "--workspace", str(released.batch.workspace)]
    result = CliRunner().invoke(app, [*head, "--checkout", str(released.clone), "list"])

    refused(result, "corpus_tree_mismatch", "concurrent")


# --- the dataset's own bytes -----------------------------------------------------------------


def test_edited_chapter_or_index_bytes_are_refused(released: ApplyBench) -> None:
    original = (released.clone / PATH1).read_bytes()
    edit_committed(released, PATH1, original.replace(b"Prop.", b"Prop:", 1))
    refused(read(released, "list"), "corpus_tree_mismatch", "input")
    edit_committed(released, PATH1, original)
    edit_committed(released, INDEX_PATH, b"# Forarbeider\n")
    refused(read(released, "list"), "corpus_tree_mismatch", "input")


def test_a_version_1_or_unknown_manifest_is_refused(bench: ApplyBench) -> None:
    bench.batch.workspace.mkdir(mode=0o700)
    git(bench.clone, "switch", "--quiet", "main")
    (bench.clone / "forarbeider").mkdir()
    (bench.clone / INDEX_PATH).write_text(render_index(EMPTY_MANIFEST))
    edit_committed(bench, MANIFEST_PATH, manifest_text(EMPTY_MANIFEST).encode())
    refused(read(bench, "list"), "manifest_incompatible", "manifest")
    edit_committed(bench, MANIFEST_PATH, b'{"schema_version": 3}\n')
    refused(read(bench, "list"), "manifest_incompatible", "manifest")


def test_a_file_that_does_not_restate_its_member_is_refused(released: ApplyBench) -> None:
    manifest = parse_any_manifest((released.clone / MANIFEST_PATH).read_bytes())
    original = (released.clone / PATH1).read_bytes()
    forged = original.replace(b"normative: false", b"normative: true", 1)
    first = manifest.documents[0].model_copy(update={"markdown_sha256": _sha(forged)})
    edited = manifest.model_copy(update={"documents": (first, *manifest.documents[1:])})
    (released.clone / PATH1).write_bytes(forged)
    edit_committed(released, MANIFEST_PATH, manifest_v2_text(edited).encode())

    refused(read(released, "list"), "corpus_tree_mismatch", "front_matter")


def _sha(payload: bytes) -> str:
    import hashlib  # noqa: PLC0415

    return hashlib.sha256(payload).hexdigest()


# --- the owner's records ---------------------------------------------------------------------


def _record(bench: ApplyBench, folder: str) -> Path:
    (found,) = bench.records(folder)
    return found


def test_without_the_workspace_or_its_records_nothing_is_served(released: ApplyBench) -> None:
    refused(
        read(released, "list", workspace=released.root / "elsewhere"),
        "release_record_missing",
        "workspace",
    )
    receipt = _record(released, "batch_releases")
    saved = receipt.read_bytes()
    receipt.unlink()
    refused(read(released, "list"), "release_record_missing", "receipt")
    receipt.write_bytes(saved)
    receipt.chmod(0o600)
    confirmation = _record(released, "batch_confirmations")
    confirmation.rename(released.root / "moved.json")
    refused(read(released, "list"), "release_record_missing", "receipt")


def test_a_forged_or_mismatched_receipt_is_refused(released: ApplyBench) -> None:
    receipt = _record(released, "batch_releases")
    saved = receipt.read_text()
    receipt.write_text(saved.replace('"head_commit"', '"head_commit" ', 1))
    refused(read(released, "list"), "release_record_corrupt", receipt.stem)
    payload = json.loads(saved)
    payload["head_commit"] = "0" * 40
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    refused(read(released, "list"), "receipt_mismatch", "receipt")


def test_a_confirmation_naming_a_plan_that_is_gone_is_refused(released: ApplyBench) -> None:
    plan = _record(released, "batch_plans")
    plan.rename(released.root / "moved-plan.json")

    refused(read(released, "list"), "release_record_missing", plan.stem)


@pytest.mark.parametrize("folder", ["policies", "reports", "authorizations"])
def test_a_clearance_whose_cited_record_is_gone_is_refused(
    released: ApplyBench, folder: str
) -> None:
    (cited,) = released.records(folder)
    cited.rename(released.root / f"moved-{folder}.json")

    refused(read(released, "list"), "release_record_missing", cited.stem)


def test_a_clearance_rewritten_in_the_manifest_is_refused(released: ApplyBench) -> None:
    manifest = parse_any_manifest((released.clone / MANIFEST_PATH).read_bytes())
    first = manifest.documents[0]
    chapter = first.chapters[0]
    other = chapter.clearance.model_copy(update={"source_proof_sha256": "9" * 64})
    member = first.model_copy(
        update={"chapters": (chapter.model_copy(update={"clearance": other}), *first.chapters[1:])}
    )
    edited = manifest.model_copy(update={"documents": (member, *manifest.documents[1:])})
    edit_committed(released, MANIFEST_PATH, manifest_v2_text(edited).encode())

    refused(read(released, "list"), "receipt_mismatch", "chapters")


def test_two_confirmations_for_the_same_bytes_are_ambiguous(released: ApplyBench) -> None:
    confirmation = _record(released, "batch_confirmations")
    payload = json.loads(confirmation.read_text())
    payload["decided_at"] = "2100-04-10T12:31:00Z"
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    twin = confirmation.parent / f"{'a' * 64}.json"
    twin.write_text(text)
    twin.chmod(0o600)

    refused(read(released, "list"), "receipt_mismatch", "ambiguous")


# --- withdrawal and partial release ----------------------------------------------------------


def test_a_withdrawn_document_is_never_served_again(released: ApplyBench) -> None:
    original = (released.clone / PATH1).read_bytes()
    back_to_batch_branch(released)
    assert released.withdraw(DOC1, "2,3").exit_code == 0
    publish(released, "owner: withdraw")

    record = loaded(read(released, "list"))
    assert [item["document"]["id"] for item in record["documents"]] == [DOC2]
    for command in (["show"], ["provenance"], ["chapter", "--chapter", "2"]):
        refused(
            read(released, command[0], "--document", DOC1, *command[1:]),
            "document_not_released",
            "document",
        )
    assert git(released.clone, "show", f"HEAD~1:{PATH1}").encode() == original
    (released.clone / PATH1).write_bytes(original)
    refused(read(released, "list"), "checkout_dirty", "untracked")


def test_a_partial_release_carries_its_notice_and_serves_only_its_chapters(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    bench.batch.add(1, extras={3: TABLE})
    bench.batch.add(2, extras={3: QUOTE})
    bench.batch.ready(httpx_mock)
    request = bench.batch.request([bench.batch.document(1), bench.batch.document(2)])
    assert bench.apply(bench.planned(request), request).exit_code == 0
    publish(bench)

    record = loaded(read(bench, "show", "--document", DOC1))
    assert [item["chapter_number"] for item in record["chapters"]] == [2]
    assert record["partial_curated_excerpt"] is True
    refused(
        read(bench, "chapter", "--document", DOC1, "--chapter", "3"),
        "chapter_not_released",
        "chapter",
    )
    refused(read(bench, "show", "--document", "regjeringen-1"), "document_not_released", "document")


# --- isolation -------------------------------------------------------------------------------


def test_the_reader_is_reached_only_through_its_own_ops_command() -> None:
    import lovspor  # noqa: PLC0415

    source = Path(lovspor.__file__).parent
    importers = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if "release_reader" in path.read_text()
        and not path.relative_to(source).as_posix().startswith("forarbeider/")
    }
    assert importers == {"ops_cli.py"}
    os.environ.get("LOVVERK_CORPUS_PATH")


@pytest.mark.parametrize(
    ("line", "forged"),
    [
        ('source_provider: "regjeringen.no"', 'source_provider: "elsewhere"'),
        ("renderer_version: 1", "renderer_version: 2"),
        ('dataset: "forarbeider"', 'dataset: "lover"'),
    ],
)
def test_every_front_matter_field_must_restate_the_member(
    released: ApplyBench, line: str, forged: str
) -> None:
    """B2 regression (Codex round 1): a provider or other field the check skipped."""
    member = parse_any_manifest((released.clone / MANIFEST_PATH).read_bytes()).documents[0]
    markdown = (released.clone / PATH1).read_text()
    release_reader.require_restated(member, markdown)
    assert line in markdown

    with pytest.raises(ForarbeiderRefusedError) as caught:
        release_reader.require_restated(member, markdown.replace(line, forged, 1))
    assert caught.value.detail == "front_matter"
    with pytest.raises(ForarbeiderRefusedError):
        release_reader.require_restated(member, markdown.replace("---\n", "---\nextra: 1\n", 1))
