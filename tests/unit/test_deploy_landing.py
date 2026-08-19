import re
from pathlib import Path

_CADDYFILE = Path("deploy/digitalocean/Caddyfile")
_PROVISION = Path("deploy/digitalocean/provision.sh")
_LANDING = Path("deploy/digitalocean/site/index.html")


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


def test_landing_page_keeps_the_bearer_token_connection_instructions() -> None:
    text = _LANDING.read_text(encoding="utf-8")

    # lovspor.no since the domain moved; the old alias still resolves, but a
    # visitor copying the snippet should end up on the canonical host.
    assert "https://lovspor.no/mcp" in text
    assert '--header "Authorization: Bearer YOUR_TOKEN"' in text
    assert "Email for a beta token" in text


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
