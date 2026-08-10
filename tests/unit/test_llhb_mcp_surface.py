"""Stage 6 treatment tool surface: server config, tool list, schema hash."""

import json
import sysconfig
from pathlib import Path

import pytest

from lovspor.llhb import mcp_surface
from lovspor.llhb.mcp_surface import (
    ToolSurfaceError,
    allowed_tools,
    server_config,
    server_config_json,
    tool_config,
    tool_surface,
    verify_server_command,
)
from tests.unit.llhb_fixtures import build_corpus

CORPUS_DOCS = {"testloven": ("Testloven", "### § 1. Formål\n\nLoven gjelder.\n")}


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    build_corpus(tmp_path, CORPUS_DOCS)
    return tmp_path


class TestToolSurface:
    def test_lists_the_served_tools_sorted(self, corpus: Path) -> None:
        surface = tool_surface(corpus)

        assert "get_section" in surface.names
        assert "semantic_search" in surface.names
        assert list(surface.names) == sorted(surface.names)
        assert len(surface.names) == 16

    def test_schema_hash_is_hex_and_reproducible(self, corpus: Path) -> None:
        first = tool_surface(corpus)
        second = tool_surface(corpus)

        assert len(first.schema_sha256) == 64
        assert set(first.schema_sha256) <= set("0123456789abcdef")
        assert first.schema_sha256 == second.schema_sha256

    def test_hash_does_not_depend_on_the_openai_key(
        self, corpus: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A key only enables semantic_search's backend, not the surface.

        If the recorded hash moved with the ambient environment, two runs
        of the same pinned corpus would disagree about what the model was
        shown — the metadata would be describing the machine, not the run.
        """
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        without_key = tool_surface(corpus)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")

        assert tool_surface(corpus).schema_sha256 == without_key.schema_sha256

    def test_rejects_a_server_that_exposes_nothing(
        self, corpus: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty surface would record a treatment arm with no treatment."""

        class _ToollessServer:
            async def list_tools(self) -> list[object]:
                return []

        monkeypatch.setattr(mcp_surface, "build_server", lambda _path: _ToollessServer())

        with pytest.raises(ToolSurfaceError, match="exposes no tools"):
            tool_surface(corpus)


class TestVerifyServerCommand:
    def test_accepts_this_environment_s_entry_point(self) -> None:
        verify_server_command(Path(sysconfig.get_path("scripts")) / "lovspor")

    def test_rejects_a_command_from_another_environment(self, tmp_path: Path) -> None:
        """The surface is read in-process; launching a different install
        would record a server the run never spoke to."""
        stranger = tmp_path / "bin" / "lovspor"
        stranger.parent.mkdir()
        stranger.write_text("#!/bin/sh\n", encoding="utf-8")

        with pytest.raises(ToolSurfaceError, match="lives outside"):
            verify_server_command(stranger)

    def test_rejects_a_missing_command(self, tmp_path: Path) -> None:
        with pytest.raises(ToolSurfaceError, match="no lovspor executable"):
            verify_server_command(tmp_path / "nowhere" / "lovspor")


class TestServerConfig:
    def test_binds_the_stdio_server_to_the_pinned_corpus(self, tmp_path: Path) -> None:
        config = server_config(tmp_path / "bin" / "lovspor", tmp_path / "pin")

        server = config["mcpServers"]["lovverk"]
        assert server["command"] == str(tmp_path / "bin" / "lovspor")
        assert server["args"] == ["mcp", "--corpus-path", str(tmp_path / "pin")]

    def test_rejects_a_relative_server_command(self, tmp_path: Path) -> None:
        with pytest.raises(ToolSurfaceError, match="server command"):
            server_config(Path(".venv/bin/lovspor"), tmp_path / "pin")

    def test_rejects_a_relative_corpus_path(self, tmp_path: Path) -> None:
        with pytest.raises(ToolSurfaceError, match="corpus path"):
            server_config(tmp_path / "bin" / "lovspor", Path("../lovverk"))

    def test_json_is_compact_and_key_sorted(self, tmp_path: Path) -> None:
        rendered = server_config_json(tmp_path / "bin" / "lovspor", tmp_path / "pin")

        assert " " not in rendered.replace(str(tmp_path), "")
        assert json.loads(rendered) == server_config(tmp_path / "bin" / "lovspor", tmp_path / "pin")


class TestToolConfig:
    def test_namespaces_tools_and_records_the_backend(self, corpus: Path) -> None:
        surface = tool_surface(corpus)

        config = tool_config(surface, corpus, "6ec7059d53d25ddae99d8a64bf5157a90c4c166c")

        assert config["transport"] == "native-mcp"
        assert config["tool_schema_sha256"] == surface.schema_sha256
        assert config["tools"] == allowed_tools(surface)
        assert config["tools"][0].startswith("mcp__lovverk__")
        assert "6ec7059d53d25ddae99d8a64bf5157a90c4c166c" in config["backend"]
        assert str(corpus) in config["backend"]
