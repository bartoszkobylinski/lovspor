"""Where observatory data lives — and, more importantly, where it must not.

ADR-0010 §5 puts raw observed artifacts and the observation log outside the
engine repository and outside the public ``lovverk`` corpus. The reason is not
tidiness: observed material is evidence that bytes were retrievable, not law,
and a published repository is exactly where that distinction would quietly
disappear. Redistribution of any of it waits on a per-source licensing basis
that does not exist yet.

The boundary is a check, not a convention. The root comes from
``LOVSPOR_OBSERVATORY_ROOT`` with no in-repo default, because a default under
``data/`` would put the whole archive one forgotten env var away from the
thing the ADR forbids.
"""

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from lovspor.errors import ConfigError, StorageBoundaryError

ENV_OBSERVATORY_ROOT = "LOVSPOR_OBSERVATORY_ROOT"


def _repository_root(start: Path) -> Path:
    """Return the repository containing ``start``, or its package directory.

    A checkout has ``.git`` as a directory and a linked worktree has it as a
    file, so existence — not ``is_dir`` — is the test. An installed package
    has neither anywhere above it; there the package directory itself is the
    tree that observatory data must stay out of.
    """
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start.parent


def engine_root() -> Path:
    """Locate the engine tree this module was loaded from."""
    return _repository_root(Path(__file__).resolve().parent)


def resolve_root(raw: str | None, forbidden: Sequence[Path]) -> Path:
    """Validate a configured observatory root against the storage boundary.

    Raises:
        ConfigError: the value is unset, empty, or not an absolute path. A
            relative root would resolve against whatever working directory the
            process happened to start in — including a repository checkout.
        StorageBoundaryError: the root is, or sits inside, a forbidden tree.
    """
    if raw is None or not raw.strip():
        raise ConfigError(
            f"{ENV_OBSERVATORY_ROOT} is not set; ADR-0010 §5 requires an explicit path"
        )
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ConfigError(f"{ENV_OBSERVATORY_ROOT} must be an absolute path, got {raw!r}")
    root = candidate.resolve()
    for tree in forbidden:
        resolved = tree.resolve()
        if root == resolved or resolved in root.parents:
            raise StorageBoundaryError(
                f"observatory root {root} is inside {resolved}; ADR-0010 §5 keeps observed "
                "material outside the engine repository and the lovverk corpus",
            )
    return root


def observatory_root(env: Mapping[str, str] | None = None, corpus_root: Path | None = None) -> Path:
    """Resolve the observatory root from the environment.

    ``corpus_root`` is the local ``lovverk`` checkout when the caller knows it;
    passing it extends the boundary check to the corpus repository, which is
    the second tree ADR-0010 §5 names.
    """
    environment = os.environ if env is None else env
    forbidden = [engine_root()] if corpus_root is None else [engine_root(), corpus_root]
    return resolve_root(environment.get(ENV_OBSERVATORY_ROOT), forbidden)
