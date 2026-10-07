"""The benchmark publication manifest (ADR-0014 Decision 4, ADR:706).

LLHB results reach the site only through ``benchmarks/llhb/PUBLICATION.json``,
authored and approved by the benchmark owner. Each entry names one source
file under ``benchmarks/llhb/``, the exact field of every value it publishes,
the epistemic label to show beside it, the ``DECISIONS.md`` ruling it derives
from and its display wording. The builder opens the manifest and the files it
names, and nothing else under ``results/``: a new or re-scored report changes
nothing on the site until the manifest changes in a reviewed PR. No manifest,
no benchmark section (ADR:2953-2956).

ADR-0014 fixes what an entry holds, not its JSON shape; the shape below is
this implementation's. Every value becomes a ``code`` fact — the artifact is
the repository-relative source path at ``lovspor_commit``, the field its
dotted path — so it is ledgered and hashed like every other fact. Formats:

* ``count`` — a whole number (reports store counts as ``42.0``), rendered as an int.
* ``percent`` — a rate in ``[0, 1]``, published as the field times 100, rounded
  half-up to one decimal; the ledger's value is that percentage.
* ``text`` — a string, verbatim.

An entry may also carry ``requires``: bounds on fields its wording depends on
("the accuracy gain holds across repeats"). A bound that no longer holds
fails the build instead of leaving a sentence the data has stopped supporting.
"""

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from lovspor.site.errors import SiteBuildError
from lovspor.site.facts import FactSource, FactValue, Lang

PUBLICATION_PATH = "benchmarks/llhb/PUBLICATION.json"
SOURCE_ROOT = PurePosixPath("benchmarks/llhb")

ValueFormat = Literal["count", "percent", "text"]
Name = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$")]
Text = Annotated[str, StringConstraints(min_length=1)]
_ONE_DECIMAL = Decimal("0.1")


class _Closed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Wording(_Closed):
    nb: Text
    en: Text

    def text(self, lang: Lang) -> str:
        return self.nb if lang == "nb" else self.en


class ManifestValue(_Closed):
    field: Text
    format: ValueFormat


class Requirement(_Closed):
    """One bound a field must keep for the entry's wording to stay true."""

    field: Text
    below: float | None = None
    above: float | None = None

    @model_validator(mode="after")
    def _one_bound(self) -> Self:
        if (self.below is None) == (self.above is None):
            raise ValueError("a requirement names exactly one of below and above")
        return self

    def holds(self, value: float) -> bool:
        if self.below is not None:
            return value < self.below
        return self.above is not None and value > self.above


def _inside_benchmark_tree(source: str) -> bool:
    path = PurePosixPath(source)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and path.parts[: len(SOURCE_ROOT.parts)] == SOURCE_ROOT.parts
        and source != PUBLICATION_PATH
    )


class Entry(_Closed):
    id: Name
    source: Text
    values: Annotated[dict[Name, ManifestValue], Field(min_length=1)]
    requires: tuple[Requirement, ...] = ()
    label: Wording
    ruling: Text
    wording: Wording

    @model_validator(mode="after")
    def _source_in_tree(self) -> Self:
        if not _inside_benchmark_tree(self.source):
            raise ValueError(f"source {self.source!r} is not a file under {SOURCE_ROOT}/")
        return self


class PublicationManifest(_Closed):
    schema_version: Literal["1"]
    benchmark: Text
    approved_by_role: Text
    approved_on: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    entries: Annotated[tuple[Entry, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _ids_unique(self) -> Self:
        ids = [entry.id for entry in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("an entry id is listed twice")
        return self


def fact_id(entry_id: str, name: str) -> str:
    return f"llhb.{entry_id}.{name}"


class Publication(_Closed):
    """The manifest as read, every file it named with its hash, and its facts."""

    manifest: PublicationManifest
    manifest_sha256: str
    source_hashes: tuple[tuple[str, str], ...]
    facts: tuple[FactSource, ...]

    def artifact_hashes(self) -> tuple[tuple[str, str], ...]:
        return ((PUBLICATION_PATH, self.manifest_sha256), *self.source_hashes)

    def context(self, lang: Lang) -> dict[str, dict[str, object]]:
        """What a template needs per entry: wording, label, ruling and fact ids."""
        return {
            entry.id: {
                "wording": entry.wording.text(lang),
                "label": entry.label.text(lang),
                "ruling": entry.ruling,
                "ids": {name: fact_id(entry.id, name) for name in entry.values},
            }
            for entry in self.manifest.entries
        }


def _field(document: object, path: str, where: str) -> object:
    value = document
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            raise SiteBuildError(f"{where}: field {path!r} is not in the source")
        value = value[key]
    return value


def _is_number(raw: object) -> bool:
    return isinstance(raw, int | float) and not isinstance(raw, bool)


def _formatted(raw: object, value_format: ValueFormat, where: str) -> FactValue:
    if value_format == "text":
        if not isinstance(raw, str):
            raise SiteBuildError(f"{where}: {raw!r} is not text")
        return raw
    if value_format == "count":
        if not _is_number(raw) or not float(str(raw)).is_integer():
            raise SiteBuildError(f"{where}: {raw!r} is not a whole count")
        return int(float(str(raw)))
    if not _is_number(raw) or not 0 <= float(str(raw)) <= 1:
        raise SiteBuildError(f"{where}: {raw!r} is not a rate")
    percent = (Decimal(str(raw)) * 100).quantize(_ONE_DECIMAL, rounding=ROUND_HALF_UP)
    return float(percent)


def _read(checkout: Path, relative: str) -> bytes:
    try:
        return (checkout / relative).read_bytes()
    except OSError as error:
        raise SiteBuildError(f"cannot read {relative}: {error}") from error


def _entry_facts(entry: Entry, document: object) -> tuple[FactSource, ...]:
    for requirement in entry.requires:
        raw = _field(document, requirement.field, entry.source)
        if not _is_number(raw) or not requirement.holds(float(str(raw))):
            raise SiteBuildError(
                f"{entry.source}: {requirement.field} = {raw!r} no longer supports "
                f"the wording of entry {entry.id!r}"
            )
    return tuple(
        FactSource(
            id=fact_id(entry.id, name),
            kind="code",
            artifact=entry.source,
            field=value.field,
            value=_formatted(_field(document, value.field, entry.source), value.format, name),
        )
        for name, value in entry.values.items()
    )


def _parse_manifest(raw: bytes) -> PublicationManifest:
    try:
        return PublicationManifest.model_validate_json(raw)
    except ValueError as error:
        raise SiteBuildError(
            f"{PUBLICATION_PATH} is not a publication manifest: {error}"
        ) from error


def _parse_source(raw: bytes, source: str) -> object:
    try:
        return json.loads(raw)
    except ValueError as error:
        raise SiteBuildError(f"{source} is not JSON: {error}") from error


def load_publication(checkout: Path) -> Publication | None:
    """The approved benchmark values, or ``None`` when no manifest exists."""
    if not (checkout / PUBLICATION_PATH).is_file():
        return None
    manifest_bytes = _read(checkout, PUBLICATION_PATH)
    manifest = _parse_manifest(manifest_bytes)
    hashes: dict[str, str] = {}
    facts: list[FactSource] = []
    for entry in manifest.entries:
        raw = _read(checkout, entry.source)
        hashes[entry.source] = hashlib.sha256(raw).hexdigest()
        facts.extend(_entry_facts(entry, _parse_source(raw, entry.source)))
    return Publication(
        manifest=manifest,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        source_hashes=tuple(sorted(hashes.items())),
        facts=tuple(facts),
    )
