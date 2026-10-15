"""Experimental Stortinget case → sanctioned act crosswalk (ADR-0017, #610).

This module accepts caller-supplied primary-source page evidence; it performs no
network IO and never scrapes Lovdata. Only an exact official Stortinget case page
that itself identifies both the sanctioned act and Lovvedtak is sufficient here.

This is a proof of concept, NOT a general legislative history or legal-validity API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import parse_qs, urlsplit

Status = Literal["attested", "ambiguous", "unresolved", "contradicted", "unsupported"]
Representation = Literal["excerpt", "full_page"]

_LOV_ID = re.compile(r"LOV-(\d{4})-(\d{2})-(\d{2})-(\d+)")
_LOVVEDTAK = re.compile(
    r"\bLovvedtak\s+(\d{1,4})\s*\(\s*(\d{4})\s*[-\u2013]\s*(\d{4})\s*\)",
    re.IGNORECASE,
)
_SANCTION = re.compile(
    r"\bSanksjonert\s+(\d{1,2})\.(\d{1,2})\.(\d{4})\s*\(\s*lov\s*(\d+)\s*\)",
    re.IGNORECASE,
)


class _VisibleText(HTMLParser):
    """Minimal text projection; ignore script/style (not evidence)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def _text(source: str) -> str:
    if "<" not in source:
        return " ".join(source.split())
    parser = _VisibleText()
    parser.feed(source)
    return " ".join(" ".join(parser.parts).split())


_CASE_HOSTS = frozenset({"stortinget.no", "www.stortinget.no"})


def _case_id(url: str) -> int | None:
    """Trust a typed id only in the official Stortinget case-page URL."""
    parts = urlsplit(url)
    # The whole authority, not .hostname/.port: .port raises on a malformed
    # port and is falsy for ":0" and ":", so userinfo or any port is refused.
    if parts.scheme != "https" or parts.netloc.casefold() not in _CASE_HOSTS:
        return None
    if parts.fragment:
        return None
    if parts.path.rstrip("/").casefold() != "/no/saker-og-publikasjoner/saker/sak":
        return None
    query = parse_qs(parts.query, keep_blank_values=True)
    if set(query) != {"p"} or len(query["p"]) != 1:
        return None
    value = query["p"][0]
    return int(value) if value.isascii() and value.isdecimal() and int(value) > 0 else None


def _sanctioned_ids(text: str) -> set[str]:
    """The word 'Sanksjonert' must be present; ikrafttredelse is not sanction."""
    found: set[str] = set()
    for day, month, year, number in _SANCTION.findall(text):
        try:
            date(int(year), int(month), int(day))
        except ValueError:
            continue
        found.add(f"LOV-{year}-{int(month):02d}-{int(day):02d}-{int(number)}")
    return found


def _decisions(text: str) -> set[tuple[str, int]]:
    return {
        (f"{int(first):04d}-{int(last):04d}", int(number))
        for number, first, last in _LOVVEDTAK.findall(text)
        if int(last) == int(first) + 1
    }


@dataclass(frozen=True)
class CrosswalkRequest:
    """Stable identifiers supplied by a caller, not extracted from the page."""

    law_id: str
    session_id: str
    lovvedtak_number: int


@dataclass(frozen=True)
class CrosswalkProbe:
    """A link verdict about a single supplied source representation."""

    status: Status
    law_id: str
    session_id: str
    lovvedtak_number: int
    sak_id: int | None
    source_url: str
    input_sha256: str
    representation: Representation
    reason: str


def _classify_evidence(request: CrosswalkRequest, source_body: str) -> tuple[Status, str]:
    text = _text(source_body)
    sanctions = _sanctioned_ids(text)
    decisions = _decisions(text)
    if len(sanctions) > 1 or len(decisions) > 1:
        return "ambiguous", "Several law/decision identities occur in the source"
    if not sanctions or not decisions:
        return "unresolved", "Missing explicit sanction or Lovvedtak reference"
    expected = (request.session_id, request.lovvedtak_number)
    if request.law_id not in sanctions or expected not in decisions:
        return "contradicted", "Explicit source identifiers contradict the requested link"
    return "attested", "The official case page names both exact identifiers"


def _classify_request(
    request: CrosswalkRequest, sak_id: int | None, representation: Representation
) -> tuple[Status, str] | None:
    if sak_id is None or _LOV_ID.fullmatch(request.law_id) is None:
        return "unsupported", "Invalid official case URL or Lovdata law identity"
    if request.lovvedtak_number <= 0 or not re.fullmatch(r"\d{4}-\d{4}", request.session_id):
        return "unsupported", "Invalid parliamentary decision identity"
    if representation not in {"excerpt", "full_page"}:
        return "unsupported", "Unrecognised evidence representation"
    return None


def resolve_case_page(
    request: CrosswalkRequest,
    *,
    source_url: str,
    source_body: str,
    representation: Representation = "excerpt",
) -> CrosswalkProbe:
    """Evaluate caller-provided evidence; hash is NOT an official site checksum."""
    sak_id = _case_id(source_url)
    validation = _classify_request(request, sak_id, representation)
    verdict, reason = validation or _classify_evidence(request, source_body)
    return CrosswalkProbe(
        status=verdict,
        law_id=request.law_id,
        session_id=request.session_id,
        lovvedtak_number=request.lovvedtak_number,
        sak_id=sak_id,
        source_url=source_url,
        input_sha256=sha256(source_body.encode("utf-8")).hexdigest(),
        representation=representation,
        reason=reason,
    )
