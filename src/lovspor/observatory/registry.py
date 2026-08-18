"""Source registry: eligibility, activation, and the access-policy record.

ADR-0010 §4 separates two things that are easy to conflate. An official
municipal or fylkeskommune site is an **eligible** capture source — being
public makes it a candidate. **Activating** it requires a recorded per-source
check of ``robots.txt``, site terms and crawl constraints, and that record is
kept beside the source rather than in an operator's memory.

Permission to fetch is still not permission to redistribute. Nothing here
grants the latter; ADR-0010 §5 and §6 keep republication behind a separate
per-source licensing basis that no code in this package can satisfy.

Authorities are keyed by their official identifier, never by name: names are
spelled inconsistently, change, and collide across administrative levels.
"""

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import ParseError, SourceNotActivatedError
from lovspor.observatory.model import AuthorityType, require_utc

REGISTRY_VERSION = 1


class AccessPolicyCheck(BaseModel):
    """The recorded outcome of checking whether a source may be crawled.

    ``reviewed_by`` is mandatory and free text: someone has to have looked,
    and an unattributed check is not evidence that anyone did.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    checked_at: datetime
    robots_txt_url: str = Field(min_length=1)
    robots_allows: bool
    terms_reviewed: bool
    terms_url: str | None = None
    rate_limit_seconds: float = Field(gt=0)
    user_agent: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)
    note: str = ""

    @field_validator("checked_at")
    @classmethod
    def _utc_checked_at(cls, value: datetime) -> datetime:
        return require_utc(value)

    @property
    def permits_capture(self) -> bool:
        """Whether this check clears the source for capture."""
        return self.robots_allows and self.terms_reviewed


class SourceRecord(BaseModel):
    """One authority as a capture source, with its activation state."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    authority_type: AuthorityType
    authority_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    canonical_domain: str = Field(min_length=1)
    access_policy: AccessPolicyCheck | None = None
    active: bool = False

    @model_validator(mode="after")
    def _active_requires_clearance(self) -> "SourceRecord":
        """Refuse an active source whose access-policy record does not clear it.

        The gate lives in the type, not in the caller: a record loaded from a
        hand-edited registry file gets the same check as one built in code, so
        activation cannot be granted by editing JSON.
        """
        if self.active and (self.access_policy is None or not self.access_policy.permits_capture):
            raise ValueError(
                f"source {self.authority_id} is active without an access-policy check "
                "that permits capture (ADR-0010 §4)",
            )
        return self


class SourceRegistry(BaseModel):
    """All known sources, keyed by official authority identifier."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    version: int = REGISTRY_VERSION
    sources: dict[str, SourceRecord] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _keys_match_records(self) -> "SourceRegistry":
        for key, record in self.sources.items():
            if key != record.authority_id:
                raise ValueError(
                    f"registry key {key!r} does not match authority_id {record.authority_id!r}"
                )
        return self

    def active(self) -> tuple[SourceRecord, ...]:
        """Return the activated sources, ordered by identifier."""
        return tuple(record for _, record in sorted(self.sources.items()) if record.active)


def activate(source: SourceRecord, check: AccessPolicyCheck) -> SourceRecord:
    """Attach an access-policy check and activate the source.

    Raises:
        SourceNotActivatedError: the check does not permit capture — either
            ``robots.txt`` disallows it or the site terms were never reviewed.
            Refused here rather than downgraded to a warning, because the next
            step after activation is traffic against someone else's server.
    """
    if not check.permits_capture:
        raise SourceNotActivatedError(
            f"access-policy check for {source.authority_id} does not permit capture "
            f"(robots_allows={check.robots_allows}, terms_reviewed={check.terms_reviewed})",
        )
    data = source.model_dump()
    data.update(access_policy=check.model_dump(), active=True)
    return SourceRecord.model_validate(data)


def require_active(registry: SourceRegistry, authority_id: str) -> SourceRecord:
    """Return a source that is cleared for capture, or refuse.

    The single gate every fetcher must pass through, so that "was this source
    allowed?" has one answer in one place.
    """
    record = registry.sources.get(authority_id)
    if record is None:
        raise SourceNotActivatedError(f"authority {authority_id} is not in the source registry")
    if not record.active:
        raise SourceNotActivatedError(
            f"authority {authority_id} has no recorded access-policy check permitting capture",
        )
    return record


def read_registry(path: Path) -> SourceRegistry:
    """Load and validate a registry file.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        ParseError: the file is not valid UTF-8 JSON, or does not match the
            registry schema — including an active source without clearance.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParseError(f"{path}: unreadable source registry: {exc}") from exc
    try:
        return SourceRegistry.model_validate(data)
    except ValidationError as exc:
        raise ParseError(f"{path}: invalid source registry: {exc}") from exc


def write_registry(registry: SourceRegistry, path: Path) -> None:
    """Write a registry deterministically: sorted keys, two-space indent, UTF-8."""
    text = json.dumps(
        registry.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False
    )
    atomic_write_text(path, text + "\n")
