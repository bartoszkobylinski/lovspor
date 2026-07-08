"""lovspor — Norwegian law change tracker."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the version declared in pyproject.toml, read
    # from the installed package metadata. Avoids the drift that shipped
    # 0.2.0 reporting itself as 0.1.0 (a second hardcoded copy went stale).
    __version__ = version("lovspor")
except PackageNotFoundError:  # pragma: no cover - source tree without an install
    __version__ = "0.0.0+unknown"
