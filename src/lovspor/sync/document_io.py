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

_DATASET_TO_TYPE = {
    "gjeldende-lover": "lov",
    "gjeldende-sentrale-forskrifter": "forskrift",
}
_DATASET_TO_SUBDIR = {
    "gjeldende-lover": "lover",
    "gjeldende-sentrale-forskrifter": "forskrifter",
}


def doc_type_for_dataset(source_dataset: str) -> str:
    """Return the document type label ('lov' | 'forskrift') for a dataset."""
    try:
        return _DATASET_TO_TYPE[source_dataset]
    except KeyError as exc:
        raise ValueError(
            f"unknown source_dataset: {source_dataset!r}",
        ) from exc


def document_path(
    corpus_root: Path,
    source_dataset: str,
    doc_id: str,
) -> Path:
    """Resolve the on-disk Markdown path for a document in the corpus."""
    try:
        subdir = _DATASET_TO_SUBDIR[source_dataset]
    except KeyError as exc:
        raise ValueError(
            f"unknown source_dataset: {source_dataset!r}",
        ) from exc
    return corpus_root / subdir / f"{doc_id}.md"


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
