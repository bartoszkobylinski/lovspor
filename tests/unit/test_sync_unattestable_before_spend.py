"""A keyed sync that will be refused by attestation pays nothing first (#804).

A changed document that cannot render keeps its prior record and old
`xml_hash`, so the ADR-0012 attestation refuses the run after the commit and
before the push. Every other changed document had been embedded by then, so
each night paid for vectors it threw away. The refusal now comes before the
first provider call.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from lovspor.errors import ParseError, RenderError
from lovspor.storage.manifest import read_manifest
from lovspor.sync.orchestrator import _ChangePlan, _guard_change_reembed, run_sync
from lovspor.sync.reembed_guard import rendered_or_refused
from lovspor.temporal_attestation import AttestationError
from tests.unit.test_sync_change_guard_before_backfill import (
    _live_migrations,
    _manifest_bytes,
    _with_one_stale_sidecar,
)
from tests.unit.test_sync_change_reembed_guard import (
    _OBSERVED,
    _corpus,
    _CountingEmbedder,
    _doc,
    _state,
    _upstream,
)


@pytest.mark.parametrize("allow_mass_reembed", [False, True])
def test_an_unrenderable_changed_document_stops_the_sync_before_any_embedding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allow_mass_reembed: bool
) -> None:
    settings = _corpus(tmp_path, 3)
    before = _state(settings.lovverk_repo_path)
    embedder = _CountingEmbedder()
    broken = replace(_doc(1), xml_bytes=b"<html", xml_hash="f" * 64)
    _upstream(monkeypatch, [_doc(0, "Body. Amended."), broken, _doc(2, "Body. Amended.")], embedder)

    with pytest.raises(AttestationError, match="lov-1") as refused:
        run_sync(settings, allow_mass_reembed=allow_mass_reembed)

    assert embedder.texts == 0
    assert _state(settings.lovverk_repo_path) == before
    assert "before any embedding" in str(refused.value)


def test_an_unrenderable_new_document_is_still_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _corpus(tmp_path, 2)
    embedder = _CountingEmbedder()
    broken_new = replace(_doc(5), xml_bytes=b"<html")
    _upstream(monkeypatch, [_doc(0, "Body. Amended."), _doc(1), broken_new], embedder)

    report = run_sync(settings)

    assert report.changed_count == 1
    assert embedder.texts == 1


@pytest.mark.parametrize("error_type", [ParseError, RenderError])
@pytest.mark.parametrize("prior_kind", ["new", "same-hash", "changed-hash"])
def test_render_refusal_depends_on_upstream_hash_not_failure_type(
    tmp_path: Path, error_type: type[Exception], prior_kind: str
) -> None:
    """#804 refuses carried-forward stale XML; other render failures stay skippable."""
    settings = _corpus(tmp_path, 1)
    record = read_manifest(settings.lovverk_repo_path / "manifest.json").documents["lov-0"]
    prior = None if prior_kind == "new" else record
    xml_hash = "f" * 64 if prior_kind == "changed-hash" else record.xml_hash
    failure = error_type("source content cannot be rendered")

    def render() -> str:
        raise failure

    if prior_kind == "changed-hash":
        with pytest.raises(AttestationError, match="lov-0 changed upstream") as refused:
            rendered_or_refused(render, "lov-0", xml_hash, prior)
        assert refused.value.__cause__ is failure
        assert str(failure) in str(refused.value)
        assert str(refused.value) == (
            f"lov-0 changed upstream but cannot render ({failure}); its old rendering "
            "would be carried forward, which the counted-conformance attestation "
            "refuses, so the sync stops before any embedding is paid for"
        )
    else:
        assert rendered_or_refused(render, "lov-0", xml_hash, prior) is None


def test_unexpected_renderer_failure_is_not_silently_skipped() -> None:
    failure = RuntimeError("unexpected renderer failure")

    def render() -> str:
        raise failure

    with pytest.raises(RuntimeError) as raised:
        rendered_or_refused(render, "lov-0", "f" * 64, None)

    assert raised.value is failure


def test_renamed_unrenderable_document_with_unchanged_hash_is_skipped(tmp_path: Path) -> None:
    """A rename alone must not turn a render failure into upstream-change refusal."""
    settings = _corpus(tmp_path, 1)
    before = _state(settings.lovverk_repo_path)
    prior = read_manifest(settings.lovverk_repo_path / "manifest.json")
    record = prior.documents["lov-0"]
    doc = replace(_doc(0), xml_bytes=b"<html", xml_hash=record.xml_hash, slug="renamed-law")
    plan = _ChangePlan([], [], [doc.doc_id], {doc.doc_id: doc}, prior, _OBSERVED)

    _guard_change_reembed(settings, plan, allow=False)

    assert _state(settings.lovverk_repo_path) == before


def test_the_refusal_also_comes_before_the_paid_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With #803's ordering, a stale sidecar is not repaired by a run that is refused."""
    settings = _with_one_stale_sidecar(tmp_path)
    repo = settings.lovverk_repo_path
    before, manifest = _state(repo), _manifest_bytes(settings)
    embedder = _CountingEmbedder()
    broken = replace(_doc(1), xml_bytes=b"<html", xml_hash="f" * 64)
    docs = [_doc(0), broken] + [_doc(i) for i in range(2, 100)]
    _live_migrations(monkeypatch, docs, embedder)

    with pytest.raises(AttestationError, match="lov-1"):
        run_sync(settings)

    assert embedder.texts == 0
    assert _state(repo) == before
    assert _manifest_bytes(settings) == manifest


def test_changed_and_renamed_unrenderable_document_refuses_before_backfill_with_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#804 applies to changed XML even when rename classification takes precedence."""
    settings = _with_one_stale_sidecar(tmp_path)
    repo = settings.lovverk_repo_path
    before = _state(repo)
    published = {
        path.relative_to(repo): path.read_bytes()
        for path in repo.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    }
    embedder = _CountingEmbedder()
    broken = replace(_doc(1), xml_bytes=b"<html", xml_hash="f" * 64, slug="renamed-law")
    docs = [_doc(0), broken] + [_doc(i) for i in range(2, 100)]
    _live_migrations(monkeypatch, docs, embedder)

    with pytest.raises(AttestationError, match="lov-1 changed upstream"):
        run_sync(settings, allow_mass_reembed=True)

    assert embedder.texts == 0
    assert _state(repo) == before
    assert {
        path.relative_to(repo): path.read_bytes()
        for path in repo.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    } == published
