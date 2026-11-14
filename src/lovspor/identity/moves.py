"""The file moves of the U1 renames, planned from the base commit (#871, #910).

A rename moves a document's Markdown to its new slug, with the front-matter
``slug`` rewritten, and its embedding sidecar when the base has one. Both are
read from git at the base, so the plan carries the exact bytes the apply must
leave: the rewritten Markdown as a planned file, the sidecar as the digest of
the bytes it moves.

Markdown the base lacks, or whose front matter does not name the renamed
document (``id``) and its old slug, each on exactly one line, gets no planned
text: it is listed in ``problems`` (and in the report), and the apply refuses
it instead of copying it unchanged.
"""

import hashlib
import json
import re
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from lovspor.errors import IdentityMigrationError
from lovspor.identity.migration import Rename

EMBEDDINGS = "embeddings"
MARKDOWN: Final = "markdown"
SIDECAR: Final = "sidecar"
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


class FileMove(BaseModel):
    """One file the apply moves from ``source`` to ``target``, which then holds ``sha256``.

    ``sha256`` is ``None`` for Markdown the plan could not rewrite (see ``problems``).
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["markdown", "sidecar"]
    source: str
    target: str
    sha256: str | None


class PlannedMoves(BaseModel):
    """Every move of the renames, the rewritten Markdown by target, and what refused."""

    model_config = ConfigDict(frozen=True)

    markdown: dict[str, str] = Field(default_factory=dict)
    moves: list[FileMove] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return sorted(move.source for move in self.moves)

    @property
    def sidecars(self) -> list[FileMove]:
        return [move for move in self.moves if move.kind == SIDECAR]


def markdown_path(rename: Rename, slug: str) -> str:
    return f"{rename.directory}/{slug}.md"


def sidecar_path(rename: Rename, slug: str) -> str:
    return f"{rename.directory}/{EMBEDDINGS}/{slug}.bin"


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def rewrite_slug(text: str, rename: Rename, where: str) -> str:
    """``text`` with its front-matter ``slug`` moved to ``rename.to_slug``; refuses a mismatch.

    The front matter must name the renamed document (``id``) and its old slug,
    each on exactly one line, as the renderer writes them.
    """
    match = _FRONT_MATTER.match(text)
    lines = match.group(1).split("\n") if match else []
    for key, value in (("id", rename.doc_id), ("slug", rename.from_slug)):
        if lines.count(f"{key}: {_quoted(value)}") != 1:
            raise IdentityMigrationError(f"{where}: front matter does not hold {key}: {value!r}")
    old = f"\nslug: {_quoted(rename.from_slug)}\n"
    return text.replace(old, f"\nslug: {_quoted(rename.to_slug)}\n", 1)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _markdown_move(rename: Rename, blob: bytes | None) -> PlannedMoves:
    source = markdown_path(rename, rename.from_slug)
    target = markdown_path(rename, rename.to_slug)
    try:
        if blob is None:
            raise IdentityMigrationError(f"{source}: no Markdown at the base")
        text = rewrite_slug(blob.decode("utf-8"), rename, source)
    except (IdentityMigrationError, UnicodeDecodeError) as exc:
        move = FileMove(kind=MARKDOWN, source=source, target=target, sha256=None)
        return PlannedMoves(moves=[move], problems=[str(exc)])
    digest = sha256_bytes(text.encode("utf-8"))
    move = FileMove(kind=MARKDOWN, source=source, target=target, sha256=digest)
    return PlannedMoves(markdown={target: text}, moves=[move])


def plan_rename_moves(
    rename: Rename, markdown: bytes | None, sidecar: bytes | None
) -> PlannedMoves:
    """The moves of one rename, from the base blobs of its Markdown and sidecar."""
    moves = _markdown_move(rename, markdown)
    if sidecar is None:
        return moves
    move = FileMove(
        kind=SIDECAR,
        source=sidecar_path(rename, rename.from_slug),
        target=sidecar_path(rename, rename.to_slug),
        sha256=sha256_bytes(sidecar),
    )
    return moves.model_copy(update={"moves": [*moves.moves, move]})


def merge_moves(parts: list[PlannedMoves]) -> PlannedMoves:
    """One ``PlannedMoves`` of all ``parts``, sorted by target path."""
    markdown = {path: text for part in parts for path, text in part.markdown.items()}
    return PlannedMoves(
        markdown=dict(sorted(markdown.items())),
        moves=sorted((m for part in parts for m in part.moves), key=lambda m: m.target),
        problems=sorted(p for part in parts for p in part.problems),
    )
