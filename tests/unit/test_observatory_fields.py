"""Tests for lovspor.observatory.fields — "somebody filled this in" (#207)."""

import pytest
from pydantic import BaseModel, ValidationError

from lovspor.observatory.fields import NonBlankStr, TrimmedNonBlankStr

BLANKS = ["", " ", "\t", "\n", " \t "]


class _Kept(BaseModel):
    value: NonBlankStr
    optional: NonBlankStr | None = None


class _Trimmed(BaseModel):
    value: TrimmedNonBlankStr


class TestNonBlankStr:
    @pytest.mark.parametrize("blank", BLANKS)
    def test_a_value_with_no_content_is_refused(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            _Kept(value=blank)

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
        with pytest.raises(ValidationError):
            _Trimmed(value=blank)

    def test_surrounding_whitespace_is_taken_off(self) -> None:
        assert _Trimmed(value="  Bartosz Kobyliński\n").value == "Bartosz Kobyliński"

    def test_inner_whitespace_is_kept(self) -> None:
        assert _Trimmed(value=" a  b ").value == "a  b"
