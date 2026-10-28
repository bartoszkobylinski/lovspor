#!/usr/bin/env python
"""Re-measure the three corpus data-quality questions of issue #624.

Read-only: reads a ``lovverk`` checkout and, optionally, the cached Lovdata
tarballs; writes nothing to either. The three questions, from
``docs/publication-plan.md`` § Open questions:

1. **In-force dates in the wrong order**: current documents whose
   ``last_change_in_force`` precedes ``date_in_force``.
2. **Documents without dates**: current documents with neither field. With
   ``--xml-cache``, each one's ``<dd class="dateInForce">`` is read from the
   cached tarball and classified, which says whether the absence is upstream
   (no field at all), a textual value the renderer cannot date (``Kongen
   bestemmer``), or dates the renderer loses because text precedes them.
3. **Footnote markers glued to words**: how many ``[^n]`` markers the body
   carries and how many sit flush against a word, plus the word+digit tokens
   that would be a glued marker if the renderer still fused them.

Every list is deterministic for a given checkout: sorted by document id.

Usage::

    uv run python scripts/measure_corpus_quality.py /abs/path/to/lovverk \\
        [--xml-cache data/cache] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DD = re.compile(r'<dd class="(dateInForce|lastChangeInForce)">([^<]*)')
_MARKER = re.compile(r"\[\^[^\]]+\]")
_ATTACHED_MARKER = re.compile(r"[^\W\d_]\[\^[^\]]+\]")
#: A lower-case word of five or more letters followed by one digit: what a
#: footnote marker fused into its word looked like (``amtskasserere1``).
_GLUED_SHAPE = re.compile(r"\b([^\W\d_]{5,})(\d)\b")
_TARBALLS = ("gjeldende-lover.tar.bz2", "gjeldende-sentrale-forskrifter.tar.bz2")

DateClass = str
NO_FIELD = "no dateInForce field"
TEXT_ONLY = "text only (no date to keep)"
TEXT_THEN_DATES = "text first, dates after it (renderer keeps none)"
LEADING_DATE = "leading date (would have rendered)"


@dataclass
class Measurement:
    current: int = 0
    reversed_dates: list[dict[str, str]] = field(default_factory=list)
    undated: list[dict[str, str]] = field(default_factory=list)
    markers: int = 0
    attached_markers: int = 0
    glued_shape: Counter[str] = field(default_factory=Counter)
    glued_shape_docs: int = 0

    def as_json(self) -> dict[str, Any]:
        return {
            "current": self.current,
            "reversed_dates": self.reversed_dates,
            "undated": self.undated,
            "markers": self.markers,
            "attached_markers": self.attached_markers,
            "glued_shape_tokens": sum(self.glued_shape.values()),
            "glued_shape_docs": self.glued_shape_docs,
            "glued_shape_top": self.glued_shape.most_common(25),
        }


def split_front_matter(text: str) -> tuple[dict[str, str | None], str]:
    """The top-level scalar keys of the YAML front matter, and the body after it."""
    if not text.startswith("---\n"):
        raise ValueError("no front matter")
    end = text.index("\n---\n", 4)
    fields: dict[str, str | None] = {}
    for line in text[4:end].splitlines():
        if line.startswith((" ", "-")) or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        value = raw.strip()
        fields[key.strip()] = None if value in ("", "null") else value.strip('"')
    return fields, text[end + 5 :]


def measure_document(doc_id: str, record: dict[str, Any], text: str, into: Measurement) -> None:
    fields, body = split_front_matter(text)
    in_force, last_change = fields.get("date_in_force"), fields.get("last_change_in_force")
    into.current += 1
    if in_force and last_change and last_change < in_force:
        row = {"id": doc_id, "path": record["markdown_path"]}
        into.reversed_dates.append(row | {"date_in_force": in_force, "last_change": last_change})
    if not in_force and not last_change:
        into.undated.append({"id": doc_id, "type": record["doc_type"], "title": record["title"]})
    into.markers += len(_MARKER.findall(body))
    into.attached_markers += len(_ATTACHED_MARKER.findall(body))
    glued = [word + digit for word, digit in _GLUED_SHAPE.findall(body)]
    into.glued_shape_docs += bool(glued)
    into.glued_shape.update(glued)


def measure(corpus: Path) -> Measurement:
    """Measure every current document of the central datasets, in id order."""
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    found = Measurement()
    for doc_id, record in sorted(manifest["documents"].items()):
        if record["status"] != "current":
            continue
        text = (corpus / record["markdown_path"]).read_text(encoding="utf-8")
        measure_document(doc_id, record, text, found)
    return found


def classify_date_field(raw: str | None) -> DateClass:
    """What the renderer's leading-date rule makes of a ``dateInForce`` value."""
    if raw is None:
        return NO_FIELD
    if _ISO_DATE.match(raw):
        return LEADING_DATE
    if _ISO_DATE.search(raw):
        return TEXT_THEN_DATES
    return TEXT_ONLY


def header_dates(cache: Path, wanted: set[str]) -> dict[str, dict[str, str]]:
    """``{doc_id: {dd class: text}}`` for the wanted ids, read from the cached tarballs."""
    found: dict[str, dict[str, str]] = {}
    for name in _TARBALLS:
        path = cache / name
        if not path.is_file():
            continue
        with tarfile.open(path, mode="r:bz2") as archive:
            _read_headers(archive, wanted, found)
    return found


def _read_headers(
    archive: tarfile.TarFile, wanted: set[str], into: dict[str, dict[str, str]]
) -> None:
    for member in archive:
        doc_id = Path(member.name).stem
        if doc_id not in wanted or not member.isfile():
            continue
        handle = archive.extractfile(member)
        if handle is None:
            continue
        head = handle.read(65536).decode("utf-8", errors="replace")
        into[doc_id] = dict(_DD.findall(head))


def classify_undated(found: Measurement, cache: Path) -> Counter[str]:
    """Add ``xml_date_in_force`` and ``class`` to every undated row; count the classes."""
    headers = header_dates(cache, {row["id"] for row in found.undated})
    classes: Counter[str] = Counter()
    for row in found.undated:
        header = headers.get(row["id"])
        if header is None:
            row["class"] = "not in the cached tarball"
        else:
            row["xml_date_in_force"] = header.get("dateInForce", "")
            row["class"] = classify_date_field(header.get("dateInForce"))
        classes[row["class"]] += 1
    return classes


def report(found: Measurement, classes: Counter[str] | None) -> str:
    lines = [
        f"current documents: {found.current}",
        f"1. last_change_in_force before date_in_force: {len(found.reversed_dates)}",
        *(
            f"   {r['id']}  {r['date_in_force']} < {r['last_change']}  {r['path']}"
            for r in found.reversed_dates
        ),
        f"2. no date_in_force and no last_change_in_force: {len(found.undated)}",
    ]
    if classes is not None:
        lines.extend(f"   {n:4d}  {name}" for name, n in classes.most_common())
    lines.append(
        f"3. [^n] markers: {found.markers} ({found.attached_markers} flush against a word); "
        f"word+digit tokens of the glued shape: {sum(found.glued_shape.values())} "
        f"in {found.glued_shape_docs} documents"
    )
    lines.extend(f"   {token}: {n}" for token, n in found.glued_shape.most_common(10))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("corpus", type=Path, help="A lovverk checkout.")
    parser.add_argument("--xml-cache", type=Path, help="data/cache with the Lovdata tarballs.")
    parser.add_argument("--json", type=Path, help="Write every list here as JSON.")
    args = parser.parse_args(argv)
    found = measure(args.corpus)
    classes = classify_undated(found, args.xml_cache) if args.xml_cache else None
    print(report(found, classes))
    if args.json:
        payload = found.as_json() | {"undated_classes": dict(classes or {})}
        args.json.write_text(
            json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
