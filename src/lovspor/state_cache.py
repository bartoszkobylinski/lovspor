"""A byte-budgeted LRU for resolved historical corpus states (issue #223).

One historical ``search_body`` keeps its state's whole stripped body set
resident: ~198 MiB of ``str`` on the production corpus (lovverk
``ddb130e71``, measured 2026-09-26), plus ~14 MiB of parsed manifest. A
fixed count of four such states reached ~2.3 GiB resident on top of the
live indexes, past the hosted unit's ``MemoryMax=1700M``. What bounds
memory is bytes, so the cache is bounded in bytes.

Whole entries are evicted, least recently used first: a state is useful
only whole, and half a body set would serve a wrong answer. An entry
larger than the whole budget is refused rather than admitted: the caller
still serves it for the one call that asked and lets it go, so an
oversized state costs one call's transient memory, never a resident one.
Refusing to serve it instead would turn an operator's small budget into
a disabled tool, which is not what a memory setting should mean.
"""

from __future__ import annotations

import sys
import threading
from collections import OrderedDict
from collections.abc import Mapping
from typing import Self

from lovspor.access import int_setting_from_env

BUDGET_VARIABLE = "LOVSPOR_HISTORICAL_CACHE_MIB"

DEFAULT_BUDGET_MIB = 256
"""Room for one production state (~212 MiB), not two.

The hosted unit sits at ~847 MB steady with the live indexes warm
(runbook, droplet) under ``MemoryMax=1700M``. A cold historical search
adds the state being built (~212 MiB) and ``git archive``'s own peak
(~169 MiB once narrowed to ``*.md``), both in the same cgroup: with one
cached state that is ~1.5 GB, with two it is past the limit. Revisiting
one date stays fast; a date scan pays a reload per date instead of
memory."""

STATE_OVERHEAD_BYTES = 16 * 1024 * 1024
"""Charged to every cached state before any search: its parsed manifest
(~14 MiB measured) and small per-document caches. Without a floor a
client walking dates with cheap primitives would pin one manifest per
date with nothing charged for it."""

_MIB = 1024 * 1024


def budget_bytes_from_env(environ: Mapping[str, str] | None = None) -> int:
    """The cache budget in bytes, from ``LOVSPOR_HISTORICAL_CACHE_MIB``.

    Fails closed like every deployment integer: a present value that is not
    a positive integer is a startup refusal, never a silent default.
    """
    return int_setting_from_env(BUDGET_VARIABLE, DEFAULT_BUDGET_MIB, environ) * _MIB


def state_bytes(search_bodies: Mapping[str, str]) -> int:
    """What a state costs once its search bodies are kept: the overhead
    every cached state carries plus the bodies themselves."""
    return STATE_OVERHEAD_BYTES + text_bytes(search_bodies)


def text_bytes(texts: Mapping[str, str]) -> int:
    """Resident size of the values: what Python holds, not the UTF-8 length.

    One character outside Latin-1 widens a whole ``str`` to two bytes per
    character, so the encoded length would undercount the production
    bodies by nearly half.
    """
    return sum(sys.getsizeof(text) for text in texts.values())


class ByteBudgetCache[V]:
    """Least-recently-used entries whose summed cost stays within a budget.

    Costs are declared by the caller, in bytes, and may be revised with
    :meth:`resize` once an entry grows. Thread-safe: the hosted server
    serves tool calls on worker threads.
    """

    def __init__(self, budget_bytes: int) -> None:
        self.budget_bytes = budget_bytes
        self._entries: OrderedDict[str, tuple[V, int]] = OrderedDict()
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        """Sized by ``LOVSPOR_HISTORICAL_CACHE_MIB`` (see :func:`budget_bytes_from_env`)."""
        return cls(budget_bytes_from_env(environ))

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return sum(cost for _value, cost in self._entries.values())

    def get(self, key: str) -> V | None:
        """The entry, now the most recently used; ``None`` when absent."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry[0]

    def put(self, key: str, value: V, cost: int) -> bool:
        """Admit ``value`` at ``cost``, evicting older entries to fit.

        ``False``, with nothing evicted, when ``cost`` alone exceeds the
        budget: the caller serves the value uncached.
        """
        with self._lock:
            if cost > self.budget_bytes:
                return False
            self._entries[key] = (value, cost)
            self._entries.move_to_end(key)
            self._evict_for(key)
            return True

    def resize(self, key: str, cost: int) -> bool:
        """Re-charge ``key`` at ``cost`` (absolute, so a repeat is harmless).

        ``False`` when the entry is gone or would alone exceed the budget;
        the cache is then left exactly as it was.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or cost > self.budget_bytes:
                return False
            self._entries[key] = (entry[0], cost)
            self._entries.move_to_end(key)
            self._evict_for(key)
            return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _evict_for(self, keep: str) -> None:
        """Drop least recently used entries other than ``keep`` until the
        budget holds. Callers hold the lock and have checked that ``keep``
        fits on its own, so the loop always ends."""
        total = sum(cost for _value, cost in self._entries.values())
        for key in list(self._entries):
            if total <= self.budget_bytes:
                return
            if key != keep:
                total -= self._entries.pop(key)[1]
