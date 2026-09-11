"""The on-disk shape of one release envelope and of the marker (ADR-0014 Decision 6).

``<releases>/<release_content_id>/`` holds ``corpus/``, ``site/``,
``release.json`` (the record: the id, the ``release_key`` with its
components, the hashes of the two consumed inputs, the observation's
instant and observer, and the corpus manifest's summary — no wall-clock
of the builder) and ``release.caddy``, the fragment the host's
Caddyfile imports: one ``vars lovspor_release <id>`` directive so the
running configuration can name its release, the ``@corpus`` handle
rooted at the immutable ``corpus/`` with that tree's own redirect map,
and the catch-all rooted at ``site/``. Every path in the fragment is
absolute and names the one directory the envelope will occupy after
the rename, so a configuration composed from it is one release, never
a mix.

``<releases>/ACTIVE`` is M, the durable marker: ``{active, previous}``
by id, written only by atomic rename. ``previous`` lives in the marker,
never in directory order, so a staged release that never went live
cannot masquerade as the rollback target.
"""

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from lovspor.atomic_io import atomic_write_bytes, atomic_write_text
from lovspor.publish.companion import companion_json_bytes
from lovspor.release.errors import IncompleteEnvelopeError
from lovspor.site.fingerprint import ReleaseKey

CORPUS_DIR = "corpus"
SITE_DIR = "site"
RECORD_NAME = "release.json"
FRAGMENT_NAME = "release.caddy"
MARKER_NAME = "ACTIVE"
WORLD_READABLE = 0o644
"""What Caddy's own user must read — the marker, the active fragment — whatever root's umask."""
BUILD_PREFIX = ".build-"
"""A name that is never a valid ``release_content_id``: the dot prefix."""
RELEASE_VAR = "lovspor_release"
"""The ``vars`` name the fragment leaves in the adapted configuration."""
CORPUS_PATHS = (
    "/lov",
    "/lov/*",
    "/forskrift",
    "/forskrift/*",
    "/sitemap.xml",
    "/sitemaps/*",
    "/robots.txt",
    "/site-manifest.json",
)
"""The ``@corpus`` matcher of ADR-0013, plus the manifest the site consumed."""

_RELEASE_ID = re.compile(r"^[0-9a-f]{64}$")
_VARS_LINE = re.compile(rf"^vars {RELEASE_VAR} (\S+)$", re.MULTILINE)
_PATH_LINE = re.compile(r"^\s*(?:root \* |import )(\S+)$", re.MULTILINE)


class CorpusSummary(BaseModel):
    """ADR-0013's ``site-manifest.json``, summarised: what the corpus tree is."""

    model_config = ConfigDict(frozen=True)

    corpus_commit: str
    corpus_commit_time: str
    engine_version: str
    documents: int


class ReleaseRecord(BaseModel):
    """``release.json``: what the envelope is, by id, key and consumed inputs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"]
    release_content_id: str
    release_key: ReleaseKey
    site_manifest_sha256: str
    capability_sha256: str
    observed_at: str
    observer: str
    corpus: CorpusSummary


class Marker(BaseModel):
    """``ACTIVE``: the release made live last, and the one it replaced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    active: str
    previous: str | None

    @field_validator("active", "previous")
    @classmethod
    def _release_ids_only(cls, value: str | None) -> str | None:
        # The marker names finalized release directories by their content id —
        # never a path, so a foreign value cannot steer prune or rollback.
        if value is not None and not is_release_id(value):
            raise ValueError(f"not a release id: {value!r}")
        return value


def is_release_id(name: str) -> bool:
    return _RELEASE_ID.match(name) is not None


def is_build_dir(path: Path) -> bool:
    return path.name.startswith(BUILD_PREFIX) and path.is_dir()


def release_dir(releases: Path, content_id: str) -> Path:
    return releases / content_id


def fragment_text(release_root: Path, content_id: str) -> str:
    """The fragment naming ``release_root`` — the envelope's final, absolute directory."""
    root = release_root.as_posix()
    paths = " ".join(CORPUS_PATHS)
    return (
        f"# lovspor release {content_id} — written by `lovspor release build`"
        " (ADR-0014 Decision 6). Do not edit.\n"
        "# Imported into the site block by the host's Caddyfile. Every path below names one\n"
        "# immutable release directory, so the configuration is one release, never a mix.\n"
        f"vars {RELEASE_VAR} {content_id}\n"
        f"@lovspor_corpus path {paths}\n"
        "handle @lovspor_corpus {\n"
        f"\timport {root}/{CORPUS_DIR}/redirects*.caddy\n"
        f"\troot * {root}/{CORPUS_DIR}\n"
        "\tfile_server\n"
        "}\n"
        "handle {\n"
        f"\troot * {root}/{SITE_DIR}\n"
        "\tfile_server\n"
        "}\n"
    )


def fragment_release_id(text: str) -> str | None:
    """The id the fragment's ``vars`` directive carries, if it carries exactly one."""
    found = _VARS_LINE.findall(text)
    if len(found) != 1 or not is_release_id(found[0]):
        return None
    return str(found[0])


def fragment_paths(text: str) -> tuple[str, ...]:
    """Every ``root`` and ``import`` path the fragment names."""
    return tuple(_PATH_LINE.findall(text))


def is_complete(root: Path) -> bool:
    """Both trees, the record and the fragment are present."""
    return (
        (root / CORPUS_DIR).is_dir()
        and (root / SITE_DIR).is_dir()
        and (root / RECORD_NAME).is_file()
        and (root / FRAGMENT_NAME).is_file()
    )


def missing_parts(root: Path) -> tuple[str, ...]:
    """What ``is_complete`` found absent, for the named error."""
    parts = (
        (CORPUS_DIR + "/", (root / CORPUS_DIR).is_dir()),
        (SITE_DIR + "/", (root / SITE_DIR).is_dir()),
        (RECORD_NAME, (root / RECORD_NAME).is_file()),
        (FRAGMENT_NAME, (root / FRAGMENT_NAME).is_file()),
    )
    return tuple(name for name, present in parts if not present)


def read_record(root: Path) -> ReleaseRecord:
    """The envelope's record, or :class:`IncompleteEnvelopeError` naming what is wrong."""
    path = root / RECORD_NAME
    try:
        return ReleaseRecord.model_validate_json(path.read_bytes())
    except OSError as error:
        message = f"{root.name}: {RECORD_NAME} is unreadable: {error}"
        raise IncompleteEnvelopeError(message) from error
    except ValidationError as error:
        message = f"{root.name}: {RECORD_NAME} is not a release record"
        raise IncompleteEnvelopeError(message) from error


def record_bytes(record: ReleaseRecord | Marker) -> bytes:
    """The record's — or the marker's — bytes: sorted keys, UTF-8, one trailing newline."""
    return companion_json_bytes(record.model_dump(mode="json"))


def write_record(root: Path, record: ReleaseRecord) -> None:
    """Written by replace, never in place: a hard-linked file shares its inode."""
    atomic_write_bytes(root / RECORD_NAME, record_bytes(record))


def write_fragment(root: Path, text: str) -> None:
    atomic_write_text(root / FRAGMENT_NAME, text)


def read_fragment(root: Path) -> str:
    try:
        return (root / FRAGMENT_NAME).read_text(encoding="utf-8")
    except OSError as error:
        message = f"{root.name}: {FRAGMENT_NAME} is unreadable: {error}"
        raise IncompleteEnvelopeError(message) from error


def read_marker(releases: Path) -> Marker | None:
    """M, or ``None`` when nothing has ever been made live."""
    path = releases / MARKER_NAME
    if not path.exists():
        return None
    try:
        return Marker.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as error:
        raise IncompleteEnvelopeError(f"{path} is not a marker: {error}") from error


def write_marker(releases: Path, marker: Marker) -> None:
    """M by atomic rename, never in place; the record's serialisation."""
    atomic_write_bytes(releases / MARKER_NAME, record_bytes(marker), mode=WORLD_READABLE)
