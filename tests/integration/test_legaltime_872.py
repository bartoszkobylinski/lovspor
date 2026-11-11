"""#872: date-only canonical citations bind only on the header's exact proof, on the archives.

Runs only where the pinned publicData archives sit in the gitignored cache (see
test_legaltime_pilot.py); a skip means the evidence was not replayed here, not a pass.
Raw Lovdata XML never enters the repository.

The members are every amender of the eight coverage acts that bound an operation to a date-only
refid at main ``7031b041``: 128 operations, 77 of them in members whose header lists that exact
refid, 50 where it lists no act of that date, 1 where it lists the numbered act of that date.
"""

import re
from collections import Counter
from pathlib import Path

import pytest

from lovspor.legaltime.archive import file_sha256, read_members
from lovspor.legaltime.instructions import interpret

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/legaltime"
ARCHIVES = [CACHE / "lovtidend-avd1-2001-2025.tar.bz2", CACHE / "lovtidend-avd1-2026.tar.bz2"]
PINS = {
    "lovtidend-avd1-2001-2025.tar.bz2": (
        "d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0"
    ),
    "lovtidend-avd1-2026.tar.bz2": (
        "600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2"
    ),
}
DATE_ONLY = re.compile(r"lov/\d{4}-\d{2}-\d{2}")
UNPROVEN = {  # header lists no act of the date, or (2021-05-07-33) only its numbered act
    "lti/2007/nl-20070126-003.xml": 36,
    "lti/2009/nl-20090619-074.xml": 14,
    "lti/2021/nl-20210507-033.xml": 1,
}
PROVEN = {  # member → {date-only refid its header lists: operations bound to it}
    "lti/2010/nl-20101210-076.xml": {"lov/1967-02-10": 2},
    "lti/2013/nl-20130405-012.xml": {"lov/1930-02-21": 11},
    "lti/2013/nl-20130621-100.xml": {
        "lov/1961-05-05": 12, "lov/1967-02-10": 1, "lov/1968-11-29": 7,
    },
    "lti/2014/nl-20140509-016.xml": {"lov/1930-02-21": 1},
    "lti/2015/nl-20150619-065.xml": {
        "lov/1751-10-02": 1, "lov/1916-03-17": 4, "lov/1961-02-03": 1, "lov/1963-11-15": 6,
        "lov/1967-02-10": 2,
    },
    "lti/2016/nl-20160422-003.xml": {"lov/1930-02-21": 1},
    "lti/2017/nl-20170511-026.xml": {"lov/1930-02-21": 3},
    "lti/2017/nl-20170616-063.xml": {"lov/1967-02-10": 1},
    "lti/2018/nl-20180420-006.xml": {"lov/1930-02-21": 1},
    "lti/2018/nl-20180601-025.xml": {"lov/1963-11-15": 6},
    "lti/2018/nl-20180615-037.xml": {"lov/1930-02-21": 4},
    "lti/2018/nl-20180622-083.xml": {"lov/1967-02-10": 2},
    "lti/2020/nl-20201218-149.xml": {"lov/1967-02-10": 1},
    "lti/2021/nl-20210507-034.xml": {"lov/1967-02-10": 1},
    "lti/2021/nl-20210618-127.xml": {"lov/1967-02-10": 3},
    "lti/2024/nl-20240621-046.xml": {"lov/1967-02-10": 1},
    "lti/2025/nl-20250425-012.xml": {"lov/1963-11-15": 1, "lov/1967-02-10": 1},
    "lti/2026/nl-20260619-045.xml": {"lov/1930-03-14": 2, "lov/1961-05-05": 1},
}  # fmt: skip

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in ARCHIVES), reason="no local Lovdata archives"
)


@pytest.fixture(autouse=True)
def _pinned() -> None:
    if not all(file_sha256(CACHE / name) == digest for name, digest in PINS.items()):
        pytest.skip("local archives are not the pinned ones")


@pytest.fixture(scope="module")
def documents() -> dict:
    members = [*UNPROVEN, *PROVEN]
    read = read_members(ARCHIVES[0], [m for m in members if not m.startswith("lti/2026/")])
    read.update(read_members(ARCHIVES[1], [m for m in members if m.startswith("lti/2026/")]))
    return read


def _date_only(document) -> Counter:
    return Counter(
        op.act for op in interpret(document).operations if op.act and DATE_ONLY.fullmatch(op.act)
    )


def test_every_header_proven_date_only_operation_keeps_its_act(documents: dict) -> None:
    for member, expected in PROVEN.items():
        document = documents[member]
        assert set(expected) <= set(document.header.changes_to_documents), member
        assert _date_only(document) == Counter(expected), member
    assert sum(sum(counts.values()) for counts in PROVEN.values()) == 77


def test_no_unproven_date_only_citation_binds_anything(documents: dict) -> None:
    for member in UNPROVEN:
        assert _date_only(documents[member]) == Counter(), member
    assert sum(UNPROVEN.values()) == 51


def test_unproven_date_only_blocks_of_2007_01_26_3_end_where_numbered_blocks_begin(
    documents: dict,
) -> None:
    """Items 2 (1898) and 14 (1930 skifte) lose their act; the numbered neighbours keep theirs."""
    document = documents["lti/2007/nl-20070126-003.xml"]
    acts = {op.id.split("#")[1]: op.act for op in interpret(document).operations}
    assert [acts[f"0::{n}.0"] for n in (32, 37, 61, 64, 398, 427, 462, 465)] == [
        "lov/1893-07-20-2",
        None,  # first operation under ``2. I lov 28. november 1898 om Umyndiggjørelse …``
        None,
        "lov/1902-05-22-10",
        "lov/1927-05-05-1",
        None,  # first operation under ``14. I lov 21. februar 1930 om skifte …``
        None,
        "lov/1931-06-12-1",
    ]


def test_straffeloven_cited_with_nr_and_no_full_stop_stays_unattributed(documents: dict) -> None:
    """lov/2021-05-07-33: ``I lov 20. mai 2005 nr 28 om straff …``; the header lists -28."""
    document = documents["lti/2021/nl-20210507-033.xml"]
    assert "lov/2005-05-20-28" in document.header.changes_to_documents
    operations = [op for op in interpret(document).operations if op.part == "II"]
    assert [(op.instruction, op.act) for op in operations] == [
        ("§ 189 første ledd bokstav d skal lyde:", None)
    ]
