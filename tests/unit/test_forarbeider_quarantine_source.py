"""AR5 gate: a valid but foreign quarantine chain never governs a publication (#738; §9.9).

Two quarantine chains, Q1 and Q2, both canonical and anchored on the same
witness under the same trust root. Q1 is the publication's and quarantines a
document of the batch; Q2 is independently anchored and quarantines nothing.
A sealed plan, assessment and source taken from Q2 must never send.

No owner-signed or otherwise trusted configuration names the publication's
chain yet, so the binding waits for the owner's decision (#1037). The
reproduction is kept strict: it fails the suite the moment a fix lands.
"""

from pathlib import Path

import pytest

from lovspor.forarbeider.publication_execute import execute
from lovspor.forarbeider.publication_quarantine import WitnessedSource
from lovspor.forarbeider.quarantine_succession import QuarantineStore, initialize_quarantine
from tests.unit.forarbeider_execution_support import Rig, make_rig
from tests.unit.forarbeider_release_control_support import policy_bytes
from tests.unit.test_forarbeider_quarantine_integration import (
    environment_of,
    quarantine_in_the_batch,
    reasons_of,
    remote_unchanged,
)

FOREIGN_ID = "synthetic-quarantine-foreign"


@pytest.fixture(params=["A", "B"])
def sender(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / request.param.lower(), request.param)


def foreign_chain(chosen: Rig) -> WitnessedSource:
    """Q2: another chain, anchored empty on the same witness under the same root."""
    store = QuarantineStore(chosen.tmp / "foreign-quarantine", FOREIGN_ID)
    repository = chosen.inputs.settings.repository
    assert initialize_quarantine(store, repository, chosen.anchoring()).status == "verified"
    return WitnessedSource(store, (policy_bytes(),))


@pytest.mark.xfail(
    strict=True,
    reason="#1037: no trusted configuration names the publication's quarantine chain",
)
def test_a_foreign_chain_that_quarantines_nothing_never_sends(sender: Rig) -> None:
    quarantine_in_the_batch(sender)
    foreign = foreign_chain(sender)
    environment, github = environment_of(sender)

    outcome = execute(sender.request(quarantine=foreign), environment)

    assert outcome.status == "denied"
    assert reasons_of(outcome, "ar5_quarantine_anchor") != ()
    assert remote_unchanged(sender, github)
