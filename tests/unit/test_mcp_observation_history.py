"""``get_observation_history`` and local ``recorded_at``, on a backfilled corpus (ADR-0016 5; S7).

The corpus is the S6 backfill of one regulation, committed weeks after it
was observed (``backfilled_corpus_fixtures``): an instant on the observation
axis and a date on the corpus axis select different things, and each tool
reads only its own.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lovspor.local_corpus import OBSERVATION_NOTICE
from lovspor.mcp import build_server
from tests.unit.backfilled_corpus_fixtures import (
    INTERVALS,
    LOCAL,
    backfilled_corpus,
    dated_git,
    local_address,
)
from tests.unit.local_dataset_fixtures import wire
from tests.unit.promotion_cli_fixtures import AUTHORITY, PAGE_URL


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return backfilled_corpus(tmp_path, monkeypatch)


def _without_history(corpus: Path) -> None:
    """Move .git aside rather than delete it: a rename cannot race a late writer (#582)."""
    (corpus / ".git").rename(corpus.parent / f"{corpus.name}-git-removed")


class TestHistoryFixtureSafety:
    @pytest.mark.parametrize(
        ("setting", "enabled", "disabled"),
        [("gc.auto", "1", "0"), ("maintenance.auto", "true", "false")],
    )
    def test_dated_git_overrides_enabled_repository_maintenance(
        self, tmp_path: Path, setting: str, enabled: str, disabled: str
    ) -> None:
        date = "2026-09-01T12:00:00Z"
        dated_git(tmp_path, date, "init", "-q")
        dated_git(tmp_path, date, "config", "--local", setting, enabled)

        assert dated_git(tmp_path, date, "config", "--local", "--get", setting).strip() == enabled
        assert dated_git(tmp_path, date, "config", "--get", setting).strip() == disabled

    def test_removing_history_preserves_a_late_writer_and_corpus_files(
        self, tmp_path: Path
    ) -> None:
        checkout = tmp_path / "corpus"
        git_dir = checkout / ".git"
        git_dir.mkdir(parents=True)
        document = checkout / "regulation.md"
        document.write_bytes(b"regulation\n")
        pending = git_dir / "pending.lock"
        pending.write_bytes(b"before\n")

        with pending.open("ab") as writer:
            _without_history(checkout)
            writer.write(b"after\n")

        assert not git_dir.exists()
        moved = tmp_path / "corpus-git-removed"
        assert (moved / "pending.lock").read_bytes() == b"before\nafter\n"
        assert document.read_bytes() == b"regulation\n"


def _address(corpus: Path) -> str:
    return local_address(corpus)


def _call(corpus: Path, name: str, **arguments: Any) -> Any:
    return wire(build_server(corpus), name, arguments)


def _history(corpus: Path, **arguments: Any) -> dict[str, Any]:
    result = _call(corpus, "get_observation_history", document=_address(corpus), **arguments)
    assert "error" not in result, result
    structured: dict[str, Any] = result["structured"]
    return structured


def _error(corpus: Path, name: str, **arguments: Any) -> str:
    result = _call(corpus, name, **arguments)
    assert "error" in result, result
    return str(result["error"])


class TestHistory:
    def test_every_version_is_listed_with_its_interval(self, corpus: Path) -> None:
        history = _history(corpus)

        assert history["document"] == _address(corpus)
        assert history["doc_id"].startswith(("lf-", "lk-"))
        assert history["dataset"] == LOCAL
        assert [
            (v["observed_at_first"], v["observed_at_last"], v["observation_count"])
            for v in history["versions"]
        ] == INTERVALS
        assert [v["version"] for v in history["versions"]] == [1, 2, 3]
        assert {v["primary_url"] for v in history["versions"]} == {PAGE_URL}
        assert history["versions"][0]["content_hash"] == history["versions"][2]["content_hash"]
        assert history["at"] is None

    def test_the_answer_carries_the_observation_label(self, corpus: Path) -> None:
        observation = _history(corpus)["observation"]

        assert observation["basis"] == "observed"
        assert observation["asserted"] is False
        assert observation["notice"] == OBSERVATION_NOTICE
        assert observation["authority"]["id"] == AUTHORITY

    def test_the_source_status_and_exclusions_come_from_the_file(self, corpus: Path) -> None:
        history = _history(corpus)

        assert history["source_status"]["observed_at"] == "2026-08-22T15:17:23Z"
        assert history["excluded"] == []

    def test_no_audit_record_is_served(self, corpus: Path) -> None:
        """The promotion audit names a reviewer role and archive keys; the tool needs neither."""
        assert all("promotion" not in v for v in _history(corpus)["versions"])


class TestOutcomes:
    def test_contained_names_the_version(self, corpus: Path) -> None:
        at = _history(corpus, observed_at="2026-08-20T00:00:00Z")["at"]

        assert at["outcome"] == "contained"
        assert at["observed_at"] == "2026-08-20T00:00:00Z"
        assert at["version"]["version"] == 1
        assert at["text"] is None

    def test_between_names_both_neighbours_and_asserts_neither(self, corpus: Path) -> None:
        at = _history(corpus, observed_at="2026-08-21T09:00:00+02:00")["at"]

        assert at["outcome"] == "between_observations"
        assert at["observed_at"] == "2026-08-21T07:00:00Z"
        assert (at["before"]["version"], at["after"]["version"]) == (1, 2)
        assert "neither version is asserted" in at["notice"]
        assert "version" not in at

    def test_before_first_carries_the_first_observation_and_the_floor(self, corpus: Path) -> None:
        at = _history(corpus, observed_at="2026-08-19T00:00:00Z")["at"]

        assert at == {
            "outcome": "before_first_observation",
            "observed_at": "2026-08-19T00:00:00Z",
            "observed_at_first": "2026-08-19T15:17:23Z",
            "observation_floor": "2026-08-19",
            "notice": at["notice"],
        }

    def test_after_last_is_typed_and_not_contained(self, corpus: Path) -> None:
        at = _history(corpus, observed_at="2026-09-30T12:00:00Z")["at"]

        assert at["outcome"] == "after_last_observation"
        assert at["last"]["version"] == 3
        assert at["observed_at_last"] == "2026-08-22T15:17:23Z"


class TestIncludeText:
    @pytest.mark.parametrize(
        ("instant", "outcome"),
        [
            ("2026-08-18T00:00:00Z", "before_first_observation"),
            ("2026-08-21T00:00:00Z", "between_observations"),
            ("2026-08-23T00:00:00Z", "after_last_observation"),
            ("2026-08-22T15:17:23Z", "contained"),
        ],
    )
    def test_only_older_contained_text_needs_git_history(
        self, corpus: Path, instant: str, outcome: str
    ) -> None:
        _without_history(corpus)

        at = _history(corpus, observed_at=instant, include_text=True)["at"]

        assert at["outcome"] == outcome
        if outcome == "contained":
            assert "version: 3\n" in at["text"]
        else:
            assert "text" not in at

    def test_older_intervals_are_read_without_git_but_their_text_is_refused(
        self, corpus: Path
    ) -> None:
        _without_history(corpus)
        instant = "2026-08-19T15:17:23Z"

        at = _history(corpus, observed_at=instant)["at"]
        assert at["outcome"] == "contained"
        assert at["version"]["version"] == 1
        assert at["text"] is None

        error = _error(
            corpus,
            "get_observation_history",
            document=_address(corpus),
            observed_at=instant,
            include_text=True,
        )
        assert "has no history to read v1 from" in error

    @pytest.mark.parametrize(
        ("instant", "version", "wording"),
        [
            ("2026-08-19T15:17:23Z", 1, "1. januar 2020"),
            ("2026-08-21T15:17:23Z", 2, "1. februar 2020"),
            ("2026-08-22T15:17:23Z", 3, "1. januar 2020"),
        ],
    )
    def test_the_text_is_that_of_the_contained_version(
        self, corpus: Path, instant: str, version: int, wording: str
    ) -> None:
        at = _history(corpus, observed_at=instant, include_text=True)["at"]

        assert at["version"]["version"] == version
        assert f"version: {version}\n" in at["text"]
        assert wording in at["text"]
        assert "asserted: false" in at["text"]

    def test_no_text_is_attached_to_a_gap(self, corpus: Path) -> None:
        at = _history(corpus, observed_at="2026-08-21T00:00:00Z", include_text=True)["at"]

        assert at["outcome"] == "between_observations"
        assert "text" not in at

    def test_include_text_without_an_instant_is_refused(self, corpus: Path) -> None:
        error = _error(
            corpus, "get_observation_history", document=_address(corpus), include_text=True
        )

        assert "include_text needs observed_at" in error


class TestRefusals:
    def test_a_central_slug_has_no_observation_history(self, corpus: Path) -> None:
        error = _error(corpus, "get_observation_history", document="eksempelforskrift")

        assert "'eksempelforskrift' is not a local regulation" in error
        assert "get_law_history" in error

    @pytest.mark.parametrize("value", ["2026-08-20", "2026-08-20T12:00:00"])
    def test_an_observed_at_without_an_offset_is_refused(self, corpus: Path, value: str) -> None:
        error = _error(
            corpus, "get_observation_history", document=_address(corpus), observed_at=value
        )

        assert "observed_at must be an instant with an offset" in error

    def test_an_unknown_local_address_is_not_found(self, corpus: Path) -> None:
        error = _error(corpus, "get_observation_history", document=f"{AUTHORITY}/finnes-ikke")

        assert "no current local regulation" in error


class TestRecordedAtForLocal:
    """ADR-0016 5: ``recorded_at`` works on local documents unchanged (git; ADR-0011)."""

    @pytest.mark.parametrize(
        ("recorded_at", "wording"),
        [("2026-09-15", "1. januar 2020"), ("2026-09-25", "1. februar 2020")],
    )
    def test_a_local_section_is_read_from_the_corpus_state(
        self, corpus: Path, recorded_at: str, wording: str
    ) -> None:
        section = _call(
            corpus, "get_section", slug=_address(corpus), section_id="3", recorded_at=recorded_at
        )["structured"]
        head = subprocess.run(
            ["git", "rev-list", "-n", "1", f"--before={recorded_at}T23:59:59Z", "HEAD"],
            cwd=corpus,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        assert wording in section["body"]
        assert section["recorded_at"] == recorded_at
        assert section["corpus_commit"] == head
        assert section["dataset"] == LOCAL
        assert section["observation"]["asserted"] is False
        assert len(section["content_hash"]) == 64
        assert "temporal_notice" not in section

    def test_the_label_is_the_one_that_state_carried(self, corpus: Path) -> None:
        section = _call(
            corpus, "get_section", slug=_address(corpus), section_id="1", recorded_at="2026-09-15"
        )["structured"]

        assert section["observation"]["observed_at_first"] == "2026-08-19T15:17:23Z"

    def test_an_observation_date_does_not_select_a_corpus_state(self, corpus: Path) -> None:
        """2026-08-21 is when v2 was observed; the corpus recorded nothing until September."""
        error = _error(
            corpus, "get_section", slug=_address(corpus), section_id="1", recorded_at="2026-08-21"
        )

        assert "recorded_at" not in _history(corpus, observed_at="2026-08-21T15:17:23Z")
        assert "2026-08-21" in error

    def test_a_state_before_the_promotion_has_no_local_document(self, corpus: Path) -> None:
        error = _error(
            corpus, "get_section", slug=_address(corpus), section_id="1", recorded_at="2026-09-05"
        )

        assert "no current local regulation" in error
        assert "in the corpus state at 2026-09-05" in error
        assert "corpus_commit" in error


class TestAxesStaySeparate:
    def test_no_recorded_at_tool_takes_observed_at_and_back(self, corpus: Path) -> None:
        tools = {tool.name: tool for tool in asyncio.run(build_server(corpus).list_tools())}
        takes = {name: set(tool.inputSchema["properties"]) for name, tool in tools.items()}

        assert takes["get_observation_history"] == {"document", "observed_at", "include_text"}
        assert [name for name, params in takes.items() if "observed_at" in params] == [
            "get_observation_history"
        ]
        assert "recorded_at" in takes["get_section"]
