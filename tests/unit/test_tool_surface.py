"""The neutral tool-surface descriptor both LLHB and publication read."""

import ast
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

import lovspor.tool_surface
from lovspor.llhb.mcp_surface import tool_surface
from lovspor.tool_surface import (
    ToolSurfaceDescriptor,
    describe_listed_tools,
    describe_tool_surface,
)
from tests.unit.llhb_fixtures import build_corpus

CORPUS_DOCS = {"testloven": ("Testloven", "### § 1. Formål\n\nLoven gjelder.\n")}


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    build_corpus(tmp_path, CORPUS_DOCS)
    return tmp_path


class _Tool:
    def __init__(self, name: str) -> None:
        self._name = name

    def model_dump(self, **kwargs: object) -> dict[str, object]:
        # The dump contract is part of what the hash covers, so the stub
        # holds the caller to it instead of ignoring it.
        assert kwargs["mode"] == "json"
        document: dict[str, object] = {"name": self._name, "title": None, "description": "æ"}
        if kwargs["exclude_none"]:
            return {key: value for key, value in document.items() if value is not None}
        return document


class _StubServer:
    def __init__(self, *names: str) -> None:
        self._names = names

    async def list_tools(self) -> list[object]:
        return [_Tool(name) for name in self._names]


class TestDescribeToolSurface:
    def test_names_are_sorted_unique_and_non_empty(self, corpus: Path) -> None:
        descriptor = describe_tool_surface(corpus)

        assert descriptor.names
        assert list(descriptor.names) == sorted(set(descriptor.names))

    def test_tool_count_is_the_number_of_names(self, corpus: Path) -> None:
        descriptor = describe_tool_surface(corpus)

        assert descriptor.tool_count == len(descriptor.names)

    def test_schema_hash_is_hex_and_stable(self, corpus: Path) -> None:
        first = describe_tool_surface(corpus)
        second = describe_tool_surface(corpus)

        assert len(first.schema_sha256) == 64
        assert set(first.schema_sha256) <= set("0123456789abcdef")
        assert first.schema_sha256 == second.schema_sha256

    def test_llhb_reads_the_same_descriptor(self, corpus: Path) -> None:
        """One registry, two readers: the benchmark's recorded surface and
        the published one must never be able to disagree about the same
        corpus."""
        descriptor = describe_tool_surface(corpus)
        surface = tool_surface(corpus)

        assert surface.names == descriptor.names
        assert surface.schema_sha256 == descriptor.schema_sha256

    def test_schema_hash_pins_the_canonical_form(self, corpus: Path) -> None:
        """Name order, key order, separators, non-ASCII escaping and the
        omission of empty fields are all part of what the hash means.
        Against a stub server the digest is computable by hand, so a change
        to any of them stops matching instead of quietly renaming the
        hash."""
        seen: list[Path] = []

        def factory(path: Path) -> _StubServer:
            seen.append(path)
            return _StubServer("b", "a")

        descriptor = describe_tool_surface(corpus, server_factory=factory)

        canonical = '[{"description":"æ","name":"a"},{"description":"æ","name":"b"}]'
        assert seen == [corpus]
        assert descriptor.names == ("a", "b")
        assert descriptor.tool_count == 2
        assert descriptor.schema_sha256 == hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def test_describes_a_toolless_server_as_empty(self, corpus: Path) -> None:
        """The registry reports what is served; refusing an empty surface
        is a consumer's policy (LLHB's treatment arm refuses it)."""
        descriptor = describe_tool_surface(corpus, server_factory=lambda _path: _StubServer())

        assert descriptor.names == ()
        assert descriptor.tool_count == 0
        assert descriptor.schema_sha256 == hashlib.sha256(b"[]").hexdigest()


class TestDescribeListedTools:
    def test_hashes_a_listed_tool_set_exactly_as_the_server_description_does(
        self, corpus: Path
    ) -> None:
        """The release probe hashes what ``tools/list`` returned through the
        public path with this function; the two sides of
        ``transport_surface_match`` must be one hashing, not two."""
        listed = describe_listed_tools([_Tool("b"), _Tool("a")])
        described = describe_tool_surface(corpus, server_factory=lambda _p: _StubServer("b", "a"))

        assert listed == described

    def test_orders_by_name_whatever_order_the_server_listed(self) -> None:
        assert describe_listed_tools([_Tool("b"), _Tool("a")]) == describe_listed_tools(
            [_Tool("a"), _Tool("b")]
        )

    def test_an_empty_listing_is_the_empty_surface(self) -> None:
        descriptor = describe_listed_tools([])

        assert descriptor.names == ()
        assert descriptor.schema_sha256 == hashlib.sha256(b"[]").hexdigest()


class TestToolSurfaceDescriptor:
    def test_cannot_be_edited_after_it_is_read(self) -> None:
        descriptor = ToolSurfaceDescriptor(names=("a",), schema_sha256="0" * 64, tool_count=1)

        with pytest.raises(ValidationError):
            descriptor.names = ()

    def test_rejects_a_count_that_disagrees_with_the_names(self) -> None:
        with pytest.raises(ValidationError, match="tool_count"):
            ToolSurfaceDescriptor(names=("a",), schema_sha256="0" * 64, tool_count=2)


class TestLayering:
    def test_the_registry_does_not_import_the_benchmark(self) -> None:
        """A public product surface reads this module; it must not pull
        benchmark methodology in as its registry (ADR-0014)."""
        source = Path(lovspor.tool_surface.__file__).read_text(encoding="utf-8")

        imported = _imported_modules(ast.parse(source))

        assert not [
            module
            for module in imported
            if module == "lovspor.llhb" or module.startswith("lovspor.llhb.")
        ]


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules
