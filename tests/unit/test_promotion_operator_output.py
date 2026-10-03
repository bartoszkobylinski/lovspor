"""What ``lovspor promote`` tells the operator, line for line (ADR-0016 S3).

The printed lines are the operator's interface: the commit to make, the
files written, the hold and where it was recorded, the refusal and why. They
are asserted whole, on the stream they belong to — a refusal on stderr, never
mixed into what a script reads from stdout.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, engine_root
from lovspor.promotion.commands import HELD_EXIT_CODE
from lovspor.promotion.decisions import DECISIONS_FILENAME
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
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
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

LK_ID = "lk-0301-aa8ae774921d"
SLUG = "forskrift-om-renovasjon-og-slam-eksempel-kommune"
MARKDOWN = f"lokale-forskrifter/{AUTHORITY}/{SLUG}.md"
OBSERVATIONS = f"lokale-forskrifter/{AUTHORITY}/observations/{SLUG}.json"
HISTORY = f"lokale-forskrifter/{AUTHORITY}/history/{SLUG}.json"
SUBJECT = f"promote(lokal-forskrift): {AUTHORITY}/{SLUG} v1"
FNR_LINE = "Søker med fødselsnummer 01019012480 er registrert."
HELD_LINES = (*REGULATION_LINES[:4], FNR_LINE, *REGULATION_LINES[4:])


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


def _approved(root: Path, tmp_path: Path) -> str:
    sha256 = store(root, html_page())
    assert approve(sha256, Decision().write(tmp_path)).exit_code == 0
    return sha256


def _log(root: Path) -> list[dict[str, object]]:
    text = (root / DECISIONS_FILENAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _log_path(root: Path) -> Path:
    return (root / DECISIONS_FILENAME).resolve()


def _commit_lines(corpus: Path, subject: str) -> str:
    where = shlex.quote(str(corpus.resolve()))
    return (
        "Nothing is committed. Commit now, at promotion time (never backdated):\n"
        f"  git -C {where} add -- lokale-forskrifter\n"
        f"  git -C {where} commit -m {shlex.quote(subject)}\n"
    )


def _refusal(result_stderr: str) -> str:
    assert result_stderr.startswith("Refused: "), result_stderr
    return result_stderr.removeprefix("Refused: ")


class TestLocal:
    def test_a_promotion_prints_what_it_wrote_and_the_exact_commit_to_make(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)

        result = promote("local", sha256, corpus)

        assert result.exit_code == 0, result.output
        assert result.stdout == (
            f"Promoted {LK_ID} v1 -> {MARKDOWN}\n"
            f"wrote {MARKDOWN}\n"
            f"wrote {OBSERVATIONS}\n"
            "wrote lokale-forskrifter/manifest.json\n"
            + _commit_lines(corpus, SUBJECT)
            + "Then derive its history: lovspor promote history --corpus "
            + f"{shlex.quote(str(corpus.resolve()))}\n"
        )
        assert result.stderr == ""

    def test_a_corpus_path_that_needs_quoting_is_quoted_in_every_command(
        self, root: Path, tmp_path: Path
    ) -> None:
        corpus = make_corpus(tmp_path / "with space")
        sha256 = _approved(root, tmp_path)

        result = promote("local", sha256, corpus)

        quoted = shlex.quote(str(corpus.resolve()))
        assert quoted.startswith("'")
        assert f"  git -C {quoted} add -- lokale-forskrifter\n" in result.stdout
        assert result.stdout.endswith(f"lovspor promote history --corpus {quoted}\n")

    def test_a_rerun_says_unchanged_and_where_the_version_already_is(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        promote("local", sha256, corpus)

        result = promote("local", sha256, corpus)

        assert result.stdout == (
            f"Unchanged: {LK_ID} v1 is already at\n"
            f"{MARKDOWN}; nothing written, nothing to commit.\n"
        )

    def test_without_an_approval_the_refusal_names_the_command_to_run(
        self, root: Path, corpus: Path
    ) -> None:
        sha256 = store(root, html_page())

        result = promote("local", sha256, corpus)

        assert result.stdout == ""
        assert result.stderr == (
            "Refused: no human decision is recorded for this artifact; "
            "run `lovspor promote approve`\n"
        )

    def test_a_standing_reject_names_who_rejected_it_and_when(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = store(root, html_page())
        approve(sha256, Decision(decision="reject", reason="Ikke vedtatt.").write(tmp_path))
        [decision] = _log(root)

        result = promote("local", sha256, corpus)

        assert result.exit_code == 1
        assert _refusal(result.stderr) == (
            f"the standing decision is reject ({REVIEWER}, {decision['decided_at']})\n"
        )


class TestHeld:
    def test_a_hold_prints_its_stage_each_hit_and_where_it_was_recorded(
        self, root: Path, corpus: Path
    ) -> None:
        sha256 = store(root, html_page(HELD_LINES))

        result = promote("local", sha256, corpus)

        assert result.exit_code == HELD_EXIT_CODE
        assert result.stdout == (
            "Held at extraction: personal_data - 1 personal-data hit(s); "
            "held for review, never redacted\n"
            "  personal data: fodselsnummer on line 5\n"
            "Nothing was written to the corpus. The hold is recorded in "
            f"{(root / DECISIONS_FILENAME).resolve()}\n"
        )

    def test_the_recorded_hold_names_each_hit_by_kind_and_line_never_by_value(
        self, root: Path, corpus: Path
    ) -> None:
        sha256 = store(root, html_page(HELD_LINES))

        promote("local", sha256, corpus)

        [held] = _log(root)
        assert held["personal_data"] == [{"kind": "fodselsnummer", "line": 5}]

    def test_a_preview_of_a_held_text_prints_the_same_hold(self, root: Path, corpus: Path) -> None:
        sha256 = store(root, html_page(HELD_LINES))

        result = promote("preview", sha256, corpus)

        assert result.exit_code == HELD_EXIT_CODE
        assert result.stdout == (
            "Held at extraction: personal_data - 1 personal-data hit(s); "
            "held for review, never redacted\n"
            "  personal data: fodselsnummer on line 5\n"
        )


class TestPreview:
    def test_preview_prints_the_header_then_exactly_the_markdown_local_writes(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = store(root, html_page())

        result = promote("preview", sha256, corpus)
        approve(sha256, Decision().write(tmp_path))
        promote("local", sha256, corpus)

        markdown = (corpus / MARKDOWN).read_text(encoding="utf-8")
        manifest = json.loads((corpus / "lokale-forskrifter/manifest.json").read_text("utf-8"))
        content_hash = manifest["documents"][LK_ID]["content_hash"]
        assert result.stdout == (
            f"id: {LK_ID}  version: 1\npath: {MARKDOWN}\ncontent_hash: {content_hash}\n\n"
            + markdown
        )

    def test_preview_of_a_promoted_version_prints_the_header_and_says_unchanged(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _approved(root, tmp_path)
        promote("local", sha256, corpus)
        manifest = json.loads((corpus / "lokale-forskrifter/manifest.json").read_text("utf-8"))

        result = promote("preview", sha256, corpus)

        assert result.stdout == (
            f"id: {LK_ID}  version: 1\npath: {MARKDOWN}\n"
            f"content_hash: {manifest['documents'][LK_ID]['content_hash']}\n"
            "unchanged: the corpus already holds this version\n"
        )


class TestApprove:
    def test_approve_prints_the_decision_the_reviewer_the_time_and_the_log(
        self, root: Path, tmp_path: Path
    ) -> None:
        sha256 = store(root, html_page())

        result = approve(sha256, Decision().write(tmp_path))

        [decision] = _log(root)
        assert result.stdout == (
            f"Recorded approve of {sha256} at {PAGE_URL}\n"
            f"by {REVIEWER} ({REVIEWER_ROLE}) at {decision['decided_at']} in {_log_path(root)}\n"
        )
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", str(decision["decided_at"]))

    def test_a_held_text_can_be_rejected_and_the_reject_names_no_text(
        self, root: Path, tmp_path: Path
    ) -> None:
        sha256 = store(root, html_page(HELD_LINES))

        result = approve(
            sha256, Decision(decision="reject", reason="Ikke en forskrift.").write(tmp_path)
        )

        assert result.exit_code == 0, result.output
        [decision] = _log(root)
        assert decision["decision"] == "reject"
        assert decision["content_hash"] is None
        assert decision["extractor_version"] is None

    def test_a_hold_decision_names_no_text_either(self, root: Path, tmp_path: Path) -> None:
        sha256 = store(root, html_page())

        approve(sha256, Decision(decision="hold", reason="Venter på kunngjøring.").write(tmp_path))

        [decision] = _log(root)
        assert (decision["content_hash"], decision["extractor_version"]) == (None, None)

    def test_a_machine_reviewer_is_refused_with_the_rule_it_broke(
        self, root: Path, tmp_path: Path
    ) -> None:
        sha256 = store(root, html_page())

        result = approve(sha256, Decision(decided_by="classifier").write(tmp_path))

        refusal = _refusal(result.stderr)
        assert refusal.startswith("the decision document does not validate: ")
        assert "decided_by must name a person, not 'classifier' (ADR-0016 4c)" in refusal
        assert result.stdout == ""

    def test_a_reason_with_personal_data_is_refused_and_says_why(
        self, root: Path, tmp_path: Path
    ) -> None:
        sha256 = store(root, html_page())
        reason = "Avklart med saksbehandler, ola@eksempel.kommune.invalid."

        result = approve(sha256, Decision(reason=reason).write(tmp_path))

        assert result.stderr == (
            "Refused: the reason carries personal data; it is published with the audit record\n"
        )


class TestRefusals:
    def test_a_refusal_is_one_line_on_stderr_and_nothing_on_stdout(
        self, root: Path, corpus: Path
    ) -> None:
        result = promote("local", "0" * 64, corpus)

        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr == f"Refused: {'0' * 64} names 0 artifact(s) under {AUTHORITY}: none\n"

    def test_every_candidate_is_listed_when_a_url_names_two_texts(
        self, root: Path, corpus: Path
    ) -> None:
        changed = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
        shas = sorted([store(root, html_page()), store(root, html_page(changed))])

        result = promote("local", PAGE_URL, corpus)

        listed = f"{shas[0]} at {PAGE_URL}; {shas[1]} at {PAGE_URL}"
        assert (
            result.stderr
            == f"Refused: {PAGE_URL} names 2 artifact(s) under {AUTHORITY}: {listed}\n"
        )

    def test_an_unregistered_authority_is_named_in_the_refusal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        observatory = tmp_path / "observatory"
        observatory.mkdir()
        monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
        monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
        other = ("--id", "4601", "--name", "Annen", "--domain", "annen.kommune.invalid")
        assert invoke("observatory", "register-source", *other).exit_code == 0
        sha256 = store(observatory, html_page())

        result = promote("preview", sha256, make_corpus(tmp_path))

        assert result.stderr == f"Refused: {AUTHORITY} is not in the source register\n"

    def test_a_damaged_observation_log_names_the_command_that_repairs_it(
        self, root: Path, corpus: Path
    ) -> None:
        sha256 = store(root, html_page())
        with (root / "observations.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("not json\n")

        result = promote("preview", sha256, corpus)

        assert result.stderr == (
            "Refused: the observation log is damaged; run `lovspor observatory verify` first\n"
        )


class TestHistory:
    def test_history_prints_each_file_and_the_commit_for_the_count(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        promote("local", _approved(root, tmp_path), corpus)
        git(corpus, "add", "-A")
        git(corpus, "commit", "-q", "-m", SUBJECT)

        result = invoke("promote", "history", "--corpus", str(corpus))

        assert result.exit_code == 0, result.output
        assert result.stdout == f"wrote {HISTORY}\n" + _commit_lines(
            corpus, "history(lokal-forskrift): derive history for 1 documents"
        )

    def test_current_history_says_so_and_nothing_else(self, corpus: Path) -> None:
        result = invoke("promote", "history", "--corpus", str(corpus))

        assert result.stdout == "History is current; nothing written, nothing to commit.\n"

    def test_history_needs_no_observatory_root(
        self, corpus: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENV_OBSERVATORY_ROOT, raising=False)
        monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)

        result = invoke("promote", "history", "--corpus", str(corpus))

        assert result.exit_code == 0, result.output

    def test_history_runs_beside_an_observatory_root_the_boundary_refuses(
        self, corpus: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(engine_root() / "data" / "observatory"))
        monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)

        result = invoke("promote", "history", "--corpus", str(corpus))

        assert result.exit_code == 0, result.output

    def test_history_still_refuses_a_corpus_inside_the_observatory_root(
        self, root: Path, tmp_path: Path
    ) -> None:
        inside = make_corpus(root)

        result = invoke("promote", "history", "--corpus", str(inside))

        assert result.exit_code == 1
        assert "overlaps" in result.stderr
