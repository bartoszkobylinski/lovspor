"""What ``promote backfill``, ``backfill-preview`` and ``observe`` print, line for line (S6).

The operator acts on these lines — they copy the printed commands, in the
printed order — so each test compares the whole output, not a fragment of it.
The corpus path holds a space, so every command it appears in must quote it.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from datetime import UTC, datetime
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot
from lovspor.promotion import backfill_commands
from lovspor.promotion.backfill import VersionHold, _next, _prepared
from lovspor.promotion.commands import Request
from lovspor.promotion.decisions import (
    DECISIONS_FILENAME,
    ArtifactKey,
    DecisionLog,
    HeldRecord,
    HumanDecision,
    utc_text,
)
from lovspor.promotion.models import PersonalDataHit, PersonalDataKind
from lovspor.promotion.plan import Held
from lovspor.promotion.versions import read_primary
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    KLASS_VERSION,
    PAGE_URL,
    Decision,
    approve,
    invoke,
    make_corpus,
    promote,
    provenance,
    register,
    store,
    store_failure,
)
from tests.unit.promotion_fixtures import html_page
from tests.unit.test_promotion_backfill import DAY, LOCAL, _aba, _commit_printed

SLUG = "forskrift-om-renovasjon-og-slam-eksempel-kommune"
SUBJECT = f"promote(lokal-forskrift): {AUTHORITY}/{SLUG}"
MARKDOWN = f"{LOCAL}/{AUTHORITY}/{SLUG}.md"
OBSERVATIONS = f"{LOCAL}/{AUTHORITY}/observations/{SLUG}.json"
EVIDENCE = f"{LOCAL}/{AUTHORITY}/evidence/{SLUG}.json"
BERGEN = "4601"


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
    return make_corpus(tmp_path / "with space")


def _approve(sha256: str, tmp_path: Path, decision: str = "approve") -> None:
    result = approve(sha256, Decision(decision=decision).write(tmp_path))
    assert result.exit_code == 0, result.output


def _lines(output: str) -> list[str]:
    return output.splitlines()


def _plan_lines(*statuses: str) -> list[str]:
    intervals = [
        ("2026-08-19T15:17:23Z", "2026-08-20T15:17:23Z", 2),
        ("2026-08-21T15:17:23Z", "2026-08-21T15:17:23Z", 1),
        ("2026-08-22T15:17:23Z", "2026-08-22T15:17:23Z", 1),
    ]
    return [
        f"Primary URL: {PAGE_URL}",
        *(
            f"  v{n}  {first} .. {last}  ({count} observations)  {status}"
            for n, ((first, last, count), status) in enumerate(
                zip(intervals, statuses, strict=True), start=1
            )
        ),
        "Source status: retrieved at 2026-08-22T15:17:23Z",
    ]


def _commit_lines(corpus: Path, subject: str) -> list[str]:
    where = shlex.quote(str(corpus))
    return [
        "Nothing is committed. Commit now, at promotion time (never backdated):",
        f"  git -C {where} add -- {LOCAL}",
        f"  git -C {where} commit -m {shlex.quote(subject)}",
    ]


class TestBackfillOutput:
    def test_the_first_run_prints_the_plan_the_writes_and_every_further_step(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _aba(root, tmp_path)

        result = promote("backfill", a, corpus)

        assert result.exit_code == 0, result.output
        further = (
            f"  lovspor promote backfill --authority {AUTHORITY} --artifact {a}"
            f" --corpus {shlex.quote(str(corpus))} --klass-version {KLASS_VERSION}"
        )
        assert _lines(result.output) == [
            *_plan_lines("approved", "approved", "approved"),
            "Holds by reason: none",
            f"wrote {EVIDENCE}",
            f"wrote {MARKDOWN}",
            f"wrote {OBSERVATIONS}",
            f"wrote {LOCAL}/manifest.json",
            *_commit_lines(corpus, f"{SUBJECT} v1"),
            "After that commit, the further versions, in this order:",
            further,
            *_commit_lines(corpus, f"{SUBJECT} v2")[1:],
            further,
            *_commit_lines(corpus, f"{SUBJECT} v3")[1:],
        ]

    def test_a_finished_backfill_says_so_and_nothing_else(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a = store(root, html_page())
        _approve(a, tmp_path)
        _commit_printed(corpus, promote("backfill", a, corpus).output)

        result = promote("backfill", a, corpus)

        assert result.exit_code == 0, result.output
        assert _lines(result.output)[-2:] == [
            "Holds by reason: none",
            "Nothing to write: the corpus holds every approved version.",
        ]

    def test_the_preview_names_the_next_file_and_its_commit_subject(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _aba(root, tmp_path)

        result = promote("backfill-preview", a, corpus)

        assert result.exit_code == 0, result.output
        assert _lines(result.output) == [
            *_plan_lines("approved", "approved", "approved"),
            "Holds by reason: none",
            f"Next: v1 -> {MARKDOWN}",
            f"  commit subject: {SUBJECT} v1",
        ]

    def test_a_preview_with_nothing_approved_says_nothing_is_written(
        self, root: Path, corpus: Path
    ) -> None:
        a = store(root, html_page())

        result = promote("backfill-preview", a, corpus)

        assert result.exit_code == 0, result.output
        assert _lines(result.output)[-3:] == [
            "Holds by reason: not_approved: 1",
            f"  v1 not_approved: no decision on {a}; run `lovspor promote approve`",
            "Next: nothing to write.",
        ]

    def test_a_rejection_is_listed_with_who_decided_what_and_when(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a = store(root, html_page())
        _approve(a, tmp_path, decision="reject")
        [rejection] = _decisions(root)

        result = promote("backfill-preview", a, corpus)

        when = utc_text(rejection.decided_at)
        assert f"  v1 rejected: reject of {a} at {when}" in _lines(result.output)

    def test_a_failed_fetch_of_the_primary_url_is_not_read_as_the_versions_capture(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        store_failure(root, FIRST_SEEN - DAY)
        a = store(root, html_page())
        _approve(a, tmp_path)

        result = promote("backfill", a, corpus)

        assert result.exit_code == 0, result.output
        assert f"wrote {MARKDOWN}" in _lines(result.output)

    def test_another_urls_capture_is_not_read_as_the_versions_capture(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _store_as(root, b"%PDF-1.4 not this one", "https://eksempel.kommune.invalid/annen.pdf")
        a = store(root, html_page())
        _approve(a, tmp_path)

        result = promote("backfill", a, corpus)

        assert result.exit_code == 0, result.output
        assert f"wrote {MARKDOWN}" in _lines(result.output)


def _decisions(root: Path) -> list[HumanDecision]:
    records = DecisionLog(ObservatoryRoot(root, [])).records()
    return [r for r in records if isinstance(r, HumanDecision)]


def _store_as(root: Path, payload: bytes, url: str, authority_id: str = AUTHORITY) -> str:
    """Capture ``payload`` as the observatory does, served as a PDF or for another authority."""
    sha256 = hashlib.sha256(payload).hexdigest()
    record = ArtifactObservation(
        authority_id=authority_id,
        url=url,
        observed_at=FIRST_SEEN,
        provenance=provenance(),
        sha256=sha256,
        content_type=(
            "application/pdf" if payload.startswith(b"%PDF") else "text/html; charset=utf-8"
        ),
        http_status=200,
    )
    ObservationLog(ObservatoryRoot(root, [])).append_artifact(record, payload)
    return sha256


def _promoted(root: Path, corpus: Path, tmp_path: Path) -> str:
    a = store(root, html_page())
    _approve(a, tmp_path)
    _commit_printed(corpus, promote("backfill", a, corpus).output)
    return a


def _doc_id(corpus: Path, authority_id: str = AUTHORITY) -> str:
    manifest = json.loads((corpus / LOCAL / "manifest.json").read_text(encoding="utf-8"))
    [doc_id] = [d for d, r in manifest["documents"].items() if r["authority_id"] == authority_id]
    return doc_id


def _observe(corpus: Path, *extra: str) -> list[str]:
    result = invoke("promote", "observe", "--corpus", str(corpus), *extra)
    assert result.exit_code == 0, result.output
    return _lines(result.output)


class TestObserveOutput:
    def test_a_refreshed_document_is_named_written_and_its_commit_printed(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path)
        store(root, html_page(), observed_at=FIRST_SEEN + 7 * DAY)

        lines = _observe(corpus)

        assert lines == [
            f"{_doc_id(corpus)}: refreshed",
            f"wrote {OBSERVATIONS}",
            *_commit_lines(corpus, "observe: refresh observation intervals (1 documents)"),
        ]

    def test_a_current_document_is_named_unchanged_and_nothing_is_written(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path)

        lines = _observe(corpus)

        assert lines == [
            f"{_doc_id(corpus)}: unchanged",
            "Observation intervals are current; nothing written, nothing to commit.",
        ]

    def test_a_document_of_another_authority_is_passed_over_not_the_end_of_the_run(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path)
        _promoted_for_bergen(root, corpus, tmp_path)

        lines = _observe(corpus, "--authority", BERGEN)

        assert lines == [
            f"{_doc_id(corpus, BERGEN)}: unchanged",
            "Observation intervals are current; nothing written, nothing to commit.",
        ]

    def test_versions_naming_two_primary_urls_are_skipped_with_that_reason(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _aba(root, tmp_path)
        for _ in range(2):
            _commit_printed(corpus, promote("backfill", a, corpus).output)
        path = corpus / OBSERVATIONS
        observations = json.loads(path.read_text(encoding="utf-8"))
        observations["versions"][1]["primary_url"] = f"{PAGE_URL}-kopi"
        path.write_text(json.dumps(observations), encoding="utf-8")

        lines = _observe(corpus)

        reason = "skipped: its versions name more than one primary URL"
        assert lines[0] == f"{_doc_id(corpus)}: {reason}"

    def test_a_missing_observations_file_is_skipped_naming_the_error(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path)
        (corpus / OBSERVATIONS).unlink()

        lines = _observe(corpus)

        path = corpus / OBSERVATIONS
        assert lines[0] == f"{_doc_id(corpus)}: skipped: {path} does not read (FileNotFoundError)"


def _promoted_for_bergen(root: Path, corpus: Path, tmp_path: Path) -> None:
    """A second authority's document, promoted through the supported commands."""
    url = "https://bergen.kommune.invalid/forskrifter/renovasjon"
    registered = invoke(
        "observatory", "register-source", "--id", BERGEN, "--name", "Bergen",
        "--domain", "bergen.kommune.invalid",
    )  # fmt: skip
    assert registered.exit_code == 0, registered.output
    sha256 = _store_as(root, html_page(), url, BERGEN)
    document = Decision().write(tmp_path)
    approved = invoke(
        "promote", "approve", "--authority", BERGEN, "--artifact", sha256,
        "--decision", str(document),
    )  # fmt: skip
    assert approved.exit_code == 0, approved.output
    promoted = invoke(
        "promote", "local", "--authority", BERGEN, "--artifact", sha256,
        "--corpus", str(corpus), "--klass-version", KLASS_VERSION,
    )  # fmt: skip
    assert promoted.exit_code == 0, promoted.output


class TestCorpusDivergenceIsRefused:
    def test_an_observations_file_shorter_than_the_manifest_is_refused_with_both_counts(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _aba(root, tmp_path)
        for _ in range(2):
            _commit_printed(corpus, promote("backfill", a, corpus).output)
        path = corpus / OBSERVATIONS
        observations = json.loads(path.read_text(encoding="utf-8"))
        observations["versions"] = observations["versions"][:1]
        path.write_text(json.dumps(observations), encoding="utf-8")

        result = promote("backfill", a, corpus)

        assert result.exit_code == 1
        assert _lines(result.output)[-1] == f"Refused: {path} lists 1 versions, the manifest 2"

    @pytest.mark.parametrize(
        "update, message",
        [
            ({"version": 2}, "the corpus places this text as v2, the log as v1"),
            ({"unchanged": True}, "the corpus places this text as v1, the log as v1"),
        ],
    )
    def test_a_text_the_corpus_places_otherwise_than_the_log_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path, update: dict[str, object], message: str
    ) -> None:
        a = store(root, html_page())
        _approve(a, tmp_path)
        inputs, plan, _ = backfill_commands._plan(Request(AUTHORITY, a, corpus, KLASS_VERSION))
        history = read_primary(inputs.log, inputs.fetches, PAGE_URL, plan.through)
        prepared = _prepared(inputs, history, history.versions[0])
        assert not isinstance(prepared, VersionHold)

        with pytest.raises(PromotionRefusedError) as refused:
            _next(inputs, history, prepared.model_copy(update=update), plan.approved[0])

        assert str(refused.value) == message


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
FNR_HIT = PersonalDataHit(kind=PersonalDataKind.FODSELSNUMMER, line=3)


def _held_hold(key: ArtifactKey | None) -> VersionHold:
    held = Held(
        stage="extraction", reason="personal_data", detail="1 hit", personal_data=(FNR_HIT,)
    )
    return VersionHold(
        version=1, reason="extraction:personal_data", detail="1 hit", held=held, key=key
    )


class TestRecordedHold:
    def test_a_hold_is_printed_with_its_hits_and_recorded_with_them(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        key = ArtifactKey(authority_id=AUTHORITY, sha256="a" * 64, source_url=PAGE_URL)
        decisions = DecisionLog(ObservatoryRoot(root, []))

        backfill_commands._record_hold(decisions, _held_hold(key), NOW)

        assert _lines(capsys.readouterr().out) == [
            "Held: v1 extraction:personal_data - 1 hit",
            "  personal data: fodselsnummer on line 3",
        ]
        [record] = decisions.records()
        assert isinstance(record, HeldRecord)
        assert record.personal_data == (FNR_HIT,)

    def test_a_hold_without_the_artifact_it_stands_on_is_printed_not_recorded(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        decisions = DecisionLog(ObservatoryRoot(root, []))

        backfill_commands._record_hold(decisions, _held_hold(None), NOW)

        assert "Held: v1 extraction:personal_data - 1 hit" in capsys.readouterr().out
        assert decisions.records() == ()
        assert not (root / DECISIONS_FILENAME).exists()


class TestRegistration:
    @pytest.mark.parametrize(
        "command, summary",
        [
            ("backfill-preview", "Show every observed version of one document"),
            ("backfill", "Write the next approved observed version into a lovverk checkout"),
            ("observe", "Refresh observation intervals of promoted local documents"),
        ],
    )
    def test_each_command_is_added_under_its_name(self, command: str, summary: str) -> None:
        app = typer.Typer()
        app.command("noop")(lambda: None)

        backfill_commands.register_backfill(app)

        result = CliRunner().invoke(app, [command, "--help"])
        assert result.exit_code == 0, result.output
        assert summary in " ".join(result.output.split())
