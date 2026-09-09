"""``build_release``: the seven-step order, the same-id cases, the short-circuit.

Every test builds real envelopes from a throwaway checkout and corpus into
a ``tmp_path`` releases root. The ADR's crash table is exercised by a
checkpoint that stops the procedure at a named step, in place of a kill.
"""

import json
import re
from pathlib import Path

import pytest

from lovspor.release.build import STEPS, BuildRequest, build_release, candidate
from lovspor.release.check import check_envelope
from lovspor.release.envelope import (
    BUILD_PREFIX,
    FRAGMENT_NAME,
    RECORD_NAME,
    fragment_paths,
    fragment_release_id,
    is_complete,
    is_release_id,
    read_fragment,
    read_record,
)
from lovspor.release.errors import (
    IncompleteEnvelopeError,
    ReleaseConflictError,
    ReleaseError,
)
from lovspor.site.errors import SiteBuildError
from lovspor.site.fingerprint import release_content_id
from tests.unit.release_fixtures import (
    World,
    build,
    files,
    fragment_for,
    make_world,
    observer,
    request_for,
)
from tests.unit.site_fixtures import commit_all, run_git

LATER = "2026-01-02T00:00:00Z"
_WALL_CLOCK = re.compile(r"\d{8}T\d{6}Z")


class Killed(Exception):  # noqa: N818 — a simulated process death, not a lovspor error
    """The process died right after the named step."""


def _kill_at(step: str):  # type: ignore[no-untyped-def]
    def checkpoint(reached: str) -> None:
        if reached == step:
            raise Killed(step)

    return checkpoint


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return make_world(tmp_path_factory.mktemp("world"))


@pytest.fixture
def releases(tmp_path: Path) -> Path:
    return tmp_path / "releases"


def _build_dirs(releases: Path) -> list[Path]:
    return sorted(path for path in releases.iterdir() if path.name.startswith(BUILD_PREFIX))


def _id_dirs(releases: Path) -> list[Path]:
    return sorted(path for path in releases.iterdir() if is_release_id(path.name))


def _facts(release: Path) -> dict[str, object]:
    payload: dict[str, object] = json.loads((release / "site" / "site-facts.json").read_bytes())
    return payload


class TestAFreshBuild:
    def test_finalizes_a_complete_envelope_under_its_content_id(
        self, world: World, releases: Path
    ) -> None:
        outcome = build(world, releases)

        release = releases / outcome.release_content_id
        assert not outcome.already_live and not outcome.reused
        assert is_complete(release)
        assert _build_dirs(releases) == []
        assert release_content_id(release / "corpus", release / "site") == release.name
        assert _facts(release)["release_content_id"] == release.name
        record = read_record(release)
        assert record.release_content_id == release.name
        assert record.release_key.corpus_commit == world.corpus_commit
        assert record.release_key.lovspor_commit == world.lovspor_commit
        assert record.corpus.corpus_commit == world.corpus_commit
        assert record.corpus.documents == 1
        assert record.observed_at == "2026-01-01T00:00:00Z"
        assert record.observer == "release-probe"
        assert read_fragment(release) == fragment_for(releases, release.name)
        assert check_envelope(release).release_content_id == release.name

    def test_the_fragment_names_the_final_directory_and_no_wall_clock(
        self, world: World, releases: Path
    ) -> None:
        outcome = build(world, releases)
        release = releases / outcome.release_content_id

        fragment = read_fragment(release)
        assert fragment_release_id(fragment) == release.name
        for path in fragment_paths(fragment):
            assert path.startswith(release.resolve().as_posix() + "/")
        for name in (RECORD_NAME, FRAGMENT_NAME, "site/site-facts.json"):
            assert not _WALL_CLOCK.search((release / name).read_text(encoding="utf-8"))
        assert not _WALL_CLOCK.search(release.name)

    def test_the_site_describes_the_corpus_beside_it(self, world: World, releases: Path) -> None:
        outcome = build(world, releases)
        release = releases / outcome.release_content_id

        manifest = json.loads((release / "corpus" / "site-manifest.json").read_bytes())
        assert _facts(release)["corpus_commit"] == manifest["corpus_commit"]
        assert (release / "site" / "deployment-capabilities.json").is_file()
        assert not (release / "deployment-capabilities.json").exists()

    def test_the_releases_root_is_created_and_the_envelope_is_world_readable(
        self, world: World, releases: Path
    ) -> None:
        outcome = build(world, releases)

        mode = (releases / outcome.release_content_id).stat().st_mode & 0o777
        assert mode == 0o755


class TestTheOrder:
    def test_steps_run_in_the_adr_order(self, world: World, releases: Path) -> None:
        reached: list[str] = []

        build_release(request_for(world, releases), observer(), None, reached.append)

        assert tuple(reached) == STEPS

    def test_the_id_is_written_before_the_final_check_and_after_the_structural_one(
        self, world: World, releases: Path
    ) -> None:
        seen: dict[str, object] = {}

        def checkpoint(step: str) -> None:
            if step in {"checked", "written", "verified"}:
                (only,) = _build_dirs(releases)
                seen[step] = (
                    _facts(only).get("release_content_id"),
                    (only / RECORD_NAME).exists(),
                    (only / FRAGMENT_NAME).exists(),
                )

        build_release(request_for(world, releases), observer(), None, checkpoint)

        assert seen["checked"] == (None, False, False)
        written_id, record, fragment = seen["written"]  # type: ignore[misc]
        assert isinstance(written_id, str) and is_release_id(written_id)
        assert record and fragment
        assert seen["verified"] == seen["written"]

    @pytest.mark.parametrize("step", STEPS[:-1])
    def test_a_crash_before_the_rename_leaves_only_a_build_directory(
        self, world: World, releases: Path, step: str
    ) -> None:
        with pytest.raises(Killed):
            build_release(request_for(world, releases), observer(), None, _kill_at(step))

        assert _id_dirs(releases) == []
        (left,) = _build_dirs(releases)
        assert not is_release_id(left.name)

        outcome = build(world, releases)

        assert is_complete(releases / outcome.release_content_id)
        assert _build_dirs(releases) == [left]

    def test_a_crash_after_the_rename_leaves_the_complete_envelope(
        self, world: World, releases: Path
    ) -> None:
        with pytest.raises(Killed):
            build_release(request_for(world, releases), observer(), None, _kill_at("renamed"))

        (release,) = _id_dirs(releases)
        assert _build_dirs(releases) == []
        assert is_complete(release)
        assert check_envelope(release).release_content_id == release.name

    def test_a_refusal_removes_its_own_build_directory(self, world: World, releases: Path) -> None:
        run_git(world.checkout, "commit", "--allow-empty", "-q", "-m", "moved")
        (world.checkout / "dirty.txt").write_text("x", encoding="utf-8")
        try:
            with pytest.raises(SiteBuildError, match="dirty work tree"):
                build(world, releases)
        finally:
            (world.checkout / "dirty.txt").unlink()
            run_git(world.checkout, "reset", "-q", "--hard", "HEAD~1")

        assert not releases.exists() or list(releases.iterdir()) == []


class TestTheSameId:
    def test_the_same_finalized_release_is_reused_byte_unchanged(
        self, world: World, releases: Path
    ) -> None:
        first = build(world, releases)
        release = releases / first.release_content_id
        before = files(release)
        inodes = {path: path.stat().st_ino for path in release.rglob("*") if path.is_file()}

        second = build(world, releases)

        assert second.release_content_id == first.release_content_id
        assert second.reused and not second.already_live
        assert files(release) == before
        assert {path: path.stat().st_ino for path in release.rglob("*") if path.is_file()} == inodes
        assert _build_dirs(releases) == []

    def _refuses(self, world: World, releases: Path, existing: Path) -> None:
        before = files(existing)
        with pytest.raises(ReleaseConflictError, match="not the same finalized release"):
            build(world, releases)
        assert files(existing) == before
        (left,) = _build_dirs(releases)
        assert is_complete(left)
        assert check_envelope(left).release_content_id == existing.name

    def test_a_directory_with_another_recomputed_id_is_refused(
        self, world: World, releases: Path
    ) -> None:
        existing = releases / build(world, releases).release_content_id
        page = next((existing / "corpus" / "lov").rglob("index.html"))
        page.write_bytes(page.read_bytes() + b"<!-- edited -->")

        self._refuses(world, releases, existing)

    def test_a_directory_with_another_record_is_refused(self, world: World, releases: Path) -> None:
        existing = releases / build(world, releases).release_content_id
        record = json.loads((existing / RECORD_NAME).read_bytes())
        record["observer"] = "drift-timer"
        (existing / RECORD_NAME).write_text(json.dumps(record), encoding="utf-8")

        self._refuses(world, releases, existing)

    @pytest.mark.parametrize("part", ["site", RECORD_NAME, FRAGMENT_NAME])
    def test_an_incomplete_directory_under_the_id_is_refused(
        self, world: World, releases: Path, part: str
    ) -> None:
        existing = releases / build(world, releases).release_content_id
        target = existing / part
        if target.is_dir():
            (existing / "site").rename(existing / "site.moved")
        else:
            target.unlink()

        self._refuses(world, releases, existing)


class TestTheShortCircuit:
    def test_the_same_key_observed_later_is_already_live_and_builds_nothing(
        self, world: World, releases: Path
    ) -> None:
        live = build(world, releases).release_content_id
        before = files(releases / live)

        outcome = build(world, releases, live=live, observe=observer(LATER))

        assert outcome.already_live
        assert outcome.release_content_id == live
        assert files(releases / live) == before
        assert sorted(releases.iterdir()) == [releases / live]

    def test_the_same_key_without_a_live_release_yields_other_bytes(
        self, world: World, releases: Path
    ) -> None:
        first = build(world, releases).release_content_id

        second = build(world, releases, live=None, observe=observer(LATER)).release_content_id

        assert second != first
        keys = [read_record(releases / found).release_key for found in (first, second)]
        assert keys[0] == keys[1]

    def test_a_changed_observed_state_is_a_release(self, world: World, releases: Path) -> None:
        live = build(world, releases).release_content_id

        outcome = build(world, releases, live=live, observe=observer(LATER, ready=False))

        assert not outcome.already_live
        assert outcome.release_content_id != live
        key = read_record(releases / outcome.release_content_id).release_key
        assert key.state_sha256 != read_record(releases / live).release_key.state_sha256

    def test_a_changed_corpus_commit_is_a_release(self, world: World, releases: Path) -> None:
        live = build(world, releases).release_content_id
        run_git(world.corpus, "commit", "--allow-empty", "-q", "-m", "again")
        try:
            outcome = build(world, releases, live=live)
        finally:
            run_git(world.corpus, "reset", "-q", "--hard", "HEAD~1")

        assert not outcome.already_live
        assert outcome.release_content_id != live

    def test_a_changed_toolchain_is_a_release(self, world: World, releases: Path) -> None:
        live = build(world, releases).release_content_id
        lock = world.checkout / "uv.lock"
        original = lock.read_text(encoding="utf-8")
        lock.write_text(original + "# bumped\n", encoding="utf-8")
        commit_all(world.checkout, "bump")
        try:
            outcome = build(world, releases, live=live)
        finally:
            run_git(world.checkout, "reset", "-q", "--hard", "HEAD~1")

        assert not outcome.already_live
        assert outcome.release_content_id != live

    def test_a_live_release_that_is_not_on_disk_is_refused(
        self, world: World, releases: Path
    ) -> None:
        with pytest.raises(IncompleteEnvelopeError):
            build(world, releases, live="9" * 64)
        assert not releases.exists() or _build_dirs(releases) == []


class TestTheLinkPass:
    def test_unchanged_files_share_the_live_releases_inodes(
        self, world: World, releases: Path
    ) -> None:
        live = build(world, releases).release_content_id

        changed = observer(LATER, ready=False)
        new = build(world, releases, live=live, observe=changed).release_content_id

        assert new != live
        page = next((releases / new / "corpus" / "lov").rglob("index.html"))
        twin = releases / live / page.relative_to(releases / new)
        assert page.stat().st_ino == twin.stat().st_ino
        assert page.stat().st_nlink == 2
        for name in ("site/deployment-capabilities.json", "site/site-facts.json", RECORD_NAME):
            assert (releases / new / name).stat().st_nlink == 1
        assert check_envelope(releases / new).release_content_id == new
        assert check_envelope(releases / live).release_content_id == live


class TestCandidate:
    def test_a_ref_that_is_not_a_commit_is_refused_before_the_probe(
        self, world: World, releases: Path
    ) -> None:
        asked: list[object] = []

        def observe(checkout: object) -> object:
            asked.append(checkout)
            raise AssertionError("must not be reached")

        request = BuildRequest(
            releases=releases, checkout=world.checkout, corpus=world.corpus, corpus_ref="HEAD:x"
        )
        with pytest.raises(ReleaseError, match="not a corpus commit"):
            candidate(request, observe)  # type: ignore[arg-type]
        assert asked == []

    def test_the_key_is_built_from_the_probes_state(self, world: World, releases: Path) -> None:
        found = candidate(request_for(world, releases), observer())

        assert found.corpus_commit == world.corpus_commit
        assert found.key.corpus_commit == world.corpus_commit
        assert found.key.lovspor_commit == world.lovspor_commit
        assert found.document.state.checkout.lovspor_commit == world.lovspor_commit
