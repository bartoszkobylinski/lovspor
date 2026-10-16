"""Check the published contracts of the 2026-10-14 source-first audit.

These checks reconcile local ledgers; they do not attest remote legal sources.
Contracts: docs/research/lovverk-first-100-{preregistration,audit}-2026-10-14.md.
"""

import csv
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "docs" / "research" / "data"


def _rows(name: str) -> list[dict[str, str]]:
    with (DATA / f"lovverk-first-{name}-2026-10-14.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert rows
    assert all(None not in row and None not in row.values() for row in rows)
    return rows


def test_preregistered_inventory_hashes_ranks_and_reserve_boundary() -> None:
    rows = _rows("100-selection-inventory")
    assert len(rows) == len({row["document_id"] for row in rows}) == 150
    for stratum, initial, reserve in (("old", 35, 15), ("mid", 30, 15), ("new", 35, 20)):
        group = [row for row in rows if row["stratum"] == stratum]
        assert [int(row["stratum_rank"]) for row in group] == list(range(1, initial + reserve + 1))
        assert [row["cohort"] for row in group] == ["initial"] * initial + ["reserve"] * reserve
        keys = []
        for row in group:
            document_id = row["document_id"]
            year = int(document_id[3:7])
            assert int(row["document_enactment_year"]) == year
            assert ("old" if year <= 1999 else "mid" if year <= 2015 else "new") == stratum
            value = 2166136261
            for byte in f"lovspor-610-lovverk-first-2026-10-14:{document_id}".encode():
                value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
            assert int(row["fnv1a_uint32"]) == value
            keys.append((value, document_id))
        assert keys == sorted(keys)


def test_all_initial_samples_remain_in_extraction_and_outcome_denominators() -> None:
    inventory = {
        row["document_id"]: row
        for row in _rows("100-selection-inventory")
        if row["cohort"] == "initial"
    }
    extraction = _rows("source-extraction")
    outcomes = _rows("100-audit-outcomes")
    for ledger in (extraction, outcomes):
        assert len(ledger) == len({row["document_id"] for row in ledger}) == 100
        assert {row["document_id"] for row in ledger} == inventory.keys()
        for row in ledger:
            selected = inventory[row["document_id"]]
            for field in ("stratum", "stratum_rank", "lovverk_path"):
                assert row[field] == selected[field]
    extracted = {row["document_id"]: row for row in extraction}
    assert Counter(row["stratum"] for row in extraction if row["status"] == "eligible") == {
        "old": 31,
        "mid": 27,
        "new": 25,
    }
    for row in outcomes:
        source = extracted[row["document_id"]]
        for target, origin in (
            ("extraction_status", "status"),
            ("lovverk_blob_sha", "blob_sha"),
            ("amending_lov_id", "amending_lov_id"),
            ("amendment_note_line", "note_line"),
        ):
            assert row[target] == source[origin], row["document_id"]
        if source["status"] != "eligible":
            assert row["evidence_status"] == "not_applicable_no_eligible_amendment"
            assert not row["amending_lov_id"]
            assert not row["official_case_sak_id"]


def test_deduplicated_proceedings_and_evidence_propagate_to_every_mention() -> None:
    outcomes = _rows("100-audit-outcomes")
    proceedings = _rows("52-proceedings")
    by_law = {row["amending_lov_id"]: row for row in proceedings}
    mentions = Counter(row["amending_lov_id"] for row in outcomes if row["amending_lov_id"])
    assert len(proceedings) == len(by_law) == 52
    assert sum(mentions.values()) == 83
    assert mentions.keys() == by_law.keys()
    assert mentions["LOV-2015-06-19-65"] == 10
    assert mentions["LOV-2025-04-25-12"] == 5
    for law, proceeding in by_law.items():
        assert int(proceeding["base_act_reference_occurrences"]) == mentions[law]
        assert proceeding["raw_storting_xml_captured"] == "no"
        assert proceeding["lovdata_publicdata_independently_checked"] == "no"
        assert proceeding["verified_at_utc_date"] == "2026-10-14"
        assert proceeding["verified_pair_source_url"].startswith("https://")
    for row in outcomes:
        if not row["amending_lov_id"]:
            continue
        proceeding = by_law[row["amending_lov_id"]]
        for target, origin in (
            ("official_case_sak_id", "sak_id"),
            ("decision_kind", "proceeding_kind"),
            ("parliamentary_session", "session_id"),
            ("decision_number", "decision_number"),
            ("evidence_status", "instrument_pair_evidence_status"),
            ("official_case_url", "official_storting_case_url"),
            ("reviewed_pair_evidence_url", "verified_pair_source_url"),
            ("pair_evidence_kind", "pair_evidence_kind"),
            ("official_prior_sanction_source_url", "official_prior_sanction_source_url"),
            ("verified_at_utc_date", "verified_at_utc_date"),
        ):
            assert row[target] == proceeding[origin], (row["document_id"], target)


def test_legacy_decision_and_corrected_sanction_are_preserved() -> None:
    rows = _rows("52-proceedings")
    assert Counter(row["proceeding_kind"] for row in rows) == {"Lovvedtak": 51, "Besl.O": 1}
    by_law = {row["amending_lov_id"]: row for row in rows}
    legacy = by_law["LOV-2009-06-19-107"]
    assert (legacy["proceeding_kind"], legacy["session_id"], legacy["decision_number"]) == (
        "Besl.O",
        "2008-2009",
        "87",
    )
    corrected = by_law["LOV-2025-06-20-67"]
    assert corrected["instrument_pair_evidence_status"] == (
        "official_corrected_resanction_documented_for_lovvedtak_41"
    )
    assert "LOV-2025-04-25-13" in corrected["case_page_display_caveat"]
    assert corrected["official_prior_sanction_source_url"]
    assert corrected["official_prior_sanction_source_url"] != corrected["verified_pair_source_url"]
    discrepancy = by_law["LOV-2026-03-06-8"]
    assert discrepancy["case_page_display_caveat"]
    assert discrepancy["verified_pair_source_url"].startswith("https://www.regjeringen.no/")
