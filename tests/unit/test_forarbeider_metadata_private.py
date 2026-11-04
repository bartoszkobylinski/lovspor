"""``forarbeider-metadata`` writes owner-only, whatever the umask (#783).

The chapter observation beside it has always been 0600. The metadata record
was written with the process umask, so under the usual 022 anyone on the
machine could read it. A record already at the path that others could reach is
refused, never quietly replaced: it may already have been read, and the
operator has to know.
"""

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from tests.unit.cli_output import said
from tests.unit.test_forarbeider_cli import FIXTURE, _argv


@pytest.fixture(params=[0o022, 0o000], ids=["umask-022", "umask-000"])
def umask(request: pytest.FixtureRequest) -> Iterator[int]:
    previous = os.umask(request.param)
    try:
        yield request.param
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


@pytest.mark.usefixtures("umask")
def test_a_new_record_is_owner_only(tmp_path: Path) -> None:
    output = tmp_path / "staging" / "record.json"

    result = CliRunner().invoke(app, _argv(FIXTURE, output))

    assert result.exit_code == 0, result.output
    assert _mode(output) == 0o600
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.usefixtures("umask")
def test_replacing_a_private_record_keeps_it_owner_only(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    assert CliRunner().invoke(app, _argv(FIXTURE, output)).exit_code == 0
    first = output.read_bytes()

    result = CliRunner().invoke(app, _argv(FIXTURE, output, when="2100-04-02T12:00:00Z"))

    assert result.exit_code == 0, result.output
    assert output.read_bytes() != first
    assert _mode(output) == 0o600
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("shared", [0o644, 0o640, 0o604, 0o620, 0o602])
def test_a_record_others_could_reach_is_refused_and_left_as_it_was(
    tmp_path: Path, shared: int
) -> None:
    output = tmp_path / "record.json"
    previous = b'{"existing": "exposed"}\n'
    output.write_bytes(previous)
    output.chmod(shared)

    result = CliRunner().invoke(app, _argv(FIXTURE, output))

    assert result.exit_code == 1
    assert said(f"refused: observation_unsafe: {output}", result.output)
    assert output.read_bytes() == previous
    assert _mode(output) == shared
    assert list(tmp_path.iterdir()) == [output]


def test_a_record_with_a_second_name_is_refused(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    output.write_bytes(b"{}\n")
    output.chmod(0o600)
    other = tmp_path / "elsewhere.json"
    os.link(output, other)

    result = CliRunner().invoke(app, _argv(FIXTURE, output))

    assert result.exit_code == 1
    assert said("refused: observation_unsafe:", result.output)
    assert output.read_bytes() == b"{}\n"
    assert other.stat().st_nlink == 2


def test_a_fifo_at_the_path_is_refused_without_hanging(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    os.mkfifo(output, 0o600)

    result = CliRunner().invoke(app, _argv(FIXTURE, output))

    assert result.exit_code == 1
    assert said("refused: observation_unsafe:", result.output)
    assert stat.S_ISFIFO(output.lstat().st_mode)


def test_a_link_to_a_shared_record_is_refused_and_its_target_kept(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(b"{}\n")
    target.chmod(0o644)
    link = tmp_path / "record.json"
    link.symlink_to(target)

    result = CliRunner().invoke(app, _argv(FIXTURE, link))

    assert result.exit_code == 1
    assert said("refused: observation_unsafe:", result.output)
    assert link.is_symlink()
    assert target.read_bytes() == b"{}\n"
    assert _mode(target) == 0o644


@pytest.mark.usefixtures("umask")
def test_an_output_inside_a_checkout_is_still_refused_first(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    output = tmp_path / "record.json"

    result = CliRunner().invoke(app, _argv(FIXTURE, output))

    assert result.exit_code == 1
    assert said("refused: unsafe_output_path:", result.output)
    assert not output.exists()
