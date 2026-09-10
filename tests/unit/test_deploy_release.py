"""The droplet's release wrapper, unit and runbook, as repository content (ADR-0014 Decision 6).

``publish-release.sh`` keeps only what bash must keep — the lock, the
environment, the identity split — and drives ``lovspor release`` in the
one order the ADR fixes. These tests read the files, so a wrapper that
drifts from the package's contract fails a test rather than a droplet.
"""

import os
import re
from pathlib import Path

_DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "digitalocean"
_SCRIPT = _DEPLOY / "publish-release.sh"
_UNIT = _DEPLOY / "lovspor-publish.service"
_DRIFT = _DEPLOY / "lovspor-site-drift.service"
_README = _DEPLOY / "README.md"
_CADDYFILE = _DEPLOY / "Caddyfile"
_OPERATIONS = _DEPLOY.parents[1] / "docs" / "operations.md"


def _directive(text: str, name: str) -> list[str]:
    return re.findall(rf"^{re.escape(name)}=(.*)$", text, flags=re.MULTILINE)


def _code(text: str) -> str:
    """The script without its comments."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


class TestWrapper:
    def test_is_executable_strict_and_locked(self) -> None:
        text = _SCRIPT.read_text(encoding="utf-8")

        assert os.access(_SCRIPT, os.X_OK)
        assert "set -euo pipefail" in text
        assert re.search(r"^flock -n 9 \|\| die", text, re.MULTILINE)

    def test_drives_the_commands_in_the_adr_order(self) -> None:
        code = _code(_SCRIPT.read_text(encoding="utf-8"))
        publish = code[code.index("publish() {") : code.index("case ")]

        calls = re.findall(r"control (live|commit|prune)", publish)
        assert calls == ["live", "commit", "prune", "live"]
        assert publish.index("control live") < publish.index("build_as_build_user")
        assert publish.index("build_as_build_user") < publish.index("control commit")
        assert publish.index("control commit") < publish.index("control prune")

    def test_only_the_build_drops_to_the_build_user(self) -> None:
        code = _code(_SCRIPT.read_text(encoding="utf-8"))

        sudo_lines = [line for line in code.splitlines() if "sudo -u" in line]
        assert sudo_lines and all('"${args[@]}"' in line for line in sudo_lines)
        assert "args=(release build" in code
        assert 'control() { "$LOVSPOR" release "$@"; }' in code

    def test_the_probe_credential_reaches_the_build_user_on_stdin_only(self) -> None:
        code = _code(_SCRIPT.read_text(encoding="utf-8"))

        assert '--probe-token-file /dev/stdin <"$token"' in code
        assert 'token="${CREDENTIALS_DIRECTORY:-}/site-probe"' in code
        assert "LOVSPOR_PROBE_TOKEN_FILE" not in code
        assert "cp " not in code and "install -m" not in code

    def test_exports_the_environment_with_the_adr_defaults(self) -> None:
        code = _code(_SCRIPT.read_text(encoding="utf-8"))

        for name, default in (
            ("LOVSPOR_RELEASES_ROOT", "/var/www/lovspor-releases"),
            ("LOVSPOR_CADDYFILE", "/etc/caddy/Caddyfile"),
            ("LOVSPOR_RELEASE_FRAGMENT", "/etc/caddy/lovspor-release.caddy"),
            ("LOVSPOR_CADDY_ADMIN", "unix//run/caddy/admin.sock"),
        ):
            assert f'export {name}="${{{name}:-{default}}}"' in code
        assert "export LOVSPOR_DOMAIN" in code
        assert "/etc/default/caddy-lovspor" in code

    def test_no_symlink_no_rsync_no_in_place_switch(self) -> None:
        code = _code(_SCRIPT.read_text(encoding="utf-8"))

        for forbidden in ("lovspor-current", "rsync", "ln -s", "mv -T", "readlink", "rm -rf"):
            assert forbidden not in code, forbidden

    def test_the_operators_entry_points(self) -> None:
        code = _code(_SCRIPT.read_text(encoding="utf-8"))

        assert "--rollback) control rollback" in code
        assert '--reconcile) shift; control reconcile "$@"' in code
        assert "--prune) control prune" in code
        assert '--ref) [ -n "${2:-}" ] || die' in code


class TestUnit:
    def test_runs_the_wrapper_as_root_off_the_corpus_fetch(self) -> None:
        text = _UNIT.read_text(encoding="utf-8")

        assert _directive(text, "ExecStart") == [
            "/opt/lovspor/app/deploy/digitalocean/publish-release.sh"
        ]
        assert _directive(text, "User") == ["root"]
        assert _directive(text, "Type") == ["oneshot"]
        assert "lovspor-fetch-corpus.service" in _directive(text, "Conflicts")

    def test_receives_the_probe_credential_like_the_drift_unit(self) -> None:
        text = _UNIT.read_text(encoding="utf-8")
        drift = _DRIFT.read_text(encoding="utf-8")

        assert _directive(text, "LoadCredential") == _directive(drift, "LoadCredential")
        assert _directive(text, "EnvironmentFile") == ["-/etc/default/caddy-lovspor"]

    def test_names_the_envelope_not_the_symlink(self) -> None:
        text = _UNIT.read_text(encoding="utf-8")

        assert "symlink" not in text
        assert "envelope" in _directive(text, "Description")[0]


class TestRunbook:
    def test_the_readme_documents_the_commands_and_the_triple(self) -> None:
        text = _README.read_text(encoding="utf-8")

        for phrase in (
            "lovspor release live",
            "lovspor release build",
            "lovspor release commit",
            "publish-release.sh --rollback",
            "publish-release.sh --reconcile",
            "publish-release.sh --prune",
            "/var/www/lovspor-releases/ACTIVE",
            "/etc/caddy/lovspor-release.caddy",
            "unix//run/caddy/admin.sock",
        ):
            assert phrase in text, phrase
        assert "readlink -f /var/www/lovspor-current" not in text
        assert "rsync -av --delete deploy/digitalocean/site/" not in text

    def test_the_caddyfile_imports_the_fragment_and_the_readme_says_who_installs_it(self) -> None:
        """The switch has happened in this repository, so the runbook must stop
        calling it the next PR — and must stop telling the operator to install
        this Caddyfile by hand, which on a pre-envelope box moves the admin
        endpoint to a socket nothing is configured to reach."""
        text = _README.read_text(encoding="utf-8")
        caddyfile = _CADDYFILE.read_text(encoding="utf-8")

        assert "LOVSPOR_RELEASE_FRAGMENT" in text
        assert "does the Caddyfile import the fragment?" in text
        assert "lovspor release migrate" in text
        assert "the PR after this one" not in text
        assert "install -m644 /opt/lovspor/app/deploy/digitalocean/Caddyfile" not in text
        assert "LOVSPOR_RELEASE_FRAGMENT" in caddyfile
        assert "lovspor-current" not in caddyfile
        assert (_DEPLOY / "site" / "observatory" / "index.html").is_file()

    def test_the_operations_doc_points_at_the_adr(self) -> None:
        text = _OPERATIONS.read_text(encoding="utf-8")

        assert "## Publishing the site: the release envelope (ADR-0014 Decision 6)" in text
        assert "lovspor release reconcile [--complete|--abandon]" in text
        assert "no symlink exists" in text
