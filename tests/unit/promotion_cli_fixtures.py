"""A synthetic observatory archive and a temporary lovverk checkout for ``lovspor promote``.

The archive is real on disk — blobs and ``observations.jsonl`` written by the
observatory's own ``append_artifact``, the register written by its own
``register-source`` command — under ``tmp_path``, never ``/Volumes/T7``. The
corpus is a fresh ``git init`` holding the S0 skeleton: an empty
``lokale-forskrifter/manifest.json`` beside a root ``manifest.json`` with one
central regulation. Every page is invented text from ``promotion_fixtures``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner, Result

from lovspor.cli import app
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, FetchFailure, RetrievalProvenance
from lovspor.observatory.storage import ObservatoryRoot

AUTHORITY = "0301"
KLASS_VERSION = "131-2024"
FIRST_SEEN = datetime(2026, 8, 19, 15, 17, 23, tzinfo=UTC)
PAGE_URL = "https://eksempel.kommune.invalid/forskrifter/renovasjon"
CENTRAL_DOC_ID = "sf-20200114-0063"
REVIEWER = "Kari Gjennomgang"

runner = CliRunner()


def provenance() -> RetrievalProvenance:
    return RetrievalProvenance(
        adapter="generic-html",
        channel="http",
        discovery_method="sitemap",
        user_agent="lovspor-observatory/0.1",
        rate_limit_seconds=2.0,
    )


def store(
    root: Path, payload: bytes, *, url: str = PAGE_URL, observed_at: datetime = FIRST_SEEN
) -> str:
    """Capture ``payload`` into the archive as the observatory does; its SHA-256."""
    sha256 = hashlib.sha256(payload).hexdigest()
    record = ArtifactObservation(
        authority_id=AUTHORITY,
        url=url,
        observed_at=observed_at,
        provenance=provenance(),
        sha256=sha256,
        content_type="text/html; charset=utf-8",
        http_status=200,
    )
    ObservationLog(ObservatoryRoot(root, [])).append_artifact(record, payload)
    return sha256


def store_failure(root: Path, observed_at: datetime, url: str = PAGE_URL) -> None:
    failure = FetchFailure(
        authority_id=AUTHORITY,
        url=url,
        observed_at=observed_at,
        provenance=provenance(),
        outcome="http_error",
        http_status=404,
    )
    ObservationLog(ObservatoryRoot(root, [])).append(failure)


def invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def register(root: Path) -> None:
    """Put the authority in the source register through the supported command."""
    result = invoke(
        "observatory",
        "register-source",
        "--id",
        AUTHORITY,
        "--name",
        "Eksempel",
        "--domain",
        "eksempel.kommune.invalid",
    )
    assert result.exit_code == 0, result.output


def git(corpus: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=Operator", "-c", "user.email=op@example.invalid", *args],
        cwd=corpus,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def make_corpus(base: Path) -> Path:
    """A lovverk checkout with the S0 skeleton and one central forskrift, committed."""
    corpus = base / "lovverk"
    for directory in ("lover", "forskrifter", "lokale-forskrifter"):
        (corpus / directory).mkdir(parents=True)
    central = {
        "documents": {
            CENTRAL_DOC_ID: {
                "doc_type": "forskrift",
                "xml_hash": "0" * 64,
                "markdown_path": "forskrifter/eksempelforskrift.md",
                "source_dataset": "gjeldende-sentrale-forskrifter",
                "last_seen": "2026-04-29T11:20:30Z",
                "status": "current",
                "slug": "eksempelforskrift",
            }
        },
        "generated_at": "2026-04-29T11:20:30Z",
        "version": 1,
    }
    (corpus / "manifest.json").write_text(json.dumps(central, indent=2) + "\n", encoding="utf-8")
    (corpus / "forskrifter" / "eksempelforskrift.md").write_text("---\n---\n", encoding="utf-8")
    skeleton = {"documents": {}, "generated_at": None, "version": 1}
    local_manifest = corpus / "lokale-forskrifter" / "manifest.json"
    local_manifest.write_text(json.dumps(skeleton, indent=2) + "\n", encoding="utf-8")
    git(corpus, "init", "-q")
    git(corpus, "add", "-A")
    git(corpus, "commit", "-q", "-m", "chore: initialize corpus repository")
    return corpus


@dataclass(frozen=True)
class Decision:
    decision: str = "approve"
    decided_by: str = REVIEWER
    reason: str = "Vedtatt forskrift; kilden lest i sin helhet."
    classifier: dict[str, object] | None = None

    def write(self, directory: Path) -> Path:
        path = directory / f"decision-{self.decision}.json"
        body: dict[str, object] = {
            "decision": self.decision,
            "decided_by": self.decided_by,
            "reason": self.reason,
        }
        if self.classifier is not None:
            body["classifier"] = self.classifier
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return path


def approve(artifact: str, document: Path) -> Result:
    return invoke(
        "promote",
        "approve",
        "--authority",
        AUTHORITY,
        "--artifact",
        artifact,
        "--decision",
        str(document),
    )


def promote(command: str, artifact: str, corpus: Path) -> Result:
    return invoke(
        "promote",
        command,
        "--authority",
        AUTHORITY,
        "--artifact",
        artifact,
        "--corpus",
        str(corpus),
        "--klass-version",
        KLASS_VERSION,
    )


def tree(corpus: Path) -> dict[str, bytes]:
    """Every file of the checkout outside ``.git``, by relative path."""
    return {
        path.relative_to(corpus).as_posix(): path.read_bytes()
        for path in sorted(corpus.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(corpus).parts
    }
