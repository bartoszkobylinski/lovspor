"""Typed failures of the legislative crosswalk producer (ADR-0017, #610)."""

from collections.abc import Sequence

from lovspor.errors import LovsporError


class CrosswalkError(LovsporError):
    """Base of every crosswalk refusal: a gate that cannot pass, never a warning."""


class CrosswalkContractError(CrosswalkError):
    """One or more records break the 1.0.0-rc schema or a producer invariant.

    Every problem is kept, so one run names all of them instead of the first.
    """

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__(f"{len(self.problems)} crosswalk contract problem(s): {self.problems[0]}")
