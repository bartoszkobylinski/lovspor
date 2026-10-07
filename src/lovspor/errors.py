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


class CorpusNotFoundError(LovsporError):
    """Raised when the requested doc or corpus path is not present."""


class AmbiguousSlugError(CorpusNotFoundError):
    """A slug that more than one *current* record claims (issue #243).

    A subclass so every handler that turns ``CorpusNotFoundError`` into a
    tool error keeps doing so; distinct because the answer is different:
    the documents exist, and the slug alone cannot pick one. The message
    names every candidate. Picking a winner (the old first-wins index) hid
    one document from every slug lookup with no signal that it was there —
    the real corpus carries one such pair, ``bergverksordning-for-svalbard``,
    a lov and a forskrift.
    """


class LocalCorpusError(CorpusNotFoundError):
    """A file of the local-regulations dataset is absent or not its contracted shape.

    A subclass so every tool keeps reporting it as a corpus error; distinct
    from "no such regulation" because the regulation is listed and its files
    disagree with the listing (ADR-0016 Decision 3). Serving it anyway would
    serve text without the observation label it must carry.
    """


class LocalScopeError(LovsporError):
    """A capability asked of a local regulation that this engine does not serve for it.

    Local regulations are served opt-in and slice by slice (ADR-0016 5, 7); a
    parameter that is only defined for the central corpus is refused for a
    local document rather than silently answered from the central path.
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


class StorageUnavailableError(ObservatoryError):
    """The archive could not take a write: its root is gone, or a write under it failed.

    The archive lives on external storage (ADR-0010 §5), so a volume that goes
    away mid-run is an ordinary event. It is never answered by creating the
    root again: on 2026-10-04 the blob writer's ``mkdir(parents=True)`` walked
    up and tried to recreate ``/Volumes/T7`` under a running sweep, and on a
    writable parent the same walk would have started a second, partial archive
    (issue #534). The sweep ends on this error and says so.
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


class CorrectionRefusedError(ObservatoryError):
    """A correction of the observation log was refused before anything was written.

    ADR-0015 lets an operator correct a filed record's attribution only when
    the register already supports the corrected one, and never over a
    half-written correction that says something else. Refusing is the
    validator a hand edit of the log would have skipped.
    """


class SourceNotActivatedError(ObservatoryError):
    """A source was used for capture without a recorded access-policy check.

    ADR-0010 §4 separates eligibility from activation: an official municipal
    or fylkeskommune site is an eligible capture source, but fetching it
    requires a recorded per-source check of ``robots.txt``, site terms and
    crawl constraints.
    """


class RobotsUnreadableError(SourceNotActivatedError):
    """The live ``robots.txt`` could not be read inside the cleared domain.

    Raised by ``activate-source`` (issue #449) when the file is unreachable,
    answers 5xx, redirects off the cleared domain, or redirects more than five
    times. A subclass because the answer is the one every activation refusal
    gives: a crawl policy nobody could read is no evidence the rate is polite.
    """


class RateBelowCrawlDelayError(SourceNotActivatedError):
    """The recorded ``rate_limit_seconds`` is below the site's own ``Crawl-delay``.

    ``docs/operations.md`` requires the rate to honour the delay; until issue
    #449 that rested on a reviewer reading the file by eye.
    """


class AmbiguousSourceError(SourceNotActivatedError):
    """More than one activated source covers the host being fetched.

    A subclass, because every existing handler reads this exception as "do not
    fetch that" and that answer stays right. Distinct, because the two say
    different things to an operator and want different handling: an
    unactivated source is a step not yet taken, while this is a register that
    cannot say which authority publishes a host — and an archive whose whole
    claim is that specific bytes came from a named authority must not resolve
    that by sort order.

    It happened. `4202 Grimstad` carried `arendal.kommune.no`, the same domain
    as `4203 Arendal`, and the lowest id won every time: 5,980 observations of
    Arendal's site were filed under Grimstad, while Grimstad itself was never
    fetched once and every pass over it reported success (issue #215).
    """


class StaleSourceError(SourceNotActivatedError):
    """The register no longer answers for this URL the way the run bound it.

    A subclass for the same reason ``AmbiguousSourceError`` is one: every
    existing handler reads this family as "do not fetch that", and that answer
    stays right. Distinct, because this one is about *time* rather than about
    the register's shape — the row was good when the run bound itself to it and
    is not the row on disk now.

    It happened, and #215's fix did not cover it. `4202 Grimstad` carried
    `arendal.kommune.no`; the owner repaired the row on 2026-09-03 at 07:20:44.
    The sweep already running had loaded the register once, at the start, and
    ran for 141 hours — so it kept filing under the row it remembered, and 669
    further observations of Arendal's site landed under authority 4202 after
    the repair, taking the misattributed body from 5,980 to 7,872 (issue #221).

    Refusing is the recoverable outcome and writing is not: nothing in this
    engine rewrites ``authority_id``, and nothing appends a tombstone.
    """


class PromotionError(LovsporError):
    """Base for promoting observed local regulations into ``lovverk`` (ADR-0016).

    Its own branch: promotion reads the observatory's archive and writes a
    corpus, and a refusal here must never read as a capture or sync failure.
    """


class UnreadableSourceError(PromotionError):
    """Captured bytes that the extractor's readers cannot turn into text.

    Raised by a reader and turned into a counted hold by the extractor — the
    artifact stays in the archive, unpublished, never silently skipped.
    """


class PromotionRenderError(PromotionError):
    """A local regulation the renderer refuses to write as asked.

    The inputs disagree with each other (a content hash that is not the text's)
    or the output would break the dataset's contract (an NLOD mention).
    """


class PromotionRefusedError(PromotionError):
    """A promotion the command refuses to carry out, with the reason.

    Not a hold: a hold is a property of the source (its text, its identity)
    and is recorded and counted. A refusal is about the request — no human
    approval, an artifact the archive cannot name unambiguously, a corpus path
    that is not a ``lovverk`` checkout — and nothing is written or recorded.
    """


class DecisionLogError(PromotionError):
    """The promotion decision log does not read to its end.

    The log is append-only evidence of human decisions (ADR-0016 4c); a line
    skipped because it does not parse could be the ``reject`` that keeps an
    artifact out of the corpus.
    """


class ClassifierOutputError(PromotionError):
    """The classifier's output file does not read to its end (ADR-0016 4b, S8).

    A row skipped because it does not parse could be the one candidate a
    batch should have counted, so the file is refused whole, never read past.
    """
