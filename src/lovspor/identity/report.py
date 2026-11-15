"""Write the identity-migration dry-run report (#871, migration plan §3.6).

The output directory receives the files the repair would write
(``planned/manifest.json``, ``planned/slugs.json`` and ``planned/identity.json``,
the declaration that moves the corpus to the enforced identity schema), each
repaired history file under ``planned/<corpus path>``, ``report.json``,
``report.md`` and ``SHA256SUMS`` over all of them. Every byte is a function of
the plan: no clock, no host path, sorted keys and lists, so two runs on one
base give byte-identical directories.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from lovspor.atomic_io import atomic_write_text
from lovspor.identity.audit import POST, PRE, outcome_counts
from lovspor.identity.migration import is_detached
from lovspor.identity.plan import MigrationPlan
from lovspor.identity.registry import THRESHOLD_EXCEPTION, registry_json
from lovspor.identity.repair_report import (
    HISTORY_REPAIR_NOTE,
    REPAIR_SECTIONS,
    repair_counts,
    repair_rows,
)
from lovspor.identity.schema import ENFORCED_IDENTITY_SCHEMA, IDENTITY_FILENAME, declaration_json
from lovspor.storage.manifest import Manifest

REPORT_SCHEMA_VERSION = 5
PLANNED_DIR = "planned"
SUMS_FILENAME = "SHA256SUMS"


def manifest_json(manifest: Manifest) -> str:
    """``manifest.json`` exactly as ``write_manifest`` writes it."""
    data = manifest.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def planned_files(plan: MigrationPlan) -> dict[str, str]:
    """``corpus-relative path -> content`` of every file the repair plans: the root
    files, and each repaired history file under its corpus path."""
    return {
        "manifest.json": manifest_json(plan.planned_manifest),
        "slugs.json": registry_json(plan.planned_registry),
        IDENTITY_FILENAME: declaration_json(),
        **plan.history_repair.files,
        **plan.moves.markdown,
    }


def _counts(plan: MigrationPlan) -> dict[str, int]:
    return {**_record_counts(plan), **_slug_counts(plan), **repair_counts(plan.history_repair)}


def _record_counts(plan: MigrationPlan) -> dict[str, int]:
    base_ids = set(plan.base.manifest.documents)
    return {
        "manifest_revisions": plan.manifest_revisions,
        "base_documents": len(base_ids),
        "planned_documents": len(plan.planned_manifest.documents),
        "restored_tombstones": len(plan.restored),
        "detached_tombstone_paths": len(_detached(plan)),
        "history_files": len(plan.base.history),
        "history_files_without_manifest_record": sum(
            1 for doc_id in plan.base.history.values() if doc_id not in base_ids
        ),
        "orphan_history_doc_ids": len(plan.orphan_history_doc_ids),
        "orphan_history_doc_ids_not_restored": len(plan.orphans_not_restored),
        "history_owner_mismatches": len(plan.history_mismatches),
        "unrecoverable_doc_ids": len(plan.unrecoverable),
        "changed_paths": len(plan.changed_paths),
    }


def _detached(plan: MigrationPlan) -> list[str]:
    """Removed records this plan moves off a shared path (#912), by ``doc_id``."""
    base = plan.base.manifest.documents
    return [
        doc_id
        for doc_id, record in plan.planned_manifest.documents.items()
        if is_detached(record) and not (doc_id in base and is_detached(base[doc_id]))
    ]


def _slug_counts(plan: MigrationPlan) -> dict[str, int]:
    return {
        "registry_entries": sum(len(e) for e in plan.planned_registry.slugs.values()),
        "registry_conflicts": len(plan.registry_conflicts),
        "threshold_exceptions": len(_threshold_exceptions(plan)),
        "reused_slugs": len(plan.fates),
        "reused_slugs_pre_threshold": sum(1 for f in plan.fates if PRE in f.reuse),
        "reused_slugs_post_threshold": sum(1 for f in plan.fates if POST in f.reuse),
        "u1_renames": len(plan.renames),
        "u1_renames_with_doc_id_suffix": sum(1 for r in plan.renames if r.suffix_note),
        "restored_tombstone_collisions": len(plan.collisions),
        **{f"collisions_{k}": v for k, v in outcome_counts(plan.collisions).items()},
    }


def _threshold_exceptions(plan: MigrationPlan) -> list[dict[str, object]]:
    """Registry entries whose owner the U1 threshold set although an earlier
    document held the slug (``basis: threshold-exception``), with the prior
    holders' manifest evidence. Migration exceptions, not first ownership."""
    return [
        {"directory": directory, "slug": slug, **entry.model_dump(mode="json")}
        for directory, entries in sorted(plan.planned_registry.slugs.items())
        for slug, entry in sorted(entries.items())
        if entry.basis == THRESHOLD_EXCEPTION
    ]


THRESHOLD_EXCEPTION_NOTE = (
    "threshold_exceptions are migration exceptions: the U1 threshold kept the "
    "holder at the threshold as owner although the prior holders held the slug "
    "first; the entry records that basis and is not a claim of first ownership"
)
_SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Fate of every reused slug", "slug_fates", ("directory", "slug", "owner", "reuse", "holders")),
    (
        "Threshold exceptions (owner set by the U1 threshold, not the first holder)",
        "threshold_exceptions",
        ("directory", "slug", "owner", "basis", "prior_holders"),
    ),
    (
        "U1 rename plan",
        "u1_renames",
        ("directory", "doc_id", "from_slug", "to_slug", "suffix_note"),
    ),
    (
        "Restored-tombstone collision audit",
        "restored_tombstone_collisions",
        (
            "directory",
            "slug",
            "restored_doc_id",
            "last_seen_commit",
            "missing_since_commit",
            "holder_at_base",
            "holder_status",
            "owner",
            "owning_side",
            "outcome",
        ),
    ),
    ("History owner mismatches", "history_owner_mismatches", ("path", "doc_id_in_file", "owner")),
    (
        "Registry conflicts",
        "registry_conflicts",
        ("directory", "slug", "registered_owner", "planned_owner"),
    ),
)


def _review_lists(plan: MigrationPlan) -> dict[str, list[dict[str, object]]]:
    """The lists the owner reviews, as JSON rows."""
    return {
        "slug_fates": [f.model_dump(mode="json") for f in plan.fates],
        "threshold_exceptions": _threshold_exceptions(plan),
        "u1_renames": [r.model_dump(mode="json") for r in plan.renames],
        "restored_tombstone_collisions": [c.model_dump(mode="json") for c in plan.collisions],
        "history_owner_mismatches": [m.model_dump(mode="json") for m in plan.history_mismatches],
        "registry_conflicts": [c.model_dump(mode="json") for c in plan.registry_conflicts],
        "restored_tombstones": [t.model_dump(mode="json") for t in plan.restored],
        "detached_tombstones": [
            {"doc_id": d, "markdown_path": plan.planned_manifest.documents[d].markdown_path}
            for d in _detached(plan)
        ],
        **repair_rows(plan.history_repair),
    }


def _identity_fields(plan: MigrationPlan) -> dict[str, object]:
    """The identity schema the base declares and the plan moves it to."""
    return {
        "base_registry_present": plan.base.registry is not None,
        "base_identity_schema": plan.base.identity_schema,
        "planned_identity_schema": ENFORCED_IDENTITY_SCHEMA,
        "threshold_exception_note": THRESHOLD_EXCEPTION_NOTE,
    }


def report_data(plan: MigrationPlan) -> dict[str, object]:
    """The machine-readable report; ``report.md`` renders the same data."""
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "issue": 871,
        "mode": "dry-run",
        "base_commit": plan.base.commit,
        **_identity_fields(plan),
        "nothing_to_do": plan.nothing_to_do,
        "blocked": plan.blocked,
        "counts": _counts(plan),
        **_write_fields(plan),
        "history_repair_note": HISTORY_REPAIR_NOTE,
        "history_repair_invariant_ok": not plan.history_repair.invariant_violations,
        "u1_threshold": plan.threshold.model_dump(mode="json"),
        "unrecoverable_doc_ids": plan.unrecoverable,
        "orphan_history_doc_ids_not_restored": plan.orphans_not_restored,
        **_review_lists(plan),
    }


def _write_fields(plan: MigrationPlan) -> dict[str, object]:
    """What the apply writes: planned digests, changed paths, and the rename moves beyond
    ``planned/`` (#910): sidecar digests, moved-away sources, refused rewrites."""
    changed = "".join(f"{path}\n" for path in plan.changed_paths)
    return {
        "planned_files_sha256": _planned_sha256(plan),
        "changed_paths": plan.changed_paths,
        "changed_paths_sha256": sha256_text(changed),
        "planned_sidecars_sha256": {m.target: m.sha256 for m in plan.moves.sidecars},
        "moved_from_paths": plan.moves.sources,
        "move_problems": plan.moves.problems,
    }


def _planned_sha256(plan: MigrationPlan) -> dict[str, str]:
    return {
        f"{PLANNED_DIR}/{name}": sha256_text(text) for name, text in planned_files(plan).items()
    }


def _holder_text(holder: Mapping[str, object]) -> str:
    """One holder in a table cell: id, role and status when known, manifest span, move."""
    facts = [str(holder[k]) for k in ("role", "status") if k in holder]
    span = f"{holder['first_commit']}..{holder['last_commit']}"
    moved = f" → {holder['renamed_to']}" if holder.get("renamed_to") else ""
    if not moved and holder.get("slug_after_plan"):
        moved = f", now at `{holder['slug_after_plan']}`"
    return f"`{holder['doc_id']}` ({', '.join([*facts, span])}){moved}"


def _cell(value: object) -> str:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return "; ".join(_holder_text(holder) for holder in value)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return f"`{value}`"


def _table(rows: Sequence[Mapping[str, object]], columns: tuple[str, ...]) -> list[str]:
    if not rows:
        return ["(none)", ""]
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(_cell(row.get(c)) for c in columns) + " |" for row in rows]
    return [*lines, ""]


def _header(plan: MigrationPlan) -> list[str]:
    counts = [{"count": k, "value": v} for k, v in _counts(plan).items()]
    sums = [{"file": k, "sha256": v} for k, v in _planned_sha256(plan).items()]
    changed = "".join(f"{path}\n" for path in plan.changed_paths)
    return [
        "# Identity migration dry run (#871)",
        "",
        f"Base commit: `{plan.base.commit}`. Mode: dry-run; nothing was written to the corpus.",
        f"U1 threshold: `{plan.threshold.ref}` = `{plan.threshold.commit}` "
        f"(problem: `{plan.threshold.problem}`).",
        f"Nothing to do: `{plan.nothing_to_do}`. Blocked: `{plan.blocked}`.",
        "",
        "## Counts",
        "",
        *_table(counts, ("count", "value")),
        "## Checksums",
        "",
        *_table(sums, ("file", "sha256")),
        f"SHA-256 over the sorted changed-path list: `{sha256_text(changed)}`.",
        "",
    ]


def _identity_schema_lines(plan: MigrationPlan) -> list[str]:
    return [
        "## Identity schema",
        "",
        f"Declared at the base: `{plan.base.identity_schema}`. Applying the plan writes "
        f"`{IDENTITY_FILENAME}` declaring `{ENFORCED_IDENTITY_SCHEMA}`, from which point the "
        f"sync refuses to run without a readable `slugs.json`.",
        "",
        f"{THRESHOLD_EXCEPTION_NOTE}.",
        "",
    ]


def render_markdown(plan: MigrationPlan) -> str:
    """``report.md``: counts, checksums and the review lists of ``report.json``."""
    lists = _review_lists(plan)
    lines = [*_header(plan), *_identity_schema_lines(plan)]
    for title, key, columns in (*_SECTIONS, *REPAIR_SECTIONS):
        lines += [f"## {title}", "", *_table(lists[key], columns)]
    unrecoverable = [{"doc_id": doc_id} for doc_id in plan.unrecoverable]
    lines += ["## Unrecoverable doc_ids", "", *_table(unrecoverable, ("doc_id",))]
    not_restored = [{"doc_id": doc_id} for doc_id in plan.orphans_not_restored]
    lines += ["## Orphan-history doc_ids not restored", "", *_table(not_restored, ("doc_id",))]
    violations = plan.history_repair.invariant_violations
    lines += ["## History repair", "", f"{HISTORY_REPAIR_NOTE}.", ""]
    lines += [f"Invariant (each event in exactly one document's history): `{not violations}`."]
    lines += [*(f"- {violation}" for violation in violations), ""]
    return "\n".join(lines)


def report_files(plan: MigrationPlan) -> dict[str, str]:
    """``name -> content`` of every output file but ``SHA256SUMS``."""
    files = {f"{PLANNED_DIR}/{name}": text for name, text in planned_files(plan).items()}
    files["report.json"] = (
        json.dumps(report_data(plan), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    )
    files["report.md"] = render_markdown(plan)
    return files


def report_sums(plan: MigrationPlan) -> dict[str, str]:
    """``name -> sha256`` of every output file, exactly as ``SHA256SUMS`` lists them."""
    return {name: sha256_text(text) for name, text in sorted(report_files(plan).items())}


def write_report(plan: MigrationPlan, out: Path) -> dict[str, str]:
    """Write every output file under ``out``; return ``name -> sha256`` as in ``SHA256SUMS``."""
    files = report_files(plan)
    for name, text in files.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(out / name, text)
    sums = {name: sha256_text(text) for name, text in sorted(files.items())}
    atomic_write_text(out / SUMS_FILENAME, "".join(f"{h}  {n}\n" for n, h in sums.items()))
    return sums
