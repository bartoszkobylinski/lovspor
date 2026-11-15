"""The reviewed owner-exception list: schema 2, canonical digest, owner approval (#921).

Owner decision on #921, option (a): the served owner stays, the first owner is
kept beside it, every exception carries Git evidence, and the list counts only
once the owner approves its exact digest. These cases pin the list format,
the digest, the approval command and the packaged (unsigned) list.
"""

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.errors import IdentitySchemaError
from lovspor.identity.owner_exceptions import (
    REASSIGNMENTS_PATH,
    Approval,
    ReassignmentList,
    ThresholdException,
    approve_list,
    list_digest,
    list_json,
    load_reassignments,
    read_list,
)
from lovspor.identity.registry import PriorHolder, SlugOwner, SlugRegistry, registry_json
from tests.unit.identity_owner_fixtures import (
    FIXTURE_U1,
    fixture_exception,
    pending_list,
    signed,
)
from tests.unit.test_identity_migration import NEW, OLD

#: The digest of the packaged, unsigned list: any content edit changes it (#921).
PACKAGED_DIGEST = "71c9b137f5a0960916528024c7ec8ffd58672d3bdcdcfaf6b836d4765d79365c"
BASE = "c" * 40
R1, R4 = "1" * 40, "4" * 40


def _listed() -> ReassignmentList:
    exception = {**fixture_exception(R1, R4), "slug": "endr-i-høstingsforskriften"}
    return pending_list(BASE, [FIXTURE_U1], [exception])


def _write(tmp_path: Path, listed: ReassignmentList) -> Path:
    path = tmp_path / "list.json"
    path.write_text(list_json(listed), encoding="utf-8")
    return path


# --- the digest ---


def test_the_digest_is_sha256_of_the_canonical_list_without_its_approval() -> None:
    listed = _listed()
    data = json.loads(list_json(listed))
    del data["approval"]
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    assert list_digest(listed) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert "høstingsforskriften" in canonical


def test_approving_keeps_the_digest_and_any_content_edit_changes_it() -> None:
    listed = _listed()
    edited = listed.model_copy(
        update={"corpus": listed.corpus.model_copy(update={"u1_threshold": R1})}
    )

    assert list_digest(signed(listed)) == list_digest(listed)
    assert list_digest(edited) != list_digest(listed)


def test_formatting_does_not_change_the_digest(tmp_path: Path) -> None:
    path = _write(tmp_path, _listed())
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8"))), encoding="utf-8")

    assert load_reassignments(path).digest == list_digest(_listed())


# --- the list as the gate reads it ---


def test_a_pending_list_lists_nothing(tmp_path: Path) -> None:
    reviewed = load_reassignments(_write(tmp_path, _listed()))

    assert (reviewed.approved, reviewed.changes, reviewed.threshold) == (False, {}, {})
    assert reviewed.state == f"BLOCKED: pending owner approval of digest {reviewed.digest}"


def test_an_approved_list_lists_both_kinds(tmp_path: Path) -> None:
    reviewed = load_reassignments(_write(tmp_path, signed(_listed())))

    assert reviewed.approved is True
    assert reviewed.changes == {("forskrifter", "x"): (NEW, OLD)}
    exception = reviewed.threshold[("forskrifter", "endr-i-høstingsforskriften")]
    assert (exception.first_owner_doc_id, exception.served_owner_doc_id) == (OLD, NEW)
    assert reviewed.state == f"approved by fixture owner on 2026-11-14 ({reviewed.digest})"


def test_an_approval_of_another_digest_lists_nothing(tmp_path: Path) -> None:
    listed = signed(_listed())
    stale = listed.model_copy(update={"reassignments": ()})
    reviewed = load_reassignments(_write(tmp_path, stale))

    assert (reviewed.approved, reviewed.changes, reviewed.threshold) == (False, {}, {})
    assert reviewed.state == (
        f"BLOCKED: approval digest {listed.approval.digest} is not the list's {reviewed.digest}"
    )


@pytest.mark.parametrize(
    "approval",
    [
        {"status": "approved", "digest": "d" * 64, "approved_by": "o"},
        {"status": "approved", "digest": "d" * 64, "approved_on": "2026-11-14"},
        {"status": "approved", "approved_by": "o", "approved_on": "2026-11-14"},
        {"status": "approved", "digest": "d" * 64, "approved_by": "", "approved_on": "2026-11-14"},
        {"status": "pending-owner-approval", "digest": "d" * 64},
        {"status": "pending-owner-approval", "approved_by": "o"},
        {"status": "pending-owner-approval", "approved_on": "2026-11-14"},
        {"status": "approved", "digest": "D" * 64, "approved_by": "o", "approved_on": "2026-11-14"},
    ],
    ids=[
        "no-date",
        "no-approver",
        "no-digest",
        "empty-approver",
        "pending-digest",
        "pending-approver",
        "pending-date",
        "digest-not-hex",
    ],
)
def test_an_incomplete_or_half_approval_is_unreadable(
    tmp_path: Path, approval: dict[str, str]
) -> None:
    data = json.loads(list_json(_listed()))
    path = tmp_path / "list.json"
    path.write_text(json.dumps({**data, "approval": approval}), encoding="utf-8")

    with pytest.raises(IdentitySchemaError, match="unreadable owner reassignment list"):
        load_reassignments(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_owner_first_commit", "HEAD"),
        ("served_owner_first_commit", "abc123"),
        ("first_owner_first_commit", "--all" + "0" * 35),
    ],
)
def test_evidence_must_be_a_full_commit_sha(field: str, value: str) -> None:
    entry = fixture_exception(R1, R4)
    with pytest.raises(ValueError, match="evidence"):
        ThresholdException.model_validate(
            {**entry, "evidence": {**entry["evidence"], field: value}}
        )  # type: ignore[dict-item]


def test_an_exception_whose_two_owners_are_one_is_refused() -> None:
    with pytest.raises(ValueError, match="first and served owner are one"):
        ThresholdException.model_validate({**fixture_exception(R1, R4), "served_owner_doc_id": OLD})


@pytest.mark.parametrize(
    "content",
    [
        "{",
        '{"reassignments": [], "threshold_exceptions": []}',
        '{"schema_version": 1, "reassignments": [], "threshold_exceptions": []}',
    ],
    ids=["malformed", "no-version", "schema-1"],
)
def test_an_unreadable_list_fails_closed(tmp_path: Path, content: str) -> None:
    path = tmp_path / "list.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(IdentitySchemaError, match="unreadable owner reassignment list"):
        load_reassignments(path)


@pytest.mark.parametrize("kind", ["reassignments", "threshold_exceptions"])
def test_an_address_listed_twice_is_refused(tmp_path: Path, kind: str) -> None:
    listed = _listed()
    twice = listed.model_copy(update={kind: getattr(listed, kind) * 2})

    with pytest.raises(IdentitySchemaError, match="listed twice"):
        load_reassignments(_write(tmp_path, signed(twice)))


# --- the approval command ---


def test_approve_writes_the_owner_s_sign_off(tmp_path: Path) -> None:
    path = _write(tmp_path, _listed())
    digest = list_digest(_listed())

    assert approve_list(path, digest, "Owner Name", date(2026, 11, 15)) == digest

    listed = read_list(path)
    assert listed.approval == Approval(
        status="approved", digest=digest, approved_by="Owner Name", approved_on=date(2026, 11, 15)
    )
    assert load_reassignments(path).approved is True
    assert path.read_text(encoding="utf-8") == list_json(listed)


@pytest.mark.parametrize(
    ("digest", "by", "match"),
    [
        ("e" * 64, "Owner", "is not the list's digest"),
        (None, "", "refusing the approval"),
    ],
    ids=["other-digest", "empty-approver"],
)
def test_approve_refuses_and_writes_nothing(
    tmp_path: Path, digest: str | None, by: str, match: str
) -> None:
    path = _write(tmp_path, _listed())
    before = path.read_bytes()

    with pytest.raises(IdentitySchemaError, match=match):
        approve_list(path, digest or list_digest(_listed()), by, date(2026, 11, 15))
    assert path.read_bytes() == before


def test_an_approved_list_is_not_signed_again(tmp_path: Path) -> None:
    path = _write(tmp_path, signed(_listed()))
    before = path.read_bytes()

    with pytest.raises(IdentitySchemaError, match="already approved; nothing written"):
        approve_list(path, list_digest(_listed()), "Other", date(2026, 11, 16))
    assert path.read_bytes() == before


def test_cli_prints_the_digest_and_state(tmp_path: Path) -> None:
    path = _write(tmp_path, _listed())

    result = CliRunner().invoke(app, ["ops", "identity-exceptions-digest", "--path", str(path)])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary == {
        "approved": False,
        "digest": list_digest(_listed()),
        "reassignments": 1,
        "reviewed_base": BASE,
        "state": f"BLOCKED: pending owner approval of digest {list_digest(_listed())}",
        "threshold_exceptions": 1,
        "u1_threshold": BASE,
    }


def _approve(path: Path, digest: str, on: str) -> object:
    args = ["ops", "identity-exceptions-approve", "--path", str(path), "--digest", digest]
    return CliRunner().invoke(app, [*args, "--by", "Owner", "--on", on])


def test_cli_approves_by_digest(tmp_path: Path) -> None:
    path = _write(tmp_path, _listed())
    digest = list_digest(_listed())

    result = _approve(path, digest, "2026-11-15")

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    assert result.output == (  # type: ignore[attr-defined]
        f"approved {path} by Owner on 2026-11-15, digest {digest}\n"
    )
    assert read_list(path).approval.approved_on == date(2026, 11, 15)


@pytest.mark.parametrize(
    ("digest", "on", "message"),
    [
        ("e" * 64, "2026-11-15", "is not the list's digest"),
        (None, "15.11.2026", "not a YYYY-MM-DD"),
    ],
    ids=["other-digest", "bad-date"],
)
def test_cli_refusal_exits_2_and_writes_nothing(
    tmp_path: Path, digest: str | None, on: str, message: str
) -> None:
    path = _write(tmp_path, _listed())
    before = path.read_bytes()

    result = _approve(path, digest or list_digest(_listed()), on)

    assert result.exit_code == 2  # type: ignore[attr-defined]
    assert message in result.output  # type: ignore[attr-defined]
    assert path.read_bytes() == before


def test_cli_digest_of_an_unreadable_list_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("{", encoding="utf-8")

    result = CliRunner().invoke(app, ["ops", "identity-exceptions-digest", "--path", str(path)])

    assert result.exit_code == 2
    assert "unreadable owner reassignment list" in result.output


# --- the packaged list: unsigned, evidence-bound ---


def test_the_packaged_list_is_unsigned_with_its_pinned_digest() -> None:
    listed = read_list(REASSIGNMENTS_PATH)

    assert listed.approval == Approval(status="pending-owner-approval")
    assert list_digest(listed) == PACKAGED_DIGEST
    assert REASSIGNMENTS_PATH.read_text(encoding="utf-8") == list_json(listed)


def test_the_packaged_list_is_bound_to_the_reviewed_plan() -> None:
    listed = read_list(REASSIGNMENTS_PATH)

    assert listed.corpus.reviewed_base == "c310127ecc65192f8260eab56bd3809ce0354130"
    assert listed.corpus.u1_threshold == "f6d7e25bb041aea043ca9243de4e32131054f8bc"
    assert Counter(r.kind for r in listed.reassignments) == {"u1-rename": 9, "history-repair": 4}
    assert len(listed.threshold_exceptions) == 56
    assert Counter(t.directory for t in listed.threshold_exceptions) == {
        "forskrifter": 49,
        "lover": 7,
    }


def test_the_packaged_bilforskriften_exception_keeps_both_owners() -> None:
    listed = read_list(REASSIGNMENTS_PATH)
    exception = next(t for t in listed.threshold_exceptions if t.slug == "endr-i-bilforskriften")

    assert (exception.first_owner_doc_id, exception.served_owner_doc_id) == (
        "sf-20260324-0472",
        "sf-20260513-0853",
    )
    assert exception.evidence.first_owner_first_commit == "3dddeca2a2587bec15adb8698ad78d17c40cfaed"
    assert exception.evidence.served_owner_first_commit.startswith("479c42e6a")


def test_the_packaged_downstream_readings_are_recorded() -> None:
    """Advisory: the production-pin harness and the rule simulation at the reviewed base."""
    listed = read_list(REASSIGNMENTS_PATH)
    harness = Counter(t.advisory[0].http_status for t in listed.threshold_exceptions)
    predicted = Counter(
        t.advisory[1].http_status.split("->")[0] for t in listed.threshold_exceptions
    )

    assert harness == {"200": 37, "301": 1, "410": 18}
    assert predicted == {"200": 36, "301": 1, "410": 19}
    assert {t.advisory[0].lovverk_commit[:9] for t in listed.threshold_exceptions} == {"3c4f1865e"}


# --- the corpus registry: two owners, unchanged on disk ---


def test_a_first_holder_entry_has_one_owner_for_both_roles() -> None:
    entry = SlugOwner(owner=OLD, assigned_at="2026")

    assert (entry.first_owner_doc_id, entry.served_owner_doc_id) == (OLD, OLD)


def test_a_threshold_exception_s_first_owner_is_its_oldest_prior_holder() -> None:
    oldest = PriorHolder(doc_id="sf-1", first_commit=R1, last_commit=R1)
    later = PriorHolder(doc_id="sf-2", first_commit=R4, last_commit=R4)
    entry = SlugOwner(
        owner=NEW, assigned_at="2026", basis="threshold-exception", prior_holders=(oldest, later)
    )

    assert (entry.first_owner_doc_id, entry.served_owner_doc_id) == ("sf-1", NEW)


def test_slugs_json_keeps_its_schema_1_format() -> None:
    """Rollback: ``slugs.json`` gains no field, so any lovspor version reads it."""
    first = PriorHolder(doc_id=OLD, first_commit=R1, last_commit=R1)
    entry = SlugOwner(
        owner=NEW, assigned_at="2026", basis="threshold-exception", prior_holders=(first,)
    )
    data = json.loads(registry_json(SlugRegistry().assign("forskrifter", "x", entry)))

    assert data["schema_version"] == 1
    assert set(data["slugs"]["forskrifter"]["x"]) == {
        "owner",
        "assigned_at",
        "basis",
        "prior_holders",
    }
