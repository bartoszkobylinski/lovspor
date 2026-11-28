"""AR7b-1: the engine's reviewed selection of one witness log per repository and purpose.

AR7a (:mod:`~lovspor.forarbeider.witness_checkpoint`) verifies the log a
caller offers: the owner signed its bootstrap, and its history is the one its
own authority pins. The owner can approve several logs for one repository and
purpose, and each verifies (#1052). Which of them is *the* log is a third,
separate fact. A signature, a pin, a directory or the most recently seen log
never establishes it.

**The mapping.** ``trust/witness-logs`` ships with the engine. Each line is
``<owner>/<repository> <purpose> <log_id>``, and ``log_id`` is the SHA-256 of
the canonical owner-signed bootstrap (``log_id_of``). A repository and
purpose appear once, and so does a log id. Anything else refuses the whole
registry. The file's digest is pinned in code by
:data:`ENGINE_LOG_REGISTRY_SHA256`, beside the trust root's own digest, which
keeps its meaning. Only a registry whose bytes match is ``engine_pinned``.
:func:`load_log_registry` takes no directory, so a caller can neither supply
the mapping nor override it.

**Three facts, three flags.** :func:`select` returns a
:class:`SelectionVerdict`:

- ``owner_approved``: the bootstrap is canonical and owner-signed;
- ``authoritative``: the engine-pinned registry maps the bootstrap's repository
  and purpose to exactly this log id, and the bootstrap was judged under the
  engine-pinned trust root;
- ``pinned_consistent``: AR7a's :func:`~lovspor.forarbeider.witness_checkpoint.current`
  is ``verified``, so the whole pinned lineage holds and the store's head is the pin.

The status is ``selected`` only when all three hold. :func:`require_selected`
is the publication-facing gate, and it refuses everything else. A missing
mapping and an unknown repository or purpose are ``log_unregistered``. A
second log is ``log_not_selected``. Nothing is chosen on first use, and nothing
falls back to another log.

**Rotation.** Selecting another log for a repository and purpose takes one
reviewed engine change, in this order:

1. the owner signs the new log's bootstrap;
2. the witness bootstraps its pin (``pin_missing`` until then);
3. one engine PR changes the registry line and :data:`ENGINE_LOG_REGISTRY_SHA256`
   together.

From that build on, the old log is ``log_not_selected``. Its history stays
verifiable through AR7a, but nothing publishes from it, and no build falls back
to it. Editing the file without the digest gives ``registry_not_engine_pinned``.
So does restoring an older file.

This module changes no AR5 call site and binds no quarantine source (#1037).
"""

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal, NamedTuple

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import TRUST_DIRECTORY, Origin
from lovspor.forarbeider.witness_checkpoint import (
    CheckpointVerdict,
    Witnessing,
    approved_bootstrap,
    current,
)
from lovspor.forarbeider.witness_checkpoint_models import Checkpoint, LogBootstrap, log_id_of

REGISTRY_FILE: Final = "witness-logs"
#: SHA-256 over the domain and the shipped ``trust/witness-logs`` bytes. A
#: rotation changes the file and this digest in one reviewed engine change.
ENGINE_LOG_REGISTRY_SHA256 = "62f48b38c679b70c4a730b1ecffa26a481f47dc0936f1ef2d3e83b42d5c144df"
MAX_REGISTRY_BYTES: Final = 64 * 1024
_DIRECTORY: Final = TRUST_DIRECTORY
_DOMAIN: Final = b"lovspor-forarbeider-log-registry/1\n"
_LINE: Final = re.compile(
    r"^([A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}) ([a-z][a-z0-9_]{0,63}) ([0-9a-f]{64})$"
)

SelectionStatus = Literal[
    "selected",
    "bootstrap_invalid",
    "registry_unavailable",
    "registry_invalid",
    "registry_not_engine_pinned",
    "trust_root_not_engine_pinned",
    "log_unregistered",
    "log_not_selected",
    "log_not_current",
]


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, f"witness log registry: {detail}")


@dataclass(frozen=True)
class LogRegistry:
    """The parsed registry and the exact bytes it was parsed from."""

    data: bytes
    logs: tuple[tuple[str, str, str], ...]

    @property
    def sha256(self) -> str:
        return sha256(_DOMAIN + self.data).hexdigest()

    @property
    def origin(self) -> Origin:
        return "engine_pinned" if self.sha256 == ENGINE_LOG_REGISTRY_SHA256 else "unpinned"

    def selected(self, repository: str, purpose: str) -> str | None:
        """The one log id mapped to ``repository`` and ``purpose``; ``None`` when none is."""
        found = [log for (repo, aim, log) in self.logs if (repo, aim) == (repository, purpose)]
        return found[0] if len(found) == 1 else None


def parse_log_registry(data: bytes) -> LogRegistry:
    """The registry in ``data``.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid``: not ASCII, a line out of
            format, or a repository and purpose or a log id listed twice.
    """
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as error:
        raise _refused("not ASCII") from error
    logs: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _LINE.fullmatch(line)
        if match is None:
            raise _refused("a line is not <owner>/<repository> <purpose> <log_id>")
        logs.append((match.group(1), match.group(2), match.group(3)))
    if len({(repo, aim) for repo, aim, _ in logs}) != len(logs):
        raise _refused("a repository and purpose are listed twice")
    if len({log for _, _, log in logs}) != len(logs):
        raise _refused("a log id is listed twice")
    return LogRegistry(data, tuple(logs))


def load_log_registry() -> LogRegistry:
    """The engine's own registry. It takes no directory: no caller can supply one.

    Raises:
        OSError: the registry cannot be read.
        ForarbeiderRefusedError: ``schema_invalid``: see :func:`parse_log_registry`.
    """
    path = Path(_DIRECTORY) / REGISTRY_FILE
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(path)
    data = path.read_bytes()
    if len(data) > MAX_REGISTRY_BYTES:
        raise _refused("too large")
    return parse_log_registry(data)


# --- selection -----------------------------------------------------------------------------------


class SelectionVerdict(NamedTuple):
    """Three separate facts about an offered log; ``selected`` only when all three hold."""

    status: SelectionStatus
    reasons: tuple[str, ...]
    log_id: str | None
    selected_log_id: str | None
    registry_sha256: str | None
    owner_approved: bool
    authoritative: bool
    pinned_consistent: bool
    checkpoint: CheckpointVerdict | None = None


class SelectedCheckpoint(NamedTuple):
    """What a publication-facing dependent relies on: the selected log's pinned checkpoint."""

    log_id: str
    registry_sha256: str
    checkpoint: Checkpoint


class _Choice(NamedTuple):
    status: SelectionStatus
    reasons: tuple[str, ...] = ()
    selected_log_id: str | None = None
    registry_sha256: str | None = None


def _choice(witnessing: Witnessing, bootstrap: LogBootstrap, log_id: str) -> _Choice:
    """Whether the engine's reviewed registry selects exactly this log."""
    try:
        registry = load_log_registry()
    except OSError:
        return _Choice("registry_unavailable")
    except ForarbeiderRefusedError as error:
        return _Choice("registry_invalid", (error.detail,))
    if registry.origin != "engine_pinned":
        return _Choice("registry_not_engine_pinned", registry_sha256=registry.sha256)
    if witnessing.log.root.origin != "engine_pinned":
        return _Choice("trust_root_not_engine_pinned", registry_sha256=registry.sha256)
    chosen = registry.selected(bootstrap.repository, bootstrap.purpose)
    if chosen is None:
        return _Choice("log_unregistered", registry_sha256=registry.sha256)
    status: SelectionStatus = "selected" if chosen == log_id else "log_not_selected"
    return _Choice(status, (), chosen, registry.sha256)


def select(witnessing: Witnessing) -> SelectionVerdict:
    """Whether the offered log is owner-approved, the engine's selected log, and current.

    The authority is read only for a log the registry selects.
    """
    bootstrap = approved_bootstrap(witnessing.log, witnessing.at)
    if isinstance(bootstrap, str):
        return SelectionVerdict(
            "bootstrap_invalid", (bootstrap,), None, None, None, False, False, False
        )
    log_id = log_id_of(bootstrap)
    choice = _choice(witnessing, bootstrap, log_id)
    found = SelectionVerdict(
        choice.status,
        choice.reasons,
        log_id,
        choice.selected_log_id,
        choice.registry_sha256,
        owner_approved=True,
        authoritative=choice.status == "selected",
        pinned_consistent=False,
    )
    return found if choice.status != "selected" else _currentness(witnessing, found)


def _currentness(witnessing: Witnessing, found: SelectionVerdict) -> SelectionVerdict:
    """AR7a's verdict on a selected log: ``pinned_consistent`` only when it is ``verified``."""
    verdict = current(witnessing)
    if verdict.status != "verified" or verdict.log_id != found.log_id:
        reasons = (f"checkpoint_{verdict.status}",)
        return found._replace(status="log_not_current", reasons=reasons, checkpoint=verdict)
    return found._replace(pinned_consistent=True, checkpoint=verdict)


def require_selected(witnessing: Witnessing) -> SelectedCheckpoint:
    """The publication-facing gate: an owner-approved, engine-selected, current log.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: log_selection_<status>``
            for every status but ``selected``.
    """
    verdict = select(witnessing)
    checkpoint = None if verdict.checkpoint is None else verdict.checkpoint.pinned
    complete = verdict.owner_approved and verdict.authoritative and verdict.pinned_consistent
    log_id, registry = verdict.log_id, verdict.registry_sha256
    if not complete or checkpoint is None or log_id is None or registry is None:
        raise ForarbeiderRefusedError(
            RefusalReason.RELEASE_NOT_ELIGIBLE, f"log_selection_{verdict.status}"
        )
    return SelectedCheckpoint(log_id, registry, checkpoint)
