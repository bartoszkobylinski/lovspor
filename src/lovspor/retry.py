"""Small retry helper with exponential backoff.

Intentionally not a wrapper around an external library: only one retry
site exists in lovspor (HTTP downloads from Lovdata), so a tiny function
is preferable to taking a dependency.
"""

import time
from collections.abc import Callable
from typing import TypeVar

# PEP 695 (`def f[T]`) would be cleaner, but mutmut 2.x cannot parse it and
# crashes during mutation generation. We pin mutmut 2.5.1 because 3.x has
# open bugs with editable installs and dataclasses. Keep pre-PEP 695 syntax
# until mutmut 3.x stabilizes.
T = TypeVar("T")


def retry_with_backoff(
    func: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 1.0,
    backoff_factor: float = 2.0,
    retryable: tuple[type[Exception], ...] = (Exception,),
) -> T:
    """Call ``func``; retry on ``retryable`` with exponential backoff.

    Delay grows as ``base_delay_seconds * (backoff_factor ** attempt)``.
    The final attempt is uncaught: its exception bubbles directly.

    Raises:
        ValueError: if ``attempts < 1``.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for attempt in range(attempts - 1):
        try:
            return func()
        except retryable:
            time.sleep(base_delay_seconds * (backoff_factor**attempt))
    return func()
