"""Tests for lovspor.observatory.storage — the ADR-0010 §5 storage boundary."""

from pathlib import Path

import pytest

from lovspor.errors import ConfigError, StorageBoundaryError
from lovspor.observatory.storage import (
    ENV_CORPUS_ROOT,
    ENV_OBSERVATORY_ROOT,
    ObservatoryRoot,
    _repository_root,
    engine_root,
    forbidden_trees,
    observatory_root,
)


class TestConfiguredRoot:
    def test_absolute_root_outside_forbidden_trees_is_accepted(self, tmp_path: Path) -> None:
        root = tmp_path / "observatory"

        assert ObservatoryRoot(root, [tmp_path / "engine"]).path == root.resolve()

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_unset_root_is_a_config_error(self, raw: str | None) -> None:
        with pytest.raises(ConfigError, match=ENV_OBSERVATORY_ROOT):
            ObservatoryRoot(raw, [])

    def test_relative_root_is_refused(self) -> None:
        """A relative path resolves against the process cwd — which may be a checkout."""
        with pytest.raises(ConfigError, match="absolute"):
            ObservatoryRoot("data/observatory", [])

    def test_user_home_shorthand_is_expanded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))

        assert ObservatoryRoot("~/observatory", []).path == (tmp_path / "observatory").resolve()

    def test_roots_compare_and_hash_by_path(self, tmp_path: Path) -> None:
        first = ObservatoryRoot(tmp_path / "obs", [])
        second = ObservatoryRoot(str(tmp_path / "obs"), [])

        assert first == second
        assert len({first, second}) == 1
        assert first != tmp_path
        assert "obs" in repr(first)


class TestStorageBoundary:
    def test_root_inside_a_forbidden_tree_is_refused(self, tmp_path: Path) -> None:
        engine = tmp_path / "engine"
        engine.mkdir()

        with pytest.raises(StorageBoundaryError, match="ADR-0010"):
            ObservatoryRoot(engine / "data" / "observatory", [engine])

    def test_root_equal_to_a_forbidden_tree_is_refused(self, tmp_path: Path) -> None:
        engine = tmp_path / "engine"
        engine.mkdir()

        with pytest.raises(StorageBoundaryError):
            ObservatoryRoot(engine, [engine])

    def test_corpus_repository_is_a_forbidden_tree_too(self, tmp_path: Path) -> None:
        """ADR-0010 §5 names two trees: the engine repo and the public corpus."""
        corpus = tmp_path / "lovverk"
        corpus.mkdir()

        with pytest.raises(StorageBoundaryError):
            ObservatoryRoot(corpus / "observed", [tmp_path / "engine", corpus])

    def test_sibling_directory_sharing_a_prefix_is_allowed(self, tmp_path: Path) -> None:
        """``/x/engine-data`` is not inside ``/x/engine``; a string prefix check would fail this."""
        engine = tmp_path / "engine"
        engine.mkdir()

        assert (
            ObservatoryRoot(tmp_path / "engine-data", [engine]).path
            == (tmp_path / "engine-data").resolve()
        )


class TestForbiddenTrees:
    def test_engine_tree_is_always_forbidden(self) -> None:
        assert engine_root() in forbidden_trees({})

    def test_corpus_tree_is_added_from_the_environment(self, tmp_path: Path) -> None:
        """Protecting the published corpus must not depend on a caller remembering it."""
        corpus = tmp_path / "lovverk"

        assert corpus in forbidden_trees({ENV_CORPUS_ROOT: str(corpus)})

    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_corpus_setting_is_ignored(self, value: str) -> None:
        assert forbidden_trees({ENV_CORPUS_ROOT: value}) == [engine_root()]

    def test_defaults_to_the_real_process_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Called with no ``env`` argument, this must read ``os.environ`` — the
        production call site never passes one."""
        corpus = tmp_path / "lovverk"
        monkeypatch.setenv(ENV_CORPUS_ROOT, str(corpus))

        assert corpus in forbidden_trees()


class TestEnvironmentResolution:
    def test_root_is_read_from_the_environment(self, tmp_path: Path) -> None:
        root = tmp_path / "observatory"

        assert observatory_root({ENV_OBSERVATORY_ROOT: str(root)}).path == root.resolve()

    def test_engine_tree_is_forbidden_by_default(self) -> None:
        inside = engine_root() / "data" / "observatory"

        with pytest.raises(StorageBoundaryError):
            observatory_root({ENV_OBSERVATORY_ROOT: str(inside)})

    def test_corpus_tree_is_forbidden_without_being_passed(self, tmp_path: Path) -> None:
        corpus = tmp_path / "lovverk"
        corpus.mkdir()

        with pytest.raises(StorageBoundaryError):
            observatory_root(
                {ENV_OBSERVATORY_ROOT: str(corpus / "raw"), ENV_CORPUS_ROOT: str(corpus)},
            )

    def test_missing_environment_variable_is_a_config_error(self) -> None:
        with pytest.raises(ConfigError):
            observatory_root({})

    def test_defaults_to_the_real_process_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Called with no ``env`` argument, this must read ``os.environ`` — the
        production call site never passes one."""
        root = tmp_path / "observatory"
        monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(root))
        monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)

        assert observatory_root().path == root.resolve()


class TestEngineRootDetection:
    def test_engine_root_contains_the_package(self) -> None:
        detected = engine_root()

        assert (detected / ".git").exists() or detected.name == "lovspor"

    def test_package_without_a_repository_falls_back_to_its_parent(self, tmp_path: Path) -> None:
        """An installed package may have no .git above it; the package dir is then the tree.

        The walk is bounded by ``ceiling`` so the assertion cannot be changed by
        a stray ``.git`` somewhere above the temporary directory — which is
        exactly what made an earlier version of this test fail on CI but pass
        locally.
        """
        package = tmp_path / "site-packages" / "lovspor" / "observatory"
        package.mkdir(parents=True)

        assert _repository_root(package, tmp_path) == package.parent

    def test_repository_marker_is_found_when_it_is_a_worktree_file(self, tmp_path: Path) -> None:
        """A linked worktree carries .git as a file, not a directory."""
        repo = tmp_path / "repo"
        (repo / "src" / "lovspor").mkdir(parents=True)
        (repo / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")

        assert _repository_root(repo / "src" / "lovspor", tmp_path) == repo
