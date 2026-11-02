"""A synthetic release desk for the PR3B-1 preflight tests. No real chapter text.

Pages are the committed synthetic ``chapter-l.html`` with its identity and
paging rewritten, so one fixture yields chapters 2 and 3 of a Prop. L and of a
Prop. LS. Everything is written owner-only under ``tmp_path``, which is outside
every Git checkout.
"""

import json
import os
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider import release_commands
from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_models import ChapterResult, result_text
from lovspor.forarbeider.models import metadata_text
from lovspor.forarbeider.page import build_record
from lovspor.forarbeider.review_ledger import DecisionRequest, load_history, record_decision
from lovspor.forarbeider.review_models import (
    ALL_ATTESTATIONS,
    Attestation,
    ReviewReason,
    ReviewStatus,
    subject_key,
    subject_of,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "forarbeider" / "chapter-l.html"
RETRIEVED = "2100-04-01T12:00:00Z"
REVIEWED = "2100-04-02T12:00:00Z"
DOSSIER_AT = "2100-04-03T12:00:00Z"
DECIDED = "2100-04-04T12:00:00Z"
VALID = "2100-04-04T12:00:00Z/2100-05-01T00:00:00Z"
AT = "2100-04-10T12:00:00Z"
PRIVATE_MARKER = "Synthetisk privat merknad som aldri skal i loggen"
REVIEWER = "reviewer-a"
OWNER = "owner-b"
KINDS = {"l": ("prop.-901-l-20992100/id9000001", "Prop. 901 L")}
KINDS["ls"] = ("prop.-902-ls-20992100/id9000003", "Prop. 902 LS")
_THIRD_CHAPTER = (
    ('href="?ch=1" data-longdoc-prev', 'href="?ch=2" data-longdoc-prev'),
    ('?ch=1" data-longdoc-prev', '?ch=2" data-longdoc-prev'),
    ('href="?ch=3" data-longdoc-next', 'href="?ch=4" data-longdoc-next'),
    ('?ch=3" data-longdoc-next', '?ch=4" data-longdoc-next'),
    ('nav-item-current="2"', 'nav-item-current="3"'),
    (">2 Bakgrunn</h1>", ">Tilr&#xE5;ding</h1>"),
)


def page(kind: str, chapter: int, extra: str = "") -> tuple[bytes, str]:
    """A synthetic saved page of chapter 2 or 3 and the URL it was saved from."""
    text = FIXTURE.read_text(encoding="utf-8")
    if chapter == 3:
        for old, new in _THIRD_CHAPTER:
            text = text.replace(old, new)
    last = '<p id="kap2-2-p1">Avsluttende vurdering.</p>'
    text = text.replace(last, f"{last}{extra}")
    slug, reference = KINDS[kind]
    text = text.replace(KINDS["l"][0], slug).replace(KINDS["l"][1], reference)
    return text.encode(), f"https://www.regjeringen.no/no/dokumenter/{slug}/?ch={chapter}"


def private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
    return path


def moment(text: str) -> datetime:
    return datetime.fromisoformat(text)


def said_digest(output: str, word: str) -> str:
    match = re.search(rf"{word} ([0-9a-f]{{64}})", output)
    assert match is not None, output
    return match[1]


class Desk:
    """One proposition staged privately, its PR3A ledger and a release workspace."""

    def __init__(self, root: Path, kind: str = "l", extras: dict[int, str] | None = None) -> None:
        self.root = root
        self.kind = kind
        self.workspace = root / "workspace"
        self.ledger = root / "ledger"
        self.pages: dict[int, Path] = {}
        self.observations: dict[int, Path] = {}
        self.results: dict[int, ChapterResult] = {}
        for chapter in (2, 3):
            self.stage(chapter, (extras or {}).get(chapter, ""))
        payload, url = page(kind, 2, (extras or {}).get(2, ""))
        record = build_record(payload, url, moment(RETRIEVED))
        self.metadata = private(root / "staged" / "metadata.json", metadata_text(record).encode())
        self.evidence = {kind: self.write_evidence(kind) for kind in _EVIDENCE_KINDS}

    def stage(self, chapter: int, extra: str = "", name: str = "") -> ChapterResult:
        payload, url = page(self.kind, chapter, extra)
        label = name or f"ch{chapter}"
        self.pages[chapter] = private(self.root / "pages" / f"{label}.html", payload)
        result = build_chapter_result(payload, url, moment(RETRIEVED))
        observation = self.root / "staged" / f"{label}.json"
        self.observations[chapter] = private(observation, result_text(result).encode())
        self.results[chapter] = result
        return result

    def write_evidence(self, kind: str, chapter_label: str = "") -> Path:
        text = f"{PRIVATE_MARKER}: {kind} {chapter_label}".encode()
        return private(self.root / "evidence" / f"{kind}{chapter_label}.txt", text)

    def decide(self, chapter: int, status: ReviewStatus, at: str = REVIEWED) -> None:
        result = self.results[chapter]
        history = load_history(self.ledger, subject_of(result))
        approved = status is ReviewStatus.APPROVED
        reasons = (ReviewReason.ATTESTATIONS_COMPLETE,) if approved else (ReviewReason.REOPENED,)
        attestations = tuple(
            Attestation(kind=kind, attested=True, evidence=("adr:0018",))
            for kind in ALL_ATTESTATIONS
        )
        request = DecisionRequest(
            subject=subject_of(result),
            reviewer=REVIEWER,
            decided_at=at,
            status=status,
            reason_codes=reasons,
            evidence=("adr:0018",),
            attestations=attestations if approved else (),
            expected_revision=len(history),
        )
        record_decision(self.ledger, request)

    def approve_all(self) -> None:
        for chapter in self.results:
            self.decide(chapter, ReviewStatus.APPROVED)

    def checkpoint(self, chapter: int) -> str:
        result = self.results[chapter]
        history = load_history(self.ledger, subject_of(result))
        return f"{subject_key(subject_of(result))}@{len(history)}:{history[-1].sha256}"

    def scope(self, chapters: Sequence[int] = (2, 3), pages: bool = True) -> list[str]:
        arguments = ["ops", "forarbeider-release"]
        arguments += ["--workspace", str(self.workspace), "--ledger", str(self.ledger)]
        arguments += ["chapters", "--metadata", str(self.metadata)]
        for chapter in chapters:
            arguments += ["--observation", str(self.observations[chapter])]
            if pages:
                arguments += ["--page", str(self.pages[chapter])]
        return arguments

    def invoke(self, arguments: list[str], text: str | None = None) -> Any:
        return CliRunner().invoke(app, arguments, input=text)

    def evidence_options(self, chapters: str = "2,3") -> list[str]:
        options = []
        for kind, path in self.evidence.items():
            options += ["--evidence", f"{kind}@{chapters}={path}"]
        return options

    def dossier(self, options: list[str] | None = None, chapters: Sequence[int] = (2, 3)) -> Any:
        bound = ",".join(str(chapter) for chapter in chapters)
        given = self.evidence_options(bound) if options is None else options
        reviewed = ["--reviewed", f"{REVIEWER}@{DOSSIER_AT}"]
        return self.invoke([*self.scope(chapters), "dossier", *given, *reviewed])

    def authorize(self, dossier: str, valid: str = VALID, chapters: Sequence[int] = (2, 3)) -> Any:
        options = ["--dossier", dossier, "--decided", f"{OWNER}@{DECIDED}", "--valid", valid]
        document = self.results[chapters[0]].id.split("-ch")[0]
        return self.invoke(
            [*self.scope(chapters), "authorize", *options], f"authorize {document} {dossier[:12]}\n"
        )

    def preflight(
        self, authorization: str | None, checkpoints: Sequence[str] = (), **scope: Any
    ) -> Any:
        options = ["--at", scope.pop("at", AT)]
        if authorization is not None:
            options += ["--authorization", authorization]
        for value in checkpoints:
            options += ["--checkpoint", value]
        return self.invoke([*self.scope(**scope), "preflight", *options])

    def released(self, chapters: Sequence[int] = (2, 3)) -> tuple[str, str]:
        """A dossier and an authorization over approved chapters: their digests."""
        dossier = said_digest(self.dossier(chapters=chapters).output, "dossier")
        authorized = self.authorize(dossier, chapters=chapters)
        assert authorized.exit_code == 0, authorized.output
        return dossier, said_digest(authorized.output, "authorization")

    def all_checkpoints(self) -> list[str]:
        return [self.checkpoint(chapter) for chapter in self.results]

    def report(self, output: str) -> dict[str, Any]:
        sha = said_digest(output, "report")
        loaded: dict[str, Any] = json.loads(
            (self.workspace / "reports" / f"{sha}.json").read_text()
        )
        return loaded


_EVIDENCE_KINDS = (
    "privacy_processing_basis",
    "publication_purpose",
    "source_completeness",
    "source_rights",
    "third_party_screening",
)


@pytest.fixture
def at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_commands, "_interactive", lambda: True)


def chapter_reasons(report: dict[str, Any], number: int) -> list[str]:
    outcome = next(item for item in report["chapters"] if item["chapter_number"] == number)
    reasons: list[str] = outcome["reasons"]
    return reasons
