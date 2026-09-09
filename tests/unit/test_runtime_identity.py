"""Runtime identity: what a process actually runs (ADR-0014 Decision 4)."""

import hashlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path

import pytest

import lovspor
from lovspor.runtime_identity import (
    Distribution,
    canonical_environment_sha256,
    installed_distributions,
    installed_environment_sha256,
    interpreter,
    normalise_name,
    tree_sha256,
)

PACKAGE_DIR = Path(lovspor.__file__).resolve().parent


def _package(root: Path) -> Path:
    package = root / "lovspor"
    (package / "site" / "templates").mkdir(parents=True)
    (package / "publish").mkdir()
    (package / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (package / "publish" / "pages.py").write_text("y = 2\n", encoding="utf-8")
    (package / "site" / "templates" / "_base.html").write_text("<html>", encoding="utf-8")
    return package


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class TestTreeSha256:
    def test_is_the_digest_of_the_canonical_listing(self, tmp_path: Path) -> None:
        package = _package(tmp_path)

        listing = f"__init__.py\0{_sha(b'x = 1\n')}\npublish/pages.py\0{_sha(b'y = 2\n')}\n"
        assert tree_sha256(package) == _sha(listing.encode("utf-8"))

    def test_moves_with_a_runtime_file_and_with_its_path(self, tmp_path: Path) -> None:
        package = _package(tmp_path)
        before = tree_sha256(package)

        (package / "publish" / "pages.py").write_text("y = 3\n", encoding="utf-8")
        changed = tree_sha256(package)
        (package / "publish" / "pages.py").rename(package / "publish" / "page.py")
        moved = tree_sha256(package)

        assert len({before, changed, moved}) == 3

    def test_moves_when_a_runtime_file_is_added(self, tmp_path: Path) -> None:
        package = _package(tmp_path)
        before = tree_sha256(package)

        (package / "new.py").write_text("", encoding="utf-8")

        assert tree_sha256(package) != before

    def test_ignores_the_site_tree_bytecode_and_caches(self, tmp_path: Path) -> None:
        """Templates and site content are not what the process runs; a
        compiled cache is an artefact of having run (ADR:777-780)."""
        package = _package(tmp_path)
        before = tree_sha256(package)

        (package / "site" / "templates" / "_base.html").write_text("<body>", encoding="utf-8")
        (package / "site" / "build.py").write_text("z = 1\n", encoding="utf-8")
        (package / "__pycache__").mkdir()
        (package / "__pycache__" / "__init__.cpython-312.pyc").write_bytes(b"\x00")
        (package / "publish" / "stray.pyc").write_bytes(b"\x00")

        assert tree_sha256(package) == before

    def test_covers_the_real_package(self) -> None:
        digest = tree_sha256(PACKAGE_DIR)

        assert len(digest) == 64
        assert digest == tree_sha256(PACKAGE_DIR)


class TestInterpreter:
    def test_names_implementation_and_exact_version(self) -> None:
        found = interpreter()
        major, minor, micro = sys.version_info[:3]

        assert found.implementation == sys.implementation.name
        assert found.version == f"{major}.{minor}.{micro}"
        assert found.label == f"{sys.implementation.name} {major}.{minor}.{micro}"


class TestCanonicalEnvironmentSha256:
    def test_is_order_independent(self) -> None:
        a = Distribution(name="httpx", version="0.28.1", direct_url=None)
        b = Distribution(name="pydantic", version="2.11.0", direct_url=None)

        assert canonical_environment_sha256((a, b)) == canonical_environment_sha256((b, a))

    def test_is_the_digest_of_the_canonical_listing(self) -> None:
        a = Distribution(name="httpx", version="0.28.1", direct_url=None)
        b = Distribution(name="x", version="1", direct_url="git+https://example.invalid/x@abc")

        listing = "httpx\x000.28.1\x00\nx\x001\x00git+https://example.invalid/x@abc\n"
        assert canonical_environment_sha256((b, a)) == _sha(listing.encode("utf-8"))

    def test_moves_with_version_and_direct_url(self) -> None:
        base = Distribution(name="httpx", version="0.28.1", direct_url=None)
        bumped = Distribution(name="httpx", version="0.28.2", direct_url=None)
        pinned = Distribution(name="httpx", version="0.28.1", direct_url="file:///x")

        digests = {canonical_environment_sha256((d,)) for d in (base, bumped, pinned)}
        assert len(digests) == 3


class TestInstalledDistributions:
    def test_covers_the_production_closure_and_nothing_else(self) -> None:
        """Direct dependencies, their transitive closure and the extras they
        name (pyjwt[crypto] pulls cryptography) — no dev group, no extra of
        lovspor itself, not lovspor's own editable install."""
        names = {distribution.name for distribution in installed_distributions()}

        assert {"httpx", "pydantic", "jinja2", "markupsafe", "cryptography", "mcp"} <= names
        assert not names & {"pytest", "ruff", "mypy", "hypothesis", "pytest-httpx"}
        assert not names & {"sentence-transformers", "torch"}
        assert "lovspor" not in names

    def test_names_are_normalised_and_unique(self) -> None:
        distributions = installed_distributions()
        names = [distribution.name for distribution in distributions]

        assert names == sorted(names)
        assert len(names) == len(set(names))
        assert all(name == name.lower() and "_" not in name for name in names)

    def test_records_no_direct_url_for_index_installs(self) -> None:
        by_name = {d.name: d for d in installed_distributions()}

        assert by_name["httpx"].direct_url is None


class TestInstalledEnvironmentSha256:
    def test_is_the_canonical_hash_of_the_installed_closure(self) -> None:
        assert installed_environment_sha256() == canonical_environment_sha256(
            installed_distributions()
        )
        assert installed_environment_sha256() == installed_environment_sha256()


class _StubDistribution(importlib.metadata.Distribution):
    """A distribution defined by its metadata text alone."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def read_text(self, filename: str) -> str | None:
        return self._files.get(filename)

    def locate_file(self, path: str | os.PathLike[str]) -> Path:
        return Path(path)


def _metadata(name: str, version: str, *requires: str) -> str:
    lines = [f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"]
    lines.extend(f"Requires-Dist: {requirement}\n" for requirement in requires)
    return "".join(lines)


@pytest.fixture
def index(monkeypatch: pytest.MonkeyPatch) -> dict[str, _StubDistribution]:
    stubs = {
        "app": _StubDistribution(
            {"METADATA": _metadata("app", "0.1", "A_lib[x]>=1", "b; extra == 'opt'", "missing")}
        ),
        "a-lib": _StubDistribution(
            {"METADATA": _metadata("A_lib", "1.0", 'c; extra == "x"', "d; extra == 'z'", "e")}
        ),
        "b": _StubDistribution({"METADATA": _metadata("b", "2")}),
        "c": _StubDistribution(
            {
                "METADATA": _metadata("c", "3"),
                "direct_url.json": json.dumps(
                    {"url": "https://example.invalid/c.git", "vcs_info": {"commit_id": "abc"}}
                ),
            }
        ),
        "d": _StubDistribution({"METADATA": _metadata("d", "4")}),
        "e": _StubDistribution(
            {"METADATA": _metadata("e", "5"), "direct_url.json": json.dumps({"url": "file:///e"})}
        ),
    }

    def distribution(name: str) -> _StubDistribution:
        try:
            return stubs[normalise_name(name)]
        except KeyError:
            raise importlib.metadata.PackageNotFoundError(name) from None

    monkeypatch.setattr(importlib.metadata, "distribution", distribution)
    return stubs


class TestClosureWalk:
    def test_follows_requested_extras_and_skips_the_rest(self, index: dict[str, object]) -> None:
        """``a_lib[x]`` pulls ``c``; ``d`` sits behind an extra nobody asked
        for, ``b`` behind an extra of the root, ``missing`` is not
        installed, and the root itself is excluded."""
        found = installed_distributions("app")

        assert [d.name for d in found] == ["a-lib", "c", "e"]

    def test_records_direct_urls_with_their_commit(self, index: dict[str, object]) -> None:
        by_name = {d.name: d for d in installed_distributions("app")}

        assert by_name["c"].direct_url == "https://example.invalid/c.git@abc"
        assert by_name["e"].direct_url == "file:///e"
        assert by_name["a-lib"].direct_url is None
        assert by_name["a-lib"].version == "1.0"
