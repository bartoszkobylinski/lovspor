"""``lovspor ops alert-target``: is the failure alert's webhook set? (issue #671).

``sync-units.sh`` asks this command instead of parsing ``alert.env`` itself, so
its verdict is the alert reader's own (``load_alert_config``): a shell copy of
the reader diverged twice in review (an authority-less URL, an upper-case
scheme). Only the file is read — the deploy reports ``/etc/lovspor/alert.env``,
not whatever the operator's shell exports.
"""

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.ops.alert import load_alert_config
from lovspor.ops.errors import AlertConfigError, AlertEnvUnreadableError

TOPIC = "lovspor-private-topic"
WEBHOOK = f"https://ntfy.example.org/{TOPIC}"


def _env(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "alert.env"
    path.write_text(text, encoding="utf-8")
    return path


def _target(env_file: Path) -> tuple[int, str, str]:
    result = CliRunner().invoke(app, ["ops", "alert-target", "--env-file", str(env_file)])
    return result.exit_code, result.stdout, result.stderr


class TestAlertTarget:
    def test_a_set_webhook_is_configured_and_never_printed(self, tmp_path: Path) -> None:
        code, out, err = _target(_env(tmp_path, f"LOVSPOR_ALERT_WEBHOOK={WEBHOOK}\n"))

        assert code == 0
        assert out == "alert target: configured\n"
        assert TOPIC not in out + err

    def test_an_empty_webhook_is_not_configured_and_names_the_file(self, tmp_path: Path) -> None:
        env_file = _env(tmp_path, "LOVSPOR_ALERT_WEBHOOK=\nLOVSPOR_ALERT_FORMAT=text\n")

        code, out, err = _target(env_file)

        assert code == 3
        assert out == "alert target: not configured\n"
        assert str(env_file) in err
        assert "LOVSPOR_ALERT_WEBHOOK" in err

    def test_an_absent_file_is_not_configured(self, tmp_path: Path) -> None:
        code, out, _ = _target(tmp_path / "absent.env")

        assert (code, out) == (3, "alert target: not configured\n")

    def test_a_webhook_the_alert_would_refuse_is_not_configured_with_the_reason(
        self, tmp_path: Path
    ) -> None:
        code, out, err = _target(_env(tmp_path, f"LOVSPOR_ALERT_WEBHOOK=ftp://{TOPIC}/x\n"))

        assert code == 3
        assert out == "alert target: not configured (refused by `lovspor ops alert`)\n"
        assert str(tmp_path / "alert.env") in err
        assert TOPIC not in out + err

    def test_a_format_the_alert_would_refuse_is_not_configured(self, tmp_path: Path) -> None:
        text = f"LOVSPOR_ALERT_WEBHOOK={WEBHOOK}\nLOVSPOR_ALERT_FORMAT=xml\n"

        code, out, err = _target(_env(tmp_path, text))

        assert code == 3
        assert out == "alert target: not configured (refused by `lovspor ops alert`)\n"
        assert TOPIC not in out + err

    def test_an_unreadable_file_is_unknown_not_configured(self, tmp_path: Path) -> None:
        if os.geteuid() == 0:
            pytest.skip("root reads a mode-000 file")
        env_file = _env(tmp_path, f"LOVSPOR_ALERT_WEBHOOK={WEBHOOK}\n")
        env_file.chmod(0o000)

        code, out, err = _target(env_file)

        assert code == 3
        assert out.startswith("alert target: unknown (")
        assert str(env_file) in err
        assert TOPIC not in out + err

    def test_the_callers_environment_does_not_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOVSPOR_ALERT_WEBHOOK", WEBHOOK)

        code, out, _ = _target(_env(tmp_path, "LOVSPOR_ALERT_WEBHOOK=\n"))

        assert (code, out) == (3, "alert target: not configured\n")

    def test_the_callers_format_does_not_override_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOVSPOR_ALERT_FORMAT", "xml")
        env_file = _env(tmp_path, f"LOVSPOR_ALERT_WEBHOOK={WEBHOOK}\nLOVSPOR_ALERT_FORMAT=json\n")

        code, out, err = _target(env_file)

        assert (code, out, err) == (0, "alert target: configured\n", "")

    def test_a_directory_is_reported_as_unreadable_even_as_root(self, tmp_path: Path) -> None:
        """The reader's OSError must become exit 3 rather than a CLI traceback."""
        code, out, err = _target(tmp_path)

        assert code == 3
        assert out.startswith("alert target: unknown (")
        assert str(tmp_path) in out
        assert "run as root" in out
        assert str(tmp_path) in err
        assert "is unreadable" in err

    @pytest.mark.parametrize("url", ["https://[secret/x", "https://secret\uff0fhost/private-topic"])
    def test_an_unparseable_url_is_reported_as_refused_without_secrets(
        self, tmp_path: Path, url: str
    ) -> None:
        env_file = _env(tmp_path, f"LOVSPOR_ALERT_WEBHOOK={url}\n")

        code, out, err = _target(env_file)

        assert code == 3
        assert out == "alert target: not configured (refused by `lovspor ops alert`)\n"
        assert str(env_file) in err
        assert "secret" not in out + err
        assert "private-topic" not in out + err


class TestTheReaderRefusesWithItsOwnError:
    """A URL ``urlsplit`` cannot parse raised a bare ``ValueError`` out of
    ``load_alert_config``: the alert unit died on a traceback instead of the
    named configuration error, and ``alert-target`` would have crashed too."""

    @pytest.mark.parametrize(
        "url", ["https://[::1/x", "http://[secret/x", "https://ex\uff0fample/x"]
    )
    def test_an_unparseable_url_is_an_alert_config_error(self, tmp_path: Path, url: str) -> None:
        env_file = _env(tmp_path, f"LOVSPOR_ALERT_WEBHOOK={url}\n")

        with pytest.raises(AlertConfigError, match="LOVSPOR_ALERT_WEBHOOK") as info:
            load_alert_config({}, env_file)

        assert "secret" not in str(info.value)

    def test_an_unreadable_file_has_its_own_error(self, tmp_path: Path) -> None:
        with pytest.raises(AlertEnvUnreadableError, match="cannot read"):
            load_alert_config({}, tmp_path)
