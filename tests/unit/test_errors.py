import pytest

from lovspor.errors import (
    ConfigError,
    ExtractionError,
    LovsporError,
    NetworkError,
    ParseError,
)


def test_lovspor_error_is_exception() -> None:
    assert issubclass(LovsporError, Exception)


@pytest.mark.parametrize(
    "subclass",
    [NetworkError, ParseError, ExtractionError, ConfigError],
)
def test_subclasses_inherit_from_lovspor_error(
    subclass: type[Exception],
) -> None:
    assert issubclass(subclass, LovsporError)


@pytest.mark.parametrize(
    "subclass",
    [NetworkError, ParseError, ExtractionError, ConfigError],
)
def test_subclasses_caught_as_lovspor_error(
    subclass: type[LovsporError],
) -> None:
    with pytest.raises(LovsporError):
        raise subclass("test message")


def test_lovspor_error_carries_message() -> None:
    err = LovsporError("specific cause")
    assert "specific cause" in str(err)
