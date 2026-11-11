"""A synthetic batch desk for the A2b planner tests (#738). No real chapter text.

Each document is a release ``Desk`` over the committed synthetic page with its
identity rewritten to ``Prop. 9NN L`` / ``id90001NN``, so up to 26 distinct
propositions share one workspace and one PR3A ledger. The source proof is a
real ``forarbeider-fetch`` run made offline through ``pytest-httpx``: it
stores the chapter pages it is given and the robots.txt and terms bytes the
standing policy is then signed over. Everything is owner-only under
``tmp_path``, outside every Git checkout.
"""

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider.review_models import ReviewStatus
from lovspor.regjeringen_fetch.fetch import FetchClock, FetchSettings, prepare_out, run_fetch
from lovspor.regjeringen_fetch.models import FetchRun, evidence_file_name
from tests.unit.forarbeider_policy_support import PolicyDesk
from tests.unit.forarbeider_release_support import KINDS, Desk, private, said_digest

ROBOTS = "https://www.regjeringen.no/robots.txt"
TERMS = "https://www.regjeringen.no/no/om-nettstedet/vilkar/id0000001/"
ROBOTS_BODY = b"User-agent: *\nDisallow: /sok\n"
TERMS_BODY = b"<html><body>synthetic terms</body></html>"
RUN_AT = datetime(2100, 4, 10, 6, 0, tzinfo=UTC)
PLAN_AT = "2100-04-10T12:00:00Z"
BRANCH = "forarbeider/batch-2100-04-10"
CHAPTER_MARKER = "Avsluttende vurdering"


def kind_of(number: int, letters: str = "l") -> str:
    """Register ``Prop. 9NN L`` (or LS) as a page kind and return its key."""
    key = f"batch{letters}{number}"
    slug = f"prop.-{900 + number}-{letters}-20992100/id{9000100 + number}"
    KINDS.setdefault(key, (slug, f"Prop. {900 + number} {letters.upper()}"))
    return key


class BatchDesk:
    """Documents sharing one workspace and ledger, a source proof and a standing policy."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.workspace = root / "workspace"
        self.ledger = root / "ledger"
        self.desks: dict[int, Desk] = {}
        self.run_file: Path | None = None
        self.policy_sha = ""
        self._requests = 0

    def add(self, number: int, extras: dict[int, str] | None = None, letters: str = "l") -> Desk:
        desk = Desk(self.root / f"d{number}", kind_of(number, letters), extras)
        desk.workspace, desk.ledger = self.workspace, self.ledger
        self.desks[number] = desk
        return desk

    def pages(self, skip: Iterable[str] = ()) -> dict[str, bytes]:
        skipped = set(skip)
        found = {}
        for desk in self.desks.values():
            for number, result in desk.results.items():
                if result.source_page_url not in skipped:
                    found[result.source_page_url] = desk.pages[number].read_bytes()
        return found

    def fetch(self, httpx_mock: HTTPXMock, pages: dict[str, bytes], at: datetime = RUN_AT) -> Path:
        """One offline fetch run of ``pages`` with robots.txt and terms: its record."""
        out = self.root / f"fetch-{len(list(self.root.glob('fetch-*')))}"
        httpx_mock.add_response(url=ROBOTS, content=ROBOTS_BODY)
        httpx_mock.add_response(url=TERMS, content=TERMS_BODY)
        for url, body in pages.items():
            httpx_mock.add_response(url=url, content=body)
        clock = FetchClock(now=lambda: at, monotonic=lambda: 0.0, sleep=lambda _: None)
        settings = FetchSettings(clock=clock, terms_url=TERMS)
        with httpx.Client() as client:
            run_fetch(client, tuple(pages), prepare_out(out), settings)
        (record,) = sorted(out.glob("fetch-run-*.json"))
        self.run_file = record
        return record

    def sign_policy(self, run_file: Path) -> str:
        """A standing policy whose robots and terms evidence are the run's stored bytes."""
        run = FetchRun.model_validate_json(run_file.read_bytes())
        policy = PolicyDesk(self.root / "policy")
        policy.workspace, policy.ledger = self.workspace, self.ledger
        policy.evidence["robots_txt"] = run_file.parent / str(evidence_file_name(run.robots))
        assert run.terms is not None
        policy.evidence["terms_page"] = run_file.parent / str(evidence_file_name(run.terms))
        self.policy_sha = policy.signed()
        return self.policy_sha

    def ready(self, httpx_mock: HTTPXMock, skip: Iterable[str] = ()) -> None:
        self.sign_policy(self.fetch(httpx_mock, self.pages(skip)))

    def flagged_report(self, number: int, chapters: Sequence[int]) -> tuple[str, list[str]]:
        """Approve, dossier, authorize and preflight ``chapters``: the report and checkpoints."""
        desk = self.desks[number]
        for chapter in chapters:
            desk.decide(chapter, ReviewStatus.APPROVED)
        _, authorization = desk.released(chapters=chapters)
        checkpoints = [desk.checkpoint(chapter) for chapter in chapters]
        result = desk.preflight(authorization, checkpoints)
        return said_digest(result.output, "report"), checkpoints

    def document(self, number: int, review: tuple[str, list[str]] | None = None) -> dict[str, Any]:
        desk = self.desks[number]
        entry: dict[str, Any] = {
            "metadata": str(desk.metadata),
            "observations": [str(path) for path in desk.observations.values()],
            "pages": [str(path) for path in desk.pages.values()],
        }
        if review is not None:
            entry["review"] = {"report": review[0], "checkpoints": review[1]}
        return entry

    def request(
        self, documents: Sequence[dict[str, Any]], branch: str = BRANCH, **changes: Any
    ) -> Path:
        payload = {
            "schema_version": 1,
            "record_type": "forarbeider_batch_request",
            "target_branch": branch,
            "documents": list(documents),
            **changes,
        }
        self._requests += 1
        path = self.root / "private" / f"batch-{self._requests}.json"
        return private(path, json.dumps(payload).encode())

    def plan(
        self, request: Path, at: str = PLAN_AT, policy: str = "", run: Path | None = None
    ) -> Any:
        proof = run or self.run_file
        assert proof is not None
        arguments = ["--request", str(request), "--policy", policy or self.policy_sha]
        arguments += ["--source-proof", str(proof), "--at", at]
        return CliRunner().invoke(app, [*self._head(), "batch", "plan", *arguments])

    def show(self, sha: str) -> Any:
        return CliRunner().invoke(app, [*self._head(), "batch", "show", sha])

    def stored(self, sha: str) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(
            (self.workspace / "batch_plans" / f"{sha}.json").read_text()
        )
        return loaded

    def _head(self) -> list[str]:
        workspace, ledger = ["--workspace", str(self.workspace)], ["--ledger", str(self.ledger)]
        return ["ops", "forarbeider-release", *workspace, *ledger]


def chapters_of(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        chapter["chapter_id"]: chapter
        for document in plan["documents"]
        for chapter in document["chapters"]
    }
