"""A page saved after regjeringen.no's scripts ran is named as such, not as a conflict (#738).

The site's long-document script cuts a subheader longer than 150 characters at
130 and wraps the rest in a ``button[data-show-doctitle]`` and a
``span.visuallyhidden``. The served page carries the plain title, as every page
the reader was built on did. A browser's "complete" save keeps the rewritten
tree, so the subheader no longer equals ``DC.Title``. That was refused as
``conflicting_identity``, which sent the operator looking for a wrong document.
It is still refused, and nothing is written, but as ``browser_rendered_page``:
save the page source instead.

The pages here are the synthetic fixtures with that one rewrite applied; no
government text is involved.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.page import read_proposition
from tests.unit.cli_output import said
from tests.unit.test_forarbeider_chapter_cli import FIXTURE as CHAPTER
from tests.unit.test_forarbeider_chapter_cli import URL as CHAPTER_URL
from tests.unit.test_forarbeider_chapter_cli import run as run_chapter
from tests.unit.test_forarbeider_cli import FIXTURE as METADATA
from tests.unit.test_forarbeider_cli import URL as METADATA_URL
from tests.unit.test_forarbeider_cli import _argv

TITLE = "Strukturell testproposisjon om et oppdiktet lovforslag"
_PLAIN = f'<h2 class="subheader truncate">\n{TITLE}</h2>'


def _rendered(page: bytes, cut: int = 20) -> bytes:
    """The subheader as the site's script leaves it in a browser."""
    text = page.decode()
    assert _PLAIN in text
    shown, hidden = TITLE[:cut], TITLE[cut:]
    rewritten = (
        f'<h2 class="subheader truncate">{shown}<button type="button" aria-hidden="true" '
        f'data-show-doctitle="">... <span class="show-full-doctitle">(vis)</span></button>'
        f'<span class="visuallyhidden">{hidden}</span></h2>'
    )
    return text.replace(_PLAIN, rewritten).encode()


def _metadata(tmp_path: Path, page: bytes) -> tuple[int, str, Path]:
    saved, output = tmp_path / "saved.html", tmp_path / "record.json"
    saved.write_bytes(page)
    result = CliRunner().invoke(app, _argv(saved, output))
    return result.exit_code, result.output, output


@pytest.mark.parametrize("cut", [0, 20, len(TITLE)])
def test_the_metadata_command_names_a_browser_rendered_page(tmp_path: Path, cut: int) -> None:
    code, printed, output = _metadata(tmp_path, _rendered(METADATA.read_bytes(), cut))

    assert code == 1
    assert said("refused: browser_rendered_page: subheader", printed)
    assert not output.exists()


def test_the_chapter_command_names_it_too_and_writes_nothing(tmp_path: Path) -> None:
    saved, output = tmp_path / "saved.html", tmp_path / "chapter.json"
    saved.write_bytes(_rendered(CHAPTER.read_bytes()))

    code, printed = run_chapter(saved, output)

    assert code == 1
    assert said("refused: browser_rendered_page: subheader", printed)
    assert not output.exists()


def test_an_existing_record_survives_the_refusal(tmp_path: Path) -> None:
    (tmp_path / "record.json").write_bytes(b'{"existing": "kept"}\n')

    code, _, output = _metadata(tmp_path, _rendered(METADATA.read_bytes()))

    assert code == 1
    assert output.read_bytes() == b'{"existing": "kept"}\n'


def test_a_subheader_that_simply_disagrees_is_still_a_conflict() -> None:
    page = METADATA.read_bytes().replace(TITLE.encode() + b"</h2>", b"Et annet forslag</h2>")

    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_proposition(page, METADATA_URL)

    assert caught.value.reason is RefusalReason.CONFLICTING_IDENTITY
    assert caught.value.detail == "subheader"


def test_the_control_outside_the_subheader_changes_nothing() -> None:
    marker = b'<button type="button" data-show-doctitle="">x</button>'
    page = METADATA.read_bytes().replace(b"</h2>", b"</h2>" + marker, 1)

    assert read_proposition(page, METADATA_URL).title == TITLE


def test_the_plain_page_still_reads(tmp_path: Path) -> None:
    assert read_proposition(METADATA.read_bytes(), METADATA_URL).title == TITLE
    assert run_chapter(CHAPTER, tmp_path / "chapter.json")[0] == 0
    assert CHAPTER_URL.endswith("?ch=2")
