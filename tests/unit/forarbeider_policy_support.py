"""A synthetic policy desk for the standing-policy tests (#738, Amendment 1).

One private evidence file per kind, written owner-only under ``tmp_path``. The
phrase a sign needs is read from what the command itself printed on a first,
unconfirmed run, so a test types exactly what the owner would be shown.
"""

import json
import re
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider.policy_models import PolicyEvidence, PolicyEvidenceKind, StandingPolicy
from tests.unit.forarbeider_release_support import PRIVATE_MARKER, private, said_digest

OWNER = "owner-b"
DECIDED = "2100-04-04T12:00:00Z"
VALID = "2100-04-04T12:00:00Z/2100-07-01T00:00:00Z"
UNTIL = "2100-07-01T00:00:00+00:00"
TERMS_URL = "https://www.regjeringen.no/no/om-nettstedet/vilkar/id0000001/"
KINDS = tuple(kind.value for kind in PolicyEvidenceKind)
_PROMPT = re.compile(r"Type to confirm: (.+?): ?$", re.MULTILINE)


def evidence_options(values: list[str]) -> list[str]:
    return [token for value in values for token in ("--evidence", value)]


def shown_phrase(output: str) -> str:
    """The phrase the prompt asked for, or ``""`` when no prompt was reached."""
    match = _PROMPT.search(output)
    return "" if match is None else match[1]


class PolicyDesk:
    """A release workspace and one private evidence file per policy kind."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.workspace = root / "workspace"
        self.ledger = root / "ledger"
        self.terms_url = TERMS_URL
        self.evidence = {
            kind: private(root / "evidence" / f"{kind}.txt", f"{PRIVATE_MARKER} {kind}".encode())
            for kind in KINDS
        }

    def invoke(self, args: list[str], phrase: str | None = None) -> Any:
        head = ["ops", "forarbeider-release", "--workspace", str(self.workspace)]
        head += ["--ledger", str(self.ledger), "policy"]
        return CliRunner().invoke(app, [*head, *args], input=phrase)

    def sign_args(self, options: list[str] | None, decided: str, valid: str) -> list[str]:
        evidence = [f"{kind}={path}" for kind, path in self.evidence.items()]
        given = options if options is not None else evidence_options(evidence)
        return [
            "sign",
            *given,
            "--terms-url",
            self.terms_url,
            "--decided",
            f"{OWNER}@{decided}",
            "--valid",
            valid,
        ]

    def shown(self, *, decided: str = DECIDED, valid: str = VALID) -> Any:
        """A sign run answered with an empty line: it prints and refuses."""
        return self.invoke(self.sign_args(None, decided, valid), "\n")

    def sign(
        self,
        *,
        options: list[str] | None = None,
        phrase: str | None = None,
        decided: str = DECIDED,
        valid: str = VALID,
    ) -> Any:
        args = self.sign_args(options, decided, valid)
        if phrase is None:
            phrase = shown_phrase(self.invoke(args, "\n").output) + "\n"
        return self.invoke(args, phrase)

    def signed(self, *, decided: str = DECIDED, valid: str = VALID) -> str:
        result = self.sign(decided=decided, valid=valid)
        assert result.exit_code == 0, result.output
        return said_digest(result.output, "policy")

    def revoke(self, sha: str, at: str) -> Any:
        return self.invoke(["revoke", "--policy", sha, "--decided", f"{OWNER}@{at}"])

    def show(self, sha: str, at: str) -> Any:
        return self.invoke(["show", sha, "--at", at])

    def record(self, sha: str) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(
            (self.workspace / "policies" / f"{sha}.json").read_text()
        )
        return loaded


def standing_policy(**changes: Any) -> StandingPolicy:
    fields: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "forarbeider_standing_policy",
        "source_provider": "regjeringen.no",
        "document_types": ("prop-l", "prop-ls"),
        "evidence": tuple(
            PolicyEvidence(kind=kind, path=f"/p/{kind}", sha256=f"{index:064x}", size_bytes=1)
            for index, kind in enumerate(PolicyEvidenceKind)
        ),
        "owner": OWNER,
        "decided_at": "2100-04-04T12:00:00Z",
        "valid_from": "2100-04-05T00:00:00Z",
        "valid_until": "2100-07-01T00:00:00Z",
        "terms_url": TERMS_URL,
        "clears": "unflagged_chapters",
        "legal_sufficiency_decided_by_code": False,
        "identity_authenticated": False,
    }
    return StandingPolicy.model_validate({**fields, **changes})
