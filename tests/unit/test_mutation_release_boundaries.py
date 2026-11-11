"""Observable release guard boundaries, using real Git, files and validated plans."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.forarbeider import apply_preflight as preflight
from lovspor.forarbeider import release_checkout as checkout
from lovspor.forarbeider.batch_models import BatchPlan
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.release_commands import ReleaseScope
from lovspor.forarbeider.release_models import digest, record_text
from lovspor.git_read import GitReadError, git_environment, read_git
from tests.unit.forarbeider_checkout_support import BRANCH, git, release_clone
from tests.unit.test_forarbeider_batch_models import (
    POLICY,
    REVIEW,
    _chapter,
    _plan,
    _planned,
)


def test_absent_path_uses_the_platform_executable_search_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PATH", raising=False)
    assert git_environment(tmp_path)["PATH"] == os.defpath


def test_disallowed_arguments_report_only_the_verb(tmp_path: Path) -> None:
    with pytest.raises(GitReadError) as caught:
        read_git(tmp_path, ("push", "private-destination"))
    assert str(caught.value) == "git argument vector not allowed: ('push',)"


def test_missing_executable_reports_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(GitReadError) as caught:
        read_git(tmp_path, ("symbolic-ref", "--quiet", "HEAD"))
    assert str(caught.value) == "git symbolic-ref could not run"
    assert isinstance(caught.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize("kind", ["missing", "directory", "file", "broken_link", "loop"])
def test_canonical_checkout_checks_real_path_kinds(tmp_path: Path, kind: str) -> None:
    path = tmp_path.resolve() / "candidate"
    if kind == "directory":
        path.mkdir()
        assert checkout.canonical_checkout(path) == path
        return
    if kind == "file":
        path.write_text("not a directory")
    elif kind == "broken_link":
        path.symlink_to(tmp_path / "absent")
    elif kind == "loop":
        path.symlink_to(path)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        checkout.canonical_checkout(path)
    assert str(caught.value) == "checkout_unsafe: path"


def test_no_configured_corpus_means_no_extra_forbidden_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)
    assert [name for name, _ in checkout.forbidden_checkouts()] == ["engine", "sync_clone"]


def test_extensions_config_refusal_keeps_the_key() -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        checkout.require_safe_config({"extensions.worktreeconfig": ("true",)})
    assert str(caught.value) == "checkout_config_unsafe: extensions.worktreeconfig"


@pytest.mark.parametrize("extra", ["remote.origin.extra.url", "remote.origin.fetch"])
def test_dotted_remote_and_origin_without_url_are_refused(extra: str) -> None:
    config = {extra: ("unused",)}
    detail = "origin"
    if extra == "remote.origin.extra.url":
        config["remote.origin.url"] = ("https://github.com/bartoszkobylinski/lovverk.git",)
        detail = "remotes"
    with pytest.raises(ForarbeiderRefusedError) as caught:
        checkout.require_trusted_remote(config)
    assert str(caught.value) == f"checkout_remote_untrusted: {detail}"


def test_layout_refuses_a_path_alias_even_with_a_nonbare_repository(tmp_path: Path) -> None:
    clone = release_clone(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(clone)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        checkout.require_layout(alias)
    assert str(caught.value) == "checkout_alias: toplevel"


def test_layout_refuses_an_external_git_directory_even_when_common_matches(tmp_path: Path) -> None:
    clone = release_clone(tmp_path)
    external = clone.parent / "external-git"
    (clone / ".git").rename(external)
    (clone / ".git").write_text(f"gitdir: {external}\n")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        checkout.require_layout(clone)
    assert str(caught.value) == "checkout_alias: common_dir"


def test_layout_with_non_utf8_directory_reports_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Git can emit non-UTF-8 filesystem bytes on Linux. Supply that process
    # response at the executable boundary, also on filesystems that forbid them.
    executable = tmp_path / "git"
    executable.write_text("#!/bin/sh\nprintf '\\377\\n'\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ForarbeiderRefusedError) as caught:
        checkout.require_layout(tmp_path)
    assert str(caught.value) == "checkout_unverifiable: rev-parse"


def test_non_utf8_branch_reports_symbolic_ref(tmp_path: Path) -> None:
    clone = release_clone(tmp_path)
    (clone / ".git" / "HEAD").write_bytes(b"ref: refs/heads/bad-\xff\n")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        checkout.current_branch(clone)
    assert str(caught.value) == "checkout_unverifiable: symbolic-ref"


def test_git_commit_output_is_hex_even_for_non_ascii_commit_text(tmp_path: Path) -> None:
    clone = release_clone(tmp_path)
    git(clone, "commit", "--quiet", "--allow-empty", "-m", "Æøå")
    branch, commit = checkout.current_branch(clone)
    assert branch == BRANCH
    assert commit == git(clone, "rev-parse", "HEAD").strip()
    assert len(commit) == 40
    assert set(commit) <= set("0123456789abcdef")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        checkout.trusted_roots(clone)
    assert str(caught.value) == "checkout_identity_mismatch: root_commit"


@pytest.mark.parametrize(
    ("record", "fault"),
    [(b"u UU conflict", "index"), (b"2 R. renamed", "index"), (b"2 .M renamed", "worktree")],
)
def test_status_conflicts_and_both_rename_columns(record: bytes, fault: str) -> None:
    assert checkout.status_fault(record) == fault


def test_multiple_dirty_paths_still_report_the_first_fault(tmp_path: Path) -> None:
    clone = release_clone(tmp_path)
    (clone / "lover" / "README.md").write_text("changed")
    (clone / "untracked").write_text("new")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        checkout.require_clean(clone)
    assert str(caught.value) == "checkout_dirty: worktree"


@pytest.mark.parametrize("kind", ["policy", "review"])
def test_clearance_binding_errors_are_complete(kind: str) -> None:
    if kind == "policy":
        chapter = _chapter(1, 2, clearance={**POLICY, "policy_sha256": "9" * 64})
        document = _planned(1, [chapter])
        message = "a policy clearance names another policy or source proof"
    else:
        chapter = _chapter(1, 2, classification="flagged", clearance=REVIEW)
        document = {**_planned(1, [chapter]), "report_sha256": None}
        message = "a review clearance names another report"
    with pytest.raises(ValidationError) as caught:
        BatchPlan.model_validate(_plan([document]))
    assert caught.value.errors()[0]["msg"] == f"Value error, {message}"


def test_unsafe_workspace_refusal_names_its_role(tmp_path: Path) -> None:
    real = tmp_path / "private"
    real.write_text("a file is not a workspace")
    scope = ReleaseScope(workspace=real, ledger=tmp_path / "ledger")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        preflight.existing_workspace(scope)
    assert caught.value.detail == "workspace"
    assert str(tmp_path) not in str(caught.value)


def test_equal_policy_and_review_counts_still_include_chapters(tmp_path: Path) -> None:
    chapters = [_chapter(1, 2), _chapter(1, 3, classification="flagged", clearance=REVIEW)]
    plan = BatchPlan.model_validate(_plan([_planned(1, chapters)]))
    assert plan.counts.standing_policy == plan.counts.owner_review == 1
    text = record_text(plan)
    sha = digest(text.encode())
    folder = tmp_path / "batch_plans"
    folder.mkdir(mode=0o700)
    record = folder / f"{sha}.json"
    record.write_text(text)
    record.chmod(0o600)
    assert preflight.stored_plan(tmp_path, sha) == (sha, plan)


def test_binding_preserves_an_existing_manifest_digest() -> None:
    plan = BatchPlan.model_validate(_plan([_planned(1)]))
    state = checkout.CheckoutState(
        branch=plan.target_branch,
        head_commit="a" * 40,
        base_manifest_sha256="b" * 64,
        root_commits=("c" * 40,),
    )
    binding = preflight.binding_of("d" * 64, plan, state)
    assert binding.base_manifest_sha256 == "b" * 64
    absent = preflight.binding_of(
        "d" * 64, plan, state.model_copy(update={"base_manifest_sha256": None})
    )
    assert digest(record_text(binding).encode()) != digest(record_text(absent).encode())


def test_document_without_included_chapters_prints_a_dash() -> None:
    excluded = _chapter(2, 2, included=False, clearance=None, reasons=["page_not_fetched"])
    plan = BatchPlan.model_validate(_plan([_planned(1), _planned(2, [excluded])]))
    lines = preflight._document_lines(plan)
    assert lines[0].endswith("chapters 2,3")
    assert lines[1].endswith("coverage none chapters -")
