"""Observable G3 coordination protocol and evidence diagnostics."""

import hashlib
import json

import pytest
from pytest_httpx import HTTPXMock

from lovspor.embeddings import g3_race as race
from lovspor.embeddings.r2_client import R2Client, R2Credentials

TARGET = "lovspor-embedding-cache/schema-1/entry"


def test_both_writers_have_the_other_as_peer() -> None:
    assert race.peer_of("a") == "b"
    assert race.peer_of("b") == "a"


def test_readiness_has_canonical_sorted_json_bytes() -> None:
    readiness = race.Readiness("r1", "a", TARGET, "a" * 64)
    expected = (
        '{"candidate_sha256": "' + "a" * 64 + '", "run_id": "r1", '
        '"target": "' + TARGET + '", "writer": "a"}'
    ).encode()
    assert readiness.encode() == expected


def test_coordination_key_has_32_digest_characters() -> None:
    barrier = race.Barrier(None, "r1", "a", timeout=0)  # type: ignore[arg-type]
    digest = hashlib.sha256(TARGET.encode()).hexdigest()[:32]
    assert barrier.key(TARGET, "b") == (f"lovspor-embedding-cache/g3-barrier/r1/{digest}/b.json")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"{", "the peer's coordination object is corrupt"),
        (
            race.Readiness("other", "b", TARGET, "b" * 64).encode(),
            "the peer's coordination object names another run, writer or key",
        ),
    ],
)
def test_invalid_readiness_has_exact_diagnostic(body: bytes, message: str) -> None:
    with pytest.raises(race.RaceError) as caught:
        race.Readiness.decode(body, race.Readiness("r1", "a", TARGET, "a" * 64))
    assert str(caught.value) == message


def test_invalid_run_has_exact_diagnostic() -> None:
    with pytest.raises(race.RaceError) as caught:
        race.Barrier(None, "UPPER", "a", timeout=0)  # type: ignore[arg-type]
    assert str(caught.value) == "the run id must be lowercase letters, digits and dashes"


@pytest.fixture
def bucket() -> R2Client:
    return R2Client(R2Credentials("a" * 32, "staging", "key", "secret"))


def test_racing_client_publishes_readiness_and_entry_bytes(
    bucket: R2Client, httpx_mock: HTTPXMock
) -> None:
    barrier = race.Barrier(bucket, "r1", "a", timeout=0)
    base = bucket.credentials.endpoint + "/staging/"
    peer = race.Readiness("r1", "b", TARGET, "b" * 64)
    httpx_mock.add_response(method="PUT", url=base + barrier.key(TARGET, "a"))
    httpx_mock.add_response(
        method="GET", url=base + barrier.key(TARGET, "b"), content=peer.encode()
    )
    httpx_mock.add_response(method="PUT", url=base + TARGET)
    httpx_mock.add_response(method="PUT", url=base + TARGET + "2")
    client = race.RacingClient(bucket, barrier)

    assert client.put_if_absent(TARGET, b"entry-a").status == 200
    assert client.put_if_absent(TARGET + "2", b"second-entry").status == 200

    requests = httpx_mock.get_requests()
    assert json.loads(requests[0].content) == {
        "run_id": "r1",
        "writer": "a",
        "target": TARGET,
        "candidate_sha256": hashlib.sha256(b"entry-a").hexdigest(),
    }
    assert requests[2].content == b"entry-a"
    assert requests[3].content == b"second-entry"


def test_existing_coordination_object_has_exact_diagnostic(
    bucket: R2Client, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(method="PUT", status_code=412)
    with pytest.raises(race.RaceError) as caught:
        race.Barrier(bucket, "r1", "a", timeout=0).meet(TARGET, "a" * 64)
    assert str(caught.value) == "this writer's coordination object already exists: rerun the race"


def evidence() -> tuple[dict[str, object], dict[str, object]]:
    a: dict[str, object] = {
        "writer": "a",
        "run_id": "r1",
        "target": TARGET,
        "candidate_sha256": "a" * 64,
        "peer_candidate_sha256": "b" * 64,
        "put_status": 200,
        "remote_sha256": "a" * 64,
        "local_sha256": "a" * 64,
        "sidecar_sha256": "c" * 64,
    }
    return a, dict(
        a, writer="b", candidate_sha256="b" * 64, peer_candidate_sha256="a" * 64, put_status=412
    )


@pytest.mark.parametrize(
    ("writer", "field", "value", "message"),
    [
        (1, "writer", "a", "evidence must come from writer a and writer b"),
        (1, "run_id", "r2", "the writers ran in different races"),
        (1, "target", "other", "the writers did not attempt the same key"),
        (
            1,
            "candidate_sha256",
            "a" * 64,
            "the writers' candidates are equal: nothing was contested",
        ),
        (1, "remote_sha256", "b" * 64, "the bucket does not hold the winner's candidate"),
        (0, "local_sha256", "b" * 64, "the winner's local copy is not its own candidate"),
        (1, "local_sha256", "b" * 64, "the loser did not adopt the winner's bytes"),
        (1, "sidecar_sha256", "d" * 64, "the two writers wrote different sidecars"),
    ],
)
def test_judge_returns_exact_actionable_reason(
    writer: int, field: str, value: object, message: str
) -> None:
    writers = evidence()
    writers[writer][field] = value
    assert message in race.judge(*writers)
