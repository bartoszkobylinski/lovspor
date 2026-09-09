"""The publication toolchain: Jinja2 as a core dependency (ADR-0014 Decision 1)."""

import importlib.metadata
import tomllib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


class TestJinja2IsACoreDependency:
    def test_jinja2_resolves_in_the_installed_environment(self) -> None:
        """The renderer participates in byte output, so it is a determinism
        input that must be installed, not assumed (ADR-0014 Decision 1)."""
        assert importlib.metadata.version("jinja2")

    def test_the_lock_names_jinja2_under_lovspor_itself(self) -> None:
        """Not a publication extra: `uv sync --frozen --no-dev` must build the
        site on the documented host, so the lock's own `lovspor` entry — not a
        transitive edge through torch — has to carry it."""
        lock = tomllib.loads((_REPO / "uv.lock").read_text(encoding="utf-8"))
        lovspor = next(package for package in lock["package"] if package["name"] == "lovspor")

        assert {dependency["name"] for dependency in lovspor["dependencies"]} >= {"jinja2"}
        assert any(
            requirement["name"] == "jinja2" and "marker" not in requirement
            for requirement in lovspor["metadata"]["requires-dist"]
        )
