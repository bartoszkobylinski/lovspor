"""mutation-result.json names timed-out and suspicious mutants, not only survivors (#776).

PR #773: `mutants.timeout = 1`, `survivors = []`, and the one name that mattered,
`lovspor.forarbeider.corpus_tree.x__read_public__mutmut_6`, sat only in
`mutation-raw.log`. Remediation and triage started blind.

Two sources, in order of what they prove:

- `--timeouts-file` / `--suspicious-file`: `mutation_survivors.py` detail (file,
  symbol, diff) for the ids `mutmut results` lists as `timeout` / `suspicious`,
  or a bare id list when that step could not run;
- otherwise the raw log's own "Mutant results" section, one `⏰ <id>` or
  `🤔 <id>` line per mutant, read as ids only and labelled so.

Nothing here changes a count, the score or the gate: the lists explain the
verdict, they never move it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from tests.unit.test_agentic_ci_scripts import (
    FULL_SHA,
    _progress_line,
    mutation_gate,
    mutation_to_json,
)

TIMEOUT_ID = "lovspor.forarbeider.corpus_tree.x__read_public__mutmut_6"
SUSPICIOUS_ID = "lovspor.forarbeider.page.xǁReaderǁread__mutmut_12"
SURVIVOR_ID = "lovspor.forarbeider.page.x__title__mutmut_3"


def _raw_log(*, timeout: int = 1, suspicious: int = 1, results: str | None = None) -> str:
    """A log shaped like scripts/mutmut-pr.sh output on a real red run."""
    section = (
        results
        if results is not None
        else (
            "Mutant results\n--------------\n"
            f"🎉 lovspor.forarbeider.page.x__title__mutmut_1\n"
            f"⏰ {TIMEOUT_ID}\n"
            f"🤔 {SUSPICIOUS_ID}\n"
        )
    )
    return (
        "budget: 3600s for 1 changed file(s)\n"
        "\r"
        + _progress_line(killed=40, timeout=timeout, suspicious=suspicious)
        + "\n12.50 mutations/second\n\n"
        + section
        + "\n=== surviving mutants\n\n"
        f"killed:     40 / {40 + timeout + suspicious}\nsurvived:   0\n"
        f"timed out:  {timeout}\nsuspicious: {suspicious}\n"
    )


def _detail(mutant_id: str, file: str = "src/lovspor/forarbeider/corpus_tree.py") -> str:
    return json.dumps(
        {
            "id": mutant_id,
            "file": file,
            "line": None,
            "symbol": "_read_public",
            "class": None,
            "symbol_line": 120,
            "operator": None,
            "diff": (
                "--- a\n+++ b\n-    flags = os.O_RDONLY | os.O_NONBLOCK\n+    flags = os.O_RDONLY"
            ),
            "detail_source": "mutants_shadow_tree",
        }
    )


def _run(tmp_path: Path, raw: str, **files: str) -> dict[str, object]:
    raw_file = tmp_path / "raw.log"
    raw_file.write_text(raw)
    out = tmp_path / "result.json"
    argv = ["--commit", FULL_SHA, "--raw", str(raw_file), "--tool-exit-code", "12"]
    argv += ["--out", str(out)]
    for flag, text in files.items():
        path = tmp_path / f"{flag}.txt"
        path.write_text(text)
        argv += [f"--{flag.replace('_', '-')}-file", str(path)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.argv", ["mutation_to_json.py", *argv])
        assert mutation_to_json.main() == 0
    return json.loads(out.read_text())  # type: ignore[no-any-return]


def test_the_detail_files_name_each_bucket_with_file_symbol_and_diff(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _raw_log(),
        timeouts=_detail(TIMEOUT_ID) + "\n",
        suspicious=_detail(SUSPICIOUS_ID, "src/lovspor/forarbeider/page.py") + "\n",
    )

    [timed_out] = result["timed_out"]  # type: ignore[misc]
    assert timed_out["id"] == TIMEOUT_ID
    assert timed_out["file"] == "src/lovspor/forarbeider/corpus_tree.py"
    assert timed_out["symbol"] == "_read_public"
    assert "O_NONBLOCK" in timed_out["diff"]
    assert [entry["id"] for entry in result["suspicious"]] == [SUSPICIOUS_ID]  # type: ignore[union-attr]
    assert result["survivors"] == []


def test_without_detail_files_the_raw_log_still_names_them(tmp_path: Path) -> None:
    result = _run(tmp_path, _raw_log())

    [timed_out] = result["timed_out"]  # type: ignore[misc]
    assert timed_out["id"] == TIMEOUT_ID
    assert timed_out["diff"] is None
    assert timed_out["detail_source"] == "id_only: named in the raw log"
    assert [entry["id"] for entry in result["suspicious"]] == [SUSPICIOUS_ID]  # type: ignore[union-attr]


@pytest.mark.parametrize("contents", [None, "", " \n\t\n"], ids=["missing", "empty", "blank"])
def test_unavailable_detail_files_fall_back_independently(
    tmp_path: Path, contents: str | None
) -> None:
    """The documented raw-log fallback applies to each bucket separately."""
    unavailable = tmp_path / "timeouts.jsonl"
    if contents is not None:
        unavailable.write_text(contents)
    detailed = tmp_path / "suspicious.jsonl"
    detail = json.loads(_detail(SUSPICIOUS_ID))
    detailed.write_text(json.dumps(detail) + "\n")

    result = mutation_to_json.parse_unverdicted(_raw_log(), unavailable, detailed)

    assert result["timed_out"] == [
        dict.fromkeys(mutation_to_json.SURVIVOR_KEYS, None)
        | {"id": TIMEOUT_ID, "detail_source": "id_only: named in the raw log"}
    ]
    assert result["suspicious"] == [detail]


@pytest.mark.parametrize(("key", "emoji"), [("timed_out", "⏰"), ("suspicious", "🤔")])
def test_detail_source_takes_precedence_over_raw_names(
    tmp_path: Path, key: str, emoji: str
) -> None:
    """Raw names are a fallback, not additional evidence over a collected list."""
    path = tmp_path / "detail.jsonl"
    detail = json.loads(_detail(TIMEOUT_ID))
    path.write_text(json.dumps(detail) + "\n")

    result = mutation_to_json.parse_unverdicted(
        f"{emoji} {SUSPICIOUS_ID}\n",
        path if key == "timed_out" else None,
        path if key == "suspicious" else None,
    )

    assert result[key] == [detail]


@pytest.mark.parametrize("with_lists", [False, True], ids=["old-artifact", "named-artifact"])
def test_additive_lists_do_not_change_the_gate_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_lists: bool
) -> None:
    """Schema 1 remains valid without lists; diagnostic names never clear a red gate."""
    result = _run(tmp_path, _raw_log())
    assert result["schema_version"] == 1
    if not with_lists:
        del result["timed_out"]
        del result["suspicious"]
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(result))
    monkeypatch.setattr("sys.argv", ["mutation_gate.py", str(path)])

    assert mutation_gate.main() == 1
    monkeypatch.setattr("sys.argv", ["mutation_gate.py", "--summary", str(path)])
    assert mutation_gate.main() == 0


def test_a_bare_id_list_is_read_as_ids(tmp_path: Path) -> None:
    result = _run(tmp_path, _raw_log(suspicious=0), timeouts=f"{TIMEOUT_ID}\n")

    assert [entry["id"] for entry in result["timed_out"]] == [TIMEOUT_ID]  # type: ignore[union-attr]
    assert result["timed_out"][0]["detail_source"] == "id_only: no detail collected"  # type: ignore[index]


@pytest.mark.parametrize(
    "line",
    [
        f"⏰ {TIMEOUT_ID}",
        f"⏰  {TIMEOUT_ID}  ",
        f"  ⏰ {TIMEOUT_ID}",
        f"⏰ {TIMEOUT_ID}\r",
    ],
    ids=["plain", "padded", "indented", "crlf"],
)
def test_raw_log_variants_are_read(tmp_path: Path, line: str) -> None:
    raw = _raw_log(suspicious=0, results=f"Mutant results\n--------------\n{line}\n")

    assert [entry["id"] for entry in _run(tmp_path, raw)["timed_out"]] == [TIMEOUT_ID]  # type: ignore[union-attr]


def test_the_progress_line_and_the_totals_are_not_mistaken_for_mutants(tmp_path: Path) -> None:
    raw = _raw_log(results="")

    result = _run(tmp_path, raw)

    assert result["timed_out"] == []
    assert result["suspicious"] == []
    assert result["mutants"]["timeout"] == 1  # type: ignore[index]


def test_a_mutant_named_twice_is_listed_once(tmp_path: Path) -> None:
    raw = _raw_log(suspicious=0, results=f"⏰ {TIMEOUT_ID}\n⏰ {TIMEOUT_ID}\n")

    assert len(_run(tmp_path, raw)["timed_out"]) == 1  # type: ignore[arg-type]


def test_the_lists_never_move_the_counts_score_or_gate(tmp_path: Path) -> None:
    named = _run(tmp_path, _raw_log(), timeouts=_detail(TIMEOUT_ID) + "\n")
    bare = _run(tmp_path, _raw_log(results=""))

    for key in ("mutants", "score", "gate", "survivors", "completed"):
        assert named[key] == bare[key]
    assert named["gate"]["reason"] == "timeout_mutants"  # type: ignore[index]


def test_an_all_killed_run_lists_nothing(tmp_path: Path) -> None:
    result = _run(tmp_path, _progress_line(killed=5))

    assert result["timed_out"] == []
    assert result["suspicious"] == []
    assert result["mutants"]["timeout"] == 0  # type: ignore[index]


def test_the_summary_lists_the_timed_out_mutant(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = _run(tmp_path, _raw_log(suspicious=0), timeouts=_detail(TIMEOUT_ID) + "\n")
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(result))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.argv", ["mutation_gate.py", "--summary", str(path)])
        mutation_gate.main()

    summary = capsys.readouterr().out
    assert "- Timed out:" in summary
    assert f"`{TIMEOUT_ID}` — src/lovspor/forarbeider/corpus_tree.py:120" in summary


def test_the_blocked_wording_points_at_the_list() -> None:
    assert "does not list them" not in mutation_gate.BLOCKERS["timeout"]
    assert "`timed_out`" in mutation_gate.BLOCKERS["timeout"]
    assert "`suspicious`" in mutation_gate.BLOCKERS["invalid"]
    assert SURVIVOR_ID.endswith("_3")


_REPO = Path(__file__).parents[2]


def _workflow_steps() -> dict[str, str]:
    workflow = yaml.safe_load((_REPO / ".github/workflows/pr-pipeline.yml").read_text())
    return {
        step["name"]: step["run"]
        for step in workflow["jobs"]["mutation"]["steps"]
        if "name" in step and "run" in step
    }


def _run_job_steps(tmp_path: Path, results: str, raw: str) -> dict[str, object]:
    """Collect + build as the mutation job runs them, with `uv` stubbed to this interpreter."""
    (tmp_path / "scripts").symlink_to(_REPO / "scripts")
    (tmp_path / "mutation-equivalents").symlink_to(_REPO / "mutation-equivalents")
    (tmp_path / "mutation-raw.log").write_text(raw)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "results.txt").write_text(results)
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'if [ "$2" = mutmut ]; then cat "$RESULTS"; exit 0; fi\n'
        f'shift; [ "$1" = python ] && shift; exec "{sys.executable}" "$@"\n'
    )
    python = bin_dir / "python"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    for stub in (uv, python):
        stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RESULTS": str(tmp_path / "results.txt"),
    }
    steps = _workflow_steps()
    values = {
        "${{ github.event.pull_request.head.sha }}": FULL_SHA,
        "${{ steps.mut.outputs.exit_code || '3' }}": "4",
    }
    for name in ("Collect survivors, with what each one changed", "Build mutation-result.json"):
        script = steps[name]
        for expression, value in values.items():
            script = script.replace(expression, value)
        done = subprocess.run(
            ["bash", "-e", "-c", script],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr
    return json.loads((tmp_path / "mutation-result.json").read_text())  # type: ignore[no-any-return]


def test_the_mutation_job_names_the_timed_out_mutant_end_to_end(tmp_path: Path) -> None:
    results = (
        f"    {TIMEOUT_ID}: timeout\n    {SUSPICIOUS_ID}: suspicious\n"
        "    other__mutmut_1: no tests\n"
    )

    result = _run_job_steps(tmp_path, results, _raw_log(results=""))

    assert [entry["id"] for entry in result["timed_out"]] == [TIMEOUT_ID]  # type: ignore[union-attr]
    assert [entry["id"] for entry in result["suspicious"]] == [SUSPICIOUS_ID]  # type: ignore[union-attr]
    # No shadow tree in this sandbox: the ids come through, and say so.
    assert str(result["timed_out"][0]["detail_source"]).startswith("id_only")  # type: ignore[index]
    assert result["survivors"] == []
