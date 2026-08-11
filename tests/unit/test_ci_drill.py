"""Tests for the temporary remediation-drill module (never merged)."""

from lovspor.ci_drill import drill_offset


def test_drill_offset_identity() -> None:
    assert drill_offset(5) == 5
    assert drill_offset(0) == 0
    assert drill_offset(-3) == -3
