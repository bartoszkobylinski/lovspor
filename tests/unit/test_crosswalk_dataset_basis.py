"""The dataset-level licence basis of the current-year Lovtidend avd. I archive (ADR-0021).

R2 (2026-11-06) rejected the endpoint statement for `lovtidend-avd1-2026.tar.bz2`
and stays rejected. The archive's basis is now `stated_for_dataset`: the
current-year part of the NLOD 2.0 dataset "Norsk Lovtidend, Avdeling I". The gate
accepts it whatever R2 says, only for that file, and only for members cited from
its own Avdeling I year.
"""

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.canonical import parse_records
from lovspor.crosswalk.contract import schema_problems
from lovspor.crosswalk.manifest import ATTRIBUTION, BLOCKERS, SOURCE_TERMS
from lovspor.crosswalk.release import Approval, accepted_bases, ineligibility
from lovspor.crosswalk.rights import licence_basis, within_dataset
from tests.unit.crosswalk_support import PRODUCER, archive_pin, attested, make_capture

OLD = "lovtidend-avd1-2001-2025.tar.bz2"
CURRENT = "lovtidend-avd1-2026.tar.bz2"
NOT_ACCEPTED = "rests on an archive whose licence basis is not accepted"
OUTSIDE = "cites a member outside its archive's dataset"


@pytest.fixture
def record(tmp_path: Path, httpx_mock: HTTPXMock) -> dict[str, Any]:
    built = build(make_capture(tmp_path, httpx_mock), BuildOptions(producer_commit=PRODUCER))
    return next(r for r in parse_records(built) if r["amending_lov_id"] == "LOV-2022-06-17-63")


def _approval(r2: str) -> Approval:
    return Approval.model_validate(
        {
            "input_sha256": sha256(b"").hexdigest(),
            "approved_by": "synthetic test approval",
            "approved_on": "2026-11-07",
            "R1": "approved",
            "R2": r2,
        }
    )


def _on_current(record: dict[str, Any], year: str = "2026") -> dict[str, Any]:
    """The record with its chain cited from the 2026 archive, member year as given."""
    text = json.dumps(record).replace(OLD, CURRENT).replace("lti/2022/", f"lti/{year}/")
    moved = json.loads(text)
    moved["source_pins"] = record["source_pins"]
    return moved


@pytest.mark.parametrize(
    ("filename", "basis"),
    [
        (OLD, "stated_for_file"),
        (CURRENT, "stated_for_dataset"),
        ("lovtidend-avd1-2027.tar.bz2", "inferred_same_dataset"),
        ("lovtidend-avd2-2026.tar.bz2", "inferred_same_dataset"),
        ("lovtidend-avd2-2001-2025.tar.bz2", "inferred_same_dataset"),
        ("gjeldende-lover.tar.bz2", "inferred_same_dataset"),
    ],
)
def test_only_the_current_avd1_part_gets_the_dataset_basis(filename: str, basis: str) -> None:
    assert licence_basis(filename) == basis


@pytest.mark.parametrize("r2", ["approved", "rejected"])
def test_the_dataset_basis_does_not_depend_on_r2(r2: str) -> None:
    bases = accepted_bases(_approval(r2))
    assert "stated_for_dataset" in bases
    assert ("stated_for_endpoint" in bases) is (r2 == "approved")
    assert "inferred_same_dataset" not in bases


def test_a_chain_on_the_2026_archive_passes_rights_under_rejected_r2(
    record: dict[str, Any],
) -> None:
    moved = _on_current(record)
    assert any(e["upstream_id"].startswith(f"{CURRENT}!lti/2026/") for e in moved["evidence"])
    assert ineligibility(moved, accepted_bases(_approval("rejected"))) is None


def test_the_endpoint_basis_stays_rejected(record: dict[str, Any]) -> None:
    moved = _on_current(record)
    moved["source_pins"] = {
        **record["source_pins"],
        "lovdata_archives": [
            {**p, "licence_basis": "stated_for_endpoint"} if CURRENT in p["source_url"] else p
            for p in record["source_pins"]["lovdata_archives"]
        ],
    }
    assert ineligibility(moved, accepted_bases(_approval("rejected"))) == NOT_ACCEPTED
    assert ineligibility(moved, accepted_bases(_approval("approved"))) is None


@pytest.mark.parametrize("year", ["2025", "2027"])
def test_a_member_outside_the_avd1_year_is_refused(record: dict[str, Any], year: str) -> None:
    moved = _on_current(record, year)
    assert ineligibility(moved, accepted_bases(_approval("approved"))) == OUTSIDE


@pytest.mark.parametrize(
    ("archive", "member", "inside"),
    [
        (CURRENT, "lti/2026/nl-20260123-001.xml", True),
        (CURRENT, "lti/2025/nl-20251230-001.xml", False),
        (CURRENT, "ltii/2026/nl-20260123-001.xml", False),
        (CURRENT, "x/lti/2026/nl.xml", False),
        (OLD, "lti/2009/nl-20090619-107.xml", True),
    ],
)
def test_within_dataset(archive: str, member: str, inside: bool) -> None:
    assert within_dataset(archive, member) is inside


def test_a_release_candidate_may_pin_the_dataset_basis() -> None:
    candidate = attested()
    candidate["release_state"] = "release_candidate"
    candidate["source_pins"]["lovdata_archives"].append(
        archive_pin(CURRENT, basis="stated_for_dataset")
    )
    assert schema_problems(candidate) == []


def test_the_attribution_names_lovdata_the_dataset_the_licence_and_the_changes() -> None:
    (lovdata,) = ATTRIBUTION["sources"]
    assert lovdata == {
        "source": "Lovdata",
        "licensor": "Stiftelsen Lovdata",
        "dataset": "Norsk Lovtidend, Avdeling I",
        "dataset_url": "https://data.norge.no/datasets/c0c6a87c-f597-3735-965f-650be23426a0",
        "source_url": "https://api.lovdata.no/v1/publicData/",
        "licence": "NLOD-2.0",
        "licence_url": "https://data.norge.no/nlod/no/2.0",
    }
    nb, en = ATTRIBUTION["nb"], ATTRIBUTION["en"]
    assert nb.startswith("Kilde: Lovdata.") and en.startswith("Source: Lovdata.")
    for text in (nb, en):
        assert "Norsk Lovtidend, Avdeling I" in text
        assert "Stiftelsen Lovdata" in text
    assert "https://data.norge.no/nlod/no/2.0" in nb
    assert "endret av Lovspor" in nb and "Changed by Lovspor" in en
    assert "har ikke godkjent eller anbefalt Lovspor" in nb
    assert "have not approved or endorsed Lovspor" in en
    assert "stay under NLOD 2.0" in ATTRIBUTION["licence_of_source_data"]
    assert "status" not in ATTRIBUTION


def test_r3_is_no_longer_a_blocker_but_r4_is() -> None:
    assert [b.split(":")[0] for b in BLOCKERS if b.startswith("R")] == ["R4"]


def test_source_terms_record_what_was_read_and_when() -> None:
    lovdata = [t for t in SOURCE_TERMS if t["source"].startswith("Stiftelsen Lovdata")]
    assert {t["terms_url"] for t in lovdata} == {
        "https://api.lovdata.no/om-api-tjenesten/",
        "https://data.norge.no/datasets/c0c6a87c-f597-3735-965f-650be23426a0",
        "https://lovdata.no/info/vilkar",
    }
    assert {t["read_on"] for t in lovdata} == {"2026-11-07"}
