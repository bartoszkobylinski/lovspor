"""The planner rebuilds slug histories that name a non-owner from git (#871, N2/N3).

Each fixture is a real repository: the defect lives in which commit a
deletion is filed under, which only git history can show. The base slug
history is the pre-#871 extractor's output, wrong on purpose: it names the
later holder and carries the owner's deletion as the later holder's
``updated`` event (the ``endr-i-utrullingsforskriften`` / ``5511aa4`` shape).
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.history import HistoryRecord, extract_lineage
from lovspor.identity.apply import apply_plan
from lovspor.identity.history import IdentityHistory
from lovspor.identity.plan import MigrationPlan, plan_migration
from lovspor.identity.repair import NO_IDENTITY, NOT_AN_EVENT
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

OWNER = "sf-20250101-0001"  # first holder of "x": moved onto it, then removed
LATER = "sf-20260601-0002"  # took "x" after the threshold; current, so renamed
KEEP = "sf-20250101-0003"
OLD_PATH = f"forskrifter/{OWNER}.md"
PATH = "forskrifter/x.md"
SLUG_JSON = "forskrifter/history/x.json"
NEW_JSON = "forskrifter/history/x-2026.json"


def _owner_by_id() -> str:
    return f"forskrifter/history/by-id/{OWNER}.json"


def _later_by_id() -> str:
    return f"forskrifter/history/by-id/{LATER}.json"


def _manifest(repo: Path, documents: dict[str, dict], day: str) -> None:
    write_manifest_json(repo, {KEEP: record(KEEP, "c"), **documents}, f"2026-{day}T00:00:00Z")


def _wrong_slug_history(repo: Path, shas: dict[str, str]) -> None:
    """The pre-#871 file: the later holder's name over the owner's deletion."""
    events = [
        {"commit": shas["later_add"][:7], "date": "2026-06-01", "type": "added"},
        {"commit": shas["owner_remove"][:7], "date": "2026-06-01", "type": "updated"},
    ]
    payload = {"doc_id": LATER, "events": events, "schema_version": 1, "slug": "x"}
    write_doc(repo, SLUG_JSON, json.dumps(payload) + "\n")


def _owner_era(repo: Path) -> dict[str, str]:
    write_doc(repo, PATH, "legacy text without front matter\n")
    write_doc(repo, "forskrifter/c.md", doc_text(KEEP, "c", "body"))
    _manifest(repo, {}, "04-01")
    shas = {"legacy_add": commit_all(repo, "sync: 1 new, 0 changed, 0 removed")}
    (repo / PATH).unlink()
    shas["legacy_remove"] = commit_all(repo, "sync: 0 new, 0 changed, 1 removed")
    write_doc(repo, OLD_PATH, doc_text(OWNER, "x", "owner body\nline two\nline three"))
    _manifest(repo, {OWNER: record(OWNER, "x")}, "05-01")
    shas["owner_add"] = commit_all(repo, "sync: 1 new, 0 changed, 0 removed")
    git(repo, "mv", OLD_PATH, PATH)
    shas["owner_move"] = commit_all(repo, "migration: rename 1 documents to slug-based filenames")
    write_doc(repo, PATH, doc_text(OWNER, "x", "owner body\nline two\nline 3"))
    shas["rerender"] = commit_all(repo, "migration: re-render 1 documents")
    return shas


def _build(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = init_repo(tmp_path / "lovverk")
    shas = _owner_era(repo)
    (repo / PATH).unlink()
    _manifest(repo, {OWNER: record(OWNER, "x", "removed")}, "05-02")
    shas["owner_remove"] = commit_all(repo, "sync: 0 new, 0 changed, 1 removed")
    _manifest(repo, {}, "07-02")
    shas["threshold"] = commit_all(repo, "sync: update manifest, index, and history")
    write_doc(repo, PATH, doc_text(LATER, "x", "later body"))
    _manifest(repo, {LATER: record(LATER, "x")}, "10-10")
    shas["later_add"] = commit_all(repo, "add(forskrift): x")
    _wrong_slug_history(repo, shas)
    shas["history"] = commit_all(repo, "sync: update manifest, index, and history")
    return repo, shas


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    return _build(tmp_path)


def _plan(corpus: tuple[Path, dict[str, str]]) -> MigrationPlan:
    repo, shas = corpus
    return plan_migration(repo, "HEAD", shas["threshold"])


def _json(plan: MigrationPlan, path: str) -> dict:
    return json.loads(plan.history_repair.files[path])


def test_every_commit_on_the_path_chain_is_attributed_by_its_own_file(
    corpus: tuple[Path, dict[str, str]],
) -> None:
    _, shas = corpus

    [repair] = _plan(corpus).history_repair.repairs

    assert [
        (e.commit, e.path, e.before_doc, e.after_doc, e.doc_id, e.rule) for e in repair.trace.events
    ] == [
        (shas["later_add"], PATH, None, LATER, LATER, "id-at-commit"),
        (shas["owner_remove"], PATH, OWNER, None, OWNER, "id-at-parent"),
        (shas["owner_move"], PATH, None, OWNER, OWNER, "id-at-commit"),
        (shas["owner_add"], OLD_PATH, None, OWNER, OWNER, "id-at-commit"),
    ]
    assert [(s.path, s.moved_to, s.moved_at) for s in repair.trace.segments] == [
        (PATH, None, None),
        (OLD_PATH, PATH, shas["owner_move"]),
    ]


def test_commits_no_document_names_are_excluded_and_listed(
    corpus: tuple[Path, dict[str, str]],
) -> None:
    _, shas = corpus

    [repair] = _plan(corpus).history_repair.repairs

    assert [(x.commit, x.reason) for x in repair.trace.excluded] == [
        (shas["rerender"], NOT_AN_EVENT),
        (shas["legacy_remove"], NO_IDENTITY),
        (shas["legacy_add"], NO_IDENTITY),
    ]


def test_the_owners_slug_history_holds_the_owners_events_only(
    corpus: tuple[Path, dict[str, str]],
) -> None:
    _, shas = corpus
    plan = _plan(corpus)

    record = HistoryRecord.model_validate(_json(plan, SLUG_JSON))

    assert (record.schema_version, record.slug, record.doc_id) == (1, "x", OWNER)
    assert [e.commit for e in record.events] == [
        shas[k][:7] for k in ("owner_remove", "owner_move", "owner_add")
    ]
    assert (record.events[1].type, record.events[1].from_path) == ("renamed", OLD_PATH)
    assert plan.history_repair.files["forskrifter/history/x.md"].count("## ") == 3


def test_the_misattributed_deletion_goes_back_to_the_owner(
    corpus: tuple[Path, dict[str, str]],
) -> None:
    _, shas = corpus

    [repair] = _plan(corpus).history_repair.repairs

    assert repair.target.file_doc_id == LATER
    assert [(e.commit, e.attributed_to) for e in repair.base_file_events] == [
        (shas["later_add"][:7], LATER),
        (shas["owner_remove"][:7], OWNER),
    ]


def test_the_renamed_holder_gets_its_new_slug_history_and_keeps_its_paths(
    corpus: tuple[Path, dict[str, str]],
) -> None:
    _, shas = corpus
    plan = _plan(corpus)

    renamed = HistoryRecord.model_validate(_json(plan, NEW_JSON))
    later = IdentityHistory.model_validate(_json(plan, _later_by_id()))

    assert (renamed.slug, renamed.doc_id) == ("x-2026", LATER)
    assert [e.commit for e in renamed.events] == [shas["later_add"][:7]]
    assert [(e.commit, e.path) for e in later.events] == [(shas["later_add"][:7], PATH)]


def test_each_document_gets_its_own_by_id_history(corpus: tuple[Path, dict[str, str]]) -> None:
    _, shas = corpus
    plan = _plan(corpus)

    owner = IdentityHistory.model_validate(_json(plan, _owner_by_id()))

    assert [(e.commit, e.path) for e in owner.events] == [
        (shas["owner_remove"][:7], PATH),
        (shas["owner_move"][:7], PATH),
        (shas["owner_add"][:7], OLD_PATH),
    ]
    assert plan.history_repair.invariant_violations == []
    assert not plan.blocked


def test_the_owners_events_are_recovered_where_a_head_walk_finds_none(
    tmp_path: Path,
) -> None:
    """A removed later holder: walking back from HEAD gives the owner nothing (N2)."""
    repo, shas = _build(tmp_path)
    (repo / PATH).unlink()
    _manifest(repo, {LATER: record(LATER, "x", "removed")}, "10-11")
    later_remove = commit_all(repo, "remove(forskrift): x")

    plan = plan_migration(repo, "HEAD", shas["threshold"])

    assert extract_lineage(repo, PATH, OWNER).steps == []
    assert plan.renames == []
    owner = HistoryRecord.model_validate(_json(plan, SLUG_JSON))
    assert [e.commit for e in owner.events] == [
        shas[k][:7] for k in ("owner_remove", "owner_move", "owner_add")
    ]
    later = IdentityHistory.model_validate(_json(plan, _later_by_id()))
    assert [e.commit for e in later.events] == [later_remove[:7], shas["later_add"][:7]]
    assert NEW_JSON not in plan.history_repair.files


def test_the_plan_lists_every_repaired_file_as_changed(corpus: tuple[Path, dict[str, str]]) -> None:
    plan = _plan(corpus)

    slug_files = [SLUG_JSON, "forskrifter/history/x.md", NEW_JSON, "forskrifter/history/x-2026.md"]
    expected = sorted([_owner_by_id(), _later_by_id(), *slug_files])
    assert plan.history_repair.changed == expected
    assert set(expected) <= set(plan.changed_paths)


def test_a_repaired_corpus_has_nothing_left_to_repair(corpus: tuple[Path, dict[str, str]]) -> None:
    repo, shas = corpus
    apply_plan(repo, _plan(corpus))
    commit_all(repo, "migration: repair document identity (#871)")

    again = plan_migration(repo, "HEAD", shas["threshold"])

    assert again.history_mismatches == []
    assert again.history_repair.repairs == []
    assert again.nothing_to_do
    assert json.loads((repo / SLUG_JSON).read_text(encoding="utf-8"))["doc_id"] == OWNER


def test_the_repair_commit_reverts_as_a_unit(corpus: tuple[Path, dict[str, str]]) -> None:
    repo, shas = corpus
    apply_plan(repo, _plan(corpus))
    commit_all(repo, "migration: repair document identity (#871)")

    git(repo, "revert", "--no-edit", "HEAD")

    assert tree_of(repo) == tree_of(repo, shas["history"])


def _by_id_at_base(repo: Path, rel_path: str, history: dict) -> str:
    write_doc(repo, rel_path, json.dumps(history) + "\n")
    return commit_all(repo, "sync: update manifest, index, and history")


def _event(commit: str, path: str, event_type: str = "updated") -> dict:
    return {
        "commit": commit,
        "date": "2026-06-01",
        "path": path,
        "subject": "s",
        "type": event_type,
    }


def test_a_by_id_event_attributed_to_another_document_blocks_the_plan(
    corpus: tuple[Path, dict[str, str]],
) -> None:
    repo, shas = corpus
    wrong = {
        "doc_id": LATER,
        "events": [_event(shas["owner_remove"][:7], PATH)],
        "schema_version": 1,
    }
    _by_id_at_base(repo, _later_by_id(), wrong)

    plan = _plan(corpus)

    assert [
        (c.file, c.commit, c.doc_in_file, c.attributed_to) for c in plan.history_repair.conflicts
    ] == [(_later_by_id(), shas["owner_remove"][:7], LATER, OWNER)]
    assert plan.history_repair.invariant_violations != []
    assert plan.blocked


def test_a_by_id_event_the_trace_does_not_know_is_kept(corpus: tuple[Path, dict[str, str]]) -> None:
    repo, _ = corpus
    prior = {
        "doc_id": OWNER,
        "events": [_event("abcdef0", "forskrifter/y.md")],
        "schema_version": 1,
    }
    _by_id_at_base(repo, _owner_by_id(), prior)

    plan = _plan(corpus)

    owner = IdentityHistory.model_validate(_json(plan, _owner_by_id()))
    assert ("abcdef0", "forskrifter/y.md") in [(e.commit, e.path) for e in owner.events]
    assert not plan.blocked


def test_a_new_slug_history_naming_another_document_blocks_the_plan(
    corpus: tuple[Path, dict[str, str]],
) -> None:
    repo, _ = corpus
    taken = {"doc_id": KEEP, "events": [], "schema_version": 1, "slug": "x-2026"}
    write_doc(repo, NEW_JSON, json.dumps(taken) + "\n")
    commit_all(repo, "sync: update manifest, index, and history")

    plan = _plan(corpus)

    assert [(c.file, c.doc_in_file, c.attributed_to) for c in plan.history_repair.conflicts] == [
        (NEW_JSON, KEEP, LATER)
    ]
    assert plan.blocked


def test_the_report_carries_each_events_provenance_and_file_hashes(
    corpus: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    _, shas = corpus
    out = tmp_path / "out"
    out.mkdir()

    sums = write_report(_plan(corpus), out)

    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    rows = {(r["commit"], r["path"]): r for r in data["history_repair_events"]}
    deletion = rows[(shas["owner_remove"], PATH)]
    assert (deletion["before_doc"], deletion["after_doc"], deletion["doc_id"]) == (
        OWNER,
        None,
        OWNER,
    )
    assert (deletion["rule"], deletion["role"]) == ("id-at-parent", "owner")
    assert data["counts"]["history_repair_events"] == 4
    assert data["counts"]["history_repair_excluded"] == 3
    assert data["history_repair_invariant_ok"] is True
    planned = f"planned/{SLUG_JSON}"
    assert sums[planned] == data["planned_files_sha256"][planned]
    assert (out / planned).read_text(encoding="utf-8") == _plan(corpus).history_repair.files[
        SLUG_JSON
    ]
    assert f"  {planned}\n" in (out / SUMS_FILENAME).read_text(encoding="utf-8")


def test_two_runs_write_identical_history_files(
    corpus: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for out in (first, second):
        out.mkdir()
        write_report(_plan(corpus), out)

    files = sorted(p.relative_to(first) for p in first.rglob("*") if p.is_file())
    assert Path("planned") / NEW_JSON in files
    assert all((first / p).read_bytes() == (second / p).read_bytes() for p in files)


def test_cli_exits_3_on_a_conflicting_by_id_history(
    corpus: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, shas = corpus
    wrong = {
        "doc_id": LATER,
        "events": [_event(shas["owner_remove"][:7], PATH)],
        "schema_version": 1,
    }
    _by_id_at_base(repo, _later_by_id(), wrong)
    args = ["ops", "identity-plan", "--repo", str(repo), "--base", "HEAD"]
    args += ["--u1-threshold", shas["threshold"], "--out", str(tmp_path / "out")]

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 3
    assert json.loads(result.output)["counts"]["history_repair_conflicts"] == 1
