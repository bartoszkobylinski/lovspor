"""The publishing CLIs, driven directly, cannot publish a resumed comparison (#816).

`score_run.py` and the confirmatory ceremony's `commit_manifest` and `score`
steps are the paths that write a primary comparison. Each one is driven here
the way an operator would, against a pair whose treatment arm was resumed
after its manifest was built. The report writer is a spy: reaching it would
mean the comparison was published.
"""

import sys
from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.pair_manifest import PairManifestError, write_pair_manifest
from tests.unit.test_llhb_pair_manifest import PLAN_REL, RUN_IDS, _build, _git, _runs_root, repo
from tests.unit.test_llhb_resume_publication_gate import _mark_resumed, _script

__all__ = ["repo"]  # the pytest fixture


class _Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> tuple[Path, Any, int]:
        self.calls.append(args)
        raise AssertionError("a resumed comparison reached the report writer")


def _resumed_pair_with_manifest(repo: Path) -> Path:
    """A manifest that verifies in every respect but the resumed treatment arm.

    It is re-pinned to the commit that marked the arm resumed, so the scorer
    pin, the files and the dirty-tree check all pass and only the resume gate
    can refuse it.
    """
    built = _build(repo)
    _mark_resumed(repo, RUN_IDS[1])
    manifest = repo.parent / "pair-manifest.json"
    write_pair_manifest(
        built.model_copy(update={"scorer_commit": _git(repo, "rev-parse", "HEAD")}), manifest
    )
    return manifest


def test_score_run_refuses_a_resumed_pair_before_writing_a_report(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _resumed_pair_with_manifest(repo)
    score_run = _script("score_run")
    spy = _Spy()
    monkeypatch.setattr(score_run, "REPO_ROOT", repo)
    monkeypatch.setattr(score_run, "write_report", spy)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "score_run.py",
            "--manifest",
            str(manifest),
            "--corpus-path",
            str(tmp_path / "corpus"),
            "--runs-root",
            str(_runs_root(repo)),
        ],
    )

    with pytest.raises(PairManifestError, match=f"{RUN_IDS[1]} was resumed"):
        score_run.main()

    assert spy.calls == []


def _ceremony_module(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    ceremony = _script("run_confirmatory_pair")
    monkeypatch.setattr(ceremony, "REPO_ROOT", repo)
    monkeypatch.setattr(ceremony, "RUNS_ROOT", _runs_root(repo))
    monkeypatch.setattr(ceremony, "MANIFESTS_DIR", repo / "manifests")
    return ceremony


def test_the_ceremony_scores_no_resumed_pair(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _resumed_pair_with_manifest(repo)
    module = _ceremony_module(repo, monkeypatch)
    spy = _Spy()
    monkeypatch.setattr(module.score_run_script, "write_report", spy)
    ceremony = module.Ceremony.model_construct(
        manifest_path=str(manifest), corpus_path=str(tmp_path / "corpus")
    )

    with pytest.raises(PairManifestError, match=f"{RUN_IDS[1]} was resumed"):
        module.score(ceremony)

    assert spy.calls == []


def test_the_ceremony_commits_no_manifest_for_a_resumed_pair(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mark_resumed(repo, RUN_IDS[1])
    module = _ceremony_module(repo, monkeypatch)
    arms = [
        module.ArmRecord(run_id=run_id, started_at="2026-11-06T00:00:00Z") for run_id in RUN_IDS
    ]
    ceremony = module.Ceremony.model_construct(
        state="FAIRNESS_VERIFIED",
        control=arms[0],
        treatment=arms[1],
        analysis_plan_path=PLAN_REL,
    )

    with pytest.raises(PairManifestError, match=f"{RUN_IDS[1]} was resumed"):
        module.commit_manifest(ceremony)

    assert not (repo / "manifests").exists()
