"""SMTP submission for ``innsyn send``: settings, the message, and the transport.

The transport is the one part of a send that talks to the world, so it is a
protocol: the run is given one, and a test gives it a fake. The real one is
SMTP submission with STARTTLS and the system's certificate verification; it
refuses a server that does not offer STARTTLS before any credential is sent,
and connects once per letter, so a long pause between letters never meets an
idle connection the server has already dropped.

Settings come from the environment (``.env`` is loaded by the CLI). The
password is held as a secret and never printed, logged or put in an error.
"""

import base64
import smtplib
import ssl
from collections.abc import Mapping
from datetime import datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, make_msgid
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from lovspor.errors import ConfigError, InnsynSendError
from lovspor.innsyn.sender import Sender

ENV_SMTP_HOST = "LOVSPOR_SMTP_HOST"
ENV_SMTP_PORT = "LOVSPOR_SMTP_PORT"
ENV_SMTP_USER = "LOVSPOR_SMTP_USER"
ENV_SMTP_PASSWORD = "LOVSPOR_SMTP_PASSWORD"  # noqa: S105 - the variable's name
SUBMISSION_PORT = 587
_TIMEOUT_SECONDS = 60.0


class SmtpSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    user: str = Field(min_length=1)
    password: SecretStr


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set (in the environment or .env)")
    return value


def _port(environ: Mapping[str, str]) -> int:
    raw = environ.get(ENV_SMTP_PORT, "").strip() or str(SUBMISSION_PORT)
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:  # noqa: PLR2004 - the TCP port range
        raise ConfigError(f"{ENV_SMTP_PORT} must be a port number, got {raw!r}")
    return int(raw)


def smtp_settings(environ: Mapping[str, str]) -> SmtpSettings:
    """Raises: ConfigError: a setting is missing or the port is not a port; named, never shown."""
    return SmtpSettings(
        host=_required(environ, ENV_SMTP_HOST),
        port=_port(environ),
        user=_required(environ, ENV_SMTP_USER),
        password=SecretStr(_required(environ, ENV_SMTP_PASSWORD)),
    )


class MailMessage(BaseModel):
    """One letter as a message: what goes in the headers and the body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sender: Sender
    to: str
    subject: str
    body: str
    date: datetime
    message_id: str

    def email(self) -> EmailMessage:
        """UTF-8 text/plain; non-ASCII header words are RFC 2047-encoded."""
        message = EmailMessage(policy=SMTP)
        message["From"] = Address(self.sender.name, addr_spec=self.sender.email)
        message["To"] = self.to
        message["Subject"] = self.subject
        message["Date"] = format_datetime(self.date)
        message["Message-ID"] = self.message_id
        message.set_content(self.body, charset="utf-8", cte="quoted-printable")
        return message


def message_id(sender: Sender) -> str:
    """A fresh Message-ID on the sender's own domain."""
    return make_msgid(idstring="innsyn", domain=sender.email.rpartition("@")[2])


@runtime_checkable
class Transport(Protocol):
    def deliver(self, message: EmailMessage) -> None:
        """Hand ``message`` to the server; return only once it is accepted.

        Raises:
            InnsynSendError: not accepted, or the server could not be reached securely.
        """


def tls_context() -> ssl.SSLContext:
    """The system trust store, with certificate and host name verification on."""
    return ssl.create_default_context()


class SmtpTransport:
    """SMTP submission with STARTTLS, one connection per message."""

    def __init__(self, settings: SmtpSettings, context: ssl.SSLContext | None = None) -> None:
        self._settings = settings
        self._context = tls_context() if context is None else context

    def deliver(self, message: EmailMessage) -> None:
        settings = self._settings
        try:
            smtp = smtplib.SMTP(settings.host, settings.port, timeout=_TIMEOUT_SECONDS)
        except OSError as exc:
            raise _failed(settings, exc) from None
        try:
            smtp.starttls(context=self._context)
            smtp.login(settings.user, settings.password.get_secret_value())
            smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise _failed(settings, exc) from None
        finally:
            _close(smtp)


def _failed(settings: SmtpSettings, exc: Exception) -> InnsynSendError:
    """The error the operator sees; ``from None`` at the raise keeps the original out of it."""
    reason = redact(f"{type(exc).__name__}: {exc}", settings)
    return InnsynSendError(f"{settings.host}:{settings.port}: {reason}")


def _spellings(settings: SmtpSettings) -> set[str]:
    """Every form a server reply or an exception could show the password in."""
    secret = settings.password.get_secret_value()
    raw = secret.encode()
    plain = b"\0" + settings.user.encode() + b"\0" + raw
    encoded = {base64.b64encode(value).decode() for value in (raw, plain)}
    return {secret, repr(secret)[1:-1], repr(raw)[2:-1], *encoded} - {""}


def redact(text: str, settings: SmtpSettings) -> str:
    """``text`` with the password, as typed, escaped or base64-encoded, replaced."""
    for spelling in sorted(_spellings(settings), key=len, reverse=True):
        text = text.replace(spelling, "[redacted]")
    return text


def _close(smtp: smtplib.SMTP) -> None:
    """End the session; a failure to say QUIT does not undo a message already accepted."""
    try:
        smtp.quit()
    except (smtplib.SMTPException, OSError):
        smtp.close()
