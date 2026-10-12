"""Where each authority's request goes, and in which written standard (issue #509, point 2).

``directory.jsonl`` is append-only. Each line records one fact an operator
established, with its evidence: the page the fact was read on and when it
was read (a live visit, or the ``ObservedAt`` of an archived capture). The
latest line for an authority stands; earlier ones stay as history.

* ``recipient`` — the mailbox a request is sent to (normally ``postmottak@``).
  An authority with none is **unresolved**: it gets no letter, and nothing
  here derives an address from a domain pattern. A guessed mailbox looks
  exactly like a verified one in a letter, and a request that went nowhere
  would start a § 32 clock that is not running.
* ``maalform`` — ``nb`` or ``nn`` for the letter. No machine-readable source
  of the authorities' språkvedtak was found reliable enough to read
  automatically, so the operator records it from the evidence they read
  (Språkrådet's list of språkvedtak, or the authority's own pages). Unset, a
  letter is written in bokmål and the manifest says the form was not set:
  språklova's rules on the written standard bind public bodies, not a private
  requester, so a bokmål letter is a valid request everywhere. The owner
  decides whether unset authorities are sent before their form is recorded.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from lovspor.errors import InnsynRefusedError
from lovspor.innsyn.journal import Journal
from lovspor.innsyn.population import AUTHORITY_ID_PATTERN, PopulationSnapshot
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.model import require_utc

Maalform = Literal["nb", "nn"]
DEFAULT_MAALFORM: Maalform = "nb"

MAILBOX_PATTERN = r"^[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}$"


class Evidence(BaseModel):
    """The page a fact was read on, and when it was read there."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(pattern=r"^https?://\S+$")
    read_at: datetime

    @field_validator("read_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class _Recorded(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_id: str = Field(pattern=AUTHORITY_ID_PATTERN)
    evidence: Evidence
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class RecipientRecorded(_Recorded):
    kind: Literal["recipient"] = "recipient"
    address: str = Field(pattern=MAILBOX_PATTERN)

    @field_validator("address", mode="before")
    @classmethod
    def _lowercase(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class MaalformRecorded(_Recorded):
    kind: Literal["maalform"] = "maalform"
    maalform: Maalform


DirectoryRecord = Annotated[RecipientRecorded | MaalformRecorded, Field(discriminator="kind")]
_ADAPTER: TypeAdapter[DirectoryRecord] = TypeAdapter(DirectoryRecord)


class Contact(BaseModel):
    """What the directory holds for one authority now."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recipient: RecipientRecorded | None = None
    maalform: MaalformRecorded | None = None

    @property
    def letter_form(self) -> Maalform:
        return DEFAULT_MAALFORM if self.maalform is None else self.maalform.maalform


def parse_fact(data: dict[str, object]) -> DirectoryRecord:
    """Raises: ValidationError: not a recipient or målform record."""
    return _ADAPTER.validate_python(data)


def directory_journal(workspace: InnsynWorkspace) -> Journal[DirectoryRecord]:
    return Journal(workspace.directory_log, _ADAPTER)


def contacts(records: list[DirectoryRecord]) -> dict[str, Contact]:
    """The standing recipient and målform per authority: the latest line of each kind."""
    held: dict[str, Contact] = {}
    for record in records:
        contact = held.get(record.authority_id, Contact())
        field = "recipient" if isinstance(record, RecipientRecorded) else "maalform"
        held[record.authority_id] = contact.model_copy(update={field: record})
    return held


def record_fact(
    workspace: InnsynWorkspace, population: PopulationSnapshot, fact: DirectoryRecord
) -> None:
    """Append ``fact`` for an authority of the population.

    Raises:
        InnsynRefusedError: the authority is not in the population snapshot.
    """
    if fact.authority_id not in population.by_id():
        raise InnsynRefusedError(
            f"{fact.authority_id} is not in the population valid at {population.valid_at}"
        )
    workspace.ensure()
    directory_journal(workspace).append([fact])
