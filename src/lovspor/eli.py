"""Official Norwegian ELI for an act, derived from Lovdata's ref_id (#819).

Lovspor does not mint ELIs. This maps a Lovdata ``ref_id`` (the FRBR work
identity, e.g. ``lov/2005-05-20-28``) to the URI Lovdata publishes for the
same act, and only for the one shape that permitted official sources attest:

    https://lovdata.no/eli/{lov|forskrift}/{yyyy}/{mm}/{dd}/{number}

Evidence, classification and the open questions are in
``docs/research/eli-assessment-2026-11-06.md``. In short: the type, the
zero-padded date, the unpadded serial number and the absence of a
jurisdiction segment are attested by Digdir's national specifications and by
archive observations; an act without a serial number, any subdivision,
language, version or point in time is not, so those return ``None`` with a
named reason. A returned URI is constructed, never resolved: nothing here
makes a request.

The URI identifies the whole act. It is never a provision's identity.
"""

import re
from collections import Counter
from collections.abc import Iterable
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

ELI_HOST = "https://lovdata.no/eli"

ELI_ACT_PATTERN = re.compile(
    r"https://lovdata\.no/eli/(?:lov|forskrift)/[0-9]{4}/[0-9]{2}/[0-9]{2}/[1-9][0-9]*"
)
"""Every URI this module can return: type, date and number, nothing below."""

_NUMBERED = re.compile(r"(lov|forskrift)/([0-9]{4})-([0-9]{2})-([0-9]{2})-([1-9][0-9]*)", re.ASCII)
_NUMBERLESS = re.compile(r"(?:lov|forskrift)/[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)

EliReason = Literal["mapped", "no_serial_number", "invalid_date", "unsupported_shape"]

AUDIT_NOTE = (
    "URIs are constructed from ref_id by the attested pattern; they were not "
    "resolved against lovdata.no and no request was made."
)

_PADDING_THRESHOLD = 10
"""A month or day below this is written with a leading zero, the form on
which official sources disagree (the assessment, finding 3)."""


class EliOutcome(BaseModel):
    """What one ref_id maps to: a URI, or None and why."""

    model_config = ConfigDict(frozen=True)

    ref_id: str
    uri: str | None
    reason: EliReason


class EliAudit(BaseModel):
    """Counts over a set of ref_ids, with deterministic samples per reason."""

    model_config = ConfigDict(frozen=True)

    total: int
    distinct: int
    by_reason: dict[str, int]
    padding_sensitive: int
    samples: dict[str, list[EliOutcome]]
    note: str = AUDIT_NOTE


def act_eli(ref_id: str) -> EliOutcome:
    """The official act-level ELI for ``ref_id``, or None with a reason."""
    match = _NUMBERED.fullmatch(ref_id)
    if match is None:
        reason: EliReason = (
            "no_serial_number" if _NUMBERLESS.fullmatch(ref_id) else "unsupported_shape"
        )
        return EliOutcome(ref_id=ref_id, uri=None, reason=reason)
    kind, year, month, day, number = match.groups()
    try:
        date(int(year), int(month), int(day))
    except ValueError:
        return EliOutcome(ref_id=ref_id, uri=None, reason="invalid_date")
    uri = f"{ELI_HOST}/{kind}/{year}/{month}/{day}/{number}"
    return EliOutcome(ref_id=ref_id, uri=uri, reason="mapped")


def official_act_eli(ref_id: str) -> str | None:
    """The URI alone; None wherever the evidence does not cover the shape."""
    return act_eli(ref_id).uri


def audit_ref_ids(ref_ids: Iterable[str], sample_size: int) -> EliAudit:
    """Read-only coverage report: how many ref_ids map, and why the rest do not."""
    refs = list(ref_ids)
    outcomes = {ref: act_eli(ref) for ref in set(refs)}
    by_reason = Counter(outcomes[ref].reason for ref in refs)
    samples: dict[str, list[EliOutcome]] = {}
    for ref in sorted(outcomes):
        bucket = samples.setdefault(outcomes[ref].reason, [])
        if len(bucket) < sample_size:
            bucket.append(outcomes[ref])
    return EliAudit(
        total=len(refs),
        distinct=len(outcomes),
        by_reason=dict(sorted(by_reason.items())),
        padding_sensitive=sum(_padding_sensitive(outcomes[ref]) for ref in refs),
        samples=dict(sorted(samples.items())),
    )


def _padding_sensitive(outcome: EliOutcome) -> bool:
    """A mapped URI whose month or day carries a leading zero."""
    if outcome.uri is None:
        return False
    month, day = outcome.uri.split("/")[-3:-1]
    return int(month) < _PADDING_THRESHOLD or int(day) < _PADDING_THRESHOLD
