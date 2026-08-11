"""Stage 6 treatment tool surface: server config, tool list, schema hash."""

import hashlib
import json
import re
import sys
import sysconfig
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

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

# The interpreter the LLHB apparatus runs on. Lives here, not in the anchor
# document: if the anchor's own `python` field decided when the hash guard
# runs, editing the anchor to a version no CI leg has would switch the guard
# off — the anchor would opt out of its own verification. The CI matrix must
# keep a leg on this version (.github/workflows/test.yml).
APPARATUS_PYTHON = "3.12"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    build_corpus(tmp_path, CORPUS_DOCS)
    return tmp_path


class TestApparatusCiLeg:
    def test_the_ci_matrix_keeps_the_apparatus_interpreter(self) -> None:
        """The hash guard runs only on APPARATUS_PYTHON, so a matrix that
        drops that leg stays green on every remaining leg while no leg
        checks the hash. The comment in test.yml is advice; this is the
        enforcement, and it fails on whatever legs remain. The check is
        on the legs GitHub will actually run — an axis entry cancelled by
        ``exclude`` is not a leg, so listing "3.12" while excluding it
        must fail here too."""
        workflow = yaml.safe_load(
            (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "test.yml").read_text(
                encoding="utf-8"
            )
        )

        legs = _effective_matrix_versions(workflow["jobs"]["test"]["strategy"]["matrix"])

        assert APPARATUS_PYTHON in legs


def _effective_matrix_versions(matrix: dict[str, Any]) -> set[str]:
    """The python versions GitHub will actually spawn jobs for.

    Axis entries minus ``exclude`` matches plus ``include`` additions —
    the three ways test.yml can decide the set of legs. str() guards the
    YAML float trap: an unquoted 3.12 loads as a number, and the leg
    would count as missing on a quoting change.
    """
    versions = {str(version) for version in matrix["python-version"]}
    for entry in matrix.get("exclude") or []:
        versions.discard(str(entry.get("python-version")))
    for entry in matrix.get("include") or []:
        if "python-version" in entry:
            versions.add(str(entry["python-version"]))
    return versions


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

    def test_schema_hash_pins_the_canonical_form(
        self, corpus: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Key order, separators, non-ASCII escaping and the omission of
        empty fields are all part of what the hash means. Against a stub
        surface the expected digest is computable by hand, so a change to
        any of them stops matching instead of quietly renaming the hash."""

        class _Tool:
            def model_dump(self, **kwargs: object) -> dict[str, object]:
                # The dump contract is part of what the hash covers, so the
                # stub holds the caller to it instead of ignoring it.
                assert kwargs["mode"] == "json"
                document: dict[str, object] = {"name": "b", "title": None, "description": "æ"}
                if kwargs["exclude_none"]:
                    return {key: value for key, value in document.items() if value is not None}
                return document

        class _StubServer:
            async def list_tools(self) -> list[object]:
                return [_Tool(), _Tool()]

        monkeypatch.setattr(mcp_surface, "build_server", lambda _path: _StubServer())

        canonical = '[{"description":"æ","name":"b"},{"description":"æ","name":"b"}]'
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert tool_surface(corpus).schema_sha256 == expected

    def test_the_surface_cannot_be_edited_after_it_is_read(self, corpus: Path) -> None:
        """The recorded surface is evidence; a caller must not be able to
        adjust it between reading the server and writing the metadata."""
        surface = tool_surface(corpus)

        with pytest.raises(ValidationError):
            surface.names = ()

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

    def test_rejects_another_executable_in_the_same_directory(self) -> None:
        """Living in the right venv is not the same as being the server.
        Any other entry point there may expose a different tool set."""
        with pytest.raises(ToolSurfaceError, match="is not"):
            verify_server_command(Path(sysconfig.get_path("scripts")) / "python")

    def test_rejects_a_command_from_another_environment(self, tmp_path: Path) -> None:
        """The surface is read in-process; launching a different install
        would record a server the run never spoke to."""
        stranger = tmp_path / "bin" / "lovspor"
        stranger.parent.mkdir()
        stranger.write_text("#!/bin/sh\n", encoding="utf-8")

        with pytest.raises(ToolSurfaceError, match="is not"):
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

    def test_json_pins_the_exact_bytes_on_the_argv(self, tmp_path: Path) -> None:
        """This string is passed to the CLI verbatim and is part of what a
        rerun has to reproduce, so key order, separators and non-ASCII
        escaping are pinned rather than merely round-tripped. The corpus
        path carries a Norwegian letter on purpose: an ASCII-only path
        cannot tell an escaping change from a no-op."""
        corpus = tmp_path / "lovverk-æøå"
        rendered = server_config_json(tmp_path / "bin" / "lovspor", corpus)

        expected = (
            '{"mcpServers":{"lovverk":{"args":["mcp","--corpus-path","'
            + str(corpus)
            + '"],"command":"'
            + str(tmp_path / "bin" / "lovspor")
            + '"}}}'
        )
        assert rendered == expected
        assert "æ" in rendered
        assert json.loads(rendered) == server_config(tmp_path / "bin" / "lovspor", corpus)


class TestToolConfig:
    def test_namespaces_tools_and_records_the_backend(self, corpus: Path) -> None:
        surface = tool_surface(corpus)

        config = tool_config(surface, "6ec7059d53d25ddae99d8a64bf5157a90c4c166c")

        assert config["transport"] == "native-mcp"
        assert config["tool_schema_sha256"] == surface.schema_sha256
        assert config["tools"] == allowed_tools(surface)
        assert config["tools"][0].startswith("mcp__lovverk__")
        assert "6ec7059d53d25ddae99d8a64bf5157a90c4c166c" in config["backend"]

    def test_every_served_tool_passes_the_run_metadata_schema(self, corpus: Path) -> None:
        """The gate refuses a declared tool that does not name the lovverk
        server. The surface this module produces is what a real run
        declares, so the producer has to satisfy the pattern that grades
        it — for every tool served, not just the first."""
        pattern = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "benchmarks"
                / "llhb"
                / "schema"
                / "run_metadata.schema.json"
            ).read_text(encoding="utf-8")
        )["properties"]["tool_config"]["properties"]["tools"]["items"]["pattern"]

        names = tool_config(tool_surface(corpus), "6" * 40)["tools"]

        assert names and all(re.match(pattern, name) for name in names)

    def test_the_frozen_surface_document_is_what_the_code_serves(self, tmp_path: Path) -> None:
        """tool-surface-v1.json is the expectation check_fairness compares a
        run's declaration against, so it must be the code's own account,
        re-derived here on every run. Two corpora with disjoint content are
        the witness that the surface comes from build_server, not from the
        documents. Sampling cannot rule out deliberately corpus-conditional
        tool registration — but it does not have to: a real run's declared
        tool_config is computed from the pinned corpus (run_arm.py), so a
        surface that diverged there would disagree with this anchor and fail
        the fairness gate. This test is the early warning; the gate is the
        enforcement. If this fails, the served surface changed: regenerating
        the document is an apparatus decision to make explicitly, not a
        fixture to patch.

        The hash is compared only on the apparatus interpreter: tool
        descriptions come from docstrings, and CPython 3.13 dedents
        docstrings at compile time, so each interpreter serves genuinely
        different description bytes. The names are interpreter-stable and
        checked everywhere; a run made on a different interpreter records
        a different hash and fails the fairness gate on its own. Which
        interpreter is the apparatus is this test's constant, asserted
        against the anchor on every interpreter — read from the anchor
        alone, an anchor edited to a version no CI leg runs would switch
        its own hash guard off."""
        committed = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "benchmarks"
                / "llhb"
                / "runner"
                / "tool-surface-v1.json"
            ).read_text(encoding="utf-8")
        )
        corpora = (
            ("first", CORPUS_DOCS),
            ("second", {"annenloven": ("Annen loven", "### § 2. Virkeområde\n\nAnnet innhold.\n")}),
        )
        apparatus_interpreter = f"{sys.version_info[0]}.{sys.version_info[1]}" == APPARATUS_PYTHON

        assert committed["python"] == APPARATUS_PYTHON
        assert committed["server"] == mcp_surface.SERVER_NAME
        for name, docs in corpora:
            root = tmp_path / name
            build_corpus(root, docs)
            surface = tool_surface(root)
            assert committed["tools"] == allowed_tools(surface), name
            if apparatus_interpreter:
                assert committed["tool_schema_sha256"] == surface.schema_sha256, name

    def test_the_backend_names_the_commit_not_the_machine(self, corpus: Path) -> None:
        """Published metadata identifies the corpus by commit. A checkout
        path identifies whoever ran it, and leaks their filesystem."""
        config = tool_config(tool_surface(corpus), "6" * 40)

        assert str(corpus) not in config["backend"]
        assert str(Path.home()) not in config["backend"]
