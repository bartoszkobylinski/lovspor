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


def _case_id(url: str) -> int | None:
    """Trust a typed id only in the official Stortinget case-page URL."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in {"stortinget.no", "www.stortinget.no"}:
        return None
    if parts.username or parts.password or parts.port or parts.fragment:
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


def resolve_case_page(
    *,
    law_id: str,
    session_id: str,
    lovvedtak_number: int,
    source_url: str,
    source_body: str,
    representation: Representation = "excerpt",
) -> CrosswalkProbe:
    """Fail closed on missing/conflicting case-page evidence.

    The hash attests only the *supplied bytes* (an excerpt by default), NOT
    the entire upstream document. Callers must acquire and retain sources
    separately and must not pass a Lovdata web page as a Stortinget source.
    """
    sak_id = _case_id(source_url)
    verdict: Status = "unresolved"
    reason = "The source does not establish the complete law–decision–case chain"
    if sak_id is None or _LOV_ID.fullmatch(law_id) is None:
        verdict, reason = "unsupported", "Invalid official case URL or Lovdata law identity"
    elif lovvedtak_number <= 0 or not re.fullmatch(r"\d{4}-\d{4}", session_id):
        verdict, reason = "unsupported", "Invalid parliamentary decision identity"
    elif representation not in {"excerpt", "full_page"}:
        verdict, reason = "unsupported", "Unrecognised evidence representation"
    else:
        text = _text(source_body)
        sanctions = _sanctioned_ids(text)
        decisions = _decisions(text)
        expected_decision = (session_id, lovvedtak_number)
        if len(sanctions) > 1 or len(decisions) > 1:
            verdict, reason = "ambiguous", "Several law/decision identities occur in the source"
        elif not sanctions or not decisions:
            verdict, reason = "unresolved", "Missing explicit sanction or Lovvedtak reference"
        elif law_id not in sanctions or expected_decision not in decisions:
            verdict, reason = (
                "contradicted",
                "Explicit source identifiers contradict the requested link",
            )
        else:
            verdict, reason = "attested", "The official case page names both exact identifiers"
    return CrosswalkProbe(
        status=verdict,
        law_id=law_id,
        session_id=session_id,
        lovvedtak_number=lovvedtak_number,
        sak_id=sak_id,
        source_url=source_url,
        input_sha256=sha256(source_body.encode("utf-8")).hexdigest(),
        representation=representation,
        reason=reason,
    )
