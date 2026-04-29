"""Manifest read/write with deterministic JSON serialization.

The manifest is the change-detection ledger. It maps document IDs to
records carrying enough state to decide, on the next sync run, whether
each document is new / changed / removed / unchanged.

Determinism contract: same in-memory ``Manifest`` -> byte-identical
JSON file. The sync pipeline depends on this to recognize "no upstream
changes" by simply diffing the file. JSON output is sorted by document
ID, indented two spaces, UTF-8 (Norwegian characters preserved
literally), with a single trailing newline.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from lovspor.errors import ParseError

MANIFEST_VERSION = 1
ManifestStatus = Literal["current", "removed"]


class ManifestRecord(BaseModel):
    """Per-document state in the manifest.

    Sprint 4 added ``slug`` and ``title`` to support human-readable
    filenames and auto-generated INDEX pages. Sprint 5 added
    ``total_changes`` and ``last_changed`` so future MCP-style tools
    (e.g. ``list_recent_changes``) can sort and filter on history
    metadata without loading each ``history/<slug>.json`` file.
    Sprint 8 PR-D added ``eu_basis`` for the EU / EEA cross-references
    that the new ``get_eu_basis`` and ``search_eu_implementations``
    MCP tools answer over.

    All five are ``None``-defaulted for backward compatibility:
    manifests written by Sprint 3 still load (no slug/title), Sprint 4
    manifests still load (no history fields), Sprint 5 / Sprint 6 / 7
    manifests still load (no eu_basis). A record with ``eu_basis is
    None`` triggers the Sprint 8 backfill migration on next sync.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_type: str
    xml_hash: str
    markdown_path: str
    source_dataset: str
    last_seen: datetime
    status: ManifestStatus
    slug: str | None = None
    title: str | None = None
    total_changes: int | None = None
    last_changed: str | None = None
    eu_basis: list[str] | None = None


class Manifest(BaseModel):
    """The change-detection manifest. Documents keyed by doc_id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = MANIFEST_VERSION
    generated_at: datetime
    documents: dict[str, ManifestRecord]

    @field_validator("version")
    @classmethod
    def _supported_version(cls, value: int) -> int:
        """Reject manifest versions this engine does not understand.

        Forward compatibility comes via bumping ``MANIFEST_VERSION`` in
        a coordinated way, not by silently accepting unknown payloads
        that may carry incompatible semantics.
        """
        if value != MANIFEST_VERSION:
            raise ValueError(
                f"unsupported manifest version {value}; "
                f"this engine reads version {MANIFEST_VERSION}",
            )
        return value


def read_manifest(path: Path) -> Manifest:
    """Load and validate a manifest from disk.

    Raises:
        FileNotFoundError: ``path`` does not exist. Callers wanting an
            empty starting state should construct ``Manifest`` directly.
        ParseError: file contents are not valid UTF-8, not valid JSON,
            do not match the manifest schema, or use an unsupported
            manifest version.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError(f"{path}: manifest is not valid UTF-8: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"{path}: malformed JSON manifest: {exc}") from exc
    try:
        return Manifest.model_validate(data)
    except ValidationError as exc:
        raise ParseError(f"{path}: invalid manifest schema: {exc}") from exc


def write_manifest(manifest: Manifest, path: Path) -> None:
    """Write a manifest to disk deterministically.

    Output is keyed JSON sorted by document ID, indented two spaces,
    UTF-8 with a trailing newline. Same input -> byte-identical file.
    Parent directories are created if missing.
    """
    data = manifest.model_dump(mode="json")
    text = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
