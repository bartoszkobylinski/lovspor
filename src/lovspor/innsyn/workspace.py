"""The innsyn workspace: one operator-chosen directory, never a repository.

It holds the population snapshots, the recipient directory, the sender and
the request ledger. The path comes from ``--dir`` or ``LOVSPOR_INNSYN_DIR``
with no default: the ledger names mailboxes and holds correspondence
metadata, and a default inside a checkout or on the archive volume would put
that one forgotten setting away from a place it must not be. The engine
repository and the ``lovverk`` corpus are refused outright, as for the
observatory root (ADR-0010 §5).

The directory itself is made when missing, its parent never: a parent that
is not there is a volume that went away, and standing it up again would start
a second, partial ledger (the lesson of issue #534).
"""

from collections.abc import Sequence
from pathlib import Path

from lovspor.errors import ConfigError, StorageUnavailableError
from lovspor.observatory.storage import forbidden_trees

ENV_INNSYN_DIR = "LOVSPOR_INNSYN_DIR"

POPULATION_DIRNAME = "population"
DIRECTORY_FILENAME = "directory.jsonl"
REQUESTS_FILENAME = "requests.jsonl"
SENDER_FILENAME = "sender.json"


class InnsynWorkspace:
    """A directory that passed the boundary check; every innsyn file lives under it."""

    __slots__ = ("_path",)

    def __init__(self, raw: Path | None, forbidden: Sequence[Path]) -> None:
        """Validate ``raw``.

        Raises:
            ConfigError: unset, relative, or inside the engine repo or corpus.
        """
        if raw is None or not str(raw).strip():
            raise ConfigError(f"set --dir or {ENV_INNSYN_DIR}; the innsyn ledger has no default")
        candidate = raw.expanduser()
        if not candidate.is_absolute():
            raise ConfigError(f"the innsyn directory must be an absolute path, got {raw}")
        resolved = candidate.resolve()
        for tree in forbidden:
            inside = tree.resolve()
            if resolved == inside or inside in resolved.parents:
                raise ConfigError(f"the innsyn directory {resolved} is inside {inside}")
        self._path = resolved

    @property
    def path(self) -> Path:
        return self._path

    @property
    def population_dir(self) -> Path:
        return self._path / POPULATION_DIRNAME

    @property
    def directory_log(self) -> Path:
        return self._path / DIRECTORY_FILENAME

    @property
    def requests_log(self) -> Path:
        return self._path / REQUESTS_FILENAME

    @property
    def sender_path(self) -> Path:
        return self._path / SENDER_FILENAME

    def ensure(self, directory: Path | None = None) -> Path:
        """Make ``directory`` (default: the workspace) without ever making its parent.

        Raises:
            StorageUnavailableError: the parent is missing.
        """
        target = self._path if directory is None else directory
        if target != self._path:
            self.ensure()
        try:
            target.mkdir(exist_ok=True)
        except FileNotFoundError as exc:
            raise StorageUnavailableError(f"{target.parent} is missing; {target} not made") from exc
        return target


def innsyn_workspace(raw: Path | None) -> InnsynWorkspace:
    return InnsynWorkspace(raw, forbidden_trees())
