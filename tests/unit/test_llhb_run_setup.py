"""Stage 5 run setup: pilot case selection and metadata composition."""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.results import ResultsStore
from lovspor.llhb.run_setup import (
    ControlRunSpec,
    RunSetupError,
    compose_control_metadata,
    pilot_cases,
    sha256_text,
)
from lovspor.llhb.schema import canonical_jsonl, dataset_checksum, load_schema, validate_case

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "llhb" / "schema"

PROMPT = "Du er en juridisk assistent.\n"


def make_case(case_id: str) -> dict[str, Any]:
    return {"case_id": case_id, "question": "Hva sier loven?"}


def make_spec(**overrides: Any) -> ControlRunSpec:
    spec = {
        "run_id": "llhb-v1-run-20260808-pilot1",
        "model_id": "claude-opus-5",
        "system_prompt_text": PROMPT,
        "system_prompt_path": "benchmarks/llhb/runner/system-prompt-v1.txt",
        "lovspor_commit": "0" * 40,
        "lovverk_commit": "1" * 40,
        "runner_commit": "0" * 40,
        "case_order_seed": 42,
        "started_at": "2026-08-08T12:00:00Z",
        "notes": "pilot on discarded candidates; NOT the frozen dataset",
    }
    spec.update(overrides)
    return ControlRunSpec(**spec)


class TestPilotCases:
    def test_excludes_frozen_and_slices_sorted(self) -> None:
        candidates = [make_case(f"llhb-v1-C1-{i:03d}") for i in (5, 1, 3, 2, 4)]
        frozen = {"llhb-v1-C1-002", "llhb-v1-C1-004"}

        picked = pilot_cases(candidates, frozen, limit=2)

        assert [case["case_id"] for case in picked] == ["llhb-v1-C1-001", "llhb-v1-C1-003"]

    def test_rejects_limit_beyond_available(self) -> None:
        candidates = [make_case("llhb-v1-C1-001")]

        with pytest.raises(RunSetupError, match="limit"):
            pilot_cases(candidates, set(), limit=2)

    def test_rejects_non_positive_limit(self) -> None:
        with pytest.raises(RunSetupError, match="limit"):
            pilot_cases([make_case("llhb-v1-C1-001")], set(), limit=0)


class TestComposeControlMetadata:
    def test_is_schema_valid_and_store_accepted(self, tmp_path: Path) -> None:
        cases = [make_case("llhb-v1-C1-001"), make_case("llhb-v1-C1-002")]

        metadata = compose_control_metadata(make_spec(), cases)

        schema = load_schema(SCHEMA_DIR / "run_metadata.schema.json")
        assert validate_case(metadata, schema) == []
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        assert store.open_run(metadata).is_dir()

    def test_checksum_covers_the_actual_case_set(self) -> None:
        cases = [make_case("llhb-v1-C1-002"), make_case("llhb-v1-C1-001")]

        metadata = compose_control_metadata(make_spec(), cases)

        assert metadata["dataset_checksum"] == dataset_checksum(canonical_jsonl(cases))

    def test_prompt_hash_and_control_shape(self) -> None:
        metadata = compose_control_metadata(make_spec(), [make_case("llhb-v1-C1-001")])

        assert metadata["system_prompt_sha256"] == hashlib.sha256(PROMPT.encode()).hexdigest()
        assert metadata["condition"] == "control"
        assert metadata["tool_config"] is None
        assert metadata["sampling"] == {"temperature": None}
        assert metadata["provider"] == "anthropic"


class TestSha256Text:
    def test_hashes_utf8_bytes(self) -> None:
        assert sha256_text("æøå") == hashlib.sha256("æøå".encode()).hexdigest()
