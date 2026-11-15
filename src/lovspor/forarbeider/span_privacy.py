"""Privacy screening of one span or gap, apart from any rights question (#738, FC5; §3.5).

Pattern-specific detectors, each named by a reason code; the matched text is
never returned or stored. A hit means ``review_required``. Silence means
``no_detector_hit``, which is not proof that no personal data is there. A gap,
or a span of a part whose markup is unproved, is ``unresolved``: what it holds
was not read as a span, so silence there says even less.

- ``email_address``, ``phone_number``, ``bank_account_number``,
  ``street_address``: direct identifiers by their written shape;
- ``national_id_number``: eleven digits that pass both fødselsnummer check
  digits; ``national_id_like_number``: eleven digits that do not, still held
  for review because a mistyped number is still a number;
- ``special_category_term``: health, criminal, ethnic, religious, sexual,
  union, biometric or genetic terms, minus the fixed domain phrases that name
  a topic rather than a person (``biometrisk risiko``);
- ``in_quotation``: added beside any hit inside a quotation.
"""

import re

from lovspor.forarbeider.span_assessment_models import PrivacyState

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+47|\btlf\.?|\btelefon)[:\s]*\d(?:\s?\d){7}", re.IGNORECASE)
_ELEVEN = re.compile(r"(?<!\d)(\d{6})\s?(\d{5})(?!\d)")
_ACCOUNT = re.compile(r"(?<!\d)\d{4}\.\d{2}\.\d{5}(?!\d)")
_STREET = re.compile(
    r"\b[A-ZÆØÅ][a-zæøå]+(?:veien|vegen|veg|vei|gata|gaten|gate|plass|allé)\s\d+[A-Za-z]?\b"
)
_SPECIAL = re.compile(
    r"helseopplysning|diagnose|sykdom|straffbar|straffedom|domfelt|etnisk|religiøs"
    r"|seksuell|fagforening|biometri|genetisk",
    re.IGNORECASE,
)
#: Phrases that name a topic of regulation, not a person's special-category data.
_EXEMPT = re.compile(r"biometrisk(?:e)? risiko", re.IGNORECASE)
_WEIGHTS = ((3, 7, 6, 1, 8, 9, 4, 5, 2), (5, 4, 3, 2, 7, 6, 5, 4, 3, 2))
_ELEVEN_DIGITS = 11


def screen(
    text: str | None, *, quoted: bool, unproved: bool
) -> tuple[PrivacyState, tuple[str, ...]]:
    """The privacy state of an entry's text, with the detectors that fired."""
    reasons = _hits(text or "")
    if reasons and quoted:
        reasons.append("in_quotation")
    if reasons:
        return PrivacyState.REVIEW_REQUIRED, tuple(reasons)
    if text is None or unproved:
        return PrivacyState.UNRESOLVED, ("not_screened_as_a_span",)
    return PrivacyState.NO_DETECTOR_HIT, ()


def screen_gap(text: str | None, code: str) -> tuple[PrivacyState, tuple[str, ...]]:
    """A gap: any hit is a review; otherwise it stays unresolved, never silent."""
    reasons = _hits(text or "")
    if reasons:
        return PrivacyState.REVIEW_REQUIRED, tuple(reasons)
    return PrivacyState.UNRESOLVED, (f"extraction_gap:{code}",)


def _hits(text: str) -> list[str]:
    found = [
        name
        for name, pattern in (
            ("email_address", _EMAIL),
            ("phone_number", _PHONE),
            ("bank_account_number", _ACCOUNT),
            ("street_address", _STREET),
        )
        if pattern.search(text)
    ]
    found += _national_ids(text)
    if _SPECIAL.search(_EXEMPT.sub("", text)):
        found.append("special_category_term")
    return found


def _national_ids(text: str) -> list[str]:
    numbers = ["".join(match.groups()) for match in _ELEVEN.finditer(text)]
    if not numbers:
        return []
    valid = any(_checks(number) for number in numbers)
    return ["national_id_number" if valid else "national_id_like_number"]


def _checks(number: str) -> bool:
    """Both fødselsnummer check digits hold."""
    digits = [int(char) for char in number]
    if len(digits) != _ELEVEN_DIGITS:
        return False
    for weights, position in zip(_WEIGHTS, (9, 10), strict=True):
        remainder = sum(w * d for w, d in zip(weights, digits, strict=False)) % 11
        check = 0 if remainder == 0 else 11 - remainder
        if check == 10 or check != digits[position]:  # noqa: PLR2004 - mod 11 has no digit 10
            return False
    return True
