"""FC8: version lineage, chain integrity and machine-readable diffs (#738).

Synthetic evidence only (the invented Prop. 901 L). Every record is private
ingestion: nothing here is fetched, signed, published or made eligible.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization import AuthorizationRequest
from lovspor.forarbeider.policy_evaluation import decision_text, evaluate_policy
from lovspor.forarbeider.publication_policy import policy_text
from lovspor.forarbeider.span_assessment import assess_document, assessment_text
from lovspor.forarbeider.span_assessment_models import RightsClass
from lovspor.forarbeider.version_diff import diff_snapshots, diff_text, load_snapshot
from lovspor.forarbeider.version_diff_models import VersionDiff
from lovspor.forarbeider.version_lineage import (
    Observation,
    entry_text,
    parse_entry,
    record_observation,
    verification_text,
    verify_lineage,
)
from lovspor.forarbeider.version_lineage_models import (
    STORAGE_GUARANTEE,
    LineageEntry,
    LineageVerification,
)
from lovspor.forarbeider.version_manifest import build_version_manifest, manifest_text
from tests.unit.forarbeider_authorization_support import (
    OWNER_NAMESPACES,
    full_request,
    new_key,
    signer_line,
    trust_root,
)
from tests.unit.forarbeider_authorization_support import policy as synthetic_policy
from tests.unit.forarbeider_lineage_support import (
    CHANGED_BODY,
    LATER,
    LATEST,
    OTHER_PDF,
    Chain,
    at,
    chain,
    changed,
    codes,
    forged,
    linked,
    original,
    without,
)
from tests.unit.forarbeider_version_support import PDF, PDF_URL, evidence, pages
from tests.unit.test_forarbeider_document_extract import WHEN


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def refused(reason: RefusalReason) -> Any:
    return pytest.raises(ForarbeiderRefusedError, match=reason.value)


# --- first version -----------------------------------------------------------------


def test_a_first_observation_is_the_first_version_bound_to_every_artifact() -> None:
    source = original()
    recorded = record_observation((), Observation(source))
    entry = recorded.entry
    manifest = build_version_manifest(source)
    assert (entry.sequence, entry.content_change) == (0, "first_version")
    assert (entry.previous_entry_sha256, entry.previous_version_id) == (None, None)
    assert entry.version_id == manifest.version_id
    assert entry.document_id == manifest.content.document.document_id
    assert entry.manifest_sha256 == sha(manifest_text(manifest).encode())
    assert entry.assessment_sha256 == sha(assessment_text(assess_document(source)).encode())
    assert (entry.policy_sha256, entry.policy_decision_sha256, entry.authorization_sha256) == (
        None,
        None,
        None,
    )
    assert entry.observed_at == WHEN
    assert recorded.raw == entry_text(entry).encode()
    assert (entry.release_eligible, entry.published) == (False, False)


def test_the_first_version_names_every_raw_source_and_keeps_them_as_artifacts() -> None:
    source = linked()
    recorded = record_observation((), Observation(source))
    given = [*source.pages, source.landing, *source.captures]
    raws = sorted({sha(page.payload) for page in given if page is not None})
    assert list(recorded.entry.source_sha256s) == raws
    for digest in [*raws, recorded.entry.manifest_sha256, recorded.entry.assessment_sha256]:
        assert sha(recorded.artifacts[digest]) == digest


def test_a_single_entry_chain_verifies() -> None:
    built = chain(original())
    verification = verify_lineage(built.raws, built.artifacts)
    assert verification.status == "verified"
    assert verification.findings == ()
    assert (verification.content_versions, verification.observations) == (1, 1)
    assert verification.head_entry_sha256 == sha(built.raws[0])
    assert verification.storage_guarantee == STORAGE_GUARANTEE
    assert (verification.release_eligible, verification.published) == (False, False)


def test_the_storage_limitation_is_stated_in_every_verification() -> None:
    assert "append_only_storage_not_proven" in STORAGE_GUARANTEE
    built = chain(original())
    assert STORAGE_GUARANTEE in verification_text(verify_lineage(built.raws, built.artifacts))


# --- identical replay ---------------------------------------------------------------


def test_the_same_bytes_read_later_are_an_observation_not_a_new_version() -> None:
    built = chain(original(WHEN), original(LATER))
    first, second = built.entries()
    assert second.content_change == "unchanged"
    assert second.version_id == first.version_id == second.previous_version_id
    assert second.manifest_sha256 != first.manifest_sha256
    older = load_snapshot(built.raws[0], built.artifacts).assessment
    newer = load_snapshot(built.raws[1], built.artifacts).assessment
    assert older.entries == newer.entries
    assert second.observed_at == LATER
    verification = verify_lineage(built.raws, built.artifacts)
    assert verification.status == "verified"
    assert (verification.content_versions, verification.observations) == (1, 2)


def test_a_byte_identical_rerun_appends_nothing() -> None:
    built = chain(original())
    again = built.add(original())
    assert again.appended is False
    assert again.raw == built.raws[0]
    assert len(built.raws) == 1


def test_recording_is_deterministic() -> None:
    first = record_observation((), Observation(original()))
    second = record_observation((), Observation(original()))
    assert first.raw == second.raw
    assert first.artifacts == second.artifacts


def test_page_order_does_not_change_the_entry() -> None:
    source = original()
    shuffled = source._replace(pages=tuple(reversed(source.pages)))
    assert record_observation((), Observation(shuffled)).raw == (
        record_observation((), Observation(source)).raw
    )


# --- new version --------------------------------------------------------------------


def test_changed_bytes_are_a_new_version_linked_to_the_previous_one() -> None:
    built = chain(original(), changed())
    first, second = built.entries()
    assert (second.sequence, second.content_change) == (1, "new_version")
    assert second.version_id != first.version_id
    assert second.previous_version_id == first.version_id
    assert second.previous_entry_sha256 == sha(built.raws[0])
    verification = verify_lineage(built.raws, built.artifacts)
    assert verification.status == "verified"
    assert (verification.content_versions, verification.observations) == (2, 2)
    assert [link.content_change for link in verification.chain] == ["first_version", "new_version"]


def test_a_replay_after_a_new_version_keeps_the_new_version() -> None:
    built = chain(original(), changed(LATER), changed(LATEST))
    third = built.entries()[2]
    assert third.content_change == "unchanged"
    assert third.previous_version_id == built.entries()[1].version_id
    assert verify_lineage(built.raws, built.artifacts).content_versions == 2


def test_a_version_returning_after_another_is_refused_as_a_cycle() -> None:
    built = chain(original(WHEN), changed(LATER))
    with refused(RefusalReason.LINEAGE_CONFLICT):
        built.add(original(LATEST))


def test_an_observation_older_than_the_head_is_refused() -> None:
    built = chain(original(LATER))
    with refused(RefusalReason.LINEAGE_CONFLICT):
        built.add(changed(WHEN))


def test_an_observation_of_another_document_is_refused() -> None:
    built = chain(original())
    head = built.entries()[0]
    other = forged(head, document_id="regjeringen-9999999")
    with refused(RefusalReason.LINEAGE_CONFLICT):
        record_observation((other,), Observation(changed()))


def test_a_broken_history_is_refused_before_appending() -> None:
    built = chain(original(), changed())
    with refused(RefusalReason.LINEAGE_CONFLICT):
        record_observation((built.raws[1],), Observation(changed(LATEST)))


# --- changed chapter, attachment, classification and policy -------------------------


def _diff(built: Chain, older: int = 0, newer: int = 1) -> VersionDiff:
    return diff_snapshots(
        load_snapshot(built.raws[older], built.artifacts),
        load_snapshot(built.raws[newer], built.artifacts),
    )


def test_a_changed_chapter_is_reported_by_id_and_digest_without_text() -> None:
    built = chain(original(), changed())
    diff = _diff(built)
    assert diff.causes == ("source_content_changed",)
    assert diff.same_content_version is False
    assert diff.observation.semantic_change is True
    document = built.entries()[1].document_id
    assert [(item.key, item.change) for item in diff.parts] == [(f"{document}-ch2", "changed")]
    assert [(item.key, item.change) for item in diff.entries] == [
        (f"{document}-ch2/kap2-1/1", "changed")
    ]
    assert (diff.chapters, diff.attachments, diff.assessments) == ((), (), ())
    text = diff_text(diff)
    assert "Endret" not in text and "Tekst i kapittel" not in text
    assert diff.text_included is False


def test_a_missing_chapter_page_is_a_part_inventory_change() -> None:
    built = chain(original(), at(evidence(pages(1, 3, 4)), LATER))
    diff = _diff(built)
    assert [item.change for item in diff.parts] == ["removed"]
    assert diff.parts[0].older_sha256 is not None and diff.parts[0].newer_sha256 is None


def test_a_changed_attachment_is_an_attachment_inventory_change() -> None:
    built = chain(linked(PDF, WHEN), linked(OTHER_PDF, LATER))
    diff = _diff(built)
    assert [(item.key, item.change) for item in diff.attachments] == [(PDF_URL, "changed")]
    assert diff.causes == ("source_content_changed",)
    assert {sha(PDF)} <= set(diff.sources.removed)
    assert {sha(OTHER_PDF)} <= set(diff.sources.added)


def test_an_added_attachment_is_reported_as_added() -> None:
    built = chain(original(), linked(PDF, LATER))
    assert ("added", PDF_URL) in {(item.change, item.key) for item in _diff(built).attachments}


def test_a_replay_has_no_difference() -> None:
    built = chain(original(WHEN), original(LATER))
    diff = _diff(built)
    assert diff.causes == ("no_change",)
    assert diff.same_content_version is True
    assert (diff.entries, diff.parts, diff.chapters, diff.attachments) == ((), (), (), ())
    assert (diff.assessments, diff.decisions) == ((), ())
    assert diff.observation.semantic_change is False


def test_retrieval_time_alone_changes_metadata_and_fc5_fc7_digests_not_meaning() -> None:
    built = Chain()
    built.add(original(WHEN), synthetic_policy())
    built.add(original(LATER), synthetic_policy())
    older, newer = built.entries()
    assert older.version_id == newer.version_id
    assert older.assessment_sha256 != newer.assessment_sha256
    assert older.policy_decision_sha256 != newer.policy_decision_sha256
    diff = _diff(built)
    assert diff.causes == ("no_change",)
    observation = diff.observation
    assert observation.semantic_change is False
    assert observation.observed_at_changed and observation.provenance_changed
    assert observation.manifest_sha256_changed and observation.assessment_sha256_changed
    assert observation.policy_decision_sha256_changed
    assert not observation.authorization_sha256_changed
    assert (diff.decisions, diff.blockers.added, diff.blockers.removed) == ((), (), ())


def test_a_changed_policy_is_a_reevaluation_not_a_new_version() -> None:
    first = synthetic_policy()
    second = synthetic_policy(rights_rules=first.rights_rules[1:], policy_version=2)
    built = Chain()
    built.add(original(WHEN), first)
    built.add(original(LATER), second)
    older, newer = built.entries()
    assert newer.content_change == "unchanged"
    assert newer.policy_sha256 != older.policy_sha256
    assert newer.policy_decision_sha256 != older.policy_decision_sha256
    diff = _diff(built)
    assert diff.causes == ("policy_changed",)
    assert diff.decisions
    assert all(item.older != item.newer for item in diff.decisions)
    assert verify_lineage(built.raws, built.artifacts).status == "verified"


def test_a_changed_classifier_is_told_apart_from_a_source_change() -> None:
    built = chain(original(WHEN), original(LATER))
    older = load_snapshot(built.raws[0], built.artifacts)
    newer = load_snapshot(built.raws[1], built.artifacts)
    processing = older.entry.processing.model_copy(
        update={"classifier_version": "span-candidates/0"}
    )
    first = older.assessment.entries[0]
    flipped = (
        RightsClass.STATUTE_TEXT
        if first.rights is not RightsClass.STATUTE_TEXT
        else (RightsClass.OFFICIAL_TEXT)
    )
    entries = (first.model_copy(update={"rights": flipped}), *older.assessment.entries[1:])
    older = older._replace(
        entry=older.entry.model_copy(update={"processing": processing}),
        assessment=older.assessment.model_copy(update={"entries": entries}),
    )
    diff = diff_snapshots(older, newer)
    assert "classifier_version_changed" in diff.causes
    assert "source_content_changed" not in diff.causes
    assert [(c.entry_id, c.field) for c in diff.assessments] == [(first.entry_id, "rights")]


def test_diffs_are_deterministic() -> None:
    built = chain(original(), changed())
    assert diff_text(_diff(built)) == diff_text(_diff(built))


# --- missing parent, rewrites, forks and cycles -------------------------------------


def test_a_missing_middle_entry_is_an_explicit_gap() -> None:
    built = chain(original(WHEN), changed(LATER), changed(LATEST))
    verification = verify_lineage([built.raws[0], built.raws[2]], built.artifacts)
    assert verification.status == "incomplete"
    assert ("parent_missing", "gap") in codes(verification)
    assert len(verification.chain) == 1


def test_a_missing_first_entry_is_an_explicit_gap() -> None:
    built = chain(original(WHEN), changed(LATER))
    verification = verify_lineage([built.raws[1]], built.artifacts)
    assert verification.status == "incomplete"
    assert ("root_missing", "gap") in codes(verification)


def test_no_entries_is_incomplete_never_verified() -> None:
    verification = verify_lineage([], {})
    assert verification.status == "incomplete"
    assert ("root_missing", "gap") in codes(verification)


def test_a_modified_historical_entry_is_a_rewrite() -> None:
    built = chain(original(WHEN), changed(LATER), changed(LATEST))
    middle = built.entries()[1]
    rewritten = forged(middle, observed_at=WHEN + (LATER - WHEN) / 2)
    verification = verify_lineage([built.raws[0], rewritten, built.raws[2]], built.artifacts)
    assert verification.status == "invalid"
    assert ("history_rewritten", "invalid") in codes(verification)


def test_a_malformed_entry_is_invalid() -> None:
    built = chain(original(WHEN), changed(LATER))
    garbled = built.raws[1].replace(b'"sequence": 1', b'"sequence":1')
    verification = verify_lineage([built.raws[0], garbled], built.artifacts)
    assert verification.status == "invalid"
    assert ("entry_malformed", "invalid") in codes(verification)


def test_two_successors_of_one_entry_are_a_fork() -> None:
    left = chain(original(WHEN), changed(LATER))
    right = chain(original(WHEN), linked(PDF, LATER))
    assert left.raws[0] == right.raws[0]
    artifacts = {**left.artifacts, **right.artifacts}
    verification = verify_lineage([*left.raws, right.raws[1]], artifacts)
    assert verification.status == "invalid"
    assert ("fork", "invalid") in codes(verification)


def test_two_first_entries_are_a_fork() -> None:
    left = chain(original())
    right = chain(linked())
    verification = verify_lineage([*left.raws, *right.raws], {**left.artifacts, **right.artifacts})
    assert ("fork", "invalid") in codes(verification)


def test_a_forged_returning_version_is_a_cycle() -> None:
    built = chain(original(WHEN), changed(LATER))
    first, second = built.entries()
    returning = forged(
        first,
        sequence=2,
        previous_entry_sha256=sha(built.raws[1]),
        previous_version_id=second.version_id,
        content_change="new_version",
        observed_at=LATEST,
    )
    verification = verify_lineage([*built.raws, returning], built.artifacts)
    assert ("version_cycle", "invalid") in codes(verification)


def test_a_skipped_sequence_number_is_invalid() -> None:
    built = chain(original(WHEN), changed(LATER))
    skipped = forged(built.entries()[1], sequence=5)
    verification = verify_lineage([built.raws[0], skipped], built.artifacts)
    assert ("sequence_mismatch", "invalid") in codes(verification)


def test_a_wrong_previous_version_is_invalid() -> None:
    built = chain(original(WHEN), changed(LATER), changed(LATEST))
    third = built.entries()[2]
    wrong = forged(
        third, previous_version_id=built.entries()[0].version_id, content_change="new_version"
    )
    verification = verify_lineage([*built.raws[:2], wrong], built.artifacts)
    assert ("version_link_mismatch", "invalid") in codes(verification)


def test_time_running_backwards_in_a_chain_is_invalid() -> None:
    built = chain(original(WHEN), changed(LATER))
    earlier = forged(built.entries()[1], observed_at=WHEN - (LATER - WHEN))
    verification = verify_lineage([built.raws[0], earlier], built.artifacts)
    assert ("observation_out_of_order", "invalid") in codes(verification)


def test_a_chain_mixing_documents_is_invalid() -> None:
    built = chain(original(WHEN), changed(LATER))
    other = forged(built.entries()[1], document_id="regjeringen-9999999")
    verification = verify_lineage([built.raws[0], other], built.artifacts)
    assert ("document_conflict", "invalid") in codes(verification)


def test_an_anchored_head_detects_truncation() -> None:
    built = chain(original(WHEN), changed(LATER))
    head = sha(built.raws[1])
    assert verify_lineage(built.raws, built.artifacts, expected_head=head).status == "verified"
    truncated = verify_lineage(built.raws[:1], built.artifacts, expected_head=head)
    assert ("head_mismatch", "invalid") in codes(truncated)


def test_without_an_anchor_truncation_is_not_detectable() -> None:
    built = chain(original(WHEN), changed(LATER))
    verification = verify_lineage(built.raws[:1], built.artifacts)
    assert verification.status == "verified"
    assert verification.expected_head_sha256 is None


def test_an_anchored_head_detects_a_modified_head() -> None:
    built = chain(original(WHEN), changed(LATER))
    head = sha(built.raws[1])
    altered = forged(built.entries()[1], observed_at=LATEST)
    verification = verify_lineage([built.raws[0], altered], built.artifacts, expected_head=head)
    assert ("head_mismatch", "invalid") in codes(verification)


def test_reordered_and_repeated_inputs_give_the_same_verification() -> None:
    built = chain(original(WHEN), changed(LATER), changed(LATEST))
    forward = verify_lineage(built.raws, built.artifacts)
    backward = verify_lineage(
        [*reversed(built.raws), built.raws[0]], dict(reversed(built.artifacts.items()))
    )
    assert verification_text(forward) == verification_text(backward)


# --- referenced evidence ------------------------------------------------------------


def test_an_altered_source_page_is_invalid() -> None:
    built = chain(original())
    digest = built.entries()[0].source_sha256s[0]
    artifacts = {**built.artifacts, digest: built.artifacts[digest] + b" "}
    verification = verify_lineage(built.raws, artifacts)
    assert verification.status == "invalid"
    assert ("artifact_altered", "invalid") in codes(verification)


def test_a_missing_source_page_is_an_explicit_gap() -> None:
    built = chain(original())
    digest = built.entries()[0].source_sha256s[0]
    verification = verify_lineage(built.raws, without(built.artifacts, digest))
    assert verification.status == "incomplete"
    assert ("artifact_missing", "gap") in codes(verification)


def test_a_missing_manifest_or_assessment_is_an_explicit_gap() -> None:
    built = chain(original())
    entry = built.entries()[0]
    for digest in (entry.manifest_sha256, entry.assessment_sha256):
        verification = verify_lineage(built.raws, without(built.artifacts, digest))
        assert ("artifact_missing", "gap") in codes(verification)


def test_an_altered_manifest_is_invalid() -> None:
    built = chain(original())
    digest = built.entries()[0].manifest_sha256
    artifacts = {**built.artifacts, digest: built.artifacts[digest].replace(b"  ", b" ", 1)}
    assert ("artifact_altered", "invalid") in codes(verify_lineage(built.raws, artifacts))


def test_an_entry_pointing_at_another_versions_manifest_is_invalid() -> None:
    built = chain(original(WHEN), changed(LATER))
    first, second = built.entries()
    swapped = forged(first, manifest_sha256=second.manifest_sha256)
    verification = verify_lineage([swapped], built.artifacts)
    assert verification.status == "invalid"
    assert ("artifact_mismatch", "invalid") in codes(verification)


def test_an_entry_naming_other_sources_than_its_manifest_is_invalid() -> None:
    built = chain(original(WHEN), linked(PDF, LATER))
    first = built.entries()[0]
    padded = forged(first, source_sha256s=tuple(sorted({*first.source_sha256s, sha(PDF)})))
    verification = verify_lineage([padded], built.artifacts)
    assert ("artifact_mismatch", "invalid") in codes(verification)


def test_a_manifest_not_rebuilt_from_its_sources_is_invalid() -> None:
    built = chain(original())
    entry = built.entries()[0]
    stored = json.loads(built.artifacts[entry.manifest_sha256])
    stored["provenance"]["pages"][0]["url"] += "?x=1"
    raw = (json.dumps(stored, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    rebound = forged(entry, manifest_sha256=sha(raw))
    verification = verify_lineage([rebound], {**built.artifacts, sha(raw): raw})
    assert verification.status == "invalid"
    assert ("artifact_not_rebuilt", "invalid") in codes(verification)


def test_an_altered_decision_is_invalid_and_a_missing_policy_is_a_gap() -> None:
    built = Chain()
    built.add(original(), synthetic_policy())
    entry = built.entries()[0]
    assert entry.policy_decision_sha256 is not None and entry.policy_sha256 is not None
    altered = {**built.artifacts, entry.policy_decision_sha256: b"{}\n"}
    assert ("artifact_altered", "invalid") in codes(verify_lineage(built.raws, altered))
    missing = verify_lineage(built.raws, without(built.artifacts, entry.policy_sha256))
    assert ("artifact_missing", "gap") in codes(missing)


def test_a_policy_decision_is_reproduced_from_the_sources_and_policy() -> None:
    policy = synthetic_policy()
    built = Chain()
    built.add(original(), policy)
    entry = built.entries()[0]
    expected = decision_text(evaluate_policy(original(), policy)).encode()
    assert built.artifacts[entry.policy_decision_sha256 or ""] == expected
    assert built.artifacts[entry.policy_sha256 or ""] == policy_text(policy).encode()


# --- FC7b authorization -------------------------------------------------------------


def test_an_authorization_verdict_is_bound_by_digest_and_policy(tmp_path: Path) -> None:
    owner = new_key(tmp_path / "keys", "owner")
    root = trust_root(tmp_path / "root", [signer_line("test-owner", owner, OWNER_NAMESPACES)])
    policy = synthetic_policy()
    request = full_request(policy, owner)
    built = Chain()
    built.observe(Observation(original(), policy, request, root))
    entry = built.entries()[0]
    assert entry.authorization_sha256 is not None
    assert entry.processing.authorization_verifier_version == "policy-auth/1"
    assert verify_lineage(built.raws, built.artifacts).status == "verified"
    altered = {**built.artifacts, entry.authorization_sha256: b"{}\n"}
    assert ("artifact_altered", "invalid") in codes(verify_lineage(built.raws, altered))


def test_an_authorization_for_another_policy_is_refused(tmp_path: Path) -> None:
    owner = new_key(tmp_path / "keys", "owner")
    root = trust_root(tmp_path / "root", [signer_line("test-owner", owner, OWNER_NAMESPACES)])
    request = full_request(synthetic_policy(policy_version=2), owner)
    with refused(RefusalReason.LINEAGE_CONFLICT):
        record_observation((), Observation(original(), synthetic_policy(), request, root))


def test_an_authorization_without_a_policy_or_root_is_refused(tmp_path: Path) -> None:
    owner = new_key(tmp_path / "keys", "owner")
    request = full_request(synthetic_policy(), owner)
    with refused(RefusalReason.LINEAGE_CONFLICT):
        record_observation((), Observation(original(), None, request, None))
    no_root = AuthorizationRequest(policy_text(synthetic_policy()).encode(), request.checked_at)
    with refused(RefusalReason.LINEAGE_CONFLICT):
        record_observation((), Observation(original(), synthetic_policy(), no_root, None))


# --- caller claims are never trusted ------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("release_eligible", True),
        ("published", True),
        ("verified", True),
        ("complete", True),
        ("content_change", "first_version"),
        ("previous_entry_sha256", None),
    ],
)
def test_an_entry_claiming_status_or_eligibility_does_not_parse(field: str, value: object) -> None:
    built = chain(original(WHEN), changed(LATER))
    payload = json.loads(built.raws[1])
    payload[field] = value
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with refused(RefusalReason.SCHEMA_INVALID):
        parse_entry(raw)


def test_a_replay_cannot_claim_a_new_version() -> None:
    built = chain(original(WHEN), original(LATER))
    with pytest.raises(ValidationError):
        LineageEntry.model_validate(
            {**built.entries()[1].model_dump(), "content_change": "new_version"}
        )


def test_a_non_canonical_entry_does_not_parse() -> None:
    built = chain(original())
    with refused(RefusalReason.SCHEMA_INVALID):
        parse_entry(built.raws[0].replace(b"\n", b"\r\n"))


def test_a_verification_cannot_claim_verified_with_findings_or_eligibility() -> None:
    built = chain(original(WHEN), changed(LATER))
    bad = verify_lineage(built.raws[1:], built.artifacts)
    payload = bad.model_dump()
    with pytest.raises(ValidationError):
        LineageVerification.model_validate({**payload, "status": "verified"})
    good = verify_lineage(built.raws, built.artifacts).model_dump()
    for field in ("release_eligible", "published"):
        with pytest.raises(ValidationError):
            LineageVerification.model_validate({**good, field: True})
    with pytest.raises(ValidationError):
        LineageVerification.model_validate({**good, "storage_guarantee": "append_only_proven"})


def test_no_record_has_a_true_eligibility() -> None:
    for model in (LineageEntry, LineageVerification, VersionDiff):
        for name in ("release_eligible", "published"):
            assert model.model_fields[name].annotation.__args__ == (False,)  # type: ignore[union-attr]


def test_a_snapshot_is_refused_when_its_artifacts_do_not_verify() -> None:
    built = chain(original())
    entry = built.entries()[0]
    with refused(RefusalReason.LINEAGE_CONFLICT):
        load_snapshot(built.raws[0], without(built.artifacts, entry.assessment_sha256))
    digest = entry.source_sha256s[0]
    with refused(RefusalReason.LINEAGE_CONFLICT):
        load_snapshot(built.raws[0], {**built.artifacts, digest: b"altered"})


def test_a_diff_between_documents_is_refused() -> None:
    built = chain(original())
    entry = built.entries()[0]
    snapshot = load_snapshot(built.raws[0], built.artifacts)
    other = snapshot._replace(entry=entry.model_copy(update={"document_id": "regjeringen-9999999"}))
    with refused(RefusalReason.LINEAGE_CONFLICT):
        diff_snapshots(snapshot, other)


def test_changed_body_constant_is_not_in_any_record() -> None:
    built = chain(original(), changed())
    for raw in built.raws:
        assert CHANGED_BODY.encode() not in raw
        assert b"Endret" not in raw


@pytest.mark.parametrize("kind", ["manifest", "assessment", "decision", "policy"])
def test_digest_valid_but_malformed_artifacts_are_invalid(kind: str) -> None:
    """A matching hash alone never makes a referenced record valid (FC8)."""
    built = Chain()
    built.add(original(), synthetic_policy())
    field = {"decision": "policy_decision_sha256"}.get(kind, f"{kind}_sha256")
    raw = b"{}\n"
    rebound = forged(built.entries()[0], **{field: sha(raw)})
    artifacts = {**built.artifacts, sha(raw): raw}
    result = verify_lineage([rebound], artifacts)
    assert result.status == "invalid"
    assert any(
        finding.code == "artifact_invalid" and finding.detail == kind for finding in result.findings
    )
    with refused(RefusalReason.LINEAGE_CONFLICT):
        load_snapshot(rebound, artifacts)


@pytest.mark.parametrize("kind", ["assessment", "decision"])
def test_rehashed_canonical_artifacts_must_still_rebuild_from_sources(kind: str) -> None:
    """FC8 promises reproduction, including reasons, not merely digest agreement."""
    built = Chain()
    built.add(original(), synthetic_policy())
    entry = built.entries()[0]
    field = "assessment_sha256" if kind == "assessment" else "policy_decision_sha256"
    digest = getattr(entry, field)
    payload = json.loads(built.artifacts[digest])
    reason_field = "rights_reasons" if kind == "assessment" else "reasons"
    payload["entries"][0][reason_field] = ["invented reason absent from the source"]
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    rebound = forged(entry, **{field: sha(raw)})
    artifacts = {**built.artifacts, sha(raw): raw}
    result = verify_lineage([rebound], artifacts)
    assert result.status == "invalid"
    assert any(
        finding.code == "artifact_not_rebuilt" and finding.detail == kind
        for finding in result.findings
    )
    with refused(RefusalReason.LINEAGE_CONFLICT):
        load_snapshot(rebound, artifacts)


def test_equal_observation_times_allow_a_new_content_version() -> None:
    """Only observations before the head are refused, not equal instants."""
    built = chain(original(WHEN), changed(WHEN))
    assert built.entries()[1].content_change == "new_version"
    assert verify_lineage(built.raws, built.artifacts).status == "verified"
    diff = _diff(built)
    assert diff.observation.observed_at_changed is False
    assert diff.observation.semantic_change is True
    assert diff.causes == ("source_content_changed",)


def test_reverse_diff_swaps_added_removed_sources_and_attachment_digests() -> None:
    built = chain(linked(PDF, WHEN), linked(OTHER_PDF, LATER))
    forward, reverse = _diff(built), _diff(built, 1, 0)
    assert forward.sources.added == reverse.sources.removed == (sha(OTHER_PDF),)
    assert forward.sources.removed == reverse.sources.added == (sha(PDF),)
    assert len(forward.attachments) == len(reverse.attachments) == 1
    before, after = forward.attachments[0], reverse.attachments[0]
    assert before.key == after.key == PDF_URL
    assert before.change == after.change == "changed"
    assert before.older_sha256 == after.newer_sha256
    assert before.newer_sha256 == after.older_sha256
