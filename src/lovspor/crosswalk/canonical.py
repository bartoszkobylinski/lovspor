"""Canonical bytes of crosswalk records (proposal item l, ruling D11).

One JSON object per line, UTF-8, sorted keys, `,`/`:` separators, `\\n` line ends.
Records sort by `(amending_lov_id, record_revision)`; inside a record, evidence by
`(role, upstream_id, source_url)`, sanction events by `law_id` and archive pins by
`source_url`. The same records always give the same bytes, whatever order they
were produced in.
"""

import json
from collections.abc import Iterable
from functools import partial
from typing import Any

from lovspor.crosswalk.errors import CrosswalkContractError

Record = dict[str, Any]
UTF8 = "utf-8"
# One fixed set of options, so no call site can drift from the canonical form.
_dumps = partial(json.dumps, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def canonical_json(value: object) -> str:
    return _dumps(value)


def record_key(record: Record) -> tuple[str, int]:
    return str(record["amending_lov_id"]), int(record["record_revision"])


def _evidence_key(item: Record) -> tuple[str, str, str]:
    return str(item["role"]), str(item.get("upstream_id", "")), str(item["source_url"])


def canonical_record(record: Record) -> Record:
    """The record with every list in its canonical order; no key is added or dropped."""
    out = dict(record)
    if "evidence" in out:
        out["evidence"] = sorted(out["evidence"], key=_evidence_key)
    if "sanction_events" in out:
        out["sanction_events"] = sorted(out["sanction_events"], key=lambda e: e["law_id"])
    pins = out.get("source_pins")
    if isinstance(pins, dict) and "lovdata_archives" in pins:
        archives = sorted(pins["lovdata_archives"], key=lambda pin: pin["source_url"])
        out["source_pins"] = {**pins, "lovdata_archives": archives}
    return out


def serialize_records(records: Iterable[Record]) -> bytes:
    ordered = sorted((canonical_record(r) for r in records), key=record_key)
    return "".join(canonical_json(record) + "\n" for record in ordered).encode()


def _refuse_constant(name: str) -> float:
    raise CrosswalkContractError([f"non-finite number {name} is not JSON"])


def _parsed_line(number: int, line: str) -> Record:
    try:
        value = json.loads(line, parse_constant=_refuse_constant)
    except json.JSONDecodeError as exc:
        raise CrosswalkContractError([f"line {number}: not JSON ({exc.msg})"]) from exc
    if not isinstance(value, dict):
        raise CrosswalkContractError([f"line {number}: not a JSON object"])
    return value


def parse_records(data: bytes) -> list[Record]:
    """Read a JSONL file strictly: UTF-8, one object per line, final newline, no blank line."""
    try:
        text = data.decode()
    except UnicodeDecodeError as exc:
        raise CrosswalkContractError(["file is not UTF-8"]) from exc
    if text and not text.endswith("\n"):
        raise CrosswalkContractError(["file does not end with a newline"])
    lines = text.split("\n")[:-1]
    if any(not line for line in lines):
        raise CrosswalkContractError(["file has a blank line"])
    return [_parsed_line(number, line) for number, line in enumerate(lines, start=1)]


def is_canonical(data: bytes) -> bool:
    """True only if re-serializing the parsed records gives exactly these bytes."""
    return serialize_records(parse_records(data)) == data
