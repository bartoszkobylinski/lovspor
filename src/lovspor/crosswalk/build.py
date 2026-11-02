"""`lovspor ops crosswalk build`: canonical JSONL from a pinned capture, offline (#610, P3).

No network: the build reads only the capture manifest and the bytes it pins, each
re-hashed against its recorded digest. Discovery reads only the capture's
`discovery` stage, so the #617 labels a study's verification pass captured can
never stand in for discovery evidence. The same capture, scope, commits and
previous release always give byte-identical output.

History is append-only (ruling D6): a law whose record is unchanged in substance
keeps its previous revisions and gets nothing new; a changed record is appended
as the next revision; a law absent from this capture keeps its history; nothing
is rewritten or dropped. Supersession is derived from the highest revision.
"""

import copy
from dataclasses import dataclass
from pathlib import Path

from lovspor.crosswalk.cache import (
    Entry,
    Manifest,
    cited_decisions,
    law_records,
    load_manifest,
    parsed_listing,
)
from lovspor.crosswalk.canonical import (
    canonical_json,
    canonical_record,
    parse_records,
    serialize_records,
)
from lovspor.crosswalk.capture import checked_scope
from lovspor.crosswalk.contract import validate_records
from lovspor.crosswalk.discovery import attach_discovery, discovery_record
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.crosswalk.records import Context, Record, build_record, decision_key
from lovspor.crosswalk.rights import NLOD_2_0, licence_basis
from lovspor.stortinget_case_source import LawDecision

_VOLATILE_EVIDENCE = ("body_sha256", "retrieved_at")


@dataclass(frozen=True)
class BuildOptions:
    """Inputs beside the capture. The scope comes from the manifest when it records one."""

    producer_commit: str
    lovverk_commit: str | None = None
    law_ids: tuple[str, ...] | None = None
    previous: bytes | None = None


def _agree(given: object, recorded: object, name: str) -> None:
    if given is not None and recorded is not None and given != recorded:
        raise CrosswalkError(f"the build's {name} differ from the capture's recorded scope")


def _scope(manifest: Manifest, options: BuildOptions) -> tuple[list[str], str]:
    recorded = manifest.get("scope") or {}
    kept_laws, kept_commit = recorded.get("law_ids"), recorded.get("lovverk_commit")
    laws = sorted(set(options.law_ids)) if options.law_ids else None
    _agree(laws, sorted(set(kept_laws)) if kept_laws else None, "law ids")
    _agree(options.lovverk_commit, kept_commit, "Lovverk commit")
    commit = options.lovverk_commit or kept_commit or ""
    return checked_scope(laws or kept_laws or [], commit), commit


def archive_pins(manifest: Manifest) -> list[Record]:
    """Every archive the capture searched, with the licence basis this file has on record."""
    return [
        {
            "source_url": entry["source_url"],
            "sha256": entry["sha256"],
            "size_bytes": entry["size_bytes"],
            "last_modified": entry["last_modified"],
            "retrieved_at": entry["retrieved_at"],
            "licence": NLOD_2_0,
            "licence_basis": licence_basis(entry["filename"]),
        }
        for entry in manifest["lovdata"]
    ]


def _sanctioned(laws: list[Entry]) -> dict[str, list[Entry]]:
    found: dict[str, list[Entry]] = {}
    for law in laws:
        if law["law_decision"]["status"] == "attested":
            found.setdefault(decision_key(law["law_decision"]["decision"]), []).append(law)
    return found


def _publications(
    cache: Path, manifest: Manifest, decisions: list[LawDecision]
) -> dict[str, frozenset[str] | None]:
    sessions = sorted({decision.session_id for decision in decisions})
    return {s: parsed_listing(cache, manifest, "publikasjoner", s) for s in sessions}


def derive_records(cache: Path, manifest: Manifest, options: BuildOptions) -> list[Record]:
    """Revision-1 records of every act in scope, attested or not: no act is dropped."""
    law_ids, lovverk_commit = _scope(manifest, options)
    laws = law_records(cache, manifest["lovdata"], law_ids)
    decisions = cited_decisions(laws)
    found = [discovery_record(cache, manifest, decision) for decision in decisions]
    attach_discovery(laws, found)
    ctx = Context(
        pins=archive_pins(manifest),
        commits=(lovverk_commit, options.producer_commit),
        sanctioned=_sanctioned(laws),
        discoveries={decision_key(d["decision"]): d for d in found},
        publications=_publications(cache, manifest, decisions),
    )
    return [build_record(law, ctx) for law in laws]


def substance(record: Record) -> str:
    """What a record says, without its revision, pins or capture-specific bytes and times."""
    kept = copy.deepcopy(canonical_record(record))
    for name in ("record_revision", "observed_on", "source_pins"):
        kept.pop(name, None)
    for item in kept.get("evidence", []):
        for name in _VOLATILE_EVIDENCE:
            item.pop(name, None)
    return canonical_json(kept)


def append_revisions(fresh: list[Record], previous: list[Record]) -> list[Record]:
    """Previous revisions untouched; a changed act gets the next revision, an unchanged none."""
    latest: dict[str, Record] = {}
    for record in previous:
        current = latest.get(record["amending_lov_id"])
        if current is None or record["record_revision"] > current["record_revision"]:
            latest[record["amending_lov_id"]] = record
    appended = []
    for record in fresh:
        last = latest.get(record["amending_lov_id"])
        if last is not None and substance(last) == substance(record):
            continue
        revision = 1 if last is None else last["record_revision"] + 1
        appended.append({**record, "record_revision": revision})
    return previous + appended


def build(cache: Path, options: BuildOptions) -> bytes:
    """The canonical JSONL of the whole history after this capture, validated before return."""
    previous = parse_records(options.previous) if options.previous is not None else []
    validate_records(previous)
    records = append_revisions(derive_records(cache, load_manifest(cache), options), previous)
    validate_records(records)
    return serialize_records(records)
