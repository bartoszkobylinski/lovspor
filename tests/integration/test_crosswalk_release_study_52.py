"""The release-candidate gate over the real 52-act capture (#610, P4, rulings R1, R2).

Runs only where `scripts/study_52.py capture` left its cache; a skip is not a
pass. The approval here is SYNTHETIC test data: no owner ruling is recorded.
"""

import json
from hashlib import sha256
from pathlib import Path

import pytest

from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.canonical import parse_records
from lovspor.crosswalk.check import check
from lovspor.crosswalk.release import Approval, promote

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
    return build(CACHE, options)


def _approval(data: bytes, r2: str) -> Approval:
    return Approval.model_validate(
        {
            "input_sha256": sha256(data).hexdigest(),
            "approved_by": "synthetic test approval",
            "approved_on": "2026-11-03",
            "R1": "approved",
            "R2": r2,
        }
    )


def _candidates(data: bytes) -> set[str]:
    return {
        r["amending_lov_id"] for r in parse_records(data) if r["release_state"] != "research_only"
    }


def test_a_fresh_build_is_reproduced_from_the_capture(built: bytes) -> None:
    report = check(built, cache=CACHE)
    assert report.passed, report.problems[:5]


def test_r2_decides_the_2026_archive_records(built: bytes) -> None:
    rejected, counts = promote(built, _approval(built, "rejected"), CACHE)
    approved, _ = promote(built, _approval(built, "approved"), CACHE)
    on_2026 = {law for law in STUDY["inputs"]["mentions"] if law.startswith("LOV-2026-")}
    attested = {
        law["amending_lov_id"] for law in STUDY["laws"] if law["chain"]["status"] == "attested"
    }
    assert _candidates(approved) == attested
    assert _candidates(rejected) == attested - on_2026
    assert counts["promoted"] == len(attested - on_2026)
    assert "LOV-2009-06-19-107" not in _candidates(approved)
