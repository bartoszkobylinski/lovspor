"""Loading the probe credential, for every command that runs the probe (ADR-0014 Decision 4).

The secret is read from an explicit path, else from
``$CREDENTIALS_DIRECTORY/site-probe`` — the path systemd's
``LoadCredential=`` delivers — and from nowhere else. A secret that
cannot be loaded is the observer's failure, never fatal: the probe then
records step (b) ``unobserved, probe_credential_missing``, and the
command prints one line naming the path, never the content.
"""

import os
from pathlib import Path
from typing import NamedTuple

from pydantic import SecretStr

PROBE_CREDENTIAL_NAME = "site-probe"


class ProbeCredential(NamedTuple):
    """The secret, or ``None`` beside the one line saying why step (b) will be unobserved."""

    token: SecretStr | None
    notice: str | None


def probe_token_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    return Path(directory) / PROBE_CREDENTIAL_NAME if directory else None


def load_probe_token(explicit: Path | None) -> ProbeCredential:
    path = probe_token_path(explicit)
    if path is None:
        return ProbeCredential(None, "probe credential: none configured (step (b) unobserved)")
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        return ProbeCredential(None, f"probe credential unreadable: {path}: {error.strerror}")
    except UnicodeDecodeError:
        # An undecodable secret is a credential that cannot be loaded — an observer
        # failure to record, not a crash; its bytes never reach the message.
        return ProbeCredential(None, f"probe credential undecodable: {path}: not UTF-8")
    if not token:
        return ProbeCredential(None, f"probe credential empty: {path}")
    return ProbeCredential(SecretStr(token), None)
