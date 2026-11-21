"""AR4b-1 path A: ``createCommitOnBranch`` with ``expectedHeadOid`` via a transport (#738; §9.3).

This module builds the one GraphQL mutation a path-A publication sends and
judges the answer. It holds **no transport**: the caller passes an object with
an App ``identity``, a ``head`` read and a ``post``. In this repository the only
such object is the tests' stub of the endpoint; nothing here can open a
connection, and no production transport exists (AR4b-2 and AR7 come first).

The mutation binds the repository, ``main``, ``expectedHeadOid`` = the base
commit, a headline naming the release manifest digest, a body naming the
transaction, plan and batch, and exactly the proposed files, each as the bytes
the AR4a read-back proved, base64-encoded. There are no deletions.

The answer is one of three, and never guessed:

- ``published``: a well-formed commit id, and ``main`` then reads as that id;
- ``refused``: GitHub's stale-head refusal with no commit; nothing was published;
- ``outcome_unknown``: anything else, including a lost response, an error of
  another kind, a malformed body, or ``main`` not at the returned commit.
  Recovery (§9.3a, AR4b-2) must decide; nothing is sent again.
"""

import base64
import re
from collections.abc import Mapping
from typing import Any, Literal, NamedTuple, Protocol

from lovspor.forarbeider.publication_execution_models import AppIdentity, PublicationSettings
from lovspor.forarbeider.publication_models import PublicationTransaction

MUTATION = (
    "mutation($input: CreateCommitOnBranchInput!) "
    "{ createCommitOnBranch(input: $input) { commit { oid } } }"
)
_OID = re.compile(r"[0-9a-f]{40}")
_STALE = "Expected branch to point to"


class GraphQLTransport(Protocol):
    """What path A needs from the endpoint; only a test stub implements it here."""

    @property
    def identity(self) -> AppIdentity: ...

    def head(self, repository: str, branch: str) -> str | None: ...

    def post(self, payload: dict[str, Any]) -> Any: ...


class Sent(NamedTuple):
    status: Literal["published", "refused", "outcome_unknown"]
    commit: str | None
    reasons: tuple[str, ...]


def headline(transaction: PublicationTransaction) -> str:
    return f"forarbeider release {transaction.content.release_manifest_sha256}"


def body(transaction: PublicationTransaction) -> str:
    content = transaction.content
    return (
        f"transaction {transaction.transaction_id}\nplan {content.plan_id}\n"
        f"batch {content.batch_id}\npath {content.publication.path}\n"
    )


def payload_of(
    transaction: PublicationTransaction, files: Mapping[str, bytes], settings: PublicationSettings
) -> dict[str, Any]:
    """The exact mutation for ``transaction``: its files, its base, its repository and branch."""
    additions = [
        {"path": item.path, "contents": base64.b64encode(files[item.path]).decode("ascii")}
        for item in transaction.content.files
    ]
    given = {
        "branch": {"repositoryNameWithOwner": settings.repository, "branchName": settings.branch},
        "message": {"headline": headline(transaction), "body": body(transaction)},
        "expectedHeadOid": transaction.content.publication.base_commit,
        "fileChanges": {"additions": additions, "deletions": []},
    }
    return {"query": MUTATION, "variables": {"input": given}}


def _stale(answer: Mapping[str, Any]) -> bool:
    errors = answer.get("errors")
    if not isinstance(errors, list) or not errors:
        return False
    data = answer.get("data")
    created = data.get("createCommitOnBranch") if isinstance(data, Mapping) else None
    messages = [e.get("message", "") for e in errors if isinstance(e, Mapping)]
    types = [e.get("type") for e in errors if isinstance(e, Mapping)]
    return (
        created is None
        and len(messages) == len(errors)
        and all(
            kind == "STALE_DATA" and _STALE in str(text)
            for kind, text in zip(types, messages, strict=True)
        )
    )


def _commit(answer: Mapping[str, Any]) -> str | None:
    if answer.get("errors"):
        return None
    data = answer.get("data")
    created = data.get("createCommitOnBranch") if isinstance(data, Mapping) else None
    commit = created.get("commit") if isinstance(created, Mapping) else None
    oid = commit.get("oid") if isinstance(commit, Mapping) else None
    return oid if isinstance(oid, str) and _OID.fullmatch(oid) else None


def judged(answer: object, read_head: str | None) -> Sent:
    """What the endpoint's answer proves, with ``main`` as read right after it."""
    if not isinstance(answer, Mapping):
        return Sent("outcome_unknown", None, ("response_malformed",))
    if _stale(answer):
        return Sent("refused", None, ("expected_head_mismatch",))
    oid = _commit(answer)
    if oid is None:
        return Sent("outcome_unknown", None, ("response_malformed",))
    if read_head != oid:
        return Sent("outcome_unknown", oid, ("main_not_at_returned_commit",))
    return Sent("published", oid, ())


def send(
    transport: GraphQLTransport, payload: dict[str, Any], settings: PublicationSettings
) -> Sent:
    """Send the mutation once; any failure of the call itself is an unknown outcome."""
    try:
        answer = transport.post(payload)
    except Exception:
        return Sent("outcome_unknown", None, ("response_lost",))
    try:
        read = transport.head(settings.repository, settings.branch)
    except Exception:
        read = None
    return judged(answer, read)
