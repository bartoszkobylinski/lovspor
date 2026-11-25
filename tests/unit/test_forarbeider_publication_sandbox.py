"""AR4b-1: a simulation sends only through disposable test infrastructure (#738).

A simulation waives the AR1, FC7b and AR5-AR7 checks, so the label alone must
not let a caller aim it at a real endpoint or a real repository. Path A sends
only through the in-memory :class:`SimulatedGitHub`; path B pushes only to a
:class:`DisposableRemote` that was made fresh in a private sandbox and is
re-verified on disk before every use.
"""

import ast
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import publication_attempts, publication_path_b, publication_sandbox
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.publication_authorize import (
    AdapterFacts,
    AuthorizationContext,
    authorize,
)
from lovspor.forarbeider.publication_execute import (
    Simulation,
    execute,
    next_attempt_id,
    publish_authorized,
)
from lovspor.forarbeider.publication_sandbox import (
    DisposableRemote,
    SimulatedGitHub,
    create_disposable_remote,
)
from tests.unit.forarbeider_execution_support import APP, NOW, KeySigner, Rig, make_rig
from tests.unit.forarbeider_publication_support import git
from tests.unit.forarbeider_release_control_support import BOT

NOT_SANDBOXED = "simulation_transport_not_sandboxed"
NOT_DISPOSABLE = "simulation_remote_not_disposable"


@pytest.fixture
def rig_a(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "a", "A")


@pytest.fixture
def rig_b(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "b", "B")


def attempts(rig: Rig) -> list[Path]:
    folder = rig.workspace / rig.transaction.transaction_id
    return sorted(folder.iterdir()) if folder.exists() else []


class ForeignTransport:
    """A transport that is not the simulated one: every touch is recorded."""

    def __init__(self, head_oid: str) -> None:
        self.head_oid = head_oid
        self.touched: list[str] = []

    @property
    def identity(self) -> Any:
        self.touched.append("identity")
        return APP

    def head(self, repository: str, branch: str) -> str | None:
        self.touched.append("head")
        return self.head_oid

    def post(self, payload: dict[str, Any]) -> Any:
        self.touched.append("post")
        return {"data": {"createCommitOnBranch": {"commit": {"oid": "f" * 40}}}}


class RealEndpoint(SimulatedGitHub):  # type: ignore[misc]
    """Stands in for a client of the real API: it would send to GitHub."""

    endpoint = "https://api.github.com/graphql"
    sent: list[dict[str, Any]] = []  # noqa: RUF012

    def post(self, payload: dict[str, Any]) -> Any:
        RealEndpoint.sent.append(payload)
        return super().post(payload)


# --- path A -----------------------------------------------------------------------------------


def test_an_arbitrary_transport_is_refused_before_anything_is_read_or_posted(rig_a: Rig) -> None:
    foreign = ForeignTransport(rig_a.base)

    with pytest.raises(ForarbeiderRefusedError) as raised:
        execute(rig_a.request(), Simulation(clock=lambda: NOW, transport=foreign))  # type: ignore[arg-type]

    assert raised.value.detail == NOT_SANDBOXED
    assert foreign.touched == []
    assert attempts(rig_a) == []


def test_a_real_endpoint_never_receives_a_mutation(rig_a: Rig) -> None:
    RealEndpoint.sent.clear()
    real = RealEndpoint(head_oid=rig_a.base, identity=APP)

    with pytest.raises(ForarbeiderRefusedError) as raised:
        execute(rig_a.request(), Simulation(clock=lambda: NOW, transport=real))

    assert raised.value.detail == NOT_SANDBOXED
    assert RealEndpoint.sent == [] and real.calls == []
    assert attempts(rig_a) == []


def test_genuine_evidence_cannot_be_spent_on_a_foreign_transport(rig_a: Rig) -> None:
    github = SimulatedGitHub(head_oid=rig_a.base, identity=APP)
    context = AuthorizationContext(
        rig_a.inputs,
        rig_a.root,
        rig_a.clone,
        lambda: github.head(github.repository, "main"),
        NOW,
        next_attempt_id(rig_a.request()),
        "simulation",
        AdapterFacts(app=APP),
        rig_a.anchoring(),
        rig_a.workspace,
        rig_a.ledger,
        rig_a.quarantine,
    )
    evidence = authorize(context)
    assert evidence.decision == "simulation_only", evidence.denied_by
    foreign = ForeignTransport(rig_a.base)

    with pytest.raises(ForarbeiderRefusedError) as raised:
        publish_authorized(
            evidence,
            rig_a.request(),
            Simulation(clock=lambda: NOW, transport=foreign),  # type: ignore[arg-type]
        )

    assert raised.value.detail == NOT_SANDBOXED
    assert foreign.touched == []
    assert attempts(rig_a) == []


def test_the_simulated_endpoint_cannot_have_its_methods_replaced() -> None:
    github = SimulatedGitHub(head_oid="0" * 40, identity=APP)

    with pytest.raises(AttributeError):
        github.post = lambda payload: None  # type: ignore[method-assign]


@pytest.mark.structural
def test_the_sandbox_imports_nothing_that_can_reach_a_network() -> None:
    tree = ast.parse(Path(publication_sandbox.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    allowed = {"base64", "collections.abc", "dataclasses", "datetime", "hmac", "os"}
    allowed |= {"pathlib", "secrets", "stat", "typing"}
    foreign = {
        name
        for name in imported
        if name not in allowed and name.split(".")[:2] != ["lovspor", "forarbeider"]
    }
    assert foreign == set()


def test_the_simulated_endpoint_still_publishes(rig_a: Rig) -> None:
    github = SimulatedGitHub(head_oid=rig_a.base, identity=APP)
    outcome = execute(rig_a.request(), Simulation(clock=lambda: NOW, transport=github))

    assert outcome.status == "published"
    assert len(github.calls) == 1


def test_a_lost_answer_from_the_simulated_endpoint_still_requires_recovery(rig_a: Rig) -> None:
    github = SimulatedGitHub(head_oid=rig_a.base, identity=APP, fault="lost")
    first = execute(rig_a.request(), Simulation(clock=lambda: NOW, transport=github))
    github.fault = None
    again = execute(rig_a.request(), Simulation(clock=lambda: NOW, transport=github))

    assert (first.status, again.status) == ("outcome_unknown", "recovery_required")
    assert len(github.calls) == 1


# --- path B -----------------------------------------------------------------------------------


def simulation_b(rig: Rig, remote: Any) -> Simulation:
    return Simulation(clock=lambda: NOW, remote=remote, signer=KeySigner(BOT, rig.bot))


def bare_copy(rig: Rig, where: Path) -> Path:
    """A pre-existing bare repository with ``main`` at the base, not made by the sandbox."""
    where.mkdir(mode=0o700, parents=True)
    target = where / "remote.git"
    subprocess.run(["git", "clone", "--bare", "--quiet", str(rig.source), str(target)], check=True)
    return target


def refused_push(rig: Rig, remote: Any, watched: Path) -> None:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        execute(rig.request(), simulation_b(rig, remote))
    assert raised.value.detail == NOT_DISPOSABLE
    assert git(watched, "rev-parse", "main") == rig.base
    assert attempts(rig) == []


def test_an_arbitrary_local_git_path_is_refused(rig_b: Rig) -> None:
    target = bare_copy(rig_b, rig_b.tmp / "elsewhere")

    refused_push(rig_b, target, target)


def test_a_hand_made_remote_over_a_pre_existing_repository_is_refused(rig_b: Rig) -> None:
    target = bare_copy(rig_b, rig_b.tmp / "elsewhere")
    forged = DisposableRemote(target.parent, rig_b.remote.token)

    refused_push(rig_b, forged, target)


def test_a_remote_with_another_token_is_refused(rig_b: Rig) -> None:
    forged = DisposableRemote(rig_b.remote.sandbox, "0" * 64)

    refused_push(rig_b, forged, rig_b.bare)


def test_an_escaping_sandbox_path_is_refused(rig_b: Rig) -> None:
    sandbox = rig_b.remote.sandbox
    escaped = DisposableRemote(sandbox / ".." / sandbox.name, rig_b.remote.token)

    refused_push(rig_b, escaped, rig_b.bare)


def test_a_symlinked_sandbox_is_refused(rig_b: Rig) -> None:
    link = rig_b.tmp / "link"
    link.symlink_to(rig_b.remote.sandbox)

    refused_push(rig_b, DisposableRemote(link, rig_b.remote.token), rig_b.bare)


def test_a_remote_swapped_for_a_symlink_is_refused(rig_b: Rig) -> None:
    target = bare_copy(rig_b, rig_b.tmp / "elsewhere")
    rig_b.bare.rename(rig_b.tmp / "moved.git")
    rig_b.bare.symlink_to(target)

    refused_push(rig_b, rig_b.remote, target)


@pytest.mark.parametrize(
    "tamper",
    ["config", "hook", "alternates", "extra_entry", "group_readable"],
)
def test_unexpected_repository_configuration_is_refused(rig_b: Rig, tamper: str) -> None:
    bare = rig_b.bare
    if tamper == "config":
        with (bare / "config").open("a") as stream:
            stream.write("[receive]\n\tdenyNonFastForwards = false\n")
    elif tamper == "hook":
        (bare / "hooks").mkdir()
        (bare / "hooks" / "pre-receive").write_text("#!/bin/sh\nexit 0\n")
    elif tamper == "alternates":
        (bare / "objects" / "info" / "alternates").write_text(f"{rig_b.source}/.git/objects\n")
    elif tamper == "extra_entry":
        (rig_b.remote.sandbox / "other").mkdir()
    else:
        rig_b.remote.sandbox.chmod(0o750)

    refused_push(rig_b, rig_b.remote, bare)


def test_a_disposable_remote_refuses_non_fast_forward_updates_itself(rig_b: Rig) -> None:
    config = (rig_b.bare / "config").read_text()

    assert "denyNonFastForwards = true" in config
    assert "denyDeletes = true" in config
    assert not (rig_b.bare / "hooks").exists()


@pytest.mark.parametrize("kind", ["not_empty", "symlink", "group_readable", "escaping"])
def test_a_disposable_remote_is_made_only_in_a_private_empty_sandbox(rig_b: Rig, kind: str) -> None:
    sandbox = rig_b.tmp / "fresh"
    sandbox.mkdir(mode=0o700)
    if kind == "not_empty":
        (sandbox / "remote.git").mkdir()
    elif kind == "symlink":
        link = rig_b.tmp / "fresh-link"
        link.symlink_to(sandbox)
        sandbox = link
    elif kind == "group_readable":
        sandbox.chmod(0o750)
    else:
        sandbox = rig_b.tmp / "fresh" / ".." / "fresh"

    with pytest.raises(ForarbeiderRefusedError) as raised:
        create_disposable_remote(sandbox, rig_b.clone, rig_b.base)
    assert raised.value.detail == NOT_DISPOSABLE


def test_the_disposable_remote_still_publishes(rig_b: Rig) -> None:
    outcome = execute(rig_b.request(), simulation_b(rig_b, rig_b.remote))

    assert outcome.status == "published"
    assert git(rig_b.bare, "rev-parse", "main") == outcome.commit


def test_an_unreadable_remote_after_the_push_still_requires_recovery(
    rig_b: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = publication_path_b.remote_head
    reads: list[Path] = []

    def flaky(bare: Path) -> str | None:
        reads.append(bare)
        return real(bare) if len(reads) == 1 else None

    monkeypatch.setattr(publication_path_b, "remote_head", flaky)
    first = execute(rig_b.request(), simulation_b(rig_b, rig_b.remote))
    monkeypatch.setattr(publication_path_b, "remote_head", real)
    again = execute(rig_b.request(), simulation_b(rig_b, rig_b.remote))

    assert (first.status, again.status) == ("outcome_unknown", "recovery_required")


def test_a_remote_tampered_after_the_attempt_is_recorded_is_never_pushed(
    rig_b: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = publication_attempts.append

    def append_then_tamper(workspace: Path, record: Any) -> None:
        original(workspace, record)
        if record.state == "prepared_to_send":
            with (rig_b.bare / "config").open("a") as stream:
                stream.write("[receive]\n\tdenyNonFastForwards = false\n")

    monkeypatch.setattr(publication_attempts, "append", append_then_tamper)
    with pytest.raises(ForarbeiderRefusedError) as raised:
        execute(rig_b.request(), simulation_b(rig_b, rig_b.remote))
    monkeypatch.setattr(publication_attempts, "append", original)

    assert raised.value.detail == NOT_DISPOSABLE
    assert git(rig_b.bare, "rev-parse", "main") == rig_b.base
    again = execute(rig_b.request(), simulation_b(rig_b, rig_b.remote))
    assert again.status == "recovery_required"
