"""run_sync against a real corpus, with nothing of the orchestrator replaced (issue #427).

PR #422's mutation remediation killed these ``run_sync`` survivors by
monkeypatching the orchestrator's own functions and asserting on the arguments
they received. That pins a call shape, not behaviour, and two of those mutants
were equivalent — only the mock could tell them apart; they are registered in
``mutation-equivalents.toml`` instead. Here every orchestrator function is the
real one: the corpus is a temporary git repo, each upstream document is the
``synthetic-flat-law.xml`` fixture served in a real tarball, and the only things
replaced are the two HTTP services, through pytest-httpx — Lovdata's
``publicData`` API and the OpenAI embeddings endpoint.
"""

import io
import json
import re
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.embeddings.provider import EmbeddingConfig
from lovspor.errors import MassReembedError
from lovspor.settings import Settings
from lovspor.sources.lovdata import DEFAULT_BASE_URL
from lovspor.storage.manifest import read_manifest
from lovspor.sync.orchestrator import _rerender_upstream, _UpstreamDoc, run_sync
from tests.unit.test_sync_orchestrator import _git, _settings

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_FIXTURE_LAW = (_FIXTURES / "synthetic-flat-law.xml").read_bytes()
# The flat law has no sections, so it embeds to an empty sidecar without ever
# calling the provider. The regulation fixture has two sections but no header
# to index it by, so its sections are carried into the law's body and the
# result is served as a regulation — a document the embedder has work for.
_SECTIONS = (
    (_FIXTURES / "synthetic-flat-forskrift-2s.xml")
    .read_bytes()
    .partition(b"</h1>")[2]
    .partition(b"</main>")[0]
)
_REGULATION = _FIXTURE_LAW.replace(b"</main>", _SECTIONS + b"</main>")
_REGULATION_ID = "sf-18000101-0001"
_CATALOGUE: list[dict[str, Any]] = json.loads(
    (_FIXTURES / "lovdata_list_response.json").read_text(encoding="utf-8"),
)
_OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
_EMBEDDING_DIM = 8


def _law(name: str, clause: str = "Papirdrage") -> bytes:
    """The fixture law retitled to ``name``; ``clause`` varies its body text."""
    return (
        _FIXTURE_LAW.replace(b"Drage-Flyging", name.encode())
        .replace(b"Drage-flyging", name.encode())
        .replace(b"Papirdrage", clause.encode())
    )


def _tarball(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        for doc_id, content in members.items():
            info = tarfile.TarInfo(name=f"nl/{doc_id}.xml")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _serve(httpx_mock: HTTPXMock, docs: dict[str, bytes], drift: bool = False) -> None:
    """Lovdata's ``/list`` and both tarball downloads, for one sync.

    An ``sf-`` document goes in the regulations tarball, any other in the
    laws one. ``drift`` adds a field the archive schema does not model, as
    Lovdata would if it extended the ``/list`` response.
    """
    regulations = {doc_id: xml for doc_id, xml in docs.items() if doc_id.startswith("sf-")}
    laws = {doc_id: xml for doc_id, xml in docs.items() if doc_id not in regulations}
    tarballs = {
        "gjeldende-lover.tar.bz2": _tarball(laws),
        "gjeldende-sentrale-forskrifter.tar.bz2": _tarball(regulations),
    }
    extra = {"mimeType": "application/x-bzip2"} if drift else {}
    listing = [
        {**entry, **extra, "sizeBytes": str(len(tarballs.get(entry["filename"], b"")))}
        for entry in _CATALOGUE
    ]
    httpx_mock.add_response(url=f"{DEFAULT_BASE_URL}/list", json=listing)
    for filename, content in tarballs.items():
        httpx_mock.add_response(url=f"{DEFAULT_BASE_URL}/get/{filename}", content=content)


def _serve_embeddings(httpx_mock: HTTPXMock) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        data = [
            {"index": index, "embedding": [1.0] * _EMBEDDING_DIM} for index in range(len(texts))
        ]
        return httpx.Response(200, json={"data": data})

    httpx_mock.add_callback(respond, method="POST", url=_OPENAI_EMBEDDINGS_URL, is_reusable=True)


def _keyed(settings: Settings, **overrides: object) -> Settings:
    """``settings`` with an embedding credential, so the sync embeds."""
    embedding = EmbeddingConfig(dimension=_EMBEDDING_DIM, api_key="sk-test")
    return settings.model_copy(update={"embedding": embedding, **overrides})


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _manifest(settings: Settings) -> dict[str, Any]:
    path = settings.lovverk_repo_path / "manifest.json"
    documents: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))["documents"]
    return documents


def _markdown(settings: Settings, doc_id: str) -> Path:
    return settings.lovverk_repo_path / _manifest(settings)[doc_id]["markdown_path"]


def _last_seen(settings: Settings, doc_id: str) -> datetime:
    return datetime.fromisoformat(_manifest(settings)[doc_id]["last_seen"])


def _retrieved_at(markdown: str) -> datetime:
    match = re.search(r'^retrieved_at: "([^"]+)"$', markdown, re.MULTILINE)
    assert match is not None
    return datetime.fromisoformat(match.group(1))


def test_archive_drift_is_reported_by_a_sync_that_writes(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    settings = _settings(tmp_path)
    _serve(httpx_mock, {"lov-17990401-000": _law("Drageloven")}, drift=True)

    report = run_sync(settings)

    assert report.new_count == 1
    assert report.unknown_archive_fields == ("mimeType",)


def test_archive_drift_is_reported_by_a_sync_with_nothing_to_do(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    settings = _settings(tmp_path)
    laws = {"lov-17990401-000": _law("Drageloven")}
    _serve(httpx_mock, laws)
    run_sync(settings)
    _serve(httpx_mock, laws, drift=True)

    report = run_sync(settings)

    assert (report.new_count, report.changed_count, report.unchanged_count) == (0, 0, 1)
    assert report.unknown_archive_fields == ("mimeType",)


def test_a_first_keyed_sync_does_not_mass_reembed_without_the_override(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    """A corpus published keylessly has no sidecars; the first keyed sync
    selects all of it for backfill. Over the token budget the guard must stop
    that before any provider call — unless the operator passed the override,
    which ``run_sync`` must never assume."""
    settings = _settings(tmp_path)
    _serve(httpx_mock, {_REGULATION_ID: _REGULATION})
    run_sync(settings)
    _serve(httpx_mock, {_REGULATION_ID: _REGULATION})

    with pytest.raises(MassReembedError, match="No provider call was made"):
        run_sync(_keyed(settings, reembed_guard_max_tokens=1))

    assert httpx_mock.get_requests(url=_OPENAI_EMBEDDINGS_URL) == []


def test_a_new_document_lands_with_its_sidecar_in_its_own_commit(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    settings = _keyed(_settings(tmp_path))
    _serve(httpx_mock, {_REGULATION_ID: _REGULATION})
    _serve_embeddings(httpx_mock)

    run_sync(settings)

    repo = settings.lovverk_repo_path
    slug = _manifest(settings)[_REGULATION_ID]["slug"]
    sidecar = _markdown(settings, _REGULATION_ID).parent / "embeddings" / f"{slug}.bin"
    assert f"add(forskrift): {slug}" in _git_out(repo, "log", "--format=%s").splitlines()
    tracked = _git_out(repo, "ls-tree", "-r", "--name-only", "HEAD").split()
    assert sidecar.relative_to(repo).as_posix() in tracked
    assert _git_out(repo, "status", "--porcelain") == ""


def test_a_changed_document_is_stamped_with_the_time_of_this_sync(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    settings = _settings(tmp_path)
    _serve(httpx_mock, {"lov-17990401-000": _law("Drageloven")})
    run_sync(settings)
    first_seen = _last_seen(settings, "lov-17990401-000")
    _serve(httpx_mock, {"lov-17990401-000": _law("Drageloven", clause="Silkedrage")})

    report = run_sync(settings)

    assert report.changed_count == 1
    markdown = _markdown(settings, "lov-17990401-000").read_text(encoding="utf-8")
    assert "Silkedrage" in markdown
    assert _last_seen(settings, "lov-17990401-000") > first_seen
    assert _retrieved_at(markdown) == _last_seen(settings, "lov-17990401-000")


def test_a_forced_rerender_keeps_going_past_a_document_it_leaves_alone(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    """The first document re-renders byte-identical and is skipped; the second
    was hand-edited in the corpus and must still be restored."""
    settings = _settings(tmp_path)
    laws = {"lov-17990401-000": _law("Aaloven"), "lov-17990401-001": _law("Beloven")}
    _serve(httpx_mock, laws)
    run_sync(settings)
    edited = _markdown(settings, "lov-17990401-001")
    published = edited.read_text(encoding="utf-8")
    edited.write_text(published + "\nhand edit\n", encoding="utf-8")
    _git(settings.lovverk_repo_path, "commit", "-qam", "hand edit")
    _serve(httpx_mock, laws)

    report = run_sync(settings, force_rerender=True)

    assert (report.unchanged_count, report.rerendered_count) == (1, 1)
    assert edited.read_text(encoding="utf-8") == published


def _strip_slug(settings: Settings, doc_id: str) -> None:
    """Rewrite ``doc_id``'s record as a pre-slug (Sprint 3) manifest held it."""
    path = settings.lovverk_repo_path / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["documents"][doc_id]["slug"] = None
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _git(settings.lovverk_repo_path, "commit", "-qam", "legacy record")


def _plant_bystander_sidecars(settings: Settings) -> set[str]:
    """Committed sidecars that belong to no document in the sync.

    ``XXXX`` is the name mutation testing substitutes for the empty slug a
    slug-less record deletes under; it stands in for any bystander file here.
    """
    embeddings = settings.lovverk_repo_path / "lover" / "embeddings"
    embeddings.mkdir(parents=True, exist_ok=True)
    names = {"XXXX.bin", "bystander.bin"}
    for name in names:
        (embeddings / name).write_bytes(b"someone else's vectors")
    _git(settings.lovverk_repo_path, "add", "-A")
    _git(settings.lovverk_repo_path, "commit", "-qm", "bystanders")
    return {f"lover/embeddings/{name}" for name in names}


@pytest.mark.parametrize(
    ("served", "slug"),
    [
        (_law("Drageloven"), "forbud-mot-drageloven"),
        (_law("Ormeloven", clause="Silkedrage"), "forbud-mot-ormeloven"),
    ],
    ids=["renamed", "changed"],
)
def test_a_slugless_legacy_record_taking_a_slug_deletes_no_other_sidecar(
    served: bytes, slug: str, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    """A record with no slug has no sidecar of its own to delete. Giving it
    one — a rename of unchanged content, or a content change that moves the
    file — must leave every embedding sidecar in the corpus where it was."""
    settings = _settings(tmp_path)
    _serve(httpx_mock, {"lov-17990401-000": _law("Drageloven")})
    run_sync(settings)
    _strip_slug(settings, "lov-17990401-000")
    bystanders = _plant_bystander_sidecars(settings)
    _serve(httpx_mock, {"lov-17990401-000": served})

    run_sync(settings)

    tracked = _git_out(settings.lovverk_repo_path, "ls-tree", "-r", "--name-only", "HEAD")
    assert bystanders <= set(tracked.split())
    assert _manifest(settings)["lov-17990401-000"]["slug"] == slug


@pytest.mark.parametrize("published", [True, False], ids=["file", "manifest-only"])
def test_assumption_a_hash_matched_rerender_always_carries_an_observation(
    published: bool, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    """Pins the argument registering the ``_rerender_is_noop(..., None, ...)``
    mutant in ``run_sync`` as equivalent: ``_rerender_is_noop`` reads ``now``
    only as the fallback for a missing ``retrieved_at``, and past its hash check
    the document came from ``_rerender_upstream``, which always sets one — from
    the published file, or from the manifest when the file is gone."""
    settings = _settings(tmp_path)
    _serve(httpx_mock, {"lov-17990401-000": _law("Drageloven")})
    run_sync(settings)
    prior = read_manifest(settings.lovverk_repo_path / "manifest.json").documents[
        "lov-17990401-000"
    ]
    if not published:
        _markdown(settings, "lov-17990401-000").unlink()
    fetched = _UpstreamDoc("lov-17990401-000", "gjeldende-lover", b"", prior.xml_hash, "x", "", ())

    carried = _rerender_upstream(settings, fetched, prior).retrieved_at

    assert carried is not None
    assert carried == prior.last_seen
