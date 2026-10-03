"""Data models for the identity of a promoted local regulation (ADR-0016 Decision 1).

An identity is either minted or held — never both, never a guess. A held
result has no ``doc_id`` field at all, so no caller can read an id out of a
regulation the rules refused to identify (ADR-0016 1a.3, 4h).
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_KLASS_CODE_LENGTH = {"kommune": 4, "fylkeskommune": 2}
_DIGITS = re.compile(r"[0-9]+")


class AuthorityType(StrEnum):
    KOMMUNE = "kommune"
    FYLKESKOMMUNE = "fylkeskommune"


class Authority(BaseModel):
    """The publishing authority (ADR-0016 1c).

    ``id`` is the SSB KLASS code exactly as the observatory registry holds it:
    four digits for a kommune, two for a fylkeskommune. The length already
    tells them apart; ``type`` states it, and the two must agree.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    type: AuthorityType
    name: str = Field(min_length=1)
    klass_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _klass_code_matches_type(self) -> Authority:
        expected = _KLASS_CODE_LENGTH[self.type.value]
        if not _DIGITS.fullmatch(self.id) or len(self.id) != expected:
            msg = f"a {self.type.value} KLASS code is {expected} digits, got {self.id!r}"
            raise ValueError(msg)
        return self


class ExtractedRegulation(BaseModel):
    """What the extractor (slice S2) hands identity: text already pulled out of a page.

    ``identification_block`` is the regulation's own title block, header and
    kunngjøring line — the only place an ``lf-`` id may be read from.
    ``body`` is everything else; a date-number in it is a citation, never the
    document's id. ``vedtaksdato`` is the decision date as the text states it,
    or ``None``; it is never filled from an observation time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    identification_block: str
    body: str
    vedtaksdato: date | None = None

    @property
    def full_text(self) -> str:
        return f"{self.identification_block}\n{self.body}"


class IdScheme(StrEnum):
    LF = "lf"
    LK = "lk"


class HoldReason(StrEnum):
    NO_IDENTITY = "no_identity"
    CENTRAL_COLLISION = "central_collision"


class MintedIdentity(BaseModel):
    """An immutable document id, with the facts it was minted from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["minted"] = "minted"
    doc_id: str
    scheme: IdScheme
    ref_id: str | None
    authority: Authority
    normalised_title: str
    content_hash: str
    candidates: tuple[str, ...] = ()


class HeldIdentity(BaseModel):
    """A regulation the identity rules refuse to name; counted, never dropped."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["held"] = "held"
    reason: HoldReason
    detail: str
    authority: Authority
    content_hash: str
    candidates: tuple[str, ...] = ()
    colliding_ref_id: str | None = None


IdentityResult = Annotated[MintedIdentity | HeldIdentity, Field(discriminator="outcome")]
