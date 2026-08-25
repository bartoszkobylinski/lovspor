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
_LICENSE = _ROOT / "LICENSE"
_MCP_DOC = _ROOT / "docs" / "mcp.md"
_RELEASING_DOC = _ROOT / "docs" / "releasing.md"

_PENDING_MARKER = "first PyPI release is pending"
_RELEASED_MARKER = "Lovspor is distributed on PyPI"

# The version this working tree would publish. Single-sourced here because a
# bump has to move `pyproject.toml`, `uv.lock` and the installed metadata
# together — CI runs `uv sync --frozen`, so a forgotten `uv lock` fails the
# build rather than shipping a mismatched artifact.
_EXPECTED_VERSION = "0.6.0"

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
# the banner says "on PyPI", the install steps still say "wait".
#
# SCOPE, stated plainly: this is a denylist of phrasings, so it is a tripwire,
# never a proof. It cannot catch a pre-release framing nobody has written yet —
# add one when it appears. What carries the real weight for docs/mcp.md and
# docs/releasing.md is the positive marker requirement in `_release_state`: they
# must commit to a release state, and the two must agree. The README has no
# marker (see `_DISTRIBUTION_DOCS`), so its guard adds a phrasing-independent
# check on top of this list — see the README test at the bottom of this file.
_PRE_RELEASE_PROSE = (
    re.compile(
        r"once\s+(?:`?\d+\.\d+\.\d+`?|it|the\s+(?:first\s+)?"
        r"(?:release|package|version|publication))\s+is\s+published",
        re.IGNORECASE,
    ),
    re.compile(r"until\s+\S+\s+(?:lands|publishes|is\s+published)", re.IGNORECASE),
    re.compile(r"release\s+is\s+(?:still\s+)?pending", re.IGNORECASE),
    re.compile(r"not\s+(?:currently\s+)?on\s+PyPI", re.IGNORECASE),
    re.compile(r"not\s+yet\s+(?:published|released|on\s+PyPI)", re.IGNORECASE),
    re.compile(r"(?:will\s+be|becomes)\s+available\s+(?:on\s+PyPI|after|once|when)", re.IGNORECASE),
    re.compile(r"after\s+the\s+first\s+(?:publication|release|upload)", re.IGNORECASE),
    re.compile(r"awaiting\s+(?:the\s+)?(?:first\s+)?(?:release|publication)", re.IGNORECASE),
    re.compile(r"coming\s+(?:soon\s+)?to\s+PyPI", re.IGNORECASE),
    re.compile(r"page\s+is\s+currently\s+absent", re.IGNORECASE),
    re.compile(r"page\s+404s", re.IGNORECASE),
)

# Phrasing-independent backstop, applied to every active distribution doc: any
# single sentence that talks about distribution *and* puts it in the future or
# in the negative. This is what the denylist above cannot promise — it fails on
# wordings nobody has written yet, because it matches shape, not vocabulary.
_DISTRIBUTION_TOPIC = re.compile(r"\bPyPI\b|\bpublish(?:ed|ing)?\b|\brelease[sd]?\b", re.IGNORECASE)
_FUTURE_OR_ABSENT = re.compile(
    r"\b(?:will\s+be|soon|upcoming|awaiting|pending|planned|future|not\s+yet|yet\s+to\s+be|"
    r"not\s+available|once\s+\w+\s+is\s+published|"
    r"after\s+the\s+first\s+(?:release|publication|upload))\b",
    re.IGNORECASE,
)

# The two sentences in docs/releasing.md that legitimately pair both halves,
# because that document is *about* releasing: PyPI's own "pending publisher"
# registration state, and the step that swapped the quoted "pending" wording out
# of these docs. Narrow on purpose — a third one must be looked at, not waved
# through, so a real regression cannot hide behind a broad exemption.
_META_DISCUSSION_ALLOWANCES = (
    re.compile(r"pending\s+publisher", re.IGNORECASE),
    re.compile(r"[\"'`]pending[\"'`]\s+wording", re.IGNORECASE),
)


def _deferred_distribution_sentences(text: str) -> list[str]:
    """Sentences that put distribution in the future or in the negative."""
    return [
        " ".join(sentence.split())
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if _DISTRIBUTION_TOPIC.search(sentence)
        and _FUTURE_OR_ABSENT.search(sentence)
        and not any(allowed.search(sentence) for allowed in _META_DISCUSSION_ALLOWANCES)
    ]


def _release_state(label: str, text: str) -> str:
    """Classify one doc's PyPI release state, rejecting self-contradiction.

    Marker presence alone is too weak: a doc can carry the released marker in
    its banner and still tell the reader further down to wait for the release.
    So a released doc must also be free of pre-release prose — by known phrasing
    *and* by shape, so an unseen wording fails here too.
    """
    pending = _PENDING_MARKER in text
    released = _RELEASED_MARKER in text

    assert pending != released, (
        f"{label} must state exactly one release state, not both and not neither"
    )

    if released:
        stale: list[str] = [
            pattern.pattern for pattern in _PRE_RELEASE_PROSE if pattern.search(text)
        ]
        stale += _deferred_distribution_sentences(text)
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


def test_project_version_and_mcp_cap_are_pinned() -> None:
    project = _pyproject()["project"]
    deps = project["dependencies"]
    runtime_mcp = [dep for dep in deps if dep.startswith("mcp")]

    assert project["version"] == _EXPECTED_VERSION
    assert runtime_mcp == ["mcp>=1.28.1,<2"]


def test_package_license_metadata_and_readme_agree_on_agpl_v3() -> None:
    """A release must not advertise terms different from its bundled license."""
    project = _pyproject()["project"]
    readme = _README.read_text(encoding="utf-8")
    license_text = _LICENSE.read_text(encoding="utf-8")

    assert project["license"] == {"file": "LICENSE"}
    assert (
        project["classifiers"].count(
            "License :: OSI Approved :: GNU Affero General Public License v3"
        )
        == 1
    )
    assert not [classifier for classifier in project["classifiers"] if "MIT License" in classifier]
    assert license_text.startswith(
        "                    GNU AFFERO GENERAL PUBLIC LICENSE\n"
        "                       Version 3, 19 November 2007\n"
    )
    assert "GNU Affero General Public License v3.0 (AGPL-3.0)" in readme
    assert "corpus data" in readme and "remains NLOD 2.0" in readme


def test_engine_license_docs_do_not_regress_to_stale_mit_claims() -> None:
    """The 2026-08-12 relicense (MIT -> AGPL-3.0, decisions.md §18) must stay
    reflected in every doc that states the engine's current license.

    Found stale: docs/mcp.md and docs/roadmap.md still asserted the engine
    was MIT-licensed as current fact, weeks after pyproject.toml, LICENSE and
    README had already moved to AGPL-3.0 and decisions.md §18 recorded the
    change. Historical MIT mentions may stay per decisions.md's own
    "supersede, don't delete" convention and roadmap.md's "Superseded" notes
    — the regression this guards is the *current-state* claim drifting back
    to MIT, or the §18 record disappearing.
    """
    mcp_doc = _MCP_DOC.read_text(encoding="utf-8")
    roadmap = (_ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
    decisions = (_ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")

    # The exact stale claims this fix removed must never reappear verbatim.
    assert "The engine is MIT-licensed" not in mcp_doc
    assert "engine code (this repo) is MIT-licensed" not in mcp_doc
    assert "open infrastructure: engine public under MIT," not in roadmap

    # Each active surface must name the current license, not merely drop MIT.
    assert mcp_doc.count("AGPL-3.0") >= 2  # distribution banner + license section
    assert "AGPL-3.0" in roadmap

    # decisions.md keeps historical MIT sentences by convention, so what must
    # not regress is the relicense record and its supersession pointers.
    assert "## 18. Engine relicensed MIT -> AGPL-3.0" in decisions
    assert "**Superseded in part (2026-08-12):** the engine is no longer MIT" in decisions
    assert "**Licence superseded 2026-08-12 — the engine is AGPL-3.0" in decisions


def test_lockfile_matches_the_declared_mcp_cap_and_resolves_to_a_1x_release() -> None:
    lock = (_ROOT / "uv.lock").read_text(encoding="utf-8")

    assert '{ name = "mcp", specifier = ">=1.28.1,<2" }' in lock
    assert 'name = "mcp"\nversion = "1.28.1"' in lock


def test_lockfile_carries_the_same_version_as_pyproject() -> None:
    """A bump without `uv lock` builds fine locally and fails CI's `--frozen` sync."""
    lock = (_ROOT / "uv.lock").read_text(encoding="utf-8")

    assert f'name = "lovspor"\nversion = "{_EXPECTED_VERSION}"' in lock


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
    assert result.stdout.strip() == _EXPECTED_VERSION


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

    Three layers, weakest last: the pending marker must be absent outright; no
    known pre-release phrasing may appear; and no sentence may pair a
    distribution topic with a future or absence framing, which catches wordings
    the denylist has never seen.
    """
    readme = _README.read_text(encoding="utf-8")

    assert _PENDING_MARKER not in readme, "README claims the release is still pending"

    stale = [pattern.pattern for pattern in _PRE_RELEASE_PROSE if pattern.search(readme)]
    assert not stale, f"README carries pre-release prose: {stale}"

    assert not _deferred_distribution_sentences(readme), "README defers distribution to the future"


@pytest.mark.parametrize(
    "unseen",
    [
        "PyPI availability depends on a future release.",
        "The PyPI package will be available after the first publication.",
        "A release is coming soon; the package is not yet published.",
        "Installation from PyPI is pending our first upload.",
    ],
)
@pytest.mark.parametrize("label,path", (*_DISTRIBUTION_DOCS, ("README", _README)))
def test_distribution_docs_reject_pre_release_wordings_the_denylist_never_saw(
    unseen: str, label: str, path: Path
) -> None:
    """Guard the guard, second layer: an unseen phrasing must fail in every doc.

    The denylist cannot promise this — the sentence-level check is what makes
    the promise, so it is tested against wordings deliberately absent from
    `_PRE_RELEASE_PROSE`, against each real active distribution doc rather than
    a synthetic string.
    """
    doc = path.read_text(encoding="utf-8")

    assert not _deferred_distribution_sentences(doc), f"{label} is already flagged before mutation"
    assert _deferred_distribution_sentences(f"{doc}\n\n{unseen}\n"), (
        f"unseen pre-release wording slipped through {label}: {unseen!r}"
    )


@pytest.mark.parametrize("label,path", _DISTRIBUTION_DOCS)
def test_release_state_itself_rejects_unseen_pre_release_wordings(label: str, path: Path) -> None:
    """The shape check must fire through the invariant, not only in the helper.

    `_release_state` is what the agreement test calls, so a doc keeping the
    released marker while adding an unseen pre-release sentence must fail there.
    """
    doc = path.read_text(encoding="utf-8")

    with pytest.raises(AssertionError, match="pre-release prose"):
        _release_state(label, f"{doc}\n\nPyPI availability depends on a future release.\n")


def test_every_meta_discussion_allowance_still_matches_the_doc_it_was_written_for() -> None:
    """A dead allowance is a hole: it exempts nothing today and hides drift later."""
    releasing = _RELEASING_DOC.read_text(encoding="utf-8")

    for allowed in _META_DISCUSSION_ALLOWANCES:
        assert allowed.search(releasing), f"allowance no longer matches anything: {allowed.pattern}"


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
