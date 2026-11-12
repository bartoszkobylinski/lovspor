"""#893 on the pinned archives: nested items are dated by their host provision, never by the
outer law's own "straks".

Runs only where the pinned publicData archives sit in the gitignored cache; a skip means the
evidence was not replayed on this machine, not a pass.

* ``lov/2013-04-05-12`` part I replaces vergemålsloven § 102 (host list "Fra den tid loven trer
  i kraft gjøres følgende endringer i andre lover:"). Item 50 amends straffeloven § 261 første
  ledd (op ``#1:I:253.0``), item 51 amends tvisteloven §§ 2-2, 2-3, 2-4. Replay-6 dated them
  2013-04-05 from part III "Loven trer i kraft straks.". Vergemålsloven commenced 2013-07-01 by
  ``forskrift/2013-04-05-338`` ("Delt ikraftsetting … Loven trer i kraft 1. juli 2013 med unntak
  av § 51 annet ledd, § 54 tredje ledd, § 65 og § 66 som trer i kraft straks."), a partial
  decision the grammar does not read: so the host date is not proven and the items are
  ``unresolved``. 2013-07-01 would be accepted only with that proof; 2013-04-05 never.
* ``lov/2020-12-18-149`` part I replaces arveloven § 181 with the same host list; item 31 amends
  tvisteloven § 6-2 (op ``#1:I:183.0``). Arveloven commenced 2021-01-01 by
  ``forskrift/2020-02-28-200`` ("Lova gjeld frå 1. januar 2021."), a full decision: the item is
  ``dated`` 2021-01-01, not 2020-12-18.
"""

from datetime import date
from pathlib import Path

import pytest

from lovspor.legaltime.archive import collect, file_sha256
from lovspor.legaltime.commencement import Timing
from lovspor.legaltime.run import events_for

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/legaltime"
ARCHIVES = [CACHE / "lovtidend-avd1-2001-2025.tar.bz2", CACHE / "lovtidend-avd1-2026.tar.bz2"]
PIN = "d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0"
STRAFFELOVEN = "lov/2005-05-20-28"
TVISTELOVEN = "lov/2005-06-17-90"

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in ARCHIVES), reason="no local Lovdata archives"
)


@pytest.fixture(scope="module")
def tvisteloven() -> dict[str, Timing]:
    if file_sha256(ARCHIVES[0]) != PIN:
        pytest.skip("local archives are not the pinned ones")
    return {e.operation.id: e.timing for e in events_for(collect(TVISTELOVEN, ARCHIVES))}


def test_straffeloven_261_item_is_never_dated_by_the_outer_laws_straks() -> None:
    if file_sha256(ARCHIVES[0]) != PIN:
        pytest.skip("local archives are not the pinned ones")
    events = {e.operation.id: e.timing for e in events_for(collect(STRAFFELOVEN, ARCHIVES))}
    timing = events["lov/2013-04-05-12#1:I:253.0"]
    assert timing.effective != date(2013, 4, 5)
    assert timing.status == "unresolved"
    assert timing.reason.startswith("nested amendment: host provision's commencement not proven")
    assert "partial decision outside the grammar" in timing.reason
    assert "forskrift/2013-04-05-338" in {p.refid for p in timing.proofs}


@pytest.mark.parametrize("op", ["#1:I:256.0", "#1:I:259.0", "#1:I:261.0"])
def test_tvisteloven_items_in_the_vergemaal_host_are_unresolved(
    tvisteloven: dict[str, Timing], op: str
) -> None:
    timing = tvisteloven[f"lov/2013-04-05-12{op}"]
    assert (timing.status, timing.effective) == ("unresolved", None)


def test_tvisteloven_item_in_the_arveloven_host_is_dated_by_arvelovens_commencement(
    tvisteloven: dict[str, Timing],
) -> None:
    timing = tvisteloven["lov/2020-12-18-149#1:I:183.0"]
    assert (timing.status, timing.effective) == ("dated", date(2021, 1, 1))
    assert timing.known_from == date(2020, 12, 18)  # the later of the two publications
    refids = [p.refid for p in timing.proofs]
    assert refids == ["lov/2020-12-18-149", "forskrift/2020-02-28-200"]
