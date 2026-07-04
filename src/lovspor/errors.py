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


class CorpusStateError(LovsporError):
    """The corpus git repo is in an unexpected state to start a sync.

    Currently raised when the worktree is dirty at sync start, which
    means a prior sync wrote the manifest (the change-detection source of
    truth) but crashed before committing it. Proceeding would read the
    uncommitted manifest, classify everything unchanged, and silently
    drop the work — so the sync aborts loudly instead.
    """
