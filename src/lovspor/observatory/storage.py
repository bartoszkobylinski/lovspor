"""Where observatory data lives — and, more importantly, where it must not.

ADR-0010 §5 puts raw observed artifacts and the observation log outside the
engine repository and outside the public ``lovverk`` corpus. The reason is not
tidiness: observed material is evidence that bytes were retrievable, not law,
and a published repository is exactly where that distinction would quietly
disappear. Redistribution of any of it waits on a per-source licensing basis
that does not exist yet.

The boundary is a type, not a convention. :class:`ObservatoryRoot` validates
in its constructor, so an instance cannot exist without having passed the
check, and everything that writes observatory data takes one — a bare
``Path`` will not type-check and will not be accepted at runtime. The root
comes from ``LOVSPOR_OBSERVATORY_ROOT`` with no in-repo default, because a
default under ``data/`` would leave the archive one forgotten env var away
from the thing the ADR forbids.
"""

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from lovspor.errors import ConfigError, StorageBoundaryError

ENV_OBSERVATORY_ROOT = "LOVSPOR_OBSERVATORY_ROOT"
ENV_CORPUS_ROOT = "LOVSPOR_OUTPUT_REPO_PATH"


def _repository_root(start: Path, ceiling: Path | None = None) -> Path:
    """Return the repository containing ``start``, or its package directory.

    A checkout has ``.git`` as a directory and a linked worktree has it as a
    file, so existence — not ``is_dir`` — is the test. An installed package
    may have neither anywhere above it; there the package directory itself is
    the tree that observatory data must stay out of.

    ``ceiling`` bounds the upward walk at a known directory. Production passes
    none and searches to the filesystem root, which is the conservative
    direction: finding an enclosing repository can only ever *widen* what the
    boundary refuses.
    """
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
        if ceiling is not None and candidate == ceiling:
            break
    return start.parent


def engine_root() -> Path:
    """Locate the engine tree this module was loaded from."""
    return _repository_root(Path(__file__).resolve().parent)


class ObservatoryRoot:
    """A directory that has passed the ADR-0010 §5 storage-boundary check.

    Validation happens in ``__init__``, so there is no such thing as an
    unvalidated instance: code that writes observed material asks for this
    type and therefore cannot be handed a path that was never checked. That
    is the difference between a boundary and a docstring.
    """

    __slots__ = ("_path",)

    def __init__(self, raw: str | Path | None, forbidden: Sequence[Path]) -> None:
        """Validate ``raw`` against ``forbidden`` and remember the result.

        Raises:
            ConfigError: the value is unset, empty, or not an absolute path. A
                relative root resolves against whatever working directory the
                process happened to start in — including a checkout.
            StorageBoundaryError: the root is, or sits inside, a forbidden tree.
        """
        text = str(raw) if isinstance(raw, Path) else raw
        if text is None or not text.strip():
            raise ConfigError(
                f"{ENV_OBSERVATORY_ROOT} is not set; ADR-0010 §5 requires an explicit path",
            )
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            raise ConfigError(f"{ENV_OBSERVATORY_ROOT} must be an absolute path, got {text!r}")
        self._path = _reject_forbidden(candidate.resolve(), forbidden)

    @property
    def path(self) -> Path:
        return self._path

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ObservatoryRoot) and other._path == self._path

    def __hash__(self) -> int:
        return hash(self._path)

    def __repr__(self) -> str:
        return f"ObservatoryRoot({str(self._path)!r})"


def _reject_forbidden(root: Path, forbidden: Sequence[Path]) -> Path:
    """Refuse a root that is, or lies inside, any forbidden tree."""
    for tree in forbidden:
        resolved = tree.resolve()
        if root == resolved or resolved in root.parents:
            raise StorageBoundaryError(
                f"observatory root {root} is inside {resolved}; ADR-0010 §5 keeps observed "
                "material outside the engine repository and the lovverk corpus",
            )
    return root


def forbidden_trees(env: Mapping[str, str] | None = None) -> list[Path]:
    """The trees observatory storage may not enter.

    Always the engine repository. The corpus repository is added whenever the
    environment says where it is, so protecting it does not depend on a caller
    remembering to pass it — the tree ADR-0010 §5 cares most about is the one
    that gets published.
    """
    environment = os.environ if env is None else env
    trees = [engine_root()]
    corpus = environment.get(ENV_CORPUS_ROOT)
    if corpus and corpus.strip():
        trees.append(Path(corpus).expanduser())
    return trees


def observatory_root(env: Mapping[str, str] | None = None) -> ObservatoryRoot:
    """Resolve and validate the observatory root from the environment."""
    environment = os.environ if env is None else env
    return ObservatoryRoot(environment.get(ENV_OBSERVATORY_ROOT), forbidden_trees(environment))
