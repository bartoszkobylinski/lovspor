"""Issue #470: unreadable credentials must reach the loader, not fail CLI parsing."""

import os
from pathlib import Path

import click
import pytest
from typer.main import get_command

from lovspor.cli import app


@pytest.mark.parametrize(
    ("command_path", "required_args"),
    [
        (("release-probe",), ["--corpus", ".", "--out", "capabilities.json"]),
        (("site-drift-check",), []),
        (("release", "build"), ["--corpus", ".", "--live", "none"]),
    ],
)
@pytest.mark.parametrize("source", ["flag", "environment"])
def test_unreadable_probe_token_reaches_the_command(
    command_path: tuple[str, ...],
    required_args: list[str],
    source: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate the OS readability check even under root; parse without running probes."""
    credential = tmp_path / "site-probe"
    credential.write_text("lsp_secret-must-not-appear", encoding="utf-8")
    real_access = os.access

    def access(path: object, mode: int, **kwargs: object) -> bool:
        if path == str(credential) and mode == os.R_OK:
            return False
        return real_access(path, mode, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "access", access)
    # Prove this setup triggers the validation that issue #470 removes.
    with pytest.raises(click.BadParameter, match="not readable"):
        click.Path(readable=True).convert(str(credential), None, None)
    monkeypatch.delenv("LOVSPOR_PROBE_TOKEN_FILE", raising=False)
    args = list(required_args)
    if source == "flag":
        args += ["--probe-token-file", str(credential)]
    else:
        monkeypatch.setenv("LOVSPOR_PROBE_TOKEN_FILE", str(credential))

    command = get_command(app)
    for name in command_path:
        assert isinstance(command, click.Group)
        command = command.commands[name]

    with command.make_context(command_path[-1], args) as context:
        # Typer converts Click's string to Path when invoking the callback.
        assert context.params["probe_token_file"] == str(credential)
