"""Every Observatory module imports on its own, in a fresh interpreter.

An import cycle passes ruff and passes mypy — both read the source, neither
executes it — and is refused only by the interpreter. Inside a running suite
the cycle can also hide: whichever module pytest happened to import first has
already populated ``sys.modules`` for the second, so the failure depends on
collection order rather than on the code. Each module is therefore imported
here as the *first* import of a new process.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_PACKAGE = "lovspor.observatory"
_PACKAGE_DIR = Path(__file__).resolve().parents[2] / "src" / "lovspor" / "observatory"

# Written out rather than discovered, so that adding a module is a deliberate
# entry here; the test below pins this list against the directory.
_MODULES = (
    "lovspor.observatory",
    "lovspor.observatory.commands",
    "lovspor.observatory.discovery",
    "lovspor.observatory.events",
    "lovspor.observatory.fetch",
    "lovspor.observatory.freshness",
    "lovspor.observatory.heartbeat",
    "lovspor.observatory.listing",
    "lovspor.observatory.log",
    "lovspor.observatory.model",
    "lovspor.observatory.registry",
    "lovspor.observatory.storage",
    "lovspor.observatory.sweeps",
)


@pytest.mark.parametrize("module", _MODULES)
def test_observatory_module_imports_first_in_a_fresh_interpreter(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_the_pinned_module_list_is_the_package_on_disk() -> None:
    on_disk = {
        _PACKAGE if path.stem == "__init__" else f"{_PACKAGE}.{path.stem}"
        for path in _PACKAGE_DIR.glob("*.py")
    }

    assert set(_MODULES) == on_disk
