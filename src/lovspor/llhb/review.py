"""Manual-review support for the LLHB candidate pool (Stage 3.5).

Turns the generated manual-review queue into an executable owner
process: per-case review items with all decision-relevant evidence,
a decisions file whose incompleteness is detectable, a closed
reason-code vocabulary, and the deterministic Stage 4 gate.

Nothing here decides anything: decisions belong to the owner. The
library only prepares evidence, records what the owner enters, and
refuses to unblock Stage 4 while any queued case lacks a final
disposition or any ``needs_fix`` case is unresolved. No benchmark
model output exists at this stage and none is consulted.
"""

import json
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from lovspor.errors import LovsporError

KEEP_REASONS: Final[tuple[str, ...]] = (
    "valid",
    "useful-ambiguity",
    "good-adversarial-case",
    "acceptable-near-duplicate",
    "scope-boundary-clear",
)

DROP_REASONS: Final[tuple[str, ...]] = (
    "linguistically-unnatural",
    "redundant",
    "scope-boundary-unclear",
    "ambiguous-ground-truth",
    "weak-adversarial-case",
    "category-mismatch",
    "corpus-evidence-insufficient",
)

NEEDS_FIX_REASONS: Final[tuple[str, ...]] = (
    "wording-only",
    "metadata-error",
    "classification-review",
)

REASONS_BY_DECISION: Final[dict[str, tuple[str, ...]]] = {
    "keep": KEEP_REASONS,
    "drop": DROP_REASONS,
    "needs_fix": NEEDS_FIX_REASONS,
}

_GENERIC_TOPIC_MAX_WORDS = 2

_EVIDENCE_STRING_KEYS: Final[frozenset[str]] = frozenset(
    {"slug", "section_id", "source_class", "authority", "status", "subtype"},
)
"""Evidence keys whose STRING values are identifiers/metadata, safe to show.

Every other string value in oracle evidence may carry rendered statutory
text (headings, validate_citation reasons with section inventories) and is
dropped from review items — packets are coordinates + hashes + counts only
(Codex, PR #18 finding 2)."""


class ReviewError(LovsporError):
    """A decisions file violates the review contract."""


class ReviewDecision(BaseModel):
    """One owner decision; ``decision is None`` means not yet reviewed."""

    case_id: str
    category: str
    queue_reasons: list[str]
    decision: str | None = None  # keep | drop | needs_fix
    reviewer: str | None = None
    reviewed_at: str | None = None
    reason_code: str | None = None
    notes: str | None = None


class ReviewItem(BaseModel):
    """Everything the owner needs to decide one queued case."""

    case_id: str
    category: str
    subcategory: str
    difficulty: str
    question: str
    expected_behaviour: str
    queue_reasons: list[str]
    expected_act_slug: str | None
    expected_section_id: str | None
    expected_occurrence: int | None
    claimed_act_slug: str | None
    claimed_section_id: str | None
    citation_exists: bool | None
    quote_ref: dict[str, Any] | None
    fabricated_quote_text: str | None
    ground_truth_evidence: dict[str, Any]
    validator_status: str
    validator_issues: list[dict[str, Any]]
    near_duplicates: list[str]
    provenance: dict[str, Any]
    structural_notes: list[str]


class CompletenessReport(BaseModel):
    total_queued: int
    reviewed: int
    remaining: int
    keep: int
    drop: int
    needs_fix: int
    c5_remaining: list[str]
    c8_remaining: list[str]
    stratified_remaining: list[str]
    invalid_records: list[str]
    stage4_unblocked: bool


def init_decisions(queue: list[dict[str, Any]]) -> list[ReviewDecision]:
    """Empty decisions template: immutable metadata only, decisions null."""
    return [
        ReviewDecision(
            case_id=str(entry["case_id"]),
            category=str(entry["case_id"]).split("-")[2],
            queue_reasons=[str(r) for r in entry["reasons"]],
        )
        for entry in queue
    ]


def load_decisions(path: Path) -> list[ReviewDecision]:
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                records.append(ReviewDecision.model_validate_json(line))
            except ValueError as exc:
                raise ReviewError(f"{path}:{number}: invalid decision record: {exc}") from exc
    return records


def save_decisions(path: Path, decisions: list[ReviewDecision]) -> None:
    lines = "".join(
        json.dumps(d.model_dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for d in decisions
    )
    path.write_text(lines, encoding="utf-8")


def decision_problems(record: ReviewDecision) -> list[str]:
    """Contract violations of one decision record (empty = fine).

    A pending record (decision None) is fine; a decided record must be
    complete and use a reason code from the closed list for its decision.
    """
    if record.decision is None:
        return []
    problems = []
    codes = REASONS_BY_DECISION.get(record.decision)
    if codes is None:
        problems.append(f"unknown decision {record.decision!r}")
    elif record.reason_code not in codes:
        problems.append(f"reason_code {record.reason_code!r} not valid for {record.decision}")
    if not record.reviewer:
        problems.append("reviewer missing")
    if not record.reviewed_at:
        problems.append("reviewed_at missing")
    return problems


def completeness(
    queue: list[dict[str, Any]],
    decisions: list[ReviewDecision],
) -> CompletenessReport:
    """The deterministic Stage 4 gate over queue + decisions.

    The decisions file must correspond to the queue EXACTLY: every queued
    case once, no duplicates, no stray rows. Codex (PR #18 finding 1)
    showed the set-based version passed a file with duplicate conflicting
    rows or an extra non-queued id — both must block.
    """
    queue_ids = {str(e["case_id"]) for e in queue}
    id_counts: dict[str, int] = {}
    for decision in decisions:
        id_counts[decision.case_id] = id_counts.get(decision.case_id, 0) + 1
    invalid = sorted(
        f"{d.case_id}: {problem}" for d in decisions for problem in decision_problems(d)
    )
    invalid += sorted(
        f"{case_id}: appears {count} times in decisions file"
        for case_id, count in id_counts.items()
        if count > 1
    )
    invalid += sorted(
        f"{case_id}: not in the review queue" for case_id in id_counts if case_id not in queue_ids
    )
    reviewed_ids = {
        d.case_id for d in decisions if d.decision is not None and d.case_id in queue_ids
    }
    remaining = [str(e["case_id"]) for e in queue if str(e["case_id"]) not in reviewed_ids]
    missing = [str(e["case_id"]) for e in queue if str(e["case_id"]) not in id_counts]
    counts = {"keep": 0, "drop": 0, "needs_fix": 0}
    for decision in decisions:
        if decision.decision in counts:
            counts[decision.decision] += 1
    report = CompletenessReport(
        total_queued=len(queue),
        reviewed=len(queue) - len(remaining),
        remaining=len(remaining),
        keep=counts["keep"],
        drop=counts["drop"],
        needs_fix=counts["needs_fix"],
        c5_remaining=[i for i in remaining if "-C5-" in i],
        c8_remaining=[i for i in remaining if "-C8-" in i],
        stratified_remaining=_stratified_remaining(queue, reviewed_ids),
        invalid_records=invalid + [f"{i}: missing from decisions file" for i in missing],
        stage4_unblocked=False,
    )
    report.stage4_unblocked = (
        report.remaining == 0 and report.needs_fix == 0 and not report.invalid_records
    )
    return report


def _stratified_remaining(queue: list[dict[str, Any]], reviewed: set[str]) -> list[str]:
    return [
        str(e["case_id"])
        for e in queue
        if "stratified-10pct-sample" in e["reasons"] and str(e["case_id"]) not in reviewed
    ]


def near_duplicate_clusters(flags: list[dict[str, Any]]) -> list[list[str]]:
    """Union-find over near-duplicate pairs → sorted clusters."""
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for flag in flags:
        left, right = find(str(flag["a"])), find(str(flag["b"]))
        if left != right:
            parent[right] = left
    clusters: dict[str, set[str]] = {}
    for node in parent:
        clusters.setdefault(find(node), set()).add(node)
    return sorted(sorted(members) for members in clusters.values())


def build_review_items(
    queue: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    dedup_flags: list[dict[str, Any]],
) -> list[ReviewItem]:
    """One evidence-complete review item per queued case, queue order."""
    cases = {str(c["case_id"]): c for c in candidates}
    ledger_by_id = {str(entry["case_id"]): entry for entry in ledger}
    neighbours: dict[str, list[str]] = {}
    for flag in dedup_flags:
        neighbours.setdefault(str(flag["a"]), []).append(str(flag["b"]))
        neighbours.setdefault(str(flag["b"]), []).append(str(flag["a"]))
    items = []
    for entry in queue:
        case_id = str(entry["case_id"])
        case = cases.get(case_id)
        if case is None:
            raise ReviewError(f"queued case {case_id} is not in the candidate pool")
        near = sorted(neighbours.get(case_id, []))
        items.append(_review_item(case, entry, ledger_by_id.get(case_id), near))
    return items


def _review_item(
    case: dict[str, Any],
    entry: dict[str, Any],
    ledger_entry: dict[str, Any] | None,
    near: list[str],
) -> ReviewItem:
    return ReviewItem(
        case_id=str(case["case_id"]),
        category=str(case["category"]),
        subcategory=str(case["subcategory"]),
        difficulty=str(case["difficulty"]),
        question=str(case["question"]),
        expected_behaviour=str(case["expected_behaviour"]),
        queue_reasons=[str(r) for r in entry["reasons"]],
        expected_act_slug=case.get("expected_act_slug"),
        expected_section_id=case.get("expected_section_id"),
        expected_occurrence=case.get("expected_occurrence"),
        claimed_act_slug=case.get("claimed_act_slug"),
        claimed_section_id=case.get("claimed_section_id"),
        citation_exists=case.get("citation_exists"),
        quote_ref=case.get("quote_ref"),
        fabricated_quote_text=case.get("fabricated_quote_text"),
        ground_truth_evidence=sanitize_evidence(dict(case["ground_truth_evidence"])),
        validator_status=str(ledger_entry["status"]) if ledger_entry else "unknown",
        validator_issues=list(ledger_entry["issues"]) if ledger_entry else [],
        near_duplicates=near,
        provenance=dict(case["provenance"]),
        structural_notes=_structural_notes(case),
    )


def sanitize_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Evidence view safe for committed artifacts: no free-text values."""
    sanitized = _sanitize_value(evidence, None)
    return sanitized if isinstance(sanitized, dict) else {}


_EVIDENCE_DROP_KEYS: Final[frozenset[str]] = frozenset({"heading", "reason"})
"""Keys that only ever carry rendered text — dropped even when null."""


def _sanitize_value(value: Any, key: str | None) -> Any:
    if isinstance(value, dict):
        cleaned = {
            k: _sanitize_value(v, k) for k, v in value.items() if k not in _EVIDENCE_DROP_KEYS
        }
        return {k: v for k, v in cleaned.items() if v is not _DROPPED}
    if isinstance(value, list):
        items = [_sanitize_value(v, key) for v in value]
        return [v for v in items if v is not _DROPPED]
    if isinstance(value, str):
        return value if key in _EVIDENCE_STRING_KEYS else _DROPPED
    return value


_DROPPED: Final = object()


def _structural_notes(case: dict[str, Any]) -> list[str]:
    """Purely structural observations — never a legal or model judgment."""
    notes = []
    category = str(case["category"])
    if category == "C5":
        notes.extend(_c5_notes(case))
    if category == "C8":
        notes.extend(_c8_notes(case))
    if case.get("quote_ref") is not None:
        ref = case["quote_ref"]
        notes.append(
            "authentic quote by reference only — materialize locally: "
            f"slug={ref['slug']} section={ref['section_id']} span={ref.get('char_span')} "
            f"sha256={str(ref['sha256_normalized'])[:16]}…"
        )
    return notes


def _c5_notes(case: dict[str, Any]) -> list[str]:
    if case["subcategory"] == "duplicate-section-id":
        evidence = case["ground_truth_evidence"].get("duplicate_occurrences", {})
        occurrences = evidence.get("occurrences")
        count = len(occurrences) if occurrences is not None else evidence.get("count")
        notes = [
            f"ambiguity mechanism: duplicate section id — {case['expected_act_slug']} "
            f"§ {case['expected_section_id']} has {count} occurrences",
        ]
        if case.get("expected_behaviour") == "must_disambiguate":
            notes.append(
                "ground truth encodes every valid occurrence; must_disambiguate is "
                "the designed expectation (ruling 17) — review the question wording, "
                "not the ambiguity itself"
            )
        elif case.get("expected_occurrence") is None:
            notes.append(
                "DEBATABLE: no occurrence pinned — decide whether 'handle the ambiguity' "
                "is a fair expected behaviour for this question wording"
            )
        return notes
    return [
        f"ambiguity mechanism: repealed-as-current — manifest lists "
        f"{case['claimed_act_slug']!r} as removed (tombstone); the question presents "
        "it as current law, so the expected behaviour is rejection",
    ]


def _c8_notes(case: dict[str, Any]) -> list[str]:
    scope = case["ground_truth_evidence"].get("scope", {})
    notes = [
        f"requires source class {scope.get('source_class')!r}, which the corpus scope "
        f"excludes ({scope.get('authority')})",
        "manual judgment: could the statutory text alone still answer this adequately? "
        "If absence of an adequate answer cannot be reasonably established, DROP "
        "(scope-boundary-unclear) rather than inventing confidence",
    ]
    topic_words = _question_topic_words(str(case["question"]))
    if topic_words <= _GENERIC_TOPIC_MAX_WORDS:
        notes.append(
            "structural flag: very generic topic wording — likely DROP candidate "
            "(linguistically-unnatural)"
        )
    return notes


def _question_topic_words(question: str) -> int:
    """Rough structural size of the topic slot: words beyond the frame's ~9."""
    frame_overhead = 9
    return max(0, len(question.split()) - frame_overhead)
