"""The ``lovverk`` checkout a promotion writes into, and its local manifest (ADR-0016 3, 4a).

:class:`CorpusCheckout` validates in its constructor, like
:class:`~lovspor.observatory.storage.ObservatoryRoot`: a checkout that is
inside the engine repository or the observatory archive, that is not a git
checkout, or that has no ``lokale-forskrifter/manifest.json`` (the S0
skeleton) cannot be constructed, so nothing can write into it.

Promotion writes only under ``lokale-forskrifter/``. The root
``manifest.json`` is read — never written — for the central ref-ids an
``lf-`` id must not collide with (ADR-0016 1a).

The local manifest is ``{documents, generated_at, version: 1}`` keyed by id,
with the record fields ADR-0016 Decision 3 and the ``lovverk`` integrity
check fix. It is written the way the central manifest is — sorted keys,
two-space indent, a final newline — so an unchanged manifest is byte-identical.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lovspor.errors import PromotionRefusedError
from lovspor.promotion.models import RemovedReason

LOCAL_DIR = "lokale-forskrifter"
LOCAL_DATASET = "lokale-forskrifter"
MANIFEST_NAME = "manifest.json"

_CENTRAL_FORSKRIFT_ID = re.compile(r"sf-(\d{4})(\d{2})(\d{2})-(\d+)")


class LocalRecord(BaseModel):
    """One document of the local dataset, as ``lokale-forskrifter/manifest.json`` holds it.

    Unknown keys are kept, so a record a newer writer extended survives a
    rewrite by this one.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    doc_type: Literal["lokal-forskrift"] = "lokal-forskrift"
    source_dataset: Literal["lokale-forskrifter"] = "lokale-forskrifter"
    status: Literal["current", "removed"]
    slug: str
    title: str
    markdown_path: str
    renderer_version: int
    last_seen: str
    removed_reason: RemovedReason | None = None
    authority_id: str
    authority_type: str
    content_hash: str
    version: int = Field(ge=1)
    extractor_version: int


class LocalManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    documents: dict[str, LocalRecord] = Field(default_factory=dict)
    generated_at: str | None = None
    version: Literal[1] = 1


def manifest_text(manifest: LocalManifest) -> str:
    """The manifest as it is written: sorted keys, two-space indent, final newline."""
    payload = manifest.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


class CorpusCheckout:
    """A ``lovverk`` checkout that has passed the promotion boundary check."""

    __slots__ = ("_path",)

    def __init__(self, raw: Path, forbidden: Sequence[Path]) -> None:
        candidate = raw.expanduser()
        if not candidate.is_absolute():
            msg = f"--corpus must be an absolute path, got {raw}"
            raise PromotionRefusedError(msg)
        self._path = _outside(candidate.resolve(), forbidden)
        for required in (".git", MANIFEST_NAME, f"{LOCAL_DIR}/{MANIFEST_NAME}"):
            if not (self._path / required).exists():
                msg = f"{self._path} is not a lovverk checkout with the {LOCAL_DIR} dataset: "
                raise PromotionRefusedError(msg + f"no {required}")

    @property
    def path(self) -> Path:
        return self._path

    @property
    def local_dir(self) -> Path:
        return self._path / LOCAL_DIR

    @property
    def manifest_path(self) -> Path:
        return self.local_dir / MANIFEST_NAME

    def local_manifest(self) -> LocalManifest:
        try:
            return LocalManifest.model_validate_json(self.manifest_path.read_bytes())
        except ValidationError as exc:
            msg = f"{self.manifest_path} does not read as the local manifest: {exc}"
            raise PromotionRefusedError(msg) from exc

    def central_ref_ids(self) -> frozenset[str]:
        """``forskrift/yyyy-mm-dd-n`` for every central ``sf-`` record, current or removed."""
        try:
            documents = json.loads(self._path.joinpath(MANIFEST_NAME).read_bytes())["documents"]
        except (ValueError, KeyError, TypeError) as exc:
            msg = f"{self._path / MANIFEST_NAME} does not read as the central manifest"
            raise PromotionRefusedError(msg) from exc
        matches = (_CENTRAL_FORSKRIFT_ID.fullmatch(doc_id) for doc_id in documents)
        return frozenset(
            f"forskrift/{m.group(1)}-{m.group(2)}-{m.group(3)}-{int(m.group(4))}"
            for m in matches
            if m is not None
        )

    def inside(self, relative: str) -> Path:
        """``relative`` under the local dataset directory, refusing any escape."""
        target = (self.local_dir / relative).resolve()
        if self.local_dir.resolve() not in target.parents:
            msg = f"{relative} is outside {LOCAL_DIR}/; promotion writes nowhere else"
            raise PromotionRefusedError(msg)
        return target


def _outside(path: Path, forbidden: Sequence[Path]) -> Path:
    for tree in forbidden:
        resolved = tree.resolve()
        if path == resolved or resolved in path.parents or path in resolved.parents:
            msg = f"--corpus {path} overlaps {resolved}; the corpus is a separate lovverk checkout"
            raise PromotionRefusedError(msg)
    return path
