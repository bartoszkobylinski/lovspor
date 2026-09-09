"""Fixtures shared by the release-envelope tests (ADR-0014 Decision 6).

A *world* is a throwaway lovspor-shaped checkout and a one-document
lovverk corpus; an *observer* is the injected probe, answering with a
valid document for whatever checkout it is asked about, at a fixed
instant. Real trees are built from them into a ``tmp_path`` releases
root; nothing here reads the developer's own repository.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from lovspor.release.build import BuildOutcome, BuildRequest, build_release
from lovspor.release.envelope import fragment_release_id, fragment_text, read_fragment
from lovspor.site.capabilities import CapabilityDocument, Checkout
from tests.unit.site_fixtures import (
    OBSERVED_AT,
    available_observation,
    document_for,
    throwaway_checkout,
    throwaway_corpus,
    with_surface,
)

Observer = Callable[[Checkout], CapabilityDocument]


class World(NamedTuple):
    checkout: Path
    lovspor_commit: str
    corpus: Path
    corpus_commit: str


def make_world(root: Path) -> World:
    checkout, lovspor_commit = throwaway_checkout(root / "lovspor")
    corpus, corpus_commit = throwaway_corpus(root / "lovverk")
    return World(checkout, lovspor_commit, corpus, corpus_commit)


def observation_for(checkout: Checkout, observed_at: str = OBSERVED_AT) -> dict[str, Any]:
    """Every clause of ``available`` met for exactly this checkout."""
    observation = with_surface(available_observation(), checkout.expected_tool_surface_sha256)
    identity = checkout.expected_runtime_identity.model_dump()
    observation["process"]["runtime_identity"] = identity
    for record in ("process", "transport"):
        observation[record]["observed_at"] = observed_at
    return observation


def observer(observed_at: str = OBSERVED_AT, **process: Any) -> Observer:
    """An observer answering ``available`` at ``observed_at``, ``process`` fields overridden."""

    def observe(checkout: Checkout) -> CapabilityDocument:
        observation = observation_for(checkout, observed_at)
        observation["process"].update(process)
        return CapabilityDocument.model_validate(
            document_for(observation, checkout.model_dump(mode="json"))
        )

    return observe


def request_for(world: World, releases: Path, ref: str = "HEAD") -> BuildRequest:
    return BuildRequest(
        releases=releases, checkout=world.checkout, corpus=world.corpus, corpus_ref=ref
    )


def build(
    world: World,
    releases: Path,
    live: str | None = None,
    observe: Observer | None = None,
) -> BuildOutcome:
    return build_release(request_for(world, releases), observe or observer(), live)


def files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def fragment_for(releases: Path, content_id: str) -> str:
    """The fragment a release under ``releases`` carries, as the build writes it."""
    return fragment_text(releases.resolve() / content_id, content_id)


def release_id_of(release: Path) -> str:
    found = fragment_release_id(read_fragment(release))
    assert found is not None
    return found
