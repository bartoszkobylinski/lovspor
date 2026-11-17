"""The nightly mutation ledger: results carried from one night to the next (#943)."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PY = REPO_ROOT / "scripts" / "ci" / "mutation_ledger.py"
LEDGER_SH = REPO_ROOT / "scripts" / "ci" / "mutation_ledger.sh"
LEGACY_PY = REPO_ROOT / "scripts" / "ci" / "mutation_legacy.py"
KILLED, SURVIVED, TIMEOUT, NO_TESTS = 1, 0, 36, 33
F = "lovspor.mod.x_f__mutmut_"
G = "lovspor.mod.x_g__mutmut_"


def _ledger() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mutation_ledger", LEDGER_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ledger = _ledger()


def _meta(exit_codes: dict[str, int | None]) -> dict[str, object]:
    return {
        "exit_code_by_key": exit_codes,
        "hash_by_function_name": {"x_f": "h"},
        "type_check_error_by_key": {},
        "durations_by_key": {},
        "estimated_durations_by_key": {},
    }


def _write_meta(root: Path, rel: str, exit_codes: dict[str, int | None]) -> Path:
    path = root / "mutants" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_meta(exit_codes)), encoding="utf-8")
    return path


class TestSelect:
    def test_an_unmeasured_function_stays_one_glob(self) -> None:
        results = {F + "1": None, F + "2": None}
        assert ledger.select([F + "*"], results) == [F + "*"]

    def test_a_fully_measured_function_is_not_run_again(self) -> None:
        results = {F + "1": KILLED, F + "2": KILLED, G + "1": None}
        assert ledger.select([F + "*", G + "*"], results) == [G + "*"]

    def test_a_partly_measured_function_names_only_its_unmeasured_mutants(self) -> None:
        results = {F + "1": KILLED, F + "10": None, F + "2": None}
        assert ledger.select([F + "*"], results) == [F + "2", F + "10"]

    def test_the_prefix_does_not_reach_a_function_whose_name_extends_it(self) -> None:
        results = {F + "1": KILLED, F + "2": None, "lovspor.mod.x_f__mutmut_x__mutmut_1": None}
        assert ledger.select([F + "*"], results) == [F + "2"]

    def test_explicit_names_run_only_while_unmeasured(self) -> None:
        results = {F + "4": KILLED, F + "5": None}
        assert ledger.select([F + "4", F + "5"], results) == [F + "5"]

    def test_a_glob_that_matches_no_mutant_is_left_to_mutmut(self) -> None:
        assert ledger.select([G + "*"], {F + "1": None}) == [G + "*"]

    def test_any_other_wildcard_is_passed_through(self) -> None:
        assert ledger.select(["lovspor.mod.*"], {F + "1": KILLED}) == ["lovspor.mod.*"]

    def test_with_nothing_left_one_measured_mutant_in_scope_prints_the_tally(self) -> None:
        results = {F + "2": KILLED, F + "1": KILLED, G + "1": None}
        assert ledger.select([F + "*"], results) == [F + "1"]

    def test_no_pattern_selects_nothing(self) -> None:
        assert ledger.select([], {F + "1": None}) == []


class TestUnpack:
    def _archive(self, tmp_path: Path, members: dict[str, bytes]) -> Path:
        archive = tmp_path / "ledger.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return archive

    def test_killed_survived_and_timeout_results_are_carried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        codes = {
            F + "1": KILLED,
            F + "2": SURVIVED,
            F + "3": TIMEOUT,
            F + "4": NO_TESTS,
            F + "5": None,
        }
        archive = self._archive(
            tmp_path, {"mutants/src/lovspor/mod.py.meta": json.dumps(_meta(codes)).encode()}
        )
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)

        tally = ledger.unpack(archive)

        restored = json.loads((work / "mutants/src/lovspor/mod.py.meta").read_text())
        assert restored["exit_code_by_key"] == {
            F + "1": KILLED,
            F + "2": SURVIVED,
            F + "3": TIMEOUT,
            F + "4": None,
            F + "5": None,
        }
        assert restored["hash_by_function_name"] == {"x_f": "h"}
        assert (tally.files, tally.kept, tally.reset, tally.dropped) == (1, 3, 1, 0)

    def test_a_meta_cut_short_by_a_checkpoint_is_dropped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive = self._archive(tmp_path, {"mutants/src/lovspor/mod.py.meta": b'{"exit_code_by'})
        monkeypatch.chdir(tmp_path)

        tally = ledger.unpack(archive)

        assert not (tmp_path / "mutants/src/lovspor/mod.py.meta").exists()
        assert (tally.files, tally.dropped) == (0, 1)

    @pytest.mark.parametrize(
        "name",
        ["mutants/../escape.meta", "elsewhere/mod.py.meta", "mutants/src/mod.py", "/abs/x.meta"],
    )
    def test_nothing_but_a_meta_under_mutants_is_extracted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        archive = self._archive(tmp_path, {name: json.dumps(_meta({})).encode()})
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)

        tally = ledger.unpack(archive)

        assert tally.files == 0
        assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == ["ledger.tar.gz"]


def test_pack_then_unpack_round_trips_every_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "night1"
    _write_meta(source, "src/lovspor/a.py.meta", {F + "1": KILLED})
    _write_meta(source, "src/lovspor/sub/b.py.meta", {G + "1": KILLED})
    (source / "mutants" / "src" / "lovspor" / "a.py").write_text("not a meta")
    archive = tmp_path / "ledger.tar.gz"
    monkeypatch.chdir(source)
    assert ledger.pack(archive) == 2
    assert not archive.with_name("ledger.tar.gz.partial").exists()
    night2 = tmp_path / "night2"
    night2.mkdir()
    monkeypatch.chdir(night2)

    ledger.unpack(archive)

    assert ledger.results_by_key(night2) == {F + "1": KILLED, G + "1": KILLED}
    assert not (night2 / "mutants" / "src" / "lovspor" / "a.py").exists()


# The premise the whole ledger stands on, asserted against the installed mutmut
# rather than carried from reading its source: a restored result survives
# regeneration while its function is unchanged, and is reset when it changed.
PYPROJECT = '[tool.mutmut]\nsource_paths = ["src/lovspor/"]\n'
TWO_FUNCTIONS = "def f(a):\n    return a + 1\n\n\ndef g(a):\n    return a * 2\n"


def _generate(repo: Path) -> None:
    subprocess.run(
        [sys.executable, str(LEGACY_PY), "generate", "--jobs", "1"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": ""},
    )


def _mark_all(repo: Path, code: int) -> None:
    meta = repo / "mutants" / "src" / "lovspor" / "mod.py.meta"
    data = json.loads(meta.read_text())
    data["exit_code_by_key"] = dict.fromkeys(data["exit_code_by_key"], code)
    meta.write_text(json.dumps(data))


def _night_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / "src" / "lovspor").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(PYPROJECT)
    (repo / "src" / "lovspor" / "__init__.py").write_text("")
    (repo / "src" / "lovspor" / "mod.py").write_text(TWO_FUNCTIONS)
    _generate(repo)
    _mark_all(repo, KILLED)
    archive = tmp_path / "ledger.tar.gz"
    monkeypatch.chdir(repo)
    ledger.pack(archive)
    subprocess.run(["rm", "-rf", str(repo / "mutants")], check=True)
    return repo, archive


def test_mutmut_keeps_a_restored_result_only_while_its_function_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, archive = _night_one(tmp_path, monkeypatch)
    (repo / "src" / "lovspor" / "mod.py").write_text(TWO_FUNCTIONS.replace("a * 2", "a * 3"))

    ledger.unpack(archive)
    _generate(repo)

    results = ledger.results_by_key(repo)
    f_codes = {v for k, v in results.items() if ".x_f__" in k}
    g_codes = {v for k, v in results.items() if ".x_g__" in k}
    assert f_codes == {KILLED}
    assert g_codes == {None}
    assert ledger.select([F + "*", G + "*"], results) == [G + "*"]


class TestLedgerBranch:
    """mutation_ledger.sh against a real bare `origin`."""

    def _clone(self, tmp_path: Path) -> Path:
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        work = tmp_path / "work"
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=work, check=True)
        _write_meta(work, "src/lovspor/mod.py.meta", {F + "1": KILLED})
        return work

    def _sh(self, work: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(LEDGER_SH), *args],
            cwd=work,
            capture_output=True,
            text=True,
            env={**os.environ, "MUTATION_LEDGER_PYTHON": sys.executable, "GITHUB_RUN_ID": "7"},
            check=False,
        )

    def test_a_saved_ledger_is_fetched_for_its_own_base(self, tmp_path: Path) -> None:
        work = self._clone(tmp_path)
        assert self._sh(work, "save", "b" * 40, "h" * 40).returncode == 0

        out = tmp_path / "restored.tar.gz"
        done = self._sh(work, "fetch", "b" * 40, str(out))

        assert done.returncode == 0, done.stderr
        assert self._sh(work, "base").stdout.strip() == "b" * 40
        with tarfile.open(out, "r:gz") as tar:
            assert tar.getnames() == ["mutants/src/lovspor/mod.py.meta"]

    def test_a_ledger_for_another_base_is_ignored(self, tmp_path: Path) -> None:
        work = self._clone(tmp_path)
        self._sh(work, "save", "b" * 40, "h" * 40)

        out = tmp_path / "restored.tar.gz"
        done = self._sh(work, "fetch", "c" * 40, str(out))

        assert done.returncode == 0
        assert "ignoring it" in done.stdout
        assert not out.exists()

    def test_no_ledger_is_a_whole_range_not_an_error(self, tmp_path: Path) -> None:
        work = self._clone(tmp_path)
        out = tmp_path / "restored.tar.gz"

        done = self._sh(work, "fetch", "b" * 40, str(out))

        assert done.returncode == 0
        assert self._sh(work, "base").stdout == ""
        assert not out.exists()

    def test_drop_deletes_the_branch_and_is_idempotent(self, tmp_path: Path) -> None:
        work = self._clone(tmp_path)
        self._sh(work, "save", "b" * 40, "h" * 40)

        assert self._sh(work, "drop").returncode == 0
        assert self._sh(work, "drop").returncode == 0
        assert self._sh(work, "base").stdout == ""
