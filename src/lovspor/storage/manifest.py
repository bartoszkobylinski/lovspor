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

from lovspor.atomic_io import atomic_write_text
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

    All are ``None``-defaulted for backward compatibility:
    manifests written by Sprint 3 still load (no slug/title), Sprint 4
    manifests still load (no history fields), Sprint 5 / Sprint 6 / 7
    manifests still load (no eu_basis). A record with ``eu_basis is
    None`` triggers the Sprint 8 backfill migration on next sync.

    ``embedding_hash`` records the ``xml_hash`` the doc's ``<slug>.bin``
    embedding sidecar was built from. It is the staleness signal: when it
    differs from ``xml_hash`` (or is ``None`` — no valid embedding, e.g. a
    keyless sync that changed the Markdown but skipped the embedder), the
    sidecar is stale and the embeddings backfill re-embeds the doc.
    Existence-on-disk alone cannot catch a stale-but-present ``.bin``.

    ``renderer_version`` records the ``RENDERER_VERSION`` that produced the
    Markdown. Change detection keys on the *upstream XML* hash, so a renderer
    fix leaves the record ``unchanged`` and never reaches it; sync compares this
    stamp against the current version and re-renders the doc when they differ. A
    record with ``renderer_version is None`` predates the stamp — treated as
    stale and re-rendered once, then carrying the current version thereafter.

    ``removed_reason`` explains a ``removed`` status. ``None`` — the default,
    and the case for every ordinary removal — means the document simply left
    the upstream dataset. A value distinguishes a removal that upstream did not
    ask for: ``"upstream_placeholder"`` marks a document Lovdata still lists as
    current but serves as an error notice rather than legal text (see
    ``lovspor.parsing.placeholder``). Without it the manifest would claim
    Lovdata deleted a law it did not delete — a lie in the one artifact whose
    entire value is being trustworthy.

    Unknown fields are ignored, not rejected: a manifest written by a *newer*
    engine (say, one that adds a per-record key after ``renderer_version``) must
    still load in an older reader such as a cached ``uvx`` MCP build. Additive
    fields are transparent to readers that don't use them; genuinely
    incompatible changes are gated by ``MANIFEST_VERSION`` instead (see
    ``Manifest._supported_version``). This is the fix for the 0.3.0
    ``renderer_version`` rollout, which broke every older MCP reader with a
    pydantic ``extra_forbidden`` error the moment it read a stamped manifest.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

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
    embedding_hash: str | None = None
    renderer_version: int | None = None
    removed_reason: str | None = None


class Manifest(BaseModel):
    """The change-detection manifest. Documents keyed by doc_id."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    version: int = MANIFEST_VERSION
    generated_at: datetime
    documents: dict[str, ManifestRecord]

    @field_validator("version")
    @classmethod
    def _supported_version(cls, value: int) -> int:
        """Reject manifest versions this engine does not understand.

        The version gate — not ``extra``-field strictness — is the real
        forward-compatibility guard: a genuinely incompatible manifest must
        bump ``MANIFEST_VERSION`` so older engines refuse it wholesale here.
        Purely *additive* fields (a newer engine stamping a new per-record or
        top-level key without bumping the version) are tolerated and ignored
        instead (``extra="ignore"``), so a newer ``lovverk`` corpus never
        breaks an older reader over a field it simply doesn't use.
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
    atomic_write_text(path, text + "\n")
