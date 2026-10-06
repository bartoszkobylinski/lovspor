"""``lovspor promote batch``: classifier candidates under one spot-check gate (ADR-0016 S8).

Every state is reached as an operator reaches it: the archive through the
observatory's own writers, the classifier output as the JSON Lines file the
classifier writes, decisions through ``promote approve``, the corpus through
``promote batch --write``. Archive and corpus live under ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion import plan
from lovspor.promotion.batch_commands import BLOCKED_EXIT_CODE
from lovspor.promotion.decisions import DECISIONS_FILENAME
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    KLASS_VERSION,
    Decision,
    approve,
    git,
    invoke,
    make_corpus,
    register,
    store,
    tree,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

FNR_LINE = "Søker med fødselsnummer 01019012480 er registrert."
BASE = "https://eksempel.kommune.invalid/forskrifter"
TITLES = (
    "Forskrift om renovasjon og slam, Eksempel kommune",
    "Forskrift om skolekretser, Eksempel kommune",
    "Forskrift om feiing og tilsyn, Eksempel kommune",
)


def page(title: str, *extra: str) -> bytes:
    return html_page((title, *REGULATION_LINES[1:], *extra))


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    register(observatory)
    return observatory


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    return make_corpus(tmp_path)


class Batch:
    """A synthetic batch: archived pages, the classifier's rows for them, and a spec."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.rows: list[dict[str, object]] = []
        self.report_dir = base / "reports"

    def add(self, payload: bytes, slug: str, *, r1: bool = True) -> str:
        url = f"{BASE}/{slug}"
        sha256 = store(self.base / "observatory", payload, url=url)
        self.rows.append(
            {
                "url": url,
                "authority": AUTHORITY,
                "form": "html",
                "sha": sha256,
                "r1": r1,
                "r2": True,
                "self_operative": True,
                "enacted_by": True,
                "n_sections": 3,
            }
        )
        return sha256

    def spec(self, **overrides: object) -> Path:
        output = self.base / "predictions.jsonl"
        output.write_text("".join(json.dumps(r) + "\n" for r in self.rows), encoding="utf-8")
        body: dict[str, object] = {
            "batch_id": "0301-test-1",
            "authority_id": AUTHORITY,
            "klass_version": KLASS_VERSION,
            "classifier_output": str(output),
            "classifier_version": "r1.1-2026-10-03",
            "sample_rate": "1",
        }
        path = self.base / "batch.json"
        path.write_text(json.dumps(body | overrides), encoding="utf-8")
        return path

    def run(self, corpus: Path, *extra: str, **overrides: object) -> tuple[int, str]:
        args = ["promote", "batch", "--spec", str(self.spec(**overrides)), "--corpus", str(corpus)]
        result = invoke(*args, "--report-dir", str(self.report_dir), *extra)
        return result.exit_code, result.output

    def report(self, batch_id: str = "0301-test-1") -> dict[str, object]:
        path = self.report_dir / f"batch-{batch_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def batch(root: Path) -> Batch:
    return Batch(root.parent)


def _approve(sha256: str, tmp_path: Path, decision: str = "approve") -> None:
    result = approve(sha256, Decision(decision=decision).write(tmp_path))
    assert result.exit_code == 0, result.output


def _sampled(report: dict[str, object]) -> list[str]:
    items = report["items"]
    assert isinstance(items, list)
    return [i["key"]["sha256"] for i in items if i["sampled"]]


class TestAssessment:
    def test_holds_are_counted_by_reason_and_nothing_is_written(
        self, batch: Batch, corpus: Path, root: Path
    ) -> None:
        batch.add(page(TITLES[0]), "a")
        batch.add(page(TITLES[1], FNR_LINE), "b")
        batch.add(page(TITLES[2], "Utskrift fra Lovdata"), "c")
        batch.add(page("Forskrift om noe annet"), "d", r1=False)
        before = tree(corpus)

        code, output = batch.run(corpus)

        report = batch.report()
        assert report["holds_by_reason"] == {
            "extraction:lovdata_copy": 1,
            "extraction:personal_data": 1,
        }
        assert report["summary"]["candidates"] == 3
        assert report["summary"]["ready"] == 1
        assert "held extraction:personal_data: 1" in output
        assert tree(corpus) == before
        assert not (root / DECISIONS_FILENAME).exists()
        assert code == BLOCKED_EXIT_CODE

    def test_the_markdown_report_names_the_gate_the_holds_and_the_preview(
        self, batch: Batch, corpus: Path
    ) -> None:
        sha256 = batch.add(page(TITLES[0]), "a")
        batch.add(page(TITLES[1], FNR_LINE), "b")

        batch.run(corpus)

        text = (batch.report_dir / "batch-0301-test-1.md").read_text(encoding="utf-8")
        assert "## Gate: BLOCKED" in text
        assert "| extraction:personal_data | 1 |" in text
        assert "fodselsnummer on line" in text
        assert "01019012480" not in text
        assert f"lovspor promote preview --authority {AUTHORITY} --artifact {sha256}" in text
        assert "sample rate: 1" in text

    def test_a_candidate_missing_from_the_archive_is_counted_as_refused(
        self, batch: Batch, corpus: Path
    ) -> None:
        batch.add(page(TITLES[0]), "a")
        batch.rows.append(batch.rows[0] | {"sha": "f" * 64})

        batch.run(corpus)

        assert batch.report()["holds_by_reason"] == {"request:refused": 1}

    def test_two_candidates_minting_one_id_are_both_held(self, batch: Batch, corpus: Path) -> None:
        batch.add(page(TITLES[0]), "a")
        batch.add(page(TITLES[0]), "a-kopi")

        batch.run(corpus)

        assert batch.report()["holds_by_reason"] == {"batch:same_id_in_batch": 2}

    def test_a_listed_batch_assesses_only_the_listed_artifacts(
        self, batch: Batch, corpus: Path
    ) -> None:
        batch.add(page(TITLES[0]), "a")
        listed = batch.add(page(TITLES[1]), "b")

        batch.run(corpus, artifacts=[listed])

        assert [i["key"]["sha256"] for i in batch.report()["items"]] == [listed]


class TestSample:
    def test_reports_are_byte_identical_for_an_unchanged_batch(
        self, batch: Batch, corpus: Path
    ) -> None:
        batch.add(page(TITLES[0]), "a")
        batch.add(page(TITLES[1], FNR_LINE), "b")
        batch.run(corpus)
        before = tree(batch.report_dir)

        batch.run(corpus)

        assert tree(batch.report_dir) == before
        assert set(before) == {"batch-0301-test-1.md", "batch-0301-test-1.json"}
        assert all(b"01019012480" not in content for content in before.values())

    def test_the_same_seed_gives_the_same_sample(self, batch: Batch, corpus: Path) -> None:
        for n, title in enumerate(TITLES):
            batch.add(page(title), f"p{n}")

        batch.run(corpus, sample_rate="0.5")
        first = _sampled(batch.report())
        batch.run(corpus, sample_rate="0.5")

        assert _sampled(batch.report()) == first
        assert len(first) == 2

    @pytest.mark.parametrize("rate", [None, "0", "1.5", 1])
    def test_the_sample_rate_is_required_and_explicit(
        self, batch: Batch, corpus: Path, rate: object
    ) -> None:
        batch.add(page(TITLES[0]), "a")
        spec = json.loads(batch.spec().read_text(encoding="utf-8"))
        spec.pop("sample_rate")
        if rate is not None:
            spec["sample_rate"] = rate
        path = batch.base / "bad.json"
        path.write_text(json.dumps(spec), encoding="utf-8")

        result = invoke(
            "promote",
            "batch",
            "--spec",
            str(path),
            "--corpus",
            str(corpus),
            "--report-dir",
            str(batch.report_dir),
        )

        assert result.exit_code == 1
        assert "sample_rate" in result.output


class TestGate:
    def test_approval_for_another_extractor_blocks_the_sample(
        self,
        batch: Batch,
        corpus: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _approve(batch.add(page(TITLES[0]), "a"), tmp_path)
        monkeypatch.setattr(plan, "EXTRACTOR_VERSION", plan.EXTRACTOR_VERSION + 1)
        before = tree(corpus)

        code, output = batch.run(corpus, "--write")

        assert code == BLOCKED_EXIT_CODE, output
        report = batch.report()
        assert report["items"][0]["review"] == "approval_for_another_text"
        assert len(report["gate"]["awaiting_review"]) == 1
        assert report["summary"]["would_write"] == 0
        assert tree(corpus) == before

    def test_a_passing_partial_sample_never_promotes_an_unreviewed_item(
        self, batch: Batch, corpus: Path, tmp_path: Path
    ) -> None:
        for n, title in enumerate(TITLES):
            batch.add(page(title), f"p{n}")
        batch.run(corpus, sample_rate="0.1")
        sampled = _sampled(batch.report())
        assert len(sampled) == 1
        _approve(sampled[0], tmp_path)

        code, output = batch.run(corpus, "--write", sample_rate="0.1")

        assert code == 0, output
        report = batch.report()
        assert report["gate"]["verdict"] == "pass"
        assert report["summary"]["would_write"] == 1
        assert len(_documents(corpus)) == 1
        git(corpus, "add", "-A")
        git(corpus, "commit", "-q", "-m", "promote sampled item")
        before = tree(corpus)

        code, output = batch.run(corpus, "--write", sample_rate="0.1")

        assert code == 0, output
        assert _sampled(batch.report()) == sampled
        assert batch.report()["gate"]["verdict"] == "pass"
        assert batch.report()["summary"]["unchanged"] == 1
        assert batch.report()["summary"]["ready"] == 2
        assert batch.report()["summary"]["would_write"] == 0
        assert tree(corpus) == before

    def test_one_rejected_sample_item_blocks_the_batch(
        self, batch: Batch, corpus: Path, tmp_path: Path
    ) -> None:
        first = batch.add(page(TITLES[0]), "a")
        second = batch.add(page(TITLES[1]), "b")
        _approve(first, tmp_path)
        _approve(second, tmp_path, "reject")
        before = tree(corpus)

        code, output = batch.run(corpus, "--write")

        assert code == BLOCKED_EXIT_CODE
        assert f"rejected in sample, blocks the batch: {second}" in output
        assert tree(corpus) == before
        assert batch.report()["gate"]["rejected"][0]["sha256"] == second

    def test_an_unreviewed_sample_item_blocks_the_batch(
        self, batch: Batch, corpus: Path, tmp_path: Path
    ) -> None:
        _approve(batch.add(page(TITLES[0]), "a"), tmp_path)
        batch.add(page(TITLES[1]), "b")

        code, output = batch.run(corpus, "--write")

        assert code == BLOCKED_EXIT_CODE
        assert "1 sampled item(s) await review" in output

    def test_a_sampled_item_the_reviewer_held_blocks_the_batch(
        self, batch: Batch, corpus: Path, tmp_path: Path
    ) -> None:
        _approve(batch.add(page(TITLES[0]), "a"), tmp_path, "hold")

        code, _ = batch.run(corpus, "--write")

        assert code == BLOCKED_EXIT_CODE
        assert batch.report()["items"][0]["review"] == "held_by_reviewer"

    def test_a_passing_gate_writes_one_item_per_run_and_never_commits(
        self, batch: Batch, corpus: Path, tmp_path: Path
    ) -> None:
        for slug, title in zip(("a", "b"), TITLES, strict=False):
            _approve(batch.add(page(title), slug), tmp_path)
        head = git(corpus, "rev-parse", "HEAD")

        code, output = batch.run(corpus, "--write")

        assert code == 0, output
        assert "1 more approved item(s): commit, then rerun this command." in output
        assert git(corpus, "rev-parse", "HEAD") == head
        assert len(_documents(corpus)) == 1
        git(corpus, "add", "-A")
        git(corpus, "commit", "-q", "-m", "promote(lokal-forskrift): first")

        code, output = batch.run(corpus, "--write")

        assert code == 0, output
        assert len(_documents(corpus)) == 2
        assert "more approved" not in output
        git(corpus, "add", "-A")
        git(corpus, "commit", "-q", "-m", "promote(lokal-forskrift): second")

        code, output = batch.run(corpus, "--write")

        assert code == 0, output
        assert "Nothing to write" in output
        assert batch.report()["summary"]["unchanged"] == 2

    def test_a_dry_run_of_a_passing_batch_writes_nothing(
        self, batch: Batch, corpus: Path, tmp_path: Path
    ) -> None:
        _approve(batch.add(page(TITLES[0]), "a"), tmp_path)
        before = tree(corpus)

        code, output = batch.run(corpus)

        assert code == 0, output
        assert "Dry run" in output
        assert tree(corpus) == before
        assert batch.report()["summary"]["would_write"] == 1

    def test_a_batch_with_nothing_promotable_is_empty_not_passed(
        self, batch: Batch, corpus: Path
    ) -> None:
        batch.add(page(TITLES[0], FNR_LINE), "a")

        code, _ = batch.run(corpus, "--write")

        assert code == 0
        assert batch.report()["gate"]["verdict"] == "empty"


class TestReportPlacement:
    def test_a_symlink_into_the_corpus_is_refused(self, batch: Batch, corpus: Path) -> None:
        batch.add(page(TITLES[0]), "a")
        alias = batch.base / "corpus-alias"
        alias.symlink_to(corpus, target_is_directory=True)
        batch.report_dir = alias / "reports"
        before = tree(corpus)

        code, output = batch.run(corpus)

        assert code == 1
        assert "report names archive material" in output
        assert tree(corpus) == before

    def test_a_relative_report_dir_is_refused(self, batch: Batch, corpus: Path) -> None:
        batch.add(page(TITLES[0]), "a")
        batch.report_dir = Path("reports")

        code, output = batch.run(corpus)

        assert code == 1
        assert "--report-dir must be an absolute path" in output

    def test_a_report_dir_inside_the_corpus_is_refused(self, batch: Batch, corpus: Path) -> None:
        batch.add(page(TITLES[0]), "a")
        batch.report_dir = corpus / "reports"

        code, output = batch.run(corpus)

        assert code == 1
        assert "report names archive material" in output
        assert not batch.report_dir.exists()


def _documents(corpus: Path) -> dict[str, object]:
    path = corpus / "lokale-forskrifter" / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))["documents"]
