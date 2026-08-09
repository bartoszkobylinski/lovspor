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
    return [
        *compare_run_metadata(control.metadata, treatment.metadata),
        *paired_case_violations(control.records, treatment.records),
        *control_violations(control.records),
        *treatment_violations(treatment.records),
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


def control_violations(records: list[dict[str, Any]]) -> list[str]:
    """Evidence that a control case was not toolless after all (ruling #25)."""
    problems = []
    for record in records:
        case_id = str(record.get("case_id"))
        calls = record.get("tool_calls") or []
        if calls:
            problems.append(f"{case_id}: control case issued {len(calls)} tool call(s)")
        problems.extend(_harness_violations(record, case_id, tools_expected=False))
    return problems


def treatment_violations(records: list[dict[str, Any]]) -> list[str]:
    """Evidence that a treatment case did not actually have the treatment."""
    problems = []
    for record in records:
        problems.extend(
            _harness_violations(record, str(record.get("case_id")), tools_expected=True)
        )
    return problems


def _harness_violations(record: dict[str, Any], case_id: str, *, tools_expected: bool) -> list[str]:
    harness = record.get("harness")
    if not isinstance(harness, dict):
        # An errored case never got far enough to report an environment;
        # a completed one without evidence is an unverifiable measurement.
        return [f"{case_id}: completed without harness evidence"] if record.get("completed") else []
    problems = _offered_violations(harness, case_id, tools_expected=tools_expected)
    denials = harness.get("permission_denials") or []
    if denials:
        problems.append(f"{case_id}: the harness denied {len(denials)} tool call(s)")
    return problems


def _offered_violations(
    harness: dict[str, Any], case_id: str, *, tools_expected: bool
) -> list[str]:
    offered = harness.get("exposed_tools") or []
    if not tools_expected:
        return [f"{case_id}: control case was offered {len(offered)} tool(s)"] if offered else []
    if not offered:
        return [f"{case_id}: treatment case was offered no tools"]
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
