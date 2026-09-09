"""The publication toolchain and the two release identifiers (ADR-0014 Decision 4).

* ``toolchain_fingerprint`` (ADR:711, ADR:645-668): SHA-256 over the exact
  interpreter (implementation and ``major.minor.patch``), the resolved
  Jinja2 version and the SHA-256 of ``uv.lock`` at ``lovspor_commit``, its
  components recorded beside it. A renderer that participates in byte
  output is a determinism input, so the fingerprint is a component of
  ``release_key`` and byte-identity is compared only between equal
  fingerprints. The ``uv.lock`` hash is build provenance only — what runs
  is attested by ``environment_sha256`` (``lovspor.runtime_identity``).
* ``release_key`` (ADR:1003-1006): ``(corpus_commit, lovspor_commit,
  state_sha256, toolchain_fingerprint)`` — the semantic trigger. It names
  the meaning of a release, never its bytes.
* ``release_content_id`` (ADR:1012-1020): SHA-256 over the canonical
  listing of the release's served content, ``<release>/corpus/`` and
  ``<release>/site/`` — one line per regular file, release-relative path
  and the file's SHA-256, ``path\\0sha256\\n``, sorted bytewise, nothing
  else. ``site/site-facts.json`` is hashed in canonical JSON form with its
  own ``release_content_id`` field removed, because a file cannot carry
  its own hash — the only exclusion. ``write_release_content_id`` fills
  that placeholder in place, atomically; the release script wires it (PR 2).

Nothing here calls a clock.
"""

import hashlib
import importlib.metadata
import json
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.atomic_io import atomic_write_text
from lovspor.publish.companion import companion_json_bytes
from lovspor.runtime_identity import Interpreter, interpreter
from lovspor.site.capabilities import State, state_sha256
from lovspor.site.errors import SiteBuildError

_FACTS_NAME = "site/site-facts.json"
_ID_FIELD = "release_content_id"


class Toolchain(BaseModel):
    """The fingerprint with the components it was computed from."""

    model_config = ConfigDict(frozen=True)

    fingerprint: str
    interpreter: Interpreter
    jinja2_version: str
    uv_lock_sha256: str


class ReleaseKey(BaseModel):
    """The semantic trigger of a release: same key, same meaning."""

    model_config = ConfigDict(frozen=True)

    corpus_commit: str
    lovspor_commit: str
    state_sha256: str
    toolchain_fingerprint: str


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def toolchain_fingerprint(checkout: Path) -> Toolchain:
    """Fingerprint the environment this build runs in, against ``checkout``'s lock."""
    lock = checkout / "uv.lock"
    try:
        uv_lock_sha256 = _sha256(lock.read_bytes())
    except OSError as error:
        raise SiteBuildError(f"uv.lock not readable at the checkout: {error}") from error
    found = interpreter()
    jinja2_version = importlib.metadata.version("jinja2")
    canonical = f"interpreter\0{found.label}\njinja2\0{jinja2_version}\nuv.lock\0{uv_lock_sha256}\n"
    return Toolchain(
        fingerprint=_sha256(canonical.encode("utf-8")),
        interpreter=found,
        jinja2_version=jinja2_version,
        uv_lock_sha256=uv_lock_sha256,
    )


def release_key(
    corpus_commit: str, lovspor_commit: str, state: State, toolchain: Toolchain
) -> ReleaseKey:
    return ReleaseKey(
        corpus_commit=corpus_commit,
        lovspor_commit=lovspor_commit,
        state_sha256=state_sha256(state),
        toolchain_fingerprint=toolchain.fingerprint,
    )


def _facts_sha256(raw: bytes) -> str:
    facts: dict[str, object] = json.loads(raw)
    facts.pop(_ID_FIELD, None)
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256(canonical.encode("utf-8"))


def _listing(root: Path, prefix: str) -> Iterator[str]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        name = f"{prefix}/{path.relative_to(root).as_posix()}"
        digest = (
            _facts_sha256(path.read_bytes()) if name == _FACTS_NAME else _sha256(path.read_bytes())
        )
        yield f"{name}\0{digest}\n"


def release_content_id(corpus_dir: Path, site_dir: Path) -> str:
    """The provenance identity: the bytes of the two served trees, nothing else."""
    lines = sorted([*_listing(corpus_dir, "corpus"), *_listing(site_dir, "site")])
    return _sha256("".join(lines).encode("utf-8"))


def write_release_content_id(facts_path: Path, content_id: str) -> None:
    """Fill the ``release_content_id: null`` placeholder of ``site-facts.json``.

    Idempotent for the same id; any other value already present means the
    file is not the one this id was computed over, and is refused.
    """
    facts: dict[str, object] = json.loads(facts_path.read_bytes())
    if _ID_FIELD not in facts:
        raise SiteBuildError(f"{facts_path.name} carries no {_ID_FIELD} placeholder")
    if facts[_ID_FIELD] not in (None, content_id):
        raise SiteBuildError(f"{facts_path.name} already carries another {_ID_FIELD}")
    facts[_ID_FIELD] = content_id
    atomic_write_text(facts_path, companion_json_bytes(facts).decode("utf-8"))
