"""Who signs the letters (``sender.json``): the owner's name and the reply address.

Configuration, not evidence, so it is replaced rather than appended: the
ledger records what each letter said, and the letter carries the signature.
Kept out of the engine's code because a name in source is a name every clone
signs with.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lovspor.atomic_io import atomic_replace_bytes
from lovspor.errors import InnsynRecordError, InnsynRefusedError
from lovspor.innsyn.directory import MAILBOX_PATTERN
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.fields import TrimmedNonBlankStr
from lovspor.observatory.model import require_utc


class Sender(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: TrimmedNonBlankStr
    email: str = Field(pattern=MAILBOX_PATTERN)
    recorded_at: datetime

    @field_validator("email", mode="before")
    @classmethod
    def _lowercase(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("recorded_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


def write_sender(workspace: InnsynWorkspace, sender: Sender) -> None:
    workspace.ensure()
    atomic_replace_bytes(workspace.sender_path, (sender.model_dump_json() + "\n").encode())


def read_sender(workspace: InnsynWorkspace) -> Sender:
    """Raises: InnsynRefusedError: not set; InnsynRecordError: does not parse."""
    path = workspace.sender_path
    if not path.exists():
        raise InnsynRefusedError("no sender; run `innsyn set-sender --name … --email …` first")
    try:
        return Sender.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        raise InnsynRecordError(f"{path}: not a sender record") from exc
