"""The unit-failure alert: its configuration, its message and its delivery (issue #478).

One webhook, set by the operator, so the target — ntfy, Discord, Slack, an
e-mail gateway — is the operator's choice, not the code's. Two body
formats cover them:

``text`` (default)
    The message as ``text/plain``, plus ntfy's ``Title``/``Priority``/``Tags``
    headers. ntfy publishes the body of a POST to a topic URL as the
    notification; any endpoint that takes a plain body does the same, and
    ignores the headers.
``json``
    ``{"text": ..., "content": ..., "unit": ..., ...}``: Slack incoming
    webhooks read ``text``, Discord webhooks read ``content``, and a
    generic receiver gets the facts as fields.

The message is bounded (``MESSAGE_CHARS``) below Discord's 2,000-character
content limit and ntfy's 4,096-byte message limit, keeping the newest
journal lines.
"""

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

from lovspor.ops.errors import AlertConfigError, AlertDeliveryError
from lovspor.release.caddy import environment_file_value

WEBHOOK_VAR = "LOVSPOR_ALERT_WEBHOOK"
FORMAT_VAR = "LOVSPOR_ALERT_FORMAT"
DEFAULT_ALERT_ENV = Path("/etc/lovspor/alert.env")
LINE_CHARS = 240
MESSAGE_CHARS = 1800
TIMEOUT_SECONDS = 15.0


class AlertFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


class AlertConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    webhook: SecretStr | None = None
    format: AlertFormat = AlertFormat.TEXT


class Alert(BaseModel):
    model_config = ConfigDict(frozen=True)

    unit: str
    host: str
    at: datetime
    result: str
    exit_status: int | None
    journal: tuple[str, ...]
    test: bool = False


def load_alert_config(environ: dict[str, str], env_file: Path) -> AlertConfig:
    """The process environment wins over ``env_file``; an empty value is unset.

    A missing file is an unconfigured alert, not an error: provisioning writes
    it commented out, and a box without it still runs every unit."""
    text = _read_env_file(env_file)
    webhook = _setting(environ, text, WEBHOOK_VAR)
    fmt = _setting(environ, text, FORMAT_VAR) or AlertFormat.TEXT.value
    return AlertConfig(webhook=_webhook(webhook), format=_format(fmt))


def _read_env_file(env_file: Path) -> str:
    try:
        return env_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as error:
        raise AlertConfigError(f"cannot read {env_file}: {error.strerror}") from error


def _setting(environ: dict[str, str], text: str, name: str) -> str | None:
    value = environ.get(name, "").strip() or (environment_file_value(text, name) or "").strip()
    return value or None


def _webhook(value: str | None) -> SecretStr | None:
    if value is None:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise AlertConfigError(f"{WEBHOOK_VAR} must be an http:// or https:// URL")
    return SecretStr(value)


def _format(value: str) -> AlertFormat:
    try:
        return AlertFormat(value.lower())
    except ValueError as error:
        allowed = ", ".join(f.value for f in AlertFormat)
        raise AlertConfigError(f"{FORMAT_VAR} must be one of {allowed}; got {value!r}") from error


def redact_url(url: str) -> str:
    """Scheme, host and port only: the path and any userinfo are the secret part."""
    try:
        parts = urlsplit(url)
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return "<redacted>"
    if not parts.scheme or not parts.hostname:
        return "<redacted>"
    return f"{parts.scheme}://{parts.hostname}{port}/…"


def title(alert: Alert) -> str:
    prefix = "[TEST] " if alert.test else ""
    return f"{prefix}lovspor: {alert.unit} failed on {alert.host}"


def render_text(alert: Alert) -> str:
    header = _header(alert)
    lines = [_cut(line) for line in alert.journal] or ["(no journal lines)"]
    omitted = 0
    while len(_compose(header, lines, omitted)) > MESSAGE_CHARS and len(lines) > 1:
        lines.pop(0)
        omitted += 1
    return _compose(header, lines, omitted)[:MESSAGE_CHARS]


def _header(alert: Alert) -> list[str]:
    status = "unknown" if alert.exit_status is None else str(alert.exit_status)
    return [
        f"{'[TEST] ' if alert.test else ''}lovspor unit failed: {alert.unit}",
        f"host: {alert.host}",
        f"time: {_utc(alert.at)}",
        f"result: {alert.result}",
        f"exit status: {status}",
        "last journal lines:",
    ]


def _compose(header: list[str], lines: list[str], omitted: int) -> str:
    note = [f"({omitted} older journal lines omitted)"] if omitted else []
    return "\n".join([*header, *note, *lines])


def _cut(line: str) -> str:
    return line if len(line) <= LINE_CHARS else line[: LINE_CHARS - 1] + "…"


def _utc(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def send_alert(alert: Alert, config: AlertConfig, client: httpx.Client) -> int:
    """POST the alert; return the HTTP status, or raise ``AlertDeliveryError``."""
    if config.webhook is None:
        raise AlertConfigError(f"{WEBHOOK_VAR} is not set")
    try:
        response = _post(client, config.webhook.get_secret_value(), alert, config.format)
    except httpx.HTTPError as error:
        # The exception text can quote the URL; its type is enough to act on.
        raise AlertDeliveryError(f"webhook unreachable: {type(error).__name__}") from error
    if not response.is_success:
        raise AlertDeliveryError(f"webhook answered HTTP {response.status_code}")
    return response.status_code


def _post(client: httpx.Client, url: str, alert: Alert, fmt: AlertFormat) -> httpx.Response:
    text = render_text(alert)
    if fmt is AlertFormat.JSON:
        return client.post(url, json=_json_body(alert, text), timeout=TIMEOUT_SECONDS)
    return client.post(
        url, content=text.encode("utf-8"), headers=_ntfy_headers(alert), timeout=TIMEOUT_SECONDS
    )


def _ntfy_headers(alert: Alert) -> dict[str, str]:
    return {
        "Content-Type": "text/plain; charset=utf-8",
        "Title": title(alert).encode("ascii", "replace").decode("ascii"),
        "Priority": "high",
        "Tags": "warning",
    }


def _json_body(alert: Alert, text: str) -> dict[str, Any]:
    return {
        "text": text,
        "content": text,
        "unit": alert.unit,
        "host": alert.host,
        "time": _utc(alert.at),
        "result": alert.result,
        "exit_status": alert.exit_status,
        "journal": [_cut(line) for line in alert.journal],
        "test": alert.test,
    }
