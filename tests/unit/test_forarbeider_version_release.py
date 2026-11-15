"""FC8b: a deterministic release manifest over exact document versions (#738).

Synthetic only: the invented Prop. 901 L of the version fixtures, and copies
of it renamed to other invented ids. No real text, no fetch, no signing key
outside ``tmp_path``.
"""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization import AuthorizationRequest
from lovspor.forarbeider.policy_trust import TrustRoot
from lovspor.forarbeider.version_lineage import Observation, sha
from lovspor.forarbeider.version_manifest import Evidence
from lovspor.forarbeider.version_release import (
    DocumentOffer,
    build_release,
    parse_release,
    release_text,
    verify_release,
)
from lovspor.forarbeider.version_release_models import (
    RELEASE_BLOCKERS,
    Blocker,
    DocumentSelection,
    ReleaseManifest,
    digest_of,
)
from tests.unit.forarbeider_authorization_support import (
    CHECKED_AT,
    OWNER_NAMESPACES,
    approval,
    automated,
    full_request,
    new_key,
    signed,
    signer_line,
    trust_root,
)
from tests.unit.forarbeider_authorization_support import policy as synthetic_policy
from tests.unit.forarbeider_lineage_support import (
    LATER,
    LATEST,
    Chain,
    chain,
    changed,
    original,
    without,
)
from tests.unit.forarbeider_version_support import pages
from tests.unit.test_forarbeider_document_extract import WHEN

FIRST = "regjeringen-9000001"
SECOND = "regjeringen-9000002"
THIRD = "regjeringen-9000003"


def renamed(source: Evidence, number: int) -> Evidence:
    """``source`` as the invented document ``regjeringen-900000<number>``."""
    new = f"id900000{number}"

    def moved(page: Any) -> Any:
        return page._replace(
            url=page.url.replace("id9000001", new),
            payload=page.payload.replace(b"id9000001", new.encode()),
        )

    landing = None if source.landing is None else moved(source.landing)
    return Evidence(
        tuple(moved(p) for p in source.pages), landing, tuple(moved(p) for p in source.captures)
    )


def offer(built: Chain, document_id: str = FIRST, **changes: Any) -> DocumentOffer:
    head = sha(built.raws[-1]) if built.raws else None
    fields: dict[str, Any] = {
        "document_id": document_id,
        "entries": tuple(built.raws),
        "claimed_head": head,
    }
    return DocumentOffer(**{**fields, **changes})


def blocker_codes(manifest: ReleaseManifest, document_id: str = FIRST) -> set[str]:
    selection = selected(manifest, document_id)
    return {item.code for item in selection.blockers}


def selected(manifest: ReleaseManifest, document_id: str = FIRST) -> DocumentSelection:
    return next(item for item in manifest.content.documents if item.document_id == document_id)


def three() -> tuple[list[DocumentOffer], dict[str, bytes]]:
    offers, artifacts = [], {}
    for number, document_id in ((3, THIRD), (1, FIRST), (2, SECOND)):
        built = chain(renamed(original(), number))
        offers.append(offer(built, document_id))
        artifacts.update(built.artifacts)
    return offers, artifacts


def authorized(tmp_path: Path) -> tuple[Chain, AuthorizationRequest, TrustRoot]:
    owner = new_key(tmp_path / "keys", "owner")
    root = trust_root(tmp_path / "root", [signer_line("test-owner", owner, OWNER_NAMESPACES)])
    policy = synthetic_policy()
    request = full_request(policy, owner)
    built = Chain()
    built.observe(Observation(original(), policy, request, root))
    return built, request, root


# --- one and many documents ----------------------------------------------------------


def test_one_document_selects_its_head_version_and_binds_every_artifact() -> None:
    built = chain(original())
    manifest = build_release([offer(built)], built.artifacts)
    head = built.entries()[-1]
    selection, binding = manifest.content.documents[0], manifest.bindings[0]
    assert selection.version_id == head.version_id
    assert selection.source_sha256s == head.source_sha256s
    assert selection.processing == head.processing
    assert binding.lineage_head_sha256 == sha(built.raws[-1])
    assert binding.manifest_sha256 == head.manifest_sha256
    assert binding.assessment_sha256 == head.assessment_sha256
    assert binding.lineage_status == "verified"
    assert manifest.content.release_blockers == RELEASE_BLOCKERS
    assert manifest.content.head_trust == "unanchored"
    assert (manifest.release_eligible, manifest.published, manifest.text_included) == (
        False,
        False,
        False,
    )


def test_documents_are_ordered_by_id_whatever_order_they_are_offered_in() -> None:
    offers, artifacts = three()
    manifest = build_release(offers, artifacts)
    reversed_manifest = build_release(list(reversed(offers)), artifacts)
    assert [item.document_id for item in manifest.content.documents] == [FIRST, SECOND, THIRD]
    assert release_text(manifest) == release_text(reversed_manifest)
    assert manifest.counts.documents == 3


def test_a_rebuild_from_the_same_inputs_is_byte_identical() -> None:
    offers, artifacts = three()
    first = release_text(build_release(offers, artifacts))
    assert first == release_text(build_release(offers, dict(reversed(artifacts.items()))))
    assert parse_release(first.encode()) == build_release(offers, artifacts)


def test_a_byte_identical_rerun_changes_nothing() -> None:
    once = chain(original())
    twice = chain(original(), original())
    assert twice.raws == once.raws
    assert release_text(build_release([offer(once)], once.artifacts)) == release_text(
        build_release([offer(twice)], twice.artifacts)
    )


def test_the_same_content_read_later_keeps_the_release_id_and_moves_the_bindings() -> None:
    early = chain(original())
    late = chain(original(), original(LATER))
    before = build_release([offer(early)], early.artifacts)
    after = build_release([offer(late)], late.artifacts)
    assert after.release_id == before.release_id
    assert after.bindings_sha256 != before.bindings_sha256
    assert before.observations[0].observed_at == WHEN
    assert after.observations[0].observed_at == LATER
    assert after.observations[0].observations == 2


def test_a_new_version_replaces_the_old_one() -> None:
    built = chain(original(), changed(LATER))
    manifest = build_release([offer(built)], built.artifacts)
    first, second = built.entries()
    assert selected(manifest).version_id == second.version_id != first.version_id
    assert manifest.observations[0].content_versions == 2
    alone = chain(original())
    assert manifest.release_id != build_release([offer(alone)], alone.artifacts).release_id


# --- the head ------------------------------------------------------------------------


def test_a_head_the_caller_states_is_never_trusted() -> None:
    built = chain(original())
    manifest = build_release([offer(built)], built.artifacts)
    assert selected(manifest).status == "blocked"
    assert "head_unanchored" in blocker_codes(manifest)
    assert manifest.bindings[0].claimed_head_sha256 == sha(built.raws[-1])


def test_a_missing_head_blocks() -> None:
    built = chain(original())
    manifest = build_release([offer(built, claimed_head=None)], built.artifacts)
    assert {"head_missing", "head_unanchored"} <= blocker_codes(manifest)
    assert selected(manifest).status == "blocked"


def test_a_conflicting_head_excludes_and_selects_nothing() -> None:
    built = chain(original(), changed(LATER))
    older_head = sha(built.raws[0])
    manifest = build_release([offer(built, claimed_head=older_head)], built.artifacts)
    selection = selected(manifest)
    assert selection.status == "excluded"
    assert selection.version_id is None
    assert "lineage:head_mismatch" in blocker_codes(manifest)
    assert manifest.bindings[0].lineage_head_sha256 is None


def test_a_head_cut_from_the_chain_is_caught_by_the_claimed_head() -> None:
    built = chain(original(), changed(LATER))
    truncated = offer(built, entries=(built.raws[0],))
    manifest = build_release([truncated], built.artifacts)
    assert selected(manifest).status == "excluded"
    assert "lineage:head_mismatch" in blocker_codes(manifest)


# --- lineage and evidence ------------------------------------------------------------


def test_an_incomplete_lineage_excludes() -> None:
    built = chain(original(), changed(LATER), changed(LATEST))
    gapped = offer(built, entries=(built.raws[0], built.raws[2]), claimed_head=None)
    manifest = build_release([gapped], built.artifacts)
    assert selected(manifest).status == "excluded"
    assert {"lineage_incomplete", "lineage:parent_missing"} <= blocker_codes(manifest)
    anchored = build_release([gapped._replace(claimed_head=sha(built.raws[2]))], built.artifacts)
    assert {"lineage_invalid", "lineage:head_mismatch"} <= blocker_codes(anchored)


def test_an_invalid_lineage_excludes() -> None:
    built = chain(original())
    other = chain(changed(LATER))
    forked = offer(built, entries=(built.raws[0], other.raws[0]))
    manifest = build_release([forked], {**built.artifacts, **other.artifacts})
    assert selected(manifest).status == "excluded"
    assert {"lineage_invalid", "lineage:fork"} <= blocker_codes(manifest)


def test_an_empty_lineage_excludes() -> None:
    manifest = build_release([DocumentOffer(FIRST, ())], {})
    assert selected(manifest).status == "excluded"
    assert "lineage:root_missing" in blocker_codes(manifest)
    assert manifest.counts.excluded == 1


def test_an_altered_source_excludes() -> None:
    built = chain(original())
    source = built.entries()[0].source_sha256s[0]
    artifacts = {**built.artifacts, source: built.artifacts[source] + b" "}
    manifest = build_release([offer(built)], artifacts)
    assert selected(manifest).status == "excluded"
    assert "lineage:artifact_altered" in blocker_codes(manifest)


def test_a_missing_source_excludes() -> None:
    built = chain(original())
    source = built.entries()[0].source_sha256s[0]
    manifest = build_release([offer(built)], without(built.artifacts, source))
    assert selected(manifest).status == "excluded"
    assert "lineage:artifact_missing" in blocker_codes(manifest)


def test_an_altered_decision_excludes() -> None:
    built = Chain()
    built.add(original(), synthetic_policy())
    decision = built.entries()[0].policy_decision_sha256
    assert decision is not None
    altered = built.artifacts[decision].replace(b'"policy_status"', b'"policy_status" ', 1)
    manifest = build_release([offer(built)], {**built.artifacts, decision: altered})
    assert selected(manifest).status == "excluded"
    assert "lineage:artifact_altered" in blocker_codes(manifest)


def test_one_bad_document_does_not_hide_the_others() -> None:
    offers, artifacts = three()
    offers[0] = offers[0]._replace(entries=())
    manifest = build_release(offers, artifacts)
    assert [item.status for item in manifest.content.documents] == [
        "blocked",
        "blocked",
        "excluded",
    ]
    assert (manifest.counts.blocked, manifest.counts.excluded) == (2, 1)


# --- identities ----------------------------------------------------------------------


def test_a_document_offered_twice_is_refused() -> None:
    built = chain(original())
    with pytest.raises(ForarbeiderRefusedError) as error:
        build_release([offer(built), offer(built)], built.artifacts)
    assert error.value.reason is RefusalReason.LINEAGE_CONFLICT


def test_a_lineage_offered_under_another_id_is_refused() -> None:
    built = chain(renamed(original(), 2))
    with pytest.raises(ForarbeiderRefusedError) as error:
        build_release([offer(built, FIRST)], built.artifacts)
    assert error.value.reason is RefusalReason.LINEAGE_CONFLICT


def test_one_lineage_offered_under_two_ids_is_refused() -> None:
    built = chain(original())
    with pytest.raises(ForarbeiderRefusedError):
        build_release([offer(built, FIRST), offer(built, SECOND)], built.artifacts)


@pytest.mark.parametrize(
    "changes", [{"document_id": " "}, {"claimed_head": "HEAD"}, {"claimed_head": "0" * 63}]
)
def test_a_malformed_offer_is_refused(changes: dict[str, str]) -> None:
    built = chain(original())
    with pytest.raises(ForarbeiderRefusedError) as error:
        build_release([offer(built, **changes)], built.artifacts)
    assert error.value.reason is RefusalReason.SCHEMA_INVALID


# --- gates ---------------------------------------------------------------------------


def test_an_unevaluated_version_is_blocked_by_policy() -> None:
    built = chain(original())
    manifest = build_release([offer(built)], built.artifacts)
    assert "policy_not_evaluated" in blocker_codes(manifest)
    assert selected(manifest).policy is None


def test_an_evaluated_version_names_its_policy_and_fc7_blockers() -> None:
    built = Chain()
    policy = synthetic_policy()
    built.add(original(), policy)
    manifest = build_release([offer(built)], built.artifacts)
    reference = selected(manifest).policy
    assert reference is not None
    assert (reference.policy_id, reference.policy_version) == ("synthetic-policy", 1)
    assert reference.policy_sha256 == built.entries()[0].policy_sha256
    assert manifest.bindings[0].policy_decision_sha256 == built.entries()[0].policy_decision_sha256
    codes = blocker_codes(manifest)
    assert "fc7b_authorization_missing" in codes
    assert any(code.startswith("fc7:") for code in codes) or "fc7_unresolved" in codes


def test_fc7_deny_and_unresolved_block() -> None:
    built = Chain()
    built.add(original(), synthetic_policy(rights_rules=()))
    manifest = build_release([offer(built)], built.artifacts)
    codes = blocker_codes(manifest)
    assert {"fc7_rules_not_satisfied", "fc7_unresolved"} <= codes
    categories = {item.category for item in selected(manifest).blockers}
    assert "policy" in categories


def test_an_incomplete_source_blocks() -> None:
    built = chain(Evidence(tuple(pages(1, 2, 3)), original().landing, ()))
    manifest = build_release([offer(built)], built.artifacts)
    state = selected(manifest).source_state
    assert state is not None
    assert state.value != "source_complete"
    assert blocker_codes(manifest) & {"source_incomplete", "source_unknown"}


def test_publication_never_complete_blocks() -> None:
    built = chain(original())
    manifest = build_release([offer(built)], built.artifacts)
    assert "publication_incomplete" in blocker_codes(manifest)


def test_d10_and_d11_always_block() -> None:
    built = chain(original())
    manifest = build_release([offer(built)], built.artifacts)
    assert {"d10_legal_assessment_unresolved", "d11_automated_publication_not_accepted"} <= (
        blocker_codes(manifest)
    )


def test_an_authorization_is_reverified_through_its_verifier(tmp_path: Path) -> None:
    built, request, root = authorized(tmp_path)
    manifest = build_release([offer(built, authorization=request)], built.artifacts, root)
    codes = blocker_codes(manifest)
    assert selected(manifest).status == "blocked"
    assert manifest.bindings[0].authorization_sha256 == built.entries()[0].authorization_sha256
    assert "fc7b:d10_legal_assessment_not_verifiable" in codes
    assert "fc7b:d11_automated_publication_not_accepted" in codes
    assert not codes & {"fc7b_authorization_missing", "fc7b_authorization_not_reverified"}


def test_an_authorization_without_its_signed_records_is_not_reverified(tmp_path: Path) -> None:
    built, _, root = authorized(tmp_path)
    manifest = build_release([offer(built)], built.artifacts, root)
    assert "fc7b_authorization_not_reverified" in blocker_codes(manifest)
    assert selected(manifest).status == "blocked"


def test_an_authorization_without_a_trust_root_is_not_reverified(tmp_path: Path) -> None:
    built, request, _ = authorized(tmp_path)
    manifest = build_release([offer(built, authorization=request)], built.artifacts)
    assert "fc7b_authorization_not_reverified" in blocker_codes(manifest)


def test_signed_records_that_do_not_reproduce_the_verdict_exclude(tmp_path: Path) -> None:
    built, request, root = authorized(tmp_path)
    other = AuthorizationRequest(
        request.policy, CHECKED_AT + timedelta(hours=1), request.policy_approval
    )
    manifest = build_release([offer(built, authorization=other)], built.artifacts, root)
    assert selected(manifest).status == "excluded"
    assert "fc7b_authorization_not_reproduced" in blocker_codes(manifest)


def test_a_pending_authorization_blocks(tmp_path: Path) -> None:
    owner = new_key(tmp_path / "keys", "owner")
    root = trust_root(tmp_path / "root", [signer_line("test-owner", owner, OWNER_NAMESPACES)])
    policy = synthetic_policy()
    request = full_request(policy, owner, automated_publication=None)
    built = Chain()
    built.observe(Observation(original(), policy, request, root))
    manifest = build_release([offer(built, authorization=request)], built.artifacts, root)
    codes = blocker_codes(manifest)
    assert "fc7b_missing_evidence" in codes
    assert "fc7b:automated_publication_approval_missing" in codes


def test_a_missing_legal_reference_blocks(tmp_path: Path) -> None:
    owner = new_key(tmp_path / "keys", "owner")
    root = trust_root(tmp_path / "root", [signer_line("test-owner", owner, OWNER_NAMESPACES)])
    policy = synthetic_policy()
    approved = signed(approval(policy), owner)
    bare = signed(automated(policy, approved.record, legal_assessment_reference=None), owner)
    request = full_request(policy, owner, policy_approval=approved, automated_publication=bare)
    built = Chain()
    built.observe(Observation(original(), policy, request, root))
    manifest = build_release([offer(built, authorization=request)], built.artifacts, root)
    assert "fc7b:legal_assessment_reference_missing" in blocker_codes(manifest)


def test_a_blocked_newer_version_is_never_bypassed_by_an_older_one(tmp_path: Path) -> None:
    built, request, root = authorized(tmp_path)
    built.add(changed(LATER))
    older, newer = built.entries()
    manifest = build_release([offer(built, authorization=request)], built.artifacts, root)
    selection = selected(manifest)
    assert selection.version_id == newer.version_id != older.version_id
    assert "policy_not_evaluated" in blocker_codes(manifest)
    assert manifest.bindings[0].authorization_sha256 is None


# --- nothing eligible ----------------------------------------------------------------


def test_a_fully_blocked_corpus_still_gives_a_valid_manifest() -> None:
    offers, artifacts = three()
    manifest = build_release(offers, artifacts)
    assert manifest.counts.included == 0
    assert all(item.blockers for item in manifest.content.documents)
    assert parse_release(release_text(manifest).encode()) == manifest


def test_no_documents_give_an_empty_valid_manifest() -> None:
    manifest = build_release([], {})
    assert manifest.counts.documents == 0
    assert manifest.content.release_blockers == RELEASE_BLOCKERS


def test_an_included_document_cannot_be_recorded_while_the_head_is_unanchored() -> None:
    built = chain(original())
    manifest = build_release([offer(built)], built.artifacts)
    cleared = selected(manifest).model_copy(update={"status": "included", "blockers": ()})
    content = manifest.content.model_copy(update={"documents": (cleared,)})
    data = manifest.model_dump(mode="json")
    data["content"] = content.model_dump(mode="json")
    data["release_id"] = digest_of(content)
    data["counts"] = {"documents": 1, "included": 1, "excluded": 0, "blocked": 0}
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(data)


def test_a_selection_cannot_claim_a_status_its_blockers_do_not_give() -> None:
    built = chain(original())
    data = selected(build_release([offer(built)], built.artifacts)).model_dump(mode="json")
    with pytest.raises(ValidationError):
        DocumentSelection.model_validate({**data, "blockers": [], "status": "blocked"})
    with pytest.raises(ValidationError):
        DocumentSelection.model_validate({**data, "status": "included"})
    bad = [Blocker(code="lineage_invalid", category="integrity").model_dump()]
    with pytest.raises(ValidationError):
        DocumentSelection.model_validate({**data, "blockers": bad, "status": "blocked"})


@pytest.mark.parametrize("field", ["release_eligible", "published", "text_included"])
def test_a_manifest_claiming_release_does_not_parse(field: str) -> None:
    built = chain(original())
    data = json.loads(release_text(build_release([offer(built)], built.artifacts)))
    data[field] = True
    with pytest.raises(ForarbeiderRefusedError):
        parse_release(json.dumps(data).encode())


def test_the_manifest_holds_no_source_text() -> None:
    built = chain(original())
    text = release_text(build_release([offer(built)], built.artifacts))
    assert "Tekst i kapittel" not in text
    assert "Innledning" not in text


# --- verification --------------------------------------------------------------------


def test_an_honest_manifest_is_reproduced() -> None:
    offers, artifacts = three()
    raw = release_text(build_release(offers, artifacts)).encode()
    verification = verify_release(raw, offers, artifacts)
    assert verification.status == "reproduced"
    assert verification.offered_release_id == verification.rebuilt_release_id
    assert verification.head_trust == "unanchored"
    assert verification.release_eligible is False


def _codes(verification: Any) -> set[tuple[str, str | None]]:
    return {(finding.code, finding.document_id) for finding in verification.findings}


def test_an_artifact_altered_after_the_build_is_detected() -> None:
    offers, artifacts = three()
    raw = release_text(build_release(offers, artifacts)).encode()
    first = next(o for o in offers if o.document_id == FIRST)
    head = json.loads(first.entries[-1])
    source = head["source_sha256s"][0]
    altered = {**artifacts, source: artifacts[source] + b" "}
    verification = verify_release(raw, offers, altered)
    assert verification.status == "invalid"
    assert ("selection_changed", FIRST) in _codes(verification)


def test_a_missing_or_extra_document_is_detected() -> None:
    offers, artifacts = three()
    raw = release_text(build_release(offers[1:], artifacts)).encode()
    assert ("document_missing", THIRD) in _codes(verify_release(raw, offers, artifacts))
    full = release_text(build_release(offers, artifacts)).encode()
    assert ("document_added", THIRD) in _codes(verify_release(full, offers[1:], artifacts))


def test_reordered_documents_do_not_parse() -> None:
    offers, artifacts = three()
    data = json.loads(release_text(build_release(offers, artifacts)))
    data["content"]["documents"].reverse()
    data["bindings"].reverse()
    data["observations"].reverse()
    raw = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
    assert _codes(verify_release(raw, offers, artifacts)) == {("manifest_malformed", None)}


def test_a_forged_manifest_with_recomputed_digests_is_detected() -> None:
    offers, artifacts = three()
    manifest = build_release(offers, artifacts)
    first = selected(manifest)
    lighter = first.model_copy(
        update={"blockers": tuple(b for b in first.blockers if b.category != "governance")}
    )
    documents = (lighter, *manifest.content.documents[1:])
    content = manifest.content.model_copy(update={"documents": documents})
    forged = manifest.model_copy(update={"content": content, "release_id": digest_of(content)})
    raw = release_text(ReleaseManifest.model_validate(forged.model_dump())).encode()
    verification = verify_release(raw, offers, artifacts)
    assert ("selection_changed", FIRST) in _codes(verification)
    assert ("release_id_mismatch", None) in _codes(verification)


def test_forged_bindings_are_detected() -> None:
    offers, artifacts = three()
    manifest = build_release(offers, artifacts)
    binding = manifest.bindings[0].model_copy(update={"manifest_sha256": "0" * 64})
    bindings = (binding, *manifest.bindings[1:])
    update = {"bindings": bindings, "bindings_sha256": digest_of(*bindings)}
    forged = manifest.model_copy(update=update)
    raw = release_text(ReleaseManifest.model_validate(forged.model_dump())).encode()
    assert ("binding_changed", FIRST) in _codes(verify_release(raw, offers, artifacts))


def test_non_canonical_bytes_do_not_parse() -> None:
    offers, artifacts = three()
    raw = release_text(build_release(offers, artifacts)).encode()
    assert _codes(verify_release(raw + b"\n", offers, artifacts)) == {("manifest_malformed", None)}


def test_inputs_the_builder_refuses_are_a_finding() -> None:
    offers, artifacts = three()
    raw = release_text(build_release(offers, artifacts)).encode()
    verification = verify_release(raw, [*offers, offers[0]], artifacts)
    assert _codes(verification) == {("inputs_refused", None)}


@pytest.mark.parametrize("altered", [False, True], ids=["missing", "altered"])
def test_release_checks_evidence_of_older_versions(altered: bool) -> None:
    """FC8b verifies the whole lineage, not only the selected head's evidence."""
    built = chain(original(), changed(LATER))
    older, newer = built.entries()
    old_source = next(key for key in older.source_sha256s if key not in newer.source_sha256s)
    artifacts = dict(built.artifacts)
    if altered:
        artifacts[old_source] += b" "
    else:
        del artifacts[old_source]

    manifest = build_release([offer(built)], artifacts)

    assert selected(manifest).status == "excluded"
    assert selected(manifest).version_id is None
    assert manifest.bindings[0].lineage_head_sha256 is None
    expected = "lineage:artifact_altered" if altered else "lineage:artifact_missing"
    assert expected in blocker_codes(manifest)


@pytest.mark.parametrize(
    "field", ["release_id", "bindings_sha256", "counts", "bindings", "observations"]
)
def test_parse_release_rejects_inconsistent_manifest_sections(field: str) -> None:
    """Canonical encoding alone cannot make an inconsistent record valid."""
    built = chain(original())
    manifest = build_release([offer(built)], built.artifacts)
    data = manifest.model_dump(mode="json")
    if field in {"release_id", "bindings_sha256"}:
        data[field] = "0" * 64
    elif field == "counts":
        data[field]["documents"] = 2
    else:
        data[field][0]["document_id"] = SECOND
    # Preserve canonical encoding so this exercises model consistency, not whitespace.
    raw = (json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()

    with pytest.raises(ForarbeiderRefusedError) as error:
        parse_release(raw)
    assert error.value.reason is RefusalReason.SCHEMA_INVALID


@pytest.mark.parametrize("raw", [b"", b"\xff", b"{", b"[]", b"null"])
def test_malformed_release_is_reported_without_losing_the_rebuild(raw: bytes) -> None:
    rebuilt = build_release([], {})

    verification = verify_release(raw, [], {})

    assert verification.status == "invalid"
    assert verification.offered_release_id is None
    assert verification.rebuilt_release_id == rebuilt.release_id
    assert _codes(verification) == {("manifest_malformed", None)}


@pytest.mark.parametrize(
    "field,value", [("observed_at", LATER), ("observations", 99), ("content_versions", 99)]
)
def test_observation_changes_are_detected_outside_both_digests(field: str, value: Any) -> None:
    built = chain(original())
    manifest = build_release([offer(built)], built.artifacts)
    observation = manifest.observations[0].model_copy(update={field: value})
    forged = manifest.model_copy(update={"observations": (observation,)})
    raw = release_text(forged).encode()

    parsed = parse_release(raw)
    assert parsed.release_id == manifest.release_id
    assert parsed.bindings_sha256 == manifest.bindings_sha256
    verification = verify_release(raw, [offer(built)], built.artifacts)
    assert verification.status == "invalid"
    assert _codes(verification) == {("observation_changed", FIRST)}
