"""The #790 estimate includes every provider input of a multi-chunk law."""

from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.inputs import build_embedding_inputs
from lovspor.embeddings.model import count_input_tokens
from lovspor.errors import MassReembedError
from lovspor.sync import orchestrator
from tests.unit.test_sync_change_reembed_guard import (
    _OBSERVED,
    _corpus,
    _CountingEmbedder,
    _doc,
    _state,
    _upstream,
)


@pytest.mark.parametrize("budget_offset", [-1, 0])
def test_change_guard_counts_all_chunks_as_one_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    budget_offset: int,
) -> None:
    """docs/operations.md promises the tokenizer count of inputs actually sent."""
    settings = _corpus(tmp_path, 1)
    doc = _doc(0, "Bestemmelsen gjelder. " * 4000)
    rendered = orchestrator.render_full_document(
        doc.xml_bytes, orchestrator._frontmatter_context(doc, _OBSERVED)
    )
    inputs = build_embedding_inputs(rendered)
    assert len(inputs) > 1
    assert len({item.section_id for item in inputs}) == 1
    tokens = sum(count_input_tokens(item.text) for item in inputs)
    assert tokens > max(count_input_tokens(item.text) for item in inputs)
    settings = settings.model_copy(update={"reembed_guard_max_tokens": tokens + budget_offset})
    repo = settings.lovverk_repo_path
    before = _state(repo)
    manifest_before = (repo / "manifest.json").read_bytes()
    payloads: list[str] = []

    class RecordingEmbedder(_CountingEmbedder):
        def encode(self, texts: list[str]) -> np.ndarray:
            payloads.extend(texts)
            return super().encode(texts)

    embedder = RecordingEmbedder()
    _upstream(monkeypatch, [doc], embedder)

    if budget_offset == -1:
        with pytest.raises(MassReembedError) as refused:
            orchestrator.run_sync(settings)
        assert "1 changed document(s)" in str(refused.value)
        assert f"token workload {tokens:,} > {tokens - 1:,}" in str(refused.value)
        assert payloads == []
        assert embedder.texts == 0
        assert _state(repo) == before
        assert (repo / "manifest.json").read_bytes() == manifest_before
    else:
        report = orchestrator.run_sync(settings)
        assert report.changed_count == 1
        assert payloads == [item.text for item in inputs]
        assert embedder.texts == len(inputs)
        assert sum(count_input_tokens(text) for text in payloads) == tokens
        assert "override active" not in capsys.readouterr().err
