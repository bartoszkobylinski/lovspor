"""``lovspor promote batch``: classifier candidates under one spot-check gate (ADR-0016 S8).

Every state is reached as an operator reaches it: the archive through the
observatory's own writers, the classifier output as the JSON Lines file the
classifier writes, decisions through ``promote approve``, the corpus through
``promote batch --write``. Archive and corpus live under ``tmp_path``.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion import plan
from lovspor.promotion.batch import BatchAssessment, BatchSpec
from lovspor.promotion.batch_commands import BLOCKED_EXIT_CODE, _spec
from lovspor.promotion.batch_report import report_json, report_markdown
from lovspor.promotion.decisions import DECISIONS_FILENAME
from lovspor.promotion.sample import parse_sample_rate
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
        assert (
            "Nothing to write: no approved item of this batch is missing from the corpus."
            in output.splitlines()
        )
        assert batch.report()["summary"]["unchanged"] == 2

    def test_a_dry_run_of_a_passing_batch_writes_nothing(
        self, batch: Batch, corpus: Path, tmp_path: Path
    ) -> None:
        _approve(batch.add(page(TITLES[0]), "a"), tmp_path)
        before = tree(corpus)

        code, output = batch.run(corpus)

        assert code == 0, output
        assert "Dry run: nothing written, nothing recorded." in output.splitlines()
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


def _expected_markdown(report: dict, corpus: Path) -> str:
    """Independent specification of the owner-facing report and runnable commands."""
    spec = report["spec"]
    gate = report["gate"]
    lines = [
        f"# Promotion batch {spec['batch_id']}",
        "",
        f"- authority: {spec['authority_id']} (KLASS {spec['klass_version']})",
        f"- classifier: {spec['classifier_version']}, output sha256 "
        f"{report['classifier_output_sha256']}",
        f"- extractor v{report['extractor_version']}, renderer v{report['renderer_version']}",
        f"- sample rate: {spec['sample_rate']} (stated in the batch spec; ADR-0016 4g recommends 1 "
        "= 100 % for the first authority and every new adapter family)",
        f"- listed artifacts: {len(spec['artifacts']) or 'all candidates'}",
        "",
        f"## Gate: {gate['verdict'].upper()}",
        "",
    ]
    blocking = [
        f"- rejected in sample, blocks the batch: {k['sha256']} {k['source_url']}"
        for k in gate["rejected"]
    ]
    blocking += [
        f"- awaiting review: {k['sha256']} {k['source_url']}" for k in gate["awaiting_review"]
    ]
    if blocking:
        lines += [*blocking, ""]
    counts = report["summary"]
    lines += ["| measure | count |", "|---|--:|"]
    lines += [
        f"| {k} | {counts[k]} |"
        for k in ("candidates", "ready", "unchanged", "held", "refused", "sampled", "would_write")
    ]
    lines += ["", "## Holds by reason", "", "| reason | count |", "|---|--:|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(report["holds_by_reason"].items())] or [
        "| (none) | 0 |"
    ]
    lines += ["", "## Sample for review", ""]
    for item in report["items"]:
        if not item["sampled"]:
            continue
        key = item["key"]
        command = shlex.join(
            [
                "lovspor",
                "promote",
                "preview",
                "--authority",
                spec["authority_id"],
                "--artifact",
                key["sha256"],
                "--corpus",
                str(corpus),
                "--klass-version",
                spec["klass_version"],
            ]
        )
        lines += [
            f"### {item['title']}",
            "",
            f"- {key['source_url']}",
            f"- sha256 {key['sha256']}",
            f"- {item['doc_id']} v{item['version']} -> {item['markdown_path']} ({item['outcome']})",
            f"- review: {item['review']}",
            f"- classifier: {item['classifier']['class_name']} on "
            f"{', '.join(item['classifier']['evidence'])}",
            f"- read it: `{command}`",
            "",
        ]
    lines += ["## Held and refused", ""]
    holds = []
    for item in report["items"]:
        if item["hold"] is None:
            continue
        key = item["key"]
        holds.append(f"- `{item['hold']}` {key['sha256']} {key['source_url']}: {item['detail']}")
        holds += [
            f"  - personal data: {h['kind']} on line {h['line']}" for h in item["personal_data"]
        ]
    lines += holds or ["(none)"]
    return "\n".join(lines).rstrip("\n") + "\n"


@pytest.mark.parametrize("scenario", ["ready", "mixed", "duplicates", "listed", "approved"])
def test_batch_report_bytes_and_assessment_fields(
    batch: Batch, corpus: Path, tmp_path: Path, scenario: str
) -> None:
    first = batch.add(page(TITLES[0]), "a")
    second = batch.add(page(TITLES[1]), "b")
    overrides = {}
    if scenario == "mixed":
        _approve(first, tmp_path, "reject")
        batch.add(page(TITLES[2], FNR_LINE), "personal")
        batch.rows.append(batch.rows[0] | {"sha": "f" * 64})
    elif scenario == "duplicates":
        batch.add(page(TITLES[0]), "copy")
    elif scenario == "approved":
        _approve(first, tmp_path)
        _approve(second, tmp_path)
    elif scenario == "listed":
        overrides["artifacts"] = [second]
    batch.report_dir = batch.base / "nested" / "reports"
    _, output = batch.run(corpus, **overrides)
    report = batch.report()
    items = report["items"]
    expected_counts = {
        "candidates": len(items),
        "ready": sum(i["outcome"] == "ready" for i in items),
        "unchanged": 0,
        "held": 2 if scenario == "duplicates" else int(scenario == "mixed"),
        "refused": int(scenario == "mixed"),
        "sampled": 1 if scenario in {"duplicates", "listed"} else 2,
        "would_write": 2 if scenario == "approved" else 0,
    }
    assert report["summary"] == expected_counts
    assert (
        f"Batch 0301-test-1: {', '.join(f'{k} {v}' for k, v in expected_counts.items())}"
        in output.splitlines()
    )
    assert f"Gate: {report['gate']['verdict']}" in output.splitlines()
    assert (
        f"Report: {batch.report_dir / 'batch-0301-test-1.md'} (+ batch-0301-test-1.json)"
        in output.splitlines()
    )
    for item in items:
        if item["outcome"] == "ready":
            assert item["title"] in TITLES
            assert item["version"] == 1
            assert item["markdown_path"].startswith("lokale-forskrifter/0301/")
            assert item["markdown_path"].endswith(".md")
            assert len(item["content_hash"]) == 64
        if item["hold"] == "batch:same_id_in_batch":
            assert item["outcome"] == "held"
            assert item["detail"] == (
                f"2 candidates of this batch mint {item['doc_id']}; which URL is primary is a "
                "recorded human decision (ADR-0016 1d) — promote the primary one alone"
            )
        if item["outcome"] in {"refused", "held"}:
            assert item["detail"] and item["detail"] != "None"
    json_path = batch.report_dir / "batch-0301-test-1.json"
    assert (
        json_path.read_text(encoding="utf-8")
        == json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    )
    assert (batch.report_dir / "batch-0301-test-1.md").read_text(
        encoding="utf-8"
    ) == _expected_markdown(report, corpus)


@pytest.mark.parametrize("suffix", [" ", "X", "\n\n"])
def test_report_preserves_trailing_hold_detail(batch: Batch, corpus: Path, suffix: str) -> None:
    batch.add(page(TITLES[0], "Utskrift fra Lovdata"), "held")
    batch.run(corpus)
    report = batch.report()
    report["items"][-1]["detail"] = "Owner-visible detail" + suffix
    assessment = BatchAssessment.model_validate(
        {k: v for k, v in report.items() if k not in {"summary", "holds_by_reason"}}
    )
    assert report_markdown(assessment, corpus) == _expected_markdown(report, corpus)


@pytest.mark.parametrize("detail", ["Blåbær fra Tromsø", "日本語", "Review 🔍"])
def test_report_json_preserves_literal_unicode(batch: Batch, corpus: Path, detail: str) -> None:
    batch.add(page(TITLES[0], "Utskrift fra Lovdata"), "held")
    batch.run(corpus)
    report = batch.report()
    report["items"][-1]["detail"] = detail
    assessment = BatchAssessment.model_validate(
        {k: v for k, v in report.items() if k not in {"summary", "holds_by_reason"}}
    )

    rendered = report_json(assessment)

    assert detail in rendered
    assert "\\u" not in rendered
    assert json.loads(rendered)["items"][-1]["detail"] == detail
    assert report_json(assessment) == rendered


def test_missing_spec_diagnostic(batch: Batch) -> None:
    missing = batch.base / "missing-spec.json"
    with pytest.raises(PromotionRefusedError) as caught:
        _spec(missing)
    assert str(caught.value) == f"cannot read the batch spec at {missing}: {caught.value.__cause__}"


@pytest.mark.parametrize("rate", [1, "not-a-rate"])
def test_sample_rate_diagnostic(batch: Batch, rate: object) -> None:
    path = batch.spec(sample_rate=rate)
    if isinstance(rate, str):
        with pytest.raises(PromotionRefusedError) as cause:
            parse_sample_rate(rate)
        message = str(cause.value)
    else:
        message = 'sample_rate is a string such as "1" (100 %) or "0.05"; it has no default'
    with pytest.raises(ValidationError) as caught:
        BatchSpec.model_validate_json(path.read_bytes())
    assert caught.value.errors()[0]["msg"] == "Value error, " + message


def test_invalid_noncandidate_refuses_batch_before_any_write(
    batch: Batch, corpus: Path, root: Path, tmp_path: Path
) -> None:
    """S8 refuses the whole classifier file, even rows outside the batch population."""
    _approve(batch.add(page(TITLES[0]), "a"), tmp_path)
    batch.rows.append(batch.rows[0] | {"r1": False, "sha": "invalid"})
    corpus_before = tree(corpus)
    archive_before = tree(root)

    code, output = batch.run(corpus, "--write")

    assert code == 1, output
    assert "line 2" in output
    assert not batch.report_dir.exists()
    assert tree(corpus) == corpus_before
    assert tree(root) == archive_before


def test_reordered_classifier_rows_preserve_assessment_and_sample(
    batch: Batch, corpus: Path
) -> None:
    """The stated batch id draws the same items in any classifier input order."""
    for n, title in enumerate(TITLES):
        batch.add(page(title), f"p{n}")
    code, output = batch.run(corpus, sample_rate="0.5")
    assert code == BLOCKED_EXIT_CODE, output
    before = batch.report()
    batch.rows.reverse()

    code, output = batch.run(corpus, sample_rate="0.5")

    assert code == BLOCKED_EXIT_CODE, output
    after = batch.report()
    assert after["classifier_output_sha256"] != before["classifier_output_sha256"]
    assert after["items"] == before["items"]
    assert after["gate"] == before["gate"]
    assert after["summary"] == before["summary"]
    assert len(_sampled(after)) == 2


def test_later_rejection_revokes_batch_approval(
    batch: Batch, corpus: Path, root: Path, tmp_path: Path
) -> None:
    """A standing approval must still stand when the batch is assessed for writing."""
    sha256 = batch.add(page(TITLES[0]), "a")
    _approve(sha256, tmp_path)
    code, output = batch.run(corpus)
    assert code == 0, output
    assert batch.report()["summary"]["would_write"] == 1
    _approve(sha256, tmp_path, "reject")
    corpus_before = tree(corpus)
    archive_before = tree(root)

    code, output = batch.run(corpus, "--write")

    assert code == BLOCKED_EXIT_CODE, output
    report = batch.report()
    assert report["items"][0]["review"] == "rejected"
    assert report["gate"]["rejected"] == [report["items"][0]["key"]]
    assert report["summary"]["would_write"] == 0
    assert tree(corpus) == corpus_before
    assert tree(root) == archive_before


def test_report_inside_engine_tree_is_refused(
    batch: Batch, corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S8 reports contain archive material and must stay outside the engine tree."""
    engine = batch.base / "engine"
    engine.mkdir()
    monkeypatch.setattr("lovspor.promotion.batch_commands.engine_root", lambda: engine)
    batch.report_dir = engine / "reports"
    batch.add(page(TITLES[0]), "a")
    corpus_before = tree(corpus)

    code, output = batch.run(corpus)

    assert code == 1, output
    assert "report names archive material" in output
    assert not batch.report_dir.exists()
    assert tree(engine) == {}
    assert tree(corpus) == corpus_before


def test_empty_classifier_batch_writes_only_empty_reports(
    batch: Batch, corpus: Path, root: Path
) -> None:
    """An empty population is empty, never a passed gate or a corpus write."""
    corpus_before = tree(corpus)
    archive_before = tree(root)

    code, output = batch.run(corpus, "--write")

    assert code == 0, output
    report = batch.report()
    assert report["items"] == []
    assert report["gate"] == {"verdict": "empty", "rejected": [], "awaiting_review": []}
    assert report["holds_by_reason"] == {}
    assert report["summary"] == dict.fromkeys(
        ("candidates", "ready", "unchanged", "held", "refused", "sampled", "would_write"), 0
    )
    assert tree(corpus) == corpus_before
    assert tree(root) == archive_before
    assert set(tree(batch.report_dir)) == {"batch-0301-test-1.md", "batch-0301-test-1.json"}


def test_same_id_collision_holds_an_unchanged_and_a_ready_candidate(
    batch: Batch, corpus: Path, root: Path, tmp_path: Path
) -> None:
    """S8 holds every colliding candidate, including an already written version."""
    first = batch.add(page(TITLES[0]), "a")
    _approve(first, tmp_path)
    code, output = batch.run(corpus, "--write")
    assert code == 0, output
    git(corpus, "add", "-A")
    git(corpus, "commit", "-q", "-m", "promote original version")
    code, output = batch.run(corpus)
    assert code == 0, output
    assert batch.report()["items"][0]["outcome"] == "unchanged"
    second = batch.add(page(TITLES[0], "En ny bestemmelse."), "copy")
    assert second != first
    _approve(second, tmp_path)
    corpus_before = tree(corpus)
    archive_before = tree(root)

    code, output = batch.run(corpus, "--write")

    assert code == 0, output
    report = batch.report()
    assert {i["key"]["sha256"] for i in report["items"]} == {first, second}
    assert len({i["doc_id"] for i in report["items"]}) == 1
    assert all(i["outcome"] == "held" and not i["sampled"] for i in report["items"])
    assert report["holds_by_reason"] == {"batch:same_id_in_batch": 2}
    assert report["gate"]["verdict"] == "empty"
    assert report["summary"]["would_write"] == 0
    assert tree(corpus) == corpus_before
    assert tree(root) == archive_before
