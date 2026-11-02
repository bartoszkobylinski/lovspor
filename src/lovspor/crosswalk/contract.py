"""The 1.0.0-rc crosswalk contract: JSON Schema plus the producer invariants (#610).

The schema (`legislative-crosswalk-1.0.0-rc.schema.json`, shipped in this package)
holds what JSON Schema can say. The invariants below are the cross-field and
cross-record rules of proposal item l that it cannot: they run only on a record
the schema accepted, because each assumes the structure the schema guarantees.
Nothing here reads bytes or the network; provenance against cached bytes is the
check stage's work.
"""

import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import cache
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qs, urlsplit

from jsonschema import Draft202012Validator

from lovspor.crosswalk.canonical import Record, record_key
from lovspor.crosswalk.errors import CrosswalkContractError
from lovspor.legislative_chain import member_names_law

SCHEMA_VERSION = "1.0.0-rc"
SCHEMA_RESOURCE = "legislative-crosswalk-1.0.0-rc.schema.json"
ARCHIVE_URL = "https://api.lovdata.no/v1/publicData/get/{}"


@cache
def schema() -> dict[str, Any]:
    text = files("lovspor.crosswalk").joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


@cache
def _validator() -> Draft202012Validator:
    Draft202012Validator.check_schema(schema())
    return Draft202012Validator(schema())


def schema_problems(record: object) -> list[str]:
    errors = _validator().iter_errors(record)
    found = {
        f"/{'/'.join(str(p) for p in error.absolute_path)}: {error.message}" for error in errors
    }
    return sorted(found)


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def _by_role(record: Record, role: str) -> list[Record]:
    return [item for item in record["evidence"] if item["role"] == role]


def _sanctioned_law(record: Record) -> list[str]:
    """Invariant 1: an attested law -> decision edge names the amending act as sanctioned."""
    if record["edge_status"]["law_decision"] != "attested":
        return []
    laws = {event["law_id"] for event in record["sanction_events"]}
    return [] if record["amending_lov_id"] in laws else ["amending law is not a sanction event"]


def _sanction_links(record: Record) -> list[str]:
    """Invariant 2: one event per law; a correction names another event of the record."""
    laws = [event["law_id"] for event in record["sanction_events"]]
    problems = ["two sanction events share a law_id"] if len(laws) != len(set(laws)) else []
    for event in record["sanction_events"]:
        target = event.get("corrects_law_id")
        if target is not None and (target == event["law_id"] or target not in laws):
            problems.append(f"corrects_law_id {target} names no other event of the record")
    return problems


def _case_named(record: Record) -> list[str]:
    """Invariant 3: case evidence is the record's own sak, by URL and by upstream id."""
    problems = []
    for item in _by_role(record, "decision_case"):
        wanted = str(record["sak_id"])
        if (
            _query(item["source_url"]) != {"sakid": [wanted]}
            or item["upstream_id"] != f"sak/{wanted}"
        ):
            problems.append(f"decision_case evidence {item['upstream_id']} is not sak {wanted}")
    return problems


def _publication_named(record: Record) -> list[str]:
    """The publication list read is the decision's own session's Lovvedtak list."""
    decision = record["decision"]
    problems = []
    for item in _by_role(record, "decision_publication"):
        session = decision["session_id"] if decision else None
        expected = {"publikasjontype": ["lovvedtak"], "sesjonid": [str(session)]}
        if (
            _query(item["source_url"]) != expected
            or item["upstream_id"] != f"publikasjoner/{session}"
        ):
            problems.append(f"decision_publication {item['upstream_id']} is not session {session}")
    return problems


def _pinned_archives(record: Record) -> set[str]:
    return {pin["source_url"] for pin in record["source_pins"]["lovdata_archives"]}


def _archives_pinned(record: Record) -> list[str]:
    """Invariant 4: every archive a record cites is pinned, by the URL that names it."""
    pinned, problems = _pinned_archives(record), []
    for item in record["evidence"]:
        archive, sep, _ = item["upstream_id"].partition("!")
        if sep and (
            item["source_url"] != ARCHIVE_URL.format(archive) or item["source_url"] not in pinned
        ):
            problems.append(f"evidence {item['upstream_id']} cites an archive that is not pinned")
    for event in record["sanction_events"]:
        if event["source_url"] not in pinned:
            problems.append(f"sanction event {event['law_id']} cites an archive that is not pinned")
    return problems


def _law_member(record: Record) -> list[str]:
    """Law identity evidence is the amending act's own Lovtidend document."""
    law_id, problems = record["amending_lov_id"], []
    for item in _by_role(record, "law_identity") + _by_role(record, "law_decision_pair"):
        _, _, member = item["upstream_id"].partition("!")
        if not member_names_law(member, law_id):
            problems.append(f"evidence {item['upstream_id']} is not the document of {law_id}")
    return problems


def _one_parser_per_role(record: Record) -> list[str]:
    """Invariant 5: an edge's evidence never mixes parser versions."""
    versions: dict[str, set[str]] = defaultdict(set)
    for item in record["evidence"]:
        versions[item["role"]].add(item["parser_version"])
    return [
        f"{role} evidence mixes parser versions"
        for role, v in sorted(versions.items())
        if len(v) > 1
    ]


def _utc_date(stamp: str) -> str:
    return datetime.fromisoformat(stamp).astimezone(UTC).date().isoformat()


def _observed_on(record: Record) -> list[str]:
    """`observed_on` is the UTC date of the latest capture the record rests on: no wall clock."""
    stamps = [item["retrieved_at"] for item in record["evidence"]]
    stamps += [pin["retrieved_at"] for pin in record["source_pins"]["lovdata_archives"]]
    latest = max((_utc_date(stamp) for stamp in stamps), default=None)
    if latest is None or record["observed_on"] == latest:
        return []
    return [f"observed_on {record['observed_on']} is not the latest capture date {latest}"]


_INVARIANTS = (
    _sanctioned_law,
    _sanction_links,
    _case_named,
    _publication_named,
    _archives_pinned,
    _law_member,
    _one_parser_per_role,
    _observed_on,
)


def record_problems(record: object) -> list[str]:
    """Schema problems; and only when there are none, the invariant problems."""
    found = schema_problems(record)
    if found or not isinstance(record, dict):
        return found
    return [problem for check in _INVARIANTS for problem in check(record)]


def history_problems(records: Iterable[Record]) -> list[str]:
    """Invariant 6: per law, revisions are 1..n without gaps and never repeated."""
    revisions: dict[str, list[int]] = defaultdict(list)
    for record in records:
        law_id, revision = record_key(record)
        revisions[law_id].append(revision)
    return [
        f"{law_id}: revisions {sorted(found)} are not 1..{len(found)} without repeats"
        for law_id, found in sorted(revisions.items())
        if sorted(found) != list(range(1, len(found) + 1))
    ]


def validate_records(records: list[Record]) -> None:
    """Raise with every problem of every record, then the history problems."""
    problems = [
        f"{record.get('amending_lov_id')}@{record.get('record_revision')}: {problem}"
        for record in records
        for problem in record_problems(record)
    ]
    if not problems:
        problems = history_problems(records)
    if problems:
        raise CrosswalkContractError(problems)
