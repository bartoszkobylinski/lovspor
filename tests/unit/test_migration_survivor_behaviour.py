"""Migration contracts exercised with a real archive and corpus, without module mocks."""

from __future__ import annotations

import json
import locale
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.storage import ENV_OBSERVATORY_ROOT, ObservatoryRoot
from lovspor.promotion.commands import _authority
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import DecisionLog, WithdrawalRecord
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.migrate import (
    MigrationInputs,
    _front_matter,
    _primary_url,
    _published_source,
    _refuse_new_text,
    _require_version_approval,
    _reread,
    _source_key,
    migration_files,
    plan_migration,
    planned_diff,
    published,
    published_klass_version,
)
from lovspor.promotion.versions import read_primary
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    KLASS_VERSION,
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

LOCAL = "lokale-forskrifter"


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request):
    root_path = tmp_path / "archive"
    root_path.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(root_path))
    register(root_path)
    corpus_path = make_corpus(tmp_path)
    lines = REGULATION_LINES
    if getattr(request, "param", None) == "lovdata":
        lines = (REGULATION_LINES[0], "Dato: FOR-2019-12-12-2077", *REGULATION_LINES[1:])
    blob = store(root_path, html_page(lines))
    decision = approve(blob, Decision().write(tmp_path))
    assert decision.exit_code == 0, decision.output
    result = promote("local", blob, corpus_path)
    assert result.exit_code == 0, result.output
    git(corpus_path, "add", "-A")
    git(corpus_path, "commit", "-q", "-m", "promoted fixture")
    corpus = CorpusCheckout(corpus_path, [])
    [(doc_id, record)] = corpus.local_manifest().documents.items()
    document = published(corpus, doc_id, record)
    root = ObservatoryRoot(root_path, [])
    inputs = MigrationInputs(root, DecisionLog(root), corpus)
    authority = _authority(root, AUTHORITY, KLASS_VERSION)
    return inputs, document, authority


def _migrate(inputs, *args):
    return invoke("promote", "migrate", "--corpus", str(inputs.corpus.path), *args)


def _old(inputs, document):
    path = inputs.corpus.path / LOCAL / "manifest.json"
    data = json.loads(path.read_bytes())
    data["documents"][document.doc_id]["extractor_version"] = EXTRACTOR_VERSION - 1
    path.write_text(json.dumps(data), encoding="utf-8")
    audit_path = inputs.corpus.path / document.observations_path
    audit = json.loads(audit_path.read_bytes())
    for version in audit["versions"]:
        version["promotion"]["extractor_version"] = EXTRACTOR_VERSION - 1
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    git(inputs.corpus.path, "add", "-A")
    git(inputs.corpus.path, "commit", "-q", "-m", "previous extractor metadata")


def test_diff_preserves_names_newlines_and_every_file(tmp_path: Path):
    corpus = CorpusCheckout(make_corpus(tmp_path), [])
    (corpus.path / LOCAL / "a.txt").write_text("før\nkeep\n", encoding="utf-8")
    assert planned_diff(corpus, {f"{LOCAL}/z.txt": "ny\n", f"{LOCAL}/a.txt": "etter\nkeep\n"}) == (
        f"--- a/{LOCAL}/a.txt\n+++ b/{LOCAL}/a.txt\n"
        "@@ -1,2 +1,2 @@\n-før\n+etter\n keep\n"
        f"--- /dev/null\n+++ b/{LOCAL}/z.txt\n@@ -0,0 +1 @@\n+ny\n"
    )


@pytest.mark.parametrize("operation", ["published", "planned_diff"])
def test_utf8_reading_under_ascii_locale(state, operation):
    inputs, _, _ = state
    # Exercise the reader in this process as well, so mutation test selection
    # records the function coverage; the child also controls startup UTF-8 mode.
    previous = locale.setlocale(locale.LC_CTYPE)
    try:
        locale.setlocale(locale.LC_CTYPE, "C")
        [(doc_id, record)] = inputs.corpus.local_manifest().documents.items()
        if operation == "published":
            assert published(inputs.corpus, doc_id, record).markdown == (
                inputs.corpus.path / record.markdown_path
            ).read_bytes().decode("utf-8")
        else:
            diff = planned_diff(inputs.corpus, {record.markdown_path: "replacement\n"})
            assert "§" in diff
            assert diff.endswith("+replacement\n")
    finally:
        locale.setlocale(locale.LC_CTYPE, previous)
    # Disable Python's UTF-8 mode and locale coercion in a fresh interpreter.
    code = """
import locale, sys
assert not sys.flags.utf8_mode
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.migrate import published, planned_diff
assert locale.getencoding().lower() in ('ascii', 'us-ascii', 'ansi_x3.4-1968')
c = CorpusCheckout(__import__('pathlib').Path(sys.argv[1]), [])
i, r = next(iter(c.local_manifest().documents.items()))
if sys.argv[2] == 'published':
    assert published(c, i, r).markdown == (c.path / r.markdown_path).read_bytes().decode('utf-8')
else:
    diff = planned_diff(c, {r.markdown_path: 'replacement\\n'})
    assert '\\u00a7' in diff
    assert diff.endswith('+replacement\\n')
"""
    env = {**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    result = subprocess.run(
        [sys.executable, "-c", code, str(inputs.corpus.path), operation],
        env=env,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def test_unreadable_observations_name_the_error(state):
    inputs, document, _ = state
    (inputs.corpus.path / document.observations_path).write_text("{}", encoding="utf-8")
    with pytest.raises(PromotionRefusedError) as error:
        published(inputs.corpus, document.doc_id, document.record)
    assert (
        str(error.value)
        == f"{document.doc_id}: its rendering or observations do not read (ValidationError)"
    )


@pytest.mark.parametrize("rules", [1, 2, 4])
def test_front_matter_ignores_body_keys_and_horizontal_rules(state, rules):
    _, document, _ = state
    changed = document.model_copy(
        update={
            "markdown": document.markdown
            + '\nsource_url: "body"\n'
            + '\n---\nsource_sha256: "body"\n' * rules
        }
    )
    assert _front_matter(changed) == _front_matter(document)
    assert _source_key(changed) == _source_key(document)
    bare = document.model_copy(update={"markdown": '---\nid: "x"\n---\n'})
    with pytest.raises(PromotionRefusedError, match="names no klass_version"):
        published_klass_version(bare)


@pytest.mark.parametrize(
    "read, message", [(_source_key, "source"), (_published_source, "observed source")]
)
def test_missing_front_matter_has_actionable_refusal(state, read, message):
    _, document, _ = state
    torn = document.model_copy(update={"markdown": "---\nid: x\n---\n"})
    with pytest.raises(PromotionRefusedError) as error:
        read(torn)
    assert str(error.value) == f"{document.doc_id}: its front matter does not name its {message}"


def test_reread_returns_the_unchanged_published_document(state):
    inputs, document, authority = state
    prepared = _reread(inputs, document, _source_key(document), authority)
    assert prepared.markdown == document.markdown
    assert prepared.unchanged is True


def test_text_change_refusal_names_both_hashes_and_next_command(state):
    _, document, _ = state
    new_hash = "0123456789abcdef" * 4
    with pytest.raises(PromotionRefusedError) as error:
        _refuse_new_text(document, new_hash)
    assert str(error.value) == (
        f"{document.doc_id}: extractor v{EXTRACTOR_VERSION} reads another text (content_hash "
        f"{document.record.content_hash[:12]}… → {new_hash[:12]}…). That is a new version, "
        "not a migration: approve it and run `lovspor promote backfill` (or `promote local`)"
    )


def test_changed_rendering_is_refused_without_rewriting(state):
    inputs, document, authority = state
    changed = document.model_copy(update={"markdown": document.markdown + "extra\n"})
    with pytest.raises(PromotionRefusedError) as error:
        plan_migration(inputs, changed, authority)
    assert str(error.value) == (
        f"{document.doc_id}: under extractor v{EXTRACTOR_VERSION} its rendering changes "
        "though its text does not; a migration never rewrites the Markdown"
    )


@pytest.mark.parametrize("damage", ["gap", "urls"])
def test_observations_require_contiguous_versions_at_one_url(state, damage):
    _, document, _ = state
    first = document.observations.versions[0]
    if damage == "gap":
        versions = (first.model_copy(update={"version": 2}),)
    else:
        versions = (
            first,
            first.model_copy(update={"version": 2, "primary_url": PAGE_URL + "/other"}),
        )
    altered = document.model_copy(
        update={
            "record": document.record.model_copy(update={"version": 2}),
            "observations": document.observations.model_copy(update={"versions": versions}),
        }
    )
    with pytest.raises(PromotionRefusedError) as error:
        _primary_url(altered)
    assert str(error.value) == f"{document.observations_path} does not list one URL's versions 1..2"


def test_earlier_version_without_decision_lists_all_blobs(state, tmp_path):
    inputs, document, authority = state
    # Distinct archived HTML, identical extracted text: both blobs can be approved.
    store(inputs.root.path, html_page() + b"<!-- second capture -->")
    history = read_primary(inputs.log, inputs.fetches(AUTHORITY), PAGE_URL, None)
    version = history.versions[0]
    assert len(version.source_sha256s) == 2
    prepared = _reread(inputs, document, _source_key(document), authority)
    empty_path = tmp_path / "empty-decisions"
    empty_path.mkdir()
    empty = DecisionLog(ObservatoryRoot(empty_path, []))
    with pytest.raises(PromotionRefusedError) as error:
        _require_version_approval(empty, version, prepared)
    assert str(error.value).startswith(
        f"v1 (approve one of {', '.join(sorted(version.source_sha256s))}): "
    )


@pytest.mark.parametrize("match_by", ["artifact", "document"])
def test_withdrawal_is_checked_by_artifact_and_document(state, match_by):
    inputs, document, authority = state
    key = _source_key(document)
    inputs.decisions.append(
        WithdrawalRecord(
            doc_id=document.doc_id if match_by == "document" else "another-document",
            authority_id=AUTHORITY,
            slug=document.record.slug,
            removed_reason="withdrawn_misclassified",
            artifacts=(key,) if match_by == "artifact" else (),
            decided_by=REVIEWER,
            reviewer_role=REVIEWER_ROLE,
            decided_at=FIRST_SEEN,
            reason="Misclassified source.",
        )
    )
    with pytest.raises(PromotionRefusedError, match="was withdrawn"):
        plan_migration(inputs, document, authority)


def test_cli_refusals_go_to_stderr(state):
    inputs, document, _ = state
    _old(inputs, document)
    (inputs.corpus.path / document.record.markdown_path).write_bytes(b"\xff")
    result = _migrate(inputs)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith(f"Refused: {document.doc_id}: ")


def test_cli_current_document_and_partial_selector_messages(state):
    inputs, document, _ = state
    result = _migrate(inputs)
    assert result.exit_code == 0, result.output
    assert result.stdout == (
        f"{document.doc_id}: already at extractor v{EXTRACTOR_VERSION}\n"
        "Nothing to migrate; nothing written, nothing to commit.\n"
    )
    selected = _migrate(inputs, "--authority", AUTHORITY, "--slug", document.record.slug)
    assert selected.exit_code == 0, selected.output
    assert selected.stdout == result.stdout
    for option, value in [("--authority", AUTHORITY), ("--slug", document.record.slug)]:
        result = _migrate(inputs, option, value)
        assert result.exit_code == 1
        assert (
            result.output.strip() == "Refused: name one document with both --authority and --slug, "
            "or neither for every document"
        )


def test_cli_dry_run_has_exact_diff_then_status(state):
    inputs, document, authority = state
    _old(inputs, document)
    record = inputs.corpus.local_manifest().documents[document.doc_id]
    migration = plan_migration(inputs, published(inputs.corpus, document.doc_id, record), authority)
    expected = planned_diff(inputs.corpus, migration_files(inputs.corpus, [migration]))
    result = _migrate(inputs, "--dry-run")
    assert result.exit_code == 0, result.output
    assert result.stdout == expected + "Dry run: nothing written, nothing recorded.\n"


def test_cli_write_names_migration_and_written_paths(state):
    inputs, document, _ = state
    _old(inputs, document)
    result = _migrate(inputs)
    assert result.exit_code == 0, result.output
    assert (
        f"{document.doc_id}: migrated extractor v{EXTRACTOR_VERSION - 1} "
        f"-> v{EXTRACTOR_VERSION}; the Markdown is unchanged\n"
    ) in result.stdout
    assert f"wrote {LOCAL}/manifest.json\n" in result.stdout
    assert f"wrote {document.observations_path}\n" in result.stdout


def test_current_document_does_not_stop_processing_later_records(state):
    inputs, document, _ = state
    path = inputs.corpus.path / LOCAL / "manifest.json"
    data = json.loads(path.read_bytes())
    later_id = "zz-later-document"
    data["documents"][later_id] = {
        **data["documents"][document.doc_id],
        "markdown_path": f"{LOCAL}/{AUTHORITY}/missing.md",
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    git(inputs.corpus.path, "add", "-A")
    git(inputs.corpus.path, "commit", "-q", "-m", "later missing document")
    result = _migrate(inputs)
    assert result.exit_code == 1, result.output
    assert f"Refused: {later_id}:" in result.stderr


@pytest.mark.parametrize("state", ["lovdata"], indirect=True)
def test_reread_refuses_a_new_central_corpus_collision(state):
    inputs, document, authority = state
    assert document.doc_id == "lf-20191212-2077"
    path = inputs.corpus.path / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["documents"]["sf-20191212-2077"] = next(iter(manifest["documents"].values()))
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PromotionRefusedError) as error:
        _reread(inputs, document, _source_key(document), authority)
    assert (
        str(error.value)
        == f"{document.doc_id}: extractor v{EXTRACTOR_VERSION} names its text otherwise"
    )


@pytest.mark.parametrize(
    "markdown, expected",
    [
        ("key: value---\n", {"key": "value---"}),
        ('XX---\nXXsource_url: "retained"\n', {"XXsource_url": '"retained"'}),
    ],
)
def test_front_matter_preserves_raw_values_in_a_torn_header(state, markdown, expected):
    _, document, _ = state
    torn = document.model_copy(update={"markdown": markdown})
    assert _front_matter(torn) == expected
