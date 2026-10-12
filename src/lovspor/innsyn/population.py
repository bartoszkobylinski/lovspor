"""The population of local authorities, read from SSB KLASS at a date (issue #509).

Every kommune and every fylkeskommune is asked, not only those whose pages
the observatory saw linking an LF id. The list is SSB's, not ours: KLASS
classification 131 (Standard for kommuneinndeling) and 127 (Standard for
fylkeskommune), each read with ``codesAt`` for one date. No count is pinned
anywhere: the snapshot is the answer, with the URLs it was read from and when.

Two kinds of code are not authorities and are kept as ``excluded``, with the
reason, rather than dropped silently:

* fylke 99 (``9999``, ``9900``) is SSB's "Uoppgitt" (unspecified);
* fylkeskommune ``0300`` is Oslo, which KLASS itself notes is both kommune and
  fylkeskommune; it is asked once, as kommune ``0301``.

A fylkeskommune's authority id is its fylkesnummer (``1100`` -> ``11``), the
id the observatory register and the LF ledger use.
"""

from datetime import date, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lovspor.atomic_io import atomic_replace_bytes
from lovspor.errors import InnsynRecordError, InnsynRefusedError, NetworkError, ParseError
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.model import require_utc
from lovspor.sources.lovdata import DEFAULT_USER_AGENT

KLASS_BASE_URL = "https://data.ssb.no/api/klass/v1"
KOMMUNE_CLASSIFICATION = 131
FYLKESKOMMUNE_CLASSIFICATION = 127
KLASS_TIMEOUT_SECONDS = 30.0

AuthorityKind = Literal["kommune", "fylkeskommune"]

AUTHORITY_ID_PATTERN = r"^(?:\d{2}|\d{4})$"

_OSLO_FYLKESKOMMUNE = "0300"
_OSLO_REASON = "Oslo is both kommune and fylkeskommune (KLASS 127 note); asked once as 0301"
_UNSPECIFIED_REASON = "Uoppgitt: SSB's code for an unspecified area, not an authority"
_HTTP_OK = 200


class Authority(BaseModel):
    """One kommune or fylkeskommune, as KLASS names it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_id: str = Field(pattern=AUTHORITY_ID_PATTERN)
    kind: AuthorityKind
    name: str = Field(min_length=1)
    klass_code: str = Field(pattern=r"^\d{4}$")

    @property
    def letter_name(self) -> str:
        """The name a letter addresses: the Norwegian form, before any Sami one."""
        first = self.name.split(" - ", 1)[0].strip()
        return f"{first} kommune" if self.kind == "kommune" else first


class ExcludedCode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    classification_id: int
    code: str
    name: str
    reason: str


class ClassificationRead(BaseModel):
    """One KLASS read: what was asked, and how many codes came back."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    classification_id: int
    url: str
    codes: int = Field(ge=0)


class PopulationSnapshot(BaseModel):
    """Every authority SSB listed at ``valid_at``, read at ``read_at``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    valid_at: date
    read_at: datetime
    sources: tuple[ClassificationRead, ...]
    authorities: tuple[Authority, ...]
    excluded: tuple[ExcludedCode, ...]

    @field_validator("read_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    def by_id(self) -> dict[str, Authority]:
        return {authority.authority_id: authority for authority in self.authorities}


class _KlassCode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str = Field(pattern=r"^\d{4}$")
    name: str = Field(min_length=1)


class _KlassCodes(BaseModel):
    model_config = ConfigDict(extra="ignore")

    codes: list[_KlassCode]


def codes_url(classification_id: int, valid_at: date) -> str:
    return (
        f"{KLASS_BASE_URL}/classifications/{classification_id}/codesAt.json"
        f"?date={valid_at.isoformat()}"
    )


def klass_client() -> httpx.Client:
    return httpx.Client(
        timeout=KLASS_TIMEOUT_SECONDS,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
    )


def read_codes(client: httpx.Client, classification_id: int, valid_at: date) -> list[_KlassCode]:
    """The codes of one classification at ``valid_at``.

    Raises:
        NetworkError: the request failed or was not answered 200.
        ParseError: the body is not KLASS's ``codes`` shape.
    """
    url = codes_url(classification_id, valid_at)
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise NetworkError(f"KLASS {classification_id}: {exc}") from exc
    if response.status_code != _HTTP_OK:
        raise NetworkError(f"KLASS {classification_id}: HTTP {response.status_code} from {url}")
    try:
        return _KlassCodes.model_validate_json(response.content).codes
    except ValidationError as exc:
        raise ParseError(f"KLASS {classification_id}: unexpected response from {url}") from exc


def _unspecified(code: str) -> bool:
    """Fylke 99, kommune 9999: KLASS's "Uoppgitt" in both classifications."""
    return code.startswith("99") and set(code[2:]) <= {"0", "9"}


def _excluded(classification_id: int, code: _KlassCode, reason: str) -> ExcludedCode:
    return ExcludedCode(
        classification_id=classification_id, code=code.code, name=code.name, reason=reason
    )


def kommuner(codes: list[_KlassCode]) -> tuple[list[Authority], list[ExcludedCode]]:
    kept, excluded = [], []
    for code in codes:
        if _unspecified(code.code):
            excluded.append(_excluded(KOMMUNE_CLASSIFICATION, code, _UNSPECIFIED_REASON))
            continue
        kept.append(
            Authority(authority_id=code.code, kind="kommune", name=code.name, klass_code=code.code)
        )
    return kept, excluded


def _fylke_reason(code: str) -> str | None:
    if _unspecified(code):
        return _UNSPECIFIED_REASON
    return _OSLO_REASON if code == _OSLO_FYLKESKOMMUNE else None


def fylkeskommuner(codes: list[_KlassCode]) -> tuple[list[Authority], list[ExcludedCode]]:
    """Raises: ParseError: a code that is not a fylkesnummer followed by ``00``."""
    kept, excluded = [], []
    for code in codes:
        reason = _fylke_reason(code.code)
        if reason is not None:
            excluded.append(_excluded(FYLKESKOMMUNE_CLASSIFICATION, code, reason))
            continue
        if not code.code.endswith("00"):
            raise ParseError(f"KLASS 127 code {code.code} is not a fylkesnummer followed by 00")
        kept.append(
            Authority(
                authority_id=code.code[:2],
                kind="fylkeskommune",
                name=code.name,
                klass_code=code.code,
            )
        )
    return kept, excluded


def _read(
    client: httpx.Client, classification_id: int, valid_at: date
) -> tuple[ClassificationRead, list[_KlassCode]]:
    codes = read_codes(client, classification_id, valid_at)
    read = ClassificationRead(
        classification_id=classification_id,
        url=codes_url(classification_id, valid_at),
        codes=len(codes),
    )
    return read, codes


def fetch_population(client: httpx.Client, valid_at: date, read_at: datetime) -> PopulationSnapshot:
    """Read both classifications at ``valid_at`` into one snapshot.

    Raises:
        NetworkError, ParseError: as :func:`read_codes` and :func:`fylkeskommuner`.
    """
    municipal_read, municipal = _read(client, KOMMUNE_CLASSIFICATION, valid_at)
    county_read, county = _read(client, FYLKESKOMMUNE_CLASSIFICATION, valid_at)
    kept_k, excluded_k = kommuner(municipal)
    kept_f, excluded_f = fylkeskommuner(county)
    return PopulationSnapshot(
        valid_at=valid_at,
        read_at=read_at,
        sources=(municipal_read, county_read),
        authorities=tuple(sorted(kept_k + kept_f, key=lambda a: a.authority_id)),
        excluded=tuple(excluded_k + excluded_f),
    )


def snapshot_filename(snapshot: PopulationSnapshot) -> str:
    stamp = snapshot.read_at.strftime("%Y%m%dT%H%M%S.%fZ")
    return f"population-{snapshot.valid_at.isoformat()}-read-{stamp}.json"


def write_snapshot(workspace: InnsynWorkspace, snapshot: PopulationSnapshot) -> Path:
    """Write the snapshot as a new file; an earlier snapshot is never replaced."""
    directory = workspace.ensure(workspace.population_dir)
    path = directory / snapshot_filename(snapshot)
    if path.exists():
        raise InnsynRefusedError(f"{path} exists; a snapshot is never replaced")
    atomic_replace_bytes(path, (snapshot.model_dump_json(indent=2) + "\n").encode())
    return path


def latest_snapshot(workspace: InnsynWorkspace) -> PopulationSnapshot:
    """The snapshot with the latest ``valid_at``, then the latest read.

    Raises:
        InnsynRefusedError: no snapshot has been fetched.
        InnsynRecordError: the chosen snapshot does not parse.
    """
    files = sorted(workspace.population_dir.glob("population-*.json"))
    if not files:
        raise InnsynRefusedError("no population snapshot; run `innsyn fetch-population` first")
    try:
        return PopulationSnapshot.model_validate_json(files[-1].read_bytes())
    except ValidationError as exc:
        raise InnsynRecordError(f"{files[-1]}: not a population snapshot") from exc
