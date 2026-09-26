"""No test spawns ``uv run`` or ``uv sync`` (issue #400).

Mutmut runs the suite with its cwd inside ``mutants/``, a shadow tree with its
own ``pyproject.toml`` and a ``.venv`` symlinked to the real one. A ``uv run``
spawned there syncs the shadow project into the real venv and re-points the
editable install at ``mutants/src``: every later plain pytest run in that
checkout then imports the shadow tree instead of ``src/``. A test that needs
the interpreter already runs inside the synced env, so ``sys.executable`` is
the right argv[0].

The guard is static and deliberately narrow: it flags a call whose argv is a
list or tuple literal beginning with ``"uv"`` and naming ``run`` or ``sync``.
Strings that merely mention ``uv run`` (workflow assertions, an MCP config
whose ``command`` is ``"uv"``, the fast-gate test's stub names) are not argv
and never match. An argv assembled at runtime is out of its reach.
"""

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1]
SYNCING_SUBCOMMANDS = frozenset({"run", "sync"})


def _argv_literals(call: ast.Call) -> list[ast.List | ast.Tuple]:
    candidates = [*call.args[:1], *(kw.value for kw in call.keywords if kw.arg == "args")]
    return [node for node in candidates if isinstance(node, ast.List | ast.Tuple)]


def _constants(node: ast.List | ast.Tuple) -> list[object]:
    return [elt.value if isinstance(elt, ast.Constant) else None for elt in node.elts]


def _spawns_uv(call: ast.Call) -> bool:
    for argv in _argv_literals(call):
        words = _constants(argv)
        if words[:1] == ["uv"] and SYNCING_SUBCOMMANDS & set(words[1:]):
            return True
    return False


def uv_spawns(source: str) -> list[int]:
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    return sorted(call.lineno for call in calls if _spawns_uv(call))


class TestDetector:
    def test_flags_a_uv_run_argv(self) -> None:
        source = 'subprocess.run(["uv", "run", "python", "x.py"], check=True)\n'

        assert uv_spawns(source) == [1]

    def test_flags_a_uv_sync_argv_passed_by_keyword(self) -> None:
        source = 'subprocess.Popen(args=("uv", "--quiet", "sync"))\n'

        assert uv_spawns(source) == [1]

    def test_ignores_an_mcp_config_whose_command_is_uv(self) -> None:
        source = 'write({"command": "uv", "args": ["run", "lovspor", "mcp"]})\n'

        assert uv_spawns(source) == []

    def test_ignores_a_harmless_uv_subcommand_and_prose(self) -> None:
        source = 'subprocess.run(["uv", "--version"])\nprint("uv run pytest")\n'

        assert uv_spawns(source) == []

    def test_ignores_the_interpreter_under_test(self) -> None:
        source = 'subprocess.run([sys.executable, "uv", "run"])\n'

        assert uv_spawns(source) == []


def test_no_file_under_tests_spawns_uv_run_or_sync() -> None:
    offenders = [
        f"{path.relative_to(TESTS_DIR)}:{line}"
        for path in sorted(TESTS_DIR.rglob("*.py"))
        for line in uv_spawns(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], "use sys.executable, not uv, in tests (issue #400)"
