"""Host-level exclusive workload lock (issue #169).

Two workloads on this machine must never overlap: an LLHB benchmark arm —
250 timed model calls whose latencies, timeouts and retries are frozen into
the run — and an Observatory sweep, hundreds of fetches against the source
register. Either alone is fine; together, the benchmark's operational record
carries a second workload's fingerprint and the preregistration (ruling
#30) has nothing to say about which of the two produced a timeout.

So both hold one lock, and neither waits:

* the benchmark finding the lock held **refuses** — queued behind a sweep it
  would start at an unplanned moment on a host state nobody inspected;
* the sweep finding the lock held **defers** — records that it did not run
  and exits; the next scheduled sweep picks up. The Observatory loses a
  window, never its integrity.

The primitive is ``flock(LOCK_EX | LOCK_NB)`` on one file, held for the
whole workload. The kernel drops the lock with the file descriptor, so a
holder that crashes or is killed leaves no stale lock to clean up — the
failure mode of pid files. The file's content is advisory only: who holds
it, since when, under which pid, so a refusal can name the holder.

The path follows the repo's XDG convention (``access.py``,
``corpus_fetch.py``): ``$XDG_STATE_HOME/lovspor/exclusive-workload.lock``,
else ``~/.local/state/lovspor/exclusive-workload.lock``;
``LOVSPOR_EXCLUSIVE_LOCK_PATH`` overrides both. State, not config or
cache: it describes what the host is doing right now.
"""

import fcntl
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from lovspor.errors import LovsporError

ENV_LOCK_PATH = "LOVSPOR_EXCLUSIVE_LOCK_PATH"
LOCK_FILENAME = "exclusive-workload.lock"


@dataclass(frozen=True)
class Holder:
    """Who holds the lock — written into the lock file for the refusal message."""

    owner: str
    pid: int
    since: str

    def describe(self) -> str:
        return f"{self.owner} (pid {self.pid}, since {self.since})"


class ExclusiveWorkloadHeldError(LovsporError):
    """The host's exclusive workload lock belongs to another process.

    Raised instead of waiting: neither workload queues behind the other
    (issue #169). ``holder`` is None when the lock file carried no readable
    record — the lock is still held; only the name is missing.
    """

    def __init__(self, wanted_by: str, holder: Holder | None, path: Path) -> None:
        self.wanted_by = wanted_by
        self.holder = holder
        self.path = path
        who = holder.describe() if holder is not None else "an unidentified process"
        super().__init__(
            f"{wanted_by} refused: the exclusive workload lock is held by {who}; "
            f"neither workload waits for the other (issue #169) — {path}"
        )


def default_lock_path(environment: Mapping[str, str] | None = None) -> Path:
    """Where the lock lives; see the module docstring for the precedence."""
    env = os.environ if environment is None else environment
    explicit = env.get(ENV_LOCK_PATH)
    if explicit:
        return Path(explicit).expanduser()
    xdg = env.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "lovspor" / LOCK_FILENAME


def read_holder(path: Path) -> Holder | None:
    """The advisory holder record, or None when absent or unreadable.

    Advisory: the flock is the truth about whether the lock is held; this
    only names the holder. A torn or foreign file therefore degrades the
    message, never the decision.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return Holder(owner=str(data["owner"]), pid=int(data["pid"]), since=str(data["since"]))
    except (KeyError, TypeError, ValueError):
        return None


@contextmanager
def exclusive_workload(owner: str, path: Path | None = None) -> Iterator[Holder]:
    """Hold the host's exclusive workload lock for the duration of the block.

    Raises ``ExclusiveWorkloadHeldError`` immediately when another process holds
    it. The descriptor is not inherited by children (``subprocess`` closes
    fds by default), so a spawned ``claude`` or fetch worker cannot outlive
    the lock of the process that owns it.
    """
    lock_path = default_lock_path() if path is None else path
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExclusiveWorkloadHeldError(owner, read_holder(lock_path), lock_path) from None
        held = Holder(
            owner=owner,
            pid=os.getpid(),
            since=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(asdict(held)) + "\n")
        handle.flush()
        try:
            yield held
        finally:
            # Empty the record before releasing so a later reader never
            # attributes the lock to a process that is gone.
            handle.seek(0)
            handle.truncate()
            handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
