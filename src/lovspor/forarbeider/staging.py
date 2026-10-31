"""Where staging metadata may land: outside every Git repository.

Nothing here may reach ``lovverk`` or the engine before the publication gates
of ADR-0018, so any ancestor holding ``.git`` refuses, whichever repository it
is. A ``.git`` file counts too: that is what a worktree has.
"""

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
    path = raw.expanduser()
    if not path.is_absolute() or path.suffix != ".json":
        raise refused
    path = path.resolve()
    if path.is_dir() or _inside_a_repository(path) or _inside_any(path, forbidden):
        raise refused
    return path


def _inside_a_repository(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in path.parents)


def _inside_any(path: Path, trees: Sequence[Path]) -> bool:
    return any(tree.resolve() in path.parents for tree in trees)
