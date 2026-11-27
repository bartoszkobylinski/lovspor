"""ADD-ONLY contract gates: legal history may be added, never at the existing product's cost.

The owner's product goal is the existing Lovspor plus provision-level legal
history and source-backed legislative provenance, as an addition. The owner
accepted the functional-preservation contract these gates implement on
2026-11-20; it is kept privately. Its acceptance is the contract, not a
permission to deploy: it authorises no implementation, and merging this module,
like every change to its goldens, is a separate owner decision. Each gate below
pins one part of the production surface. A failure means a change to that
surface; the owner decides it, in a pull request that names each golden it
changes, the class of that change and its evidence. A golden is never refreshed
just to make a pull request pass, and no automation regenerates one.

Gates, with what each pins and what it does not:

* G1 URLs: every current document's canonical URL at lovverk ``c310127e``
  (5 905 documents) is still derived from its manifest record by the
  publisher's own route table, slug grammar and ``document_url``. The real
  301/410 map is pinned by digest and re-checked against a lovverk clone by
  ``scripts/quality/capture_add_only_baseline.py --check``. ``c310127e`` is a
  historical corpus state, frozen on purpose: G1 does not claim that the live
  corpus still has 5 905 documents, and the pin is not moved to a newer corpus
  head because lovverk moved on. URLs published after it are not covered here.
* G2 MCP surface: the 18 tool names and each tool's parameter names. Adding
  a tool or a parameter fails here too, so an addition is reviewed as one.
  Names only: parameter types, required fields, defaults and descriptions are
  pinned by the schema hash of ``benchmarks/llhb/runner/tool-surface-v9.json``
  (``test_llhb_mcp_surface.py``, compared on the apparatus interpreter only)
  and by the wire output of existing calls (``test_mcp_existing_calls.py``),
  not here.
* G3 MCP temporal answers: the wire output of the version and
  ``recorded_at`` calls on a deterministic two-state git corpus. A sample on a
  fixture, not a replay of every document's real history. The 21 existing
  calls are pinned by ``test_mcp_existing_calls.py``.
* G4 research boundary: no production module imports ``lovspor.legaltime``,
  ``lovspor.crosswalk`` or ``lovspor.forarbeider``, statically or at runtime;
  the one importer outside them is ``ops_cli.py``, which registers operator
  commands.
* G5 legal history is off: no setting, no MCP tool, no sync step reaches it.
  G4 and G5 catch research wired into serving or sync by accident. They are
  not a standing ban: an owner-approved additive tool, setting or sync step
  lands by a pull request that changes these gates or their goldens, with the
  owner's decision recorded for it.
* G6 deployment boundary: every workflow that names the lovverk write key,
  the seal key, a protected environment or the corpus remote, with its
  triggers, permissions and, per job, guard, environment, checkouts, secrets,
  variables, pushes and scripts; and the seal script's push (#898, #956,
  #965). It pins workflow text. Who may deploy to an environment is a
  repository setting it cannot read.
* G7 destructive migration: no workflow applies an identity plan, rewrites
  history or force-pushes, and only the identity apply path writes
  ``identity.json``. A pattern tripwire, not a proof that no workflow can
  destroy history.
"""

import ast
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from lovspor.mcp import build_server
from lovspor.publish import inventory
from lovspor.publish.inventory import DocumentPlan
from lovspor.publish.pages import document_url
from lovspor.settings import Settings
from tests.unit.local_dataset_fixtures import build_central, wire

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "lovspor"
GOLDEN = ROOT / "tests" / "fixtures" / "add_only"
WORKFLOWS = ROOT / ".github" / "workflows"
RESEARCH = ("legaltime", "crosswalk", "forarbeider")
#: The only module outside the research packages that may import them: it registers
#: their ``lovspor ops`` commands, operator-only, and calls none of them.
RESEARCH_IMPORTERS = {"ops_cli.py"}
PRODUCTION_MODULES = (
    "lovspor.mcp",
    "lovspor.mcp_local",
    "lovspor.mcp_contract",
    "lovspor.sync.orchestrator",
    "lovspor.publish.emit",
    "lovspor.publish.redirects",
    "lovspor.site",
    "lovspor.rendering",
    "lovspor.storage.manifest",
    "lovspor.embeddings",
    "lovspor.corpus_audit",
)


def _golden(name: str) -> Any:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


# --- G1 URLs ---


@pytest.fixture(scope="module")
def urls() -> dict[str, Any]:
    return _golden("production-urls-c310127e.json")


def test_g1_every_production_url_is_still_derived_from_its_manifest_record(
    urls: dict[str, Any],
) -> None:
    changed = []
    for doc_id, row in urls["current"].items():
        route = inventory._ROUTES[row["doc_type"]]
        assert inventory._CANONICAL_SLUG.fullmatch(row["slug"]), (doc_id, row["slug"])
        plan = DocumentPlan.model_construct(route=route, slug=row["slug"])
        if document_url(plan) != row["url"]:
            changed.append((doc_id, row["url"], document_url(plan)))
    assert changed == [], f"{len(changed)} public URL(s) would change, first {changed[:3]}"


def test_g1_the_baseline_is_the_whole_production_corpus(urls: dict[str, Any]) -> None:
    """lovverk ``c310127e``: 5 905 current and 121 removed records, 830 301s, 5 332 410s."""
    assert urls["lovverk_commit"] == "c310127ecc65192f8260eab56bd3809ce0354130"
    assert (len(urls["current"]), len(urls["removed"])) == (5905, 121)
    assert (urls["redirects"]["count_301"], urls["redirects"]["count_410"]) == (830, 5332)
    seen = {(row["doc_type"], row["slug"]) for row in urls["current"].values()}
    assert len(seen) == len(urls["current"])  # one URL per document


# --- G2 MCP surface ---


def _tools(root: Path) -> dict[str, list[str]]:
    listed = asyncio.run(build_server(root).list_tools())
    return {t.name: sorted((t.inputSchema.get("properties") or {}).keys()) for t in listed}


def test_g2_the_mcp_tools_and_their_parameters_are_unchanged(tmp_path: Path) -> None:
    build_central(tmp_path)
    current, pinned = _tools(tmp_path), _golden("mcp-tools.json")
    assert sorted(set(pinned) - set(current)) == [], "an existing MCP tool was removed or renamed"
    assert sorted(set(current) - set(pinned)) == [], "a new MCP tool: review it as an addition"
    assert current == pinned, "an existing MCP tool's parameters changed"


# --- G3 MCP temporal defaults ---

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "HOME": "/nonexistent",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


def _git(repo: Path, *args: str, when: str = "2026-05-01T12:00:00+00:00") -> None:
    env = {**_GIT_ENV, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _law(title: str, section: str, text: str) -> str:
    return f"---\ntitle: {title}\n---\n\n# {title}\n\n## Kapittel 9.\n\n### {section}\n\n{text}\n"


def _record(slug: str, xml_hash: str) -> dict[str, object]:
    return {
        "doc_type": "lov",
        "xml_hash": xml_hash,
        "markdown_path": f"lover/{slug}.md",
        "source_dataset": "gjeldende-lover",
        "last_seen": "2026-05-01T04:00:00Z",
        "status": "current",
        "slug": slug,
        "title": slug.capitalize(),
    }


def _state(repo: Path, version: int, when: str) -> None:
    section = "§ 9-9. Regel" if version == 1 else "§ 9-8. Regel"
    (repo / "lover" / "testloven.md").write_text(
        _law("Testloven", section, f"Testloven v{version} tekst."), encoding="utf-8"
    )
    manifest = {"testloven-id": _record("testloven", f"hash-{version}")}
    payload = {"version": 1, "generated_at": "2026-05-01T04:00:00Z", "documents": manifest}
    (repo / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    _git(repo, "add", "-A", when=when)
    _git(repo, "commit", "-q", "-m", f"sync {version}", when=when)


def temporal_corpus(root: Path) -> Path:
    """Two deterministic states: 2026-05-01 (§ 9-9) and 2026-05-10 (§ 9-8)."""
    repo = root / "corpus"
    (repo / "lover").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "commit.gpgsign", "false")
    _state(repo, 1, "2026-05-01T12:00:00+00:00")
    _state(repo, 2, "2026-05-10T12:00:00+00:00")
    return repo


TEMPORAL_CALLS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("get_law_history", {"slug": "testloven"}),
    ("list_law_versions", {"slug": "testloven"}),
    ("get_law_at", {"slug": "testloven", "target_date": "2026-05-05"}),
    ("get_law_at", {"slug": "testloven", "target_date": "2026-05-12"}),
    ("diff_law_versions", {"slug": "testloven", "date_a": "2026-05-05", "date_b": "2026-05-12"}),
    ("get_section", {"slug": "testloven", "section_id": "9-9", "recorded_at": "2026-05-05"}),
    ("get_section", {"slug": "testloven", "section_id": "9-9"}),
    ("search_body", {"query": "tekst", "recorded_at": "2026-05-05"}),
    ("validate_citation", {"citation": "testloven § 9-9", "recorded_at": "2026-05-05"}),
    ("validate_citation", {"citation": "testloven § 9-9"}),
)


def temporal_outputs(root: Path) -> list[dict[str, Any]]:
    repo = temporal_corpus(root)
    server = build_server(repo)
    found = []
    for name, arguments in TEMPORAL_CALLS:
        text = json.dumps(wire(server, name, arguments), sort_keys=True, ensure_ascii=False)
        output = json.loads(
            text.replace(str(repo.resolve()), "<corpus>").replace(str(repo), "<corpus>")
        )
        found.append({"tool": name, "arguments": arguments, "wire": output})
    return found


def test_g3_temporal_calls_answer_exactly_as_today(tmp_path: Path) -> None:
    assert temporal_outputs(tmp_path) == _golden("mcp-temporal-calls.json")


# --- G4 research boundary ---


def _research_imports(path: Path) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        if any(n.startswith(tuple(f"lovspor.{r}" for r in RESEARCH)) for n in names):
            lines.append(node.lineno)
    return lines


def test_g4_only_the_ops_cli_imports_a_research_package() -> None:
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC)
        if relative.parts[0] in RESEARCH or str(relative) in RESEARCH_IMPORTERS:
            continue
        if lines := _research_imports(path):
            found[str(relative)] = lines
    assert found == {}, f"production code imports a research package: {found}"


def test_g4_importing_production_loads_no_research_module() -> None:
    probe = (
        "import importlib, json, sys\n"
        f"for name in {list(PRODUCTION_MODULES)!r}: importlib.import_module(name)\n"
        f"print(json.dumps(sorted(m for m in sys.modules if m.split('.')[1:2] and "
        f"m.split('.')[1] in {list(RESEARCH)!r} and m.startswith('lovspor.'))))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert json.loads(done.stdout) == []


# --- G5 legal history is off ---

_HISTORY = re.compile(r"legal.?time|legal.?history|crosswalk|forarbeid|replay", re.I)


def test_g5_no_setting_turns_legal_history_on() -> None:
    assert [f for f in Settings.model_fields if _HISTORY.search(f)] == []


def test_g5_no_mcp_tool_serves_legal_history(tmp_path: Path) -> None:
    build_central(tmp_path)
    assert [name for name in _tools(tmp_path) if _HISTORY.search(name)] == []


def test_g5_the_sync_workflow_runs_no_research_command() -> None:
    text = (WORKFLOWS / "sync.yml").read_text(encoding="utf-8")
    assert re.findall(r"lovspor\s+ops\s+(?:legal-time|crosswalk|forarbeider)", text) == []


# --- G6 deployment boundary ---

#: Names whose appearance in a workflow makes it part of the deployment boundary:
#: the lovverk write key, the seal key, the two protected environments, the corpus remote.
_BOUNDARY_MARKERS = re.compile(
    r"LOVVERK_DEPLOY_KEY|LOVVERK_SEAL_SIGNING_KEY|lovverk-sync|lovverk-seal|"
    r"github\.com[:/]bartoszkobylinski/lovverk"
)
#: Ways to hand a job every secret at once, whatever their names.
_ALL_SECRETS = re.compile(r"toJSON\(\s*secrets\s*\)|secrets:\s*inherit|secrets\[")
_PUSH = re.compile(r"\bgit\b.*\bpush\b")
SEAL_SCRIPT = ROOT / "scripts" / "seal" / "sign-and-tag.sh"


def _workflow_files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def _logical_lines(text: str) -> list[str]:
    """Lines with shell continuations joined, comments dropped."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [line.strip() for line in joined.splitlines() if not line.lstrip().startswith("#")]


def _pushes(text: str) -> list[str]:
    return sorted({line for line in _logical_lines(text) if _PUSH.search(line)})


def _secrets(text: str) -> list[str]:
    names = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))
    return sorted(names | ({"<all secrets>"} if _ALL_SECRETS.search(text) else set()))


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    triggers = workflow.get(True) or workflow.get("on") or {}
    if isinstance(triggers, str | list):
        triggers = dict.fromkeys([triggers] if isinstance(triggers, str) else triggers)
    found: dict[str, Any] = {}
    for name, raw in sorted(triggers.items()):
        spec = raw or {}
        if name == "schedule":
            found[name] = [entry["cron"] for entry in spec]
            continue
        inputs = {
            key: {k: v for k, v in sorted(field.items()) if k != "description"}
            for key, field in sorted((spec.get("inputs") or {}).items())
        }
        found[name] = {**{k: v for k, v in sorted(spec.items()) if k != "inputs"}, "inputs": inputs}
    return found


def _actions(steps: list[dict[str, Any]]) -> list[str]:
    """Every action a job runs, without its version: a new one can act with the job's keys."""
    return sorted({str(step["uses"]).split("@", 1)[0] for step in steps if step.get("uses")})


def _job_boundary(job: dict[str, Any]) -> dict[str, Any]:
    steps = job.get("steps") or []
    text = yaml.safe_dump(job, sort_keys=True, width=10**9)
    runs = "\n".join(str(step.get("run", "")) for step in steps)
    return {
        "uses": job.get("uses"),
        "if": job.get("if"),
        "needs": job.get("needs"),
        "runs-on": job.get("runs-on"),
        "container": job.get("container"),
        "environment": job.get("environment"),
        "matrix": (job.get("strategy") or {}).get("matrix"),
        "permissions": job.get("permissions"),
        "actions": _actions(steps),
        "checkouts": [
            step.get("with") or {}
            for step in steps
            if str(step.get("uses", "")).startswith("actions/checkout@")
        ],
        "secrets": _secrets(text),
        "variables": sorted(set(re.findall(r"vars\.([A-Za-z0-9_]+)", text))),
        "pushes": _pushes(runs),
        "scripts": sorted({line for line in _logical_lines(runs) if "scripts/" in line}),
    }


def deployment_boundary() -> dict[str, Any]:
    """What decides which code reaches production, with which credentials, and where it writes.

    Every workflow that names the lovverk write key, the seal key, a protected
    environment or the corpus remote: its triggers with their filters and inputs,
    permissions, concurrency and defaults, every secret and push anywhere in its
    text, and per job the guard, runner, environment, actions, checkouts, secrets,
    variables, pushes and the scripts it runs. Then, for every workflow file, the
    secrets it reads, so a new holder of a key shows here even without a marker;
    and the pushes of the seal script, which runs outside the workflow text.
    """
    workflows, credentials = {}, {}
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        if found := _secrets(text):
            credentials[path.name] = found
        if not _BOUNDARY_MARKERS.search(text):
            continue
        workflow = yaml.safe_load(text)
        workflows[path.name] = {
            "triggers": _triggers(workflow),
            "permissions": workflow.get("permissions"),
            "concurrency": workflow.get("concurrency"),
            "defaults": workflow.get("defaults"),
            "env_secrets": _secrets(yaml.safe_dump(workflow.get("env") or {})),
            "secrets": _secrets(text),
            "pushes": _pushes(text),
            "jobs": {name: _job_boundary(job) for name, job in sorted(workflow["jobs"].items())},
        }
    return {
        "workflows": workflows,
        "workflow_secrets": credentials,
        "seal_script_pushes": _pushes(SEAL_SCRIPT.read_text(encoding="utf-8")),
    }


def test_g6_the_production_deployment_boundary_is_unchanged() -> None:
    """The boundary as the owner-accepted publication-seal design left it (#965, merged
    2026-11-19): ``sync.yml``'s ``sync`` job in ``lovverk-sync`` and its ``seal`` job in
    ``lovverk-seal``, the seal key in the seal job only, the notes-then-main push and the
    seal tag push, and the one-time ``verify-lovverk-key.yml`` check. Each environment's
    deployment policy is a repository setting no file holds, so this gate cannot see it.
    A change here changes what reaches production; it fails until the owner reviews the
    golden in a pull request that names the change, its class and its evidence."""
    assert json.loads(json.dumps(deployment_boundary())) == _golden("sync-boundary.json")


# --- G7 destructive migration ---

_DESTRUCTIVE = re.compile(
    r"identity-(?:apply|plan)|push\s+(?:--force|-f\b|--force-with-lease)|filter-branch|"
    r"reset\s+--hard|update-ref\s+-d|branch\s+-D"
)


def test_g7_no_workflow_migrates_identity_or_rewrites_history() -> None:
    found = {
        path.name: _DESTRUCTIVE.findall(path.read_text(encoding="utf-8"))
        for path in _workflow_files()
    }
    assert {name: hits for name, hits in found.items() if hits} == {}


def test_g7_only_the_identity_apply_path_writes_the_identity_declaration() -> None:
    writers = sorted(
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if re.search(r"write_declaration\(|IDENTITY_FILENAME\)\.write", path.read_text("utf-8"))
        and path.name != "schema.py"
    )
    assert writers == _golden("identity-declaration-writers.json")
