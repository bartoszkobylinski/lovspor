"""Promotion CLI output and persistent effects of the surviving mutations."""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from lovspor.forarbeider import corpus_commands as commands
from lovspor.forarbeider import corpus_models, corpus_render, release_commands
from lovspor.forarbeider import corpus_transaction as transaction
from lovspor.forarbeider.corpus_plan import PromotionPlan
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_models import digest
from tests.unit.cli_output import said
from tests.unit.forarbeider_promotion_support import (
    eligible,
    plan,
    planned,
    release_options,
    sandbox,
    simulate,
    withdraw,
)
from tests.unit.forarbeider_release_support import Desk, at_a_terminal, said_digest  # noqa: F401
from tests.unit.test_forarbeider_mutation_remediation import scope

pytestmark = pytest.mark.usefixtures("at_a_terminal")


def test_sandbox_and_fixture_output(tmp_path: Path) -> None:
    desk = Desk(tmp_path / "desk")
    result = sandbox(desk, tmp_path / "box")
    assert result.exit_code == 0
    assert (
        result.output
        == "SANDBOX created: a new empty Git checkout with a synthetic baseline, no remote\n"
    )
    out = tmp_path / "fixtures"
    result = desk.invoke([*release_options(desk), "synthetic-fixtures", "--out", str(out)])
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "FIXTURES synthetic, invented text only:",
        *sorted(p.name for p in out.iterdir()),
    ]


def test_plan_and_simulation_exact_receipts(tmp_path: Path) -> None:
    desk = Desk(tmp_path / "desk")
    box = tmp_path / "box"
    assert sandbox(desk, box).exit_code == 0
    report, checkpoints = eligible(desk)
    result = plan(desk, report, checkpoints, box)
    assert result.exit_code == 0, result.output
    sha = said_digest(result.output, "plan")
    record = json.loads((desk.workspace / "plans" / f"{sha}.json").read_bytes())
    assert result.output.splitlines() == [
        f"PLAN regjeringen-9000001 plan {sha} action release publication_authorized=false",
        "base forarbeider/manifest.json absent",
        *[
            f"write {item['path']} {item['sha256']} {item['size_bytes']}"
            for item in record["writes"]
        ],
        "synthetic_inputs=true writes_corpus=false",
    ]
    manifest_sha = next(
        item["sha256"] for item in record["writes"] if item["path"] == "forarbeider/manifest.json"
    )
    suffix = (
        f"regjeringen-9000001 plan {sha} in the synthetic sandbox: "
        f"forarbeider/manifest.json {manifest_sha} "
        "(publication_authorized=false, no git add, commit or push)\n"
    )
    first = simulate(desk, sha, box)
    assert first.exit_code == 0, first.output
    assert first.output == "APPLIED " + suffix
    again = simulate(desk, sha, box)
    assert again.exit_code == 0
    assert again.output == "UNCHANGED " + suffix
    result = withdraw(desk, box, "regjeringen-9000001")
    assert result.exit_code == 0, result.output
    stored = said_digest(result.output, "record")
    manifest_sha = digest((box / "forarbeider/manifest.json").read_bytes())
    assert result.output.splitlines() == [
        "Type to confirm: withdraw regjeringen-9000001: withdraw regjeringen-9000001",
        f"WITHDRAWN regjeringen-9000001 record {stored}",
        "removed forarbeider/2099-2100/prop-l/9000001.md",
        f"rewritten forarbeider/manifest.json {manifest_sha}",
        (
            "warning: Git history and every clone keep the earlier bytes;"
            " a revert does not erase them. Taking down a real corpus is "
            "the owner's decision, outside this command."
        ),
    ]


def test_recovered_simulation_receipt(tmp_path: Path) -> None:
    desk = Desk(tmp_path / "desk")
    box = tmp_path / "box"
    assert sandbox(desk, box).exit_code == 0
    sha = planned(desk, box)
    record = json.loads((desk.workspace / "plans" / f"{sha}.json").read_bytes())
    writes = record["writes"]
    stage = box / transaction.STAGING
    stage.mkdir()
    for item in writes:
        data = next(
            p.read_bytes()
            for p in (desk.workspace / "previews").iterdir()
            if digest(p.read_bytes()) == item["sha256"]
        )
        (stage / item["sha256"]).write_bytes(data)
    manifest_sha = next(i["sha256"] for i in writes if i["path"] == "forarbeider/manifest.json")
    journal = {
        "schema_version": 1,
        "record_type": "forarbeider_corpus_journal",
        "target_manifest_sha256": manifest_sha,
        "operations": [{"path": i["path"], "sha256": i["sha256"]} for i in writes],
    }
    (box / transaction.JOURNAL).write_text(json.dumps(journal))
    result = simulate(desk, sha, box)
    assert result.exit_code == 0, result.output
    assert (
        result.output == f"UNCHANGED regjeringen-9000001 plan {sha} in the synthetic sandbox: "
        f"forarbeider/manifest.json {manifest_sha} recovered_interrupted_change "
        "(publication_authorized=false, no git add, commit or push)\n"
    )


@pytest.mark.parametrize("option", ["report", "plan"])
def test_invalid_digest_names_the_option(tmp_path: Path, option: str) -> None:
    desk = Desk(tmp_path / "desk")
    box = tmp_path / "box"
    assert sandbox(desk, box).exit_code == 0
    result = plan(desk, "bad", [], box) if option == "report" else simulate(desk, "bad", box)
    assert result.exit_code == 1
    assert said(f"refused: release_invalid: {option}", result.output), result.output
    assert not (box / "forarbeider").exists()


def verified_inputs(desk: Desk):

    report, checkpoints = eligible(desk)
    stored = json.loads((desk.workspace / "reports" / f"{report}.json").read_bytes())
    run = release_commands.Run(
        authorization=stored["authorization_sha256"],
        checkpoints=tuple(checkpoints),
        at=stored[("evaluated_at")],
    )
    return scope(desk), run, commands._rerun(scope(desk), run, report)


@pytest.mark.parametrize("missing", ["dossier", "authorization", "unobserved"])
def test_rendering_requires_complete_release_evidence(tmp_path: Path, missing: str) -> None:

    _, _, verified = verified_inputs(Desk(tmp_path / "desk"))
    if missing == "unobserved":
        inputs = verified.inputs._replace(observed=())
    else:
        inputs = verified.inputs._replace(**{missing: None})
    with pytest.raises(ForarbeiderRefusedError) as caught:
        commands.rendering_of(verified._replace(inputs=inputs))
    assert (caught.value.reason, caught.value.detail) == (RefusalReason.PROMOTION_DENIED, "report")


def test_publication_date_remains_on_its_own_axis(tmp_path: Path) -> None:

    _, _, verified = verified_inputs(Desk(tmp_path / "desk"))
    metadata = verified.inputs.metadata.model_copy(update={"published_at": date(2099, 12, 1)})
    verified = verified._replace(inputs=verified.inputs._replace(metadata=metadata))
    assert commands.rendering_of(verified).times.source_published_at == date(2099, 12, 1)


def test_rerun_changed_report_and_denial_are_observable(tmp_path: Path) -> None:

    desk = Desk(tmp_path / "desk")
    scope, run, verified = verified_inputs(desk)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        commands._rerun(scope, run, "a" * 64)
    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.PROMOTION_DENIED,
        ("report_changed"),
    )
    desk.evidence["source_rights"].write_bytes(b"changed evidence")
    before = set((desk.workspace / "reports").iterdir())
    shutil.rmtree(desk.workspace / "reports")
    with pytest.raises(ForarbeiderRefusedError) as caught:
        commands._rerun(scope, run, verified.report_sha256)
    assert caught.value.reason == RefusalReason.PROMOTION_DENIED
    saved = desk.workspace / "reports" / f"{caught.value.detail}.json"
    assert saved not in before
    assert saved.is_file()
    assert "reports" in {p.name for p in desk.workspace.iterdir()}
    assert json.loads(saved.read_bytes())["outcome"] == "deny"


def test_authorization_expires_at_equal_clock_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    _, _, verified = verified_inputs(Desk(tmp_path / "desk"))
    expires = verified.inputs.authorization.record.valid_until
    monkeypatch.setattr(commands, "_now", lambda: expires)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        commands._require_unexpired(verified)
    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.PROMOTION_DENIED,
        ("authorization_expired"),
    )


def test_plan_stale_when_only_base_digest_changes(tmp_path: Path) -> None:

    desk = Desk(tmp_path / "desk")
    box = tmp_path / "box"
    assert sandbox(desk, box).exit_code == 0
    sha = planned(desk, box)
    record = PromotionPlan.model_validate_json(
        (desk.workspace / "plans" / f"{sha}.json").read_bytes()
    )
    record = record.model_copy(update={"base_manifest_sha256": "a" * 64})
    with pytest.raises(ForarbeiderRefusedError) as caught:
        commands._simulated_draft(scope(desk), record, box)
    assert (caught.value.reason, caught.value.detail) == (RefusalReason.PLAN_STALE, "plan")


def test_recovery_can_precede_a_new_applied_change(tmp_path: Path) -> None:

    desk = Desk(tmp_path / "desk")
    box = tmp_path / "box"
    assert sandbox(desk, box).exit_code == 0
    (box / "forarbeider").mkdir()
    files = {
        "forarbeider/manifest.json": corpus_models.manifest_text(corpus_models.EMPTY_MANIFEST),
        "forarbeider/INDEX.md": corpus_render.render_index(corpus_models.EMPTY_MANIFEST),
    }
    for name, text in files.items():
        (box / name).write_text(text)
    sha = planned(desk, box)
    journal = {
        "schema_version": 1,
        "record_type": "forarbeider_corpus_journal",
        "target_manifest_sha256": digest(files["forarbeider/manifest.json"].encode()),
        "operations": [
            {"path": name, "sha256": digest(text.encode())} for name, text in files.items()
        ],
    }
    (box / transaction.JOURNAL).write_text(json.dumps(journal))
    result = simulate(desk, sha, box)
    assert result.exit_code == 0, result.output
    target = digest((box / "forarbeider/manifest.json").read_bytes())
    assert result.output == (
        f"APPLIED regjeringen-9000001 plan {sha} in the synthetic sandbox: "
        f"forarbeider/manifest.json {target} recovered_interrupted_change "
        "(publication_authorized=false, no git add, commit or push)\n"
    )


def test_release_plan_without_report_is_refused_before_simulation(tmp_path: Path) -> None:
    desk = Desk(tmp_path / "desk")
    box = tmp_path / "box"
    assert sandbox(desk, box).exit_code == 0
    sha = planned(desk, box)
    record = json.loads((desk.workspace / "plans" / f"{sha}.json").read_bytes())
    record["report_sha256"] = None
    raw = (json.dumps(record, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()
    torn = digest(raw)
    (desk.workspace / "plans" / f"{torn}.json").write_bytes(raw)
    (desk.workspace / "plans" / f"{torn}.json").chmod(0o600)
    result = simulate(desk, torn, box)
    assert result.exit_code == 1
    assert said("refused: release_record_corrupt", result.output), result.output
    assert not (box / "forarbeider").exists()
