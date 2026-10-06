"""``lovspor promote withdraw``: a forward-only withdrawal, honoured on rerun (ADR-0016 4f, S9).

Every state is reached the way an operator reaches it: the register through
``observatory register-source``, approvals through ``promote approve``, the
documents through ``promote local``, the withdrawal through ``promote
withdraw``. The archive and the ``lovverk`` checkout are temporary
(``promotion_cli_fixtures``); nothing is committed by the commands.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import Result

from lovspor.errors import CorpusNotFoundError
from lovspor.mcp import CorpusReader
from lovspor.mcp_local import ServedCorpus
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion.corpus import LocalRecord
from lovspor.promotion.decisions import DECISIONS_FILENAME, ArtifactKey, WithdrawalRecord
from lovspor.promotion.models import RemovedReason
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    PAGE_URL,
    REVIEWER,
    REVIEWER_ROLE,
    Decision,
    approve,
    git,
    invoke,
    make_corpus,
    promote,
    register,
    store,
    tree,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

LOCAL = "lokale-forskrifter"
LF_LINES = (REGULATION_LINES[0], "Dato: FOR-2019-12-12-2077", *REGULATION_LINES[1:])
LF_ID = "lf-20191212-2077"
WITHDRAW_REASON = "Klassifisert feil: siden er en høring, ikke en vedtatt forskrift."


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


def _withdrawal(directory: Path, **changes: object) -> Path:
    body: dict[str, object] = {
        "decision": "withdraw",
        "removed_reason": "withdrawn_misclassified",
        "decided_by": REVIEWER,
        "reviewer_role": REVIEWER_ROLE,
        "reason": WITHDRAW_REASON,
    }
    body.update(changes)
    path = directory / "withdrawal.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def _withdraw(corpus: Path, slug: str, document: Path) -> Result:
    return invoke(
        "promote",
        "withdraw",
        "--authority",
        AUTHORITY,
        "--slug",
        slug,
        "--corpus",
        str(corpus),
        "--decision",
        str(document),
    )


def _promoted(root: Path, corpus: Path, tmp_path: Path, payload: bytes, url: str) -> str:
    """Approve and promote ``payload`` served at ``url``; its artifact SHA-256."""
    sha256 = store(root, payload, url=url, observed_at=FIRST_SEEN + timedelta(minutes=len(url)))
    assert approve(sha256, Decision().write(tmp_path)).exit_code == 0
    result = promote("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    return sha256


def _manifest(corpus: Path) -> dict[str, dict[str, Any]]:
    path = corpus / LOCAL / "manifest.json"
    documents: dict[str, dict[str, Any]] = json.loads(path.read_text("utf-8"))["documents"]
    return documents


def _log_lines(root: Path) -> list[dict[str, Any]]:
    path = root / DECISIONS_FILENAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _commit_subject(result: Result) -> str:
    return next(
        line.split("commit -m ", 1)[1].strip("'")
        for line in result.output.splitlines()
        if "commit -m" in line
    )


def _one_withdrawn(root: Path, corpus: Path, tmp_path: Path) -> tuple[str, str, str]:
    """One promoted document, withdrawn; (doc_id, slug, artifact sha256)."""
    sha256 = _promoted(root, corpus, tmp_path, html_page(LF_LINES), PAGE_URL)
    slug = _manifest(corpus)[LF_ID]["slug"]
    result = _withdraw(corpus, slug, _withdrawal(tmp_path))
    assert result.exit_code == 0, result.output
    return LF_ID, slug, sha256


class TestWithdraw:
    def test_withdrawal_preserves_every_version_and_blocks_every_promoted_artifact(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        first = _promoted(root, corpus, tmp_path, html_page(LF_LINES), PAGE_URL)
        changed = (*LF_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
        later_url = f"{PAGE_URL}-revidert"
        second = store(
            root, html_page(changed), url=later_url, observed_at=FIRST_SEEN + timedelta(days=5)
        )
        assert approve(second, Decision().write(tmp_path)).exit_code == 0
        promoted = promote("local", second, corpus)
        assert promoted.exit_code == 0, promoted.output
        slug = _manifest(corpus)[LF_ID]["slug"]
        path = corpus / LOCAL / AUTHORITY / "observations" / f"{slug}.json"
        before = json.loads(path.read_text(encoding="utf-8"))
        assert [v["version"] for v in before["versions"]] == [1, 2]
        assert "withdrawal" not in before

        result = _withdraw(corpus, slug, _withdrawal(tmp_path))

        assert result.exit_code == 0, result.output
        after = json.loads(path.read_text(encoding="utf-8"))
        assert {k: v for k, v in after.items() if k != "withdrawal"} == before
        withdrawal = _log_lines(root)[-1]
        assert withdrawal["artifacts"] == [
            {"authority_id": AUTHORITY, "sha256": first, "source_url": PAGE_URL},
            {"authority_id": AUTHORITY, "sha256": second, "source_url": later_url},
        ]
        manifest = json.loads((corpus / LOCAL / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["generated_at"] == withdrawal["decided_at"]
        assert manifest["documents"][LF_ID]["version"] == 2
        fresh = make_corpus(tmp_path / "second")
        fresh_before, log_before = tree(fresh), _log_lines(root)
        for artifact in (first, second):
            for command in ("preview", "local"):
                refused = promote(command, artifact, fresh)
                assert refused.exit_code == 1, refused.output
                assert "was withdrawn" in refused.output
        assert tree(fresh) == fresh_before
        assert _log_lines(root) == log_before

    def test_the_record_is_removed_with_its_reason_and_the_markdown_deleted(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        doc_id, slug, _ = _one_withdrawn(root, corpus, tmp_path)

        record = _manifest(corpus)[doc_id]
        assert record["status"] == "removed"
        assert record["removed_reason"] == "withdrawn_misclassified"
        assert record["version"] == 1
        assert not (corpus / LOCAL / AUTHORITY / f"{slug}.md").exists()

    def test_the_observations_are_kept_with_the_withdrawal_naming_a_role_only(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _, slug, _ = _one_withdrawn(root, corpus, tmp_path)

        path = corpus / LOCAL / AUTHORITY / "observations" / f"{slug}.json"
        observations = json.loads(path.read_text(encoding="utf-8"))
        assert [v["version"] for v in observations["versions"]] == [1]
        withdrawal = observations["withdrawal"]
        assert withdrawal["removed_reason"] == "withdrawn_misclassified"
        assert withdrawal["reviewed_by_role"] == REVIEWER_ROLE
        assert withdrawal["reason"] == WITHDRAW_REASON
        assert REVIEWER.split()[0] not in path.read_text(encoding="utf-8")

    def test_the_decision_log_names_the_reviewer_and_every_artifact(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        doc_id, slug, sha256 = _one_withdrawn(root, corpus, tmp_path)

        withdrawal = _log_lines(root)[-1]
        assert withdrawal["kind"] == "withdrawal"
        assert withdrawal["doc_id"] == doc_id
        assert withdrawal["slug"] == slug
        assert withdrawal["decided_by"] == REVIEWER
        assert withdrawal["reviewer_role"] == REVIEWER_ROLE
        assert withdrawal["removed_reason"] == "withdrawn_misclassified"
        assert withdrawal["artifacts"] == [
            {"authority_id": AUTHORITY, "sha256": sha256, "source_url": PAGE_URL}
        ]

    def test_it_never_commits_and_prints_the_withdraw_commit(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _promoted(root, corpus, tmp_path, html_page(LF_LINES), PAGE_URL)
        git(corpus, "add", "--", LOCAL)
        git(corpus, "commit", "-q", "-m", "promote(lokal-forskrift): 0301/x v1")
        head = git(corpus, "rev-parse", "HEAD")
        slug = _manifest(corpus)[LF_ID]["slug"]

        result = _withdraw(corpus, slug, _withdrawal(tmp_path))

        assert result.exit_code == 0, result.output
        assert git(corpus, "rev-parse", "HEAD") == head
        assert _commit_subject(result) == f"withdraw(lokal-forskrift): {AUTHORITY}/{slug}"
        assert f"git -C {corpus} add -- {LOCAL}" in result.output
        assert sha256 not in result.output

    def test_the_withdraw_commit_is_a_removed_event_and_keeps_the_history(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path, html_page(LF_LINES), PAGE_URL)
        git(corpus, "add", "--", LOCAL)
        git(corpus, "commit", "-q", "-m", "promote(lokal-forskrift): 0301/x v1")
        assert invoke("promote", "history", "--corpus", str(corpus)).exit_code == 0
        git(corpus, "add", "--", LOCAL)
        git(corpus, "commit", "-q", "-m", "history(lokal-forskrift): derive history for 1")
        slug = _manifest(corpus)[LF_ID]["slug"]
        history = corpus / LOCAL / AUTHORITY / "history" / f"{slug}.json"
        kept = history.read_bytes()

        result = _withdraw(corpus, slug, _withdrawal(tmp_path))
        git(corpus, "add", "--", LOCAL)
        git(corpus, "commit", "-q", "-m", _commit_subject(result))

        assert history.read_bytes() == kept
        assert git(corpus, "status", "--porcelain") == ""
        log = git(corpus, "log", "--format=%s", "--", f"{LOCAL}/{AUTHORITY}/{slug}.md")
        assert log.splitlines()[0] == f"withdraw(lokal-forskrift): {AUTHORITY}/{slug}"

    def test_the_other_documents_and_the_central_datasets_are_untouched(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path, html_page(LF_LINES), f"{PAGE_URL}-lf")
        _promoted(root, corpus, tmp_path, html_page(), PAGE_URL)
        [other] = [doc_id for doc_id in _manifest(corpus) if doc_id != LF_ID]
        before, other_record = tree(corpus), _manifest(corpus)[other]

        _withdraw(corpus, _manifest(corpus)[LF_ID]["slug"], _withdrawal(tmp_path))

        after = tree(corpus)
        changed = {
            path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
        }
        slug = _manifest(corpus)[LF_ID]["slug"]
        assert changed == {
            f"{LOCAL}/manifest.json",
            f"{LOCAL}/{AUTHORITY}/{slug}.md",
            f"{LOCAL}/{AUTHORITY}/observations/{slug}.json",
        }
        assert _manifest(corpus)[other] == other_record

    def test_a_rerun_writes_nothing_and_records_nothing(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _, slug, _ = _one_withdrawn(root, corpus, tmp_path)
        before, log_before = tree(corpus), _log_lines(root)

        result = _withdraw(corpus, slug, _withdrawal(tmp_path, removed_reason="withdrawn_legal"))

        assert result.exit_code == 0, result.output
        assert "already withdrawn (withdrawn_misclassified)" in result.output
        assert tree(corpus) == before
        assert _log_lines(root) == log_before


class TestWithdrawRefusals:
    @pytest.mark.parametrize("contents", [None, b"{", b"\xff", b"{}"])
    def test_unreadable_observations_refuse_before_recording_or_writing(
        self, root: Path, corpus: Path, tmp_path: Path, contents: bytes | None
    ) -> None:
        _promoted(root, corpus, tmp_path, html_page(LF_LINES), PAGE_URL)
        slug = _manifest(corpus)[LF_ID]["slug"]
        observations = corpus / LOCAL / AUTHORITY / "observations" / f"{slug}.json"
        if contents is None:
            observations.unlink()
        else:
            observations.write_bytes(contents)
        before, log_before = tree(corpus), _log_lines(root)

        result = _withdraw(corpus, slug, _withdrawal(tmp_path))

        assert result.exit_code == 1, result.output
        assert "the withdrawal must name the artifacts it covers" in result.output
        assert tree(corpus) == before
        assert _log_lines(root) == log_before

    def test_an_unknown_slug_is_refused(self, root: Path, corpus: Path, tmp_path: Path) -> None:
        before = tree(corpus)

        result = _withdraw(corpus, "finnes-ikke", _withdrawal(tmp_path))

        assert result.exit_code == 1
        assert f"no local regulation {AUTHORITY}/finnes-ikke" in result.output
        assert tree(corpus) == before

    @pytest.mark.parametrize(
        "changes",
        [
            {"removed_reason": "absent_from_source"},
            {"decision": "reject"},
            {"decided_by": "classifier"},
            {"reviewer_role": None},
            {"reason": " "},
        ],
    )
    def test_a_document_that_is_not_a_human_withdrawal_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path, changes: dict[str, object]
    ) -> None:
        _promoted(root, corpus, tmp_path, html_page(LF_LINES), PAGE_URL)
        before, log_before = tree(corpus), _log_lines(root)
        document = _withdrawal(tmp_path, **changes)
        if changes == {"reviewer_role": None}:
            body = json.loads(document.read_text(encoding="utf-8"))
            del body["reviewer_role"]
            document.write_text(json.dumps(body), encoding="utf-8")

        result = _withdraw(corpus, _manifest(corpus)[LF_ID]["slug"], document)

        assert result.exit_code == 1
        assert "withdrawal document does not validate" in result.output
        assert tree(corpus) == before
        assert _log_lines(root) == log_before

    @pytest.mark.parametrize(
        "reason",
        ["Meldt av Kari Gjennomgang.", "Avklart med saksbehandler, ola@eksempel.kommune.invalid."],
    )
    def test_a_published_reason_with_a_name_or_personal_data_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path, reason: str
    ) -> None:
        _promoted(root, corpus, tmp_path, html_page(LF_LINES), PAGE_URL)
        before = tree(corpus)

        result = _withdraw(
            corpus, _manifest(corpus)[LF_ID]["slug"], _withdrawal(tmp_path, reason=reason)
        )

        assert result.exit_code == 1
        assert tree(corpus) == before

    def test_an_unreadable_withdrawal_document_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        result = _withdraw(corpus, "x", tmp_path / "absent.json")

        assert result.exit_code == 1
        assert "cannot read the withdrawal document" in result.output


class TestRerunHonoursTheLog:
    @pytest.mark.parametrize("command", ["preview", "local"])
    @pytest.mark.parametrize("standing", ["withdrawal", "reject"])
    def test_artifact_refusal_never_reads_the_archived_payload(
        self,
        root: Path,
        corpus: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        command: str,
        standing: str,
    ) -> None:
        if standing == "withdrawal":
            _, _, sha256 = _one_withdrawn(root, corpus, tmp_path)
        else:
            sha256 = store(root, html_page())
            rejected = approve(
                sha256, Decision(decision="reject", reason="Ikke vedtatt.").write(tmp_path)
            )
            assert rejected.exit_code == 0, rejected.output
        before, log_before = tree(corpus), _log_lines(root)

        def unexpected_read(*args: object, **kwargs: object) -> None:
            pytest.fail("a withdrawn or rejected artifact must be refused before payload access")

        monkeypatch.setattr("lovspor.promotion.commands.read_artifact", unexpected_read)
        result = promote(command, sha256, corpus)

        assert result.exit_code == 1, result.output
        expected = "was withdrawn" if standing == "withdrawal" else "standing decision is reject"
        assert expected in result.output
        assert tree(corpus) == before
        assert _log_lines(root) == log_before

    def test_a_withdrawn_document_is_not_promoted_again_even_after_a_new_approval(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _, _, sha256 = _one_withdrawn(root, corpus, tmp_path)
        approve(sha256, Decision().write(tmp_path))
        before = tree(corpus)

        result = promote("local", sha256, corpus)

        assert result.exit_code == 1
        assert f"{LF_ID} was withdrawn (withdrawn_misclassified" in result.output
        assert tree(corpus) == before

    def test_a_withdrawal_holds_in_a_checkout_that_never_saw_it(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _, _, sha256 = _one_withdrawn(root, corpus, tmp_path)
        fresh = make_corpus(tmp_path / "second")
        before = tree(fresh)

        result = promote("local", sha256, fresh)

        assert result.exit_code == 1
        assert f"{LF_ID} was withdrawn" in result.output
        assert tree(fresh) == before

    def test_another_artifact_of_the_withdrawn_document_is_refused_by_its_id(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _one_withdrawn(root, corpus, tmp_path)
        changed = (*LF_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
        later = store(root, html_page(changed), observed_at=FIRST_SEEN + timedelta(days=5))
        approve(later, Decision().write(tmp_path))
        fresh = make_corpus(tmp_path / "second")

        result = promote("local", later, fresh)

        assert result.exit_code == 1
        assert f"{LF_ID} was withdrawn" in result.output

    @pytest.mark.parametrize("command", ["preview", "local"])
    def test_a_withdrawn_artifact_is_refused_by_preview_and_local(
        self, root: Path, corpus: Path, tmp_path: Path, command: str
    ) -> None:
        _, _, sha256 = _one_withdrawn(root, corpus, tmp_path)
        log_before = _log_lines(root)

        result = promote(command, sha256, make_corpus(tmp_path / "second"))

        assert result.exit_code == 1
        assert "was withdrawn" in result.output
        assert _log_lines(root) == log_before

    @pytest.mark.parametrize("command", ["preview", "local"])
    def test_a_standing_reject_is_honoured_before_anything_is_read(
        self, root: Path, corpus: Path, tmp_path: Path, command: str
    ) -> None:
        lines = (
            *REGULATION_LINES[:4],
            "Søker med fødselsnummer 01019012480.",
            *REGULATION_LINES[4:],
        )
        sha256 = store(root, html_page(lines))
        approve(sha256, Decision(decision="reject", reason="Ikke vedtatt.").write(tmp_path))
        log_before = _log_lines(root)

        result = promote(command, sha256, corpus)

        assert result.exit_code == 1
        assert "standing decision is reject" in result.output
        assert _log_lines(root) == log_before

    def test_a_later_approval_supersedes_a_reject(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = store(root, html_page())
        approve(sha256, Decision(decision="reject", reason="Ikke vedtatt.").write(tmp_path))
        approve(sha256, Decision().write(tmp_path))

        assert promote("local", sha256, corpus).exit_code == 0


class TestServing:
    def _served(self, corpus: Path) -> ServedCorpus:
        return ServedCorpus(CorpusReader(corpus), warm=False)

    def _calls(self, served: ServedCorpus, address: str) -> tuple[object, ...]:
        return (
            served.get_law(address),
            served.get_section(address, "1", None, None),
            served.search_laws("renovasjon", LOCAL, 10),
        )

    def test_a_withdrawn_document_is_not_served_and_the_others_are_byte_identical(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path, html_page(LF_LINES), f"{PAGE_URL}-lf")
        _promoted(root, corpus, tmp_path, html_page(), PAGE_URL)
        [other] = [doc_id for doc_id in _manifest(corpus) if doc_id != LF_ID]
        withdrawn_address = f"{AUTHORITY}/{_manifest(corpus)[LF_ID]['slug']}"
        other_address = f"{AUTHORITY}/{_manifest(corpus)[other]['slug']}"
        get_law, get_section, _ = self._calls(self._served(corpus), other_address)
        search_before = self._served(corpus).search_laws("renovasjon", LOCAL, 10)

        _withdraw(corpus, _manifest(corpus)[LF_ID]["slug"], _withdrawal(tmp_path))

        served = self._served(corpus)
        assert served.get_law(other_address) == get_law
        assert served.get_section(other_address, "1", None, None) == get_section
        assert served.search_laws("renovasjon", LOCAL, 10) == [
            hit for hit in search_before if hit["doc_id"] != LF_ID
        ]
        for address in (withdrawn_address, LF_ID):
            with pytest.raises(CorpusNotFoundError):
                served.get_law(address)
            with pytest.raises(CorpusNotFoundError):
                served.get_section(address, "1", None, None)


def test_the_removed_reasons_are_the_adr_set() -> None:
    assert {reason.value for reason in RemovedReason} == {
        "withdrawn_misclassified",
        "withdrawn_identity",
        "withdrawn_personal_data",
        "withdrawn_legal",
    }


def _withdrawal_record(artifacts: tuple[ArtifactKey, ...], reason: str) -> WithdrawalRecord:
    return WithdrawalRecord(
        doc_id=LF_ID,
        authority_id=AUTHORITY,
        slug="s",
        removed_reason=RemovedReason.WITHDRAWN_IDENTITY,
        artifacts=artifacts,
        decided_by=REVIEWER,
        reviewer_role=REVIEWER_ROLE,
        decided_at=FIRST_SEEN,
        reason=reason,
    )


class TestModels:
    def test_a_manifest_record_outside_the_closed_set_does_not_read(self) -> None:
        record = {
            "status": "removed",
            "removed_reason": "absent_from_source",
            "slug": "s",
            "title": "t",
            "markdown_path": f"{LOCAL}/{AUTHORITY}/s.md",
            "renderer_version": 1,
            "last_seen": "2026-08-19T15:17:23Z",
            "authority_id": AUTHORITY,
            "authority_type": "kommune",
            "content_hash": "c" * 64,
            "version": 1,
            "extractor_version": 1,
        }

        with pytest.raises(ValidationError):
            LocalRecord.model_validate(record)
        readable = LocalRecord.model_validate(record | {"removed_reason": "withdrawn_legal"})
        assert readable.removed_reason is RemovedReason.WITHDRAWN_LEGAL

    def test_a_withdrawal_record_keeps_the_reviewer_name_out_of_its_reason(self) -> None:
        with pytest.raises(ValidationError):
            _withdrawal_record((), f"Meldt av {REVIEWER}.")

    def test_a_withdrawal_covers_its_artifacts_and_its_id_only(self) -> None:
        key = ArtifactKey(authority_id=AUTHORITY, sha256="a" * 64, source_url=PAGE_URL)
        other = key.model_copy(update={"sha256": "b" * 64})

        withdrawal = _withdrawal_record((key,), "To dokumenter er ett.")

        assert withdrawal.withdraws(key, None)
        assert withdrawal.withdraws(other, LF_ID)
        assert not withdrawal.withdraws(other, None)
        assert not withdrawal.withdraws(other, "lf-20200101-0001")
