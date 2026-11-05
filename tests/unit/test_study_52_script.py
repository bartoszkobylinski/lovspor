"""scripts/study_52.py: inputs, label-free discovery over a cache, failures, evaluation.

Lovdata and Stortinget documents here are SYNTHETIC, in the real shapes; raw
Lovdata XML is never committed. HTTP is mocked with pytest-httpx only.
"""

import importlib.util
import io
import json
import tarfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.crosswalk import cache as crosswalk_cache
from lovspor.errors import LovsporError
from lovspor.stortinget_case_source import LawDecision
from tests.unit.crosswalk_support import tar_bytes

ROOT = Path(__file__).resolve().parents[2]
NS = 'xmlns="http://data.stortinget.no"'


def _load():
    spec = importlib.util.spec_from_file_location(
        "study_52_under_test", ROOT / "scripts/study_52.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


study = _load()


# --- inputs ------------------------------------------------------------------------


def test_inputs_are_the_frozen_52_with_their_denominators() -> None:
    inputs = study.load_inputs()
    assert (inputs["base_laws"], inputs["eligible_references"], inputs["no_reference"]) == (
        100,
        83,
        17,
    )
    assert inputs["amending_acts"] == len(inputs["mentions"]) == 52
    assert sum(inputs["mentions"].values()) == 83
    assert inputs["mentions"]["LOV-2015-06-19-65"] == 10
    assert inputs["strata"] == {
        "old": {"eligible": 31, "none": 4},
        "mid": {"eligible": 27, "none": 3},
        "new": {"eligible": 25, "none": 10},
    }
    assert sum(law.startswith("LOV-2026-") for law in inputs["mentions"]) == 15


def test_inputs_never_read_the_labels() -> None:
    assert "sak_id" not in json.dumps(study.load_inputs())


def test_committed_study_keeps_all_input_acts_and_csv_matches_json() -> None:
    data = ROOT / "docs/research/data"
    results = json.loads((data / "study-52-2026-11-02.json").read_text(encoding="utf-8"))
    inputs = study.load_inputs()
    law_ids = [law["amending_lov_id"] for law in results["laws"]]
    assert len(law_ids) == len(set(law_ids)) == 52
    assert set(law_ids) == set(inputs["mentions"])
    assert results["release_state"] == "research_only"
    assert study.render_csv(results) == (data / "study-52-2026-11-02.csv").read_text(
        encoding="utf-8"
    )


# --- law edge ----------------------------------------------------------------------


def _document(law_id: str, misc: str) -> bytes:
    slug = law_id.removeprefix("LOV-")
    return (
        '<html lang="no"><body><header class="documentHeader"><dl>'
        f'<dd class="legacyID">{law_id}</dd><dd class="dokid">LTI/lov/{slug}</dd>'
        f'<dd class="miscInformation">{misc}</dd><dd class="refid">lov/{slug}</dd>'
        "</dl></header></body></html>"
    ).encode()


def _tar(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:bz2") as tar:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return path


def _archives(cache: Path, old: dict[str, bytes], new: dict[str, bytes]) -> list[dict]:
    records = []
    for name, members in zip(study.ARCHIVES, (old, new), strict=True):
        path = _tar(cache / "lovdata" / name, members)
        digest = sha256(path.read_bytes()).hexdigest()
        records.append({"filename": name, "path": f"lovdata/{name}", "sha256": digest})
    return records


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ({"lovvedtak": [{"session_id": "2021-2022", "number": 85}], "besl_o": []}, "attested"),
        ({"lovvedtak": [{"session_id": "a", "number": 1}] * 2, "besl_o": []}, "ambiguous"),
        ({"lovvedtak": [], "besl_o": [{"session_id": "2008-2009", "number": 87}]}, "unsupported"),
        ({"lovvedtak": [], "besl_o": []}, "unresolved"),
        ({"error": "ParseError: x"}, "unresolved"),
    ],
)
def test_law_edge_statuses(header: dict, expected: str) -> None:
    assert crosswalk_cache.law_edge(header)["status"] == expected


def test_law_records_cover_both_archives_and_keep_every_outcome(tmp_path: Path) -> None:
    archives = _archives(
        tmp_path,
        {
            "lti/2009/nl-20090619-107.xml": _document(
                "LOV-2009-06-19-107", "Besl.O.nr.87 (2008\u20132009)."
            ),
            "lti/2012/nl-20120120-007.xml": _document("LOV-2012-01-20-7", "Fremmet av MD."),
            "lti/2012/nl-20120127-009.xml": _document("LOV-2012-01-27-8", "wrong identity"),
        },
        {
            "lti/2026/nl-20260306-008.xml": _document(
                "LOV-2026-03-06-8", "Lovvedtak 26 (2025\u20132026)."
            )
        },
    )
    mentions = {
        "LOV-2009-06-19-107": 1,
        "LOV-2012-01-20-7": 2,
        "LOV-2012-01-27-9": 1,
        "LOV-2026-03-06-8": 1,
        "LOV-2026-12-31-99": 1,
    }
    laws = {law["amending_lov_id"]: law for law in study.law_records(tmp_path, archives, mentions)}
    assert list(laws) == sorted(mentions)
    assert laws["LOV-2026-03-06-8"]["lovdata_matches"][0]["archive"] == study.ARCHIVES[1]
    assert laws["LOV-2026-03-06-8"]["law_decision"]["decision"] == {
        "session_id": "2025-2026",
        "number": 26,
    }
    assert laws["LOV-2009-06-19-107"]["law_decision"]["status"] == "unsupported"
    assert laws["LOV-2012-01-20-7"]["law_decision"]["kind"] == "none"
    assert laws["LOV-2012-01-20-7"]["base_act_reference_occurrences"] == 2
    assert laws["LOV-2012-01-27-9"]["law_identity"]["status"] == "unresolved"
    assert "identity mismatch" in laws["LOV-2012-01-27-9"]["law_identity"]["reason"]
    assert laws["LOV-2026-12-31-99"]["law_identity"] == {
        "status": "unresolved",
        "reason": "no member in either archive",
    }
    assert study.cited_decisions(laws.values()) == [LawDecision("2025-2026", 26)]


def test_an_act_in_both_archives_is_ambiguous_not_chosen(tmp_path: Path) -> None:
    body = _document("LOV-2026-03-06-8", "Lovvedtak 26 (2025-2026).")
    member = {"lti/2026/nl-20260306-008.xml": body}
    archives = _archives(tmp_path, member, member)
    (law,) = study.law_records(tmp_path, archives, {"LOV-2026-03-06-8": 1})
    assert law["law_identity"]["status"] == "ambiguous"
    assert len(law["lovdata_matches"]) == 2


def test_listing_keys_include_the_session_before() -> None:
    keys = crosswalk_cache.listing_keys(
        [LawDecision("2025-2026", 26), LawDecision("2024-2025", 41)]
    )
    assert keys == [
        ("publikasjoner", "2024-2025"),
        ("publikasjoner", "2025-2026"),
        ("saker", "2023-2024"),
        ("saker", "2024-2025"),
        ("saker", "2025-2026"),
    ]


# --- discovery over a cache --------------------------------------------------------


def _saker(*items: tuple[int, str]) -> bytes:
    saker = "".join(f"<sak><id>{i}</id><henvisning>{h}</henvisning></sak>" for i, h in items)
    return f"<saker_oversikt {NS}><saker_liste>{saker}</saker_liste></saker_oversikt>".encode()


def _pubs(*ids: str) -> bytes:
    pubs = "".join(f"<publikasjon><id>{i}</id><type>lovvedtak</type></publikasjon>" for i in ids)
    return (
        f"<publikasjoner_oversikt {NS}><publikasjoner_liste>{pubs}"
        "</publikasjoner_liste></publikasjoner_oversikt>"
    ).encode()


def _sak(sak_id: int, session: str, *decisions: tuple[str, int]) -> bytes:
    refs = "".join(
        f"<publikasjon_referanse><eksport_id>vedtak-{s[:4]}{s[-2:]}-{n:03d}</eksport_id>"
        f"<lenke_tekst>Lovvedtak {n} ({s})</lenke_tekst><type>lovvedtak</type>"
        "</publikasjon_referanse>"
        for s, n in decisions
    )
    return (
        f"<detaljert_sak {NS}><id>{sak_id}</id><sak_sesjon>{session}</sak_sesjon>"
        f"<publikasjon_referanse_liste>{refs}</publikasjon_referanse_liste></detaljert_sak>"
    ).encode()


def _put(cache: Path, manifest: dict, role: tuple[str, str, object], body: bytes | None) -> None:
    stage, kind, key = role
    record = {"stage": stage, "kind": kind, "key": key, "source_url": f"https://x/{kind}/{key}"}
    if body is None:
        record.update(error="StortingetStatusError: Stortinget HTTP 429", http_status=429)
    else:
        digest = sha256(body).hexdigest()
        path = cache / f"{stage}-{kind}-{key}-{digest}.xml"
        path.write_bytes(body)
        record.update(
            error=None,
            http_status=200,
            sha256=digest,
            path=path.name,
            content_type="text/xml",
            retrieved_at=datetime(2026, 11, 2, tzinfo=UTC).isoformat(),
        )
    manifest["stortinget"].append(record)


LATER = LawDecision("2025-2026", 26)


LISTED = _pubs("vedtak-202526-026")


def _cross_session_cache(
    tmp_path: Path, *, previous: bytes | None, case: bytes | None, pubs: bytes | None = LISTED
):
    manifest: dict = {"stortinget": []}
    _put(tmp_path, manifest, ("discovery", "publikasjoner", "2025-2026"), pubs)
    _put(
        tmp_path,
        manifest,
        ("discovery", "saker", "2025-2026"),
        _saker((1, "Lovvedtak 260 (2025-2026)")),
    )
    _put(tmp_path, manifest, ("discovery", "saker", "2024-2025"), previous)
    _put(tmp_path, manifest, ("discovery", "sak", 103533), case)
    return manifest


def test_cross_session_case_is_discovered_from_the_session_before(tmp_path: Path) -> None:
    manifest = _cross_session_cache(
        tmp_path,
        previous=_saker((103533, "Innst. 1 L (2025-2026), Lovvedtak 25-27 (2025-2026)")),
        case=_sak(103533, "2024-2025", ("2025-2026", 26)),
    )
    found = study.discovery_record(tmp_path, manifest, LATER)
    assert (found["outcome"], found["sak_ids"], found["candidates"]) == (
        "discovered",
        [103533],
        [103533],
    )
    assert found["publication_listed"] is True
    assert found["export_id"] == "vedtak-202526-026"
    assert crosswalk_cache.discovery_candidates(tmp_path, manifest, [LATER]) == [103533]


FOUND_CASE = {
    "previous": _saker((103533, "Lovvedtak 26 (2025-2026)")),
    "case": _sak(103533, "2024-2025", ("2025-2026", 26)),
}
ATTESTED_LAW = {
    "law_decision": {"status": "attested", "decision": {"session_id": "2025-2026", "number": 26}}
}


def test_a_case_whose_decision_is_not_on_the_publication_list_is_contradicted(
    tmp_path: Path,
) -> None:
    manifest = _cross_session_cache(tmp_path, **FOUND_CASE, pubs=_pubs("vedtak-202526-025"))
    found = study.discovery_record(tmp_path, manifest, LATER)
    assert (found["publication_listed"], found["outcome"], found["sak_ids"]) == (
        False,
        "unlisted",
        [103533],
    )
    assert "absent from the session's Lovvedtak publication list" in found["reason"]
    chain = study._chain(ATTESTED_LAW, found)
    assert chain["status"] == "contradicted"
    assert chain["reason"].startswith("discovery unlisted: the decision is absent")


@pytest.mark.parametrize("pubs", [None, b"<html><body>maintenance</body></html>"])
def test_an_unread_or_unparseable_publication_list_never_attests(
    tmp_path: Path, pubs: bytes | None
) -> None:
    manifest = _cross_session_cache(tmp_path, **FOUND_CASE, pubs=pubs)
    found = study.discovery_record(tmp_path, manifest, LATER)
    assert (found["publication_listed"], found["outcome"], found["sak_ids"]) == (
        None,
        "incomplete",
        [103533],
    )
    assert found["reason"] == "the session's Lovvedtak publication list was not read or parsed"
    chain = study._chain(ATTESTED_LAW, found)
    assert chain == {
        "status": "unresolved",
        "reason": "discovery incomplete: the session's Lovvedtak publication list "
        "was not read or parsed",
    }


def test_a_listed_decision_with_a_confirmed_case_still_attests(tmp_path: Path) -> None:
    manifest = _cross_session_cache(tmp_path, **FOUND_CASE)
    found = study.discovery_record(tmp_path, manifest, LATER)
    assert (found["publication_listed"], found["outcome"], found["reason"]) == (
        True,
        "discovered",
        None,
    )
    assert study._chain(ATTESTED_LAW, found) == {
        "status": "attested",
        "reason": None,
        "sak_id": 103533,
    }


def test_publication_gate_leaves_other_outcomes_alone() -> None:
    for outcome in ("not_found", "ambiguous", "incomplete"):
        for listed in (True, False, None):
            assert study._confirmed(outcome, listed) == (outcome, None)


def test_a_malformed_http_200_saker_listing_is_a_recorded_failure(tmp_path: Path) -> None:
    manifest = _cross_session_cache(tmp_path, previous=b"<saker_oversikt", case=None)
    assert crosswalk_cache.discovery_candidates(tmp_path, manifest, [LATER]) == []
    found = study.discovery_record(tmp_path, manifest, LATER)
    assert (found["outcome"], found["reason"]) == (
        "incomplete",
        "a saker listing was not read or did not parse",
    )
    by_key = {(item["kind"], item["key"]): item for item in found["listings"]}
    broken = by_key[("saker", "2024-2025")]
    assert broken["http_status"] == 200
    assert broken["sha256"] == sha256(b"<saker_oversikt").hexdigest()
    assert broken["parse_error"] == "ParseError: Invalid Stortinget listing XML"
    assert by_key[("saker", "2025-2026")]["parse_error"] is None
    assert by_key[("publikasjoner", "2025-2026")]["parse_error"] is None


def test_a_wrong_root_listing_records_its_parse_error(tmp_path: Path) -> None:
    manifest = _cross_session_cache(tmp_path, **FOUND_CASE, pubs=_saker((1, "x")))
    found = study.discovery_record(tmp_path, manifest, LATER)
    listing = next(item for item in found["listings"] if item["kind"] == "publikasjoner")
    assert listing["parse_error"] == "ParseError: Unexpected Stortinget listing root"
    assert found["outcome"] == "incomplete"


def test_an_incomplete_discovery_keeps_the_law_with_an_unresolved_chain() -> None:
    law = {**ATTESTED_LAW}
    decision = {
        "decision": {"session_id": "2025-2026", "number": 26},
        "outcome": "incomplete",
        "reason": "a saker listing was not read or did not parse",
    }
    study.attach_discovery([law], [decision])
    assert law["discovery"] == {
        "outcome": "incomplete",
        "reason": "a saker listing was not read or did not parse",
        "sak_ids": None,
        "candidates": None,
        "failed": None,
    }
    assert law["chain"]["status"] == "unresolved"
    assert law["chain"]["other_case_decisions"] == []


def test_unread_listing_or_case_is_incomplete_never_not_found(tmp_path: Path) -> None:
    manifest = _cross_session_cache(tmp_path, previous=None, case=None)
    assert study.discovery_record(tmp_path, manifest, LATER)["outcome"] == "incomplete"
    manifest = _cross_session_cache(
        tmp_path, previous=_saker((103533, "Lovvedtak 26 (2025-2026)")), case=None
    )
    found = study.discovery_record(tmp_path, manifest, LATER)
    assert (found["outcome"], found["failed"]) == ("incomplete", [103533])


def test_unparseable_candidate_is_failed_not_absent(tmp_path: Path) -> None:
    manifest = _cross_session_cache(
        tmp_path, previous=_saker((103533, "Lovvedtak 26 (2025-2026)")), case=b"<html/>"
    )
    found = study.discovery_record(tmp_path, manifest, LATER)
    assert (found["outcome"], found["failed"]) == ("incomplete", [103533])


def test_confirmed_case_with_an_unread_candidate_keeps_chain_unresolved(tmp_path: Path) -> None:
    manifest = _cross_session_cache(
        tmp_path,
        previous=_saker((103533, "Lovvedtak 26 (2025-2026)"), (7, "Lovvedtak 26 (2025-2026)")),
        case=_sak(103533, "2024-2025", ("2025-2026", 26)),
    )
    _put(tmp_path, manifest, ("discovery", "sak", 7), None)

    found = study.discovery_record(tmp_path, manifest, LATER)

    assert (found["outcome"], found["sak_ids"], found["candidates"], found["failed"]) == (
        "incomplete",
        [103533],
        [7, 103533],
        [7],
    )
    assert [case["sak_id"] for case in found["cases"]] == [103533]
    law = {"law_decision": {"status": "attested", "decision": found["decision"]}}
    assert study._chain(law, found) == {
        "status": "unresolved",
        "reason": "discovery incomplete",
    }


@pytest.mark.parametrize("kind", ["sak", "saker"])
def test_latest_refusal_does_not_reuse_an_older_success(tmp_path: Path, kind: str) -> None:
    manifest = _cross_session_cache(
        tmp_path,
        previous=_saker((103533, "Lovvedtak 26 (2025-2026)")),
        case=_sak(103533, "2024-2025", ("2025-2026", 26)),
    )
    assert study.discovery_record(tmp_path, manifest, LATER)["outcome"] == "discovered"
    key = 103533 if kind == "sak" else "2024-2025"
    _put(tmp_path, manifest, ("discovery", kind, key), None)

    found = study.discovery_record(tmp_path, manifest, LATER)

    assert found["outcome"] == "incomplete"
    if kind == "sak":
        assert (found["sak_ids"], found["failed"], found["cases"]) == ([], [103533], [])
    else:
        assert found["reason"] == "a saker listing was not read or did not parse"


def test_tampered_cache_is_refused(tmp_path: Path) -> None:
    manifest = _cross_session_cache(
        tmp_path,
        previous=_saker((103533, "Lovvedtak 26 (2025-2026)")),
        case=_sak(103533, "2024-2025"),
    )
    record = manifest["stortinget"][-1]
    (tmp_path / record["path"]).write_bytes(b"changed")
    with pytest.raises(LovsporError, match="do not match the manifest"):
        study.discovery_record(tmp_path, manifest, LATER)


def test_discovery_runs_with_the_labels_unreadable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(study, "LABELS_CSV", tmp_path / "absent.csv")
    manifest = _cross_session_cache(
        tmp_path,
        previous=_saker((103533, "Lovvedtak 26 (2025-2026)"), (7, "Lovvedtak 26 (2025-2026)")),
        case=_sak(103533, "2024-2025", ("2025-2026", 26)),
    )
    _put(tmp_path, manifest, ("discovery", "sak", 7), _sak(7, "2025-2026", ("2025-2026", 26)))
    found = study.discovery_record(tmp_path, manifest, LATER)
    assert (found["outcome"], found["sak_ids"]) == ("ambiguous", [7, 103533])


def test_chain_follows_the_weakest_label_free_edge() -> None:
    law = {"law_decision": {"status": "attested", "decision": {"session_id": "s", "number": 1}}}
    assert study._chain(law, None)["status"] == "unresolved"
    assert study._chain(law, {"outcome": "not_found"})["reason"] == "discovery not_found"
    assert study._chain(law, {"outcome": "ambiguous", "sak_ids": [1, 2]})["status"] == "ambiguous"
    assert study._chain(law, {"outcome": "discovered", "sak_ids": [9]})["sak_id"] == 9
    unsupported = {"law_decision": {"status": "unsupported", "reason": "Besl. O."}}
    assert study._chain(unsupported, None) == {"status": "unsupported", "reason": "Besl. O."}


# --- evaluation --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("discovery", "expected"),
    [
        ({"outcome": "discovered", "sak_ids": [5]}, "match"),
        ({"outcome": "discovered", "sak_ids": [6]}, "mismatch"),
        ({"outcome": "ambiguous", "sak_ids": [5, 6]}, "ambiguous_with_label"),
        ({"outcome": "ambiguous", "sak_ids": [6, 7]}, "ambiguous_without_label"),
        ({"outcome": "not_found", "sak_ids": []}, "not_found"),
        ({"outcome": "incomplete"}, "incomplete"),
    ],
)
def test_discovery_against_label(discovery: dict, expected: str) -> None:
    law = {"law_decision": {"status": "attested"}, "discovery": discovery}
    assert study._discovery_vs_label(law, 5) == expected


def test_recall_is_not_measured_without_a_single_header_lovvedtak() -> None:
    law = {"law_decision": {"status": "unsupported"}, "discovery": {"outcome": "not_attempted"}}
    assert study._discovery_vs_label(law, 43206) == "not_measured"


@pytest.mark.parametrize(
    ("header", "label", "expected"),
    [
        ({"lovvedtak": [{"session_id": "2011-2012", "number": 28}]}, ("Lovvedtak", 28), "agree"),
        ({"lovvedtak": [{"session_id": "2011-2012", "number": 29}]}, ("Lovvedtak", 28), "disagree"),
        ({"besl_o": [{"session_id": "2011-2012", "number": 28}]}, ("Besl.O", 28), "agree"),
        (
            {"besl_o": [{"session_id": "2011-2012", "number": 28}]},
            ("Lovvedtak", 28),
            "not_comparable",
        ),
        ({}, ("Other", 28), "not_comparable"),
    ],
)
def test_header_against_label(header: dict, label: tuple[str, int], expected: str) -> None:
    row = {"proceeding_kind": label[0], "session_id": "2011-2012", "decision_number": str(label[1])}
    assert study._header_vs_label({"header": header}, row) == expected


# --- drift -------------------------------------------------------------------------


def test_drift_row_classifies_by_parsed_identity() -> None:
    same = study.Observation("a", ("sak:1",))
    assert study._drift_row("sak/1", same, study.Observation("b", ("sak:1",)), "x")["verdict"] == (
        "drift_in_bytes_only"
    )
    assert study._drift_row("sak/1", same, study.Observation("b", ("sak:2",)), "x")["verdict"] == (
        "identity_changed"
    )


# --- verification pass -------------------------------------------------------------


def _verification_cache(tmp_path: Path, body: bytes | None) -> dict:
    manifest: dict = {"stortinget": []}
    _put(tmp_path, manifest, ("verification", "sak", 103533), body)
    return manifest


LAW_26 = {
    "amending_lov_id": "LOV-2026-03-06-8",
    "header": {"lovvedtak": [{"session_id": "2025-2026", "number": 26}], "besl_o": []},
    "law_decision": {
        "status": "attested",
        "kind": "lovvedtak",
        "decision": {"session_id": "2025-2026", "number": 26},
    },
    "lovdata_matches": [{"sha256": "0" * 64}],
}


@pytest.mark.parametrize(
    ("decisions", "status", "case_edge"),
    [
        ((("2025-2026", 26),), "attested", "attested"),
        ((("2025-2026", 27),), "contradicted", "contradicted"),
        ((("2025-2026", 26), ("2025-2026", 27)), "attested", "attested"),
        ((), "unresolved", "unresolved"),
    ],
)
def test_label_case_is_verified_against_the_header_decision(
    tmp_path: Path, decisions: tuple, status: str, case_edge: str
) -> None:
    manifest = _verification_cache(tmp_path, _sak(103533, "2024-2025", *decisions))
    snapshot, record = study._case(tmp_path, manifest, "verification", 103533)
    verified = study._verify(LAW_26, snapshot, record)
    assert (verified["status"], verified["law_edge"], verified["case_edge"]) == (
        status,
        "attested",
        case_edge,
    )
    assert verified["case"]["parser_version"] == study.SAK_PARSER_VERSION


def test_unreadable_label_case_is_unresolved_with_its_error(tmp_path: Path) -> None:
    snapshot, record = study._case(
        tmp_path, _verification_cache(tmp_path, None), "verification", 103533
    )
    assert study._verify(LAW_26, snapshot, record) == {
        "status": "unresolved",
        "reason": "StortingetStatusError: Stortinget HTTP 429",
    }
    assert study._verify(LAW_26, None, None)["reason"] == "label case not captured"
    manifest = _verification_cache(tmp_path, b"<html/>")
    snapshot, record = study._case(tmp_path, manifest, "verification", 103533)
    assert study._verify(LAW_26, snapshot, record)["reason"] == "label case bytes do not parse"


def test_besl_o_label_case_is_unsupported_and_still_recorded(tmp_path: Path) -> None:
    manifest = _verification_cache(tmp_path, _sak(103533, "2024-2025"))
    snapshot, record = study._case(tmp_path, manifest, "verification", 103533)
    law = {"law_decision": {"status": "unsupported", "kind": "besl_o", "reason": "Besl. O."}}
    verified = study._verify(law, snapshot, record)
    assert (verified["status"], verified["case"]["sak_id"]) == ("unsupported", 103533)


def test_decision_summary_counts_cross_session_and_multi_decision_cases() -> None:
    def found(session: str, sak_session: str, typed: int) -> dict:
        case = {"sak_sesjon": sak_session, "typed_lovvedtak": [{}] * typed}
        decision = {"session_id": session, "number": 1}
        return {"outcome": "discovered", "decision": decision, "cases": [case], "candidates": [1]}

    decisions = [
        found("2025-2026", "2024-2025", 1),
        found("2024-2025", "2024-2025", 10),
        {"outcome": "incomplete", "decision": {}, "publication_listed": None},
    ]
    summary = study.summarise_decisions(decisions)
    assert summary["case_session_before_decision_session"] == 1
    assert summary["cases_with_several_typed_lovvedtak"] == 1
    assert summary["decision_outcome"] == {"discovered": 2, "incomplete": 1}
    assert summary["candidates_per_decision"] == {"0": 1, "1": 2}


# --- cache location (ADR-0017 D2) ----------------------------------------------------


@pytest.mark.parametrize("gitdir", ["clone", "worktree"])
def test_capture_into_a_checkout_is_refused_before_anything_is_written(
    tmp_path: Path, gitdir: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    if gitdir == "clone":
        (repo / ".git").mkdir()
    else:
        (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    with pytest.raises(LovsporError, match="refusing to write the capture cache inside the Git"):
        study.capture(repo / "data" / "cache" / "study-52")
    assert sorted(p.name for p in repo.rglob("*")) == [".git"]


def test_the_default_capture_cache_is_inside_this_checkout_and_refused() -> None:
    """The in-repo default serves `build` over an old cache; a new capture needs --cache."""
    with pytest.raises(LovsporError, match="inside the Git checkout"):
        study.capture(ROOT / "data" / "cache" / "study-52-never-created")
    assert not (ROOT / "data" / "cache" / "study-52-never-created").exists()


def test_capture_into_the_lovverk_corpus_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "lovverk"
    (corpus / "lover").mkdir(parents=True)
    (corpus / "manifest.json").write_text("{}")
    with pytest.raises(LovsporError, match="inside the Lovverk corpus"):
        study.capture(corpus / "crosswalk" / "study-52")
    assert sorted(p.name for p in corpus.rglob("*")) == ["lover", "manifest.json"]


# --- capture happy path (#610, P2 refactor onto lovspor.crosswalk) -------------------

LOVDATA_API = "https://api.lovdata.no/v1/publicData"
STORTINGET_EKSPORT = "https://data.stortinget.no/eksport"


def test_capture_wires_lovdata_then_verification_and_writes_one_manifest(
    tmp_path: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """study.capture() now delegates to lovspor.crosswalk.capture; this pins the
    glue that stayed in the script: the scope it writes, and the verification
    pass over #617's labels that discovery never sees (#768)."""
    law = "LOV-2022-06-17-63"
    archive_name = "lovtidend-avd1-2026.tar.bz2"
    body = tar_bytes({})  # no member claims `law`, so discovery finds no decision to chase.
    httpx_mock.add_response(
        url=f"{LOVDATA_API}/list",
        json=[
            {
                "filename": archive_name,
                "description": "d",
                "sizeBytes": len(body),
                "lastModified": "2026-10-01T00:00:00Z",
            }
        ],
    )
    httpx_mock.add_response(url=f"{LOVDATA_API}/get/{archive_name}", content=body)

    labels = tmp_path / "labels.csv"
    labels.write_text("sak_id\n103533\n7\n103533\n", encoding="utf-8")
    monkeypatch.setattr(study, "LABELS_CSV", labels)
    monkeypatch.setattr(study, "load_inputs", lambda: {"mentions": {law: 1}})

    for sak_id in (7, 103533):
        httpx_mock.add_response(
            url=f"{STORTINGET_EKSPORT}/sak?sakid={sak_id}",
            content=_sak(sak_id, "2021-2022"),
            headers={"Content-Type": "text/xml"},
        )

    cache = tmp_path / "run"
    manifest = study.capture(cache)

    assert manifest == crosswalk_cache.load_manifest(cache)
    assert manifest["scope"] == {"law_ids": [law], "lovverk_commit": None}
    assert [a["filename"] for a in manifest["lovdata"]] == [archive_name]
    keys = [(r["stage"], r["kind"], r["key"]) for r in manifest["stortinget"]]
    assert keys == [("verification", "sak", 7), ("verification", "sak", 103533)]
    assert all(r["error"] is None for r in manifest["stortinget"])
    assert datetime.fromisoformat(manifest["started_at"]) <= datetime.fromisoformat(
        manifest["finished_at"]
    )
    log = (cache / "progress.log").read_text(encoding="utf-8")
    assert "capture start" in log
    assert "lovdata done" in log
    assert "capture done" in log
    assert not any(
        httpx.URL(str(r.url)).path.startswith("/eksport/saker")
        or httpx.URL(str(r.url)).path.startswith("/eksport/publikasjoner")
        for r in httpx_mock.get_requests()
    )
