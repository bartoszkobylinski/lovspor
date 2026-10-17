"""``lovspor promote received``: preview, approve and promote a received text (ADR-0016 A1).

End to end through the CLI only, the way an operator reaches it: the innsyn
workspace is built with the innsyn commands (fetch-population against the
captured KLASS responses, the pilot register, record-received,
classify-received), the decision log lives in a temporary observatory root
and the corpus is a temporary ``lovverk`` checkout. The received file is
named after a case officer, as a kommune's answer often is: the name must
reach no corpus file and no output of ``local``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import Result
from pytest_httpx import HTTPXMock

from lovspor.innsyn.workspace import ENV_INNSYN_DIR
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from tests.unit.innsyn_support import FIXTURES, fetched, innsyn
from tests.unit.promotion_cli_fixtures import (
    KLASS_VERSION,
    REVIEWER,
    REVIEWER_ROLE,
    Decision,
    invoke,
    make_corpus,
    tree,
)
from tests.unit.promotion_fixtures import html_page, minimal_pdf
from tests.unit.test_promotion_render_received import RECEIVED_FRONT_MATTER_KEYS

FJALER = "4646-20261005T074425Z"
REQUESTED = "2009-02-16-306"
RECEIVED_AT = "2026-10-07T12:45:06Z"
FILE_NAME = "Svar på innsynskrav - Ola Nordmann.html"
MAILBOX = "postmottak@fjaler.kommune.no"
LOCAL = "lokale-forskrifter"


@pytest.fixture
def workspace(tmp_path: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = fetched(tmp_path / "innsyn", httpx_mock)
    _innsyn(target, "import-pilot", "--register", str(FIXTURES / "pilot" / "sent.csv"))
    monkeypatch.setenv(ENV_INNSYN_DIR, str(target))
    return target


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "observatory"
    target.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(target))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    return target


@pytest.fixture
def corpus(tmp_path: Path, root: Path) -> Path:
    return make_corpus(tmp_path)


def _innsyn(workspace: Path, *args: str) -> str:
    result = innsyn(workspace, *args)
    assert result.exit_code == 0, result.output
    return result.output


def _receive(workspace: Path, payload: bytes, name: str = FILE_NAME, kind: str = "") -> str:
    """Record ``payload`` as received on Fjaler's request and classify it; its SHA-256."""
    folder = workspace.parent / "mail"
    folder.mkdir(exist_ok=True)
    (folder / name).write_bytes(payload)
    output = _innsyn(
        workspace,
        "record-received",
        "--request",
        FJALER,
        "--file",
        str(folder / name),
        "--at",
        RECEIVED_AT,
    )
    sha256 = output.split("document ")[1].split()[0]
    classify = ("--request", FJALER, "--document", sha256, "--kind", kind or "regulation_text")
    # Only a regulation text or a vedtak answers a requested id.
    answers = ("--answers", REQUESTED) if kind in ("", "regulation_text") else ()
    _innsyn(workspace, "classify-received", *classify, *answers)
    return sha256


def _received(command: str, sha256: str, corpus: Path) -> Result:
    return invoke(
        "promote",
        "received",
        command,
        "--request",
        FJALER,
        "--document",
        sha256,
        "--corpus",
        str(corpus),
        "--klass-version",
        KLASS_VERSION,
    )


def _approve(sha256: str, tmp_path: Path, decision: str = "approve") -> Result:
    document = Decision(decision=decision).write(tmp_path)
    return invoke(
        "promote",
        "received",
        "approve",
        "--request",
        FJALER,
        "--document",
        sha256,
        "--decision",
        str(document),
    )


def _promoted(workspace: Path, corpus: Path, tmp_path: Path) -> tuple[str, Result]:
    sha256 = _receive(workspace, html_page())
    approved = _approve(sha256, tmp_path)
    assert approved.exit_code == 0, approved.output
    return sha256, _received("local", sha256, corpus)


def _log(root: Path) -> list[dict[str, object]]:
    path = root / "promotions.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _markdown(corpus: Path) -> str:
    [path] = (corpus / LOCAL / "4646").glob("*.md")
    return path.read_text(encoding="utf-8")


class TestPreview:
    def test_preview_twice_prints_identical_output(self, workspace: Path, corpus: Path) -> None:
        sha256 = _receive(workspace, html_page())
        first = _received("preview", sha256, corpus)
        assert first.exit_code == 0, first.output
        assert _received("preview", sha256, corpus).stdout == first.stdout
        assert '\nbasis: "received"\n' in first.stdout

    def test_preview_writes_and_records_nothing(
        self, workspace: Path, corpus: Path, root: Path
    ) -> None:
        sha256 = _receive(workspace, html_page())
        before = tree(corpus)
        assert _received("preview", sha256, corpus).exit_code == 0
        assert tree(corpus) == before
        assert _log(root) == []


class TestLocal:
    def test_approve_then_local_writes_the_four_files(
        self, workspace: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _, result = _promoted(workspace, corpus, tmp_path)
        assert result.exit_code == 0, result.output
        written = [line for line in result.stdout.splitlines() if line.startswith("wrote ")]
        assert len(written) == 4
        assert "commit -m 'promote(lokal-forskrift): 4646/" in result.stdout

    def test_a_rerun_is_unchanged_and_writes_nothing(
        self, workspace: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256, first = _promoted(workspace, corpus, tmp_path)
        assert first.exit_code == 0, first.output
        before = tree(corpus)
        again = _received("local", sha256, corpus)
        assert again.exit_code == 0, again.output
        assert again.stdout.startswith("Unchanged: ")
        assert tree(corpus) == before

    def test_the_front_matter_is_the_received_one(
        self, workspace: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(workspace, corpus, tmp_path)
        head = _markdown(corpus).split("---\n")[1]
        keys = tuple(line.split(":", 1)[0] for line in head.splitlines())
        assert keys == RECEIVED_FRONT_MATTER_KEYS
        assert yaml.safe_load(head)["basis"] == "received"

    def test_no_observed_field_or_url_is_written_anywhere(
        self, workspace: Path, corpus: Path, tmp_path: Path
    ) -> None:
        before = tree(corpus)
        _promoted(workspace, corpus, tmp_path)
        new = b"".join(v for p, v in tree(corpus).items() if before.get(p) != v)
        for observed in (b"http", b"observed_at_first", b"source_url"):
            assert observed not in new

    def test_no_file_name_mailbox_or_reviewer_name_is_published(
        self, workspace: Path, corpus: Path, tmp_path: Path, root: Path
    ) -> None:
        _, result = _promoted(workspace, corpus, tmp_path)
        published = b"".join(tree(corpus).values()).decode("utf-8")
        local_part = MAILBOX.partition("@")[0]
        for private in ("Ola Nordmann", "Svar på innsynskrav", MAILBOX, local_part, REVIEWER):
            assert private not in published
            assert private not in result.stdout
        [record] = [r for r in _log(root) if r["kind"] == "promoted"]
        for private in ("Ola Nordmann", "Svar på innsynskrav", MAILBOX, local_part):
            assert private not in json.dumps(record, ensure_ascii=False)

    def test_the_promoted_record_names_the_received_key_and_the_audit(
        self, workspace: Path, corpus: Path, tmp_path: Path, root: Path
    ) -> None:
        sha256, _ = _promoted(workspace, corpus, tmp_path)
        [record] = [r for r in _log(root) if r["kind"] == "promoted"]
        key = {"authority_id": "4646", "innsyn_request": FJALER, "sha256": sha256}
        assert record["artifact"] == key
        audit = record["audit"]
        assert isinstance(audit, dict)
        assert audit["innsyn"]["answers"] == REQUESTED
        assert audit["reviewed_by_role"] == REVIEWER_ROLE

    def test_local_without_an_approval_is_refused(self, workspace: Path, corpus: Path) -> None:
        sha256 = _receive(workspace, html_page())
        before = tree(corpus)
        result = _received("local", sha256, corpus)
        assert result.exit_code == 1
        assert "no human decision is recorded" in result.output
        assert "Traceback" not in result.output
        assert tree(corpus) == before


class TestAScan:
    """A1-2: a text-layer-less PDF is held as empty_text, never OCR'd or published."""

    def test_it_is_held_and_recorded_with_its_received_key(
        self, workspace: Path, corpus: Path, root: Path
    ) -> None:
        sha256 = _receive(workspace, minimal_pdf(()), name="skannet.pdf")
        before = tree(corpus)
        result = _received("local", sha256, corpus)
        assert result.exit_code == 3, result.output
        assert tree(corpus) == before
        [held] = _log(root)
        assert held["kind"] == "held"
        assert held["reason"] == "empty_text"
        assert held["artifact"] == {
            "authority_id": "4646",
            "innsyn_request": FJALER,
            "sha256": sha256,
        }

    def test_it_cannot_be_approved(self, workspace: Path, corpus: Path, tmp_path: Path) -> None:
        sha256 = _receive(workspace, minimal_pdf(()), name="skannet.pdf")
        result = _approve(sha256, tmp_path)
        assert result.exit_code == 1
        assert "nothing to approve: the text is held (empty_text" in result.output
        assert _received("preview", sha256, corpus).exit_code == 3


class TestRefusals:
    def test_minutes_are_refused_before_anything_is_read_or_written(
        self, workspace: Path, corpus: Path, root: Path
    ) -> None:
        sha256 = _receive(workspace, html_page(), kind="minutes")
        before = tree(corpus)
        for command in ("preview", "local"):
            result = _received(command, sha256, corpus)
            assert result.exit_code == 1
            assert "classified minutes; only a regulation_text is promoted" in result.output
        assert tree(corpus) == before
        assert _log(root) == []

    def test_a_withdrawn_received_document_is_not_promoted_again(
        self, workspace: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256, _ = _promoted(workspace, corpus, tmp_path)
        manifest = json.loads((corpus / LOCAL / "manifest.json").read_text(encoding="utf-8"))
        [record] = manifest["documents"].values()
        withdrawal = tmp_path / "withdrawal.json"
        body = {
            "decision": "withdraw",
            "removed_reason": "withdrawn_misclassified",
            "decided_by": REVIEWER,
            "reviewer_role": REVIEWER_ROLE,
            "reason": "Klassifisert feil: et utkast, ikke en vedtatt forskrift.",
        }
        withdrawal.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        withdraw = ("--slug", record["slug"], "--corpus", str(corpus), "--decision")
        done = invoke("promote", "withdraw", "--authority", "4646", *withdraw, str(withdrawal))
        assert done.exit_code == 0, done.output
        before = tree(corpus)
        result = _received("local", sha256, corpus)
        assert result.exit_code == 1
        assert "a withdrawn document is never promoted again" in result.output
        assert tree(corpus) == before

    def test_a_rejected_document_is_refused(
        self, workspace: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _receive(workspace, html_page())
        assert _approve(sha256, tmp_path, decision="reject").exit_code == 0
        result = _received("local", sha256, corpus)
        assert result.exit_code == 1
        assert "the standing decision is reject" in result.output

    def test_an_unset_innsyn_directory_is_refused_cleanly(
        self, workspace: Path, corpus: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha256 = _receive(workspace, html_page())
        monkeypatch.delenv(ENV_INNSYN_DIR)
        for command in ("preview", "local"):
            result = _received(command, sha256, corpus)
            assert result.exit_code == 1
            assert f"Refused: set {ENV_INNSYN_DIR} to the innsyn workspace" in result.output
            assert "Traceback" not in result.output
