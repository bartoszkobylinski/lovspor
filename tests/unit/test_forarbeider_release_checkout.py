"""The release-checkout guard B1 apply runs first (#738, B1a).

Real Git on scratch clones in ``tmp_path``; no network, no real corpus. Each
refusal is asserted by its typed reason and fixed detail code, and every
case, a pass included, leaves the whole tree, ``.git`` included, byte for
byte and mtime for mtime as it found it. The guard never creates a clone.
"""

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from lovspor import git_read
from lovspor.forarbeider import release_checkout, release_commands
from lovspor.forarbeider.corpus_models import EMPTY_MANIFEST, manifest_text
from lovspor.forarbeider.corpus_render import render_index
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.release_checkout import (
    TRUSTED_ROOT_COMMITS,
    CheckoutState,
    require_changes,
    status_path,
    verify_checkout,
)
from lovspor.forarbeider.release_lock import HeldLock, release_lock
from lovspor.forarbeider.release_models import digest
from lovspor.git_read import GitReadError, GitReading
from tests.unit.forarbeider_checkout_support import (
    BRANCH,
    commit_all,
    git,
    release_clone,
    root_commit,
    snapshot,
    trust,
)

ORIGIN = "remote.origin.url"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The sync clone path and the configured corpus path point nowhere real."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)


@pytest.fixture
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    made = release_clone(tmp_path / "work")
    trust(monkeypatch, made)
    return made


def refused(
    raw: Path, reason: str, detail: str, *, branch: str = BRANCH, tree: Path | None = None
) -> None:
    """``raw`` is refused with exactly this reason and detail, and nothing changed."""
    watched = tree or raw.parent
    before = snapshot(watched)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        verify_checkout(raw, branch)
    assert (caught.value.reason.value, caught.value.detail) == (reason, detail)
    assert snapshot(watched) == before


def passes(raw: Path, branch: str = BRANCH) -> CheckoutState:
    before = snapshot(raw.parent)
    state = verify_checkout(raw, branch)
    assert snapshot(raw.parent) == before
    return state


# --- the pass --------------------------------------------------------------------------------


def test_the_pinned_root_is_the_lovverk_initial_commit() -> None:
    assert frozenset({"27aef25110812552319a83228b65aed9af44bfd5"}) == TRUSTED_ROOT_COMMITS


def test_a_clean_release_clone_on_the_plan_branch_passes(clone: Path) -> None:
    state = passes(clone)
    assert state.branch == BRANCH
    assert state.head_commit == git(clone, "rev-parse", "HEAD").strip()
    assert state.root_commits == (root_commit(clone),)
    assert state.base_manifest_sha256 is None


def test_an_existing_dataset_is_read_and_its_manifest_digest_kept(clone: Path) -> None:
    manifest = manifest_text(EMPTY_MANIFEST)
    (clone / "forarbeider").mkdir()
    (clone / "forarbeider" / "manifest.json").write_text(manifest)
    (clone / "forarbeider" / "INDEX.md").write_text(render_index(EMPTY_MANIFEST))
    commit_all(clone, "empty dataset")
    assert passes(clone).base_manifest_sha256 == digest(manifest.encode())


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/bartoszkobylinski/lovverk.git",
        "https://github.com/bartoszkobylinski/lovverk",
        "git@github.com:bartoszkobylinski/lovverk.git",
        "ssh://git@github.com/bartoszkobylinski/lovverk",
    ],
)
def test_every_trusted_remote_form_is_accepted(clone: Path, url: str) -> None:
    git(clone, "remote", "set-url", "origin", url)
    passes(clone)


def test_a_trusted_pushurl_is_accepted(clone: Path) -> None:
    git(clone, "config", "remote.origin.pushurl", "git@github.com:bartoszkobylinski/lovverk.git")
    passes(clone)


# --- the path --------------------------------------------------------------------------------


def test_a_symlinked_checkout_is_refused(clone: Path, tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(clone)
    refused(link, "checkout_unsafe", "path", tree=tmp_path)


def test_a_checkout_below_a_symlinked_parent_is_refused(tmp_path: Path) -> None:
    real = release_clone(tmp_path / "real")
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    refused(tmp_path / "alias" / real.name, "checkout_unsafe", "path", tree=tmp_path)


def test_a_relative_path_is_refused(clone: Path) -> None:
    refused(Path(clone.name), "checkout_unsafe", "path", tree=clone.parent)


def test_a_path_with_dot_dot_is_refused(clone: Path) -> None:
    refused(clone / "lover" / "..", "checkout_unsafe", "path", tree=clone.parent)


def test_a_missing_path_is_refused_and_never_created(tmp_path: Path) -> None:
    missing = tmp_path / "nothing-here"
    refused(missing, "checkout_unsafe", "path", tree=tmp_path)
    assert not missing.exists()


def test_a_file_is_not_a_checkout(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("x")
    refused(tmp_path / "file", "checkout_unsafe", "path", tree=tmp_path)


def test_a_checkout_others_can_write_is_refused(clone: Path) -> None:
    clone.chmod(0o775)
    refused(clone, "checkout_unsafe", "path")


def test_a_parent_others_can_write_is_refused(clone: Path) -> None:
    clone.parent.chmod(0o777)
    try:
        refused(clone, "checkout_unsafe", "parent")
    finally:
        clone.parent.chmod(0o700)


def test_a_subdirectory_is_not_the_checkout(clone: Path) -> None:
    refused(clone / "lover", "checkout_alias", "git_dir", tree=clone.parent)


# --- forbidden trees -------------------------------------------------------------------------


def test_the_engine_checkout_is_refused_before_any_git_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_process(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("git ran on the engine")

    monkeypatch.setattr(git_read.subprocess, "run", no_process)
    engine = Path(release_commands.__file__).resolve().parents[3]
    with pytest.raises(ForarbeiderRefusedError) as caught:
        verify_checkout(engine, BRANCH)
    assert (caught.value.reason.value, caught.value.detail) == ("checkout_forbidden", "engine")


def test_the_sync_clone_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sync = release_clone(tmp_path / "xdg", name="lovverk")
    trust(monkeypatch, sync)
    refused(sync, "checkout_forbidden", "sync_clone")


def test_the_configured_corpus_path_is_refused(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOVVERK_CORPUS_PATH", str(clone))
    refused(clone, "checkout_forbidden", "corpus_path")


def test_a_checkout_inside_the_configured_corpus_is_refused(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOVVERK_CORPUS_PATH", str(clone.parent))
    refused(clone, "checkout_forbidden", "corpus_path")


def test_a_checkout_holding_the_sync_path_is_refused_though_it_does_not_exist(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(clone))
    assert not (clone / "lovverk").exists()
    refused(clone, "checkout_forbidden", "sync_clone")


def test_a_letter_case_alias_of_the_sync_clone_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync = release_clone(tmp_path / "xdg", name="lovverk")
    alias = sync.parent / "LOVVERK"
    if not alias.exists():
        pytest.skip("case-sensitive file system: no letter-case alias exists")
    trust(monkeypatch, sync)
    refused(alias, "checkout_forbidden", "sync_clone", tree=sync.parent)


# --- aliases of another repository -----------------------------------------------------------


def test_a_linked_worktree_is_refused(clone: Path, tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    git(clone, "worktree", "add", "--quiet", "-b", "forarbeider/linked", str(linked))
    refused(linked, "checkout_alias", "git_dir", branch="forarbeider/linked", tree=tmp_path)


def test_borrowed_objects_are_refused(clone: Path, tmp_path: Path) -> None:
    other = release_clone(tmp_path / "other")
    alternates = clone / ".git" / "objects" / "info" / "alternates"
    alternates.write_text(f"{other / '.git' / 'objects'}\n")
    refused(clone, "checkout_alias", "alternates", tree=tmp_path)


def test_an_inherited_git_environment_cannot_redirect_the_guard(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dirty = release_clone(tmp_path / "dirty")
    (dirty / "lover" / "README.md").write_text("changed\n")
    monkeypatch.setenv("GIT_DIR", str(dirty / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(dirty))
    passes(clone)
    monkeypatch.setenv("GIT_DIR", str(clone / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(clone))
    trust(monkeypatch, dirty)
    refused(dirty, "checkout_dirty", "worktree", tree=tmp_path)


# --- configuration and remote ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value", "detail"),
    [
        ("filter.lfs.clean", "cat", "filter"),
        ("core.fsmonitor", "true", "core.fsmonitor"),
        ("core.worktree", "/elsewhere", "core.worktree"),
        ("core.sparseCheckout", "true", "core.sparsecheckout"),
        ("core.hooksPath", "/elsewhere", "core.hookspath"),
        ("include.path", "/elsewhere/config", "include"),
        ("includeIf.gitdir:/x/.path", "/elsewhere/config", "includeif"),
        ("url.https://mirror.invalid/.insteadOf", "https://github.com/", "url"),
        ("url.https://mirror.invalid/.pushInsteadOf", "https://github.com/", "url"),
    ],
)
def test_unsafe_local_configuration_is_refused(
    clone: Path, key: str, value: str, detail: str
) -> None:
    git(clone, "config", key, value)
    refused(clone, "checkout_config_unsafe", detail)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/someone/lovverk.git",
        "https://github.com/bartoszkobylinski/lovspor.git",
        "https://github.com/bartoszkobylinski/lovverk.git/",
        "https://gitlab.com/bartoszkobylinski/lovverk.git",
        "http://github.com/bartoszkobylinski/lovverk.git",
        "file:///srv/lovverk",
    ],
)
def test_an_untrusted_origin_is_refused(clone: Path, url: str) -> None:
    git(clone, "remote", "set-url", "origin", url)
    refused(clone, "checkout_remote_untrusted", "origin")


def test_no_origin_is_refused(clone: Path) -> None:
    git(clone, "remote", "remove", "origin")
    refused(clone, "checkout_remote_untrusted", "origin")


def test_two_urls_on_origin_are_refused(clone: Path) -> None:
    git(clone, "config", "--add", ORIGIN, "git@github.com:bartoszkobylinski/lovverk.git")
    refused(clone, "checkout_remote_untrusted", "origin")


def test_a_second_remote_is_refused(clone: Path) -> None:
    git(clone, "remote", "add", "upstream", "https://github.com/bartoszkobylinski/lovverk.git")
    refused(clone, "checkout_remote_untrusted", "remotes")


def test_a_top_level_remote_setting_is_not_a_second_remote(clone: Path) -> None:
    git(clone, "config", "remote.pushDefault", "origin")
    passes(clone)


def test_a_foreign_pushurl_is_refused(clone: Path) -> None:
    git(clone, "config", "remote.origin.pushurl", "https://example.invalid/x.git")
    refused(clone, "checkout_remote_untrusted", "pushurl")


# --- identity --------------------------------------------------------------------------------


def test_an_unknown_root_commit_is_refused(tmp_path: Path) -> None:
    stranger = release_clone(tmp_path / "work")
    refused(stranger, "checkout_identity_mismatch", "root_commit")


def test_a_clone_without_the_lovverk_shape_is_refused(clone: Path) -> None:
    git(clone, "rm", "-r", "--quiet", "lover")
    git(clone, "commit", "--quiet", "-m", "no lover")
    refused(clone, "checkout_identity_mismatch", "structure")


def test_a_symlinked_root_manifest_is_refused(clone: Path) -> None:
    (clone / "manifest.json").unlink()
    (clone / "manifest.json").symlink_to(clone / "lover" / "README.md")
    commit_all(clone, "link")
    refused(clone, "checkout_identity_mismatch", "structure")


def test_a_shallow_clone_is_refused(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(clone, "commit", "--quiet", "--allow-empty", "-m", "second")
    shallow = tmp_path / "shallow-base" / "shallow"
    shallow.parent.mkdir(mode=0o700)
    git(tmp_path, "clone", "--quiet", "--depth", "1", f"file://{clone}", str(shallow))
    git(shallow, "remote", "set-url", "origin", "https://github.com/bartoszkobylinski/lovverk.git")
    trust(monkeypatch, clone)
    refused(shallow, "checkout_identity_mismatch", "shallow")


# --- branch ----------------------------------------------------------------------------------


def test_main_is_refused(clone: Path) -> None:
    git(clone, "switch", "--quiet", "main")
    refused(clone, "checkout_branch_mismatch", "branch")


def test_another_forarbeider_branch_is_refused(clone: Path) -> None:
    git(clone, "switch", "--quiet", "-c", "forarbeider/other")
    refused(clone, "checkout_branch_mismatch", "branch")


def test_a_detached_head_is_refused(clone: Path) -> None:
    git(clone, "checkout", "--quiet", "--detach")
    refused(clone, "checkout_branch_mismatch", "detached")


def test_an_unborn_branch_is_refused(clone: Path) -> None:
    git(clone, "switch", "--quiet", "--orphan", "forarbeider/unborn")
    refused(clone, "checkout_branch_mismatch", "unborn", branch="forarbeider/unborn")


# --- operations in progress ------------------------------------------------------------------


def _diverged(clone: Path) -> None:
    """``side`` and the batch branch each change ``lover/README.md`` differently."""
    git(clone, "switch", "--quiet", "-c", "side")
    (clone / "lover" / "README.md").write_text("side\n")
    commit_all(clone, "side")
    git(clone, "switch", "--quiet", BRANCH)
    (clone / "lover" / "README.md").write_text("batch\n")
    commit_all(clone, "batch")


def _merge(clone: Path) -> None:
    git(clone, "merge", "--quiet", "side", check=False)


def _cherry_pick(clone: Path) -> None:
    git(clone, "cherry-pick", "side", check=False)


def _rebase(clone: Path) -> None:
    git(clone, "rebase", "side", check=False)


def _revert(clone: Path) -> None:
    (clone / "lover" / "README.md").write_text("later\n")
    commit_all(clone, "later")
    git(clone, "revert", "--no-edit", "HEAD~1", check=False)


def _am(clone: Path) -> None:
    patch = git(clone, "format-patch", "-1", "side", "--stdout")
    (clone.parent / "side.patch").write_text(patch)
    git(clone, "am", str(clone.parent / "side.patch"), check=False)


def _bisect(clone: Path) -> None:
    git(clone, "bisect", "start")


@pytest.mark.parametrize(
    ("operation", "detail"),
    [
        (_merge, "merge"),
        (_cherry_pick, "cherry_pick"),
        (_rebase, "rebase"),
        (_revert, "revert"),
        (_am, "am"),
        (_bisect, "bisect"),
    ],
)
def test_a_real_operation_in_progress_is_refused(clone: Path, operation: Any, detail: str) -> None:
    _diverged(clone)
    operation(clone)
    refused(clone, "checkout_operation_in_progress", detail)


@pytest.mark.parametrize(
    ("marker", "directory", "detail"),
    [
        ("MERGE_HEAD", False, "merge"),
        ("CHERRY_PICK_HEAD", False, "cherry_pick"),
        ("REVERT_HEAD", False, "revert"),
        ("BISECT_LOG", False, "bisect"),
        ("BISECT_START", False, "bisect"),
        ("rebase-merge", True, "rebase"),
        ("rebase-apply", True, "rebase"),
        ("sequencer", True, "sequencer"),
    ],
)
def test_every_operation_marker_is_refused_alone(
    clone: Path, marker: str, directory: bool, detail: str
) -> None:
    path = clone / ".git" / marker
    if directory:
        path.mkdir()
    else:
        path.write_text(git(clone, "rev-parse", "HEAD"))
    refused(clone, "checkout_operation_in_progress", detail)


@pytest.mark.parametrize(
    ("name", "detail"),
    [
        ("lovspor-forarbeider.lock", "lock"),
        ("lovspor-forarbeider-journal.json", "journal"),
        ("lovspor-forarbeider-staging", "staging"),
    ],
)
def test_an_unfinished_forarbeider_change_is_refused(clone: Path, name: str, detail: str) -> None:
    (clone / ".git" / name).write_text("{}")
    refused(clone, "checkout_pending_change", detail)


# --- cleanliness -----------------------------------------------------------------------------


def test_an_unstaged_edit_is_refused(clone: Path) -> None:
    (clone / "lover" / "README.md").write_text("edited\n")
    refused(clone, "checkout_dirty", "worktree")


def test_a_staged_edit_is_refused(clone: Path) -> None:
    (clone / "lover" / "README.md").write_text("edited\n")
    git(clone, "add", "lover/README.md")
    refused(clone, "checkout_dirty", "index")


def test_a_staged_new_file_is_refused(clone: Path) -> None:
    (clone / "forskrifter" / "new.md").write_text("new\n")
    git(clone, "add", "forskrifter/new.md")
    refused(clone, "checkout_dirty", "index")


def test_a_deleted_tracked_file_is_refused(clone: Path) -> None:
    (clone / "forskrifter" / "README.md").unlink()
    refused(clone, "checkout_dirty", "worktree")


def test_an_untracked_file_is_refused(clone: Path) -> None:
    (clone / "stray.txt").write_text("x\n")
    refused(clone, "checkout_dirty", "untracked")


def test_an_ignored_file_inside_the_dataset_is_refused(clone: Path) -> None:
    (clone / ".gitignore").write_text("forarbeider/junk.md\n")
    commit_all(clone, "ignore")
    (clone / "forarbeider").mkdir()
    (clone / "forarbeider" / "junk.md").write_text("junk\n")
    refused(clone, "checkout_dirty", "ignored_dataset")


def test_an_ignored_file_outside_the_dataset_is_allowed(clone: Path) -> None:
    (clone / ".gitignore").write_text(".DS_Store\n")
    commit_all(clone, "ignore")
    (clone / ".DS_Store").write_text("x")
    passes(clone)


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_an_edit_hidden_from_status_by_an_index_flag_is_refused(clone: Path, flag: str) -> None:
    git(clone, "update-index", flag, "manifest.json")
    (clone / "manifest.json").write_text("{}\n")
    assert git(clone, "status", "--porcelain") == ""
    refused(clone, "checkout_dirty", "hidden_flag")


def test_a_dataset_that_is_not_its_manifest_is_refused(clone: Path) -> None:
    (clone / "forarbeider").mkdir()
    (clone / "forarbeider" / "stray.md").write_text("x\n")
    commit_all(clone, "stray")
    refused(clone, "corpus_tree_mismatch", "forarbeider/manifest.json")


# --- unverifiable git ------------------------------------------------------------------------


def _answering(monkeypatch: pytest.MonkeyPatch, argv0: str, reading: GitReading | None) -> None:
    real = release_checkout.read_git

    def fake(checkout: Path, argv: tuple[str, ...]) -> GitReading:
        if argv[0] != argv0:
            return real(checkout, argv)
        if reading is None:
            raise GitReadError("timed out")
        return reading

    monkeypatch.setattr(release_checkout, "read_git", fake)


@pytest.mark.parametrize(
    ("argv0", "reading"),
    [
        ("status", GitReading(code=128, out=b"")),
        ("status", GitReading(code=0, out=b"# branch.oid x\x00")),
        ("status", None),
        ("config", GitReading(code=0, out=b"\xff\xfe\x00")),
        ("rev-parse", GitReading(code=0, out=b"/one/line\n")),
        ("symbolic-ref", GitReading(code=128, out=b"")),
        ("symbolic-ref", GitReading(code=0, out=b"refs/tags/v1\n")),
        ("rev-list", GitReading(code=0, out=b"")),
    ],
)
def test_git_that_fails_or_answers_unexpectedly_is_unverifiable(
    clone: Path, monkeypatch: pytest.MonkeyPatch, argv0: str, reading: GitReading | None
) -> None:
    _answering(monkeypatch, argv0, reading)
    expected = ("checkout_unverifiable", argv0)
    if argv0 == "rev-list":
        expected = ("checkout_identity_mismatch", "root_commit")
    refused(clone, *expected)


def test_a_head_that_is_not_a_commit_digest_is_unverifiable(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = release_checkout.read_git

    def fake(checkout: Path, argv: tuple[str, ...]) -> GitReading:
        if argv[:2] == ("rev-parse", "--verify"):
            return GitReading(code=0, out=b"not-a-digest\n")
        return real(checkout, argv)

    monkeypatch.setattr(release_checkout, "read_git", fake)
    refused(clone, "checkout_unverifiable", "rev-parse")


def test_the_guard_runs_only_allowlisted_git(clone: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, ...]] = []
    real = release_checkout.read_git

    def record(checkout: Path, argv: tuple[str, ...]) -> GitReading:
        seen.append(argv)
        return real(checkout, argv)

    monkeypatch.setattr(release_checkout, "read_git", record)
    passes(clone)
    assert seen
    assert set(seen) <= git_read.ALLOWED


def _status(uid: int, mode: int) -> os.stat_result:
    return os.stat_result((stat.S_IFDIR | mode, 0, 0, 0, uid, 0, 0, 0, 0, 0))


@pytest.mark.parametrize(
    ("uid", "mode", "unsafe"),
    [
        ("me", 0o755, False),
        ("me", 0o700, False),
        ("me", 0o775, True),
        ("me", 0o757, True),
        ("root", 0o755, False),
        ("root", stat.S_ISVTX | 0o777, False),
        ("root", 0o777, True),
        ("other", 0o755, True),
        ("other", stat.S_ISVTX | 0o777, True),
    ],
)
def test_a_parent_is_unsafe_when_someone_else_could_rename_the_checkout(
    uid: str, mode: int, unsafe: bool
) -> None:
    owners = {"me": os.geteuid(), "root": 0, "other": os.geteuid() + 4242}
    assert release_checkout._unsafe_parent(_status(owners[uid], mode)) is unsafe


@pytest.mark.parametrize(
    "answer",
    [
        GitReading(code=0, out=b"git version 2.31.9\n"),
        GitReading(code=0, out=b"git version 1.9.0\n"),
        GitReading(code=0, out=b"not a version\n"),
        GitReading(code=0, out=b"git version two\n"),
        GitReading(code=0, out=b"\xff\xfe\n"),
        GitReading(code=129, out=b""),
    ],
)
def test_a_git_too_old_or_unreadable_to_ignore_global_config_is_refused(
    clone: Path, monkeypatch: pytest.MonkeyPatch, answer: GitReading
) -> None:
    """Below 2.32 ``GIT_CONFIG_GLOBAL`` is silently ignored: the guard fails closed (#738)."""
    _answering(monkeypatch, "version", answer)
    refused(clone, "checkout_unverifiable", "git_version")


def test_a_failing_version_command_is_refused(clone: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _answering(monkeypatch, "version", None)
    refused(clone, "checkout_unverifiable", "git_version")


@pytest.mark.parametrize(
    "answer",
    [
        b"git version 2.32.0\n",
        b"git version 2.45.2\n",
        b"git version 2.39.5 (Apple Git-154)\n",
        b"git version 2.45.2.windows.1\n",
        b"git version 3.0\n",
    ],
)
def test_a_git_new_enough_is_accepted(
    clone: Path, monkeypatch: pytest.MonkeyPatch, answer: bytes
) -> None:
    _answering(monkeypatch, "version", GitReading(code=0, out=answer))
    passes(clone)


def test_the_version_is_asked_before_any_other_git(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, ...]] = []
    real = release_checkout.read_git

    def record(checkout: Path, argv: tuple[str, ...]) -> GitReading:
        seen.append(argv)
        return real(checkout, argv)

    monkeypatch.setattr(release_checkout, "read_git", record)
    passes(clone)
    assert seen[0] == ("version",)


def _foreign_root(clone: Path) -> tuple[str, str]:
    """Point the batch branch at an unrelated root commit with the trusted root's tree."""
    trusted = root_commit(clone)
    tree = git(clone, "rev-parse", f"{trusted}^{{tree}}").strip()
    foreign = git(clone, "commit-tree", tree, "-m", "foreign root").strip()
    git(clone, "update-ref", f"refs/heads/{BRANCH}", foreign)
    return foreign, trusted


@pytest.mark.parametrize("override", ["replace", "graft"])
def test_local_history_overrides_cannot_disguise_a_foreign_root(clone: Path, override: str) -> None:
    """Codex, PR #875: replace objects or grafts must not make a foreign root look trusted."""
    foreign, trusted = _foreign_root(clone)
    if override == "replace":
        tree = git(clone, "rev-parse", f"{foreign}^{{tree}}").strip()
        disguise = git(clone, "commit-tree", tree, "-p", trusted, "-m", "disguise").strip()
        git(clone, "replace", foreign, disguise)
    else:
        (clone / ".git" / "info" / "grafts").write_text(f"{foreign} {trusted}\n")
    assert root_commit(clone) == trusted
    refused(clone, "checkout_identity_mismatch", "root_commit")


def test_a_graft_file_on_a_genuine_history_is_refused(clone: Path) -> None:
    head = git(clone, "rev-parse", "HEAD").strip()
    (clone / ".git" / "info" / "grafts").write_text(f"{head}\n")
    refused(clone, "checkout_unsafe", "grafts")


def test_a_replace_ref_on_a_genuine_history_is_refused(clone: Path) -> None:
    head = git(clone, "rev-parse", "HEAD").strip()
    tree = git(clone, "rev-parse", "HEAD^{tree}").strip()
    twin = git(clone, "commit-tree", tree, "-p", head, "-m", "twin").strip()
    git(clone, "replace", twin, head)
    refused(clone, "checkout_unsafe", "replace_refs")


def test_a_merge_with_both_trusted_and_foreign_roots_is_refused(clone: Path) -> None:
    """Every history root must be trusted; finding one trusted root is insufficient."""
    trusted = root_commit(clone)
    head = git(clone, "rev-parse", "HEAD").strip()
    tree = git(clone, "rev-parse", "HEAD^{tree}").strip()
    foreign = git(clone, "commit-tree", tree, "-m", "unrelated root").strip()
    merged = git(
        clone, "commit-tree", tree, "-p", head, "-p", foreign, "-m", "merge unrelated history"
    ).strip()
    git(clone, "update-ref", f"refs/heads/{BRANCH}", merged)
    assert set(git(clone, "rev-list", "--max-parents=0", "HEAD").split()) == {trusted, foreign}
    assert git(clone, "status", "--porcelain") == ""

    refused(clone, "checkout_identity_mismatch", "root_commit")


# --- B1b regressions -------------------------------------------------------------------------


def test_a_real_worktree_config_extension_is_refused_though_its_file_hides_a_hook(
    clone: Path,
) -> None:
    """``config --local`` never lists ``.git/config.worktree``; the extension alone must refuse.

    With ``extensions.worktreeConfig`` on, Git reads a second, per-worktree file
    the guard's ``config --local --list`` does not show, so a hooks path or a
    filter written there would run unseen. The guard refuses the extension in a
    real clone, whatever that file holds.
    """
    git(clone, "config", "extensions.worktreeConfig", "true")
    git(clone, "config", "--worktree", "core.hooksPath", "/elsewhere")
    assert (clone / ".git" / "config.worktree").exists()
    assert "hookspath" not in git(clone, "config", "--local", "--list").lower()

    refused(clone, "checkout_config_unsafe", "extensions.worktreeconfig")


# --- B1b: the caller's own lock and the paths it just wrote ----------------------------------


def _dataset(clone: Path) -> None:
    """An empty v1 dataset committed, so a later write can modify tracked files."""
    (clone / "forarbeider").mkdir()
    (clone / "forarbeider" / "manifest.json").write_text(manifest_text(EMPTY_MANIFEST))
    (clone / "forarbeider" / "INDEX.md").write_text(render_index(EMPTY_MANIFEST))
    commit_all(clone, "empty dataset")


def test_the_lock_the_caller_holds_is_accepted_and_no_other(clone: Path) -> None:

    with release_lock(clone) as held:
        state = verify_checkout(clone, BRANCH, held)
        assert state.branch == BRANCH
        with pytest.raises(ForarbeiderRefusedError) as caught:
            verify_checkout(clone, BRANCH)
        assert caught.value.detail == "lock"
        with pytest.raises(ForarbeiderRefusedError) as caught:
            verify_checkout(clone, BRANCH, HeldLock(held.device, held.inode + 1))
        assert caught.value.detail == "lock"


def test_a_held_lock_never_excuses_a_journal_or_staging(clone: Path) -> None:

    for name, detail in (
        ("lovspor-forarbeider-journal.json", "journal"),
        ("lovspor-forarbeider-staging", "staging"),
    ):
        (clone / ".git" / name).write_text("{}")
        with release_lock(clone) as held, pytest.raises(ForarbeiderRefusedError) as caught:
            verify_checkout(clone, BRANCH, held)
        assert (caught.value.reason.value, caught.value.detail) == (
            "checkout_pending_change",
            detail,
        )
        (clone / ".git" / name).unlink()


def test_exactly_the_written_paths_are_accepted(clone: Path) -> None:
    _dataset(clone)
    (clone / "forarbeider" / "INDEX.md").write_text("changed\n")
    new = clone / "forarbeider" / "2099-2100" / "prop-l"
    new.mkdir(parents=True)
    (new / "9000101.md").write_text("new\n")
    written = frozenset({"forarbeider/INDEX.md", "forarbeider/2099-2100/prop-l/9000101.md"})

    require_changes(clone, written)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        require_changes(clone, written - {"forarbeider/INDEX.md"})
    assert (caught.value.reason.value, caught.value.detail) == ("checkout_dirty", "worktree")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        require_changes(clone, written | {"forarbeider/manifest.json"})
    assert (caught.value.reason.value, caught.value.detail) == ("corpus_tree_mismatch", "status")


def test_a_deleted_tracked_file_counts_as_written_but_a_staged_change_never(clone: Path) -> None:

    _dataset(clone)
    (clone / "forarbeider" / "INDEX.md").unlink()
    require_changes(clone, frozenset({"forarbeider/INDEX.md"}))
    git(clone, "add", "-A")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        require_changes(clone, frozenset({"forarbeider/INDEX.md"}))
    assert (caught.value.reason.value, caught.value.detail) == ("checkout_dirty", "index")


def test_a_write_outside_the_named_paths_is_dirty(clone: Path) -> None:

    _dataset(clone)
    (clone / "lover" / "README.md").write_text("touched\n")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        require_changes(clone, frozenset({"lover/README.md"} - {"lover/README.md"}))
    assert caught.value.detail == "worktree"


@pytest.mark.parametrize(
    ("record", "path"),
    [
        (b"? forarbeider/a b.md", "forarbeider/a b.md"),
        (b"1 .M N... 100644 100644 100644 " + b"0" * 40 + b" " + b"0" * 40 + b" f/x y", "f/x y"),
        (b"1 .D N... 100644 100644 000000 " + b"0" * 40 + b" " + b"0" * 40 + b" f/z", "f/z"),
        (b"1 M. N... 100644 100644 100644 " + b"0" * 40 + b" " + b"0" * 40 + b" f/z", None),
        (b"1 .A N... 100644 100644 100644 " + b"0" * 40 + b" " + b"0" * 40 + b" f/z", None),
        (b"2 .M N... 100644 100644 100644 h h R100 f/z", None),
        (b"u UU N... 1 2 3 4 h h h f/z", None),
        (b"? \xff", None),
        (b"1 .M short", None),
    ],
)
def test_only_unstaged_changes_and_new_files_name_a_path(record: bytes, path: str | None) -> None:

    assert status_path(record) == path
