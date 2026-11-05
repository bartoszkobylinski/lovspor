"""The capture law-list limit applies to source bytes, before UTF-8 decoding."""

from pathlib import Path

import pytest

from lovspor.crosswalk.commands import MAX_LAW_LIST_BYTES, read_law_ids
from lovspor.crosswalk.errors import CrosswalkError

LAW = "LOV-2022-06-17-63"


@pytest.mark.parametrize("ending", ["ø", "😀"])
def test_multibyte_comment_ending_at_byte_limit_is_accepted(tmp_path: Path, ending: str) -> None:
    prefix = (LAW + "\n#").encode()
    suffix = (ending * 2).encode("utf-8")
    body = prefix + b"x" * (MAX_LAW_LIST_BYTES - len(prefix) - len(suffix)) + suffix
    path = tmp_path / "laws.txt"
    path.write_bytes(body)

    assert len(body) == MAX_LAW_LIST_BYTES
    assert read_law_ids(path) == [LAW]
    assert path.read_bytes() == body


@pytest.mark.parametrize("ending", ["ø", "😀"])
def test_multibyte_law_list_over_byte_limit_is_refused(tmp_path: Path, ending: str) -> None:
    prefix = (LAW + "\n#").encode()
    suffix = (ending * 2).encode("utf-8")
    body = prefix + b"x" * (MAX_LAW_LIST_BYTES + 1 - len(prefix) - len(suffix)) + suffix
    path = tmp_path / "laws.txt"
    path.write_bytes(body)

    assert len(body) == MAX_LAW_LIST_BYTES + 1
    assert len(body.decode("utf-8")) < MAX_LAW_LIST_BYTES
    with pytest.raises(CrosswalkError) as caught:
        read_law_ids(path)
    assert str(caught.value) == f"law list too large: {path}"
    assert path.read_bytes() == body
