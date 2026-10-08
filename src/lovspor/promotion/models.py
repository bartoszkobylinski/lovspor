"""Data models for the identity of a promoted local regulation (ADR-0016 Decision 1).

An identity is either minted or held — never both, never a guess. A held
result has no ``doc_id`` field at all, so no caller can read an id out of a
regulation the rules refused to identify (ADR-0016 1a.3, 4h).
"""

from __future__ import annotations

import re
from datetime import UTC, date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

_KLASS_CODE_LENGTH = {"kommune": 4, "fylkeskommune": 2}
_DIGITS = re.compile(r"[0-9]+")


class AuthorityType(StrEnum):
    KOMMUNE = "kommune"
    FYLKESKOMMUNE = "fylkeskommune"


class RemovedReason(StrEnum):
    """Why a local document left the served dataset (ADR-0016 4f): a closed set.

    Every value is a withdrawal — a human decision recorded in the decision
    log. A promoted source whose archive blob is tombstoned (ADR-0010 §7) is
    withdrawn on the basis the tombstone states, under one of these.
    """

    WITHDRAWN_MISCLASSIFIED = "withdrawn_misclassified"
    WITHDRAWN_IDENTITY = "withdrawn_identity"
    WITHDRAWN_PERSONAL_DATA = "withdrawn_personal_data"
    WITHDRAWN_LEGAL = "withdrawn_legal"


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


class SourceForm(StrEnum):
    HTML = "html"
    PDF = "pdf"
    DOCX = "docx"


class ExtractionHoldReason(StrEnum):
    """Why extracted text may not be published (ADR-0016 4h): held, counted, never dropped."""

    UNSUPPORTED_FORMAT = "unsupported_format"
    UNREADABLE = "unreadable"
    EMPTY_TEXT = "empty_text"
    GARBLED_TEXT = "garbled_text"
    LOVDATA_COPY = "lovdata_copy"
    PLACEHOLDER_DATE = "placeholder_date"
    NO_BODY = "no_body"
    NO_TITLE = "no_title"
    TITLE_TRUNCATED = "title_truncated"
    PERSONAL_DATA = "personal_data"


class PersonalDataKind(StrEnum):
    """What the personal-data gate found (ADR-0016 Decision 6, personopplysningsloven)."""

    FODSELSNUMMER = "fodselsnummer"
    EMAIL = "email"
    PHONE = "phone"
    POSTAL_ADDRESS = "postal_address"
    CONTACT_LINE = "contact_line"
    SIGNATURE = "signature"
    BYLINE = "byline"


class PersonalDataHit(BaseModel):
    """Where a hit sits — kind and 1-based line of the extracted text, never the value.

    The hold report is read by a human with the source open; repeating the
    personal data in it would copy what the gate exists to keep contained.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: PersonalDataKind
    line: int = Field(ge=1)


class RegulationFields(BaseModel):
    """Pre-filled fields for human confirmation, each as the text states it or empty.

    ``vedtatt`` and ``ikraft`` are source-explicit dates or ``None``; an
    in-force phrase that is not a date ("straks") is kept verbatim in
    ``ikraft_text``. Each is the value every statement of the text agrees on
    (``stated_dates.py``); statements that disagree leave it ``None``. Nothing
    here is filled from an observation time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1)
    hjemmel: tuple[str, ...] = ()
    vedtatt: date | None = None
    vedtatt_av: str | None = None
    ikraft: date | None = None
    ikraft_text: str | None = None


class ExtractedDocument(BaseModel):
    """A source the extractor read in full: the regulation's text and its fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["extracted"] = "extracted"
    extractor_version: int
    source_form: SourceForm
    regulation: ExtractedRegulation
    fields: RegulationFields


class HeldExtraction(BaseModel):
    """A source whose text may not be published as it stands, with the reason."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["held"] = "held"
    extractor_version: int
    source_form: SourceForm | None
    reason: ExtractionHoldReason
    detail: str
    personal_data: tuple[PersonalDataHit, ...] = ()


ExtractionResult = Annotated[ExtractedDocument | HeldExtraction, Field(discriminator="outcome")]


class ObservedSource(BaseModel):
    """Where and when the rendered version was first observed (ADR-0016 Decision 2).

    ``observed_at_first`` is the observation axis only — the first capture of
    *this* version's content — and must carry a timezone; it is written in
    UTC. ``source_sha256`` names the archived blob the text was rendered from.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_at_first: AwareDatetime
    source_url: str = Field(pattern=r"^https?://\S+$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def observed_at_first_utc(self) -> str:
        return self.observed_at_first.astimezone(UTC).isoformat().replace("+00:00", "Z")


class LocalDocument(BaseModel):
    """Everything one rendered version of a local regulation is made from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: MintedIdentity
    extracted: ExtractedDocument
    slug: str = Field(pattern=r"^[^\s/\\.][^\s/\\]*$")
    version: int = Field(ge=1)
    source: ObservedSource
