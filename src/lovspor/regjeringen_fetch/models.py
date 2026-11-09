"""What one explicit-list fetch run did, URL by URL (#738)."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator


class FetchOutcome(StrEnum):
    """Every way one listed URL can end. Only ``fetched`` stores a page."""

    FETCHED = "fetched"
    ROBOTS_DISALLOWED = "robots_disallowed"
    REDIRECT_NOT_FOLLOWED = "redirect_not_followed"
    STOPPED_BY_SERVER = "stopped_by_server"
    NOT_ATTEMPTED = "not_attempted"
    HTTP_STATUS = "http_status"
    TOO_LARGE = "too_large"
    TRANSPORT_ERROR = "transport_error"


class FetchRecord(BaseModel):
    """One listed URL: what was asked, when, and what came back."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    outcome: FetchOutcome
    retrieved_at: AwareDatetime
    status: int | None = None
    sha256: str | None = None
    size: int | None = None
    location: str | None = None


class SourceKind(StrEnum):
    """The two source-policy pages a run reads besides the listed URLs."""

    ROBOTS_TXT = "robots_txt"
    WEBSITE_TERMS = "website_terms"


class SourceState(StrEnum):
    """What a source-policy page said (contract Amendment 1 point 8).

    Only ``published`` stores bytes. ``absent`` (a 4xx other than 403/429) is
    "no page published", never evidence for a policy.
    """

    PUBLISHED = "published"
    ABSENT = "absent"
    REFUSED = "refused"
    UNREADABLE = "unreadable"


class Hop(BaseModel):
    """One response on the way to a source-policy page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    status: int
    location: str | None = None


class SourceEvidence(BaseModel):
    """Where a source-policy page was read, what came back, and its stored digest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SourceKind
    requested_url: str
    hops: tuple[Hop, ...]
    state: SourceState
    status: int | None
    retrieved_at: AwareDatetime
    sha256: str | None = None
    size: int | None = None

    @model_validator(mode="after")
    def _bytes_only_when_published(self) -> "SourceEvidence":
        stored = self.sha256 is not None and self.size is not None
        bare = self.sha256 is None and self.size is None
        if (self.state is SourceState.PUBLISHED and not stored) or (
            self.state is not SourceState.PUBLISHED and not bare
        ):
            raise ValueError("a digest and size belong to published evidence only")
        return self


class FetchRun(BaseModel):
    """The whole run: the robots.txt it obeyed, the terms it read, every URL.

    ``robots`` and ``terms`` are the current source proof a standing policy is
    compared against; ``terms`` is ``None`` when no ``--terms-url`` was given.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    record_type: Literal["regjeringen_fetch_run"] = "regjeringen_fetch_run"
    user_agent: str
    started_at: AwareDatetime
    robots: SourceEvidence
    terms: SourceEvidence | None
    delay_seconds: float
    records: tuple[FetchRecord, ...]


def all_fetched(run: FetchRun) -> bool:
    """Whether every listed URL produced a stored page."""
    return all(record.outcome is FetchOutcome.FETCHED for record in run.records)


def evidence_file_name(evidence: SourceEvidence) -> str | None:
    """The stored file of published evidence, named by kind and digest."""
    if evidence.sha256 is None:
        return None
    if evidence.kind is SourceKind.ROBOTS_TXT:
        return f"robots-{evidence.sha256}.txt"
    return f"terms-{evidence.sha256}.html"


def run_file_name(started_at: datetime) -> str:
    """The run record's file name: sortable, and unique per second of start."""
    return f"fetch-run-{started_at.strftime('%Y%m%dT%H%M%S%z')}.json"
