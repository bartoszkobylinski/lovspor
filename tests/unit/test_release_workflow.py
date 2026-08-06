"""Security and packaging invariants of the PyPI release path.

The release workflow is the only path that can publish immutable bytes under
the `lovspor` name. Its trigger, token scope and artifact hand-off are security
properties, not style. The surrounding docs and packaging metadata are pinned
here too, so a regression shows up as a failing test instead of a bad release.
"""

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _ROOT / ".github" / "workflows" / "release.yml"
_README = _ROOT / "README.md"
_MCP_DOC = _ROOT / "docs" / "mcp.md"
_RELEASING_DOC = _ROOT / "docs" / "releasing.md"

_PENDING_MARKER = "first PyPI release is pending"
_RELEASED_MARKER = "Lovspor is distributed on PyPI"

# The README is deliberately NOT here. Since the 2026-08-06 user-first rewrite it
# is a landing page, not a release doc: it shows the `uvx` install and delegates
# every distribution fact (version, burned `0.2.0`-`0.3.0`, release process) to
# docs/releasing.md. It cannot carry a release-state marker without carrying that
# trivia back. What still binds it is the contradiction guard below — a README
# telling the reader to wait for a release the other docs call published is the
# failure this invariant exists to catch, and that check survives the move.
_DISTRIBUTION_DOCS = (
    ("docs/mcp.md", _MCP_DOC),
    ("docs/releasing.md", _RELEASING_DOC),
)

# Prose that only makes sense before the first release. A doc claiming the
# released state while still carrying any of it is a half-finished transition:
# the banner says "on PyPI", the install steps still say "wait". Deliberately a
# denylist of the framings these docs have actually used — it narrows the gap
# rather than closing it, so add to it when a new pre-release phrasing appears.
_PRE_RELEASE_PROSE = (
    re.compile(r"once\s+`?\d+\.\d+\.\d+`?\s+is\s+published", re.IGNORECASE),
    re.compile(r"until\s+\S+\s+(?:lands|publishes|is\s+published)", re.IGNORECASE),
    re.compile(r"release\s+is\s+(?:still\s+)?pending", re.IGNORECASE),
    re.compile(r"not\s+(?:currently\s+)?on\s+PyPI", re.IGNORECASE),
    re.compile(r"page\s+is\s+currently\s+absent", re.IGNORECASE),
    re.compile(r"page\s+404s", re.IGNORECASE),
)


def _release_state(label: str, text: str) -> str:
    """Classify one doc's PyPI release state, rejecting self-contradiction.

    Marker presence alone is too weak: a doc can carry the released marker in
    its banner and still tell the reader further down to wait for the release.
    So a released doc must also be free of pre-release prose.
    """
    pending = _PENDING_MARKER in text
    released = _RELEASED_MARKER in text

    assert pending != released, (
        f"{label} must state exactly one release state, not both and not neither"
    )

    if released:
        stale = [pattern.pattern for pattern in _PRE_RELEASE_PROSE if pattern.search(text)]
        assert not stale, (
            f"{label} says lovspor is on PyPI but still carries pre-release prose: {stale}"
        )

    return "pending" if pending else "released"


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _workflow_steps(job: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _workflow()["jobs"][job]["steps"]
    return steps


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_release_workflow_triggers_only_on_published_releases() -> None:
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))  # PyYAML resolves bare `on:` to True.

    assert triggers == {"release": {"types": ["published"]}}, triggers


def test_only_the_publish_job_gets_oidc_token_minting_power() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["pypi-publish"]["permissions"] == {"id-token": "write"}
    assert "permissions" not in jobs["build"]


def test_publish_job_runs_no_repository_code() -> None:
    """No checkout, no shell, no local Python: only artifact download + upload."""
    steps = _workflow_steps("pypi-publish")

    assert [step["name"] for step in steps] == [
        "Download distributions",
        "Publish to PyPI (trusted publishing)",
    ]
    assert all("run" not in step for step in steps), steps
    assert not any("checkout" in str(step.get("uses", "")) for step in steps), steps


def test_build_job_fails_closed_on_tag_version_mismatch_before_publish_steps() -> None:
    steps = _workflow_steps("build")
    names = [str(step.get("name", "")) for step in steps]
    assert_at = names.index("Assert built version matches the release tag")
    check_at = names.index("Check distribution metadata")
    upload_at = names.index("Upload distributions")

    assert assert_at < check_at < upload_at, names
    assert "dist/lovspor-${tag}.tar.gz" in str(steps[assert_at]["run"])
    assert "exit 1" in str(steps[assert_at]["run"])


def test_release_workflow_fails_if_the_expected_dist_artifact_is_missing() -> None:
    upload = next(
        step for step in _workflow_steps("build") if step.get("name") == "Upload distributions"
    )

    assert upload["with"]["name"] == "dist"
    assert upload["with"]["path"] == "dist/"
    assert upload["with"]["if-no-files-found"] == "error"


def test_project_version_and_mcp_cap_are_pinned_for_the_first_re_release() -> None:
    project = _pyproject()["project"]
    deps = project["dependencies"]
    runtime_mcp = [dep for dep in deps if dep.startswith("mcp")]

    assert project["version"] == "0.5.0"
    assert runtime_mcp == ["mcp>=1.28.1,<2"]


def test_lockfile_matches_the_declared_mcp_cap_and_resolves_to_a_1x_release() -> None:
    lock = (_ROOT / "uv.lock").read_text(encoding="utf-8")

    assert '{ name = "mcp", specifier = ">=1.28.1,<2" }' in lock
    assert 'name = "mcp"\nversion = "1.28.1"' in lock


def test_the_mcp_cap_is_load_bearing_because_the_server_imports_fastmcp() -> None:
    """If this import goes away, the `<2` ceiling deserves re-review."""
    mcp_source = (_ROOT / "src" / "lovspor" / "mcp.py").read_text(encoding="utf-8")

    assert "from mcp.server.fastmcp import FastMCP" in mcp_source


def test_wheel_ships_only_the_lovspor_package() -> None:
    wheel = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert wheel["packages"] == ["src/lovspor"], wheel


def test_console_entry_point_targets_the_lovspor_cli() -> None:
    scripts = _pyproject()["project"]["scripts"]

    assert scripts == {"lovspor": "lovspor.cli:app"}


def test_importing_lovspor_and_its_cli_surface_needs_no_openai_key() -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OPENAI_API_KEY", "OPENAI_APIKEY"}
        and not key.startswith(("LOVSPOR_", "LOVVERK_"))
    }

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import lovspor, lovspor.cli, lovspor.mcp; print(lovspor.__version__)",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.5.0"


def test_release_docs_agree_on_version_workflow_environment_and_burned_versions() -> None:
    texts = {
        "docs/mcp.md": _MCP_DOC.read_text(encoding="utf-8"),
        "docs/releasing.md": _RELEASING_DOC.read_text(encoding="utf-8"),
    }

    for label, text in texts.items():
        assert "0.4.0" in text, label

    assert "release.yml" in texts["docs/releasing.md"]
    assert "`pypi`" in texts["docs/releasing.md"]
    for label, text in texts.items():
        assert "0.2.0" in text, label
        assert "0.3.0" in text, label
        assert "burned" in text or "never be reused" in text or "withdrawn" in text, label


def test_distribution_docs_agree_on_whether_lovspor_is_on_pypi_yet() -> None:
    """No reader may be told both "install from PyPI" and "the project page is absent".

    The contradiction is resolved by state, not by silence. Each doc must commit
    to exactly one of the two release states, and all three must commit to the
    same one — so this keeps biting after a transition flips them rather than
    passing vacuously once a marker is deleted.
    """
    states = {
        label: _release_state(label, doc.read_text(encoding="utf-8"))
        for label, doc in _DISTRIBUTION_DOCS
    }

    assert len(set(states.values())) == 1, f"docs disagree about the release state: {states}"


@pytest.mark.parametrize(
    "stale",
    [
        "From PyPI — works once `0.4.0` is published:",
        "From PyPI (the first release is still pending):",
        "lovspor is not currently on PyPI.",
    ],
)
def test_release_state_invariant_rejects_stale_publish_caveats(stale: str) -> None:
    """Guard the guard: marker presence must not be enough to pass as released.

    A half-finished transition leaves the released marker in the banner while
    prose further down still tells the reader to wait for the release. Feeding
    that shape through the invariant must raise, not classify it as released.
    Built from a real released doc, so the fixture cannot drift into a shape
    the invariant never sees in this repo.
    """
    doc = _MCP_DOC.read_text(encoding="utf-8")
    assert _release_state("docs/mcp.md", doc) == "released", "fixture doc is not in released state"

    with pytest.raises(AssertionError, match="pre-release prose"):
        _release_state("docs/mcp.md", f"{doc}\n\n{stale}\n")


def test_readme_never_tells_the_reader_to_wait_for_a_published_release() -> None:
    """The README states no release state, so pin the half-finished shape directly.

    It shows `uvx lovspor`, which only works once the package is on PyPI. If a
    future transition leaves pre-release prose there while docs/releasing.md
    says published, a reader gets both answers from the same project — the exact
    contradiction `_release_state` rejects for the docs that do carry markers.
    """
    readme = _README.read_text(encoding="utf-8")

    stale = [pattern.pattern for pattern in _PRE_RELEASE_PROSE if pattern.search(readme)]
    assert not stale, f"README carries pre-release prose: {stale}"


def test_post_stage1_closure_state_holds_on_active_surfaces() -> None:
    """The Stage 1 migration narrative is closed; active docs must stay closed.

    The 2026-08-04 documentation-state audit found active surfaces still
    telling the pre-migration story — including two runtime error strings
    pointing operators at "the embedding-space migration", an operation that
    was never implemented in the engine and whose question was closed by
    regenerating the corpus. The release-state pin above did not cover the
    files that drifted, which is exactly where they drifted.

    Denylist entries are the exact stale framings these surfaces actually
    used — narrow on purpose, targeting ACTIVE documents only. Historical
    evidence (docs/evidence/*, the notebook) may and must keep historically
    correct language; nothing here scans it.
    """
    embeddings_doc = (_ROOT / "docs" / "embeddings.md").read_text(encoding="utf-8")
    mcp_doc = _MCP_DOC.read_text(encoding="utf-8")
    mcp_source = (_ROOT / "src" / "lovspor" / "mcp.py").read_text(encoding="utf-8")
    roadmap = (_ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
    publication_plan = (_ROOT / "docs" / "publication-plan.md").read_text(encoding="utf-8")

    # The dead operator instruction must never return to a runtime remedy.
    assert "embedding-space migration" not in mcp_source

    # embeddings.md is the public current-state ESI document: it must state
    # the closure and must not present the settled decision as pending.
    assert "regenerated" in embeddings_doc and "2026-08-04" in embeddings_doc
    for stale in (
        "until a separate migration annotates them",
        "evidence-gated migration",
        "reserved for a separate migration",
    ):
        assert stale not in embeddings_doc, f"reopened migration narrative: {stale!r}"
        assert stale not in mcp_doc, f"reopened migration narrative in mcp.md: {stale!r}"

    # Distribution reality: resumed on PyPI. The roadmap's gap list is an
    # active surface; the plan document is historical but must carry its
    # dated completion marker so its PRIVATE-era prose reads as history.
    assert "RESUMED 2026-08-03" in roadmap
    assert "no downloadable releases" not in roadmap
    assert "(Completed 2026-08-03" in publication_plan
