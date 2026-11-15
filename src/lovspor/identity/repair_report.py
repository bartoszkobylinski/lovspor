"""The history-repair part of the identity-migration report (#871).

Every attributed event is a row with its provenance: the full commit, the
path, the ``id`` of the file before and after the commit, the rule that gave
it to its document, and whether the slug history at the base held it. The
provenance lives here, in the report, never as extra fields in the schema-1
history files.
"""

import hashlib

from lovspor.identity.repair_plan import HistoryRepair

HISTORY_REPAIR_NOTE = (
    "each history_repair event is attributed from its original commit: the front-matter id "
    "of the file at the commit, or for a deletion the id at its parent; nothing is copied "
    "from the history files at the base"
)

REPAIR_SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "History repair: attributed events",
        "history_repair_events",
        ("slug", "commit", "path", "before_doc", "after_doc", "doc_id", "role", "rule", "type"),
    ),
    (
        "History repair: excluded commits",
        "history_repair_excluded",
        ("slug", "commit", "path", "before_doc", "after_doc", "reason"),
    ),
    (
        "History repair: events in the slug history at the base",
        "history_repair_base_file_events",
        ("slug", "commit", "type", "doc_in_file", "attributed_to"),
    ),
    (
        "History repair: paths walked",
        "history_repair_segments",
        ("slug", "path", "tip", "moved_to", "moved_at"),
    ),
    ("History repair: planned files", "history_repair_files", ("file", "sha256", "changed")),
    (
        "History repair: conflicts (block the plan)",
        "history_repair_conflicts",
        ("file", "commit", "path", "doc_in_file", "attributed_to"),
    ),
)


def _role(doc_id: str, owner: str, renamed: dict[str, str]) -> str:
    if doc_id == owner:
        return "owner"
    return f"renamed to {renamed[doc_id]}" if doc_id in renamed else "other holder"


def _event_rows(repair: HistoryRepair) -> list[dict[str, object]]:
    return [
        {
            "directory": r.target.directory,
            "slug": r.target.slug,
            **e.model_dump(mode="json", exclude={"event"}),
            "role": _role(e.doc_id, r.target.owner, r.target.renamed),
            "type": e.event.type,
            "subject": e.event.subject,
            "date": e.event.date.isoformat(),
        }
        for r in repair.repairs
        for e in r.trace.events
    ]


def _excluded_rows(repair: HistoryRepair) -> list[dict[str, object]]:
    return [
        {"directory": r.target.directory, "slug": r.target.slug, **x.model_dump(mode="json")}
        for r in repair.repairs
        for x in r.trace.excluded
    ]


def _base_file_rows(repair: HistoryRepair) -> list[dict[str, object]]:
    return [
        {"slug": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode="json")}
        for r in repair.repairs
        for e in r.base_file_events
    ]


def _segment_rows(repair: HistoryRepair) -> list[dict[str, object]]:
    return [
        {"slug": r.target.slug, **s.model_dump(mode="json")}
        for r in repair.repairs
        for s in r.trace.segments
    ]


def _file_rows(repair: HistoryRepair) -> list[dict[str, object]]:
    changed = set(repair.changed)
    return [
        {"file": path, "sha256": _sha256(text), "changed": path in changed}
        for path, text in repair.files.items()
    ]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def repair_rows(repair: HistoryRepair) -> dict[str, list[dict[str, object]]]:
    """The history-repair review lists, as JSON rows."""
    return {
        "history_repair_events": _event_rows(repair),
        "history_repair_excluded": _excluded_rows(repair),
        "history_repair_base_file_events": _base_file_rows(repair),
        "history_repair_segments": _segment_rows(repair),
        "history_repair_files": _file_rows(repair),
        "history_repair_conflicts": [c.model_dump(mode="json") for c in repair.conflicts],
        "history_repair_invariant_violations": [
            {"violation": v} for v in repair.invariant_violations
        ],
    }


def repair_counts(repair: HistoryRepair) -> dict[str, int]:
    return {
        "history_repair_slugs": len(repair.repairs),
        "history_repair_events": sum(len(r.trace.events) for r in repair.repairs),
        "history_repair_excluded": sum(len(r.trace.excluded) for r in repair.repairs),
        "history_repair_files": len(repair.files),
        "history_repair_changed_files": len(repair.changed),
        "history_repair_conflicts": len(repair.conflicts),
        "history_repair_invariant_violations": len(repair.invariant_violations),
    }
