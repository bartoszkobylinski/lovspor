"""Deployment contract for the ruling #31 Borealis control arm."""

import ast
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "llhb" / "runner" / "borealis_modal.py"
)


def _tree() -> ast.Module:
    return ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))


def test_borealis_server_uses_the_frozen_model_and_unquantized_bfloat16_contract() -> None:
    """The published control result must remain reproducible with its ruled serving setup."""
    tree = _tree()
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"MODEL", "SERVED_NAME", "PORT"}
    }
    popen = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
    )
    argv = popen.args[0]
    assert isinstance(argv, ast.List)
    constants = [element.value for element in argv.elts if isinstance(element, ast.Constant)]

    assert assignments == {
        "MODEL": "NbAiLab/borealis-27b",
        "SERVED_NAME": "borealis-27b",
        "PORT": 8000,
    }
    assert "--dtype" in constants
    assert constants[constants.index("--dtype") + 1] == "bfloat16"
    assert "--max-model-len" in constants
    assert constants[constants.index("--max-model-len") + 1] == "8192"
    assert not any("quant" in value.lower() for value in constants if isinstance(value, str))


def test_borealis_server_passes_api_key_without_embedding_a_credential() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = _tree()
    subscripts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "os"
        and node.value.attr == "environ"
    ]

    assert any(
        isinstance(node.slice, ast.Constant) and node.slice.value == "VLLM_API_KEY"
        for node in subscripts
    )
    assert "--api-key" in source
