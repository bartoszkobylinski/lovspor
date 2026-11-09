"""What one explicit-list fetch run did, URL by URL (#738)."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict


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


class FetchRun(BaseModel):
    """The whole run: the robots.txt it obeyed, the spacing it kept, every URL.

    ``robots_sha256`` is the digest of the robots.txt read live at the start, so
    a standing policy can tell when the rules it was signed against changed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    record_type: Literal["regjeringen_fetch_run"] = "regjeringen_fetch_run"
    user_agent: str
    started_at: AwareDatetime
    robots_url: str
    robots_status: int
    robots_sha256: str
    delay_seconds: float
    records: tuple[FetchRecord, ...]


def all_fetched(run: FetchRun) -> bool:
    """Whether every listed URL produced a stored page."""
    return all(record.outcome is FetchOutcome.FETCHED for record in run.records)


def run_file_name(started_at: datetime) -> str:
    """The run record's file name: sortable, and unique per second of start."""
    return f"fetch-run-{started_at.strftime('%Y%m%dT%H%M%S%z')}.json"
