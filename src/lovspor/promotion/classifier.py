"""The classifier's output, as promotion reads it (ADR-0016 4b, slice S8).

The classifier is a parallel workstream (ADR-0016 Out of Scope); promotion
consumes it through this one reader and nothing else. Its first real output,
the R1.1 run of 2026-10-03, is JSON Lines, one object per captured artifact:

* ``authority`` — the KLASS code the artifact is filed under;
* ``sha`` and ``url`` — the archived bytes and where they were served, the
  same pair as :class:`~lovspor.promotion.decisions.ArtifactKey`;
* ``form`` — ``html``, ``pdf``, ``docx`` …;
* ``r1`` — an enacted forskrift; ``r2`` — ``r1`` or other adopted rules
  (vedtekter, reglement), so ``r1`` without ``r2`` cannot occur;
* the rules' signals: booleans such as ``self_operative`` or ``draft`` and
  counts such as ``n_sections``.

The file does not name the rule set that wrote it, so the operator states it
(``classifier_version``); it is never inferred from a file name. Observation
times in the file (``first``, ``last``) are ignored: the archive is the only
source of the observation axis.

Only ``r1`` makes a promotion candidate: the § 14 basis covers enacted
regulations, not the wider ``r2`` class (owner decision 2026-10-03). A row
that does not validate refuses the whole file — never skipped.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator

from lovspor.errors import ClassifierOutputError
from lovspor.promotion.decisions import ArtifactKey, ClassifierEvidence

#: The rules' boolean signals, positive and negative, in the order evidence lists them.
SIGNALS = (
    "adopted_title",
    "case_doc",
    "draft",
    "enacted_by",
    "forskrift_header",
    "hjemmel",
    "ikraft",
    "lf_id",
    "lovtidend",
    "national",
    "proposal_head",
    "self_early",
    "self_operative",
    "title_forskrift",
    "valid_from",
)

#: Signals that read as a regulation's own markers; ``adopted_title`` and
#: ``valid_from`` only widen ``r2`` and say nothing for an enacted forskrift.
_NOT_EVIDENCE = frozenset({"adopted_title", "valid_from"})


class ClassifierClass(StrEnum):
    ENACTED_REGULATION = "enacted_regulation"
    ADOPTED_RULES = "adopted_rules"
    OTHER = "other"


class ClassifiedArtifact(BaseModel):
    """One row of the classifier's output: which artifact, which class, on what signals."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    authority_id: str = Field(alias="authority", pattern=r"^(?:\d{2}|\d{4})$")
    sha256: str = Field(alias="sha", pattern=r"^[0-9a-f]{64}$")
    url: str = Field(pattern=r"^https?://\S+$")
    form: str = Field(min_length=1)
    r1: StrictBool
    r2: StrictBool
    n_sections: int = Field(default=0, ge=0)
    signals: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _true_signals(cls, data: object) -> object:
        if isinstance(data, dict):
            true = tuple(name for name in SIGNALS if data.get(name) is True)
            return {**data, "signals": tuple(s for s in true if s not in _NOT_EVIDENCE)}
        return data

    @model_validator(mode="after")
    def _r1_is_inside_r2(self) -> ClassifiedArtifact:
        if self.r1 and not self.r2:
            msg = "r1 without r2: every enacted forskrift is in the r2 class too"
            raise ValueError(msg)
        return self

    @property
    def key(self) -> ArtifactKey:
        return ArtifactKey(authority_id=self.authority_id, sha256=self.sha256, source_url=self.url)

    @property
    def class_name(self) -> ClassifierClass:
        if self.r1:
            return ClassifierClass.ENACTED_REGULATION
        return ClassifierClass.ADOPTED_RULES if self.r2 else ClassifierClass.OTHER

    def evidence(self, classifier_version: str) -> ClassifierEvidence:
        """The evidence an audit record carries: the class and the signals it was read on."""
        return ClassifierEvidence(
            classifier_version=classifier_version,
            class_name=self.class_name.value,
            evidence=(*self.signals, f"n_sections={self.n_sections}"),
        )


class ClassifierOutput(BaseModel):
    """A whole classifier run: its stated version, the file's hash and every row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    classifier_version: Annotated[str, Field(min_length=1)]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: tuple[ClassifiedArtifact, ...]

    def candidates(
        self, authority_id: str, listed: tuple[str, ...] = ()
    ) -> tuple[ClassifiedArtifact, ...]:
        """The enacted regulations of ``authority_id`` — all, or only the ``listed`` hashes."""
        found = sorted(
            (
                a
                for a in self.artifacts
                if a.authority_id == authority_id
                and a.class_name is ClassifierClass.ENACTED_REGULATION
            ),
            key=lambda a: (a.url, a.sha256),
        )
        if not listed:
            return tuple(found)
        missing = sorted(set(listed) - {a.sha256 for a in found})
        if missing:
            msg = f"not enacted-regulation candidates of {authority_id}: {', '.join(missing)}"
            raise ClassifierOutputError(msg)
        return tuple(a for a in found if a.sha256 in listed)


def read_classifier_output(path: Path, classifier_version: str) -> ClassifierOutput:
    """Every row of the classifier's JSON Lines file, refused whole if one does not read."""
    version = classifier_version.strip()
    if not version:
        msg = "the classifier version must be stated; the output file does not name it"
        raise ClassifierOutputError(msg)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        msg = f"cannot read the classifier output at {path}: {exc}"
        raise ClassifierOutputError(msg) from exc
    try:
        text = raw.decode()
    except UnicodeDecodeError as exc:
        msg = f"the classifier output at {path} is not UTF-8: {exc}"
        raise ClassifierOutputError(msg) from exc
    return ClassifierOutput(
        classifier_version=version,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        artifacts=_rows(text, path),
    )


def _rows(text: str, path: Path) -> tuple[ClassifiedArtifact, ...]:
    lines = text.split("\n")
    if lines[-1]:
        msg = f"{path}: the last row is incomplete (no newline)"
        raise ClassifierOutputError(msg)
    rows: dict[tuple[str, str], ClassifiedArtifact] = {}
    for number, line in enumerate(lines[:-1], start=1):
        artifact = _row(line, number)
        seen = rows.setdefault((artifact.url, artifact.sha256), artifact)
        if seen != artifact:
            msg = f"line {number}: two rows disagree on {artifact.sha256} at {artifact.url}"
            raise ClassifierOutputError(msg)
    return tuple(rows.values())


def _row(line: str, number: int) -> ClassifiedArtifact:
    try:
        return ClassifiedArtifact.model_validate(json.loads(line))
    except (ValueError, ValidationError) as exc:
        msg = f"classifier output line {number} does not read: {exc}"
        raise ClassifierOutputError(msg) from exc
