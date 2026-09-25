"""Determinism across machines: the digest a runner publishes, and the workflow comparing them.

ADR-0013 asks for a byte-identical output tree "twice, and across machines".
A test only ever runs on one machine, so what is pinned here is everything a
single machine can promise: the digest sees paths and bytes and nothing else,
the fixture build is independent of where on disk it happens, the comparison
names the files that differ, and the workflow really does run on machines
that differ (#242).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "ci" / "publish_tree_digest.py"
_WORKFLOW = _ROOT / ".github" / "workflows" / "publish-determinism.yml"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("publish_tree_digest", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


digest_script = _load()


def _write(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


FILES = {"lov/a/index.html": b"<p>a</p>", "index.json": b"{}", "lov/b/index.html": b"b"}


class TestTreeDigest:
    def test_creation_order_does_not_matter(self, tmp_path: Path) -> None:
        forward = _write(tmp_path / "one", FILES)
        backward = _write(tmp_path / "two", dict(reversed(list(FILES.items()))))

        assert digest_script.tree_digest(forward) == digest_script.tree_digest(backward)

    def test_modification_times_do_not_matter(self, tmp_path: Path) -> None:
        tree = _write(tmp_path / "one", FILES)
        before = digest_script.tree_digest(tree)
        os.utime(tree / "index.json", (1, 1))

        assert digest_script.tree_digest(tree) == before

    def test_permissions_do_not_matter(self, tmp_path: Path) -> None:
        tree = _write(tmp_path / "one", FILES)
        before = digest_script.tree_digest(tree)
        (tree / "index.json").chmod(0o444)

        assert digest_script.tree_digest(tree) == before

    def test_listing_uses_forward_slashes_in_utf8_byte_order(self, tmp_path: Path) -> None:
        tree = _write(tmp_path / "one", {"ø/x": b"1", "z/x": b"2", "a/x": b"3"})

        paths = [line.split("\t")[0] for line in digest_script.tree_listing(tree).splitlines()]

        assert paths == ["a/x", "z/x", "ø/x"]

    @pytest.mark.parametrize(
        "change",
        [
            {"index.json": b"{ }"},
            {"lov/c/index.html": b"new"},
        ],
    )
    def test_any_byte_or_file_change_moves_the_digest(
        self, tmp_path: Path, change: dict[str, bytes]
    ) -> None:
        base = _write(tmp_path / "one", FILES)
        changed = _write(tmp_path / "two", {**FILES, **change})

        assert digest_script.tree_digest(base) != digest_script.tree_digest(changed)

    def test_a_rename_moves_the_digest(self, tmp_path: Path) -> None:
        base = _write(tmp_path / "one", FILES)
        renamed = _write(tmp_path / "two", {**FILES})
        (renamed / "index.json").rename(renamed / "Index.json")

        assert digest_script.tree_digest(base) != digest_script.tree_digest(renamed)

    def test_removing_a_file_moves_the_digest(self, tmp_path: Path) -> None:
        base = _write(tmp_path / "one", FILES)
        missing = _write(tmp_path / "two", FILES)
        (missing / "index.json").unlink()

        assert digest_script.tree_digest(base) != digest_script.tree_digest(missing)

    def test_empty_directories_are_not_part_of_the_tree(self, tmp_path: Path) -> None:
        base = _write(tmp_path / "one", FILES)
        with_empty = _write(tmp_path / "two", FILES)
        (with_empty / "empty").mkdir()

        assert digest_script.tree_digest(base) == digest_script.tree_digest(with_empty)


class TestFixtureBuild:
    def test_the_fixture_builds_the_same_tree_wherever_it_is_built(self, tmp_path: Path) -> None:
        # Absolute build paths are the cheapest cross-machine leak to catch
        # locally: two scratch directories stand in for two runners' temp dirs.
        first = digest_script.build_fixture_site(tmp_path / "a")
        second = digest_script.build_fixture_site(tmp_path / "somewhere" / "else")

        assert digest_script.tree_listing(first) == digest_script.tree_listing(second)

    def test_the_fixture_exercises_the_cross_machine_hazards(self, tmp_path: Path) -> None:
        listing = digest_script.tree_listing(digest_script.build_fixture_site(tmp_path))

        assert "vimpel-føring" in listing
        assert "testforskriften" in listing
        assert "dobbeltloven" in listing

    def test_write_digest_records_the_digest_of_the_listing(self, tmp_path: Path) -> None:
        dest = tmp_path / "digest" / "leg"
        digest = digest_script.write_digest(tmp_path / "work", dest)

        assert (dest / "digest.txt").read_text(encoding="utf-8") == digest + "\n"
        site_digest = digest_script.tree_digest(tmp_path / "work" / "site")
        assert digest == site_digest
        assert (dest / "listing.txt").read_text(encoding="utf-8") == digest_script.tree_listing(
            tmp_path / "work" / "site"
        )


def _leg(root: Path, name: str, listing: str) -> Path:
    leg = root / name
    leg.mkdir(parents=True)
    (leg / "listing.txt").write_text(listing, encoding="utf-8")
    digest = digest_script.hashlib.sha256(listing.encode("utf-8")).hexdigest()
    (leg / "digest.txt").write_text(digest + "\n", encoding="utf-8")
    return leg


class TestCompare:
    def test_agreeing_machines_report_nothing(self, tmp_path: Path) -> None:
        legs = [_leg(tmp_path, name, "a\t1\n") for name in ("linux", "mac")]

        assert digest_script.compare(legs) == []

    def test_disagreement_names_each_machine_and_the_differing_file(self, tmp_path: Path) -> None:
        linux = _leg(tmp_path, "linux", "a\t1\nb\t2\n")
        mac = _leg(tmp_path, "mac", "a\t1\nb\t9\n")

        problems = digest_script.compare([linux, mac])

        assert any(p.startswith("linux: ") for p in problems)
        assert any(p.startswith("mac: ") for p in problems)
        assert "only on linux: b\t2" in problems
        assert "only on mac: b\t9" in problems

    def test_a_single_digest_is_not_a_comparison(self, tmp_path: Path) -> None:
        problems = digest_script.compare([_leg(tmp_path, "linux", "a\t1\n")])

        assert problems == ["need at least 2 digests to compare, got 1"]

    def test_the_cli_exits_non_zero_on_disagreement(self, tmp_path: Path) -> None:
        legs = [_leg(tmp_path, "linux", "a\t1\n"), _leg(tmp_path, "mac", "a\t2\n")]

        assert digest_script.main(["--compare", *map(str, legs)]) == 1
        assert digest_script.main(["--compare", str(legs[0]), str(legs[0])]) == 0


def _workflow() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return loaded


class TestWorkflow:
    def test_legs_run_on_more_than_one_operating_system(self) -> None:
        legs = _workflow()["jobs"]["digest"]["strategy"]["matrix"]["include"]

        assert len({leg["os"] for leg in legs}) >= 2
        assert len({leg["python-version"] for leg in legs}) >= 2

    def test_every_job_runs_on_github_hosted_runners(self) -> None:
        jobs = _workflow()["jobs"]
        legs = jobs["digest"]["strategy"]["matrix"]["include"]

        hosted = {"ubuntu-latest", "macos-latest"}
        assert {leg["os"] for leg in legs} <= hosted
        assert jobs["compare"]["runs-on"] == "ubuntu-latest"

    def test_permissions_are_read_only(self) -> None:
        assert _workflow()["permissions"] == {"contents": "read"}

    def test_compare_waits_for_every_leg_and_calls_the_script(self) -> None:
        compare = _workflow()["jobs"]["compare"]
        runs = [str(step.get("run", "")) for step in compare["steps"]]

        assert compare["needs"] == "digest"
        assert any("publish_tree_digest.py --compare" in run for run in runs)

    def test_every_action_is_pinned_to_a_commit(self) -> None:
        steps = [step for job in _workflow()["jobs"].values() for step in job["steps"]]
        uses = [str(step["uses"]) for step in steps if "uses" in step]

        assert uses
        for ref in uses:
            sha = ref.partition("@")[2]
            assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), ref

    def test_it_runs_on_every_pull_request_to_main(self) -> None:
        # PyYAML reads the bare key `on` as the boolean True.
        triggers = _workflow()[True]

        assert triggers["pull_request"] == {"branches": ["main"]}
