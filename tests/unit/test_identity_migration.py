"""The offline identity-migration dry run on synthetic lovverk repos (#871).

The synthetic history reproduces both measured defects: a removed document's
record is later dropped from the manifest (the ``f6d7e25bb`` shape), and its
slug is then handed to a newer document whose history file overwrites the old
``doc_id`` (the ``endr-i-høstingsforskriften`` shape).
"""

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.errors import IdentityMigrationError
from lovspor.identity.apply import apply_plan
from lovspor.identity.plan import plan_migration
from lovspor.identity.report import SUMS_FILENAME, write_report
from tests.unit.identity_git_fixtures import (
    commit_all,
    doc_text,
    git,
    init_repo,
    record,
    tree_of,
    write_doc,
    write_manifest_json,
)

OLD = "sf-20250101-0001"  # first holder of "x", removed, then its record lost
NEW = "sf-20260601-0002"  # later holder of "x"
KEEP = "sf-20250101-0003"  # an ordinary current document
LOST = "sf-20250101-0004"  # a current document whose record was dropped


def _history(repo: Path, slug: str, doc_id: str) -> None:
    payload = {"doc_id": doc_id, "events": [], "schema_version": 1, "slug": slug}
    write_doc(repo, f"forskrifter/history/{slug}.json", json.dumps(payload) + "\n")


def _base_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = init_repo(tmp_path / "lovverk")
    shas: dict[str, str] = {}
    for slug, doc_id in (("x", OLD), ("c", KEEP), ("d", LOST)):
        write_doc(repo, f"forskrifter/{slug}.md", doc_text(doc_id, slug, "body"))
        _history(repo, slug, doc_id)
    docs = {OLD: record(OLD, "x"), KEEP: record(KEEP, "c"), LOST: record(LOST, "d")}
    write_manifest_json(repo, docs, "2026-05-01T00:00:00Z")
    shas["r1"] = commit_all(repo, "sync: seed")
    (repo / "forskrifter/x.md").unlink()
    write_manifest_json(repo, {**docs, OLD: record(OLD, "x", "removed")}, "2026-05-02T00:00:00Z")
    shas["r2"] = commit_all(repo, "remove(forskrift): x")
    (repo / "forskrifter/d.md").unlink()
    write_manifest_json(repo, {KEEP: record(KEEP, "c")}, "2026-07-02T00:00:00Z")
    shas["r3"] = commit_all(repo, "sync: update manifest, index, and history")
    write_doc(repo, "forskrifter/x.md", doc_text(NEW, "x", "new body"))
    _history(repo, "x", NEW)
    write_manifest_json(
        repo, {KEEP: record(KEEP, "c"), NEW: record(NEW, "x")}, "2026-10-10T00:00:00Z"
    )
    shas["r4"] = commit_all(repo, "add(forskrift): x")
    return repo, shas


@pytest.fixture
def base(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    return _base_repo(tmp_path)


def test_restores_every_lost_record_with_its_provenance(base: tuple[Path, dict[str, str]]) -> None:
    repo, shas = base

    plan = plan_migration(repo, "HEAD")

    assert [(t.doc_id, t.last_status) for t in plan.restored] == [
        (OLD, "removed"),
        (LOST, "current"),
    ]
    old = plan.restored[0]
    assert (old.last_seen_commit, old.missing_since_commit) == (shas["r2"], shas["r3"])
    restored = plan.planned_manifest.documents[OLD]
    assert restored.status == "removed"
    assert restored.slug == "x"
    assert shas["r2"] in (restored.removed_reason or "")
    assert plan.planned_manifest.documents[LOST].status == "removed"


def test_the_first_holder_owns_the_slug(base: tuple[Path, dict[str, str]]) -> None:
    repo, shas = base

    plan = plan_migration(repo, "HEAD")

    registry = plan.planned_registry
    assert registry.owner_of("forskrifter", "x") == OLD
    assert registry.slugs["forskrifter"]["x"].assigned_at == "2026-05-01T00:00:00+00:00"
    assert registry.slugs["forskrifter"]["x"].prior_holders == ()
    assert [(f.slug, f.owner, f.reuse) for f in plan.fates] == [("x", OLD, ["post-threshold"])]
    holders = [
        (h.doc_id, h.role, h.first_commit, h.last_commit, h.renamed_to)
        for h in plan.fates[0].holders
    ]
    assert holders == [
        (OLD, "owner", shas["r1"], shas["r2"], None),
        (NEW, "later", shas["r4"], shas["r4"], "x-2026"),
    ]


def test_the_later_holder_gets_a_distinct_slug_by_the_collision_rule(
    base: tuple[Path, dict[str, str]],
) -> None:
    repo, _ = base

    plan = plan_migration(repo, "HEAD")

    assert [(r.doc_id, r.from_slug, r.to_slug) for r in plan.renames] == [(NEW, "x", "x-2026")]
    assert plan.planned_manifest.documents[NEW].markdown_path == "forskrifter/x-2026.md"
    assert plan.planned_registry.owner_of("forskrifter", "x-2026") == NEW


def test_lists_collisions_mismatched_histories_and_changed_paths(
    base: tuple[Path, dict[str, str]],
) -> None:
    repo, _ = base

    plan = plan_migration(repo, "HEAD")

    assert [
        (c.restored_doc_id, c.holder_at_base, c.owner, c.owning_side, c.outcome)
        for c in plan.collisions
    ] == [(OLD, NEW, OLD, "restored", f"the holder moves to x-2026; the slug stays with {OLD}")]
    assert [(m.path, m.doc_id_in_file, m.owner) for m in plan.history_mismatches] == [
        ("forskrifter/history/x.json", NEW, OLD)
    ]
    assert plan.changed_paths == [
        f"forskrifter/history/by-id/{OLD}.json",
        f"forskrifter/history/by-id/{NEW}.json",
        "forskrifter/history/x-2026.json",
        "forskrifter/history/x-2026.md",
        "forskrifter/history/x.json",
        "forskrifter/history/x.md",
        "forskrifter/x-2026.md",
        "forskrifter/x.md",
        "identity.json",
        "manifest.json",
        "slugs.json",
    ]
    assert not plan.blocked
    assert not plan.nothing_to_do


def test_two_runs_write_byte_identical_reports(
    base: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, _ = base
    first, second = tmp_path / "run1", tmp_path / "run2"
    for out in (first, second):
        out.mkdir()
        write_report(plan_migration(repo, "HEAD"), out)

    files = sorted(p.relative_to(first) for p in first.rglob("*") if p.is_file())
    assert [str(p) for p in files] == [
        SUMS_FILENAME,
        f"planned/forskrifter/history/by-id/{OLD}.json",
        f"planned/forskrifter/history/by-id/{NEW}.json",
        "planned/forskrifter/history/x-2026.json",
        "planned/forskrifter/history/x-2026.md",
        "planned/forskrifter/history/x.json",
        "planned/forskrifter/history/x.md",
        "planned/identity.json",
        "planned/manifest.json",
        "planned/slugs.json",
        "report.json",
        "report.md",
    ]
    assert all((first / p).read_bytes() == (second / p).read_bytes() for p in files)


def test_the_report_names_its_base_and_checksums(
    base: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, shas = base

    sums = write_report(plan_migration(repo, "HEAD"), tmp_path)

    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["base_commit"] == shas["r4"]
    assert data["counts"]["restored_tombstones"] == 2
    assert data["counts"]["history_files_without_manifest_record"] == 1
    assert data["planned_files_sha256"]["planned/slugs.json"] == sums["planned/slugs.json"]
    sums_file = (tmp_path / SUMS_FILENAME).read_text(encoding="utf-8")
    assert f"{sums['report.md']}  report.md\n" in sums_file
    assert "x-2026" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_a_repaired_corpus_has_nothing_to_do(base: tuple[Path, dict[str, str]]) -> None:
    repo, _ = base
    apply_plan(repo, plan_migration(repo, "HEAD"))
    commit_all(repo, "migration: repair document identity (#871)")

    again = plan_migration(repo, "HEAD")

    assert again.nothing_to_do
    assert again.restored == []
    assert again.renames == []
    assert again.changed_paths == []


def test_the_repair_is_one_commit_that_revert_undoes(base: tuple[Path, dict[str, str]]) -> None:
    repo, shas = base
    apply_plan(repo, plan_migration(repo, "HEAD"))
    commit_all(repo, "migration: repair document identity (#871)")
    assert 'slug: "x-2026"' in (repo / "forskrifter/x-2026.md").read_text(encoding="utf-8")

    git(repo, "revert", "--no-edit", "HEAD")

    assert tree_of(repo) == tree_of(repo, shas["r4"])


def test_a_doc_id_no_manifest_ever_held_blocks_the_plan(base: tuple[Path, dict[str, str]]) -> None:
    repo, _ = base
    _history(repo, "ghost", "sf-19990101-0009")
    commit_all(repo, "a history file nobody recorded")

    plan = plan_migration(repo, "HEAD")

    assert plan.unrecoverable == ["sf-19990101-0009"]
    assert plan.blocked


def test_a_base_without_a_manifest_is_refused(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "lovverk")
    write_doc(repo, "README.md", "corpus\n")
    commit_all(repo, "init")

    with pytest.raises(IdentityMigrationError, match="no manifest.json at the base"):
        plan_migration(repo, "HEAD")


def test_an_unreadable_manifest_revision_is_refused_by_commit(
    base: tuple[Path, dict[str, str]],
) -> None:
    repo, _ = base
    (repo / "manifest.json").write_text("{broken", encoding="utf-8")
    broken = commit_all(repo, "broken manifest")
    write_manifest_json(repo, {KEEP: record(KEEP, "c")}, "2026-11-01T00:00:00Z")
    commit_all(repo, "fixed manifest")

    with pytest.raises(IdentityMigrationError, match=broken):
        plan_migration(repo, "HEAD")


def test_an_unknown_base_is_refused(base: tuple[Path, dict[str, str]]) -> None:
    repo, _ = base

    with pytest.raises(IdentityMigrationError, match="rev-parse"):
        plan_migration(repo, "no-such-ref")


def test_the_dry_run_leaves_the_checkout_untouched(
    base: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, _ = base
    before = (git(repo, "status", "--porcelain"), git(repo, "for-each-ref"), tree_of(repo))

    write_report(plan_migration(repo, "HEAD"), tmp_path)

    assert (git(repo, "status", "--porcelain"), git(repo, "for-each-ref"), tree_of(repo)) == before


def test_cli_writes_the_report_and_prints_the_checksums(
    base: tuple[Path, dict[str, str]],
    tmp_path: Path,
) -> None:
    repo, shas = base
    out = tmp_path / "report"

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "identity-plan",
            "--repo",
            str(repo),
            "--base",
            "HEAD",
            "--u1-threshold",
            "HEAD",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["base_commit"] == shas["r4"]
    assert summary["sha256"]["report.json"]
    assert (out / "report.json").exists()


def test_cli_exits_3_on_a_blocked_plan(base: tuple[Path, dict[str, str]], tmp_path: Path) -> None:
    repo, _ = base
    _history(repo, "ghost", "sf-19990101-0009")
    commit_all(repo, "a history file nobody recorded")

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "identity-plan",
            "--repo",
            str(repo),
            "--base",
            "HEAD",
            "--u1-threshold",
            "HEAD",
            "--out",
            str(tmp_path / "r"),
        ],
    )

    assert result.exit_code == 3
    assert (tmp_path / "r" / "report.json").exists()


def test_cli_refuses_an_output_directory_inside_the_checkout(
    base: tuple[Path, dict[str, str]],
) -> None:
    repo, _ = base
    out = repo / "reports"

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "identity-plan",
            "--repo",
            str(repo),
            "--base",
            "HEAD",
            "--u1-threshold",
            "HEAD",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 2
    assert "inside the corpus checkout" in result.output
    assert not out.exists()


def test_cli_exits_2_on_an_unreadable_base(
    base: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, _ = base

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "identity-plan",
            "--repo",
            str(repo),
            "--base",
            "nope",
            "--u1-threshold",
            "nope",
            "--out",
            str(tmp_path / "r"),
        ],
    )

    assert result.exit_code == 2
    assert not (tmp_path / "r").exists()


def test_report_checksums_match_every_written_byte(
    base: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    """The report's integrity claims must describe the artifacts on disk."""
    repo, _ = base
    out = tmp_path / "checksums"
    sums = write_report(plan_migration(repo, "HEAD"), out)

    actual = {
        path.relative_to(out).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in out.rglob("*")
        if path.is_file() and path.name != SUMS_FILENAME
    }
    assert sums == actual
    assert (out / SUMS_FILENAME).read_text(encoding="utf-8") == "".join(
        f"{digest}  {name}\n" for name, digest in sorted(actual.items())
    )
    data = json.loads((out / "report.json").read_bytes())
    assert data["planned_files_sha256"] == {
        name: digest for name, digest in actual.items() if name.startswith("planned/")
    }
    changed_paths = "".join(f"{path}\n" for path in data["changed_paths"])
    assert data["changed_paths_sha256"] == hashlib.sha256(changed_paths.encode("utf-8")).hexdigest()


def test_historical_base_excludes_later_commits_and_dirty_files(
    base: tuple[Path, dict[str, str]],
) -> None:
    """The plan reads the named commit through git, not HEAD or working files."""
    repo, shas = base
    expected = plan_migration(repo, shas["r2"])
    (repo / "manifest.json").write_bytes(b"not JSON\xff")
    (repo / "slugs.json").write_text("broken registry", encoding="utf-8")
    _history(repo, "untracked", "unknown-id")
    before = git(repo, "status", "--porcelain")

    actual = plan_migration(repo, shas["r2"])

    assert actual == expected
    assert actual.manifest_revisions == 2
    assert set(actual.planned_manifest.documents) == {OLD, KEEP, LOST}
    assert actual.restored == []
    assert actual.renames == []
    assert actual.unrecoverable == []
    assert git(repo, "status", "--porcelain") == before
    assert (repo / "manifest.json").read_bytes() == b"not JSON\xff"


def test_cli_refuses_output_through_a_symlink_into_the_checkout(
    base: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    """The documented outside-checkout restriction applies to resolved paths."""
    repo, _ = base
    alias = tmp_path / "corpus-alias"
    alias.symlink_to(repo, target_is_directory=True)
    before = git(repo, "status", "--porcelain")

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "identity-plan",
            "--repo",
            str(repo),
            "--base",
            "HEAD",
            "--u1-threshold",
            "HEAD",
            "--out",
            str(alias / "reports"),
        ],
    )

    assert result.exit_code == 2
    assert "inside the corpus checkout" in result.output  # Plain typer.echo error line.
    assert not (repo / "reports").exists()
    assert git(repo, "status", "--porcelain") == before


# --- U1 threshold (owner decision on #871: lovverk f6d7e25bb) ---


def test_a_reuse_after_the_threshold_is_renamed(base: tuple[Path, dict[str, str]]) -> None:
    repo, shas = base

    plan = plan_migration(repo, "HEAD", shas["r3"])

    assert plan.threshold.commit == shas["r3"]
    assert plan.threshold.problem is None
    assert [(r.doc_id, r.to_slug) for r in plan.renames] == [(NEW, "x-2026")]
    assert plan.planned_registry.owner_of("forskrifter", "x") == OLD
    assert not plan.blocked


def test_a_reuse_at_the_threshold_keeps_the_url_and_records_the_prior_holder(
    base: tuple[Path, dict[str, str]],
) -> None:
    repo, shas = base

    plan = plan_migration(repo, "HEAD", shas["r4"])

    entry = plan.planned_registry.slugs["forskrifter"]["x"]
    assert entry.owner == NEW
    assert entry.assigned_at == "2026-10-10T00:00:00+00:00"
    assert [(p.doc_id, p.first_commit, p.last_commit) for p in entry.prior_holders] == [
        (OLD, shas["r1"], shas["r2"])
    ]
    assert plan.renames == []
    assert plan.planned_manifest.documents[NEW].markdown_path == "forskrifter/x.md"
    assert [(f.owner, f.reuse) for f in plan.fates] == [(NEW, ["pre-threshold"])]
    assert [c.outcome for c in plan.collisions] == [
        "the holder keeps the slug; the restored document is recorded as a prior holder"
    ]
    assert plan.history_mismatches == []


def test_recorded_prior_holders_reach_the_registry_file_and_the_report(
    base: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, shas = base

    write_report(plan_migration(repo, "HEAD", shas["r4"]), tmp_path)

    registry = json.loads((tmp_path / "planned/slugs.json").read_text(encoding="utf-8"))
    assert registry["slugs"]["forskrifter"]["x"]["prior_holders"] == [
        {"doc_id": OLD, "first_commit": shas["r1"], "last_commit": shas["r2"]}
    ]
    assert "prior_holders" not in registry["slugs"]["forskrifter"]["c"]
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["u1_threshold"]["commit"] == shas["r4"]
    assert registry["slugs"]["forskrifter"]["x"]["basis"] == "threshold-exception"
    assert registry["slugs"]["forskrifter"]["c"]["basis"] == "first-holder"
    assert data["counts"]["threshold_exceptions"] == 1
    assert [(v["slug"], v["basis"]) for v in data["threshold_exceptions"]] == [
        ("x", "threshold-exception")
    ]
    assert "not a claim of first ownership" in data["threshold_exception_note"]
    report_md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## Threshold exceptions (owner set by the U1 threshold, not the first holder)" in (
        report_md
    )


@pytest.mark.parametrize("threshold", ["no-such-ref", "readme-commit"])
def test_a_threshold_missing_from_the_manifest_history_blocks_the_plan(
    base: tuple[Path, dict[str, str]], tmp_path: Path, threshold: str
) -> None:
    repo, _ = base
    write_doc(repo, "README.md", "x\n")
    commit_all(repo, "a commit that does not touch the manifest")
    git(repo, "tag", "readme-commit")

    plan = plan_migration(repo, "HEAD", threshold)

    assert plan.blocked
    assert plan.threshold.problem is not None
    result = CliRunner().invoke(
        app,
        [
            "ops",
            "identity-plan",
            "--repo",
            str(repo),
            "--base",
            "HEAD",
            "--u1-threshold",
            threshold,
            "--out",
            str(tmp_path / "r"),
        ],
    )
    assert result.exit_code == 3
    assert json.loads((tmp_path / "r" / "report.json").read_bytes())["blocked"] is True


def test_every_orphan_history_doc_id_is_restored(base: tuple[Path, dict[str, str]]) -> None:
    repo, _ = base

    plan = plan_migration(repo, "HEAD")

    assert plan.orphan_history_doc_ids == [LOST]  # x.json was rewritten to name NEW
    assert plan.orphans_not_restored == []
