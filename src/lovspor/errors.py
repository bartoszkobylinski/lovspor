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


class RenderError(LovsporError):
    """Rendering produced output that lost source content.

    Distinct from ``ParseError`` (input was well-formed and parsed): the
    renderer dropped text that was present in the XML, so committing the
    output would publish an incomplete legal document.
    """


class ExtractionError(LovsporError):
    """Failed to safely extract or validate an archive."""


class ConfigError(LovsporError):
    """Misconfiguration: invalid env var, malformed config file, bad path."""
