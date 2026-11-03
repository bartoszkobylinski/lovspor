"""The forarbeider reader stays out of every default path (#738, ADR-0018 PR3B-3).

The law readers never see ``forarbeider/``: ``get_law``, ``corpus_status``,
law and body search, sections and recent changes answer the same whether the
dataset is absent, released, corrupt or unsafe. Only ``ops_cli.py`` imports
the package, and the reader's own modules hold no write, no process and no
network call, so reading cannot change anything.
"""

import ast
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lovspor.mcp import CorpusReader
from tests.unit.forarbeider_reader_support import released_box
from tests.unit.forarbeider_release_support import Desk, at_a_terminal  # noqa: F401
from tests.unit.test_mcp import _record, _seed_corpus

PACKAGE = Path(__file__).parents[2] / "src" / "lovspor" / "forarbeider"
READER_MODULES = ("corpus_reader.py", "reader_views.py", "reader_cli.py")
#: Names that would write, move, delete, spawn or reach out.
WRITING = {
    "write_bytes",
    "write_text",
    "write_record",
    "write_leaf",
    "mkdir",
    "makedirs",
    "unlink",
    "rmdir",
    "rename",
    "replace",
    "remove",
    "rmtree",
    "symlink_to",
    "chmod",
    "apply",
    "finish",
    "roll_back",
    "O_WRONLY",
    "O_RDWR",
    "O_CREAT",
    "subprocess",
    "socket",
    "httpx",
    "urlopen",
}


def _law_answers(root: Path) -> dict[str, Any]:
    reader = CorpusReader(root)
    calls: dict[str, Callable[[], Any]] = {
        "get_law": lambda: reader.get_law("skatteloven"),
        "search_laws": lambda: reader.search_laws("skatt"),
        "search_body": lambda: reader.search_body("Skatteloven"),
        "list_sections": lambda: reader.list_sections("skatteloven"),
        "list_recent_changes": reader.list_recent_changes,
    }
    answers = {name: call() for name, call in calls.items()}
    status = reader.corpus_status()
    answers["corpus_status"] = {key: value for key, value in status.items() if key != "checked_at"}
    return answers


@pytest.fixture
def law(tmp_path: Path) -> Path:
    root = tmp_path / "law"
    _seed_corpus(root, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    return root


def _released_dataset(tmp_path: Path, law: Path) -> None:
    box = released_box(Desk(tmp_path / "desk"), tmp_path / "sandbox")
    shutil.copytree(box / "forarbeider", law / "forarbeider")


def _corrupt_dataset(tmp_path: Path, law: Path) -> None:
    (law / "forarbeider").mkdir()
    (law / "forarbeider" / "manifest.json").write_bytes(b"\xff not a manifest")
    (law / "forarbeider" / "INDEX.md").write_text("edited\n")


def _unsafe_dataset(tmp_path: Path, law: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (law / "forarbeider").symlink_to(tmp_path / "elsewhere")
    os.mkfifo(tmp_path / "elsewhere" / "manifest.json", 0o600)


@pytest.mark.usefixtures("at_a_terminal")
@pytest.mark.parametrize("state", [_released_dataset, _corrupt_dataset, _unsafe_dataset])
def test_the_law_readers_answer_the_same_whatever_forarbeider_holds(
    tmp_path: Path, law: Path, state: Callable[[Path, Path], None]
) -> None:
    before = _law_answers(law)

    state(tmp_path, law)

    assert _law_answers(law) == before
    assert "forarbeider" not in (law / "manifest.json").read_text()


def _names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {alias.name for alias in node.names} | {node.module or ""}
        elif isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
    return names


@pytest.mark.parametrize("module", READER_MODULES)
def test_the_reader_modules_hold_no_write_process_or_network_call(module: str) -> None:
    assert _names(PACKAGE / module) & WRITING == set()


def test_the_reader_is_reached_only_through_the_ops_mount() -> None:
    importers = {
        path.name
        for path in (PACKAGE.parent).rglob("*.py")
        if "lovspor.forarbeider.reader_cli" in path.read_text(encoding="utf-8")
        or "reader_cli as" in path.read_text(encoding="utf-8")
    }
    assert importers == {"ops_cli.py"}
