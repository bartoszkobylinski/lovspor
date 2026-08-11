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

import re
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


# Mirrors result_record.schema.json's case_id pattern. Kept as a constant
# rather than read from the schema so this module stays import-light; a test
# asserts the two never drift apart.
_CASE_ID_RE = re.compile(r"^llhb-v1-C[1-8]-[0-9]{3}$")
# A namespaced MCP tool is mcp__<server>__<tool>; the middle field names the
# server whose connection the case depended on.
_NAMESPACED_TOOL_RE = re.compile(r"^mcp__(?P<server>[^_]+(?:_[^_]+)*)__.+$")
# The treatment is one named server (METHODOLOGY §5), not "an MCP server":
# a run whose tools all name `decoy` and whose transcript shows `decoy`
# connected is internally consistent and has no lovspor treatment in it.
# Duplicated from mcp_surface.SERVER_NAME rather than imported — that module
# pulls in the whole MCP server — and mirrored by run_metadata.schema.json's
# tool_config.tools pattern; tests assert all three never drift apart.
_SERVER_NAME = "lovverk"


class RunArtifacts(BaseModel):
    """One run as it sits on disk: its metadata document and its records."""

    metadata: dict[str, Any]
    records: list[dict[str, Any]]


class ExpectedSurface(BaseModel, frozen=True):
    """The apparatus surface this benchmark version froze.

    Loaded from a committed document (``runner/tool-surface-v1.json``)
    that a test regenerates from the code on every run, never from the
    run under judgment: the one thing the previous checks still trusted
    was the run's own account of what it offered.
    """

    tools: tuple[str, ...]
    tool_schema_sha256: str


class FrozenExpectation(BaseModel, frozen=True):
    """What the publication claims was preregistered (METHODOLOGY, FREEZE.md).

    Built by the runner from the committed frozen dataset, its lock and
    the committed prompt file — never from either run. ``check_pair``
    proves the arms agree with each other; both can agree on the wrong
    dataset, and cross-arm equality cannot see that. Pilots run
    discarded candidates by design, so this is a separate gate for the
    published evaluation, not part of ``check_pair``.
    """

    dataset_sha256: str
    case_ids: tuple[str, ...]
    lovverk_commit: str
    system_prompt_sha256: str
    system_prompt_path: str


def frozen_violations(run: RunArtifacts, label: str, expected: FrozenExpectation) -> list[str]:
    """One arm compared against the frozen evaluation, field by field.

    The case-id comparison is by identity, not grammar or count:
    ``coverage_violations`` accepts any well-formed id the dataset
    grammar allows, and a run of 250 well-formed ids can still be a
    different experiment than the 250 the freeze pinned.
    """
    pins = (
        ("dataset_checksum", expected.dataset_sha256),
        ("lovverk_commit", expected.lovverk_commit),
        ("system_prompt_sha256", expected.system_prompt_sha256),
        ("system_prompt_path", expected.system_prompt_path),
    )
    problems = [
        f"{label} run records {field} {run.metadata.get(field)!r}, "
        f"but the frozen evaluation pins {pinned!r}"
        for field, pinned in pins
        if run.metadata.get(field) != pinned
    ]
    problems.extend(_frozen_case_set_violations(run, label, expected))
    return problems


def _frozen_case_set_violations(
    run: RunArtifacts, label: str, expected: FrozenExpectation
) -> list[str]:
    ids = _case_ids(run.records)
    missing = sorted(set(expected.case_ids) - ids)
    extra = sorted(ids - set(expected.case_ids))
    problems = []
    if missing:
        problems.append(f"{label} run never ran frozen case(s) {_preview(missing)}")
    if extra:
        problems.append(
            f"{label} run holds record(s) for case(s) {_preview(extra)} "
            "that are not in the frozen dataset"
        )
    return problems


def _preview(ids: list[str], limit: int = 10) -> str:
    """A findable sample plus an explicit count, never a silent cut.

    A truncated run misses most of the frozen set; a finding quoting
    hundreds of ids buries every other finding around it. The count is
    the substance, the sample makes it checkable.
    """
    if len(ids) <= limit:
        return str(ids)
    return f"{ids[:limit]} and {len(ids) - limit} more ({len(ids)} in total)"


def check_pair(
    control: RunArtifacts, treatment: RunArtifacts, expected: ExpectedSurface
) -> list[str]:
    """Every way this pair fails to be a fair control-treatment comparison."""
    declared = (treatment.metadata.get("tool_config") or {}).get("tools") or []
    return [
        *compare_run_metadata(control.metadata, treatment.metadata),
        *frozen_surface_violations(treatment.metadata, expected),
        *coverage_violations(control, "control"),
        *coverage_violations(treatment, "treatment"),
        *identity_violations(control, "control"),
        *identity_violations(treatment, "treatment"),
        *bookkeeping_violations(control, "control"),
        *bookkeeping_violations(treatment, "treatment"),
        *paired_case_violations(control.records, treatment.records),
        *paired_completion_violations(control.records, treatment.records),
        *control_violations(control.records),
        *treatment_violations(treatment.records, declared),
    ]


def coverage_violations(run: RunArtifacts, label: str) -> list[str]:
    """Records that are missing outright, or fewer than the run claims.

    Every other check here is a statement about the records present, so
    all of them pass vacuously on a run with no records at all. Two
    empty runs agree on everything and compare nothing.
    """
    if not run.records:
        return [f"{label} run has no records, so the pair compares nothing"]
    malformed = sorted(
        {
            str(record.get("case_id"))
            for record in run.records
            if not _CASE_ID_RE.match(str(record.get("case_id") or ""))
        }
    )
    if malformed:
        # str(None) is "None", which pairs happily with another record that
        # also has no id, and any id outside the frozen grammar pairs with
        # its twin in the other arm just as well — two runs agreeing on
        # material the dataset could never have produced.
        return [f"{label} run holds record(s) whose case id is not a dataset id: {malformed}"]
    case_ids = [str(record.get("case_id")) for record in run.records]
    duplicates = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
    if duplicates:
        # Every later check keys cases by id, so a duplicate collapses into
        # one comparison while still counting twice toward the total.
        return [f"{label} run holds more than one record for case(s) {duplicates}"]
    declared = run.metadata.get("cases_total")
    if not isinstance(declared, int) or isinstance(declared, bool):
        return [
            f"{label} run declares no cases_total, so whether it covered the "
            "dataset cannot be checked from the artifacts"
        ]
    if len(case_ids) != declared:
        return [
            f"{label} run holds {len(case_ids)} record(s) but its metadata "
            f"declares {declared} case(s)"
        ]
    return []


def bookkeeping_violations(run: RunArtifacts, label: str) -> list[str]:
    """A run's own summary contradicting the records it is a summary of.

    ``cases_completed`` and ``errors_total`` are exempt from the
    cross-arm comparison, because the arms may legitimately differ
    there. That exemption is what makes them worth checking against
    each run's own records instead: nothing else does.
    """
    completed = sum(1 for record in run.records if record.get("completed"))
    counts = (("cases_completed", completed), ("errors_total", len(run.records) - completed))
    problems = []
    for field, observed in counts:
        declared = run.metadata.get(field)
        if isinstance(declared, int) and not isinstance(declared, bool) and declared != observed:
            problems.append(
                f"{label} run declares {field} {declared}, but its records show {observed}"
            )
    return problems


def identity_violations(run: RunArtifacts, label: str) -> list[str]:
    """Records that do not belong to the run they are filed under.

    ``ResultsStore`` enforces this when a record is written, but this
    module reads committed files directly — a record edited or copied in
    afterwards would otherwise be compared as if it were this run's.
    """
    problems = []
    for record in run.records:
        for field in ("run_id", "provider", "model_id", "condition"):
            if record.get(field) != run.metadata.get(field):
                problems.append(
                    f"{label} record {record.get('case_id')} has {field} "
                    f"{record.get(field)!r}, but the run declares {run.metadata.get(field)!r}"
                )
    return problems


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
    problems.extend(_declared_surface_violations(declared))
    for record in records:
        case_id = str(record.get("case_id"))
        problems.extend(_harness_violations(record, case_id, declared))
        problems.extend(_undeclared_call_violations(record, case_id, declared))
    return problems


def frozen_surface_violations(
    treatment_metadata: dict[str, Any], expected: ExpectedSurface
) -> list[str]:
    """The declared surface, checked against the frozen apparatus.

    Every per-record check reads its expectation out of the run's own
    ``tool_config``, so a run that declared a subset of the real surface
    — and whose transcripts agree with that subset — agreed only with
    itself. The frozen surface is the first fact in this module that the
    run being judged had no hand in.
    """
    config = treatment_metadata.get("tool_config") or {}
    declared = sorted(str(name) for name in config.get("tools") or [])
    problems = []
    if declared != sorted(expected.tools):
        missing = sorted(set(expected.tools) - set(declared))
        extra = sorted(set(declared) - set(expected.tools))
        problems.append(
            "treatment run declares a surface that is not the frozen apparatus "
            f"surface (missing {missing}, unexpected {extra})"
        )
    recorded = config.get("tool_schema_sha256")
    if recorded != expected.tool_schema_sha256:
        problems.append(
            f"treatment run records tool_schema_sha256 {recorded!r}, but the frozen "
            f"apparatus surface hashes to {expected.tool_schema_sha256!r}"
        )
    return problems


def _undeclared_call_violations(
    record: dict[str, Any], case_id: str, declared: tuple[str, ...]
) -> list[str]:
    """Calls the transcript shows to tools the run never declared.

    The offered-surface comparison sees what the harness listed, not
    what the model reached: a record whose calls name tools outside the
    declared surface is evidence the surface description is false,
    whichever of the two documents is lying.
    """
    calls = record.get("tool_calls") or []
    named = {str(call.get("name")) for call in calls if isinstance(call, dict)}
    undeclared = sorted(named - set(declared))
    if undeclared:
        return [f"{case_id}: called tool(s) {undeclared} outside the declared surface"]
    return []


def _declared_surface_violations(declared: tuple[str, ...]) -> list[str]:
    """Declared tools that are not this benchmark's treatment.

    The server check reads its expectation out of these names, so the
    names have to carry a server and it has to be the right one. A bare
    name expects nothing and passes with no server connected at all; a
    name pointing at some other server passes as soon as that other
    server connects, which is a run with no lovspor treatment in it.
    """
    named = [(name, _NAMESPACED_TOOL_RE.match(name)) for name in declared]
    anonymous = sorted(name for name, match in named if not match)
    foreign = sorted(
        name for name, match in named if match and match.group("server") != _SERVER_NAME
    )
    problems: list[str] = []
    if anonymous:
        problems.append(
            f"treatment run declares tool(s) {anonymous} that name no MCP server, "
            "so nothing about the treatment they were supposed to provide can be checked"
        )
    if foreign:
        problems.append(
            f"treatment run declares tool(s) {foreign} served by some MCP server other "
            f"than {_SERVER_NAME!r}, which is not the treatment this benchmark measures"
        )
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
    return _server_violations(harness, case_id, declared)


def _server_violations(
    harness: dict[str, Any], case_id: str, declared: tuple[str, ...]
) -> list[str]:
    """Every server the declared tools name, checked by name.

    "Some server connected" is not the claim: a run where the lovverk
    server failed and an unrelated one connected has no treatment in it
    at all, and would otherwise pass. The names come from the run's own
    declared tools (`mcp__<server>__<tool>`) rather than a constant, so
    the check follows whatever surface the run says it offered.
    """
    connected = {
        str(server.get("name"))
        for server in harness.get("mcp_servers") or []
        if isinstance(server, dict) and server.get("status") == "connected"
    }
    named = (_NAMESPACED_TOOL_RE.match(name) for name in declared)
    expected = {match.group("server") for match in named if match}
    missing = sorted(expected - connected)
    if missing:
        return [
            f"{case_id}: MCP server(s) {missing} did not connect, so the case ran "
            "without the treatment its tools were supposed to provide"
        ]
    return []


def _case_ids(records: list[dict[str, Any]]) -> set[str]:
    return {str(record.get("case_id")) for record in records}
