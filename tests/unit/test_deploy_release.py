"""The droplet's release wrapper, unit and runbook, as repository content (ADR-0014 Decision 6).

``publish-release.sh`` keeps only what bash must keep — the lock, the
environment, the identity split — and drives ``lovspor release`` in the
one order the ADR fixes. These tests read the files, so a wrapper that
drifts from the package's contract fails a test rather than a droplet.
"""

import os
import re
import subprocess
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


def _function(code: str, name: str) -> str:
    """One shell function's body, from its opening line to the next top-level one."""
    start = code.index(f"{name}() {{")
    end = code.index("\n}\n", start)
    return code[start:end]


def _dispatcher(code: str) -> str:
    match = re.search(r'^case "\$\{1:-\}" in\n.*?^esac$', code, re.DOTALL | re.MULTILINE)
    assert match is not None, "publish-release.sh no longer ends in a dispatcher"
    return match.group(0)


# The dispatcher is bash, so it is tested by running it — with every worker
# it calls replaced by an echo, so nothing builds, reloads or touches a
# droplet and the test asserts the routing alone.
_STUBS = """
die() { printf 'die: %s\\n' "$*" >&2; exit 1; }
publish() { printf 'publish %s\\n' "$*"; }
migrate() { printf 'migrate %s\\n' "$*"; }
control() { printf 'control %s\\n' "$*"; }
"""


def _dispatch(*argv: str) -> subprocess.CompletedProcess[str]:
    dispatcher = _dispatcher(_code(_SCRIPT.read_text(encoding="utf-8")))
    script = f"set -euo pipefail\n{_STUBS}\n{dispatcher}\n"
    return subprocess.run(
        ["bash", "-c", script, "publish-release.sh", *argv],
        check=False,
        capture_output=True,
        text=True,
    )


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
        assert "--migrate-rollback) control migrate --rollback" in code
        assert "--retire) control migrate --retire" in code
        assert "--migrate)" in code

    def test_the_first_migration_builds_without_asking_what_is_live(self) -> None:
        """ADR-0014 Migration: on a pre-envelope box the admin socket does not
        exist yet, so `release live` — which reads Caddy's running configuration
        over it — cannot answer. The build is told there is no live release, and
        the preflight of `release migrate` does the equivalent check over TCP."""
        migrate = _function(_code(_SCRIPT.read_text(encoding="utf-8")), "migrate")

        assert 'build_as_build_user "$ref" none' in migrate
        assert "control live" not in migrate
        assert 'control migrate "$release_id"' in migrate

    def test_the_migration_is_never_a_phase_of_an_ordinary_publish(self) -> None:
        publish = _function(_code(_SCRIPT.read_text(encoding="utf-8")), "publish")

        assert "migrate" not in publish

    def test_migrate_builds_head_by_default_and_the_named_commit_with_ref(self) -> None:
        assert _dispatch("--migrate").stdout == "migrate HEAD\n"
        assert _dispatch("--migrate", "--ref", "abc1234").stdout == "migrate abc1234\n"

    def test_migrate_refuses_a_ref_without_a_commit_and_an_unknown_word(self) -> None:
        missing = _dispatch("--migrate", "--ref")
        unknown = _dispatch("--migrate", "--now")

        assert missing.returncode == 1 and "--ref needs a commit" in missing.stderr
        assert unknown.returncode == 1 and "usage" in unknown.stderr

    def test_the_rollback_and_the_retire_reach_the_command_that_owns_them(self) -> None:
        """`--retire` deletes the pre-envelope trees, the only way back from the
        first migration, so it is its own run and never part of one."""
        assert _dispatch("--migrate-rollback").stdout == "control migrate --rollback\n"
        assert _dispatch("--retire").stdout == "control migrate --retire\n"

    def test_the_ordinary_entry_points_still_route_where_they_did(self) -> None:
        assert _dispatch().stdout == "publish \n"
        assert _dispatch("--ref", "abc1234").stdout == "publish abc1234\n"
        assert _dispatch("--prune").stdout == "control prune\n"
        assert _dispatch("--reconcile", "--complete").stdout == "control reconcile --complete\n"
        assert _dispatch("--rollback").stdout == "control rollback\ncontrol prune\n"
        assert _dispatch("--nope").returncode == 1

    def test_the_usage_line_names_every_entry_point(self) -> None:
        text = _SCRIPT.read_text(encoding="utf-8")
        header = text[: text.index("set -euo pipefail")]

        for phrase in (
            "publish-release.sh --migrate",
            "publish-release.sh --migrate-rollback",
            "publish-release.sh --retire",
        ):
            assert phrase in header, phrase
        assert "--migrate [--ref <commit>]" in _code(text)


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
        # The hand-written pages are gone from the deploy tree: the site the
        # release serves is built, and the old pages survive only as the
        # fixtures the generator's golden comparisons read.
        assert not (_DEPLOY / "site").exists()

    def test_the_site_is_documented_as_part_of_the_release_not_an_rsync(self) -> None:
        text = _README.read_text(encoding="utf-8")

        # ADR-0014 Decision 6: the site is built and released with the corpus as
        # one envelope; an rsync into a live root is the mixed snapshot the
        # envelope exists to prevent, so the shortcut is gone from the runbook.
        assert "## The site is part of the release" in text
        assert "lovspor build-site" in text
        # Public SSH is firewalled off the droplet; documenting the public IPv4
        # here sends the reader into a port-22 timeout, which is exactly how this
        # deploy step failed the first time it was run.
        assert "TAILSCALE" in text
        assert "lovspor-observatory" in text

    def test_the_operations_doc_points_at_the_adr(self) -> None:
        text = _OPERATIONS.read_text(encoding="utf-8")

        assert "## Publishing the site: the release envelope (ADR-0014 Decision 6)" in text
        assert "lovspor release reconcile [--complete|--abandon]" in text
        assert "no symlink exists" in text
