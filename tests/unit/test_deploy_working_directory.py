"""Every deploy script leaves the operator's working directory before it runs lovspor (#300).

A root login shell on the droplet starts in ``/root``, mode ``0700``. Run as the
build user from there, ``release build`` constructs the MCP server, whose
FastMCP settings stat ``./.env`` — and that stat is EACCES, not "absent", so the
build dies before it does anything. ``lovspor-publish.service`` runs from
``/`` and never saw it; the operator's manual runs — README steps 4a, 4b and 5 —
all did.

The scripts cannot run here (no ``sudo``, no ``caddy``), so what is pinned is
the seam: ``cd /`` under strict mode, before the first line that names the
lovspor executable.
"""

from pathlib import Path

import pytest

_DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "digitalocean"
_SCRIPTS = ("publish-release.sh", "rehearse-migration.sh", "rehearse-urls.sh")


def _code_lines(script: Path) -> list[str]:
    """The script's statements, stripped, without comments or blank lines."""
    lines = (line.strip() for line in script.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _first_naming_lovspor(lines: list[str]) -> int:
    return next(index for index, line in enumerate(lines) if '"$LOVSPOR"' in line)


@pytest.mark.parametrize("name", _SCRIPTS)
def test_moves_to_the_root_directory_before_anything_names_lovspor(name: str) -> None:
    lines = _code_lines(_DEPLOY / name)

    assert "cd /" in lines, name
    assert lines.index("cd /") < _first_naming_lovspor(lines), name


@pytest.mark.parametrize("name", _SCRIPTS)
def test_moves_under_strict_mode_so_a_failed_cd_stops_the_run(name: str) -> None:
    lines = _code_lines(_DEPLOY / name)

    assert lines.index("set -euo pipefail") < lines.index("cd /"), name
