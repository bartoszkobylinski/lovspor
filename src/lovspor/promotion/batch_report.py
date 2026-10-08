"""The holds report of a promotion batch: Markdown for the owner, JSON beside it (S8).

``batch-<batch_id>.md`` is what the owner reads before the spot-check: the
gate, the counts, holds by reason, the sampled items with the command that
shows each one, and every hold with its reason — a regulation captured on N
pages is one line naming its id, content_hash and first pages (#566), its
full page list in the sidecar. ``batch-<batch_id>.json``
carries the same assessment for scripts. Both are pure functions of the
assessment — no clock — so an unchanged batch rewrites identical bytes.

The report names archive URLs and regulation titles, so it is written
outside the engine repository and outside the corpus checkout (ADR-0010 §5:
nothing from the archive is copied into ``lovspor``). Personal-data holds
are reported by kind and line, never by value (ADR-0016 Decision 6).
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Iterable, Sequence
from pathlib import Path

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import PromotionRefusedError
from lovspor.promotion.batch import BatchAssessment, BatchItem

#: Pages of a candidate group the Markdown names; the JSON sidecar lists every one (#566).
FIRST_PAGES = 3


def summary(assessment: BatchAssessment) -> dict[str, int]:
    """The batch's counts, by outcome, sample and write."""
    items = assessment.items
    return {
        "candidates": len(items),
        "ready": sum(i.outcome == "ready" for i in items),
        "unchanged": sum(i.outcome == "unchanged" for i in items),
        "held": sum(i.outcome == "held" for i in items),
        "refused": sum(i.outcome == "refused" for i in items),
        "sampled": sum(i.sampled for i in items),
        "would_write": len(assessment.to_write),
    }


def report_json(assessment: BatchAssessment) -> str:
    payload = assessment.model_dump(mode="json") | {
        "summary": summary(assessment),
        "holds_by_reason": assessment.holds_by_reason,
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def report_markdown(assessment: BatchAssessment, corpus: Path) -> str:
    lines = [
        *_heading(assessment),
        *_gate(assessment),
        *_table(("measure", "count"), summary(assessment).items()),
        "",
        "## Holds by reason",
        "",
        *_table(("reason", "count"), assessment.holds_by_reason.items()),
        "",
        *_sample(assessment, corpus),
        *_holds(assessment.items, f"{_stem(assessment)}.json"),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def write_report(
    directory: Path, assessment: BatchAssessment, corpus: Path, forbidden: Sequence[Path]
) -> tuple[Path, Path]:
    """Write both files under ``directory``, refused inside the corpus or a ``forbidden`` tree."""
    target = _outside(directory, (corpus, *forbidden))
    target.mkdir(parents=True, exist_ok=True)
    stem = _stem(assessment)
    markdown, sidecar = target / f"{stem}.md", target / f"{stem}.json"
    atomic_write_text(markdown, report_markdown(assessment, corpus))
    atomic_write_text(sidecar, report_json(assessment))
    return markdown, sidecar


def _stem(assessment: BatchAssessment) -> str:
    return f"batch-{assessment.spec.batch_id}"


def _outside(directory: Path, forbidden: Sequence[Path]) -> Path:
    if not directory.is_absolute():
        msg = f"--report-dir must be an absolute path, got {directory}"
        raise PromotionRefusedError(msg)
    resolved = directory.resolve()
    for tree in (t.resolve() for t in forbidden):
        if resolved == tree or tree in resolved.parents:
            msg = f"--report-dir {resolved} is inside {tree}; the report names archive material"
            raise PromotionRefusedError(msg)
    return resolved


def _heading(assessment: BatchAssessment) -> list[str]:
    spec = assessment.spec
    return [
        f"# Promotion batch {spec.batch_id}",
        "",
        f"- authority: {spec.authority_id} (KLASS {spec.klass_version})",
        f"- classifier: {spec.classifier_version}, output sha256 "
        f"{assessment.classifier_output_sha256}",
        f"- extractor v{assessment.extractor_version}, renderer v{assessment.renderer_version}",
        f"- sample rate: {spec.sample_rate} (stated in the batch spec; ADR-0016 4g recommends 1 "
        "= 100 % for the first authority and every new adapter family)",
        f"- listed artifacts: {len(spec.artifacts) or 'all candidates'}",
        "",
    ]


def _gate(assessment: BatchAssessment) -> list[str]:
    gate = assessment.gate
    blocking = [
        f"- rejected in sample, blocks the batch: {k.sha256} {k.source_url}" for k in gate.rejected
    ]
    blocking += [f"- awaiting review: {k.sha256} {k.source_url}" for k in gate.awaiting_review]
    heading = [f"## Gate: {gate.verdict.upper()}", ""]
    return [*heading, *blocking, ""] if blocking else heading


def _table(header: tuple[str, str], rows: Iterable[tuple[str, int]]) -> list[str]:
    body = [f"| {name} | {count} |" for name, count in rows]
    return [f"| {header[0]} | {header[1]} |", "|---|--:|", *(body or ["| (none) | 0 |"])]


def _sample(assessment: BatchAssessment, corpus: Path) -> list[str]:
    lines = ["## Sample for review", ""]
    for item in (i for i in assessment.items if i.sampled):
        lines += [
            f"### {item.title}",
            "",
            f"- {item.key.source_url}",
            f"- sha256 {item.key.sha256}",
            f"- {item.doc_id} v{item.version} -> {item.markdown_path} ({item.outcome})",
            *_canonical(item),
            f"- review: {item.review.value}",
            f"- classifier: {item.classifier.class_name} on {', '.join(item.classifier.evidence)}",
            f"- read it: `{_preview(assessment, item, corpus)}`",
            "",
        ]
    return lines


def _canonical(item: BatchItem) -> list[str]:
    """A folded group is reviewed on its canonical page; the report says it has others (#566)."""
    if not item.sources:
        return []
    return [f"- canonical page of {len(item.sources)} that carry this text (#566), all in the JSON"]


def _preview(assessment: BatchAssessment, item: BatchItem, corpus: Path) -> str:
    spec = assessment.spec
    words = ["lovspor", "promote", "preview", "--authority", spec.authority_id]
    words += ["--artifact", item.key.sha256, "--corpus", str(corpus)]
    return shlex.join([*words, "--klass-version", spec.klass_version])


def _holds(items: tuple[BatchItem, ...], sidecar: str) -> list[str]:
    held: list[str] = []
    for item in (i for i in items if i.hold is not None):
        if item.sources:
            held += _group_hold(item, sidecar)
            continue
        held.append(f"- `{item.hold}` {item.key.sha256} {item.key.source_url}: {item.detail}")
        held += [f"  - personal data: {h.kind.value} on line {h.line}" for h in item.personal_data]
    return ["## Held and refused", "", *(held or ["(none)"])]


def _group_hold(item: BatchItem, sidecar: str) -> list[str]:
    """One line for one regulation on N pages (#566): the first pages, the rest in the JSON."""
    pages = item.sources
    lines = [
        f"- `{item.hold}` 1 regulation, {len(pages)} pages: {item.doc_id}, "
        f"content_hash {item.content_hash}: {item.detail}",
        *(f"  - {k.source_url} ({k.sha256})" for k in pages[:FIRST_PAGES]),
    ]
    if len(pages) > FIRST_PAGES:
        lines.append(f"  - and {len(pages) - FIRST_PAGES} more page(s), listed in {sidecar}")
    return lines
