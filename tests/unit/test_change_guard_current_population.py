"""The extracted guard sizes blast radius against current documents only."""

from pathlib import Path

import pytest

from lovspor.errors import MassReembedError
from lovspor.sync.orchestrator import _ChangePlan, _guard_change_reembed
from tests.unit.test_embedding_input_staleness import _prior, _settings
from tests.unit.test_sync_change_reembed_guard import _OBSERVED, _doc


@pytest.mark.parametrize("current_count", [99, 100])
@pytest.mark.parametrize("removed_count", [0, 100])
def test_change_fraction_floor_and_denominator_use_only_current_documents(
    tmp_path: Path, current_count: int, removed_count: int
) -> None:
    """The 100-document floor excludes tombstones, as does the fraction itself.

    Three new documents exceed the default 2% budget at 100 current records.
    Adding tombstones must neither activate the floor at 99 nor dilute the
    fraction at 100. This enforces the guard's documented current-doc contract.
    """
    prior = _prior(current_count)
    template = prior.documents["doc-0"]
    prior.documents.update(
        {
            f"removed-{index}": template.model_copy(update={"status": "removed"})
            for index in range(removed_count)
        }
    )
    docs = [_doc(index) for index in range(3)]
    plan = _ChangePlan(
        [doc.doc_id for doc in docs],
        [],
        [],
        {doc.doc_id: doc for doc in docs},
        prior,
        _OBSERVED,
    )
    settings = _settings(tmp_path)

    if current_count == 100:
        with pytest.raises(MassReembedError) as refused:
            _guard_change_reembed(settings, plan, allow=False)
        assert "3 changed document(s)" in str(refused.value)
        assert "3.0% of 100 current documents" in str(refused.value)
        assert "document fraction 3.0% > 2.0%" in str(refused.value)
    else:
        _guard_change_reembed(settings, plan, allow=False)

    assert not settings.lovverk_repo_path.exists()
