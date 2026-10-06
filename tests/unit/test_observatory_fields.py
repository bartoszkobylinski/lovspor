"""Tests for lovspor.observatory.fields — "somebody filled this in" (#207)."""

import pytest
from pydantic import BaseModel, ValidationError

from lovspor.observatory.fields import (
    BareHostStr,
    NonBlankStr,
    TrimmedNonBlankStr,
    require_bare_host,
)

BLANKS = ["", " ", "\t", "\n", " \t ", "\u00a0", "\u2003"]


class _Kept(BaseModel):
    value: NonBlankStr
    optional: NonBlankStr | None = None


class _Trimmed(BaseModel):
    value: TrimmedNonBlankStr


class TestNonBlankStr:
    @pytest.mark.parametrize("blank", BLANKS)
    def test_a_value_with_no_content_is_refused(self, blank: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _Kept(value=blank)

        if blank:
            assert exc_info.value.errors()[0]["msg"] == "Value error, must not be blank"

    @pytest.mark.parametrize("blank", BLANKS)
    def test_an_optional_field_refuses_blank_but_accepts_none(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            _Kept(value="x", optional=blank)

        assert _Kept(value="x").optional is None

    def test_the_value_is_stored_exactly_as_given(self) -> None:
        """Records of this type are read back from files that are
        fingerprinted and rewritten; a read that quietly edits a value
        would make the in-memory record disagree with the bytes on disk."""
        assert _Kept(value="  project owner ").value == "  project owner "


class TestTrimmedNonBlankStr:
    @pytest.mark.parametrize("blank", BLANKS)
    def test_a_value_with_no_content_is_refused(self, blank: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _Trimmed(value=blank)

        if blank:
            assert exc_info.value.errors()[0]["msg"] == "Value error, must not be blank"

    def test_surrounding_whitespace_is_taken_off(self) -> None:
        assert _Trimmed(value="  Bartosz Kobyliński\n").value == "Bartosz Kobyliński"

    def test_inner_whitespace_is_kept(self) -> None:
        assert _Trimmed(value=" a  b ").value == "a  b"


class _Host(BaseModel):
    value: BareHostStr


class TestBareHostStr:
    """Issue #557: one host check for the register and the survey."""

    @pytest.mark.parametrize(
        "not_a_host",
        [
            "kommune.no/path",
            "kommune.no?x",
            "kommune.no#y",
            "kommune.no@evil.example",
            "kommune.no:443",
            "kom mune.no",
            "kommune.no\t",
            "\N{NO-BREAK SPACE}kommune.no",
        ],
    )
    def test_anything_but_a_bare_host_is_refused(self, not_a_host: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _Host(value=not_a_host)

        assert exc_info.value.errors()[0]["msg"] == (
            f"Value error, a host without scheme or path was expected, got: {not_a_host!r}"
        )

    @pytest.mark.parametrize("blank", BLANKS)
    def test_a_blank_host_is_refused(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            _Host(value=blank)

    def test_a_bare_host_is_kept_as_given(self) -> None:
        assert _Host(value="Baerum.Kommune.No.").value == "Baerum.Kommune.No."

    def test_the_plain_check_returns_the_host(self) -> None:
        assert require_bare_host("kommune.no") == "kommune.no"

    def test_the_plain_check_refuses_with_the_same_words(self) -> None:
        with pytest.raises(ValueError, match="a host without scheme or path was expected"):
            require_bare_host("kommune.no@evil.example")

    def test_the_plain_check_refuses_an_empty_host(self) -> None:
        """The survey calls the check without NonBlankStr in front of it, so
        ``--domain ""`` was probed as a host (Codex test on PR #560)."""
        with pytest.raises(ValueError, match="a host without scheme or path was expected"):
            require_bare_host("")
