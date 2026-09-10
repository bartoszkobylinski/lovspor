import re
from pathlib import Path

from lovspor.release.caddy import FRAGMENT_ENV, config_pair
from lovspor.release.envelope import fragment_text
from tests.unit.caddy_fakes import admin_listen, toy_adapt

# Resolved from this file, never from the working directory: nothing
# guarantees pytest is invoked from the repository root.
_DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "digitalocean"
_CADDYFILE = _DEPLOY / "Caddyfile"
_LANDING = _DEPLOY / "site" / "index.html"  # Norwegian, canonical
_LANDING_EN = _DEPLOY / "site" / "en" / "index.html"
_README = _DEPLOY / "README.md"


def _app_paths() -> set[str]:
    text = _CADDYFILE.read_text(encoding="utf-8")
    match = re.search(r"@app path (?P<paths>.+)\n", text)
    assert match is not None
    return set(match.group("paths").split())


def test_caddyfile_app_matcher_pins_the_full_public_proxy_surface() -> None:
    # A missed path silently falls through to file_server and 404s from the public
    # hostname while still working on localhost — the exact regression this branch fixes.
    assert _app_paths() == {
        "/mcp",
        "/mcp/*",
        "/healthz",
        "/readyz",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/*",
    }


def test_caddyfile_keeps_the_response_security_headers_declared() -> None:
    text = _CADDYFILE.read_text(encoding="utf-8")

    assert 'Strict-Transport-Security "max-age=31536000; includeSubDomains"' in text
    assert 'X-Content-Type-Options "nosniff"' in text
    assert "-Server" in text


def _significant() -> list[str]:
    """The Caddyfile without its comments and blank lines, as an adapter reads it."""
    return [
        line
        for line in _CADDYFILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_caddyfile_binds_the_admin_api_to_the_permissioned_socket() -> None:
    """ADR-0014 Decision 6: the admin API is what makes a release live and the
    only thing that can say what Caddy serves, so it must not be reachable by
    anything else on the box. The `|0660` suffix is load-bearing — it is the
    mode Caddy creates the socket with — so this is pinned exactly, not by
    substring."""
    lines = _significant()

    assert lines[0] == "{", lines[0]
    assert [line.strip() for line in lines[1 : lines.index("}")]] == [
        "admin unix//run/caddy/admin.sock|0660"
    ]


def test_caddyfile_serves_the_release_through_the_fragment_and_roots_nothing_itself() -> None:
    """The host's file names no release directory at all: every root, every
    redirect map and the release id itself come from the imported fragment, so
    making a release live is a rename plus a reload and never an edit here."""
    text = _CADDYFILE.read_text(encoding="utf-8")

    assert "\timport {$LOVSPOR_RELEASE_FRAGMENT:/etc/caddy/lovspor-release.caddy}\n" in text
    for directive in ("root *", "file_server"):
        assert not any(line.strip().startswith(directive) for line in _significant()), directive
    # ADR-0014 Decision 6: no symlink exists and the flat site root is retired.
    assert "lovspor-current" not in text
    assert "/var/www/lovspor" not in text


def test_the_composed_configuration_names_the_release_and_the_socket(tmp_path: Path) -> None:
    """This file plus one release's fragment is one release, provably.

    The toy adapter of the control-plane tests, never a real `caddy`: CI has
    none, and what has to hold is that the composed configuration carries the
    fragment's `lovspor_release` var and this file's admin address — exactly
    what the first migration's preflight demands of it before it moves
    anything.
    """
    content_id = "b" * 64
    fragment = tmp_path / "lovspor-release.caddy"
    fragment.write_text(
        fragment_text(tmp_path / "releases" / content_id, content_id), encoding="utf-8"
    )

    config = toy_adapt(_CADDYFILE, {"LOVSPOR_DOMAIN": "lovspor.test", FRAGMENT_ENV: str(fragment)})

    assert admin_listen(config) == "unix//run/caddy/admin.sock|0660"
    assert config_pair(config).release_id == content_id


def test_both_languages_keep_the_bearer_token_connection_instructions() -> None:
    """The snippet is the one thing a visitor copies, so it must survive
    translation intact — and identically, since a host or header that differs
    between the two pages sends half the readers somewhere wrong."""
    for page in (_LANDING, _LANDING_EN):
        text = page.read_text(encoding="utf-8")

        # lovspor.no since the domain moved; the old alias still resolves, but
        # a visitor copying the snippet should end up on the canonical host.
        assert "https://lovspor.no/mcp" in text, page
        assert '--header "Authorization: Bearer YOUR_TOKEN"' in text, page

    assert "Be om et beta-token" in _LANDING.read_text(encoding="utf-8")
    assert "Email for a beta token" in _LANDING_EN.read_text(encoding="utf-8")


def test_the_language_switch_leads_somewhere_on_both_pages() -> None:
    """A toggle that dead-ends is worse than no toggle: the reader has already
    decided the page is in the wrong language before they click it."""
    assert '<a href="/en/">EN</a>' in _LANDING.read_text(encoding="utf-8")
    assert '<a href="/">NO</a>' in _LANDING_EN.read_text(encoding="utf-8")
    assert _LANDING_EN.is_file()


def test_neither_landing_page_claims_a_particular_profession() -> None:
    """The engine is open source and the corpus is public data. Naming an
    audience the project has not earned reads as a claim about who vouches
    for it."""
    for page in (_LANDING, _LANDING_EN):
        text = page.read_text(encoding="utf-8").lower()

        for claim in ("accountant", "payroll", "in-house legal", "compliance", "revisor"):
            assert claim not in text, f"{page}: {claim}"


def test_html_lang_attribute_matches_the_actual_page_language() -> None:
    """A crawler or screen reader trusts this attribute over the visible text;
    a mismatch here is invisible to a human skimming the page but wrong for
    every tool that reads it."""
    assert '<html lang="no">' in _LANDING.read_text(encoding="utf-8")
    assert '<html lang="en">' in _LANDING_EN.read_text(encoding="utf-8")


def test_hreflang_alternate_links_point_reciprocally_at_each_other() -> None:
    """Both pages must advertise both language variants, and each must point
    the *other* page's hreflang at itself — a one-sided or self-pointing
    hreflang pair is a silent SEO regression that no visible rendering catches."""
    no_text = _LANDING.read_text(encoding="utf-8")
    en_text = _LANDING_EN.read_text(encoding="utf-8")

    assert '<link rel="alternate" hreflang="no" href="https://lovspor.no/">' in no_text
    assert '<link rel="alternate" hreflang="en" href="https://lovspor.no/en/">' in no_text
    assert '<link rel="alternate" hreflang="no" href="https://lovspor.no/">' in en_text
    assert '<link rel="alternate" hreflang="en" href="https://lovspor.no/en/">' in en_text


def test_footer_nlod_link_matches_each_pages_own_language() -> None:
    """The footer attribution link is localized independently of the running
    text; drop the locale segment on one side and the two pages point at
    different NLOD document variants without any other test catching it."""
    assert 'href="https://data.norge.no/nlod/2.0"' in _LANDING.read_text(encoding="utf-8")
    assert 'href="https://data.norge.no/nlod/en/2.0"' in _LANDING_EN.read_text(encoding="utf-8")


def test_the_crawler_advertises_a_page_that_exists() -> None:
    """Every observatory request carries `+https://lovspor.no/observatory`.

    A site administrator who follows it is the one reader this page has, and
    they are following it because a robot they did not invite showed up. The
    page has to exist, name the robot, and say how to stop it — before it
    explains anything about the project.
    """
    page = _LANDING.parent / "observatory" / "index.html"

    text = page.read_text(encoding="utf-8")

    assert "lovspor-observatory" in text
    assert "User-agent: lovspor-observatory" in text
    assert "Disallow: /" in text
    # The block instruction comes before the pitch, not after it.
    assert text.index("Disallow: /") < text.index("Hva som lagres")


def test_both_landing_pages_link_to_an_observatory_page_that_exists_on_disk() -> None:
    for page in (_LANDING, _LANDING_EN):
        assert '<a href="/observatory/">' in page.read_text(encoding="utf-8"), page

    assert (_LANDING.parent / "observatory" / "index.html").is_file()


def test_landing_and_observatory_pages_agree_on_contact_and_source_link() -> None:
    observatory = _LANDING.parent / "observatory" / "index.html"

    # A visitor is bounced between these pages by the crawler link and the
    # language switch; a contact address or source link that differs between
    # them is a trust bug, whichever page they happened to land on.
    for page in (_LANDING, _LANDING_EN, observatory):
        text = page.read_text(encoding="utf-8")

        assert "bartosz.kobylinski@gmail.com" in text, page
        assert "https://github.com/bartoszkobylinski/lovspor" in text, page


def test_readme_documents_the_site_as_part_of_the_release_not_an_rsync() -> None:
    text = _README.read_text(encoding="utf-8")

    # ADR-0014 Decision 6: the site is built and released with the corpus as
    # one envelope; an rsync into a live root is the mixed snapshot the
    # envelope exists to prevent, so the shortcut is gone from the runbook.
    assert "## The site is part of the release" in text
    assert "rsync -av --delete deploy/digitalocean/site/" not in text
    assert "lovspor build-site" in text
    # Public SSH is firewalled off the droplet; documenting the public IPv4
    # here sends the reader into a port-22 timeout, which is exactly how this
    # deploy step failed the first time it was run.
    assert "TAILSCALE" in text
    assert "lovspor-observatory" in text
