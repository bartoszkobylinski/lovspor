"""A mutant is only active inside the pytest process mutmut runs (#763).

PR #754: the same assertion failed to kill `typer.prompt(None)` from a child
started with `sys.executable -c "from lovspor..."` (run 41345083093, 828/829)
and killed it in process (run 41350617481, 829/829). The cause is mutmut's own
path setup, pinned here so a mutmut upgrade that changes it turns red instead of
silently changing which remediation tests can kill:

- `setup_source_paths()` puts `mutants/src` first on `sys.path` and removes
  `src`, in the running interpreter only; it sets no `PYTHONPATH`;
- a child interpreter starts with its own `sys.path`, where the editable install
  points back at `src/`, so it imports the original module and the mutant never
  runs there, whatever `MUTANT_UNDER_TEST` says.

The rule this backs is in `.github/codex/mutation-remediation.md` (checked here)
and in the mutation-testing skill.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from importlib.machinery import PathFinder
from pathlib import Path

import pytest
from mutmut.utils.file_utils import setup_source_paths

_REPO = Path(__file__).parents[2]


@pytest.fixture
def mutated_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    (tmp_path / "src").mkdir()
    (tmp_path / "mutants" / "src").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [str(tmp_path / "src"), *sys.path])
    monkeypatch.delenv("PYTHONPATH", raising=False)
    yield tmp_path


def test_the_mutated_tree_is_put_on_this_processs_path_only(mutated_tree: Path) -> None:
    setup_source_paths()

    assert sys.path[0] == str((mutated_tree / "mutants" / "src").absolute())
    assert str(mutated_tree / "src") not in sys.path
    assert "PYTHONPATH" not in os.environ


def test_a_child_interpreter_does_not_see_the_mutated_tree(mutated_tree: Path) -> None:
    setup_source_paths()

    child = subprocess.run(
        [sys.executable, "-c", "import sys; print('\\n'.join(sys.path))"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "MUTANT_UNDER_TEST": "pkg.mod.x_f__mutmut_1"},
    )

    assert str((mutated_tree / "mutants" / "src").absolute()) not in child.stdout.splitlines()


def test_the_remediation_prompt_says_so() -> None:
    prompt = " ".join((_REPO / ".github" / "codex" / "mutation-remediation.md").read_text().split())

    assert "A killing test must call the mutated code in the pytest process" in prompt
    assert "`subprocess`" in prompt
    assert "`sys.executable`" in prompt
    assert "imports the original" in prompt


def test_child_imports_original_even_when_parent_resolves_shadow_package(
    mutated_tree: Path,
) -> None:
    """Check the import consequence claimed by the remediation prompt (#763)."""
    shadow_package = mutated_tree / "mutants" / "src" / "lovspor"
    shadow_package.mkdir()
    shadow_init = shadow_package / "__init__.py"
    shadow_init.write_text("", encoding="utf-8")
    setup_source_paths()

    # PathFinder bypasses sys.modules: other tests already imported lovspor.
    parent_spec = PathFinder.find_spec("lovspor", sys.path)
    assert parent_spec is not None
    assert parent_spec.origin == str(shadow_init)

    # The original is wherever the editable install points, read from a child
    # started the same way without a mutant. Not a path built from this file:
    # under mutmut this file itself runs from the `mutants/` copy (#796).
    original, child = (
        _child_lovspor_file({k: v for k, v in os.environ.items() if k != "MUTANT_UNDER_TEST"}),
        _child_lovspor_file({**os.environ, "MUTANT_UNDER_TEST": "lovspor.x_f__mutmut_1"}),
    )

    assert child == original
    assert child != shadow_init.resolve()
    assert child.parts[-3:] == ("src", "lovspor", "__init__.py")


def _child_lovspor_file(env: dict[str, str]) -> Path:
    child = subprocess.run(
        [sys.executable, "-c", "import lovspor; print(lovspor.__file__)"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        env=env,
    )
    return Path(child.stdout.strip()).resolve()
