"""Regression tests for the lovspor.no canonical-host rename in the docs.

The hosted MCP endpoint moved from the personal-domain alias
`lovspor.bartoszkobylinski.com` to the canonical `lovspor.no`, mirroring the
earlier deploy/landing-page rename pinned in test_deploy_landing.py. README.md,
docs/operations.md and docs/roadmap.md must cite only the canonical host;
docs/mcp.md is the one place that documents the old alias, and must present it
as retirable, not as the address to use.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_README = _ROOT / "README.md"
_MCP_DOC = _ROOT / "docs" / "mcp.md"
_OPERATIONS_DOC = _ROOT / "docs" / "operations.md"
_ROADMAP_DOC = _ROOT / "docs" / "roadmap.md"

_CANONICAL = "https://lovspor.no/mcp"
_OLD_ALIAS = "lovspor.bartoszkobylinski.com"


def test_readme_hosted_endpoint_uses_the_canonical_host() -> None:
    text = _README.read_text(encoding="utf-8")

    assert _CANONICAL in text
    assert _OLD_ALIAS not in text


def test_operations_doc_hosted_endpoint_uses_the_canonical_host() -> None:
    text = _OPERATIONS_DOC.read_text(encoding="utf-8")

    assert _CANONICAL in text
    assert _OLD_ALIAS not in text


def test_roadmap_doc_hosted_endpoint_uses_the_canonical_host_everywhere() -> None:
    text = _ROADMAP_DOC.read_text(encoding="utf-8")

    # Three independent roadmap entries cite the endpoint (Sprint 12 item 1,
    # the Class D gap bullet, and the Class D "Progress" bullet) -- a partial
    # rename would leave the old alias standing in whichever one was missed.
    assert text.count(_CANONICAL) == 3
    assert _OLD_ALIAS not in text


def test_mcp_doc_uses_the_canonical_host_and_flags_the_old_alias_as_retirable() -> None:
    text = _MCP_DOC.read_text(encoding="utf-8")

    assert _CANONICAL in text
    # Unlike the other three docs, mcp.md is the one place that still names
    # the old alias -- it must frame it as a retirable fallback, never as the
    # address a reader should copy.
    assert _OLD_ALIAS in text
    assert "may be retired without notice" in text
    assert "use the canonical name" in text
    assert text.index(_CANONICAL) < text.index(_OLD_ALIAS)
