"""Regression tests for the lovspor.no canonical-host rename in the docs.

The hosted MCP endpoint moved from the personal-domain alias
`lovspor.bartoszkobylinski.com` to the canonical `lovspor.no`, mirroring the
earlier deploy/landing-page rename pinned in test_deploy_landing.py. The alias
was retired on 2026-09-17 — dropped from `LOVSPOR_DOMAIN`, its certificate
removed, and its DNS record deleted — so no document may cite it any more:
naming it now sends a reader to a host that does not resolve.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_README = _ROOT / "README.md"
_MCP_DOC = _ROOT / "docs" / "mcp.md"
_OPERATIONS_DOC = _ROOT / "docs" / "operations.md"
_ROADMAP_DOC = _ROOT / "docs" / "roadmap.md"
_PUBLISH_RELEASE = _ROOT / "deploy" / "digitalocean" / "publish-release.sh"
_REHEARSE_URLS = _ROOT / "deploy" / "digitalocean" / "rehearse-urls.sh"

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


def test_mcp_doc_hosted_endpoint_uses_the_canonical_host() -> None:
    text = _MCP_DOC.read_text(encoding="utf-8")

    assert _CANONICAL in text
    # mcp.md used to be the one document allowed to name the alias, on the
    # grounds that it still resolved. It no longer does, so the exemption is
    # gone with it and this doc is held to the same rule as the other three.
    assert _OLD_ALIAS not in text


def test_deploy_script_guidance_does_not_name_the_retired_alias() -> None:
    for script in (_PUBLISH_RELEASE, _REHEARSE_URLS):
        text = script.read_text(encoding="utf-8")

        assert _OLD_ALIAS not in text, script
        assert "do not source it" in text, script
