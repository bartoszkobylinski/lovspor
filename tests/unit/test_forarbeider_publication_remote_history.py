"""Remote recovery must reject incomplete or non-linear history."""

import pytest

from lovspor.forarbeider.publication_remote import HISTORY_LIMIT, Reader, RemoteCommit, history


def commit(oid: str, *parents: str) -> RemoteCommit:
    return RemoteCommit(oid, parents, "", "", {}, (), None)


def test_history_accepts_limit_and_returns_oldest_first() -> None:
    commits = {str(i): commit(str(i), str(i - 1)) for i in range(1, HISTORY_LIMIT + 1)}
    result = history(Reader(lambda: str(HISTORY_LIMIT), commits.get), "0")
    assert result.broken is None
    assert [item.oid for item in result.commits] == [str(i) for i in range(1, HISTORY_LIMIT + 1)]


def test_history_rejects_one_commit_over_limit() -> None:
    commits = {str(i): commit(str(i), str(i - 1)) for i in range(1, HISTORY_LIMIT + 2)}
    result = history(Reader(lambda: str(HISTORY_LIMIT + 1), commits.get), "0")
    assert result.commits == ()
    assert result.broken == "remote_main_not_a_descendant_of_the_base"


@pytest.mark.parametrize(
    ("head", "commits", "reason"),
    [
        (None, {}, "remote_main_unreadable"),
        ("1", {}, "remote_commit_unreadable"),
        ("1", {"1": commit("1")}, "remote_main_not_a_descendant_of_the_base"),
        ("1", {"1": commit("1", "0", "other")}, "remote_main_not_a_descendant_of_the_base"),
        ("2", {"2": commit("2", "1")}, "remote_commit_unreadable"),
    ],
)
def test_history_discards_partial_or_unverifiable_history(
    head: str | None, commits: dict[str, RemoteCommit], reason: str
) -> None:
    result = history(Reader(lambda: head, commits.get), "0")
    assert result.commits == ()
    assert result.broken == reason
