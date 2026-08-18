"""Source registry: eligibility, activation, and the access-policy record.

ADR-0010 §4 separates two things that are easy to conflate. An official
municipal or fylkeskommune site is an **eligible** capture source — being
public makes it a candidate. **Activating** it requires a recorded per-source
check of ``robots.txt``, site terms and crawl constraints, and that record
carries the *outcome* of the check, not merely evidence that someone looked.

Permission to fetch is still not permission to redistribute. Nothing here
grants the latter; ADR-0010 §5 and §6 keep republication behind a separate
per-source licensing basis that no code in this package can satisfy.

The capture gate takes the URL about to be fetched, not an authority
identifier. Clearing an authority and then fetching some other host would
otherwise pass the gate — including a host the ADR forbids outright.

Authorities are keyed by their official identifier, never by name: names are
spelled inconsistently, change, and collide across administrative levels.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import ParseError, SourceNotActivatedError
from lovspor.observatory.model import AuthorityType, require_utc
from lovspor.observatory.storage import ObservatoryRoot

REGISTRY_VERSION: Literal[1] = 1
REGISTRY_FILENAME = "sources.json"

# ADR-0010 §4 forbids crawling or mass-downloading lovdata.no outright: its
# terms prohibit massenedlasting, and the canonical corpus depends on that
# relationship staying intact. The deny is central rather than per-source, so
# registering the host — by mistake or by expedience — cannot unlock it.
GLOBALLY_DENIED_HOSTS = frozenset({"lovdata.no"})


def _host_matches(host: str, domain: str) -> bool:
    """True when ``host`` is ``domain`` or a subdomain of it.

    Compared label-wise, never as a string suffix: ``notbaerum.no`` must not
    match ``baerum.no``, and ``baerum.no.evil.example`` must not match either.
    """
    host = host.lower().rstrip(".")
    domain = domain.lower().rstrip(".")
    return host == domain or host.endswith(f".{domain}")


class AccessPolicyCheck(BaseModel):
    """The recorded outcome of checking whether a source may be crawled.

    ``terms_reviewed`` and ``terms_permit_capture`` are deliberately separate
    fields. The first says a human read the site's terms; the second says what
    they concluded. Collapsing them would let "I read the terms and they
    prohibit automated access" clear a source for crawling, which is the
    opposite of what the review was for.

    ``reviewed_by`` is mandatory and free text: someone has to have looked,
    and an unattributed check is not evidence that anyone did.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    checked_at: datetime
    robots_txt_url: str = Field(min_length=1)
    robots_allows: bool
    terms_reviewed: bool
    terms_permit_capture: bool
    terms_url: str | None = None
    rate_limit_seconds: float = Field(gt=0)
    user_agent: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)
    note: str = ""

    @field_validator("checked_at")
    @classmethod
    def _utc_checked_at(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _conclusion_requires_a_review(self) -> "AccessPolicyCheck":
        """A verdict on the terms cannot exist without the review that produced it."""
        if self.terms_permit_capture and not self.terms_reviewed:
            raise ValueError("terms_permit_capture cannot be true when terms_reviewed is false")
        return self

    @property
    def permits_capture(self) -> bool:
        """Whether this check clears the source for capture."""
        return self.robots_allows and self.terms_reviewed and self.terms_permit_capture


class SourceRecord(BaseModel):
    """One authority as a capture source, with its activation state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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

    def covers(self, host: str) -> bool:
        """Whether this source's activation extends to ``host``."""
        return _host_matches(host, self.canonical_domain)


class SourceRegistry(BaseModel):
    """All known sources, keyed by official authority identifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = REGISTRY_VERSION
    sources: dict[str, SourceRecord] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _keys_match_records(self) -> "SourceRegistry":
        for key, record in self.sources.items():
            if key != record.authority_id:
                raise ValueError(
                    f"registry key {key!r} does not match authority_id {record.authority_id!r}",
                )
        return self

    def active(self) -> tuple[SourceRecord, ...]:
        """Return the activated sources, ordered by identifier."""
        return tuple(record for _, record in sorted(self.sources.items()) if record.active)


def activate(source: SourceRecord, check: AccessPolicyCheck) -> SourceRecord:
    """Attach an access-policy check and activate the source.

    Raises:
        SourceNotActivatedError: the check does not permit capture — robots
            disallows it, the terms were never reviewed, or the review found
            they prohibit it. Refused here rather than downgraded to a
            warning, because the next step after activation is traffic against
            someone else's server.
    """
    if not check.permits_capture:
        raise SourceNotActivatedError(
            f"access-policy check for {source.authority_id} does not permit capture "
            f"(robots_allows={check.robots_allows}, terms_reviewed={check.terms_reviewed}, "
            f"terms_permit_capture={check.terms_permit_capture})",
        )
    data = source.model_dump()
    data.update(access_policy=check.model_dump(), active=True)
    return SourceRecord.model_validate(data)


def capture_host(url: str) -> str:
    """The host a capture URL targets, or refuse the URL outright.

    Two callers need this and both must fail the same way: the gate below, and
    the fetcher choosing a per-host rate-limit key. A caller that substituted a
    placeholder for a missing host would quietly pool every such URL into one
    bucket — and the placeholder would never be exercised, because the gate has
    already refused the URL by then.

    Raises:
        SourceNotActivatedError: the URL has no host.
    """
    host = urlsplit(url).hostname
    if host is None:
        raise SourceNotActivatedError(f"cannot authorise capture of {url!r}: no host")
    return host


def authorise_capture(registry: SourceRegistry, url: str) -> SourceRecord:
    """Return the activated source that covers ``url``, or refuse to fetch it.

    The single door every fetcher passes through, taking the target URL rather
    than an authority id: activation clears a *source*, and a source is a
    domain, not a licence to fetch anything on behalf of that authority.

    Raises:
        SourceNotActivatedError: the URL has no host, its host is globally
            denied, no registered source covers it, or the covering source has
            no access-policy check permitting capture.
    """
    host = capture_host(url)
    if any(_host_matches(host, denied) for denied in GLOBALLY_DENIED_HOSTS):
        raise SourceNotActivatedError(
            f"{host} is globally denied: ADR-0010 §4 forbids crawling or mass-downloading it",
        )
    covering = [record for _, record in sorted(registry.sources.items()) if record.covers(host)]
    if not covering:
        raise SourceNotActivatedError(f"no registered source covers {host}")
    active = [record for record in covering if record.active]
    if not active:
        raise SourceNotActivatedError(
            f"{host} has no recorded access-policy check permitting capture",
        )
    return active[0]


def read_registry(path: Path) -> SourceRegistry:
    """Load and validate a registry file.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        ParseError: the file is not valid UTF-8 JSON, does not match the
            registry schema — including an active source without clearance —
            or declares a schema version this engine does not read.
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


def registry_path(root: ObservatoryRoot) -> Path:
    """Where the source registry lives inside a validated observatory root.

    The registry holds no observed bytes, but it does hold the access-policy
    checks, and those belong with the archive they authorise rather than in a
    published repository. Taking an :class:`ObservatoryRoot` rather than a
    bare path means the ADR-0010 §5 boundary has already been checked.
    """
    return root.path / REGISTRY_FILENAME


def read_access_policy_check(path: Path) -> AccessPolicyCheck:
    """Load a reviewer's access-policy check from a JSON document.

    The check is a document rather than a handful of command-line flags on
    purpose. It records a human decision about someone else's server, it has
    to stay readable months later when the question is *why* a source was
    activated, and a conclusion typed into a shell leaves nothing to re-read.

    Validation is the model's, so a document claiming ``terms_permit_capture``
    without ``terms_reviewed`` is refused here exactly as it would be in code.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        ParseError: the file is not valid UTF-8 JSON or does not match the
            access-policy schema.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParseError(f"{path}: unreadable access-policy check: {exc}") from exc
    try:
        return AccessPolicyCheck.model_validate(data)
    except ValidationError as exc:
        raise ParseError(f"{path}: invalid access-policy check: {exc}") from exc
