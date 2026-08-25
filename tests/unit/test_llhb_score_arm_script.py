"""Single-arm scoring script (ruling #31): metadata, report shape, refusals."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from lovspor.errors import LovsporError
from lovspor.llhb.metrics import ArmReport, MetricEstimate, OutcomeCounts

_SCRIPT = Path(__file__).parents[2] / "benchmarks" / "llhb" / "runner" / "score_arm.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("score_arm_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


score_arm_script = _load()

COUNTS = OutcomeCounts(
    PASS=1, FAIL=1, UNRESOLVED_SEMANTIC=0, SCORER_ERROR=0, MODEL_ERROR=0, total=2
)
ARM = ArmReport(
    metrics={
        "citation_hallucination_rate": MetricEstimate(
            numerator=1, denominator=2, rate=0.5, ci_low=0.1, ci_high=0.9
        ),
        "post_direct_retrieval_hallucination_rate": MetricEstimate(
            numerator=0, denominator=0, rate=None, ci_low=None, ci_high=None
        ),
    },
    outcomes=COUNTS,
    c8_outcomes=COUNTS,
    unresolved={"unattached_quotes": 0},
)
METADATA = {
    "provider": "norallm",
    "model_id": "NorMistral-11b-thinking:latest",
    "condition": "control",
    "dataset_checksum": "a" * 64,
    "lovverk_commit": "b" * 40,
    "lovspor_commit": "c" * 40,
    "system_prompt_sha256": "d" * 64,
    "sampling": {"temperature": 0.0},
    "run_id": "llhb-v1-run-20260825-nmctrl1",
}


class TestArguments:
    def test_run_id_and_corpus_path_are_required(self) -> None:
        with pytest.raises(SystemExit):
            score_arm_script.parse_args(["--run-id", "x"])
        with pytest.raises(SystemExit):
            score_arm_script.parse_args(["--corpus-path", "corpus"])

    def test_defaults_point_at_the_frozen_dataset(self) -> None:
        args = score_arm_script.parse_args(["--run-id", "x", "--corpus-path", "corpus"])

        assert args.cases == score_arm_script.FROZEN_JSONL
        assert args.out is None


VALID_METADATA_PATH = (
    Path(__file__).parents[2]
    / "benchmarks"
    / "llhb"
    / "results"
    / "runs"
    / "llhb-v1-run-20260825-nmctrl1"
    / "run-metadata.json"
)


def valid_metadata(**overrides: object) -> dict[str, object]:
    metadata = dict(json.loads(VALID_METADATA_PATH.read_text(encoding="utf-8")))
    metadata.update(overrides)
    return metadata


def write_run(tmp_path: Path, metadata: dict[str, object], name: str | None = None) -> str:
    run_dir = tmp_path / (name or str(metadata["run_id"]))
    run_dir.mkdir()
    (run_dir / "run-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return run_dir.name


class TestMetadata:
    def test_a_missing_run_fails_closed(self, tmp_path: Path) -> None:
        with pytest.raises(LovsporError, match=r"run-metadata\.json"):
            score_arm_script.read_metadata(tmp_path, "llhb-v1-run-20260825-nope")

    def test_metadata_is_read_from_the_run_directory(self, tmp_path: Path) -> None:
        metadata = valid_metadata()
        name = write_run(tmp_path, metadata)

        assert score_arm_script.read_metadata(tmp_path, name) == metadata

    def test_metadata_must_claim_the_directory_it_sits_in(self, tmp_path: Path) -> None:
        name = write_run(tmp_path, valid_metadata(), name="llhb-v1-run-20260825-copy")

        with pytest.raises(LovsporError, match="declares run_id"):
            score_arm_script.read_metadata(tmp_path, name)

    def test_metadata_missing_required_provenance_fails_closed(self, tmp_path: Path) -> None:
        metadata = valid_metadata()
        del metadata["dataset_checksum"]
        name = write_run(tmp_path, metadata)

        with pytest.raises(LovsporError, match="not schema-valid"):
            score_arm_script.read_metadata(tmp_path, name)

    def test_treatment_metadata_is_rejected(self, tmp_path: Path) -> None:
        metadata = valid_metadata(condition="lovspor", tool_config={"tools": ["x"]})
        name = write_run(tmp_path, metadata)

        with pytest.raises(LovsporError, match="single-arm scoring is for control"):
            score_arm_script.read_metadata(tmp_path, name)


class TestReport:
    def test_the_report_is_single_arm_with_identity_and_no_delta(self) -> None:
        report = score_arm_script.build_report("llhb-v1-run-20260825-nmctrl1", METADATA, ARM)

        assert report["report_kind"] == "single-arm"
        assert report["epistemic_status"] == score_arm_script.EPISTEMIC_STATUS
        assert report["provider"] == "norallm"
        assert report["condition"] == "control"
        assert report["cases_scored"] == 2
        assert report["metrics"]["citation_hallucination_rate"]["rate"] == 0.5
        assert report["outcomes"]["total"] == 2
        assert "delta" not in report and "primary" not in report
        assert all("delta" not in estimate for estimate in report["metrics"].values())

    def test_the_table_prints_every_metric_and_the_status(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = score_arm_script.build_report("llhb-v1-run-20260825-nmctrl1", METADATA, ARM)

        score_arm_script.print_table(report)

        out = capsys.readouterr().out
        assert "citation_hallucination_rate" in out
        assert "0.500 [0.100, 0.900]" in out
        assert "n/a" in out
        assert score_arm_script.EPISTEMIC_STATUS in out
