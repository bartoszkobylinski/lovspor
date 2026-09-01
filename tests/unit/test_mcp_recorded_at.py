"""Tests for the recorded_at historical state view (ADR-0011).

Covers the ADR's Validation criteria at the reader layer: state
resolution and evidence fields, bundle consistency, snapshot closure
(cross-references validated in-state), the outcome taxonomy (boundary vs
historical negative), conditional ``xml_hash``, and the ``search_body``
envelope.
"""

import difflib
import inspect
import json
import os
import subprocess
from datetime import UTC, date, datetime
from datetime import time as dt_time
from pathlib import Path

import pytest

import lovspor.mcp as mcp_module
from lovspor.mcp import (
    CorpusNotFoundError,
    CorpusReader,
    _citation_hint_from,
    _citation_verdict,
    _parse_recorded_at,
    build_server,
)
from lovspor.snapshot import HistoryBoundaryError, StateIntegrityError
from lovspor.timetravel import _RevisionEntry

# ---------- fixture: a two-state corpus ----------


def _run_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
    )


def _commit_all(repo: Path, message: str, iso_date: str) -> str:
    _run_git(repo, "add", "-A")
    stamp = {"GIT_AUTHOR_DATE": iso_date, "GIT_COMMITTER_DATE": iso_date}
    _run_git(repo, "commit", "-m", message, env=stamp)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _record(slug: str, xml_hash: str) -> dict[str, object]:
    return {
        "doc_type": "lov",
        "xml_hash": xml_hash,
        "markdown_path": f"lover/{slug}.md",
        "source_dataset": "gjeldende-lover",
        "last_seen": "2026-05-01T04:00:00Z",
        "status": "current",
        "slug": slug,
        "title": slug.capitalize(),
    }


def _manifest(records: dict[str, dict[str, object]]) -> str:
    return json.dumps(
        {"version": 1, "generated_at": "2026-05-01T04:00:00Z", "documents": records},
    )


def _doc(title: str, sections: str) -> str:
    return f"---\ntitle: {title}\n---\n\n# {title}\n\n{sections}"


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, str, str]:
    """State 1 (2026-05-01): grunnloven-grl §1-1 -> testloven §9-9.
    State 2 (2026-05-10): testloven's §9-9 renamed to §9-8, nyloven added,
    grunnloven-grl body rewritten."""
    repo = tmp_path / "corpus"
    (repo / "lover").mkdir(parents=True)
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "lover" / "grunnloven-grl.md").write_text(
        _doc(
            "Grunnloven",
            "## Kapittel 1.\n\n### § 1-1. Formål\n\n"
            "Grunnloven v1 tekst, jf. testloven § 9-9. Se også § 1-1.\n",
        ),
    )
    (repo / "lover" / "testloven.md").write_text(
        _doc("Testloven", "## Kapittel 9.\n\n### § 9-9. Regel\n\nTestloven v1 tekst.\n"),
    )
    (repo / "manifest.json").write_text(
        _manifest(
            {
                "doc-a": _record("grunnloven-grl", "hash-a1"),
                "doc-b": _record("testloven", "hash-b1"),
            },
        ),
    )
    sha1 = _commit_all(repo, "sync 1", "2026-05-01T12:00:00Z")
    (repo / "lover" / "grunnloven-grl.md").write_text(
        _doc(
            "Grunnloven",
            "## Kapittel 1.\n\n### § 1-1. Formål\n\n"
            "Grunnloven v2 tekst, jf. testloven § 9-9. Se også § 1-1.\n",
        ),
    )
    (repo / "lover" / "testloven.md").write_text(
        _doc("Testloven", "## Kapittel 9.\n\n### § 9-8. Regel\n\nTestloven v2 tekst.\n"),
    )
    (repo / "lover" / "nyloven.md").write_text(
        _doc("Nyloven", "## Kapittel 1.\n\n### § 1. Ny regel\n\nNyloven tekst.\n"),
    )
    (repo / "manifest.json").write_text(
        _manifest(
            {
                "doc-a": _record("grunnloven-grl", "hash-a2"),
                "doc-b": _record("testloven", "hash-b2"),
                "doc-c": _record("nyloven", "hash-c1"),
            },
        ),
    )
    sha2 = _commit_all(repo, "sync 2", "2026-05-10T12:00:00Z")
    return repo, sha1, sha2


@pytest.fixture
def reader(corpus: tuple[Path, str, str]) -> CorpusReader:
    repo, _, _ = corpus
    return CorpusReader(repo)


# ---------- evidence fields and state resolution ----------


def test_historical_get_section_serves_state_body_with_evidence(
    reader: CorpusReader,
    corpus: tuple[Path, str, str],
) -> None:
    _, sha1, _ = corpus

    result = reader.at_state("2026-05-05").get_section("grunnloven-grl", "1-1")

    assert "Grunnloven v1 tekst" in result["body"]
    assert result["corpus_commit"] == sha1
    assert result["recorded_at"] == "2026-05-05"
    assert result["xml_hash"] == "hash-a1"


def test_bundle_members_share_one_resolved_corpus_commit(
    reader: CorpusReader,
    corpus: tuple[Path, str, str],
) -> None:
    _, sha1, _ = corpus
    state = reader.at_state("2026-05-05")

    section = state.get_section("grunnloven-grl", "1-1")
    other = state.get_section("testloven", "9-9")
    search = state.search_body("tekst")

    assert {section["corpus_commit"], other["corpus_commit"], search["corpus_commit"]} == {sha1}


def test_two_docs_last_touched_on_different_days_share_the_global_commit(
    reader: CorpusReader,
    corpus: tuple[Path, str, str],
) -> None:
    # ADR-0011 point 4: corpus_commit is the global state commit, never
    # the document's own last-touching revision.
    _, _, sha2 = corpus
    state = reader.at_state("2026-05-15")

    assert state.get_section("grunnloven-grl", "1-1")["corpus_commit"] == sha2
    assert state.get_section("nyloven", "1")["corpus_commit"] == sha2


# ---------- snapshot closure ----------


def test_cross_reference_validates_against_the_state_not_head(
    reader: CorpusReader,
) -> None:
    # testloven § 9-9 exists at state 1 but was renamed to § 9-8 by HEAD.
    # A leak to the current index would flag the historical ref invalid.
    historical = reader.at_state("2026-05-05").get_section("grunnloven-grl", "1-1")
    current = reader.get_section("grunnloven-grl", "1-1")

    historical_ref = historical["cross_references"][0]
    current_ref = current["cross_references"][0]
    assert historical_ref["target_slug"] == "testloven"
    assert historical_ref["valid"] is True
    assert current_ref["valid"] is False


# ---------- outcome taxonomy ----------


def test_document_added_after_date_is_a_historical_negative(
    reader: CorpusReader,
    corpus: tuple[Path, str, str],
) -> None:
    _, sha1, _ = corpus

    with pytest.raises(CorpusNotFoundError) as excinfo:
        reader.at_state("2026-05-05").get_section("nyloven", "1")

    message = str(excinfo.value)
    assert "corpus state at 2026-05-05" in message
    assert sha1 in message
    assert "get_law_history" in message


def test_pre_history_date_raises_boundary_error(reader: CorpusReader) -> None:
    with pytest.raises(HistoryBoundaryError) as excinfo:
        reader.at_state("2026-04-01")

    assert "2026-05-01" in str(excinfo.value)


def test_future_recorded_at_is_refused() -> None:
    tomorrow = datetime.now(UTC).date().toordinal() + 1

    with pytest.raises(ValueError, match="in the future"):
        _parse_recorded_at(date.fromordinal(tomorrow).isoformat())


def test_malformed_recorded_at_is_refused() -> None:
    with pytest.raises(ValueError, match="ISO date"):
        _parse_recorded_at("05/01/2026")


# ---------- validate_citation against a state ----------


def test_citation_of_document_added_later_is_invalid_in_the_state(
    reader: CorpusReader,
    corpus: tuple[Path, str, str],
) -> None:
    _, sha1, _ = corpus

    verdict = reader.at_state("2026-05-05").validate_citation("nyloven § 1")

    assert verdict["valid"] is False
    assert verdict["corpus_commit"] == sha1
    # A corpus statement about the snapshot, never a boundary outcome.
    assert verdict["recorded_at"] == "2026-05-05"


def test_citation_valid_in_state_where_section_existed(reader: CorpusReader) -> None:
    verdict = reader.at_state("2026-05-05").validate_citation("testloven § 9-9")

    assert verdict["valid"] is True
    assert verdict["section_id"] == "9-9"
    current = reader.validate_citation("testloven § 9-9")
    assert current["valid"] is False


# ---------- verify_quote against a state ----------


def test_quote_verified_in_its_state_and_not_at_head(
    reader: CorpusReader,
) -> None:
    state = reader.at_state("2026-05-05")

    verdict = state.verify_quote("grunnloven-grl", "1-1", "Grunnloven v1 tekst")
    current = reader.verify_quote("grunnloven-grl", "1-1", "Grunnloven v1 tekst")

    assert verdict["verified"] is True
    assert verdict["xml_hash"] == "hash-a1"
    assert current["verified"] is False


def test_quote_failure_without_resolved_document_carries_no_xml_hash(
    reader: CorpusReader,
    corpus: tuple[Path, str, str],
) -> None:
    _, sha1, _ = corpus

    verdict = reader.at_state("2026-05-05").verify_quote("nyloven", "1", "Nyloven tekst")

    assert verdict["verified"] is False
    assert verdict["corpus_commit"] == sha1
    assert "xml_hash" not in verdict


# ---------- search_body envelope ----------


def test_empty_search_still_carries_the_evidence_stamp(
    reader: CorpusReader,
    corpus: tuple[Path, str, str],
) -> None:
    _, sha1, _ = corpus

    envelope = reader.at_state("2026-05-05").search_body("finnes ikke i korpuset")

    assert envelope == {
        "recorded_at": "2026-05-05",
        "corpus_commit": sha1,
        "results": [],
    }


def test_search_finds_state_text_not_head_text(reader: CorpusReader) -> None:
    envelope = reader.at_state("2026-05-05").search_body("Testloven v1 tekst")

    slugs = [hit["slug"] for hit in envelope["results"]]
    assert slugs == ["testloven"]
    assert reader.search_body("Testloven v1 tekst") == []


# ---------- MCP tool layer (ADR-0011 point 7: T0 composition) ----------


def _tool_fn(repo: Path, name: str):

    return build_server(repo)._tool_manager._tools[name].fn


def test_historical_tool_response_carries_not_evaluated_notice(
    corpus: tuple[Path, str, str],
) -> None:
    repo, sha1, _ = corpus

    result = _tool_fn(repo, "get_section")(
        slug="grunnloven-grl",
        section_id="1-1",
        recorded_at="2026-05-05",
    )

    assert result["corpus_commit"] == sha1
    assert result["temporal_notice"]["status"] == "not_evaluated"
    assert "recorded_at" in result["temporal_notice"]["reason"]


def test_not_evaluated_notices_are_not_one_shared_object(
    corpus: tuple[Path, str, str],
) -> None:
    repo, _, _ = corpus
    fn = _tool_fn(repo, "get_section")

    first = fn(slug="grunnloven-grl", section_id="1-1", recorded_at="2026-05-05")
    second = fn(slug="testloven", section_id="9-9", recorded_at="2026-05-05")

    assert first["temporal_notice"] is not second["temporal_notice"]


def test_recorded_at_is_never_used_as_the_evaluation_date(
    corpus: tuple[Path, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ADR-0011 point 7: recorded_at must not reach build_notice, directly
    # or by default. The live path still evaluates at today's date.
    repo, _, _ = corpus
    calls: list[date] = []
    original = mcp_module.build_notice

    def spy(body: str, evaluation_date: date, **kwargs: object):
        calls.append(evaluation_date)
        return original(body, evaluation_date, **kwargs)

    monkeypatch.setattr(mcp_module, "build_notice", spy)
    fn = _tool_fn(repo, "get_section")

    fn(slug="grunnloven-grl", section_id="1-1", recorded_at="2026-05-05")
    assert calls == []

    fn(slug="grunnloven-grl", section_id="1-1")
    # The live path evaluates at the Norwegian calendar day, exactly as
    # before this change — assert against the helper itself, not against
    # UTC, which diverges from Europe/Oslo around midnight.
    assert calls == [mcp_module.evaluation_date_today()]


def test_search_body_tool_shape_is_conditional_on_recorded_at(
    corpus: tuple[Path, str, str],
) -> None:
    repo, sha1, _ = corpus
    fn = _tool_fn(repo, "search_body")

    live = fn(query="tekst")
    historical = fn(query="tekst", recorded_at="2026-05-05")

    assert isinstance(live, list)
    assert historical["corpus_commit"] == sha1
    assert [hit["slug"] for hit in historical["results"]] == [
        hit["slug"] for hit in live if hit["slug"] != "nyloven"
    ]


def test_live_tool_responses_carry_no_state_evidence_fields(
    corpus: tuple[Path, str, str],
) -> None:
    # Additivity: without recorded_at the response shape is unchanged —
    # no corpus_commit, no recorded_at echo.
    repo, _, _ = corpus

    result = _tool_fn(repo, "get_section")(slug="grunnloven-grl", section_id="1-1")

    assert "corpus_commit" not in result
    assert "recorded_at" not in result
    assert "temporal_notice" in result


# ---------- rename lineage (ADR-0011 point 5, second half) ----------


@pytest.fixture
def corpus_with_rename(corpus: tuple[Path, str, str]) -> tuple[Path, str, str, str]:
    """State 3 (2026-05-20): testloven renamed to testloven-tl (git mv +
    manifest slug change) — the real rename fixture the ADR requires."""
    repo, sha1, sha2 = corpus
    _run_git(repo, "mv", "lover/testloven.md", "lover/testloven-tl.md")
    (repo / "manifest.json").write_text(
        _manifest(
            {
                "doc-a": _record("grunnloven-grl", "hash-a2"),
                "doc-b": {**_record("testloven-tl", "hash-b3")},
                "doc-c": _record("nyloven", "hash-c1"),
            },
        ),
    )
    sha3 = _commit_all(repo, "sync 3: rename", "2026-05-20T12:00:00Z")
    return repo, sha1, sha2, sha3


def test_current_slug_maps_through_lineage_to_the_state_slug(
    corpus_with_rename: tuple[Path, str, str, str],
) -> None:
    repo, sha1, _, _ = corpus_with_rename

    result = CorpusReader(repo).at_state("2026-05-05").get_section("testloven-tl", "9-9")

    assert result["slug"] == "testloven"
    assert result["mapped_from_slug"] == "testloven-tl"
    assert "Testloven v1 tekst" in result["body"]
    assert result["corpus_commit"] == sha1
    assert result["xml_hash"] == "hash-b1"


def test_mapping_is_reported_only_when_applied(
    corpus_with_rename: tuple[Path, str, str, str],
) -> None:
    repo, _, _, _ = corpus_with_rename

    result = CorpusReader(repo).at_state("2026-05-05").get_section("testloven", "9-9")

    assert "mapped_from_slug" not in result


def test_verify_quote_resolves_through_the_same_lineage(
    corpus_with_rename: tuple[Path, str, str, str],
) -> None:
    repo, _, _, _ = corpus_with_rename

    verdict = (
        CorpusReader(repo)
        .at_state("2026-05-05")
        .verify_quote("testloven-tl", "9-9", "Testloven v1 tekst")
    )

    assert verdict["verified"] is True
    assert verdict["xml_hash"] == "hash-b1"


def test_unmappable_current_slug_stays_a_historical_negative(
    corpus_with_rename: tuple[Path, str, str, str],
) -> None:
    # nyloven exists now but its lineage does not reach 2026-05-05: the
    # answer is the in-state negative, never a fallback to today's docs.
    repo, _, _, _ = corpus_with_rename

    with pytest.raises(CorpusNotFoundError, match="corpus state at 2026-05-05"):
        CorpusReader(repo).at_state("2026-05-05").get_section("nyloven", "1")


# ---------- state integrity (ADR-0011 point 6 outcome 2) ----------


@pytest.fixture
def inconsistent_corpus(tmp_path: Path) -> Path:
    """A committed state whose manifest lists a current document with no
    committed file behind it."""
    repo = tmp_path / "corpus"
    (repo / "lover").mkdir(parents=True)
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "lover" / "ekteloven.md").write_text(
        _doc("Ekteloven", "## Kapittel 1.\n\n### § 1. Regel\n\nEkte tekst.\n"),
    )
    (repo / "manifest.json").write_text(
        _manifest(
            {
                "doc-real": _record("ekteloven", "hash-e1"),
                "doc-ghost": _record("spokelsesloven", "hash-g1"),
            },
        ),
    )
    _commit_all(repo, "sync with ghost", "2026-05-01T12:00:00Z")
    return repo


def test_manifest_record_without_committed_body_is_an_integrity_error(
    inconsistent_corpus: Path,
) -> None:
    state = CorpusReader(inconsistent_corpus).at_state("2026-05-05")

    with pytest.raises(StateIntegrityError, match="not historical absence"):
        state.get_section("spokelsesloven", "1")


def test_historical_search_refuses_an_inconsistent_state(
    inconsistent_corpus: Path,
) -> None:
    state = CorpusReader(inconsistent_corpus).at_state("2026-05-05")

    with pytest.raises(StateIntegrityError, match="spokelsesloven"):
        state.search_body("tekst")


def test_historical_search_never_follows_manifest_referenced_symlink(
    corpus: tuple[Path, str, str],
) -> None:
    """Archive members are read only when they are regular files.

    A committed symlink at a manifest path must be treated as corrupt state,
    not followed to another archive member (or to a path outside the tree).
    """
    repo, _, _ = corpus
    body_path = repo / "lover" / "testloven.md"
    body_path.unlink()
    body_path.symlink_to("grunnloven-grl.md")
    (repo / "manifest.json").touch()
    _commit_all(repo, "sync with unsafe body link", "2026-05-20T12:00:00Z")

    with pytest.raises(StateIntegrityError, match="testloven"):
        CorpusReader(repo).at_state("2026-05-20").search_body("tekst")


def test_historical_search_fails_loudly_on_malformed_utf8(
    corpus: tuple[Path, str, str],
) -> None:
    repo, _, _ = corpus
    (repo / "lover" / "testloven.md").write_bytes(b"---\n---\n\n# Test\n\n\xff\n")
    (repo / "manifest.json").touch()
    _commit_all(repo, "sync with malformed utf8", "2026-05-20T12:00:00Z")

    with pytest.raises(UnicodeDecodeError):
        CorpusReader(repo).at_state("2026-05-20").search_body("tekst")


@pytest.mark.parametrize(
    "value",
    [
        "20260501",  # compact ISO — fromisoformat accepts it, the contract does not
        "2026-W18-5",  # ISO week date — same
        "2026-05-01T00:00",  # datetime, not a calendar date
        "2026-5-1",  # unpadded
        "01-05-2026",  # wrong field order
    ],
)
def test_recorded_at_rejects_non_canonical_iso_forms(value: str) -> None:
    # The contract names exactly one wire form (YYYY-MM-DD); runtime must
    # not accept the wider ISO-8601 grammar date.fromisoformat knows.
    with pytest.raises(ValueError, match="ISO date YYYY-MM-DD"):
        _parse_recorded_at(value)


def test_recorded_at_still_rejects_impossible_calendar_dates() -> None:
    # The lexical gate must not replace calendar validation.
    with pytest.raises(ValueError, match="ISO date YYYY-MM-DD"):
        _parse_recorded_at("2026-02-30")


def test_validate_citation_resolves_through_the_same_lineage(
    corpus_with_rename: tuple[Path, str, str, str],
) -> None:
    # Authored by the independent codex-tests round on 41066fc: the three
    # guards must agree — a current-slug citation resolves through the
    # same unambiguous lineage as get_section and verify_quote.
    repo, _, _, _ = corpus_with_rename

    verdict = CorpusReader(repo).at_state("2026-05-05").validate_citation("testloven-tl § 9-9")

    assert verdict["valid"] is True
    assert verdict["slug"] == "testloven"
    assert verdict["section_id"] == "9-9"
    assert verdict["mapped_from_slug"] == "testloven-tl"


def test_citation_lineage_never_overrides_the_state_namespace(
    corpus_with_rename: tuple[Path, str, str, str],
) -> None:
    # A slug the state itself knows resolves directly — no alias, no
    # mapping field — and an unmappable current slug stays invalid.
    repo, _, _, _ = corpus_with_rename
    state = CorpusReader(repo).at_state("2026-05-05")

    direct = state.validate_citation("testloven § 9-9")
    unmappable = state.validate_citation("nyloven § 1")

    assert direct["valid"] is True
    assert "mapped_from_slug" not in direct
    assert unmappable["valid"] is False


# ---------- mutation-survivor kills (needs-human:mutation, PR #222) ----------


def test_states_for_dates_resolving_to_one_commit_share_caches(
    reader: CorpusReader,
    corpus: tuple[Path, str, str],
) -> None:
    # The per-commit cache is the whole point of _SnapshotData: two dates
    # resolving to one commit must share it, not rebuild it.
    _, sha1, _ = corpus

    a = reader.at_state("2026-05-05")
    b = reader.at_state("2026-05-06")

    assert a.corpus_commit == b.corpus_commit == sha1
    assert a._data is b._data


def test_snapshot_state_cache_is_bounded_fifo(reader: CorpusReader) -> None:
    reader._snapshot_states.clear()
    for i in range(4):
        reader._snapshot_states[f"k{i}"] = object()  # type: ignore[assignment]

    state = reader.at_state("2026-05-05")

    assert len(reader._snapshot_states) == 4
    assert "k0" not in reader._snapshot_states
    assert state.corpus_commit in reader._snapshot_states


def test_lineage_cutoff_is_end_of_day_inclusive(
    reader: CorpusReader,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime.combine(date(2026, 5, 5), dt_time.max).replace(tzinfo=UTC)
    entry = _RevisionEntry("sha", cutoff, "lover/x.md")
    monkeypatch.setattr(mcp_module, "_iter_follow_log", lambda *_: [entry])

    assert reader._lineage_path_at("grunnloven-grl", date(2026, 5, 5)) == "lover/x.md"


def test_parse_recorded_at_evaluates_today_in_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []
    real = datetime

    class _SpyDateTime:
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            seen.append(tz)
            return real.now(UTC)

    monkeypatch.setattr(mcp_module, "datetime", _SpyDateTime)

    _parse_recorded_at("2020-01-01")

    assert seen == [UTC]


def test_citation_core_prefers_the_longest_matching_slug() -> None:
    def resolve(_slug: str, section_id: str) -> tuple[str, dict[str, object], None]:
        return section_id, {"heading": "h"}, None

    verdict = _citation_verdict("a-lov lov § 1", {"a-lov", "lov"}, resolve)

    # "lov" wins a plain lexicographic max; the longest token must win.
    assert verdict["slug"] == "a-lov"


def test_citation_core_keeps_an_empty_injected_reason_empty() -> None:
    verdict = _citation_verdict(
        "testloven § 9-9",
        {"testloven"},
        lambda _slug, section_id: (section_id, None, None),
    )

    assert verdict["valid"] is False
    assert verdict["reason"] == ""


def test_citation_hint_caps_at_three_suggestions() -> None:
    slugs = ["lova1", "lova2", "lova3", "lovb1", "lovb2", "lovb3"]
    hint = _citation_hint_from(slugs, "lova9 lovb9 § 5")

    listed = hint.split("did you mean ")[1].split("?")[0].split(", ")
    assert len(listed) == 3


def test_assumption_difflib_close_matches_defaults() -> None:
    # Pins the stdlib defaults two registered equivalent mutants argue from
    # (mutation-equivalents.toml, issue #132): dropping n=3 or cutoff=0.6
    # from a get_close_matches call changes nothing ONLY while these hold.
    sig = inspect.signature(difflib.get_close_matches)
    assert sig.parameters["n"].default == 3
    assert sig.parameters["cutoff"].default == 0.6


def test_historical_unknown_section_names_the_state_slug(reader: CorpusReader) -> None:
    with pytest.raises(CorpusNotFoundError, match="in 'grunnloven-grl'"):
        reader.at_state("2026-05-05").get_section("grunnloven-grl", "9-99")


def test_historical_self_reference_targets_the_acts_own_slug(reader: CorpusReader) -> None:
    result = reader.at_state("2026-05-05").get_section("grunnloven-grl", "1-1")

    self_refs = [ref for ref in result["cross_references"] if ref["target_section_id"] == "1-1"]
    assert self_refs
    assert self_refs[0]["target_slug"] == "grunnloven-grl"
    assert self_refs[0]["valid"] is True
    assert result["section_id"] == "1-1"


def test_live_same_act_reference_survives_the_section_id_cache(
    reader: CorpusReader,
) -> None:
    # The live path seeds the per-act section-id cache from get_section's
    # own parse; a poisoned seed breaks same-act cross-references on the
    # cached read.
    reader.get_section("grunnloven-grl", "1-1")
    second = reader.get_section("grunnloven-grl", "1-1")

    self_refs = [ref for ref in second["cross_references"] if ref["target_section_id"] == "1-1"]
    assert self_refs
    assert self_refs[0]["target_slug"] == "grunnloven-grl"
    assert self_refs[0]["valid"] is True


def test_state_slug_citation_never_gains_a_lineage_mapping(
    corpus_with_rename: tuple[Path, str, str, str],
) -> None:
    repo, _, _, _ = corpus_with_rename

    verdict = CorpusReader(repo).at_state("2026-05-05").validate_citation("grunnloven-grl § 1-1")

    assert verdict["valid"] is True
    assert "mapped_from_slug" not in verdict


def test_historical_archive_failure_is_loud(
    reader: CorpusReader,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A failing `git archive` must surface as the subprocess error the
    # check= contract promises — never limp on into tar parsing.
    real_run = mcp_module.subprocess.run

    def fake_run(cmd: list[str], **kwargs: object):
        if cmd[:2] == ["git", "archive"]:
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(128, cmd)
            return subprocess.CompletedProcess(cmd, 128, stdout=b"", stderr=b"")
        return real_run(cmd, **kwargs)  # type: ignore[arg-type]

    state = reader.at_state("2026-05-05")
    monkeypatch.setattr(mcp_module.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        state.search_body("tekst")


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "lover").mkdir(exist_ok=True)
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")


@pytest.fixture
def aux_corpus(tmp_path: Path) -> Path:
    """Micro corpus for message/limit kills: two near-identical slugs, one
    duplicate-§ act, and 25 docs sharing a common word."""
    repo = tmp_path / "corpus"
    _init_repo(repo)
    records: dict[str, dict[str, object]] = {}
    for slug in ("aloven", "alovene"):
        (repo / "lover" / f"{slug}.md").write_text(
            _doc(slug.capitalize(), "## Kapittel 1.\n\n### § 1. Regel\n\nTekst.\n"),
        )
        records[f"doc-{slug}"] = _record(slug, f"hash-{slug}")
    (repo / "lover" / "dobbeltloven.md").write_text(
        _doc(
            "Dobbeltloven",
            "## Kapittel 1.\n\n### § 1. Regel\n\nførste variant.\n\n"
            "### § 1. Regel\n\nandre variant.\n",
        ),
    )
    records["doc-dobbelt"] = _record("dobbeltloven", "hash-dobbelt")
    for i in range(25):
        slug = f"treff{i:02d}loven"
        (repo / "lover" / f"{slug}.md").write_text(
            _doc(slug, f"## Kapittel 1.\n\n### § 1. Regel\n\nfellesord nummer {i}.\n"),
        )
        records[f"doc-{slug}"] = _record(slug, f"hash-{i}")
    (repo / "manifest.json").write_text(_manifest(records))
    _commit_all(repo, "sync", "2026-05-01T12:00:00Z")
    return repo


def _ghost_corpus(tmp_path: Path, ghosts: int) -> Path:
    repo = tmp_path / "corpus"
    _init_repo(repo)
    (repo / "lover" / "ekteloven.md").write_text(
        _doc("Ekteloven", "## Kapittel 1.\n\n### § 1. Regel\n\nEkte tekst.\n"),
    )
    records = {"doc-real": _record("ekteloven", "hash-e1")}
    for i in range(1, ghosts + 1):
        records[f"doc-g{i}"] = _record(f"ghost{i}loven", f"hash-g{i}")
    (repo / "manifest.json").write_text(_manifest(records))
    _commit_all(repo, "sync with ghosts", "2026-05-01T12:00:00Z")
    return repo


def test_unknown_slug_hint_joins_suggestions_exactly(aux_corpus: Path) -> None:
    state = CorpusReader(aux_corpus).at_state("2026-05-05")

    with pytest.raises(CorpusNotFoundError) as excinfo:
        state.get_section("alovenx", "1")

    message = str(excinfo.value)
    listed = message.split("did you mean ")[1].split("? ", maxsplit=1)[0]
    assert set(listed.split(", ")) == {"aloven", "alovene"}


def test_unknown_slug_without_near_miss_has_no_hint_filler(aux_corpus: Path) -> None:
    state = CorpusReader(aux_corpus).at_state("2026-05-05")

    with pytest.raises(CorpusNotFoundError) as excinfo:
        state.get_section("zzz-qqq-www", "1")

    message = str(excinfo.value)
    assert "did you mean" not in message
    assert "no law with slug 'zzz-qqq-www' in the corpus state at " in message
    # With no suggestions the hint is EMPTY — the recovery sentence follows
    # the corpus_commit directly, with no filler between them.
    assert "); the document may have entered the corpus later" in message


def test_historical_occurrence_selects_the_duplicate_section(aux_corpus: Path) -> None:
    state = CorpusReader(aux_corpus).at_state("2026-05-05")

    section = state.get_section("dobbeltloven", "1", occurrence=2)
    verdict = state.verify_quote("dobbeltloven", "1", "andre variant", occurrence=2)

    assert "andre variant" in section["body"]
    assert verdict["verified"] is True


def test_historical_search_respects_the_result_limit(aux_corpus: Path) -> None:
    state = CorpusReader(aux_corpus).at_state("2026-05-05")

    envelope = state.search_body("fellesord")

    assert len(envelope["results"]) == 20


def test_historical_search_respects_the_dataset_filter(aux_corpus: Path) -> None:
    state = CorpusReader(aux_corpus).at_state("2026-05-05")

    envelope = state.search_body("fellesord", dataset="forskrifter")

    assert envelope["results"] == []


def test_integrity_error_names_five_missing_docs_then_elides(tmp_path: Path) -> None:
    state = CorpusReader(_ghost_corpus(tmp_path, 7)).at_state("2026-05-05")

    with pytest.raises(StateIntegrityError) as excinfo:
        state.search_body("tekst")

    shown = str(excinfo.value).split("tree: ")[1].split(" — ")[0]
    assert shown == ("ghost1loven, ghost2loven, ghost3loven, ghost4loven, ghost5loven …")


def test_integrity_error_does_not_elide_at_exactly_five(tmp_path: Path) -> None:
    state = CorpusReader(_ghost_corpus(tmp_path, 5)).at_state("2026-05-05")

    with pytest.raises(StateIntegrityError) as excinfo:
        state.search_body("tekst")

    shown = str(excinfo.value).split("tree: ")[1].split(" — ")[0]
    assert shown == ("ghost1loven, ghost2loven, ghost3loven, ghost4loven, ghost5loven")
