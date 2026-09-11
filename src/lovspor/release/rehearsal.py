"""The first migration, rehearsed on a second Caddy instance (ADR-0014 Validation (g)).

The production cutover is a load that moves the admin endpoint from TCP
to a permissioned Unix socket, and its reverse is a load delivered to
the socket. Neither can be tried twice on the box that serves the site,
so both are walked first on a second instance — its own unit, ports,
runtime directory, drop-in directory and socket, every one of them a
``MigrationHost`` field — and this module is the sequence and the
assertions, not a second copy of the procedure: every step runs through
``migrate``'s own ``first_migration``, ``abandon_first_migration`` and
``rollback_first_migration``.

The ADR's five sub-steps, in order:

* (i) the instance on the previous Caddyfile: TCP answers ``GET
  /config/`` and no socket exists;
* (ii) Migration's steps 3-4 as written, with the address transition
  asserted at the procedure's own checkpoints — the socket absent
  immediately before (c), answering immediately after it, TCP refusing,
  R over the socket naming the envelope, and ``systemctl show`` naming
  the explicit ``--address``; and, before it, a (c) whose configuration
  Caddy rejects at load, which must leave TCP answering, no socket and R
  unmoved — R moves only on a successful load;
* (iii) the first-migration rollback, which must reach the socket:
  afterwards the previous configuration runs, TCP answers, the socket is
  gone and ``ExecReload=`` is stock. Its negative fixture is a plain
  ``systemctl reload`` of the previous Caddyfile through the stock line
  while the socket is running, which must **fail** — ``caddy reload``
  derives the address from the file it supplies — proving the rollback's
  explicit ``--address`` is load-bearing;
* (iv) the cutover again, then one steady-state ``systemctl reload``
  through the drop-in: exit 0, R unchanged, TCP still refusing;
* (v) two restarts, the four facts of :mod:`admin_socket` after each,
  and the negative fixture that proves they would catch a socket
  provisioned once by hand: a Caddyfile without the creation-mode suffix,
  the mode and group set by hand, and a restart that takes them away.

Nothing here can run in CI — there is no ``caddy`` binary and no systemd
on the runner — so the sequencing and every assertion live here, where
``FakeCaddy`` exercises them, and the harness that starts the instance is
a wrapper with no assertions of its own.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.atomic_io import atomic_write_bytes, atomic_write_text
from lovspor.release.admin_socket import (
    DEFAULT_UNPRIVILEGED_USER,
    AdminSocket,
    check_admin_socket,
)
from lovspor.release.caddy import ConfigPair, adapt, config_pair
from lovspor.release.caddy import reload as reload_caddy
from lovspor.release.control import ControlPlane
from lovspor.release.envelope import WORLD_READABLE
from lovspor.release.errors import (
    AdminSocketError,
    MigrationFailedError,
    RehearsalFailedError,
    UnobservableError,
)
from lovspor.release.migrate import (
    PRE_ENVELOPE_DROP_IN,
    SOCKET_MODE,
    MigrationHost,
    abandon_first_migration,
    first_migration,
    rollback_first_migration,
)


@dataclass(frozen=True)
class RehearsalFixtures:
    """The two configurations the negative fixtures need; the harness writes them.

    Both are the rehearsal's own new Caddyfile with one thing taken out,
    and neither is ever installed on the production host: ``rejected``
    validates and fails at load — the harness makes it bind a port that
    is already taken — and ``unsuffixed`` binds the same admin socket
    without the ``|0660`` creation-mode suffix.
    """

    rejected: Path
    unsuffixed: Path


@dataclass(frozen=True)
class Rehearsal:
    """The second instance, the envelope to cut over to, and the two fixtures."""

    plane: ControlPlane
    host: MigrationHost
    fixtures: RehearsalFixtures
    content_id: str
    unprivileged_user: str = DEFAULT_UNPRIVILEGED_USER

    @property
    def access(self) -> AdminSocket:
        """The four facts asked about exactly the host the migration just wrote."""
        return AdminSocket(
            socket_admin=self.host.socket_admin,
            tcp_admin=self.host.tcp_admin,
            release_group=self.host.release_group,
            unprivileged_user=self.unprivileged_user,
            runner=self.plane.runner,
            admin_client=self.host.admin_client,
            ownership=self.host.ownership,
        )


class Step(BaseModel):
    """One assertion of the rehearsal that held, and what it read."""

    model_config = ConfigDict(frozen=True)

    name: str
    detail: str


class RehearsalReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    steps: tuple[Step, ...]

    def describe(self) -> tuple[str, ...]:
        return tuple(f"{step.name}: {step.detail}" for step in self.steps)


def _require(held: bool, step: str, detail: str) -> None:
    """The rehearsal's one refusal: a named sub-step and what did not hold."""
    if not held:
        raise RehearsalFailedError(step, detail)


def _present(path: Path) -> bool:
    """Whether the *name* is taken; a dangling symlink at the socket's name is not absence."""
    return path.is_symlink() or path.exists()


def _running(plan: Rehearsal, address: str) -> ConfigPair | None:
    """The pair the instance runs at ``address``, or ``None`` when nothing answers there."""
    try:
        return config_pair(plan.host.admin_client(address).running_config())
    except UnobservableError:
        return None


def _systemctl(plan: Rehearsal, verb: str, step: str) -> None:
    """``systemctl <verb> <unit>`` on the rehearsal instance; a non-zero exit ends the rehearsal."""
    done = plan.plane.runner.run(("systemctl", verb, plan.host.unit), {})
    _require(
        done.returncode == 0,
        step,
        f"systemctl {verb} {plan.host.unit} failed: {done.stderr.strip() or done.returncode}",
    )


def _daemon_reload(plan: Rehearsal, step: str) -> None:
    """``systemctl daemon-reload`` — no unit name: it reloads systemd's own state."""
    done = plan.plane.runner.run(("systemctl", "daemon-reload"), {})
    _require(
        done.returncode == 0,
        step,
        f"systemctl daemon-reload failed: {done.stderr.strip() or done.returncode}",
    )


def _exec_reload(plan: Rehearsal, step: str) -> str:
    done = plan.plane.runner.run(("systemctl", "show", plan.host.unit, "-p", "ExecReload"), {})
    _require(
        done.returncode == 0,
        step,
        f"systemctl show {plan.host.unit} -p ExecReload failed: {done.stderr.strip()}",
    )
    return done.stdout


def start_on_previous(plan: Rehearsal) -> Step:
    """(i) The instance on the previous Caddyfile: TCP answers, and no socket exists."""
    running = _running(plan, plan.host.tcp_admin)
    if running is None:
        raise RehearsalFailedError("i", f"nothing answers on {plan.host.tcp_admin}")
    _require(
        running.release_id is None,
        "i",
        f"{plan.host.tcp_admin} already runs release {running.release_id}",
    )
    _require(not _present(plan.host.socket), "i", f"{plan.host.socket} exists before the cutover")
    return Step(name="i", detail=f"{plan.host.tcp_admin} answers {running.describe()}; no socket")


def before_cutover(plan: Rehearsal) -> Step:
    """(ii) At the ``validated`` checkpoint, immediately before (c): no socket yet."""
    _require(not _present(plan.host.socket), "ii", f"{plan.host.socket} exists before (c)")
    return Step(name="ii.before-c", detail=f"{plan.host.socket} does not exist")


def after_cutover(plan: Rehearsal) -> Step:
    """(ii) At the ``reloaded`` checkpoint: the socket answers the envelope, TCP is gone."""
    running = _running(plan, plan.host.socket_admin)
    if running is None:
        raise RehearsalFailedError("ii", f"{plan.host.socket_admin} does not answer after (c)")
    _require(
        running.release_id == plan.content_id,
        "ii",
        f"over the socket Caddy runs {running.describe()}, not release {plan.content_id[:12]}",
    )
    _require(
        _running(plan, plan.host.tcp_admin) is None,
        "ii",
        f"{plan.host.tcp_admin} still answers after (c); the admin endpoint did not move",
    )
    return Step(name="ii.after-c", detail=f"the socket answers {running.describe()}; TCP refuses")


_AT_CHECKPOINT: dict[str, Callable[[Rehearsal], Step]] = {
    "validated": before_cutover,
    "reloaded": after_cutover,
}
"""Which of the procedure's own checkpoints the address transition is read at."""


def cutover(plan: Rehearsal) -> tuple[Step, ...]:
    """(ii) Migration's steps 3-4 exactly as written, asserted at its own checkpoints."""
    observed: list[Step] = []

    def at(reached: str) -> None:
        check = _AT_CHECKPOINT.get(reached)
        if check is not None:
            observed.append(check(plan))

    report = first_migration(plan.plane, plan.host, plan.content_id, at)
    wanted = f"--address {plan.host.socket_admin}"
    shown = _exec_reload(plan, "ii")
    _require(wanted in shown, "ii", f"ExecReload does not name {wanted}: {shown.strip()}")
    observed.append(Step(name="ii.exec-reload", detail=f"ExecReload names {wanted}"))
    observed.append(Step(name="ii", detail=f"migrated {report.active[:12]} to {report.admin}"))
    return tuple(observed)


def _refused_load(plan: Rehearsal, host: MigrationHost) -> str:
    """(c) with the rejected configuration; loading it at all ends the rehearsal."""
    try:
        first_migration(plan.plane, host, plan.content_id)
    except MigrationFailedError as error:
        return error.detail
    raise RehearsalFailedError(
        "ii.rejected", f"{host.caddyfile_source} loaded; the fixture must be refused at load"
    )


def rejected_cutover(plan: Rehearsal) -> tuple[Step, ...]:
    """(ii) A (c) Caddy rejects at load: R moves only on a successful load, so nothing moved."""
    before = _running(plan, plan.host.tcp_admin)
    if before is None:
        raise RehearsalFailedError("ii.rejected", f"nothing answers on {plan.host.tcp_admin}")
    failure = _refused_load(plan, replace(plan.host, caddyfile_source=plan.fixtures.rejected))
    after = _running(plan, plan.host.tcp_admin)
    _require(
        after == before,
        "ii.rejected",
        f"{plan.host.tcp_admin} runs {after.describe() if after else 'nothing'}, "
        f"not the previous {before.describe()}",
    )
    _require(
        not _present(plan.host.socket), "ii.rejected", f"{plan.host.socket} exists after a refusal"
    )
    abandon_first_migration(plan.plane, plan.host)
    return (
        Step(name="ii.rejected", detail=f"R unmoved on {plan.host.tcp_admin}: {failure}"),
        _abandoned(plan),
    )


def _abandoned(plan: Rehearsal) -> Step:
    """After *abandon*: the previous Caddyfile is back, and neither record survives it."""
    for path, role in ((plan.plane.fragment, "fragment"), (plan.host.previous_caddyfile, "backup")):
        _require(not _present(path), "ii.rejected", f"the {role} {path} survived the abandon")
    running = _running(plan, plan.host.tcp_admin)
    _require(
        running is not None and running.release_id is None,
        "ii.rejected",
        f"{plan.host.tcp_admin} does not run the pre-envelope configuration after the abandon",
    )
    return Step(
        name="ii.abandoned", detail="the previous Caddyfile is back; no fragment, no backup"
    )


def _install(plan: Rehearsal, data: bytes) -> None:
    atomic_write_bytes(plan.plane.caddyfile, data, mode=WORLD_READABLE)


def _stock_state(plan: Rehearsal) -> None:
    """The previous Caddyfile on disk under the stock ``ExecReload=``, with the socket running."""
    _install(plan, plan.host.previous_caddyfile.read_bytes())
    atomic_write_text(plan.host.drop_in, PRE_ENVELOPE_DROP_IN, mode=WORLD_READABLE)
    _daemon_reload(plan, "iii.plain-reload")


def _restore_state(plan: Rehearsal, kept: tuple[bytes, bytes]) -> None:
    caddyfile, drop_in = kept
    _install(plan, caddyfile)
    atomic_write_bytes(plan.host.drop_in, drop_in, mode=WORLD_READABLE)
    _daemon_reload(plan, "iii.plain-reload")


def plain_reload_refused(plan: Rehearsal) -> Step:
    """(iii) The negative fixture: the stock reload line cannot reach the running socket.

    ``caddy reload`` derives the address from the file it is handed, and
    the previous Caddyfile names a TCP address while the socket is what
    is running — so this must fail, and the rollback's explicit
    ``--address`` is what makes the way back work at all.
    """
    kept = (plan.plane.caddyfile.read_bytes(), plan.host.drop_in.read_bytes())
    _stock_state(plan)
    try:
        done = reload_caddy(plan.plane.runner, plan.host.unit)
    finally:
        _restore_state(plan, kept)
    _require(
        done.returncode != 0,
        "iii.plain-reload",
        f"systemctl reload {plan.host.unit} of the previous Caddyfile reached the socket; "
        "the rollback's explicit --address would then be redundant",
    )
    return Step(name="iii.plain-reload", detail="the stock reload line reached nothing, as it must")


def rollback(plan: Rehearsal) -> tuple[Step, ...]:
    """(iii) The first-migration rollback, delivered to the socket, and its negative fixture."""
    plain = plain_reload_refused(plan)
    previous = adapt(plan.plane.runner, plan.host.previous_caddyfile, plan.plane.fragment)
    report = rollback_first_migration(plan.plane, plan.host)
    running = _running(plan, plan.host.tcp_admin)
    if running is None:
        raise RehearsalFailedError("iii", f"{plan.host.tcp_admin} does not answer afterwards")
    _require(
        running == previous,
        "iii",
        f"{plan.host.tcp_admin} runs {running.describe()}, not the previous {previous.describe()}",
    )
    _require(not _present(plan.host.socket), "iii", f"{plan.host.socket} survived the rollback")
    wanted = f"--address {plan.host.socket_admin}"
    _require(wanted not in _exec_reload(plan, "iii"), "iii", f"ExecReload still names {wanted}")
    return (
        plain,
        Step(name="iii", detail=f"rolled back over {report.admin_before} to {report.admin}"),
    )


def steady_reload(plan: Rehearsal) -> Step:
    """(iv) One steady-state reload through the drop-in: exit 0, R unchanged, TCP still refusing."""
    before = _running(plan, plan.host.socket_admin)
    done = reload_caddy(plan.plane.runner, plan.host.unit)
    _require(
        done.returncode == 0,
        "iv",
        f"systemctl reload {plan.host.unit} failed: {done.stderr.strip() or done.returncode}",
    )
    after = _running(plan, plan.host.socket_admin)
    if after is None:
        raise RehearsalFailedError("iv", f"{plan.host.socket_admin} stopped answering")
    _require(after == before, "iv", f"the reload moved R to {after.describe()}")
    _require(_running(plan, plan.host.tcp_admin) is None, "iv", f"{plan.host.tcp_admin} answers")
    return Step(name="iv", detail=f"the drop-in's reload reached {plan.host.socket_admin}")


def steady_state(plan: Rehearsal) -> tuple[Step, ...]:
    """(iv) The cutover again, then one ``systemctl reload`` through the drop-in's address."""
    return (*cutover(plan), steady_reload(plan))


def _facts(plan: Rehearsal, step: str) -> Step:
    try:
        return Step(name=step, detail=check_admin_socket(plan.access).describe())
    except AdminSocketError as error:
        raise RehearsalFailedError(step, str(error)) from error


def _by_hand(plan: Rehearsal) -> None:
    """What an operator would do once: chmod and chgrp the socket the running Caddy made."""
    plan.host.socket.chmod(SOCKET_MODE)
    gid = plan.host.ownership.gid_of(plan.host.release_group)
    plan.host.ownership.chown(plan.host.socket, -1, gid)


def _facts_refused(plan: Rehearsal) -> str:
    """The four facts must refuse the hand-provisioned socket, on the mode or on the group."""
    try:
        check_admin_socket(plan.access)
    except AdminSocketError as error:
        _require(
            "mode" in str(error) or "gid" in str(error),
            "v.by-hand",
            f"the refusal is not about the mode or the group: {error}",
        )
        return str(error)
    raise RehearsalFailedError(
        "v.by-hand", "a socket whose mode and group were set once by hand passed the four facts"
    )


def hand_provisioned_socket_fails(plan: Rehearsal) -> Step:
    """(v) The negative fixture: a restart takes back a mode and a group set once by hand."""
    kept = plan.plane.caddyfile.read_bytes()
    try:
        _install(plan, plan.fixtures.unsuffixed.read_bytes())
        _systemctl(plan, "restart", "v.by-hand")
        _by_hand(plan)
        _systemctl(plan, "restart", "v.by-hand")
        failure = _facts_refused(plan)
    finally:
        _install(plan, kept)
        _systemctl(plan, "restart", "v.by-hand")
    return Step(name="v.by-hand", detail=f"the recreated socket failed the facts: {failure}")


def restarts(plan: Rehearsal) -> tuple[Step, ...]:
    """(v) Two restarts, the four facts after each, and the hand-provisioned socket that fails."""
    steps: list[Step] = []
    for attempt in ("v.1", "v.2"):
        _systemctl(plan, "restart", attempt)
        steps.append(_facts(plan, attempt))
    steps.append(hand_provisioned_socket_fails(plan))
    return tuple(steps)


def rehearse(plan: Rehearsal) -> RehearsalReport:
    """The whole of ADR-0014 Validation (g), in its order, on the second instance."""
    steps: tuple[Step, ...] = (start_on_previous(plan),)
    steps += rejected_cutover(plan)
    steps += cutover(plan)
    steps += rollback(plan)
    steps += steady_state(plan)
    steps += restarts(plan)
    return RehearsalReport(steps=steps)
