"""``load_probe_token``: the probe credential read from a path, or from stdin as ``-``.

Issue #467: ``publish-release.sh`` hands the build user the root-only
systemd credential as stdin. Re-opening it by path (``/dev/stdin``) is
permission-checked against the root-owned file and fails; reading the
already-open fd 0 does not. These tests run a real interpreter with a real
file or pipe as its fd 0, the shape the deploy script produces.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lovspor.site.probe_credential import STDIN, load_probe_token

TOKEN = "lsp_probe-secret"

_LOAD = (
    "from pathlib import Path\n"
    "from lovspor.site.probe_credential import load_probe_token\n"
    "token, notice = load_probe_token(Path('-'))\n"
    "print(token.get_secret_value() if token else '', notice or '', sep='|')\n"
)


def _load_from_real_stdin(stdin: object, cwd: Path) -> tuple[str, str]:
    completed = subprocess.run(
        [sys.executable, "-c", _LOAD],
        stdin=stdin,  # type: ignore[arg-type]
        capture_output=True,
        text=True,
        cwd=cwd,
        check=True,
    )
    token, notice = completed.stdout.rstrip("\n").split("|")
    return token, notice


class TestStdin:
    def test_a_token_piped_on_stdin_is_read(self, tmp_path: Path) -> None:
        read, write = os.pipe()
        os.write(write, f"{TOKEN}\n".encode())
        os.close(write)
        with os.fdopen(read, "rb") as pipe:
            assert _load_from_real_stdin(pipe, tmp_path) == (TOKEN, "")

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 000 file by path anyway")
    def test_a_file_the_reader_cannot_open_by_path_is_read_from_its_open_fd(
        self, tmp_path: Path
    ) -> None:
        """The droplet's shape: fd 0 is open, the path behind it is not readable."""
        secret = tmp_path / "site-probe"
        secret.write_text(f"{TOKEN}\n", encoding="utf-8")
        with secret.open("rb") as handle:
            secret.chmod(0)
            try:
                assert not os.access(secret, os.R_OK)
                assert _load_from_real_stdin(handle, tmp_path) == (TOKEN, "")
            finally:
                secret.chmod(0o600)

    def test_dash_never_opens_a_file_named_dash(self, tmp_path: Path) -> None:
        (tmp_path / "-").write_text("lsp_the-wrong-one\n", encoding="utf-8")
        read, write = os.pipe()
        os.write(write, TOKEN.encode())
        os.close(write)
        with os.fdopen(read, "rb") as pipe:
            assert _load_from_real_stdin(pipe, tmp_path) == (TOKEN, "")

    def test_an_empty_stdin_is_recorded_not_fatal(self, tmp_path: Path) -> None:
        with Path(os.devnull).open("rb") as empty:
            token, notice = _load_from_real_stdin(empty, tmp_path)

        assert (token, notice) == ("", "probe credential empty: <stdin>")

    def test_an_undecodable_stdin_is_recorded_without_its_bytes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "malformed"
        source.write_bytes(b"lsp_secret-\xff")
        with source.open("rb") as handle:
            monkeypatch.setattr(sys, "stdin", handle)
            token, notice = load_probe_token(STDIN)

        assert token is None
        assert notice == "probe credential undecodable: <stdin>: not UTF-8"

    def test_a_closed_stdin_is_recorded_not_fatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "stdin", None)

        token, notice = load_probe_token(STDIN)

        assert token is None
        assert notice is not None
        assert notice.startswith("probe credential unreadable: <stdin>: ")


class TestPath:
    def test_a_path_is_still_read_as_a_file(self, tmp_path: Path) -> None:
        secret = tmp_path / "site-probe"
        secret.write_text(f"{TOKEN}\n", encoding="utf-8")

        token, notice = load_probe_token(secret)

        assert token is not None and token.get_secret_value() == TOKEN
        assert notice is None

    def test_an_unreadable_path_is_named_in_the_notice(self, tmp_path: Path) -> None:
        missing = tmp_path / "absent"

        token, notice = load_probe_token(missing)

        assert token is None
        assert notice == f"probe credential unreadable: {missing}: No such file or directory"
