"""Stage 6 fairness checks over committed control/treatment artifacts."""

from typing import Any

from lovspor.llhb.fairness import (
    RunArtifacts,
    check_pair,
    compare_run_metadata,
    control_violations,
    coverage_violations,
    identity_violations,
    paired_case_violations,
    paired_completion_violations,
    treatment_violations,
)

TOOL_CONFIG = {
    "transport": "native-mcp",
    "tools": ["mcp__lovverk__get_section"],
    "tool_schema_sha256": "c" * 64,
    "backend": "local stdio",
}


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

        assert "no MCP server was connected" in problems[0]

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
            [treatment_record("llhb-v1-C1-001", harness=empty)], declared_tools=[]
        )

        assert "treatment run declares no tool surface in tool_config.tools" in problems
        assert any("no MCP server was connected" in problem for problem in problems)

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

        assert check_pair(control, treatment) != []


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

        problems = check_pair(empty_control, empty_treatment)

        assert "control run has no records, so the pair compares nothing" in problems
        assert "treatment run has no records, so the pair compares nothing" in problems

    def test_flags_fewer_records_than_the_run_claims(self) -> None:
        run = RunArtifacts(metadata=metadata(cases_total=2), records=[record("llhb-v1-C1-001")])

        problems = coverage_violations(run, "control")

        assert problems == ["control run holds 1 record(s) but its metadata declares 2 case(s)"]

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

    def test_a_record_matching_its_run_passes(self) -> None:
        run = RunArtifacts(metadata=metadata(cases_total=1), records=[record("llhb-v1-C1-001")])

        assert identity_violations(run, "control") == []


class TestCheckPair:
    def test_a_clean_pilot_pair_reports_nothing(self) -> None:
        control = RunArtifacts(metadata=metadata(cases_total=1), records=[record("llhb-v1-C1-001")])
        treatment = RunArtifacts(
            metadata=treatment_metadata(cases_total=1),
            records=[treatment_record("llhb-v1-C1-001")],
        )

        assert check_pair(control, treatment) == []

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

        problems = check_pair(control, treatment)

        assert any("case_order_seed differs" in problem for problem in problems)
        assert any("never ran case(s)" in problem for problem in problems)
        assert any("issued 1 tool call(s)" in problem for problem in problems)
