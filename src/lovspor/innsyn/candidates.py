"""Mailbox candidates read from the archive's own captures (read-only).

The pilot found every recipient in the authority's captured pages, not by a
live visit (``find_postmottak.py``, 2026-10-03): the general mailboxes
(``postmottak@``, ``post@``, ``postmottak.<unit>@``) under ``.no``, counted per
distinct blob, cited by the shortest URL that carries them (usually the front
page or the contact page). This is that scan over the corrected log.

A candidate is a proposal, never a recipient. Nothing here writes to the
archive or to the directory: the operator opens the cited page, chooses, and
records the choice with ``innsyn set-recipient``, whose evidence is that page.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from lovspor.errors import LogIntegrityError
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, ObservationRecord

MAILBOX = re.compile(rb"\b(post(?:mottak)?(?:\.[a-z0-9-]+)?@[a-z0-9.-]+\.no)\b", re.IGNORECASE)

_SUCCESS = range(200, 300)

_Key = tuple[str, str]


class Candidate(BaseModel):
    """One mailbox seen on an authority's pages, and its shortest citation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_id: str
    address: str
    pages: int
    url: str
    observed_at: datetime
    sha256: str


def _html(record: ArtifactObservation) -> bool:
    media_type = record.content_type.split(";", 1)[0].lower()
    return record.http_status in _SUCCESS and "html" in media_type


@dataclass
class _Scan:
    log: ObservationLog
    wanted: frozenset[str] | None
    seen: set[str] = field(default_factory=set)
    found: dict[_Key, Candidate] = field(default_factory=dict)

    def __call__(self, record: ObservationRecord) -> None:
        if not isinstance(record, ArtifactObservation) or not _html(record):
            return
        if self.wanted is not None and record.authority_id not in self.wanted:
            return
        if record.sha256 in self.seen:
            return
        self.seen.add(record.sha256)
        for address in self._addresses(record.sha256):
            self._keep(record, address)

    def _addresses(self, sha256: str) -> set[str]:
        try:
            blob = self.log.read_blob(sha256)
        except FileNotFoundError:
            return set()
        return {match.group(1).decode("ascii").lower() for match in MAILBOX.finditer(blob)}

    def _keep(self, record: ArtifactObservation, address: str) -> None:
        key = (record.authority_id, address)
        held = self.found.get(key)
        pages = 1 if held is None else held.pages + 1
        if held is None or len(record.url) < len(held.url):
            cite = (record.url, record.observed_at, record.sha256)
        else:
            cite = (held.url, held.observed_at, held.sha256)
        url, observed_at, sha256 = cite
        self.found[key] = Candidate(
            authority_id=record.authority_id,
            address=address,
            pages=pages,
            url=url,
            observed_at=observed_at,
            sha256=sha256,
        )


def mailbox_candidates(log: ObservationLog, wanted: frozenset[str] | None) -> list[Candidate]:
    """Every candidate for ``wanted`` (every authority when None), most pages first.

    Raises:
        LogIntegrityError: the log does not read clean.
    """
    scan = _Scan(log, wanted)
    result = log.scan_corrected_into(scan)
    if not result.complete:
        raise LogIntegrityError(
            f"{log.log_path}: unreadable record after byte {result.clean_through}",
        )
    return sorted(scan.found.values(), key=lambda c: (c.authority_id, -c.pages, c.address))
