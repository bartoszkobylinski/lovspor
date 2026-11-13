"""What one explicit-list fetch run did, URL by URL (#738).

Schema 3 (FC1a) adds, for every stored HTML page (a listed page or the terms
page, never robots.txt), a ``normalized_sha256`` and the ``normalization_rule``
that produced it (:mod:`lovspor.regjeringen_fetch.normalize`). It is a
comparison value only: the raw ``sha256`` names the stored file and is what
every check and comparison uses. Schema 2 records carry neither field and are
read as written; nothing is filled in for them.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

NormalizedDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
# The rules a reader accepts; ``normalize.NORMALIZATION_RULE`` is the one written.
NormalizationRule = Literal["regjeringen-csp-nonce/1"]
_SCHEMA_BEFORE_NORMALIZATION = 2


class _Normalized(BaseModel):
    """The optional normalized pair: both present or both absent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    normalized_sha256: NormalizedDigest | None = None
    normalization_rule: NormalizationRule | None = None

    @property
    def normalized(self) -> bool:
        """Whether this page carries a normalized digest."""
        return self.normalized_sha256 is not None

    @model_validator(mode="after")
    def _pair(self) -> "_Normalized":
        if (self.normalized_sha256 is None) != (self.normalization_rule is None):
            raise ValueError("normalized_sha256 and normalization_rule come together")
        return self


class FetchOutcome(StrEnum):
    """Every way one listed URL can end. Only ``fetched`` stores a page."""

    FETCHED = "fetched"
    ROBOTS_DISALLOWED = "robots_disallowed"
    REDIRECT_NOT_FOLLOWED = "redirect_not_followed"
    STOPPED_BY_SERVER = "stopped_by_server"
    NOT_ATTEMPTED = "not_attempted"
    HTTP_STATUS = "http_status"
    TOO_LARGE = "too_large"
    CONTENT_ENCODED = "content_encoded"
    TRANSPORT_ERROR = "transport_error"


class FetchRecord(_Normalized):
    """One listed URL: what was asked, when, and what came back."""

    url: str
    outcome: FetchOutcome
    retrieved_at: AwareDatetime
    status: int | None = None
    sha256: str | None = None
    size: int | None = None
    location: str | None = None

    @model_validator(mode="after")
    def _normalized_only_when_stored(self) -> "FetchRecord":
        if self.normalized and self.sha256 is None:
            raise ValueError("a normalized digest belongs to a stored page only")
        return self


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


class SourceEvidence(_Normalized):
    """Where a source-policy page was read, what came back, and its stored digest."""

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
        if self.normalized and self.kind is SourceKind.ROBOTS_TXT:
            raise ValueError("robots.txt is compared raw and has no normalized digest")
        if self.normalized and not stored:
            raise ValueError("a normalized digest belongs to stored evidence only")
        return self


class FetchRun(BaseModel):
    """The whole run: the robots.txt it obeyed, the terms it read, every URL.

    ``robots`` and ``terms`` are the current source proof a standing policy is
    compared against; ``terms`` is ``None`` when no ``--terms-url`` was given.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2, 3] = 3
    record_type: Literal["regjeringen_fetch_run"] = "regjeringen_fetch_run"
    user_agent: str
    started_at: AwareDatetime
    robots: SourceEvidence
    terms: SourceEvidence | None
    delay_seconds: float
    records: tuple[FetchRecord, ...]

    @model_validator(mode="after")
    def _normalized_by_schema(self) -> "FetchRun":
        """Schema 2: no page carries a normalized digest. Schema 3: every stored HTML page does."""
        html: list[_Normalized] = [record for record in self.records if record.sha256 is not None]
        if self.terms is not None and self.terms.sha256 is not None:
            html.append(self.terms)
        normalized = [page.normalized for page in html]
        if self.schema_version == _SCHEMA_BEFORE_NORMALIZATION:
            if any(normalized):
                raise ValueError("schema 2 records carry no normalized digest")
        elif not all(normalized):
            raise ValueError("schema 3 records a normalized digest for every stored HTML page")
        return self


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
