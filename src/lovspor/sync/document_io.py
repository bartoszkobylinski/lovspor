"""Thin helpers composing renderer + frontmatter + filesystem IO.

Each function here is a single composed step in the per-document sync
loop. The orchestrator ties them together; keeping them separate makes
the pipeline easy to test in isolation.
"""

from pathlib import Path

from lovspor.rendering.document import (
    FrontmatterContext,
    build_frontmatter,
)
from lovspor.rendering.frontmatter import serialize_frontmatter
from lovspor.rendering.markdown_renderer import render_markdown
from lovspor.storage.manifest import Manifest

_DATASET_TO_TYPE = {
    "gjeldende-lover": "lov",
    "gjeldende-sentrale-forskrifter": "forskrift",
}
_DATASET_TO_SUBDIR = {
    "gjeldende-lover": "lover",
    "gjeldende-sentrale-forskrifter": "forskrifter",
}
_DATASET_TO_LABEL = {
    "gjeldende-lover": "Lover",
    "gjeldende-sentrale-forskrifter": "Sentrale forskrifter",
}
INDEX_FILENAME = "INDEX.md"


def doc_type_for_dataset(source_dataset: str) -> str:
    """Return the document type label ('lov' | 'forskrift') for a dataset."""
    try:
        return _DATASET_TO_TYPE[source_dataset]
    except KeyError as exc:
        raise ValueError(
            f"unknown source_dataset: {source_dataset!r}",
        ) from exc


def dataset_dir(corpus_root: Path, source_dataset: str) -> Path:
    """Return the dataset's on-disk subdirectory (``corpus/lover``,
    ``corpus/forskrifter``). Used by callers that need to write
    siblings of the per-act files (history/, INDEX.md)."""
    try:
        subdir = _DATASET_TO_SUBDIR[source_dataset]
    except KeyError as exc:
        raise ValueError(
            f"unknown source_dataset: {source_dataset!r}",
        ) from exc
    return corpus_root / subdir


def document_path(
    corpus_root: Path,
    source_dataset: str,
    slug: str,
) -> Path:
    """Resolve the on-disk Markdown path for a document in the corpus.

    The basename is the document's ``slug`` (human-readable kortform of
    the law, e.g. ``skatteloven``). The Lovdata stable identifier
    (e.g. ``nl-19990326-014``) lives in the manifest and the rendered
    file's frontmatter, not the filename.
    """
    try:
        subdir = _DATASET_TO_SUBDIR[source_dataset]
    except KeyError as exc:
        raise ValueError(
            f"unknown source_dataset: {source_dataset!r}",
        ) from exc
    return corpus_root / subdir / f"{slug}.md"


def render_full_document(
    xml_bytes: bytes,
    context: FrontmatterContext,
) -> str:
    """Render the full Markdown file content: frontmatter + body.

    Deterministic end-to-end: same (xml_bytes, context) -> byte-identical
    string out.
    """
    frontmatter = build_frontmatter(xml_bytes, context)
    fm_text = serialize_frontmatter(frontmatter)
    body = render_markdown(xml_bytes)
    return fm_text + "\n" + body


def write_document(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` as UTF-8, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def delete_document(path: Path) -> None:
    """Remove a document file if it exists. No-op if absent."""
    path.unlink(missing_ok=True)


def generate_index(
    corpus_root: Path,
    source_dataset: str,
    manifest: Manifest,
) -> Path:
    """Write a sorted human-readable INDEX.md for the dataset's subdirectory.

    Lists every record in ``manifest`` whose ``source_dataset`` matches and
    whose status is ``current`` (tombstones excluded). Output is sorted
    alphabetically by slug for deterministic byte-identical regeneration:
    same manifest input -> same INDEX output regardless of dict iteration
    order.
    """
    try:
        subdir = _DATASET_TO_SUBDIR[source_dataset]
        label = _DATASET_TO_LABEL[source_dataset]
    except KeyError as exc:
        raise ValueError(
            f"unknown source_dataset: {source_dataset!r}",
        ) from exc
    index_path = corpus_root / subdir / INDEX_FILENAME
    matching = sorted(
        (
            rec
            for rec in manifest.documents.values()
            if rec.source_dataset == source_dataset and rec.status == "current"
        ),
        key=lambda r: r.slug or r.markdown_path,
    )
    lines = [
        f"# {label}",
        "",
        f"_{len(matching)} current documents_",
        "",
    ]
    for rec in matching:
        slug = rec.slug or Path(rec.markdown_path).stem
        title = rec.title or "(no title)"
        lines.append(f"- [{slug}]({slug}.md) — {title}")
    lines.append("")
    write_document(index_path, "\n".join(lines))
    return index_path
