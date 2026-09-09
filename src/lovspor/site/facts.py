"""Fact sources, kinds and the ledger (ADR-0014 Decision 4).

No number or status describing Lovspor's state is typed into a template:
each is read at build time from a named artifact, carries its **kind**,
and is recorded in a ledger — one entry per rendered value: page,
artifact identity, field, kind, value (ADR:1085-1119). Templates reach
facts only through the ``fact(id, kind=...)`` callable this module
builds per page (ADR:1123-1128).

Kinds. The ADR names ``code`` and ``hosted`` (ADR:723) and three artifact
classes a ledger entry may name (ADR:2412-2415): a repository-relative
path at ``lovspor_commit`` or the tool-surface descriptor, the
release-relative ``corpus/site-manifest.json``, and
``deployment-capabilities.json``. ``FactKind`` therefore has a third
value, ``corpus``, for the manifest's fields: a document count read from
the corpus release is neither a claim about the checkout's code nor an
observation of the hosted endpoint, and labelling it ``code`` would
mislabel it. This is an implementation reading of Decision 4's artifact
classes, recorded here; the artifact class is enforced per kind — a
``hosted`` fact names ``deployment-capabilities.json`` and nothing else,
a ``corpus`` fact names the manifest, a ``code`` fact a checkout path or
``tool-surface@<commit>`` — so a ``hosted`` claim from any other artifact
cannot be constructed (ADR:747-752).

A template that asks for a fact under the wrong kind has a *kind
mismatch*; that fails the build, because it is a defect in repository
content. A ``hosted`` fact whose record is ``unobserved`` renders its
degraded wording in the page language — *not attested at this release —
unobserved (reason), observed <observed_at>* — never the previous value,
never blank, never zero (ADR:940-948); it is ledgered like any other.

Every rendered value is HTML-escaped at this boundary (Decision 3) and
wrapped in ``<span data-fact data-kind>`` so the post-render numeral scan
can tell a ledgered number from a typed one.
"""

import re
from collections.abc import Callable
from typing import Annotated, Literal, Self

from markupsafe import Markup
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from lovspor.site.errors import SiteBuildError

FactKind = Literal["code", "corpus", "hosted"]
Lang = Literal["nb", "en"]
FactValue = str | int | bool

CAPABILITIES_ARTIFACT = "deployment-capabilities.json"
MANIFEST_ARTIFACT = "corpus/site-manifest.json"
DESCRIPTOR_PREFIX = "tool-surface@"

_DEGRADED: dict[Lang, str] = {
    "nb": "ikke attestert ved denne utgivelsen — uobservert ({reason}), observert {observed_at}",
    "en": "not attested at this release — unobserved ({reason}), observed {observed_at}",
}
_THOUSANDS: dict[Lang, str] = {"nb": "\u00a0", "en": ","}
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SPAN = Markup('<span data-fact="{id}" data-kind="{kind}">{text}</span>')


class KindMismatchError(SiteBuildError):
    """A template rendered a fact under a kind other than its source's.

    A hosted value under a code label, a code value under a hosted label:
    the page would blur what the build exists to keep apart (ADR:747-752).
    """


class Unobserved(BaseModel):
    """Why and when a hosted record could not be observed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str
    observed_at: str


def _artifact_is_of_kind(kind: FactKind, artifact: str) -> bool:
    if kind == "hosted":
        return artifact == CAPABILITIES_ARTIFACT
    if kind == "corpus":
        return artifact == MANIFEST_ARTIFACT
    if artifact.startswith(DESCRIPTOR_PREFIX):
        return _is_commit(artifact.removeprefix(DESCRIPTOR_PREFIX))
    return (
        artifact not in {CAPABILITIES_ARTIFACT, MANIFEST_ARTIFACT}
        and not artifact.startswith("/")
        and ".." not in artifact.split("/")
    )


def _is_commit(text: str) -> bool:
    return _COMMIT.match(text) is not None


class FactSource(BaseModel):
    """One fact: what it says, which artifact and field it was read from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Annotated[str, StringConstraints(min_length=1)]
    kind: FactKind
    artifact: Annotated[str, StringConstraints(min_length=1)]
    field: Annotated[str, StringConstraints(min_length=1)]
    value: FactValue | None = None
    unobserved: Unobserved | None = None

    @model_validator(mode="after")
    def _one_reading(self) -> Self:
        if not _artifact_is_of_kind(self.kind, self.artifact):
            raise ValueError(f"a {self.kind} fact cannot read {self.artifact!r}")
        if (self.value is None) == (self.unobserved is None):
            raise ValueError("a fact has a value or an unobserved record, never both or neither")
        if self.unobserved is not None and self.kind != "hosted":
            raise ValueError("only a hosted fact can be unobserved")
        return self


class FactRegistry(BaseModel):
    """Every fact a build may render, keyed by id; the registry decides the kind."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: tuple[FactSource, ...]

    @model_validator(mode="after")
    def _ids_unique(self) -> Self:
        seen: set[str] = set()
        for source in self.sources:
            if source.id in seen:
                raise ValueError(f"fact id {source.id!r} is registered twice")
            seen.add(source.id)
        return self

    def get(self, fact_id: str) -> FactSource:
        for source in self.sources:
            if source.id == fact_id:
                return source
        raise SiteBuildError(f"unknown fact {fact_id!r}")


class LedgerEntry(BaseModel):
    """One rendered value: page, artifact identity, field, kind, value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page: str
    artifact: str
    field: str
    kind: FactKind
    value: FactValue | None
    unobserved: Unobserved | None


class FactLedger:
    """The values a build rendered, in render order, one entry per distinct value."""

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    def record(self, page: str, source: FactSource) -> None:
        entry = LedgerEntry(
            page=page,
            artifact=source.artifact,
            field=source.field,
            kind=source.kind,
            value=source.value,
            unobserved=source.unobserved,
        )
        if entry not in self._entries:
            self._entries.append(entry)


def _format_value(value: FactValue, lang: Lang) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:,}".replace(",", _THOUSANDS[lang])
    return value


def _wording(source: FactSource, lang: Lang) -> str:
    if source.unobserved is not None:
        return _DEGRADED[lang].format(
            reason=source.unobserved.reason, observed_at=source.unobserved.observed_at
        )
    if source.value is None:  # pragma: no cover - excluded by FactSource's validator
        raise SiteBuildError(f"fact {source.id!r} has no value")
    return _format_value(source.value, lang)


def fact_renderer(
    page: str, lang: Lang, registry: FactRegistry, ledger: FactLedger
) -> Callable[..., Markup]:
    """The per-page ``fact(id, *, kind)`` a template renders values through."""

    def fact(fact_id: str, *, kind: FactKind) -> Markup:
        source = registry.get(fact_id)
        if source.kind != kind:
            raise KindMismatchError(
                f"{page}: fact {fact_id!r} is {source.kind}, rendered as {kind}"
            )
        ledger.record(page, source)
        return _SPAN.format(id=source.id, kind=source.kind, text=_wording(source, lang))

    return fact
