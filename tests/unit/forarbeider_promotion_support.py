"""Driving plan, sandbox, simulate and withdraw through the CLI (#738, PR3B-2).

A :class:`Desk` stages the same structural pages the simulator bundles, so a
plain desk is synthetic input; one given ``extras`` is not.
"""

from pathlib import Path
from typing import Any

from tests.unit.forarbeider_release_support import OWNER, Desk, said_digest

WITHDRAWN = "2100-04-12T12:00:00Z"


def release_options(desk: Desk) -> list[str]:
    return [
        "ops",
        "forarbeider-release",
        "--workspace",
        str(desk.workspace),
        "--ledger",
        str(desk.ledger),
    ]


def eligible(desk: Desk) -> tuple[str, list[str]]:
    """An eligible preview of chapters 2 and 3: the report digest and its checkpoints."""
    desk.approve_all()
    _, authorization = desk.released()
    checkpoints = desk.all_checkpoints()
    result = desk.preflight(authorization, checkpoints)
    assert result.exit_code == 0, result.output
    return said_digest(result.output, "report"), checkpoints


def plan(desk: Desk, report: str, checkpoints: list[str], corpus: Path) -> Any:
    options = ["--report", report, "--corpus", str(corpus)]
    for value in checkpoints:
        options += ["--checkpoint", value]
    return desk.invoke([*desk.scope(), "plan", *options])


def sandbox(desk: Desk, path: Path) -> Any:
    return desk.invoke([*release_options(desk), "sandbox-create", "--path", str(path)])


def simulate(desk: Desk, plan_sha: str, path: Path) -> Any:
    options = ["--plan", plan_sha, "--sandbox", str(path)]
    return desk.invoke([*desk.scope(), "simulate", *options])


def withdraw(desk: Desk, path: Path, document: str, at: str = WITHDRAWN) -> Any:
    options = ["--sandbox", str(path), "--document", document, "--decided", f"{OWNER}@{at}"]
    return desk.invoke(
        [*release_options(desk), "sandbox-withdraw", *options], f"withdraw {document}\n"
    )


def planned(desk: Desk, corpus: Path) -> str:
    """A plan of chapters 2 and 3 against ``corpus``: its digest."""
    report, checkpoints = eligible(desk)
    result = plan(desk, report, checkpoints, corpus)
    assert result.exit_code == 0, result.output
    return said_digest(result.output, "plan")


def tree(root: Path) -> dict[str, bytes]:
    """Every file below ``root``, ``.git`` included, by relative path."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
