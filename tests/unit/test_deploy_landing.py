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
    assert (
        'install -m644 "$APP_DIR/deploy/digitalocean/site/index.html" /var/www/lovspor/index.html'
    ) in text


def test_landing_page_keeps_the_bearer_token_connection_instructions() -> None:
    text = _LANDING.read_text(encoding="utf-8")

    assert "https://lovspor.bartoszkobylinski.com/mcp" in text
    assert '--header "Authorization: Bearer YOUR_TOKEN"' in text
    assert "Email for a beta token" in text
