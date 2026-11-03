"""What exactly each PR3B-2 part says, writes and refuses (#738, ADR-0018 PR3B-2).

The behaviour tests prove a refusal happens; these pin its reason and detail,
the exact lines an operator reads, the permissions of what is created and the
edges of each bound, so a quieter or wrong-labelled refusal fails here.
"""

import os
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider import corpus_commands, corpus_transaction, corpus_tree
from lovspor.forarbeider.corpus_commands import Verified, rendering_of
from lovspor.forarbeider.corpus_models import ForarbeiderManifest, ManifestDocument
from lovspor.forarbeider.corpus_transaction import JOURNAL, STAGING
from lovspor.forarbeider.corpus_tree import create_sandbox, read_dataset, require_sandbox
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_models import (
    PreflightReport,
    ReleaseAuthorization,
    ReleaseDossier,
    digest,
)
from lovspor.forarbeider.release_preflight import Observed, PreflightInputs, Stored
from lovspor.forarbeider.review_ledger import load_history
from lovspor.forarbeider.review_models import ReviewStatus, subject_of
from tests.unit.cli_output import said
from tests.unit.forarbeider_corpus_support import (
    manifest,
    member,
    metadata_of,
    read_beside_fifo,
    times,
)
from tests.unit.forarbeider_promotion_support import (
    eligible,
    plan,
    planned,
    release_options,
    sandbox,
    simulate,
    withdraw,
)
from tests.unit.forarbeider_release_support import (
    DECIDED,
    Desk,
    at_a_terminal,  # noqa: F401
    said_digest,
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")
DOCUMENT = "forarbeider/2099-2100/prop-l/9000001.md"


def _mode(path: Path) -> int:
    return os.lstat(path).st_mode & 0o777


def _refusal(action: Any, *arguments: Any) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        action(*arguments)
    return caught.value


@pytest.fixture
def desk(tmp_path: Path) -> Desk:
    return Desk(tmp_path / "desk")


@pytest.fixture
def box(desk: Desk, tmp_path: Path) -> Path:
    path = tmp_path / "sandbox"
    assert sandbox(desk, path).exit_code == 0
    return path


# The manifest says why it refuses.


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"path": "forarbeider/2099-2100/prop-l/9.md"}, "path is not this document's scoped path"),
        ({"listed_chapters": [2, 3, 4]}, "listed chapters are the source's 1..N"),
        (
            {"listed_chapters": [1, 2]},
            "released chapters are listed ones, in source order, once each",
        ),
        ({"coverage": "every_listed_chapter"}, "coverage does not match the released chapters"),
        ({"release_status": "withdrawn"}, "only a withdrawn document has a withdrawal time"),
        ({"markdown_sha256": None}, "a released document has a rendering, a withdrawn one none"),
    ],
)
def test_a_member_refusal_names_its_rule(desk: Desk, changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        ManifestDocument.model_validate(member(desk, **changes))


def test_a_chapter_under_another_id_or_url_names_its_rule(desk: Desk) -> None:
    built = member(desk)
    renamed = {**built, "chapters": [{**built["chapters"][0], "chapter_id": "regjeringen-1-ch2"}]}
    moved = dict(built)
    moved["document"] = {**built["document"], "source_url": built["document"]["source_url"] + "x/"}

    for broken in (renamed, moved):
        with pytest.raises(ValidationError, match="a chapter belongs to another document|identity"):
            ManifestDocument.model_validate(broken)
    with pytest.raises(ValidationError, match="a chapter belongs to another document"):
        ManifestDocument.model_validate(moved)


def test_an_approval_after_the_owner_names_its_rule_and_one_at_the_same_instant_is_kept(
    desk: Desk,
) -> None:
    built = member(desk)
    late = [{**item, "review_decided_at": "2100-04-04T12:00:01Z"} for item in built["chapters"]]
    same = [{**item, "review_decided_at": DECIDED} for item in built["chapters"]]

    with pytest.raises(ValidationError, match="a chapter was approved after the owner decided"):
        ManifestDocument.model_validate({**built, "chapters": late})
    assert ManifestDocument.model_validate({**built, "chapters": same}).chapters


def test_times_out_of_order_name_their_rule(desk: Desk) -> None:
    early = {**times(), "released_at": "2100-04-03T00:00:00Z"}

    with pytest.raises(ValidationError, match="review, owner decision, release and withdrawal"):
        ManifestDocument.model_validate(member(desk, times=early))


def test_every_listed_chapter_released_is_accepted_as_such(desk: Desk) -> None:
    built = member(desk, listed_chapters=[1, 2], coverage="every_listed_chapter")
    first = {**built["chapters"][0], "chapter_number": 1, "chapter_id": "regjeringen-9000001-ch1"}
    first["source_page_url"] = first["source_page_url"].replace("?ch=2", "?ch=1")
    second = built["chapters"][0]

    parsed = ManifestDocument.model_validate({**built, "chapters": [first, second]})

    assert parsed.coverage == "every_listed_chapter"


def test_a_manifest_refusal_names_its_rule(tmp_path: Path) -> None:
    first = member(Desk(tmp_path / "l"))
    second = member(Desk(tmp_path / "ls", kind="ls"))

    with pytest.raises(ValidationError, match="documents must be sorted by path, once each"):
        ForarbeiderManifest.model_validate({**manifest(), "documents": [second, first]})
    twin = {**first, "path": "forarbeider/2099-2100/prop-ls/9000001.md"}
    twin["document"] = {**first["document"], "document_type": "prop-ls"}
    with pytest.raises(ValidationError, match="a document is listed twice"):
        ForarbeiderManifest.model_validate(manifest(first, twin))


# The tree reader and the sandbox.


def test_a_sandbox_is_owner_only_throughout(tmp_path: Path) -> None:
    path = create_sandbox(tmp_path / "sandbox", ())

    directories = [path, path / "lover", path / ".git", path / ".git" / "refs" / "heads"]
    assert {_mode(item) for item in directories} == {0o700}
    files = [path / "README.md", path / "lover" / "README.md", path / ".git" / "HEAD"]
    assert {_mode(item) for item in files} == {0o600}


def test_a_sandbox_that_cannot_be_written_is_refused_by_label(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o500)
    try:
        error = _refusal(create_sandbox, parent / "sandbox", ())
    finally:
        parent.chmod(0o700)

    assert (error.reason, error.detail) == (RefusalReason.OUTPUT_WRITE_FAILED, "sandbox")


@pytest.mark.parametrize(
    "config",
    [
        '[remote "origin"]\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n',
        '[url "https://example.invalid/"]\n\tinsteadOf = x\n',
    ],
)
def test_a_remote_section_or_any_url_is_not_a_sandbox(tmp_path: Path, config: str) -> None:
    path = create_sandbox(tmp_path / "sandbox", ())
    with (path / ".git" / "config").open("a") as stream:
        stream.write(config)

    error = _refusal(require_sandbox, path, ())

    assert (error.reason, error.detail) == (RefusalReason.CORPUS_NOT_SANDBOX, "sandbox")


def test_an_unreadable_git_config_is_not_a_sandbox(tmp_path: Path) -> None:
    path = create_sandbox(tmp_path / "sandbox", ())
    (path / ".git" / "config").chmod(0o000)
    try:
        error = _refusal(require_sandbox, path, ())
    finally:
        (path / ".git" / "config").chmod(0o600)

    assert error.reason is RefusalReason.CORPUS_NOT_SANDBOX


def test_others_may_traverse_a_sandbox_but_not_write_it(tmp_path: Path) -> None:
    path = create_sandbox(tmp_path / "sandbox", ())

    path.chmod(0o701)
    assert require_sandbox(path, ()) == path
    path.chmod(0o720)
    error = _refusal(require_sandbox, path, ())
    path.chmod(0o700)

    assert error.reason is RefusalReason.CORPUS_NOT_SANDBOX


def test_a_symlinked_directory_inside_the_dataset_is_refused(tmp_path: Path) -> None:
    path = create_sandbox(tmp_path / "sandbox", ())
    (path / "forarbeider").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (path / "forarbeider" / "2099-2100").symlink_to(tmp_path / "elsewhere")

    error = _refusal(read_dataset, path)

    assert (error.reason, error.detail) == (RefusalReason.UNSAFE_INPUT_PATH, "forarbeider")


def test_a_file_of_exactly_the_bound_is_read_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = create_sandbox(tmp_path / "sandbox", ())
    largest = max(len(text.encode()) for text in corpus_tree.BASELINE.values())
    bound = max(
        largest, len(corpus_tree.MARKER_TEXT.encode()), len((path / ".git" / "config").read_bytes())
    )
    monkeypatch.setattr(corpus_tree, "MAX_CORPUS_FILE_BYTES", bound)

    assert require_sandbox(path, ()) == path
    monkeypatch.setattr(corpus_tree, "MAX_CORPUS_FILE_BYTES", 1)
    assert _refusal(require_sandbox, path, ()).reason is RefusalReason.CORPUS_NOT_SANDBOX


# The transaction.


def test_dataset_directories_are_owner_only(desk: Desk, box: Path) -> None:
    assert simulate(desk, planned(desk, box), box).exit_code == 0

    for directory in ("forarbeider", "forarbeider/2099-2100", "forarbeider/2099-2100/prop-l"):
        assert _mode(box / directory) == 0o700, directory


def test_a_staging_directory_left_without_a_journal_does_not_block_a_change(
    desk: Desk, box: Path
) -> None:
    sha = planned(desk, box)
    (box / STAGING).mkdir(mode=0o700)
    (box / STAGING / "leftover").write_bytes(b"x")

    result = simulate(desk, sha, box)

    assert result.exit_code == 0, result.output
    assert not (box / STAGING).exists() and not (box / JOURNAL).exists()


def test_a_dataset_parent_that_is_not_a_directory_is_refused_by_label(
    desk: Desk, box: Path
) -> None:
    sha = planned(desk, box)
    (box / "forarbeider").write_text("not a directory\n")

    result = simulate(desk, sha, box)

    assert result.exit_code == 1
    assert said("refused: unsafe_input_path: forarbeider", result.output), result.output


# Exactly what the commands print.


def test_the_command_lines_are_exactly_these(desk: Desk, box: Path, tmp_path: Path) -> None:
    fixtures = desk.invoke(
        [*release_options(desk), "synthetic-fixtures", "--out", str(tmp_path / "fx")]
    )
    assert fixtures.output.splitlines()[0] == "FIXTURES synthetic, invented text only:"
    assert fixtures.output.splitlines()[1] == "prop-l-ch2.html"
    created = sandbox(desk, tmp_path / "other")
    assert created.output == (
        "SANDBOX created: a new empty Git checkout with a synthetic baseline, no remote\n"
    )
    report, checkpoints = eligible(desk)
    shown = plan(desk, report, checkpoints, box).output.splitlines()
    sha = said_digest(shown[0], "plan")
    assert (
        shown[0]
        == f"PLAN regjeringen-9000001 plan {sha} action release publication_authorized=false"
    )
    assert shown[1] == "base forarbeider/manifest.json absent"
    assert [line.split()[1] for line in shown[2:5]] == [
        DOCUMENT,
        "forarbeider/INDEX.md",
        "forarbeider/manifest.json",
    ]
    assert shown[5] == "synthetic_inputs=true writes_corpus=false"
    manifest_sha = shown[4].split()[2]
    tail = f"in the synthetic sandbox: forarbeider/manifest.json {manifest_sha}"
    tail += " (publication_authorized=false, no git add, commit or push)\n"
    assert simulate(desk, sha, box).output == f"APPLIED regjeringen-9000001 plan {sha} {tail}"
    assert simulate(desk, sha, box).output == f"UNCHANGED regjeringen-9000001 plan {sha} {tail}"
    lines = withdraw(desk, box, "regjeringen-9000001").output.splitlines()
    assert lines[2:4] == [f"removed {DOCUMENT}", lines[3]]
    assert lines[3].startswith("rewritten forarbeider/manifest.json ")
    assert lines[4] == corpus_commands.HISTORY_WARNING


@pytest.mark.parametrize("command", ["plan", "simulate"])
def test_a_malformed_digest_is_refused_by_its_option(desk: Desk, box: Path, command: str) -> None:
    if command == "plan":
        result = plan(desk, "not-a-digest", [], box)
    else:
        result = simulate(desk, "not-a-digest", box)

    assert said(
        f"refused: release_invalid: {'report' if command == 'plan' else 'plan'}", result.output
    )


def test_a_report_that_changed_but_still_clears_is_denied_as_changed(desk: Desk, box: Path) -> None:
    report, checkpoints = eligible(desk)
    desk.decide(3, ReviewStatus.HELD, at="2100-04-09T00:00:00Z")

    result = plan(desk, report, checkpoints, box)

    assert result.exit_code == 2
    assert said("refused: promotion_denied: report_changed", result.output)


def test_a_report_that_now_denies_is_kept_and_named(desk: Desk, box: Path) -> None:
    report, checkpoints = eligible(desk)
    for path in (desk.workspace / "dossiers").iterdir():
        path.unlink()

    result = plan(desk, report, checkpoints, box)

    denied = re.search(r"promotion_denied: ([0-9a-f]{64})", result.output)
    assert denied is not None, result.output
    assert (desk.workspace / "reports" / f"{denied[1]}.json").exists()


def test_an_authorization_ends_at_valid_until_itself(
    desk: Desk, box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = planned(desk, box)
    monkeypatch.setattr(corpus_commands, "_now", lambda: datetime(2100, 5, 1, tzinfo=UTC))

    result = simulate(desk, sha, box)

    assert said("refused: promotion_denied: authorization_expired", result.output)


def test_a_withdrawn_document_is_refused_by_its_id_on_release(desk: Desk, box: Path) -> None:
    sha = planned(desk, box)
    assert simulate(desk, sha, box).exit_code == 0
    assert withdraw(desk, box, "regjeringen-9000001").exit_code == 0

    result = simulate(desk, sha, box)

    assert said("refused: document_not_released: regjeringen-9000001", result.output)


def test_previews_in_a_shared_directory_are_refused_by_label(desk: Desk, box: Path) -> None:
    report, checkpoints = eligible(desk)
    (desk.workspace / "previews").mkdir(mode=0o700)
    (desk.workspace / "previews").chmod(0o755)

    result = plan(desk, report, checkpoints, box)

    assert said("refused: review_ledger_unsafe: previews", result.output), result.output


# A verified preflight becomes a rendering only when it is complete.


def _verified(desk: Desk, **changes: Any) -> Verified:
    desk.approve_all()
    result = desk.results[2]
    history = load_history(desk.ledger, subject_of(result))
    authorization = Stored(
        ReleaseAuthorization.model_construct(decided_at=datetime(2100, 4, 4, 12, tzinfo=UTC)),
        "a" * 64,
    )
    dossier = Stored(
        ReleaseDossier.model_construct(reviewed_at=datetime(2100, 4, 3, 12, tzinfo=UTC)),
        "b" * 64,
    )
    fields: dict[str, Any] = {
        "metadata": metadata_of(desk),
        "metadata_page": None,
        "observed": (Observed(result, None, history),),
        "dossier": dossier,
        "evidence": {},
        "authorization": authorization,
        "revoked": False,
        "checkpoints": {},
        "at": datetime(2100, 4, 10, 12, tzinfo=UTC),
    }
    report = PreflightReport.model_construct(planned_chapters=changes.pop("planned", (2,)))
    return Verified(PreflightInputs(**{**fields, **changes}), report, digest(b"r"))


@pytest.mark.parametrize(
    "changes",
    [{"dossier": None}, {"authorization": None}, {"planned": (2, 3)}],
    ids=["no-dossier", "no-authorization", "unobserved-chapter"],
)
def test_a_preflight_missing_any_part_renders_nothing(desk: Desk, changes: dict[str, Any]) -> None:
    error = _refusal(rendering_of, _verified(desk, **changes))

    assert (error.reason, error.detail) == (RefusalReason.PROMOTION_DENIED, "report")


def test_a_rendering_carries_the_stated_publication_date_and_the_owner_times(desk: Desk) -> None:
    verified = _verified(desk)
    metadata = verified.inputs.metadata.model_copy(update={"published_at": date(2099, 12, 1)})

    rendering = rendering_of(verified._replace(inputs=verified.inputs._replace(metadata=metadata)))

    assert rendering.times.source_published_at == date(2099, 12, 1)
    assert rendering.times.owner_decided_at == datetime(2100, 4, 4, 12, tzinfo=UTC)
    assert rendering.times.reviewed_at == datetime(2100, 4, 3, 12, tzinfo=UTC)
    assert [item.result.chapter_number for item in rendering.chapters] == [2]


def test_bytes_staged_for_a_change_are_owner_only(
    desk: Desk, box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = planned(desk, box)

    def crash(source: Path, target: Path) -> None:
        if target.name == "INDEX.md":
            raise OSError("simulated crash")
        source.replace(target)

    monkeypatch.setattr(corpus_transaction, "replace", crash)
    assert simulate(desk, sha, box).exit_code == 1

    assert _mode(box / STAGING) == 0o700
    assert (box / JOURNAL).exists()


def test_a_fifo_in_the_dataset_is_refused_without_waiting_for_a_writer(tmp_path: Path) -> None:
    """Opening a FIFO without O_NONBLOCK waits for a writer forever: the read must not."""
    path = create_sandbox(tmp_path / "sandbox", ())
    (path / "forarbeider").mkdir()
    os.mkfifo(path / "forarbeider" / "manifest.json")

    blocked, outcome = read_beside_fifo(path, path / "forarbeider" / "manifest.json")

    assert not blocked, "the reader blocked on a FIFO"
    assert outcome == [RefusalReason.UNSAFE_INPUT_PATH]
