"""A batch desk, a scratch release clone and a clock for the B1b apply tests (#738).

Every state is reached through supported commands: staging, review, policy,
an offline fetch run, a stored batch plan, real Git on a scratch clone. The
owner's typed phrase is the prompt's own phrase unless a test types another
or acts while the owner reads (``typed``). Output is checked for chapter
text, private notes, the owner's handle and local paths on every call.
"""

import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import typer
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider import apply_preflight, batch_apply, batch_withdraw
from lovspor.forarbeider.release_commands import ReleaseScope
from tests.unit.forarbeider_batch_support import BRANCH, CHAPTER_MARKER, PLAN_AT, BatchDesk
from tests.unit.forarbeider_checkout_support import git, release_clone, trust
from tests.unit.forarbeider_release_support import OWNER, PRIVATE_MARKER, said_digest

QUOTE = "<blockquote><p>Sitat.</p></blockquote>"
TABLE = "<table><tr><td>x</td></tr></table>"
NOW = datetime(2100, 4, 10, 13, 0, tzinfo=UTC)
CONFIRMED = "2100-04-10T12:30:00Z"
DOC1, DOC2 = "regjeringen-9000101", "regjeringen-9000102"
PATH1 = "forarbeider/2099-2100/prop-l/9000101.md"
PATH2 = "forarbeider/2099-2100/prop-l/9000102.md"
_PROMPT = "Type to confirm: "


def _as_shown(shown: str) -> str:
    return shown


class ApplyBench:
    """One batch desk, one release clone on the batch branch, one clock, one typist."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.batch = BatchDesk(root)
        self.clone = release_clone(root / "release")
        self.monkeypatch = monkeypatch
        self.typed: Callable[[str], str] = _as_shown
        self.prompts: list[str] = []
        trust(monkeypatch, self.clone)
        self.at(NOW)

    def _prompt(self, text: str, *_: Any, **__: Any) -> str:
        shown = text.removeprefix(_PROMPT)
        self.prompts.append(shown)
        return self.typed(shown)

    def at(self, moment: datetime) -> None:
        for module in (apply_preflight, batch_apply, batch_withdraw):
            self.monkeypatch.setattr(module, "_now", lambda: moment)

    def mixed(self, httpx_mock: HTTPXMock) -> Path:
        """Doc 1: clean 2 and 3. Doc 2: clean 2, flagged 3 with an eligible report."""
        self.batch.add(1)
        self.batch.add(2, extras={3: QUOTE})
        review = self.batch.flagged_report(2, (3,))
        self.batch.ready(httpx_mock)
        return self.batch.request([self.batch.document(1), self.batch.document(2, review)])

    def planned(self, request: Path, at: str = PLAN_AT) -> str:
        result = self.batch.plan(request, at=at)
        assert result.exit_code in (0, 2), result.output
        return said_digest(result.output, "BATCH PLAN")

    def head(self) -> list[str]:
        workspace = ["--workspace", str(self.batch.workspace)]
        return ["ops", "forarbeider-release", *workspace, "--ledger", str(self.batch.ledger)]

    def invoke(self, arguments: list[str]) -> Any:
        """One ``batch`` command; the owner types at its prompt, if it has one."""
        with pytest.MonkeyPatch.context() as typist:
            typist.setattr(typer, "prompt", self._prompt)
            result = CliRunner().invoke(app, [*self.head(), "batch", *arguments])
        for secret in (CHAPTER_MARKER, PRIVATE_MARKER, str(self.root), OWNER):
            assert secret not in result.output, secret
        return result

    def clone_command(self, *arguments: str, **given: Any) -> Any:
        """``batch release-clone --checkout --branch <command>``, the clone given or the bench's."""
        checkout = str(given.get("checkout", self.clone))
        branch = given.get("branch", BRANCH)
        return self.invoke(
            ["release-clone", "--checkout", checkout, "--branch", branch, *arguments]
        )

    def apply(self, sha: str, request: Path, decided: str = CONFIRMED, **given: Any) -> Any:
        proof = given.pop("proof", None) or self.batch.run_file
        arguments = [sha, "--request", str(request), "--source-proof", str(proof)]
        return self.clone_command("apply", *arguments, "--decided", f"{OWNER}@{decided}", **given)

    def recover(self, branch: str = BRANCH) -> Any:
        return self.clone_command("recover", branch=branch)

    def withdraw(self, document: str, chapters: str, **given: Any) -> Any:
        reason = given.pop("reason", "privacy")
        decided = given.pop("decided", "2100-04-10T12:45:00Z")
        arguments = ["--document", document, "--chapters", chapters, "--reason", reason]
        return self.clone_command(
            "withdraw", *arguments, "--decided", f"{OWNER}@{decided}", **given
        )

    def scope(self) -> ReleaseScope:
        return ReleaseScope(workspace=self.batch.workspace, ledger=self.batch.ledger)

    def status(self) -> list[str]:
        """Git's own view of the clone: every changed or new path, sorted."""
        out = git(self.clone, "status", "--porcelain", "--untracked-files=all")
        return sorted(line[3:] for line in out.splitlines())

    def records(self, folder: str) -> list[Path]:
        directory = self.batch.workspace / folder
        return sorted(directory.glob("*.json")) if directory.exists() else []


def outside_dataset(root: Path, clone: Path) -> dict[str, bytes | None]:
    """Every file and link below ``root`` but the clone's ``forarbeider/`` and the workspace's
    B1b records, by bytes (a link by its target): what a release may never change."""
    skipped = {clone / "forarbeider"}
    found: dict[str, bytes | None] = {}
    for folder, directories, files in os.walk(root):
        here = Path(folder)
        directories[:] = [name for name in directories if here / name not in skipped]
        for name in files:
            path = here / name
            status = os.lstat(path)
            if stat.S_ISLNK(status.st_mode):
                found[str(path)] = str(path.readlink()).encode()
            else:
                found[str(path)] = path.read_bytes() if stat.S_ISREG(status.st_mode) else None
    return found


B1B_RECORDS = (
    "batch_confirmations",
    "batch_releases",
    "release_withdrawal_intents",
    "release_withdrawals",
)


def without_records(found: dict[str, bytes | None], workspace: Path) -> dict[str, bytes | None]:
    folders = tuple(str(workspace / name) for name in B1B_RECORDS)
    return {path: data for path, data in found.items() if not path.startswith(folders)}
