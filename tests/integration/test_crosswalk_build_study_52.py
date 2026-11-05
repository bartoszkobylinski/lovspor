"""The producer's offline build over the real 52-act capture agrees with the study (#610).

Raw Lovdata bytes are never committed, so this runs only where
`scripts/study_52.py capture` left its cache. A skip here means the build was
NOT checked against real bytes on that machine; it is not a pass.
"""

import json
from pathlib import Path

import pytest

from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.canonical import is_canonical, parse_records
from lovspor.crosswalk.contract import validate_records

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/study-52"
STUDY = json.loads(
    (ROOT / "docs/research/data/study-52-2026-11-02.json").read_text(encoding="utf-8")
)
pytestmark = pytest.mark.skipif(
    not (CACHE / "capture-manifest.json").exists(), reason="no study-52 capture cache here"
)


@pytest.fixture(scope="module")
def built() -> bytes:
    options = BuildOptions(
        producer_commit="b" * 40,
        lovverk_commit="a" * 40,
        law_ids=tuple(STUDY["inputs"]["mentions"]),
    )
    first = build(CACHE, options)
    assert build(CACHE, options) == first
    return first


def test_every_study_act_is_kept_and_valid(built: bytes) -> None:
    records = parse_records(built)
    assert is_canonical(built)
    validate_records(records)
    assert sorted(r["amending_lov_id"] for r in records) == sorted(STUDY["inputs"]["mentions"])
    assert {r["release_state"] for r in records} == {"research_only"}


def test_each_record_agrees_with_the_study_label_free_chain(built: bytes) -> None:
    study = {law["amending_lov_id"]: law for law in STUDY["laws"]}
    for record in parse_records(built):
        law = study[record["amending_lov_id"]]
        assert record["status"] == law["chain"]["status"], record["amending_lov_id"]
        if record["status"] == "attested":
            assert [record["sak_id"]] == law["discovery"]["sak_ids"]


def test_the_besl_o_act_stays_unsupported(built: bytes) -> None:
    record = next(r for r in parse_records(built) if r["amending_lov_id"] == "LOV-2009-06-19-107")
    assert (record["status"], record["reason_code"]) == (
        "unsupported",
        "decision_kind_not_supported",
    )
