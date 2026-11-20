"""Synthetic fixtures for the AR4a publication-preparation tests (#738).

Every document is the invented structural proposition of the FC6 fixtures,
renumbered; every repository is a temporary Git repository made here; every
plan is sealed the way the engine's planner seals one. No real text, no
network, no key.

**What is synthetic, and why.** FC6 never computes ``publication_complete``
today (``publication_constraints_absent``), so no real FC8b selection can be
planned. The rows below therefore *state* ``source_complete`` and
``publication_complete`` and carry only the release-wide blockers, exactly as
the AR2 and AR3 tests do; the FC6 manifest, the raw pages and the FC7 decision
they bind are real bytes built from the fixtures.
"""

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from lovspor.forarbeider.document_extract import SourcePage, extract_document
from lovspor.forarbeider.policy_evaluation import (
    DocumentDecision,
    EntryDecision,
    PolicyDecision,
    decision_text,
)
from lovspor.forarbeider.publication_policy import policy_digest
from lovspor.forarbeider.release_contract_models import PlanEvidence
from lovspor.forarbeider.release_plan import Row, build_plan
from lovspor.forarbeider.release_plan_models import ReleasePlan
from lovspor.forarbeider.release_provenance import issue_provenance
from lovspor.forarbeider.release_quarantine_models import (
    QuarantinedDocument,
    QuarantineEvidence,
    QuarantineSet,
)
from lovspor.forarbeider.version_manifest import Evidence, build_version_manifest, manifest_text
from lovspor.forarbeider.version_models import PublicationState, SourceState
from lovspor.forarbeider.version_release_models import (
    DocumentBinding,
    DocumentSelection,
    digest_of,
    status_of,
)
from tests.unit.forarbeider_release_control_support import constraints, policy
from tests.unit.forarbeider_release_plan_support import (
    Store,
    context,
    document,
    policy_reference,
    release_wide,
)
from tests.unit.forarbeider_version_support import landing, pages

ROOT_SHA = "f" * 64
LEDGER_SHA = "c" * 64
PathName = Literal["A", "B"]


def _renumbered(page: SourcePage, number: int) -> SourcePage:
    def swap(text: str) -> str:
        return text.replace("id9000001", f"id{9000000 + number}").replace("901", str(900 + number))

    return SourcePage(swap(page.payload.decode()).encode(), swap(page.url), page.retrieved_at)


def source_pages(number: int, body: str | None = None) -> list[SourcePage]:
    """The fixture's chapter pages for document ``number``; ``body`` replaces chapter 2's text."""
    found = [_renumbered(page, number) for page in pages()]
    if body is not None:
        second = found[1]
        text = second.payload.decode().replace("<p>Tekst i kapittel 2.</p>", body)
        found[1] = second._replace(payload=text.encode())
    return found


def _decision(manifest_version: str, chosen: list[SourcePage], number: int) -> PolicyDecision:
    extraction = extract_document(chosen)
    entries = tuple(
        EntryDecision(
            entry_id=entry.id,
            part_id=part.id,
            source_sha256=part.source_sha256,
            decision="allow",
            rule="synthetic:allow",
            evidence_entries=(),
            reasons=("synthetic",),
        )
        for part in extraction.parts
        for entry in part.entries
    )
    chosen_policy = policy()
    return PolicyDecision(
        document_id=document(number),
        version_id=manifest_version,
        assessment_sha256="a" * 64,
        source_sha256s=tuple(sorted({part.source_sha256 for part in extraction.parts})),
        policy_id=chosen_policy.policy_id,
        policy_version=chosen_policy.policy_version,
        policy_sha256=policy_digest(chosen_policy),
        source_state=SourceState.SOURCE_COMPLETE,
        attachment_discovery="verified",
        entries=entries,
        attachments=(),
        document=DocumentDecision(rules_satisfied=True, blockers=()),
    )


def synthetic_row(store: Store, number: int, body: str | None = None, **changes: Any) -> Row:
    """Document ``number``: real FC6 manifest, raw pages and FC7 decision; stated complete."""
    chosen = source_pages(number, body)
    front = _renumbered(landing(), number)
    manifest = build_version_manifest(Evidence(tuple(chosen), front, ()))
    manifest_key = store.put(manifest_text(manifest).encode())
    raw = sorted(store.put(page.payload) for page in [*chosen, front])
    decision = _decision(manifest.version_id, chosen, number)
    decision_key = store.put(decision_text(decision).encode())
    blockers = release_wide()
    fields: dict[str, Any] = {
        "document_id": document(number),
        "status": status_of(blockers),
        "version_id": manifest.version_id,
        "source_sha256s": tuple(raw),
        "policy": policy_reference(),
        "processing": None,
        "source_state": SourceState.SOURCE_COMPLETE,
        "publication_state": PublicationState.PUBLICATION_COMPLETE,
        "blockers": blockers,
    }
    selection = DocumentSelection.model_validate(fields | changes)
    binding = DocumentBinding(
        document_id=document(number),
        lineage_status="verified",
        lineage_entries=1,
        claimed_head_sha256=None,
        lineage_head_sha256=None,
        manifest_sha256=manifest_key,
        assessment_sha256=None,
        policy_decision_sha256=decision_key,
        authorization_sha256=None,
    )
    return Row(selection, binding)


def quarantine_of(*numbers: int) -> QuarantineSet:
    return QuarantineSet(
        ledger_sha256=LEDGER_SHA,
        documents=tuple(
            QuarantinedDocument(
                document_id=document(n), reasons=("quarantine:stage:render_failed",)
            )
            for n in sorted(numbers)
        ),
    )


def sealed_quarantine(found: QuarantineSet, base_plan_id: str = "b" * 64) -> QuarantineEvidence:
    evidence = QuarantineEvidence(
        status="verified",
        source="engine_quarantine",
        reasons=(),
        quarantine=found,
        quarantine_sha256=digest_of(found),
        base_plan_id=base_plan_id,
        inventory_sha256="e" * 64,
        limits_sha256="a" * 64,
        trust_root_sha256=ROOT_SHA,
    )
    return issue_provenance(evidence, "quarantine_assessment")


def plan_of(
    store: Store,
    rows: Sequence[Row],
    numbers: Sequence[int],
    found: QuarantineSet | None = None,
    **caps: Any,
) -> ReleasePlan:
    limits = constraints(**caps) if caps else None
    return build_plan(context(numbers, rows, limits), rows, store.artifacts, found)


def sealed_plan(plan: ReleasePlan) -> PlanEvidence:
    evidence = PlanEvidence(
        status="verified",
        source="engine_release_planner",
        reasons=(),
        plan=plan,
        plan_sha256=digest_of(plan),
        trust_root_sha256=ROOT_SHA,
    )
    return issue_provenance(evidence, "batch_plan")


# --- temporary Git repositories ------------------------------------------------------------


def git(repository: Path, *argv: str) -> str:
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Synthetic",
        "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
        "GIT_COMMITTER_NAME": "Synthetic",
        "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
        "GIT_AUTHOR_DATE": "2099-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2099-01-01T00:00:00Z",
    }
    done = subprocess.run(
        ["git", "-C", str(repository), *argv],
        capture_output=True,
        check=True,
        env=environment,
        text=True,
    )
    return done.stdout.rstrip("\n")


def source_repository(directory: Path, files: dict[str, bytes] | None = None) -> tuple[Path, str]:
    """A repository with one commit on ``main``; its path and the commit."""
    repository = directory / "lovverk-forarbeider"
    repository.mkdir(mode=0o700)
    git(repository, "init", "--quiet", "--initial-branch=main")
    for name, payload in (files or {"README.md": b"Synthetic publication repository.\n"}).items():
        target = repository / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    git(repository, "add", "--all")
    git(repository, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "synthetic base")
    return repository, git(repository, "rev-parse", "HEAD")


def commit_files(repository: Path, files: dict[str, bytes], message: str) -> str:
    """The test's own stand-in for a publication: commit ``files`` on ``main``."""
    for name, payload in files.items():
        target = repository / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    git(repository, "add", "--all")
    git(repository, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", message)
    return git(repository, "rev-parse", "HEAD")


def snapshot(repository: Path) -> tuple[str, str, str]:
    """What a preparation must never change in the source: refs, HEAD and status."""
    return (
        git(repository, "for-each-ref", "--format=%(refname) %(objectname)"),
        git(repository, "rev-parse", "HEAD"),
        git(repository, "status", "--porcelain=v1", "--untracked-files=all", "--ignored"),
    )


def workdir(directory: Path) -> Path:
    found = directory / "work"
    found.mkdir(mode=0o700)
    return found
