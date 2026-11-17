"""Which committed sidecars have a known quantization provenance (#936).

Delta embedding carries a committed int8 row into a rebuilt document only
when the sidecar's scale is ``float32(max|v| / 127)`` of the floats of that
document's own inputs. Int8 bytes cannot prove that, so provenance is shown
by one of two records, and a sidecar matching neither is unknown: its
document is re-embedded in full and quantized from floats, as a rebuild is.

* The manifest record's ``embedding_sidecar_sha256``, which a keyed writer of
  this engine stamps with the bytes it wrote. A sidecar whose bytes differ
  from a stamped value is unknown, whatever else it matches.
* For an unstamped record, the audited legacy set in ``attested_sidecars.txt``:
  the SHA-256 of every sidecar committed at lovverk ``c310127e`` that the
  writer audit traced to the rebuild quantizer (directly, or through the
  bit-for-bit LSPE v2 cutover), paired with the ``embedding_input_hash`` its
  record carried, so a record re-annotated with other texts is not vouched
  for. The file names its audit. Keyed by content, so a sidecar the identity
  migration or a slug swap moves keeps it.

The stamp vouches for the scale; which texts the rows embed rests on
``embedding_input_hash``. A path that changes that hash without rewriting
the sidecar (``lovspor annotate``) clears the stamp.
"""

from functools import cache
from importlib import resources

from lovspor.embeddings.store import sidecar_sha256
from lovspor.errors import ConfigError

__all__ = ["attested_sidecars", "has_known_provenance", "parse_attested", "sidecar_sha256"]

ATTESTED_FILENAME = "attested_sidecars.txt"
_SHA256_HEX = 64


@cache
def attested_sidecars() -> frozenset[tuple[str, str]]:
    """The audited legacy sidecar digests shipped with the engine."""
    text = resources.files(__package__).joinpath(ATTESTED_FILENAME).read_text(encoding="utf-8")
    return parse_attested(text)


def parse_attested(text: str) -> frozenset[tuple[str, str]]:
    """``(sidecar digest, input hash)`` lines of the attested file; comments and blanks skipped.

    A line that is not two lowercase SHA-256 values is refused, so a damaged
    file can never widen what counts as known.
    """
    pairs: set[tuple[str, str]] = set()
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        fields = entry.split(" ")
        if len(fields) != 2 or not all(_is_sha256(field) for field in fields):  # noqa: PLR2004
            raise ConfigError(f"{ATTESTED_FILENAME}: not a digest pair: {entry[:140]!r}")
        pairs.add((fields[0], fields[1]))
    return frozenset(pairs)


def _is_sha256(value: str) -> bool:
    return len(value) == _SHA256_HEX and not value.strip("0123456789abcdef")


def has_known_provenance(stamped: str | None, digest: str, input_hash: str | None) -> bool:
    """A stamped record vouches only for its own bytes; else the audited pairs decide."""
    if stamped is not None:
        return stamped == digest
    return input_hash is not None and (digest, input_hash) in attested_sidecars()
