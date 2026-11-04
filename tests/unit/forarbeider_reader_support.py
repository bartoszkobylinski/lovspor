"""Driving ``lovspor ops forarbeider-read`` over a synthetic sandbox (#738, PR3B-3)."""

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from lovspor.cli import app
from tests.unit.forarbeider_promotion_support import planned, sandbox, simulate
from tests.unit.forarbeider_release_support import Desk

L_ID = "regjeringen-9000001"
LS_ID = "regjeringen-9000003"
SESSION = "2099-2100"
L_DOCUMENT = "forarbeider/2099-2100/prop-l/9000001.md"
LOOKUP = {L_ID: ("prop-l", SESSION), LS_ID: ("prop-ls", SESSION)}


def read(box: Path, *arguments: str) -> Any:
    """Run the reader; stdout and stderr together in ``output``."""
    return CliRunner().invoke(app, ["ops", "forarbeider-read", "--sandbox", str(box), *arguments])


def exact(document: str) -> list[str]:
    """The three options that name one document exactly."""
    kind, session = LOOKUP[document]
    return ["--document", document, "--session", session, "--type", kind]


def loaded(result: Any) -> dict[str, Any]:
    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.stdout)
    return payload


def released_box(desk: Desk, path: Path) -> Path:
    """A sandbox with the desk's proposition released into it."""
    if not path.exists():
        assert sandbox(desk, path).exit_code == 0
    assert simulate(desk, planned(desk, path), path).exit_code == 0
    return path
