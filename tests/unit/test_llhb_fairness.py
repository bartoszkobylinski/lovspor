"""Stage 6 fairness checks over committed control/treatment artifacts."""

import json
from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb import fairness as fairness_module
from lovspor.llhb.fairness import (
    ExpectedSurface,
    FrozenExpectation,
    RunArtifacts,
    bookkeeping_violations,
    check_pair,
    compare_run_metadata,
    control_violations,
    coverage_violations,
    frozen_surface_violations,
    frozen_violations,
    identity_violations,
    paired_case_violations,
    paired_completion_violations,
    treatment_violations,
)
from lovspor.llhb.mcp_surface import SERVER_NAME

TOOL_CONFIG = {
    "transport": "native-mcp",
    "tools": ["mcp__lovverk__get_section"],
    "tool_schema_sha256": "c" * 64,
    "backend": "local stdio",
}
# The frozen apparatus surface the fixtures agree with; tests that need a
# disagreement override one side or the other.
EXPECTED = ExpectedSurface(tools=("mcp__lovverk__get_section",), tool_schema_sha256="c" * 64)


def metadata(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "run_id": "llhb-v1-run-20260810-ctrl",
        "llhb_version": "1.0",
        "dataset_checksum": "a" * 64,
        "provider": "anthropic",
        "model_id": "claude-opus-5",
        "condition": "control",
        "system_prompt_sha256": "b" * 64,
        "system_prompt_path": "benchmarks/llhb/runner/system-prompt-v1.txt",
        "tool_config": None,
        "sampling": {"temperature": None},
        "max_turns": None,
        "started_at": "2026-08-10T09:00:00Z",
        "finished_at": "2026-08-10T09:30:00Z",
        "lovspor_commit": "0123abc",
        "lovverk_commit": "6" * 40,
        "runner_commit": "0123abc",
        "case_order_seed": 42,
        "cases_total": 2,
        "cases_completed": 2,
        "errors_total": 0,
        "notes": "pilot",
    }
    document.update(overrides)
    return document


def treatment_metadata(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "run_id": "llhb-v1-run-20260810-treat",
        "condition": "lovspor",
        "tool_config": TOOL_CONFIG,
        "started_at": "2026-08-10T10:00:00Z",
        "finished_at": "2026-08-10T10:40:00Z",
        "notes": "treatment pilot",
    }
    fields.update(overrides)
    return metadata(**fields)


def record(case_id: str, **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "run_id": "llhb-v1-run-20260810-ctrl",
        "case_id": case_id,
        "provider": "anthropic",
        "model_id": "claude-opus-5",
        "condition": "control",
        "final_answer": "Svar.",
        "tool_calls": [],
        "timing": {"started_at": "2026-08-10T09:00:01Z", "total_ms": 100},
        "errors": [],
        "completed": True,
        "harness": {"exposed_tools": [], "mcp_servers": [], "permission_denials": []},
    }
    document.update(overrides)
    return document


def treatment_record(case_id: str, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        # Its own run's id, not the control's: a record carries the identity
        # of the run it belongs to, and identity_violations checks exactly that.
        "run_id": "llhb-v1-run-20260810-treat",
        "condition": "lovspor",
        "tool_calls": [{"index": 0, "name": "mcp__lovverk__get_section", "arguments": {}}],
        "harness": {
            "exposed_tools": ["mcp__lovverk__get_section"],
            "mcp_servers": [{"name": "lovverk", "status": "connected"}],
            "permission_denials": [],
        },
    }
    fields.update(overrides)
    return record(case_id, **fields)


class TestCompareRunMetadata:
    def test_a_clean_pair_has_no_findings(self) -> None:
        assert compare_run_metadata(metadata(), treatment_metadata()) == []

    def test_flags_a_different_dataset(self) -> None:
        problems = compare_run_metadata(metadata(), treatment_metadata(dataset_checksum="d" * 64))

        assert len(problems) == 1
        assert "dataset_checksum differs" in problems[0]

    def test_flags_a_different_system_prompt(self) -> None:
        problems = compare_run_metadata(
            metadata(), treatment_metadata(system_prompt_sha256="e" * 64)
        )

        assert any("system_prompt_sha256 differs" in problem for problem in problems)

    def test_flags_a_different_model_seed_or_pin(self) -> None:
        problems = compare_run_metadata(
            metadata(),
            treatment_metadata(
                model_id="claude-sonnet-5", case_order_seed=7, lovverk_commit="f" * 40
            ),
        )

        assert len(problems) == 3

    def test_allows_the_fields_that_must_differ(self) -> None:
        """run_id, condition, tool_config and the timestamps are the
        design's own differences, not violations of it."""
        assert compare_run_metadata(metadata(), treatment_metadata()) == []

    def test_per_run_bookkeeping_is_exempt_from_the_comparison(self) -> None:
        """The exemption list is the whole safety net: too short and honest
        pairs are rejected, too long and a real difference slips past. Each
        exempt field is named here so shortening the list fails a test."""
        problems = compare_run_metadata(
            metadata(),
            treatment_metadata(cases_completed=1, errors_total=1, evaluator_version="scorer-v1"),
        )

        assert problems == []

    def test_flags_a_field_recorded_in_only_one_run(self) -> None:
        problems = compare_run_metadata(metadata(), treatment_metadata(api_version="2026-08-01"))

        assert any("recorded in only one" in problem for problem in problems)

    def test_flags_mislabelled_conditions(self) -> None:
        problems = compare_run_metadata(
            metadata(condition="lovspor", tool_config=TOOL_CONFIG), treatment_metadata()
        )

        assert any("control run is labelled" in problem for problem in problems)

    def test_flags_a_treatment_labelled_control(self) -> None:
        problems = compare_run_metadata(metadata(), treatment_metadata(condition="control"))

        assert any("treatment run is labelled" in problem for problem in problems)


class TestPairedCases:
    def test_matching_case_sets_pass(self) -> None:
        control = [record("llhb-v1-C1-001"), record("llhb-v1-C2-001")]
        treatment = [treatment_record("llhb-v1-C2-001"), treatment_record("llhb-v1-C1-001")]

        assert paired_case_violations(control, treatment) == []

    def test_flags_a_case_only_the_control_ran(self) -> None:
        problems = paired_case_violations(
            [record("llhb-v1-C1-001"), record("llhb-v1-C2-001")],
            [treatment_record("llhb-v1-C1-001")],
        )

        assert problems == ["treatment run never ran case(s) ['llhb-v1-C2-001']"]

    def test_flags_a_case_only_the_treatment_ran(self) -> None:
        problems = paired_case_violations(
            [record("llhb-v1-C1-001")],
            [treatment_record("llhb-v1-C1-001"), treatment_record("llhb-v1-C3-001")],
        )

        assert problems == ["control run never ran case(s) ['llhb-v1-C3-001']"]


class TestControlViolations:
    def test_a_toolless_control_run_passes(self) -> None:
        assert control_violations([record("llhb-v1-C1-001")]) == []

    def test_flags_a_control_case_that_called_a_tool(self) -> None:
        problems = control_violations(
            [record("llhb-v1-C1-001", tool_calls=[{"index": 0, "name": "x", "arguments": {}}])]
        )

        assert "issued 1 tool call(s)" in problems[0]

    def test_flags_a_control_case_that_was_offered_tools(self) -> None:
        offered = {"exposed_tools": ["Bash"], "mcp_servers": [], "permission_denials": []}

        problems = control_violations([record("llhb-v1-C1-001", harness=offered)])

        assert "was offered 1 tool(s)" in problems[0]

    def test_flags_a_completed_case_without_harness_evidence(self) -> None:
        problems = control_violations([record("llhb-v1-C1-001", harness=None)])

        assert problems == ["llhb-v1-C1-001: completed without harness evidence"]

    def test_an_errored_case_without_evidence_is_not_a_violation(self) -> None:
        errored = record("llhb-v1-C1-001", harness=None, completed=False, final_answer=None)

        assert control_violations([errored]) == []


class TestTreatmentViolations:
    def test_a_connected_treatment_run_passes(self) -> None:
        assert (
            treatment_violations([treatment_record("llhb-v1-C1-001")], TOOL_CONFIG["tools"]) == []
        )

    def test_flags_a_case_whose_server_never_connected(self) -> None:
        broken = {
            "exposed_tools": ["mcp__lovverk__get_section"],
            "mcp_servers": [{"name": "lovverk", "status": "failed"}],
            "permission_denials": [],
        }

        problems = treatment_violations(
            [treatment_record("llhb-v1-C1-001", harness=broken)], TOOL_CONFIG["tools"]
        )

        assert "did not connect" in problems[0]

    def test_flags_a_case_offered_no_tools(self) -> None:
        empty = {"exposed_tools": [], "mcp_servers": [], "permission_denials": []}

        problems = treatment_violations(
            [treatment_record("llhb-v1-C1-001", harness=empty)], TOOL_CONFIG["tools"]
        )

        assert problems == [
            "llhb-v1-C1-001: offered surface does not match the declared surface "
            "(missing ['mcp__lovverk__get_section'], unexpected [])"
        ]

    def test_flags_denied_tool_calls(self) -> None:
        denied = {
            "exposed_tools": ["mcp__lovverk__get_section"],
            "mcp_servers": [{"name": "lovverk", "status": "connected"}],
            "permission_denials": [{"tool_name": "mcp__lovverk__get_law"}],
        }

        problems = treatment_violations(
            [treatment_record("llhb-v1-C1-001", harness=denied)], TOOL_CONFIG["tools"]
        )

        assert "denied 1 tool call(s)" in problems[0]


class TestDeclaredSurface:
    def test_flags_a_surface_that_is_not_the_declared_one(self) -> None:
        """tool_config says what the run offered; the transcript says what
        it actually offered. A count check would pass a different tool."""
        swapped = {
            "exposed_tools": ["mcp__lovverk__get_law"],
            "mcp_servers": [{"name": "lovverk", "status": "connected"}],
            "permission_denials": [],
        }

        problems = treatment_violations(
            [treatment_record("llhb-v1-C1-001", harness=swapped)],
            declared_tools=TOOL_CONFIG["tools"],
        )

        assert any("does not match the declared surface" in problem for problem in problems)

    def test_accepts_the_declared_surface_in_any_order(self) -> None:
        declared = ["mcp__lovverk__get_section", "mcp__lovverk__get_law"]
        harness = {
            "exposed_tools": list(reversed(declared)),
            "mcp_servers": [{"name": "lovverk", "status": "connected"}],
            "permission_denials": [],
        }

        record = treatment_record("llhb-v1-C1-001", harness=harness)

        assert treatment_violations([record], declared_tools=declared) == []

    def test_control_surface_must_be_exactly_empty(self) -> None:
        offered = {"exposed_tools": ["Bash"], "mcp_servers": [], "permission_denials": []}

        problems = control_violations([record("llhb-v1-C1-001", harness=offered)])

        assert len(problems) == 1

    def test_flags_a_treatment_run_that_declares_no_surface(self) -> None:
        """An empty tool_config must never read as 'this is the control
        arm'; the arm is what the caller says it is."""
        empty = {"exposed_tools": [], "mcp_servers": [], "permission_denials": []}

        problems = treatment_violations(
            [treatment_record("llhb-v1-C1-001", harness=empty, tool_calls=[])], declared_tools=[]
        )

        # With nothing declared there is no server to name, so the missing
        # declaration is the whole finding — and it is the substantive one.
        assert problems == ["treatment run declares no tool surface in tool_config.tools"]

    def test_an_empty_tool_config_is_not_a_fair_pair(self) -> None:
        control = RunArtifacts(metadata=metadata(), records=[record("llhb-v1-C1-001")])
        treatment = RunArtifacts(
            metadata=treatment_metadata(tool_config={}),
            records=[
                treatment_record(
                    "llhb-v1-C1-001",
                    tool_calls=[],
                    harness={"exposed_tools": [], "mcp_servers": [], "permission_denials": []},
                )
            ],
        )

        assert check_pair(control, treatment, EXPECTED) != []


class TestPairedCompletion:
    def test_matching_completions_pass(self) -> None:
        control = [record("llhb-v1-C1-001")]
        treatment = [treatment_record("llhb-v1-C1-001")]

        assert paired_completion_violations(control, treatment) == []

    def test_flags_a_case_that_completed_in_one_arm_only(self) -> None:
        """An errored treatment case has no answer to compare, and its
        missing harness evidence is not itself a violation - so without
        this check the pair passes while one arm never ran the case."""
        control = [record("llhb-v1-C1-001")]
        treatment = [
            treatment_record("llhb-v1-C1-001", completed=False, final_answer=None, harness=None)
        ]

        problems = paired_completion_violations(control, treatment)

        assert problems == ["llhb-v1-C1-001: completed in the control arm only"]

    def test_flags_a_case_that_completed_in_the_treatment_arm_only(self) -> None:
        control = [record("llhb-v1-C1-001", completed=False, final_answer=None, harness=None)]
        treatment = [treatment_record("llhb-v1-C1-001")]

        problems = paired_completion_violations(control, treatment)

        assert problems == ["llhb-v1-C1-001: completed in the treatment arm only"]

    def test_a_case_missing_from_the_treatment_arm_does_not_end_the_scan(self) -> None:
        """The skip for an unpaired case skips that case only. Turning it
        into an early exit would drop every finding after the first gap,
        and a report that stops early reads exactly like a clean one."""
        control = [
            record("llhb-v1-C1-001"),
            record("llhb-v1-C2-001"),
        ]
        treatment = [treatment_record("llhb-v1-C2-001", completed=False, harness=None)]

        problems = paired_completion_violations(control, treatment)

        assert problems == ["llhb-v1-C2-001: completed in the control arm only"]

    def test_findings_come_back_in_case_order(self) -> None:
        """A gate whose output humans diff between runs has to be ordered
        by the data, not by whatever order the records were appended in."""
        control = [
            record("llhb-v1-C2-001", completed=False, final_answer=None, harness=None),
            record("llhb-v1-C1-001"),
        ]
        treatment = [
            treatment_record("llhb-v1-C2-001", completed=False, harness=None),
            treatment_record("llhb-v1-C1-001", completed=False, harness=None),
        ]

        problems = paired_completion_violations(control, treatment)

        assert problems == [
            "llhb-v1-C1-001: completed in the control arm only",
            "llhb-v1-C2-001: errored in both arms, so it compares nothing",
        ]

    def test_flags_a_case_that_errored_in_both_arms(self) -> None:
        control = [record("llhb-v1-C1-001", completed=False, final_answer=None, harness=None)]
        treatment = [
            treatment_record("llhb-v1-C1-001", completed=False, final_answer=None, harness=None)
        ]

        problems = paired_completion_violations(control, treatment)

        assert problems == ["llhb-v1-C1-001: errored in both arms, so it compares nothing"]


class TestCoverage:
    def test_two_empty_runs_are_not_a_fair_comparison(self) -> None:
        """Every other check states something about the records present, so
        all of them pass vacuously when there are none."""
        empty_control = RunArtifacts(metadata=metadata(cases_total=0), records=[])
        empty_treatment = RunArtifacts(metadata=treatment_metadata(cases_total=0), records=[])

        problems = check_pair(empty_control, empty_treatment, EXPECTED)

        assert "control run has no records, so the pair compares nothing" in problems
        assert "treatment run has no records, so the pair compares nothing" in problems

    def test_flags_fewer_records_than_the_run_claims(self) -> None:
        run = RunArtifacts(metadata=metadata(cases_total=2), records=[record("llhb-v1-C1-001")])

        problems = coverage_violations(run, "control")

        assert problems == ["control run holds 1 record(s) but its metadata declares 2 case(s)"]

    def test_flags_duplicate_records_for_one_case(self) -> None:
        """Every later check keys cases by id, so a duplicate collapses into
        one comparison while still counting twice toward the total."""
        run = RunArtifacts(
            metadata=metadata(cases_total=2),
            records=[record("llhb-v1-C1-001"), record("llhb-v1-C1-001")],
        )

        problems = coverage_violations(run, "control")

        assert problems == ["control run holds more than one record for case(s) ['llhb-v1-C1-001']"]

    def test_flags_a_run_that_declares_no_case_total(self) -> None:
        """The schema allows a null cases_total, and a run carrying one
        cannot be checked for coverage at all — that is the finding."""
        run = RunArtifacts(metadata=metadata(cases_total=None), records=[record("llhb-v1-C1-001")])

        problems = coverage_violations(run, "control")

        assert problems == [
            "control run declares no cases_total, so whether it covered the "
            "dataset cannot be checked from the artifacts"
        ]

    def test_a_complete_run_passes(self) -> None:
        run = RunArtifacts(
            metadata=metadata(cases_total=2),
            records=[record("llhb-v1-C1-001"), record("llhb-v1-C2-001")],
        )

        assert coverage_violations(run, "control") == []


class TestRecordIdentity:
    def test_flags_a_record_from_another_model(self) -> None:
        """ResultsStore checks this when a record is written; this module
        reads committed files, where a record could have been edited or
        copied in from a different run afterwards."""
        run = RunArtifacts(
            metadata=treatment_metadata(cases_total=1),
            records=[treatment_record("llhb-v1-C1-001", model_id="claude-sonnet-5")],
        )

        problems = identity_violations(run, "treatment")

        assert len(problems) == 1
        assert "model_id 'claude-sonnet-5'" in problems[0]

    def test_flags_a_record_filed_under_another_run(self) -> None:
        run = RunArtifacts(
            metadata=metadata(cases_total=1),
            records=[record("llhb-v1-C1-001", run_id="llhb-v1-run-20260810-other")],
        )

        assert identity_violations(run, "control") != []

    @pytest.mark.parametrize(
        ("field", "wrong"),
        [
            ("run_id", "llhb-v1-run-20260810-other"),
            ("provider", "openai"),
            ("model_id", "claude-sonnet-5"),
            ("condition", "control"),
        ],
    )
    def test_every_identity_field_is_checked(self, field: str, wrong: str) -> None:
        """Each field on its own: a record carrying the other arm's
        condition, or another provider, is a record from a different
        measurement, and dropping any one of them from the comparison
        leaves that swap invisible."""
        run = RunArtifacts(
            metadata=treatment_metadata(cases_total=1),
            records=[treatment_record("llhb-v1-C1-001", **{field: wrong})],
        )

        problems = identity_violations(run, "treatment")

        assert len(problems) == 1
        assert f"{field} {wrong!r}" in problems[0]

    def test_a_record_matching_its_run_passes(self) -> None:
        run = RunArtifacts(metadata=metadata(cases_total=1), records=[record("llhb-v1-C1-001")])

        assert identity_violations(run, "control") == []


class TestCheckPair:
    def test_a_clean_pilot_pair_reports_nothing(self) -> None:
        control = RunArtifacts(
            metadata=metadata(cases_total=1, cases_completed=1),
            records=[record("llhb-v1-C1-001")],
        )
        treatment = RunArtifacts(
            metadata=treatment_metadata(cases_total=1, cases_completed=1),
            records=[treatment_record("llhb-v1-C1-001")],
        )

        assert check_pair(control, treatment, EXPECTED) == []

    def test_collects_findings_from_every_layer(self) -> None:
        control = RunArtifacts(
            metadata=metadata(case_order_seed=1),
            records=[
                record("llhb-v1-C1-001", tool_calls=[{"index": 0, "name": "x", "arguments": {}}])
            ],
        )
        treatment = RunArtifacts(
            metadata=treatment_metadata(),
            records=[treatment_record("llhb-v1-C9-999")],
        )

        problems = check_pair(control, treatment, EXPECTED)

        assert any("case_order_seed differs" in problem for problem in problems)
        assert any("never ran case(s)" in problem for problem in problems)
        assert any("issued 1 tool call(s)" in problem for problem in problems)


class TestUnidentifiedRecords:
    def test_flags_records_with_no_case_id(self) -> None:
        """str(None) is "None", so two anonymous records pair with each
        other and the arms look matched when nothing was compared."""
        run = RunArtifacts(
            metadata=metadata(cases_total=1),
            records=[{**record("llhb-v1-C1-001"), "case_id": None}],
        )

        problems = coverage_violations(run, "control")

        assert problems == [
            "control run holds record(s) whose case id is not a dataset id: ['None']"
        ]

    def test_flags_an_id_outside_the_frozen_grammar(self) -> None:
        """An id the dataset could never have produced pairs just as happily
        with its twin in the other arm, so both runs agree on material that
        is not the benchmark's."""
        run = RunArtifacts(
            metadata=metadata(cases_total=1),
            records=[{**record("llhb-v1-C1-001"), "case_id": "not-an-llhb-case"}],
        )

        problems = coverage_violations(run, "control")

        assert problems == [
            "control run holds record(s) whose case id is not a dataset id: ['not-an-llhb-case']"
        ]

    def test_the_pattern_matches_the_committed_schema(self) -> None:
        """The constant mirrors result_record.schema.json; a test rather
        than an import keeps this module light without letting them drift."""
        schema = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "benchmarks"
                / "llhb"
                / "schema"
                / "result_record.schema.json"
            ).read_text(encoding="utf-8")
        )

        assert fairness_module._CASE_ID_RE.pattern == schema["properties"]["case_id"]["pattern"]

    def test_an_empty_case_id_counts_as_missing(self) -> None:
        run = RunArtifacts(
            metadata=metadata(cases_total=1),
            records=[{**record("llhb-v1-C1-001"), "case_id": "  "}],
        )

        assert coverage_violations(run, "control") != []


class TestBookkeeping:
    def test_flags_a_summary_that_contradicts_the_records(self) -> None:
        """cases_completed and errors_total are exempt from the cross-arm
        comparison, so nothing else checks them at all."""
        run = RunArtifacts(
            metadata=metadata(cases_total=1, cases_completed=0, errors_total=1),
            records=[record("llhb-v1-C1-001")],
        )

        problems = bookkeeping_violations(run, "control")

        assert problems == [
            "control run declares cases_completed 0, but its records show 1",
            "control run declares errors_total 1, but its records show 0",
        ]

    def test_a_consistent_summary_passes(self) -> None:
        errored = record("llhb-v1-C2-001", completed=False, final_answer=None, harness=None)
        run = RunArtifacts(
            metadata=metadata(cases_total=2, cases_completed=1, errors_total=1),
            records=[record("llhb-v1-C1-001"), errored],
        )

        assert bookkeeping_violations(run, "control") == []

    def test_a_contradictory_pair_is_not_fair(self) -> None:
        control = RunArtifacts(
            metadata=metadata(cases_total=1, cases_completed=0, errors_total=1),
            records=[record("llhb-v1-C1-001")],
        )
        treatment = RunArtifacts(
            metadata=treatment_metadata(cases_total=1, cases_completed=0, errors_total=1),
            records=[treatment_record("llhb-v1-C1-001")],
        )

        assert check_pair(control, treatment, EXPECTED) != []


class TestNamedServer:
    """ "Some server connected" is not the claim. A case where the lovverk
    server failed and an unrelated one connected has no treatment in it,
    and the run's own declared tools name which server had to be up."""

    def make(self, servers: list[dict[str, str]]) -> dict[str, Any]:
        return treatment_record(
            "llhb-v1-C1-001",
            harness={
                "exposed_tools": ["mcp__lovverk__get_section"],
                "mcp_servers": servers,
                "permission_denials": [],
            },
        )

    def test_flags_a_case_where_a_different_server_connected(self) -> None:
        record = self.make(
            [
                {"name": "lovverk", "status": "failed"},
                {"name": "something-else", "status": "connected"},
            ]
        )

        problems = treatment_violations([record], ["mcp__lovverk__get_section"])

        assert problems == [
            "llhb-v1-C1-001: MCP server(s) ['lovverk'] did not connect, so the case "
            "ran without the treatment its tools were supposed to provide"
        ]

    def test_the_declared_tools_name_the_server_that_had_to_be_up(self) -> None:
        record = self.make([{"name": "lovverk", "status": "connected"}])

        assert treatment_violations([record], ["mcp__lovverk__get_section"]) == []

    def test_a_declared_tool_that_names_no_server_is_a_finding(self) -> None:
        """A bare name matches no mcp__<server>__ prefix, so the server check
        would expect nothing and pass — a treatment run with no MCP
        connection at all, graded as a fair comparison."""
        record = treatment_record(
            "llhb-v1-C1-001",
            tool_calls=[],
            harness={
                "exposed_tools": ["get_section"],
                "mcp_servers": [],
                "permission_denials": [],
            },
        )

        problems = treatment_violations([record], ["get_section"])

        assert problems == [
            "treatment run declares tool(s) ['get_section'] that name no MCP server, "
            "so nothing about the treatment they were supposed to provide can be checked"
        ]

    def test_one_anonymous_tool_among_namespaced_ones_is_a_finding(self) -> None:
        """The check is over the whole declared surface, not over whether
        some tool of it happens to name a server."""
        declared = ["mcp__lovverk__get_section", "get_law"]
        record = treatment_record(
            "llhb-v1-C1-001",
            harness={
                "exposed_tools": declared,
                "mcp_servers": [{"name": "lovverk", "status": "connected"}],
                "permission_denials": [],
            },
        )

        problems = treatment_violations([record], declared)

        assert any("['get_law']" in problem for problem in problems)

    def test_a_toolless_treatment_run_is_not_a_fair_pair(self) -> None:
        """The whole pair, not just the arm check: a treatment declaring
        only bare tool names, with a harness that matches it exactly and no
        server connected, must not read as a control-treatment comparison."""
        harness = {"exposed_tools": ["get_section"], "mcp_servers": [], "permission_denials": []}
        control = RunArtifacts(
            metadata=metadata(cases_total=1, cases_completed=1),
            records=[record("llhb-v1-C1-001")],
        )
        treatment = RunArtifacts(
            metadata=treatment_metadata(
                cases_total=1,
                cases_completed=1,
                tool_config={**TOOL_CONFIG, "tools": ["get_section"]},
            ),
            records=[treatment_record("llhb-v1-C1-001", harness=harness)],
        )

        assert check_pair(control, treatment, EXPECTED) != []

    def test_a_declared_tool_of_another_server_is_a_finding(self) -> None:
        """The whole surface renamed to another server, with that server
        connected on every case, is internally consistent and has no
        lovspor treatment in it — the server name is part of the claim."""
        record = treatment_record(
            "llhb-v1-C1-001",
            tool_calls=[],
            harness={
                "exposed_tools": ["mcp__decoy__get_section"],
                "mcp_servers": [{"name": "decoy", "status": "connected"}],
                "permission_denials": [],
            },
        )

        problems = treatment_violations([record], ["mcp__decoy__get_section"])

        assert problems == [
            "treatment run declares tool(s) ['mcp__decoy__get_section'] served by some "
            "MCP server other than 'lovverk', which is not the treatment this benchmark measures"
        ]

    def test_a_decoy_server_is_not_a_fair_pair(self) -> None:
        """The whole pair: a treatment whose declared surface, exposed
        surface and connected server all agree on some other server must
        not read as a control-treatment comparison."""
        harness = {
            "exposed_tools": ["mcp__decoy__get_section"],
            "mcp_servers": [{"name": "decoy", "status": "connected"}],
            "permission_denials": [],
        }
        control = RunArtifacts(
            metadata=metadata(cases_total=1, cases_completed=1),
            records=[record("llhb-v1-C1-001")],
        )
        treatment = RunArtifacts(
            metadata=treatment_metadata(
                cases_total=1,
                cases_completed=1,
                tool_config={**TOOL_CONFIG, "tools": ["mcp__decoy__get_section"]},
            ),
            records=[treatment_record("llhb-v1-C1-001", harness=harness)],
        )

        assert check_pair(control, treatment, EXPECTED) != []

    def test_the_server_name_is_the_one_the_runner_serves(self) -> None:
        """Duplicated rather than imported, because mcp_surface pulls in the
        whole MCP server; nothing else stops the two from drifting apart."""
        assert fairness_module._SERVER_NAME == SERVER_NAME

    def test_the_schema_pins_the_same_server(self) -> None:
        """The module refuses a foreign server at grading time, the schema
        refuses one at write time; both name the same server or one of the
        two layers is checking something else."""
        schema = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "benchmarks"
                / "llhb"
                / "schema"
                / "run_metadata.schema.json"
            ).read_text(encoding="utf-8")
        )
        committed = schema["properties"]["tool_config"]["properties"]["tools"]["items"]["pattern"]

        assert committed == f"^mcp__{fairness_module._SERVER_NAME}__.+$"


class TestFrozenSurface:
    """The declaration is compared against the frozen apparatus, not
    believed. Every per-record check reads its expectation out of the
    run's own tool_config, so before this check a run declaring a subset
    of the real surface — transcripts agreeing with the subset — agreed
    only with itself and passed."""

    WIDE = ExpectedSurface(
        tools=("mcp__lovverk__get_section", "mcp__lovverk__search_laws"),
        tool_schema_sha256="c" * 64,
    )

    def test_a_declared_subset_of_the_apparatus_is_a_finding(self) -> None:
        problems = frozen_surface_violations(treatment_metadata(), self.WIDE)

        assert problems == [
            "treatment run declares a surface that is not the frozen apparatus "
            "surface (missing ['mcp__lovverk__search_laws'], unexpected [])"
        ]

    def test_a_declared_superset_is_the_same_finding(self) -> None:
        config = {**TOOL_CONFIG, "tools": [*TOOL_CONFIG["tools"], "mcp__lovverk__get_law"]}

        problems = frozen_surface_violations(treatment_metadata(tool_config=config), EXPECTED)

        assert any("unexpected ['mcp__lovverk__get_law']" in problem for problem in problems)

    def test_a_recorded_hash_that_is_not_the_apparatus_hash_is_a_finding(self) -> None:
        config = {**TOOL_CONFIG, "tool_schema_sha256": "d" * 64}

        problems = frozen_surface_violations(treatment_metadata(tool_config=config), EXPECTED)

        assert len(problems) == 1
        assert "tool_schema_sha256" in problems[0]

    def test_the_declared_apparatus_surface_passes(self) -> None:
        assert frozen_surface_violations(treatment_metadata(), EXPECTED) == []

    def test_a_subset_pair_that_agrees_with_itself_is_not_fair(self) -> None:
        """The Codex repro: metadata narrowed to one tool, every harness
        narrowed to the same one, server connected. Internally consistent,
        and not the treatment the benchmark froze."""
        control = RunArtifacts(
            metadata=metadata(cases_total=1, cases_completed=1),
            records=[record("llhb-v1-C1-001")],
        )
        treatment = RunArtifacts(
            metadata=treatment_metadata(cases_total=1, cases_completed=1),
            records=[treatment_record("llhb-v1-C1-001")],
        )

        assert check_pair(control, treatment, self.WIDE) != []


class TestUndeclaredCalls:
    def test_a_call_outside_the_declared_surface_is_a_finding(self) -> None:
        """The offered-surface check sees what the harness listed, not what
        the model reached; the calls themselves are the other witness."""
        stray = treatment_record(
            "llhb-v1-C1-001",
            tool_calls=[{"index": 0, "name": "mcp__lovverk__search_laws", "arguments": {}}],
        )

        problems = treatment_violations([stray], TOOL_CONFIG["tools"])

        assert problems == [
            "llhb-v1-C1-001: called tool(s) ['mcp__lovverk__search_laws'] "
            "outside the declared surface"
        ]

    def test_calls_within_the_declared_surface_pass(self) -> None:
        assert (
            treatment_violations([treatment_record("llhb-v1-C1-001")], TOOL_CONFIG["tools"]) == []
        )

    def test_an_errored_case_with_stray_calls_is_still_a_finding(self) -> None:
        """A case that errored is exempt from harness evidence, not from
        this: the calls it made were still made."""
        stray = treatment_record(
            "llhb-v1-C1-001",
            completed=False,
            harness=None,
            tool_calls=[{"index": 0, "name": "mcp__lovverk__get_law", "arguments": {}}],
        )

        problems = treatment_violations([stray], TOOL_CONFIG["tools"])

        assert problems == [
            "llhb-v1-C1-001: called tool(s) ['mcp__lovverk__get_law'] outside the declared surface"
        ]


class TestFrozenEvaluation:
    """Stage 7: the published pair is anchored to the frozen dataset, not
    to itself. check_pair proves the two arms agree with each other; both
    can agree on the wrong dataset, a truncated case set, an unpinned
    corpus or an edited prompt, and every one of those is invisible to a
    cross-arm comparison. These checks compare each arm against what the
    publication claims was preregistered."""

    FROZEN = FrozenExpectation(
        dataset_sha256="a" * 64,
        case_ids=("llhb-v1-C1-001", "llhb-v1-C2-001"),
        lovverk_commit="6" * 40,
        system_prompt_sha256="b" * 64,
        system_prompt_path="benchmarks/llhb/runner/system-prompt-v1.txt",
    )

    def full_run(self) -> RunArtifacts:
        return RunArtifacts(
            metadata=metadata(),
            records=[record("llhb-v1-C1-001"), record("llhb-v1-C2-001")],
        )

    def test_a_run_matching_the_frozen_evaluation_passes(self) -> None:
        assert frozen_violations(self.full_run(), "control", self.FROZEN) == []

    @pytest.mark.parametrize(
        ("field", "wrong"),
        [
            ("dataset_checksum", "d" * 64),
            ("lovverk_commit", "f" * 40),
            ("system_prompt_sha256", "e" * 64),
            ("system_prompt_path", "benchmarks/llhb/runner/system-prompt-v2.txt"),
        ],
    )
    def test_a_metadata_value_off_the_frozen_pin_is_a_finding(self, field: str, wrong: str) -> None:
        """Cross-arm equality cannot see these: both arms carrying the same
        wrong value is exactly the failure mode."""
        run = RunArtifacts(
            metadata=metadata(**{field: wrong}),
            records=[record("llhb-v1-C1-001"), record("llhb-v1-C2-001")],
        )

        problems = frozen_violations(run, "control", self.FROZEN)

        assert len(problems) == 1
        assert field in problems[0]
        assert repr(wrong) in problems[0]

    def test_a_frozen_case_the_run_never_ran_is_a_finding(self) -> None:
        run = RunArtifacts(metadata=metadata(), records=[record("llhb-v1-C1-001")])

        problems = frozen_violations(run, "treatment", self.FROZEN)

        assert problems == ["treatment run never ran frozen case(s) ['llhb-v1-C2-001']"]

    def test_a_record_outside_the_frozen_dataset_is_a_finding(self) -> None:
        """Grammar and count are coverage_violations' business; identity is
        this check's. A record whose id parses fine but is not one of the
        250 frozen cases is a different experiment."""
        run = RunArtifacts(
            metadata=metadata(),
            records=[
                record("llhb-v1-C1-001"),
                record("llhb-v1-C2-001"),
                record("llhb-v1-C3-001"),
            ],
        )

        problems = frozen_violations(run, "control", self.FROZEN)

        assert problems == [
            "control run holds record(s) for case(s) ['llhb-v1-C3-001'] "
            "that are not in the frozen dataset"
        ]

    def test_the_label_names_the_arm(self) -> None:
        run = RunArtifacts(metadata=metadata(dataset_checksum="d" * 64), records=[])

        problems = frozen_violations(run, "treatment", self.FROZEN)

        assert all(problem.startswith("treatment run") for problem in problems)

    def test_a_long_id_list_is_sampled_with_an_explicit_count(self) -> None:
        """Hundreds of quoted ids bury every finding around them; a bare
        count hides which cases. The finding carries both: a checkable
        sample and the full count, never a silent cut."""
        many = tuple(f"llhb-v1-C1-{n:03d}" for n in range(1, 31))
        frozen = FrozenExpectation(
            dataset_sha256="a" * 64,
            case_ids=many,
            lovverk_commit="6" * 40,
            system_prompt_sha256="b" * 64,
            system_prompt_path="benchmarks/llhb/runner/system-prompt-v1.txt",
        )
        run = RunArtifacts(metadata=metadata(), records=[])

        problems = frozen_violations(run, "control", frozen)

        assert len(problems) == 1
        assert "and 20 more (30 in total)" in problems[0]
        assert "llhb-v1-C1-010" in problems[0]
        assert "llhb-v1-C1-011" not in problems[0]
