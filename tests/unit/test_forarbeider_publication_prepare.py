"""AR4a: offline publication preparation in an isolated release clone (#738; §9.3 step 3, §9.4).

Synthetic only: the invented FC6 fixture proposition, renumbered; temporary
Git repositories in pytest's directory; plans sealed the way the planner seals
them. Nothing here fetches, signs, commits in a release clone, pushes or
publishes.
"""

from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider import publication_stage
from lovspor.forarbeider.corpus_render import NOT_LAW
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_evaluation import decision_text, parse_decision
from lovspor.forarbeider.publication_models import (
    UNAVAILABLE_CHECKS,
    ProposalCounts,
    PublicationTransaction,
    within_caps,
)
from lovspor.forarbeider.publication_prepare import (
    PreparationOutcome,
    PreparationRequest,
    prepare_publication,
)
from lovspor.forarbeider.release_attribution import attribute_plan
from lovspor.forarbeider.release_lock import release_lock
from lovspor.forarbeider.release_plan import Row
from lovspor.forarbeider.release_plan_models import BatchCaps, ReleasePlan, plan_digest
from lovspor.forarbeider.release_quarantine import EMPTY_LEDGER, merge, quarantine_set
from lovspor.forarbeider.release_quarantine_models import QuarantineSet
from lovspor.forarbeider.version_release_models import digest_of
from tests.unit.forarbeider_publication_support import (
    PathName,
    commit_files,
    git,
    plan_of,
    quarantine_of,
    sealed_plan,
    sealed_quarantine,
    snapshot,
    source_repository,
    synthetic_row,
)
from tests.unit.forarbeider_release_control_support import CHECKED_AT
from tests.unit.forarbeider_release_plan_support import Store, document

UNSAFE_BODY = "<p>Tekst \u202e i kapittel 2.</p>"


class Bench:
    """Synthetic documents, a sealed plan honouring a quarantine, and a source repository."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.store = Store()
        self.rows: dict[int, Row] = {}
        self.source, self.base = source_repository(tmp_path)
        self.runs = 0

    def add(self, number: int, body: str | None = None, **changes: Any) -> Row:
        self.rows[number] = synthetic_row(self.store, number, body, **changes)
        return self.rows[number]

    def plan(self, found: QuarantineSet | None = None, **caps: Any) -> ReleasePlan:
        numbers = sorted(self.rows)
        return plan_of(
            self.store, list(self.rows.values()), numbers, found or quarantine_of(), **caps
        )

    def request(
        self,
        plan: ReleasePlan | None = None,
        path: PathName = "A",
        found: QuarantineSet | None = None,
        **changes: Any,
    ) -> PreparationRequest:
        chosen = found or quarantine_of()
        made = plan or self.plan(chosen)
        fields: dict[str, Any] = {
            "plan": sealed_plan(made),
            "quarantine": sealed_quarantine(chosen),
            "batch_id": made.batches[0].batch_id,
            "rows": tuple(self.rows.values()),
            "artifacts": dict(self.store.artifacts),
            "path": path,
            "base_commit": self.base,
        }
        return PreparationRequest(**(fields | changes))

    def prepare(self, request: PreparationRequest | None = None, fresh: bool = True) -> Any:
        if fresh:
            self.runs += 1
        work = self.tmp / f"work-{self.runs}"
        if not work.exists():
            _made(work)
        return prepare_publication(request or self.request(), self.source, work)


def _made(path: Path) -> Path:
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    found = Bench(tmp_path)
    found.add(1)
    found.add(2)
    return found


def files_of(outcome: PreparationOutcome) -> dict[str, bytes]:
    assert outcome.clone is not None and outcome.transaction is not None
    return {
        item.path: (outcome.clone / item.path).read_bytes()
        for item in outcome.transaction.content.files
    }


def status_of(clone: Path) -> list[str]:
    raw = git(clone, "status", "--porcelain=v1", "--untracked-files=all", "--ignored")
    return sorted(raw.splitlines())


def halt_codes(outcome: PreparationOutcome) -> set[str]:
    return {item.code for item in outcome.halts}


# --- exact proposed bytes ----------------------------------------------------------------------


def test_a_batch_is_prepared_as_exact_bytes_in_an_isolated_clone(bench: Bench) -> None:
    outcome = bench.prepare()

    assert outcome.status == "prepared"
    assert (outcome.local, outcome.halts) == ((), ())
    transaction = outcome.transaction
    assert transaction is not None and outcome.clone is not None
    manifest = transaction.content.release_manifest_path
    assert [item.path for item in transaction.content.files] == sorted(
        [
            "INDEX.md",
            "catalogue.json",
            "documents/regjeringen-9000001.md",
            "documents/regjeringen-9000002.md",
            manifest,
        ]
    )
    written = files_of(outcome)
    for item in transaction.content.files:
        assert sha256(written[item.path]).hexdigest() == item.sha256
        assert len(written[item.path]) == item.size_bytes
    assert manifest == f"releases/{sha256(written[manifest]).hexdigest()}.json"
    assert status_of(outcome.clone) == sorted(f"?? {path}" for path in written)
    assert git(outcome.clone, "rev-parse", "HEAD") == bench.base


def test_counts_are_recounted_from_the_rendered_bytes_not_the_plan(bench: Bench) -> None:
    outcome = bench.prepare()

    transaction = outcome.transaction
    assert transaction is not None
    written = files_of(outcome)
    counts = transaction.content.counts
    assert counts == ProposalCounts(
        documents=2, files=len(written), bytes=sum(len(data) for data in written.values())
    )
    plan = bench.plan()
    assert (counts.files, counts.bytes) != (plan.batches[0].files, plan.batches[0].bytes)


def test_a_document_renders_to_these_exact_bytes(bench: Bench) -> None:
    row = bench.rows[1]
    outcome = bench.prepare()

    text = files_of(outcome)["documents/regjeringen-9000001.md"].decode()
    policy = row.selection.policy
    assert policy is not None
    url = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/"
    assert text == (
        "---\n"
        'basis: "åndsverkloven § 14"\n'
        'document_id: "regjeringen-9000001"\n'
        'document_type: "prop-l"\n'
        "normative: false\n"
        f'policy_id: "{policy.policy_id}"\n'
        f'policy_sha256: "{policy.policy_sha256}"\n'
        f"policy_version: {policy.policy_version}\n"
        "proposition_number: 901\n"
        'renderer: "publication-render/1"\n'
        'session: "2099-2100"\n'
        "source_license: null\n"
        f'source_url: "{url}"\n'
        'title: "Strukturell testproposisjon om et oppdiktet lovforslag"\n'
        f'version_id: "{row.selection.version_id}"\n'
        "---\n"
        "\n"
        f"{NOT_LAW}\n"
        ">\n"
        "> Published on the basis of åndsverkloven § 14; no licence is asserted."
        " Annexes, figures, images, tables and footnotes are never included.\n"
        "\n"
        "# Strukturell testproposisjon om et oppdiktet lovforslag\n"
        "\n"
        f"Source: <{url}?ch=1>\n\n## 1 Innledning\n\nTekst i kapittel 1.\n"
        "\n"
        f"Source: <{url}?ch=2>\n\n## 2 Bakgrunn\n\nTekst i kapittel 2.\n"
        "\n"
        f"Source: <{url}?ch=3>\n\n## Tilråding\n\nTekst i kapittel 3.\n"
    )


def test_the_same_inputs_give_the_same_transaction_and_bytes(bench: Bench) -> None:
    first, second = bench.prepare(), bench.prepare()

    assert first.transaction == second.transaction
    assert first.clone != second.clone
    assert files_of(first) == files_of(second)


def test_paths_a_and_b_share_the_documents_but_never_a_transaction(bench: Bench) -> None:
    a = bench.prepare(bench.request(path="A"))
    b = bench.prepare(bench.request(path="B"))

    assert a.transaction is not None and b.transaction is not None
    assert a.transaction.transaction_id != b.transaction.transaction_id
    assert a.transaction.content.publication.path == "A"
    assert b.transaction.content.publication.path == "B"
    shared = ["INDEX.md", "catalogue.json", "documents/regjeringen-9000001.md"]
    assert {p: files_of(a)[p] for p in shared} == {p: files_of(b)[p] for p in shared}
    assert (
        a.transaction.content.release_manifest_path != b.transaction.content.release_manifest_path
    )


def test_the_transaction_is_bound_to_the_exact_plan_batch_and_base(bench: Bench) -> None:
    plan = bench.plan()
    outcome = bench.prepare(bench.request(plan))

    transaction = outcome.transaction
    assert transaction is not None
    content = transaction.content
    assert (content.plan_id, content.batch_id) == (plan.plan_id, plan.batches[0].batch_id)
    assert content.publication.base_commit == bench.base
    assert content.quarantine_sha256 == plan.basis.quarantine_sha256
    by_id = {item.document_id: item for item in plan.documents}
    for published in content.documents:
        planned = by_id[published.document_id]
        assert published.selection_sha256 == planned.selection_sha256
        assert published.binding_sha256 == planned.binding_sha256
        assert published.version_id == planned.version_id
    assert transaction.transaction_id == digest_of(content)


def test_nothing_unavailable_is_reported_as_passed(bench: Bench) -> None:
    transaction = bench.prepare().transaction

    assert transaction is not None
    assert transaction.content.unavailable_checks == UNAVAILABLE_CHECKS
    assert transaction.content.authorized is False
    assert transaction.content.published is False
    assert (
        transaction.content.pending_release_blockers == bench.plan().basis.pending_release_blockers
    )


def test_the_source_repository_is_never_touched(bench: Bench) -> None:
    before = snapshot(bench.source)
    outcome = bench.prepare()

    assert snapshot(bench.source) == before
    assert outcome.clone is not None
    assert git(outcome.clone, "remote") == ""
    assert not outcome.clone.is_relative_to(bench.source)


# --- stale plans and changed bindings -------------------------------------------------------


def test_an_unsealed_plan_halts(bench: Bench) -> None:
    request = bench.request()
    forged = request.plan.model_copy(update={"provenance": None})
    outcome = bench.prepare(request._replace(plan=forged))

    assert outcome.status == "halted"
    assert halt_codes(outcome) == {"prepare:plan_unverified"}
    assert outcome.transaction is None


def test_a_plan_without_a_quarantine_assessment_halts(bench: Bench) -> None:
    numbers = sorted(bench.rows)
    plan = plan_of(bench.store, list(bench.rows.values()), numbers, None)
    outcome = bench.prepare(bench.request(plan))

    assert halt_codes(outcome) == {"prepare:plan_quarantine_unassessed"}


def test_a_quarantine_other_than_the_plans_halts(bench: Bench) -> None:
    request = bench.request()
    other = sealed_quarantine(quarantine_of(7))
    outcome = bench.prepare(request._replace(quarantine=other))

    assert halt_codes(outcome) == {"prepare:quarantine_mismatch"}


def test_a_batch_the_plan_does_not_have_halts(bench: Bench) -> None:
    outcome = bench.prepare(bench.request(batch_id="batch-" + "0" * 56))

    assert halt_codes(outcome) == {"prepare:batch_not_in_plan"}


def test_a_changed_binding_halts_before_anything_is_written(bench: Bench) -> None:
    request = bench.request()
    row = bench.rows[2]
    moved = row.binding.model_copy(update={"assessment_sha256": "9" * 64})
    rows = (bench.rows[1], Row(row.selection, moved))
    outcome = bench.prepare(request._replace(rows=rows))

    assert halt_codes(outcome) == {"prepare:binding_mismatch"}
    assert outcome.clone is None or status_of(outcome.clone) == []


def test_a_changed_selection_halts(bench: Bench) -> None:
    request = bench.request()
    row = bench.rows[1]
    moved = row.selection.model_copy(update={"source_sha256s": ("8" * 64,)})
    outcome = bench.prepare(request._replace(rows=(Row(moved, row.binding), bench.rows[2])))

    assert halt_codes(outcome) == {"prepare:selection_mismatch"}


def test_a_missing_row_halts(bench: Bench) -> None:
    outcome = bench.prepare(bench.request()._replace(rows=(bench.rows[1],)))

    assert halt_codes(outcome) == {"prepare:row_missing"}


# --- expected base commit -------------------------------------------------------------------------


def test_a_base_other_than_mains_head_halts_and_nothing_is_cloned(bench: Bench) -> None:
    commit_files(bench.source, {"README.md": b"moved on\n"}, "unrelated")
    outcome = bench.prepare()

    assert halt_codes(outcome) == {"prepare:base_commit_mismatch"}
    assert outcome.clone is None


def test_an_unknown_base_commit_halts(bench: Bench) -> None:
    outcome = bench.prepare(bench.request(base_commit="1" * 40))

    assert halt_codes(outcome) == {"prepare:base_commit_mismatch"}


# --- quarantine -------------------------------------------------------------------------------


def test_a_quarantined_document_is_never_prepared(bench: Bench) -> None:
    found = quarantine_of(2)
    outcome = bench.prepare(bench.request(found=found))

    transaction = outcome.transaction
    assert transaction is not None
    assert [item.document_id for item in transaction.content.documents] == [document(1)]
    assert "documents/regjeringen-9000002.md" not in files_of(outcome)


def test_a_sealed_plan_that_batches_a_quarantined_document_halts(bench: Bench) -> None:
    """A planner fault: the plan names the quarantine yet batches a document it restricts."""
    found = quarantine_of(2)
    honest = bench.plan(quarantine_of())
    basis = honest.basis.model_copy(update={"quarantine_sha256": digest_of(found)})
    raw = honest.model_dump(mode="json")
    raw["basis"] = basis.model_dump(mode="json")
    raw["plan_id"] = plan_digest(basis, honest.documents, honest.batches)
    faulty = ReleasePlan.model_validate(raw)
    outcome = bench.prepare(bench.request(faulty, found=found))

    assert halt_codes(outcome) == {"prepare:quarantined_document_in_batch"}
    assert outcome.transaction is None


def test_a_document_local_failure_rolls_back_and_feeds_quarantine(bench: Bench) -> None:
    bench.add(2, UNSAFE_BODY)
    outcome = bench.prepare()

    assert outcome.status == "refused_local"
    assert outcome.halts == ()
    assert outcome.transaction is None
    [failure] = outcome.local
    assert (failure.document_id, failure.code) == (document(2), "render_failed")
    assert failure.version_id == bench.rows[2].selection.version_id
    assert outcome.clone is not None and status_of(outcome.clone) == []
    assert not (outcome.clone / publication_stage.JOURNAL).exists()


def test_unaffected_documents_are_prepared_after_the_quarantine_re_plans(bench: Bench) -> None:
    bench.add(2, UNSAFE_BODY)
    bench.add(3)
    base = bench.plan()
    refused = bench.prepare(bench.request(base))
    rows = list(bench.rows.values())

    attributed = attribute_plan(base, rows, bench.store.artifacts, refused.local)
    assert attributed.halts == ()
    ledger = merge(EMPTY_LEDGER, attributed.local, CHECKED_AT, base.plan_id)
    found = quarantine_set(ledger)
    replanned = bench.plan(found)
    outcome = bench.prepare(bench.request(replanned, found=found))

    assert outcome.status == "prepared"
    transaction = outcome.transaction
    assert transaction is not None
    assert [item.document_id for item in transaction.content.documents] == [
        document(1),
        document(3),
    ]
    assert transaction.content.quarantine_sha256 == digest_of(found)


def test_an_altered_artifact_is_attributed_to_its_document(bench: Bench) -> None:
    request = bench.request()
    key = bench.rows[2].binding.manifest_sha256
    assert key is not None
    artifacts = dict(request.artifacts) | {key: b"{}\n"}
    outcome = bench.prepare(request._replace(artifacts=artifacts))

    assert outcome.status == "refused_local"
    assert [(f.document_id, f.code) for f in outcome.local] == [
        (document(2), "artifact_integrity_mismatch")
    ]


def test_a_global_failure_is_never_downgraded_by_a_local_one(bench: Bench) -> None:
    bench.add(2, UNSAFE_BODY)
    row = bench.rows[1]
    key = row.binding.policy_decision_sha256
    assert key is not None
    decision = parse_decision(bench.store.artifacts[key])
    denied = decision.entries[1].model_copy(update={"decision": "deny"})
    entries = (decision.entries[0], denied, *decision.entries[2:])
    changed = decision_text(decision.model_copy(update={"entries": entries})).encode()
    new_key = bench.store.put(changed)
    bench.rows[1] = Row(
        row.selection, row.binding.model_copy(update={"policy_decision_sha256": new_key})
    )
    outcome = bench.prepare()

    assert outcome.status == "halted"
    assert halt_codes(outcome) == {"prepare:fc7_decision_contradicts_plan"}
    assert [f.document_id for f in outcome.local] == [document(2)]
    assert outcome.transaction is None
    assert outcome.halt is not None
    assert outcome.halt.batch_id == bench.plan().batches[0].batch_id


def test_an_unreadable_decision_is_global_like_ar3(bench: Bench) -> None:
    request = bench.request()
    key = bench.rows[1].binding.policy_decision_sha256
    assert key is not None
    artifacts = {k: v for k, v in request.artifacts.items() if k != key}
    outcome = bench.prepare(request._replace(artifacts=artifacts))

    assert outcome.status == "halted"
    assert "prepare:fc7_decision_unavailable" in halt_codes(outcome)


# --- what is already on the base ------------------------------------------------------------


def _publish(bench: Bench, outcome: PreparationOutcome) -> str:
    """The test's stand-in for a publication: the proposed bytes committed to the source."""
    return commit_files(bench.source, files_of(outcome), "synthetic publication")


def test_a_version_already_published_halts(bench: Bench) -> None:
    first = bench.prepare()
    bench.base = _publish(bench, first)
    outcome = bench.prepare()

    assert halt_codes(outcome) == {"prepare:version_already_published"}


def test_a_new_version_replaces_the_published_file(bench: Bench) -> None:
    first = bench.prepare()
    bench.base = _publish(bench, first)
    bench.rows.pop(2)
    old = files_of(first)["documents/regjeringen-9000001.md"]
    bench.add(1, "<p>Endret oppdiktet tekst i kapittel 2.</p>")
    outcome = bench.prepare()

    assert outcome.status == "prepared"
    transaction = outcome.transaction
    assert transaction is not None and outcome.clone is not None
    [published] = transaction.content.documents
    assert published.previous_sha256 == sha256(old).hexdigest()
    lines = status_of(outcome.clone)
    assert " M documents/regjeringen-9000001.md" in lines
    assert " M catalogue.json" in lines and " M INDEX.md" in lines
    catalogue = files_of(outcome)["catalogue.json"].decode()
    assert "regjeringen-9000002" in catalogue


def test_a_base_tree_whose_documents_are_not_catalogued_halts(tmp_path: Path) -> None:
    bench = Bench(tmp_path)
    stray = {"README.md": b"x\n", "documents/regjeringen-9000009.md": b"stray\n"}
    bench.source, bench.base = source_repository(_made(tmp_path / "other"), stray)
    bench.add(1)
    outcome = bench.prepare()

    assert halt_codes(outcome) == {"prepare:base_tree_inconsistent"}


# --- unexpected paths, concurrency and retries ----------------------------------------------


def test_an_unauthorized_change_during_preparation_halts_and_rolls_back(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = publication_stage.replace

    def tampered(source: Path, target: Path) -> None:
        original(source, target)
        if target.name.endswith(".json") and target.parent.name == "releases":
            (target.parents[1] / "README.md").write_bytes(b"changed by someone else\n")

    monkeypatch.setattr(publication_stage, "replace", tampered)
    outcome = bench.prepare()

    assert outcome.status == "halted"
    assert halt_codes(outcome) == {"prepare:unexpected_change"}
    assert outcome.clone is not None
    assert status_of(outcome.clone) == [" M README.md"]
    assert not (outcome.clone / "documents").exists()
    assert not (outcome.clone / publication_stage.JOURNAL).exists()


def test_a_second_preparer_on_the_same_clone_is_refused(bench: Bench) -> None:
    first = bench.prepare()
    assert first.clone is not None

    with release_lock(first.clone), pytest.raises(ForarbeiderRefusedError) as raised:
        bench.prepare(fresh=False)
    assert raised.value.reason is RefusalReason.CORPUS_LOCKED


def test_a_retry_of_a_prepared_transaction_is_unchanged(bench: Bench) -> None:
    first = bench.prepare()
    assert first.clone is not None
    before = {p: (first.clone / p).stat().st_mtime_ns for p in files_of(first)}
    again = bench.prepare(fresh=False)

    assert again.status == "unchanged"
    assert again.transaction == first.transaction
    assert {p: (first.clone / p).stat().st_mtime_ns for p in files_of(first)} == before


def test_a_clone_another_transaction_prepared_is_refused(bench: Bench) -> None:
    first = bench.prepare()
    assert first.clone is not None
    bench.base = commit_files(bench.source, {"README.md": b"moved on\n"}, "unrelated")

    with pytest.raises(ForarbeiderRefusedError) as raised:
        bench.prepare(fresh=False)
    assert raised.value.reason is RefusalReason.CHECKOUT_PENDING_CHANGE


# --- pure checks ----------------------------------------------------------------------------


def test_rendered_counts_must_keep_the_batch_caps() -> None:
    caps = BatchCaps(documents=2, files=5, bytes=100)

    assert within_caps(ProposalCounts(documents=2, files=5, bytes=100), caps)
    assert not within_caps(ProposalCounts(documents=3, files=5, bytes=100), caps)
    assert not within_caps(ProposalCounts(documents=2, files=6, bytes=100), caps)
    assert not within_caps(ProposalCounts(documents=2, files=5, bytes=101), caps)


def test_a_transaction_cannot_claim_authorization_or_publication(bench: Bench) -> None:
    transaction = bench.prepare().transaction
    assert transaction is not None
    raw = transaction.model_dump(mode="json")
    raw["content"]["authorized"] = True

    with pytest.raises(ValidationError):
        PublicationTransaction.model_validate(raw)
