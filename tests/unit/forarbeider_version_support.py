"""Synthetic evidence for the version manifest tests (#738, FC6, FC6b).

The invented proposition of ``chapter-l.html`` (Prop. 901 L, id 9000001) lists
chapters 1 to 4, chapter 4 an annex; ``landing-l-plain.html`` is its landing
page. Attachment bytes are invented. No real government text is used.
"""

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider.document_extract import SourcePage
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.version_manifest import Evidence, build_version_manifest
from lovspor.forarbeider.version_models import VersionManifest
from tests.unit.test_forarbeider_document_extract import WHEN, chapter_page

FIXTURES = Path(__file__).parents[1] / "fixtures" / "forarbeider"
CORPUS = FIXTURES / "corpus"
TITLES = {1: "1 Innledning", 2: "2 Bakgrunn", 3: "Tilråding", 4: "1 Oppdiktet vedlegg"}
DOCUMENT_PATH = "/no/dokumenter/prop.-901-l-20992100/id9000001/"
LANDING_URL = "https://www.regjeringen.no" + DOCUMENT_PATH
ASSETS = "https://www.regjeringen.no/contentassets/" + "0" * 32 + "/no"
PDF = b"%PDF-1.7\n% invented attachment\n%%EOF\n"
ZIP = b"PK\x03\x04 invented archive"
PDF_URL = ASSETS + "/vedlegg/vedlegg-1.pdf"
_DEFAULT: Any = object()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def first_or_any(number: int) -> SourcePage:
    """Chapter ``number``; the first chapter links back to the landing page."""
    built = chapter_page(number, TITLES[number])
    raw = re.sub(r'href="[^"]*\?ch=0"', f'href="{DOCUMENT_PATH}"', built.payload.decode())
    return built._replace(payload=raw.encode())


def pages(*numbers: int) -> list[SourcePage]:
    return [first_or_any(number) for number in numbers or (1, 2, 3, 4)]


def with_body(number: int, body: str) -> SourcePage:
    """Chapter ``number`` with ``body`` in place of its one paragraph."""
    original = first_or_any(number)
    marker = f"<p>Tekst i kapittel {number}.</p>"
    return original._replace(payload=original.payload.decode().replace(marker, body).encode())


def with_markup(page: SourcePage, before: str, markup: str) -> SourcePage:
    """``page`` with ``markup`` inserted just before the first ``before``."""
    text = page.payload.decode()
    assert before in text
    return page._replace(payload=text.replace(before, markup + before, 1).encode())


def landing(markup: str = "", before: str = '<p><a class="btn-cta"') -> SourcePage:
    raw = (FIXTURES / "landing-l-plain.html").read_bytes()
    page = SourcePage(payload=raw, url=LANDING_URL, retrieved_at=WHEN)
    return with_markup(page, before, markup) if markup else page


def capture(url: str, payload: bytes) -> SourcePage:
    return SourcePage(payload=payload, url=url, retrieved_at=WHEN)


def build(
    given: list[SourcePage] | None = None,
    landing_page: SourcePage | None = _DEFAULT,
    captures: tuple[SourcePage, ...] = (),
) -> VersionManifest:
    chosen = pages() if given is None else given
    front = landing() if landing_page is _DEFAULT else landing_page
    return build_version_manifest(Evidence(tuple(chosen), front, captures))


def evidence(
    given: list[SourcePage] | None = None,
    landing_page: SourcePage | None = _DEFAULT,
    captures: tuple[SourcePage, ...] = (),
) -> Evidence:
    chosen = pages() if given is None else given
    front = landing() if landing_page is _DEFAULT else landing_page
    return Evidence(tuple(chosen), front, captures)


def codes(reasons: Any) -> set[tuple[str, str]]:
    return {(reason.code, reason.effect.value) for reason in reasons}


def refused(reason: RefusalReason) -> Any:
    return pytest.raises(ForarbeiderRefusedError, match=reason.value)
