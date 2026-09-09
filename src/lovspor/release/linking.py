"""The ``--link-dest`` pass, in-process (ADR-0013 Decision 8, ADR-0014 Decision 6 step 1).

``publish-release.sh`` used to populate a release with ``rsync --checksum
--link-dest=<live>``: a file whose bytes did not change becomes a hard
link to the live release's inode and so keeps its old mtime, which is
what keeps Caddy's ``ETag``/``Last-Modified`` truthful per page instead
of telling every crawler the whole site changed. The same pass here,
under the temporary build name and never into the final one: every
regular file of the build whose bytes equal the same path in the live
release is replaced — by link-then-rename, never in place — with a hard
link to that file.

From this pass on, a build file may share its inode with the live
release. Every later write in the build directory is therefore a
replace (``atomic_io``), which is why the id-carrying files of step 4
are written that way.
"""

import hashlib
import os
from pathlib import Path

from lovspor.release.errors import ReleaseError


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _same_bytes(path: Path, twin: Path) -> bool:
    if not twin.is_file() or twin.is_symlink():
        return False
    if path.stat().st_size != twin.stat().st_size:
        return False
    return _sha256(path) == _sha256(twin)


def _relink(path: Path, twin: Path) -> None:
    """``path`` becomes a hard link to ``twin`` by link-then-rename."""
    temporary = path.with_name(f"{path.name}.link")
    try:
        os.link(twin, temporary)
        temporary.replace(path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ReleaseError(f"cannot hard-link {path} to the live release: {error}") from error


def hardlink_unchanged(build: Path, live: Path) -> int:
    """Link every unchanged regular file of ``build`` to its twin in ``live``; count them."""
    linked = 0
    for path in sorted(build.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        twin = live / path.relative_to(build)
        if _same_bytes(path, twin):
            _relink(path, twin)
            linked += 1
    return linked
