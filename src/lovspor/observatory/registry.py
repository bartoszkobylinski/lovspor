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


def host_within_domain(host: str, domain: str) -> bool:
    """Public name for the domain test the capture gate already applies.

    Exposed so the redirect rule and the activation gate cannot drift apart:
    "inside the cleared domain" must mean exactly one thing (issue #158).
    """
    return _host_matches(host, domain)


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


#: What a completed investigation concluded about a source. Closed on purpose:
#: a free-text outcome cannot be counted, and `observatory status` has to count
#: these so a held source stays visible rather than quietly dropping out.
CaptureOutcome = Literal["no_machine_reachable_source", "access_blocked"]


class CaptureVerdict(BaseModel):
    """What was concluded about capturing a source, and on what evidence.

    The twin of :class:`AccessPolicyCheck`, and recorded for the same reason.
    That one carries a human's conclusion that a source *may* be fetched and
    has to answer "why was this activated?" months later. This one carries the
    conclusion that fetching it yields nothing, and answers "why does this one
    never produce anything?" — so that the next sweep does not re-derive it
    from scratch and reach the same silent zero (issue #195).

    ``routes_checked`` is mandatory and non-empty. A bare "unreachable" would
    have to be re-trusted by every later reader; the routes make the verdict
    re-readable instead, and a route that opens later is then a specific thing
    to re-test rather than a whole investigation to redo.

    ``recheck_after`` is mandatory because a verdict that never expires is
    exactly the silence this record exists to prevent, moved one level up: a
    source that stops being asked looks identical to a source that has nothing
    to say. The web changes, and a conclusion from a year ago is a different
    claim from one reached last week.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: CaptureOutcome
    routes_checked: tuple[str, ...] = Field(min_length=1)
    evidence: str = Field(min_length=1)
    reached_at: datetime
    reviewed_by: str = Field(min_length=1)
    recheck_after: datetime

    @field_validator("reached_at", "recheck_after")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _the_recheck_follows_the_verdict(self) -> "CaptureVerdict":
        """A re-check date at or before the verdict is already due on arrival,
        which records an expiry while granting none."""
        if self.recheck_after <= self.reached_at:
            raise ValueError(
                f"recheck_after {self.recheck_after.isoformat()} must be later than "
                f"reached_at {self.reached_at.isoformat()}"
            )
        return self

    def due(self, now: datetime) -> bool:
        """Whether this verdict has reached its re-check date."""
        return now >= self.recheck_after


class SourceRecord(BaseModel):
    """One authority as a capture source, with its activation state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_type: AuthorityType
    authority_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    canonical_domain: str = Field(min_length=1)
    #: Overview pages to read as a second entry into discovery (issue #151).
    #: Declared by a human at activation, one per listing, because "this URL is
    #: an index of documents" is a judgement about a page and not something to
    #: infer: guessing it would let an error page or a search form be walked as
    #: though it listed anything.
    #:
    #: Empty for every source that publishes a sitemap. It exists for the 116
    #: municipalities that publish none, where discovery currently has no entry
    #: at all and a capture is structurally a no-op.
    listing_entry_points: tuple[str, ...] = ()
    access_policy: AccessPolicyCheck | None = None
    #: What an investigation concluded about capturing this source (#195).
    #: Independent of ``active``: concluding that a source publishes nothing a
    #: machine can reach is not a withdrawal of permission to fetch it, and the
    #: re-check depends on the source still being activated.
    capture_verdict: CaptureVerdict | None = None
    active: bool = False

    @model_validator(mode="after")
    def _listings_stay_inside_the_cleared_domain(self) -> "SourceRecord":
        """A listing must live on the domain this source was cleared for.

        The access-policy check answers a question about one domain, so a
        listing on another host would be crawled under a clearance nobody gave
        for it. The fetcher's domain guard would refuse those pages anyway;
        refusing the record is better, because a registry that can hold an
        unreachable entry point invites someone to widen the guard instead.
        """
        outside = [url for url in self.listing_entry_points if not self._inside(url)]
        if outside:
            raise ValueError(
                f"source {self.authority_id} lists entry points outside "
                f"{self.canonical_domain}: {', '.join(outside)}"
            )
        return self

    def _inside(self, url: str) -> bool:
        host = urlsplit(url).hostname
        return host is not None and _host_matches(host, self.canonical_domain)

    @model_validator(mode="after")
    def _the_clearance_belongs_to_the_domain_it_was_given_for(self) -> "SourceRecord":
        """Refuse a record whose access-policy check answers about another domain.

        The check answers one question — may *this* domain be crawled, on
        these terms, at this rate — and the domain it answered for is the host
        of the ``robots.txt`` the reviewer read. Nothing tied the two together
        before, so swapping ``canonical_domain`` and leaving the check in
        place let a clearance obtained for ``haugesund.no`` authorise traffic
        to ``haugesund.kommune.no`` (issue #166). One field edited by hand,
        and the review that gates every request is answering about a server
        nobody looked at.

        Refused in the type, like every other clearance rule here, so a
        hand-edited registry gets the same answer as code: a domain change
        withdraws the clearance, and only a fresh review restores it.
        """
        policy = self.access_policy
        if policy is None:
            return self
        host = urlsplit(policy.robots_txt_url).hostname
        if host is None or not _host_matches(host, self.canonical_domain):
            raise ValueError(
                f"source {self.authority_id} carries an access-policy check performed "
                f"against {policy.robots_txt_url} , which is outside "
                f"{self.canonical_domain}; a domain change needs a fresh review "
                "(replace-source-domain, then activate-source)"
            )
        return self

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
    try:
        return SourceRecord.model_validate(data)
    except ValidationError as exc:
        # The record was valid on the way in and only the clearance changed,
        # so the one rule left to break is the domain binding: a check read
        # against some other host. That is an operator handing over the wrong
        # document, not a bug, and it belongs in the same refusal as a check
        # that does not permit capture (issue #166).
        raise SourceNotActivatedError(
            f"the access-policy check for {source.authority_id} was not performed for "
            f"{source.canonical_domain}: {exc}"
        ) from exc


def replace_domain(source: SourceRecord, domain: str) -> SourceRecord:
    """The same authority on a different domain, with its clearance withdrawn.

    One operation rather than three, because the three are not independently
    safe. The clearance, the activation and the entry points were all obtained
    for the old domain: a review of its terms, a decision to send traffic, and
    pages someone confirmed list documents. None of that transfers to a host
    nobody has looked at, and leaving any of it in place is what would let a
    migration quietly authorise the new domain (issue #166).

    The capture verdict goes for the same reason (issue #195): "this source
    publishes nothing a machine can reach" was concluded about the old host,
    from routes checked there. Carried across, it would hold a server nobody
    has looked at out of every sweep, under evidence that is not about it.

    So the record comes back inactive, with no policy, no entry points and no
    verdict, and the only route to capturing the new domain is a fresh
    ``activate-source`` on a fresh review — which is the point.

    Raises:
        SourceNotActivatedError: the domain is unchanged. A replacement that
            replaces nothing would withdraw a live clearance and report
            success, which is a worse outcome than refusing a typo.
    """
    if _host_matches(source.canonical_domain, domain) and _host_matches(
        domain, source.canonical_domain
    ):
        raise SourceNotActivatedError(
            f"source {source.authority_id} is already on {source.canonical_domain}"
        )
    data = source.model_dump()
    data.update(
        canonical_domain=domain,
        access_policy=None,
        active=False,
        listing_entry_points=(),
        capture_verdict=None,
    )
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


def read_capture_verdict(path: Path) -> CaptureVerdict:
    """Load a completed capture investigation from a JSON document.

    A document rather than flags, for the reason the access-policy check is
    one: it is the record of a human conclusion, it carries the routes that
    were checked, and it has to stay readable months later when the question
    is why a source never produces anything.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        ParseError: the file is not valid UTF-8 JSON or does not match the
            capture-verdict schema.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParseError(f"{path}: unreadable capture verdict: {exc}") from exc
    try:
        return CaptureVerdict.model_validate(data)
    except ValidationError as exc:
        raise ParseError(f"{path}: invalid capture verdict: {exc}") from exc
