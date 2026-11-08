"""A vanished cache root stops corpus writes and further provider spend (#825)."""

from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import with_embedding_cache
from lovspor.errors import EmbeddingCacheStorageError
from tests.unit.test_embedding_cache_boundary import (
    _Provider,
    _settings,
    _write,
    cache_dir,
)

__all__ = ["cache_dir"]


@pytest.mark.parametrize("remove_during_response", [False, True])
def test_vanished_root_stops_payment_and_sidecar_publication(
    cache_dir: Path, tmp_path: Path, remove_during_response: bool
) -> None:
    """#825 and cache.py: a lost store is an error, never a payable miss.

    If the root disappears during a paid request, the storage-error contract
    requires stopping before the next request, without publishing a sidecar.
    """

    class RemovingProvider(_Provider):
        def encode(self, texts: list[str]) -> np.ndarray:
            vectors = super().encode(texts)
            if remove_during_response and self.requests == 1:
                cache_dir.rmdir()
            return vectors

    settings = _settings(tmp_path)
    provider = RemovingProvider()
    cached = with_embedding_cache(provider, settings)
    if not remove_during_response:
        cache_dir.rmdir()

    with pytest.raises(EmbeddingCacheStorageError, match="root is gone"):
        _write(settings, cached)

    assert provider.requests == int(remove_during_response)
    assert provider.texts == (2 if remove_during_response else 0)
    assert not cache_dir.exists()
    assert not list(settings.lovverk_repo_path.rglob("*.bin"))
