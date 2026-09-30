"""Exceptions of the unit-failure alert (issue #478).

No message here ever carries the webhook URL: it is a bearer secret for
most targets (an ntfy topic, a Slack or Discord hook path), and these
messages end up in the journal.
"""

from lovspor.errors import ConfigError, LovsporError, NetworkError


class AlertConfigError(ConfigError):
    """The alert configuration cannot be read or is malformed."""


class AlertDeliveryError(NetworkError):
    """The webhook could not be reached or refused the alert."""


class UnitFactsError(LovsporError):
    """systemd could not be asked about the failed unit."""
