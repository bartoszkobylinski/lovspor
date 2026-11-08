"""Phase 3 verification contracts from the research note, §4 (#744)."""

from datetime import date

import pytest

from lovspor.legaltime.document import Ledd, Provision
from lovspor.legaltime.replay import ProvisionState, ReplayState
from lovspor.legaltime.verify import verify
from tests.unit.test_legaltime_baseline import ACT, _act


def _provision(key: str, count: int = 2) -> Provision:
    return Provision(
        key=key,
        label=key,
        heading="",
        ledd=tuple(Ledd(xml=str(i), text=str(i)) for i in range(count)),
    )


def _state(*provisions: ProvisionState, problems: tuple[str, ...] = ()) -> ReplayState:
    return ReplayState(
        act=ACT, at=date(2023, 1, 1), status="replayed", provisions=provisions,
        applied=(), future=(), pending=(), unresolved=(), act_level_problems=problems,
    )  # fmt: skip


@pytest.mark.parametrize(
    "in_force,effective,detail",
    [
        ("not_yet_in_force", date(2023, 7, 1), "not_yet_in_force; commencement 2023-07-01"),
        ("pending", None, "pending; commencement undated"),
    ],
)
def test_identical_text_without_legal_effect_is_not_proven(
    in_force: str, effective: date | None, detail: str
) -> None:
    provision = _provision("§1")
    mine = ProvisionState(
        **provision.model_dump(exclude={"notes", "chapter", "part"}),
        in_force=in_force,
        effective=effective,
    )
    oracle = _act().model_copy(update={"provisions": (provision,)})
    result = verify(_state(mine), oracle)
    assert result.counts == {"not_in_force": 1}
    assert result.sections[0].detail == detail
    assert result.ledd_counts == {"oracle_ledd": 2, "ledd_not_in_force": 2}
    assert result.counts_before_blanket == {}

    unresolved = verify(_state(mine.model_copy(update={"status": "unresolved"})), oracle)
    assert unresolved.counts == {"unresolved": 1}
    assert unresolved.ledd_counts == {"oracle_ledd": 2, "ledd_unresolved": 2}


def test_ledd_counts_partition_shared_oracle_units_and_respect_one_based_flags() -> None:
    shared = _provision("§1", 3)
    differing = _provision("§2")
    mine = ProvisionState(
        key=shared.key, label=shared.label, heading="", ledd=shared.ledd,
        ledd_not_in_force=(1, 3),
    )  # fmt: skip
    changed = ProvisionState(
        key="§2", label="§2", heading="", ledd=(Ledd(xml="different", text="different"),)
    )
    extra = ProvisionState(key="§3", label="§3", heading="", ledd=shared.ledd)
    oracle = _act().model_copy(update={"provisions": (shared, differing, _provision("§4", 4))})
    result = verify(_state(mine, changed, extra), oracle)
    assert result.counts == {
        "exact_raw": 1,
        "text_differs": 1,
        "only_in_replay": 1,
        "only_in_oracle": 1,
    }
    assert result.ledd_counts == {
        "oracle_ledd": 5,
        "ledd_proven": 1,
        "ledd_not_in_force": 2,
        "ledd_text_differs": 2,
    }
    assert sum(v for k, v in result.ledd_counts.items() if k != "oracle_ledd") == 5


def test_blanket_preserves_diagnostic_counts_but_proves_no_sections_or_ledd() -> None:
    provision = _provision("§1")
    mine = ProvisionState(key="§1", label="§1", heading="", ledd=provision.ledd)
    oracle = _act().model_copy(update={"provisions": (provision, _provision("§2"))})
    result = verify(_state(mine, problems=("unknown target",)), oracle)
    assert result.counts_before_blanket == {"exact_raw": 1, "only_in_oracle": 1}
    assert result.counts == {"unresolved": 2}
    assert all(s.verdict == "unresolved" and "unknown target" in s.detail for s in result.sections)
    assert result.ledd_counts == {"oracle_ledd": 2, "ledd_unresolved": 2}
    assert result.act_level_problems == ("unknown target",)
