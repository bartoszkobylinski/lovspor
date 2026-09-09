"""Runtime identity: what a process actually runs (ADR-0014 Decision 4).

The running MCP process attests three components, each describing what
it runs rather than what a file says it should (ADR:768-790):

* ``tree_sha256`` — the hash of the installed ``lovspor`` package files
  excluding ``lovspor/site/`` (templates and site content are not the
  runtime), ``__pycache__/`` and ``*.pyc`` (artefacts of having run).
  Canonical form: one line per file, ``relative-path\\0sha256\\n``, sorted
  bytewise, hashed as UTF-8.
* ``environment_sha256`` — a canonical hash over the installed
  distributions the interpreter imports: normalised name, version and
  direct URL where one applies, enumerated through ``importlib.metadata``
  and restricted to ``lovspor``'s resolved dependency closure under the
  production selection — no dev group, no extra of ``lovspor`` itself.
  ``lovspor``'s own distribution is excluded: its code is what
  ``tree_sha256`` attests, and its editable-install URL would name a
  filesystem location, which no identity may do.
* ``interpreter`` — implementation and ``major.minor.patch``.

A lockfile is not the installed environment (ADR:778-790): nothing here
reads ``uv.lock``. The site build computes the *expected* identity from
the checkout with these same functions, so the comparison in the
capability document is between two values of one canonical form.

Core layer beside ``tool_surface.py``, on purpose: the ``/readyz``
attestation must compute this without importing site tooling.
"""

import hashlib
import importlib.metadata
import json
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict

_EXCLUDED_DIRS = frozenset({"site"})
_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[(?P<extras>[^\]]*)\])?"
)
_EXTRA_MARKER = re.compile(r"""extra\s*==\s*['"]([^'"]+)['"]""")


class Interpreter(BaseModel):
    """The exact interpreter: implementation name and ``major.minor.patch``."""

    model_config = ConfigDict(frozen=True)

    implementation: str
    version: str

    @property
    def label(self) -> str:
        return f"{self.implementation} {self.version}"


class Distribution(BaseModel):
    """One installed distribution in canonical form."""

    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    direct_url: str | None


class _Requirement(NamedTuple):
    name: str
    extras: frozenset[str]
    extra_markers: frozenset[str]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_runtime_file(relative: Path) -> bool:
    if relative.suffix == ".pyc" or "__pycache__" in relative.parts:
        return False
    return len(relative.parts) == 1 or relative.parts[0] not in _EXCLUDED_DIRS


def tree_sha256(package_dir: Path) -> str:
    """Hash of the runtime source tree under ``package_dir`` (``src/lovspor``)."""
    lines = []
    for path in sorted(package_dir.rglob("*")):
        relative = path.relative_to(package_dir)
        if path.is_file() and not path.is_symlink() and _is_runtime_file(relative):
            lines.append(f"{relative.as_posix()}\0{_sha256(path.read_bytes())}\n")
    return _sha256("".join(sorted(lines)).encode("utf-8"))


def interpreter() -> Interpreter:
    major, minor, micro = sys.version_info[:3]
    return Interpreter(implementation=sys.implementation.name, version=f"{major}.{minor}.{micro}")


def canonical_environment_sha256(distributions: Iterable[Distribution]) -> str:
    """One canonical form for both sides of ``environment_match``."""
    lines = sorted(f"{d.name}\0{d.version}\0{d.direct_url or ''}\n" for d in distributions)
    return _sha256("".join(lines).encode("utf-8"))


def normalise_name(name: str) -> str:
    """PEP 503 normalisation, so ``typing_extensions`` and ``Typing-Extensions`` are one."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement(text: str) -> _Requirement | None:
    head, _, marker = text.partition(";")
    match = _REQUIREMENT.match(head)
    if match is None:
        return None
    extras = frozenset(
        normalise_name(extra) for extra in (match["extras"] or "").split(",") if extra.strip()
    )
    extra_markers = frozenset(normalise_name(extra) for extra in _EXTRA_MARKER.findall(marker))
    return _Requirement(normalise_name(match["name"]), extras, extra_markers)


def _direct_url(distribution: importlib.metadata.Distribution) -> str | None:
    text = distribution.read_text("direct_url.json")
    if text is None:
        return None
    payload: dict[str, object] = json.loads(text)
    url = str(payload.get("url", ""))
    vcs = payload.get("vcs_info")
    commit = vcs.get("commit_id") if isinstance(vcs, dict) else None
    return f"{url}@{commit}" if commit else url


def _describe(distribution: importlib.metadata.Distribution) -> Distribution:
    return Distribution(
        name=normalise_name(distribution.metadata["Name"]),
        version=distribution.version,
        direct_url=_direct_url(distribution),
    )


def _wanted(
    distribution: importlib.metadata.Distribution, extras: set[str]
) -> Iterator[_Requirement]:
    for text in distribution.requires or ():
        requirement = _parse_requirement(text)
        if requirement is not None and (
            not requirement.extra_markers or requirement.extra_markers & extras
        ):
            yield requirement


def installed_distributions(root: str = "lovspor") -> tuple[Distribution, ...]:
    """``root``'s installed dependency closure, root excluded, sorted by name.

    Walks ``Requires-Dist`` through ``importlib.metadata``: a requirement
    behind an ``extra`` marker is followed only when that extra was asked
    for by a dependant (``pyjwt[crypto]`` pulls ``cryptography``); ``root``
    itself is walked with no extra, as ``uv sync --frozen --no-dev``
    installs it. A requirement that is not installed is not in the
    environment and is skipped.
    """
    requested: dict[str, set[str]] = {}
    found: dict[str, Distribution] = {}
    queue: list[tuple[str, frozenset[str]]] = [(normalise_name(root), frozenset())]
    while queue:
        name, extras = queue.pop()
        known = requested.setdefault(name, set())
        if name in found and extras <= known:
            continue
        known |= extras
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        found[name] = _describe(distribution)
        queue.extend((req.name, req.extras) for req in _wanted(distribution, known))
    found.pop(normalise_name(root), None)
    return tuple(found[name] for name in sorted(found))


def installed_environment_sha256() -> str:
    """The environment this interpreter imports, in the one canonical form."""
    return canonical_environment_sha256(installed_distributions())
