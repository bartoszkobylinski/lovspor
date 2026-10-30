"""The registry artifact behind the connect guide's dated claims (#648)."""

import hashlib
from datetime import date
from pathlib import Path

import pytest

from lovspor.site import routes
from lovspor.site.build import _registry_bytes
from lovspor.site.errors import SiteBuildError
from lovspor.site.facts import FactSource
from lovspor.site.sources import REGISTRY_ARTIFACT, fact_registry
from tests.unit.test_site_sources import _artifacts


def test_absent_registry_omits_both_walk_facts_and_artifact() -> None:
    artifacts = _artifacts()

    assert REGISTRY_ARTIFACT not in dict(artifacts.artifact_hashes())
    assert not any(
        source.id.startswith("code.connect.") for source in fact_registry(artifacts).sources
    )


@pytest.mark.parametrize("payload", [b"", b"registry\n", b"registry\r\n", "dato: blå\n".encode()])
def test_registry_hash_covers_exact_bytes_and_preserves_other_artifacts(payload: bytes) -> None:
    original = _artifacts()
    artifacts = original.model_copy(update={"registry_bytes": payload})

    assert artifacts.artifact_hashes() == tuple(
        sorted(
            (*original.artifact_hashes(), (REGISTRY_ARTIFACT, hashlib.sha256(payload).hexdigest()))
        )
    )
    assert artifacts.artifact_hashes() == artifacts.artifact_hashes()


def test_registry_adds_each_dated_code_fact_without_changing_existing_facts() -> None:
    original = _artifacts()
    artifacts = original.model_copy(update={"registry_bytes": b"registry"})
    sources = fact_registry(artifacts).sources

    assert tuple(source for source in sources if source.id.startswith("code.connect.")) == (
        *(
            FactSource(
                id=f"code.connect.{slug}.walked_on",
                kind="code",
                artifact=REGISTRY_ARTIFACT,
                field=f"CLIENT_REGISTRY.{slug}.walked_on",
                value="2026-09-30",
            )
            for slug in ("claude", "chatgpt")
        ),
        FactSource(
            id="code.connect.claude-code.walked_on",
            kind="code",
            artifact=REGISTRY_ARTIFACT,
            field="CLAUDE_CODE_HOSTED_WALKED_ON",
            value="2026-10-30",
        ),
        FactSource(
            id="code.connect.vscode.walked_on",
            kind="code",
            artifact=REGISTRY_ARTIFACT,
            field="VSCODE_HOSTED_WALKED_ON",
            value="2026-10-30",
        ),
    )
    assert tuple(source for source in sources if not source.id.startswith("code.connect.")) == (
        fact_registry(original).sources
    )


@pytest.mark.parametrize("walked_on", [date(2024, 2, 29), date(2027, 1, 2)])
def test_claude_code_fact_follows_its_own_walk_date(
    monkeypatch: pytest.MonkeyPatch, walked_on: date
) -> None:
    """#729 binds this section's date to its constant, outside CLIENT_REGISTRY."""
    artifacts = _artifacts().model_copy(update={"registry_bytes": b"registry"})
    original = fact_registry(artifacts).sources
    monkeypatch.setattr(routes, "CLAUDE_CODE_HOSTED_WALKED_ON", walked_on)

    sources = fact_registry(artifacts).sources
    fact_id = "code.connect.claude-code.walked_on"

    assert tuple(source for source in sources if source.id == fact_id) == (
        FactSource(
            id="code.connect.claude-code.walked_on",
            kind="code",
            artifact=REGISTRY_ARTIFACT,
            field="CLAUDE_CODE_HOSTED_WALKED_ON",
            value=walked_on.isoformat(),
        ),
    )
    assert tuple(source for source in sources if source.id != fact_id) == (
        tuple(source for source in original if source.id != fact_id)
    )


def test_registry_bytes_are_read_from_the_selected_checkout(tmp_path: Path) -> None:
    registry = tmp_path / REGISTRY_ARTIFACT
    registry.parent.mkdir(parents=True)
    payload = b"# checkout-specific registry\r\n# \xff\n"
    registry.write_bytes(payload)

    assert _registry_bytes(tmp_path) == payload


def test_missing_checkout_registry_is_a_useful_build_error(tmp_path: Path) -> None:
    with pytest.raises(
        SiteBuildError, match=f"the checkout has no client registry {REGISTRY_ARTIFACT}"
    ) as caught:
        _registry_bytes(tmp_path)

    assert isinstance(caught.value.__cause__, FileNotFoundError)
    assert not (tmp_path / REGISTRY_ARTIFACT).exists()
