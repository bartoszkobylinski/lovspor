"""Internal consistency of the 20-act observational audit, not source verification."""

import csv
from collections import Counter
from pathlib import Path

_RESEARCH = Path(__file__).resolve().parents[2] / "docs" / "research"
_STEM = "legislative-crosswalk-20"


def _rows() -> list[dict[str, str]]:
    path = _RESEARCH / "data" / f"{_STEM}-case-audit-2026-10-14.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_audit_sample_matches_report_denominators() -> None:
    """The Results section distinguishes acts, cases, years and multi-act rows."""
    rows = _rows()
    assert [row["sample_id"] for row in rows] == [f"{i:02}" for i in range(1, 21)]
    assert len({row["lov_id_normalized_from_storting_sanction"] for row in rows}) == 20
    assert len({(row["session_id"], row["lovvedtak_number"]) for row in rows}) == 20
    cases = Counter(row["sak_id"] for row in rows)
    assert len(cases) == 18
    assert {case: count for case, count in cases.items() if count > 1} == {
        "63056": 2,
        "102903": 2,
    }
    assert Counter(row["case_shape"] for row in rows) == {
        "multi-act": 11,
        "single-act": 9,
    }
    assert {row["lov_id_normalized_from_storting_sanction"].split("-")[1] for row in rows} == {
        "2015",
        "2016",
        "2022",
        "2023",
        "2024",
        "2025",
        "2026",
    }


def test_audit_evidence_levels_do_not_imply_unperformed_checks() -> None:
    """E3 and E4 explicitly distinguish inspected pages from missing captures."""
    checked_counts = {
        "63056": "5",
        "89016": "2",
        "102903": "2",
        "200266": "2",
        "200235": "3",
        "200312": "8",
    }
    checked_rows = []
    for row in _rows():
        assert row["storting_sak_page_pair_attested"] == "yes (same source case page)"
        assert row["vote_overview_link_present"] == "yes"
        assert row["raw_storting_api_xml_captured"] == "no"
        assert row["independent_lovdata_publicdata_id_checked"] == "no"
        case_url = "https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/"
        assert row["case_url"] == f"{case_url}?p={row['sak_id']}"
        if row["sak_id"] in checked_counts:
            checked_rows.append(row)
            assert row["vote_page_opened_and_results_read"] == "yes"
            assert (
                row["vote_count_for_case_first_reading_if_checked"] == checked_counts[row["sak_id"]]
            )
            assert row["checked_vote_page_url"] == (
                f"{case_url}Voteringsoversikt/?dnid=1&p={row['sak_id']}"
            )
        else:
            assert row["vote_page_opened_and_results_read"] == "no"
            assert row["vote_count_for_case_first_reading_if_checked"] == ""
            assert row["checked_vote_page_url"] == ""
    assert len(checked_rows) == 8
    assert len({row["sak_id"] for row in checked_rows}) == 6


def test_audit_markdown_table_matches_csv() -> None:
    """Every displayed act/decision/case and vote observation agrees with the CSV."""
    report = (_RESEARCH / f"{_STEM}-act-audit-2026-10-14.md").read_text(encoding="utf-8")
    table = [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in report.splitlines()
        if line.startswith("| ") and line.split("|")[1].strip().isdigit()
    ]
    assert len(table) == 20
    for cells, row in zip(table, _rows(), strict=True):
        overview = "Link"
        if row["vote_page_opened_and_results_read"] == "yes":
            count = row["vote_count_for_case_first_reading_if_checked"]
            overview = f"[Yes, {count} case votes]({row['checked_vote_page_url']})"
        assert cells == [
            str(int(row["sample_id"])),
            f"`{row['lov_id_normalized_from_storting_sanction']}`",
            f"{row['session_id']}: {row['lovvedtak_number']}",
            f"[{row['sak_id']}]({row['case_url']})",
            row["case_shape"],
            overview,
        ]
