"""``lovspor ops forarbeider-edition``: a landing page's edition evidence beside a v1 record (#784).

The evidence is a sidecar. The v1 metadata record it names is read, never
rewritten, so every v1 reader and the release preflight's reproduction check
see exactly the bytes they saw before.
"""

import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider.commands import MAX_HTML_BYTES
from lovspor.forarbeider.edition_models import EditionEvidence, read_edition_evidence
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import read_metadata
from tests.unit.cli_output import said

FIXTURES = Path(__file__).parents[1] / "fixtures" / "forarbeider"
CORRECTED = FIXTURES / "landing-ls-corrected.html"
PLAIN = FIXTURES / "landing-l-plain.html"
LS_LANDING = "https://www.regjeringen.no/no/dokumenter/prop.-900-ls-20992100/id9000002/"
L_LANDING = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/"
CAPTURED = "2100-05-20T10:15:00+02:00"
CHAPTER_WHEN = "2100-04-01T12:00:00Z"
PAGE_TEXT = ("Strukturell", "Testdepartementet", "oppdiktet")


@pytest.fixture(params=[0o022, 0o000], ids=["umask-022", "umask-000"])
def umask(request: pytest.FixtureRequest) -> Iterator[int]:
    previous = os.umask(request.param)
    try:
        yield request.param
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _metadata(tmp_path: Path, kind: str = "ls") -> Path:
    number = {"ls": "900-ls", "l": "901-l"}[kind]
    document = {"ls": "9000002", "l": "9000001"}[kind]
    output = tmp_path / "records" / f"regjeringen-{document}.json"
    chapter = {"ls": 2, "l": 1}[kind]
    url = f"https://www.regjeringen.no/no/dokumenter/prop.-{number}-20992100/id{document}/?ch={chapter}"
    argv = [
        "ops",
        "forarbeider-metadata",
        "--html",
        str(FIXTURES / f"prop-{kind}.html"),
        "--source-url",
        url,
        "--retrieved-at",
        CHAPTER_WHEN,
        "--output",
        str(output),
    ]
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.output
    return output


def _beside(metadata: Path) -> Path:
    return metadata.with_name(f"{metadata.stem}.edition.json")


def _argv(landing: Path, metadata: Path, **overrides: str) -> list[str]:
    return [
        "ops",
        "forarbeider-edition",
        "--landing-html",
        str(landing),
        "--landing-url",
        overrides.get("url", LS_LANDING),
        "--retrieved-at",
        overrides.get("when", CAPTURED),
        "--metadata",
        str(metadata),
    ]


def _run(argv: list[str]) -> tuple[int, str]:
    result = CliRunner().invoke(app, argv)
    return result.exit_code, result.output


@pytest.mark.usefixtures("umask")
def test_a_corrected_landing_page_is_recorded_privately(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)
    output = _beside(metadata)

    code, said_text = _run(_argv(CORRECTED, metadata))

    assert code == 0, said_text
    assert said_text == (
        f"wrote regjeringen-9000002: edition corrected (stated 2100-05-13), "
        f"chapter edition unknown, to {output.resolve()}\n"
    )
    assert _mode(output) == 0o600
    assert sorted(output.parent.iterdir()) == sorted([metadata, output])
    evidence = read_edition_evidence(output)
    assert evidence.edition.kind == "corrected"
    assert evidence.landing_sha256 == hashlib.sha256(CORRECTED.read_bytes()).hexdigest()
    assert evidence.landing_retrieved_at.isoformat() == CAPTURED
    assert evidence.chapter_edition == "unknown"


def test_the_evidence_names_the_v1_record_it_sits_beside(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)
    output = _beside(metadata)

    assert _run(_argv(CORRECTED, metadata))[0] == 0

    record = read_metadata(metadata)
    evidence = read_edition_evidence(output)
    assert evidence.id == record.id
    assert evidence.source_url == record.source_url
    assert evidence.chapter_record.source_page_url == record.source_page_url
    assert evidence.chapter_record.source_sha256 == record.source_sha256
    assert evidence.chapter_record.retrieved_at == record.retrieved_at


def test_the_v1_record_is_not_touched(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)
    before = metadata.read_bytes()

    assert _run(_argv(CORRECTED, metadata))[0] == 0

    assert metadata.read_bytes() == before
    assert read_metadata(metadata).schema_version == 1


def test_a_landing_page_stating_no_edition_records_unknown(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path, "l")
    output = _beside(metadata)

    code, said_text = _run(_argv(PLAIN, metadata, url=L_LANDING))

    assert code == 0, said_text
    assert "edition unknown," in said_text
    evidence = read_edition_evidence(output)
    assert evidence.edition.kind == "unknown"
    assert evidence.edition.corrected_on is None
    assert evidence.edition.original_edition_url is None


def test_a_landing_page_of_another_document_is_not_associated(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path, "l")
    output = _beside(metadata)

    code, said_text = _run(_argv(CORRECTED, metadata))

    assert code == 1
    assert said("refused: edition_identity_mismatch", said_text)
    assert not output.exists()


def test_a_rerun_with_the_same_inputs_changes_nothing(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)
    output = _beside(metadata)
    assert _run(_argv(CORRECTED, metadata))[0] == 0
    first = output.read_bytes()

    code, said_text = _run(_argv(CORRECTED, metadata))

    assert code == 0
    assert said_text.startswith("unchanged regjeringen-9000002:")
    assert output.read_bytes() == first


@pytest.mark.parametrize("change", ["bytes", "time"])
def test_a_rerun_with_other_evidence_never_replaces_the_first(tmp_path: Path, change: str) -> None:
    metadata = _metadata(tmp_path)
    output = _beside(metadata)
    assert _run(_argv(CORRECTED, metadata))[0] == 0
    first = output.read_bytes()
    landing = tmp_path / "landing.html"
    landing.write_bytes(CORRECTED.read_bytes() + (b"\n" if change == "bytes" else b""))
    when = "2100-05-21T10:15:00+02:00" if change == "time" else CAPTURED

    code, said_text = _run(_argv(landing, metadata, when=when))

    assert code == 1
    assert said("refused: observation_exists", said_text)
    assert output.read_bytes() == first


def test_the_same_inputs_give_byte_identical_evidence(tmp_path: Path) -> None:
    first, second = _metadata(tmp_path / "a"), _metadata(tmp_path / "b")

    assert _run(_argv(CORRECTED, first))[0] == 0
    assert _run(_argv(CORRECTED, second))[0] == 0

    assert _beside(first).read_bytes() == _beside(second).read_bytes()


def test_a_v1_reader_refuses_the_evidence_as_a_record(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)
    assert _run(_argv(CORRECTED, metadata))[0] == 0

    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_metadata(_beside(metadata))

    assert caught.value.reason is RefusalReason.SCHEMA_INVALID


def test_the_evidence_claims_no_release_review_or_licence(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)
    assert _run(_argv(CORRECTED, metadata))[0] == 0

    payload = json.loads(_beside(metadata).read_text(encoding="utf-8"))

    assert payload["chapter_edition"] == "unknown"
    assert payload["landing_provenance"] == "operator_saved_offline"
    assert not {"released", "review", "source_license", "published_at"} & set(payload)


def test_a_metadata_record_others_can_read_is_refused(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)
    metadata.chmod(0o644)

    code, said_text = _run(_argv(CORRECTED, metadata))

    assert code == 1
    assert said("refused: observation_unsafe", said_text)
    assert not _beside(metadata).exists()


def test_a_file_that_is_not_a_v1_record_is_refused(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    metadata.chmod(0o600)

    code, said_text = _run(_argv(CORRECTED, metadata))

    assert code == 1
    assert said("refused: schema_invalid", said_text)
    assert not _beside(metadata).exists()


def test_a_record_inside_any_repository_gets_no_evidence_beside_it(tmp_path: Path) -> None:
    copied = tmp_path / "lovverk" / "forarbeider" / "regjeringen-9000002.json"
    copied.parent.mkdir(parents=True)
    (tmp_path / "lovverk" / ".git").mkdir()
    copied.write_bytes(_metadata(tmp_path).read_bytes())
    copied.chmod(0o600)

    code, said_text = _run(_argv(CORRECTED, copied))

    assert code == 1
    assert said("refused: unsafe_output_path", said_text)
    assert list(copied.parent.iterdir()) == [copied]


def test_a_relative_metadata_path_is_refused(tmp_path: Path) -> None:
    code, said_text = _run(_argv(CORRECTED, Path("regjeringen-9000002.json")))

    assert code == 1
    assert said("refused: unsafe_output_path", said_text)


def test_a_retrieval_time_without_an_offset_is_refused(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)

    code, said_text = _run(_argv(CORRECTED, metadata, when="2100-05-20T10:15:00"))

    assert code == 1
    assert said("refused: invalid_timestamp", said_text)
    assert not _beside(metadata).exists()


def test_a_chapter_url_given_as_the_landing_url_is_refused(tmp_path: Path) -> None:
    code, said_text = _run(_argv(CORRECTED, _metadata(tmp_path), url=f"{LS_LANDING}?ch=1"))

    assert code == 1
    assert said("refused: landing_page_required", said_text)


@pytest.mark.parametrize("landing", ["corrected", "conflicting"])
def test_nothing_the_page_says_reaches_the_output(tmp_path: Path, landing: str) -> None:
    source = tmp_path / "landing.html"
    text = CORRECTED.read_text(encoding="utf-8")
    source.write_text(text if landing == "corrected" else text.replace("edition", "x"), "utf-8")

    result = CliRunner().invoke(app, _argv(source, _metadata(tmp_path)))

    for phrase in PAGE_TEXT:
        assert phrase not in result.output


def test_the_command_runs_in_a_real_process(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)
    output = _beside(metadata)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from lovspor.cli import app; app()",
            *_argv(CORRECTED, metadata),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert isinstance(read_edition_evidence(output), EditionEvidence)


@pytest.mark.parametrize("extra", [0, 1], ids=["at-limit", "over-limit"])
def test_landing_size_limit_is_enforced_before_writing(tmp_path: Path, extra: int) -> None:
    metadata = _metadata(tmp_path)
    before = metadata.read_bytes()
    landing = tmp_path / "landing.html"
    payload = CORRECTED.read_bytes()
    with landing.open("wb") as stream:
        stream.write(payload)
        stream.write(b" " * (MAX_HTML_BYTES + extra - len(payload)))

    code, output = _run(_argv(landing, metadata))

    assert metadata.read_bytes() == before
    if extra:
        assert code == 1
        assert said("refused: input_too_large", output)
        assert list(metadata.parent.iterdir()) == [metadata]
    else:
        assert code == 0, output
        evidence = read_edition_evidence(_beside(metadata))
        assert evidence.landing_sha256 == hashlib.sha256(landing.read_bytes()).hexdigest()
        assert evidence.edition.kind == "corrected"


def test_missing_landing_is_a_typed_refusal_without_output(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)
    before = metadata.read_bytes()

    code, output = _run(_argv(tmp_path / "missing.html", metadata))

    assert code == 1
    assert said("refused: input_unreadable", output)
    assert metadata.read_bytes() == before
    assert list(metadata.parent.iterdir()) == [metadata]


def test_symlinked_metadata_is_refused_without_following_it(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)
    before = metadata.read_bytes()
    alias = metadata.with_name("alias.json")
    alias.symlink_to(metadata)

    code, output = _run(_argv(CORRECTED, alias))

    assert code == 1
    assert said("refused: observation_unsafe", output)
    assert alias.is_symlink()
    assert metadata.read_bytes() == before
    assert set(metadata.parent.iterdir()) == {metadata, alias}
