"""`lovspor ops crosswalk check`: fail-closed validation of a crosswalk file (#610, P4).

A file passes only if all of these hold:
- it is canonical JSONL;
- every record meets the 1.0.0-rc schema and the producer invariants, and the
  history is contiguous;
- given the previous release, every earlier revision is still present,
  byte-identical (ruling D6: nothing rewritten, nothing deleted);
- given the capture, a rebuild from the cached bytes reproduces the parsed
  identity of every current record's evidence.

A current record the capture cannot reproduce is reported as unverified. That
is not a failure of `check`, but it bars the record from the release-candidate
gate.
"""

from dataclasses import dataclass, field
from pathlib import Path

from lovspor.crosswalk.build import BuildOptions, derive_records
from lovspor.crosswalk.cache import load_manifest
from lovspor.crosswalk.canonical import (
    Record,
    canonical_json,
    canonical_record,
    is_canonical,
    parse_records,
    record_key,
)
from lovspor.crosswalk.contract import validate_records
from lovspor.crosswalk.errors import CrosswalkContractError

_IDENTITY_FIELDS = ("role", "upstream_id", "parsed_identity_sha256", "parser_version")
_REBUILD_COMMIT = "0" * 40


@dataclass
class CheckReport:
    problems: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


def current_records(records: list[Record]) -> dict[str, Record]:
    """The highest revision of each law: supersession is derived, never written."""
    latest: dict[str, Record] = {}
    for record in records:
        law_id, revision = record_key(record)
        if law_id not in latest or revision > latest[law_id]["record_revision"]:
            latest[law_id] = record
    return latest


def _lines(records: list[Record]) -> dict[tuple[str, int], str]:
    return {record_key(r): canonical_json(canonical_record(r)) for r in records}


def append_only_problems(records: list[Record], previous: list[Record]) -> list[str]:
    """Every earlier revision present and byte-identical; nothing rewritten or dropped."""
    now, before = _lines(records), _lines(previous)
    problems = []
    for key, line in sorted(before.items()):
        if key not in now:
            problems.append(f"{key[0]}@{key[1]}: revision of the previous release was removed")
        elif now[key] != line:
            problems.append(f"{key[0]}@{key[1]}: revision of the previous release was rewritten")
    return problems


def _identities(record: Record) -> set[tuple[str, ...]]:
    return {tuple(str(item[f]) for f in _IDENTITY_FIELDS) for item in record["evidence"]}


def provenance(records: list[Record], cache: Path) -> tuple[list[str], list[str]]:
    """Rebuild from the cached bytes; compare each current record's evidence identities."""
    manifest, current = load_manifest(cache), current_records(records)
    options = _rebuild_options(manifest, sorted(current))
    fresh = {r["amending_lov_id"]: r for r in derive_records(cache, manifest, options)}
    problems, unverified = [], []
    for law_id, record in sorted(current.items()):
        rebuilt = fresh.get(law_id)
        if rebuilt is None or record["record_state"] != "active":
            unverified.append(law_id)
        elif _identities(record) != _identities(rebuilt):
            problems.append(f"{law_id}: evidence identities are not reproduced from the capture")
    return problems, unverified


def _rebuild_options(manifest: dict[str, object], law_ids: list[str]) -> BuildOptions:
    """The capture's own scope; a capture that records none is rebuilt for the file's acts.

    Commits do not enter evidence identities, so placeholders are used for them.
    """
    if manifest.get("scope"):
        return BuildOptions(producer_commit=_REBUILD_COMMIT)
    laws = tuple(law_ids)
    return BuildOptions(_REBUILD_COMMIT, lovverk_commit=_REBUILD_COMMIT, law_ids=laws)


def _contract_problems(records: list[Record]) -> list[str]:
    try:
        validate_records(records)
    except CrosswalkContractError as exc:
        return list(exc.problems)
    return []


def check(data: bytes, previous: bytes | None = None, cache: Path | None = None) -> CheckReport:
    report = CheckReport()
    try:
        report.records = parse_records(data)
    except CrosswalkContractError as exc:
        report.problems = list(exc.problems)
        return report
    # The contract runs first: canonical ordering reads fields the schema guarantees.
    report.problems = _contract_problems(report.records)
    if report.problems:
        return report
    if not is_canonical(data):
        report.problems.append("file is not in canonical form")
    if previous is not None:
        report.problems += append_only_problems(report.records, parse_records(previous))
    if cache is not None and not report.problems:
        found, report.unverified = provenance(report.records, cache)
        report.problems += found
    return report


def require_passed(report: CheckReport) -> None:
    if not report.passed:
        raise CrosswalkContractError(report.problems)
