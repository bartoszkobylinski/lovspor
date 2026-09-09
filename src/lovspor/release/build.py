"""``lovspor release build``: one envelope, in the seven-step order (ADR-0014 Decision 6).

Before anything is rendered: the candidate's ``release_key`` —
``corpus_commit``, ``lovspor_commit``, the probe's ``state_sha256``,
``toolchain_fingerprint`` — is compared with the reconciled live
release's, recorded in its ``release.json``; equal means "already live"
and no render is spent. Otherwise, **in exactly this order**, so that
nothing under a ``release_content_id`` name is ever incomplete and the
final check can assert the id it is named by:

1. build everything under ``<releases>/.build-<random>/`` — the corpus
   via ``emit_site`` into ``corpus/``, the site via ``build_site`` into
   ``site/`` with ``corpus/site-manifest.json`` and the probe's document
   as its explicit inputs — then the link pass against the live release;
2. the structural checks that do not depend on the id;
3. ``release_content_id`` over ``corpus/`` + ``site/``;
4. the id-carrying files, each written by replace: ``site-facts.json``'s
   placeholder, ``release.json`` and ``release.caddy``;
5. the final ``publish-check`` on the temporary directory;
6. one ``rename(2)`` to ``<releases>/<release_content_id>/`` — or, when
   that directory exists, *reuse* if it is the same finalized release and
   *refuse* if it is anything else, both directories untouched;
7. the id, for the transaction (``control``), which never starts on a
   ``.build-*`` directory.

The observer is injected: the command layer runs the real probe with a
real client and its clock; tests hand in a document. ``checkpoint`` is
called with the name of each completed step so a test can stop the
procedure exactly there and assert what a crash would leave.
"""

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict

from lovspor.publish.emit import emit_site
from lovspor.publish.inventory import PublishError
from lovspor.release.check import MANIFEST_NAME, Structure, check_envelope, check_structure
from lovspor.release.envelope import (
    BUILD_PREFIX,
    CORPUS_DIR,
    RECORD_NAME,
    SITE_DIR,
    CorpusSummary,
    ReleaseRecord,
    fragment_text,
    is_complete,
    read_record,
    release_dir,
    write_fragment,
    write_record,
)
from lovspor.release.errors import ReleaseConflictError, ReleaseError
from lovspor.release.linking import hardlink_unchanged
from lovspor.site.build import CAPABILITIES_NAME, FACTS_NAME, SiteInputs, build_site
from lovspor.site.capabilities import CapabilityDocument, Checkout
from lovspor.site.errors import SiteBuildError
from lovspor.site.fingerprint import (
    ReleaseKey,
    release_content_id,
    release_key,
    toolchain_fingerprint,
    write_release_content_id,
)
from lovspor.site.fixture import document_bytes, expected_checkout

Checkpoint = Callable[[str], None]
Observe = Callable[[Checkout], CapabilityDocument]

STEPS = ("built", "linked", "checked", "identified", "written", "verified", "renaming", "renamed")
"""The checkpoint names, in order; ``renaming`` precedes the one rename."""


class BuildRequest(BaseModel):
    """Where to build from and into."""

    model_config = ConfigDict(frozen=True)

    releases: Path
    checkout: Path
    corpus: Path
    corpus_ref: str = "HEAD"


class BuildOutcome(BaseModel):
    """The id the caller commits, and how it was arrived at."""

    model_config = ConfigDict(frozen=True)

    release_content_id: str
    already_live: bool
    reused: bool


class Candidate(NamedTuple):
    """What the short-circuit compares and the build consumes."""

    corpus_commit: str
    document: CapabilityDocument
    key: ReleaseKey


def _no_checkpoint(step: str) -> None:
    del step


def _corpus_commit(corpus: Path, ref: str) -> str:
    """``ref`` resolved to a full commit sha; a blob or tree ref is refused here."""
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],  # noqa: S607
            cwd=corpus,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise ReleaseError(f"cannot run git in {corpus}: {error}") from error
    if result.returncode != 0:
        raise ReleaseError(f"not a corpus commit: {ref} in {corpus}")
    return result.stdout.strip()


def candidate(request: BuildRequest, observe: Observe) -> Candidate:
    """Probe, then key: the observation precedes the decision whether to build."""
    corpus_commit = _corpus_commit(request.corpus, request.corpus_ref)
    expected = expected_checkout(request.checkout, request.corpus)
    document = observe(expected)
    toolchain = toolchain_fingerprint(request.checkout)
    key = release_key(corpus_commit, expected.lovspor_commit, document.state, toolchain)
    return Candidate(corpus_commit, document, key)


def already_live(releases: Path, live: str | None, key: ReleaseKey) -> bool:
    """The short-circuit: the whole key equal to the live release's, nothing less."""
    if live is None:
        return False
    return read_record(release_dir(releases, live)).release_key == key


def _new_build_dir(releases: Path) -> Path:
    releases.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix=BUILD_PREFIX, dir=releases))
    # mkdtemp creates 0700; the trees below are served by another identity.
    build.chmod(0o755)
    return build


def _build_trees(request: BuildRequest, found: Candidate, build: Path) -> None:
    """Step 1: the corpus, then the site from the manifest beside it and the document."""
    emit_site(request.corpus, found.corpus_commit, build / CORPUS_DIR)
    capabilities = build / CAPABILITIES_NAME
    capabilities.write_bytes(document_bytes(found.document))
    report = build_site(
        SiteInputs(
            checkout=request.checkout,
            corpus=request.corpus,
            corpus_manifest=build / CORPUS_DIR / MANIFEST_NAME,
            capabilities=capabilities,
            out=build / SITE_DIR,
        )
    )
    capabilities.unlink()
    if report.release_key != found.key:
        raise ReleaseError("the site build's release_key is not the candidate's")


def _record(found: Candidate, structure: Structure, content_id: str) -> ReleaseRecord:
    process = found.document.observation.process
    manifest = structure.manifest
    return ReleaseRecord(
        schema_version="1",
        release_content_id=content_id,
        release_key=found.key,
        site_manifest_sha256=str(structure.facts["site_manifest_sha256"]),
        capability_sha256=str(structure.facts["capability_sha256"]),
        observed_at=process.observed_at,
        observer=process.observer,
        corpus=CorpusSummary(
            corpus_commit=manifest.corpus_commit,
            corpus_commit_time=manifest.corpus_commit_time,
            engine_version=manifest.engine_version,
            documents=manifest.documents,
        ),
    )


def _finalize(build: Path, found: Candidate, checkpoint: Checkpoint) -> str:
    """Steps 2-5 under the temporary name."""
    structure = check_structure(build)
    checkpoint("checked")
    content_id = release_content_id(build / CORPUS_DIR, build / SITE_DIR)
    checkpoint("identified")
    write_release_content_id(build / SITE_DIR / FACTS_NAME, content_id)
    write_record(build, _record(found, structure, content_id))
    write_fragment(build, fragment_text(build.resolve().parent / content_id, content_id))
    checkpoint("written")
    check_envelope(build)
    checkpoint("verified")
    return content_id


def _same_release(existing: Path, build: Path, content_id: str) -> bool:
    """Same recomputed id and the same record: the same finalized release."""
    if not is_complete(existing):
        return False
    if release_content_id(existing / CORPUS_DIR, existing / SITE_DIR) != content_id:
        return False
    return (existing / RECORD_NAME).read_bytes() == (build / RECORD_NAME).read_bytes()


def _place(build: Path, releases: Path, content_id: str, checkpoint: Checkpoint) -> bool:
    """Step 6: one rename, or reuse, or refuse. Returns whether the existing one was reused."""
    final = release_dir(releases, content_id)
    checkpoint("renaming")
    if final.exists():
        if not _same_release(final, build, content_id):
            raise ReleaseConflictError(
                f"{final} exists and is not the same finalized release as {build.name}; "
                "neither directory was touched"
            )
        shutil.rmtree(build)
        return True
    build.rename(final)
    checkpoint("renamed")
    return False


def build_release(
    request: BuildRequest,
    observe: Observe,
    live: str | None,
    checkpoint: Checkpoint = _no_checkpoint,
) -> BuildOutcome:
    """The procedure above; ``live`` is the reconciled live release the caller established."""
    found = candidate(request, observe)
    if live is not None and already_live(request.releases, live, found.key):
        return BuildOutcome(release_content_id=live, already_live=True, reused=False)
    build = _new_build_dir(request.releases)
    try:
        _build_trees(request, found, build)
        checkpoint("built")
        if live is not None:
            hardlink_unchanged(build, release_dir(request.releases, live))
        checkpoint("linked")
        content_id = _finalize(build, found, checkpoint)
        reused = _place(build, request.releases, content_id, checkpoint)
    except ReleaseConflictError:
        raise
    except (ReleaseError, SiteBuildError, PublishError):
        shutil.rmtree(build, ignore_errors=True)
        raise
    return BuildOutcome(release_content_id=content_id, already_live=False, reused=reused)
