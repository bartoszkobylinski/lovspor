import tomllib
from pathlib import Path

from lovspor import __version__


def test_version_matches_pyproject() -> None:
    """__version__ derives from installed package metadata; guard it against
    drifting from pyproject's declared version — the bug that shipped 0.2.0
    reporting itself as 0.1.0 from a second, hardcoded copy."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert __version__ == declared
