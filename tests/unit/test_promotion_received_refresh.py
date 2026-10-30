"""``lovspor promote received refresh``: re-render an unchanged received version (#645).

End to end through the CLI, the way an operator reaches it: a repealed
Lovdata print is received, approved and promoted with ``promote received
local``, then its Markdown is put back the way the renderer wrote it before
#639 (no ``status``, no ``ikraft_end``). ``local`` cannot repair that — the
version is unchanged — and ``refresh`` must, without moving the version.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import Result
from pytest_httpx import HTTPXMock

from lovspor.errors import PromotionRefusedError
from lovspor.innsyn.workspace import ENV_INNSYN_DIR
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot
from lovspor.promotion import received_commands
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import DecisionLog
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.migrate import published
from lovspor.promotion.plan import Held
from lovspor.promotion.received_refresh import Refresh, _body, _same_version, write_refreshes
from tests.unit.innsyn_support import FIXTURES, fetched
from tests.unit.promotion_cli_fixtures import AUTHORITY, git, invoke, make_corpus, register, tree
from tests.unit.promotion_fixtures import minimal_pdf
from tests.unit.test_promotion_lovdata_validity import REPEALED_PRINT
from tests.unit.test_promotion_migrate import _approve as approve_observed
from tests.unit.test_promotion_migrate import _promoted_once
from tests.unit.test_promotion_received_commands import (
    FJALER,
    LOCAL,
    _approve,
    _innsyn,
    _log,
    _receive,
    _received,
)
from tests.unit.test_promotion_received_decisions import _withdrawal
from tests.unit.test_promotion_refresh import _straks

_VALIDITY = re.compile(r"\n(?:status|ikraft_end): [^\n]*")


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


@pytest.fixture
def promoted(workspace: Path, corpus: Path, tmp_path: Path) -> tuple[str, Path]:
    """The received print, promoted: its SHA-256 and its Markdown file."""
    sha256 = _receive(workspace, minimal_pdf(REPEALED_PRINT), name="utskrift.pdf")
    assert _approve(sha256, tmp_path).exit_code == 0
    result = _received("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    [path] = (corpus / LOCAL / "4646").glob("*.md")
    return sha256, path


def _refresh(corpus: Path, *extra: str) -> Result:
    return invoke("promote", "received", "refresh", "--corpus", str(corpus), *extra)


def _stale(path: Path) -> str:
    """Rewrite ``path`` as the renderer wrote it before #639; the current rendering."""
    current = path.read_text(encoding="utf-8")
    assert '\nstatus: "repealed"\n' in current
    path.write_text(_VALIDITY.sub("", current), encoding="utf-8")
    return current


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _slug(path: Path) -> str:
    return path.stem


class TestRefresh:
    def test_local_cannot_repair_the_stale_rendering(
        self, promoted: tuple[str, Path], corpus: Path
    ) -> None:
        sha256, path = promoted
        _stale(path)
        before = tree(corpus)
        result = _received("local", sha256, corpus)
        assert result.exit_code == 0, result.output
        assert result.stdout.startswith("Unchanged: ")
        assert tree(corpus) == before

    def test_it_rewrites_only_the_markdown_and_prints_the_commit(
        self, promoted: tuple[str, Path], corpus: Path, root: Path
    ) -> None:
        _, path = promoted
        current = _stale(path)
        before, log = tree(corpus), _log(root)
        result = _refresh(corpus)
        assert result.exit_code == 0, result.output
        assert path.read_text(encoding="utf-8") == current
        changed = {p for p, v in tree(corpus).items() if before.get(p) != v}
        assert changed == {p for p in before if p.endswith(f"{_slug(path)}.md")}
        assert _log(root) == log
        subject = f"migration(lokal-forskrift): re-render 4646/{_slug(path)} front matter"
        assert f"commit -m '{subject}'" in result.stdout
        assert f"wrote {LOCAL}/4646/{path.name}" in result.stdout
        assert (
            "lf-20010203-9001 v1: front matter re-rendered; "
            "version, content_hash and provenance unchanged\n"
        ) in result.stdout

    def test_a_current_rendering_is_left_alone(
        self, promoted: tuple[str, Path], corpus: Path
    ) -> None:
        before = tree(corpus)
        result = _refresh(corpus)
        assert result.exit_code == 0, result.output
        assert result.stdout == (
            "lf-20010203-9001: unchanged; its rendering is current\n"
            "Nothing to refresh; nothing written, nothing to commit.\n"
        )
        assert tree(corpus) == before

    def test_a_dry_run_prints_the_diff_and_writes_nothing(
        self, promoted: tuple[str, Path], corpus: Path
    ) -> None:
        _stale(promoted[1])
        before = tree(corpus)
        result = _refresh(corpus, "--dry-run")
        assert result.exit_code == 0, result.output
        assert '\n+status: "repealed"\n' in result.stdout
        assert result.stdout.endswith("Dry run: nothing written.\n")
        assert tree(corpus) == before

    def test_an_observed_document_is_not_read(
        self, promoted: tuple[str, Path], corpus: Path
    ) -> None:
        _, path = promoted
        _stale(path)
        _edit(path, '\nbasis: "received"\n', '\nbasis: "observed"\n')
        before = tree(corpus)
        result = _refresh(corpus)
        assert result.exit_code == 0, result.output
        assert result.stdout == "Nothing to refresh; nothing written, nothing to commit.\n"
        assert tree(corpus) == before


class TestRefusals:
    def _refused(self, corpus: Path) -> str:
        before = tree(corpus)
        result = _refresh(corpus)
        assert result.exit_code == 1, result.output
        assert "Traceback" not in result.output
        assert tree(corpus) == before
        assert result.stderr.startswith("Refused: lf-20010203-9001: ")
        return result.stderr

    def test_without_a_standing_approval(
        self, promoted: tuple[str, Path], corpus: Path, tmp_path: Path
    ) -> None:
        sha256, path = promoted
        _stale(path)
        assert _approve(sha256, tmp_path, decision="hold").exit_code == 0
        assert "the standing decision is hold" in self._refused(corpus)

    def test_a_changed_body(self, promoted: tuple[str, Path], corpus: Path) -> None:
        _, path = promoted
        _stale(path)
        path.write_text(path.read_text(encoding="utf-8") + "\nEt tillegg.\n", encoding="utf-8")
        assert "its body would change" in self._refused(corpus)

    def test_a_moved_provenance_field(self, promoted: tuple[str, Path], corpus: Path) -> None:
        _, path = promoted
        _stale(path)
        text = path.read_text(encoding="utf-8")
        received_at = re.search(r'\nreceived_at: "([^"]+)"\n', text)
        assert received_at is not None
        _edit(path, received_at.group(1), "2026-01-01T00:00:00Z")
        assert "would change received_at;" in self._refused(corpus)

    def test_a_new_text_is_a_new_version(self, promoted: tuple[str, Path], corpus: Path) -> None:
        manifest_path = corpus / LOCAL / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        [record] = manifest["documents"].values()
        record["content_hash"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        reason = self._refused(corpus)
        assert "That is a new version, not a re-render" in reason
        content_hash = re.search(
            r'\ncontent_hash: "([^"]+)"', promoted[1].read_text(encoding="utf-8")
        )
        assert content_hash is not None
        assert reason.endswith(
            f"(content_hash {content_hash.group(1)[:12]}…). "
            "That is a new version, not a re-render: "
            "approve it and run `lovspor promote received local`\n"
        )

    def test_front_matter_naming_no_received_source(
        self, promoted: tuple[str, Path], corpus: Path
    ) -> None:
        _, path = promoted
        text = path.read_text(encoding="utf-8")
        path.write_text(re.sub(r"\ninnsyn_request: [^\n]*", "", text), encoding="utf-8")
        assert "does not name its received source (KeyError)" in self._refused(corpus)


@pytest.mark.parametrize("mode", ["write", "dry-run", "refusal"])
def test_batch_refresh_preserves_other_files_and_continues_after_a_refusal(
    mode: str,
    promoted: tuple[str, Path],
    workspace: Path,
    corpus: Path,
    root: Path,
    tmp_path: Path,
) -> None:
    """Operations #645 promises per-document refusal and Markdown-only batch writes."""
    _, first = promoted
    lines = tuple(line.replace("2001-02-03-9001", "2001-02-03-9006") for line in REPEALED_PRINT)
    sha256 = _receive(workspace, minimal_pdf(lines), name="second.pdf")
    assert _approve(sha256, tmp_path).exit_code == 0
    result = _received("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    [second] = [path for path in first.parent.glob("*.md") if path != first]
    first_current, second_current = _stale(first), _stale(second)
    if mode == "refusal":
        # The lower id is visited first: its refusal must not stop the later refresh.
        first.write_text(first.read_text(encoding="utf-8") + "\nEt tillegg.\n", encoding="utf-8")
    before, log, held = tree(corpus), _log(root), tree(workspace)
    result = _refresh(corpus, *(("--dry-run",) if mode == "dry-run" else ()))
    assert result.exit_code == (1 if mode == "refusal" else 0), result.output
    expected = before.copy()
    if mode != "dry-run":
        expected[second.relative_to(corpus).as_posix()] = second_current.encode("utf-8")
        if mode == "write":
            expected[first.relative_to(corpus).as_posix()] = first_current.encode("utf-8")
    assert tree(corpus) == expected
    assert _log(root) == log
    assert tree(workspace) == held
    if mode == "refusal":
        assert "Refused: lf-20010203-9001: its body would change" in result.stderr
        assert f"re-render 4646/{second.stem} front matter" in result.stdout
    elif mode == "dry-run":
        assert result.stdout.count('+status: "repealed"') == 2
        assert result.stdout.endswith("Dry run: nothing written.\n")
    else:
        assert "re-render front matter of 2 received documents" in result.stdout
        again = _refresh(corpus)
        assert again.exit_code == 0, again.output
        assert again.stdout.count("unchanged; its rendering is current") == 2
        assert "nothing to commit" in again.stdout
        assert tree(corpus) == expected


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_refresh_refuses_a_missing_or_corrupt_named_source(
    damage: str, promoted: tuple[str, Path], workspace: Path, corpus: Path
) -> None:
    """A refresh must use the same hash-verified held source as received local."""
    sha256, path = promoted
    _stale(path)
    blob = workspace / "received" / sha256[:2] / sha256
    assert blob.is_file()
    if damage == "missing":
        blob.unlink()
    else:
        blob.write_bytes(b"not the received document")
    reason = TestRefusals()._refused(corpus)
    assert (
        "missing or unreadable" if damage == "missing" else "does not hash to its name"
    ) in reason


@pytest.mark.parametrize("skip", ["removed", "observed"])
def test_batch_skips_an_earlier_ineligible_document(
    skip: str, promoted: tuple[str, Path], corpus: Path
) -> None:
    _, path = promoted
    current = _stale(path)
    manifest_path = corpus / LOCAL / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = dict(manifest["documents"]["lf-20010203-9001"])
    if skip == "removed":
        record.update(status="removed", removed_reason="withdrawn_misclassified")
    else:
        earlier = path.with_name("earlier.md")
        earlier.write_text(
            current.replace('basis: "received"', 'basis: "observed"'), encoding="utf-8"
        )
        record["markdown_path"] = earlier.relative_to(corpus).as_posix()
    manifest["documents"]["lf-20010203-9000"] = record
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = _refresh(corpus)
    assert result.exit_code == 0, result.output
    assert path.read_text(encoding="utf-8") == current


def test_refresh_checks_document_withdrawal_even_in_an_old_checkout(
    promoted: tuple[str, Path], corpus: Path, root: Path
) -> None:
    _stale(promoted[1])
    withdrawal = _withdrawal().model_copy(update={"doc_id": "lf-20010203-9001"})
    DecisionLog(ObservatoryRoot(root, [])).append(withdrawal)
    assert "was withdrawn" in TestRefusals()._refused(corpus)


def test_held_source_refusal_explains_stage_reason_and_detail(
    promoted: tuple[str, Path], corpus: Path
) -> None:
    checkout = CorpusCheckout(corpus, [])
    doc_id, record = next(iter(checkout.local_manifest().documents.items()))
    document = published(checkout, doc_id, record)
    held = Held(stage="identity", reason="no_identity", detail="no vedtaksdato")
    with pytest.raises(PromotionRefusedError) as exc:
        _same_version(document, held)
    assert str(exc.value) == "its source is now held at identity (no_identity: no vedtaksdato)"


def test_multiple_moved_fields_are_reported_in_sorted_order(
    promoted: tuple[str, Path], corpus: Path
) -> None:
    _, path = promoted
    _stale(path)
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^received_at: .*$", 'received_at: "2026-01-01T00:00:00Z"', text)
    text = text.replace("version: 1", "version: 99")
    assert "version: 99" in text
    path.write_text(text, encoding="utf-8")
    assert (
        TestRefusals()
        ._refused(corpus)
        .endswith(
            "re-rendering would change received_at, version; a refresh keeps the version as it is\n"
        )
    )


@pytest.mark.parametrize("body", ["first\n---\nlast\n", "first\n---\n", "", "---\n"])
def test_body_keeps_horizontal_rules_and_trailing_bytes(body: str) -> None:
    assert _body('---\nid: "example"\n---\n' + body) == body


def test_refresh_repairs_a_duplicate_opening_front_matter_marker(
    promoted: tuple[str, Path], corpus: Path
) -> None:
    _, path = promoted
    current = _stale(path)
    stale = path.read_text(encoding="utf-8")
    path.write_text("---\n" + stale, encoding="utf-8")
    result = _refresh(corpus)
    assert result.exit_code == 0, result.output
    assert path.read_text(encoding="utf-8") == current


@pytest.mark.parametrize("mode", ["write", "dry-run", "missing-workspace"])
def test_general_refresh_handles_both_channels_without_losing_observed_work(
    mode: str,
    promoted: tuple[str, Path],
    workspace: Path,
    corpus: Path,
    root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operations #595: received refusal must still allow observed refreshes."""
    register(root)
    sha256 = _promoted_once(root, corpus, tmp_path, monkeypatch, _straks)
    approve_observed(sha256, tmp_path)
    [observed] = (corpus / LOCAL / AUTHORITY).glob("*.md")
    received = promoted[1]
    received_current = _stale(received)
    if mode == "missing-workspace":
        monkeypatch.delenv(ENV_INNSYN_DIR)
    before, log, held = tree(corpus), _log(root), tree(workspace)
    head = git(corpus, "rev-parse", "HEAD")

    result = invoke(
        "promote",
        "refresh",
        "--corpus",
        str(corpus),
        *(("--dry-run",) if mode == "dry-run" else ()),
    )

    assert result.exit_code == (1 if mode == "missing-workspace" else 0), result.output
    expected = before.copy()
    if mode == "dry-run":
        assert '-ikraft_text: "straks"' in result.stdout
        assert '+status: "repealed"' in result.stdout
        assert "Dry run: nothing written." in result.stdout
    else:
        observed_after = observed.read_bytes()
        assert observed_after != before[observed.relative_to(corpus).as_posix()]
        assert b'ikraft_text: "straks"' not in observed_after
        expected[observed.relative_to(corpus).as_posix()] = observed_after
        if mode == "write":
            expected[received.relative_to(corpus).as_posix()] = received_current.encode("utf-8")
        else:
            assert "Refused: lf-20010203-9001:" in result.stderr
            assert f"set {ENV_INNSYN_DIR}" in result.stderr
    assert tree(corpus) == expected
    assert _log(root) == log
    assert tree(workspace) == held
    assert git(corpus, "rev-parse", "HEAD") == head


class TestDryRunAwaitingReapproval:
    """#719: the dry run shows the diff a re-approval at the running extractor covers."""

    @pytest.fixture
    def approved_earlier(
        self, promoted: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Path:
        """The promoted print, its standing approval given at the previous extractor."""
        sha256, path = promoted
        with monkeypatch.context() as patched:
            patched.setattr(received_commands, "EXTRACTOR_VERSION", EXTRACTOR_VERSION - 1)
            assert _approve(sha256, tmp_path).exit_code == 0
        return path

    @pytest.fixture
    def earlier(self, approved_earlier: Path) -> Path:
        """That print, rendered as before #639."""
        _stale(approved_earlier)
        return approved_earlier

    def test_the_dry_run_prints_the_diff_and_names_the_reapproval(
        self, earlier: Path, corpus: Path, root: Path
    ) -> None:
        before, log = tree(corpus), _log(root)
        result = _refresh(corpus, "--dry-run")
        assert result.exit_code == 1, result.output
        assert '\n+status: "repealed"\n' in result.stdout
        assert (
            f"lf-20010203-9001: needs re-approval at v{EXTRACTOR_VERSION}: its approval was "
            "given at an earlier extractor; the diff below is what approving it again covers\n"
        ) in result.stdout
        assert result.stdout.endswith(
            "Dry run: nothing written. 1 document(s) need re-approval at "
            f"v{EXTRACTOR_VERSION} before refresh writes them.\n"
        )
        assert result.stderr == ""
        assert (tree(corpus), _log(root)) == (before, log)

    def test_the_write_still_refuses(self, earlier: Path, corpus: Path, root: Path) -> None:
        before, log = tree(corpus), _log(root)
        result = _refresh(corpus)
        assert result.exit_code == 1, result.output
        assert "approval was given for another text or extractor" in result.stderr
        assert "needs re-approval" not in result.stdout
        assert (tree(corpus), _log(root)) == (before, log)

    def test_a_moved_provenance_field_is_still_refused(self, earlier: Path, corpus: Path) -> None:
        received_at = re.search(r'\nreceived_at: "([^"]+)"\n', earlier.read_text(encoding="utf-8"))
        assert received_at is not None
        _edit(earlier, received_at.group(1), "2026-01-01T00:00:00Z")
        before = tree(corpus)
        result = _refresh(corpus, "--dry-run")
        assert result.exit_code == 1, result.output
        assert "would change received_at;" in result.stderr
        assert "needs re-approval" not in result.stdout
        assert tree(corpus) == before

    def test_a_held_decision_shows_no_diff(
        self, earlier: Path, corpus: Path, tmp_path: Path, promoted: tuple[str, Path]
    ) -> None:
        assert _approve(promoted[0], tmp_path, decision="hold").exit_code == 0
        result = _refresh(corpus, "--dry-run")
        assert result.exit_code == 1, result.output
        assert "the standing decision is hold" in result.stderr
        assert "+status" not in result.stdout

    def test_a_current_rendering_keeps_the_approval_refusal(
        self, approved_earlier: Path, corpus: Path
    ) -> None:
        result = _refresh(corpus, "--dry-run")
        assert result.exit_code == 1, result.output
        assert "approval was given for another text or extractor" in result.stderr
        assert "needs re-approval" not in result.stdout

    @pytest.mark.parametrize("decision", ["reject", "withdrawn"])
    def test_a_rejected_or_withdrawn_document_has_no_preview(
        self,
        decision: str,
        earlier: Path,
        promoted: tuple[str, Path],
        corpus: Path,
        root: Path,
        workspace: Path,
        tmp_path: Path,
    ) -> None:
        """Operations #719 excludes rejected and withdrawn documents from previews."""
        if decision == "reject":
            assert _approve(promoted[0], tmp_path, decision="reject").exit_code == 0
            reason = "the standing decision is reject"
        else:
            withdrawal = _withdrawal().model_copy(update={"doc_id": "lf-20010203-9001"})
            DecisionLog(ObservatoryRoot(root, [])).append(withdrawal)
            reason = "was withdrawn"
        before, log, held = tree(corpus), _log(root), tree(workspace)

        result = _refresh(corpus, "--dry-run")

        assert result.exit_code == 1, result.output
        assert reason in result.stderr
        assert result.stdout == ""
        assert (tree(corpus), _log(root), tree(workspace)) == (before, log, held)

    @pytest.mark.parametrize("general", [False, True], ids=["received", "general"])
    @pytest.mark.parametrize("dry_run", [False, True], ids=["write", "dry-run"])
    def test_pending_reapproval_does_not_hide_an_approved_batch_member(
        self,
        general: bool,
        dry_run: bool,
        earlier: Path,
        workspace: Path,
        corpus: Path,
        root: Path,
        tmp_path: Path,
    ) -> None:
        """#719 previews only pending approvals; other documents still refresh normally."""
        lines = tuple(line.replace("2001-02-03-9001", "2001-02-03-9006") for line in REPEALED_PRINT)
        sha256 = _receive(workspace, minimal_pdf(lines), name="second.pdf")
        assert _approve(sha256, tmp_path).exit_code == 0
        result = _received("local", sha256, corpus)
        assert result.exit_code == 0, result.output
        [second] = [path for path in earlier.parent.glob("*.md") if path != earlier]
        current = _stale(second)
        before, log, held = tree(corpus), _log(root), tree(workspace)
        head = git(corpus, "rev-parse", "HEAD")
        command = ("promote", "refresh") if general else ("promote", "received", "refresh")

        result = invoke(*command, "--corpus", str(corpus), *(("--dry-run",) if dry_run else ()))

        assert result.exit_code == 1, result.output
        expected = before.copy()
        if dry_run:
            assert result.stderr == ""
            assert result.stdout.count('+status: "repealed"') == 2
            assert f"lf-20010203-9001: needs re-approval at v{EXTRACTOR_VERSION}:" in result.stdout
            assert "lf-20010203-9006: needs re-approval" not in result.stdout
            assert result.stdout.endswith(
                f"1 document(s) need re-approval at v{EXTRACTOR_VERSION} "
                "before refresh writes them.\n"
            )
        else:
            assert "Refused: lf-20010203-9001:" in result.stderr
            assert "approval was given for another text or extractor" in result.stderr
            assert "lf-20010203-9006 v1: front matter re-rendered" in result.stdout
            expected[second.relative_to(corpus).as_posix()] = current.encode("utf-8")
        assert (tree(corpus), _log(root), tree(workspace)) == (expected, log, held)
        assert git(corpus, "rev-parse", "HEAD") == head


def test_a_refresh_awaiting_reapproval_is_never_written(corpus: Path) -> None:
    """#719: only a dry run builds one; a write that is handed one refuses it."""
    waiting = Refresh(
        doc_id="lf-20010203-9001",
        authority_id="4646",
        slug="s",
        version=1,
        markdown_path=f"{LOCAL}/4646/s.md",
        markdown="---\nid: x\n---\nbody\n",
        needs_reapproval=True,
    )
    before = tree(corpus)
    with pytest.raises(PromotionRefusedError, match="another text or extractor"):
        write_refreshes(CorpusCheckout(corpus, []), [waiting])
    assert tree(corpus) == before


class TestLateCaseNumber:
    """A saksnummer recorded after publication fills the version's null (#720)."""

    _NULL = "\ninnsyn_saksnummer: null\n"

    def _record(self, workspace: Path, number: str = "2026/1347") -> None:
        _innsyn(workspace, "record-case-number", "--request", FJALER, "--number", number)

    def test_refresh_fills_a_null_case_number(
        self, promoted: tuple[str, Path], corpus: Path, workspace: Path
    ) -> None:
        _, path = promoted
        published = path.read_text(encoding="utf-8")
        assert self._NULL in published
        self._record(workspace)
        result = _refresh(corpus)
        assert result.exit_code == 0, result.output
        filled = published.replace(self._NULL, '\ninnsyn_saksnummer: "2026/1347"\n')
        assert path.read_text(encoding="utf-8") == filled

    def test_refresh_never_changes_a_set_case_number(
        self, promoted: tuple[str, Path], corpus: Path, workspace: Path
    ) -> None:
        _, path = promoted
        _edit(path, self._NULL, '\ninnsyn_saksnummer: "2026/9999"\n')
        self._record(workspace)
        before = tree(corpus)
        result = _refresh(corpus)
        assert result.exit_code == 1, result.output
        assert "would change innsyn_saksnummer;" in result.stderr
        assert tree(corpus) == before

    def test_refresh_never_empties_a_set_case_number(
        self, promoted: tuple[str, Path], corpus: Path
    ) -> None:
        _, path = promoted
        _edit(path, self._NULL, '\ninnsyn_saksnummer: "2026/9999"\n')
        result = _refresh(corpus)
        assert result.exit_code == 1, result.output
        assert "would change innsyn_saksnummer;" in result.stderr
