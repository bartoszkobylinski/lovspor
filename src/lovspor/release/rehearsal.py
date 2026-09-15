"""The first migration, rehearsed on a second Caddy instance (ADR-0014 Validation (g)).

The production cutover is a load that moves the admin endpoint from TCP
to a permissioned Unix socket, and its reverse is a load delivered to
the socket. Neither can be tried twice on the box that serves the site,
so both are walked first on a second instance — its own unit, ports,
runtime directory, drop-in directory and socket, every one of them a
``MigrationHost`` field — and this module is the sequence and the
assertions, not a second copy of the procedure: every step runs through
``migrate``'s own ``first_migration`` and ``rollback_first_migration``,
and the way out of a refused load through ``reconcile`` itself.

The ADR's five sub-steps, in order:

* (i) the instance on the previous Caddyfile: TCP answers ``GET
  /config/`` and no socket exists;
* (ii) Migration's steps 3-4 as written, with the address transition
  asserted at the procedure's own checkpoints — the socket absent
  immediately before (c), answering immediately after it, TCP refusing,
  R over the socket naming the envelope, and ``systemctl show`` naming
  the explicit ``--address``; and, before it, a (c) whose configuration
  Caddy rejects at load. R must stay the previous configuration — R
  moves only on a successful load — but the admin endpoint does not wait
  for one: the droplet's Caddy v2.11.4 started the rejected
  configuration's socket endpoint and stopped TCP before the site failed
  to start (#302). ADR-0014 Amendment 1 requires that ordering exactly —
  R read over the socket, TCP refusing, the running configuration still
  naming TCP — so a Caddy that orders a refusal differently fails the
  step and the migration stops for review. ``reconcile --abandon``, the
  way out README step 5 names, then must leave TCP answering the previous
  configuration, nothing answering on the socket, no file at its name
  and nothing of the attempt behind, so the cutover starts again from the
  state (i) asserted;
* (iii) the first-migration rollback, which must reach the socket:
  afterwards the previous configuration runs, TCP answers, nothing
  answers on the socket and no file is left at its name, and
  ``ExecReload=`` is stock. Its negative fixture is a plain
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
    SOCKET_MODE,
    MigrationHost,
    drop_in_text,
    first_migration,
    rollback_first_migration,
    stranded_on_socket,
)
from lovspor.release.reconcile import ReconcileReport, reconcile


@dataclass(frozen=True)
class RehearsalFixtures:
    """The two configurations the negative fixtures need; the harness writes them.

    Both are the rehearsal's own new Caddyfile with one thing changed,
    and neither is ever installed on the production host: ``rejected``
    validates and fails at load — the harness adds a site on a port this
    Caddy may not bind, which the droplet refused with *permission
    denied* — and ``unsuffixed`` binds the same admin socket without the
    ``|0660`` creation-mode suffix.
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


def _described(pair: ConfigPair | None) -> str:
    return pair.describe() if pair is not None else "nothing"


def _moved_to_socket(plan: Rehearsal) -> ConfigPair:
    """After the refusal the socket answers and TCP does not; the pair read over the socket."""
    tcp, socket = plan.host.tcp_admin, plan.host.socket_admin
    on_tcp = _running(plan, tcp)
    _require(
        on_tcp is None,
        "ii.rejected",
        f"{tcp} still answers after the refusal, running {_described(on_tcp)}; Caddy v2.11.4 "
        f"moves the admin endpoint to {socket}, and any other ordering stops the migration for "
        "review",
    )
    on_socket = _running(plan, socket)
    if on_socket is None:
        raise RehearsalFailedError(
            "ii.rejected", f"nothing answers on {tcp} or {socket} after the refusal"
        )
    return on_socket


def refused_on_socket(plan: Rehearsal, before: ConfigPair, failure: str) -> Step:
    """(ii) The ordering the droplet's Caddy v2.11.4 gave a refused load, exactly (#302).

    ADR-0014 Amendment 1: the rejected configuration's socket answers, TCP
    refuses, and what runs over the socket is still the previous
    configuration — its routes, and its admin option naming TCP. The pair
    hashes the routes and cannot see the admin option, so that half of the
    pairing is asked the way ``reconcile`` asks it.
    """
    socket = plan.host.socket_admin
    running = _moved_to_socket(plan)
    _require(
        running == before,
        "ii.rejected",
        f"over {socket} Caddy runs {running.describe()}, not the previous {before.describe()}; "
        "R moves only on a successful load",
    )
    _require(
        stranded_on_socket(plan.host, socket),
        "ii.rejected",
        f"the configuration over {socket} binds the admin endpoint to the socket; a refused load "
        "leaves the previous one running, which binds it elsewhere",
    )
    moved = f"admin moved to {socket}, TCP refuses, R unmoved {before.describe()}"
    return Step(name="ii.rejected", detail=f"refused ({failure}); {moved}")


def rejected_cutover(plan: Rehearsal) -> tuple[Step, ...]:
    """(ii) A (c) Caddy rejects at load, then the way out of it README step 5 names.

    R moves only on a successful load, but the admin endpoint does not
    wait for one, so a file restore would leave Caddy on the socket: the
    way out is ``reconcile --abandon``, run exactly as the operator runs it.
    """
    before = _running(plan, plan.host.tcp_admin)
    if before is None:
        raise RehearsalFailedError("ii.rejected", f"nothing answers on {plan.host.tcp_admin}")
    failure = _refused_load(plan, replace(plan.host, caddyfile_source=plan.fixtures.rejected))
    refused = refused_on_socket(plan, before, failure)
    report = reconcile(plan.plane, "abandon", plan.host)
    return (refused, abandoned(plan, before, report))


def _attempt_names(plan: Rehearsal) -> tuple[tuple[Path, str], ...]:
    """Every name the refused attempt wrote or left behind, and how a refusal names it."""
    host, plane = plan.host, plan.plane
    return (
        (host.socket, "socket file"),
        (plane.fragment, "fragment"),
        (plane.next_fragment, "staged fragment"),
        (host.previous_caddyfile, "Caddyfile backup"),
        (host.previous_drop_in, "drop-in backup"),
        (host.absent_drop_in, "absent drop-in record"),
    )


def _back_on_tcp(plan: Rehearsal, before: ConfigPair, report: ReconcileReport) -> None:
    """What the abandon reported, and what TCP then runs: the configuration read before (c)."""
    tcp = plan.host.tcp_admin
    _require(
        report.action == "abandoned" and report.admin == tcp,
        "ii.rejected",
        f"reconcile --abandon reported {report.action} on {report.admin}, not abandoned on {tcp}",
    )
    running = _running(plan, tcp)
    _require(
        running == before,
        "ii.rejected",
        f"{tcp} runs {_described(running)} after the abandon, not the previous {before.describe()}",
    )


def abandoned(plan: Rehearsal, before: ConfigPair, report: ReconcileReport) -> Step:
    """After ``reconcile --abandon``: TCP runs the previous configuration, and nothing is left.

    The socket must neither answer nor leave a file at its name — the
    cutover's preflight refuses one, and Caddy v2.11.4 leaves one when its
    endpoint moves back to TCP (#303) — and nothing else the attempt wrote
    may survive. That the files are the pre-envelope ones is the
    preflight's to assert, and the cutover that follows runs it whole.
    """
    _back_on_tcp(plan, before, report)
    _require(
        _running(plan, plan.host.socket_admin) is None,
        "ii.rejected",
        f"{plan.host.socket_admin} still answers after the abandon",
    )
    for path, role in _attempt_names(plan):
        _require(not _present(path), "ii.rejected", f"the {role} {path} survived the abandon")
    left = "nothing answers on the socket, and no socket file, fragment or backup is left"
    return Step(
        name="ii.abandoned",
        detail=f"{plan.host.tcp_admin} runs the previous {before.describe()}; {left}",
    )


def _install(plan: Rehearsal, data: bytes) -> None:
    atomic_write_bytes(plan.plane.caddyfile, data, mode=WORLD_READABLE)


def _stock_state(plan: Rehearsal) -> None:
    """The previous Caddyfile on disk under the stock ``ExecReload=``, with the socket running.

    The drop-in goes back to the migration's *own* (a) text rather than to
    the pre-envelope one: that state — the runtime-directory lines without
    the ``ExecReload=`` pair — is exactly the migration between (a) and
    (e), and it takes away the one thing this fixture is about. Dropping
    ``RuntimeDirectory=`` as well would change three things at once and
    put the running socket's directory at systemd's mercy.
    """
    _install(plan, plan.host.previous_caddyfile.read_bytes())
    atomic_write_text(
        plan.host.drop_in, drop_in_text(plan.host, with_exec_reload=False), mode=WORLD_READABLE
    )
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


def _previous_back(plan: Rehearsal, kept: bytes) -> ConfigPair:
    """The backup's bytes back at the Caddyfile's own path, adapted from there.

    Caddy hides the path of the Caddyfile it loaded (#316), so the backup
    adapted at its own name is one path apart from anything the Caddyfile
    loads. The bytes are asked first: R equal to what the Caddyfile adapts
    to says nothing unless that file is the previous one.
    """
    _require(
        plan.plane.caddyfile.read_bytes() == kept,
        "iii",
        f"{plan.plane.caddyfile} is not the previous Caddyfile after the way back",
    )
    return adapt(plan.plane.runner, plan.plane.caddyfile, plan.plane.fragment)


def rolled_back(plan: Rehearsal, previous: ConfigPair) -> ConfigPair:
    """(iii) What must hold after the way back: the previous configuration, on TCP, no socket.

    ``previous`` is the pre-envelope Caddyfile adapted from the Caddyfile's
    own path, so the equality covers what the ADR spells out — no ``vars``
    handler and the symlink roots — rather than only the release id.
    """
    running = _running(plan, plan.host.tcp_admin)
    if running is None:
        raise RehearsalFailedError("iii", f"{plan.host.tcp_admin} does not answer afterwards")
    _require(running.release_id is None, "iii", f"it still names release {running.release_id}")
    _require(
        running == previous,
        "iii",
        f"{plan.host.tcp_admin} runs {running.describe()}, not the previous {previous.describe()}",
    )
    _require(
        _running(plan, plan.host.socket_admin) is None,
        "iii",
        f"{plan.host.socket_admin} still answers after the way back",
    )
    _require(not _present(plan.host.socket), "iii", f"{plan.host.socket} survived the rollback")
    return running


def rollback(plan: Rehearsal) -> tuple[Step, ...]:
    """(iii) The first-migration rollback, delivered to the socket, and its negative fixture."""
    plain = plain_reload_refused(plan)
    kept = plan.host.previous_caddyfile.read_bytes()
    report = rollback_first_migration(plan.plane, plan.host)
    rolled_back(plan, _previous_back(plan, kept))
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
    """(v) The negative fixture: a restart takes back a mode and a group set once by hand.

    The ADR pairs "without the creation-mode suffix" with "and the setgid
    directory". Only the suffix is taken away here: ``RuntimeDirectory=``
    and ``RuntimeDirectoryMode=2770`` recreate the setgid directory at
    every start, so taking that away too would mean editing the drop-in
    as well — three changes where the fixture is about one. The mode
    assertion is what fails either way, and ``_facts_refused`` insists
    the refusal names the mode or the group and nothing else.
    """
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
