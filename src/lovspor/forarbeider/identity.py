"""What a regjeringen.no URL and an official reference prove about a document.

The numeric ``idNNN`` of the URL is the upstream identity. The typed reference
is read from the slug and again from the page, and :mod:`lovspor.forarbeider.page`
requires the two to agree; nothing here derives an identity from a title.
"""

import re
from typing import Literal
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason

ALLOWED_HOST = "www.regjeringen.no"
DocumentType = Literal["prop-l", "prop-ls"]

_DOCUMENT_PATH = re.compile(r"/no/dokumenter/(?P<slug>[^/]+)/id(?P<id>[1-9]\d*)/")
_SLUG = re.compile(
    r"prop\.-(?P<number>[1-9]\d*)-(?P<letter>ls|l)-(?P<first>\d{4})(?P<second>\d{4})"
)
_REFERENCE = re.compile(
    r"Prop\. (?P<number>[1-9]\d*) (?P<letter>[A-Z]+) \((?P<first>\d{4})\u2013(?P<second>\d{4})\)"
)
_CHAPTER_QUERY = re.compile(r"ch=(?P<chapter>[1-9]\d*)")
_DOCUMENT_TYPES: dict[str, DocumentType] = {"l": "prop-l", "ls": "prop-ls"}


class OfficialReference(BaseModel):
    """A reference such as Prop. 54 LS, as ``(document_type, session, proposition_number)``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_type: DocumentType
    session: str
    proposition_number: int


class SourceLocator(BaseModel):
    """One allowlisted page of one proposition; ``chapter`` is ``None`` on the landing page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    document_id: int
    reference: OfficialReference
    chapter: int | None

    @property
    def canonical_url(self) -> str:
        return f"https://{ALLOWED_HOST}/no/dokumenter/{self.slug}/id{self.document_id}/"

    @property
    def page_url(self) -> str:
        suffix = "" if self.chapter is None else f"?ch={self.chapter}"
        return self.canonical_url + suffix


def parse_source_url(url: str) -> SourceLocator:
    """The locator ``url`` proves, refusing anything but an exact document URL.

    The closing comparison catches what ``urlsplit`` quietly strips, such as
    a trailing newline.

    Raises:
        ForarbeiderRefusedError: wrong host, scheme, path, query or type.
    """
    path, query = _allowlisted_parts(url)
    document = _DOCUMENT_PATH.fullmatch(path)
    if document is None:
        raise ForarbeiderRefusedError(RefusalReason.INVALID_SOURCE_URL, url)
    locator = SourceLocator(
        slug=document["slug"],
        document_id=int(document["id"]),
        reference=_reference_from_slug(document["slug"], url),
        chapter=_chapter_from_query(query),
    )
    if locator.page_url != url:
        raise ForarbeiderRefusedError(RefusalReason.INVALID_SOURCE_URL, url)
    return locator


def parse_reference(text: str) -> OfficialReference:
    """The reference a heading or a metadata tag states, whole or not at all.

    Raises:
        ForarbeiderRefusedError: not a reference, or not a Prop. L / LS.
    """
    match = _REFERENCE.fullmatch(text)
    if match is None:
        raise ForarbeiderRefusedError(RefusalReason.REFERENCE_MISSING, "official reference")
    document_type = _DOCUMENT_TYPES.get(match["letter"].lower())
    if document_type is None:
        raise ForarbeiderRefusedError(RefusalReason.UNSUPPORTED_DOCUMENT_TYPE, match["letter"])
    return OfficialReference(
        document_type=document_type,
        session=_session(match["first"], match["second"]),
        proposition_number=int(match["number"]),
    )


def chapter_of(href: str, parent: SourceLocator) -> tuple[int, str]:
    """The number and exact URL of a chapter link, which must be a chapter of ``parent``.

    Raises:
        ForarbeiderRefusedError: the link is anything else, ``chapter_foreign``.
    """
    foreign = ForarbeiderRefusedError(RefusalReason.CHAPTER_FOREIGN, href)
    try:
        locator = parse_source_url(urljoin(parent.canonical_url, href))
    except ForarbeiderRefusedError as error:
        raise foreign from error
    if locator.canonical_url != parent.canonical_url or locator.chapter is None:
        raise foreign
    return locator.chapter, locator.page_url


def _allowlisted_parts(url: str) -> tuple[str, str]:
    try:
        parts = urlsplit(url)
    except ValueError as error:
        raise ForarbeiderRefusedError(RefusalReason.INVALID_SOURCE_URL, url) from error
    if parts.netloc != ALLOWED_HOST:
        raise ForarbeiderRefusedError(RefusalReason.HOST_NOT_ALLOWED, parts.netloc)
    if parts.scheme != "https" or parts.fragment:
        raise ForarbeiderRefusedError(RefusalReason.INVALID_SOURCE_URL, url)
    return parts.path, parts.query


def _reference_from_slug(slug: str, url: str) -> OfficialReference:
    match = _SLUG.fullmatch(slug)
    if match is None:
        raise ForarbeiderRefusedError(RefusalReason.UNSUPPORTED_DOCUMENT_TYPE, slug)
    try:
        session = _session(match["first"], match["second"])
    except ForarbeiderRefusedError as error:
        raise ForarbeiderRefusedError(RefusalReason.INVALID_SOURCE_URL, url) from error
    return OfficialReference(
        document_type=_DOCUMENT_TYPES[match["letter"]],
        session=session,
        proposition_number=int(match["number"]),
    )


def _chapter_from_query(query: str) -> int | None:
    if not query:
        return None
    match = _CHAPTER_QUERY.fullmatch(query)
    if match is None:
        raise ForarbeiderRefusedError(RefusalReason.UNEXPECTED_QUERY, query)
    return int(match["chapter"])


def _session(first: str, second: str) -> str:
    if int(second) != int(first) + 1:
        raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, f"{first}-{second}")
    return f"{first}-{second}"
