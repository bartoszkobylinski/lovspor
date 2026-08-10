"""Stage 6 fairness and payload checks (METHODOLOGY §5, ruling #25).

The benchmark's whole claim is that one pair of runs differed in
exactly one respect: whether the model could reach the pinned corpus.
This module checks that claim against the artifacts the runs left
behind, instead of trusting the intent of the code that produced them.

Everything here reads committed run metadata and result records. It
never re-runs anything, so a violation found here is a fact about the
stored evidence: a run whose fairness report is not empty must not be
reported as a control-treatment comparison.
"""

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

# Fields whose whole purpose is to differ between the two arms, plus the
# per-run bookkeeping that no design invariant constrains. Everything
# else must match, including the dataset checksum, the prompt hash, the
# model id, the seed and every recorded commit.
_MAY_DIFFER = frozenset(
    {
        "run_id",
        "condition",
        "tool_config",
        "started_at",
        "finished_at",
        "notes",
        "cases_completed",
        "errors_total",
        "evaluator_version",
    }
)


class RunArtifacts(BaseModel):
    """One run as it sits on disk: its metadata document and its records."""

    metadata: dict[str, Any]
    records: list[dict[str, Any]]


def check_pair(control: RunArtifacts, treatment: RunArtifacts) -> list[str]:
    """Every way this pair fails to be a fair control-treatment comparison."""
    declared = (treatment.metadata.get("tool_config") or {}).get("tools") or []
    return [
        *compare_run_metadata(control.metadata, treatment.metadata),
        *paired_case_violations(control.records, treatment.records),
        *paired_completion_violations(control.records, treatment.records),
        *control_violations(control.records),
        *treatment_violations(treatment.records, declared),
    ]


def compare_run_metadata(control: dict[str, Any], treatment: dict[str, Any]) -> list[str]:
    """Differences between the two run-metadata documents the design forbids."""
    problems = _condition_labels(control, treatment)
    for key in sorted((set(control) | set(treatment)) - _MAY_DIFFER):
        if key not in control or key not in treatment:
            problems.append(f"{key} is recorded in only one of the two runs")
        elif control[key] != treatment[key]:
            problems.append(
                f"{key} differs: control {control[key]!r}, treatment {treatment[key]!r}"
            )
    return problems


def _condition_labels(control: dict[str, Any], treatment: dict[str, Any]) -> list[str]:
    problems = []
    if control.get("condition") != "control":
        problems.append(f"control run is labelled {control.get('condition')!r}")
    if treatment.get("condition") != "lovspor":
        problems.append(f"treatment run is labelled {treatment.get('condition')!r}")
    return problems


def paired_case_violations(
    control_records: list[dict[str, Any]], treatment_records: list[dict[str, Any]]
) -> list[str]:
    """Cases one arm ran and the other did not."""
    control_ids = _case_ids(control_records)
    treatment_ids = _case_ids(treatment_records)
    problems = []
    for label, missing in (
        ("treatment", control_ids - treatment_ids),
        ("control", treatment_ids - control_ids),
    ):
        if missing:
            problems.append(f"{label} run never ran case(s) {sorted(missing)}")
    return problems


def paired_completion_violations(
    control_records: list[dict[str, Any]], treatment_records: list[dict[str, Any]]
) -> list[str]:
    """Cases that produced no comparison, in one arm or in both.

    A case that errored has no answer, and an errored case is also
    allowed to carry no harness evidence — so without this check a pair
    where one arm never finished passes every other test.
    """
    treatment_by_id = {str(record.get("case_id")): record for record in treatment_records}
    problems = []
    for record in sorted(control_records, key=lambda item: str(item.get("case_id"))):
        case_id = str(record.get("case_id"))
        other = treatment_by_id.get(case_id)
        if other is None:
            continue  # a missing case is paired_case_violations' finding, not this one
        problems.extend(_completion_pair(case_id, bool(record.get("completed")), other))
    return problems


def _completion_pair(case_id: str, control_done: bool, treatment: dict[str, Any]) -> list[str]:
    treatment_done = bool(treatment.get("completed"))
    if control_done and not treatment_done:
        return [f"{case_id}: completed in the control arm only"]
    if treatment_done and not control_done:
        return [f"{case_id}: completed in the treatment arm only"]
    if not control_done:
        return [f"{case_id}: errored in both arms, so it compares nothing"]
    return []


def control_violations(records: list[dict[str, Any]]) -> list[str]:
    """Evidence that a control case was not toolless after all (ruling #25)."""
    problems = []
    for record in records:
        case_id = str(record.get("case_id"))
        calls = record.get("tool_calls") or []
        if calls:
            problems.append(f"{case_id}: control case issued {len(calls)} tool call(s)")
        problems.extend(_harness_violations(record, case_id, None))
    return problems


def treatment_violations(records: list[dict[str, Any]], declared_tools: Sequence[str]) -> list[str]:
    """Evidence that a treatment case did not actually have the treatment.

    ``declared_tools`` is the run metadata's ``tool_config.tools``: what
    the run says it offered. The transcript says what it did offer, and
    the two must be the same surface, not merely the same size.

    The arm is decided by the caller, never inferred from that list
    being empty — a treatment run declaring no tools is a finding, not a
    control run.
    """
    declared = tuple(str(name) for name in declared_tools)
    problems = [] if declared else ["treatment run declares no tool surface in tool_config.tools"]
    for record in records:
        problems.extend(_harness_violations(record, str(record.get("case_id")), declared))
    return problems


def _harness_violations(
    record: dict[str, Any], case_id: str, declared: tuple[str, ...] | None
) -> list[str]:
    """``declared`` is None for the control arm, a surface for treatment."""
    harness = record.get("harness")
    if not isinstance(harness, dict):
        # An errored case never got far enough to report an environment;
        # a completed one without evidence is an unverifiable measurement.
        # An errored case is caught by paired_completion_violations.
        return [f"{case_id}: completed without harness evidence"] if record.get("completed") else []
    problems = _offered_violations(harness, case_id, declared)
    denials = harness.get("permission_denials") or []
    if denials:
        problems.append(f"{case_id}: the harness denied {len(denials)} tool call(s)")
    return problems


def _offered_violations(
    harness: dict[str, Any], case_id: str, declared: tuple[str, ...] | None
) -> list[str]:
    offered = tuple(str(name) for name in harness.get("exposed_tools") or ())
    if declared is None:
        return [f"{case_id}: control case was offered {len(offered)} tool(s)"] if offered else []
    if sorted(offered) != sorted(declared):
        missing = sorted(set(declared) - set(offered))
        extra = sorted(set(offered) - set(declared))
        return [
            f"{case_id}: offered surface does not match the declared surface "
            f"(missing {missing}, unexpected {extra})"
        ]
    return _server_violations(harness, case_id)


def _server_violations(harness: dict[str, Any], case_id: str) -> list[str]:
    servers = harness.get("mcp_servers") or []
    connected = [
        server
        for server in servers
        if isinstance(server, dict) and server.get("status") == "connected"
    ]
    if not connected:
        return [f"{case_id}: no MCP server was connected; the case ran without the treatment"]
    return []


def _case_ids(records: list[dict[str, Any]]) -> set[str]:
    return {str(record.get("case_id")) for record in records}
