"""Tests for lovspor.observatory.storage — the ADR-0010 §5 storage boundary."""

from pathlib import Path

import pytest

from lovspor.errors import ConfigError, StorageBoundaryError
from lovspor.observatory.storage import (
    ENV_OBSERVATORY_ROOT,
    _repository_root,
    engine_root,
    observatory_root,
    resolve_root,
)


class TestConfiguredRoot:
    def test_absolute_root_outside_forbidden_trees_is_accepted(self, tmp_path: Path) -> None:
        root = tmp_path / "observatory"

        assert resolve_root(str(root), [tmp_path / "engine"]) == root.resolve()

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_unset_root_is_a_config_error(self, raw: str | None) -> None:
        with pytest.raises(ConfigError, match=ENV_OBSERVATORY_ROOT):
            resolve_root(raw, [])

    def test_relative_root_is_refused(self) -> None:
        """A relative path resolves against the process cwd — which may be a checkout."""
        with pytest.raises(ConfigError, match="absolute"):
            resolve_root("data/observatory", [])

    def test_user_home_shorthand_is_expanded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))

        assert resolve_root("~/observatory", []) == (tmp_path / "observatory").resolve()


class TestStorageBoundary:
    def test_root_inside_a_forbidden_tree_is_refused(self, tmp_path: Path) -> None:
        engine = tmp_path / "engine"
        engine.mkdir()

        with pytest.raises(StorageBoundaryError, match="ADR-0010"):
            resolve_root(str(engine / "data" / "observatory"), [engine])

    def test_root_equal_to_a_forbidden_tree_is_refused(self, tmp_path: Path) -> None:
        engine = tmp_path / "engine"
        engine.mkdir()

        with pytest.raises(StorageBoundaryError):
            resolve_root(str(engine), [engine])

    def test_corpus_repository_is_a_forbidden_tree_too(self, tmp_path: Path) -> None:
        """ADR-0010 §5 names two trees: the engine repo and the public corpus."""
        corpus = tmp_path / "lovverk"
        corpus.mkdir()

        with pytest.raises(StorageBoundaryError):
            resolve_root(str(corpus / "observed"), [tmp_path / "engine", corpus])

    def test_sibling_directory_sharing_a_prefix_is_allowed(self, tmp_path: Path) -> None:
        """``/x/engine-data`` is not inside ``/x/engine``; a string prefix check would fail this."""
        engine = tmp_path / "engine"
        engine.mkdir()

        assert (
            resolve_root(str(tmp_path / "engine-data"), [engine])
            == (tmp_path / "engine-data").resolve()
        )


class TestEnvironmentResolution:
    def test_root_is_read_from_the_environment(self, tmp_path: Path) -> None:
        root = tmp_path / "observatory"

        assert observatory_root({ENV_OBSERVATORY_ROOT: str(root)}) == root.resolve()

    def test_engine_tree_is_forbidden_by_default(self) -> None:
        """No caller has to remember the engine repo — resolution adds it."""
        inside = engine_root() / "data" / "observatory"

        with pytest.raises(StorageBoundaryError):
            observatory_root({ENV_OBSERVATORY_ROOT: str(inside)})

    def test_corpus_root_extends_the_boundary(self, tmp_path: Path) -> None:
        corpus = tmp_path / "lovverk"
        corpus.mkdir()

        with pytest.raises(StorageBoundaryError):
            observatory_root({ENV_OBSERVATORY_ROOT: str(corpus / "raw")}, corpus)

    def test_missing_environment_variable_is_a_config_error(self) -> None:
        with pytest.raises(ConfigError):
            observatory_root({})


class TestEngineRootDetection:
    def test_engine_root_contains_the_package(self) -> None:
        detected = engine_root()

        assert (detected / ".git").exists() or detected.name == "lovspor"

    def test_package_without_a_repository_falls_back_to_its_parent(self, tmp_path: Path) -> None:
        """An installed package has no .git above it; the package dir is then the forbidden tree."""
        package = tmp_path / "site-packages" / "lovspor" / "observatory"
        package.mkdir(parents=True)

        assert _repository_root(package) == package.parent

    def test_repository_marker_is_found_when_it_is_a_worktree_file(self, tmp_path: Path) -> None:
        """A linked worktree carries .git as a file, not a directory."""
        repo = tmp_path / "repo"
        (repo / "src" / "lovspor").mkdir(parents=True)
        (repo / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")

        assert _repository_root(repo / "src" / "lovspor") == repo
