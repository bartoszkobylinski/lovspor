"""``lovspor promote``: approve, preview, local and history, end to end (ADR-0016 S3).

Every state here is reached the way an operator reaches it — the register
through ``observatory register-source``, the decision log through ``promote
approve``, the corpus through ``promote local`` — over a synthetic archive and
a temporary ``lovverk`` checkout (``promotion_cli_fixtures``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion.commands import HELD_EXIT_CODE
from lovspor.promotion.decisions import DECISIONS_FILENAME
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    PAGE_URL,
    REVIEWER,
    Decision,
    approve,
    git,
    invoke,
    make_corpus,
    promote,
    register,
    store,
    store_failure,
    tree,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

FNR_LINE = "Søker med fødselsnummer 01019012480 er registrert."
LK_ID = "lk-0301-aa8ae774921d"


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


def _approved(root: Path, tmp_path: Path, payload: bytes = b"") -> str:
    sha256 = store(root, payload or html_page())
    result = approve(sha256, Decision().write(tmp_path))
    assert result.exit_code == 0, result.output
    return sha256


def _log_lines(root: Path) -> list[dict[str, object]]:
    path = root / DECISIONS_FILENAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _manifest(corpus: Path) -> dict[str, dict[str, object]]:
    path = corpus / "lokale-forskrifter" / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))["documents"]


class TestPromoteLocal:
    def test_an_approved_artifact_writes_the_document_its_observations_and_the_manifest(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        before = tree(corpus)

        result = promote("local", sha256, corpus)

        assert result.exit_code == 0, result.output
        changed = {path for path, data in tree(corpus).items() if before.get(path) != data}
        [doc_id] = _manifest(corpus)
        slug = _manifest(corpus)[doc_id]["slug"]
        assert changed == {
            f"lokale-forskrifter/{AUTHORITY}/{slug}.md",
            f"lokale-forskrifter/{AUTHORITY}/evidence/{slug}.json",
            f"lokale-forskrifter/{AUTHORITY}/observations/{slug}.json",
            "lokale-forskrifter/manifest.json",
        }
        assert f"promote(lokal-forskrift): {AUTHORITY}/{slug} v1" in result.output
        assert "git -C" in result.output

    def test_the_root_manifest_and_the_central_datasets_are_never_touched(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        before = tree(corpus)

        promote("local", sha256, corpus)

        after = tree(corpus)
        for path, data in before.items():
            if not path.startswith("lokale-forskrifter/"):
                assert after[path] == data
        assert {p for p in after if not p.startswith("lokale-forskrifter/")} == {
            p for p in before if not p.startswith("lokale-forskrifter/")
        }

    def test_a_rerun_writes_nothing_and_records_nothing(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        promote("local", sha256, corpus)
        after_first, log_after_first = tree(corpus), _log_lines(root)

        result = promote("local", sha256, corpus)

        assert result.exit_code == 0, result.output
        assert "Unchanged" in result.output
        assert tree(corpus) == after_first
        assert _log_lines(root) == log_after_first

    def test_a_rerun_after_the_commit_leaves_the_checkout_clean(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        promote("local", sha256, corpus)
        git(corpus, "add", "-A")
        git(corpus, "commit", "-q", "-m", "promote(lokal-forskrift): 0301/x v1")

        promote("local", sha256, corpus)

        assert git(corpus, "status", "--porcelain") == ""

    def test_without_an_approval_nothing_is_written(self, root: Path, corpus: Path) -> None:
        sha256 = store(root, html_page())
        before = tree(corpus)

        result = promote("local", sha256, corpus)

        assert result.exit_code == 1
        assert "promote approve" in result.output
        assert tree(corpus) == before

    @pytest.mark.parametrize("standing", ["reject", "hold"])
    def test_a_standing_reject_or_hold_is_not_promoted(
        self, root: Path, corpus: Path, tmp_path: Path, standing: str
    ) -> None:
        sha256 = _approved(root, tmp_path)
        approve(sha256, Decision(decision=standing, reason="Ikke vedtatt.").write(tmp_path))
        before = tree(corpus)

        result = promote("local", sha256, corpus)

        assert result.exit_code == 1
        assert f"standing decision is {standing}" in result.output
        assert tree(corpus) == before

    def test_the_artifact_can_be_named_by_the_url_that_served_it(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _approved(root, tmp_path)

        result = promote("local", PAGE_URL, corpus)

        assert result.exit_code == 0, result.output

    def test_a_url_that_served_two_texts_is_refused_with_both_listed(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        first = _approved(root, tmp_path)
        changed = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
        second = store(root, html_page(changed), observed_at=FIRST_SEEN + timedelta(days=3))

        result = promote("local", PAGE_URL, corpus)

        assert result.exit_code == 1
        assert first in result.output
        assert second in result.output

    def test_the_observations_read_end_at_the_approval(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        store(root, html_page(), observed_at=datetime(2099, 1, 1, tzinfo=UTC))

        promote("local", sha256, corpus)

        observations = _observations_file(corpus)
        [version] = observations["versions"]
        assert version["observation_count"] == 1
        assert version["observed_at_last"] == "2026-08-19T15:17:23Z"

    def test_the_source_status_is_the_last_fetch_of_the_primary_url(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        store(root, html_page())
        store_failure(root, FIRST_SEEN + timedelta(days=1))
        sha256 = store(root, html_page(), observed_at=FIRST_SEEN + timedelta(days=2))
        approve(sha256, Decision().write(tmp_path))
        store_failure(root, FIRST_SEEN + timedelta(days=3))

        promote("local", sha256, corpus)

        observations = _observations_file(corpus)
        assert observations["source_status"] == {
            "outcome": "http_error",
            "http_status": 404,
            "observed_at": "2026-08-22T15:17:23Z",
        }
        assert observations["versions"][0]["observation_count"] == 2

    def test_a_relative_corpus_path_is_refused(self, root: Path, tmp_path: Path) -> None:
        sha256 = _approved(root, tmp_path)

        result = promote("local", sha256, Path("lovverk"))

        assert result.exit_code == 1
        assert "absolute" in result.output

    def test_a_corpus_without_the_local_dataset_skeleton_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        (corpus / "lokale-forskrifter" / "manifest.json").unlink()

        result = promote("local", sha256, corpus)

        assert result.exit_code == 1
        assert "lokale-forskrifter/manifest.json" in result.output

    def test_the_engine_repository_is_refused_as_a_corpus(self, root: Path, tmp_path: Path) -> None:
        sha256 = _approved(root, tmp_path)
        engine = Path(__file__).resolve().parents[2]

        result = promote("local", sha256, engine)

        assert result.exit_code == 1
        assert "overlaps" in result.output

    def test_the_observatory_archive_is_refused_as_a_corpus(
        self, root: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)

        result = promote("local", sha256, root)

        assert result.exit_code == 1
        assert "overlaps" in result.output


def _observations_file(corpus: Path) -> dict[str, object]:
    [path] = (corpus / "lokale-forskrifter" / AUTHORITY / "observations").glob("*.json")
    return json.loads(path.read_text(encoding="utf-8"))


class TestHolds:
    def test_personal_data_writes_nothing_and_is_recorded_without_the_value(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        lines = (*REGULATION_LINES[:4], FNR_LINE, *REGULATION_LINES[4:])
        sha256 = store(root, html_page(lines))
        before = tree(corpus)

        result = promote("local", sha256, corpus)

        assert result.exit_code == HELD_EXIT_CODE
        assert "personal_data" in result.output
        assert tree(corpus) == before
        [held] = _log_lines(root)
        assert held["kind"] == "held"
        assert held["stage"] == "extraction"
        assert held["reason"] == "personal_data"
        assert "01019012480" not in json.dumps(held)

    def test_a_repeated_hold_is_recorded_once(self, root: Path, corpus: Path) -> None:
        lines = (*REGULATION_LINES[:4], FNR_LINE, *REGULATION_LINES[4:])
        sha256 = store(root, html_page(lines))

        promote("local", sha256, corpus)
        promote("local", sha256, corpus)

        assert len(_log_lines(root)) == 1

    def test_no_id_and_no_vedtaksdato_is_an_identity_hold(self, root: Path, corpus: Path) -> None:
        undated = (
            REGULATION_LINES[0],
            "Vedtatt av kommunestyret med hjemmel i forurensningsloven § 30.",
            *REGULATION_LINES[2:],
        )
        sha256 = store(root, html_page(undated))
        before = tree(corpus)

        result = promote("local", sha256, corpus)

        assert result.exit_code == HELD_EXIT_CODE
        assert "no_identity" in result.output
        assert tree(corpus) == before
        assert _log_lines(root)[-1]["stage"] == "identity"

    def test_an_lf_id_a_central_record_already_carries_is_held(
        self, root: Path, corpus: Path
    ) -> None:
        lines = (REGULATION_LINES[0], "Dato: FOR-2020-01-14-63", *REGULATION_LINES[1:])
        sha256 = store(root, html_page(lines))

        result = promote("local", sha256, corpus)

        assert result.exit_code == HELD_EXIT_CODE
        assert "central_collision" in result.output

    def test_a_held_text_cannot_be_approved(self, root: Path, tmp_path: Path) -> None:
        lines = (*REGULATION_LINES[:4], FNR_LINE, *REGULATION_LINES[4:])
        sha256 = store(root, html_page(lines))

        result = approve(sha256, Decision().write(tmp_path))

        assert result.exit_code == 1
        assert "nothing to approve" in result.output
        assert _log_lines(root) == []


class TestIdentityInTheCorpus:
    def test_an_lf_id_from_the_identification_block_keys_the_manifest(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        lines = (REGULATION_LINES[0], "Dato: FOR-2019-12-12-2077", *REGULATION_LINES[1:])
        sha256 = _approved(root, tmp_path, html_page(lines))

        promote("local", sha256, corpus)

        assert list(_manifest(corpus)) == ["lf-20191212-2077"]

    def test_a_fallback_id_is_stable_across_checkouts(self, root: Path, tmp_path: Path) -> None:
        sha256 = _approved(root, tmp_path)
        first, second = make_corpus(tmp_path / "a"), make_corpus(tmp_path / "b")

        promote("local", sha256, first)
        promote("local", sha256, second)

        assert tree(first) == tree(second)
        assert list(_manifest(first)) == [LK_ID]


class TestNextVersion:
    def test_new_content_at_the_same_url_is_the_next_version_under_the_same_slug(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        promote("local", _approved(root, tmp_path), corpus)
        changed = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
        later = FIRST_SEEN + timedelta(days=5)
        sha256 = store(root, html_page(changed), observed_at=later)
        approve(sha256, Decision().write(tmp_path))

        result = promote("local", sha256, corpus)

        assert result.exit_code == 0, result.output
        record = _manifest(corpus)[LK_ID]
        assert record["version"] == 2
        assert f"{AUTHORITY}/{record['slug']} v2" in result.output
        versions = _observations_file(corpus)["versions"]
        assert [v["version"] for v in versions] == [1, 2]

    def test_content_first_observed_before_the_current_version_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        changed = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
        earlier = store(root, html_page(changed), observed_at=FIRST_SEEN - timedelta(days=5))
        promote("local", _approved(root, tmp_path), corpus)
        approve(earlier, Decision().write(tmp_path))
        before = tree(corpus)

        result = promote("local", earlier, corpus)

        assert result.exit_code == 1
        assert "backfill" in result.output
        assert tree(corpus) == before


class TestPreview:
    def test_preview_prints_the_rendering_and_writes_and_records_nothing(
        self, root: Path, corpus: Path
    ) -> None:
        sha256 = store(root, html_page())
        before = tree(corpus)

        result = promote("preview", sha256, corpus)

        assert result.exit_code == 0, result.output
        assert f'id: "{LK_ID}"' in result.output
        assert "# Forskrift om renovasjon og slam" in result.output
        assert tree(corpus) == before
        assert _log_lines(root) == []

    def test_a_preview_of_a_held_text_records_nothing(self, root: Path, corpus: Path) -> None:
        lines = (*REGULATION_LINES[:4], FNR_LINE, *REGULATION_LINES[4:])
        sha256 = store(root, html_page(lines))

        result = promote("preview", sha256, corpus)

        assert result.exit_code == HELD_EXIT_CODE
        assert _log_lines(root) == []


class TestApprove:
    def test_an_approval_names_the_text_and_the_reviewer(self, root: Path, tmp_path: Path) -> None:
        sha256 = _approved(root, tmp_path)

        [decision] = _log_lines(root)

        assert decision["kind"] == "decision"
        assert decision["decision"] == "approve"
        assert decision["decided_by"] == REVIEWER
        assert decision["artifact"] == {
            "authority_id": AUTHORITY,
            "sha256": sha256,
            "source_url": PAGE_URL,
        }
        assert decision["content_hash"] is not None
        assert decision["extractor_version"] == 4

    @pytest.mark.parametrize("machine", ["classifier", "lovspor", " Classifier "])
    def test_a_machine_cannot_be_the_reviewer(
        self, root: Path, tmp_path: Path, machine: str
    ) -> None:
        sha256 = store(root, html_page())

        result = approve(sha256, Decision(decided_by=machine).write(tmp_path))

        assert result.exit_code == 1
        assert _log_lines(root) == []

    def test_a_reason_with_personal_data_is_refused(self, root: Path, tmp_path: Path) -> None:
        sha256 = store(root, html_page())
        reason = "Avklart med saksbehandler, ola@eksempel.kommune.invalid."

        result = approve(sha256, Decision(reason=reason).write(tmp_path))

        assert result.exit_code == 1
        assert "personal data" in result.output
        assert _log_lines(root) == []

    def test_an_unreadable_decision_document_is_refused(self, root: Path, tmp_path: Path) -> None:
        sha256 = store(root, html_page())

        result = approve(sha256, tmp_path / "absent.json")

        assert result.exit_code == 1
        assert "cannot read the decision document" in result.output

    def test_an_artifact_the_archive_does_not_hold_is_refused(
        self, root: Path, tmp_path: Path
    ) -> None:
        result = approve("0" * 64, Decision().write(tmp_path))

        assert result.exit_code == 1
        assert "names 0 artifact(s)" in result.output

    def test_classifier_evidence_travels_into_the_audit_record(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = store(root, html_page())
        classifier = {
            "classifier_version": "rules-3",
            "class_name": "forskrift",
            "evidence": ["R12: tittel begynner med Forskrift"],
        }
        approve(sha256, Decision(classifier=classifier).write(tmp_path))

        promote("local", sha256, corpus)

        audit = _observations_file(corpus)["versions"][0]["promotion"]
        assert audit["classifier"] == classifier


class TestHistory:
    def test_the_commit_is_promotion_time_and_observed_at_stays_in_the_front_matter(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        result = promote("local", sha256, corpus)
        subject = next(
            line.split("commit -m ", 1)[1].strip("'")
            for line in result.output.splitlines()
            if "commit -m" in line
        )
        git(corpus, "add", "--", "lokale-forskrifter")
        git(corpus, "commit", "-q", "-m", subject)

        history = _history(corpus)

        [event] = history["events"]
        assert event["type"] == "added"
        assert event["subject"] == subject
        committed = datetime.fromisoformat(git(corpus, "log", "-1", "--format=%aI").strip())
        assert event["date"] == committed.astimezone(UTC).date().isoformat()
        assert event["date"] != FIRST_SEEN.date().isoformat()
        markdown = next((corpus / "lokale-forskrifter" / AUTHORITY).glob("*.md"))
        assert 'observed_at_first: "2026-08-19T15:17:23Z"' in markdown.read_text(encoding="utf-8")

    def test_a_second_version_is_an_update_event(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promote_and_commit(corpus, _approved(root, tmp_path))
        changed = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
        sha256 = store(root, html_page(changed), observed_at=FIRST_SEEN + timedelta(days=5))
        approve(sha256, Decision().write(tmp_path))
        _promote_and_commit(corpus, sha256)

        history = _history(corpus)

        assert [event["type"] for event in history["events"]] == ["updated", "added"]

    def test_history_is_idempotent(self, root: Path, corpus: Path, tmp_path: Path) -> None:
        _promote_and_commit(corpus, _approved(root, tmp_path))
        _history(corpus)
        git(corpus, "add", "-A")
        git(
            corpus, "commit", "-q", "-m", "history(lokal-forskrift): derive history for 1 documents"
        )

        result = promote_history(corpus)

        assert "History is current" in result
        assert git(corpus, "status", "--porcelain") == ""

    def test_before_any_commit_there_is_no_history_to_write(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        promote("local", _approved(root, tmp_path), corpus)

        assert "History is current" in promote_history(corpus)
        assert not list((corpus / "lokale-forskrifter" / AUTHORITY).glob("history/*"))


def _promote_and_commit(corpus: Path, sha256: str) -> None:
    result = promote("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    subject = next(
        line.split("commit -m ", 1)[1].strip("'")
        for line in result.output.splitlines()
        if "commit -m" in line
    )
    git(corpus, "add", "--", "lokale-forskrifter")
    git(corpus, "commit", "-q", "-m", subject)


def promote_history(corpus: Path) -> str:
    result = invoke("promote", "history", "--corpus", str(corpus))
    assert result.exit_code == 0, result.output
    return result.output


def _history(corpus: Path) -> dict[str, list[dict[str, object]]]:
    promote_history(corpus)
    [path] = (corpus / "lokale-forskrifter" / AUTHORITY / "history").glob("*.json")
    return json.loads(path.read_text(encoding="utf-8"))


class TestRefusals:
    def test_preview_of_a_promoted_version_says_unchanged(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        promote("local", sha256, corpus)

        result = promote("preview", sha256, corpus)

        assert result.exit_code == 0
        assert "unchanged" in result.output

    def test_a_withdrawn_id_is_not_promoted_again(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        promote("local", sha256, corpus)
        _edit_record(corpus, status="removed", removed_reason="withdrawn_misclassified")

        result = promote("local", sha256, corpus)

        assert result.exit_code == 1
        assert "withdrawn" in result.output

    def test_an_id_filed_under_another_authority_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        lines = (REGULATION_LINES[0], "Dato: FOR-2019-12-12-2077", *REGULATION_LINES[1:])
        sha256 = _approved(root, tmp_path, html_page(lines))
        promote("local", sha256, corpus)
        _edit_record(corpus, authority_id="4601", content_hash="0" * 64)

        result = promote("local", sha256, corpus)

        assert result.exit_code == 1
        assert "filed under authority 4601" in result.output

    def test_an_approval_reason_mentioning_nlod_is_never_written(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = store(root, html_page())
        approve(sha256, Decision(reason="Kommunal tekst, ikke NLOD-data.").write(tmp_path))
        before = tree(corpus)

        result = promote("local", sha256, corpus)

        assert result.exit_code == 1
        assert "NLOD" in result.output
        assert tree(corpus) == before

    def test_a_local_manifest_that_does_not_read_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        (corpus / "lokale-forskrifter" / "manifest.json").write_text(
            '{"documents": [], "version": 1}', encoding="utf-8"
        )

        result = promote("local", sha256, corpus)

        assert result.exit_code == 1
        assert "local manifest" in result.output

    def test_a_central_manifest_that_does_not_read_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        (corpus / "manifest.json").write_text("[]", encoding="utf-8")

        result = promote("local", sha256, corpus)

        assert result.exit_code == 1
        assert "central manifest" in result.output


def _edit_record(corpus: Path, **changes: object) -> None:
    path = corpus / "lokale-forskrifter" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    [record] = manifest["documents"].values()
    record.update(changes)
    path.write_text(json.dumps(manifest), encoding="utf-8")


class TestOperatorReachability:
    def test_approval_and_promotion_are_reached_through_supported_commands_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Can an operator reach a promoted document using only supported interfaces?

        The register is written by ``observatory register-source``, the
        artifact by the observatory's capture writer (``append_artifact``,
        what a sweep calls), the approval by ``promote approve`` and the
        corpus by ``promote local`` / ``promote history``. Nothing is
        constructed by hand: no decision-log line, no manifest record.
        """
        observatory = tmp_path / "archive"
        observatory.mkdir()
        monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
        monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
        register(observatory)
        checkout = make_corpus(tmp_path)
        sha256 = store(observatory, html_page())

        steps = [
            promote("preview", sha256, checkout),
            approve(sha256, Decision().write(tmp_path)),
            promote("local", sha256, checkout),
        ]

        assert [step.exit_code for step in steps] == [0, 0, 0]
        assert [line["kind"] for line in _log_lines(observatory)] == ["decision", "promoted"]
        assert list(_manifest(checkout)) == [LK_ID]
        engine = Path(__file__).resolve().parents[2]
        assert not list(engine.rglob(DECISIONS_FILENAME))
