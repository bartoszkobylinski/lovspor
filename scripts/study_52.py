"""Study the legislative chain over the 52 amending acts frozen by #617 (#610, Workstream B).

Two stages, run in order:

  capture  Network. Downloads both Lovtidend avd. I archives from Lovdata publicData
           and the Stortinget listings and cases into the gitignored cache, and
           writes the capture manifest there. Raw bytes never leave the cache.
  build    Offline. Derives every result from the cached bytes and writes the
           committed JSON and CSV. tests/integration/test_study_52_replay.py runs
           the same build and compares it with the committed JSON.

Discovery is label-free: the manually located sak ids of #617 are read only by the
verification pass and the evaluation, never by candidate generation or acceptance.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx

from lovspor.crosswalk.cache import (
    MANIFEST,
    cached_body,
    case_snapshot,
    cited_decisions,
    hints,
    latest,
    listing_parse_error,
    listing_snapshot,
    parsed_listing,
    parses,
    write_manifest,
)
from lovspor.crosswalk.cache import law_records as crosswalk_law_records
from lovspor.crosswalk.capture import Pacer, capture_lovdata, capture_sak, capture_stortinget
from lovspor.legislative_chain import (
    CHAIN_PARSER_VERSION,
    ODELSTING_PARSER_VERSION,
    LawPublication,
    resolve_chain,
)
from lovspor.sources.lovdata import LovdataClient
from lovspor.stortinget_case_source import (
    SAK_PARSER_VERSION,
    CaseMetadata,
    CaseSnapshot,
    LawDecision,
)
from lovspor.stortinget_discovery import (
    DISCOVERY_PARSER_VERSION,
    Observation,
    candidate_saks,
    case_identity,
    classify_drift,
    discover,
    export_id,
    parse_case,
    previous_session,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/research/data"
SOURCE_CSV = DATA / "lovverk-first-source-extraction-2026-10-14.csv"
# Evaluation only. Nothing in the discovery path may read this file.
LABELS_CSV = DATA / "lovverk-first-52-proceedings-2026-10-14.csv"
PILOT_EVIDENCE = DATA / "pilot-e4-evidence-2026-11-01.json"
ARCHIVES = ("lovtidend-avd1-2001-2025.tar.bz2", "lovtidend-avd1-2026.tar.bz2")
PARSER_VERSIONS = {
    "lovtidend_identity": CHAIN_PARSER_VERSION,
    "lovtidend_besl_o": ODELSTING_PARSER_VERSION,
    "stortinget_sak": SAK_PARSER_VERSION,
    "stortinget_discovery": DISCOVERY_PARSER_VERSION,
}
OUT_OF_SCOPE = (
    "vote_to_section",
    "authorship",
    "valid_at",
    "corrective_resanction_relation",
)
# Known ground truth (#617 audit, #618). Read by the evaluation only.
CORRECTIVE_RESANCTION = {"LOV-2025-06-20-67": ("LOV-2025-04-25-13", 101001)}
SOURCE_DISCREPANCY = {"LOV-2026-03-06-8": 103533}


# --- inputs ------------------------------------------------------------------------


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def load_inputs(path: Path = SOURCE_CSV) -> dict[str, Any]:
    """The 52 acts exactly as tests/unit/test_lovverk_first_audit_data.py derives them."""
    rows = _csv_rows(path)
    eligible = [row for row in rows if row["status"] == "eligible"]
    mentions = Counter(row["amending_lov_id"] for row in eligible)
    strata = Counter((row["stratum"], row["status"] == "eligible") for row in rows)
    return {
        "source_csv": str(path.relative_to(ROOT)),
        "source_csv_sha256": sha256(path.read_bytes()).hexdigest(),
        "base_laws": len(rows),
        "eligible_references": len(eligible),
        "no_reference": len(rows) - len(eligible),
        "amending_acts": len(mentions),
        "mentions": dict(sorted(mentions.items())),
        "strata": {
            stratum: {"eligible": strata[(stratum, True)], "none": strata[(stratum, False)]}
            for stratum in ("old", "mid", "new")
        },
    }


# --- capture -----------------------------------------------------------------------
# The network machinery lives in lovspor.crosswalk.capture; only the verification
# pass, which reads the #617 labels, stays here.


def _client() -> httpx.Client:
    return httpx.Client(timeout=60, follow_redirects=False)


def _capture_stortinget(cache: Path, manifest: dict[str, Any], pacer: Pacer) -> None:
    with _client() as client:
        capture_stortinget(client, cache, manifest, pacer)
        # The verification pass reads the labels; discovery above has finished.
        for sak_id in sorted({int(row["sak_id"]) for row in _csv_rows(LABELS_CSV)}):
            record = capture_sak(client, cache, pacer, ("verification", sak_id))
            manifest["stortinget"].append(record)
            write_manifest(cache, manifest)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def capture(cache: Path) -> dict[str, Any]:
    cache.mkdir(parents=True, exist_ok=True)
    pacer = Pacer(cache / "progress.log")
    pacer.note("capture start")
    manifest: dict[str, Any] = {"started_at": _now(), "lovdata": [], "stortinget": []}
    manifest["scope"] = {"law_ids": sorted(load_inputs()["mentions"]), "lovverk_commit": None}
    with LovdataClient() as client:
        manifest["lovdata"] = capture_lovdata(client, cache)
    pacer.note(f"lovdata done: {[r['sha256'][:12] for r in manifest['lovdata']]}")
    write_manifest(cache, manifest)
    _capture_stortinget(cache, manifest, pacer)
    manifest["finished_at"] = _now()
    write_manifest(cache, manifest)
    pacer.note("capture done")
    return manifest


# --- law edge (Lovdata) ------------------------------------------------------------


def law_records(
    cache: Path, archives: list[dict[str, Any]], mentions: dict[str, int]
) -> list[dict[str, Any]]:
    records = crosswalk_law_records(cache, archives, mentions)
    for record in records:
        record["base_act_reference_occurrences"] = mentions[record["amending_lov_id"]]
    return records


# --- discovery (label-free) --------------------------------------------------------
# Cache reading lives in lovspor.crosswalk.cache; these names keep the study's text.
_latest = latest
_cached_body = cached_body
_listing = listing_snapshot
_parsed_listing = parsed_listing
_parse_error = listing_parse_error
_hints = hints
_case = case_snapshot
_parses = parses


def _publication_listed(cache: Path, manifest: dict[str, Any], decision: LawDecision):
    """True or False from the session's Lovvedtak list; None if it was not read or parsed."""
    ids = _parsed_listing(cache, manifest, "publikasjoner", decision.session_id)
    return None if ids is None else export_id(decision) in ids


# Evidence policy (#768): the session's Lovvedtak publication list is a REQUIRED,
# independent confirmation that the decision exists. A typed case reference alone
# never discovers: with the list unread or unparseable the outcome is "incomplete",
# and with the decision absent from a list that was read it is "unlisted", which
# the chain reports as contradicted. Neither can become attested.
_UNCONFIRMED = {
    None: ("incomplete", "the session's Lovvedtak publication list was not read or parsed"),
    False: ("unlisted", "the decision is absent from the session's Lovvedtak publication list"),
}


def _confirmed(outcome: str, listed: bool | None) -> tuple[str, str | None]:
    """Gate a discovery on the publication list; other outcomes pass unchanged."""
    if outcome != "discovered" or listed is True:
        return outcome, None
    return _UNCONFIRMED[listed]


def discovery_record(
    cache: Path, manifest: dict[str, Any], decision: LawDecision
) -> dict[str, Any]:
    hints = _hints(cache, manifest, decision)
    listed = _publication_listed(cache, manifest, decision)
    base = {"decision": asdict(decision), "export_id": export_id(decision)}
    base["publication_listed"] = listed
    base["listings"] = _listings_used(cache, manifest, decision)
    base["parser_versions"] = {
        "stortinget_discovery": DISCOVERY_PARSER_VERSION,
        "stortinget_sak": SAK_PARSER_VERSION,
    }
    if hints is None:
        reason = "a saker listing was not read or did not parse"
        return {**base, "outcome": "incomplete", "reason": reason}
    candidates = candidate_saks(hints, decision)
    pairs = [_case(cache, manifest, "discovery", sak_id) for sak_id in candidates]
    cases = [snapshot for snapshot, _ in pairs if snapshot is not None and _parses(snapshot)]
    failed = [sak_id for sak_id in candidates if sak_id not in {c.sak_id for c in cases}]
    found = discover(decision, cases, failed)
    outcome, reason = _confirmed(found.outcome, listed)
    return {
        **base,
        "outcome": outcome,
        "reason": reason,
        "sak_ids": list(found.sak_ids),
        "candidates": list(found.candidates),
        "failed": list(found.failed),
        "cases": [_case_summary(s) for s in cases if s.sak_id in found.sak_ids],
    }


def _listings_used(
    cache: Path, manifest: dict[str, Any], decision: LawDecision
) -> list[dict[str, Any]]:
    session = decision.session_id
    keys = (("publikasjoner", session), ("saker", session), ("saker", previous_session(session)))
    fields = ("kind", "key", "source_url", "sha256", "retrieved_at", "http_status", "error")
    used = [(key, _latest(manifest, "discovery", *key)) for key in keys]
    return [
        {**{f: record.get(f) for f in fields}, "parse_error": _parse_error(cache, manifest, *key)}
        for key, record in used
        if record is not None
    ]


def _case_summary(snapshot: CaseSnapshot) -> dict[str, Any]:
    case = parse_case(snapshot)
    return {
        "sak_id": case.sak_id,
        "sak_sesjon": case.session_id,
        "typed_lovvedtak": [asdict(d) for d in case.lovvedtak],
        "source_url": snapshot.source_url,
        "sha256": snapshot.source_sha256,
        "retrieved_at": snapshot.observed_at.isoformat(),
        "parser_version": SAK_PARSER_VERSION,
    }


def _chain(law: dict[str, Any], found: dict[str, Any] | None) -> dict[str, Any]:
    """The label-free chain: the header's decision, then a typed, unique case."""
    edge = law["law_decision"]
    if edge["status"] != "attested":
        return {"status": edge["status"], "reason": edge.get("reason")}
    if found is None or found["outcome"] in {"not_found", "incomplete"}:
        outcome = "not_attempted" if found is None else found["outcome"]
        detail = "" if found is None or not found.get("reason") else f": {found['reason']}"
        return {"status": "unresolved", "reason": f"discovery {outcome}{detail}"}
    if found["outcome"] == "unlisted":
        return {"status": "contradicted", "reason": f"discovery unlisted: {found['reason']}"}
    if found["outcome"] == "ambiguous":
        return {"status": "ambiguous", "reason": f"typed in saker {found['sak_ids']}"}
    return {"status": "attested", "reason": None, "sak_id": found["sak_ids"][0]}


def _other_decisions(law: dict[str, Any], found: dict[str, Any] | None) -> list[Any]:
    if found is None or found["outcome"] != "discovered":
        return []
    own = law["law_decision"]["decision"]
    return [d for d in found["cases"][0]["typed_lovvedtak"] if d != own]


def attach_discovery(laws: list[dict[str, Any]], decisions: list[dict[str, Any]]) -> None:
    by_decision = {json.dumps(d["decision"], sort_keys=True): d for d in decisions}
    for law in laws:
        decision = law["law_decision"].get("decision")
        found = by_decision.get(json.dumps(decision, sort_keys=True)) if decision else None
        law["discovery"] = (
            {"outcome": "not_attempted"}
            if found is None
            else {k: found.get(k) for k in ("outcome", "reason", "sak_ids", "candidates", "failed")}
        )
        law["chain"] = _chain(law, found)
        law["chain"]["other_case_decisions"] = _other_decisions(law, found)


# --- verification and evaluation (labels) ------------------------------------------


def _labels() -> dict[str, dict[str, str]]:
    return {row["amending_lov_id"]: row for row in _csv_rows(LABELS_CSV)}


def _verify(law: dict[str, Any], snapshot: CaseSnapshot | None, record: Any) -> dict[str, Any]:
    edge = law["law_decision"]
    if record is None or snapshot is None:
        reason = "label case not captured" if record is None else record["error"]
        return {"status": "unresolved", "reason": reason}
    if not _parses(snapshot):
        return {"status": "unresolved", "reason": "label case bytes do not parse"}
    case = parse_case(snapshot)
    summary = _case_summary(snapshot)
    if edge["kind"] == "besl_o":
        return {"status": "unsupported", "reason": edge["reason"], "case": summary}
    if edge["status"] != "attested":
        return {"status": "unresolved", "reason": "no single header Lovvedtak", "case": summary}
    member = law["lovdata_matches"][0]["sha256"]
    publication_decision = LawDecision(**edge["decision"])
    verdict = resolve_chain(_publication(law, member), case, publication_decision)
    return {
        "status": verdict.status,
        "law_edge": verdict.law_edge,
        "case_edge": verdict.case_edge,
        "other_case_decisions": [asdict(d) for d in verdict.other_case_decisions],
        "case": summary,
    }


def _publication(law: dict[str, Any], member_sha256: str) -> LawPublication:
    cited = tuple(LawDecision(**d) for d in law["header"]["lovvedtak"])
    return LawPublication(law["amending_lov_id"], cited, member_sha256)


def _discovery_vs_label(law: dict[str, Any], label_sak: int) -> str:
    if law["law_decision"]["status"] != "attested":
        return "not_measured"
    outcome = law["discovery"]["outcome"]
    found = law["discovery"].get("sak_ids", [])
    if outcome == "discovered":
        return "match" if found == [label_sak] else "mismatch"
    if outcome == "ambiguous":
        return "ambiguous_with_label" if label_sak in found else "ambiguous_without_label"
    return outcome


def _header_vs_label(law: dict[str, Any], label: dict[str, str]) -> str:
    """The label's decision kind picks which header citation it is compared with."""
    kind = {"Lovvedtak": "lovvedtak", "Besl.O": "besl_o"}.get(label["proceeding_kind"])
    cited = law.get("header", {}).get(kind, []) if kind else []
    expected = {"session_id": label["session_id"], "number": int(label["decision_number"])}
    if not cited:
        return "not_comparable"
    return "agree" if cited == [expected] else "disagree"


def evaluate(cache: Path, manifest: dict[str, Any], laws: list[dict[str, Any]]) -> list[Any]:
    labels = _labels()
    rows = []
    for law in laws:
        label = labels[law["amending_lov_id"]]
        sak_id = int(label["sak_id"])
        snapshot, record = _case(cache, manifest, "verification", sak_id)
        rows.append(
            {
                "amending_lov_id": law["amending_lov_id"],
                "label": {
                    "sak_id": sak_id,
                    "proceeding_kind": label["proceeding_kind"],
                    "session_id": label["session_id"],
                    "decision_number": int(label["decision_number"]),
                },
                "header_vs_label_decision": _header_vs_label(law, label),
                "discovery_vs_label": _discovery_vs_label(law, sak_id),
                "verification": _verify(law, snapshot, record),
            }
        )
    return rows


# --- drift -------------------------------------------------------------------------


def _observation(snapshot: CaseSnapshot) -> Observation:
    return Observation(snapshot.source_sha256, case_identity(parse_case(snapshot)))


def _pilot_observation(record: dict[str, Any]) -> Observation:
    typed = tuple(LawDecision(**d) for d in record["typed_lovvedtak"])
    case = CaseMetadata(record["sak_id"], record["sak_sesjon"], typed, record["sha256"])
    return Observation(record["sha256"], case_identity(case))


def _drift_row(subject: str, before: Observation, after: Observation, label: str):
    return {
        "subject": subject,
        "comparison": label,
        "before_sha256": before.sha256,
        "after_sha256": after.sha256,
        "verdict": classify_drift(before, after),
    }


def drift(cache: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    pilot = json.loads(PILOT_EVIDENCE.read_text(encoding="utf-8"))
    pilot_saks = {r["sak_id"]: r for r in pilot["stortinget"]}
    for record in manifest["stortinget"]:
        if record["kind"] != "sak" or record["stage"] != "verification":
            continue
        after, _ = _case(cache, manifest, "verification", record["key"])
        before, _ = _case(cache, manifest, "discovery", record["key"])
        if after is not None and before is not None:
            row = _drift_row(
                f"sak/{record['key']}",
                _observation(before),
                _observation(after),
                "discovery_vs_verification",
            )
            rows.append(row)
        if after is not None and record["key"] in pilot_saks:
            old = _pilot_observation(pilot_saks[record["key"]])
            rows.append(
                _drift_row(
                    f"sak/{record['key']}",
                    old,
                    _observation(after),
                    "pilot_2026-11-01_vs_verification",
                )
            )
    return _dedupe(rows)


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for row in rows:
        key = json.dumps(row, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return sorted(out, key=lambda r: (r["subject"], r["comparison"]))


def lovdata_drift(manifest: dict[str, Any], laws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pilot (2026-11-01) against this capture: archive digest, and the shared acts' members."""
    pilot = json.loads(PILOT_EVIDENCE.read_text(encoding="utf-8"))
    archive = next(r for r in manifest["lovdata"] if r["filename"] == ARCHIVES[0])
    rows = [
        {
            "subject": ARCHIVES[0],
            "comparison": "pilot_2026-11-01_vs_study",
            "before_sha256": pilot["lovdata_archive"]["sha256"],
            "after_sha256": archive["sha256"],
            "before_last_modified": pilot["lovdata_archive"]["last_modified"],
            "after_last_modified": archive["last_modified"],
            "verdict": "identical"
            if pilot["lovdata_archive"]["sha256"] == archive["sha256"]
            else "archive_digest_changed",
        }
    ]
    by_law = {law["amending_lov_id"]: law for law in laws}
    for claim in pilot["claims"]:
        law = by_law.get(claim["law_id"])
        if law is None or len(law["lovdata_matches"]) != 1 or "lovdata_member_sha256" not in claim:
            continue
        before = Observation(
            claim["lovdata_member_sha256"], _cited_identity(claim["lovdata_cited_decisions"])
        )
        after = Observation(
            law["lovdata_matches"][0]["sha256"], _cited_identity(law["header"].get("lovvedtak", []))
        )
        rows.append(
            _drift_row(f"member/{claim['law_id']}", before, after, "pilot_2026-11-01_vs_study")
        )
    return _dedupe(rows)


def _cited_identity(decisions: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(f"lovvedtak:{d['session_id']}:{d['number']}" for d in decisions)


# --- summary and output ------------------------------------------------------------


def _count(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(v) for v in values).items()))


def summarise(laws: list[dict[str, Any]], evaluation: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [
        e
        for e, law in zip(evaluation, laws, strict=True)
        if law["law_decision"]["status"] == "attested"
    ]
    verified = [e["verification"]["status"] for e in eligible]
    return {
        "lovdata_archive_of_member": _count(
            "+".join(sorted({m["archive"] for m in law["lovdata_matches"]})) or "none"
            for law in laws
        ),
        "law_identity": _count(law["law_identity"]["status"] for law in laws),
        "header_citation": _count(law["law_decision"]["kind"] for law in laws),
        "law_decision_edge": _count(law["law_decision"]["status"] for law in laws),
        "discovery_outcome": _count(law["discovery"]["outcome"] for law in laws),
        "chain_status_label_free": _count(law["chain"]["status"] for law in laws),
        "chain_status_by_year": _by(laws, "law_year"),
        "chain_status_by_archive": _by_archive(laws),
        "discovery_vs_label": _count(e["discovery_vs_label"] for e in evaluation),
        "discovery_recall_denominator": len(eligible),
        "discovery_recall_matches": sum(e["discovery_vs_label"] == "match" for e in eligible),
        "header_vs_label_decision": _count(e["header_vs_label_decision"] for e in evaluation),
        "verification_status_all": _count(e["verification"]["status"] for e in evaluation),
        "verification_denominator": len(eligible),
        "verification_attested": verified.count("attested"),
    }


def summarise_decisions(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """How the discovered cases relate to their decision's session and to other decisions."""
    found = [d for d in decisions if d["outcome"] == "discovered"]
    return {
        "decisions_searched": len(decisions),
        "decision_outcome": _count(d["outcome"] for d in decisions),
        "publication_listed": _count(d.get("publication_listed") for d in decisions),
        "candidates_per_decision": _count(len(d.get("candidates", [])) for d in decisions),
        "case_session_before_decision_session": sum(
            d["cases"][0]["sak_sesjon"] != d["decision"]["session_id"] for d in found
        ),
        "cases_with_several_typed_lovvedtak": sum(
            len(d["cases"][0]["typed_lovvedtak"]) > 1 for d in found
        ),
    }


def _by(laws: list[dict[str, Any]], field: str) -> dict[str, dict[str, int]]:
    groups: dict[str, list[str]] = {}
    for law in laws:
        groups.setdefault(str(law[field]), []).append(law["chain"]["status"])
    return {key: _count(values) for key, values in sorted(groups.items())}


def _by_archive(laws: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    groups: dict[str, list[str]] = {}
    for law in laws:
        key = "+".join(sorted({m["archive"] for m in law["lovdata_matches"]})) or "none"
        groups.setdefault(key, []).append(law["chain"]["status"])
    return {key: _count(values) for key, values in sorted(groups.items())}


def known_cases(laws: list[dict[str, Any]], evaluation: list[dict[str, Any]]) -> list[Any]:
    """What the sources say on the #618 and 103533 cases; no relation is inferred."""
    by_law = {e["amending_lov_id"]: (law, e) for law, e in zip(laws, evaluation, strict=True)}
    rows = []
    for law_id, (earlier, sak_id) in CORRECTIVE_RESANCTION.items():
        law, evaluated = by_law[law_id]
        rows.append(
            {
                "case": "#618 corrective re-sanction",
                "amending_lov_id": law_id,
                "earlier_sanction_not_in_52": earlier,
                "sak_id": sak_id,
                "header_lovvedtak": law.get("header", {}).get("lovvedtak", []),
                "label_free_chain": law["chain"]["status"],
                "verification": evaluated["verification"]["status"],
                "corrective_relation": {
                    "status": "unsupported",
                    "reason": "no source read here states which act corrects which (#618)",
                },
            }
        )
    for law_id, sak_id in SOURCE_DISCREPANCY.items():
        law, evaluated = by_law[law_id]
        rows.append(
            {
                "case": "sak 103533 case page vs government notice",
                "amending_lov_id": law_id,
                "sak_id": sak_id,
                "header_lovvedtak": law.get("header", {}).get("lovvedtak", []),
                "label_free_chain": law["chain"]["status"],
                "discovery": law["discovery"],
                "verification": evaluated["verification"]["status"],
            }
        )
    return rows


def build(cache: Path) -> dict[str, Any]:
    manifest = json.loads((cache / MANIFEST).read_text(encoding="utf-8"))
    inputs = load_inputs()
    laws = law_records(cache, manifest["lovdata"], inputs["mentions"])
    decisions = [discovery_record(cache, manifest, d) for d in cited_decisions(laws)]
    attach_discovery(laws, decisions)
    evaluation = evaluate(cache, manifest, laws)
    return {
        "study": "lovspor #610 workstream B: legislative chain over the 52 acts of #617",
        "release_state": "research_only",
        "parser_versions": PARSER_VERSIONS,
        "out_of_scope": list(OUT_OF_SCOPE),
        "inputs": inputs,
        "capture": {
            "started_at": manifest["started_at"],
            "finished_at": manifest.get("finished_at"),
            "lovdata": manifest["lovdata"],
            "stortinget": [
                {k: v for k, v in r.items() if k != "path"} for r in manifest["stortinget"]
            ],
        },
        "laws": laws,
        "decisions": decisions,
        "evaluation": evaluation,
        "known_cases": known_cases(laws, evaluation),
        "drift": drift(cache, manifest) + lovdata_drift(manifest, laws),
        "summary": {**summarise(laws, evaluation), **summarise_decisions(decisions)},
    }


CSV_COLUMNS = (
    "amending_lov_id",
    "law_year",
    "base_act_reference_occurrences",
    "lovdata_archive",
    "lovdata_member",
    "lovdata_member_sha256",
    "law_identity",
    "header_citation",
    "header_session_id",
    "header_number",
    "law_decision_edge",
    "discovery_outcome",
    "discovered_sak_ids",
    "candidate_sak_ids",
    "failed_sak_ids",
    "chain_status",
    "chain_reason",
    "other_case_decisions",
    "label_sak_id",
    "header_vs_label_decision",
    "discovery_vs_label",
    "verification_status",
    "verification_law_edge",
    "verification_case_edge",
    "verification_sak_sha256",
    "verification_sak_url",
)


def _ids(values: Iterable[Any]) -> str:
    return " ".join(str(v) for v in values)


def csv_row(law: dict[str, Any], evaluated: dict[str, Any]) -> dict[str, Any]:
    match = law["lovdata_matches"][0] if len(law["lovdata_matches"]) == 1 else {}
    decision = law["law_decision"].get("decision", {})
    verification = evaluated["verification"]
    case = verification.get("case", {})
    others = law["chain"]["other_case_decisions"]
    return {
        "amending_lov_id": law["amending_lov_id"],
        "law_year": law["law_year"],
        "base_act_reference_occurrences": law["base_act_reference_occurrences"],
        "lovdata_archive": match.get("archive", ""),
        "lovdata_member": match.get("member", ""),
        "lovdata_member_sha256": match.get("sha256", ""),
        "law_identity": law["law_identity"]["status"],
        "header_citation": law["law_decision"]["kind"],
        "header_session_id": decision.get("session_id", ""),
        "header_number": decision.get("number", ""),
        "law_decision_edge": law["law_decision"]["status"],
        "discovery_outcome": law["discovery"]["outcome"],
        "discovered_sak_ids": _ids(law["discovery"].get("sak_ids", [])),
        "candidate_sak_ids": _ids(law["discovery"].get("candidates", [])),
        "failed_sak_ids": _ids(law["discovery"].get("failed", [])),
        "chain_status": law["chain"]["status"],
        "chain_reason": law["chain"]["reason"] or "",
        "other_case_decisions": _ids(f"{d['session_id']}/{d['number']}" for d in others),
        "label_sak_id": evaluated["label"]["sak_id"],
        "header_vs_label_decision": evaluated["header_vs_label_decision"],
        "discovery_vs_label": evaluated["discovery_vs_label"],
        "verification_status": verification["status"],
        "verification_law_edge": verification.get("law_edge", ""),
        "verification_case_edge": verification.get("case_edge", ""),
        "verification_sak_sha256": case.get("sha256", ""),
        "verification_sak_url": case.get("source_url", ""),
    }


def render_csv(results: dict[str, Any]) -> str:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for law, evaluated in zip(results["laws"], results["evaluation"], strict=True):
        writer.writerow(csv_row(law, evaluated))
    return stream.getvalue()


def render_json(results: dict[str, Any]) -> str:
    return json.dumps(results, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("capture", "build"))
    parser.add_argument("--cache", type=Path, default=ROOT / "data/cache/study-52")
    parser.add_argument("--json", type=Path, default=DATA / "study-52-2026-11-02.json")
    parser.add_argument("--csv", type=Path, default=DATA / "study-52-2026-11-02.csv")
    args = parser.parse_args()
    if args.stage == "capture":
        capture(args.cache)
        return 0
    results = build(args.cache)
    args.json.write_text(render_json(results), encoding="utf-8")
    args.csv.write_text(render_csv(results), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
