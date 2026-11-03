"""The crosswalk manifest, written by the tool from the file it describes (#610, P5).

Everything in it is derived from the checked JSONL bytes and from fixed facts
recorded in this module (each with the date it was read). It has no wall clock,
so the same file always gives the same manifest. The rights blockers stay in it
until the owner resolves them. There is no `released` state: the file-level
`release_state` is `release_candidate` only if a current record is one.
Nothing here publishes.
"""

import json
from collections import Counter
from functools import partial
from hashlib import sha256

from lovspor.crosswalk.canonical import Record, canonical_json, canonical_record
from lovspor.crosswalk.check import check, current_records, require_passed
from lovspor.crosswalk.contract import SCHEMA_VERSION

MANIFEST_VERSION = 1
_pretty = partial(json.dumps, indent=2, sort_keys=True, ensure_ascii=False)
ATTRIBUTION = {
    "nb": (
        "Inneholder data under Norsk lisens for offentlige data (NLOD) tilgjengeliggjort av "
        "Stiftelsen Lovdata (https://api.lovdata.no/v1/publicData/) og Stortinget "
        "(https://data.stortinget.no/). Lisens: https://data.norge.no/nlod/no/2.0. "
        "Dataene er bearbeidet av Lovspor: koblingene er utledet fra kildene og gjengir ikke "
        "kildetekstene."
    ),
    "en": (
        "Contains data under the Norwegian Licence for Open Government Data (NLOD) made "
        "available by Stiftelsen Lovdata and Stortinget. Changed by Lovspor: the links are "
        "derived from the sources and do not reproduce their texts."
    ),
    "status": "provisional",
}
SOURCE_TERMS = [
    {
        "source": "Stiftelsen Lovdata, publicData",
        "terms_url": "https://api.lovdata.no/om-api-tjenesten/",
        "read_on": "2026-11-02",
        "licence": "NLOD-2.0",
    },
    {
        "source": "Stortinget, data.stortinget.no eksport",
        "terms_url": "https://data.stortinget.no/om-datatjenesten/bruksvilkar/",
        "read_on": "2026-11-02",
        "licence": "NLOD (current version; the terms name none)",
    },
]
BLOCKERS = [
    "R3: Lovdata attribution wording unconfirmed; lovdata.no is not read, ask api@lovdata.no",
    "R4: regjeringen.no reuse basis and attribution unruled; no correction is captured",
    "Publication, dataset repository and tags are separate owner steps; none is taken here",
]
COVERAGE = "Covers exactly the acts listed in this file; no claim beyond them."


def _counts(records: list[Record]) -> dict[str, object]:
    current = list(current_records(records).values())
    return {
        "revisions": len(records),
        "acts": len(current),
        "status": dict(sorted(Counter(r["status"] for r in current).items())),
        "release_state": dict(sorted(Counter(r["release_state"] for r in current).items())),
        "record_state": dict(sorted(Counter(r["record_state"] for r in current).items())),
    }


def _pins(records: list[Record]) -> list[Record]:
    """Each distinct archive pin once, by URL then content; a re-pinned archive keeps both."""
    unique = {canonical_json(p): p for r in records for p in r["source_pins"]["lovdata_archives"]}
    return sorted(unique.values(), key=lambda pin: (pin["source_url"], canonical_json(pin)))


def _commits(records: list[Record], name: str) -> list[str]:
    return sorted({r["source_pins"][name] for r in records})


def _parsers(records: list[Record]) -> list[str]:
    return sorted({item["parser_version"] for r in records for item in r["evidence"]})


def _release_state(records: list[Record]) -> str:
    states = {r["release_state"] for r in current_records(records).values()}
    return "release_candidate" if "release_candidate" in states else "research_only"


def build_manifest(data: bytes, filename: str) -> dict[str, object]:
    """The manifest of one checked file; refuses a file that does not pass `check`."""
    report = check(data)
    require_passed(report)
    records = [canonical_record(r) for r in report.records]
    return {
        "manifest_version": MANIFEST_VERSION,
        "schema_version": SCHEMA_VERSION,
        "release_state": _release_state(records),
        "files": [{"name": filename, "sha256": sha256(data).hexdigest(), "size_bytes": len(data)}],
        "counts": _counts(records),
        "producer_commits": _commits(records, "producer_commit"),
        "lovverk_commits": _commits(records, "lovverk_commit"),
        "lovdata_archives": _pins(records),
        "parser_versions": _parsers(records),
        "source_terms": SOURCE_TERMS,
        "attribution": ATTRIBUTION,
        "unresolved_blockers": BLOCKERS,
        "coverage": COVERAGE,
    }


def render_manifest(manifest: dict[str, object]) -> bytes:
    return (_pretty(manifest) + "\n").encode()
