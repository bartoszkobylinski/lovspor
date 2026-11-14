# Data Model

Every value that crosses a module boundary in `lovspor` is a **frozen Pydantic
model** (or, for the embeddings hot path, a frozen dataclass). This document is the
reference for those models, the on-disk `manifest.json` schema, the Markdown front
matter emitted into the corpus, and the identifier conventions.

All models are frozen. The models loaded from stored or upstream JSON — the
manifest (`Manifest` / `ManifestRecord`), the history record (`HistoryRecord` /
`HistoryEvent`), and the Lovdata catalogue entry (`LovdataArchive`) —
additionally set `extra="forbid"`, so an unknown key raises `ParseError` on load
rather than being silently dropped. The render- and result-side models (e.g.
`LegalDocumentFrontMatter`, `DownloadResult`, `SyncReport`) are frozen but not
`forbid`. Field types below are quoted from the class definitions — see
[`architecture.md`](architecture.md) for where each lives.

## On-disk artifacts

The engine writes eight kinds of file into the `lovverk` clone, and one block of
a seventh: the corpus-status block of `README.md`, between
`<!-- lovspor:corpus-status:start -->` and `<!-- lovspor:corpus-status:end -->`,
re-rendered by sync and by every `promote` write from both manifests
(`corpus_readme.py`, #661). The rest of the README is hand-written and never
touched.

| Path | Content | Defined by |
|---|---|---|
| `<dataset>/<slug>.md` | Rendered law/forskrift: YAML front matter + Markdown body | `LegalDocumentFrontMatter` + `render_markdown()` |
| `<dataset>/INDEX.md` | Alphabetical discovery list for a dataset | `_IndexFrontMatter` |
| `<dataset>/history/<slug>.json` | Machine-readable per-act change history | `HistoryRecord` |
| `<dataset>/history/<slug>.md` | Human-readable twin of the above | `_HistoryFrontMatter` |
| `<dataset>/history/by-id/<doc_id>.json` | One document's events only, append-only, each with the path it had (#871) | `IdentityHistory` |
| `<dataset>/embeddings/<slug>.bin` | int8-quantized per-section vectors (LSPE) | `EmbeddingFile` — see [`embeddings.md`](embeddings.md) |
| `manifest.json` (corpus root) | The change-detection ledger; a record is tombstoned, never dropped (#871) | `Manifest` + `ManifestRecord` |
| `slugs.json` (corpus root) | Slug ownership registry: `directory → slug → {owner, assigned_at, basis, prior_holders?}`, entries immutable and append-only. `basis` is `first-holder`, or `threshold-exception` for an owner the migration's U1 threshold kept although the documents in `prior_holders` held the slug first (a migration exception, not a claim of first ownership). Created only by applying the #871 migration, never by the sync | `SlugRegistry` |
| `identity.json` (corpus root) | The corpus's identity schema: `{"schema_version": 1}` = enforced, a readable `slugs.json` is required and the sync fails closed (`IdentitySchemaError`) without one. No file = schema 0, the named legacy mode (no slug ownership enforced, one log line per sync), but only when the corpus's git history proves it was never migrated: no commit reachable from HEAD ever touched `identity.json` (one `git log -1 --full-history -- identity.json`). A missing file with such a commit (both files deleted, the migration reverted), a shallow clone, or a directory that is not a git root fails closed and names the recovery (`git checkout <last commit with the file> -- identity.json slugs.json`, then commit). Any other declaration, a `slugs.json` without a declaration, an empty `slugs.json` beside a committed manifest with records, or (at sync start, in the planner and in `lovspor audit`) a `slugs.json` missing an address the manifest holds, or one that dropped or rewrote (owner, `assigned_at`, basis, prior holders) any entry a revision of `slugs.json` held since the migration commit (working-tree edits included; a formatting-only rewrite and an entry restored to its original pass),, or in which one address was first added with different content on two commits neither of which contains the other (concurrent writers: a merge's parent order would decide which counts as first, so the refusal names both commits and owners and the recovery, resetting the unpublished side to the published commit; identical concurrent adds pass), is refused. A never-migrated corpus where some commit once added and later removed a root `identity.json` is refused as migrated: a fail-closed false positive (none at lovverk, 2026-11-13) that needs an owner decision, never a history rewrite. A rewritten history that drops every commit touching `identity.json` is the one downgrade this evidence cannot see; it needs a force-push to lovverk `main`, which branch protection refuses (`allow_force_pushes: false`, `enforce_admins: true`). At sync start and in `lovspor audit` an enforced `slugs.json` must also agree with the corpus beside it, because the registry-history check takes the migration commit's own registry as its baseline (#911): every owner and prior holder is a `doc_id` of the manifest (a tombstone is never dropped), every `current` record sits on an address it owns, every slug history `<directory>/history/<slug>.json` names its address's owner, and no key appears twice in the file (`json.loads` would keep the last). Written only by applying the #871 migration, together with `slugs.json` | `IdentityDeclaration` |

`<dataset>` is `lover` or `forskrifter`. Only the three `.md` kinds carry YAML
front matter; the `.json` and `.bin` files do not.

## Pydantic models

### Document front matter

**`LegalDocumentFrontMatter`** (`rendering/document.py`) — the complete front
matter emitted on a rendered law/forskrift. Fields, in emission order:

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | the doc_id (map key in the manifest) |
| `slug` | `str` | filename stem |
| `type` | `str` | `lov` or `forskrift` |
| `ref_id` | `str \| None` | Lovdata's human reference form (`<dd class="refid">`) |
| `title` | `str` | |
| `short_title` | `str \| None` | Lovdata kortform |
| `language` | `str` | |
| `ministry` | `list[str]` | `[]` when absent |
| `date_in_force` | `str \| None` | |
| `last_change_in_force` | `str \| None` | |
| `last_updated` | `str \| None` | |
| `xml_hash` | `str` | the normalized-XML hash |
| `source_provider` | `str` | `"Lovdata"` |
| `source_dataset` | `str` | e.g. `gjeldende-lover` |
| `source_license` | `str` | `"NLOD 2.0"` |
| `retrieved_at` | `datetime` | source-observation time — see below |
| `status` | `str` | `"current"` |
| `eu_basis` | `list[str]` | CELEX ids (uppercased); `[]` when none |

`build_frontmatter(xml_bytes, context)` merges XML-extracted metadata with a
caller-supplied **`FrontmatterContext`** (`doc_id`, `slug`, `doc_type`, `xml_hash`,
`source_dataset`, `retrieved_at`, plus the `status`/`source_provider`/`source_license`
defaults) — the XML cannot supply provenance, so the caller provides it.

### Manifest ledger

**`Manifest`** (`storage/manifest.py`) — `version: int` (validated `== 1`),
`generated_at: datetime`, `documents: dict[str, ManifestRecord]` keyed by doc_id.

**`ManifestRecord`** — one entry per document:

| Field | Type | Default |
|---|---|---|
| `doc_type` | `str` (`"lov"`\|`"forskrift"`) | required |
| `xml_hash` | `str` | required |
| `markdown_path` | `str` | required (repo-relative) |
| `source_dataset` | `str` | required |
| `last_seen` | `datetime` | required — same source-observation semantics as `retrieved_at` |
| `status` | `Literal["current","removed"]` | required |
| `slug` | `str \| None` | `None` |
| `title` | `str \| None` | `None` |
| `total_changes` | `int \| None` | `None` |
| `last_changed` | `str \| None` | `None` |
| `eu_basis` | `list[str] \| None` | `None` |
| `embedding_hash` | `str \| None` | `None` |
| `renderer_version` | `int \| None` | `None` |
| `embedding_space` | `str \| None` | `None` |
| `embedding_space_id` | `str \| None` | `None` |
| `embedding_input_hash` | `str \| None` | `None` |
| `removed_reason` | `str \| None` | `None` |

The optional fields default to `None` for backward compatibility with older-sprint
manifests. `embedding_hash` records the `xml_hash` the doc's `.bin` was built from;
`None` or a mismatch means the embeddings are stale and get rebuilt. `renderer_version`
records the `RENDERER_VERSION` that produced the Markdown; `None` or a mismatch means the
renderer has moved on and the doc is re-rendered on the next sync (see
`docs/operations.md`).

`embedding_space` and `embedding_space_id` record the **Embedding Space Identity** of the
sidecar the record describes (ADR-0005 Stage 1): the canonical provider/model/dim/endpoint
descriptor and its 128-bit digest, stamped at generation time by whichever embedder
actually produced the vectors. The manifest is the **only** authority for this identity —
the `.bin` format carries none — and `semantic_search` compares `embedding_space_id`
against the running embedder's identity *before* reading any vector: same → searchable,
different → excluded, absent → excluded as Unknown. Absence is a defined state, not an
error: it means no identity was established (keyless write, or an embedder that declared
none), and crucially **absence alone never makes a record stale** — an identity-less
record is refused by search but is not silently re-embedded. Both fields apply to
`current` records only. The full ESI definition, canonical serialization and staleness
rules live in `docs/embeddings.md` — this table deliberately does not restate them.

`embedding_input_hash` records the **Embedding Input Identity** (ADR-0006): the
canonical SHA-256 of the exact ordered `(section_id, chunk_text)` stream the sidecar's
vectors were generated from — a freshness axis, distinct from the source hash, the
embedding space, and the storage format. Keyed writers stamp it at generation time
(the empty-stream digest for a sectionless document); keyless writes and tombstones
carry `None`. Under a keyed sync an **absent or mismatching** value is stale and the
document is re-embedded — absence is deliberately not a legacy-immune state here,
because it is unverifiable and old writers strip unknown fields on rewrite; the
mass-re-embed guard (both a document-fraction and a token-workload threshold,
fail-closed before any provider call, overridable only via
`lovspor sync --allow-mass-reembed`) is what prevents surprise corpus-wide spend.
The identity definition and staleness rules live in `docs/embeddings.md`.

`removed_reason` explains a `removed` status: `None` means the document simply left the
upstream dataset; `"upstream_placeholder"` marks a document Lovdata still lists but serves
as an error notice rather than legal text, which the corpus withholds rather than
publishing (see `lovspor.parsing.placeholder`).

A `removed` record is a **tombstone** — `status` is flipped to
`"removed"`, `doc_type` / `xml_hash` / `markdown_path` / `source_dataset` / `last_seen` / `slug` / `title` are kept,
`removed_reason` is set as above, and
`total_changes` / `last_changed` / `eu_basis` / `embedding_hash` / `renderer_version` /
`embedding_space` / `embedding_space_id` / `embedding_input_hash` revert to `None` — a tombstone claims no
embedding identity because it has no current sidecar to describe.

### History

**`HistoryRecord`** (`history.py`) — `schema_version: int` (`== 1`), `slug: str`
(validated safe basename), `doc_id: str`, `events: list[HistoryEvent]` (newest
first). Serialized to `history/<slug>.json`.

**`HistoryEvent`** — one dated change:

| Field | Type | Notes |
|---|---|---|
| `date` | `datetime.date` | |
| `commit` | `str` | 7-char short SHA |
| `type` | `Literal["added","updated","renamed","removed"]` | |
| `subject` | `str` | git commit subject |
| `from_path` / `to_path` | `str \| None` | set only on `renamed` |
| `lines_added` / `lines_removed` | `int \| None` | |

**History repair in the identity migration (#871).** A pre-#871 corpus has
slug histories that name a document other than the slug's registry owner
(`history_owner_mismatches` in the plan report). Their contents are the defect:
the old extractor overwrote the owner's events with the later holder's, and
filed some owner deletions under the later holder. `lovspor ops identity-plan`
therefore rebuilds each of them from git, never from the file
(`identity/repair.py`, `identity/repair_plan.py`):

- Path list: the slug's `<dataset>/<slug>.md`, plus each path a file was moved
  from into it (git rename detection at the commit that added it), walked back
  from that commit. No `--follow`; every walked path is in the report.
- Attribution, per commit C on path P: the front-matter `id` of P at C
  (`id-at-commit`); otherwise, for a deletion or an overwrite by a file with no
  `id`, the `id` of P at C's parent (`id-at-parent`). A commit neither names is
  excluded and listed, and so is a commit that is no history event (a re-render).
  The event itself is classified by the sync's own rules (`classify_commit`).
- Planned files, all under `planned/`: `history/<slug>.json` and `.md` (schema 1,
  `doc_id` = owner, the owner's events only); `history/<new-slug>.json` and `.md`
  for a later holder the plan renames (U1); and `history/by-id/<doc_id>.json` for
  every document the chain attributes an event to, merged append-only into the
  file at the base. A by-id event at the base that the attribution gives another
  document, or a new slug's history at the base that names another document,
  blocks the plan (exit 3).
- Provenance lives in the report, never in the history files: each event's
  commit, path, `id` before and after, rule and role; the excluded commits; the
  events the base file held with the document each now belongs to; the sha256
  of each planned file. Invariant, checked: each attributed event is in exactly
  one document's by-id history, the one the attribution names.
- On a repaired corpus no slug history names a non-owner, so the plan has no
  history to repair (`nothing_to_do`). `git revert` of the one migration
  commit restores the base tree byte for byte (tested), but it is **not** a
  supported rollback once pushed: the reverted corpus still shows
  `identity.json` in its history, so sync, `lovspor audit` and the planner
  refuse it (N1) instead of falling back to legacy. Recovery after a pushed
  migration is forward: restore a **matched pair** of `identity.json` and
  `slugs.json` from one commit — the last commit on `main` that held both and
  that `lovspor audit` accepted — never `slugs.json` from the migration commit
  alone (the registry-history check refuses any entry added since then and
  later missing), and never one file without the other. Take both from the same
  commit (`git checkout <last good commit> -- identity.json slugs.json`), run
  `lovspor audit --corpus-path <repo>` before committing, and name that commit
  in the commit message, so the restored pair's provenance stays traceable.
  Nothing is rewritten or bypassed: the history keeps the bad commit and the
  restore. Before the push, discarding the local migration commit is the
  rollback.

### Upstream source

**`LovdataArchive`** (`sources/lovdata.py`) — one `/list` catalogue entry;
`extra="forbid", populate_by_name=True` with camelCase aliases: `filename`
(path-traversal validated), `description`, `size_bytes` (alias `sizeBytes`),
`last_modified` (alias `lastModified`).

**`DownloadResult`** — `filename`, `path: pathlib.Path`, `size_bytes`, `sha256`
(64-char hex, no `sha256:` prefix).

### Sync control

**`ChangeSet`** (`sync/change_detector.py`) — disjoint partition of doc_ids:
`new`, `changed`, `removed`, `unchanged` (each `list[str]`).

**`SyncReport`** (`sync/orchestrator.py`) — `run_sync`'s return value:
`new_count`, `changed_count`, `removed_count`, `unchanged_count`.

**`TarballMember`** (`extraction/tarball.py`) — `name: str`, `content: bytes`.

### Settings

**`Settings`** (`settings.py`) — runtime config, built via `Settings.from_env()`:

| Field | Type | Default | Env var |
|---|---|---|---|
| `data_dir` | `pathlib.Path` | required | `LOVSPOR_DATA_DIR` |
| `lovverk_repo_path` | `pathlib.Path` | required | `LOVSPOR_OUTPUT_REPO_PATH` |
| `git_commit_mode` | `str` | `"per-document"` | `LOVSPOR_GIT_COMMIT_MODE` (`per-document`\|`single`) |
| `max_removal_ratio` | `float` | `0.10` | `LOVSPOR_MAX_REMOVAL_RATIO` (validated `(0,1]`) |
| `http_timeout_seconds` | `float` | `120.0` | `LOVSPOR_HTTP_TIMEOUT_SECONDS` |
| `http_user_agent` | `str` | `lovspor/0.1 (+…)` | `LOVSPOR_HTTP_USER_AGENT` |
| `log_level` | `str` | `"INFO"` | `LOVSPOR_LOG_LEVEL` |
| `openai_api_key` | `str \| None` | `None` | `OPENAI_API_KEY` (fallback `OPENAI_APIKEY`) |

`data_dir` and `lovverk_repo_path` are resolved to absolute paths.

### Embedding carrier types

These live on the search hot path and are frozen dataclasses, not Pydantic — no
per-object validation. See [`embeddings.md`](embeddings.md) for the binary layout.

- **`EmbeddingSection`** (`embeddings/sections.py`) — `section_id`, `text`.
- **`EmbeddingFile`** (`embeddings/store.py`) — `dim`, `scale`,
  `sections: list[tuple[str, np.ndarray]]`. `EMBEDDING_DIM = 3072`.
- **`SearchHit`** (`embeddings/search.py`) — `slug`, `section_id`, `score` (cosine).
- **`EmbeddingModel`** (`embeddings/model.py`) — a `@runtime_checkable` Protocol
  (`encode()`, `get_dimension()`); concrete impl `OpenAIEmbedder`.

## `manifest.json` schema

Written by `write_manifest` as `model_dump(mode="json")` →
`json.dumps(sort_keys=True, indent=2, ensure_ascii=False)` + trailing newline —
deterministic, keys sorted, Norwegian characters literal.

```jsonc
{
  "version": 1,                        // must == 1
  "generated_at": "<ISO 8601 datetime>",
  "documents": {                       // keyed by doc_id
    "lov-19990326-014": {              // one ManifestRecord
      "doc_type": "lov",               // "lov" | "forskrift"
      "xml_hash": "<hex>",             // normalized-XML hash
      "markdown_path": "lover/skatteloven.md",
      "source_dataset": "gjeldende-lover",
      "last_seen": "<ISO datetime>",
      "status": "current",             // "current" | "removed" (tombstone)
      "slug": "skatteloven",           // str | null
      "title": "Skatteloven",          // str | null
      "total_changes": 12,             // int | null
      "last_changed": "2026-04-15",    // ISO date str | null
      "eu_basis": ["32016R0679"],      // list[str] | null
      "embedding_hash": "<hex>",       // str | null; == xml_hash when fresh
      "renderer_version": 1            // int | null; == RENDERER_VERSION when fresh
    }
  }
}
```

## Markdown front matter

`serialize_frontmatter(model)` (`rendering/frontmatter.py`) emits `---`, one line
per `model_dump()` field **in declaration order**, `---`, then a blank line. Empty
list → `key: []`; non-empty list → a `  - ` block sequence; `None` → `null`;
`datetime` → ISO string. The three file kinds differ only by which model is passed:

- **Law / forskrift** (`<dataset>/<slug>.md`) — `LegalDocumentFrontMatter` (all keys
  listed above; `ref_id`/`short_title`/`date_in_force`/`last_change_in_force`/
  `last_updated` render as `null` when the XML lacks them).
- **Dataset index** (`<dataset>/INDEX.md`) — `_IndexFrontMatter`:
  `type: "index"`, `dataset`, `source_provider: "Lovdata"`,
  `source_license: "NLOD 2.0"`. No timestamp (byte-identical regeneration).
- **History** (`<dataset>/history/<slug>.md`) — `_HistoryFrontMatter`:
  `type: "history"`, `slug`, `source_provider`, `source_license`. No timestamp.

### `retrieved_at` / `last_seen` semantics

Both fields carry the **same source-observation timestamp**: the UTC time the
sync first retrieved/observed the document's *current upstream content
version*. While the normalized XML is unchanged, both values stay stable —
`retrieved_at == last_seen` is the invariant. A renderer-only re-render is an
artifact-generation event, not a new upstream-content observation, so it
preserves the timestamp on both sides (seed source: the Published Rendering's
own frontmatter, `sync/orchestrator.py::_preserved_observation`). Neither
field is an artifact-render timestamp; the v1 model deliberately has no such
field. A true XML-content change advances both fields to the observing sync's
time.

## Identifiers

- **`doc_id`** — the tarball member filename without `.xml` (e.g.
  `lov-19990326-014`). It is the manifest map key, the `id:` in front matter, and
  `HistoryRecord.doc_id`. **Stable across renames** — the on-disk slug can change,
  doc_id does not. Not the filename on disk (the slug is).
- **`ref_id`** — Lovdata's human reference form (e.g. `lov/1999-03-26-14`), from
  `<dd class="refid">`. Distinct from doc_id; nullable.
- **`slug`** — `derive_slug(short_title, title, doc_id)`: prefer the kortform, else
  the title with bracketed content stripped, else the doc_id. Lowercased,
  non-alphanumerics collapsed to `-`, Norwegian `æøå` (and German `äöü`) preserved,
  capped at 200 UTF-8 bytes. Collisions are resolved **per dataset** (`-2`, `-3`, …,
  skipping natural slugs), sorted by doc_id for determinism.
- **`section_id`** — parsed from rendered `### § <id>.` headings (e.g. `5-12`, `3a`);
  the embedding unit key and the `section_id` in `SearchHit` and MCP tool output.
