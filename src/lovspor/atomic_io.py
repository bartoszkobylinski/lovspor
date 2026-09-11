"""Atomic writes: fill a staging file this call made, then ``os.replace`` it in.

``os.replace`` is atomic when source and target share a filesystem, so a
reader (e.g. the MCP server reading ``manifest.json`` while a sync writes it)
never observes a half-written file — it sees either the complete previous
version or the complete new one. A crash mid-write leaves the original intact.

The staging file is created exclusively and written through its descriptor,
never reopened by name: the name is predictable — ``<name>.tmp`` beside the
target — and on the production droplet the writer is root, so a symlink
planted there was followed, its target truncated and chmod'd, and the link
itself then renamed onto the target so later reads went through it (#280).
"""

import os
import tempfile
from pathlib import Path

_STAGING_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
"""Create or step aside: a taken name — file, directory, symlink — is never opened.

``O_EXCL`` alone already refuses a symlink, dangling included; ``O_NOFOLLOW``
says it a second time, so an edit that ever relaxes the first does not
silently reopen the hole.
"""

_UMASK_DEFAULT = 0o666
"""What ``open`` hands the umask when the caller asked for no particular mode.

The creation mode ``Path.write_bytes`` used, kept so an unmoded write still
lands on exactly the mode it always did.
"""


def atomic_write_text(
    path: Path, text: str, *, encoding: str = "utf-8", mode: int | None = None
) -> None:
    """Write ``text`` to ``path`` atomically via a staging file beside it.

    Encoded here rather than by a text stream, so a codec that refuses the
    string refuses before anything has been created, and no newline
    translation stands between the caller's string and the bytes on disk.
    Every other guarantee is :func:`atomic_write_bytes`'s.
    """
    atomic_write_bytes(path, text.encode(encoding), mode=mode)


def atomic_write_bytes(path: Path, payload: bytes, *, mode: int | None = None) -> None:
    """Write ``payload`` to ``path`` atomically via a staging file beside it.

    Used by the observatory blob store, where the stored object is a captured
    HTTP body — arbitrary bytes that must never be decoded, normalised or
    re-encoded on the way to disk (ADR-0010 §2: raw bytes exactly as
    received).

    Parent directories are created if missing. The staging file lives in the
    same directory as ``path`` so ``replace`` stays on one filesystem. On any
    ``OSError`` it is removed and the error re-raised, so a failed write
    neither corrupts the target nor leaves behind the file this call made — a
    name it did not make, it neither opens nor removes. ``mode``, when given,
    is set on the staging file's descriptor before the rename, so the target
    is never observed with the umask's mode; ``None`` leaves the umask's,
    except on the stepped-aside path of :func:`_stage_beside`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged = _stage(path, mode)
    try:
        _fill(descriptor, payload, mode)
        staged.replace(path)
    except OSError:
        staged.unlink(missing_ok=True)
        raise


def _stage(path: Path, mode: int | None) -> tuple[int, Path]:
    """An empty file this call created beside ``path``, and its open descriptor.

    A taken staging name is stepped around rather than refused. Every writer
    in the repo stages here, so refusing would turn one stale ``.tmp`` — the
    one a ``kill -9`` leaves behind, which no cleanup of ours can tell from an
    attacker's — into a permanent outage for that path, and would hand anyone
    able to create a file in the directory a denial of service for the price
    of one symlink. Stepping aside keeps the write correct either way and
    leaves the foreign name on disk, where an operator can still see it.
    """
    staged = path.with_name(f"{path.name}.tmp")
    try:
        return os.open(staged, _STAGING_FLAGS, _UMASK_DEFAULT if mode is None else mode), staged
    except FileExistsError:
        return _stage_beside(path)


def _stage_beside(path: Path) -> tuple[int, Path]:
    """A unique name in the target's own directory, so ``replace`` stays on one filesystem.

    ``mkstemp`` creates owner-only and never consults the umask, so an unmoded
    write that had to step aside lands on ``0600`` rather than the umask's
    mode: the anomaly path errs closed. A caller that needs a particular mode
    passes one, and :func:`_fill` applies it to whichever file was staged.
    """
    descriptor, unique = tempfile.mkstemp(prefix=f"{path.name}.tmp.", dir=path.parent)
    return descriptor, Path(unique)


def _fill(descriptor: int, payload: bytes, mode: int | None) -> None:
    """Fill and mode the staging file through its descriptor alone.

    ``fchmod`` rather than ``Path.chmod``: the mode lands on the file this
    call created, which nothing at the name can redirect.
    """
    with os.fdopen(descriptor, "wb") as handle:
        if mode is not None:
            os.fchmod(handle.fileno(), mode)
        handle.write(payload)
