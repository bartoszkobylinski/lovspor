"""URL continuity inside the release transaction, fail-closed (#820).

A release becomes live only through ``control.commit_release`` (and the
crash-table resolution ``reconcile --complete``). Both now ask, before
anything public moves, whether the candidate answers every URL the live
release serves: :func:`lovspor.publish.check.check_continuity` over the two
``corpus/`` trees, the same check ``publish-check --previous`` runs.

Which release is live is never a directory listing: it is the reconciled
triple's release, R = D = M.active, read under :func:`transaction_lock`.
Its trees must still hash to its own ``release_content_id`` before they
are compared, so a damaged or truncated previous tree is a refusal, never a
comparison against fewer URLs.

*No* live release is a bootstrap only when the triple proves it: no marker,
and R and D naming no release. A marker that is unreadable, or a marker
whose release Caddy does not serve, never reaches here — ``read_triple``
and ``situation`` refuse those first.

The lock is an exclusive ``flock(2)`` on the releases root itself: no file
is created, the build user cannot swap the inode out from under it (the
root's parent is not theirs), and it is independent of the wrapper's own
``/run/lock/lovspor-publish.lock``, so a command run by hand outside
``publish-release.sh`` still cannot interleave with another.
"""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from lovspor.publish.check import ContinuityReport, check_continuity
from lovspor.publish.inventory import PublishError
from lovspor.release.envelope import CORPUS_DIR, SITE_DIR, is_complete, release_dir
from lovspor.release.errors import ContinuityRefusedError, TransactionLockedError
from lovspor.site.fingerprint import release_content_id


@contextmanager
def transaction_lock(releases: Path) -> Iterator[None]:
    """Hold the releases root exclusively, or refuse at once; never wait."""
    try:
        descriptor = os.open(releases, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as error:
        raise TransactionLockedError(f"cannot open {releases} to lock it: {error}") from error
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise TransactionLockedError(
                f"another release transaction holds {releases}; nothing was changed, "
                "retry once it has finished"
            ) from error
        yield
    finally:
        os.close(descriptor)


def _require_intact(releases: Path, live: str) -> Path:
    """The live release's corpus tree, once its trees still hash to its id."""
    previous = release_dir(releases, live)
    if not is_complete(previous):
        raise ContinuityRefusedError(
            f"the live release {live[:12]} is not a complete envelope at {previous}; "
            "URL continuity cannot be checked, so nothing is committed"
        )
    try:
        found = release_content_id(previous / CORPUS_DIR, previous / SITE_DIR)
    except OSError as error:
        raise ContinuityRefusedError(
            f"the live release {live[:12]} is unreadable: {error}; nothing is committed"
        ) from error
    if found != live:
        raise ContinuityRefusedError(
            f"the live release's trees hash to {found[:12]}, not {live[:12]}: it was "
            "changed after it went live; URL continuity cannot be checked, nothing is committed"
        )
    return previous / CORPUS_DIR


def require_continuity(releases: Path, live: str | None, candidate: str) -> ContinuityReport | None:
    """``candidate`` answers every URL ``live`` serves; ``None`` on a proven bootstrap."""
    if live is None:
        return None
    previous = _require_intact(releases, live)
    try:
        return check_continuity(previous, release_dir(releases, candidate) / CORPUS_DIR)
    except (PublishError, OSError) as error:
        raise ContinuityRefusedError(
            f"release {candidate[:12]} not committed: against the live release {live[:12]}, "
            f"{error}. Publish a release that keeps the URL live, 301s it to a live page "
            "or retires it with 410 (docs/url-contract.md §4)"
        ) from error
