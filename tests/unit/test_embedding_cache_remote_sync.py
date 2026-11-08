"""The remote cache at the sync's one paid boundary (#802, design §6 and §8.1).

A fake provider pays per request, and a mocked R2 bucket (``pytest-httpx``)
stands in for Cloudflare. These tests ask what a sync would see:

* what it costs;
* which sidecar bytes it writes;
* when each paid unit becomes durable;
* what an operator sees when it fails.
"""

import traceback
from pathlib import Path

import httpx
import numpy as np
import pytest
from pytest_httpx import HTTPXMock

from lovspor.embeddings import cache_remote
from lovspor.embeddings.cache import CACHE_DIR_VARIABLE, CachingEmbedder, with_embedding_cache
from lovspor.embeddings.cache_remote import RemoteEntryStore
from lovspor.errors import EmbeddingCacheError, EmbeddingCacheStorageError
from tests.unit import r2_fakes
from tests.unit.test_embedding_cache_boundary import _Provider, _settings, _write

_REQUESTS = 4  # the fixture document: 7 inputs, two per request


class _Noisy(_Provider):
    """Same plan, different floats: what a retried or overlapping request can return."""

    def encode(self, texts: list[str]) -> np.ndarray:
        matrix = super().encode(texts)
        return np.roll(matrix, 1, axis=1)


class _Logged(_Provider):
    """Records each paid request in a log shared with the bucket."""

    def __init__(self, log: list[str], fail_after: int | None = None) -> None:
        super().__init__(fail_after=fail_after)
        self._log = log

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = super().encode(texts)
        self._log.append("paid")
        return vectors


@pytest.fixture
def remote_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Remote cache configured from the environment, as ``sync.yml`` would set it."""
    root = tmp_path / "runner-1"
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(root))
    for name, value in _remote_values().items():
        monkeypatch.setenv(name, value)
    return root


def _remote_values() -> dict[str, str]:
    return {
        cache_remote.REMOTE_VARIABLE: "r2",
        cache_remote.BUCKET_VARIABLE: r2_fakes.BUCKET,
        cache_remote.ACCOUNT_VARIABLE: r2_fakes.ACCOUNT_ID,
        cache_remote.KEY_ID_VARIABLE: r2_fakes.KEY_ID,
        cache_remote.SECRET_VARIABLE: r2_fakes.SECRET,
    }


@pytest.fixture
def bucket(httpx_mock: HTTPXMock) -> r2_fakes.FakeBucket:
    fake = r2_fakes.FakeBucket()
    httpx_mock.add_callback(fake, is_reusable=True)
    return fake


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_remote, "_BACKOFF_SECONDS", (0.0, 0.0))


def _runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """A fresh runner: a new, empty local cache directory; the same bucket."""
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(tmp_path / name))


def test_the_environment_wires_the_remote_store(remote_env: Path, tmp_path: Path) -> None:
    embedder = with_embedding_cache(_Provider(), _settings(tmp_path))
    assert isinstance(embedder, CachingEmbedder)
    assert isinstance(embedder.store, RemoteEntryStore)


def test_without_the_remote_variable_the_cache_is_local_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(tmp_path / "local"))
    for name in _remote_values():
        monkeypatch.delenv(name, raising=False)
    embedder = with_embedding_cache(_Provider(), _settings(tmp_path))
    assert isinstance(embedder, CachingEmbedder)
    assert not isinstance(embedder.store, RemoteEntryStore)


def test_cache_off_ignores_credentials_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off by default: credentials in the environment turn nothing on."""
    monkeypatch.delenv(CACHE_DIR_VARIABLE, raising=False)
    for name, value in _remote_values().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(cache_remote.REMOTE_VARIABLE)
    provider = _Provider()
    assert with_embedding_cache(provider, _settings(tmp_path)) is provider


def test_a_remote_cache_without_a_local_directory_is_refused(
    remote_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking for durability and silently getting none would hide unprotected spend."""
    monkeypatch.delenv(CACHE_DIR_VARIABLE)
    with pytest.raises(EmbeddingCacheError, match=CACHE_DIR_VARIABLE):
        with_embedding_cache(_Provider(), _settings(tmp_path))


@pytest.mark.parametrize(
    "missing",
    [
        cache_remote.BUCKET_VARIABLE,
        cache_remote.ACCOUNT_VARIABLE,
        cache_remote.KEY_ID_VARIABLE,
        cache_remote.SECRET_VARIABLE,
    ],
)
def test_a_partial_remote_configuration_is_refused_by_name(
    remote_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.delenv(missing)
    with pytest.raises(EmbeddingCacheError, match=missing) as refused:
        with_embedding_cache(_Provider(), _settings(tmp_path))
    assert r2_fakes.SECRET not in str(refused.value)
    assert r2_fakes.KEY_ID not in str(refused.value)


def test_an_unknown_remote_backend_is_refused(
    remote_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cache_remote.REMOTE_VARIABLE, "s3")
    with pytest.raises(EmbeddingCacheError, match="only 'r2'"):
        with_embedding_cache(_Provider(), _settings(tmp_path))


def test_a_cache_hit_from_the_bucket_costs_zero_on_a_fresh_runner(
    remote_env: Path,
    bucket: r2_fakes.FakeBucket,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    first = _Provider()
    reference = _write(settings, with_embedding_cache(first, settings))
    assert first.requests == _REQUESTS

    _runner(tmp_path, monkeypatch, "runner-2")
    second = _Provider()
    assert _write(settings, with_embedding_cache(second, settings)) == reference
    assert second.requests == 0


def test_the_remote_cached_sidecar_equals_an_uncached_one(
    remote_env: Path,
    bucket: r2_fakes.FakeBucket,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "uncached").mkdir()
    uncached_settings = _settings(tmp_path / "uncached")
    uncached = _write(uncached_settings, _Provider())
    settings = _settings(tmp_path)
    assert _write(settings, with_embedding_cache(_Provider(), settings)) == uncached


def test_each_paid_unit_is_in_the_bucket_before_the_next_request(
    remote_env: Path, bucket: r2_fakes.FakeBucket, tmp_path: Path
) -> None:
    log: list[str] = []
    real_answer = bucket.answer

    def logged(method: str, path: str, headers: object, body: bytes) -> tuple[int, bytes]:
        status, data = real_answer(method, path, headers, body)
        if method == "PUT" and status == 200:
            log.append("committed")
        return status, data

    bucket.answer = logged  # type: ignore[method-assign]
    settings = _settings(tmp_path)
    with pytest.raises(RuntimeError, match="job killed"):
        _write(settings, with_embedding_cache(_Logged(log, fail_after=2), settings))
    assert log == ["paid", "committed", "paid", "committed"]
    assert bucket.puts() == 2


def test_a_killed_runner_resumes_on_a_fresh_one_paying_only_the_misses(
    remote_env: Path,
    bucket: r2_fakes.FakeBucket,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(RuntimeError, match="job killed"):
        _write(settings, with_embedding_cache(_Provider(fail_after=2), settings))

    _runner(tmp_path, monkeypatch, "runner-2")
    resumed = _Provider()
    _write(settings, with_embedding_cache(resumed, settings))
    assert resumed.requests == _REQUESTS - 2


def test_two_writers_with_different_vectors_end_on_one_value(
    remote_env: Path,
    bucket: r2_fakes.FakeBucket,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writer B pays, loses every key to writer A, and writes A's sidecar bytes."""
    settings = _settings(tmp_path)
    writer_a = _write(settings, with_embedding_cache(_Provider(), settings))
    committed_by_a = dict(bucket.objects)
    bucket.objects.clear()

    def a_commits_first(key: str) -> None:
        bucket.objects.setdefault(key, committed_by_a[key])

    bucket.before_put = a_commits_first
    _runner(tmp_path, monkeypatch, "runner-b")
    noisy = _Noisy()
    writer_b = _write(settings, with_embedding_cache(noisy, settings))

    assert noisy.requests == _REQUESTS, "B paid, and its responses were discarded"
    assert writer_b == writer_a
    assert bucket.objects == committed_by_a, "nothing overwritten"
    local_b = {p.name: p.read_bytes() for p in (tmp_path / "runner-b").rglob("*.lsec")}
    assert sorted(local_b.values()) == sorted(committed_by_a.values())


def test_an_unwritable_bucket_stops_before_the_next_paid_request(
    remote_env: Path, bucket: r2_fakes.FakeBucket, tmp_path: Path
) -> None:
    bucket.fail("PUT", 503, 503, 503)
    settings = _settings(tmp_path)
    provider = _Provider()
    with pytest.raises(EmbeddingCacheStorageError):
        _write(settings, with_embedding_cache(provider, settings))
    assert provider.requests == 1
    assert not list((tmp_path / "runner-1").rglob("*.lsec")), "nothing stored locally"


def test_an_ambiguous_put_never_leads_to_paying_again(
    remote_env: Path, bucket: r2_fakes.FakeBucket, tmp_path: Path
) -> None:
    bucket.fail("PUT", r2_fakes.COMMIT_THEN_TIMEOUT)
    settings = _settings(tmp_path)
    provider = _Provider()
    _write(settings, with_embedding_cache(provider, settings))
    assert provider.requests == _REQUESTS


def test_no_secret_reaches_errors_logs_output_or_files(
    remote_env: Path,
    bucket: r2_fakes.FakeBucket,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level("DEBUG")
    bucket.fail("PUT", 403)
    settings = _settings(tmp_path)
    with pytest.raises(EmbeddingCacheStorageError) as refused:
        _write(settings, with_embedding_cache(_Provider(), settings))
    shown = "".join(traceback.format_exception(refused.value)) + caplog.text
    captured = capfd.readouterr()
    shown += captured.out + captured.err
    files = b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    for secret in (r2_fakes.SECRET, r2_fakes.KEY_ID, r2_fakes.ACCOUNT_ID):
        assert secret not in shown
        assert secret.encode() not in files


def test_the_remote_client_is_built_with_a_timeout(remote_env: Path, tmp_path: Path) -> None:
    embedder = with_embedding_cache(_Provider(), _settings(tmp_path))
    assert isinstance(embedder, CachingEmbedder)
    store = embedder.store
    assert isinstance(store, RemoteEntryStore)
    timeout = store.client.http.timeout
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read is not None
