"""Temporary CI acceptance drill for the mutation remediation workflow.

This module exists only on the drill PR branch and is never merged. The
arithmetic below is intentionally shaped so mutmut produces one killable
mutant (``0`` -> ``1``) and one equivalent mutant (``value + 0`` ->
``value - 0``), so the remediation workflow's classification and BLOCKED
path can be exercised end to end (acceptance scenarios 3/4).
"""


def drill_offset(value: int) -> int:
    """Return ``value`` unchanged via an identity mutmut can mutate."""
    return value + 0
