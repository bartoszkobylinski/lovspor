"""Exception hierarchy for lovspor.

All recoverable failures inherit from `LovsporError` so callers can catch
the family without resorting to bare `Exception`.
"""


class LovsporError(Exception):
    """Base exception for all lovspor errors."""


class NetworkError(LovsporError):
    """Network-related failure: timeout, connection error, or HTTP 5xx."""


class ParseError(LovsporError):
    """Failed to parse upstream data (XML, JSON, or manifest)."""


class ExtractionError(LovsporError):
    """Failed to safely extract or validate an archive."""


class ConfigError(LovsporError):
    """Misconfiguration: invalid env var, malformed config file, bad path."""
