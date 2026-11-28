"""AR7b-1 test support: engine builds with a reviewed witness-log registry (#738, #1052).

An *engine build* is what a reviewed engine change produces: a registry file
and the digest pinned in code, changed together, and the engine's trust-root
digest. A test simulates one by setting the module globals the engine reads,
the only way this API can be changed, because :func:`load_log_registry` and
:func:`select` take nothing a caller could use to name a registry. Every
signature is a real ``ssh-keygen`` signature, and every authority is a real
bare Git repository.
"""

import multiprocessing
from hashlib import sha256
from pathlib import Path
from typing import Any, NamedTuple

from lovspor.forarbeider import policy_trust, witness_checkpoint, witness_log_registry
from lovspor.forarbeider.policy_trust import LOG_NAMESPACE
from lovspor.forarbeider.version_models import canonical_text
from lovspor.forarbeider.witness_checkpoint import TrustedLog
from lovspor.forarbeider.witness_checkpoint_models import AuthorityLocator, LogBootstrap, log_id_of
from tests.unit.forarbeider_authorization_support import sign
from tests.unit.forarbeider_checkpoint_support import REF, World, make_authority

DOMAIN = b"lovspor-forarbeider-log-registry/1\n"


class Build(NamedTuple):
    """One engine build: where its registry lives and the digests pinned in its code."""

    directory: Path
    registry_sha256: str
    trust_root_sha256: str


def registry_bytes(*lines: tuple[str, str, str]) -> bytes:
    return b"".join(f"{repo} {purpose} {log}\n".encode() for repo, purpose, log in lines)


def write_build(directory: Path, data: bytes, trust_root_sha256: str) -> Build:
    """A reviewed build: this registry file and, pinned with it, its digest."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / witness_log_registry.REGISTRY_FILE).write_bytes(data)
    return Build(directory, sha256(DOMAIN + data).hexdigest(), trust_root_sha256)


def install(build: Build) -> None:
    """Run as ``build``: the module globals a reviewed engine change would set."""
    witness_log_registry._DIRECTORY = build.directory  # type: ignore[misc]
    witness_log_registry.ENGINE_LOG_REGISTRY_SHA256 = build.registry_sha256
    policy_trust.ENGINE_TRUST_ROOT_SHA256 = build.trust_root_sha256


def log_id(log: TrustedLog) -> str:
    return log_id_of(LogBootstrap.model_validate_json(log.bootstrap))


def another_log(world: World, name: str, **update: Any) -> TrustedLog:
    """A second log the owner genuinely approved, on its own independent authority, pinned."""
    bare = make_authority(world.tmp / f"authority-{name}" / "witness.git")
    fields = {
        "nonce": sha256(name.encode()).hexdigest(),
        "authority": AuthorityLocator(remote=str(bare), ref=REF),
    }
    approved = world.bootstrap.model_copy(update=fields | update)
    raw = canonical_text(approved).encode()
    log = TrustedLog(raw, sign(world.owner, raw, LOG_NAMESPACE), world.root)
    outcome = witness_checkpoint.bootstrap(world.witnessing(log=log), world.signer)
    assert outcome.status == "bootstrapped"
    return log


# --- a restarted engine --------------------------------------------------------------------------


def _select_in_child(build: Build, world: World, log: TrustedLog) -> str:
    install(build)
    return witness_log_registry.select(world.witnessing(log=log)).status


def select_in_child(build: Build, world: World, log: TrustedLog) -> str:
    """``select`` in a fresh interpreter running ``build``: nothing carries over but the build."""
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        return pool.apply(_select_in_child, (build, world, log))
