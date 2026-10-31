"""``server.json``: the entry the official MCP registry publishes for the hosted server."""

import json
import tomllib
from pathlib import Path

from lovspor.site.probe import CANONICAL_MCP_URL

ROOT = Path(__file__).parents[2]
MAX_DESCRIPTION = 100


def _entry() -> dict[str, object]:
    return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def test_entry_points_at_the_canonical_hosted_endpoint() -> None:
    assert _entry()["remotes"] == [{"type": "streamable-http", "url": CANONICAL_MCP_URL}]


def test_entry_version_is_the_package_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert _entry()["version"] == project["version"]


def test_description_fits_the_registry_limit() -> None:
    assert len(str(_entry()["description"])) <= MAX_DESCRIPTION


def test_entry_lists_no_installable_packages() -> None:
    """Operations documents this as a hosted-endpoint-only listing."""
    assert not _entry().get("packages")


def test_entry_keeps_the_publishing_identity_and_repository_together() -> None:
    """The documented GitHub login publishes under this repository owner's name."""
    entry = _entry()
    assert entry["name"] == "io.github.bartoszkobylinski/lovspor"
    assert entry["repository"] == {
        "url": "https://github.com/bartoszkobylinski/lovspor",
        "source": "github",
    }


def test_registry_entry_is_available_in_the_mutation_shadow_tree() -> None:
    """These file-backed tests also run from mutmut's copied project tree."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "server.json" in config["tool"]["mutmut"]["also_copy"]
