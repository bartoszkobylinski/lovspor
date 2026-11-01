"""``lovspor ops forarbeider-chapter``: the operator's one route to a chapter result (#738)."""

import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider.chapter_models import read_chapter
from lovspor.forarbeider.commands import MAX_HTML_BYTES
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.staging import write_observation
from tests.unit.cli_output import said

FIXTURE = Path(__file__).parents[1] / "fixtures" / "forarbeider" / "chapter-l.html"
URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=2"
WHEN = "2100-04-01T12:00:00Z"
_ENGINE = Path(__file__).parents[2]


def run(html: Path, output: Path, url: str = URL, when: str = WHEN) -> tuple[int, str]:
    result = CliRunner().invoke(
        app,
        [
            "ops",
            "forarbeider-chapter",
            "--html",
            str(html),
            "--source-url",
            url,
            "--retrieved-at",
            when,
            "--output",
            str(output),
        ],
    )
    return result.exit_code, result.output


def test_saved_page_to_held_result_end_to_end(tmp_path: Path) -> None:
    output = tmp_path / "staging" / "chapter.json"
    code, printed = run(FIXTURE, output)
    assert code == 0, printed
    assert said("held regjeringen-9000001-ch2", printed)
    assert said("reasons: review_required", printed)
    assert read_chapter(output).release_state == "held"
    assert json.loads(output.read_text(encoding="utf-8"))["blocks"][0]["text"] == "2 Bakgrunn"


def test_nothing_of_the_chapter_is_printed(tmp_path: Path) -> None:
    _, printed = run(FIXTURE, tmp_path / "chapter.json")
    assert "Departementet" not in printed
    assert "Avsluttende" not in printed


def test_the_result_is_private_to_its_owner(tmp_path: Path) -> None:
    output = tmp_path / "chapter.json"
    run(FIXTURE, output)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_same_observation_again_changes_nothing(tmp_path: Path) -> None:
    output = tmp_path / "chapter.json"
    run(FIXTURE, output)
    before = output.stat().st_mtime_ns
    code, printed = run(FIXTURE, output)
    assert code == 0
    assert said("unchanged regjeringen-9000001-ch2", printed)
    assert output.stat().st_mtime_ns == before
    assert list(tmp_path.iterdir()) == [output]


def test_identical_output_others_can_read_is_refused_and_left(tmp_path: Path) -> None:
    output = tmp_path / "chapter.json"
    run(FIXTURE, output)
    output.chmod(0o644)
    before = output.read_bytes()
    code, printed = run(FIXTURE, output)
    assert code == 1
    assert said("refused: observation_unsafe", printed)
    assert output.read_bytes() == before
    assert stat.S_IMODE(output.stat().st_mode) == 0o644


def test_another_observation_never_overwrites(tmp_path: Path) -> None:
    output = tmp_path / "chapter.json"
    run(FIXTURE, output)
    before = output.read_bytes()
    code, printed = run(FIXTURE, output, when="2100-04-02T12:00:00Z")
    assert code == 1
    assert said("refused: observation_exists", printed)
    assert output.read_bytes() == before
    assert list(tmp_path.iterdir()) == [output]


def test_a_later_observation_lands_at_its_own_path(tmp_path: Path) -> None:
    run(FIXTURE, tmp_path / "first.json")
    code, _ = run(FIXTURE, tmp_path / "second.json", when="2100-04-02T12:00:00Z")
    assert code == 0
    assert read_chapter(tmp_path / "first.json").source_sha256 == (
        read_chapter(tmp_path / "second.json").source_sha256
    )


def test_a_withheld_chapter_is_still_written_without_text(tmp_path: Path) -> None:
    html = tmp_path / "page.html"
    text = FIXTURE.read_text(encoding="utf-8")
    html.write_text(text.replace("Avsluttende vurdering.", 'Tekst<img src="a.png">'), "utf-8")
    output = tmp_path / "chapter.json"
    code, printed = run(html, output)
    assert code == 0
    assert said("body withheld", printed)
    assert read_chapter(output).blocks is None
    assert "Departementet" not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("relative", "reason"),
    [
        ("chapter.txt", "unsafe_output_path"),
        ("repository/chapter.json", "unsafe_output_path"),
    ],
)
def test_unsafe_output_is_refused(tmp_path: Path, relative: str, reason: str) -> None:
    (tmp_path / "repository" / ".git").mkdir(parents=True)
    code, printed = run(FIXTURE, tmp_path / relative)
    assert code == 1
    assert said(f"refused: {reason}", printed)
    assert not (tmp_path / relative).exists()


def test_output_in_the_engine_is_refused() -> None:
    target = _ENGINE / "chapter-output-probe.json"
    code, printed = run(FIXTURE, target)
    assert code == 1
    assert said("refused: unsafe_output_path", printed)
    assert not target.exists()


def test_a_relative_output_is_refused(tmp_path: Path) -> None:
    code, printed = run(FIXTURE, Path("chapter.json"))
    assert code == 1
    assert said("refused: unsafe_output_path", printed)


def test_a_parent_that_is_a_file_is_a_write_failure(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    code, printed = run(FIXTURE, blocker / "chapter.json")
    assert code == 1
    assert said("refused: output_write_failed", printed)


def test_too_large_input_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    html = tmp_path / "page.html"
    with html.open("wb") as stream:
        stream.truncate(MAX_HTML_BYTES + 1)
    output = tmp_path / "chapter.json"
    code, printed = run(html, output)
    assert code == 1
    assert said("refused: input_too_large", printed)
    assert not output.exists()


def test_input_at_the_limit_reaches_the_page_checks(tmp_path: Path) -> None:
    html = tmp_path / "page.html"
    with html.open("wb") as stream:
        stream.truncate(MAX_HTML_BYTES)
    code, printed = run(html, tmp_path / "chapter.json")
    assert code == 1
    assert not said("input_too_large", printed)


def test_missing_input_is_refused(tmp_path: Path) -> None:
    code, printed = run(tmp_path / "absent.html", tmp_path / "chapter.json")
    assert code == 1
    assert said("refused: input_unreadable", printed)


def test_wrong_chapter_writes_nothing(tmp_path: Path) -> None:
    output = tmp_path / "chapter.json"
    code, printed = run(FIXTURE, output, url=URL.replace("ch=2", "ch=3"))
    assert code == 1
    assert said("refused: page_chapter_unproved", printed)
    assert not output.exists()


def test_timestamp_without_offset_is_refused(tmp_path: Path) -> None:
    code, printed = run(FIXTURE, tmp_path / "chapter.json", when="2100-04-01T12:00:00")
    assert code == 1
    assert said("refused: invalid_timestamp", printed)


def test_a_failed_link_leaves_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_source: Path, _target: Path) -> None:
        raise OSError("simulated")

    monkeypatch.setattr(os, "link", refuse)
    code, printed = run(FIXTURE, tmp_path / "chapter.json")
    assert code == 1
    assert said("refused: output_write_failed", printed)
    assert list(tmp_path.iterdir()) == []


def test_a_failed_read_of_the_existing_file_is_a_write_failure(tmp_path: Path) -> None:
    target = tmp_path / "chapter.json"
    target.write_text("old", encoding="utf-8")
    target.chmod(0)
    try:
        with pytest.raises(ForarbeiderRefusedError) as refused:
            write_observation(target, "new")
    finally:
        target.chmod(0o600)
    assert refused.value.reason is RefusalReason.OUTPUT_WRITE_FAILED


def test_write_observation_reports_what_it_did(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "er" / "chapter.json"
    assert write_observation(target, "æ\n") is True
    assert target.read_bytes() == "æ\n".encode()
    assert write_observation(target, "æ\n") is False
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("competing_text", ["same æ\n", "another observation\n"])
def test_competing_observation_at_publication_is_never_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, competing_text: str
) -> None:
    """Enforce operations.md: an observation is never replaced, even at publication."""
    target = tmp_path / "chapter.json"
    original_link = os.link
    payload = "same æ\n"

    def competing_link(source: Path, destination: Path) -> None:
        assert destination == target
        assert not destination.exists()
        assert source.read_bytes() == payload.encode("utf-8")
        assert stat.S_IMODE(source.stat().st_mode) == 0o600
        destination.write_text(competing_text, encoding="utf-8")
        destination.chmod(0o600)
        original_link(source, destination)

    monkeypatch.setattr(os, "link", competing_link)
    if competing_text == payload:
        assert write_observation(target, payload) is False
    else:
        with pytest.raises(ForarbeiderRefusedError) as refused:
            write_observation(target, payload)
        assert refused.value.reason is RefusalReason.OBSERVATION_EXISTS
    assert target.read_bytes() == competing_text.encode("utf-8")
    assert list(tmp_path.iterdir()) == [target]


def test_the_staged_file_is_a_hidden_neighbour_of_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked: list[Path] = []
    real_link = os.link

    def spy(source: Path, target: Path) -> None:
        linked.append(Path(source))
        real_link(source, target)

    monkeypatch.setattr(os, "link", spy)
    target = tmp_path / "chapter.json"
    write_observation(target, "x")
    assert linked[0].parent == tmp_path
    assert linked[0].name.startswith(".chapter.json.")


def test_a_staged_file_already_gone_does_not_fail_a_finished_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = os.link

    def link_and_lose(source: Path, target: Path) -> None:
        real_link(source, target)
        Path(source).unlink()

    monkeypatch.setattr(os, "link", link_and_lose)
    target = tmp_path / "chapter.json"
    assert write_observation(target, "x") is True
    assert list(tmp_path.iterdir()) == [target]
