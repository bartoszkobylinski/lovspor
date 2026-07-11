"""Atomic text writes: write a sibling temp file, then ``os.replace`` it in.

``os.replace`` is atomic when source and target share a filesystem, so a
reader (e.g. the MCP server reading ``manifest.json`` while a sync writes it)
never observes a half-written file — it sees either the complete previous
version or the complete new one. A crash mid-write leaves the original intact
and no stray temp file behind.
"""

from pathlib import Path


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically via a sibling temp file.

    Parent directories are created if missing. The temp file lives in the
    same directory as ``path`` so ``replace`` stays on one filesystem. On any
    ``OSError`` the temp file is removed and the error re-raised, so a failed
    write neither corrupts the target nor leaves a ``.tmp`` behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
