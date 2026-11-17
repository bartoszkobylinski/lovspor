"""Tests for scripts/ci/corpus_freshness.py and its workflow.

The check must fail on a sync that did not succeed and on a corpus that fell
behind Lovdata, must never read "could not tell" as "fresh" where it decides,
and must keep what lovspor.no serves a warning, never a failure. No network:
every input is a fixture handed to `observe` through a fake fetcher.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from lovspor.sync import orchestrator

_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = _ROOT / "scripts" / "ci" / "corpus_freshness.py"
WORKFLOW = _ROOT / ".github" / "workflows" / "corpus-freshness.yml"
NOW = datetime(2026, 11, 17, 16, 30, tzinfo=UTC)
ENGINE = "bartoszkobylinski/lovspor"
CORPUS = "bartoszkobylinski/lovverk"
LOVVERK_SHA = "a" * 40
ENGINE_SHA = "b" * 40


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("corpus_freshness", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cf = _load()


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(hours_ago: float, conclusion: str | None = "success", status: str = "completed") -> Any:
    return cf.SyncRun(status, conclusion, NOW - timedelta(hours=hours_ago), f"run-{hours_ago}")


def _observed(
    *,
    runs: list[Any] | None = None,
    head_hours_ago: float | None = 5,
    archives: dict[str, datetime] | None = None,
    **extra: Any,
) -> Any:
    head = (
        None
        if head_hours_ago is None
        else cf.Commit(LOVVERK_SHA, NOW - timedelta(hours=head_hours_ago))
    )
    return cf.Observed(
        now=NOW,
        sync_runs=[_run(5)] if runs is None else runs,
        lovverk_main=head,
        archives=archives,
        **extra,
    )


def _errors(findings: list[Any]) -> list[str]:
    return [f.message for f in findings if f.level == cf.ERROR]


def _levels(findings: list[Any]) -> set[str]:
    return {f.level for f in findings}


def _old_archives(hours_ago: float) -> dict[str, datetime]:
    return {name: NOW - timedelta(hours=hours_ago) for name in cf.TRACKED_ARCHIVES}


class TestParseTime:
    def test_reads_z_suffix_as_utc(self) -> None:
        assert cf.parse_time("2026-11-14T02:31:00Z") == datetime(2026, 11, 14, 2, 31, tzinfo=UTC)

    def test_normalises_an_offset(self) -> None:
        parsed = cf.parse_time("2026-10-29T16:36:05+01:00")
        assert parsed == datetime(2026, 10, 29, 15, 36, 5, tzinfo=UTC)

    def test_reads_naive_input_as_utc(self) -> None:
        assert cf.parse_time("2026-11-14T02:31:00").tzinfo == UTC


class TestSyncCheck:
    def test_a_recent_success_passes(self) -> None:
        assert _errors(cf.judge_sync([_run(5)], NOW, 36)) == []

    def test_the_latest_failure_fails_even_after_older_successes(self) -> None:
        errors = _errors(cf.judge_sync([_run(30), _run(4, "failure")], NOW, 36))
        assert len(errors) == 1
        assert "concluded failure" in errors[0]
        assert "run-4" in errors[0]

    def test_a_cancelled_run_fails(self) -> None:
        assert _errors(cf.judge_sync([_run(4, "cancelled")], NOW, 36))

    def test_a_later_success_clears_an_earlier_failure(self) -> None:
        assert _errors(cf.judge_sync([_run(30, "failure"), _run(2)], NOW, 36)) == []

    def test_an_old_success_fails_as_a_stopped_schedule(self) -> None:
        errors = _errors(cf.judge_sync([_run(40)], NOW, 36))
        assert len(errors) == 1
        assert "has completed for" in errors[0]

    def test_an_in_progress_run_is_noted_and_the_last_completed_one_decides(self) -> None:
        runs = [_run(0.5, None, "in_progress"), _run(28, "failure")]
        findings = cf.judge_sync(runs, NOW, 36)
        assert _errors(findings)
        assert any(f.level == cf.NOTICE and "in_progress" in f.message for f in findings)

    def test_no_completed_run_fails(self) -> None:
        assert _errors(cf.judge_sync([_run(1, None, "queued")], NOW, 36))
        assert _errors(cf.judge_sync([], NOW, 36))

    def test_an_unreadable_run_list_fails_closed(self) -> None:
        assert _errors(cf.judge_sync(None, NOW, 36)) == ["could not read the sync.yml run list"]


class TestCorpusCheck:
    def test_a_young_corpus_passes_without_reading_lovdata(self) -> None:
        findings = cf.judge_corpus(_observed(head_hours_ago=35, archives=None), 36)
        assert _errors(findings) == []

    def test_an_old_corpus_with_newer_lovdata_fails(self) -> None:
        archives = _old_archives(60)
        archives["gjeldende-sentrale-forskrifter.tar.bz2"] = NOW - timedelta(hours=14)
        errors = _errors(cf.judge_corpus(_observed(head_hours_ago=48, archives=archives), 36))
        assert len(errors) == 1
        assert "gjeldende-sentrale-forskrifter.tar.bz2" in errors[0]

    def test_an_old_corpus_with_nothing_newer_upstream_passes(self) -> None:
        findings = cf.judge_corpus(_observed(head_hours_ago=72, archives=_old_archives(80)), 36)
        assert _errors(findings) == []
        assert "nothing newer" in findings[0].message

    def test_the_limit_itself_is_not_stale(self) -> None:
        findings = cf.judge_corpus(_observed(head_hours_ago=36, archives=_old_archives(1)), 36)
        assert _errors(findings) == []

    def test_unreadable_lovdata_fails_once_the_corpus_is_old(self) -> None:
        errors = _errors(cf.judge_corpus(_observed(head_hours_ago=48, archives=None), 36))
        assert errors and "could not be read" in errors[0]

    def test_a_tracked_archive_missing_from_the_list_fails(self) -> None:
        archives = {"gjeldende-lover.tar.bz2": NOW - timedelta(hours=80)}
        errors = _errors(cf.judge_corpus(_observed(head_hours_ago=48, archives=archives), 36))
        assert errors == [
            "lovverk main aaaaaaaa is 2.0 days old and Lovdata no longer lists "
            "gjeldende-sentrale-forskrifter.tar.bz2"
        ]

    def test_an_unreadable_lovverk_head_fails_closed(self) -> None:
        errors = _errors(cf.judge_corpus(_observed(head_hours_ago=None), 36))
        assert errors == ["could not read lovverk main"]

    def test_a_successful_sync_after_the_upstream_change_still_fails_with_a_hint(self) -> None:
        archives = _old_archives(60)
        archives["gjeldende-lover.tar.bz2"] = NOW - timedelta(hours=20)
        observed = _observed(runs=[_run(5)], head_hours_ago=48, archives=archives)
        errors = _errors(cf.judge_corpus(observed, 36))
        assert len(errors) == 1
        assert "a successful sync started after that" in errors[0]


class TestSiteCheck:
    def test_lag_is_a_warning_never_an_error(self) -> None:
        main = cf.Commit(LOVVERK_SHA, NOW)
        lag = cf.Lag("c" * 40, main, behind_by=334, served_time=NOW - timedelta(days=19))
        findings = cf.judge(_observed(corpus_lag=lag), 36)
        assert _errors(findings) == []
        warning = next(f.message for f in findings if f.level == cf.WARNING)
        assert "334 commit(s) behind" in warning
        assert "19.0 days older" in warning

    def test_a_site_serving_main_is_a_notice(self) -> None:
        lag = cf.Lag(ENGINE_SHA, cf.Commit(ENGINE_SHA, NOW))
        findings = cf.judge_site(_observed(engine_lag=lag))
        assert _levels(findings) == {cf.NOTICE}

    def test_an_unreachable_site_is_only_a_warning(self) -> None:
        findings = cf.judge(_observed(unread=["site-facts.json corpus_commit"]), 36)
        assert _errors(findings) == []
        assert cf.WARNING in _levels(findings)


def _fake_fetch(responses: dict[str, tuple[int, Any]]) -> Any:
    def fetch(url: str) -> tuple[int, Any]:
        return responses.get(url, (cf.UNREACHABLE, None))

    return fetch


def _responses(*, served_corpus: str = "c" * 40) -> dict[str, tuple[int, Any]]:
    api = cf.GITHUB_API
    head_time = _iso(NOW - timedelta(hours=48))

    def commit(sha: str, when: str) -> dict[str, Any]:
        return {"sha": sha, "commit": {"committer": {"date": when}}}

    return {
        f"{api}/repos/{ENGINE}/actions/workflows/sync.yml/runs?branch=main&per_page=10": (
            200,
            {
                "workflow_runs": [
                    {
                        "status": "completed",
                        "conclusion": "failure",
                        "created_at": _iso(NOW - timedelta(hours=4)),
                        "html_url": "https://example.invalid/run/1",
                    }
                ]
            },
        ),
        f"{api}/repos/{CORPUS}/commits/main": (200, commit(LOVVERK_SHA, head_time)),
        f"{api}/repos/{ENGINE}/commits/main": (200, commit(ENGINE_SHA, _iso(NOW))),
        cf.LOVDATA_LIST_URL: (
            200,
            [
                {
                    "filename": name,
                    "description": "",
                    "sizeBytes": "1",
                    "lastModified": _iso(NOW - timedelta(hours=14)),
                }
                for name in cf.TRACKED_ARCHIVES
            ],
        ),
        cf.SITE_FACTS_URL: (200, {"corpus_commit": served_corpus}),
        cf.CAPABILITIES_URL: (200, {"state": {"checkout": {"lovspor_commit": ENGINE_SHA}}}),
        f"{api}/repos/{CORPUS}/compare/{served_corpus}...{LOVVERK_SHA}": (
            200,
            {"ahead_by": 7, "base_commit": commit(served_corpus, _iso(NOW - timedelta(days=3)))},
        ),
    }


class TestObserve:
    def test_reads_every_input_from_the_fetcher(self) -> None:
        observed = cf.observe(_fake_fetch(_responses()), ENGINE, CORPUS, NOW)
        assert observed.sync_runs[0].conclusion == "failure"
        assert observed.lovverk_main.sha == LOVVERK_SHA
        assert set(observed.archives) == set(cf.TRACKED_ARCHIVES)
        assert observed.corpus_lag.behind_by == 7
        assert observed.engine_lag.served == observed.engine_lag.main.sha
        assert observed.unread == []

    def test_an_unanswered_compare_keeps_the_lag_unknown(self) -> None:
        responses = _responses()
        del responses[next(u for u in responses if "/compare/" in u)]
        observed = cf.observe(_fake_fetch(responses), ENGINE, CORPUS, NOW)
        assert observed.corpus_lag.behind_by is None
        assert observed.corpus_lag.served_time is None

    def test_nothing_reachable_reads_as_unknown_not_fresh(self) -> None:
        observed = cf.observe(_fake_fetch({}), ENGINE, CORPUS, NOW)
        assert observed.sync_runs is None
        assert observed.lovverk_main is None
        assert observed.archives is None
        assert len(observed.unread) == 2

    def test_a_malformed_run_list_reads_as_unknown(self) -> None:
        responses = _responses()
        url = next(u for u in responses if "/actions/" in u)
        responses[url] = (200, {"workflow_runs": [{"status": "completed"}]})
        assert cf.observe(_fake_fetch(responses), ENGINE, CORPUS, NOW).sync_runs is None


class TestMain:
    def test_fails_and_writes_the_report(self, tmp_path: Path, monkeypatch: Any) -> None:
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        report = tmp_path / "report.md"
        argv = ["--repo", ENGINE, "--corpus-repo", CORPUS, "--report", str(report)]
        assert cf.main(argv, fetch=_fake_fetch(_responses())) == 1
        text = report.read_text(encoding="utf-8")
        assert "concluded failure" in text
        assert "Lovdata modified" in text
        assert "7 commit(s) behind" in text
        assert summary.read_text(encoding="utf-8").startswith("## Corpus freshness")

    def test_passes_when_only_warnings_remain(self, tmp_path: Path) -> None:
        responses = _responses()
        runs_url = next(u for u in responses if "/actions/" in u)
        responses[runs_url][1]["workflow_runs"][0]["conclusion"] = "success"
        responses[cf.LOVDATA_LIST_URL] = (
            200,
            [
                {"filename": n, "lastModified": _iso(NOW - timedelta(hours=60))}
                for n in cf.TRACKED_ARCHIVES
            ],
        )
        argv = ["--repo", ENGINE, "--corpus-repo", CORPUS]
        assert cf.main(argv, fetch=_fake_fetch(responses)) == 0

    def test_refuses_a_non_https_url(self) -> None:
        with pytest.raises(ValueError, match="non-https"):
            cf.fetch_json("http://example.invalid/list")


class TestWorkflow:
    @pytest.fixture
    def workflow(self) -> dict[Any, Any]:
        loaded: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        return loaded

    def test_runs_daily_after_the_sync_window(self, workflow: dict[Any, Any]) -> None:
        triggers = workflow[True]  # YAML 1.1 reads the bare key `on` as True
        assert triggers["schedule"] == [{"cron": "30 16 * * *"}]
        assert "workflow_dispatch" in triggers

    def test_is_read_only_apart_from_issues(self, workflow: dict[Any, Any]) -> None:
        assert workflow["permissions"] == {
            "contents": "read",
            "actions": "read",
            "issues": "write",
        }

    def test_holds_no_secret(self) -> None:
        assert "secrets." not in WORKFLOW.read_text(encoding="utf-8")

    def test_checks_the_corpus_the_sync_writes(self, workflow: dict[Any, Any]) -> None:
        run = json.dumps(workflow["jobs"]["freshness"]["steps"])
        assert "--corpus-repo bartoszkobylinski/lovverk" in run
        assert "--max-age-hours 36" in run

    def test_reports_into_one_reused_issue_only_when_the_check_failed(
        self, workflow: dict[Any, Any]
    ) -> None:
        step = workflow["jobs"]["freshness"]["steps"][-1]
        assert step["if"] == "failure() && steps.check.outcome == 'failure'"
        assert "gh issue comment" in step["run"]
        assert "gh issue create" in step["run"]

    def test_tracks_the_archives_the_sync_tracks(self) -> None:
        expected = tuple(f"{d}.tar.bz2" for d in orchestrator._TRACKED_DATASETS)
        assert expected == cf.TRACKED_ARCHIVES
