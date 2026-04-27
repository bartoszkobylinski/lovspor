"""Unit tests for lovspor.mcp.

Cover the CorpusReader business logic (filtering, sorting, lookups,
error paths) without exercising the MCP wire protocol — that belongs
to Anthropic's SDK and isn't ours to test. The build_server / FastMCP
glue is tested by constructing the server instance and verifying the
four expected tool names are registered.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.mcp import (
    CorpusNotFoundError,
    CorpusReader,
    build_server,
)
from lovspor.storage.manifest import Manifest, ManifestRecord, write_manifest


def _record(
    *,
    slug: str,
    title: str,
    source_dataset: str = "gjeldende-lover",
    status: str = "current",
    last_changed: str | None = None,
    total_changes: int | None = None,
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
    )


def _seed_corpus(
    root: Path,
    records: dict[str, ManifestRecord],
    *,
    write_files: bool = True,
    write_history_for: list[str] | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    write_manifest(
        Manifest(generated_at=datetime(2026, 4, 27, tzinfo=UTC), documents=records),
        root / "manifest.json",
    )
    if write_files:
        for record in records.values():
            doc_path = root / record.markdown_path
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path.write_text(
                f"---\nid: x\ntitle: {record.title}\n---\n\n# {record.title}\n",
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


def test_get_law_returns_file_content(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    out = CorpusReader(tmp_path).get_law("skatteloven")
    assert "# Skatteloven" in out


def test_get_law_raises_for_unknown_slug(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    with pytest.raises(CorpusNotFoundError, match="no current law"):
        CorpusReader(tmp_path).get_law("does-not-exist")


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
    with pytest.raises(CorpusNotFoundError, match="file is missing"):
        CorpusReader(tmp_path).get_law("x")


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
    with pytest.raises(CorpusNotFoundError, match="history file missing"):
        CorpusReader(tmp_path).get_law_history("x")


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
    with pytest.raises(ValueError, match="limit must be non-negative"):
        CorpusReader(tmp_path).list_recent_changes(limit=-1)


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
    with pytest.raises(ValueError, match="ISO date YYYY-MM-DD"):
        CorpusReader(tmp_path).list_recent_changes(since="not a date")


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


# ---------- build_server ----------


def test_build_server_registers_four_tools(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    server = build_server(tmp_path)
    # FastMCP exposes registered tools via list_tools(); the wrapper is
    # async, so we verify by checking the internal registration count
    # via the underlying tool manager.
    tool_names = sorted(server._tool_manager._tools.keys())
    assert tool_names == sorted(
        ["get_law", "get_law_history", "list_recent_changes", "search_laws"],
    )


def test_build_server_raises_eagerly_on_bad_corpus(tmp_path: Path) -> None:
    """Misconfigured corpus path fails at server startup, not first call."""
    with pytest.raises(CorpusNotFoundError):
        build_server(tmp_path / "nonexistent")
