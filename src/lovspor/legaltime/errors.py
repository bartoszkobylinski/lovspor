"""Typed refusals of the legal-time replay pilot (#744)."""

from lovspor.errors import LovsporError


class LegalTimeError(LovsporError):
    """Base of every replay refusal: a condition the pilot will not guess past."""


class LegalTimeDocumentError(LegalTimeError):
    """A Lovtidend member does not have the document shape the parser requires."""


class LegalTimeInputError(LegalTimeError):
    """A pinned input (archive, member, oracle) is missing or does not verify."""
