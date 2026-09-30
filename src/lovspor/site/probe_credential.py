"""Loading the probe credential, for every command that runs the probe (ADR-0014 Decision 4).

The secret is read from an explicit path, else from
``$CREDENTIALS_DIRECTORY/site-probe`` — the path systemd's
``LoadCredential=`` delivers — and from nowhere else. A secret that
cannot be loaded is the observer's failure, never fatal: the probe then
records step (b) ``unobserved, probe_credential_missing``, and the
command prints one line naming the path, never the content.

``-`` names stdin: the already-open fd 0 is read, and no path is opened.
``publish-release.sh`` hands the build user a root-only credential that way
(issue #467); re-opening it as ``/dev/stdin`` is permission-checked against
the root-owned file and fails.
"""

import errno
import os
import sys
from pathlib import Path
from typing import Annotated, NamedTuple

import typer
from pydantic import SecretStr

PROBE_CREDENTIAL_NAME = "site-probe"
STDIN = Path("-")
_STDIN_NAME = "<stdin>"

ProbeTokenFileOption = Annotated[
    Path | None,
    typer.Option(
        "--probe-token-file",
        envvar="LOVSPOR_PROBE_TOKEN_FILE",
        allow_dash=True,
        help="File holding the probe credential, or '-' for stdin (default: "
        "$CREDENTIALS_DIRECTORY/site-probe, as systemd LoadCredential= delivers it). "
        "Missing, empty or unreadable: step (b) is recorded unobserved with reason "
        "probe_credential_missing, never as a failure.",
    ),
]


class ProbeCredential(NamedTuple):
    """The secret, or ``None`` beside the one line saying why step (b) will be unobserved."""

    token: SecretStr | None
    notice: str | None


def probe_token_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    return Path(directory) / PROBE_CREDENTIAL_NAME if directory else None


def _read_stdin() -> str:
    stream = sys.stdin
    if stream is None:
        raise OSError(errno.EBADF, "stdin is closed")
    # The bytes, decoded as UTF-8 whatever the locale, like a path read.
    data: str | bytes = getattr(stream, "buffer", stream).read()
    return data.decode("utf-8") if isinstance(data, bytes) else data


def _read_secret(path: Path) -> str:
    if path == STDIN:
        return _read_stdin()
    return path.read_text(encoding="utf-8")


def load_probe_token(explicit: Path | None) -> ProbeCredential:
    path = probe_token_path(explicit)
    if path is None:
        return ProbeCredential(None, "probe credential: none configured (step (b) unobserved)")
    name = _STDIN_NAME if path == STDIN else str(path)
    try:
        token = _read_secret(path).strip()
    except OSError as error:
        return ProbeCredential(None, f"probe credential unreadable: {name}: {error.strerror}")
    except UnicodeDecodeError:
        # An undecodable secret is a credential that cannot be loaded — an observer
        # failure to record, not a crash; its bytes never reach the message.
        return ProbeCredential(None, f"probe credential undecodable: {name}: not UTF-8")
    if not token:
        return ProbeCredential(None, f"probe credential empty: {name}")
    return ProbeCredential(SecretStr(token), None)
