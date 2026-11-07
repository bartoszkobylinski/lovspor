"""Where the dataset-level basis stops (ADR-0021): the gate still fails closed around it.

The basis holds for the members of `lovtidend-avd1-2026.tar.bz2` under `lti/2026/`.
A member path that only spells that prefix — through `.`, `..` or an empty
segment — is not shown to lie there. A chain on a later year's archive, or one
carrying a correction, stays `research_only` whatever R2 says.
"""

import copy
import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.canonical import parse_records
from lovspor.crosswalk.release import Approval, accepted_bases, ineligibility
from lovspor.crosswalk.rights import licence_basis, within_dataset
from tests.unit.crosswalk_support import PRODUCER, archive_pin, make_capture

OLD = "lovtidend-avd1-2001-2025.tar.bz2"
CURRENT = "lovtidend-avd1-2026.tar.bz2"
LATER = "lovtidend-avd1-2027.tar.bz2"


@pytest.fixture
def record(tmp_path: Path, httpx_mock: HTTPXMock) -> dict[str, Any]:
    built = build(make_capture(tmp_path, httpx_mock), BuildOptions(producer_commit=PRODUCER))
    return next(r for r in parse_records(built) if r["amending_lov_id"] == "LOV-2022-06-17-63")


def _bases(r2: str) -> frozenset[str]:
    approval = Approval.model_validate(
        {
            "input_sha256": sha256(b"").hexdigest(),
            "approved_by": "synthetic test approval",
            "approved_on": "2026-11-07",
            "R1": "approved",
            "R2": r2,
        }
    )
    return accepted_bases(approval)


def _moved(record: dict[str, Any], archive: str, year: str) -> dict[str, Any]:
    """The record with its chain cited from `archive`, its member under `lti/<year>/`."""
    text = json.dumps(record).replace(OLD, archive).replace("lti/2022/", f"lti/{year}/")
    moved = json.loads(text)
    moved["source_pins"] = copy.deepcopy(record["source_pins"])
    return moved


@pytest.mark.parametrize(
    "member",
    [
        "lti/2026/../2025/nl-20251230-001.xml",
        "lti/2026/./nl-20260123-001.xml",
        "lti/2026//nl-20260123-001.xml",
        "lti/2026/nl/../../2025/nl.xml",
    ],
)
def test_a_member_that_only_spells_the_year_is_outside_the_dataset(member: str) -> None:
    assert within_dataset(CURRENT, member) is False


def test_a_normal_member_of_the_year_is_inside_the_dataset() -> None:
    assert within_dataset(CURRENT, "lti/2026/nl-20260123-001.xml") is True
    assert within_dataset(CURRENT, "lti/2026/sub/nl-20260123-001.xml") is True


def test_a_dotted_member_path_bars_the_record(record: dict[str, Any]) -> None:
    moved = _moved(record, CURRENT, "2026/../2025")
    assert ineligibility(moved, _bases("approved")) == (
        "cites a member outside its archive's dataset"
    )


@pytest.mark.parametrize("r2", ["approved", "rejected"])
def test_a_chain_on_a_later_year_stays_research_only(record: dict[str, Any], r2: str) -> None:
    moved = _moved(record, LATER, "2027")
    moved["source_pins"]["lovdata_archives"].append(archive_pin(LATER, licence_basis(LATER)))
    assert ineligibility(moved, _bases(r2)) == (
        "rests on an archive whose licence basis is not accepted"
    )


def test_a_correction_on_the_dataset_basis_stays_research_only(record: dict[str, Any]) -> None:
    moved = _moved(record, CURRENT, "2026")
    assert ineligibility(moved, _bases("rejected")) is None
    moved["evidence"].append({**moved["evidence"][0], "role": "correction"})
    assert ineligibility(moved, _bases("approved")) == "carries a correction (R4 is blocked)"
