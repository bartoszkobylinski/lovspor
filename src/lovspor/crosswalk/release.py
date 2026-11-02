"""The release-candidate gate: deliberately separate and explicit (#610, P4, rulings R1, R2).

The build never promotes anything. This gate promotes only when an approval
file exists that the owner wrote for these exact input bytes (by SHA-256), with
R1 ruled `approved`. It never writes, edits or infers an approval. Then:

- a record is promoted only when the file passes `check` against the capture;
- the record is attested, active and reproduced from the cached bytes;
- every archive its evidence and sanction events cite has a licence basis the
  rulings accept: `stated_for_file` always, `stated_for_endpoint` only when R2
  is `approved`, and `inferred_same_dataset` never;
- it carries no correction (R4 is BLOCKED).

Promotion appends a new revision with `release_state: release_candidate` and
rewrites nothing. Every other record stays `research_only`. There is no
`released` state; publication is outside this tool.
"""

from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lovspor.crosswalk.canonical import Record, serialize_records
from lovspor.crosswalk.check import check, current_records, require_passed
from lovspor.crosswalk.contract import ARCHIVE_URL, validate_records
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.crosswalk.rights import STATED_FOR_ENDPOINT, STATED_FOR_FILE


class Approval(BaseModel):
    """The owner's approval of one input file. Written by the owner, never by this tool."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_by: str = Field(min_length=1)
    approved_on: date
    r1: Literal["approved"] = Field(alias="R1")
    r2: Literal["approved", "rejected"] = Field(alias="R2")


def accepted_bases(approval: Approval) -> frozenset[str]:
    if approval.r2 == "approved":
        return frozenset({STATED_FOR_FILE, STATED_FOR_ENDPOINT})
    return frozenset({STATED_FOR_FILE})


def cited_archives(record: Record) -> set[str]:
    """Archive URLs the record's evidence and sanction events rest on, not merely pins."""
    cited = {e["source_url"] for e in record["sanction_events"]}
    for item in record["evidence"]:
        archive, sep, _ = item["upstream_id"].partition("!")
        if sep:
            cited.add(ARCHIVE_URL.format(archive))
    return cited


def ineligibility(record: Record, bases: frozenset[str]) -> str | None:
    """Why a current record stays research_only, or None when it may be promoted."""
    if record["release_state"] != "research_only":
        return "already a release candidate"
    if record["status"] != "attested" or record["record_state"] != "active":
        return "not an active attested record"
    if any(item["role"] == "correction" for item in record["evidence"]):
        return "carries a correction (R4 is blocked)"
    pins = {p["source_url"]: p["licence_basis"] for p in record["source_pins"]["lovdata_archives"]}
    if any(pins.get(url) not in bases for url in cited_archives(record)):
        return "rests on an archive whose licence basis is not accepted"
    return None


def _promotions(
    records: list[Record], unverified: set[str], bases: frozenset[str]
) -> tuple[list[Record], dict[str, int]]:
    appended, counts = [], {"promoted": 0}
    for law_id, record in sorted(current_records(records).items()):
        why = "not reproduced from the capture" if law_id in unverified else None
        why = why or ineligibility(record, bases)
        if why is not None:
            counts[why] = counts.get(why, 0) + 1
            continue
        revision = record["record_revision"] + 1
        appended.append(
            {**record, "release_state": "release_candidate", "record_revision": revision}
        )
        counts["promoted"] += 1
    return appended, counts


def promote(data: bytes, approval: Approval, cache: Path) -> tuple[bytes, dict[str, int]]:
    """The input plus one promoted revision per eligible record; counts per outcome."""
    if sha256(data).hexdigest() != approval.input_sha256:
        raise CrosswalkError("the approval names other bytes than this input")
    report = check(data, cache=cache)
    require_passed(report)
    appended, counts = _promotions(report.records, set(report.unverified), accepted_bases(approval))
    records = report.records + appended
    validate_records(records)
    return serialize_records(records), counts
