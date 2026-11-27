"""AR6c test support: genuine release histories on both paths, for the history auditor (#738).

The first release goes through the real pipeline (execute, recover, attest).
Later releases are made the way a runner holding the bot key would make them:
a manifest built on the previous head, signed by the bot in the release
namespace with real ``ssh-keygen``, committed as the configured path's
signature class (path A: GitHub verifies the commit for the pinned App; path B:
the bot key signs the commit object), and attested by the bot in the
publication namespace through the real attestation store. Nothing a reader
judges is mocked; only the runner claims are invented, and readers never
verify those.
"""

import os
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import NamedTuple

from lovspor.forarbeider import publication_path_b
from lovspor.forarbeider.policy_trust import PUBLICATION_NAMESPACE, RELEASE_NAMESPACE
from lovspor.forarbeider.publication_attestation import (
    Expected,
    PublicSource,
    attest,
    derive_facts,
    public_source,
)
from lovspor.forarbeider.publication_attestation_models import (
    ATTESTATION_FILE,
    ATTESTATION_SIGNATURE_FILE,
    PublicationAttestation,
    RunnerClaims,
)
from lovspor.forarbeider.publication_audit import AuditRequest, AuditSource, audit, audit_source
from lovspor.forarbeider.publication_audit_models import AuditReport
from lovspor.forarbeider.publication_execute import execute
from lovspor.forarbeider.publication_manifest_signature import parsed_manifest
from lovspor.forarbeider.publication_models import (
    RELEASE_FILE,
    ReleaseManifestFile,
    publication_for,
    release_signature_path,
)
from lovspor.forarbeider.publication_path_a import headline_for
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.version_models import canonical_text
from tests.unit.forarbeider_authorization_support import Key, sign
from tests.unit.forarbeider_execution_support import APP, NOW, KeySigner, Rig
from tests.unit.forarbeider_publication_support import git
from tests.unit.forarbeider_release_control_support import BOT
from tests.unit.test_forarbeider_publication_stale_authorization import Adapters


class History(NamedTuple):
    adapters: Adapters
    commits: list[str]


def bot(rig: Rig) -> KeySigner:
    return KeySigner(BOT, rig.bot)


def expected(rig: Rig) -> Expected:
    return Expected(rig.root, rig.inputs.settings)


def writer(rig: Rig, adapters: Adapters) -> PublicSource:
    return public_source(adapters.at(NOW), expected(rig), (rig.clone, bot(rig)))


def reader(rig: Rig, adapters: Adapters) -> AuditSource:
    return audit_source(adapters.at(NOW), expected(rig))


def run(rig: Rig, adapters: Adapters, boundary: str | None, **options: object) -> AuditReport:
    request = AuditRequest(expected(rig), boundary, **options)  # type: ignore[arg-type]
    return audit(reader(rig, adapters), request, NOW)


def first_release(rig: Rig) -> History:
    """The first release, through the real pipeline: published, recovered and attested."""
    adapters = Adapters(rig)
    sent = execute(rig.request(), adapters.at(NOW))
    assert sent.status == "published" and sent.commit is not None
    assert recover(rig.request(), adapters.at(NOW)).classification == "published_authorized"
    assert attest(rig.request(), adapters.at(NOW), bot(rig)).status == "attested"
    return History(adapters, [sent.commit])


def head(rig: Rig, adapters: Adapters) -> str:
    found = writer(rig, adapters).main.head()
    assert found is not None
    return found


def manifest_at(rig: Rig, adapters: Adapters, commit: str) -> ReleaseManifestFile:
    found = writer(rig, adapters).main.commit(commit)
    assert found is not None
    (raw,) = [data for path, data in found.files.items() if RELEASE_FILE.fullmatch(path) and data]
    manifest = parsed_manifest(raw)
    assert isinstance(manifest, ReleaseManifestFile), manifest
    return manifest


class Release(NamedTuple):
    manifest: bytes
    files: dict[str, bytes]


def next_release(
    rig: Rig, template: ReleaseManifestFile, parent: str, n: int, batch: int | None = None
) -> Release:
    """Release ``n`` on ``parent`` (of batch ``batch``, ``n`` by default): new bytes per file."""
    files = {item.path: f"{item.path} release {n}\n".encode() for item in template.files}
    listed = tuple(
        item.model_copy(
            update={
                "sha256": sha256(files[item.path]).hexdigest(),
                "size_bytes": len(files[item.path]),
            }
        )
        for item in template.files
    )
    manifest = template.model_copy(
        update={
            "publication": publication_for(rig.path, parent),  # type: ignore[arg-type]
            "files": listed,
            "batch_id": "batch-" + sha256(f"batch {batch or n}".encode()).hexdigest()[:56],
            "batch_sequence": batch or n,
        }
    )
    return Release(canonical_text(manifest).encode(), files)


def release_files(found: Release, signature: bytes | None) -> dict[str, bytes]:
    digest = sha256(found.manifest).hexdigest()
    files = dict(found.files) | {f"releases/{digest}.json": found.manifest}
    if signature is not None:
        files[release_signature_path(digest)] = signature
    return files


def commit_files(
    rig: Rig, adapters: Adapters, files: dict[str, bytes], signer: Key | None = None
) -> str:
    """One commit of ``files`` on ``main``; ``signer`` replaces the configured class's signer."""
    digests = [p for p in files if RELEASE_FILE.fullmatch(p)]
    headline = headline_for(digests[0][9:-5]) if digests else "a foreign change"
    if rig.path == "A":
        identity = (APP, True) if signer is None else (None, False)
        return adapters.github.advance(files, (headline, "release"), identity)
    return _push_b(rig, files, headline, signer or rig.bot)


def _git_in(work: Path, argv: tuple[str, ...], stdin: bytes | None = None) -> bytes:
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    done = subprocess.run(
        ["git", *argv], cwd=work, input=stdin, capture_output=True, env=environment, check=True
    )
    return done.stdout.strip()


def work_clone(rig: Rig) -> Path:
    work = rig.tmp / "audit-work"
    if not work.exists():
        git(rig.tmp, "clone", "--quiet", str(rig.bare), str(work))
    git(work, "fetch", "--quiet", "origin", "main")
    git(work, "reset", "--quiet", "--hard", "FETCH_HEAD")
    return work


def _push_b(rig: Rig, files: dict[str, bytes], headline: str, key: Key) -> str:
    work = work_clone(rig)
    for path, data in files.items():
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_bytes(data)
    git(work, "add", "--all")
    tree = _git_in(work, ("write-tree",))
    parent = _git_in(work, ("rev-parse", "HEAD"))
    return push_object(rig, work, (tree, (parent,)), (headline, key))


def push_object(
    rig: Rig, work: Path, shape: tuple[bytes, tuple[bytes, ...]], made: tuple[str, Key]
) -> str:
    """A commit object of ``tree`` and ``parents``, signed by ``key`` (release ns), pushed."""
    tree, parents = shape
    headline, key = made
    who = f"{BOT} <{BOT}@users.noreply.invalid> {int(NOW.timestamp())} +0000".encode()
    lines = b"".join(b"parent %s\n" % p for p in parents)
    payload = b"tree %s\n%sauthor %s\ncommitter %s\n\n%s\n\nrelease\n" % (
        tree,
        lines,
        who,
        who,
        headline.encode(),
    )
    raw = publication_path_b.signed_object(payload, sign(key, payload, RELEASE_NAMESPACE))
    oid = _git_in(work, ("hash-object", "-t", "commit", "-w", "--stdin"), raw).decode()
    git(work, "push", "--quiet", str(rig.bare), f"{oid}:refs/heads/main")
    return oid


def claims(n: int) -> RunnerClaims:
    return RunnerClaims(
        transaction_id=sha256(f"transaction {n}".encode()).hexdigest(),
        attempt_id=None,
        attempt_at=None,
        remote_event_at=None,
        authorization="authorized",
        authorization_waived=(),
        receipt_version="publication-receipt/2",
        receipt_sequence=1,
        receipt_sha256=sha256(f"receipt {n}".encode()).hexdigest(),
        recovered_from_remote=True,
    )


def attestation_files(rig: Rig, adapters: Adapters, commit: str, n: int) -> dict[str, bytes]:
    found = writer(rig, adapters).main.commit(commit)
    assert found is not None
    facts, reasons = derive_facts(found, expected(rig), NOW)
    assert facts is not None, reasons
    payload = canonical_text(PublicationAttestation(remote=facts, claims=claims(n))).encode()
    signature = sign(rig.bot, payload, PUBLICATION_NAMESPACE)
    return {ATTESTATION_FILE: payload, ATTESTATION_SIGNATURE_FILE: signature}


def attest_commit(rig: Rig, adapters: Adapters, commit: str, files: dict[str, bytes]) -> None:
    written = writer(rig, adapters).attestations.write(commit, files, NOW)
    assert written.status == "created", written


def add_release(rig: Rig, history: History, attested: bool = True) -> str:
    """One more genuine release on the current head, attested unless told otherwise."""
    adapters = history.adapters
    parent = head(rig, adapters)
    n = len(history.commits) + 1
    found = next_release(rig, manifest_at(rig, adapters, history.commits[0]), parent, n)
    signature = sign(rig.bot, found.manifest, RELEASE_NAMESPACE)
    commit = commit_files(rig, adapters, release_files(found, signature))
    if attested:
        attest_commit(rig, adapters, commit, attestation_files(rig, adapters, commit, n))
    history.commits.append(commit)
    return commit


def add_foreign(rig: Rig, history: History) -> str:
    """Someone moves ``main`` with a change that is no release (signed as the path's class)."""
    n = len(history.commits) + 1
    return commit_files(rig, history.adapters, {"INDEX.md": f"foreign {n}\n".encode()})
