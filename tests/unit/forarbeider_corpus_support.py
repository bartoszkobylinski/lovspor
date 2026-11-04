"""Synthetic manifest members for the forarbeider corpus tests (#738, PR3B-2).

Built from the structural fixture the release tests stage: an invented
proposition, so no real government text reaches a test, a log or CI.
"""

import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from lovspor.forarbeider.chapter_models import ChapterResult
from lovspor.forarbeider.corpus_transaction import finish, pending
from lovspor.forarbeider.corpus_tree import read_dataset
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import ForarbeidRecord
from lovspor.forarbeider.release_models import destination_of, document_of
from tests.unit.forarbeider_release_support import (
    AT,
    DECIDED,
    DOSSIER_AT,
    REVIEWED,
    Desk,
)

DIGEST = "a" * 64


def metadata_of(desk: Desk) -> ForarbeidRecord:
    return ForarbeidRecord.model_validate_json(desk.metadata.read_bytes())


def receipt(result: ChapterResult) -> dict[str, Any]:
    return {
        "chapter_number": result.chapter_number,
        "chapter_id": result.id,
        "chapter_title": result.chapter_title,
        "source_page_url": result.source_page_url,
        "source_sha256": result.source_sha256,
        "text_sha256": result.text_sha256,
        "extractor_version": 1,
        "normalizer_version": 1,
        "retrieved_at": result.retrieved_at.isoformat().replace("+00:00", "Z"),
        "review_decided_at": REVIEWED,
    }


def times(withdrawn_at: str | None = None) -> dict[str, Any]:
    return {
        "source_published_at": None,
        "reviewed_at": DOSSIER_AT,
        "owner_decided_at": DECIDED,
        "released_at": AT,
        "withdrawn_at": withdrawn_at,
        "time_basis": "operator_supplied_unauthenticated",
        "recorded_at": "git_commit_time",
    }


def member(desk: Desk, chapters: Sequence[int] = (2, 3), **changes: Any) -> dict[str, Any]:
    """A released member of ``desk``'s proposition, with ``changes`` laid over it."""
    metadata = metadata_of(desk)
    document = document_of(metadata)
    listed = [chapter.chapter_number for chapter in metadata.chapters]
    complete = list(chapters) == listed
    built: dict[str, Any] = {
        "document": document.model_dump(mode="json"),
        "title": metadata.title,
        "ministry": metadata.ministry,
        "source_document_type": "forarbeid",
        "normative": False,
        "source_provider": "regjeringen.no",
        "rights_basis": "åndsverkloven § 14",
        "source_license": None,
        "asserts_complete_proposition": False,
        "release_status": "released",
        "path": destination_of(document),
        "markdown_sha256": DIGEST,
        "listed_chapters": listed,
        "chapters": [receipt(desk.results[number]) for number in chapters],
        "coverage": "every_listed_chapter" if complete else "partial_curated_excerpt",
        "times": times(),
    }
    return {**built, **changes}


def manifest(*members: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "forarbeider_corpus_manifest",
        "dataset": "forarbeider",
        "renderer_version": 1,
        "documents": sorted(members, key=lambda item: item["path"]),
    }


def beside_fifo(
    action: Callable[[], object], fifo: Path
) -> tuple[bool, list[ForarbeiderRefusedError]]:
    """Run ``action`` while ``fifo`` sits where it reads: (blocked, refusals).

    A reader that opens the FIFO without O_NONBLOCK waits for a writer forever.
    After a bounded wait the test opens the writing end itself, so a blocked
    reader is released and the test fails instead of hanging the process.
    """
    refusals: list[ForarbeiderRefusedError] = []

    def run() -> None:
        try:
            action()
        except ForarbeiderRefusedError as error:
            refusals.append(error)

    reader = threading.Thread(target=run, daemon=True)
    reader.start()
    reader.join(timeout=5)
    blocked = reader.is_alive()
    if blocked:
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        reader.join(timeout=5)
    return blocked, refusals


def read_beside_fifo(checkout: Path, fifo: Path) -> tuple[bool, list[RefusalReason]]:
    """``read_dataset`` of ``checkout`` beside ``fifo``: (blocked, refusal reasons)."""
    blocked, refusals = beside_fifo(lambda: read_dataset(checkout), fifo)
    return blocked, [error.reason for error in refusals]


def refusal_of(action: Callable[[], object], fifo: Path | None = None) -> ForarbeiderRefusedError:
    """The one refusal ``action`` raises, bounded in time when ``fifo`` is in its way."""
    if fifo is None:
        try:
            action()
        except ForarbeiderRefusedError as error:
            return error
        raise AssertionError("no refusal raised")
    blocked, refusals = beside_fifo(action, fifo)
    assert not blocked, "blocked on a FIFO"
    assert len(refusals) == 1, refusals
    return refusals[0]


def finish_pending(sandbox: Path) -> bool:
    """Replay whatever change is journaled, whatever decision it names: whether there was one.

    Only for transaction-level tests; the commands check the decision first.
    """
    journal = pending(sandbox)
    if journal is None:
        return False
    finish(sandbox, journal)
    return True
