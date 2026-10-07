"""The assumptions mutation-equivalents/ entries stand on (issue #132).

A registered equivalent mutant waives a required check for good. Four of the
register's entries argue from Python's own semantics — a falsy ``None``, the
character-set argument of ``str.rstrip`` — and those cannot rot. The fifth
argues from httpx normalising the request method, and ``pyproject.toml`` pins
httpx by a floor, not an exact version: a bump could make that justification
false while the gate kept waiving the survivor, with nothing re-reading the
argument.

So an entry that argues from a dependency names the test that pins its
assumption, and the tests live here.
"""

from __future__ import annotations

import copy
import importlib.metadata
import inspect
import io
import json
import re
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import httpx
import pytest
import typer
from lxml import etree, html
from mcp.types import JSONRPCMessage
from pydantic import ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from lovspor.observatory.freshness_index import StoredRun
from lovspor.observatory.listing import safe_html_parser
from lovspor.parsing.xml_normalizer import safe_parser
from lovspor.promotion.backfill_commands import backfill, backfill_preview, observe
from lovspor.promotion.fields import _ENACTED
from lovspor.release.envelope import CorpusSummary, Marker, ReleaseRecord
from lovspor.site.capabilities import CapabilityDocument, Checkout, Observation, derive_state
from lovspor.site.fingerprint import ReleaseKey
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest, write_manifest
from tests.unit.promotion_fixtures import minimal_pdf
from tests.unit.site_fixtures import (
    available_observation,
    capability_document,
    checkout_expectations,
    throwaway_checkout,
    unobserved_transport,
)

_ROOT = Path(__file__).resolve().parents[2]
_REGISTER = _ROOT / "mutation-equivalents"
_PYPROJECT = _ROOT / "pyproject.toml"


def _entries() -> list[dict[str, Any]]:
    """Every entry of the register, one TOML file per entry (issue #516)."""
    entries: list[dict[str, Any]] = []
    for path in sorted(_REGISTER.rglob("*.toml")):
        entries += tomllib.loads(path.read_text(encoding="utf-8")).get("equivalent", [])
    return entries


def _dependency_names() -> set[str]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    requirements: list[str] = data["project"]["dependencies"]
    return {
        re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].strip() for requirement in requirements
    }


def _entries_missing_assumption_test(
    entries: list[dict[str, Any]], dependencies: set[str]
) -> list[str]:
    """Same rule `test_an_entry_arguing_from_a_dependency_names_the_test_that_pins_it`
    enforces, factored out so it can be exercised against synthetic violations
    too — a check that has only ever run against an already-clean register
    could be silently broken and nobody would see it fail."""
    violations = []
    for entry in entries:
        named = [
            dependency
            for dependency in dependencies
            if re.search(rf"\b{re.escape(dependency)}\b", entry["justification"], re.IGNORECASE)
        ]
        if named and not entry.get("assumption_test"):
            violations.append(entry.get("symbol", "<no symbol>"))
    return violations


def _broken_assumption_tests(entries: list[dict[str, Any]], root: Path) -> list[str]:
    """Same rule `test_every_declared_assumption_test_exists` enforces, factored
    out for the same reason: a node id that no longer resolves must fail this
    check, and that path needs its own proof, not just the clean-register one."""
    broken = []
    for entry in entries:
        node_id = entry.get("assumption_test")
        if not node_id:
            continue
        # rpartition, not partition: a class-scoped node id is
        # `file::Class::test_name`, and splitting on the FIRST separator left
        # `name` as "Class::test_name", which no `def` line can ever contain.
        # A valid id then read as broken — the failure mode this check exists
        # to prevent, pointed the wrong way.
        head, _, name = str(node_id).rpartition("::")
        path = head.partition("::")[0]
        target = root / path
        if not target.exists() or f"def {name}(" not in target.read_text(encoding="utf-8"):
            broken.append(str(node_id))
    return broken


def test_httpx_still_normalises_the_request_method() -> None:
    """Pins the argument that waives the ``"GET"`` -> ``"get"`` mutants in
    ``observatory/fetch.py`` and ``site/probe.py`` and the ``"POST"`` ->
    ``"post"`` one in the probe. The day httpx stops upper-casing the method,
    those mutants become real defects — a request going out as ``get`` — and
    this test is what says so."""
    url = "https://www.baerum.kommune.no/"

    assert httpx.Request("get", url).method == "GET"
    assert httpx.Request("gEt", url).method == "GET"
    assert httpx.Request("GET", url).method == "GET"
    assert httpx.Request("post", url).method == "POST"


def test_assumption_httpx_header_lookup_is_case_insensitive() -> None:
    """Pins the argument that waives the ``get("content-type")`` ->
    ``get("CONTENT-TYPE")`` and ``get("www-authenticate")`` ->
    ``get("WWW-AUTHENTICATE")`` mutants in ``site/probe.py``: an httpx
    ``Headers`` lookup lower-cases the requested name, so every spelling reads
    the one stored value, on a response's headers as on any other."""
    response = httpx.Response(
        401,
        headers={"Content-Type": "text/event-stream; charset=utf-8", "WWW-Authenticate": "Bearer"},
    )

    for name in ("content-type", "CONTENT-TYPE", "Content-Type"):
        assert response.headers.get(name) == "text/event-stream; charset=utf-8", name
    for name in ("www-authenticate", "WWW-AUTHENTICATE", "WWW-Authenticate"):
        assert response.headers.get(name) == "Bearer", name
    assert response.headers.get("X-ABSENT", "") == ""


def test_assumption_the_json_rpc_parser_ignores_a_space_at_a_line_margin() -> None:
    """Pins the argument that waives the ``removeprefix(" ")`` ->
    ``removesuffix(" ")`` mutant in ``site/probe.py::_sse_data``: the two rules
    differ by one U+0020 at one margin of a ``data:`` line's value, and the
    JSON-RPC parser — pydantic's, under ``JSONRPCMessage`` — treats a space
    before or after a token as insignificant, on one line and across the LF
    that joins two. A non-breaking space is not whitespace to it, which is why
    the earlier ``lstrip()`` had no such twin."""
    answer = '{"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}'
    reference = JSONRPCMessage.model_validate_json(answer).root

    for padded in (f" {answer}", f"{answer} ", f" {answer} "):
        assert JSONRPCMessage.model_validate_json(padded).root == reference, padded
    head, tail = answer.split(", ", 1)
    for joined in (f"{head},\n {tail}", f"{head}, \n{tail}", f"{head},\n{tail}"):
        assert JSONRPCMessage.model_validate_json(joined).root == reference, joined
    with pytest.raises(ValidationError):
        JSONRPCMessage.model_validate_json(f"\u00a0{answer}")


def _release_record() -> ReleaseRecord:
    return ReleaseRecord(
        schema_version="1",
        release_content_id="a" * 64,
        release_key=ReleaseKey(
            corpus_commit="c" * 40,
            lovspor_commit="d" * 40,
            state_sha256="e" * 64,
            toolchain_fingerprint="f" * 64,
        ),
        site_manifest_sha256="1" * 64,
        capability_sha256="2" * 64,
        observed_at="2026-01-01T00:00:00Z",
        observer="release-probe",
        corpus=CorpusSummary(
            corpus_commit="c" * 40,
            corpus_commit_time="2026-01-01T00:00:00+00:00",
            engine_version="0.0.0",
            documents=1,
        ),
    )


def test_assumption_a_python_mode_dump_serialises_like_json_mode() -> None:
    """Pins the argument that waives the ``mode="json"`` -> ``None`` / other
    string mutants in ``site/capabilities.py::state_sha256``,
    ``site/fixture.py::document_bytes``, ``release/envelope.py::record_bytes``
    and the content runs in ``observatory/freshness_index.py::_state_binding``:
    pydantic takes the JSON serialiser only for the exact string "json" and the
    Python one otherwise, and these models hold nothing the Python path renders
    differently under ``json.dumps`` — the one non-JSON-native type is
    ``tuple[CredentialMode, ...]``, which dumps as a list either way. A field
    type that the Python path leaves as an object (a datetime, an enum, bytes)
    would make this test fail, and the mutants real."""
    checkout = Checkout.model_validate(checkout_expectations())
    models: list[Any] = [
        derive_state(Observation.model_validate(available_observation()), checkout),
        derive_state(Observation.model_validate(unobserved_transport("network")), checkout),
        CapabilityDocument.model_validate(capability_document()),
        _release_record(),
        Marker(active="b" * 64, previous="a" * 64),
        Marker(active="a" * 64, previous=None),
        StoredRun(sha256="c" * 64, unchanged=0),
        StoredRun(sha256="d" * 64, unchanged=2**70),
    ]

    for model in models:
        reference = json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        for mode in ("python", None, "JSON", "XXjsonXX"):
            dumped = json.dumps(model.model_dump(mode=mode), sort_keys=True, ensure_ascii=False)
            assert dumped == reference, mode


def test_assumption_importlib_metadata_lookup_is_case_insensitive() -> None:
    """Pins the argument that waives the ``version("jinja2")`` -> ``"JINJA2"``
    mutant in ``site/fingerprint.py``: ``importlib.metadata`` normalises the
    requested name (PEP 503: case-folded, runs of ``-_.`` as one ``-``) before
    it searches for a distribution, so every spelling finds the same one."""
    assert importlib.metadata.version("JINJA2") == importlib.metadata.version("jinja2")
    assert importlib.metadata.version("Jinja2") == importlib.metadata.version("jinja2")


def test_assumption_git_rev_parse_fails_on_an_unborn_head_without_verify(tmp_path: Path) -> None:
    """Pins the argument that waives the dropped ``--verify`` in
    ``site/build.py::require_clean_work_tree``: with one revision argument,
    ``git rev-parse`` exits non-zero when it does not resolve — ``--verify``
    or not (it also echoes the argument to stdout, which ``_git`` discards on
    failure) — and prints the same sha when it does."""
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    subprocess.run(["git", "-C", str(unborn), "init", "-q"], check=True)
    without = subprocess.run(
        ["git", "-C", str(unborn), "rev-parse", "HEAD^{commit}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert without.returncode != 0

    root, head = throwaway_checkout(tmp_path / "committed")
    resolved = [
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", *flags, "HEAD^{commit}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for flags in ((), ("--verify",))
    ]
    assert resolved == [head, head]


def test_assumption_codec_names_ignore_case() -> None:
    """Pins the argument that waives ``"utf-8"`` -> ``"UTF-8"`` in
    ``observatory/document_report.py::_decoded``: the codec registry lower-cases
    the name before it looks it up, so both spellings decode the same bytes to
    the same text and refuse the same invalid bytes."""
    assert b"bl\xc3\xa5b\xc3\xa6r".decode("UTF-8") == b"bl\xc3\xa5b\xc3\xa6r".decode("utf-8")
    for spelling in ("UTF-8", "utf-8"):
        with pytest.raises(UnicodeDecodeError):
            b"\xa7".decode(spelling)


def test_assumption_codec_names_ignore_surrounding_quotes() -> None:
    """Pins the argument that waives ``.strip('"')`` -> ``.strip(None)`` in
    ``observatory/document_report.py::_charset``: ``encodings.normalize_encoding``
    drops punctuation at either end of a codec name, so a value that kept its
    quotes names the same codec, and a value of nothing but quotes names none,
    exactly like the empty string the quote-strip leaves."""
    for quoted, bare in (('"iso-8859-1"', "iso-8859-1"), ('"utf-8"', "utf-8")):
        assert b"\xc3\x86".decode(quoted) == b"\xc3\x86".decode(bare)
    for nameless in ('"', '""', ""):
        with pytest.raises(LookupError):
            b"x".decode(nameless)


def _main_text(page: str, parser: html.HTMLParser | None) -> str | None:
    main = html.document_fromstring(page, parser=parser).find(".//main")
    return None if main is None else " ".join(main.text_content().split())


def test_assumption_lxml_default_html_parser_reads_text_like_the_hardened_one() -> None:
    """Pins the argument that waives dropping ``parser=safe_html_parser()`` in
    ``observatory/document_report.py::_region``: lxml's default HTML parser has
    ``huge_tree`` off like the hardened one (a page nested past libxml2's depth
    limit loses its text under both), and the one option that differs,
    ``remove_comments``, changes no measured text — a comment is not a text
    node, so ``text_content`` never includes it."""
    pages = [
        "<html><body><main>a<!-- § b -->c</main></body></html>",
        "<main><!--x--><script>s</script>tail</main>",
        "<html><body><main>" + "<div>" * 300 + "§ deep" + "</div>" * 300 + "</main></body></html>",
    ]
    for page in pages:
        for form in (page, page.encode()):
            assert _main_text(form, None) == _main_text(form, safe_html_parser())
    assert _main_text(pages[0], None) == "ac"
    assert _main_text(pages[2], None) == ""


def test_assumption_lxml_ancestors_always_carry_a_string_tag() -> None:
    """Pins the argument that waives ``_tag``'s ``isinstance(...) or True``
    mutant in ``observatory/listing_content.py``: ``_tag`` is only ever asked
    about an ancestor, and lxml gives a non-string ``tag`` only to comments,
    processing instructions and entities — nodes that cannot hold children, so
    none is ever anyone's ancestor."""
    page = (
        "<html><body><!-- top --><main><?pi x?><!-- c --><article>"
        "<p><!-- inner --><a href='/a'>A</a></p></article></main></body></html>"
    )
    root = html.document_fromstring(page, parser=html.HTMLParser())
    nodes = list(root.iter())

    assert any(not isinstance(node.tag, str) for node in nodes)
    assert all(isinstance(ancestor.tag, str) for node in nodes for ancestor in node.iterancestors())
    with pytest.raises(TypeError):
        html.HtmlComment("c").append(html.Element("a"))


def test_the_register_reader_sees_entries() -> None:
    """The two checks below pass vacuously on an empty read; a reader pointed at
    the wrong path (the single file #516 retired) must fail here instead."""
    assert _entries()


def test_an_entry_arguing_from_a_dependency_names_the_test_that_pins_it() -> None:
    """A justification resting on a package that `pyproject.toml` can bump is
    an assumption with an expiry date nobody is watching."""
    violations = _entries_missing_assumption_test(_entries(), _dependency_names())

    assert violations == []


def test_every_declared_assumption_test_exists() -> None:
    """A node id that no longer resolves is worse than no field at all: it reads
    as covered."""
    broken = _broken_assumption_tests(_entries(), _ROOT)

    assert broken == []


def test_a_class_scoped_node_id_resolves(tmp_path: Path) -> None:
    """`file::Class::test_name` is an ordinary pytest id and must not read as
    broken merely for having two separators."""
    module = tmp_path / "tests" / "unit" / "test_x.py"
    module.parent.mkdir(parents=True)
    module.write_text("class TestThing:\n    def test_holds(self) -> None:\n        pass\n")
    entry = [{"assumption_test": "tests/unit/test_x.py::TestThing::test_holds"}]

    assert _broken_assumption_tests(entry, tmp_path) == []


def test_a_node_id_naming_a_missing_test_is_still_broken(tmp_path: Path) -> None:
    """The widened split must not widen what passes: a name that is not there
    still fails, class-scoped or not."""
    module = tmp_path / "tests" / "unit" / "test_x.py"
    module.parent.mkdir(parents=True)
    module.write_text("class TestThing:\n    def test_holds(self) -> None:\n        pass\n")
    entry = [{"assumption_test": "tests/unit/test_x.py::TestThing::test_gone"}]

    assert _broken_assumption_tests(entry, tmp_path) == [
        "tests/unit/test_x.py::TestThing::test_gone"
    ]


def test_an_entry_naming_a_dependency_without_an_assumption_test_is_caught() -> None:
    """Proves the checker actually fires: run against the real register it can
    only ever see the already-clean state, which would pass even if the rule
    were a no-op."""
    entries = [
        {
            "file": "src/lovspor/observatory/fetch.py",
            "symbol": "_request",
            "justification": "httpx normalises the method before the request exists.",
        }
    ]

    violations = _entries_missing_assumption_test(entries, {"httpx"})

    assert violations == ["_request"]


def test_an_entry_with_no_dependency_in_its_justification_is_not_flagged() -> None:
    entries = [
        {
            "file": "src/lovspor/observatory/model.py",
            "symbol": "record_to_json_line",
            "justification": "json.dumps tests ensure_ascii for truth, and None is falsy.",
        }
    ]

    violations = _entries_missing_assumption_test(entries, {"httpx"})

    assert violations == []


def test_an_entry_naming_a_dependency_with_an_assumption_test_is_not_flagged() -> None:
    entries = [
        {
            "file": "src/lovspor/observatory/fetch.py",
            "symbol": "_request",
            "justification": "httpx normalises the method before the request exists.",
            "assumption_test": (
                "tests/unit/test_mutation_equivalents_assumptions.py"
                "::test_httpx_still_normalises_the_request_method"
            ),
        }
    ]

    violations = _entries_missing_assumption_test(entries, {"httpx"})

    assert violations == []


def test_an_assumption_test_pointing_at_a_missing_file_is_caught(tmp_path: Path) -> None:
    entries = [{"symbol": "_request", "assumption_test": "tests/unit/does_not_exist.py::test_x"}]

    broken = _broken_assumption_tests(entries, tmp_path)

    assert broken == ["tests/unit/does_not_exist.py::test_x"]


def test_an_assumption_test_naming_a_missing_function_is_caught(tmp_path: Path) -> None:
    test_file = tmp_path / "tests" / "unit" / "test_pinned.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_other():\n    assert True\n")
    entries = [{"symbol": "_request", "assumption_test": "tests/unit/test_pinned.py::test_missing"}]

    broken = _broken_assumption_tests(entries, tmp_path)

    assert broken == ["tests/unit/test_pinned.py::test_missing"]


def test_an_entry_with_no_assumption_test_field_is_not_a_broken_one(tmp_path: Path) -> None:
    """Absence is legal for entries that never named a dependency; only a
    present-but-dangling node id is a defect."""
    entries = [{"symbol": "record_to_json_line"}]

    assert _broken_assumption_tests(entries, tmp_path) == []


def test_hashlib_folds_the_digest_name_case() -> None:
    """``hashlib.file_digest(f, "SHA256")`` resolves through ``hashlib.new``, whose
    OpenSSL-backed lookup is case-insensitive; the register entry for
    ``release/linking.py`` argues from this."""
    import hashlib  # noqa: PLC0415 — the assumption under test is this module's behaviour
    import io  # noqa: PLC0415

    payload = b"lovspor"
    assert hashlib.new("SHA256").name == "sha256"
    assert (
        hashlib.file_digest(io.BytesIO(payload), "SHA256").hexdigest()
        == hashlib.file_digest(io.BytesIO(payload), "sha256").hexdigest()
        == hashlib.sha256(payload).hexdigest()
    )


_OID = re.compile(r"[0-9a-f]{40}")


def _git_in(repo: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_assumption_git_notes_list_prints_two_ids_and_one_space(tmp_path: Path) -> None:
    """Pins the waiver for `partition(" ")` -> `rpartition(" ")` in
    ``temporal_attestation._note_objects``: every ``git notes list`` line is
    exactly ``<note blob id> <annotated object id>`` — two ids, one space —
    so the first and the last space are the same space."""
    root, head = throwaway_checkout(tmp_path / "notes")
    blob = _git_in(root, "hash-object", "-w", "--stdin", stdin="x")
    tree = _git_in(root, "rev-parse", f"{head}^{{tree}}").strip()
    for target in (head, blob.strip(), tree):
        _git_in(root, "notes", "--ref=refs/notes/probe", "add", "-m", "[]", target)

    lines = _git_in(root, "notes", "--ref=refs/notes/probe", "list").splitlines()

    assert len(lines) == 3
    for line in lines:
        first, second = line.split(" ")
        assert _OID.fullmatch(first) and _OID.fullmatch(second), line


def test_assumption_git_batch_check_prints_id_and_type_with_one_space(tmp_path: Path) -> None:
    """Pins the waiver for `partition(" ")` -> `rpartition(" ")` in
    ``temporal_attestation._require_commit_anchors``: with the format
    ``%(objectname) %(objecttype)`` every line — present objects, and the
    ``<id> missing`` line git prints for an absent one — holds one space."""
    root, head = throwaway_checkout(tmp_path / "batch")
    tree = _git_in(root, "rev-parse", f"{head}^{{tree}}").strip()
    missing = "f" * 40

    out = _git_in(
        root,
        "cat-file",
        "--batch-check=%(objectname) %(objecttype)",
        stdin=f"{head}\n{tree}\n{missing}\n",
    )

    assert out.splitlines() == [f"{head} commit", f"{tree} tree", f"{missing} missing"]


def _manifest_read_back(tmp_path: Path) -> tuple[Manifest, bytes]:
    """A manifest as `run_sync` holds it: written by the engine and read back,
    so `last_seen` carries the tzinfo pydantic parses a stored stamp into."""
    path = tmp_path / "manifest.json"
    record = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="gjeldende-lover/drageloven.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
        status="current",
        renderer_version=1,
    )
    generated = datetime.fromisoformat("2026-01-02T00:00:00+00:00")
    write_manifest(Manifest(generated_at=generated, documents={"lov-x": record}), path)
    return read_manifest(path), path.read_bytes()


def _rewritten(manifest: Manifest, record: ManifestRecord, path: Path) -> bytes:
    write_manifest(Manifest(generated_at=manifest.generated_at, documents={"lov-x": record}), path)
    return path.read_bytes()


def test_assumption_model_copy_without_updates_is_value_equal_and_writes_the_same_bytes(
    tmp_path: Path,
) -> None:
    """Pins the waiver for `if updates` -> `if (updates) or True` in
    ``sync/orchestrator.py::run_sync``: on the no-op re-render path the mutant
    stores ``prior_record.model_copy(update={})`` where the original stores
    ``prior_record`` itself. pydantic must hand back a record equal under
    ``==`` (the commit decision) that writes the same manifest bytes (the
    published artifact); only ``is`` may tell the two apart."""
    manifest, written = _manifest_read_back(tmp_path)
    prior = manifest.documents["lov-x"]

    copied = prior.model_copy(update={})

    assert copied is not prior
    assert copied == prior
    assert {"lov-x": copied} == manifest.documents
    assert _rewritten(manifest, copied, tmp_path / "copy.json") == written


def test_assumption_pypdf_reads_strict_for_truth_and_defaults_it_off() -> None:
    """Pins the argument that waives the ``strict=False`` -> ``strict=None`` and the
    dropped-argument mutants in ``promotion/source_text.py::pdf_lines``: pypdf's
    ``PdfReader`` defaults ``strict`` to ``False`` and reads it only for truth, so
    ``None``, ``False`` and the default all repair a PDF that ``strict=True`` refuses."""
    broken = minimal_pdf().replace(b"startxref\n", b"startxref\n1")
    lenient: dict[str, Any] = {"strict": None}

    assert inspect.signature(PdfReader).parameters["strict"].default is False
    with pytest.raises(PdfReadError):
        _ = PdfReader(io.BytesIO(broken), strict=True).pages[0]
    expected = [page.extract_text() for page in PdfReader(io.BytesIO(broken), strict=False).pages]
    for reader in (PdfReader(io.BytesIO(broken)), PdfReader(io.BytesIO(broken), **lenient)):
        assert [page.extract_text() for page in reader.pages] == expected


def test_assumption_lxml_reads_remove_blank_text_for_truth() -> None:
    """Pins the argument that waives the ``remove_blank_text=False`` ->
    ``remove_blank_text=None`` mutant in ``promotion/source_text.py::docx_lines``:
    lxml's ``XMLParser`` reads the flag for truth, so ``None`` keeps the blank text
    that ``True`` strips — here a word space before a comment inside a run."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document = f'<w:t xmlns:w="{w}"> <!--x--></w:t>'.encode()

    def text(flag: Any) -> str | None:
        return etree.fromstring(document, parser=safe_parser(remove_blank_text=flag)).text

    assert text(True) is None
    assert text(None) == text(False) == " "


def test_assumption_the_enactment_pattern_has_only_the_organ_and_date_groups() -> None:
    """Pins the argument that waives the ``groups()[-1]`` -> ``groups()[+1]`` mutant in
    ``promotion/fields.py::_enactment``: with exactly two groups, index 1 and index -1
    name the same one, the date. A third group would make the mutant a real defect."""
    assert _ENACTED.groups == 2
    assert _ENACTED.groupindex == {"organ": 1}


def test_assumption_click_echo_treats_none_as_empty_and_reads_nl_for_truth() -> None:
    """``typer.echo`` is ``click.echo``: ``None`` prints as ``""``, and ``nl`` is a truth test.

    Pins the two ``preview_impl`` entries (PR #517): ``typer.echo("")`` →
    ``typer.echo(None)`` and ``nl=False`` → ``nl=None`` write the same bytes.
    """

    def written(message: str | None, nl: bool | None) -> str:
        stream = io.StringIO()
        typer.echo(message, file=stream, nl=nl)  # type: ignore[arg-type]
        return stream.getvalue()

    assert typer.echo is click.echo
    assert written(None, True) == written("", True) == "\n"
    assert written("x", None) == written("x", False) == "x"


def test_assumption_typer_names_an_unnamed_command_after_its_function() -> None:
    """``app.command(None)(f)`` registers ``f`` as ``f.__name__`` with ``_`` read as ``-``.

    Pins the three ``register_backfill`` entries (PR #568): each explicit name
    there is the one typer derives from its callback, so dropping it registers
    the same command. Fails, too, if a callback is renamed away from its command.
    """
    app = typer.Typer()
    for callback in (backfill_preview, backfill, observe):
        app.command(None)(callback)

    group = typer.main.get_command(app)

    assert isinstance(group, click.Group)
    assert sorted(group.commands) == ["backfill", "backfill-preview", "observe"]


def test_assumption_lxml_shallow_copy_of_an_element_copies_its_subtree() -> None:
    """``copy.copy`` of an lxml element is a deep copy: editing it leaves the source.

    Pins the ``source_text._lines_of`` entry (PR #579): ``_block_text`` rewrites
    the tree it is given, and a copy keeps the region readable for the next
    candidate article, whichever of ``copy.copy`` / ``copy.deepcopy`` makes it.
    """
    source = html.fromstring("<article><p>a<b>x</b></p></article>")
    shallow = copy.copy(source)
    shallow.find(".//p").text = "edited"
    shallow.find(".//b").text = "edited"

    assert html.tostring(source) == b"<article><p>a<b>x</b></p></article>"
    assert shallow.find(".//b") is not source.find(".//b")
