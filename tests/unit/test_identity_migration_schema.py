"""The migration plan moves a corpus to the enforced identity schema (#871).

``planned/`` carries ``identity.json`` beside ``slugs.json``, applying the
plan writes both, and the plan refuses a base whose declaration and registry
disagree, exactly as the sync would.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.errors import IdentityMigrationError, IdentitySchemaError
from lovspor.identity.apply import apply_plan
from lovspor.identity.plan import plan_migration
from lovspor.identity.registry import EnforcedCorpus, corpus_identity, registered_ownership
from lovspor.identity.report import write_report
from lovspor.identity.schema import IDENTITY_FILENAME, declaration_json
from tests.unit.identity_git_fixtures import commit_all, git, write_doc
from tests.unit.test_identity_migration import _base_repo


@pytest.fixture
def base(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    return _base_repo(tmp_path)


def test_the_planned_files_declare_the_enforced_schema(
    base: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, _ = base
    out = tmp_path / "out"
    out.mkdir()

    sums = write_report(plan_migration(repo, "HEAD"), out)

    declared = (out / "planned" / IDENTITY_FILENAME).read_text(encoding="utf-8")
    assert declared == declaration_json()
    assert json.loads(declared) == {"schema_version": 1}
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert (data["base_identity_schema"], data["planned_identity_schema"]) == (0, 1)
    assert (
        data["planned_files_sha256"][f"planned/{IDENTITY_FILENAME}"]
        == sums[f"planned/{IDENTITY_FILENAME}"]
    )
    assert IDENTITY_FILENAME in data["changed_paths"]
    assert "## Identity schema" in (out / "report.md").read_text(encoding="utf-8")


def test_applying_the_plan_moves_the_corpus_to_the_enforced_schema(
    base: tuple[Path, dict[str, str]],
) -> None:
    repo, _ = base

    apply_plan(repo, plan_migration(repo, "HEAD"))

    assert (repo / IDENTITY_FILENAME).read_text(encoding="utf-8") == declaration_json()
    assert isinstance(corpus_identity(repo), EnforcedCorpus)
    commit_all(repo, "migration: repair document identity (#871)")
    again = plan_migration(repo, "HEAD")
    assert again.base.identity_schema == 1
    assert again.nothing_to_do
    assert IDENTITY_FILENAME not in again.changed_paths


def test_a_migrated_corpus_that_loses_its_registry_fails_closed(
    base: tuple[Path, dict[str, str]],
) -> None:
    repo, _ = base
    apply_plan(repo, plan_migration(repo, "HEAD"))
    commit_all(repo, "migration: repair document identity (#871)")

    (repo / "slugs.json").unlink()

    with pytest.raises(IdentitySchemaError, match="no slugs.json"):
        registered_ownership(repo, {})
    commit_all(repo, "registry lost")
    with pytest.raises(IdentityMigrationError, match="no slugs.json"):
        plan_migration(repo, "HEAD")


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({IDENTITY_FILENAME: declaration_json()}, "no slugs.json"),
        ({"slugs.json": '{"schema_version": 1, "slugs": {}}\n'}, f"no {IDENTITY_FILENAME}"),
        ({IDENTITY_FILENAME: '{"schema_version": 2}\n'}, "unknown identity schema version 2"),
        ({IDENTITY_FILENAME: "{"}, "unreadable identity declaration"),
    ],
)
def test_the_plan_refuses_an_inconsistent_base(
    base: tuple[Path, dict[str, str]], tmp_path: Path, files: dict[str, str], message: str
) -> None:
    repo, _ = base
    for name, text in files.items():
        write_doc(repo, name, text)
    commit_all(repo, "an inconsistent identity declaration")

    with pytest.raises(IdentityMigrationError, match=message):
        plan_migration(repo, "HEAD")
    result = CliRunner().invoke(
        app,
        [
            *("ops", "identity-plan", "--repo", str(repo), "--base", "HEAD"),
            *("--u1-threshold", "HEAD", "--out", str(tmp_path / "r")),
        ],
    )
    assert result.exit_code == 2
    assert not (tmp_path / "r").exists()
    assert git(repo, "status", "--porcelain") == ""
