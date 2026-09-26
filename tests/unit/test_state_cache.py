"""Tests for lovspor.state_cache — the byte-budgeted historical state cache (#223)."""

import pytest

from lovspor.errors import ConfigError
from lovspor.state_cache import (
    DEFAULT_BUDGET_MIB,
    ByteBudgetCache,
    budget_bytes_from_env,
    text_bytes,
)

_MIB = 1024 * 1024


def test_put_and_get_within_budget() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)

    assert cache.put("a", "A", 40) is True
    assert cache.put("b", "B", 40) is True

    assert cache.get("a") == "A"
    assert cache.get("b") == "B"
    assert cache.total_bytes == 80


def test_missing_key_reads_none() -> None:
    assert ByteBudgetCache[str](100).get("absent") is None


def test_put_over_budget_evicts_least_recently_used_whole_entries() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)
    cache.put("a", "A", 40)
    cache.put("b", "B", 40)
    cache.get("a")  # b is now least recently used

    cache.put("c", "C", 40)

    assert cache.get("b") is None
    assert cache.get("a") == "A"
    assert cache.get("c") == "C"
    assert cache.total_bytes == 80


def test_eviction_stops_as_soon_as_the_budget_holds() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)
    for key in "abcd":
        cache.put(key, key.upper(), 25)

    cache.put("e", "E", 25)

    assert [cache.get(key) for key in "abcde"] == [None, "B", "C", "D", "E"]


def test_an_entry_exactly_at_the_budget_is_kept() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)

    assert cache.put("a", "A", 100) is True
    assert cache.get("a") == "A"


def test_an_entry_larger_than_the_budget_is_refused_and_evicts_nothing() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)
    cache.put("a", "A", 60)

    assert cache.put("big", "BIG", 101) is False

    assert cache.get("big") is None
    assert cache.get("a") == "A"


def test_resize_grows_an_entry_and_evicts_others_first() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)
    cache.put("a", "A", 10)
    cache.put("b", "B", 10)

    assert cache.resize("b", 95) is True

    assert cache.get("a") is None
    assert cache.get("b") == "B"
    assert cache.total_bytes == 95


def test_resize_accepts_an_entry_exactly_at_the_budget() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)
    cache.put("a", "A", 40)

    assert cache.resize("a", 100) is True

    assert cache.get("a") == "A"
    assert cache.total_bytes == 100


def test_eviction_removes_as_many_old_entries_as_needed() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)
    cache.put("a", "A", 40)
    cache.put("b", "B", 40)

    assert cache.put("c", "C", 90) is True

    assert cache.get("a") is None
    assert cache.get("b") is None
    assert cache.get("c") == "C"
    assert cache.total_bytes == 90


def test_resize_is_absolute_so_repeating_it_does_not_double_charge() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)
    cache.put("a", "A", 10)
    cache.put("b", "B", 10)

    cache.resize("b", 60)
    cache.resize("b", 60)

    assert cache.get("a") == "A"
    assert cache.total_bytes == 70


def test_resize_past_the_budget_is_refused_and_leaves_the_cache_alone() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)
    cache.put("a", "A", 10)
    cache.put("b", "B", 10)

    assert cache.resize("b", 101) is False

    assert cache.get("a") == "A"
    assert cache.get("b") == "B"
    assert cache.total_bytes == 20


def test_resize_of_an_evicted_entry_is_refused() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)

    assert cache.resize("gone", 10) is False
    assert cache.total_bytes == 0


def test_put_replaces_an_existing_key_without_double_charging() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)
    cache.put("a", "A", 50)

    cache.put("a", "A2", 50)

    assert cache.get("a") == "A2"
    assert cache.total_bytes == 50


def test_clear_empties_the_cache() -> None:
    cache: ByteBudgetCache[str] = ByteBudgetCache(100)
    cache.put("a", "A", 50)

    cache.clear()

    assert cache.get("a") is None
    assert cache.total_bytes == 0


def test_text_bytes_counts_what_python_holds_not_the_utf8_length() -> None:
    # A non-Latin-1 character widens every character of the string to two
    # bytes; the budget must see what is resident, not the encoded size.
    narrow = text_bytes({"a": "x" * 1000})
    wide = text_bytes({"a": chr(0x2013) * 1000})  # EN DASH

    assert 1000 < narrow < 1100
    assert 2000 < wide < 2100
    assert text_bytes({}) == 0


def test_budget_default_when_unset_or_empty() -> None:
    assert budget_bytes_from_env({}) == DEFAULT_BUDGET_MIB * _MIB
    assert budget_bytes_from_env({"LOVSPOR_HISTORICAL_CACHE_MIB": ""}) == (
        DEFAULT_BUDGET_MIB * _MIB
    )


def test_budget_from_env_is_read_in_mebibytes() -> None:
    assert budget_bytes_from_env({"LOVSPOR_HISTORICAL_CACHE_MIB": "512"}) == 512 * _MIB


def test_cache_from_env_uses_explicit_environment_mapping() -> None:
    cache = ByteBudgetCache[str].from_env({"LOVSPOR_HISTORICAL_CACHE_MIB": "3"})

    assert cache.budget_bytes == 3 * _MIB


@pytest.mark.parametrize("raw", ["0", "-1", "lots", "1.5"])
def test_budget_from_env_refuses_unusable_values(raw: str) -> None:
    with pytest.raises(ConfigError, match="LOVSPOR_HISTORICAL_CACHE_MIB"):
        budget_bytes_from_env({"LOVSPOR_HISTORICAL_CACHE_MIB": raw})


def test_budget_default_holds_one_production_state_but_not_two() -> None:
    # 2026-09-26, lovverk ddb130e71: one state's stripped bodies measured
    # 198 MiB (sys.getsizeof), its parsed manifest 14 MiB.
    one_state = 198 * _MIB + 14 * _MIB

    assert one_state <= DEFAULT_BUDGET_MIB * _MIB < 2 * one_state
