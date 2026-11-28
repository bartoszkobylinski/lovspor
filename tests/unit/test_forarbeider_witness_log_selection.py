"""AR7b-1: one authoritative witness log per repository and purpose (#738, #1052).

AR7a's only gate, ``require_current``, accepts every owner-approved, pinned
log a caller offers: a second log for the same repository and purpose, a
switch between logs, a purpose nobody registered, and a trust root the caller
built (red at eef5351c). The engine's reviewed registry now selects one log,
and the publication-facing gate ``require_selected`` requires three things:
owner approval, the engine's selection and AR7a currentness.

Every signature is a real ``ssh-keygen`` signature, every authority is a real
bare Git repository, and the restarted engine is a real spawned process.
"""

import inspect
import shutil
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

import pytest

import lovspor
from lovspor.forarbeider import policy_trust, witness_log_registry
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.version_models import canonical_text
from lovspor.forarbeider.witness_checkpoint import TrustedLog, require_current
from lovspor.forarbeider.witness_checkpoint_models import Checkpoint, genesis, successor
from lovspor.forarbeider.witness_log_registry import (
    ENGINE_LOG_REGISTRY_SHA256,
    REGISTRY_FILE,
    LogRegistry,
    load_log_registry,
    parse_log_registry,
    require_selected,
    select,
)
from tests.unit.forarbeider_checkpoint_support import (
    REPOSITORY,
    World,
    entry,
    key_fingerprint,
    make_world,
    pin_commit,
    push_commit,
    restore,
    signed,
    snapshot,
)
from tests.unit.forarbeider_log_selection_support import (
    DOMAIN,
    Build,
    another_log,
    log_id,
    registry_bytes,
    select_in_child,
    write_build,
)

PURPOSE = "quarantine_succession"
Engine = Callable[[Build], None]


@pytest.fixture
def world(tmp_path: Path) -> World:
    return make_world(tmp_path)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    """Run the rest of the test as an engine build (restored afterwards)."""

    def run_as(build: Build) -> None:
        monkeypatch.setattr(witness_log_registry, "_DIRECTORY", build.directory)
        monkeypatch.setattr(
            witness_log_registry, "ENGINE_LOG_REGISTRY_SHA256", build.registry_sha256
        )
        monkeypatch.setattr(policy_trust, "ENGINE_TRUST_ROOT_SHA256", build.trust_root_sha256)

    return run_as


def build_for(world: World, name: str, *lines: tuple[str, str, str]) -> Build:
    return write_build(world.tmp / "engine" / name, registry_bytes(*lines), world.root.sha256)


def selecting(world: World, name: str, log: TrustedLog) -> Build:
    return build_for(world, name, (REPOSITORY, PURPOSE, log_id(log)))


def refused(world: World, detail: str, log: TrustedLog | None = None) -> None:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        require_selected(world.witnessing(**({} if log is None else {"log": log})))
    assert raised.value.detail == f"log_selection_{detail}"


# --- the shipped engine selects nothing ----------------------------------------------------------


def test_the_shipped_registry_is_empty_and_pinned_by_its_own_digest() -> None:
    shipped = load_log_registry()

    assert shipped.logs == ()
    assert shipped.sha256 == ENGINE_LOG_REGISTRY_SHA256 != policy_trust.ENGINE_TRUST_ROOT_SHA256
    assert shipped.origin == "engine_pinned"


def test_the_shipped_engine_selects_no_log(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    assert select(world.witnessing()).status == "trust_root_not_engine_pinned"
    monkeypatch.setattr(policy_trust, "ENGINE_TRUST_ROOT_SHA256", world.root.sha256)

    verdict = select(world.witnessing())

    assert verdict.status == "log_unregistered"
    assert (verdict.owner_approved, verdict.authoritative, verdict.pinned_consistent) == (
        True,
        False,
        False,
    )
    refused(world, "log_unregistered")


# --- the selected log, and every other ------------------------------------------------------------


def test_the_selected_log_passes_all_three_checks(world: World, engine: Engine) -> None:
    world.advance(1)
    engine(selecting(world, "a", world.log))

    verdict = select(world.witnessing())
    selected = require_selected(world.witnessing())

    assert verdict.status == "selected"
    assert (verdict.owner_approved, verdict.authoritative, verdict.pinned_consistent) == (
        True,
        True,
        True,
    )
    assert selected.log_id == verdict.log_id == verdict.selected_log_id == log_id(world.log)
    assert selected.checkpoint.sequence == 1
    assert selected.registry_sha256 == witness_log_registry.ENGINE_LOG_REGISTRY_SHA256


def test_a_second_approved_and_pinned_log_is_not_selected(world: World, engine: Engine) -> None:
    """Red: the same repository and purpose, another nonce and another independent authority,
    genuinely owner-signed and pinned. AR7a verifies it; the engine does not select it."""
    other = another_log(world, "b")
    world.advance(1)
    world.advance(9, log=other)
    engine(selecting(world, "a", world.log))

    verdict = select(world.witnessing(log=other))

    assert require_current(world.witnessing(log=other)).sequence == 1
    assert verdict.status == "log_not_selected"
    assert (verdict.owner_approved, verdict.authoritative, verdict.pinned_consistent) == (
        True,
        False,
        False,
    )
    assert verdict.selected_log_id == log_id(world.log) != verdict.log_id
    refused(world, "log_not_selected", other)


def test_a_caller_switching_between_logs_never_moves_the_selection(
    world: World, engine: Engine
) -> None:
    other = another_log(world, "b")
    engine(selecting(world, "a", world.log))

    statuses = [select(world.witnessing(log=log)).status for log in (world.log, other, world.log)]

    assert statuses == ["selected", "log_not_selected", "selected"]


@pytest.mark.parametrize("field", ["purpose", "repository"])
def test_an_unknown_repository_or_purpose_is_unregistered(
    world: World, engine: Engine, field: str
) -> None:
    value = {"purpose": "never_registered", "repository": "someone/elsewhere"}[field]
    other = another_log(world, "b", **{field: value})
    engine(selecting(world, "a", world.log))

    assert select(world.witnessing(log=other)).status == "log_unregistered"
    refused(world, "log_unregistered", other)


def test_a_log_with_the_selected_repository_but_another_identity_is_not_selected(
    world: World, engine: Engine
) -> None:
    """Wrong identity: one witness key more, or a registry naming a near-miss digest."""
    keys = (*world.bootstrap.witness_keys, key_fingerprint(world.second_witness))
    wider = another_log(world, "b", witness_keys=keys, nonce=world.bootstrap.nonce)
    near = log_id(world.log)[:-1] + ("0" if log_id(world.log)[-1] != "0" else "1")
    engine(build_for(world, "near", (REPOSITORY, PURPOSE, near)))

    assert select(world.witnessing()).status == "log_not_selected"
    assert select(world.witnessing(log=wider)).status == "log_not_selected"


def test_a_tampered_bootstrap_is_not_owner_approved(world: World, engine: Engine) -> None:
    engine(selecting(world, "a", world.log))
    tampered = world.bootstrap.model_copy(update={"nonce": "c" * 64})
    log = TrustedLog(canonical_text(tampered).encode(), world.signature, world.root)

    verdict = select(world.witnessing(log=log))

    assert (verdict.status, verdict.owner_approved, verdict.authoritative) == (
        "bootstrap_invalid",
        False,
        False,
    )


# --- the registry is the engine's, and only the engine's ------------------------------------------


def test_an_altered_mapping_without_a_reviewed_digest_selects_nothing(
    world: World, engine: Engine
) -> None:
    """Red: a rotation attempted by editing the shipped file, the digest in code unchanged."""
    other = another_log(world, "b")
    build = selecting(world, "a", world.log)
    engine(build)
    (build.directory / REGISTRY_FILE).write_bytes(
        registry_bytes((REPOSITORY, PURPOSE, log_id(other)))
    )

    assert select(world.witnessing(log=other)).status == "registry_not_engine_pinned"
    assert select(world.witnessing()).status == "registry_not_engine_pinned"
    refused(world, "registry_not_engine_pinned")


def test_a_reviewed_rotation_selects_the_new_log_and_never_falls_back(
    world: World, engine: Engine
) -> None:
    other = another_log(world, "b")
    engine(selecting(world, "a", world.log))
    assert select(world.witnessing()).status == "selected"

    engine(selecting(world, "rotated", other))

    assert select(world.witnessing(log=other)).status == "selected"
    assert select(world.witnessing()).status == "log_not_selected"


def test_rolling_the_registry_back_after_a_rotation_selects_nothing(
    world: World, engine: Engine
) -> None:
    """Substituted trust material: the pre-rotation file restored under the rotated build."""
    other = another_log(world, "b")
    old = selecting(world, "a", world.log)
    rotated = selecting(world, "rotated", other)
    engine(rotated)
    shutil.copyfile(old.directory / REGISTRY_FILE, rotated.directory / REGISTRY_FILE)

    assert select(world.witnessing()).status == "registry_not_engine_pinned"
    assert select(world.witnessing(log=other)).status == "registry_not_engine_pinned"


def test_a_caller_built_trust_root_is_not_the_engines(world: World, engine: Engine) -> None:
    """Red: the caller's own root holds its own owner and witness keys; the bootstrap the
    registry selects is unchanged, but the root that judged it is not the engine's."""
    engine(selecting(world, "a", world.log)._replace(trust_root_sha256="f" * 64))

    verdict = select(world.witnessing())

    assert verdict.status == "trust_root_not_engine_pinned"
    assert not verdict.authoritative
    refused(world, "trust_root_not_engine_pinned")


@pytest.mark.parametrize("damage", ["missing", "symlink", "directory"])
def test_a_registry_that_cannot_be_read_denies(world: World, engine: Engine, damage: str) -> None:
    build = selecting(world, "a", world.log)
    engine(build)
    path = build.directory / REGISTRY_FILE
    elsewhere = world.tmp / "elsewhere"
    path.rename(elsewhere)
    if damage == "symlink":
        path.symlink_to(elsewhere)
    if damage == "directory":
        path.mkdir()

    assert select(world.witnessing()).status == "registry_unavailable"
    refused(world, "registry_unavailable")


@pytest.mark.parametrize(
    "lines",
    [
        "twice_by_purpose",
        "twice_by_log",
        "malformed",
        "not_ascii",
        "trailing_space",
    ],
)
def test_an_ambiguous_or_malformed_registry_refuses_whole(lines: str) -> None:
    a, b = "a" * 64, "b" * 64
    data = {
        "twice_by_purpose": registry_bytes((REPOSITORY, PURPOSE, a), (REPOSITORY, PURPOSE, b)),
        "twice_by_log": registry_bytes((REPOSITORY, PURPOSE, a), (REPOSITORY, "other", a)),
        "malformed": f"{REPOSITORY} {PURPOSE}\n".encode(),
        "not_ascii": f"{REPOSITORY} {PURPOSE} {a}\n# æ\n".encode(),
        "trailing_space": f"{REPOSITORY} {PURPOSE} {a} \n".encode(),
    }[lines]

    with pytest.raises(ForarbeiderRefusedError) as raised:
        parse_log_registry(data)

    assert raised.value.detail.startswith("witness log registry: ")


def test_a_malformed_engine_registry_denies_as_invalid(world: World, engine: Engine) -> None:
    lines = ((REPOSITORY, PURPOSE, log_id(world.log)), (REPOSITORY, PURPOSE, "b" * 64))
    engine(build_for(world, "twice", *lines))

    verdict = select(world.witnessing())

    assert verdict.status == "registry_invalid"
    assert verdict.reasons == ("witness log registry: a repository and purpose are listed twice",)


def test_the_registry_digest_is_domain_separated_and_covers_comments(world: World) -> None:
    data = registry_bytes((REPOSITORY, PURPOSE, log_id(world.log)))

    plain, commented = parse_log_registry(data), parse_log_registry(b"# note\n" + data)

    assert plain.logs == commented.logs
    assert plain.sha256 != commented.sha256
    assert plain.sha256 == sha256(DOMAIN + data).hexdigest()


def test_no_caller_can_name_the_registry() -> None:
    assert list(inspect.signature(load_log_registry).parameters) == []
    assert list(inspect.signature(select).parameters) == ["witnessing"]
    assert list(inspect.signature(require_selected).parameters) == ["witnessing"]


# --- selection never replaces AR7a's consistency --------------------------------------------------


def test_a_selected_log_whose_authority_is_unavailable_is_not_current(
    world: World, engine: Engine
) -> None:
    engine(selecting(world, "a", world.log))
    shutil.rmtree(world.bare)

    verdict = select(world.witnessing())

    assert verdict.status == "log_not_current"
    assert verdict.reasons == ("checkpoint_authority_unavailable",)
    assert (verdict.authoritative, verdict.pinned_consistent) == (True, False)
    refused(world, "log_not_current")


def test_a_selected_log_with_an_invalid_lineage_is_not_current(
    world: World, engine: Engine
) -> None:
    world.advance(1)
    engine(selecting(world, "a", world.log))
    garbage = pin_commit(world, b"{}", world.tip())
    two = pin_commit(world, signed(world, chain_of(world, 2)[2]), garbage)
    assert "rejected" not in push_commit(world, two)

    verdict = select(world.witnessing())

    assert (verdict.status, verdict.reasons) == ("log_not_current", ("checkpoint_pin_invalid",))


def test_a_selected_log_whose_store_was_rolled_back_is_refused_though_its_pin_is_known(
    world: World, engine: Engine
) -> None:
    """The pin still names a checkpoint, so only the currentness flag stands between it
    and the gate."""
    world.advance(1)
    saved = snapshot(world.store.location, "before")
    world.advance(2)
    restore(world.store.location, saved)
    engine(selecting(world, "a", world.log))

    verdict = select(world.witnessing())

    assert verdict.status == "log_not_current"
    assert verdict.checkpoint is not None and verdict.checkpoint.pinned is not None
    assert (verdict.authoritative, verdict.pinned_consistent) == (True, False)
    refused(world, "log_not_current")


def test_a_registry_built_around_the_parser_never_selects_an_ambiguous_mapping() -> None:
    ambiguous = LogRegistry(b"", ((REPOSITORY, PURPOSE, "a" * 64), (REPOSITORY, PURPOSE, "b" * 64)))

    assert ambiguous.selected(REPOSITORY, PURPOSE) is None


def test_an_unselected_log_is_refused_before_its_authority_is_read(
    world: World, engine: Engine
) -> None:
    other = another_log(world, "b")
    engine(selecting(world, "a", world.log))
    shutil.rmtree(world.tmp / "authority-b")

    assert select(world.witnessing(log=other)).status == "log_not_selected"


def test_a_stale_selection_keeps_the_reviewed_log_until_a_reviewed_rotation(
    world: World, engine: Engine
) -> None:
    """The owner approves and pins a newer log; the engine still selects the reviewed one."""
    engine(selecting(world, "a", world.log))
    newer = another_log(world, "newer")
    world.advance(3, log=newer)

    assert select(world.witnessing(log=newer)).status == "log_not_selected"
    assert select(world.witnessing()).status == "selected"


def test_a_restarted_engine_selects_from_its_build_not_from_the_caller(world: World) -> None:
    other = another_log(world, "b")
    build = selecting(world, "a", world.log)

    statuses = [select_in_child(build, world, log) for log in (other, world.log)]

    assert statuses == ["log_not_selected", "selected"]


def test_nothing_in_src_imports_the_selection_yet() -> None:
    """AR5 call sites are unchanged: no source module imports the new registry."""
    root = Path(lovspor.__file__).parent
    importers = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        for line in p.read_text().splitlines()
        if line.lstrip().startswith(("import ", "from ")) and "witness_log_registry" in line
    ]

    assert importers == []


def chain_of(world: World, count: int) -> list[Checkpoint]:
    chain = [genesis(log_id(world.log))]
    for n in range(1, count + 1):
        chain.append(successor(chain[-1], entry(n)))
    return chain
