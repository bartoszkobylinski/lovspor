"""AR4a: the canonical, versioned publication-transaction contracts (#738; §9.3).

Pure model tests: paths A and B kept apart, the proposed file grammar, the
transaction id as the digest of its content, and a journal that can only
describe its own transaction.
"""

import ast
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import lovspor
from lovspor import git_prepare
from lovspor.forarbeider.publication_models import (
    TRANSACTION_VERSION,
    JournalOperation,
    PathA,
    PathB,
    PreparationJournal,
    ProposedFile,
    operations_of,
    publication_path,
)

SOURCE = Path(lovspor.__file__).parent


@pytest.mark.parametrize(
    "path",
    [
        "INDEX.md",
        "catalogue.json",
        "documents/regjeringen-9000001.md",
        "releases/" + "a" * 64 + ".json",
    ],
)
def test_the_publication_tree_admits_exactly_its_own_files(path: str) -> None:
    assert publication_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        ".github/workflows/x.yml",
        "documents/regjeringen-0.md",
        "documents/../README.md",
        "documents/regjeringen-9000001.md/x",
        "documents/sub/regjeringen-9000001.md",
        "releases/abc.json",
        "/INDEX.md",
        "forarbeider/manifest.json",
    ],
)
def test_anything_else_is_outside_the_publication_tree(path: str) -> None:
    assert not publication_path(path)


def test_a_proposed_file_names_a_path_of_its_role() -> None:
    fields: dict[str, Any] = {"sha256": "a" * 64, "size_bytes": 1, "previous_sha256": None}
    ProposedFile(path="INDEX.md", role="index", **fields)

    with pytest.raises(ValidationError):
        ProposedFile(path="INDEX.md", role="document", **fields)
    with pytest.raises(ValidationError):
        ProposedFile(path="README.md", role="index", **fields)


def test_paths_a_and_b_are_distinct_records() -> None:
    a = PathA(expected_head_oid="1" * 40)
    b = PathB(base_commit="1" * 40)

    assert (a.path, a.signature_class, a.base_commit) == ("A", "github_app", "1" * 40)
    assert (b.path, b.signature_class, b.base_commit) == ("B", "bot_key", "1" * 40)
    with pytest.raises(ValidationError):
        PathA.model_validate(
            {"path": "A", "expected_head_oid": "1" * 40, "signature_class": "bot_key"}
        )
    with pytest.raises(ValidationError):
        PathB.model_validate(
            {
                "path": "B",
                "base_commit": "1" * 40,
                "mechanism": "github_app_create_commit_on_branch",
            }
        )
    with pytest.raises(ValidationError):
        PathA(expected_head_oid="main")


def test_the_contract_is_versioned() -> None:
    assert TRANSACTION_VERSION == "publication-transaction/1"


def test_a_journal_operation_never_names_a_path_outside_the_tree() -> None:
    with pytest.raises(ValidationError):
        JournalOperation(path="README.md", sha256="a" * 64, previous_sha256=None)


def test_a_journal_describes_only_its_own_transaction() -> None:
    fields = PreparationJournal.model_fields
    assert {"state", "transaction", "operations"} <= set(fields)
    assert callable(operations_of)


# --- the Git boundary ---------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.structural
def test_only_the_publication_clone_imports_the_preparation_git() -> None:
    importers = {
        path.relative_to(SOURCE).as_posix()
        for path in SOURCE.rglob("*.py")
        if any(name.split(".")[:2] == ["lovspor", "git_prepare"] for name in _imports(path))
    }
    assert importers == {"forarbeider/publication_clone.py"}


def test_the_preparation_git_can_never_publish() -> None:
    commands = {shape[0] for shape in git_prepare.COMMANDS}

    assert commands <= {"clone", "checkout", "remote", "rev-parse", "status"}
    assert not commands & {"commit", "push", "fetch", "merge", "tag", "update-ref", "add"}


@pytest.mark.parametrize(
    "argv",
    [
        ("push", "origin", "main"),
        ("commit", "-m", "x"),
        ("remote", "add", "origin", "https://github.com/x/y"),
        ("checkout", "--detach", "main"),
        ("checkout", "--quiet", "--detach", "1" * 39),
        ("rev-parse", "--verify", "--quiet", "HEAD~1^{commit}"),
    ],
)
def test_a_git_vector_outside_the_allowlist_never_runs(
    tmp_path: Path, argv: tuple[str, ...]
) -> None:
    with pytest.raises(git_prepare.GitPrepareError):
        git_prepare.run_git(tmp_path, argv)
