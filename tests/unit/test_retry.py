import pytest

from lovspor.retry import retry_with_backoff


class _Counter:
    def __init__(self) -> None:
        self.calls = 0


def test_succeeds_on_first_attempt() -> None:
    counter = _Counter()

    def func() -> int:
        counter.calls += 1
        return 42

    result = retry_with_backoff(func, base_delay_seconds=0)
    assert result == 42
    assert counter.calls == 1


def test_retries_then_succeeds() -> None:
    counter = _Counter()

    def func() -> str:
        counter.calls += 1
        if counter.calls < 3:
            raise ValueError("transient")
        return "ok"

    result = retry_with_backoff(func, attempts=3, base_delay_seconds=0)
    assert result == "ok"
    assert counter.calls == 3


def test_raises_after_exhausting_attempts() -> None:
    counter = _Counter()

    def func() -> int:
        counter.calls += 1
        raise ValueError("persistent")

    with pytest.raises(ValueError, match="persistent"):
        retry_with_backoff(func, attempts=3, base_delay_seconds=0)
    assert counter.calls == 3


def test_does_not_retry_on_non_retryable() -> None:
    counter = _Counter()

    def func() -> int:
        counter.calls += 1
        raise TypeError("not retryable")

    with pytest.raises(TypeError):
        retry_with_backoff(
            func,
            attempts=3,
            base_delay_seconds=0,
            retryable=(ValueError,),
        )
    assert counter.calls == 1


def test_attempts_zero_raises_value_error() -> None:
    def func() -> int:
        return 1

    with pytest.raises(ValueError, match="attempts must be"):
        retry_with_backoff(func, attempts=0, base_delay_seconds=0)


def test_attempts_error_message_is_exact() -> None:
    with pytest.raises(ValueError) as exc_info:
        retry_with_backoff(lambda: 1, attempts=0, base_delay_seconds=0)

    assert str(exc_info.value) == "attempts must be >= 1"


def test_backoff_delays_follow_exponential_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("lovspor.retry.time.sleep", sleeps.append)

    counter = _Counter()

    def func() -> int:
        counter.calls += 1
        if counter.calls < 4:
            raise ValueError
        return 1

    retry_with_backoff(
        func,
        attempts=4,
        base_delay_seconds=1.0,
        backoff_factor=2.0,
    )
    assert sleeps == [1.0, 2.0, 4.0]


def test_default_attempts_is_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling with no args should retry exactly 3 times before raising."""
    monkeypatch.setattr("lovspor.retry.time.sleep", lambda _seconds: None)
    counter = _Counter()

    def func() -> int:
        counter.calls += 1
        raise ValueError

    with pytest.raises(ValueError):
        retry_with_backoff(func)
    assert counter.calls == 3


def test_default_delay_schedule_is_one_then_two_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defaults are base_delay_seconds=1.0 and backoff_factor=2.0."""
    sleeps: list[float] = []
    monkeypatch.setattr("lovspor.retry.time.sleep", sleeps.append)

    def func() -> int:
        raise ValueError

    with pytest.raises(ValueError):
        retry_with_backoff(func)
    assert sleeps == [1.0, 2.0]


def test_only_attempt_is_uncaught_when_attempts_is_one() -> None:
    counter = _Counter()

    def func() -> int:
        counter.calls += 1
        raise ValueError("first try")

    with pytest.raises(ValueError, match="first try"):
        retry_with_backoff(func, attempts=1, base_delay_seconds=0)
    assert counter.calls == 1
