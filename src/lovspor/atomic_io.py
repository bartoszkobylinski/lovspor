"""Atomic text writes: write a sibling temp file, then ``os.replace`` it in.

``os.replace`` is atomic when source and target share a filesystem, so a
reader (e.g. the MCP server reading ``manifest.json`` while a sync writes it)
never observes a half-written file — it sees either the complete previous
version or the complete new one. A crash mid-write leaves the original intact
and no stray temp file behind.
"""

from pathlib import Path


def atomic_write_text(
    path: Path, text: str, *, encoding: str = "utf-8", mode: int | None = None
) -> None:
    """Write ``text`` to ``path`` atomically via a sibling temp file.

    Parent directories are created if missing. The temp file lives in the
    same directory as ``path`` so ``replace`` stays on one filesystem. On any
    ``OSError`` the temp file is removed and the error re-raised, so a failed
    write neither corrupts the target nor leaves a ``.tmp`` behind. ``mode``,
    when given, is set on the temp file before the rename, so the target is
    never observed with the umask's mode; ``None`` leaves the umask's.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        _replace(tmp, path, mode)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: Path, payload: bytes, *, mode: int | None = None) -> None:
    """Write ``payload`` to ``path`` atomically via a sibling temp file.

    Byte counterpart of :func:`atomic_write_text`, with the same guarantees.
    Used by the observatory blob store, where the stored object is a captured
    HTTP body — arbitrary bytes that must never be decoded, normalised or
    re-encoded on the way to disk (ADR-0010 §2: raw bytes exactly as
    received).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_bytes(payload)
        _replace(tmp, path, mode)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _replace(tmp: Path, path: Path, mode: int | None) -> None:
    if mode is not None:
        tmp.chmod(mode)
    tmp.replace(path)
