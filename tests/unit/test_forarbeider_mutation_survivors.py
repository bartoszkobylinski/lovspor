"""Observable corpus contracts for the surviving PR mutations."""

import json
import os
import stat
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.forarbeider import corpus_models as models
from lovspor.forarbeider import corpus_render as render
from lovspor.forarbeider import corpus_transaction as transaction
from lovspor.forarbeider import corpus_tree as tree
from lovspor.forarbeider.chapter_models import TextBlock, text_hash
from lovspor.forarbeider.corpus_plan import release_draft, withdraw_draft
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_store import write_preview
from tests.unit.forarbeider_corpus_support import manifest, member, metadata_of, refusal_of, times
from tests.unit.forarbeider_release_support import REVIEWED, Desk


def rendering(desk: Desk, complete: bool = False) -> render.Rendering:
    metadata = metadata_of(desk).model_copy(
        update={"ministry": "Testdepartementet", "published_at": date(2100, 1, 2)}
    )
    if complete:
        metadata = metadata.model_copy(
            update={
                "chapters": tuple(
                    c.model_copy(update={"chapter_number": i})
                    for i, c in enumerate(metadata.chapters[:2], 1)
                )
            }
        )
    results = [desk.results[2], desk.results[3]]
    if complete:
        results = [r.model_copy(update={"chapter_number": i}) for i, r in enumerate(results, 1)]
    return render.Rendering(
        metadata,
        tuple(render.ReleasedChapter(r, datetime.fromisoformat(REVIEWED)) for r in results),
        models.DocumentTimes.model_validate(times()),
    )


def test_rendered_document_and_index_bytes(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    value = rendering(desk)
    assert render.render_document(value) == EXPECTED_DOCUMENT
    for complete, expected in [(False, EXPECTED_PARTIAL_INDEX), (True, EXPECTED_COMPLETE_INDEX)]:
        data = member(desk)
        if complete:
            data["listed_chapters"] = [1, 2]
            data["chapters"] = [
                dict(
                    data["chapters"][0],
                    chapter_number=1,
                    chapter_id="regjeringen-9000001-ch1",
                    source_page_url=desk.results[2].source_url + "?ch=1",
                ),
                dict(
                    data["chapters"][1],
                    chapter_number=2,
                    chapter_id="regjeringen-9000001-ch2",
                    source_page_url=desk.results[2].source_url + "?ch=2",
                ),
            ]
            data["coverage"] = "every_listed_chapter"
        assert (
            render.render_index(models.ForarbeiderManifest.model_validate(manifest(data)))
            == expected
        )
    assert render.render_index(models.EMPTY_MANIFEST) == EXPECTED_EMPTY_INDEX
    whole = rendering(desk, True)
    output = render.render_document(whole)
    assert 'coverage: "every_listed_chapter"' in output
    assert "> Every chapter the source lists (1\u20132) is included." in output


@pytest.mark.parametrize(
    "where", ["chapters", "foreign", "ministry", "chapter_title", "index_title"]
)
def test_render_refusal_details(tmp_path: Path, where: str) -> None:
    desk = Desk(tmp_path)
    value = rendering(desk)
    expected = where
    if where == "chapters":
        value = value._replace(chapters=())
    elif where == "foreign":
        other = Desk(tmp_path / "other", kind="ls")
        value = value._replace(
            chapters=(render.ReleasedChapter(other.results[2], datetime.fromisoformat(REVIEWED)),)
        )
        expected = "chapters"
    elif where == "ministry":
        value = value._replace(
            metadata=value.metadata.model_copy(update={"ministry": "bad\nministry"})
        )
    elif where == "chapter_title":
        result = value.chapters[0].result.model_copy(update={"chapter_title": "bad\ntitle"})
        value = value._replace(chapters=(value.chapters[0]._replace(result=result),))
    with pytest.raises(ForarbeiderRefusedError) as caught:
        if where == "index_title":
            data = member(desk, title="bad\ntitle")
            render.render_index(models.ForarbeiderManifest.model_validate(manifest(data)))
        else:
            render.render_document(value)
    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.RENDER_UNSAFE,
        "title" if where == "index_title" else expected,
    )


def test_chapter_spacing_heading_cap_and_final_text(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    value = rendering(desk)
    blocks = (
        TextBlock(kind="heading", level=2, text="Other"),
        TextBlock(kind="heading", level=6, text="Deep"),
        TextBlock(kind="list_item", level=1, text="one"),
        TextBlock(kind="list_item", level=2, text="two"),
        TextBlock(kind="paragraph", text="ends X"),
    )
    result = desk.results[2].model_copy(update={"blocks": blocks, "text_sha256": text_hash(blocks)})
    value = value._replace(chapters=(value.chapters[0]._replace(result=result),))
    output = render.render_document(value)
    assert output.endswith(
        f"Source: <{result.source_page_url}>\n\n## {result.chapter_title}\n\n"
        "### Other\n\n###### Deep\n\n- one\n  - two\n\nends X\n"
    )


@pytest.mark.parametrize(
    "case,message",
    [
        ("listed", "listed chapters are the source's 1..N"),
        ("order", "released chapters are listed ones, in source order, once each"),
        ("id", "a chapter belongs to another document"),
        ("url", "a chapter belongs to another document"),
        ("coverage", "coverage does not match the released chapters"),
        ("late", "a chapter was approved after the owner decided"),
    ],
)
def test_manifest_diagnostics(tmp_path: Path, case: str, message: str) -> None:
    desk = Desk(tmp_path)
    data = member(desk)
    if case == "listed":
        data["listed_chapters"] = [2, 3, 4]
    elif case == "order":
        data["chapters"].reverse()
    elif case in {"id", "url"}:
        other = member(Desk(tmp_path / "other", kind="ls"))
        if case == "id":
            data["chapters"] = other["chapters"]
        else:
            # A valid receipt of a different URL slug, with the same document id.
            for receipt in data["chapters"]:
                receipt["source_page_url"] = receipt["source_page_url"].replace(
                    "prop.-901-l-20992100", "prop.-902-l-20992100"
                )
    elif case == "coverage":
        data["coverage"] = "every_listed_chapter"
    elif case == "late":
        data["chapters"][0]["review_decided_at"] = "2101-01-01T00:00:00Z"
    with pytest.raises(ValidationError) as caught:
        models.ManifestDocument.model_validate(data)
    assert [error["msg"] for error in caught.value.errors()] == ["Value error, " + message]


def test_review_at_owner_decision_is_accepted(tmp_path: Path) -> None:
    data = member(Desk(tmp_path))
    for receipt in data["chapters"]:
        receipt["review_decided_at"] = data["times"]["owner_decided_at"]
    assert models.ManifestDocument.model_validate(data).chapters


def test_duplicate_manifest_keys_keep_the_diagnostic() -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        models.parse_manifest(b'{"schema_version": 1, "schema_version": 1}')
    assert caught.value.reason == RefusalReason.MANIFEST_INVALID
    assert isinstance(caught.value.__cause__, ValueError)
    assert str(caught.value.__cause__) == "a key is given twice"


def test_transaction_symlink_and_cleanup(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"public")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert transaction._current(link) == ""
    transaction._clear(tmp_path)
    (tmp_path / transaction.STAGING).mkdir(parents=True)
    transaction._clear(tmp_path)
    assert not (tmp_path / transaction.STAGING).exists()


def test_transaction_parent_permissions_and_refusal(tmp_path: Path) -> None:
    previous = os.umask(0)
    try:
        parent = transaction._parent(tmp_path, "forarbeider/session/type/doc.md")
    finally:
        os.umask(previous)
    assert parent == tmp_path / "forarbeider/session/type"
    for directory in [parent, parent.parent, parent.parent.parent]:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    (parent / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        transaction._parent(tmp_path, "forarbeider/session/type/link/doc.md")
    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.UNSAFE_OUTPUT_PATH,
        "forarbeider",
    )


def test_staging_is_private_reusable_and_canonical(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    journal = transaction.Journal(
        schema_version=1,
        record_type="forarbeider_corpus_journal",
        action="release",
        decision_sha256="a" * 64,
        target_manifest_sha256="a" * 64,
        operations=(
            transaction.Operation(path=models.INDEX_PATH, sha256="a" * 64, previous_sha256=None),
            transaction.Operation(path=models.MANIFEST_PATH, sha256="a" * 64, previous_sha256=None),
        ),
    )
    previous = os.umask(0)
    try:
        transaction._stage(tmp_path, {models.INDEX_PATH: b"hello"}, journal)
    finally:
        os.umask(previous)
    assert stat.S_IMODE((tmp_path / transaction.STAGING).stat().st_mode) == 0o700
    transaction._stage(tmp_path, {models.INDEX_PATH: b"hello"}, journal)
    assert (tmp_path / transaction.JOURNAL).read_bytes() == (
        json.dumps(journal.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode()


EXPECTED_DOCUMENT = (
    '---\ndataset: "forarbeider"\nid: "regjeringen-9000001"\nsource_'
    'document_type: "forarbeid"\nnormative: false\ndocument_type: "'
    'prop-l"\nsession: "2099-2100"\nproposition_number: 901\ntitle: '
    '"Strukturell testproposisjon om et oppdiktet lovforslag"\nmin'
    'istry: "Testdepartementet"\nsource_provider: "regjeringen.no"'
    '\nsource_url: "https://www.regjeringen.no/no/dokumenter/prop.'
    '-901-l-20992100/id9000001/"\nsource_license: null\nrights_basi'
    's: "åndsverkloven § 14"\nrelease_status: "released"\ncoverage:'
    ' "partial_curated_excerpt"\nasserts_complete_proposition: fal'
    "se\nlisted_chapters: [1, 2, 3, 4]\nreleased_chapters: [2, 3]\nc"
    'hapter_text_sha256: ["1a4b456a1fa6b33a7b8003b97d90228897fd6c'
    '251040af22729e9bd8fd718c70", "1929e420a10580d717374d86003527'
    '8e8e29aa815e2a6c6e588f9fbf3ed8dbbd"]\npublished_at: "2100-01-'
    '02"\nrenderer_version: 1\n---\n\n# Prop. 901 L (2099\u20132100): Stru'
    "kturell testproposisjon om et oppdiktet lovforslag\n\n> **Fora"
    "rbeid: preparatory material, NOT enacted law.** It does not "
    "state whether the proposal was adopted, and it is not a sour"
    "ce of law in force.\n>\n> **Partial curated excerpt:** chapter"
    "s 2, 3 of the 4 the source lists. This is not the complete p"
    "roposition.\n>\n> Published on the basis of åndsverkloven § 14"
    "; no licence is asserted. Annexes, figures, images, tables a"
    "nd footnotes are never included.\n\nSource: <https://www.regje"
    "ringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/>\n\nSo"
    "urce: <https://www.regjeringen.no/no/dokumenter/prop.-901-l-"
    "20992100/id9000001/?ch=2>\n\n## 2 Bakgrunn\n\n### 2\\.1 Innlednin"
    "g\n\nDepartementet viser til lov om prøving § 14 første ledd, "
    "jf. § 24.\n\nFørste linje andre linje, med æøå og «sitattegn» "
    "\u2013 og §\xa03.\n\n- første punkt\n- andre punkt\n  - a) underpunkt\n\n#"
    "### 2\\.2 Vurdering\n\nAvsluttende vurdering.\n\nSource: <https:/"
    "/www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id900"
    "0001/?ch=3>\n\n## Tilråding\n\n### 2\\.1 Innledning\n\nDepartemente"
    "t viser til lov om prøving § 14 første ledd, jf. § 24.\n\nFørs"
    "te linje andre linje, med æøå og «sitattegn» \u2013 og §\xa03.\n\n- fø"
    "rste punkt\n- andre punkt\n  - a) underpunkt\n\n#### 2\\.2 Vurder"
    "ing\n\nAvsluttende vurdering.\n"
)


EXPECTED_PARTIAL_INDEX = (
    "# Forarbeider\n\n> **Forarbeid: preparatory material, NOT enac"
    "ted law.** It does not state whether the proposal was adopte"
    "d, and it is not a source of law in force.\n\nMembership is `f"
    "orarbeider/manifest.json`.\n\n- [Prop. 901 L (2099\u20132100): Stru"
    "kturell testproposisjon om et oppdiktet lovforslag](2099-210"
    "0/prop-l/9000001.md): chapters 2, 3 of 4 (partial curated ex"
    "cerpt)\n"
)


EXPECTED_EMPTY_INDEX = (
    "# Forarbeider\n\n> **Forarbeid: preparatory material, NOT enac"
    "ted law.** It does not state whether the proposal was adopte"
    "d, and it is not a source of law in force.\n\nMembership is `f"
    "orarbeider/manifest.json`.\n\nNo document is released.\n"
)


EXPECTED_COMPLETE_INDEX = (
    "# Forarbeider\n\n> **Forarbeid: preparatory material, NOT enac"
    "ted law.** It does not state whether the proposal was adopte"
    "d, and it is not a source of law in force.\n\nMembership is `f"
    "orarbeider/manifest.json`.\n\n- [Prop. 901 L (2099\u20132100): Stru"
    "kturell testproposisjon om et oppdiktet lovforslag](2099-210"
    "0/prop-l/9000001.md): chapters 1, 2 of 2 (every listed chapt"
    "er)\n"
)


def test_checkout_rejects_absolute_alias(tmp_path: Path) -> None:
    checkout = tmp_path / "real"
    (checkout / ".git").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(checkout, target_is_directory=True)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        tree.checked_checkout(alias)
    assert (caught.value.reason, caught.value.detail) == (RefusalReason.UNSAFE_INPUT_PATH, "corpus")


@pytest.mark.parametrize("kind", ["symlink", "missing", "fifo", "large"])
def test_public_file_refusals(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "input"
    reason = RefusalReason.INPUT_UNREADABLE
    if kind == "symlink":
        path.symlink_to(tmp_path / "absent")
        reason = RefusalReason.UNSAFE_INPUT_PATH
    elif kind == "fifo":
        os.mkfifo(path)
        reason = RefusalReason.UNSAFE_INPUT_PATH
    elif kind == "large":
        with path.open("wb") as stream:
            stream.truncate(tree.MAX_CORPUS_FILE_BYTES + 1)
        reason = RefusalReason.INPUT_TOO_LARGE
    fifo = path if kind == "fifo" else None
    refused = refusal_of(lambda: tree._read_public(path, "forarbeider"), fifo)
    assert (refused.reason, refused.detail) == (reason, "forarbeider")


def test_public_file_exact_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "input"
    with path.open("wb") as stream:
        stream.truncate(tree.MAX_CORPUS_FILE_BYTES)
        stream.seek(tree.MAX_CORPUS_FILE_BYTES - 1)
        stream.write(b"X")
    data = tree._read_public(path, "forarbeider")
    assert len(data) == tree.MAX_CORPUS_FILE_BYTES
    assert data[-1:] == b"X"


def test_sandbox_permissions_with_open_umask(tmp_path: Path) -> None:
    previous = os.umask(0)
    try:
        box = tree.create_sandbox(tmp_path / "box", ())
    finally:
        os.umask(previous)
    for path in box.rglob("*"):
        expected = 0o700 if path.is_dir() else 0o600
        # Intermediate .git is made by parents=True, with pathlib's default mode.
        if path == box / ".git":
            continue
        assert stat.S_IMODE(path.stat().st_mode) == expected, path
    assert tree.require_sandbox(box, ()) == box


@pytest.mark.parametrize("config", [b"[remote origin]\n", b"url = nowhere\n", None])
def test_sandbox_refuses_remote_or_unreadable_config(tmp_path: Path, config: bytes | None) -> None:
    box = tree.create_sandbox(tmp_path / "box", ())
    path = box / ".git/config"
    if config is None:
        path.unlink()
    else:
        path.write_bytes(config)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        tree.require_sandbox(box, ())
    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.CORPUS_NOT_SANDBOX,
        "sandbox",
    )


@pytest.mark.parametrize("kind", ["symlink", "fifo", "changed", "owner_execute"])
def test_sandbox_baseline_and_sharing_details(tmp_path: Path, kind: str) -> None:
    box = tree.create_sandbox(tmp_path / "box", ())
    reason = RefusalReason.UNSAFE_INPUT_PATH
    if kind == "symlink":
        (box / "extra").symlink_to(tmp_path / "absent")
    elif kind == "fifo":
        os.mkfifo(box / "extra")
    elif kind == "changed":
        (box / "README.md").write_text("changed")
        reason = RefusalReason.CORPUS_TREE_MISMATCH
    else:
        box.chmod(0o701)
        assert tree.require_sandbox(box, ()) == box
        return
    fifo = box / "extra" if kind == "fifo" else None
    refused = refusal_of(lambda: tree.require_sandbox(box, ()), fifo)
    assert (refused.reason, refused.detail) == (reason, "sandbox")
    baseline = refusal_of(lambda: tree._require_baseline(box), fifo)
    assert (baseline.reason, baseline.detail) == (reason, "sandbox")


@pytest.mark.parametrize("kind", ["file", "fifo"])
def test_dataset_unsafe_details(tmp_path: Path, kind: str) -> None:
    box = tree.create_sandbox(tmp_path / "box", ())
    if kind == "file":
        (box / "forarbeider").write_text("wrong type")
    else:
        (box / "forarbeider").mkdir()
        os.mkfifo(box / "forarbeider/pipe")
    fifo = box / "forarbeider/pipe" if kind == "fifo" else None
    refused = refusal_of(lambda: tree.read_dataset(box), fifo)
    assert (refused.reason, refused.detail) == (RefusalReason.UNSAFE_INPUT_PATH, "forarbeider")


def test_write_new_never_overwrites_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "existing"
    path.write_text("keep these bytes")
    with pytest.raises(FileExistsError):
        tree._write_new(path, "bad")
    assert path.read_text() == "keep these bytes"


def test_sandbox_path_errors_are_public(tmp_path: Path) -> None:
    (tmp_path / "repo/.git").mkdir(parents=True)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        tree.create_sandbox(tmp_path / "repo/box", ())
    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.UNSAFE_OUTPUT_PATH,
        "sandbox",
    )
    with pytest.raises(ForarbeiderRefusedError) as caught:
        tree.require_sandbox(tmp_path / "absent", ())
    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.UNSAFE_INPUT_PATH,
        "sandbox",
    )


def test_preview_unsafe_directory_detail(tmp_path: Path) -> None:

    (tmp_path / "previews").symlink_to(tmp_path / "absent")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        write_preview(tmp_path, "preview")
    assert caught.value.detail == "previews"


def test_withdrawn_member_cannot_be_released_again(tmp_path: Path) -> None:

    value = rendering(Desk(tmp_path))
    released = release_draft(tree.Dataset(models.EMPTY_MANIFEST, None), value)
    dataset = tree.Dataset(models.parse_manifest(released.writes[models.MANIFEST_PATH]), "a" * 64)
    # A still-released member may be replaced.
    assert release_draft(dataset, value).writes == released.writes
    withdrawn = withdraw_draft(
        dataset, "regjeringen-9000001", datetime.fromisoformat("2100-04-12T12:00:00Z")
    )
    dataset = tree.Dataset(models.parse_manifest(withdrawn.writes[models.MANIFEST_PATH]), "b" * 64)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        release_draft(dataset, value)
    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.DOCUMENT_NOT_RELEASED,
        "regjeringen-9000001",
    )


def test_verification_names_target_manifest(tmp_path: Path) -> None:
    box = tree.create_sandbox(tmp_path / "box", ())
    journal = transaction.Journal(
        schema_version=1,
        record_type="forarbeider_corpus_journal",
        action="release",
        decision_sha256="a" * 64,
        target_manifest_sha256="a" * 64,
        operations=(
            transaction.Operation(path=models.INDEX_PATH, sha256="a" * 64, previous_sha256=None),
            transaction.Operation(path=models.MANIFEST_PATH, sha256="a" * 64, previous_sha256=None),
        ),
    )
    with pytest.raises(ForarbeiderRefusedError) as caught:
        transaction._verify(box, journal)
    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.CORPUS_TREE_MISMATCH,
        models.MANIFEST_PATH,
    )


def test_sandbox_write_failure_preserves_public_reason(tmp_path: Path) -> None:
    """Exhaust descriptors in a child, so an actual OS write fails without a mock."""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import resource
import sys
import threading
import time
from pathlib import Path
from lovspor.forarbeider.corpus_tree import create_sandbox
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
resource.setrlimit(resource.RLIMIT_NOFILE, (0, 0))
try:
    create_sandbox(Path(sys.argv[1]), ())
except ForarbeiderRefusedError as error:
    assert error.reason == RefusalReason.OUTPUT_WRITE_FAILED
    assert error.detail == 'sandbox'
else:
    raise AssertionError('a write without a file descriptor must fail')
""",
            str(tmp_path / "box"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr


def test_new_file_text_is_utf8_in_ascii_locale(tmp_path: Path) -> None:
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import threading
import time
from pathlib import Path
from lovspor.forarbeider.corpus_tree import _write_new
path = Path(sys.argv[1])
_write_new(path, '\\u00e5')
assert path.read_bytes() == b'\\xc3\\xa5'
""",
            str(tmp_path / "utf8"),
        ],
        env={**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr


@pytest.mark.parametrize("kind,level", [("heading", 2), ("list_item", 1), ("heading", 1)])
def test_chapter_title_is_inserted_unless_first_block_is_its_level_one_heading(
    tmp_path: Path, kind: str, level: int
) -> None:
    desk = Desk(tmp_path)
    value = rendering(desk)
    title = desk.results[2].chapter_title
    text = title if level != 1 or kind == "list_item" else "Different heading"
    blocks = (TextBlock(kind=kind, level=level, text=text),)
    result = desk.results[2].model_copy(update={"blocks": blocks, "text_sha256": text_hash(blocks)})
    value = value._replace(chapters=(value.chapters[0]._replace(result=result),))
    block = f"{'#' * (level + 1)} {text}" if kind == "heading" else f"- {text}"
    assert render.render_document(value).endswith(
        f"Source: <{result.source_page_url}>\n\n## {title}\n\n{block}\n"
    )


def test_block_unsafe_text_diagnostic(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    value = rendering(desk)
    blocks = (TextBlock(kind="paragraph", text="bad\u202e"),)
    result = desk.results[2].model_copy(update={"blocks": blocks, "text_sha256": text_hash(blocks)})
    value = value._replace(chapters=(value.chapters[0]._replace(result=result),))
    with pytest.raises(ForarbeiderRefusedError) as caught:
        render.render_document(value)
    assert (caught.value.reason, caught.value.detail) == (RefusalReason.RENDER_UNSAFE, "text")


def test_final_nonbreaking_space_is_preserved(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    value = rendering(desk)
    blocks = (TextBlock(kind="paragraph", text="last\u00a0"),)
    result = desk.results[2].model_copy(update={"blocks": blocks, "text_sha256": text_hash(blocks)})
    value = value._replace(chapters=(value.chapters[0]._replace(result=result),))
    assert render.render_document(value).endswith("\n\nlast\u00a0\n")


def test_staged_journal_uses_utf8_for_unicode_dataset_paths(tmp_path: Path) -> None:
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import threading
import time
from pathlib import Path
from lovspor.forarbeider.corpus_transaction import Journal, Operation, _stage, JOURNAL
box = Path(sys.argv[1])
box.mkdir()
(box / '.git').mkdir()
# Unicode decimal digits are accepted by the operation's dataset-path grammar.
path = 'forarbeider/\\u0662\\u0660\\u0669\\u0669-2100/prop-l/1.md'
journal = Journal(schema_version=1, record_type='forarbeider_corpus_journal',
    action='release', decision_sha256='a' * 64,
    target_manifest_sha256='a' * 64,
    operations=(Operation(path=path, sha256='a' * 64, previous_sha256=None),
                Operation(path='forarbeider/manifest.json', sha256='a' * 64, previous_sha256=None)))
_stage(box, {}, journal)
assert path in (box / JOURNAL).read_bytes().decode('utf-8')
""",
            str(tmp_path / "stage"),
        ],
        env={**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr


def test_dataset_lstat_oserror_has_dataset_detail(tmp_path: Path) -> None:
    # An overlong path component makes lstat fail with ENAMETOOLONG, not ENOENT.
    with pytest.raises(ForarbeiderRefusedError) as caught:
        tree._dataset_files(tmp_path / ("x" * 256))
    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.UNSAFE_INPUT_PATH,
        "forarbeider",
    )


def test_absent_ministry_is_null_in_front_matter(tmp_path: Path) -> None:
    value = rendering(Desk(tmp_path))
    value = value._replace(metadata=value.metadata.model_copy(update={"ministry": None}))
    assert "\nministry: null\n" in render.render_document(value)


def test_document_title_is_checked_before_front_matter(tmp_path: Path) -> None:
    value = rendering(Desk(tmp_path))
    value = value._replace(metadata=value.metadata.model_copy(update={"title": "bad\u202e"}))
    with pytest.raises(ForarbeiderRefusedError) as caught:
        render.render_document(value)
    assert (caught.value.reason, caught.value.detail) == (RefusalReason.RENDER_UNSAFE, "title")


@pytest.mark.parametrize("kind", ["missing", "symlink"])
def test_unreadable_marker_is_not_our_sandbox(tmp_path: Path, kind: str) -> None:
    box = tree.create_sandbox(tmp_path / "box", ())
    marker = box / tree.MARKER
    marker.unlink()
    if kind == "symlink":
        marker.symlink_to(tmp_path / "absent")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        tree.require_sandbox(box, ())
    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.CORPUS_NOT_SANDBOX,
        "sandbox",
    )


def test_walk_keeps_nested_paths_and_refuses_symlinks(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "document").write_bytes(b"public bytes")
    assert tree._walk(tmp_path, "forarbeider") == {"forarbeider/nested/document": b"public bytes"}
    (nested / "alias").symlink_to(nested / "document")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        tree._walk(tmp_path, "forarbeider")
    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.UNSAFE_INPUT_PATH,
        "forarbeider",
    )


def test_public_reader_bounds_a_file_that_grows_during_read(tmp_path: Path) -> None:
    """A real concurrent writer cannot make a size-bounded read return extra bytes."""
    path = tmp_path / "growing"
    limit = tree.MAX_CORPUS_FILE_BYTES
    with path.open("wb") as stream:
        stream.truncate(limit)
    stop = threading.Event()

    def grow_and_shrink() -> None:
        with path.open("r+b", buffering=0) as stream:
            while not stop.is_set():
                stream.truncate(limit)
                time.sleep(0.001)
                stream.truncate(limit + 2)
                time.sleep(0.001)

    writer = threading.Thread(target=grow_and_shrink)
    writer.start()
    try:
        for _ in range(64):
            try:
                payload = tree._read_public(path, "forarbeider")
            except ForarbeiderRefusedError as error:
                assert error.reason == RefusalReason.INPUT_TOO_LARGE
            else:
                assert len(payload) <= limit + 1
    finally:
        stop.set()
        writer.join()


def test_checkout_rejects_absolute_dotdot_path(tmp_path: Path) -> None:
    checkout = tmp_path / "real"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "subdirectory").mkdir()
    alias = checkout / "subdirectory" / ".."
    with pytest.raises(ForarbeiderRefusedError) as caught:
        tree.checked_checkout(alias)
    assert (caught.value.reason, caught.value.detail) == (RefusalReason.UNSAFE_INPUT_PATH, "corpus")
