"""Observable guard diagnostics and workload selection for the listed mutants."""

import sys
from datetime import timedelta
from pathlib import Path

import pytest

from lovspor.errors import MassReembedError
from lovspor.storage.manifest import Manifest
from lovspor.sync.orchestrator import _ChangePlan, _docs_to_embed, _guard_change_reembed, _write_one
from lovspor.sync.reembed_guard import guard_mass_reembed
from tests.unit.test_embedding_input_staleness import _prior, _selection, _settings
from tests.unit.test_sync_change_reembed_guard import _OBSERVED, _doc


def test_repair_refusal_explains_scope_thresholds_and_remedy(tmp_path: Path) -> None:
    with pytest.raises(MassReembedError) as refused:
        guard_mass_reembed(_selection(30, 2_000_000), _prior(500), _settings(tmp_path), False)
    assert str(refused.value) == (
        "embedding-input repair scope exceeds the mass-re-embed guard: "
        "30 document(s) selected for re-embedding (6.0% of 500 current documents), "
        "~60,000,000 input tokens. Threshold exceeded: document fraction 6.0% > 2.0% "
        "and token workload 60,000,000 > 1,000,000. No provider call was made and no "
        "sidecar was written. If this repair is intended (annotation-less corpus, deliberate "
        "pipeline change), re-run with 'lovspor sync --allow-mass-reembed' or raise "
        "LOVSPOR_REEMBED_GUARD_MAX_FRACTION / LOVSPOR_REEMBED_GUARD_MAX_TOKENS."
    )


def test_change_refusal_explains_upstream_drift_and_remedy(tmp_path: Path) -> None:
    settings = _settings(tmp_path, reembed_guard_max_tokens=1)
    doc = _doc(0)
    plan = _ChangePlan([doc.doc_id], [], [], {doc.doc_id: doc}, _prior(0), _OBSERVED)
    with pytest.raises(MassReembedError) as refused:
        _guard_change_reembed(settings, plan, allow=False)
    message = str(refused.value)
    assert message.startswith("the change-driven re-embed exceeds the mass-re-embed guard: ")
    assert message.endswith(
        "So many changed documents usually means the upstream format or the "
        "renderer changed, not the law (#679: an ELI row in every header). Check the "
        "hashes before paying; if the re-embed is intended, re-run with "
        "'lovspor sync --allow-mass-reembed' or raise "
        "LOVSPOR_REEMBED_GUARD_MAX_FRACTION / LOVSPOR_REEMBED_GUARD_MAX_TOKENS."
    )


@pytest.mark.parametrize("current", [0, 1])
def test_override_reports_tiny_corpus_fraction_and_flushes_before_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, current: int
) -> None:
    """The override's flush=True promises visibility before the paid work starts."""
    output = tmp_path / "stderr"
    with output.open("w", encoding="utf-8", buffering=8192) as stream:
        monkeypatch.setattr(sys, "stderr", stream)
        guard_mass_reembed(_selection(1, 10), _prior(current), _settings(tmp_path), True)
        # Read through another descriptor while the writer is still open and buffered.
        assert output.read_text(encoding="utf-8") == (
            "mass-reembed override active: 1 document(s) selected for re-embedding "
            f"(100.0% of {current} current documents), ~10 input tokens\n"
        )


@pytest.mark.parametrize("changed_xml", [False, True])
def test_selection_skips_only_byte_identical_rerenders(tmp_path: Path, changed_xml: bool) -> None:
    """Attempt to distinguish the timestamp mutation through real rendered files."""
    settings = _settings(tmp_path)
    original = _doc(0)
    record, _ = _write_one(settings, original, _OBSERVED, None)
    doc = _doc(0, "Amended body.") if changed_xml else original
    prior = Manifest(generated_at=_OBSERVED, documents={doc.doc_id: record})
    plan = _ChangePlan(
        [], [doc.doc_id], [], {doc.doc_id: doc}, prior, _OBSERVED + timedelta(days=1)
    )
    selected = list(_docs_to_embed(settings, plan))
    assert [item.doc_id for item in selected] == ([doc.doc_id] if changed_xml else [])


@pytest.mark.parametrize("published", ["intact", "missing", "invalid-timestamp", "changed-body"])
def test_changed_selection_preserves_observation_before_noop_check(
    tmp_path: Path, published: str
) -> None:
    """Exercise timestamp fallback and differing render bytes without replacing code."""
    settings = _settings(tmp_path)
    doc = _doc(0)
    record, _ = _write_one(settings, doc, _OBSERVED, None)
    path = settings.lovverk_repo_path / record.markdown_path
    original = path.read_text(encoding="utf-8")
    if published == "missing":
        path.unlink()
    elif published == "invalid-timestamp":
        lines = original.splitlines(keepends=True)
        assert any(line.startswith("retrieved_at:") for line in lines)
        path.write_text(
            "".join(
                'retrieved_at: "invalid"\n' if line.startswith("retrieved_at:") else line
                for line in lines
            ),
            encoding="utf-8",
        )
    elif published == "changed-body":
        path.write_text(original + "\nOutdated rendered body.\n", encoding="utf-8")
    prior = Manifest(generated_at=_OBSERVED, documents={doc.doc_id: record})
    plan = _ChangePlan(
        [], [doc.doc_id], [], {doc.doc_id: doc}, prior, _OBSERVED + timedelta(days=1)
    )

    selected = list(_docs_to_embed(settings, plan))

    assert [item.doc_id for item in selected] == ([] if published == "intact" else [doc.doc_id])
    for item in selected:
        assert item.retrieved_at == _OBSERVED
    if published == "missing":
        assert not path.exists()
    elif published == "intact":
        assert path.read_text(encoding="utf-8") == original
