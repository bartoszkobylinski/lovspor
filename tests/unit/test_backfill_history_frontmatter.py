"""Tests for ``scripts/backfill_history_frontmatter.py``.

The script is not an importable package (it lives in ``scripts/`` because it
imports the engine but is not part of it), so it is loaded from its file path
like the other script tests.
"""

import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

from lovspor.history import (
    HistoryEvent,
    HistoryRecord,
    extract_history,
    render_history_markdown,
)

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "backfill_history_frontmatter.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("backfill_history_frontmatter", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backfill = _load_script()


def _record(slug: str, doc_id: str = "nl-19990326-014") -> HistoryRecord:
    return HistoryRecord(
        slug=slug,
        doc_id=doc_id,
        events=[
            HistoryEvent(
                date=date(2026, 5, 1),
                commit="abc1234",
                type="added",
                subject=f"add(lov): {slug}",
                lines_added=10,
                lines_removed=0,
            ),
        ],
    )


def _canonical_json(record: HistoryRecord) -> str:
    return (
        json.dumps(record.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False)
        + "\n"
    )


def _legacy_body(record: HistoryRecord) -> str:
    """The pre-frontmatter file content: today's rendering minus the block."""
    rendered = render_history_markdown(record)
    body = backfill._body_after_frontmatter(rendered)
    assert body is not None
    return body


def _seed_history(
    root: Path,
    slug: str,
    *,
    subdir: str = "lover",
    legacy: bool = True,
    with_json: bool = True,
) -> Path:
    record = _record(slug)
    history_dir = root / subdir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    md_path = history_dir / f"{slug}.md"
    text = _legacy_body(record) if legacy else render_history_markdown(record)
    md_path.write_text(text, encoding="utf-8")
    if with_json:
        (history_dir / f"{slug}.json").write_text(_canonical_json(record), encoding="utf-8")
    return md_path


def _seed_both_dirs(root: Path) -> None:
    (root / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)


def test_legacy_file_plans_frontmatter_with_byte_identical_body(tmp_path: Path) -> None:
    md_path = _seed_history(tmp_path, "skatteloven")
    _seed_both_dirs(tmp_path)
    original = md_path.read_text(encoding="utf-8")

    plan = backfill.build_plan(tmp_path)

    assert plan.anomalies == []
    assert [p for p, _ in plan.writes] == [md_path]
    [(_, rendered)] = plan.writes
    assert rendered.startswith("---\n")
    assert 'source_license: "NLOD 2.0"' in rendered
    assert backfill._body_after_frontmatter(rendered) == original


def test_already_current_file_is_untouched(tmp_path: Path) -> None:
    _seed_history(tmp_path, "skatteloven", legacy=False)
    _seed_both_dirs(tmp_path)

    plan = backfill.build_plan(tmp_path)

    assert plan.writes == []
    assert plan.anomalies == []
    assert plan.already_current == 1


def test_execution_is_idempotent(tmp_path: Path) -> None:
    md_path = _seed_history(tmp_path, "skatteloven")
    _seed_both_dirs(tmp_path)

    first = backfill.build_plan(tmp_path)
    for path, rendered in first.writes:
        path.write_text(rendered, encoding="utf-8")

    second = backfill.build_plan(tmp_path)
    assert second.writes == []
    assert second.anomalies == []
    assert second.already_current == 1
    assert md_path.read_text(encoding="utf-8").startswith("---\n")


def test_missing_json_aborts_the_whole_migration(tmp_path: Path) -> None:
    """One broken pair must poison the COMPLETE plan — a partial migration
    would leave the corpus claiming a contract it does not meet."""
    _seed_history(tmp_path, "healthy")
    _seed_history(tmp_path, "broken", with_json=False)
    _seed_both_dirs(tmp_path)

    plan = backfill.build_plan(tmp_path)

    assert any(a.startswith("MISSING_JSON") for a in plan.anomalies)
    # the healthy file is still PLANNED, but main() must refuse to execute
    rc = _run_main(tmp_path, execute=True)
    assert rc == 1
    assert not (tmp_path / "lover" / "history" / "healthy.md").read_text().startswith("---")


def test_invalid_json_aborts_before_any_write(tmp_path: Path) -> None:
    _seed_history(tmp_path, "healthy")
    broken = _seed_history(tmp_path, "broken")
    broken.with_suffix(".json").write_text('{"schema_version": 1}', encoding="utf-8")
    _seed_both_dirs(tmp_path)

    rc = _run_main(tmp_path, execute=True)

    assert rc == 1
    assert not (tmp_path / "lover" / "history" / "healthy.md").read_text().startswith("---")


def test_body_mismatch_aborts_before_any_write(tmp_path: Path) -> None:
    _seed_history(tmp_path, "healthy")
    drifted = _seed_history(tmp_path, "drifted")
    drifted.write_text(
        drifted.read_text(encoding="utf-8") + "\nhand-edited drift\n",
        encoding="utf-8",
    )
    _seed_both_dirs(tmp_path)

    rc = _run_main(tmp_path, execute=True)

    assert rc == 1
    assert not (tmp_path / "lover" / "history" / "healthy.md").read_text().startswith("---")


def test_json_roundtrip_drift_aborts(tmp_path: Path) -> None:
    """A JSON the writer would re-dump differently is a source-of-truth the
    migration would owe a rewrite — refuse instead of touching it."""
    reordered = _seed_history(tmp_path, "reordered")
    record = _record("reordered")
    loose = json.dumps(record.model_dump(mode="json"), indent=4) + "\n"
    reordered.with_suffix(".json").write_text(loose, encoding="utf-8")
    _seed_both_dirs(tmp_path)

    plan = backfill.build_plan(tmp_path)

    assert any(a.startswith("JSON_ROUNDTRIP_DRIFT") for a in plan.anomalies)


def test_json_is_never_rewritten(tmp_path: Path) -> None:
    md_path = _seed_history(tmp_path, "skatteloven")
    _seed_both_dirs(tmp_path)
    json_path = md_path.with_suffix(".json")
    before = json_path.read_bytes()

    rc = _run_main(tmp_path, execute=True)

    assert rc == 0
    assert json_path.read_bytes() == before
    assert md_path.read_text(encoding="utf-8").startswith("---\n")


def test_dry_run_is_the_default_and_writes_nothing(tmp_path: Path) -> None:
    md_path = _seed_history(tmp_path, "skatteloven")
    _seed_both_dirs(tmp_path)
    before = md_path.read_bytes()

    rc = _run_main(tmp_path, execute=False)

    assert rc == 0
    assert md_path.read_bytes() == before


def _run_main(corpus: Path, *, execute: bool) -> int:
    argv = ["backfill_history_frontmatter.py", "--corpus-path", str(corpus)]
    if execute:
        argv.append("--execute")
    old_argv = sys.argv
    sys.argv = argv
    try:
        rc: int = backfill.main()
    finally:
        sys.argv = old_argv
    return rc


def test_a_leading_delimiter_without_the_contract_aborts(tmp_path: Path) -> None:
    """Codex (PR #183): a legacy body that happens to open with ``---`` was
    silently counted as already-current. Current means a CLOSED frontmatter
    block carrying the history type and the NLOD attribution; anything else
    that opens with the delimiter is an anomaly, never a skip."""
    _seed_history(tmp_path, "healthy")
    weird = tmp_path / "lover" / "history" / "weird.md"
    weird.write_text("---\n# weird — Change history\n\nlegacy body\n", encoding="utf-8")
    (tmp_path / "lover" / "history" / "weird.json").write_text(
        _canonical_json(_record("weird")),
        encoding="utf-8",
    )
    _seed_both_dirs(tmp_path)

    plan = backfill.build_plan(tmp_path)
    assert any(a.startswith("UNRECOGNIZED_FORMAT") for a in plan.anomalies)

    rc = _run_main(tmp_path, execute=True)
    assert rc == 1
    assert not (tmp_path / "lover" / "history" / "healthy.md").read_text().startswith("---")


def test_a_closed_block_missing_the_nlod_line_is_not_current(tmp_path: Path) -> None:
    weird = tmp_path / "lover" / "history"
    weird.mkdir(parents=True)
    (weird / "half.md").write_text(
        '---\ntype: "history"\nslug: "half"\n---\n\n# half — Change history\n',
        encoding="utf-8",
    )
    (weird / "half.json").write_text(_canonical_json(_record("half")), encoding="utf-8")
    _seed_both_dirs(tmp_path)

    plan = backfill.build_plan(tmp_path)

    assert any(a.startswith("UNRECOGNIZED_FORMAT") for a in plan.anomalies)


def test_a_mid_staging_failure_leaves_zero_visible_changes(tmp_path: Path) -> None:
    """Codex (PR #183): the single write loop migrated earlier files and
    abandoned later ones when a write raised mid-run. Execution now stages
    the COMPLETE set first; a staging failure unwinds every staged file and
    changes nothing visible."""
    a_path = _seed_history(tmp_path, "a-lov")
    b_path = _seed_history(tmp_path, "b-lov")
    _seed_both_dirs(tmp_path)
    before = {a_path: a_path.read_bytes(), b_path: b_path.read_bytes()}

    real_write = backfill.atomic_write_text
    calls = {"n": 0}

    def failing_write(path: Path, text: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        real_write(path, text)

    backfill.atomic_write_text = failing_write
    try:
        with pytest.raises(OSError, match="disk full"):
            _run_main(tmp_path, execute=True)
    finally:
        backfill.atomic_write_text = real_write

    assert {p: p.read_bytes() for p in before} == before
    assert list(tmp_path.rglob(f"*{backfill.STAGE_SUFFIX}")) == []

    # a clean re-run completes the migration
    assert _run_main(tmp_path, execute=True) == 0
    assert a_path.read_text(encoding="utf-8").startswith("---\n")
    assert b_path.read_text(encoding="utf-8").startswith("---\n")


# ---------- mini corpus + history-extraction invariance ----------


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _setup_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)


def test_mini_corpus_migrates_only_legacy_history_markdown(tmp_path: Path) -> None:
    """Current doc, tombstoned doc, and a history with no manifest record all
    migrate the same way; nothing outside history/*.md changes."""
    root = tmp_path / "lovverk"
    (root / "lover").mkdir(parents=True)
    doc = root / "lover" / "skatteloven.md"
    doc.write_text('---\nid: "nl-1"\n---\n\n# Skatteloven\n', encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text('{"documents": {}}', encoding="utf-8")
    _seed_history(root, "skatteloven")  # current doc
    _seed_history(root, "opphevet-lov")  # tombstoned: doc file absent
    _seed_history(root, "foreldet-lov", subdir="forskrifter")  # no manifest record

    doc_before = doc.read_bytes()
    manifest_before = manifest.read_bytes()
    jsons_before = {p: p.read_bytes() for p in root.rglob("history/*.json")}

    rc = _run_main(root, execute=True)

    assert rc == 0
    migrated = sorted(p.relative_to(root).as_posix() for p in root.rglob("history/*.md"))
    assert migrated == [
        "forskrifter/history/foreldet-lov.md",
        "lover/history/opphevet-lov.md",
        "lover/history/skatteloven.md",
    ]
    for p in root.rglob("history/*.md"):
        assert p.read_text(encoding="utf-8").startswith("---\n")
    assert doc.read_bytes() == doc_before
    assert manifest.read_bytes() == manifest_before
    assert {p: p.read_bytes() for p in root.rglob("history/*.json")} == jsons_before


def test_document_history_events_are_identical_across_the_migration(tmp_path: Path) -> None:
    """ADR-0003 invariance: the migration commit touches only history/*.md,
    which extract_history never walks — a document's event list must be
    byte-for-byte the same before and after."""
    repo = tmp_path / "lovverk"
    _setup_repo(repo)
    (repo / "lover").mkdir()
    doc = repo / "lover" / "skatteloven.md"
    doc.write_text('---\nid: "nl-1"\n---\n\nbody v1\n', encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "add(lov): skatteloven", cwd=repo)
    doc.write_text('---\nid: "nl-1"\n---\n\nbody v2\n', encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "update(lov): skatteloven", cwd=repo)

    before = extract_history(repo, "lover/skatteloven.md", "nl-1", "skatteloven")

    _seed_history(repo, "skatteloven")
    (repo / "forskrifter" / "history").mkdir(parents=True)
    assert _run_main(repo, execute=True) == 0
    _git("add", ".", cwd=repo)
    _git(
        "commit",
        "-m",
        "migration: backfill NLOD attribution in history markdown (1 files)",
        cwd=repo,
    )

    after = extract_history(repo, "lover/skatteloven.md", "nl-1", "skatteloven")

    assert after == before
