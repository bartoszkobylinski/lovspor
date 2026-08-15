"""The pair-manifest gate (DECISIONS.md ruling #30(d)).

"Aggregate scoring MUST NOT execute until a valid pair manifest exists
and all referenced hashes verify." Every test here is one way scoring
could have consumed the wrong bytes — a flipped record, an unpinned
checkout, arms that disagree about what they ran — and the gate must
refuse each one loudly.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from lovspor.llhb.pair_manifest import (
    PairManifest,
    PairManifestError,
    build_pair_manifest,
    file_sha256,
    load_pair_manifest,
    records_path,
    verify_pair_manifest,
    write_pair_manifest,
)

RUN_IDS = ("llhb-v1-run-ctrl", "llhb-v1-run-treat")
PROMPT_REL = "benchmarks/llhb/runner/system-prompt-v1.txt"
DATASET_REL = "benchmarks/llhb/dataset/frozen/llhb-v1.jsonl"
PLAN_REL = "benchmarks/llhb/ANALYSIS-PLAN-fable5-v1.md"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def _metadata(prompt_sha256: str) -> dict[str, str]:
    return {
        "llhb_version": "llhb-v1",
        "runner_commit": "b" * 40,
        "model_id": "claude-fable-5",
        "lovverk_commit": "c" * 40,
        "system_prompt_path": PROMPT_REL,
        "system_prompt_sha256": prompt_sha256,
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A committed checkout with a plan, dataset, prompt and two runs."""
    root = tmp_path / "repo"
    for rel, body in (
        (PLAN_REL, "# plan\n"),
        (DATASET_REL, '{"case_id": "llhb-v1-C1-001"}\n'),
        (PROMPT_REL, "Fast prompt.\n"),
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    prompt_sha = file_sha256(root / PROMPT_REL)
    runs = root / "benchmarks" / "llhb" / "results" / "runs"
    for run_id in RUN_IDS:
        run_dir = runs / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "records.jsonl").write_text(f'{{"run": "{run_id}"}}\n', encoding="utf-8")
        (run_dir / "run-metadata.json").write_text(
            json.dumps(_metadata(prompt_sha)), encoding="utf-8"
        )
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "base")
    return root


def _runs_root(repo: Path) -> Path:
    return repo / "benchmarks" / "llhb" / "results" / "runs"


def _build(repo: Path) -> PairManifest:
    return build_pair_manifest(repo, _runs_root(repo), RUN_IDS, repo / PLAN_REL)


class TestFileSha256:
    def test_hashes_exact_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "f.jsonl"
        path.write_bytes(b'{"a": 1}\n')
        assert file_sha256(path) == hashlib.sha256(b'{"a": 1}\n').hexdigest()

    def test_missing_file_is_a_manifest_error(self, tmp_path: Path) -> None:
        with pytest.raises(PairManifestError, match="cannot hash"):
            file_sha256(tmp_path / "absent")


class TestBuild:
    def test_round_trip_builds_writes_loads_and_verifies(self, repo: Path) -> None:
        manifest = _build(repo)
        # Outside the checkout: an uncommitted manifest inside it would dirty
        # the tree the verifier is about to pin. The real ceremony commits it.
        out = repo.parent / "pair-manifest.json"
        write_pair_manifest(manifest, out)

        loaded = load_pair_manifest(out)

        assert loaded == manifest
        assert loaded.scorer_commit == _git(repo, "rev-parse", "HEAD")
        verify_pair_manifest(loaded, repo, _runs_root(repo))

    def test_dirty_tree_refuses_to_build(self, repo: Path) -> None:
        (repo / PLAN_REL).write_text("# edited\n", encoding="utf-8")
        with pytest.raises(PairManifestError, match="dirty"):
            _build(repo)

    def test_arms_disagreeing_on_a_binding_field_refuse(self, repo: Path) -> None:
        metadata_path = _runs_root(repo) / RUN_IDS[1] / "run-metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["model_id"] = "some-other-model"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        _git(repo, "commit", "--quiet", "-am", "drift")

        with pytest.raises(PairManifestError, match="arms disagree on model_id"):
            _build(repo)

    def test_prompt_bytes_must_match_what_the_runs_recorded(self, repo: Path) -> None:
        (repo / PROMPT_REL).write_text("Different prompt.\n", encoding="utf-8")
        _git(repo, "commit", "--quiet", "-am", "prompt drift")

        with pytest.raises(PairManifestError, match=r"but the runs\s+recorded"):
            _build(repo)


class TestVerify:
    @pytest.mark.parametrize(
        "victim",
        [
            DATASET_REL,
            PLAN_REL,
            PROMPT_REL,
            f"benchmarks/llhb/results/runs/{RUN_IDS[0]}/records.jsonl",
            f"benchmarks/llhb/results/runs/{RUN_IDS[1]}/records.jsonl",
        ],
    )
    def test_any_flipped_byte_refuses_scoring(self, repo: Path, victim: str) -> None:
        manifest = _build(repo)
        path = repo / victim
        path.write_bytes(path.read_bytes() + b"x\n")
        _git(repo, "commit", "--quiet", "-am", "tamper")

        with pytest.raises(PairManifestError, match=r"refusing to score|manifest says"):
            verify_pair_manifest(manifest, repo, _runs_root(repo))

    def test_wrong_head_refuses_scoring(self, repo: Path) -> None:
        manifest = _build(repo)
        (repo / "extra.txt").write_text("x\n", encoding="utf-8")
        _git(repo, "add", "extra.txt")
        _git(repo, "commit", "--quiet", "-m", "moved on")

        with pytest.raises(PairManifestError, match="not the manifest scorer_commit"):
            verify_pair_manifest(manifest, repo, _runs_root(repo))

    def test_dirty_tree_refuses_scoring(self, repo: Path) -> None:
        manifest = _build(repo)
        (repo / "extra.txt").write_text("x\n", encoding="utf-8")
        _git(repo, "add", "extra.txt")

        with pytest.raises(PairManifestError, match="dirty"):
            verify_pair_manifest(manifest, repo, _runs_root(repo))

    def test_metadata_rebound_after_build_refuses(self, repo: Path) -> None:
        manifest = _build(repo)
        for run_id in RUN_IDS:
            metadata_path = _runs_root(repo) / run_id / "run-metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["lovverk_commit"] = "d" * 40
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        _git(repo, "commit", "--quiet", "-am", "rebind")
        rebound = _build(repo)

        assert rebound.corpus_snapshot == "d" * 40
        with pytest.raises(PairManifestError, match=r"scorer_commit|manifest says"):
            verify_pair_manifest(manifest, repo, _runs_root(repo))


class TestLoad:
    def test_malformed_json_is_a_manifest_error(self, tmp_path: Path) -> None:
        path = tmp_path / "m.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(PairManifestError, match="cannot load pair manifest"):
            load_pair_manifest(path)

    def test_wrong_schema_version_is_refused(self, repo: Path, tmp_path: Path) -> None:
        manifest = _build(repo)
        document = manifest.model_dump(mode="json")
        document["schema_version"] = 2
        path = tmp_path / "m.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(PairManifestError, match="schema_version"):
            load_pair_manifest(path)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("control_run_sha256", "abc"),
            ("scorer_commit", "Z" * 40),
            ("dataset_sha256", "g" * 64),
        ],
    )
    def test_malformed_digests_are_refused(
        self, repo: Path, tmp_path: Path, field: str, value: str
    ) -> None:
        manifest = _build(repo)
        document = manifest.model_dump(mode="json")
        document[field] = value
        path = tmp_path / "m.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(PairManifestError, match="cannot load pair manifest"):
            load_pair_manifest(path)


class TestRecordsPath:
    def test_is_the_committed_records_file(self, tmp_path: Path) -> None:
        assert records_path(tmp_path, "run-1") == tmp_path / "run-1" / "records.jsonl"
