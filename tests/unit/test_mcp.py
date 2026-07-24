"""Unit tests for lovspor.mcp.

Cover the CorpusReader business logic (filtering, sorting, lookups,
error paths) without exercising the MCP wire protocol — that belongs
to Anthropic's SDK and isn't ours to test. The build_server / FastMCP
glue is tested by constructing the server instance and verifying the
four expected tool names are registered.
"""

import asyncio
import inspect
import json
import os
import shlex
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from httpx import Response
from mcp.server.auth.middleware.auth_context import auth_context_var, get_access_token
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

import lovspor.mcp as mcp_module
from lovspor.access import (
    Credential,
    CredentialStore,
    Limits,
    hash_token,
    write_credential_file,
)
from lovspor.embeddings import write_embeddings
from lovspor.embeddings.search import SearchHit
from lovspor.errors import ConfigError
from lovspor.mcp import (
    _CROSS_REF_SECTION,
    _MAX_RESULT_LIMIT,
    _MCP_EMBED_MAX_RETRIES,
    _MCP_EMBED_TIMEOUT_SECONDS,
    _SECTION_HEADING,
    _SNIPPET_CONTEXT_CHARS,
    _TABLE_ROW_SNIPPET_CHARS,
    CORPUS_SCOPE_NOTE,
    CorpusAmbiguousSectionError,
    CorpusNotFoundError,
    CorpusReader,
    HttpConfig,
    OpenAIEmbedder,
    ParsedSection,
    SectionIndex,
    _add_health_routes,
    _bounded_limit,
    _build_embedder,
    _compute_match_owner_starts,
    _diff_section_maps,
    _extract_cross_references,
    _no_strong_match_notice,
    _normalize_for_quote_match,
    _offload_to_thread,
    _parse_sections,
    _record_summary,
    _resolve_dataset,
    _resolve_slug_in_window,
    _slug_token_in_citation,
    _snippet,
    _strip_frontmatter_and_h1,
    _subdir_for_dataset,
    _with_quota,
    build_server,
)
from lovspor.quota import QuotaEnforcer, QuotaExceededError
from lovspor.storage.manifest import Manifest, ManifestRecord, write_manifest
from lovspor.timetravel import RevisionNotFoundError, RevisionResult
from lovspor.workos_auth import CompositeVerifier

_AUTHKIT_DOMAIN = "https://vigilant-beacon-78-staging.authkit.app"
_PUBLIC_URL = "https://lovspor.bartoszkobylinski.com/mcp"


def _record(
    *,
    slug: str,
    title: str,
    source_dataset: str = "gjeldende-lover",
    status: str = "current",
    last_changed: str | None = None,
    total_changes: int | None = None,
    eu_basis: list[str] | None = None,
) -> ManifestRecord:
    subdir = "lover" if source_dataset == "gjeldende-lover" else "forskrifter"
    return ManifestRecord(
        doc_type="lov" if source_dataset == "gjeldende-lover" else "forskrift",
        xml_hash="a" * 64,
        markdown_path=f"{subdir}/{slug}.md",
        source_dataset=source_dataset,
        last_seen=datetime(2026, 4, 27, tzinfo=UTC),
        status=status,  # type: ignore[arg-type]
        slug=slug,
        title=title,
        last_changed=last_changed,
        total_changes=total_changes,
        eu_basis=eu_basis,
    )


def _seed_corpus(
    root: Path,
    records: dict[str, ManifestRecord],
    *,
    write_files: bool = True,
    write_history_for: list[str] | None = None,
    generated_at: datetime | None = None,
    body_for: dict[str, str] | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    write_manifest(
        Manifest(
            generated_at=generated_at or datetime.now(UTC),
            documents=records,
        ),
        root / "manifest.json",
    )
    if write_files:
        for record in records.values():
            doc_path = root / record.markdown_path
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            custom_body = (body_for or {}).get(record.slug or "")
            body = custom_body if custom_body is not None else f"# {record.title}\n"
            doc_path.write_text(
                f"---\nid: x\ntitle: {record.title}\n---\n\n{body}",
                encoding="utf-8",
            )
    if write_history_for:
        for slug in write_history_for:
            record = next(r for r in records.values() if r.slug == slug)
            subdir = "lover" if record.source_dataset == "gjeldende-lover" else "forskrifter"
            history_dir = root / subdir / "history"
            history_dir.mkdir(parents=True, exist_ok=True)
            (history_dir / f"{slug}.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "slug": slug,
                        "doc_id": "nl-x",
                        "events": [
                            {
                                "date": "2026-04-27",
                                "commit": "abc1234",
                                "type": "added",
                                "subject": f"add(lov): {slug}",
                            },
                        ],
                    },
                ),
                encoding="utf-8",
            )


class _FakeEmbedder:
    def __init__(self, vector: list[float]) -> None:
        self.vector = np.asarray(vector, dtype=np.float32)
        self.queries: list[str] = []

    def encode(self, texts: list[str]) -> np.ndarray:
        self.queries.extend(texts)
        return np.asarray([self.vector for _ in texts], dtype=np.float32)

    def get_dimension(self) -> int:
        return int(self.vector.shape[0])


def test_mcp_low_level_helpers_have_stable_public_contracts() -> None:
    record = _record(
        slug="skatteloven-sktl",
        title="Skatteloven",
        last_changed="2026-04-27",
        total_changes=3,
    )

    assert _slug_token_in_citation(
        "skatteloven-sktl",
        "presskatteloven-sktl og skatteloven-sktl.",
    )
    assert not _slug_token_in_citation("skatteloven-sktl", "skatteloven-sktlx")
    assert (
        _resolve_slug_in_window(
            "jf. kort-lov og lang-lov-med-navn § 1",
            "kort-lov",
            {"kort-lov", "lang-lov-med-navn"},
        )
        == "lang-lov-med-navn"
    )
    assert _normalize_for_quote_match("  Dette\nER\tTekst  ") == "dette er tekst"
    assert _record_summary("nl-1", record) == {
        "slug": "skatteloven-sktl",
        "doc_id": "nl-1",
        "title": "Skatteloven",
        "dataset": "lover",
        "last_changed": "2026-04-27",
        "total_changes": 3,
    }
    assert _subdir_for_dataset("gjeldende-sentrale-forskrifter") == "forskrifter"
    assert _resolve_dataset("lover") == "gjeldende-lover"
    assert _resolve_dataset("gjeldende-sentrale-forskrifter") == ("gjeldende-sentrale-forskrifter")
    with pytest.raises(CorpusNotFoundError, match="unknown source_dataset"):
        _subdir_for_dataset("bogus")
    with pytest.raises(CorpusNotFoundError, match="use one of: lover, forskrifter"):
        _resolve_dataset("bogus")


def test_strip_frontmatter_h1_and_snippet_boundaries_are_stable() -> None:
    rendered = (
        "---\ntitle: Skatteloven\n---\n\n# Skatteloven\n\n## Kapittel 1\n### § 1. Start\nBody text."
    )
    assert _strip_frontmatter_and_h1(rendered) == ("## Kapittel 1\n### § 1. Start\nBody text.")
    assert _strip_frontmatter_and_h1("plain text") == "plain text"

    body = "a" * 60 + "TARGET" + "b" * 60
    snippet = _snippet(body, 60, len("TARGET"))
    assert snippet == "..." + ("a" * 50) + "TARGET" + ("b" * 50) + "..."


def test_corpus_reader_constructor_and_safe_join_errors_are_specific(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(CorpusNotFoundError) as exc_info:
        CorpusReader(tmp_path / "missing")
    assert str(exc_info.value) == f"corpus path does not exist: {missing}"

    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(CorpusNotFoundError) as exc_info:
        CorpusReader(tmp_path)
    assert str(exc_info.value) == f"corpus path is missing manifest.json: {tmp_path}"

    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
    )
    reader = CorpusReader(tmp_path)
    with pytest.raises(CorpusNotFoundError) as escape_info:
        reader._safe_join("..", "outside.md")
    # Whole string, not a substring: the path-escape refusal is a security
    # message and both its wording and the '/' that rebuilds the offending
    # path are load-bearing.
    assert str(escape_info.value) == "path '../outside.md' escapes corpus root"


def test_build_embedder_reads_supported_env_names_and_warns_when_absent(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    created: list[str] = []

    class FakeOpenAIEmbedder:
        def __init__(self, api_key: str, **_kwargs: object) -> None:
            created.append(api_key)

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_APIKEY", raising=False)
    monkeypatch.setattr("lovspor.mcp.OpenAIEmbedder", FakeOpenAIEmbedder)

    assert _build_embedder() is None
    assert "semantic_search will be disabled" in capsys.readouterr().err

    monkeypatch.setenv("OPENAI_APIKEY", "sk-compact")
    assert _build_embedder().__class__ is FakeOpenAIEmbedder
    assert created == ["sk-compact"]


def test_build_embedder_uses_tight_interactive_timeout_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP server is single-threaded stdio: a hung OpenAI request blocks
    every tool for the session. The embedder it builds must carry an
    interactive timeout/retry budget, not the engine's 180s x 3 batch
    defaults (~9 min worst case) that _build_embedder used to inherit."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    embedder = _build_embedder()

    assert isinstance(embedder, OpenAIEmbedder)
    assert embedder._timeout_seconds == _MCP_EMBED_TIMEOUT_SECONDS
    assert embedder._max_retries == _MCP_EMBED_MAX_RETRIES
    # Strictly tighter than the batch defaults it previously inherited.
    assert embedder._timeout_seconds < 180.0
    assert embedder._max_retries < 3


def _hammer_cold_cache(
    trigger: Callable[[], object],
    *,
    workers: int = 8,
) -> None:
    """Fire ``trigger`` from ``workers`` threads that all start together.

    A barrier releases every thread at once so they hit a cold cache
    simultaneously; the seam under test sleeps briefly while building,
    which guarantees every caller clears the ``is None`` check before the
    first one populates the cache. ``future.result()`` re-raises any
    worker exception in the main thread.
    """
    barrier = threading.Barrier(workers)

    def worker() -> None:
        barrier.wait(timeout=5)
        trigger()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker) for _ in range(workers)]
        for future in futures:
            future.result()


def test_concurrent_cold_manifest_load_builds_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    reader = CorpusReader(tmp_path)
    builds: list[int] = []
    real_read_manifest = mcp_module.read_manifest

    def slow_counting(path: Path) -> Manifest:
        builds.append(1)
        time.sleep(0.05)
        return real_read_manifest(path)

    monkeypatch.setattr(mcp_module, "read_manifest", slow_counting)

    _hammer_cold_cache(lambda: reader.get_law("skatteloven"))

    assert builds == [1]  # one build under concurrency, not one per racing thread


def test_concurrent_cold_body_index_builds_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": "## Kapittel 1\n### § 1. Start\nbody text"},
    )
    reader = CorpusReader(tmp_path)
    strips: list[int] = []
    real_strip = mcp_module._strip_frontmatter_and_h1

    def slow_counting(text: str) -> str:
        strips.append(1)
        time.sleep(0.05)
        return real_strip(text)

    monkeypatch.setattr(mcp_module, "_strip_frontmatter_and_h1", slow_counting)

    _hammer_cold_cache(lambda: reader.search_body("body"))

    # One current doc => the 45 MB body index strips exactly once per build;
    # a lock-free double build strips once per racing thread instead.
    assert strips == [1]


def _write_embedding_file(
    root: Path,
    dataset_subdir: str,
    slug: str,
    sections: list[tuple[str, list[int]]],
    *,
    scale: float = 1.0,
) -> None:
    write_embeddings(
        root / dataset_subdir / "embeddings" / f"{slug}.bin",
        [(section_id, np.asarray(vector, dtype=np.int8)) for section_id, vector in sections],
        scale=scale,
        dim=len(sections[0][1]) if sections else 3,
    )


# ---------- CorpusReader construction ----------


def test_reader_raises_when_corpus_path_missing(tmp_path: Path) -> None:
    with pytest.raises(CorpusNotFoundError, match="does not exist"):
        CorpusReader(tmp_path / "nope")


def test_reader_raises_when_manifest_missing(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(CorpusNotFoundError, match=r"missing manifest\.json"):
        CorpusReader(tmp_path)


def test_reader_loads_manifest_lazily(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    reader = CorpusReader(tmp_path)
    assert "nl-1" in reader.manifest.documents
    # second access uses cached value (no exception when manifest file removed)
    (tmp_path / "manifest.json").unlink()
    assert "nl-1" in reader.manifest.documents


# ---------- get_law ----------


def test_slug_lookups_use_cached_index(tmp_path: Path) -> None:
    """Point lookups resolve through a slug index built once from the
    manifest, not a per-call linear scan over every record."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    reader = CorpusReader(tmp_path)
    assert "# Skatteloven" in reader.get_law("skatteloven")

    # Mutating the manifest after the first lookup must not affect
    # resolution: the index is pinned for the reader's lifetime, the
    # same contract as the cached manifest itself.
    reader.manifest.documents.clear()
    assert "# Skatteloven" in reader.get_law("skatteloven")


def test_slug_index_first_record_wins_on_duplicate_slugs(tmp_path: Path) -> None:
    """Two current records with the same slug: the first manifest entry
    wins, matching the old linear-scan behavior."""
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="dupe", title="First", eu_basis=["32016R0679"]),
            "nl-2": _record(slug="dupe", title="Second", eu_basis=[]),
        },
    )
    reader = CorpusReader(tmp_path)
    assert reader.get_eu_basis("dupe")["doc_id"] == "nl-1"


def test_get_section_duplicate_slug_stable_after_search_body(tmp_path: Path) -> None:
    """Codex PR #62 round 1: with two current records sharing a slug
    but pointing at different files, get_section resolved the FIRST
    record's file — until search_body() loaded the corpus-wide body
    index, whose last-record-wins dict silently flipped subsequent
    point lookups to the SECOND record's content. Point lookups must
    return the same content before and after a search_body call."""
    first = _record(slug="dupe", title="First")
    second = _record(slug="dupe", title="Second").model_copy(
        update={"markdown_path": "lover/dupe-second.md"},
    )
    _seed_corpus(tmp_path, {"nl-1": first, "nl-2": second}, write_files=False)
    (tmp_path / "lover").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lover" / "dupe.md").write_text(
        "---\nid: x\ntitle: First\n---\n\n## Kapittel 1.\n\n### § 1-1. F\n\nFirst body.\n",
        encoding="utf-8",
    )
    (tmp_path / "lover" / "dupe-second.md").write_text(
        "---\nid: x\ntitle: Second\n---\n\n## Kapittel 9.\n\n### § 9-9. S\n\nSecond body.\n",
        encoding="utf-8",
    )
    reader = CorpusReader(tmp_path)

    before = reader.get_section("dupe", "1-1")
    assert "First body." in before["body"]

    # search_body loads the corpus-wide body index; it must agree with
    # the slug index on which record owns a duplicated slug.
    assert [hit["slug"] for hit in reader.search_body("First body")] == ["dupe"]
    assert reader.search_body("Second body") == []

    after = reader.get_section("dupe", "1-1")
    assert after["body"] == before["body"]
    assert reader.verify_quote("dupe", "1-1", "First body.")["verified"] is True


def test_get_law_returns_file_content(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    out = CorpusReader(tmp_path).get_law("skatteloven")
    assert "# Skatteloven" in out


def test_get_law_raises_for_unknown_slug(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    with pytest.raises(CorpusNotFoundError, match="no current law"):
        CorpusReader(tmp_path).get_law("does-not-exist")


def test_get_law_unknown_slug_suggests_closest_match(tmp_path: Path) -> None:
    """The most common first call from an AI is the colloquial kortform
    ('skatteloven') instead of the canonical slug ('skatteloven-sktl').
    The error must offer the near-miss so the AI recovers in one step
    instead of a search_laws round trip."""
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="skatteloven-sktl", title="Skatteloven"),
            "nl-2": _record(slug="husleieloven", title="Husleieloven"),
        },
    )
    with pytest.raises(CorpusNotFoundError) as exc_info:
        CorpusReader(tmp_path).get_law("skatteloven")

    message = str(exc_info.value)
    assert "skatteloven-sktl" in message
    assert "did you mean" in message


def test_get_law_unknown_slug_without_near_miss_omits_suggestions(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="husleieloven", title="Husleieloven")})
    with pytest.raises(CorpusNotFoundError) as exc_info:
        CorpusReader(tmp_path).get_law("zzz-qqq-vvv")

    message = str(exc_info.value)
    assert "did you mean" not in message
    assert "search_laws" in message


def test_citation_suggestion_hint_pins_its_exact_wording(tmp_path: Path) -> None:
    # The near-miss hint is the AI's one-step recovery from a colloquial slug;
    # its wording is a contract, not decoration. Substring tests let the phrase
    # around "did you mean" mutate freely (5 survivors) — pin it whole.
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven-sktl", title="Skatteloven")})
    reader = CorpusReader(tmp_path)

    assert reader._citation_suggestion_hint("skatteloven") == (
        "; did you mean skatteloven-sktl? Use search_laws for canonical slugs"
    )
    # No near miss -> empty string, so token-less citations keep their pinned
    # exact-reason error intact.
    assert reader._citation_suggestion_hint("zzz-qqq-vvv") == ""


def test_validate_citation_unmatched_slug_suggests_canonical_form(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven-sktl", title="Skatteloven")},
    )
    out = CorpusReader(tmp_path).validate_citation("§ 5-12 skatteloven")

    assert out["valid"] is False
    assert "skatteloven-sktl" in (out["reason"] or "")


def test_get_law_skips_tombstoned_records(tmp_path: Path) -> None:
    """A removed law is not retrievable via get_law — its file is gone."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="gone", title="Gone", status="removed")},
        write_files=False,
    )
    with pytest.raises(CorpusNotFoundError, match="no current law"):
        CorpusReader(tmp_path).get_law("gone")


def test_get_law_raises_when_manifest_references_missing_file(tmp_path: Path) -> None:
    """Corpus drift: manifest says file exists but disk doesn't have it."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        write_files=False,
    )
    with pytest.raises(CorpusNotFoundError) as exc_info:
        CorpusReader(tmp_path).get_law("x")
    assert str(exc_info.value) == (
        "manifest references 'lover/x.md' but file is missing; "
        "run 'git pull' in the corpus to refresh"
    )


def test_get_law_refuses_path_escape_via_markdown_path(tmp_path: Path) -> None:
    """HIGH security regression. A manifest with markdown_path
    containing ``..`` would otherwise let CorpusReader serve files
    from outside the corpus root via the MCP wire (e.g. SSH keys,
    secrets, anything readable by the server process)."""
    secret = tmp_path / "secret.txt"
    secret.write_text("you should not see this", encoding="utf-8")
    corpus = tmp_path / "corpus"
    _seed_corpus(corpus, {"nl-1": _record(slug="evil", title="E")}, write_files=False)
    bad = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    bad["documents"]["nl-1"]["markdown_path"] = "../secret.txt"
    (corpus / "manifest.json").write_text(json.dumps(bad), encoding="utf-8")

    with pytest.raises(CorpusNotFoundError, match="escapes corpus root"):
        CorpusReader(corpus).get_law("evil")


def test_get_law_history_refuses_path_escape_via_slug(tmp_path: Path) -> None:
    """Same defense for the history file lookup. A malicious slug in
    the manifest would otherwise resolve outside the corpus root via
    the constructed ``<dataset>/history/<slug>.json`` path."""
    secret = tmp_path / "outside.json"
    secret.write_text('{"x": 1}', encoding="utf-8")
    corpus = tmp_path / "corpus"
    _seed_corpus(corpus, {"nl-1": _record(slug="evil", title="E")}, write_files=False)
    # From corpus/lover/history/, three ../ steps reach corpus's parent
    # (tmp_path), then 'outside' targets tmp_path/outside.json.
    escape_slug = "../../../outside"
    bad = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    bad["documents"]["nl-1"]["slug"] = escape_slug
    (corpus / "manifest.json").write_text(json.dumps(bad), encoding="utf-8")

    with pytest.raises(CorpusNotFoundError, match="escapes corpus root"):
        CorpusReader(corpus).get_law_history(escape_slug)


# ---------- get_law_history ----------


def test_get_law_history_returns_parsed_json(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        write_history_for=["skatteloven"],
    )
    history = CorpusReader(tmp_path).get_law_history("skatteloven")
    assert history["slug"] == "skatteloven"
    assert history["schema_version"] == 1
    assert len(history["events"]) == 1
    assert history["events"][0]["type"] == "added"


def test_get_law_history_raises_when_history_file_missing(tmp_path: Path) -> None:
    """Pre-Sprint-5 corpus has no history files yet."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    with pytest.raises(CorpusNotFoundError) as exc_info:
        CorpusReader(tmp_path).get_law_history("x")
    assert str(exc_info.value) == (
        "history file missing for 'x'; corpus may predate the Sprint 5 history layer"
    )


def test_get_law_history_raises_for_unknown_slug(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    with pytest.raises(CorpusNotFoundError, match="no current law"):
        CorpusReader(tmp_path).get_law_history("missing")


# ---------- list_recent_changes ----------


def test_list_recent_changes_orders_newest_first(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="a", title="A", last_changed="2024-01-01"),
            "nl-2": _record(slug="b", title="B", last_changed="2026-04-27"),
            "nl-3": _record(slug="c", title="C", last_changed="2025-08-15"),
        },
    )
    rows = CorpusReader(tmp_path).list_recent_changes()
    assert [r["slug"] for r in rows] == ["b", "c", "a"]


def test_list_recent_changes_respects_limit(tmp_path: Path) -> None:
    bulk = {
        f"nl-{i}": _record(slug=f"s{i}", title=f"S{i}", last_changed="2026-04-27")
        for i in range(50)
    }
    _seed_corpus(tmp_path, bulk)
    rows = CorpusReader(tmp_path).list_recent_changes(limit=5)
    assert len(rows) == 5


def test_list_recent_changes_default_limit_is_twenty(tmp_path: Path) -> None:
    bulk = {
        f"nl-{i:02d}": _record(
            slug=f"s{i:02d}",
            title=f"S{i}",
            last_changed=f"2026-04-{(i % 28) + 1:02d}",
        )
        for i in range(25)
    }
    _seed_corpus(tmp_path, bulk)

    assert len(CorpusReader(tmp_path).list_recent_changes()) == 20


def test_list_recent_changes_filters_by_dataset_alias(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="a", title="A", last_changed="2026-04-27"),
            "sf-1": _record(
                slug="b",
                title="B",
                source_dataset="gjeldende-sentrale-forskrifter",
                last_changed="2026-04-27",
            ),
        },
    )
    reader = CorpusReader(tmp_path)
    assert [r["slug"] for r in reader.list_recent_changes(dataset="lover")] == ["a"]
    assert [r["slug"] for r in reader.list_recent_changes(dataset="forskrifter")] == ["b"]


def test_list_recent_changes_filters_by_since_date(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="old", title="O", last_changed="2024-01-01"),
            "nl-2": _record(slug="recent", title="R", last_changed="2026-04-27"),
        },
    )
    rows = CorpusReader(tmp_path).list_recent_changes(since="2025-01-01")
    assert [r["slug"] for r in rows] == ["recent"]


def test_list_recent_changes_excludes_records_without_last_changed(tmp_path: Path) -> None:
    """Sprint 4 manifests have no last_changed; they should not appear
    in the recent-changes list (would break the date sort order)."""
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="legacy", title="L"),  # last_changed=None
            "nl-2": _record(slug="modern", title="M", last_changed="2026-04-27"),
        },
    )
    rows = CorpusReader(tmp_path).list_recent_changes()
    assert [r["slug"] for r in rows] == ["modern"]


def test_list_recent_changes_rejects_negative_limit(tmp_path: Path) -> None:
    """LOW regression. Python slicing with a negative limit returns
    'all but the last N' results, not 'last N' — confusing semantics
    that an AI caller could not predict. Reject the input explicitly."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X", last_changed="2026-04-27")},
    )
    with pytest.raises(ValueError) as exc_info:
        CorpusReader(tmp_path).list_recent_changes(limit=-1)
    assert str(exc_info.value) == "limit must be non-negative, got -1"


def test_list_recent_changes_zero_limit_is_empty(tmp_path: Path) -> None:
    """limit=0 is a valid request ('give me 0 results') and returns []."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X", last_changed="2026-04-27")},
    )
    assert CorpusReader(tmp_path).list_recent_changes(limit=0) == []


def test_list_recent_changes_normalizes_alternate_iso_since_form(tmp_path: Path) -> None:
    """Codex PR-A regression: date.fromisoformat() accepts both
    '2026-04-27' AND '20260427' (no dashes), but the manifest stores
    last_changed in canonical 'YYYY-MM-DD' form. A naive lexicographic
    compare against the alternate form silently filters everything out
    (digits sort before '-'). Normalize after parsing so both inputs
    reach the comparator in the same canonical form."""
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="recent", title="R", last_changed="2026-04-27"),
            "nl-2": _record(slug="older", title="O", last_changed="2024-01-01"),
        },
    )
    reader = CorpusReader(tmp_path)
    canonical = reader.list_recent_changes(since="2026-04-27")
    alternate = reader.list_recent_changes(since="20260427")
    assert [r["slug"] for r in canonical] == [r["slug"] for r in alternate] == ["recent"]


def test_list_recent_changes_rejects_malformed_since_date(tmp_path: Path) -> None:
    """Documented contract is YYYY-MM-DD; malformed strings would
    otherwise compare lexicographically as opaque text."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X", last_changed="2026-04-27")},
    )
    with pytest.raises(ValueError) as exc_info:
        CorpusReader(tmp_path).list_recent_changes(since="not a date")
    assert str(exc_info.value) == "since must be ISO date YYYY-MM-DD, got 'not a date'"


def test_list_recent_changes_accepts_full_lovdata_dataset_keys(tmp_path: Path) -> None:
    """The dataset alias resolver pins both the friendly Norwegian
    aliases AND the full Lovdata archive keys (mutmut survivor guard)."""
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="a", title="A", last_changed="2026-04-27"),
            "sf-1": _record(
                slug="b",
                title="B",
                source_dataset="gjeldende-sentrale-forskrifter",
                last_changed="2026-04-27",
            ),
        },
    )
    reader = CorpusReader(tmp_path)
    assert [r["slug"] for r in reader.list_recent_changes(dataset="gjeldende-lover")] == ["a"]
    assert [
        r["slug"] for r in reader.list_recent_changes(dataset="gjeldende-sentrale-forskrifter")
    ] == ["b"]


def test_list_recent_changes_excludes_tombstones(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="alive", title="A", last_changed="2026-04-27"),
            "nl-2": _record(
                slug="gone",
                title="G",
                status="removed",
                last_changed="2026-04-27",
            ),
        },
        write_files=False,
    )
    rows = CorpusReader(tmp_path).list_recent_changes()
    assert [r["slug"] for r in rows] == ["alive"]


# ---------- search_laws ----------


def test_bounded_limit_rejects_negative_and_clamps_large() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        _bounded_limit(-1)
    assert _bounded_limit(5) == 5
    assert _bounded_limit(_MAX_RESULT_LIMIT) == _MAX_RESULT_LIMIT
    assert _bounded_limit(10_000) == _MAX_RESULT_LIMIT


def test_search_laws_caps_results_at_limit(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {f"nl-{i}": _record(slug=f"lov-{i}", title=f"Lov {i}") for i in range(5)},
        write_files=False,
    )
    rows = CorpusReader(tmp_path).search_laws("lov", limit=2)
    assert len(rows) == 2


def test_search_laws_clamps_huge_limit_to_max(tmp_path: Path) -> None:
    """A broad query with a huge limit cannot return the whole corpus in one
    response — the result count is capped at _MAX_RESULT_LIMIT."""
    _seed_corpus(
        tmp_path,
        {
            f"nl-{i}": _record(slug=f"lov-{i}", title=f"Lov {i}")
            for i in range(_MAX_RESULT_LIMIT + 10)
        },
        write_files=False,
    )
    rows = CorpusReader(tmp_path).search_laws("lov", limit=10_000)
    assert len(rows) == _MAX_RESULT_LIMIT


def test_search_laws_substring_matches_slug(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="skatteloven", title="Lov om skatt"),
            "nl-2": _record(slug="opplaeringslova", title="Lov om grunnskolen"),
        },
    )
    rows = CorpusReader(tmp_path).search_laws("skatte")
    assert [r["slug"] for r in rows] == ["skatteloven"]


def test_search_laws_row_pins_the_full_response_contract(tmp_path: Path) -> None:
    # The tool row is _record_summary output; pin the whole dict at the tool
    # boundary so a renamed field is a hard failure, not a survivor. Every
    # value is deterministic (no snippet), so this is a clean exact-dict.
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(
                slug="skatteloven",
                title="Skatteloven",
                last_changed="2026-04-27",
                total_changes=3,
            ),
        },
        write_files=False,
    )
    rows = CorpusReader(tmp_path).search_laws("skatte")
    assert rows == [
        {
            "slug": "skatteloven",
            "doc_id": "nl-1",
            "title": "Skatteloven",
            "dataset": "lover",
            "last_changed": "2026-04-27",
            "total_changes": 3,
        },
    ]


def test_search_laws_substring_matches_title(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="x", title="Lov om jernbane"),
            "nl-2": _record(slug="y", title="Lov om noe annet"),
        },
    )
    rows = CorpusReader(tmp_path).search_laws("jernbane")
    assert [r["slug"] for r in rows] == ["x"]


def test_search_laws_is_case_insensitive(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    assert CorpusReader(tmp_path).search_laws("SKATTE")
    assert CorpusReader(tmp_path).search_laws("skatte")


def test_search_laws_empty_query_returns_empty(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    assert CorpusReader(tmp_path).search_laws("") == []
    assert CorpusReader(tmp_path).search_laws("   ") == []


def test_search_laws_filters_by_dataset(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="x", title="Trafikkloven"),
            "sf-1": _record(
                slug="y",
                title="Trafikkforskriften",
                source_dataset="gjeldende-sentrale-forskrifter",
            ),
        },
    )
    reader = CorpusReader(tmp_path)
    assert [r["slug"] for r in reader.search_laws("trafikk", dataset="lover")] == ["x"]
    assert [r["slug"] for r in reader.search_laws("trafikk", dataset="forskrifter")] == ["y"]


def test_search_laws_accepts_full_lovdata_dataset_keys(tmp_path: Path) -> None:
    """Mutmut survivor guard — same as the list_recent_changes variant."""
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="x", title="Trafikkloven"),
            "sf-1": _record(
                slug="y",
                title="Trafikkforskriften",
                source_dataset="gjeldende-sentrale-forskrifter",
            ),
        },
    )
    reader = CorpusReader(tmp_path)
    assert [r["slug"] for r in reader.search_laws("trafikk", dataset="gjeldende-lover")] == ["x"]
    assert [
        r["slug"] for r in reader.search_laws("trafikk", dataset="gjeldende-sentrale-forskrifter")
    ] == ["y"]


def test_search_laws_excludes_tombstones(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="alive", title="Alive Law"),
            "nl-2": _record(slug="gone", title="Gone Law", status="removed"),
        },
        write_files=False,
    )
    rows = CorpusReader(tmp_path).search_laws("law")
    assert [r["slug"] for r in rows] == ["alive"]


def test_search_laws_rejects_unknown_dataset(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    with pytest.raises(CorpusNotFoundError, match="unknown dataset"):
        CorpusReader(tmp_path).search_laws("anything", dataset="bogus")


# ---------- search_body ----------


def test_search_body_finds_substring_in_body_text(tmp_path: Path) -> None:
    body = "# Skatteloven\n\n§ 1. Skattefradrag for boligkjøp er regulert her.\n"
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": body},
    )
    rows = CorpusReader(tmp_path).search_body("boligkjøp")
    assert len(rows) == 1
    assert rows[0]["slug"] == "skatteloven"
    assert rows[0]["match_count"] == 1
    assert "boligkjøp" in rows[0]["snippet"].lower()


def test_search_body_row_pins_the_full_response_contract(tmp_path: Path) -> None:
    # The row key set is an API contract every MCP consumer depends on:
    # renaming a field breaks them all while the field-level tests above stay
    # green (the mutants that rename doc_id / title / dataset survived exactly
    # because no test asserted the whole row). Pin it as an exact dict.
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": "§ 1. Skattefradrag for boligkjøp."},
    )
    rows = CorpusReader(tmp_path).search_body("boligkjøp")
    assert rows == [
        {
            "slug": "skatteloven",
            "doc_id": "nl-1",
            "title": "Skatteloven",
            "dataset": "lover",
            "match_count": 1,
            "snippet": "§ 1. Skattefradrag for boligkjøp.",
        },
    ]


def test_search_body_returns_match_count_for_repeated_substring(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        body_for={"x": "skatte skatte SKATTE Skatte"},
    )
    rows = CorpusReader(tmp_path).search_body("skatte")
    assert rows[0]["match_count"] == 4


def test_search_body_snippet_includes_context_around_first_match(tmp_path: Path) -> None:
    body = "lorem ipsum " * 20 + "TARGET" + " dolor sit amet" * 20
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        body_for={"x": body},
    )
    snippet = CorpusReader(tmp_path).search_body("target")[0]["snippet"]
    assert "TARGET" in snippet
    assert snippet.startswith("...")
    assert snippet.endswith("...")
    # Snippet collapses whitespace and stays roughly bounded.
    assert len(snippet) < 200


def test_search_body_is_case_insensitive(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        body_for={"x": "JERNBANE"},
    )
    assert CorpusReader(tmp_path).search_body("jernbane")
    assert CorpusReader(tmp_path).search_body("Jernbane")
    assert CorpusReader(tmp_path).search_body("jERnBaNE")


def test_search_body_empty_query_returns_empty(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    reader = CorpusReader(tmp_path)
    assert reader.search_body("") == []
    assert reader.search_body("   ") == []


def test_search_body_no_matches_returns_empty(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        body_for={"x": "irrelevant content"},
    )
    assert CorpusReader(tmp_path).search_body("boligkjøp") == []


# ---------- tombstone exclusion across the index-building loops ----------
#
# A removed act keeps its slug and its file — it is a tombstone, not a deletion —
# so `status != "current"` is the only thing standing between repealed law and a
# live-looking answer. Returning superseded law is the worst failure this product
# can produce, worse than returning nothing. Four loops carry the identical guard
# (search_body, _find_current_by_slug's slug index, the body index, the embedding
# index); each mutation-tested guard is exercised here through the surface that
# consumes it, with the tombstone ordered first so a guard that stops the scan
# instead of skipping the record also drops the live match that follows.


def test_get_law_never_returns_a_removed_but_slugged_record(tmp_path: Path) -> None:
    # The point-lookup path is the sharpest: a broken filter here does not merely
    # list the tombstone, it returns the repealed act's full text. The file is on
    # disk (write_files defaults True), so nothing downstream masks the leak — the
    # status filter is the whole defense. The existing tombstone test uses
    # write_files=False, which hides this: the leak then fails at the file read.
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="gone", title="Gone", status="removed"),
            "nl-2": _record(slug="alive", title="Alive"),
        },
    )
    reader = CorpusReader(tmp_path)
    with pytest.raises(CorpusNotFoundError, match="no current law"):
        reader.get_law("gone")
    # The live record after the tombstone must still resolve — a guard that breaks
    # the loop instead of skipping the tombstone would swallow it.
    assert reader.get_law("alive")


def test_body_index_excludes_a_removed_but_slugged_record(tmp_path: Path) -> None:
    # The body index feeds point-lookup grounding, so a tombstone leaking into it
    # would surface repealed text as the evidence behind a citation. Asserted
    # against the index directly because search_body's own status filter would
    # otherwise mask a leak here (it drops the tombstone before the body lookup).
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="gone", title="Gone", status="removed"),
            "nl-2": _record(slug="alive", title="Alive"),
        },
        body_for={"gone": "opphevet", "alive": "gjeldende"},
    )
    index = CorpusReader(tmp_path)._load_body_index()
    assert "gone" not in index
    assert "alive" in index


def test_search_body_does_not_surface_a_removed_but_slugged_record(tmp_path: Path) -> None:
    # End-to-end guard on the property itself: repealed law never appears in a
    # body search, whichever internal filter enforces it.
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="gone", title="Gone", status="removed"),
            "nl-2": _record(slug="alive", title="Alive"),
        },
        body_for={"gone": "opphevet paragraf", "alive": "opphevet paragraf"},
    )
    rows = CorpusReader(tmp_path).search_body("opphevet")
    assert [row["slug"] for row in rows] == ["alive"]


def test_search_body_filters_by_dataset(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="lovact", title="Lov"),
            "sf-1": _record(
                slug="forskact",
                title="Forskrift",
                source_dataset="gjeldende-sentrale-forskrifter",
            ),
        },
        body_for={"lovact": "common term here", "forskact": "common term here too"},
    )
    reader = CorpusReader(tmp_path)
    assert [r["slug"] for r in reader.search_body("common", dataset="lover")] == ["lovact"]
    assert [r["slug"] for r in reader.search_body("common", dataset="forskrifter")] == ["forskact"]


def test_search_body_excludes_tombstones(tmp_path: Path) -> None:
    """Tombstoned doc has no body file on disk anymore — search must
    skip it rather than raise."""
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="alive", title="A"),
            "nl-2": _record(slug="gone", title="G", status="removed"),
        },
        body_for={"alive": "boligkjøp matched here"},
    )
    rows = CorpusReader(tmp_path).search_body("boligkjøp")
    assert [r["slug"] for r in rows] == ["alive"]


def test_search_body_skips_records_without_slug(tmp_path: Path) -> None:
    """Pre-Sprint-4 records have slug=None; search_body must silently
    skip them rather than raise."""
    legacy = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/nl-legacy.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime.now(UTC),
        status="current",
    )
    write_manifest(
        Manifest(
            generated_at=datetime.now(UTC),
            documents={"nl-1": legacy},
        ),
        tmp_path / "manifest.json",
    )
    assert CorpusReader(tmp_path).search_body("anything") == []


def test_search_body_respects_limit(tmp_path: Path) -> None:
    records = {f"nl-{i}": _record(slug=f"s{i}", title=f"S{i}") for i in range(50)}
    _seed_corpus(
        tmp_path,
        records,
        body_for={f"s{i}": "needle" for i in range(50)},
    )
    rows = CorpusReader(tmp_path).search_body("needle", limit=5)
    assert len(rows) == 5


def test_search_body_rejects_negative_limit(tmp_path: Path) -> None:
    """Same contract as list_recent_changes — Python's negative-slice
    semantics ('all but the last N') is unambiguously not what an AI
    caller intends."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    with pytest.raises(ValueError) as exc_info:
        CorpusReader(tmp_path).search_body("anything", limit=-1)
    assert str(exc_info.value) == "limit must be non-negative, got -1"


def test_search_body_default_limit_is_twenty(tmp_path: Path) -> None:
    records = {f"nl-{i:02d}": _record(slug=f"s{i:02d}", title=f"S{i}") for i in range(25)}
    _seed_corpus(
        tmp_path,
        records,
        body_for={f"s{i:02d}": "needle" for i in range(25)},
    )

    assert len(CorpusReader(tmp_path).search_body("needle")) == 20


def test_search_body_zero_limit_returns_empty(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        body_for={"x": "match here"},
    )
    assert CorpusReader(tmp_path).search_body("match", limit=0) == []


def test_search_body_orders_by_match_count_descending(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="few", title="F"),
            "nl-2": _record(slug="many", title="M"),
            "nl-3": _record(slug="some", title="S"),
        },
        body_for={
            "few": "needle",
            "many": "needle needle needle needle needle",
            "some": "needle needle",
        },
    )
    rows = CorpusReader(tmp_path).search_body("needle")
    assert [r["slug"] for r in rows] == ["many", "some", "few"]


def test_search_body_lazy_loads_index_only_once(tmp_path: Path) -> None:
    """Body index is loaded on first call and cached; subsequent calls
    reuse the cached dict (verified by deleting one of the source
    files between calls — the second call still finds the deleted
    doc's content because the index was cached)."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="cached", title="C")},
        body_for={"cached": "kryptovaluta er regulert"},
    )
    reader = CorpusReader(tmp_path)
    first = reader.search_body("kryptovaluta")
    assert len(first) == 1
    # Pull the rug from under the file system after the first scan.
    (tmp_path / "lover" / "cached.md").unlink()
    second = reader.search_body("kryptovaluta")
    assert second == first  # cached, not re-scanned


def test_search_body_rejects_unknown_dataset(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    with pytest.raises(CorpusNotFoundError, match="unknown dataset"):
        CorpusReader(tmp_path).search_body("anything", dataset="bogus")


def test_search_body_ignores_frontmatter_and_title_heading(tmp_path: Path) -> None:
    """Codex PR-A regression. The contract is 'scan body text only'.
    Without stripping frontmatter / H1, search_body would return false
    positives for terms that appear only in metadata (e.g. ministry,
    license, source_provider, or the title-duplicating H1 line).
    Both kinds of metadata are already surfaced via search_laws +
    get_law metadata, so a body hit on them is wrong by contract."""
    # Body text mentions only 'paragraph_term', frontmatter only
    # 'frontmatter_term', H1 only the title 'Skatteloven'.
    body_md = "## Kapittel 1.\n\n### § 1. Virkeområde\n\nparagraph_term explained here.\n"
    # Override the file contents directly to control frontmatter shape.
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": body_md},
    )
    raw = (tmp_path / "lover" / "skatteloven.md").read_text(encoding="utf-8")
    # Confirm the seeded file actually has frontmatter that contains
    # 'frontmatter_term' so the test would catch a regression — patch
    # the seeded file to inject the term into the frontmatter block.
    patched = raw.replace("id: x\n", "id: x\nministry: frontmatter_term\n")
    (tmp_path / "lover" / "skatteloven.md").write_text(patched, encoding="utf-8")

    reader = CorpusReader(tmp_path)
    # Body term IS findable.
    assert [r["slug"] for r in reader.search_body("paragraph_term")] == ["skatteloven"]
    # Frontmatter term is NOT findable via search_body (frontmatter stripped).
    assert reader.search_body("frontmatter_term") == []
    # H1 title is NOT findable via search_body (H1 stripped); search_laws
    # is the right tool for title matching.
    assert reader.search_body("Skatteloven") == []


# ---------- semantic_search ----------


def test_semantic_search_requires_embedder(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})

    with pytest.raises(CorpusNotFoundError) as excinfo:
        CorpusReader(tmp_path).semantic_search("bolig")

    # Pin the availability message whole — a substring match let mutations to any
    # fragment survive (Theme 1: this operator guidance wording is load-bearing).
    assert str(excinfo.value) == (
        "semantic_search is unavailable: OPENAI_API_KEY was not set "
        "at MCP server startup. Set the environment variable and "
        "restart the server."
    )


def test_semantic_search_returns_grounded_hits_with_metadata(tmp_path: Path) -> None:
    body = (
        "## Kapittel 2. Leie\n\n"
        "### § 2-10. Vedlikehold\n\n"
        "Utleieren plikter å holde boligen i stand i leietiden.\n"
    )
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(
                slug="husleieloven",
                title="Husleieloven",
                last_changed="2026-04-27",
            ),
            "nl-2": _record(slug="skatteloven", title="Skatteloven"),
        },
        body_for={"husleieloven": body},
    )
    _write_embedding_file(
        tmp_path,
        "lover",
        "husleieloven",
        [("2-10", [10, 0, 0])],
    )
    _write_embedding_file(
        tmp_path,
        "lover",
        "skatteloven",
        [("5-12", [0, 10, 0])],
    )
    embedder = _FakeEmbedder([1.0, 0.0, 0.0])

    out = CorpusReader(tmp_path, embedder=embedder).semantic_search("leierettigheter")

    assert embedder.queries == ["leierettigheter"]
    assert out["notice"] is None
    rows = out["results"]
    assert rows[0] == {
        "slug": "husleieloven",
        "section_id": "2-10",
        "occurrence": 1,
        "ambiguous_section": False,
        "score": 1.0,
        "title": "Husleieloven",
        "dataset": "lover",
        "citation_hint": "§ 2-10 husleieloven",
        "heading": "§ 2-10. Vedlikehold",
        "snippet": "Utleieren plikter å holde boligen i stand i leietiden.",
        "last_changed": "2026-04-27",
    }
    # The 0.0-score hit falls below the default min_score and is
    # filtered out — similarity that low is noise, not a candidate.
    assert [row["slug"] for row in rows] == ["husleieloven"]


def test_semantic_search_excludes_a_removed_but_slugged_record(tmp_path: Path) -> None:
    # semantic_search builds its candidate list straight from the on-disk
    # embedding files, so the status filter is the only guard between a removed
    # act's embeddings and a live-looking, high-scoring hit. Both records embed
    # along the query direction, so both would score 1.0 if the tombstone leaked;
    # correct behavior returns only the current act. The tombstone is ordered
    # first so a guard that stops the scan also drops the live record behind it.
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="gone", title="Gone", status="removed"),
            "nl-2": _record(slug="husleieloven", title="Husleieloven"),
        },
        body_for={
            "gone": "### § 1-1. Opphevet\n\nDenne loven er opphevet.\n",
            "husleieloven": "### § 2-10. Vedlikehold\n\nUtleieren plikter.\n",
        },
    )
    _write_embedding_file(tmp_path, "lover", "gone", [("1-1", [10, 0, 0])])
    _write_embedding_file(tmp_path, "lover", "husleieloven", [("2-10", [10, 0, 0])])

    out = CorpusReader(
        tmp_path,
        embedder=_FakeEmbedder([1.0, 0.0, 0.0]),
    ).semantic_search("noe", min_score=0.0)

    assert [row["slug"] for row in out["results"]] == ["husleieloven"]


def test_semantic_search_min_score_zero_returns_all_hits(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="husleieloven", title="Husleieloven"),
            "nl-2": _record(slug="skatteloven", title="Skatteloven"),
        },
    )
    _write_embedding_file(tmp_path, "lover", "husleieloven", [("2-10", [10, 0, 0])])
    _write_embedding_file(tmp_path, "lover", "skatteloven", [("5-12", [0, 10, 0])])

    out = CorpusReader(
        tmp_path,
        embedder=_FakeEmbedder([1.0, 0.0, 0.0]),
    ).semantic_search("leierettigheter", min_score=0.0)

    assert [row["slug"] for row in out["results"]] == ["husleieloven", "skatteloven"]
    assert out["results"][1]["score"] == 0.0


def test_semantic_search_all_hits_below_min_score_returns_explicit_notice(
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    _write_embedding_file(tmp_path, "lover", "skatteloven", [("5-12", [0, 10, 0])])

    out = CorpusReader(
        tmp_path,
        embedder=_FakeEmbedder([1.0, 0.0, 0.0]),
    ).semantic_search("noe helt annet")

    assert out["results"] == []
    notice = out["notice"]
    assert notice is not None
    assert "0.25" in notice  # the default min_score
    assert "0.00" in notice  # best candidate score, so the AI can judge the miss
    assert "do not cite" in notice.lower()


def test_semantic_search_grounding_fields_are_null_for_stale_embedding(
    tmp_path: Path,
) -> None:
    """A .bin section id that no longer exists in the rendered Markdown
    (corpus drift) must surface as null grounding fields, not crash."""
    body = "## Kapittel 1.\n\n### § 1-1. Finnes\n\nInnhold.\n"
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="husleieloven", title="Husleieloven")},
        body_for={"husleieloven": body},
    )
    _write_embedding_file(tmp_path, "lover", "husleieloven", [("9-9", [10, 0, 0])])

    out = CorpusReader(
        tmp_path,
        embedder=_FakeEmbedder([1.0, 0.0, 0.0]),
    ).semantic_search("vedlikehold")

    row = out["results"][0]
    assert row["section_id"] == "9-9"
    assert row["heading"] is None
    assert row["snippet"] is None


def test_semantic_search_snippet_is_truncated_and_single_line(tmp_path: Path) -> None:
    long_paragraph = "ord " * 200
    body = f"## Kapittel 1.\n\n### § 1-1. Lang\n\n{long_paragraph}\n"
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="husleieloven", title="Husleieloven")},
        body_for={"husleieloven": body},
    )
    _write_embedding_file(tmp_path, "lover", "husleieloven", [("1-1", [10, 0, 0])])

    out = CorpusReader(
        tmp_path,
        embedder=_FakeEmbedder([1.0, 0.0, 0.0]),
    ).semantic_search("noe")

    snippet = out["results"][0]["snippet"]
    assert snippet.endswith("...")
    assert len(snippet) <= 203  # 200 chars + ellipsis
    assert "\n" not in snippet


def test_semantic_search_filters_by_dataset(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="lovact", title="Lov"),
            "sf-1": _record(
                slug="forskact",
                title="Forskrift",
                source_dataset="gjeldende-sentrale-forskrifter",
            ),
        },
    )
    _write_embedding_file(tmp_path, "lover", "lovact", [("1", [10, 0, 0])])
    _write_embedding_file(tmp_path, "forskrifter", "forskact", [("2", [10, 0, 0])])
    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder([1.0, 0.0, 0.0]))

    assert [r["slug"] for r in reader.semantic_search("common", dataset="lover")["results"]] == [
        "lovact",
    ]
    assert [
        r["slug"] for r in reader.semantic_search("common", dataset="forskrifter")["results"]
    ] == ["forskact"]


def test_semantic_search_raises_when_no_embeddings_found(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})

    with pytest.raises(CorpusNotFoundError, match="no embeddings found"):
        CorpusReader(tmp_path, embedder=_FakeEmbedder([1.0, 0.0, 0.0])).semantic_search("query")


def test_semantic_search_rejects_unknown_dataset(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    _write_embedding_file(tmp_path, "lover", "x", [("1", [10, 0, 0])])

    with pytest.raises(CorpusNotFoundError, match="unknown dataset"):
        CorpusReader(
            tmp_path,
            embedder=_FakeEmbedder([1.0, 0.0, 0.0]),
        ).semantic_search("query", dataset="bogus")


def test_semantic_search_does_not_call_embedder_when_dataset_filter_has_no_hits(
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    _write_embedding_file(tmp_path, "lover", "x", [("1", [10, 0, 0])])
    embedder = _FakeEmbedder([1.0, 0.0, 0.0])

    out = CorpusReader(tmp_path, embedder=embedder).semantic_search(
        "query",
        dataset="forskrifter",
    )

    assert out["results"] == []
    # Pin the no-embeddings notice whole, not by the dataset substring (Theme 1).
    assert out["notice"] == (
        "no embedded sections available in dataset 'forskrifter'; "
        "embeddings may not be backfilled for it yet."
    )
    assert embedder.queries == []


def test_semantic_search_skips_corrupt_embedding_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="bad", title="Bad"),
            "nl-2": _record(slug="good", title="Good"),
        },
    )
    bad_path = tmp_path / "lover" / "embeddings" / "bad.bin"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_bytes(b"not an embedding file")
    _write_embedding_file(tmp_path, "lover", "good", [("1", [10, 0, 0])])

    rows = CorpusReader(
        tmp_path,
        embedder=_FakeEmbedder([1.0, 0.0, 0.0]),
    ).semantic_search("query")["results"]

    assert [r["slug"] for r in rows] == ["good"]
    assert "skipping corrupt bad.bin" in capsys.readouterr().err


def test_semantic_search_skips_embedding_files_with_wrong_dimension(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="current", title="Current"),
            "nl-2": _record(slug="legacy", title="Legacy"),
        },
    )
    good_vector = [10] + [0] * 3071
    _write_embedding_file(tmp_path, "lover", "current", [("1", good_vector)])
    _write_embedding_file(tmp_path, "lover", "legacy", [("2", [10, 0])])

    rows = CorpusReader(
        tmp_path,
        embedder=_FakeEmbedder([1.0] + [0.0] * 3071),
    ).semantic_search("query")["results"]

    assert [r["slug"] for r in rows] == ["current"]
    assert rows[0]["section_id"] == "1"
    stderr = capsys.readouterr().err
    assert "skipping legacy.bin with dim 2" in stderr
    assert "embedder expects 3072" in stderr
    assert "file is from an older model and will be re-embedded on next sync" in stderr
    assert stderr.rstrip().endswith("next sync")


def test_semantic_search_continues_after_wrong_dimension_file(
    tmp_path: Path,
) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="legacy", title="Legacy"),
            "nl-2": _record(slug="current", title="Current"),
        },
    )
    _write_embedding_file(tmp_path, "lover", "legacy", [("1", [10, 0])])
    _write_embedding_file(tmp_path, "lover", "current", [("2", [10, 0, 0])])

    rows = CorpusReader(
        tmp_path,
        embedder=_FakeEmbedder([1.0, 0.0, 0.0]),
    ).semantic_search("query")["results"]

    assert [r["slug"] for r in rows] == ["current"]
    assert rows[0]["section_id"] == "2"


@pytest.mark.parametrize("stale_count", [1, 2])
def test_semantic_search_raises_when_all_embedding_files_have_wrong_dimension(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stale_count: int,
) -> None:
    records = {
        f"nl-{idx}": _record(slug=f"legacy-{idx}", title=f"Legacy {idx}")
        for idx in range(1, stale_count + 1)
    }
    _seed_corpus(tmp_path, records)
    for idx in range(1, stale_count + 1):
        _write_embedding_file(
            tmp_path,
            "lover",
            f"legacy-{idx}",
            [(str(idx), [10, 0])],
        )

    expected_message = (
        f"no usable embeddings: all {stale_count} .bin file(s) are from an older "
        "model with a different dimension. The sync's staleness check keys on "
        "content hash, not dimension, so it will not re-embed these on its own — "
        "delete the stale <dataset>/embeddings/*.bin and run 'lovspor sync' with "
        "OPENAI_API_KEY set (missing files are re-embedded), then 'git pull' in "
        "the corpus to refresh."
    )
    with pytest.raises(CorpusNotFoundError, match=rf"all {stale_count} \.bin file") as exc_info:
        CorpusReader(
            tmp_path,
            embedder=_FakeEmbedder([1.0, 0.0, 0.0]),
        ).semantic_search("query")

    assert str(exc_info.value) == expected_message
    stderr = capsys.readouterr().err
    for idx in range(1, stale_count + 1):
        assert f"skipping legacy-{idx}.bin with dim 2" in stderr


def test_semantic_search_no_embedding_files_uses_bootstrap_message(
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder([1.0, 0.0, 0.0]))

    assert reader._stale_bin_count == 0
    with pytest.raises(CorpusNotFoundError) as exc_info:
        reader.semantic_search("query")

    message = str(exc_info.value)
    # Pin the operator remediation whole: a substring check passes against a
    # message mutated around the fragment it searches for (mutants 399-401,
    # 442-444). This is the instruction that gets embeddings turned on.
    assert message == (
        "no embeddings found in corpus; run 'lovspor sync' with OPENAI_API_KEY "
        "set to populate per-document .bin files, then 'git pull' in the "
        "corpus to refresh."
    )
    assert "older model" not in message  # the stale-bin branch must not fire
    assert reader._stale_bin_count == 0


def test_semantic_search_reuses_cached_embedding_index_and_stale_count(
    tmp_path: Path,
) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="legacy", title="Legacy"),
            "nl-2": _record(slug="current", title="Current"),
        },
    )
    _write_embedding_file(tmp_path, "lover", "legacy", [("1", [10, 0])])
    current_path = tmp_path / "lover" / "embeddings" / "current.bin"
    _write_embedding_file(tmp_path, "lover", "current", [("2", [10, 0, 0])])
    embedder = _FakeEmbedder([1.0, 0.0, 0.0])
    reader = CorpusReader(tmp_path, embedder=embedder)

    first_rows = reader.semantic_search("first")["results"]
    assert [row["slug"] for row in first_rows] == ["current"]
    assert first_rows[0]["section_id"] == "2"
    assert reader._stale_bin_count == 1

    current_path.unlink()
    second_rows = reader.semantic_search("second")["results"]

    assert [row["slug"] for row in second_rows] == ["current"]
    assert second_rows[0]["section_id"] == "2"
    assert reader._stale_bin_count == 1
    assert embedder.queries == ["first", "second"]


def test_semantic_search_empty_query_and_zero_limit_return_notice(tmp_path: Path) -> None:
    """Contract: empty results ALWAYS carry a notice explaining why —
    the AI must never have to guess whether nothing matched or the
    call itself was a no-op (Codex PR #63 round 1)."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder([1.0, 0.0, 0.0]))

    empty_query = reader.semantic_search("")
    assert empty_query["results"] == []
    assert "empty" in (empty_query["notice"] or "")

    zero_limit = reader.semantic_search("query", limit=0)
    assert zero_limit["results"] == []
    assert "limit" in (zero_limit["notice"] or "")


def test_semantic_search_rejects_negative_limit(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})

    with pytest.raises(ValueError, match="limit must be non-negative"):
        CorpusReader(
            tmp_path,
            embedder=_FakeEmbedder([1.0, 0.0, 0.0]),
        ).semantic_search("query", limit=-1)


# ---------- list_sections ----------


def test_list_sections_returns_toc_in_document_order(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": _SAMPLE_BODY_WITH_SECTIONS},
    )
    toc = CorpusReader(tmp_path).list_sections("skatteloven")

    assert toc == [
        {
            "section_id": "1-1",
            "occurrence": 1,
            "heading": "§ 1-1. Virkeområde",
            "parent_chapter": "Kapittel 1. Alminnelige bestemmelser",
            "kind": "section",
        },
        {
            "section_id": "1-2",
            "occurrence": 1,
            "heading": "§ 1-2. Hvem som pålegger skatt",
            "parent_chapter": "Kapittel 1. Alminnelige bestemmelser",
            "kind": "section",
        },
        {
            # A non-§ heading is addressable content, not just a boundary.
            "section_id": "#subsection-grouping-without-section",
            "occurrence": 1,
            "heading": "Subsection grouping without section",
            "parent_chapter": "Kapittel 5. Alminnelig inntekt og fradragene",
            "kind": "block",
        },
        {
            "section_id": "5-12",
            "occurrence": 1,
            "heading": "§ 5-12. Boligsparing for ungdom",
            "parent_chapter": "Kapittel 5. Alminnelig inntekt og fradragene",
            "kind": "section",
        },
        {
            "section_id": "5-13",
            "occurrence": 1,
            "heading": "§ 5-13. Annet",
            "parent_chapter": "Kapittel 5. Alminnelig inntekt og fradragene",
            "kind": "section",
        },
    ]


def test_list_sections_empty_act_returns_empty_list(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="tom-lov", title="Tom lov")},
        body_for={"tom-lov": "Ingen paragrafer her.\n"},
    )
    assert CorpusReader(tmp_path).list_sections("tom-lov") == []


def test_list_sections_unknown_slug_raises_with_recovery_hint(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven-sktl", title="Skatteloven")})
    with pytest.raises(CorpusNotFoundError, match="no current law"):
        CorpusReader(tmp_path).list_sections("finnes-ikke")


def test_list_sections_does_not_load_full_body_index(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="skatteloven", title="Skatteloven"),
            "nl-2": _record(slug="annen-lov", title="Annen lov"),
        },
        body_for={"skatteloven": _SAMPLE_BODY_WITH_SECTIONS},
    )
    reader = CorpusReader(tmp_path)
    reader.list_sections("skatteloven")

    assert reader._body_index is None
    assert set(reader._doc_bodies) == {"skatteloven"}


# ---------- get_section ----------


_SAMPLE_BODY_WITH_SECTIONS = """\
## Kapittel 1. Alminnelige bestemmelser

### § 1-1. Virkeområde

(1) Denne lov gjelder formuesskatt til stat og kommune.

(2) Stortinget kan fastsette unntak.

### § 1-2. Hvem som pålegger skatt

Denne paragraf regulerer skattepliktens omfang.

## Kapittel 5. Alminnelig inntekt og fradragene

### Subsection grouping without section
### § 5-12. Boligsparing for ungdom

(1) Skattefradraget gis for sparing til bolig.

(2) Fradraget reduseres ved utbetaling.

### § 5-13. Annet
Innhold for § 5-13.
"""


def test_get_section_returns_expected_fields(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": _SAMPLE_BODY_WITH_SECTIONS},
    )
    reader = CorpusReader(tmp_path)
    section = reader.get_section("skatteloven", "5-12")
    assert section["slug"] == "skatteloven"
    assert section["section_id"] == "5-12"
    assert section["heading"] == "§ 5-12. Boligsparing for ungdom"
    assert section["parent_chapter"] == "Kapittel 5. Alminnelig inntekt og fradragene"
    assert "Skattefradraget gis for sparing til bolig" in section["body"]
    assert "Fradraget reduseres ved utbetaling" in section["body"]
    assert section["cross_references"] == []
    # No cross-references -> no other act was read or parsed, and the
    # corpus-wide body index stayed untouched.
    assert set(reader._section_ids_cache) == {"skatteloven"}
    assert reader._body_index is None


@pytest.mark.parametrize(
    "raw_section_id",
    ["§ 5-12", "§5-12", "5-12.", " 5-12 ", "§ 5-12."],
)
def test_get_section_normalizes_section_id_forms(
    tmp_path: Path,
    raw_section_id: str,
) -> None:
    """AIs naturally write '§ 5-12' or '5-12.'; the bare id is the
    documented form but the obvious variants must not cost an error
    round trip. The response always carries the canonical bare id."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": _SAMPLE_BODY_WITH_SECTIONS},
    )
    section = CorpusReader(tmp_path).get_section("skatteloven", raw_section_id)

    assert section["section_id"] == "5-12"
    assert section["heading"] == "§ 5-12. Boligsparing for ungdom"


def test_verify_quote_normalizes_section_id_forms(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": _SAMPLE_BODY_WITH_SECTIONS},
    )
    out = CorpusReader(tmp_path).verify_quote(
        "skatteloven",
        "§ 5-12",
        "Skattefradraget gis for sparing til bolig",
    )

    assert out["verified"] is True
    assert out["section_id"] == "5-12"


def test_get_section_extracts_same_act_cross_references(tmp_path: Path) -> None:
    body = (
        "## Kapittel 1.\n\n"
        "### § 1-1. Main\n\n"
        "Se § 1-2 for definisjonen. Se også § 1-99 og deretter § 1-2 igjen.\n\n"
        "### § 1-2. Defined\n\n"
        "Target body.\n"
    )
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="egen-lov", title="Egen lov")},
        body_for={"egen-lov": body},
    )

    section = CorpusReader(tmp_path).get_section("egen-lov", "1-1")

    assert section["cross_references"] == [
        {
            "text": "§ 1-2",
            "target_slug": "egen-lov",
            "target_section_id": "1-2",
            "valid": True,
            "reason": None,
        },
        {
            "text": "§ 1-99",
            "target_slug": "egen-lov",
            "target_section_id": "1-99",
            "valid": False,
            "reason": "§ 1-99 not found in 'egen-lov'",
        },
    ]


def test_get_section_extracts_cross_act_references_from_slug_window(
    tmp_path: Path,
) -> None:
    current_body = (
        "## Kapittel 1.\n\n"
        "### § 1-1. Main\n\n"
        "Se § 2-1 i annen-lov. Brudd på § 9-9 i annen-lov er ugyldig.\n"
    )
    target_body = "## Kapittel 2.\n\n### § 2-1. Target\n\nTarget body.\n"
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="egen-lov", title="Egen lov"),
            "nl-2": _record(slug="annen-lov", title="Annen lov"),
        },
        body_for={
            "egen-lov": current_body,
            "annen-lov": target_body,
        },
    )

    section = CorpusReader(tmp_path).get_section("egen-lov", "1-1")

    assert section["cross_references"] == [
        {
            "text": "§ 2-1",
            "target_slug": "annen-lov",
            "target_section_id": "2-1",
            "valid": True,
            "reason": None,
        },
        {
            "text": "§ 9-9",
            "target_slug": "annen-lov",
            "target_section_id": "9-9",
            "valid": False,
            "reason": "§ 9-9 not found in 'annen-lov'",
        },
    ]


def test_get_section_keeps_adjacent_reference_contexts_separate(
    tmp_path: Path,
) -> None:
    current_body = (
        "## Kapittel 5.\n\n"
        "### § 5-12. Main\n\n"
        "Se § 5-13. Likevel kan det iht. § 9-3 i annen-lov gjelde unntak.\n\n"
        "### § 5-13. Same act target\n\n"
        "Same act body.\n"
    )
    target_body = "## Kapittel 9.\n\n### § 9-3. Cross act target\n\nCross act body.\n"
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="egen-lov", title="Egen lov"),
            "nl-2": _record(slug="annen-lov", title="Annen lov"),
        },
        body_for={
            "egen-lov": current_body,
            "annen-lov": target_body,
        },
    )

    section = CorpusReader(tmp_path).get_section("egen-lov", "5-12")

    assert section["cross_references"] == [
        {
            "text": "§ 5-13",
            "target_slug": "egen-lov",
            "target_section_id": "5-13",
            "valid": True,
            "reason": None,
        },
        {
            "text": "§ 9-3",
            "target_slug": "annen-lov",
            "target_section_id": "9-3",
            "valid": True,
            "reason": None,
        },
    ]


def test_get_section_slug_before_next_reference_does_not_leak_backward(
    tmp_path: Path,
) -> None:
    current_body = (
        "## Kapittel 5.\n\n"
        "### § 5-12. Main\n\n"
        "Se § 5-13. Etter annen-lov § 9-3.\n\n"
        "### § 5-13. Same act target\n\n"
        "Same act body.\n"
    )
    target_body = "## Kapittel 9.\n\n### § 9-3. Cross act target\n\nCross act body.\n"
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="egen-lov", title="Egen lov"),
            "nl-2": _record(slug="annen-lov", title="Annen lov"),
        },
        body_for={
            "egen-lov": current_body,
            "annen-lov": target_body,
        },
    )

    section = CorpusReader(tmp_path).get_section("egen-lov", "5-12")

    assert section["cross_references"] == [
        {
            "text": "§ 5-13",
            "target_slug": "egen-lov",
            "target_section_id": "5-13",
            "valid": True,
            "reason": None,
        },
        {
            "text": "§ 9-3",
            "target_slug": "annen-lov",
            "target_section_id": "9-3",
            "valid": True,
            "reason": None,
        },
    ]


def test_get_section_keeps_three_reference_contexts_separate(
    tmp_path: Path,
) -> None:
    current_body = (
        "## Kapittel 5.\n\n"
        "### § 5-12. Main\n\n"
        "Se § 5-13. Likevel kan § 9-3 i annen-lov gjelde. "
        f"{'fylltekst ' * 12}Se også § 5-14.\n\n"
        "### § 5-13. First same act target\n\n"
        "First target body.\n\n"
        "### § 5-14. Second same act target\n\n"
        "Second target body.\n"
    )
    target_body = "## Kapittel 9.\n\n### § 9-3. Cross act target\n\nCross act body.\n"
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="egen-lov", title="Egen lov"),
            "nl-2": _record(slug="annen-lov", title="Annen lov"),
        },
        body_for={
            "egen-lov": current_body,
            "annen-lov": target_body,
        },
    )

    section = CorpusReader(tmp_path).get_section("egen-lov", "5-12")

    assert section["cross_references"] == [
        {
            "text": "§ 5-13",
            "target_slug": "egen-lov",
            "target_section_id": "5-13",
            "valid": True,
            "reason": None,
        },
        {
            "text": "§ 9-3",
            "target_slug": "annen-lov",
            "target_section_id": "9-3",
            "valid": True,
            "reason": None,
        },
        {
            "text": "§ 5-14",
            "target_slug": "egen-lov",
            "target_section_id": "5-14",
            "valid": True,
            "reason": None,
        },
    ]


def test_get_section_cross_ref_context_excludes_eighty_first_char(
    tmp_path: Path,
) -> None:
    current_body = (
        "## Kapittel 1.\n\n"
        "### § 1-1. Main\n\n"
        f"x{' ' * 80}§ 1-2 omtales her.\n\n"
        "### § 1-2. Same act target\n\n"
        "Target body.\n"
    )
    other_body = "## Kapittel 1.\n\n### § 9-9. Other target\n\nOther body.\n"
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="egen-lov", title="Egen lov"),
            "nl-2": _record(slug="x", title="X"),
        },
        body_for={
            "egen-lov": current_body,
            "x": other_body,
        },
    )

    section = CorpusReader(tmp_path).get_section("egen-lov", "1-1")

    assert section["cross_references"] == [
        {
            "text": "§ 1-2",
            "target_slug": "egen-lov",
            "target_section_id": "1-2",
            "valid": True,
            "reason": None,
        },
    ]


def test_get_section_first_reference_context_can_start_at_body_start(
    tmp_path: Path,
) -> None:
    current_body = "## Kapittel 1.\n\n### § 1-1. Main\n\nx § 9-9 omtales her.\n"
    other_body = "## Kapittel 9.\n\n### § 9-9. Other target\n\nOther body.\n"
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="egen-lov", title="Egen lov"),
            "nl-2": _record(slug="x", title="X"),
        },
        body_for={
            "egen-lov": current_body,
            "x": other_body,
        },
    )

    section = CorpusReader(tmp_path).get_section("egen-lov", "1-1")

    assert section["cross_references"] == [
        {
            "text": "§ 9-9",
            "target_slug": "x",
            "target_section_id": "9-9",
            "valid": True,
            "reason": None,
        },
    ]


def test_get_section_second_reference_does_not_scan_before_previous_ref(
    tmp_path: Path,
) -> None:
    current_body = "## Kapittel 1.\n\n### § 1-1. Main\n\nannen-lov § 5-13. Se § 9-3 i kort-lov.\n"
    first_target_body = "## Kapittel 5.\n\n### § 5-13. First target\n\nFirst body.\n"
    second_target_body = "## Kapittel 9.\n\n### § 9-3. Second target\n\nSecond body.\n"
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="egen-lov", title="Egen lov"),
            "nl-2": _record(slug="annen-lov", title="Annen lov"),
            "nl-3": _record(slug="kort-lov", title="Kort lov"),
        },
        body_for={
            "egen-lov": current_body,
            "annen-lov": first_target_body,
            "kort-lov": second_target_body,
        },
    )

    section = CorpusReader(tmp_path).get_section("egen-lov", "1-1")
    second_ref = next(
        ref for ref in section["cross_references"] if ref["target_section_id"] == "9-3"
    )

    assert second_ref == {
        "text": "§ 9-3",
        "target_slug": "kort-lov",
        "target_section_id": "9-3",
        "valid": True,
        "reason": None,
    }


def test_get_section_prefers_longest_slug_token_in_reference_window(
    tmp_path: Path,
) -> None:
    current_body = "## Kapittel 1.\n\n### § 1-1. Main\n\nSe § 9-1 i lov lang-lov.\n"
    short_body = "## Kapittel 9.\n\n### § 9-2. Short target\n\nShort body.\n"
    long_body = "## Kapittel 9.\n\n### § 9-1. Long target\n\nLong body.\n"
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="egen-lov", title="Egen lov"),
            "nl-2": _record(slug="lov", title="Lov"),
            "nl-3": _record(slug="lang-lov", title="Lang lov"),
        },
        body_for={
            "egen-lov": current_body,
            "lov": short_body,
            "lang-lov": long_body,
        },
    )

    section = CorpusReader(tmp_path).get_section("egen-lov", "1-1")

    assert section["cross_references"] == [
        {
            "text": "§ 9-1",
            "target_slug": "lang-lov",
            "target_section_id": "9-1",
            "valid": True,
            "reason": None,
        },
    ]


def test_get_section_reuses_cached_section_ids_for_cross_references(
    tmp_path: Path,
) -> None:
    body = "## Kapittel 1.\n\n### § 1-1. Main\n\nSe § 1-2.\n\n### § 1-2. Target\n\nTarget body.\n"
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="egen-lov", title="Egen lov")},
        body_for={"egen-lov": body},
    )
    reader = CorpusReader(tmp_path)

    first_section = reader.get_section("egen-lov", "1-1")
    assert first_section["cross_references"][0]["valid"] is True
    cached_ids = reader._section_ids_cache["egen-lov"]

    second_section = reader.get_section("egen-lov", "1-1")

    assert second_section["cross_references"] == first_section["cross_references"]
    assert reader._section_ids_cache["egen-lov"] is cached_ids


def test_get_section_does_not_load_full_body_index(tmp_path: Path) -> None:
    """A point lookup must read only its own doc's file — never pay
    the corpus-wide body-index load that search_body needs."""
    body = "## Kapittel 1.\n\n### § 1-1. Main\n\nSe § 1-2 i annen-lov.\n\n### § 1-2. T\n\nB.\n"
    other = "## Kapittel 1.\n\n### § 1-2. Other\n\nOther body.\n"
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="egen-lov", title="Egen lov"),
            "nl-2": _record(slug="annen-lov", title="Annen lov"),
            "nl-3": _record(slug="untouched-lov", title="Untouched"),
        },
        body_for={"egen-lov": body, "annen-lov": other, "untouched-lov": other},
    )
    reader = CorpusReader(tmp_path)

    section = reader.get_section("egen-lov", "1-1")

    assert section["cross_references"][0]["target_slug"] == "annen-lov"
    assert section["cross_references"][0]["valid"] is True
    assert reader._body_index is None
    # Only the two acts involved in the lookup were read and parsed.
    assert set(reader._section_ids_cache) == {"egen-lov", "annen-lov"}
    assert set(reader._doc_bodies) == {"egen-lov", "annen-lov"}


def test_extract_cross_references_reports_unknown_current_slug() -> None:
    assert _extract_cross_references("Se § 1-1.", "missing-lov", set(), lambda _slug: set()) == [
        {
            "text": "§ 1-1",
            "target_slug": "missing-lov",
            "target_section_id": "1-1",
            "valid": False,
            "reason": "slug 'missing-lov' unknown",
        },
    ]


def test_compute_match_owner_starts_detects_first_adjacent_slug_owner() -> None:
    body = "Se § 5-13. Etter annen-lov § 9-3."
    matches = list(_CROSS_REF_SECTION.finditer(body))

    owners = _compute_match_owner_starts(body.lower(), matches, {"annen-lov"})

    assert owners == {0: body.index("annen-lov")}


def test_compute_match_owner_starts_ignores_unknown_slug_shaped_filler() -> None:
    body = "Se § 5-13 samt § 9-3."
    matches = list(_CROSS_REF_SECTION.finditer(body))

    owners = _compute_match_owner_starts(body.lower(), matches, {"annen-lov"})

    assert owners == {}


def test_compute_match_owner_starts_continues_after_pair_without_owner() -> None:
    body = "Se § 1-1 og § 2-1. Deretter annen-lov § 9-3."
    matches = list(_CROSS_REF_SECTION.finditer(body))

    owners = _compute_match_owner_starts(body.lower(), matches, {"annen-lov"})

    assert owners == {1: body.index("annen-lov")}


def test_compute_match_owner_starts_requires_owner_immediately_before_section() -> None:
    body = "Se § 5-13. Etter annen-lov gjelder § 9-3."
    matches = list(_CROSS_REF_SECTION.finditer(body))

    owners = _compute_match_owner_starts(body.lower(), matches, {"annen-lov"})

    assert owners == {}


def test_get_section_body_excludes_next_section(tmp_path: Path) -> None:
    """Section body must end at the next ### or ## heading. § 5-12's
    body must NOT contain content from § 5-13 that follows."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": _SAMPLE_BODY_WITH_SECTIONS},
    )
    section = CorpusReader(tmp_path).get_section("skatteloven", "5-12")
    assert "Annet" not in section["body"]
    assert "§ 5-13" not in section["body"]
    assert "Innhold for § 5-13" not in section["body"]


def test_get_section_body_excludes_next_chapter(tmp_path: Path) -> None:
    """Section body must end at the next ## chapter heading too."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": _SAMPLE_BODY_WITH_SECTIONS},
    )
    section = CorpusReader(tmp_path).get_section("skatteloven", "1-2")
    assert "Kapittel 5" not in section["body"]
    assert "Skattefradraget" not in section["body"]


def test_get_section_handles_single_number_section(tmp_path: Path) -> None:
    """Acts with a single chapter use plain ``§ N`` (e.g.
    ``§ 1. Lovens virkeområde`` in Statsforetaksloven)."""
    body = (
        "## Kapittel 1.\n\n"
        "### § 1. Lovens virkeområde\n\n"
        "Denne lov gjelder statsforetak.\n\n"
        "### § 2. Definisjoner\n\n"
        "I denne lov forstås.\n"
    )
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="statsforetaksloven", title="Statsforetaksloven")},
        body_for={"statsforetaksloven": body},
    )
    section = CorpusReader(tmp_path).get_section("statsforetaksloven", "1")
    assert section["section_id"] == "1"
    assert "Denne lov gjelder statsforetak" in section["body"]


def test_get_section_handles_letter_suffix(tmp_path: Path) -> None:
    """Some sections use a letter suffix (e.g. ``§ 5-12a``) when the
    legislator inserted a section without renumbering siblings."""
    body = (
        "## Kapittel 5.\n\n"
        "### § 5-12. Original\nOriginal content.\n\n"
        "### § 5-12a. Inserted later\n\nLetter suffix content.\n"
    )
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": body},
    )
    section = CorpusReader(tmp_path).get_section("skatteloven", "5-12a")
    assert section["heading"] == "§ 5-12a. Inserted later"
    assert "Letter suffix content" in section["body"]


def test_get_section_subsection_grouping_is_boundary(tmp_path: Path) -> None:
    """A ``### Title`` heading without ``§`` (e.g. a subsection
    grouping) closes the previous section but does not open a new
    one. Content between the grouping and the next ``### §`` is not
    attributed to any section."""
    body = (
        "## Kapittel 2.\n\n"
        "### § 2-5. Foo\nFoo body.\n\n"
        "### Subsection grouping\n"
        "Orphan content here.\n\n"
        "### § 2-10. Bar\nBar body.\n"
    )
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        body_for={"x": body},
    )
    reader = CorpusReader(tmp_path)
    foo = reader.get_section("x", "2-5")
    assert foo["body"] == "Foo body."
    bar = reader.get_section("x", "2-10")
    assert bar["body"] == "Bar body."
    # Orphan content was attributed to neither section.
    assert "Orphan" not in foo["body"]
    assert "Orphan" not in bar["body"]


def test_get_section_handles_empty_body(tmp_path: Path) -> None:
    """A section with no content between its heading and the next
    boundary returns an empty body, not a crash."""
    body = (
        "## Kapittel 1.\n\n### § 1-1. Heading only\n\n### § 1-2. Has content\nReal content here.\n"
    )
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        body_for={"x": body},
    )
    section = CorpusReader(tmp_path).get_section("x", "1-1")
    assert section["body"] == ""
    assert section["heading"] == "§ 1-1. Heading only"


def test_get_section_raises_with_available_list_when_section_missing(
    tmp_path: Path,
) -> None:
    """Codex-suggested UX: error message lists available section ids
    in natural order so the AI can recover without a separate
    get_law call."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": _SAMPLE_BODY_WITH_SECTIONS},
    )
    with pytest.raises(CorpusNotFoundError) as exc_info:
        CorpusReader(tmp_path).get_section("skatteloven", "5-99")
    msg = str(exc_info.value)
    assert msg == (
        "section '5-99' not found in 'skatteloven'; "
        "available: § 1-1, § 1-2, § 5-12, § 5-13; "
        "1 non-§ content block(s) — call list_sections to see them"
    )


def test_get_section_missing_message_when_act_has_no_sections(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        body_for={"x": "## Kapittel uten paragrafer\nBare tekst."},
    )

    with pytest.raises(CorpusNotFoundError) as exc_info:
        CorpusReader(tmp_path).get_section("x", "1-1")

    assert str(exc_info.value) == (
        "section '1-1' not found in 'x'; available: (no sections in this act)"
    )


def test_get_section_natural_order_in_error_message(tmp_path: Path) -> None:
    """Available list must order ``5-2``, ``5-10``, ``5-11`` numerically
    rather than lexicographically — otherwise '5-10' would sort before
    '5-2' and confuse the AI."""
    body = (
        "## Kapittel 5.\n\n"
        "### § 5-1. A\nA.\n"
        "### § 5-2. B\nB.\n"
        "### § 5-10. C\nC.\n"
        "### § 5-11. D\nD.\n"
    )
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        body_for={"x": body},
    )
    with pytest.raises(CorpusNotFoundError) as exc_info:
        CorpusReader(tmp_path).get_section("x", "5-99")
    msg = str(exc_info.value)
    assert msg.index("§ 5-2") < msg.index("§ 5-10")
    assert msg.index("§ 5-10") < msg.index("§ 5-11")


def test_get_section_raises_for_unknown_slug(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="real", title="Real")})
    with pytest.raises(CorpusNotFoundError, match="no current law"):
        CorpusReader(tmp_path).get_section("not-a-slug", "1-1")


def test_get_section_handles_untitled_legal_article_heading(tmp_path: Path) -> None:
    """Codex PR-B regression. Lovdata's source XML sometimes ships
    a ``legalArticleValue`` with no accompanying ``title`` field,
    in which case the renderer emits a bare ``### § N`` heading
    (no dot, no title text). The previous regex required ``\\.\\s+title``
    so these sections were invisible to get_section even though they
    exist in the rendered Markdown."""
    body = "## Kapittel 1.\n\n### § 5\n\nBody text here.\n\n### § 6\n\nMore body.\n"
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        body_for={"x": body},
    )
    section = CorpusReader(tmp_path).get_section("x", "5")
    assert section["section_id"] == "5"
    assert section["heading"] == "§ 5"
    assert section["body"] == "Body text here."


def test_get_section_available_list_with_letter_suffix_siblings_does_not_crash(
    tmp_path: Path,
) -> None:
    """Codex PR-B regression. When an act contains both ``§ 5-12``
    (numeric tail) and ``§ 5-12a`` (letter-suffix tail) and the AI
    asks for a missing section, the available-sections recovery
    message used to crash with TypeError because the natural-key
    sort mixed (5, 12) with (5, '12a'). The fix tags each segment
    with a numeric / string discriminator so comparisons stay
    type-homogeneous; numbers always sort before strings within a
    chapter."""
    body = (
        "## Kapittel 5.\n\n"
        "### § 5-2. Foo\nFoo.\n"
        "### § 5-12. Bar\nBar.\n"
        "### § 5-12a. Inserted\nInserted.\n"
    )
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        body_for={"x": body},
    )
    with pytest.raises(CorpusNotFoundError) as exc_info:
        CorpusReader(tmp_path).get_section("x", "5-99")
    msg = str(exc_info.value)
    # Order pinned: 5-2 < 5-12 (numeric) < 5-12a (letter suffix sorts last
    # within the same chapter because string > int under our key).
    assert msg.index("§ 5-2") < msg.index("§ 5-12")
    assert msg.index("§ 5-12") < msg.index("§ 5-12a")


def test_get_section_uses_cached_body_index_without_frontmatter(
    tmp_path: Path,
) -> None:
    """get_section reuses _load_body_index, so the parser only sees
    body content (frontmatter + H1 already stripped). A frontmatter
    field that happened to look like a section heading must not be
    parsed as one."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": _SAMPLE_BODY_WITH_SECTIONS},
    )
    raw = (tmp_path / "lover" / "skatteloven.md").read_text(encoding="utf-8")
    # Inject a fake section heading INTO the frontmatter — must not be
    # picked up by the section parser.
    patched = raw.replace("id: x\n", "id: x\n### § 99-99. Fake\n")
    (tmp_path / "lover" / "skatteloven.md").write_text(patched, encoding="utf-8")
    with pytest.raises(CorpusNotFoundError):
        CorpusReader(tmp_path).get_section("skatteloven", "99-99")


# ---------- validate_citation ----------


def _seed_for_citation(tmp_path: Path) -> CorpusReader:
    """Set up a corpus with skatteloven-sktl + opplaeringslova having
    real sections, so citation validation has something to resolve
    against."""
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="skatteloven-sktl", title="Skatteloven"),
            "nl-2": _record(slug="opplaeringslova", title="Opplæringslova"),
        },
        body_for={
            "skatteloven-sktl": (
                "## Kapittel 5.\n\n### § 5-12. Boligsparing\nContent.\n### § 5-13. Annet\nMore.\n"
            ),
            "opplaeringslova": ("## Kapittel 1.\n\n### § 1-1. Virkeområde\nContent.\n"),
        },
    )
    return CorpusReader(tmp_path)


def test_validate_citation_valid_with_slug_and_section(tmp_path: Path) -> None:
    result = _seed_for_citation(tmp_path).validate_citation("§ 5-12 skatteloven-sktl")
    assert result["valid"] is True
    assert result["slug"] == "skatteloven-sktl"
    assert result["section_id"] == "5-12"
    assert result["heading"] == "§ 5-12. Boligsparing"
    assert result["reason"] is None


def test_validate_citation_accepts_reverse_order(tmp_path: Path) -> None:
    """Citation with slug FIRST then § id — Norwegian convention varies."""
    result = _seed_for_citation(tmp_path).validate_citation("skatteloven-sktl § 5-12")
    assert result["valid"] is True
    assert result["slug"] == "skatteloven-sktl"
    assert result["section_id"] == "5-12"


def test_validate_citation_accepts_norwegian_filler_word(tmp_path: Path) -> None:
    """``§ 5-12 i skatteloven-sktl`` — Norwegian for 'in'."""
    result = _seed_for_citation(tmp_path).validate_citation(
        "§ 5-12 i skatteloven-sktl",
    )
    assert result["valid"] is True


def test_validate_citation_accepts_no_space_between_paragraph_and_id(
    tmp_path: Path,
) -> None:
    """``§5-12`` (no space) is also valid Norwegian shorthand."""
    result = _seed_for_citation(tmp_path).validate_citation("§5-12 skatteloven-sktl")
    assert result["valid"] is True
    assert result["section_id"] == "5-12"


def test_validate_citation_slug_only_is_valid(tmp_path: Path) -> None:
    """A bare slug (no §) is valid as long as the slug is known —
    the user is referring to the whole act."""
    result = _seed_for_citation(tmp_path).validate_citation("skatteloven-sktl")
    assert result["valid"] is True
    assert result["slug"] == "skatteloven-sktl"
    assert result["section_id"] is None
    assert result["heading"] is None
    assert set(result) == {"valid", "slug", "section_id", "heading", "reason"}
    assert result["reason"] is None


def test_validate_citation_unknown_slug_is_invalid(tmp_path: Path) -> None:
    """Strict slug match: 'skatteloven' (without -sktl) does NOT
    fuzzy-match production slug 'skatteloven-sktl'. Per Q2=A
    decision: cleaner contract, AI should use canonical slugs from
    search_laws."""
    result = _seed_for_citation(tmp_path).validate_citation("§ 5-12 skatteloven")
    assert result["valid"] is False
    assert result["slug"] is None
    assert result["section_id"] == "5-12"
    assert "ambiguous" in result["reason"].lower()


def test_validate_citation_paragraph_only_is_ambiguous(tmp_path: Path) -> None:
    """``§ 5-12`` without an act identifier is invalid because many
    acts have a section by that id — the AI can't be confirmed
    referring to a specific one."""
    result = _seed_for_citation(tmp_path).validate_citation("§ 5-12")
    assert result["valid"] is False
    assert result["slug"] is None
    assert result["section_id"] == "5-12"
    assert set(result) == {"valid", "slug", "section_id", "heading", "reason"}
    assert result["heading"] is None
    assert result["reason"] == (
        "ambiguous citation: § 5-12 found but no act identifier; "
        "many acts have a section by that id"
    )


def test_validate_citation_unparseable_returns_invalid(tmp_path: Path) -> None:
    """No § and no known slug → can't parse anything useful."""
    result = _seed_for_citation(tmp_path).validate_citation("just some prose")
    assert result["valid"] is False
    assert result["slug"] is None
    assert result["section_id"] is None
    assert set(result) == {"valid", "slug", "section_id", "heading", "reason"}
    assert result["heading"] is None
    assert result["reason"] == (
        "could not parse citation 'just some prose': no § id and no known slug found"
    )


def test_validate_citation_unknown_section_returns_helpful_error(
    tmp_path: Path,
) -> None:
    """Section that doesn't exist in the matched act → invalid +
    available-sections list quoted from get_section."""
    result = _seed_for_citation(tmp_path).validate_citation(
        "§ 5-99 skatteloven-sktl",
    )
    assert result["valid"] is False
    assert result["slug"] == "skatteloven-sktl"
    assert result["section_id"] == "5-99"
    assert set(result) == {"valid", "slug", "section_id", "heading", "reason"}
    assert result["heading"] is None
    assert "5-99" in result["reason"]
    assert "§ 5-12" in result["reason"]


def test_validate_citation_picks_longest_slug_match(tmp_path: Path) -> None:
    """If multiple manifest slugs appear as substrings of the
    citation, pick the LONGEST one (most specific)."""
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="loven", title="Loven"),
            "nl-2": _record(slug="loven-extension", title="Loven Extension"),
        },
        body_for={
            "loven-extension": "## K\n### § 1. T\nC.\n",
        },
    )
    # Citation matches both slugs as substrings; longest wins.
    result = CorpusReader(tmp_path).validate_citation("§ 1 loven-extension")
    assert result["slug"] == "loven-extension"


def test_validate_citation_rejects_slug_with_extra_suffix_characters(
    tmp_path: Path,
) -> None:
    """Codex PR-C regression. Plain ``slug in citation`` substring
    match was too lax: ``'skatteloven-sktl' in 'skatteloven-sktlX'``
    is True, so a citation with garbage trailing characters silently
    validated. The strict contract requires token-boundary matching;
    the trailing ``X`` is itself a slug character so the right end of
    the match is not at a boundary."""
    reader = _seed_for_citation(tmp_path)

    plain = reader.validate_citation("skatteloven-sktlX")
    assert plain["valid"] is False
    assert plain["slug"] is None

    with_section = reader.validate_citation("§ 5-12 skatteloven-sktlX")
    assert with_section["valid"] is False
    assert with_section["slug"] is None
    # The § id is still extracted, but slug match is rejected so the
    # citation falls into the 'ambiguous: no act identifier' branch.
    assert with_section["section_id"] == "5-12"
    assert "ambiguous" in with_section["reason"].lower()


def test_validate_citation_rejects_slug_inside_longer_word(tmp_path: Path) -> None:
    """Same defense for the LEFT boundary: a slug appearing inside a
    longer alphanumeric run (e.g. ``preskatteloven-sktl``) must not
    match — the leading ``pre`` keeps the start of the match
    boundary-less."""
    result = _seed_for_citation(tmp_path).validate_citation(
        "preskatteloven-sktl",
    )
    assert result["valid"] is False
    assert result["slug"] is None


def test_validate_citation_finds_slug_after_punctuation(tmp_path: Path) -> None:
    """Punctuation around a slug counts as a token boundary, so
    citations like ``'§ 5-12, skatteloven-sktl.'`` (with comma /
    period delimiters) still resolve."""
    result = _seed_for_citation(tmp_path).validate_citation(
        "§ 5-12, skatteloven-sktl.",
    )
    assert result["valid"] is True
    assert result["slug"] == "skatteloven-sktl"


def test_validate_citation_excludes_tombstones(tmp_path: Path) -> None:
    """A removed law's slug must not match — only current docs are
    valid citation targets."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="goneloven", title="Gone", status="removed")},
        write_files=False,
    )
    result = CorpusReader(tmp_path).validate_citation("goneloven")
    assert result["valid"] is False
    assert result["slug"] is None


def test_search_body_match_count_is_non_overlapping(tmp_path: Path) -> None:
    """Pin the str.count semantics: 'aaaa'.count('aa') returns 2,
    not 3 — non-overlapping matches. Documented behavior, not an
    accident, so a regression here would surface as silent under-
    count rather than a crash."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        body_for={"x": "## H\n\naaaa"},
    )
    rows = CorpusReader(tmp_path).search_body("aa")
    assert rows[0]["match_count"] == 2


# ---------- verify_quote ----------


def test_verify_quote_accepts_case_and_whitespace_differences(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={
            "skatteloven": (
                "## Kapittel 1.\n\n"
                "### § 1-1. Virkeområde\n\n"
                "Dette er første\nlinje med tekst.\n\n"
                "### § 1-2. Annet\n\nAndre regler.\n"
            ),
        },
    )

    result = CorpusReader(tmp_path).verify_quote(
        "skatteloven",
        "1-1",
        "DETTE er første linje med tekst.",
    )

    assert result == {
        "verified": True,
        "slug": "skatteloven",
        "section_id": "1-1",
        "reason": None,
    }


@pytest.mark.parametrize(
    ("source_text", "quoted_text"),
    [
        # Curly vs straight apostrophe (chat UIs rewrite these).
        ("barnets «beste» og foreldres ansvar", "barnets «beste» og foreldres ansvar"),
        ("avtalens \u2019gyldighet\u2019 vurderes", "avtalens 'gyldighet' vurderes"),
        # Typographic double quotes vs straight.
        ("såkalt \u201cfast eiendom\u201d her", 'såkalt "fast eiendom" her'),
        # En dash / em dash vs hyphen.
        ("perioden 2020\u20132024 gjelder", "perioden 2020-2024 gjelder"),
        ("ansvaret \u2014 uansett grunn", "ansvaret - uansett grunn"),
        # Soft hyphen inside a word disappears in copy-paste.
        ("eien\u00addomsretten overføres", "eiendomsretten overføres"),
    ],
)
def test_verify_quote_folds_typographic_punctuation(
    tmp_path: Path,
    source_text: str,
    quoted_text: str,
) -> None:
    """Curly quotes, en/em dashes and soft hyphens differ between the
    corpus text and what an AI client pastes back; an honest quote must
    not fail verification over typography. § stays distinct from $ and
    digits are untouched — only punctuation variants fold."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={
            "skatteloven": (f"## Kapittel 1.\n\n### § 1-1. Virkeområde\n\n{source_text}\n"),
        },
    )

    result = CorpusReader(tmp_path).verify_quote("skatteloven", "1-1", quoted_text)

    assert result["verified"] is True


def test_verify_quote_does_not_fold_section_sign_or_digits(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={
            "skatteloven": ("## Kapittel 1.\n\n### § 1-1. Virkeområde\n\nSe § 5-12 i loven.\n"),
        },
    )
    reader = CorpusReader(tmp_path)

    assert reader.verify_quote("skatteloven", "1-1", "Se $ 5-12 i loven.")["verified"] is False
    assert reader.verify_quote("skatteloven", "1-1", "Se § 512 i loven.")["verified"] is False
    assert reader.verify_quote("skatteloven", "1-1", "Se § 5\u20132 i loven.")["verified"] is False


def test_verify_quote_rejects_text_from_different_section(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={
            "skatteloven": (
                "## Kapittel 1.\n\n"
                "### § 1-1. Virkeområde\n\n"
                "Riktig tekst.\n\n"
                "### § 1-2. Annet\n\n"
                "Tekst fra annen paragraf.\n"
            ),
        },
    )

    result = CorpusReader(tmp_path).verify_quote(
        "skatteloven",
        "1-1",
        "Tekst fra annen paragraf.",
    )

    assert result["verified"] is False
    assert result["reason"] == (
        "quote not found in § 1-1 of 'skatteloven' after case, "
        "whitespace and typographic-punctuation normalization. The quote "
        "may be from a different section, paraphrased rather than "
        "verbatim, or hallucinated. "
        "Call get_section('skatteloven', '1-1') to read the actual text."
    )


def test_verify_quote_empty_quote_returns_false(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})

    result = CorpusReader(tmp_path).verify_quote("x", "1", "  ")

    assert result == {
        "verified": False,
        "slug": "x",
        "section_id": "1",
        "reason": "quote is empty",
    }


def test_verify_quote_unknown_section_returns_false_with_reason(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": "## Kapittel 1.\n\n### § 1-1. Virkeområde\n\nTekst.\n"},
    )

    result = CorpusReader(tmp_path).verify_quote("skatteloven", "9-9", "Tekst.")

    assert result["verified"] is False
    assert result["slug"] == "skatteloven"
    assert result["section_id"] == "9-9"
    assert "section '9-9' not found" in result["reason"]


def test_verify_quote_not_found_row_pins_the_full_response_contract(tmp_path: Path) -> None:
    # The "quote not found after normalization" return is verify_quote's core
    # anti-hallucination path, and its key set is an API contract: renaming
    # "slug" or "section_id" here breaks every consumer while the logic tests
    # (verified / reason) stay green — those key-rename mutants survived exactly
    # because no test asserted the whole row. Pin the exact key set.
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": "## Kapittel 1.\n\n### § 1-1. Virkeområde\n\nRiktig tekst.\n"},
    )

    result = CorpusReader(tmp_path).verify_quote("skatteloven", "1-1", "Hallusinert sitat.")

    assert set(result) == {"verified", "slug", "section_id", "reason"}
    assert result["verified"] is False
    assert result["slug"] == "skatteloven"
    assert result["section_id"] == "1-1"
    # Pin the anti-hallucination reason whole, not by substring (Theme 1).
    assert result["reason"] == (
        "quote not found in § 1-1 of 'skatteloven' after case, "
        "whitespace and typographic-punctuation normalization. The quote "
        "may be from a different section, paraphrased rather than "
        "verbatim, or hallucinated. "
        "Call get_section('skatteloven', '1-1') to read the actual text."
    )


# ---------- time-machine tools ----------


def test_get_law_at_passes_manifest_path_and_parsed_date_to_timetravel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    seen: dict[str, object] = {}

    def fake_get_law_at_revision(
        repo_path: Path,
        current_path: str,
        target_date: date,
    ) -> str:
        seen["args"] = (repo_path, current_path, target_date)
        return "historical markdown"

    monkeypatch.setattr("lovspor.mcp.get_law_at_revision", fake_get_law_at_revision)

    result = CorpusReader(tmp_path).get_law_at("skatteloven", "2026-04-27")

    assert result == "historical markdown"
    assert seen["args"] == (
        tmp_path,
        "lover/skatteloven.md",
        date(2026, 4, 27),
    )


def test_get_law_at_rejects_manifest_path_escaping_corpus_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A cloned manifest whose markdown_path escapes the corpus root must not
    reach git — get_law_at validates containment before the timetravel call
    (feeding '../..' raw into git pathspecs would crash or hit a stray file)."""
    evil = _record(slug="evil", title="Evil").model_copy(
        update={"markdown_path": "../../../../etc/passwd"},
    )
    _seed_corpus(tmp_path, {"nl-1": evil}, write_files=False)

    def fail_if_called(*_args: object) -> str:
        raise AssertionError("timetravel must not run for an escaping path")

    monkeypatch.setattr("lovspor.mcp.get_law_at_revision", fail_if_called)

    with pytest.raises(CorpusNotFoundError, match="escapes corpus root"):
        CorpusReader(tmp_path).get_law_at("evil", "2026-04-27")


def test_get_law_at_rejects_non_iso_date_before_manifest_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})

    def fail_if_called(*_args: object) -> str:
        raise AssertionError("timetravel lookup should not run")

    monkeypatch.setattr("lovspor.mcp.get_law_at_revision", fail_if_called)

    with pytest.raises(
        ValueError,
        match=r"^target_date must be ISO date YYYY-MM-DD, got '27-04-2026'$",
    ):
        CorpusReader(tmp_path).get_law_at("skatteloven", "27-04-2026")


def test_get_law_at_allows_todays_utc_date(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    today = datetime.now(UTC).date()

    def fake_get_law_at_revision(
        _repo_path: Path,
        _current_path: str,
        target_date: date,
    ) -> str:
        assert target_date == today
        return "today markdown"

    monkeypatch.setattr("lovspor.mcp.get_law_at_revision", fake_get_law_at_revision)

    assert CorpusReader(tmp_path).get_law_at("skatteloven", today.isoformat()) == ("today markdown")


def test_get_law_at_rejects_future_date_before_manifest_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})

    def fail_if_called(*_args: object) -> str:
        raise AssertionError("timetravel lookup should not run")

    monkeypatch.setattr("lovspor.mcp.get_law_at_revision", fail_if_called)

    today = datetime.now(UTC).date()
    with pytest.raises(
        ValueError,
        match=(
            r"^target_date 2999-01-01 is in the future "
            rf"\(today is {today.isoformat()}\); "
            r"use get_law for the current version$"
        ),
    ):
        CorpusReader(tmp_path).get_law_at("skatteloven", "2999-01-01")


def test_get_law_at_translates_missing_revision_to_corpus_not_found(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})

    def fake_get_law_at_revision(*_args: object) -> str:
        raise RevisionNotFoundError("too early")

    monkeypatch.setattr("lovspor.mcp.get_law_at_revision", fake_get_law_at_revision)

    with pytest.raises(
        CorpusNotFoundError,
        match=(
            r"^law 'skatteloven' did not exist in the corpus on 2020-01-01; "
            r"call get_law_history\('skatteloven'\) to see when it first appeared$"
        ),
    ):
        CorpusReader(tmp_path).get_law_at("skatteloven", "2020-01-01")


def test_list_law_versions_filters_content_events_and_sorts_oldest_first(
    tmp_path: Path,
) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        write_history_for=["skatteloven"],
    )
    history_path = tmp_path / "lover" / "history" / "skatteloven.json"
    history_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "slug": "skatteloven",
                "doc_id": "nl-1",
                "events": [
                    {
                        "date": "2026-04-29",
                        "commit": "upd2222",
                        "type": "updated",
                        "subject": "update(lov): skatteloven",
                        "lines_added": 5,
                        "lines_removed": 2,
                    },
                    {
                        "date": "2026-04-28",
                        "commit": "ren1111",
                        "type": "renamed",
                        "subject": "rename(lov): skatteloven",
                        "lines_added": 0,
                        "lines_removed": 0,
                    },
                    {
                        "date": "2026-04-27",
                        "commit": "add0000",
                        "type": "added",
                        "subject": "add(lov): skatteloven",
                    },
                    {
                        "date": "2026-04-30",
                        "commit": "rem3333",
                        "type": "removed",
                        "subject": "remove(lov): skatteloven",
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    versions = CorpusReader(tmp_path).list_law_versions("skatteloven")

    assert versions == [
        {
            "date": "2026-04-27",
            "commit": "add0000",
            "type": "added",
            "lines_added": None,
            "lines_removed": None,
        },
        {
            "date": "2026-04-29",
            "commit": "upd2222",
            "type": "updated",
            "lines_added": 5,
            "lines_removed": 2,
        },
    ]


def test_list_law_versions_coalesces_same_day_content_changes(
    tmp_path: Path,
) -> None:
    """Two content changes on one UTC date collapse to a single entry.

    get_law_at() resolves a date with end-of-day semantics, so it can
    only reach the latest commit on that day; the earlier same-day
    version is dropped from the listing rather than implying an
    addressability the date interface does not provide.
    """
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        write_history_for=["skatteloven"],
    )
    history_path = tmp_path / "lover" / "history" / "skatteloven.json"
    history_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "slug": "skatteloven",
                "doc_id": "nl-1",
                # get_law_history yields events newest-first, intra-day
                # included: the afternoon commit precedes the morning one.
                "events": [
                    {
                        "date": "2026-04-29",
                        "commit": "late999",
                        "type": "updated",
                        "subject": "update(lov): skatteloven (afternoon)",
                        "lines_added": 8,
                        "lines_removed": 1,
                    },
                    {
                        "date": "2026-04-29",
                        "commit": "early11",
                        "type": "updated",
                        "subject": "update(lov): skatteloven (morning)",
                        "lines_added": 3,
                        "lines_removed": 0,
                    },
                    {
                        "date": "2026-04-27",
                        "commit": "add0000",
                        "type": "added",
                        "subject": "add(lov): skatteloven",
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    versions = CorpusReader(tmp_path).list_law_versions("skatteloven")

    assert [v["date"] for v in versions] == ["2026-04-27", "2026-04-29"]
    same_day = next(v for v in versions if v["date"] == "2026-04-29")
    assert same_day["commit"] == "late999"
    assert same_day["lines_added"] == 8


# ---------- corpus_status ----------


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _git_init_corpus(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)


def _bump_mtime(path: Path) -> None:
    """Force a strictly-later mtime, regardless of filesystem granularity or
    how fast the test rewrote the file — a faithful stand-in for the mtime
    change a real ``git pull`` stamps on manifest.json."""
    later = path.stat().st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(later, later))


def test_corpus_status_stops_self_contradicting_after_pull(tmp_path: Path) -> None:
    """Regression: the reader cached the manifest for the server's lifetime
    but read git HEAD fresh each call. After a ``git pull`` landed a new sync
    commit, corpus_status reported the freshly-pulled HEAD next to a stale
    'N days old, run git pull' manifest age — a direct self-contradiction.
    All manifest-derived fields must now reflect the pulled corpus."""
    repo = tmp_path / "lovverk"
    _git_init_corpus(repo)
    _seed_corpus(
        repo,
        {"nl-1": _record(slug="x", title="X")},
        generated_at=datetime.now(UTC) - timedelta(days=30),
    )
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "sync: 1 new, 0 changed, 0 removed", cwd=repo)

    reader = CorpusReader(repo)
    before = reader.corpus_status()
    assert before["is_stale"] is True
    assert before["total_current_documents"] == 1

    # Simulate `git pull`: a fresh sync rewrites manifest.json (new
    # generated_at + an extra current doc) and lands a new commit.
    _seed_corpus(
        repo,
        {
            "nl-1": _record(slug="x", title="X"),
            "nl-2": _record(slug="y", title="Y"),
        },
        generated_at=datetime.now(UTC),
    )
    _bump_mtime(repo / "manifest.json")
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "sync: 1 new, 0 changed, 0 removed", cwd=repo)

    after = reader.corpus_status()
    assert after["total_current_documents"] == 2
    assert after["manifest_age_days"] == 0
    assert after["is_stale"] is False
    assert "current" in after["notice"]
    # Fresh HEAD and fresh manifest now agree: no more contradiction.
    assert after["head_commit"] != before["head_commit"]


def test_search_tools_reflect_corpus_pulled_after_construction(tmp_path: Path) -> None:
    """The slug and body indices are built once and pinned. A law added by a
    ``git pull`` after those indices were primed must still become findable
    — otherwise search_laws / search_body / get_law serve the pre-pull
    corpus for the rest of the session."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": "Skatt paa inntekt.\n"},
    )
    reader = CorpusReader(tmp_path)
    # Prime the slug + body indices against the pre-pull corpus.
    assert reader.search_laws("forvaltning") == []
    assert reader.search_body("forvaltning") == []
    assert reader.get_law("skatteloven")

    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="skatteloven", title="Skatteloven"),
            "nl-2": _record(slug="forvaltningsloven", title="Forvaltningsloven"),
        },
        body_for={
            "skatteloven": "Skatt paa inntekt.\n",
            "forvaltningsloven": "Regler for forvaltning.\n",
        },
    )
    _bump_mtime(tmp_path / "manifest.json")

    assert [hit["slug"] for hit in reader.search_laws("forvaltning")] == ["forvaltningsloven"]
    assert any(hit["slug"] == "forvaltningsloven" for hit in reader.search_body("forvaltning"))
    assert reader.get_law("forvaltningsloven")


def test_reader_does_not_reload_when_manifest_unchanged(tmp_path: Path) -> None:
    """The invalidation must be surgical: with manifest.json untouched, the
    cached manifest object is reused so the O(1) index caching still pays
    off. Guards against over-invalidation that would rebuild on every call."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    reader = CorpusReader(tmp_path)

    first = reader.manifest
    reader._refresh_if_stale()

    assert reader.manifest is first


def test_corpus_status_reports_fresh_when_manifest_is_recent(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X", last_changed="2026-04-27")},
    )
    status = CorpusReader(tmp_path).corpus_status()
    assert status["is_stale"] is False
    assert status["manifest_age_days"] == 0
    assert status["total_current_documents"] == 1
    assert "current" in status["notice"]
    assert status["refresh_command"].endswith(f"{tmp_path} pull")


def test_corpus_status_reports_stale_when_manifest_is_old(tmp_path: Path) -> None:
    """Manifest older than the 7-day threshold flips is_stale and the
    notice nudges the user toward git pull."""
    old_date = datetime.now(UTC) - timedelta(days=14)
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        generated_at=old_date,
    )
    status = CorpusReader(tmp_path).corpus_status()
    assert status["is_stale"] is True
    assert status["manifest_age_days"] >= 14
    assert "14 days old" in status["notice"]
    assert "git -C" in status["notice"]


def test_corpus_status_reports_seven_day_boundary_correctly(tmp_path: Path) -> None:
    """7-day-old manifest is exactly at the threshold (not stale);
    8-day-old is stale. Pinned by ``> _STALE_THRESHOLD_DAYS``."""
    fresh_boundary = datetime.now(UTC) - timedelta(days=7, hours=1)
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")}, generated_at=fresh_boundary)
    assert CorpusReader(tmp_path).corpus_status()["is_stale"] is False

    stale_boundary = datetime.now(UTC) - timedelta(days=8, hours=1)
    other = tmp_path.parent / f"{tmp_path.name}_b"
    _seed_corpus(other, {"nl-1": _record(slug="x", title="X")}, generated_at=stale_boundary)
    assert CorpusReader(other).corpus_status()["is_stale"] is True


def test_corpus_status_includes_git_head_info_when_corpus_is_a_git_repo(
    tmp_path: Path,
) -> None:
    """End-to-end with a real tiny git repo: corpus_status() must
    surface the HEAD commit's short SHA, ISO date, and subject so the
    AI can show 'last commit was X about Y'."""
    repo = tmp_path / "lovverk"
    _git_init_corpus(repo)
    _seed_corpus(repo, {"nl-1": _record(slug="x", title="X")})
    _git("add", "manifest.json", cwd=repo)
    _git("commit", "-m", "sync: 1 new, 0 changed, 0 removed", cwd=repo)

    status = CorpusReader(repo).corpus_status()
    assert status["head_commit"] is not None
    assert len(status["head_commit"]) == 7
    assert status["head_commit_subject"] == "sync: 1 new, 0 changed, 0 removed"
    assert status["head_commit_date"]
    assert len(status["head_commit_date"]) == 10  # YYYY-MM-DD


def test_corpus_status_handles_non_git_corpus_gracefully(tmp_path: Path) -> None:
    """Documented contract: a corpus directory that is NOT a git repo
    still returns a well-shaped status — git fields are None rather
    than the call raising."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    status = CorpusReader(tmp_path).corpus_status()
    assert status["head_commit"] is None
    assert status["head_commit_date"] is None
    assert status["head_commit_subject"] is None
    # Other fields still populated.
    assert status["total_current_documents"] == 1
    assert status["refresh_command"]


def test_corpus_status_row_pins_the_full_response_contract(tmp_path: Path) -> None:
    # Every field corpus_status returns is an API contract for AI consumers, but
    # the field-level tests only read a subset — so renaming an unread key (e.g.
    # manifest_generated_at) survived every test. Pin the exact key set; git
    # fields are present-and-None on a non-git corpus, which is all we need here.
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})

    status = CorpusReader(tmp_path).corpus_status()

    assert set(status) == {
        "manifest_generated_at",
        "manifest_age_days",
        "is_stale",
        "schema_compatible",
        "total_current_documents",
        "head_commit",
        "head_commit_date",
        "head_commit_subject",
        "refresh_command",
        "notice",
        "scope",
    }


def test_corpus_status_refresh_command_quotes_path_with_spaces(tmp_path: Path) -> None:
    """Codex PR-A regression. A corpus path containing spaces would
    otherwise produce 'git -C /tmp/lovverk test pull', which git parses
    as -C /tmp/lovverk with 'test' as a positional arg — silently
    targeting the wrong path. shlex.quote pins the contract: the
    refresh_command is shell-safe for any valid filesystem path."""
    spaced = tmp_path / "path with spaces"
    _seed_corpus(spaced, {"nl-1": _record(slug="x", title="X")})
    cmd = CorpusReader(spaced).corpus_status()["refresh_command"]
    # Authoritative test: shlex.split must round-trip the command back
    # to its intended argv. If the path were unquoted, parts[2] would
    # be just '/.../path' and 'with' / 'spaces' would become extra
    # positional args.
    parts = shlex.split(cmd)
    assert parts == ["git", "-C", str(spaced), "pull"]


def test_corpus_status_flags_pre_sprint_4_manifest_as_schema_stale(
    tmp_path: Path,
) -> None:
    """Manual-test regression. A manifest whose current docs have
    ``slug=None`` is on the pre-Sprint-4 schema. The MCP search/get
    tools all key off slug, so they silently return empty for every
    query — but the date-based is_stale signal would be False (the
    manifest itself was written recently by an older engine, e.g.
    when the user has an older checkout). Detect explicitly and
    surface a dedicated notice so the AI doesn't have to guess why
    everything returns empty."""
    legacy = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/nl-19990326-014.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime.now(UTC),
        status="current",
        # slug + title default to None — pre-Sprint-4 record shape
    )
    write_manifest(
        Manifest(generated_at=datetime.now(UTC), documents={"nl-1": legacy}),
        tmp_path / "manifest.json",
    )
    status = CorpusReader(tmp_path).corpus_status()
    assert status["schema_compatible"] is False
    assert status["is_stale"] is True
    # Pinned whole: this notice is the only signal that every MCP tool is
    # silently returning empty, so its remediation wording is load-bearing.
    assert status["notice"] == (
        "Corpus manifest is on the pre-Sprint-4 schema "
        "(1 of 1 current documents have no slug field). MCP search/get tools "
        "cannot operate on this schema. "
        f"Run: {status['refresh_command']} to refresh."
    )


def test_corpus_status_reports_schema_compatible_for_modern_manifest(
    tmp_path: Path,
) -> None:
    """Positive control: every record produced by the modern engine
    carries a slug, so schema_compatible is True."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    status = CorpusReader(tmp_path).corpus_status()
    assert status["schema_compatible"] is True


def test_corpus_status_schema_stale_wins_over_clock_skew_in_notice(
    tmp_path: Path,
) -> None:
    """Codex regression. When the manifest is BOTH future-dated
    (clock skew) AND on the pre-Sprint-4 schema, the previous notice
    chain put clock-skew first and ended with 'Treating as fresh',
    contradicting is_stale=True. Schema-staleness is the only
    actionable signal here (the MCP tools cannot work on the schema
    regardless of clock state) so it wins the notice slot."""
    legacy = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/nl-19990326-014.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime.now(UTC),
        status="current",
    )
    write_manifest(
        Manifest(
            generated_at=datetime.now(UTC) + timedelta(days=2),
            documents={"nl-1": legacy},
        ),
        tmp_path / "manifest.json",
    )
    status = CorpusReader(tmp_path).corpus_status()
    assert status["is_stale"] is True
    assert status["schema_compatible"] is False
    assert "pre-Sprint-4" in status["notice"]
    assert "Treating as fresh" not in status["notice"]


def test_corpus_status_mixed_slug_population_is_schema_stale(tmp_path: Path) -> None:
    """A manifest where SOME current records have slugs and others
    don't is still schema-incompatible — search/get on the slug-less
    ones would fail. Any non-zero count of current records without
    slug flips schema_compatible to false."""
    modern = _record(slug="modern", title="M")
    legacy = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/nl-legacy.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime.now(UTC),
        status="current",
    )
    write_manifest(
        Manifest(
            generated_at=datetime.now(UTC),
            documents={"nl-1": modern, "nl-2": legacy},
        ),
        tmp_path / "manifest.json",
    )
    status = CorpusReader(tmp_path).corpus_status()
    assert status["schema_compatible"] is False
    assert "1 of 2" in status["notice"]


def test_corpus_status_tombstones_only_is_schema_compatible(tmp_path: Path) -> None:
    """A manifest containing ONLY tombstones (no current docs) is
    vacuously schema_compatible — there are no current records to
    fail the slug check."""
    tombstone = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/nl-gone.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime.now(UTC),
        status="removed",
    )
    write_manifest(
        Manifest(
            generated_at=datetime.now(UTC),
            documents={"nl-1": tombstone},
        ),
        tmp_path / "manifest.json",
    )
    status = CorpusReader(tmp_path).corpus_status()
    assert status["schema_compatible"] is True
    assert status["total_current_documents"] == 0


def test_corpus_status_clamps_negative_age_for_future_dated_manifest(
    tmp_path: Path,
) -> None:
    """Codex PR-A regression. Clock skew on the user's machine (or a
    forged/manually-edited manifest) can produce a generated_at in the
    future. The previous output read 'Corpus is current (-1 days old)'
    which looks like a tooling bug. Now: clamp manifest_age_days to 0
    and surface a dedicated clock-skew notice so the AI can quote it
    verbatim."""
    future = datetime.now(UTC) + timedelta(days=2)
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="x", title="X")},
        generated_at=future,
    )
    status = CorpusReader(tmp_path).corpus_status()
    assert status["manifest_age_days"] == 0
    assert status["is_stale"] is False
    # Pinned whole: "Treating as fresh" is the part that stops the AI reading a
    # future-dated manifest as a tooling bug — it must survive verbatim.
    assert status["notice"] == (
        f"Corpus manifest is dated in the future ({status['manifest_generated_at']}); "
        "likely a clock-skew issue on your machine. Treating as fresh."
    )


def test_corpus_status_excludes_tombstones_from_total(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="alive", title="A"),
            "nl-2": _record(slug="gone", title="G", status="removed"),
        },
        write_files=False,
    )
    status = CorpusReader(tmp_path).corpus_status()
    assert status["total_current_documents"] == 1


# ---------- build_server ----------


def test_serve_loads_dotenv_before_building_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The `lovspor mcp` command does not build Settings, so serve() must
    load .env itself before build_server reads os.environ — otherwise an
    OPENAI_API_KEY set only in .env is missed and semantic_search is
    silently disabled."""
    calls: list[str] = []
    monkeypatch.setattr(mcp_module, "load_env", lambda: calls.append("load_env"))

    class _FakeServer:
        def run(self) -> None:
            calls.append("run")

    monkeypatch.setattr(mcp_module, "build_server", lambda _path: _FakeServer())

    mcp_module.serve(tmp_path)

    assert calls == ["load_env", "run"]


def test_build_server_registers_sixteen_tools(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    server = build_server(tmp_path)
    # FastMCP exposes registered tools via list_tools(); the wrapper is
    # async, so we verify by checking the internal registration count
    # via the underlying tool manager.
    tool_names = sorted(server._tool_manager._tools.keys())
    assert tool_names == sorted(
        [
            "get_law",
            "get_law_at",
            "list_law_versions",
            "diff_law_versions",
            "get_section",
            "list_sections",
            "get_law_history",
            "list_recent_changes",
            "search_laws",
            "search_body",
            "semantic_search",
            "validate_citation",
            "verify_quote",
            "get_eu_basis",
            "search_eu_implementations",
            "corpus_status",
        ],
    )


def test_build_server_raises_eagerly_on_bad_corpus(tmp_path: Path) -> None:
    """Misconfigured corpus path fails at server startup, not first call."""
    with pytest.raises(CorpusNotFoundError):
        build_server(tmp_path / "nonexistent")


def _bump_manifest_mtime(root: Path) -> None:
    """Force a mtime move so _refresh_if_stale fires regardless of the
    filesystem's timestamp granularity."""
    manifest = root / "manifest.json"
    bumped = manifest.stat().st_mtime_ns + 1_000_000_000
    os.utime(manifest, ns=(bumped, bumped))


def test_doc_body_read_before_a_refresh_is_not_cached_after_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body read that began before a corpus refresh must not land in the
    post-refresh cache. The write is atomic, but the DATA describes the old
    corpus — caching it serves the superseded legal text until the next
    refresh, which is worse than re-reading. (Codex, PR #139.)"""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": "OLD"},
    )
    reader = CorpusReader(tmp_path)
    record, epoch = reader._resolve_current("skatteloven")
    reading = threading.Event()
    may_finish = threading.Event()
    real_read = reader._read_stripped_body

    def blocking_read(rec: ManifestRecord) -> str:
        body = real_read(rec)
        reading.set()
        may_finish.wait(timeout=5)
        return body

    monkeypatch.setattr(reader, "_read_stripped_body", blocking_read)

    with ThreadPoolExecutor(max_workers=1) as pool:
        in_flight = pool.submit(reader._body_for_record, record, epoch)
        assert reading.wait(timeout=5)
        # The corpus changes underneath the in-flight read.
        _seed_corpus(
            tmp_path,
            {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
            body_for={"skatteloven": "NEW"},
        )
        _bump_manifest_mtime(tmp_path)
        reader._refresh_if_stale()
        may_finish.set()
        in_flight.result()

    assert reader._doc_bodies.get("skatteloven") != "OLD"
    assert reader._body_for_record(*reader._resolve_current("skatteloven")) == "NEW"


def test_record_resolved_before_a_refresh_cannot_poison_the_new_epoch(
    tmp_path: Path,
) -> None:
    """A record and its epoch must be resolved atomically. Pairing a
    pre-refresh record with a post-refresh epoch would let the old record's
    markdown_path pass the write-back guard and poison the fresh cache — the
    slug can point at a different file after a refresh. (Codex, PR #139 rd 2.)"""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": "old-body"},
    )
    reader = CorpusReader(tmp_path)
    old_record, old_epoch = reader._resolve_current("skatteloven")

    # The slug now resolves to a different file with different content.
    old_path = tmp_path / "lover" / "skatteloven.md"
    new_record = _record(slug="skatteloven", title="Skatteloven")
    new_record = new_record.model_copy(update={"markdown_path": "lover/skatteloven-ny.md"})
    write_manifest(
        Manifest(generated_at=datetime.now(UTC), documents={"nl-1": new_record}),
        tmp_path / "manifest.json",
    )
    (tmp_path / "lover" / "skatteloven-ny.md").write_text(
        "---\nid: x\ntitle: Skatteloven\n---\n\nnew-body",
        encoding="utf-8",
    )
    old_path.write_text("---\nid: x\ntitle: Skatteloven\n---\n\nold-body", encoding="utf-8")
    _bump_manifest_mtime(tmp_path)
    reader._refresh_if_stale()

    # A caller still holding the pre-refresh record must not cache its body.
    assert reader._body_for_record(old_record, old_epoch) == "old-body"
    assert "skatteloven" not in reader._doc_bodies

    # And resolution after the refresh pairs the NEW record with the NEW epoch.
    fresh_record, fresh_epoch = reader._resolve_current("skatteloven")
    assert fresh_record.markdown_path == "lover/skatteloven-ny.md"
    assert fresh_epoch == old_epoch + 1
    assert reader._body_for_record(fresh_record, fresh_epoch) == "new-body"


def test_section_ids_parsed_before_a_refresh_are_not_cached_after_it(
    tmp_path: Path,
) -> None:
    """Same staleness guard as the body cache, for the cross-reference
    section-id cache: an epoch captured before a refresh must not write back."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": "### § 1-1. Old\nbody"},
    )
    reader = CorpusReader(tmp_path)
    _, stale_epoch = reader._resolve_current("skatteloven")

    _bump_manifest_mtime(tmp_path)
    reader._refresh_if_stale()

    reader._remember_section_ids("skatteloven", {"9-9"}, stale_epoch)

    assert "skatteloven" not in reader._section_ids_cache


def test_refresh_bumps_the_cache_epoch(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    reader = CorpusReader(tmp_path)
    _, first = reader._resolve_current("skatteloven")

    _bump_manifest_mtime(tmp_path)
    reader._refresh_if_stale()

    assert reader._resolve_current("skatteloven")[1] == first + 1
    # An unchanged manifest must not churn the epoch, or every call would
    # discard its own cache write.
    reader._refresh_if_stale()
    assert reader._resolve_current("skatteloven")[1] == first + 1


# ---------- Sprint 12: Streamable HTTP transport ----------


def test_offload_to_thread_preserves_signature_and_leaves_the_caller_thread() -> None:
    """FastMCP derives a tool's schema from the signature and decides
    await-vs-inline from the callable itself, so the wrapper has to look like
    the original (via ``__wrapped__``) while being a coroutine function."""

    def probe(slug: str, limit: int = 5) -> str:
        """Probe docstring."""
        return f"{slug}|{limit}|{threading.current_thread().name}"

    wrapped = _offload_to_thread(probe)

    assert inspect.iscoroutinefunction(wrapped)
    assert inspect.signature(wrapped) == inspect.signature(probe)
    assert wrapped.__doc__ == "Probe docstring."

    result = asyncio.run(wrapped(slug="x", limit=2))

    assert result.startswith("x|2|")
    assert not result.endswith(threading.current_thread().name)


def test_http_mode_registers_offloaded_async_tools_with_intact_schemas(
    tmp_path: Path,
) -> None:
    """Async registration is what keeps a blocking body off the event loop;
    the schema must survive the wrapping or every tool call breaks."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})

    server = build_server(tmp_path, http=HttpConfig(host="127.0.0.1", port=9999))

    tools = server._tool_manager._tools
    assert len(tools) == 16
    assert all(tool.is_async for tool in tools.values())
    get_law = tools["get_law"]
    assert get_law.parameters["required"] == ["slug"]
    assert get_law.parameters["properties"]["slug"]["type"] == "string"


def test_stdio_mode_registers_inline_sync_tools(tmp_path: Path) -> None:
    """stdio serves one client, one tool at a time — the thread hop would buy
    nothing, so it must not be applied there."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})

    server = build_server(tmp_path)

    tools = server._tool_manager._tools
    assert len(tools) == 16
    assert not any(tool.is_async for tool in tools.values())


def test_http_mode_warms_caches_while_stdio_stays_lazy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cold index build holds the cache lock for its whole duration and
    stalls every concurrent request; the hosted server pays it at startup
    instead. stdio keeps the lazy path so a metadata-only client starts fast."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    warmed: list[int] = []
    monkeypatch.setattr(CorpusReader, "warm", lambda _self: warmed.append(1))

    build_server(tmp_path)
    assert warmed == []

    build_server(tmp_path, http=HttpConfig())
    assert warmed == [1]


def test_warm_builds_indices_and_skips_embeddings_without_an_embedder(
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    reader = CorpusReader(tmp_path)

    reader.warm()

    assert reader._slug_index is not None
    assert reader._body_index is not None
    # semantic_search is disabled without an embedder, so the ~200 MB index
    # would be dead weight.
    assert reader._embedding_index is None


def test_warm_builds_the_embedding_index_when_an_embedder_is_configured(
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    _write_embedding_file(tmp_path, "lover", "skatteloven", [("1", [1, 0])])
    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder([1.0, 0.0]))

    reader.warm()

    assert reader._embedding_index is not None


def test_health_routes_report_process_and_corpus_readiness(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    server = build_server(tmp_path, http=HttpConfig())
    _add_health_routes(server, tmp_path)

    client = TestClient(server.streamable_http_app())

    healthz = client.get("/healthz")
    assert healthz.status_code == 200
    assert healthz.json() == {"status": "ok"}

    readyz = client.get("/readyz")
    assert readyz.status_code == 200
    assert readyz.json() == {"status": "ready"}


def test_readyz_reports_unavailable_when_the_corpus_manifest_disappears(
    tmp_path: Path,
) -> None:
    """A probe must fail the instance out when the corpus vanishes underneath
    it rather than letting it serve on."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    server = build_server(tmp_path, http=HttpConfig())
    _add_health_routes(server, tmp_path)
    (tmp_path / "manifest.json").unlink()

    response = TestClient(server.streamable_http_app()).get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_serve_http_loads_dotenv_then_serves_over_streamable_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same .env ordering contract as serve(), plus the bind address has to
    reach build_server so FastMCP configures its transport security for it."""
    calls: list[str] = []
    captured: dict[str, object] = {}
    monkeypatch.setattr(mcp_module, "load_env", lambda: calls.append("load_env"))

    class _FakeServer:
        def run(self, transport: str) -> None:
            calls.append(f"run:{transport}")

    def fake_build(path: Path, *, http: HttpConfig | None = None) -> _FakeServer:
        captured["path"] = path
        captured["http"] = http
        calls.append("build")
        return _FakeServer()

    monkeypatch.setattr(mcp_module, "build_server", fake_build)
    monkeypatch.setattr(mcp_module, "_add_health_routes", lambda *_: calls.append("health"))

    config = HttpConfig(host="127.0.0.1", port=9001, credentials_path=tmp_path / "creds.json")
    mcp_module.serve_http(tmp_path, config)

    assert calls == ["load_env", "build", "health", "run:streamable-http"]
    assert captured["path"] == tmp_path
    assert captured["http"] == config


def test_serve_http_refuses_to_start_without_authentication(tmp_path: Path) -> None:
    """An unauthenticated server hands the whole tool surface to anyone who can
    reach the port. That must not be one forgotten flag away."""
    with pytest.raises(ConfigError, match="refusing to serve HTTP without authentication"):
        mcp_module.serve_http(tmp_path, HttpConfig())


def test_serve_http_allows_no_auth_only_when_explicitly_asked_and_says_so(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(mcp_module, "load_env", lambda: None)

    class _FakeServer:
        def run(self, transport: str) -> None:
            pass

    monkeypatch.setattr(mcp_module, "build_server", lambda *_a, **_k: _FakeServer())
    monkeypatch.setattr(mcp_module, "_add_health_routes", lambda *_: None)

    mcp_module.serve_http(tmp_path, HttpConfig(allow_insecure=True))

    assert "SERVING WITHOUT AUTHENTICATION" in capsys.readouterr().err


def test_http_mode_without_credentials_registers_no_auth(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})

    server = build_server(tmp_path, http=HttpConfig(allow_insecure=True))

    assert server.settings.auth is None


def test_http_mode_with_credentials_installs_the_store_as_token_verifier(
    tmp_path: Path,
) -> None:
    """A bare token_verifier is a resource-server-only config: no authorization
    server, no /token, no /authorize, no discovery routes get mounted."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    creds = tmp_path / "credentials.json"
    write_credential_file(creds, [])

    server = build_server(tmp_path, http=HttpConfig(credentials_path=creds))

    assert isinstance(server._token_verifier, CredentialStore)
    assert server.settings.auth is not None
    # None keeps /.well-known/oauth-protected-resource off the app entirely.
    assert server.settings.auth.resource_server_url is None
    paths = {route.path for route in server.streamable_http_app().routes}
    assert not any(p.startswith("/.well-known") or p in {"/token", "/authorize"} for p in paths)


@pytest.mark.parametrize(
    "partial",
    [
        {"authkit_domain": _AUTHKIT_DOMAIN},
        {"public_url": _PUBLIC_URL},
    ],
)
def test_half_configured_hosted_oauth_is_rejected(partial: dict[str, str]) -> None:
    """Half a pair used to boot into the legacy opaque-token mode with discovery
    off and WorkOS JWTs never verified — a broken auth boundary that looks healthy."""
    with pytest.raises(ConfigError, match="together"):
        HttpConfig(credentials_path=Path("creds.json"), **partial)


def _half_pair_by_assignment() -> HttpConfig:
    config = HttpConfig()
    config.authkit_domain = _AUTHKIT_DOMAIN  # plain assignment runs no validator
    return config


@pytest.mark.parametrize(
    "make_bypassed",
    [
        pytest.param(
            lambda: HttpConfig().model_copy(update={"authkit_domain": _AUTHKIT_DOMAIN}),
            id="model_copy",
        ),
        pytest.param(
            lambda: HttpConfig.model_construct(authkit_domain=_AUTHKIT_DOMAIN),
            id="model_construct",
        ),
        pytest.param(_half_pair_by_assignment, id="assignment"),
    ],
)
def test_half_configured_oauth_is_refused_through_pydantic_escape_hatches(
    make_bypassed: Callable[[], HttpConfig],
    tmp_path: Path,
) -> None:
    """``model_construct``, ``model_copy(update=...)`` and attribute assignment all
    skip validators by design, so the constructor guard alone is not the invariant.
    Re-checking where the mode is decided is what stops a half pair reaching the auth
    wiring and silently selecting opaque-token mode."""
    bypassed = make_bypassed()
    creds = tmp_path / "credentials.json"
    write_credential_file(creds, [])
    store = CredentialStore(creds)

    with pytest.raises(ConfigError, match="together"):
        bypassed.oauth_pair()
    with pytest.raises(ConfigError, match="together"):
        mcp_module._auth_kwargs(bypassed, store)
    with pytest.raises(ConfigError, match="together"):
        mcp_module._build_verifier(bypassed, store)


@pytest.mark.parametrize(
    "wire",
    [
        pytest.param(lambda config: mcp_module._auth_kwargs(config, None), id="auth_kwargs"),
        pytest.param(lambda config: mcp_module._build_verifier(config, None), id="build_verifier"),
    ],
)
def test_half_configured_oauth_is_refused_with_authentication_disabled(
    wire: Callable[[HttpConfig], object],
) -> None:
    """``--insecure-no-auth`` means "no auth at all", not "skip the config checks".

    Both wiring functions return early when there is no verifier, so a half pair
    that reached them through an escape hatch used to slip past unexamined. The
    pair is checked before that early return, so a broken config reads the same
    whether or not authentication happens to be switched on."""
    bypassed = HttpConfig.model_construct(authkit_domain=_AUTHKIT_DOMAIN)

    with pytest.raises(ConfigError, match="together"):
        wire(bypassed)


def test_hosted_oauth_installs_workos_verification_and_turns_discovery_on(
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    creds = tmp_path / "credentials.json"
    write_credential_file(creds, [])

    server = build_server(
        tmp_path,
        http=HttpConfig(
            credentials_path=creds,
            authkit_domain=_AUTHKIT_DOMAIN,
            public_url=_PUBLIC_URL,
        ),
    )

    assert isinstance(server._token_verifier, CompositeVerifier)
    assert server.settings.auth is not None
    # WorkOS is advertised as the authorization server, lovspor as the RFC 8707
    # resource — that pair is what makes a ChatGPT/Claude connector able to log in.
    assert str(server.settings.auth.issuer_url).rstrip("/") == _AUTHKIT_DOMAIN
    assert str(server.settings.auth.resource_server_url).rstrip("/") == _PUBLIC_URL
    paths = {route.path for route in server.streamable_http_app().routes}
    assert "/.well-known/oauth-protected-resource/mcp" in paths


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer nope"},
    ],
)
def test_http_mode_rejects_missing_and_malformed_bearers_before_reaching_mcp(
    tmp_path: Path,
    headers: dict[str, str],
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    creds = tmp_path / "credentials.json"
    write_credential_file(creds, [])
    server = build_server(tmp_path, http=HttpConfig(credentials_path=creds))

    response = TestClient(
        server.streamable_http_app(),
        raise_server_exceptions=False,
    ).post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
        headers=headers,
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer ")
    assert response.json() == {
        "error": "invalid_token",
        "error_description": "Authentication required",
    }


def test_http_mode_rejects_an_expired_bearer_before_reaching_mcp(
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    creds = tmp_path / "credentials.json"
    token = "lsp_expired_test_token"
    write_credential_file(
        creds,
        [
            Credential(
                credential_id="beta-001",
                label="expired tester",
                token_sha256=hash_token(token),
                expires_at=datetime.now(UTC) - timedelta(days=1),
            ),
        ],
    )
    server = build_server(tmp_path, http=HttpConfig(credentials_path=creds))

    response = TestClient(
        server.streamable_http_app(),
        raise_server_exceptions=False,
    ).post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer ")
    assert response.json() == {
        "error": "invalid_token",
        "error_description": "Authentication required",
    }


# ---------------------------------------------------------------------------
# Sprint 8 PR-D: EU / EEA cross-reference tools
# ---------------------------------------------------------------------------


def test_get_eu_basis_returns_celex_list_for_known_slug(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-pol": _record(
                slug="personopplysningsloven",
                title="Personopplysningsloven",
                eu_basis=["32016R0679"],
            ),
        },
    )
    result = CorpusReader(tmp_path).get_eu_basis("personopplysningsloven")
    assert result == {
        "slug": "personopplysningsloven",
        "doc_id": "nl-pol",
        "title": "Personopplysningsloven",
        "dataset": "lover",
        "eu_basis": ["32016R0679"],
    }


def test_get_eu_basis_returns_empty_list_for_act_without_eu_links(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="straffeloven", title="Straffeloven", eu_basis=[])},
    )
    result = CorpusReader(tmp_path).get_eu_basis("straffeloven")
    assert result["eu_basis"] == []


def test_get_eu_basis_unknown_slug_raises(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="known", title="Known", eu_basis=[])},
    )
    with pytest.raises(CorpusNotFoundError) as excinfo:
        CorpusReader(tmp_path).get_eu_basis("ghost")
    assert str(excinfo.value) == (
        "no current law with slug 'ghost'; use search_laws or list_recent_changes to discover slugs"
    )


def test_get_eu_basis_pre_sprint8_record_raises_corpus_stale(tmp_path: Path) -> None:
    """A manifest record with eu_basis=None signals the corpus predates
    Sprint 8 PR-D — return a remediation message instead of returning
    an empty list (which would be a silent lie)."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="legacy", title="Legacy", eu_basis=None)},
    )
    with pytest.raises(CorpusNotFoundError) as excinfo:
        CorpusReader(tmp_path).get_eu_basis("legacy")
    # Pinned whole: this is a "do not treat empty as absence" remediation,
    # the same class of message as the other anti-hallucination notices.
    assert str(excinfo.value) == (
        "eu_basis is unknown for 'legacy'; corpus predates Sprint 8 PR-D. "
        "Run 'git pull' in the corpus to refresh."
    )


def test_get_eu_basis_skips_tombstone(tmp_path: Path) -> None:
    """A removed record is not 'current' — get_eu_basis should treat
    the slug as unknown (same as other lookup tools)."""
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(
                slug="oldlaw",
                title="Oldlaw",
                eu_basis=["32016R0679"],
                status="removed",
            ),
        },
        write_files=False,
    )
    with pytest.raises(CorpusNotFoundError, match="no current law with slug 'oldlaw'"):
        CorpusReader(tmp_path).get_eu_basis("oldlaw")


def test_search_eu_implementations_finds_matching_acts(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-pol": _record(
                slug="personopplysningsloven",
                title="Personopplysningsloven",
                eu_basis=["32016R0679"],
            ),
            "nl-other": _record(
                slug="ekomloven",
                title="Ekomloven",
                eu_basis=["32016R0679", "32018L1972"],
            ),
            "nl-unrelated": _record(
                slug="straffeloven",
                title="Straffeloven",
                eu_basis=[],
            ),
        },
    )
    result = CorpusReader(tmp_path).search_eu_implementations("32016R0679")
    assert [hit["slug"] for hit in result] == ["ekomloven", "personopplysningsloven"]
    assert result[0] == {
        "slug": "ekomloven",
        "doc_id": "nl-other",
        "title": "Ekomloven",
        "dataset": "lover",
    }


def test_search_eu_implementations_is_case_insensitive(tmp_path: Path) -> None:
    """Lovdata stores CELEX lowercase; lovspor normalizes to uppercase
    at extraction time. The MCP tool accepts either form from callers."""
    _seed_corpus(
        tmp_path,
        {
            "nl-pol": _record(
                slug="personopplysningsloven",
                title="Personopplysningsloven",
                eu_basis=["32016R0679"],
            ),
        },
    )
    reader = CorpusReader(tmp_path)
    assert len(reader.search_eu_implementations("32016r0679")) == 1
    assert len(reader.search_eu_implementations("32016R0679")) == 1
    assert len(reader.search_eu_implementations("  32016R0679  ")) == 1


def test_search_eu_implementations_empty_query_returns_empty_list(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="x", title="X", eu_basis=["32016R0679"]),
        },
    )
    assert CorpusReader(tmp_path).search_eu_implementations("") == []
    assert CorpusReader(tmp_path).search_eu_implementations("   ") == []


def test_search_eu_implementations_no_matches_returns_empty_list(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {
            "nl-1": _record(slug="x", title="X", eu_basis=["32016R0679"]),
        },
    )
    assert CorpusReader(tmp_path).search_eu_implementations("32099Z9999") == []


def test_search_eu_implementations_skips_tombstones(tmp_path: Path) -> None:
    """A removed Norwegian act no longer 'implements' anything, so it
    must not appear in current EU-implementation results."""
    _seed_corpus(
        tmp_path,
        {
            "nl-current": _record(
                slug="alive",
                title="Alive",
                eu_basis=["32016R0679"],
            ),
            "nl-old": _record(
                slug="dead",
                title="Dead",
                eu_basis=["32016R0679"],
                status="removed",
            ),
        },
        write_files=False,
    )
    result = CorpusReader(tmp_path).search_eu_implementations("32016R0679")
    assert [hit["slug"] for hit in result] == ["alive"]


def test_search_eu_implementations_skips_pre_sprint8_records(tmp_path: Path) -> None:
    """Records with eu_basis=None (legacy schema) are silently skipped
    rather than raising. The migration will populate them on the
    next sync; in the meantime the reverse-lookup answer is partial
    but not wrong (we return the records we have authoritative
    answers for)."""
    _seed_corpus(
        tmp_path,
        {
            "nl-known": _record(
                slug="known",
                title="Known",
                eu_basis=["32016R0679"],
            ),
            "nl-legacy": _record(
                slug="legacy",
                title="Legacy",
                eu_basis=None,
            ),
        },
    )
    result = CorpusReader(tmp_path).search_eu_implementations("32016R0679")
    assert [hit["slug"] for hit in result] == ["known"]


# --- _diff_section_maps: pure section-map diff core (B2 diff tool) ---


def _sec(heading: str, body: str, chapter: str = "") -> dict[str, str]:
    """Build one parsed-section entry in the shape _parse_sections emits."""
    return {"heading": heading, "parent_chapter": chapter, "body": body}


def _secs(mapping: dict[str, dict[str, str]]) -> list[ParsedSection]:
    """Adapt the ``{id: section}`` maps these tests are written in to the ordered
    list _parse_sections now returns. Every id here is unique by construction, so
    occurrence is always 1; duplicate-id diffing has its own test below."""
    return [
        ParsedSection(
            section_id=sid,
            occurrence=1,
            heading=data["heading"],
            parent_chapter=data["parent_chapter"],
            body=data["body"],
        )
        for sid, data in mapping.items()
    ]


def test_diff_section_maps_identical_maps_is_empty() -> None:
    sections = {"1": _sec("§ 1. Formål", "Loven gjelder skatt.")}
    result = _diff_section_maps(_secs(sections), _secs(sections))
    assert result["summary"] == {
        "sections_added": 0,
        "sections_removed": 0,
        "sections_changed": 0,
    }
    assert result["sections"] == []


def test_diff_section_maps_added_section() -> None:
    before: dict[str, dict[str, str]] = {}
    after = {"2": _sec("§ 2. Virkeområde", "Ny paragraf.")}
    result = _diff_section_maps(_secs(before), _secs(after))
    assert result["summary"]["sections_added"] == 1
    [entry] = result["sections"]
    assert entry["section_id"] == "2"
    assert entry["change_type"] == "added"
    assert entry["heading"] == "§ 2. Virkeområde"
    assert "+Ny paragraf." in entry["unified_diff"]


def test_diff_section_maps_removed_section() -> None:
    before = {"3": _sec("§ 3. Opphevet", "Gammel tekst.")}
    after: dict[str, dict[str, str]] = {}
    result = _diff_section_maps(_secs(before), _secs(after))
    assert result["summary"]["sections_removed"] == 1
    [entry] = result["sections"]
    assert entry["change_type"] == "removed"
    assert "-Gammel tekst." in entry["unified_diff"]


def test_diff_section_maps_changed_body() -> None:
    before = {"1": _sec("§ 1. Formål", "Gammel setning.")}
    after = {"1": _sec("§ 1. Formål", "Ny setning.")}
    result = _diff_section_maps(_secs(before), _secs(after))
    assert result["summary"]["sections_changed"] == 1
    [entry] = result["sections"]
    assert entry["change_type"] == "changed"
    assert "-Gammel setning." in entry["unified_diff"]
    assert "+Ny setning." in entry["unified_diff"]


def test_diff_section_maps_retitle_with_identical_body_is_a_change() -> None:
    """A section keeps its id and body but the title changes — a real legal
    edit that a body-only diff would miss. The heading line is part of the
    diffed text so the retitle surfaces."""
    before = {"1": _sec("§ 1. Gammelt navn", "Samme tekst.")}
    after = {"1": _sec("§ 1. Nytt navn", "Samme tekst.")}
    result = _diff_section_maps(_secs(before), _secs(after))
    assert result["summary"]["sections_changed"] == 1
    diff = result["sections"][0]["unified_diff"]
    assert "-§ 1. Gammelt navn" in diff
    assert "+§ 1. Nytt navn" in diff


def test_diff_section_maps_emits_sections_in_natural_order() -> None:
    before: dict[str, dict[str, str]] = {}
    after = {
        "5-10": _sec("§ 5-10. Ti", "x"),
        "1": _sec("§ 1. En", "x"),
        "5-2": _sec("§ 5-2. To", "x"),
    }
    result = _diff_section_maps(_secs(before), _secs(after))
    assert [e["section_id"] for e in result["sections"]] == ["1", "5-2", "5-10"]


def test_diff_section_maps_mixed_add_remove_change() -> None:
    before = {
        "1": _sec("§ 1. Formål", "Uendret."),
        "2": _sec("§ 2. Fjernes", "Borte snart."),
        "3": _sec("§ 3. Endres", "Før."),
    }
    after = {
        "1": _sec("§ 1. Formål", "Uendret."),
        "3": _sec("§ 3. Endres", "Etter."),
        "4": _sec("§ 4. Ny", "Lagt til."),
    }
    result = _diff_section_maps(_secs(before), _secs(after))
    assert result["summary"] == {
        "sections_added": 1,
        "sections_removed": 1,
        "sections_changed": 1,
    }
    by_id = {e["section_id"]: e["change_type"] for e in result["sections"]}
    assert by_id == {"2": "removed", "3": "changed", "4": "added"}


def test_diff_section_maps_is_deterministic() -> None:
    before = {"1": _sec("§ 1. A", "gammel")}
    after = {"1": _sec("§ 1. A", "ny"), "2": _sec("§ 2. B", "ny b")}
    once = _diff_section_maps(_secs(before), _secs(after))
    twice = _diff_section_maps(_secs(before), _secs(after))
    assert once == twice


def test_diff_section_maps_uses_natural_order_and_omits_unchanged() -> None:
    before = {
        "1": _sec("§ 1. Uendret", "samme"),
        "5-10": _sec("§ 5-10. Ti", "gammel ti"),
        "5-2": _sec("§ 5-2. To", "gammel to"),
    }
    after = {
        "1": _sec("§ 1. Uendret", "samme"),
        "5-2": _sec("§ 5-2. To", "ny to"),
        "5-10": _sec("§ 5-10. Ti", "ny ti"),
    }

    result = _diff_section_maps(_secs(before), _secs(after))

    assert [entry["section_id"] for entry in result["sections"]] == ["5-2", "5-10"]
    assert all(entry["section_id"] != "1" for entry in result["sections"])
    assert json.dumps(result, sort_keys=True, ensure_ascii=False) == json.dumps(
        _diff_section_maps(_secs(before), _secs(after)),
        sort_keys=True,
        ensure_ascii=False,
    )


def test_diff_section_maps_marks_retitle_as_changed() -> None:
    before = {"1-1": _sec("§ 1-1. Gammelt navn", "Uendret brødtekst.")}
    after = {"1-1": _sec("§ 1-1. Nytt navn", "Uendret brødtekst.")}

    result = _diff_section_maps(_secs(before), _secs(after))

    assert result["summary"] == {
        "sections_added": 0,
        "sections_removed": 0,
        "sections_changed": 1,
    }
    assert result["sections"][0]["change_type"] == "changed"
    assert "-§ 1-1. Gammelt navn" in result["sections"][0]["unified_diff"]
    assert "+§ 1-1. Nytt navn" in result["sections"][0]["unified_diff"]


def test_diff_section_maps_marks_empty_body_retitle_as_changed() -> None:
    before = {"1-1": _sec("§ 1-1. Gammelt navn", "")}
    after = {"1-1": _sec("§ 1-1. Nytt navn", "")}

    result = _diff_section_maps(_secs(before), _secs(after))

    assert result["summary"]["sections_changed"] == 1
    assert result["sections"][0]["unified_diff"] == "\n".join(
        [
            "--- before",
            "+++ after",
            "@@ -1 +1 @@",
            "-§ 1-1. Gammelt navn",
            "+§ 1-1. Nytt navn",
        ],
    )


# --- CorpusReader.diff_law_versions: version-to-version section diff ---


def _law_md(sections: list[tuple[str, str, str]]) -> str:
    """Render a minimal lovspor-shaped Markdown doc (frontmatter + H1 +
    one chapter) from ``(section_id, title, body)`` triples."""
    lines = [
        "---",
        "id: nl-1",
        "title: Skatteloven",
        "---",
        "",
        "# Skatteloven",
        "",
        "## Kapittel 1. Alminnelige bestemmelser",
        "",
    ]
    for sid, title, body in sections:
        lines += [f"### § {sid}. {title}", "", body, ""]
    return "\n".join(lines)


def _rev(content: str, sha: str) -> RevisionResult:
    return RevisionResult(
        content=content,
        sha=sha,
        commit_date=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_diff_law_versions_reports_added_removed_changed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    before = _law_md(
        [
            ("1", "Formål", "Loven gjelder skatt."),
            ("2", "Virkeområde", "Gjelder hele landet."),
            ("3", "Opphevet", "Skal oppheves."),
        ],
    )
    after = _law_md(
        [
            ("1", "Formål", "Loven gjelder skatt."),
            ("2", "Virkeområde", "Gjelder hele riket."),
            ("4", "Ny", "Ny paragraf."),
        ],
    )
    revs = {
        date(2020, 1, 1): _rev(before, "sha-a"),
        date(2024, 1, 1): _rev(after, "sha-b"),
    }

    def fake_resolve(
        _repo_path: Path,
        _current_path: str,
        target_date: date,
    ) -> RevisionResult:
        return revs[target_date]

    monkeypatch.setattr("lovspor.mcp.resolve_law_at_revision", fake_resolve)

    result = CorpusReader(tmp_path).diff_law_versions(
        "skatteloven",
        "2020-01-01",
        "2024-01-01",
    )

    assert result["slug"] == "skatteloven"
    assert result["date_a"] == "2020-01-01"
    assert result["date_b"] == "2024-01-01"
    assert result["resolved_commit_a"] == "sha-a"
    assert result["resolved_commit_b"] == "sha-b"
    assert result["summary"] == {
        "sections_added": 1,
        "sections_removed": 1,
        "sections_changed": 1,
    }
    by_id = {e["section_id"]: e["change_type"] for e in result["sections"]}
    assert by_id == {"2": "changed", "3": "removed", "4": "added"}
    added = next(e for e in result["sections"] if e["section_id"] == "4")
    assert added["unified_diff"] == "\n".join(
        [
            "--- before",
            "+++ after",
            "@@ -0,0 +1,2 @@",
            "+§ 4. Ny",
            "+Ny paragraf.",
        ],
    )
    removed = next(e for e in result["sections"] if e["section_id"] == "3")
    assert removed["unified_diff"] == "\n".join(
        [
            "--- before",
            "+++ after",
            "@@ -1,2 +0,0 @@",
            "-§ 3. Opphevet",
            "-Skal oppheves.",
        ],
    )


def test_diff_law_versions_counts_multiple_changed_sections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    before = _law_md(
        [
            ("1", "Formål", "Gammel formålstekst."),
            ("2", "Virkeområde", "Gammelt virkeområde."),
        ],
    )
    after = _law_md(
        [
            ("1", "Formål", "Ny formålstekst."),
            ("2", "Virkeområde", "Nytt virkeområde."),
        ],
    )

    def fake_resolve(
        _repo_path: Path,
        _current_path: str,
        target_date: date,
    ) -> RevisionResult:
        return _rev(before, "sha-a") if target_date == date(2020, 1, 1) else _rev(after, "sha-b")

    monkeypatch.setattr("lovspor.mcp.resolve_law_at_revision", fake_resolve)

    result = CorpusReader(tmp_path).diff_law_versions(
        "skatteloven",
        "2020-01-01",
        "2024-01-01",
    )

    assert result["summary"] == {
        "sections_added": 0,
        "sections_removed": 0,
        "sections_changed": 2,
    }
    assert [entry["section_id"] for entry in result["sections"]] == ["1", "2"]


def test_diff_law_versions_same_content_yields_empty_diff_but_reports_commits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Identical content on both dates → no section entries, yet the response
    still reports which two commits were compared."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    md = _law_md([("1", "Formål", "Loven gjelder skatt.")])

    def fake_resolve(
        _repo_path: Path,
        _current_path: str,
        target_date: date,
    ) -> RevisionResult:
        return _rev(md, "sha-x" if target_date == date(2020, 1, 1) else "sha-y")

    monkeypatch.setattr("lovspor.mcp.resolve_law_at_revision", fake_resolve)

    result = CorpusReader(tmp_path).diff_law_versions(
        "skatteloven",
        "2020-01-01",
        "2024-01-01",
    )
    assert result["sections"] == []
    assert result["summary"] == {
        "sections_added": 0,
        "sections_removed": 0,
        "sections_changed": 0,
    }
    assert result["resolved_commit_a"] == "sha-x"
    assert result["resolved_commit_b"] == "sha-y"


def test_diff_law_versions_ignores_frontmatter_metadata_churn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    body = "\n".join(
        [
            "# Skatteloven",
            "",
            "## Kapittel 1. Alminnelige bestemmelser",
            "",
            "### § 1. Formål",
            "",
            "Loven gjelder skatt.",
            "",
        ],
    )
    before = (
        "---\n"
        "id: nl-1\n"
        "title: Skatteloven\n"
        "retrieved_at: 2020-01-01T00:00:00Z\n"
        f"xml_hash: {'a' * 64}\n"
        "---\n\n"
        f"{body}"
    )
    after = (
        "---\n"
        "id: nl-1\n"
        "title: Skatteloven\n"
        "retrieved_at: 2024-01-01T00:00:00Z\n"
        f"xml_hash: {'b' * 64}\n"
        "---\n\n"
        f"{body}"
    )

    def fake_resolve(
        _repo_path: Path,
        _current_path: str,
        target_date: date,
    ) -> RevisionResult:
        return _rev(before, "sha-a") if target_date == date(2020, 1, 1) else _rev(after, "sha-b")

    monkeypatch.setattr("lovspor.mcp.resolve_law_at_revision", fake_resolve)

    result = CorpusReader(tmp_path).diff_law_versions(
        "skatteloven",
        "2020-01-01",
        "2024-01-01",
    )

    assert result["sections"] == []
    assert result["summary"] == {
        "sections_added": 0,
        "sections_removed": 0,
        "sections_changed": 0,
    }
    serialized = json.dumps(result, sort_keys=True)
    assert "retrieved_at" not in serialized
    assert "xml_hash" not in serialized


def test_diff_law_versions_rejects_manifest_path_escaping_corpus_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evil = _record(slug="evil", title="Evil").model_copy(
        update={"markdown_path": "../../../../etc/passwd"},
    )
    _seed_corpus(tmp_path, {"nl-1": evil}, write_files=False)

    def fail_if_called(*_args: object) -> RevisionResult:
        raise AssertionError("timetravel must not run for an escaping path")

    monkeypatch.setattr("lovspor.mcp.resolve_law_at_revision", fail_if_called)

    with pytest.raises(CorpusNotFoundError, match="escapes corpus root"):
        CorpusReader(tmp_path).diff_law_versions("evil", "2020-01-01", "2024-01-01")


def test_diff_law_versions_rejects_non_iso_date_a(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})

    def fail_if_called(*_args: object) -> RevisionResult:
        raise AssertionError("timetravel lookup should not run")

    monkeypatch.setattr("lovspor.mcp.resolve_law_at_revision", fail_if_called)

    with pytest.raises(
        ValueError,
        match=r"^date_a must be ISO date YYYY-MM-DD, got '01-01-2020'$",
    ):
        CorpusReader(tmp_path).diff_law_versions("skatteloven", "01-01-2020", "2024-01-01")


def test_diff_law_versions_rejects_non_iso_date_b_before_timetravel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})

    def fail_if_called(*_args: object) -> RevisionResult:
        raise AssertionError("timetravel lookup should not run")

    monkeypatch.setattr("lovspor.mcp.resolve_law_at_revision", fail_if_called)

    with pytest.raises(
        ValueError,
        match=r"^date_b must be ISO date YYYY-MM-DD, got '01-01-2024'$",
    ):
        CorpusReader(tmp_path).diff_law_versions("skatteloven", "2020-01-01", "01-01-2024")


def test_diff_law_versions_allows_todays_utc_date(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    today = datetime.now(UTC).date()
    seen: list[date] = []
    md = _law_md([("1", "Formål", "Loven gjelder skatt.")])

    def fake_resolve(
        _repo_path: Path,
        _current_path: str,
        target_date: date,
    ) -> RevisionResult:
        seen.append(target_date)
        return _rev(md, f"sha-{len(seen)}")

    monkeypatch.setattr("lovspor.mcp.resolve_law_at_revision", fake_resolve)

    result = CorpusReader(tmp_path).diff_law_versions(
        "skatteloven",
        today.isoformat(),
        today.isoformat(),
    )

    assert seen == [today, today]
    assert result["date_a"] == today.isoformat()
    assert result["date_b"] == today.isoformat()


def test_diff_law_versions_rejects_future_date_b(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})

    def fail_if_called(*_args: object) -> RevisionResult:
        raise AssertionError("timetravel lookup should not run")

    monkeypatch.setattr("lovspor.mcp.resolve_law_at_revision", fail_if_called)

    today = datetime.now(UTC).date()
    with pytest.raises(
        ValueError,
        match=rf"^date_b 2999-01-01 is in the future \(today is {today.isoformat()}\)$",
    ):
        CorpusReader(tmp_path).diff_law_versions("skatteloven", "2020-01-01", "2999-01-01")


def test_diff_law_versions_translates_missing_revision_to_corpus_not_found(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})

    def fake_resolve(*_args: object) -> RevisionResult:
        raise RevisionNotFoundError("too early")

    monkeypatch.setattr("lovspor.mcp.resolve_law_at_revision", fake_resolve)

    with pytest.raises(
        CorpusNotFoundError,
        match=(
            r"^law 'skatteloven' did not exist in the corpus on 2018-01-01; "
            r"call get_law_history\('skatteloven'\) to see when it first appeared$"
        ),
    ):
        CorpusReader(tmp_path).diff_law_versions("skatteloven", "2018-01-01", "2024-01-01")


# --- diff_law_versions over a real git corpus (end-to-end, no monkeypatch) ---


def _law_body(title: str, sections: list[tuple[str, str, str]]) -> str:
    """Body portion (H1 + one chapter + sections) that _seed_corpus / _write_doc
    wrap in frontmatter. _strip_frontmatter_and_h1 removes the frontmatter and
    the H1, leaving the chapter + sections the diff parses."""
    lines = [f"# {title}", "", "## Kapittel 1. Alminnelige bestemmelser", ""]
    for sid, sec_title, sec_body in sections:
        lines += [f"### § {sid}. {sec_title}", "", sec_body, ""]
    return "\n".join(lines)


def _write_doc(path: Path, title: str, body: str) -> None:
    path.write_text(f"---\nid: x\ntitle: {title}\n---\n\n{body}", encoding="utf-8")


def _commit_corpus_at(repo: Path, message: str, when: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
        env=env,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_diff_law_versions_end_to_end_over_real_git(tmp_path: Path) -> None:
    """Full path with no monkeypatch: two committed versions at two dates,
    diffed via git-follow. Uses an æøå slug so the core.quotePath handling in
    timetravel is exercised end-to-end."""
    repo = tmp_path / "lovverk"
    _git_init_corpus(repo)
    slug = "arbeidsmiljøloven-aml"
    title = "Arbeidsmiljøloven"
    v1 = _law_body(
        title,
        [
            ("1-1", "Formål", "Sikre et godt arbeidsmiljø."),
            ("1-2", "Virkeområde", "Gjelder norsk landterritorium."),
        ],
    )
    _seed_corpus(
        repo,
        {"nl-1": _record(slug=slug, title=title)},
        body_for={slug: v1},
    )
    sha_a = _commit_corpus_at(repo, "add(lov): aml", "2020-01-01T12:00:00+00:00")

    v2 = _law_body(
        title,
        [
            ("1-1", "Formål", "Sikre et godt arbeidsmiljø."),
            ("1-2", "Virkeområde", "Gjelder også Svalbard."),
            ("1-3", "Ny bestemmelse", "Trer i kraft 2024."),
        ],
    )
    _write_doc(repo / "lover" / f"{slug}.md", title, v2)
    sha_b = _commit_corpus_at(repo, "update(lov): aml", "2024-01-01T12:00:00+00:00")

    result = CorpusReader(repo).diff_law_versions(slug, "2020-06-01", "2024-06-01")

    assert result["summary"] == {
        "sections_added": 1,
        "sections_removed": 0,
        "sections_changed": 1,
    }
    by_id = {e["section_id"]: e["change_type"] for e in result["sections"]}
    assert by_id == {"1-2": "changed", "1-3": "added"}
    assert result["resolved_commit_a"] == sha_a
    assert result["resolved_commit_b"] == sha_b
    changed = next(e for e in result["sections"] if e["section_id"] == "1-2")
    assert "-Gjelder norsk landterritorium." in changed["unified_diff"]
    assert "+Gjelder også Svalbard." in changed["unified_diff"]


# --- flat (chapterless) laws: sections render as ## § N., not ### § N. ---


@pytest.mark.parametrize(
    ("line", "section_id", "title"),
    [
        ("## § 1. Formål", "1", "Formål"),
        ("## § 14.", "14", None),
        ("## § 13. (Opphevet)", "13", "(Opphevet)"),
        ("### § 5-12. Title", "5-12", "Title"),
        ("### § 5", "5", None),
    ],
)
def test_section_heading_regex_matches_real_heading_shapes(
    line: str,
    section_id: str,
    title: str | None,
) -> None:
    match = _SECTION_HEADING.match(line)

    assert match is not None
    assert match.group(1) == section_id
    assert match.group(2) == title


def test_section_heading_regex_does_not_match_chapter_heading() -> None:
    assert _SECTION_HEADING.match("## Kapittel 1.") is None


def test_parse_sections_recognizes_h2_flat_law_sections() -> None:
    """Flat laws with no chapter level render paragraphs as ``## § N.`` (H2).
    _parse_sections must treat those as sections, not chapters. Regression for
    vrakloven and ~18% of multi-version laws that parsed to zero sections."""
    body = (
        "## § 1. Formål\n\nLoven gjelder berging.\n\n## § 2. Virkeområde\n\nGjelder hele landet.\n"
    )
    sections = _parse_sections(body)
    assert [s["section_id"] for s in sections] == ["1", "2"]
    assert sections[0]["heading"] == "§ 1. Formål"
    assert sections[0]["parent_chapter"] == ""
    assert sections[0]["body"] == "Loven gjelder berging."


def test_parse_sections_h2_titleless_section_with_trailing_dot() -> None:
    """``## § 14.`` (H2, trailing dot, no title) is how flat laws render a
    titleless paragraph — it must still parse as section 14."""
    body = "## § 13. (Opphevet)\n\nx\n\n## § 14.\n\nInnhold.\n"
    sections = _parse_sections(body)
    assert [s["section_id"] for s in sections] == ["13", "14"]
    assert sections[0]["heading"] == "§ 13. (Opphevet)"
    assert sections[1]["heading"] == "§ 14"
    assert sections[1]["body"] == "Innhold."


def test_parse_sections_still_handles_chaptered_h3_sections() -> None:
    """Regression: chaptered laws keep working — ``## Kapittel`` is a chapter,
    ``### § N-M`` is the section under it."""
    body = "## Kapittel 1. Alminnelig\n\n### § 1-1. Start\n\nBody.\n"
    sections = _parse_sections(body)
    assert [s["section_id"] for s in sections] == ["1-1"]
    assert sections[0]["parent_chapter"] == "Kapittel 1. Alminnelig"
    assert sections[0]["body"] == "Body."


def test_parse_sections_mixed_h2_section_and_h2_chapter() -> None:
    """``## §`` is a section; ``## Kapittel`` (no ``§``) is still a chapter."""
    body = "## Kapittel 1.\n\n### § 1-1. A\n\nx\n\n## § 2. B\n\ny\n"
    sections = _parse_sections(body)
    assert [s["section_id"] for s in sections] == ["1-1", "2"]
    assert sections[0]["parent_chapter"] == "Kapittel 1."
    assert sections[1]["parent_chapter"] == "Kapittel 1."


def test_list_and_get_section_work_on_flat_h2_law(tmp_path: Path) -> None:
    """End-to-end via the public tools: a flat law is now navigable."""
    flat = "# Vrakloven\n\n## § 1. Formål\n\nLoven gjelder berging.\n\n## § 2.\n\nMer.\n"
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="vrakloven", title="Vrakloven")},
        body_for={"vrakloven": flat},
    )
    reader = CorpusReader(tmp_path)
    assert [s["section_id"] for s in reader.list_sections("vrakloven")] == ["1", "2"]
    section = reader.get_section("vrakloven", "1")
    assert section["heading"] == "§ 1. Formål"
    assert section["parent_chapter"] == ""
    assert section["body"] == "Loven gjelder berging."


def test_flat_h2_law_section_cross_references_and_search_body_work(tmp_path: Path) -> None:
    flat = (
        "# Flat lov\n\n"
        "## § 1. Formål\n\n"
        "Se § 2. søkeord-flat.\n\n"
        "## § 2. Definisjoner\n\n"
        "Definisjonstekst.\n"
    )
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="flat-lov", title="Flat lov")},
        body_for={"flat-lov": flat},
    )

    reader = CorpusReader(tmp_path)
    section = reader.get_section("flat-lov", "1")

    assert section["cross_references"] == [
        {
            "text": "§ 2",
            "target_slug": "flat-lov",
            "target_section_id": "2",
            "valid": True,
            "reason": None,
        },
    ]
    assert [hit["slug"] for hit in reader.search_body("søkeord-flat")] == ["flat-lov"]


def test_diff_law_versions_diffs_flat_h2_law_sections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="vrakloven", title="Vrakloven")})
    before = "\n".join(
        [
            "---",
            "id: nl-1",
            "title: Vrakloven",
            "---",
            "",
            "# Vrakloven",
            "",
            "## § 1. Formål",
            "",
            "Gammel tekst.",
            "",
            "## § 2.",
            "",
            "Uendret.",
            "",
        ],
    )
    after = "\n".join(
        [
            "---",
            "id: nl-1",
            "title: Vrakloven",
            "---",
            "",
            "# Vrakloven",
            "",
            "## § 1. Formål",
            "",
            "Ny tekst.",
            "",
            "## § 2.",
            "",
            "Uendret.",
            "",
            "## § 3. Ny",
            "",
            "Ny paragraf.",
            "",
        ],
    )

    def fake_resolve(
        _repo_path: Path,
        _current_path: str,
        target_date: date,
    ) -> RevisionResult:
        return _rev(before, "sha-a") if target_date == date(2020, 1, 1) else _rev(after, "sha-b")

    monkeypatch.setattr("lovspor.mcp.resolve_law_at_revision", fake_resolve)

    result = CorpusReader(tmp_path).diff_law_versions(
        "vrakloven",
        "2020-01-01",
        "2024-01-01",
    )

    assert result["summary"] == {
        "sections_added": 1,
        "sections_removed": 0,
        "sections_changed": 1,
    }
    assert [(entry["section_id"], entry["change_type"]) for entry in result["sections"]] == [
        ("1", "changed"),
        ("3", "added"),
    ]


# --- duplicate § ids: section ids are NOT unique within a document ---
#
# Two real shapes from the corpus (lovverk @ 1cac8a60, 7 documents affected):
#   betalingssystemloven  — a `§ 6-2` in Kapittel 6 and another in Kapittel 7
#   førerkortforskriften  — Vedlegg 1 and Vedlegg 2 each restart at `§ 1`
# Before this fix `_parse_sections` keyed a dict by section_id and assigned
# last-wins, so `§ 6-2. Forskrifter` was unreachable via get_section, absent
# from list_sections, and invisible to diff_law_versions.

_DUPLICATE_ID_BODY = (
    "## Kapittel 6. Øvrige bestemmelser\n\n"
    "### § 6-1. Tilsyn\n\nNoregs Bank fører tilsyn.\n\n"
    "### § 6-2. Forskrifter\n\nKongen kan gi forskrifter.\n\n"
    "### § 6-2. Endringer i andre lover\n\nFra den tid loven trer i kraft.\n"
)

_APPENDIX_BODY = (
    "## Kapittel 1. Alminnelige bestemmelser\n\n"
    "### § 1. Definisjoner\n\nHoveddelens definisjoner.\n\n"
    "## Vedlegg 1 – Helsekrav\n\n"  # noqa: RUF001 — en dash is verbatim Lovdata text
    "### Kapittel 1. Definisjoner\n\n"
    "### § 1. Definisjoner\n\nVedleggets definisjoner.\n"
)


def test_parse_sections_preserves_both_occurrences_of_a_duplicate_id() -> None:
    """Two `§ 6-2` headings in one act are two sections, not one.

    Regression for betalingssystemloven: the dict-keyed parser dropped
    `§ 6-2. Forskrifter` entirely.
    """
    sections = _parse_sections(_DUPLICATE_ID_BODY)
    dupes = [s for s in sections if s["section_id"] == "6-2"]
    assert [s["heading"] for s in dupes] == [
        "§ 6-2. Forskrifter",
        "§ 6-2. Endringer i andre lover",
    ]
    assert [s["occurrence"] for s in dupes] == [1, 2]
    assert dupes[0]["body"] == "Kongen kan gi forskrifter."


def test_parse_sections_keeps_appendix_sections_separate_from_the_main_body() -> None:
    """An appendix restarts § numbering; its § 1 must not shadow the body's § 1."""
    sections = _parse_sections(_APPENDIX_BODY)
    ones = [s for s in sections if s["section_id"] == "1"]
    assert len(ones) == 2
    assert ones[0]["parent_chapter"] == "Kapittel 1. Alminnelige bestemmelser"
    assert ones[0]["body"] == "Hoveddelens definisjoner."
    assert ones[1]["parent_chapter"] == "Vedlegg 1 – Helsekrav"  # noqa: RUF001
    assert ones[1]["body"] == "Vedleggets definisjoner."


def test_parse_sections_numbers_occurrences_per_id_not_globally() -> None:
    """`occurrence` counts within one section_id — a unique id is always 1."""
    sections = _parse_sections(_DUPLICATE_ID_BODY)
    by_id = {(s["section_id"], s["occurrence"]) for s in sections}
    assert by_id == {("6-1", 1), ("6-2", 1), ("6-2", 2)}


def test_list_sections_lists_every_occurrence_of_a_duplicate_id(tmp_path: Path) -> None:
    """The TOC must not silently omit a section that shares an id with another."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="betalingssystemloven", title="Betalingssystemloven")},
        body_for={"betalingssystemloven": _DUPLICATE_ID_BODY},
    )
    rows = CorpusReader(tmp_path).list_sections("betalingssystemloven")
    assert [(r["section_id"], r["heading"]) for r in rows] == [
        ("6-1", "§ 6-1. Tilsyn"),
        ("6-2", "§ 6-2. Forskrifter"),
        ("6-2", "§ 6-2. Endringer i andre lover"),
    ]
    assert [r["occurrence"] for r in rows] == [1, 1, 2]


def test_get_section_refuses_to_guess_on_an_ambiguous_id(tmp_path: Path) -> None:
    """Silently returning one of two `§ 6-2` is a hallucination vector: the AI
    would quote 'Endringer i andre lover' when asked about § 6-2. Raise, and
    name both occurrences so the caller can recover in one round trip."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="betalingssystemloven", title="Betalingssystemloven")},
        body_for={"betalingssystemloven": _DUPLICATE_ID_BODY},
    )
    reader = CorpusReader(tmp_path)
    with pytest.raises(CorpusAmbiguousSectionError) as excinfo:
        reader.get_section("betalingssystemloven", "6-2")
    # Pinned whole: this message is the recovery path — it must name BOTH
    # occurrences with their chapter, or the AI cannot re-ask correctly and is
    # left to guess which § 6-2 it was shown.
    assert str(excinfo.value) == (
        "section '6-2' is ambiguous in 'betalingssystemloven': 2 sections "
        "share that id — "
        "occurrence=1: § 6-2. Forskrifter [Kapittel 6. Øvrige bestemmelser]; "
        "occurrence=2: § 6-2. Endringer i andre lover [Kapittel 6. Øvrige bestemmelser]. "
        "Re-call with occurrence=N to choose one."
    )


def test_ambiguity_message_omits_the_chapter_when_the_sections_have_none(
    tmp_path: Path,
) -> None:
    """Duplicate ids in an act with no chapter headings: each candidate is
    listed bare, with no empty ``[]`` bracket. The chapter suffix is the only
    part of the recovery message that is conditional, and nothing exercised its
    absent-chapter branch — so a mutation there could not be caught."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="chapterless", title="Chapterless")},
        body_for={
            "chapterless": (
                "### § 6-2. Forskrifter\n\nKongen kan gi forskrifter.\n\n"
                "### § 6-2. Endringer i andre lover\n\nFra den tid loven trer i kraft.\n"
            ),
        },
    )

    with pytest.raises(CorpusAmbiguousSectionError) as excinfo:
        CorpusReader(tmp_path).get_section("chapterless", "6-2")

    assert str(excinfo.value) == (
        "section '6-2' is ambiguous in 'chapterless': 2 sections share that id — "
        "occurrence=1: § 6-2. Forskrifter; "
        "occurrence=2: § 6-2. Endringer i andre lover. "
        "Re-call with occurrence=N to choose one."
    )


def test_get_section_returns_the_requested_occurrence(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="betalingssystemloven", title="Betalingssystemloven")},
        body_for={"betalingssystemloven": _DUPLICATE_ID_BODY},
    )
    reader = CorpusReader(tmp_path)
    first = reader.get_section("betalingssystemloven", "6-2", occurrence=1)
    second = reader.get_section("betalingssystemloven", "6-2", occurrence=2)
    assert first["heading"] == "§ 6-2. Forskrifter"
    assert first["body"] == "Kongen kan gi forskrifter."
    assert first["occurrence"] == 1
    assert second["heading"] == "§ 6-2. Endringer i andre lover"
    assert second["occurrence"] == 2


def test_get_section_rejects_an_out_of_range_occurrence(tmp_path: Path) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="betalingssystemloven", title="Betalingssystemloven")},
        body_for={"betalingssystemloven": _DUPLICATE_ID_BODY},
    )
    reader = CorpusReader(tmp_path)
    with pytest.raises(CorpusNotFoundError) as excinfo:
        reader.get_section("betalingssystemloven", "6-2", occurrence=3)
    # The valid range is the whole point of the message — pin it.
    assert str(excinfo.value) == (
        "occurrence 3 of section '6-2' not found in 'betalingssystemloven'; it has 2 (valid: 1-2)"
    )


def test_get_section_unique_id_is_unaffected_by_the_occurrence_machinery(
    tmp_path: Path,
) -> None:
    """Regression: the 99.9% case must behave exactly as before — no occurrence
    argument needed, no error, and the response still reports occurrence 1."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="betalingssystemloven", title="Betalingssystemloven")},
        body_for={"betalingssystemloven": _DUPLICATE_ID_BODY},
    )
    section = CorpusReader(tmp_path).get_section("betalingssystemloven", "6-1")
    assert section["heading"] == "§ 6-1. Tilsyn"
    assert section["occurrence"] == 1


def test_diff_section_maps_diffs_each_occurrence_of_a_duplicate_id() -> None:
    """Keying the diff by section_id alone collapsed an act's two `§ 6-2` into
    one, so a change to the FIRST was invisible — diff_law_versions would report
    'no change' on a section that had in fact been rewritten."""
    before = [
        ParsedSection(
            section_id="6-2",
            occurrence=1,
            heading="§ 6-2. Forskrifter",
            parent_chapter="Kapittel 6.",
            body="Gammel forskriftshjemmel.",
        ),
        ParsedSection(
            section_id="6-2",
            occurrence=2,
            heading="§ 6-2. Endringer i andre lover",
            parent_chapter="Kapittel 6.",
            body="Uendret.",
        ),
    ]
    after = [
        {**before[0], "body": "Ny forskriftshjemmel."},
        before[1],
    ]
    result = _diff_section_maps(before, after)  # type: ignore[arg-type]

    assert result["summary"] == {
        "sections_added": 0,
        "sections_removed": 0,
        "sections_changed": 1,
    }
    [entry] = result["sections"]
    assert entry["section_id"] == "6-2"
    assert entry["occurrence"] == 1
    assert entry["heading"] == "§ 6-2. Forskrifter"
    assert "+Ny forskriftshjemmel." in entry["unified_diff"]


def test_diff_section_maps_reports_occurrence_of_the_changed_second_duplicate() -> None:
    """A change to the SECOND § 6-2 must report occurrence=2, not a constant 1.
    Pins the ``source is not None`` guard on the occurrence field: collapsing it
    makes every entry claim occurrence 1, silently mislabelling which duplicate
    of a repeated section-id was rewritten. Also pins the entry's key set."""
    before = [
        ParsedSection(
            section_id="6-2",
            occurrence=1,
            heading="§ 6-2. Forskrifter",
            parent_chapter="Kapittel 6.",
            body="Uendret.",
        ),
        ParsedSection(
            section_id="6-2",
            occurrence=2,
            heading="§ 6-2. Endringer i andre lover",
            parent_chapter="Kapittel 6.",
            body="Gammel tekst.",
        ),
    ]
    after = [before[0], {**before[1], "body": "Ny tekst."}]

    result = _diff_section_maps(before, after)  # type: ignore[arg-type]

    assert set(result) == {"summary", "sections"}
    [entry] = result["sections"]
    assert entry["change_type"] == "changed"
    assert entry["occurrence"] == 2
    assert set(entry) == {"section_id", "occurrence", "heading", "change_type", "unified_diff"}


def test_diff_section_maps_does_not_misclassify_the_survivor_when_a_duplicate_id_disappears() -> (
    None
):
    """From Codex review of PR #135.

    `occurrence` is a POSITION, not an identity. If an act had two `§ 6-2` and
    now has one, the survivor renumbers from 2 to 1 — and pairing on the number
    alone diffs it against the DELETED section's text, reporting a bogus
    changed+removed instead of a single removal.
    """
    before = [
        ParsedSection(
            section_id="6-2",
            occurrence=1,
            heading="§ 6-2. Forskrifter",
            parent_chapter="Kapittel 6.",
            body="Samme tekst.",
        ),
        ParsedSection(
            section_id="6-2",
            occurrence=2,
            heading="§ 6-2. Endringer i andre lover",
            parent_chapter="Kapittel 6.",
            body="Samme tekst.",
        ),
    ]
    after = [
        ParsedSection(
            section_id="6-2",
            occurrence=1,
            heading="§ 6-2. Endringer i andre lover",
            parent_chapter="Kapittel 6.",
            body="Samme tekst.",
        ),
    ]

    result = _diff_section_maps(before, after)

    assert result["summary"] == {
        "sections_added": 0,
        "sections_removed": 1,
        "sections_changed": 0,
    }
    assert [(e["section_id"], e["change_type"], e["heading"]) for e in result["sections"]] == [
        ("6-2", "removed", "§ 6-2. Forskrifter"),
    ]


def test_semantic_hit_on_an_ambiguous_id_withholds_the_heading_instead_of_guessing(
    tmp_path: Path,
) -> None:
    """From Codex review of PR #135: grounding a repeated-id hit to the first
    occurrence is a false answer path — the vector may have matched the second.

    Codex proposed grounding by row ordinal instead. That is unsafe: a long
    section is chunked into SEVERAL vectors under the same section_id
    (embeddings/model.py), so the ordinal cannot distinguish 'chunk 2 of § 5-12'
    from 'occurrence 2 of § 6-2'. The store simply does not know which § matched.

    So withhold what cannot be known. Report the score and the id, flag the
    ambiguity, and let the caller resolve it via get_section(occurrence=N).
    """
    slug = "betalingssystemloven"
    body = (
        "## Kapittel 6. Øvrige bestemmelser\n\n"
        "### § 6-2. Forskrifter\n\nFørste forekomst.\n\n"
        "### § 6-2. Endringer i andre lover\n\nAndre forekomst.\n"
    )
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug=slug, title="Betalingssystemloven")},
        body_for={slug: body},
    )
    _write_embedding_file(tmp_path, "lover", slug, [("6-2", [1, 0]), ("6-2", [0, 1])])

    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder([0.0, 1.0]))
    [hit] = reader.semantic_search("beta", limit=1)["results"]

    assert hit["section_id"] == "6-2"
    assert hit["ambiguous_section"] is True
    assert hit["heading"] is None
    assert hit["snippet"] is None
    assert hit["occurrence"] is None
    assert hit["citation_hint"] == "§ 6-2 betalingssystemloven"


def test_semantic_hit_on_a_chunked_section_still_grounds_normally(tmp_path: Path) -> None:
    """The regression guarding the fix above: a long section embeds as several
    vectors under ONE section_id. That must NOT be mistaken for a repeated id —
    the section is unambiguous and its heading and snippet must still be served.
    """
    slug = "skatteloven"
    body = "## Kapittel 5. Inntekt\n\n### § 5-12. Boligsparing\n\nLang tekst om sparing.\n"
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug=slug, title="Skatteloven")},
        body_for={slug: body},
    )
    # Two chunk vectors, same section_id — one section, not two.
    _write_embedding_file(tmp_path, "lover", slug, [("5-12", [1, 0]), ("5-12", [0, 1])])

    reader = CorpusReader(tmp_path, embedder=_FakeEmbedder([0.0, 1.0]))
    [hit] = reader.semantic_search("sparing", limit=1)["results"]

    assert hit["ambiguous_section"] is False
    assert hit["heading"] == "§ 5-12. Boligsparing"
    assert hit["occurrence"] == 1
    assert "Lang tekst" in hit["snippet"]


def _dup(occurrence: int, title: str, body: str) -> ParsedSection:
    """One occurrence of the repeated id § 6-2."""
    return ParsedSection(
        section_id="6-2",
        occurrence=occurrence,
        heading=f"§ 6-2. {title}",
        parent_chapter="Kapittel 6.",
        body=body,
    )


def test_pair_occurrences_matches_identical_sections_before_matching_on_heading() -> None:
    """Two `§ 6-2` can share a heading and differ only in body — Lovdata reuses
    titles like 'Forskrifter'. If the FIRST is repealed, matching on heading
    alone pairs the survivor against the deleted section's body and reports a
    bogus change on top of the removal.

    Pairing exact (heading + body) matches FIRST is what prevents that.
    """
    before = [_dup(1, "Forskrifter", "Gammel hjemmel."), _dup(2, "Forskrifter", "Uendret.")]
    after = [_dup(1, "Forskrifter", "Uendret.")]

    result = _diff_section_maps(before, after)

    assert result["summary"] == {
        "sections_added": 0,
        "sections_removed": 1,
        "sections_changed": 0,
    }
    [entry] = result["sections"]
    assert entry["change_type"] == "removed"
    assert "-Gammel hjemmel." in entry["unified_diff"]


def test_pair_occurrences_matches_on_heading_before_falling_back_to_position() -> None:
    """When several occurrences of one id are reordered AND edited, position is a
    lie: the section that moved must still be diffed against ITS OWN earlier text,
    not against whatever now sits at its old index.

    Without the heading pass the leftovers are paired positionally, and each
    section's diff is computed against a different section's body — a confidently
    wrong account of what the law changed, with the right summary counts to hide it.
    """
    before = [
        _dup(1, "Alfa", "alfa-gammel"),
        _dup(2, "Beta", "beta-gammel"),
        _dup(3, "Gamma", "uendret"),
    ]
    after = [_dup(1, "Gamma", "uendret"), _dup(2, "Beta", "beta-ny"), _dup(3, "Alfa", "alfa-ny")]

    result = _diff_section_maps(before, after)

    assert result["summary"]["sections_changed"] == 2
    diffs = {e["heading"]: e["unified_diff"] for e in result["sections"]}
    # Each section is diffed against its own prior text, not its neighbour's.
    assert "-alfa-gammel" in diffs["§ 6-2. Alfa"]
    assert "+alfa-ny" in diffs["§ 6-2. Alfa"]
    assert "beta" not in diffs["§ 6-2. Alfa"]
    assert "-beta-gammel" in diffs["§ 6-2. Beta"]
    assert "+beta-ny" in diffs["§ 6-2. Beta"]
    assert "alfa" not in diffs["§ 6-2. Beta"]


def _authed_call(server: FastMCP, credential_id: str, name: str, args: dict[str, object]) -> object:
    """Invoke a registered tool as `credential_id` would, through the real
    contextvar AuthContextMiddleware sets on an authenticated request."""

    async def run() -> object:
        user = AuthenticatedUser(AccessToken(token="t", client_id=credential_id, scopes=[]))
        reset = auth_context_var.set(user)
        try:
            return await server._tool_manager.call_tool(
                name, args, context=None, convert_result=False
            )
        finally:
            auth_context_var.reset(reset)

    return asyncio.run(run())


def _quota_corpus(tmp_path: Path, limits: Limits) -> Path:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    creds = tmp_path / "credentials.json"
    write_credential_file(
        creds,
        [
            Credential(
                credential_id="beta-001",
                label="tester",
                token_sha256=hash_token("lsp_x"),
                limits=limits,
            )
        ],
    )
    return creds


def _sse_payload(response: Response) -> dict[str, object]:
    data = next(
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    )
    payload = json.loads(data)
    assert isinstance(payload, dict)
    return payload


def _initialize_mcp_session(
    client: TestClient,
    token: str,
) -> dict[str, str]:
    base_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
    }
    response = client.post(
        "/mcp",
        headers=base_headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "quota-test", "version": "1"},
            },
        },
    )
    assert response.status_code == 200
    session_headers = {
        **base_headers,
        "Mcp-Session-Id": response.headers["mcp-session-id"],
        "MCP-Protocol-Version": "2025-06-18",
    }
    initialized = client.post(
        "/mcp",
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert initialized.status_code == 202
    return session_headers


def _call_mcp_tool(
    client: TestClient,
    headers: dict[str, str],
    name: str,
    arguments: dict[str, object],
    *,
    request_id: int = 2,
) -> dict[str, object]:
    response = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200
    return _sse_payload(response)


def test_hosted_tools_are_metered_against_the_callers_credential(tmp_path: Path) -> None:
    creds = _quota_corpus(tmp_path, Limits(daily_quota=1, max_in_flight=9, rate_burst=9))
    server = build_server(tmp_path, http=HttpConfig(credentials_path=creds))

    _authed_call(server, "beta-001", "get_law", {"slug": "skatteloven"})

    with pytest.raises(ToolError, match="daily quota"):
        _authed_call(server, "beta-001", "get_law", {"slug": "skatteloven"})


def test_metering_is_per_credential_not_global(tmp_path: Path) -> None:
    """One tester burning their quota must not brake everyone else."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    creds = tmp_path / "credentials.json"
    write_credential_file(
        creds,
        [
            Credential(
                credential_id=cid,
                label=cid,
                token_sha256=hash_token(f"lsp_{cid}"),
                limits=Limits(daily_quota=1, max_in_flight=9, rate_burst=9),
            )
            for cid in ("beta-001", "beta-002")
        ],
    )
    server = build_server(tmp_path, http=HttpConfig(credentials_path=creds))

    _authed_call(server, "beta-001", "get_law", {"slug": "skatteloven"})
    with pytest.raises(ToolError, match="daily quota"):
        _authed_call(server, "beta-001", "get_law", {"slug": "skatteloven"})

    _authed_call(server, "beta-002", "get_law", {"slug": "skatteloven"})  # unaffected


def test_streamable_http_tool_body_keeps_session_identity_through_thread_hop(
    tmp_path: Path,
) -> None:
    """Re-derive the mcp 1.28.1 premise at the real transport boundary.

    The session task inherits the token from initialize. A later request
    authenticates again, but its refreshed token does not replace the context
    visible to the session task or the asyncio.to_thread worker.
    """
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    credentials_path = tmp_path / "credentials.json"
    token = "lsp_session_identity"

    def write(scopes: list[str]) -> None:
        write_credential_file(
            credentials_path,
            [
                Credential(
                    credential_id="beta-001",
                    label="tester",
                    token_sha256=hash_token(token),
                    scopes=scopes,
                    limits=Limits(),
                )
            ],
        )

    write(["session-creating-scope"])
    server = build_server(tmp_path, http=HttpConfig(credentials_path=credentials_path))

    def session_identity() -> str:
        access = get_access_token()
        assert access is not None
        return f"{access.client_id}|{','.join(access.scopes)}"

    server.add_tool(_offload_to_thread(session_identity))

    with TestClient(
        server.streamable_http_app(),
        base_url="http://127.0.0.1:8000",
    ) as client:
        headers = _initialize_mcp_session(client, token)
        write(["later-request-scope"])
        _bump_mtime(credentials_path)

        payload = _call_mcp_tool(client, headers, "session_identity", {})

    result = payload["result"]
    assert isinstance(result, dict)
    assert result["isError"] is False
    assert "beta-001|session-creating-scope" in str(result["content"])
    assert "later-request-scope" not in str(result["content"])


def test_streamable_http_quota_reads_tightened_limits_from_store(
    tmp_path: Path,
) -> None:
    """The token identity stays pinned, but mutable limits must not."""
    credentials_path = _quota_corpus(
        tmp_path,
        Limits(daily_quota=2, max_in_flight=10, rate_burst=10),
    )
    server = build_server(tmp_path, http=HttpConfig(credentials_path=credentials_path))
    token = "lsp_x"

    with TestClient(
        server.streamable_http_app(),
        base_url="http://127.0.0.1:8000",
    ) as client:
        headers = _initialize_mcp_session(client, token)
        first = _call_mcp_tool(
            client,
            headers,
            "get_law",
            {"slug": "skatteloven"},
        )
        write_credential_file(
            credentials_path,
            [
                Credential(
                    credential_id="beta-001",
                    label="tester",
                    token_sha256=hash_token(token),
                    limits=Limits(daily_quota=1, max_in_flight=10, rate_burst=10),
                )
            ],
        )
        _bump_mtime(credentials_path)

        second = _call_mcp_tool(
            client,
            headers,
            "get_law",
            {"slug": "skatteloven"},
            request_id=3,
        )

    first_result = first["result"]
    second_result = second["result"]
    assert isinstance(first_result, dict)
    assert isinstance(second_result, dict)
    assert first_result["isError"] is False
    assert second_result["isError"] is True
    assert "daily quota of 1 calls is exhausted" in str(second_result["content"])


def test_quota_guard_holds_slot_before_thread_hop_under_concurrency(
    tmp_path: Path,
) -> None:
    credentials_path = _quota_corpus(
        tmp_path,
        Limits(daily_quota=10, max_in_flight=1, rate_burst=10),
    )
    enforcer = QuotaEnforcer(CredentialStore(credentials_path))
    entered = threading.Event()
    release = threading.Event()
    calls: list[tuple[str, str]] = []

    def blocking_tool(value: str) -> str:
        calls.append((value, threading.current_thread().name))
        entered.set()
        assert release.wait(timeout=5)
        return value

    wrapped = _with_quota(_offload_to_thread(blocking_tool), enforcer)

    async def run() -> str:
        user = AuthenticatedUser(
            AccessToken(token="t", client_id="beta-001", scopes=[]),
        )
        reset = auth_context_var.set(user)
        first = asyncio.create_task(wrapped(value="accepted"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            with pytest.raises(QuotaExceededError, match="in flight"):
                await wrapped(value="refused")
        finally:
            release.set()
            auth_context_var.reset(reset)
        return await first

    assert asyncio.run(run()) == "accepted"
    assert [value for value, _thread_name in calls] == ["accepted"]
    assert calls[0][1] != threading.current_thread().name
    assert enforcer.daily_used("beta-001") == 1


def test_hosted_tools_refuse_a_call_that_carries_no_credential(tmp_path: Path) -> None:
    """RequireAuthMiddleware should make this unreachable. If it ever is
    reachable, an unidentifiable caller must not be served unmetered."""
    creds = _quota_corpus(tmp_path, Limits())
    server = build_server(tmp_path, http=HttpConfig(credentials_path=creds))

    async def run() -> object:
        return await server._tool_manager.call_tool(
            "get_law", {"slug": "skatteloven"}, context=None, convert_result=False
        )

    with pytest.raises(ToolError, match="no identified credential"):
        asyncio.run(run())


def test_insecure_hosted_mode_meters_nothing(tmp_path: Path) -> None:
    """--allow-insecure has no credential to meter; the brakes must not fire
    on an anonymous caller and lock the server out of its own tools."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    server = build_server(tmp_path, http=HttpConfig(allow_insecure=True))

    async def run() -> object:
        return await server._tool_manager.call_tool(
            "get_law", {"slug": "skatteloven"}, context=None, convert_result=False
        )

    for _ in range(3):
        asyncio.run(run())  # no raise


def test_quota_wrapper_preserves_the_tool_argument_schema(tmp_path: Path) -> None:
    """The wrapper stack has to keep __wrapped__ intact or FastMCP derives every
    tool's schema from (**kwargs) and the whole surface breaks silently."""
    creds = _quota_corpus(tmp_path, Limits())
    metered = build_server(tmp_path, http=HttpConfig(credentials_path=creds))
    plain = build_server(tmp_path)

    metered_tools = metered._tool_manager._tools
    plain_tools = plain._tool_manager._tools
    assert set(metered_tools) == set(plain_tools)
    for name in plain_tools:
        assert (
            metered._tool_manager.get_tool(name).parameters
            == plain._tool_manager.get_tool(name).parameters
        )


def test_cross_reference_is_unverifiable_when_the_target_index_is_incomplete() -> None:
    """`valid: false` from a lossy index is the worst output this server can
    produce. Missing data is visible to whoever reads it; a wrong denial reads
    as verified and gets acted on. 1 585 same-act references were reported
    invalid that way, against sections present in the file being read."""
    refs = _extract_cross_references(
        "Se § 8-9.",
        "folketrygdloven",
        {"folketrygdloven"},
        lambda _slug: SectionIndex(ids={"8-7"}, complete=False),
    )

    assert refs[0]["valid"] is None
    # Asserted whole, not by substring. A loose `in` check let mutants 1005/1007
    # survive: wrapping the message in marker text leaves the searched fragment
    # intact, so the assertion passed against a corrupted reason.
    assert refs[0]["reason"] == (
        "§ 8-9 is not in the parsed section index of 'folketrygdloven', "
        "and that index is incomplete — the act contains at least one heading "
        "this parser cannot read, so absence here is not evidence of absence"
    )


def test_cross_reference_is_invalid_when_the_target_index_is_complete() -> None:
    """The degradation is narrow on purpose: a complete index still yields a
    firm `false`, or the field would stop carrying information."""
    refs = _extract_cross_references(
        "Se § 9-99.",
        "folketrygdloven",
        {"folketrygdloven"},
        lambda _slug: SectionIndex(ids={"8-7"}, complete=True),
    )

    assert refs[0]["valid"] is False
    assert refs[0]["reason"] == "§ 9-99 not found in 'folketrygdloven'"


def test_get_section_marks_a_reference_unverifiable_on_an_unparsable_heading(
    tmp_path: Path,
) -> None:
    body = (
        "## Kapittel 1.\n\n"
        "### § 1-1. Main\n\n"
        "Se § 1-2 for definisjonen.\n\n"
        "### § x-1. Uleselig overskrift\n\n"
        "Target body.\n"
    )
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="egen-lov", title="Egen lov")},
        body_for={"egen-lov": body},
    )

    section = CorpusReader(tmp_path).get_section("egen-lov", "1-1")

    assert section["cross_references"][0]["valid"] is None


def test_snippet_returns_the_whole_row_when_the_match_is_inside_a_table() -> None:
    """The takstforskriften failure: `jobbmestring` matched the description
    column of the 2j row and the 50-char window stopped 550 characters before
    the fee. A hit that carries none of the payload reads as coverage and
    prompts no follow-up call."""
    body = (
        "# Forskrift\n\n"
        "| 2j | Helserelatert samtale om arbeid og jobbmestring for pasient "
        "som ikke blir sykmeldt. Ugyldig takstkombinasjon, 1, 2ak, 2ck, 2e | 50,- | 50,- |\n"
    )
    snippet = _snippet(body, body.index("jobbmestring"), len("jobbmestring"))

    assert snippet.startswith("| 2j |")
    assert "50,-" in snippet


def test_snippet_keeps_the_windowed_form_outside_tables() -> None:
    body = "x" * 200 + "NEEDLE" + "y" * 200
    snippet = _snippet(body, 200, len("NEEDLE"))

    assert snippet.startswith("...")
    assert snippet.endswith("...")
    assert len(snippet) < 120


def test_snippet_caps_a_pathologically_long_table_row() -> None:
    body = "| kode | " + "z" * 5000 + " NEEDLE |\n"
    snippet = _snippet(body, body.index("NEEDLE"), len("NEEDLE"))

    assert len(snippet) == _TABLE_ROW_SNIPPET_CHARS


def test_no_strong_match_notice_states_what_the_corpus_does_not_cover() -> None:
    """An empty result invites "there is no such rule". The corpus cannot
    support that claim: the fees a GP is paid for a sykmelding are set by
    L-takster in a NAV rundskriv under folketrygdloven § 21-4 — binding, in
    force, and in no dataset this server ingests.

    Pinned whole, not by substring: the wording is a safety instruction, and a
    substring check stays green against a message mutated around the fragment
    (mutants 7 in this function). The scope-note tail is asserted here via the
    constant and pinned to a literal in
    ``test_corpus_scope_note_is_pinned_verbatim``.
    """
    assert _no_strong_match_notice(0.25, 0.11) == (
        "no sections scored >= 0.25 for this query (best candidate scored 0.11). "
        "The corpus has no strong match — do NOT cite a law from memory. "
        "Tell the user no strong match was found, or retry with different "
        "wording, use search_body for exact keywords, or lower min_score. " + CORPUS_SCOPE_NOTE
    )
    # The best-is-None branch has its own phrasing that must stay pinned too.
    assert _no_strong_match_notice(0.30, None) == (
        "no sections scored >= 0.30 for this query (no candidates were scored). "
        "The corpus has no strong match — do NOT cite a law from memory. "
        "Tell the user no strong match was found, or retry with different "
        "wording, use search_body for exact keywords, or lower min_score. " + CORPUS_SCOPE_NOTE
    )


def test_corpus_status_reports_scope_alongside_freshness(tmp_path: Path) -> None:
    """Freshness and coverage are different questions, and confirming the first
    reads as confirming the second."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})

    status = CorpusReader(tmp_path).corpus_status()

    assert status["scope"] == CORPUS_SCOPE_NOTE
    assert status["notice"] != status["scope"]


def test_corpus_scope_note_is_pinned_verbatim() -> None:
    # The scope note is the anti-hallucination disclaimer on every empty
    # result. Asserting a runtime value == CORPUS_SCOPE_NOTE cannot catch a
    # mutation of the constant (both sides move together, so mutants 14-18
    # survive); pin it against an independent literal.
    assert CORPUS_SCOPE_NOTE == (
        "Corpus scope: acts (lover) and central regulations (forskrifter) from "
        "Lovdata's public data. It does NOT contain agency circulars "
        "(NAV/Helsedirektoratet rundskriv), Trygderetten or court decisions, "
        "forarbeider, or municipal regulations. A rule can be binding and absent "
        "here — an empty result is not evidence that no such rule exists."
    )


def test_prose_heading_becomes_a_block_not_a_fabricated_section() -> None:
    """Codex review of PR #144. The false positive had two victims: it invented a
    § 5 that no act contains, and it swallowed the content block that heading
    actually names. Both halves are asserted here."""
    body = "### § 5 og andre bestemmelser\n\nInnhold under overskriften.\n"

    sections = _parse_sections(body)

    assert [s["section_id"] for s in sections] == ["#5-og-andre-bestemmelser"]
    assert sections[0]["heading"] == "§ 5 og andre bestemmelser"
    assert "Innhold under overskriften." in sections[0]["body"]


def test_prose_heading_does_not_validate_a_citation_to_a_nonexistent_section(
    tmp_path: Path,
) -> None:
    """The consequence that matters: before the fix, the fabricated § 5 made a
    reference to a paragraph nobody wrote come back `valid: True`.

    It now comes back `None`, not `False`, and that is the two guards composing
    correctly rather than a weaker result. `### § 5 og andre bestemmelser` still
    LOOKS like a section heading to the completeness check, so this act's index
    is flagged incomplete and the validator refuses to assert absence either
    way. Declining to answer is the honest verdict when a §-shaped line in the
    act cannot be read; only the false claim was ever the bug."""
    body = (
        "## Kapittel 1.\n\n### § 1-1. Ekte\n\nSe § 5 for mer.\n\n"
        "### § 5 og andre bestemmelser\n\nTekst.\n"
    )
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="egen-lov", title="Egen lov")},
        body_for={"egen-lov": body},
    )

    section = CorpusReader(tmp_path).get_section("egen-lov", "1-1")

    assert section["cross_references"][0]["target_section_id"] == "5"
    assert section["cross_references"][0]["valid"] is None


def test_snippet_treats_a_pipe_line_inside_a_fence_as_a_table_row() -> None:
    """Codex flagged this edge case. Pinned rather than handled, with the reason:
    the corpus contains zero fenced code blocks and the renderer emits none — a
    line starting with `|` is a table row by construction. Returning the whole
    line would be a reasonable snippet either way; this test exists so the
    assumption fails loudly if the renderer ever learns to emit fences."""
    body = "```\n| not really a table NEEDLE |\n```\n"

    snippet = _snippet(body, body.index("NEEDLE"), len("NEEDLE"))

    assert snippet == "| not really a table NEEDLE |"


def test_semantic_search_hit_on_a_block_is_not_offered_as_a_paragraph_citation(
    tmp_path: Path,
) -> None:
    """Mutation survivor 480: nothing pinned the citation_hint for a content
    block, which is the one field standing between a caller and citing a takst
    table as a paragraph of law. `§ #takster-fra-1-juli-2026 <slug>` would be a
    paste-ready citation to a § that does not exist."""
    body = "## Kapittel II. Takster\n\n### Takster fra 1. juli 2026\n\n| 2j | Samtale | 50 |\n"
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="takstforskriften", title="Takstforskriften")},
        body_for={"takstforskriften": body},
    )
    _write_embedding_file(
        tmp_path,
        "lover",
        "takstforskriften",
        [("#takster-fra-1-juli-2026", [10, 0, 0])],
    )

    out = CorpusReader(tmp_path, embedder=_FakeEmbedder([1.0, 0.0, 0.0])).semantic_search("takst")

    hit = out["results"][0]
    assert hit["citation_hint"] == "takstforskriften > takster-fra-1-juli-2026"
    assert "§" not in hit["citation_hint"]
    assert hit["heading"] == "Takster fra 1. juli 2026"


def test_grounded_hit_on_an_act_that_left_the_manifest_degrades_to_bare_fields(
    tmp_path: Path,
) -> None:
    """Mutation survivors 449-454: the fallbacks in _grounded_hit for a hit whose
    slug no longer resolves — a stale embedding index pointing at an act that has
    since been repealed and dropped from the manifest.

    Driven directly rather than through semantic_search, because the index only
    loads .bin files for slugs the manifest still lists: the mismatch is a race
    between an index load and a corpus refresh, not a state semantic_search can
    be walked into. The fallbacks exist for that race, and until now nothing
    pinned what they fall back TO."""
    _seed_corpus(tmp_path, {"nl-1": _record(slug="levende-lov", title="Levende lov")})
    reader = CorpusReader(tmp_path)

    hit = reader._grounded_hit(SearchHit(slug="opphevet-lov", section_id="1-1", score=0.9), {})

    assert hit["slug"] == "opphevet-lov"
    assert hit["score"] == 0.9
    assert hit["citation_hint"] == "§ 1-1 opphevet-lov"
    assert hit["dataset"] == ""
    assert hit["title"] is None
    assert hit["heading"] is None
    assert hit["snippet"] is None
    assert hit["occurrence"] is None
    assert hit["ambiguous_section"] is False
    assert hit["last_changed"] is None


def test_snippet_returns_the_row_when_a_table_starts_the_document() -> None:
    """Mutation survivor 947: the row-start scan bounded rfind at index 0. Move
    that bound and a table opening the body is no longer recognised as a row —
    the caller silently gets a truncated window instead."""
    row = "| 2j | " + "x" * 300 + " NEEDLE | 50,- |"
    body = "\n" + row + "\n"

    snippet = _snippet(body, body.index("NEEDLE"), len("NEEDLE"))

    # The row must come back whole. A window would stop ~50 chars either side and
    # lose the fee — the exact failure the table branch exists to prevent.
    assert snippet == row
    assert not snippet.startswith("...")


def test_snippet_omits_the_ellipsis_when_the_window_covers_the_whole_body() -> None:
    """Mutants 961, 970, 972, 975, 976: the `...` markers tell the caller the
    snippet is a fragment. Nothing pinned the case where it is not one, so the
    boundary conditions and both else-branches were free to change."""
    body = "kort tekst NEEDLE her"

    snippet = _snippet(body, body.index("NEEDLE"), len("NEEDLE"))

    assert snippet == "kort tekst NEEDLE her"
    assert not snippet.startswith("...")
    assert not snippet.endswith("...")


def test_snippet_marks_a_fragment_that_starts_one_character_in() -> None:
    """Mutant 971: `start > 0` and `start > 1` differ only at start == 1."""
    body = "a" + "b" * _SNIPPET_CONTEXT_CHARS + "NEEDLE" + "c" * 200

    snippet = _snippet(body, body.index("NEEDLE"), len("NEEDLE"))

    assert snippet.startswith("...")


def test_snippet_collapses_whitespace_with_single_spaces() -> None:
    """Mutant 967: the join separator was unpinned because the existing window
    test had no whitespace in it to join."""
    body = "x" * 200 + "  to   ord NEEDLE fire\nord  " + "y" * 200

    snippet = _snippet(body, body.index("NEEDLE"), len("NEEDLE"))

    assert "  " not in snippet.strip(".")
    assert "to ord NEEDLE fire ord" in snippet


def test_grounded_hit_leaves_dataset_blank_when_the_manifest_key_is_unknown(
    tmp_path: Path,
) -> None:
    """Mutants 457/458: the fallback for a manifest record carrying a
    source_dataset this build does not know — a corpus written by a newer engine.
    The hit must still ground, with the dataset field empty rather than guessed."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="rar-lov", title="Rar lov", source_dataset="fremtidig-datasett")},
        body_for={"rar-lov": "### § 1-1. Tittel\n\nTekst.\n"},
    )
    reader = CorpusReader(tmp_path)

    hit = reader._grounded_hit(SearchHit(slug="rar-lov", section_id="1-1", score=0.8), {})

    assert hit["dataset"] == ""
    assert hit["title"] == "Rar lov"
    assert hit["heading"] == "§ 1-1. Tittel"


def test_list_sections_seeds_the_cross_reference_cache_under_the_act_slug(
    tmp_path: Path,
) -> None:
    """Mutants 142/143: list_sections documents the section-id set as "seeded
    into the cross-reference cache as a free by-product", but nothing checked
    the key it lands under. Seeded under the wrong key the cache silently never
    hits — same answers, every act re-parsed, and no test would notice."""
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="skatteloven", title="Skatteloven")},
        body_for={"skatteloven": _SAMPLE_BODY_WITH_SECTIONS},
    )
    reader = CorpusReader(tmp_path)

    reader.list_sections("skatteloven")

    assert set(reader._section_ids_cache) == {"skatteloven"}
    assert "1-1" in reader._section_ids_cache["skatteloven"]
