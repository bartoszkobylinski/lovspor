"""The admin socket's four facts (ADR-0014 Decision 6; the drift timer's first action).

The socket is a real file under ``tmp_path`` — the check stats it — and
the instance behind it is ``FakeCaddy``, listening where the test says.
The release group's gid is the file's own, so the group fact is about
the code and not about the machine the tests run on.
"""

from pathlib import Path

import pytest

from lovspor.release.admin_socket import (
    CONFIG_URL,
    DEFAULT_UNPRIVILEGED_USER,
    AdminSocket,
    check_admin_socket,
    unprivileged_argv,
)
from lovspor.release.caddy import DEFAULT_ADMIN
from lovspor.release.errors import AdminSocketError, ControlPlaneError
from lovspor.release.migrate import DEFAULT_RELEASE_GROUP, DEFAULT_TCP_ADMIN, SOCKET_MODE
from tests.unit.caddy_fakes import socketed_caddy

TCP = "localhost:2019"


class Socketed:
    """A Caddy listening on a socket file this fixture made, and the access that asks."""

    def __init__(self, tmp_path: Path) -> None:
        self.caddy, self.file, self.address, self.ownership = socketed_caddy(tmp_path)

    def answering_everywhere(self, address: str) -> object:
        """An admin client that answers on every address: a stray TCP listener beside the socket."""
        del address
        return self.caddy

    def access(self, **overrides: object) -> AdminSocket:
        fields: dict[str, object] = {
            "socket_admin": self.address,
            "tcp_admin": TCP,
            "release_group": "lovspor-release",
            "runner": self.caddy,
            "admin_client": self.caddy.admin_client,
            "ownership": self.ownership,
        }
        fields.update(overrides)
        return AdminSocket(**fields)  # type: ignore[arg-type]


@pytest.fixture
def socketed(tmp_path: Path) -> Socketed:
    return Socketed(tmp_path)


class TestAdminSocket:
    def test_the_defaults_are_the_droplets(self) -> None:
        access = AdminSocket()

        assert access.socket_admin == DEFAULT_ADMIN
        assert access.tcp_admin == DEFAULT_TCP_ADMIN
        assert access.release_group == DEFAULT_RELEASE_GROUP
        assert access.unprivileged_user == DEFAULT_UNPRIVILEGED_USER
        assert access.socket == Path("/run/caddy/admin.sock")

    def test_a_socket_admin_that_is_not_a_unix_address_is_refused(self) -> None:
        with pytest.raises(ControlPlaneError, match="not a Unix-socket admin address"):
            AdminSocket(socket_admin=TCP)


class TestTheFourFacts:
    def test_a_socket_that_holds_all_four_is_reported(self, socketed: Socketed) -> None:
        facts = check_admin_socket(socketed.access())

        assert facts.socket == socketed.file
        assert facts.mode == SOCKET_MODE
        assert facts.gid == socketed.file.stat().st_gid
        assert facts.release_group == "lovspor-release"
        assert facts.unprivileged_user == DEFAULT_UNPRIVILEGED_USER
        assert facts.tcp_admin == TCP

    def test_the_report_names_the_socket_the_mode_and_both_identities(
        self, socketed: Socketed
    ) -> None:
        described = check_admin_socket(socketed.access()).describe()

        assert str(socketed.file) in described
        assert "0660" in described
        assert "lovspor-release" in described
        assert DEFAULT_UNPRIVILEGED_USER in described
        assert TCP in described

    def test_a_socket_that_is_not_there_is_a_named_refusal(self, socketed: Socketed) -> None:
        socketed.file.unlink()

        with pytest.raises(AdminSocketError, match="admin socket") as raised:
            check_admin_socket(socketed.access())

        assert str(socketed.file) in str(raised.value)

    def test_the_wrong_mode_is_a_named_refusal(self, socketed: Socketed) -> None:
        socketed.file.chmod(0o666)

        with pytest.raises(AdminSocketError, match="mode 0666, not 0660"):
            check_admin_socket(socketed.access())

    def test_the_wrong_group_is_a_named_refusal(self, socketed: Socketed) -> None:
        found = socketed.file.stat().st_gid
        socketed.ownership.groups["lovspor-release"] = found + 1

        with pytest.raises(AdminSocketError) as raised:
            check_admin_socket(socketed.access())

        assert str(raised.value) == (
            f"admin socket precondition unmet: {socketed.file} has gid {found}, not "
            f"lovspor-release's {found + 1}; the runtime directory needs the setgid bit "
            "and the group"
        )

    def test_the_wrong_mode_names_the_creation_mode_suffix(self, socketed: Socketed) -> None:
        socketed.file.chmod(0o600)

        with pytest.raises(AdminSocketError) as raised:
            check_admin_socket(socketed.access())

        assert str(raised.value) == (
            f"admin socket precondition unmet: {socketed.file} has mode 0600, not 0660; "
            "the admin address needs the |0660 creation-mode suffix"
        )

    def test_a_group_that_does_not_exist_is_a_named_refusal(self, socketed: Socketed) -> None:
        with pytest.raises(AdminSocketError, match="group nosuchgroup does not exist"):
            check_admin_socket(socketed.access(release_group="nosuchgroup"))

    def test_a_release_identity_that_cannot_read_config_is_a_named_refusal(
        self, socketed: Socketed
    ) -> None:
        socketed.caddy.admin_up = False

        with pytest.raises(AdminSocketError, match="the release identity cannot"):
            check_admin_socket(socketed.access())

    def test_an_unprivileged_user_that_can_read_config_is_a_named_refusal(
        self, socketed: Socketed
    ) -> None:
        socketed.caddy.socket_users.add(DEFAULT_UNPRIVILEGED_USER)

        with pytest.raises(AdminSocketError) as raised:
            check_admin_socket(socketed.access())

        assert str(raised.value) == (
            f"admin socket precondition unmet: {DEFAULT_UNPRIVILEGED_USER} can GET /config/ "
            f"over {socketed.file}; that user must not be in lovspor-release, and the socket "
            "must not be world-reachable"
        )

    def test_a_stray_tcp_listener_beside_the_socket_is_a_named_refusal(
        self, socketed: Socketed
    ) -> None:
        """The three other facts hold; only the address the migration closed is back."""
        with pytest.raises(AdminSocketError) as raised:
            check_admin_socket(socketed.access(admin_client=socketed.answering_everywhere))

        assert str(raised.value) == (
            f"admin socket precondition unmet: {TCP} answers; on that address every process "
            "on the box can rewrite the running configuration"
        )


class TestTheUnprivilegedCall:
    def test_is_the_same_call_through_sudo_with_no_shell(self, socketed: Socketed) -> None:
        """Fixed argv, every flag of it: a call that silently succeeded would pass fact four."""
        argv = unprivileged_argv(socketed.access())

        assert argv == (
            "sudo",
            "-u",
            DEFAULT_UNPRIVILEGED_USER,
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--unix-socket",
            str(socketed.file),
            CONFIG_URL,
        )
        assert CONFIG_URL.startswith("http://")

    def test_names_the_user_the_access_names(self, socketed: Socketed) -> None:
        argv = unprivileged_argv(socketed.access(unprivileged_user="someone"))

        assert argv[2] == "someone"

    def test_is_made_for_every_check_that_gets_that_far(self, socketed: Socketed) -> None:
        check_admin_socket(socketed.access())

        assert unprivileged_argv(socketed.access()) in [argv for argv, _ in socketed.caddy.calls]

    def test_is_not_made_when_an_earlier_fact_already_failed(self, socketed: Socketed) -> None:
        socketed.file.chmod(0o600)

        with pytest.raises(AdminSocketError):
            check_admin_socket(socketed.access())

        assert socketed.caddy.calls == []

    def test_a_call_that_cannot_be_run_at_all_is_a_named_refusal(self, socketed: Socketed) -> None:
        class Unrunnable:
            def run(self, argv: object, env: object) -> object:
                raise ControlPlaneError("cannot run sudo: [Errno 2]")

        with pytest.raises(AdminSocketError, match="cannot run"):
            check_admin_socket(socketed.access(runner=Unrunnable()))
