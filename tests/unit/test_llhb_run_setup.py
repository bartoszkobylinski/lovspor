"""Run setup: pilot case selection and per-condition metadata composition."""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.results import ResultsStore
from lovspor.llhb.run_setup import (
    RunSetupError,
    RunSpec,
    compose_run_metadata,
    pilot_cases,
    sha256_text,
    verify_frozen_against_lock,
)
from lovspor.llhb.schema import canonical_jsonl, dataset_checksum, load_schema, validate_case

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "llhb" / "schema"

PROMPT = "Du er en juridisk assistent.\n"

TOOL_CONFIG = {
    "transport": "native-mcp",
    "tools": ["mcp__lovverk__get_section"],
    "tool_schema_sha256": "c" * 64,
    "backend": "local stdio `lovspor mcp` on lovverk 1111 at /pin",
}


def make_case(case_id: str) -> dict[str, Any]:
    return {"case_id": case_id, "question": "Hva sier loven?"}


def make_spec(**overrides: Any) -> RunSpec:
    spec = {
        "run_id": "llhb-v1-run-20260808-pilot1",
        "model_id": "claude-opus-5",
        "condition": "control",
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
    return RunSpec(**spec)


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

    def test_limit_one_is_valid(self) -> None:
        candidates = [make_case("llhb-v1-C1-001"), make_case("llhb-v1-C1-002")]

        assert [c["case_id"] for c in pilot_cases(candidates, set(), 1)] == ["llhb-v1-C1-001"]

    def test_limit_equal_to_pool_is_valid(self) -> None:
        candidates = [make_case("llhb-v1-C1-001"), make_case("llhb-v1-C1-002")]

        assert len(pilot_cases(candidates, set(), 2)) == 2


class TestVerifyFrozenAgainstLock:
    def make_lock(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "case_count": len(cases),
            "dataset_sha256": dataset_checksum(canonical_jsonl(cases)),
        }

    def test_accepts_matching_lock(self) -> None:
        cases = [make_case("llhb-v1-C1-001"), make_case("llhb-v1-C1-002")]

        verify_frozen_against_lock(cases, self.make_lock(cases))

    def test_rejects_count_mismatch(self) -> None:
        cases = [make_case("llhb-v1-C1-001")]
        lock = self.make_lock(cases) | {"case_count": 250}

        with pytest.raises(RunSetupError, match="250"):
            verify_frozen_against_lock(cases, lock)

    def test_rejects_checksum_mismatch(self) -> None:
        cases = [make_case("llhb-v1-C1-001")]
        lock = self.make_lock(cases) | {"dataset_sha256": "f" * 64}

        with pytest.raises(RunSetupError, match="checksum"):
            verify_frozen_against_lock(cases, lock)

    def test_rejects_non_integer_case_count(self) -> None:
        cases = [make_case("llhb-v1-C1-001")]
        base = self.make_lock(cases)

        for bad in (1.9, "1", True, None):
            with pytest.raises(RunSetupError, match="JSON integer"):
                verify_frozen_against_lock(cases, base | {"case_count": bad})

    def test_rejects_missing_case_count(self) -> None:
        cases = [make_case("llhb-v1-C1-001")]
        lock = {"dataset_sha256": self.make_lock(cases)["dataset_sha256"]}

        with pytest.raises(RunSetupError, match="JSON integer"):
            verify_frozen_against_lock(cases, lock)

    def test_rejects_non_string_sha(self) -> None:
        cases = [make_case("llhb-v1-C1-001")]

        with pytest.raises(RunSetupError, match="must be a string"):
            verify_frozen_against_lock(cases, self.make_lock(cases) | {"dataset_sha256": None})

    def test_rejects_truncated_frozen_file(self) -> None:
        cases = [make_case("llhb-v1-C1-001"), make_case("llhb-v1-C1-002")]
        lock = self.make_lock(cases)

        with pytest.raises(RunSetupError, match="lock declares 2"):
            verify_frozen_against_lock(cases[:1], lock)


class TestComposeRunMetadata:
    def test_is_schema_valid_and_store_accepted(self, tmp_path: Path) -> None:
        cases = [make_case("llhb-v1-C1-001"), make_case("llhb-v1-C1-002")]

        metadata = compose_run_metadata(make_spec(), cases)

        schema = load_schema(SCHEMA_DIR / "run_metadata.schema.json")
        assert validate_case(metadata, schema) == []
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        assert store.open_run(metadata).is_dir()

    def test_checksum_covers_the_actual_case_set(self) -> None:
        cases = [make_case("llhb-v1-C1-002"), make_case("llhb-v1-C1-001")]

        metadata = compose_run_metadata(make_spec(), cases)

        assert metadata["dataset_checksum"] == dataset_checksum(canonical_jsonl(cases))

    def test_prompt_hash_and_control_shape(self) -> None:
        metadata = compose_run_metadata(make_spec(), [make_case("llhb-v1-C1-001")])

        assert metadata["system_prompt_sha256"] == hashlib.sha256(PROMPT.encode()).hexdigest()
        assert metadata["condition"] == "control"
        assert metadata["tool_config"] is None
        assert metadata["sampling"] == {"temperature": None}
        assert metadata["provider"] == "anthropic"

    def test_treatment_metadata_carries_the_tool_config(self, tmp_path: Path) -> None:
        spec = make_spec(condition="lovspor", tool_config=TOOL_CONFIG)

        metadata = compose_run_metadata(spec, [make_case("llhb-v1-C1-001")])

        schema = load_schema(SCHEMA_DIR / "run_metadata.schema.json")
        assert validate_case(metadata, schema) == []
        assert metadata["tool_config"] == TOOL_CONFIG
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        assert store.open_run(metadata).is_dir()

    @pytest.mark.parametrize("tool", ["get_section", "mcp__decoy__get_section"])
    def test_the_schema_refuses_a_tool_that_is_not_the_treatment(self, tool: str) -> None:
        """The declared surface is the fairness check's only source for
        which server had to be up. A bare name carries no server and a
        foreign one carries the wrong server, so neither document may be
        written in the first place."""
        spec = make_spec(condition="lovspor", tool_config={**TOOL_CONFIG, "tools": [tool]})

        metadata = compose_run_metadata(spec, [make_case("llhb-v1-C1-001")])

        schema = load_schema(SCHEMA_DIR / "run_metadata.schema.json")
        assert validate_case(metadata, schema) != []

    def test_both_conditions_share_every_field_but_the_condition(self) -> None:
        """The metadata pair is the fairness evidence: if composing the two
        arms could drift a shared field, the comparison would be unfounded."""
        cases = [make_case("llhb-v1-C1-001")]
        control = compose_run_metadata(make_spec(), cases)
        treatment = compose_run_metadata(
            make_spec(condition="lovspor", tool_config=TOOL_CONFIG), cases
        )

        differing = {key for key in control if control[key] != treatment[key]}
        assert differing == {"condition", "tool_config"}

    def test_rejects_a_control_run_carrying_a_tool_config(self) -> None:
        with pytest.raises(RunSetupError, match="no tool_config"):
            compose_run_metadata(make_spec(tool_config=TOOL_CONFIG), [make_case("llhb-v1-C1-001")])

    def test_rejects_a_treatment_run_without_a_tool_config(self) -> None:
        with pytest.raises(RunSetupError, match="must carry the tool_config"):
            compose_run_metadata(make_spec(condition="lovspor"), [make_case("llhb-v1-C1-001")])

    def test_rejects_an_unknown_condition(self) -> None:
        with pytest.raises(RunSetupError, match="unknown condition"):
            compose_run_metadata(make_spec(condition="pilot"), [make_case("llhb-v1-C1-001")])


class TestSha256Text:
    def test_hashes_utf8_bytes(self) -> None:
        assert sha256_text("æøå") == hashlib.sha256("æøå".encode()).hexdigest()
