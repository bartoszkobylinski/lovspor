"""#793: read-only commands describe --cache as an existing capture; a scope-less capture says why.

`build` and `release-candidate` only read a capture, so their help must not ask
for "a new, empty directory" (that is `capture`'s write-once cache). A capture
from before scopes were recorded cannot be built through the CLI: the refusal
names the cause and the supported remedy, a recapture, instead of
`not a list of Lovdata law ids: empty`. No scope is ever inferred.
"""

import json
from pathlib import Path

import pytest
import typer
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.cache import MANIFEST, load_manifest
from lovspor.crosswalk.canonical import parse_records
from lovspor.crosswalk.commands import crosswalk_app
from lovspor.crosswalk.errors import CrosswalkError
from tests.unit.crosswalk_support import COMMIT, PRODUCER, SCENARIO_LAWS, make_capture

NO_SCOPE = (
    "this capture records no scope (it predates recorded scopes): recapture it with "
    "`lovspor ops crosswalk capture --laws FILE --lovverk-commit SHA`"
)


def _cache_help(command: str) -> str:
    group = typer.main.get_group(crosswalk_app)
    params = group.commands[command].params
    return str(next(p for p in params if p.name == "cache").help)  # type: ignore[attr-defined]


@pytest.mark.parametrize("command", ["build", "release-candidate"])
def test_read_only_commands_describe_an_existing_capture(command: str) -> None:
    assert _cache_help(command) == "An existing capture directory; only read."


def test_capture_still_asks_for_a_new_empty_directory() -> None:
    assert _cache_help("capture") == (
        "A new, empty directory outside every Git checkout and Lovverk."
    )


@pytest.fixture
def scopeless(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    cache = make_capture(tmp_path, httpx_mock)
    manifest = load_manifest(cache)
    del manifest["scope"]
    (cache / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return cache


def test_a_scopeless_capture_is_refused_with_its_remedy(scopeless: Path) -> None:
    with pytest.raises(CrosswalkError) as caught:
        build(scopeless, BuildOptions(producer_commit=PRODUCER))
    assert str(caught.value) == NO_SCOPE


def test_the_cli_names_the_remedy_and_writes_nothing(scopeless: Path, tmp_path: Path) -> None:
    output = tmp_path / "out.jsonl"
    args = ["ops", "crosswalk", "build", "--cache", str(scopeless), "--output", str(output)]
    result = CliRunner().invoke(app, [*args, "--producer-commit", PRODUCER])
    assert result.exit_code == 1
    assert f"refused: {NO_SCOPE}" in " ".join(result.output.split())
    assert not output.exists()


def test_an_explicit_library_scope_still_builds_a_scopeless_capture(scopeless: Path) -> None:
    """The library path the study used stays: the caller states the scope, nothing is guessed."""
    options = BuildOptions(PRODUCER, lovverk_commit=COMMIT, law_ids=tuple(SCENARIO_LAWS))
    assert build(scopeless, options).count(b"\n") == len(SCENARIO_LAWS)


@pytest.mark.parametrize("law_ids", [None, ()])
def test_a_commit_alone_cannot_supply_the_missing_scope(
    scopeless: Path, law_ids: tuple[str, ...] | None
) -> None:
    options = BuildOptions(PRODUCER, lovverk_commit=COMMIT, law_ids=law_ids)
    with pytest.raises(CrosswalkError) as caught:
        build(scopeless, options)
    assert str(caught.value) == NO_SCOPE


def test_explicit_laws_still_require_a_lovverk_commit(scopeless: Path) -> None:
    options = BuildOptions(PRODUCER, law_ids=tuple(SCENARIO_LAWS))
    with pytest.raises(CrosswalkError, match="lovverk_commit must be a 40-hex commit id"):
        build(scopeless, options)


def test_explicit_scope_does_not_expand_to_the_cached_laws(scopeless: Path) -> None:
    """The documented library fallback uses only the caller's scope, without cache writes."""
    before = {p.relative_to(scopeless): p.read_bytes() for p in scopeless.rglob("*") if p.is_file()}
    law = "LOV-2022-06-17-63"
    options = BuildOptions(PRODUCER, lovverk_commit=COMMIT, law_ids=(law,))

    first = build(scopeless, options)

    assert [r["amending_lov_id"] for r in parse_records(first)] == [law]
    assert build(scopeless, options) == first
    after = {p.relative_to(scopeless): p.read_bytes() for p in scopeless.rglob("*") if p.is_file()}
    assert after == before
