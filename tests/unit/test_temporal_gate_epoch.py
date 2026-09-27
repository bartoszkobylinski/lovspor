"""Tests for the gate-epoch registry (ADR-0012 Amendment 1, lovspor#432).

One immutable record per temporal parser version, noted on the boundary
commit in ``refs/notes/temporal-attestations-epoch``. Real git repos, real
notes refs — the channel is exercised exactly as a consumer clone meets it.
"""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import lovspor.temporal_attestation as attestation_module
from lovspor.temporal_attestation import (
    ATTESTATION_NOTES_REF,
    EPOCH_NOTES_REF,
    AttestationError,
    TemporalAttestation,
    TemporalGateEpoch,
    attested_commits,
    check_gate_epoch,
    fetch_gate_epochs,
    publish_gate_epochs,
    push_gate_epochs,
    read_gate_epochs,
    write_attestation,
    write_gate_epoch,
)

BOUNDARY_DATE = "2026-09-02T08:43:51Z"
GATE_DATE = "2026-09-04T08:47:34Z"
EPOCH_AT = datetime(2026, 9, 4, 8, 45, 36, tzinfo=UTC)


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    ).stdout.strip()


def _commit(repo: Path, name: str, iso_date: str) -> str:
    (repo / f"{name}.md").write_text(f"{name}\n")
    _git(repo, "add", "-A")
    stamp = {"GIT_AUTHOR_DATE": iso_date, "GIT_COMMITTER_DATE": iso_date}
    _git(repo, "commit", "-m", name, env=stamp)
    return _git(repo, "rev-parse", "HEAD")


def _init(repo: Path) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, str, str]:
    """A boundary state (pre-gate) and a gate-era state after it."""
    repo = tmp_path / "corpus"
    _init(repo)
    boundary = _commit(repo, "boundary", BOUNDARY_DATE)
    gated = _commit(repo, "gated", GATE_DATE)
    return repo, boundary, gated


def _epoch(boundary: str, **overrides: object) -> TemporalGateEpoch:
    fields: dict[str, object] = {
        "parser_version": 2,
        "epoch_at": EPOCH_AT,
        "boundary_commit": boundary,
        "recorded_at": datetime(2026, 9, 27, 10, 0, tzinfo=UTC),
        "source": "backfill",
        "evidence": "lovspor sync run 33854986231",
    }
    return TemporalGateEpoch.model_validate({**fields, **overrides})


def _attest(repo: Path, sha: str, version: int = 2) -> None:
    write_attestation(
        repo,
        TemporalAttestation(
            corpus_commit=sha,
            parser_version=version,
            documents_reconciled=1,
            notes_total=0,
            events_total=0,
            attested_at=datetime(2026, 9, 4, 9, 0, tzinfo=UTC),
        ),
    )


def _raw_note(repo: Path, commit: str, payload: str) -> None:
    _git(repo, "notes", f"--ref={EPOCH_NOTES_REF}", "add", "-f", "-m", payload, commit)


# ---------- the record ----------


def test_the_epoch_ref_sits_under_the_consumer_glob() -> None:
    """The consumer refspec is a trailing glob over ATTESTATION_NOTES_REF;
    the epoch ref must be a sibling it covers, never a second namespace."""
    assert EPOCH_NOTES_REF == "refs/notes/temporal-attestations-epoch"
    assert EPOCH_NOTES_REF.startswith(ATTESTATION_NOTES_REF)


@pytest.mark.parametrize(
    "overrides",
    [
        {"parser_version": 0},
        {"epoch_at": "2026-09-04T08:45:36"},
        {"recorded_at": "2026-09-27T10:00:00"},
        {"boundary_commit": "3c4f186"},
        {"boundary_commit": "G" * 40},
        {"source": "manual"},
        {"evidence": ""},
        {"unexpected": 1},
    ],
)
def test_record_refuses_malformed_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _epoch("a" * 40, **overrides)


def test_record_is_frozen() -> None:
    record = _epoch("a" * 40)
    with pytest.raises(ValidationError):
        record.parser_version = 3  # type: ignore[misc]


# ---------- reading ----------


def test_no_epoch_ref_reads_as_no_records(corpus: tuple[Path, str, str]) -> None:
    repo, _boundary, _gated = corpus
    assert read_gate_epochs(repo) == {}


def test_written_record_reads_back_keyed_by_version(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, _gated = corpus
    record = _epoch(boundary)

    assert write_gate_epoch(repo, record) is True

    assert read_gate_epochs(repo) == {2: record}


def test_unparseable_record_is_a_broken_channel(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, _gated = corpus
    _raw_note(repo, boundary, "not json")

    with pytest.raises(AttestationError, match="unparseable"):
        read_gate_epochs(repo)


@pytest.mark.parametrize("payload", ['{"parser_version": 2}', '[{"parser_version": 2}]'])
def test_record_of_the_wrong_shape_is_a_broken_channel(
    corpus: tuple[Path, str, str],
    payload: str,
) -> None:
    repo, boundary, _gated = corpus
    _raw_note(repo, boundary, payload)

    with pytest.raises(AttestationError, match="unparseable"):
        read_gate_epochs(repo)


def test_two_records_for_one_version_are_corrupt(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, gated = corpus
    first = _epoch(boundary).model_dump(mode="json")
    second = _epoch(gated, epoch_at=datetime(2026, 9, 5, tzinfo=UTC)).model_dump(mode="json")
    _raw_note(repo, boundary, json.dumps([first]))
    _raw_note(repo, gated, json.dumps([second]))

    with pytest.raises(AttestationError, match="duplicate"):
        read_gate_epochs(repo)


def test_duplicate_version_inside_one_note_is_corrupt(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, _gated = corpus
    record = _epoch(boundary).model_dump(mode="json")
    _raw_note(repo, boundary, json.dumps([record, record]))

    with pytest.raises(AttestationError, match="duplicate"):
        read_gate_epochs(repo)


def test_record_anchored_to_another_commit_is_corrupt(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, gated = corpus
    _raw_note(repo, gated, json.dumps([_epoch(boundary).model_dump(mode="json")]))

    with pytest.raises(AttestationError, match="corrupt"):
        read_gate_epochs(repo)


def test_record_anchored_to_a_non_commit_object_is_corrupt(
    corpus: tuple[Path, str, str],
) -> None:
    """A matching 40-hex object id is not enough: the epoch is defined on
    the last pre-epoch corpus state, which must be a commit."""
    repo, _boundary, _gated = corpus
    blob = _git(repo, "hash-object", "-w", "boundary.md")
    _raw_note(repo, blob, json.dumps([_epoch(blob).model_dump(mode="json")]))

    with pytest.raises(AttestationError, match="corrupt|commit"):
        read_gate_epochs(repo)


def test_corrupt_record_after_a_good_one_is_still_found(tmp_path: Path) -> None:
    """Skip-before-match: a valid record first must not stop the walk
    before a later note is validated."""
    repo = tmp_path / "corpus"
    _init(repo)
    commits = [_commit(repo, f"c{i}", f"2026-09-0{i + 1}T00:00:00Z") for i in range(3)]
    for index, commit in enumerate(commits[:2]):
        record = _epoch(commit, parser_version=index + 1).model_dump(mode="json")
        _raw_note(repo, commit, json.dumps([record]))
    _raw_note(repo, commits[2], "garbage")

    with pytest.raises(AttestationError, match="unparseable"):
        read_gate_epochs(repo)


def test_reader_does_not_need_the_boundary_commit(tmp_path: Path) -> None:
    """A --depth 1 consumer clone lacks the boundary commit's object; the
    record must still read (the droplet clone's depth is unknown)."""
    origin = tmp_path / "origin"
    _init(origin)
    boundary = _commit(origin, "boundary", BOUNDARY_DATE)
    _commit(origin, "gated", GATE_DATE)
    record = _epoch(boundary)
    write_gate_epoch(origin, record)
    clone = tmp_path / "shallow"
    _git(tmp_path, "clone", "--depth", "1", f"file://{origin}", str(clone))
    fetch_gate_epochs(clone)

    missing = subprocess.run(
        ["git", "cat-file", "-e", f"{boundary}^{{commit}}"], cwd=clone, check=False
    )
    assert missing.returncode != 0
    assert read_gate_epochs(clone) == {2: record}


def test_unreadable_epoch_ref_is_a_broken_channel(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, _gated = corpus
    _git(repo, "update-ref", EPOCH_NOTES_REF, _git(repo, "rev-parse", f"{boundary}^{{tree}}"))

    with pytest.raises(AttestationError, match="unreadable"):
        read_gate_epochs(repo)


# ---------- writing ----------


def test_identical_rewrite_is_a_no_op(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, _gated = corpus
    write_gate_epoch(repo, _epoch(boundary))
    notes_head = _git(repo, "rev-parse", EPOCH_NOTES_REF)

    later = _epoch(boundary, recorded_at=datetime(2026, 9, 28, tzinfo=UTC))
    assert write_gate_epoch(repo, later) is False

    assert _git(repo, "rev-parse", EPOCH_NOTES_REF) == notes_head
    assert read_gate_epochs(repo)[2].recorded_at == datetime(2026, 9, 27, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "overrides",
    [
        {"epoch_at": datetime(2026, 9, 4, 8, 45, 37, tzinfo=UTC)},
        {"source": "sync-run"},
        {"evidence": "lovspor sync run 1"},
    ],
)
def test_different_record_for_an_existing_version_is_refused(
    corpus: tuple[Path, str, str],
    overrides: dict[str, object],
) -> None:
    repo, boundary, _gated = corpus
    write_gate_epoch(repo, _epoch(boundary))

    with pytest.raises(AttestationError, match="immutable"):
        write_gate_epoch(repo, _epoch(boundary, **overrides))


def test_records_for_two_versions_share_one_boundary(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, _gated = corpus
    v2 = _epoch(boundary)
    v3 = _epoch(boundary, parser_version=3, epoch_at=datetime(2026, 9, 5, tzinfo=UTC))

    write_gate_epoch(repo, v2)
    write_gate_epoch(repo, v3)

    assert read_gate_epochs(repo) == {2: v2, 3: v3}


def test_boundary_at_or_after_the_epoch_is_refused(corpus: tuple[Path, str, str]) -> None:
    repo, _boundary, gated = corpus

    with pytest.raises(AttestationError, match="boundary"):
        write_gate_epoch(repo, _epoch(gated))
    at_instant = _epoch(gated, epoch_at=datetime(2026, 9, 4, 8, 47, 34, tzinfo=UTC))
    with pytest.raises(AttestationError, match="boundary"):
        write_gate_epoch(repo, at_instant)
    assert read_gate_epochs(repo) == {}


def test_epoch_later_than_the_first_attested_state_is_refused(
    corpus: tuple[Path, str, str],
) -> None:
    repo, boundary, gated = corpus
    _attest(repo, gated)
    late = _epoch(boundary, epoch_at=datetime(2026, 9, 4, 8, 47, 35, tzinfo=UTC))

    with pytest.raises(AttestationError, match="earliest attested"):
        write_gate_epoch(repo, late)
    assert read_gate_epochs(repo) == {}


def test_epoch_at_the_first_attested_author_date_is_accepted(
    corpus: tuple[Path, str, str],
) -> None:
    repo, boundary, gated = corpus
    _attest(repo, gated)

    exact = _epoch(boundary, epoch_at=datetime(2026, 9, 4, 8, 47, 34, tzinfo=UTC))
    assert write_gate_epoch(repo, exact) is True


def test_attestations_under_another_version_do_not_bound_the_epoch(
    corpus: tuple[Path, str, str],
) -> None:
    repo, boundary, gated = corpus
    _attest(repo, gated, version=1)
    late = _epoch(boundary, epoch_at=datetime(2026, 9, 5, tzinfo=UTC))

    assert write_gate_epoch(repo, late) is True


def test_unresolvable_boundary_is_refused(corpus: tuple[Path, str, str]) -> None:
    repo, _boundary, _gated = corpus

    with pytest.raises(AttestationError, match="cannot resolve"):
        check_gate_epoch(repo, _epoch("b" * 40))


def test_check_is_the_write_without_the_write(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, _gated = corpus
    record = _epoch(boundary)

    assert check_gate_epoch(repo, record) is True
    assert read_gate_epochs(repo) == {}
    write_gate_epoch(repo, record)
    assert check_gate_epoch(repo, record) is False


# ---------- attested commits ----------


def test_attested_commits_filters_by_version(tmp_path: Path) -> None:
    """Skip-before-match: a commit attested only under another version,
    listed first, must not end the walk."""
    repo = tmp_path / "corpus"
    _init(repo)
    shas = [_commit(repo, f"c{i}", f"2026-09-0{i + 1}T00:00:00Z") for i in range(3)]
    _attest(repo, shas[0], version=1)
    _attest(repo, shas[1], version=2)
    _attest(repo, shas[2], version=2)

    assert sorted(attested_commits(repo, 2)) == sorted(shas[1:])
    assert attested_commits(repo, 1) == [shas[0]]
    assert attested_commits(repo, 3) == []


def test_attested_commits_refuses_a_corrupt_attestation_note(
    corpus: tuple[Path, str, str],
) -> None:
    repo, _boundary, gated = corpus
    _git(repo, "notes", f"--ref={ATTESTATION_NOTES_REF}", "add", "-m", "junk", gated)

    with pytest.raises(AttestationError, match="unparseable"):
        attested_commits(repo, 2)


# ---------- transport ----------


def _bare_origin_with_clone(tmp_path: Path) -> tuple[Path, Path, str]:
    work = tmp_path / "work"
    _init(work)
    boundary = _commit(work, "boundary", BOUNDARY_DATE)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "--bare", str(work), str(origin))
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), str(clone))
    _git(clone, "config", "user.email", "test@example.com")
    _git(clone, "config", "user.name", "Test")
    return origin, clone, boundary


def test_push_then_fetch_round_trips_the_record(tmp_path: Path) -> None:
    origin, clone, boundary = _bare_origin_with_clone(tmp_path)
    record = _epoch(boundary)
    write_gate_epoch(clone, record)

    push_gate_epochs(clone)

    other = tmp_path / "other"
    _git(tmp_path, "clone", str(origin), str(other))
    assert read_gate_epochs(other) == {}
    fetch_gate_epochs(other)
    assert read_gate_epochs(other) == {2: record}


def test_fetch_from_an_origin_without_the_ref_is_the_bootstrap(tmp_path: Path) -> None:
    _origin, clone, _boundary = _bare_origin_with_clone(tmp_path)

    fetch_gate_epochs(clone)

    assert read_gate_epochs(clone) == {}


def test_fetch_from_an_unreachable_remote_is_a_broken_channel(tmp_path: Path) -> None:
    _origin, clone, _boundary = _bare_origin_with_clone(tmp_path)
    _git(clone, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    with pytest.raises(AttestationError, match="cannot reach origin"):
        fetch_gate_epochs(clone)


def test_rejected_push_is_a_typed_failure(tmp_path: Path) -> None:
    _origin, clone, boundary = _bare_origin_with_clone(tmp_path)
    write_gate_epoch(clone, _epoch(boundary))
    _git(clone, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    with pytest.raises(AttestationError, match="failed to push"):
        push_gate_epochs(clone)


# ---------- corruption probes (codex-tests round 1 follow-up) ----------


def test_record_on_an_annotated_tag_is_corrupt(corpus: tuple[Path, str, str]) -> None:
    """Even when the record names the tag's own id: a tag is not a state."""
    repo, boundary, _gated = corpus
    _git(repo, "tag", "-a", "v1", "-m", "tag", boundary)
    tag = _git(repo, "rev-parse", "v1")
    _raw_note(repo, tag, json.dumps([_epoch(tag).model_dump(mode="json")]))

    with pytest.raises(AttestationError, match="a tag and not a commit"):
        read_gate_epochs(repo)


def test_record_on_a_tree_is_corrupt(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, _gated = corpus
    tree = _git(repo, "rev-parse", f"{boundary}^{{tree}}")
    _raw_note(repo, tree, json.dumps([_epoch(tree).model_dump(mode="json")]))

    with pytest.raises(AttestationError, match="a tree and not a commit"):
        read_gate_epochs(repo)


def test_non_commit_anchor_after_a_good_record_is_still_found(tmp_path: Path) -> None:
    repo = tmp_path / "corpus"
    _init(repo)
    good = _commit(repo, "good", BOUNDARY_DATE)
    _raw_note(repo, good, json.dumps([_epoch(good).model_dump(mode="json")]))
    blob = _git(repo, "hash-object", "-w", "good.md")
    record = _epoch(blob, parser_version=3).model_dump(mode="json")
    _raw_note(repo, blob, json.dumps([record]))

    with pytest.raises(AttestationError, match="not a commit"):
        read_gate_epochs(repo)


def test_writer_refuses_a_boundary_commit_that_does_not_exist(
    corpus: tuple[Path, str, str],
) -> None:
    repo, _boundary, _gated = corpus

    with pytest.raises(AttestationError, match="cannot resolve"):
        write_gate_epoch(repo, _epoch("0" * 40))
    assert read_gate_epochs(repo) == {}


@pytest.mark.parametrize(
    "payload",
    [
        "[{",
        "{}",
        "null",
        "[]garbage",
        '[{"parser_version": 2, "epoch_at": "2026-09-04T08:45:36Z"}]',
    ],
)
def test_malformed_or_incomplete_note_is_a_broken_channel(
    corpus: tuple[Path, str, str],
    payload: str,
) -> None:
    repo, boundary, _gated = corpus
    _raw_note(repo, boundary, payload)

    with pytest.raises(AttestationError, match="unparseable"):
        read_gate_epochs(repo)


@pytest.mark.parametrize("field", ["epoch_at", "recorded_at"])
@pytest.mark.parametrize("value", ["2026-09-04T10:45:36+02:00", "2026-09-04T03:45:36-05:00"])
def test_non_utc_instants_are_refused(field: str, value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _epoch("a" * 40, **{field: value})

    assert "Value error, must be a UTC instant (offset +00:00 or Z)" in str(caught.value)


def test_a_stored_non_utc_record_is_a_broken_channel(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, _gated = corpus
    record = {**_epoch(boundary).model_dump(mode="json"), "epoch_at": "2026-09-04T10:45:36+02:00"}
    _raw_note(repo, boundary, json.dumps([record]))

    with pytest.raises(AttestationError, match="unparseable"):
        read_gate_epochs(repo)


def test_utc_spelled_as_zero_offset_is_the_same_instant() -> None:
    assert _epoch("a" * 40, epoch_at="2026-09-04T08:45:36+00:00").epoch_at == EPOCH_AT


# ---------- publishing a record a rejected push left behind ----------


def test_publish_pushes_a_local_only_record_once(tmp_path: Path) -> None:
    origin, clone, boundary = _bare_origin_with_clone(tmp_path)
    write_gate_epoch(clone, _epoch(boundary))

    assert publish_gate_epochs(clone) is True
    assert publish_gate_epochs(clone) is False
    assert _git(origin, "rev-parse", EPOCH_NOTES_REF) == _git(clone, "rev-parse", EPOCH_NOTES_REF)


def test_publish_without_a_local_record_pushes_nothing(tmp_path: Path) -> None:
    origin, clone, _boundary = _bare_origin_with_clone(tmp_path)

    assert publish_gate_epochs(clone) is False
    assert (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", EPOCH_NOTES_REF], cwd=origin, check=False
        ).returncode
        != 0
    )


def test_publish_never_overwrites_a_different_remote_record(tmp_path: Path) -> None:
    origin, clone, boundary = _bare_origin_with_clone(tmp_path)
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(origin), str(other))
    _git(other, "config", "user.email", "o@example.com")
    _git(other, "config", "user.name", "Other")
    write_gate_epoch(other, _epoch(boundary, evidence="lovspor sync run 1"))
    push_gate_epochs(other)
    remote_before = _git(origin, "rev-parse", EPOCH_NOTES_REF)
    write_gate_epoch(clone, _epoch(boundary))

    with pytest.raises(AttestationError, match="failed to push"):
        publish_gate_epochs(clone)
    assert _git(origin, "rev-parse", EPOCH_NOTES_REF) == remote_before


def test_publish_to_an_unreachable_remote_is_a_broken_channel(tmp_path: Path) -> None:
    _origin, clone, boundary = _bare_origin_with_clone(tmp_path)
    write_gate_epoch(clone, _epoch(boundary))
    _git(clone, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    with pytest.raises(AttestationError, match="cannot reach origin"):
        publish_gate_epochs(clone)


# ---------- exact diagnostics and formats, on real git ----------


def test_the_written_note_is_the_sorted_key_json_list(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, _gated = corpus
    record = _epoch(boundary)

    write_gate_epoch(repo, record)

    stored = _git(repo, "notes", f"--ref={EPOCH_NOTES_REF}", "show", boundary)
    assert stored == json.dumps([record.model_dump(mode="json")], sort_keys=True)


def test_a_failed_note_write_names_the_commit_and_git_diagnostic(
    corpus: tuple[Path, str, str],
) -> None:
    repo, boundary, _gated = corpus
    lock = repo / ".git" / f"{EPOCH_NOTES_REF}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("")

    with pytest.raises(AttestationError) as caught:
        write_gate_epoch(repo, _epoch(boundary))

    message = str(caught.value)
    assert message.startswith(f"failed to record the gate epoch on {boundary}: ")
    assert ".lock" in message
    assert not message.endswith("\n")


def test_boundary_author_date_is_compared_as_an_instant_with_its_offset(tmp_path: Path) -> None:
    """%aI carries the committer's offset: 10:45:36+02:00 IS the epoch instant."""
    repo = tmp_path / "corpus"
    _init(repo)
    at_epoch = _commit(repo, "offset", "2026-09-04T10:45:36+02:00")
    before = _commit(repo, "before", "2026-09-04T10:45:35+02:00")

    with pytest.raises(AttestationError, match="not before the epoch"):
        check_gate_epoch(repo, _epoch(at_epoch))
    assert check_gate_epoch(repo, _epoch(before)) is True


def test_an_unreadable_note_blob_names_the_blob_and_git_diagnostic(
    corpus: tuple[Path, str, str],
) -> None:
    repo, boundary, _gated = corpus
    write_gate_epoch(repo, _epoch(boundary))
    blob = _git(repo, "notes", f"--ref={EPOCH_NOTES_REF}", "list", boundary)
    (repo / ".git" / "objects" / blob[:2] / blob[2:]).unlink()

    with pytest.raises(AttestationError) as caught:
        read_gate_epochs(repo)

    message = str(caught.value)
    assert message.startswith(f"note blob {blob} unreadable: ")
    assert blob in message.removeprefix(f"note blob {blob} unreadable: ")
    assert not message.endswith("\n")


def test_an_unparseable_note_names_its_blob(corpus: tuple[Path, str, str]) -> None:
    repo, boundary, _gated = corpus
    _raw_note(repo, boundary, "not json")
    blob = _git(repo, "notes", f"--ref={EPOCH_NOTES_REF}", "list", boundary)

    with pytest.raises(AttestationError) as caught:
        read_gate_epochs(repo)

    assert str(caught.value).startswith(f"note {blob} is unparseable — a broken evidence channel: ")
    assert "Invalid JSON" in str(caught.value)


def test_an_unparseable_attestation_note_names_its_blob(corpus: tuple[Path, str, str]) -> None:
    repo, _boundary, gated = corpus
    _git(repo, "notes", f"--ref={ATTESTATION_NOTES_REF}", "add", "-m", "junk", gated)
    blob = _git(repo, "notes", f"--ref={ATTESTATION_NOTES_REF}", "list", gated)

    with pytest.raises(AttestationError) as caught:
        attested_commits(repo, 2)

    assert str(caught.value).startswith(f"note {blob} is unparseable — a broken evidence channel: ")


def test_an_unreadable_anchor_check_keeps_git_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside a repository the batch check itself fails; its stderr is the
    diagnostic, stripped, and nothing reads that as 'missing'."""
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    with pytest.raises(AttestationError) as caught:
        attestation_module._require_commit_anchors(plain, ["a" * 40])

    message = str(caught.value)
    assert message.startswith("gate-epoch anchors unreadable: ")
    assert "not a git repository" in message
    assert not message.endswith("\n")


def test_two_anchors_are_checked_one_per_line(tmp_path: Path) -> None:
    """Both anchors reach the batch check: a blob listed second is still
    judged (the input is newline-delimited, one id per line)."""
    repo = tmp_path / "corpus"
    _init(repo)
    commit = _commit(repo, "c", BOUNDARY_DATE)
    blob = _git(repo, "hash-object", "-w", "c.md")

    attestation_module._require_commit_anchors(repo, [commit, "f" * 40])
    with pytest.raises(AttestationError, match=f"attached to {blob}, a blob"):
        attestation_module._require_commit_anchors(repo, [commit, blob])
