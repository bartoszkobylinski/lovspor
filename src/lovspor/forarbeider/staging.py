"""Where staging output may land: outside every Git repository.

Nothing here may reach ``lovverk`` or the engine before the publication gates
of ADR-0018, so any ancestor holding ``.git`` refuses, whichever repository it
is. A ``.git`` file counts too: that is what a worktree has.
"""

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason


def checked_output(raw: Path, forbidden: Sequence[Path]) -> Path:
    """The resolved output path, once it is proved safe to write.

    Args:
        raw: the path the operator gave.
        forbidden: trees refused even without a ``.git``, such as a corpus copy.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path``, before anything is written.
    """
    refused = ForarbeiderRefusedError(RefusalReason.UNSAFE_OUTPUT_PATH, str(raw))
    try:
        path = raw.expanduser()
        if not path.is_absolute() or path.suffix != ".json":
            raise refused
        path = path.resolve()
    except (OSError, RuntimeError) as error:
        raise refused from error
    if path.is_dir() or _inside_a_repository(path) or _inside_any(path, forbidden):
        raise refused
    return path


def write_observation(path: Path, text: str) -> bool:
    """Create ``path`` holding ``text``, owner-only, and never replace what is there.

    An observation is a record of what was seen, so a later one takes its own
    path. ``os.link`` is what makes that hold: it publishes the whole staged
    file at once and fails if the name is taken, which ``os.replace`` would not.

    Returns:
        ``True`` when written, ``False`` when ``path`` already holds exactly ``text``.

    Raises:
        ForarbeiderRefusedError: ``observation_exists``, or ``output_write_failed``.
    """
    payload = text.encode()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return _publish(path, payload)
    except OSError as error:
        raise ForarbeiderRefusedError(RefusalReason.OUTPUT_WRITE_FAILED, str(path)) from error


def _publish(path: Path, payload: bytes) -> bool:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.link(staged, path)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ForarbeiderRefusedError(RefusalReason.OBSERVATION_EXISTS, str(path)) from None
        return False
    finally:
        staged.unlink(missing_ok=True)
    return True


def _inside_a_repository(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in path.parents)


def _inside_any(path: Path, trees: Sequence[Path]) -> bool:
    return any(tree.resolve() in path.parents for tree in trees)
