"""Version 1 authorizes one publication purpose for every chapter it covers (#765).

The PR3B contract asks for *the* identified publication purpose and an owner
decision scoped to *the* purpose. A second purpose declared for any requested
chapter makes the scope ambiguous, so it is refused rather than resolved, even
when one purpose alone would cover every chapter.
"""

from pathlib import Path

import pytest

from lovspor.forarbeider.release_models import ReleaseAuthorization, digest, record_text
from lovspor.forarbeider.release_store import read_record
from tests.unit.cli_output import said
from tests.unit.forarbeider_release_support import (
    Desk,
    at_a_terminal,  # noqa: F401
    chapter_reasons,
    said_digest,
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")


def _overlapping(desk: Desk, extra: str) -> list[str]:
    """Every gate, purpose A included, bound to 2 and 3; a distinct purpose B to ``extra``."""
    second = desk.write_evidence("publication_purpose", "-overlap")
    return [*desk.evidence_options(), "--evidence", f"publication_purpose@{extra}={second}"]


def _authorizations(desk: Desk) -> list[Path]:
    folder = desk.workspace / "authorizations"
    return sorted(folder.iterdir()) if folder.exists() else []


@pytest.mark.parametrize("extra", ["3", "2"])
def test_a_second_purpose_on_one_chapter_is_ambiguous(tmp_path: Path, extra: str) -> None:
    """A covers 2 and 3 and B only one of them: neither is chosen for the owner."""
    desk = Desk(tmp_path)
    desk.approve_all()
    dossier = said_digest(desk.dossier(_overlapping(desk, extra)).output, "dossier")

    result = desk.authorize(dossier)

    assert result.exit_code == 1, result.output
    assert said("refused: release_not_eligible: publication_purpose", result.output)
    assert _authorizations(desk) == []


def test_a_second_purpose_counts_even_once_its_file_changed(tmp_path: Path) -> None:
    """Ambiguity is in what was declared; an edited B does not leave A as the only one."""
    desk = Desk(tmp_path)
    desk.approve_all()
    options = _overlapping(desk, "3")
    dossier = said_digest(desk.dossier(options).output, "dossier")
    Path(options[-1].split("=", 1)[1]).write_bytes(b"another purpose statement")

    result = desk.authorize(dossier)

    assert result.exit_code == 1, result.output
    assert said("refused: release_not_eligible: publication_purpose", result.output)
    assert _authorizations(desk) == []


def test_one_purpose_alone_is_authorized(tmp_path: Path) -> None:
    """Control: one purpose bound to both chapters is the purpose stored."""
    desk = Desk(tmp_path)
    desk.approve_all()
    dossier = said_digest(desk.dossier().output, "dossier")

    result = desk.authorize(dossier)

    assert result.exit_code == 0, result.output
    sha = said_digest(result.output, "authorization")
    record = read_record(desk.workspace, "authorizations", sha, ReleaseAuthorization)
    assert record is not None
    assert record.purpose_sha256 == digest(desk.evidence["publication_purpose"].read_bytes())


def test_a_stored_authorization_over_an_overlapping_dossier_denies_that_chapter(
    tmp_path: Path,
) -> None:
    """Preflight re-checks: a chapter also bound to an unauthorized purpose is not planned."""
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    overlapping = said_digest(desk.dossier(_overlapping(desk, "3")).output, "dossier")
    path = desk.workspace / "authorizations" / f"{authorization}.json"
    record = ReleaseAuthorization.model_validate_json(path.read_bytes())
    text = record_text(record.model_copy(update={"dossier_sha256": overlapping}))
    stored = desk.workspace / "authorizations" / f"{digest(text.encode())}.json"
    stored.write_text(text, encoding="utf-8")
    stored.chmod(0o600)

    result = desk.preflight(digest(text.encode()), desk.all_checkpoints())

    report = desk.report(result.output)
    assert chapter_reasons(report, 2) == []
    assert chapter_reasons(report, 3) == ["purpose_not_authorized"]
    assert report["planned_chapters"] == [2]
    assert report["publication_authorized"] is False
    assert report["writes_corpus"] is False
