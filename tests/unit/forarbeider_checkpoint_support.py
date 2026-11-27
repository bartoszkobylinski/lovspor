"""AR7a test support: a real witness log, a real Git authority and real keys (#738).

The authority is a disposable bare repository whose own configuration refuses
non-fast-forward updates and deletions, exactly what a verifier requires. The
owner signs the bootstrap and the witness signs checkpoints with real
``ssh-keygen``; nothing a verifier judges is mocked. Processes that must run
apart (concurrent writers, a crash, a restart) are spawned for real.
"""

import base64
import multiprocessing
import os
import shutil
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from lovspor.forarbeider import witness_checkpoint
from lovspor.forarbeider.policy_trust import (
    ANCHOR_NAMESPACE,
    CHECKPOINT_NAMESPACE,
    LOG_NAMESPACE,
    POLICY_NAMESPACE,
    TrustRoot,
    fingerprint,
)
from lovspor.forarbeider.version_models import canonical_text
from lovspor.forarbeider.witness_checkpoint import (
    AdvanceOutcome,
    CheckpointStore,
    CheckpointVerdict,
    TrustedLog,
    Witnessing,
)
from lovspor.forarbeider.witness_checkpoint_models import (
    AuthorityLocator,
    Checkpoint,
    LogBootstrap,
    SignedCheckpoint,
    checkpoint_bytes,
)
from tests.unit.forarbeider_authorization_support import (
    BOT_NAMESPACES,
    CHECKED_AT,
    Key,
    new_key,
    sign,
    signer_line,
    trust_root,
)
from tests.unit.forarbeider_execution_support import KeySigner

REPOSITORY = "bartoszkobylinski/lovverk-forarbeider"
REF = "refs/heads/witness/quarantine-q1"
WITNESS = "test-witness"


def entry(n: int) -> str:
    return sha256(f"entry {n}".encode()).hexdigest()


def key_fingerprint(key: Key) -> str:
    return fingerprint(base64.b64decode(key.public.split()[1]))


def git(directory: Path, *argv: str) -> str:
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t.invalid",
    }
    done = subprocess.run(
        ["git", "-C", str(directory), *argv], capture_output=True, env=environment, check=False
    )
    return (done.stdout + done.stderr).decode()


def make_authority(bare: Path, monotonic: bool = True) -> Path:
    bare.parent.mkdir(parents=True, exist_ok=True)
    git(bare.parent, "init", "--bare", "--quiet", str(bare))
    if monotonic:
        git(bare, "config", "receive.denyNonFastForwards", "true")
        git(bare, "config", "receive.denyDeletes", "true")
    return bare


@dataclass
class World:
    """One owner-approved log, its witness store, its authority and the keys."""

    tmp: Path
    owner: Key
    witness_key: Key
    bot: Key
    second_witness: Key
    root: TrustRoot
    store: CheckpointStore
    bare: Path
    workspace: Path
    bootstrap: LogBootstrap
    signature: bytes

    @property
    def log(self) -> TrustedLog:
        return TrustedLog(canonical_text(self.bootstrap).encode(), self.signature, self.root)

    @property
    def signer(self) -> KeySigner:
        return KeySigner(WITNESS, self.witness_key)

    def witnessing(self, **changes: Any) -> Witnessing:
        fields: dict[str, Any] = {
            "log": self.log,
            "store": self.store,
            "at": CHECKED_AT,
            "workspace": self.workspace,
        }
        return Witnessing(**(fields | changes))

    def current(self, **changes: Any) -> CheckpointVerdict:
        return witness_checkpoint.current(self.witnessing(**changes))

    def advance(self, n: int, **changes: Any) -> AdvanceOutcome:
        return witness_checkpoint.advance(self.witnessing(**changes), self.signer, entry(n))

    def folder(self) -> Path:
        return self.store.location / witness_checkpoint.log_id_of(self.bootstrap)

    def tip(self) -> str:
        return git(self.bare, "rev-parse", "--verify", "--quiet", REF).strip()


def make_world(tmp: Path, *, bootstrap: bool = True, revoked: tuple[Key, ...] = ()) -> World:
    keys = tmp / "keys"
    names = ("owner", "witness", "bot", "witness2")
    owner, witness, bot, second = (new_key(keys, name) for name in names)
    lines = [
        signer_line("test-owner", owner, f"{POLICY_NAMESPACE},{LOG_NAMESPACE}"),
        signer_line(WITNESS, witness, f"{ANCHOR_NAMESPACE},{CHECKPOINT_NAMESPACE}"),
        signer_line("test-bot", bot, BOT_NAMESPACES),
        signer_line("test-witness-2", second, CHECKPOINT_NAMESPACE),
    ]
    root = trust_root(tmp / "root", lines, revoked)
    (tmp / "store").mkdir(mode=0o700)
    (tmp / "workspace").mkdir(mode=0o700)
    bare = make_authority(tmp / "authority" / "witness.git")
    approved = LogBootstrap(
        repository=REPOSITORY,
        purpose="quarantine_succession",
        nonce=sha256(str(tmp).encode()).hexdigest(),
        witness_keys=(key_fingerprint(witness),),
        authority=AuthorityLocator(remote=str(bare), ref=REF),
    )
    signature = sign(owner, canonical_text(approved).encode(), LOG_NAMESPACE)
    world = World(
        tmp,
        owner,
        witness,
        bot,
        second,
        root,
        CheckpointStore(tmp / "store"),
        bare,
        tmp / "workspace",
        approved,
        signature,
    )
    if bootstrap:
        assert witness_checkpoint.bootstrap(world.witnessing(), world.signer).status == (
            "bootstrapped"
        )
    return world


def signed(world: World, checkpoint: Checkpoint, key: Key | None = None) -> bytes:
    """``checkpoint`` signed by ``key`` (the witness by default) in the checkpoint namespace."""
    signature = sign(key or world.witness_key, checkpoint_bytes(checkpoint), CHECKPOINT_NAMESPACE)
    body = SignedCheckpoint(checkpoint=checkpoint, signature=signature.decode())
    return canonical_text(body).encode()


def snapshot(path: Path, name: str) -> Path:
    saved = path.with_name(f"{path.name}-{name}")
    shutil.copytree(path, saved)
    return saved


def restore(path: Path, saved: Path) -> None:
    shutil.rmtree(path)
    shutil.copytree(saved, path)


def tree(path: Path) -> dict[str, bytes]:
    """Every file below ``path``, the persistent lock file aside."""
    return {
        str(p.relative_to(path)): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file() and p.name != ".store.lock"
    }


def client(world: World) -> Path:
    """Another client of the authority, holding everything the pin's ref holds."""
    work = world.tmp / "intruder"
    if not work.exists():
        git(world.tmp, "init", "--quiet", str(work))
    git(work, "fetch", "--quiet", str(world.bare), REF)
    return work


def pin_commit(world: World, raw: bytes, parent: str | None, extra: bytes | None = None) -> str:
    """A commit holding ``raw`` as the pin (and ``extra`` as one more file), made by a client."""
    work = client(world)
    entries = b""
    files = {"checkpoint.json": raw} | ({} if extra is None else {"extra.txt": extra})
    for name, data in sorted(files.items()):
        blob = subprocess.run(
            ["git", "-C", str(work), "hash-object", "-w", "--stdin"],
            input=data,
            capture_output=True,
            check=True,
        ).stdout.strip()
        entries += b"100644 blob %s\t%s\n" % (blob, name.encode())
    made = subprocess.run(
        ["git", "-C", str(work), "mktree"], input=entries, capture_output=True, check=True
    )
    argv = ["commit-tree", made.stdout.strip().decode(), "-m", "intruder"]
    if parent is not None:
        argv += ["-p", parent]
    return git(work, *argv).strip()


def push_commit(world: World, commit: str, force: bool = False) -> str:
    plus = "+" if force else ""
    return git(client(world), "push", str(world.bare), f"{plus}{commit}:{REF}")


def push_pin(world: World, raw: bytes, parent: str | None, force: bool = False) -> str:
    """Push a commit holding ``raw`` as the pin, as any other client of the authority would."""
    return push_commit(world, pin_commit(world, raw, parent), force)


# --- spawned processes ---------------------------------------------------------------------------


def _advance_in_child(world: World, n: int) -> str:
    try:
        return world.advance(n).status
    except witness_checkpoint.ForarbeiderRefusedError as error:
        return error.detail.split(": ", 1)[-1]


def _crash_in_child(world: World, n: int) -> None:
    def crash(*_: object, **__: object) -> str:
        os._exit(17)

    witness_checkpoint.git_checkpoint.advance = crash  # type: ignore[assignment]
    world.advance(n)


def _current_in_child(world: World) -> tuple[str, int | None]:
    restarted = World(**{**world.__dict__, "store": CheckpointStore(world.store.location)})
    verdict = restarted.current()
    return verdict.status, None if verdict.pinned is None else verdict.pinned.sequence


def spawn() -> Any:
    return multiprocessing.get_context("spawn")


def in_children(jobs: list[tuple[World, int]]) -> list[str]:
    with spawn().Pool(len(jobs)) as pool:
        return pool.starmap(_advance_in_child, jobs)


def crash_in_child(world: World, n: int) -> int | None:
    process = spawn().Process(target=_crash_in_child, args=(world, n))
    process.start()
    process.join(60)
    return process.exitcode


def current_in_child(world: World) -> tuple[str, int | None]:
    with spawn().Pool(1) as pool:
        return pool.apply(_current_in_child, (world,))
