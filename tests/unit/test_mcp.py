"""Unit tests for lovspor.mcp.

Cover the CorpusReader business logic (filtering, sorting, lookups,
error paths) without exercising the MCP wire protocol — that belongs
to Anthropic's SDK and isn't ours to test. The build_server / FastMCP
glue is tested by constructing the server instance and verifying the
four expected tool names are registered.
"""

import json
import shlex
import subprocess
from datetime import UTC, datetime, timedelta
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
    with pytest.raises(ValueError, match="limit must be non-negative"):
        CorpusReader(tmp_path).search_body("anything", limit=-1)


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
    section = CorpusReader(tmp_path).get_section("skatteloven", "5-12")
    assert section["slug"] == "skatteloven"
    assert section["section_id"] == "5-12"
    assert section["heading"] == "§ 5-12. Boligsparing for ungdom"
    assert section["parent_chapter"] == "Kapittel 5. Alminnelig inntekt og fradragene"
    assert "Skattefradraget gis for sparing til bolig" in section["body"]
    assert "Fradraget reduseres ved utbetaling" in section["body"]


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
    assert "5-99" in msg
    assert "skatteloven" in msg
    assert "§ 1-1" in msg
    assert "§ 5-12" in msg


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


# ---------- corpus_status ----------


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _git_init_corpus(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)


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
    assert "pre-Sprint-4" in status["notice"]
    assert "1 of 1" in status["notice"]
    assert "git -C" in status["notice"]


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
    assert "future" in status["notice"]
    assert "clock-skew" in status["notice"]


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


def test_build_server_registers_seven_tools(tmp_path: Path) -> None:
    _seed_corpus(tmp_path, {"nl-1": _record(slug="x", title="X")})
    server = build_server(tmp_path)
    # FastMCP exposes registered tools via list_tools(); the wrapper is
    # async, so we verify by checking the internal registration count
    # via the underlying tool manager.
    tool_names = sorted(server._tool_manager._tools.keys())
    assert tool_names == sorted(
        [
            "get_law",
            "get_section",
            "get_law_history",
            "list_recent_changes",
            "search_laws",
            "search_body",
            "corpus_status",
        ],
    )


def test_build_server_raises_eagerly_on_bad_corpus(tmp_path: Path) -> None:
    """Misconfigured corpus path fails at server startup, not first call."""
    with pytest.raises(CorpusNotFoundError):
        build_server(tmp_path / "nonexistent")
