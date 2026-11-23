"""The files the identity-migration dry run writes (#871), byte for byte.

``planned/manifest.json`` is the file ``write_manifest`` would write and
``report.json`` is sorted, two-space indented UTF-8 JSON: a Norwegian title stays
as written. The identity fields and the threshold exceptions keep their
published names and JSON shapes, and ``report.md`` explains the identity schema.
"""

import json
from pathlib import Path

from lovspor.identity.plan import plan_migration
from lovspor.identity.report import (
    THRESHOLD_EXCEPTION_NOTE,
    manifest_json,
    render_markdown,
    report_data,
    report_files,
)
from lovspor.identity.schema import ENFORCED_IDENTITY_SCHEMA, IDENTITY_FILENAME
from lovspor.storage.manifest import write_manifest
from tests.unit.identity_git_fixtures import (
    commit_all,
    doc_text,
    init_repo,
    record,
    write_doc,
    write_manifest_json,
)
from tests.unit.test_identity_migration import NEW, OLD, _base_repo, _history

_LOST = "sf-20250101-0004"
_TITLE = "Forskrift om særavgift på øl"


def _lost_norwegian_title(tmp_path: Path) -> Path:
    """A document whose record a later manifest dropped: the plan restores it."""
    repo = init_repo(tmp_path / "lovverk")
    write_doc(repo, "forskrifter/d.md", doc_text(_LOST, "d", "body"))
    _history(repo, "d", _LOST)
    write_manifest_json(
        repo, {_LOST: {**record(_LOST, "d"), "title": _TITLE}}, "2026-05-01T00:00:00Z"
    )
    commit_all(repo, "sync: seed")
    (repo / "forskrifter/d.md").unlink()
    write_manifest_json(repo, {}, "2026-07-02T00:00:00Z")
    commit_all(repo, "sync: update manifest, index, and history")
    return repo


def test_the_planned_manifest_is_the_file_write_manifest_writes(tmp_path: Path) -> None:
    plan = plan_migration(_lost_norwegian_title(tmp_path), "HEAD")
    written = tmp_path / "manifest.json"
    write_manifest(plan.planned_manifest, written)

    text = manifest_json(plan.planned_manifest)

    assert _TITLE in text
    assert text == written.read_text(encoding="utf-8")


def test_report_json_is_sorted_indented_utf8(tmp_path: Path) -> None:
    plan = plan_migration(_lost_norwegian_title(tmp_path), "HEAD")

    text = report_files(plan)["report.json"]

    assert f'"title": "{_TITLE}"' in text
    assert json.loads(text) == json.loads(json.dumps(report_data(plan)))
    keys = list(json.loads(text, object_pairs_hook=lambda pairs: [k for k, _ in pairs]))
    assert keys == sorted(keys)
    assert text.startswith('{\n  "base_commit": ')
    assert text.endswith("\n}\n")


def test_the_report_says_whether_the_base_had_a_registry(tmp_path: Path) -> None:
    data = report_data(plan_migration(_lost_norwegian_title(tmp_path), "HEAD"))

    assert data["base_registry_present"] is False


def test_a_threshold_exception_is_a_json_row_with_its_prior_holders(tmp_path: Path) -> None:
    repo, shas = _base_repo(tmp_path)

    data = report_data(plan_migration(repo, "HEAD", shas["r4"]))

    exceptions = data["threshold_exceptions"]
    assert isinstance(exceptions, list)
    assert [(row["directory"], row["slug"], row["owner"]) for row in exceptions] == [
        ("forskrifter", "x", NEW)
    ]
    assert exceptions[0]["prior_holders"] == [
        {"doc_id": OLD, "first_commit": shas["r1"], "last_commit": shas["r2"]}
    ]


def test_report_md_says_which_identity_schema_the_plan_moves_to(tmp_path: Path) -> None:
    repo, _ = _base_repo(tmp_path)
    plan = plan_migration(repo, "HEAD")

    markdown = render_markdown(plan)

    start = markdown.index("## Identity schema")
    assert markdown[start : markdown.index("\n## ", start) + 1] == (
        "## Identity schema\n"
        "\n"
        f"Declared at the base: `{plan.base.identity_schema}`. Applying the plan writes "
        f"`{IDENTITY_FILENAME}` declaring `{ENFORCED_IDENTITY_SCHEMA}`, from which point the "
        "sync refuses to run without a readable `slugs.json`.\n"
        "\n"
        f"{THRESHOLD_EXCEPTION_NOTE}.\n"
        "\n"
    )
