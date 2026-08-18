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


class TemporalDerivationError(ParseError):
    """A temporal layer cannot be derived without losing or inventing facts."""


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


class UnsupportedSidecarVersionError(LovsporError):
    """A sidecar is stored in a format version this engine does not read.

    Deliberately NOT a ``ValueError``: the search path skips corrupt
    sidecars per-file, and an unsupported version folded into that skip
    would silently shrink the searched corpus (ADR-0005 §3's
    silent-partial-recall failure). An unsupported version means the
    reader is behind the corpus — the fix is updating the engine, never
    ignoring the file.
    """


class MassReembedError(LovsporError):
    """A keyed sync selected more embedding-input repair than the guard allows.

    ADR-0006's mandatory fail-closed guard: the input-hash repair population
    is measured deterministically — document count/fraction AND token
    workload — before any provider call. A wholly unannotated corpus, a broad
    transformation drift, or mass old-writer field-stripping all land here
    instead of silently spending the embedding budget. When the large repair
    is genuinely intended, re-run with the explicit operator override.
    """


class MassRemovalError(LovsporError):
    """A sync would remove more of a dataset than the safety threshold allows.

    A valid-but-empty or truncated upstream tarball makes every document
    in that dataset look removed. Rather than delete them all (and let the
    scheduled workflow auto-push the wipe), the sync aborts. When a large
    removal is genuinely correct, re-run with a higher removal ratio.
    """


class ObservatoryError(LovsporError):
    """Base for the local-law temporal observatory (ADR-0010).

    A separate branch of the hierarchy because the observatory is a separate
    collection trust domain: it may fail, retry and hold uncertain material
    without any of that reaching the deterministic central pipeline.
    """


class StorageBoundaryError(ObservatoryError):
    """Observatory storage was pointed inside a repository it must stay out of.

    ADR-0010 §5 places raw observed artifacts and the observation log outside
    the engine repository and outside the public ``lovverk`` corpus: observed
    material is evidence that specific bytes were retrievable, not law, and
    nothing promotes it into a published corpus by accident of where it was
    written. Enforced with a path check rather than documented, because a
    convention is not a boundary.
    """


class LogIntegrityError(ObservatoryError):
    """The observation log or its blob store is not in an auditable state.

    Raised for a torn or malformed log line, and for a payload whose bytes do
    not hash to the SHA-256 its record claims. Both mean the append-only
    evidence is untrustworthy at that point, which ADR-0010 §7 treats as a
    failure to surface rather than a record to skip.
    """


class TombstonedArtifactError(ObservatoryError):
    """Capture tried to re-store bytes that a tombstone retired.

    A tombstone under ADR-0010 §7 records a removal made on legal, privacy or
    comparable grounds. The source usually keeps serving the same bytes
    afterwards, so an ordinary re-crawl would restore exactly what was
    removed — silently, and with a fresh observation record making it look
    routine. Capture is refused instead; putting the bytes back has to be a
    deliberate act, not a side effect of the next crawl.
    """


class SourceNotActivatedError(ObservatoryError):
    """A source was used for capture without a recorded access-policy check.

    ADR-0010 §4 separates eligibility from activation: an official municipal
    or fylkeskommune site is an eligible capture source, but fetching it
    requires a recorded per-source check of ``robots.txt``, site terms and
    crawl constraints.
    """
