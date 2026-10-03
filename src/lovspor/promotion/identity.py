"""Identity of a promoted local regulation (ADR-0016 Decision 1a, slice S1).

Pure functions: no I/O, no clock, no network. The same extracted text, the
same authority and the same central ref-id set give the same result on every
run and every machine.

An id is chosen by the first rule that applies:

1. ``lf-yyyymmdd-nnnn`` when the regulation's *own identification block*
   names exactly one date and number. A date-number in the body is a citation
   and is never read; neither are ``Hjemmel``/``Endrer`` lines in a header.
2. ``lk-<authority_id>-<h12>`` from authority, normalised title and the
   vedtaksdato the text states.
3. Otherwise the regulation is held. No id is minted from an observation time,
   a URL or a title alone.

An ``lf-`` id whose ref-id a central (``sf-``) record already carries is held
as a collision, never merged.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date

from lovspor.promotion.models import (
    Authority,
    ExtractedRegulation,
    HeldIdentity,
    HoldReason,
    IdentityResult,
    IdScheme,
    MintedIdentity,
)

_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "januar",
            "februar",
            "mars",
            "april",
            "mai",
            "juni",
            "juli",
            "august",
            "september",
            "oktober",
            "november",
            "desember",
        ),
        start=1,
    )
}
_DATE = r"(\d{1,2}\.\s*[^\W\d_]+\s+\d{4}|\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})"
_DATE_NUMBER = _DATE + r"\s+nr\.?\s*(\d{1,6})\b"
_FOR_HEADER = re.compile(r"\s*(?:dato\s*:?\s*)?FOR-(\d{4}-\d{2}-\d{2})-(\d{1,6})\b", re.I)
_TITLE_LINE = re.compile(r"\s*forskrift\s+(?:av\s+)?" + _DATE_NUMBER, re.I)
_KUNNGJORT_LINE = re.compile(r"\s*kunngjort\b", re.I)
_DATE_NUMBER_ANYWHERE = re.compile(_DATE_NUMBER, re.I)
_TEXTUAL_DATE = re.compile(r"(\d{1,2})\.\s*([^\W\d_]+)\s+(\d{4})")
_NUMERIC_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_CENTRAL_REF_ID = re.compile(r"forskrift/(\d{4}-\d{2}-\d{2})-(\d{1,6})")
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\ufeff"))
_UNIT_SEPARATOR = "\x1f"

_LovdataId = tuple[date, int]


def normalise_title(title: str) -> str:
    """NFC, casefold, punctuation stripped, whitespace collapsed (ADR-0016 1a.2)."""
    folded = unicodedata.normalize("NFC", unicodedata.normalize("NFC", title).casefold())
    kept = "".join(ch for ch in folded if not unicodedata.category(ch).startswith("P"))
    return " ".join(kept.split())


def normalise_text(text: str) -> str:
    """Layout-insensitive form of extracted text, the input to ``content_hash``.

    Unicode NFC; line endings unified; invisible characters (soft hyphen,
    zero-width space, BOM) dropped; runs of whitespace within a line become
    one space; runs of blank lines become one. Case and punctuation are legal
    text and are kept.
    """
    unified = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    lines = [
        " ".join(line.split())
        for line in unified.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    paragraphs: list[list[str]] = [[]]
    for line in lines:
        if line:
            paragraphs[-1].append(line)
        elif paragraphs[-1]:
            paragraphs.append([])
    return "\n\n".join("\n".join(p) for p in paragraphs if p)


def content_hash(text: str) -> str:
    """SHA-256 hex of the normalised text — the version key (ADR-0016 Option B2)."""
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()


def find_identification_ids(identification_block: str) -> tuple[str, ...]:
    """Every distinct ``lf-`` id the block's identification lines name, in order seen."""
    return tuple(_lf_id(candidate) for candidate in _candidates(identification_block))


def mint_identity(
    regulation: ExtractedRegulation,
    authority: Authority,
    central_ref_ids: frozenset[str] = frozenset(),
) -> IdentityResult:
    """Apply ADR-0016 1a's rules in order; a held result carries no id."""
    candidates = _candidates(regulation.identification_block)
    facts = _Facts(regulation, authority, tuple(_lf_id(c) for c in candidates))
    if len(candidates) == 1:
        return _lovdata_identity(facts, candidates[0], _central_ids(central_ref_ids))
    return _fallback_identity(facts)


class _Facts:
    """What every outcome records, computed once."""

    def __init__(
        self, regulation: ExtractedRegulation, authority: Authority, candidates: tuple[str, ...]
    ) -> None:
        self.regulation = regulation
        self.authority = authority
        self.candidates = candidates
        self.title = normalise_title(regulation.title)
        self.hash = content_hash(regulation.full_text)

    def held(self, reason: HoldReason, detail: str, collision: str | None = None) -> HeldIdentity:
        return HeldIdentity(
            reason=reason,
            detail=detail,
            authority=self.authority,
            content_hash=self.hash,
            candidates=self.candidates,
            colliding_ref_id=collision,
        )

    def minted(self, doc_id: str, scheme: IdScheme, ref_id: str | None) -> MintedIdentity:
        return MintedIdentity(
            doc_id=doc_id,
            scheme=scheme,
            ref_id=ref_id,
            authority=self.authority,
            normalised_title=self.title,
            content_hash=self.hash,
            candidates=self.candidates,
        )


def _lovdata_identity(
    facts: _Facts, candidate: _LovdataId, central: frozenset[_LovdataId]
) -> IdentityResult:
    ref_id = _ref_id(candidate)
    if candidate in central:
        detail = f"{_lf_id(candidate)} names {ref_id}, which a central record already carries"
        return facts.held(HoldReason.CENTRAL_COLLISION, detail, ref_id)
    return facts.minted(_lf_id(candidate), IdScheme.LF, ref_id)


def _fallback_identity(facts: _Facts) -> IdentityResult:
    vedtaksdato = facts.regulation.vedtaksdato
    if vedtaksdato is None or not facts.title:
        detail = "no single lf- id in the identification block, and no stated vedtaksdato"
        if vedtaksdato is not None:
            detail = "no single lf- id in the identification block, and no title words"
        return facts.held(HoldReason.NO_IDENTITY, detail)
    seed = _UNIT_SEPARATOR.join((facts.authority.id, facts.title, vedtaksdato.isoformat()))
    h12 = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return facts.minted(f"lk-{facts.authority.id}-{h12}", IdScheme.LK, None)


def _candidates(identification_block: str) -> tuple[_LovdataId, ...]:
    found: dict[_LovdataId, None] = {}
    for line in identification_block.splitlines():
        candidate = _identification_line_id(line)
        if candidate is not None:
            found.setdefault(candidate)
    return tuple(found)


def _identification_line_id(line: str) -> _LovdataId | None:
    header = _FOR_HEADER.match(line)
    if header:
        return _checked(_parse_date(header.group(1)), header.group(2))
    title = _TITLE_LINE.match(line)
    if title:
        return _checked(_parse_date(title.group(1)), title.group(2))
    if _KUNNGJORT_LINE.match(line):
        cited = _DATE_NUMBER_ANYWHERE.search(line)
        if cited:
            return _checked(_parse_date(cited.group(1)), cited.group(2))
    return None


def _checked(day: date | None, number: str) -> _LovdataId | None:
    if day is None or int(number) == 0:
        return None
    return (day, int(number))


def _parse_date(text: str) -> date | None:
    """A real calendar date in one of the three written forms, else ``None``."""
    try:
        return _date_parts(text)
    except ValueError:
        return None


def _date_parts(text: str) -> date | None:
    textual = _TEXTUAL_DATE.fullmatch(text)
    if textual:
        month = _MONTHS.get(textual.group(2).casefold())
        if month is None:
            return None
        return date(int(textual.group(3)), month, int(textual.group(1)))
    numeric = _NUMERIC_DATE.fullmatch(text)
    if numeric:
        return date(int(numeric.group(3)), int(numeric.group(2)), int(numeric.group(1)))
    return date.fromisoformat(text)


def _central_ids(central_ref_ids: frozenset[str]) -> frozenset[_LovdataId]:
    """Central ref-ids as (date, number), so ``-63`` and ``-0063`` compare equal."""
    parsed = (_central_id(ref_id) for ref_id in central_ref_ids)
    return frozenset(candidate for candidate in parsed if candidate is not None)


def _central_id(ref_id: str) -> _LovdataId | None:
    match = _CENTRAL_REF_ID.fullmatch(ref_id)
    if match is None:
        return None
    return _checked(_parse_date(match.group(1)), match.group(2))


def _lf_id(candidate: _LovdataId) -> str:
    day, number = candidate
    return f"lf-{day:%Y%m%d}-{number:04d}"


def _ref_id(candidate: _LovdataId) -> str:
    day, number = candidate
    return f"forskrift/{day.isoformat()}-{number}"
