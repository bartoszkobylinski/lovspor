"""The release-checkout guard a batch apply runs first (#738, contract Amendment 1 point 5, B1a).

Apply writes only ``forarbeider/`` in a **dedicated lovverk release clone**,
on the plan's non-``main`` branch, and never the engine, the sync clone or
the corpus ``lovspor mcp`` reads. This module proves a path is such a clone
before anything may be written there, and refuses everything it cannot prove:

- the path is absolute and already its own canonical form, with no symlink on
  the way, owned by the operator and writable by nobody else, and so are its
  parents (a root-owned sticky directory such as ``/tmp`` excepted);
- it is not, inside, or around the engine checkout, the sync clone
  (``default_corpus_path()``) or ``$LOVVERK_CORPUS_PATH``, compared by device
  and inode when they exist (a letter-case or firmlink alias is the same
  directory) and by canonical path when they do not;
- ``.git`` is its own directory: no linked worktree, gitfile or borrowed
  objects (``alternates``), so it shares nothing with another clone;
- its local configuration runs nothing and aliases nothing (no filter,
  fsmonitor, hooks path, include, worktree, sparse checkout or URL rewrite),
  and its one remote is ``origin`` at github.com/bartoszkobylinski/lovverk;
- Git agrees it is a full, non-bare, non-shallow repository rooted here, and
  its history starts at the lovverk root commit;
- no merge, rebase, cherry-pick, revert, bisect, ``am`` or sequencer is in
  progress, and no forarbeider change is unfinished; the one lock accepted is
  the one the caller holds (B1b), proved by device and inode;
- HEAD is the plan's branch, born, never detached;
- index and worktree are clean, nothing is untracked, no index flag hides an
  edit, nothing is ignored inside ``forarbeider/``, and the dataset there is
  exactly what its manifest lists. A caller that just wrote names its paths
  (B1b): then status must list exactly those, each an unstaged change or a new
  untracked file, and nothing else.

Git is run only through :mod:`lovspor.git_read`: read-only commands, no
optional locks, a minimal environment. Nothing here writes, creates a clone
or reaches the network. Refusals carry fixed codes, never a path.
"""

import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider.corpus_models import MANIFEST_PATH
from lovspor.forarbeider.corpus_transaction import JOURNAL, LOCK, STAGING, ReadBack
from lovspor.forarbeider.corpus_tree import read_any_dataset_files
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_commands import forbidden_trees
from lovspor.forarbeider.release_lock import HeldLock, held_by_us
from lovspor.git_read import GitReadError, GitReading, read_git

#: lovverk's initial commit, "chore: initialize corpus repository" (2026-04-22, no parent).
#: Read by the session lead on 2026-11-11 from the last page of the GitHub API history of
#: bartoszkobylinski/lovverk ``main`` and from ``git rev-list --max-parents=0 HEAD`` in a local
#: clone. A remote URL is only configuration text; the root commit is content-addressed history.
TRUSTED_ROOT_COMMITS: frozenset[str] = frozenset({"27aef25110812552319a83228b65aed9af44bfd5"})
TRUSTED_REMOTE = re.compile(
    r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"bartoszkobylinski/lovverk(?:\.git)?"
)
#: Local configuration that runs a command or points Git somewhere else.
UNSAFE_CONFIG = re.compile(
    r"filter\..*|include\..*|includeif\..*|url\..*\.(?:push)?insteadof"
    r"|core\.(?:fsmonitor|worktree|sparsecheckout|hookspath)|extensions\.worktreeconfig"
)
#: Git's own markers of an operation in progress, in the order they are reported.
OPERATIONS: tuple[tuple[str, str], ...] = (
    ("rebase-apply/applying", "am"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry_pick"),
    ("REVERT_HEAD", "revert"),
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("BISECT_LOG", "bisect"),
    ("BISECT_START", "bisect"),
    ("sequencer", "sequencer"),
)
PENDING: tuple[tuple[str, str], ...] = ((LOCK, "lock"), (JOURNAL, "journal"), (STAGING, "staging"))
#: ``remote.<name>.<key>``: a top-level ``remote.pushDefault`` names no remote.
_REMOTE_KEY_DOTS = 2
#: ``GIT_CONFIG_GLOBAL`` arrived in Git 2.32 (``--path-format=absolute`` in 2.31). An older Git
#: ignores it without a word and reads the operator's global config, so it is refused.
MIN_GIT_VERSION = (2, 32)
_GIT_VERSION = re.compile(r"git version (\d+)\.(\d+)(?:\.\d+)?(?:[ .].*)?")
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
#: ``1 XY sub mH mI mW hH hI path``: an ordinary changed entry, the path last.
_ORDINARY_FIELDS = 9
_LAYOUT = (
    "rev-parse",
    "--path-format=absolute",
    "--show-toplevel",
    "--absolute-git-dir",
    "--git-common-dir",
    "--is-shallow-repository",
    "--is-bare-repository",
)
_STATUS = ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored=no")
_IGNORED = ("ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", "forarbeider")


class CheckoutState(BaseModel):
    """A checkout the guard accepted: what a later write is bound to. Never its path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    branch: str
    head_commit: str
    base_manifest_sha256: str | None
    root_commits: tuple[str, ...]


def _refused(reason: RefusalReason, detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(reason, detail)


def _shared(status: os.stat_result) -> bool:
    """Owned by someone else, or writable by group or others."""
    return status.st_uid != os.geteuid() or bool(stat.S_IMODE(status.st_mode) & 0o022)


def _unsafe_parent(status: os.stat_result) -> bool:
    """A parent someone else could rename the checkout out of; root's sticky ``/tmp`` is fine."""
    if status.st_uid not in (0, os.geteuid()):
        return True
    sticky_root = status.st_uid == 0 and bool(status.st_mode & stat.S_ISVTX)
    return bool(stat.S_IMODE(status.st_mode) & 0o022) and not sticky_root


def canonical_checkout(raw: Path) -> Path:
    """An absolute existing directory that is already its own canonical form.

    Raises:
        ForarbeiderRefusedError: ``checkout_unsafe: path``; nothing is created.
    """
    try:
        if not raw.is_absolute() or raw.resolve(strict=True) != raw:
            raise _refused(RefusalReason.CHECKOUT_UNSAFE, "path")
        status = os.lstat(raw)
    except (OSError, RuntimeError) as error:
        raise _refused(RefusalReason.CHECKOUT_UNSAFE, "path") from error
    if not stat.S_ISDIR(status.st_mode):
        raise _refused(RefusalReason.CHECKOUT_UNSAFE, "path")
    return raw


def require_private(path: Path) -> None:
    """Raises: ForarbeiderRefusedError: ``checkout_unsafe: path`` or ``parent``."""
    if _shared(os.lstat(path)):
        raise _refused(RefusalReason.CHECKOUT_UNSAFE, "path")
    if any(_unsafe_parent(os.lstat(parent)) for parent in path.parents):
        raise _refused(RefusalReason.CHECKOUT_UNSAFE, "parent")


def forbidden_checkouts() -> tuple[tuple[str, Path], ...]:
    """The engine, the sync clone and the configured corpus, each with the code it is refused by."""
    engine, sync = forbidden_trees()
    found = [("engine", engine), ("sync_clone", sync)]
    configured = os.environ.get("LOVVERK_CORPUS_PATH", "")
    if configured:
        found.append(("corpus_path", Path(configured).expanduser()))
    return tuple(found)


def _identity(path: Path) -> tuple[int, int]:
    status = path.stat()
    return status.st_dev, status.st_ino


def _identities(path: Path) -> set[tuple[int, int]]:
    """Device and inode of ``path`` and of every directory above it."""
    return {_identity(item) for item in (path, *path.parents)}


def _overlaps(path: Path, root: Path) -> bool:
    """``path`` is ``root``, inside it or around it: by inode, or by canonical path when absent."""
    try:
        target = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return root.resolve().is_relative_to(path)
    return _identity(target) in _identities(path) or _identity(path) in _identities(target)


def require_not_forbidden(path: Path) -> None:
    """Raises: ForarbeiderRefusedError: ``checkout_forbidden: engine|sync_clone|corpus_path``."""
    for code, root in forbidden_checkouts():
        if _overlaps(path, root):
            raise _refused(RefusalReason.CHECKOUT_FORBIDDEN, code)


def require_git_version(path: Path) -> None:
    """A Git new enough that ``lovspor.git_read``'s isolated environment holds.

    Raises:
        ForarbeiderRefusedError: ``checkout_unverifiable: git_version``: unreadable or too old.
    """
    try:
        reading = read_git(path, ("version",))
        match = _GIT_VERSION.fullmatch(reading.out.decode("utf-8").strip())
    except (GitReadError, UnicodeDecodeError) as error:
        raise _refused(RefusalReason.CHECKOUT_UNVERIFIABLE, "git_version") from error
    if reading.code != 0 or match is None or (int(match[1]), int(match[2])) < MIN_GIT_VERSION:
        raise _refused(RefusalReason.CHECKOUT_UNVERIFIABLE, "git_version")


def require_own_git_dir(path: Path) -> None:
    """Raises: ForarbeiderRefusedError: ``checkout_alias: git_dir|alternates``."""
    try:
        status = os.lstat(path / ".git")
    except OSError as error:
        raise _refused(RefusalReason.CHECKOUT_ALIAS, "git_dir") from error
    if not stat.S_ISDIR(status.st_mode):
        raise _refused(RefusalReason.CHECKOUT_ALIAS, "git_dir")
    if os.path.lexists(path / ".git" / "objects" / "info" / "alternates"):
        raise _refused(RefusalReason.CHECKOUT_ALIAS, "alternates")


def _read(path: Path, argv: tuple[str, ...]) -> GitReading:
    try:
        return read_git(path, argv)
    except GitReadError as error:
        raise _refused(RefusalReason.CHECKOUT_UNVERIFIABLE, argv[0]) from error


def _git(path: Path, argv: tuple[str, ...]) -> bytes:
    """The output of a command that must succeed.

    Raises:
        ForarbeiderRefusedError: ``checkout_unverifiable: <command>``.
    """
    reading = _read(path, argv)
    if reading.code != 0:
        raise _refused(RefusalReason.CHECKOUT_UNVERIFIABLE, argv[0])
    return reading.out


def _text(out: bytes, command: str) -> str:
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _refused(RefusalReason.CHECKOUT_UNVERIFIABLE, command) from error


def local_config(path: Path) -> dict[str, tuple[str, ...]]:
    """The checkout's own configuration, every value of every key, in file order."""
    found: dict[str, list[str]] = {}
    raw = _git(path, ("config", "--local", "--list", "-z"))
    for entry in _text(raw, "config").split("\0"):
        if entry:
            key, _, value = entry.partition("\n")
            found.setdefault(key, []).append(value)
    return {key: tuple(values) for key, values in found.items()}


def require_safe_config(config: Mapping[str, tuple[str, ...]]) -> None:
    """Raises: ForarbeiderRefusedError: ``checkout_config_unsafe: <key class>``."""
    for key in config:
        if UNSAFE_CONFIG.fullmatch(key):
            detail = key if key.startswith(("core.", "extensions.")) else key.split(".")[0]
            raise _refused(RefusalReason.CHECKOUT_CONFIG_UNSAFE, detail)


def require_trusted_remote(config: Mapping[str, tuple[str, ...]]) -> None:
    """Exactly one remote, ``origin``, with one trusted URL and no other push URL.

    Raises:
        ForarbeiderRefusedError: ``checkout_remote_untrusted: origin|remotes|pushurl``.
    """
    names = {
        key.removeprefix("remote.").rsplit(".", 1)[0]
        for key in config
        if key.startswith("remote.") and key.count(".") >= _REMOTE_KEY_DOTS
    }
    urls = config.get("remote.origin.url", ())
    if "origin" not in names or len(urls) != 1 or not TRUSTED_REMOTE.fullmatch(urls[0]):
        raise _refused(RefusalReason.CHECKOUT_REMOTE_UNTRUSTED, "origin")
    if names != {"origin"}:
        raise _refused(RefusalReason.CHECKOUT_REMOTE_UNTRUSTED, "remotes")
    if not all(TRUSTED_REMOTE.fullmatch(url) for url in config.get("remote.origin.pushurl", ())):
        raise _refused(RefusalReason.CHECKOUT_REMOTE_UNTRUSTED, "pushurl")


def require_layout(path: Path) -> None:
    """Git agrees: rooted here, its own Git directory, not bare, full history.

    Raises:
        ForarbeiderRefusedError: ``checkout_alias: toplevel|common_dir``,
            ``checkout_identity_mismatch: shallow`` or ``checkout_unverifiable: rev-parse``.
    """
    lines = _text(_git(path, _LAYOUT), "rev-parse").splitlines()
    if len(lines) != len(_LAYOUT) - 2:
        raise _refused(RefusalReason.CHECKOUT_UNVERIFIABLE, "rev-parse")
    top, git_dir, common, shallow, bare = lines
    if top != str(path) or bare != "false":
        raise _refused(RefusalReason.CHECKOUT_ALIAS, "toplevel")
    if git_dir != common or git_dir != str(path / ".git"):
        raise _refused(RefusalReason.CHECKOUT_ALIAS, "common_dir")
    if shallow != "false":
        raise _refused(RefusalReason.CHECKOUT_IDENTITY_MISMATCH, "shallow")


def require_no_git_operation(path: Path) -> None:
    """Raises: ForarbeiderRefusedError: ``checkout_operation_in_progress: <kind>``."""
    for marker, kind in OPERATIONS:
        if os.path.lexists(path / ".git" / marker):
            raise _refused(RefusalReason.CHECKOUT_OPERATION_IN_PROGRESS, kind)


def require_no_operation(path: Path, held: HeldLock | None = None) -> None:
    """No Git operation in progress and no unfinished forarbeider change.

    The lock is accepted only when it is the very file ``held`` names: the
    caller's own, taken before this check, never one another run left.

    Raises:
        ForarbeiderRefusedError: ``checkout_operation_in_progress: <kind>`` or
            ``checkout_pending_change: lock|journal|staging``.
    """
    require_no_git_operation(path)
    for relative, kind in PENDING:
        ours = kind == "lock" and held is not None and held_by_us(path, held)
        if os.path.lexists(path / relative) and not ours:
            raise _refused(RefusalReason.CHECKOUT_PENDING_CHANGE, kind)


def current_branch(path: Path) -> tuple[str, str]:
    """The branch HEAD names and the commit it points at.

    Raises:
        ForarbeiderRefusedError: ``checkout_branch_mismatch: detached|unborn`` or
            ``checkout_unverifiable``.
    """
    reading = _read(path, ("symbolic-ref", "--quiet", "HEAD"))
    if reading.code == 1:
        raise _refused(RefusalReason.CHECKOUT_BRANCH_MISMATCH, "detached")
    ref = _text(reading.out, "symbolic-ref").removesuffix("\n")
    if reading.code != 0 or not ref.startswith("refs/heads/"):
        raise _refused(RefusalReason.CHECKOUT_UNVERIFIABLE, "symbolic-ref")
    head = _read(path, ("rev-parse", "--verify", "--quiet", "HEAD^{commit}"))
    if head.code != 0:
        raise _refused(RefusalReason.CHECKOUT_BRANCH_MISMATCH, "unborn")
    commit = _text(head.out, "rev-parse").removesuffix("\n")
    if not _COMMIT.fullmatch(commit):
        raise _refused(RefusalReason.CHECKOUT_UNVERIFIABLE, "rev-parse")
    return ref.removeprefix("refs/heads/"), commit


def require_structure(path: Path) -> None:
    """The lovverk root shape, read without following a link.

    Raises:
        ForarbeiderRefusedError: ``checkout_identity_mismatch: structure``.
    """
    shape = (
        ("manifest.json", stat.S_ISREG),
        ("lover", stat.S_ISDIR),
        ("forskrifter", stat.S_ISDIR),
    )
    for name, kind in shape:
        try:
            mode = os.lstat(path / name).st_mode
        except OSError as error:
            raise _refused(RefusalReason.CHECKOUT_IDENTITY_MISMATCH, "structure") from error
        if not kind(mode):
            raise _refused(RefusalReason.CHECKOUT_IDENTITY_MISMATCH, "structure")


def trusted_roots(path: Path) -> tuple[str, ...]:
    """Every root commit of HEAD's history, each one the lovverk root.

    Raises:
        ForarbeiderRefusedError: ``checkout_identity_mismatch: root_commit``.
    """
    lines = _text(_git(path, ("rev-list", "--max-parents=0", "HEAD")), "rev-list").split()
    if not lines or not set(lines) <= TRUSTED_ROOT_COMMITS:
        raise _refused(RefusalReason.CHECKOUT_IDENTITY_MISMATCH, "root_commit")
    return tuple(sorted(lines))


def require_no_history_overrides(path: Path) -> None:
    """No graft file and no replace ref: local rewrites of what a commit's history is.

    Git is run with both ignored, so they cannot disguise the root commit; a clone
    carrying either is still not one whose history a release should be written on.

    Raises:
        ForarbeiderRefusedError: ``checkout_unsafe: grafts|replace_refs``.
    """
    if os.path.lexists(path / ".git" / "info" / "grafts"):
        raise _refused(RefusalReason.CHECKOUT_UNSAFE, "grafts")
    if _git(path, ("for-each-ref", "--format=%(refname)", "refs/replace")).strip():
        raise _refused(RefusalReason.CHECKOUT_UNSAFE, "replace_refs")


def status_fault(record: bytes) -> str:
    """What one ``status --porcelain=v2 -z`` record says is not clean.

    Raises:
        ForarbeiderRefusedError: ``checkout_unverifiable: status`` for a record
            of no known kind.
    """
    kind = record[:1]
    if kind == b"?":
        return "untracked"
    if kind == b"u" or (kind in (b"1", b"2") and record[2:3] != b"."):
        return "index"
    if kind in (b"1", b"2"):
        return "worktree"
    raise _refused(RefusalReason.CHECKOUT_UNVERIFIABLE, "status")


def status_path(record: bytes) -> str | None:
    """The path of a record that may be a just-written dataset file, ``None`` for any other.

    Only an unstaged change to a tracked file (``1 .M``, ``1 .D``) or an
    untracked file qualifies: a staged change, a rename or a conflict never does.
    """
    fields = record.split(b" ", 8)
    if record[:2] == b"? ":
        raw = record[2:]
    elif fields[0] == b"1" and len(fields) == _ORDINARY_FIELDS and fields[1] in (b".M", b".D"):
        raw = fields[8]
    else:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def require_changes(path: Path, changed: frozenset[str]) -> None:
    """Status lists exactly ``changed``, each an unstaged change or a new file; nothing else.

    Raises:
        ForarbeiderRefusedError: ``checkout_dirty: <kind>`` for any other record,
            ``corpus_tree_mismatch: status`` when a named path is not changed.
    """
    seen: set[str] = set()
    for record in (item for item in _git(path, _STATUS).split(b"\0") if item):
        found = status_path(record)
        if found is None or found not in changed:
            raise _refused(RefusalReason.CHECKOUT_DIRTY, status_fault(record))
        seen.add(found)
    if seen != changed:
        raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, "status")


def require_clean(path: Path, changed: frozenset[str] = frozenset()) -> None:
    """Clean index and worktree but ``changed``, nothing hidden or ignored in the dataset.

    Raises:
        ForarbeiderRefusedError: ``checkout_dirty: index|worktree|untracked|hidden_flag|``
            ``ignored_dataset``, or ``corpus_tree_mismatch: status``.
    """
    require_changes(path, changed)
    for entry in _git(path, ("ls-files", "-v", "-z")).split(b"\0"):
        tag = entry[:1]
        if tag.islower() or tag == b"S":
            raise _refused(RefusalReason.CHECKOUT_DIRTY, "hidden_flag")
    if _git(path, _IGNORED):
        raise _refused(RefusalReason.CHECKOUT_DIRTY, "ignored_dataset")


def _trusted(path: Path) -> None:
    """A lovverk clone of its own, run by nothing its configuration names, branch aside."""
    require_own_git_dir(path)
    require_git_version(path)
    config = local_config(path)
    require_safe_config(config)
    require_trusted_remote(config)
    require_layout(path)


def _repository(path: Path, held: HeldLock | None) -> None:
    """Everything that makes ``path`` a trustworthy, idle lovverk clone, branch aside."""
    _trusted(path)
    require_no_operation(path, held)


def _on_branch(path: Path, target_branch: str) -> tuple[str, tuple[str, ...]]:
    """HEAD's commit and the root commits, refused unless HEAD is ``target_branch`` of lovverk.

    Raises:
        ForarbeiderRefusedError: ``checkout_branch_mismatch``, ``checkout_identity_mismatch``
            or ``checkout_unsafe: grafts|replace_refs``.
    """
    branch, head = current_branch(path)
    if branch != target_branch:
        raise _refused(RefusalReason.CHECKOUT_BRANCH_MISMATCH, "branch")
    require_structure(path)
    roots = trusted_roots(path)
    require_no_history_overrides(path)
    return head, roots


def verify_identity(raw: Path, target_branch: str, held: HeldLock) -> str:
    """The guard for recovery: every identity check, but a journal and its changes are expected.

    The caller holds the lock it took over. A Git operation in progress still
    refuses; the dataset and the status are what the journal settles, so they
    are proved by the read-back after it, not here. Returns HEAD's commit.

    Raises:
        ForarbeiderRefusedError: the first identity condition it cannot prove.
    """
    path = canonical_checkout(raw)
    require_not_forbidden(path)
    require_private(path)
    _trusted(path)
    require_no_git_operation(path)
    if not held_by_us(path, held):
        raise _refused(RefusalReason.CHECKOUT_PENDING_CHANGE, "lock")
    return _on_branch(path, target_branch)[0]


def require_published(path: Path, head: str) -> None:
    """HEAD is the commit ``origin/main`` pointed at when the clone last fetched.

    Read from the local remote-tracking ref, without the network: it proves the
    release is committed and was on lovverk ``main`` at the last fetch, never
    that the remote holds it now.

    Raises:
        ForarbeiderRefusedError: ``release_not_published: origin_main``.
    """
    reading = _read(path, ("rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}"))
    published = _text(reading.out, "rev-parse").removesuffix("\n")
    if reading.code != 0 or published != head:
        raise _refused(RefusalReason.RELEASE_NOT_PUBLISHED, "origin_main")


def read_back_of(branch: str, head: str, held: HeldLock) -> ReadBack:
    """The release clone's read-back after a change, run before its journal is cleared.

    Status lists exactly the paths written, HEAD has not moved, the lock is
    still ours, and the dataset is exactly the manifest ``expected``'s.
    """

    def read_back(root: Path, expected: str | None, changed: frozenset[str]) -> None:
        require_changes(root, changed)
        if current_branch(root) != (branch, head):
            raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, "head")
        if not held_by_us(root, held):
            raise _refused(RefusalReason.CORPUS_LOCKED, "checkout")
        if read_any_dataset_files(root)[0].manifest_sha256 != expected:
            raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, MANIFEST_PATH)

    return read_back


def lockable_checkout(raw: Path) -> Path:
    """A path safe to make the lock in: canonical, not forbidden, private, its own ``.git``.

    Raises:
        ForarbeiderRefusedError: the guard's code for the first that fails.
    """
    path = canonical_checkout(raw)
    require_not_forbidden(path)
    require_private(path)
    require_own_git_dir(path)
    return path


def verify_checkout(
    raw: Path,
    target_branch: str,
    held: HeldLock | None = None,
    changed: frozenset[str] = frozenset(),
) -> CheckoutState:
    """Prove ``raw`` is a dedicated, clean lovverk release clone on ``target_branch``.

    ``held`` is the caller's own lock, the one lock accepted; ``changed`` the
    dataset files the caller just wrote, the only changes accepted.

    Raises:
        ForarbeiderRefusedError: the first condition it cannot prove, with a fixed code.
    """
    path = canonical_checkout(raw)
    require_not_forbidden(path)
    require_private(path)
    _repository(path, held)
    head, roots = _on_branch(path, target_branch)
    require_clean(path, changed)
    dataset, _ = read_any_dataset_files(path)
    return CheckoutState(
        branch=target_branch,
        head_commit=head,
        base_manifest_sha256=dataset.manifest_sha256,
        root_commits=roots,
    )
