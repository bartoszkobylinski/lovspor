import re
import subprocess
from pathlib import Path

_CADDYFILE = Path("deploy/digitalocean/Caddyfile")
_PROVISION = Path("deploy/digitalocean/provision.sh")
_LANDING = Path("deploy/digitalocean/site/index.html")  # Norwegian, canonical
_LANDING_EN = Path("deploy/digitalocean/site/en/index.html")
_README = Path("deploy/digitalocean/README.md")


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


def test_provision_installs_the_static_site_into_var_www() -> None:
    text = _PROVISION.read_text(encoding="utf-8")

    assert "install -d /var/www/lovspor" in text
    # Every file under site/, not a named list: the landing page stopped being
    # the only one the moment the crawler started advertising /observatory, and
    # a deploy step that names files silently omits the next page added.
    assert 'find "$APP_DIR/deploy/digitalocean/site" -type f -print0' in text
    assert 'install -m644 "$page" "/var/www/lovspor/$rel"' in text


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


def _extract_provision_sync_loop() -> str:
    text = _PROVISION.read_text(encoding="utf-8")
    match = re.search(
        r'while IFS= read -r -d "" page; do.*?'
        r'\ndone < <\(find "\$APP_DIR/deploy/digitalocean/site" -type f -print0\)\n',
        text,
        re.DOTALL,
    )
    assert match is not None, "provision.sh no longer contains the site-sync loop"
    return match.group(0)


def test_provision_sync_loop_mirrors_the_full_site_tree_including_nested_dirs(
    tmp_path: Path,
) -> None:
    # Runs the exact loop shipped in provision.sh (only the hardcoded
    # /var/www/lovspor prefix is swapped for a writable tmp path, since the
    # real destination needs root). A rewrite of this loop's logic here would
    # test the rewrite, not the deploy step that actually ships.
    app_dir = tmp_path / "app"
    site = app_dir / "deploy" / "digitalocean" / "site"
    (site / "observatory").mkdir(parents=True)
    (site / "assets" / "img").mkdir(parents=True)
    (site / "index.html").write_text("root page", encoding="utf-8")
    (site / "observatory" / "index.html").write_text("observatory page", encoding="utf-8")
    (site / "assets" / "img" / "logo.svg").write_text("<svg/>", encoding="utf-8")

    dest = tmp_path / "www"
    loop = _extract_provision_sync_loop().replace("/var/www/lovspor", str(dest))

    result = subprocess.run(
        ["bash", "-c", f'APP_DIR="{app_dir}"\n{loop}'],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    assert (dest / "index.html").read_text(encoding="utf-8") == "root page"
    assert (dest / "observatory" / "index.html").read_text(encoding="utf-8") == "observatory page"
    assert (dest / "assets" / "img" / "logo.svg").read_text(encoding="utf-8") == "<svg/>"


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


def test_readme_documents_the_landing_page_update_workflow_to_the_real_destination() -> None:
    text = _README.read_text(encoding="utf-8")

    # The rsync target must match provision.sh's actual install destination
    # (checked in test_provision_installs_the_static_site_into_var_www) —
    # otherwise the documented shortcut writes to a path Caddy never serves.
    assert "rsync -av --delete deploy/digitalocean/site/ root@" in text
    # Public SSH is firewalled off the droplet; documenting the public IPv4
    # here sends the reader into a port-22 timeout, which is exactly how this
    # deploy step failed the first time it was run.
    assert "TAILSCALE" in text
    assert "/var/www/lovspor/" in text
    assert "lovspor-observatory" in text
