"""Tool-level tests for ``get_temporal_events`` (ADR-0012 slice C).

Exercises the MCP wiring on a real two-commit git corpus: state and
section resolution, the two-axis composition (``recorded_at`` selects
which events exist, ``valid_at`` is only ever the evaluation date), the
knowledge horizon of each serving state, the reconciliation field backed
by the slice-B attestation registry, and the outcome taxonomy. The pure
composition rules live in ``test_temporal_events.py``.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import lovspor.mcp as mcp_module
import lovspor.temporal_events as temporal_events_module
from lovspor.errors import TemporalDerivationError
from lovspor.mcp import CorpusNotFoundError, TemporalScopeError, build_server
from lovspor.temporal import TEMPORAL_PARSER_VERSION
from lovspor.temporal_attestation import (
    ATTESTATION_NOTES_REF,
    AttestationError,
    TemporalAttestation,
    fetch_attestations,
    write_attestation,
)
from lovspor.temporal_events import derive_served_layer
from tests.unit.test_mcp_recorded_at import _commit_all, _doc, _manifest, _record, _run_git


def _forskrift_record(slug: str, xml_hash: str) -> dict[str, object]:
    """A central-regulation record — outside ADR-0012's measured scope."""
    return {
        **_record(slug, xml_hash),
        "doc_type": "forskrift",
        "source_dataset": "gjeldende-sentrale-forskrifter",
        "markdown_path": f"forskrifter/{slug}.md",
    }


PENDING_NOTE = (
    "> **Vert endra** ved lov [19 juni 2026 nr. 48](lov/2026-06-19-48) "
    "(i kraft frå den tid Kongen bestemmer).\n"
)

DATED_NOTE = (
    "> Endret ved [lov 19 juni 2026 nr. 48](lov/2026-06-19-48) "
    "(ikr. 5 mai 2026 iflg. [res. 5 mai 2026 nr. 100](forskrift/2026-05-05-100)).\n"
)

UNRECOGNISED_NOTE = (
    "> Endres ved lov [1 jan 2027 nr. 1](lov/2027-01-01-1) (i kraft når departementet bestemmer).\n"
)

TESTLOVEN_STATE_1 = (
    "## Kapittel 1. Innleiande føresegner\n\n"
    f"{PENDING_NOTE}\n"
    "### § 1-1. Formål\n\n"
    "Lovtekst v1.\n\n"
    f"{PENDING_NOTE}\n"
    "### § 8-7 a. Særskilde reglar\n\n"
    "Paragrafen er ikke satt i kraft.\n"
)

TESTLOVEN_STATE_2 = (
    "## Kapittel 1. Innleiande føresegner\n\n"
    f"{PENDING_NOTE}\n"
    "### § 1-1. Formål\n\n"
    "Lovtekst v2.\n\n"
    f"{DATED_NOTE}\n"
    "### § 8-7 a. Særskilde reglar\n\n"
    "Paragrafen er ikke satt i kraft.\n"
)

TOMLOVEN = "## Kapittel 9.\n\n### § 9-9. Regel\n\nIngen endringsnotar.\n"

FEILLOVEN = f"### § 1. Regel\n\nLovtekst.\n\n{UNRECOGNISED_NOTE}"


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, str, str]:
    """State 1 (2026-05-01): testloven § 1-1 pending, tomloven fact-free.
    State 2 (2026-05-10): the § 1-1 note is now dated 2026-05-05 and
    feilloven (unrecognised marker) enters the corpus."""
    repo = tmp_path / "corpus"
    (repo / "lover").mkdir(parents=True)
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "lover" / "testloven.md").write_text(_doc("Testloven", TESTLOVEN_STATE_1))
    (repo / "lover" / "tomloven.md").write_text(_doc("Tomloven", TOMLOVEN))
    (repo / "manifest.json").write_text(
        _manifest(
            {
                "doc-a": _record("testloven", "hash-t1"),
                "doc-b": _record("tomloven", "hash-o1"),
            },
        ),
    )
    sha1 = _commit_all(repo, "sync 1", "2026-05-01T12:00:00Z")
    (repo / "lover" / "testloven.md").write_text(_doc("Testloven", TESTLOVEN_STATE_2))
    (repo / "lover" / "feilloven.md").write_text(_doc("Feilloven", FEILLOVEN))
    (repo / "forskrifter").mkdir()
    (repo / "forskrifter" / "testforskriften.md").write_text(_doc("Testforskriften", TOMLOVEN))
    (repo / "manifest.json").write_text(
        _manifest(
            {
                "doc-a": _record("testloven", "hash-t2"),
                "doc-b": _record("tomloven", "hash-o1"),
                "doc-c": _record("feilloven", "hash-f1"),
                "doc-d": _forskrift_record("testforskriften", "hash-fs1"),
            },
        ),
    )
    sha2 = _commit_all(repo, "sync 2", "2026-05-10T12:00:00Z")
    return repo, sha1, sha2


def _tool_fn(repo, name="get_temporal_events"):  # type: ignore[no-untyped-def]
    return build_server(repo)._tool_manager._tools[name].fn


def _attest(repo: Path, sha: str) -> None:
    write_attestation(
        repo,
        TemporalAttestation(
            corpus_commit=sha,
            parser_version=TEMPORAL_PARSER_VERSION,
            documents_reconciled=2,
            notes_total=2,
            events_total=2,
            attested_at=datetime(2026, 5, 10, 13, 0, tzinfo=UTC),
        ),
    )


def _pending_events(result: dict) -> list[dict]:  # type: ignore[type-arg]
    return [e for e in result["events"] if e["marker_class"] == "pending_indeterminate"]


# ---------- live serving ----------


def test_live_serves_events_markers_and_reconciliation(corpus: tuple[Path, str, str]) -> None:
    repo, _sha1, sha2 = corpus
    result = _tool_fn(repo)(slug="testloven")

    assert result["corpus_commit"] == sha2
    assert result["xml_hash"] == "hash-t2"

    assert result["slug"] == "testloven"
    assert result["temporal_parser_version"] == TEMPORAL_PARSER_VERSION
    assert {event["provision"] for event in result["events"]} == {
        "Kapittel 1. Innleiande føresegner",
        "§ 1-1",
    }
    assert [marker["provision"] for marker in result["never_in_force"]] == ["§ 8-7 a"]
    assert result["reconciliation"] == "unattested"
    assert "recorded_at" not in result


def test_attested_head_serves_attested(corpus: tuple[Path, str, str]) -> None:
    repo, _sha1, sha2 = corpus
    _attest(repo, sha2)

    assert _tool_fn(repo)(slug="testloven")["reconciliation"] == "attested"


def test_attestation_covers_exactly_its_own_state(corpus: tuple[Path, str, str]) -> None:
    """ADR-0012 point 2: attested when a recorded attestation covers the
    SERVED corpus_commit — a historical state's proof never leaks to the
    live head, nor the other way around."""
    repo, sha1, _sha2 = corpus
    _attest(repo, sha1)
    fn = _tool_fn(repo)

    historical = fn(slug="testloven", recorded_at="2026-05-05")
    assert historical["reconciliation"] == "attested"
    assert fn(slug="testloven")["reconciliation"] == "unattested"


def test_corrupt_registry_is_a_typed_failure_not_unattested(
    corpus: tuple[Path, str, str],
) -> None:
    repo, _sha1, sha2 = corpus
    _run_git(repo, "notes", "--ref", ATTESTATION_NOTES_REF, "add", "-m", "not json", sha2)

    with pytest.raises(AttestationError):
        _tool_fn(repo)(slug="testloven")


def test_live_horizon_is_the_head_author_date(corpus: tuple[Path, str, str]) -> None:
    repo, _sha1, _sha2 = corpus
    fn = _tool_fn(repo)

    within = fn(slug="testloven", valid_at="2026-05-09")
    past = fn(slug="testloven", valid_at="2026-06-01")

    assert within["knowledge_horizon"] == "2026-05-10"
    assert _pending_events(within)[0]["commencement_status"] == "not_in_effect"
    assert _pending_events(past)[0]["commencement_status"] == "indeterminate"
    assert _pending_events(past)[0]["status_reason"] == "beyond_knowledge_horizon"


def test_future_valid_at_is_legal(corpus: tuple[Path, str, str]) -> None:
    """Past the horizon is a bounded answer, not a typo to refuse."""
    repo, _sha1, _sha2 = corpus
    result = _tool_fn(repo)(slug="testloven", valid_at="2031-01-01")

    assert result["valid_at"] == "2031-01-01"
    assert _pending_events(result)[0]["status_reason"] == "beyond_knowledge_horizon"


def test_valid_at_rejects_non_iso_forms(corpus: tuple[Path, str, str]) -> None:
    repo, _sha1, _sha2 = corpus

    with pytest.raises(ValueError, match="valid_at must be ISO date"):
        _tool_fn(repo)(slug="testloven", valid_at="01.05.2026")


# ---------- outcome taxonomy (ADR-0012 point 6) ----------


def test_zero_temporal_facts_is_a_successful_empty_answer(
    corpus: tuple[Path, str, str],
) -> None:
    repo, _sha1, _sha2 = corpus
    result = _tool_fn(repo)(slug="tomloven")

    assert result["events"] == []
    assert result["never_in_force"] == []
    assert result["problems"] == []
    assert result["reconciliation"] == "unattested"


def test_derivation_failure_is_typed_never_partial(corpus: tuple[Path, str, str]) -> None:
    repo, _sha1, _sha2 = corpus

    with pytest.raises(TemporalDerivationError, match="unrecognised_marker"):
        _tool_fn(repo)(slug="feilloven")


def test_unknown_section_lists_the_acts_available_ids(corpus: tuple[Path, str, str]) -> None:
    repo, _sha1, _sha2 = corpus

    with pytest.raises(CorpusNotFoundError, match=r"available: § 1-1, § 8-7a"):
        _tool_fn(repo)(slug="testloven", section_id="5-12")


def test_unknown_slug_at_a_state_names_date_and_commit(
    corpus: tuple[Path, str, str],
) -> None:
    repo, sha1, _sha2 = corpus

    with pytest.raises(CorpusNotFoundError, match=f"2026-05-05.*{sha1}"):
        _tool_fn(repo)(slug="finnesikke", recorded_at="2026-05-05")


# ---------- mechanical narrowing at the tool level ----------


def test_section_narrowing_filters_and_never_expands_scopes(
    corpus: tuple[Path, str, str],
) -> None:
    repo, _sha1, _sha2 = corpus
    result = _tool_fn(repo)(slug="testloven", section_id="1-1")

    assert result["section_id"] == "1-1"
    assert [event["provision"] for event in result["events"]] == ["§ 1-1"]
    assert result["never_in_force"] == []


# ---------- the two axes (ADR-0012 point 8) ----------


def test_recorded_at_serves_the_states_own_events_with_evidence(
    corpus: tuple[Path, str, str],
) -> None:
    repo, sha1, _sha2 = corpus
    fn = _tool_fn(repo)

    result = fn(slug="testloven", recorded_at="2026-05-05")

    assert result["recorded_at"] == "2026-05-05"
    assert result["corpus_commit"] == sha1
    assert result["xml_hash"] == "hash-t1"
    section_events = [e for e in result["events"] if e["provision"] == "§ 1-1"]
    assert section_events[0]["marker_class"] == "pending_indeterminate"
    repeat = fn(slug="testloven", recorded_at="2026-05-05")
    assert json.dumps(result, sort_keys=True) == json.dumps(repeat, sort_keys=True)


def test_two_state_acceptance_fixture(corpus: tuple[Path, str, str]) -> None:
    """The named ADR-0012 Validation criterion: a pending marker at R1
    that is dated at R2, queried on both sides of each horizon."""
    repo, _sha1, _sha2 = corpus
    fn = _tool_fn(repo)

    def section_event(recorded_at: str, valid_at: str) -> dict:  # type: ignore[type-arg]
        result = fn(slug="testloven", recorded_at=recorded_at, valid_at=valid_at)
        (event,) = [e for e in result["events"] if e["provision"] == "§ 1-1"]
        return event

    at_r1_within = section_event("2026-05-01", "2026-04-30")
    assert at_r1_within["commencement_status"] == "not_in_effect"

    at_r1_past = section_event("2026-05-01", "2026-06-01")
    assert at_r1_past["commencement_status"] == "indeterminate"
    assert at_r1_past["status_reason"] == "beyond_knowledge_horizon"

    at_r2_same_v = section_event("2026-05-10", "2026-06-01")
    assert at_r2_same_v["commencement_status"] == "in_effect"
    assert at_r2_same_v["status_reason"] is None

    at_r2_before_date = section_event("2026-05-10", "2026-05-04")
    assert at_r2_before_date["commencement_status"] == "not_in_effect"


def test_historical_response_echoes_its_own_horizon(corpus: tuple[Path, str, str]) -> None:
    repo, _sha1, _sha2 = corpus
    result = _tool_fn(repo)(slug="testloven", recorded_at="2026-05-05", valid_at="2026-06-01")

    assert result["knowledge_horizon"] == "2026-05-01"


# ---------- no clock, correct dates reach evaluation ----------


def test_no_t0_notice_and_no_clock_on_any_path(
    corpus: tuple[Path, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tool never routes through the T0 notice machinery: neither
    ``build_notice`` nor ``evaluation_date_today`` may run — evaluation is
    driven by the explicit ``valid_at`` and the state's horizon only."""
    repo, _sha1, _sha2 = corpus

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("T0 notice machinery reached from get_temporal_events")

    monkeypatch.setattr(mcp_module, "build_notice", forbidden)
    monkeypatch.setattr(mcp_module, "evaluation_date_today", forbidden)
    fn = _tool_fn(repo)

    fn(slug="testloven")
    fn(slug="testloven", valid_at="2026-05-09")
    fn(slug="testloven", recorded_at="2026-05-05", valid_at="2026-05-09")


def test_evaluation_receives_valid_at_and_the_states_horizon(
    corpus: tuple[Path, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _sha1, _sha2 = corpus
    calls: list[tuple[date, date]] = []
    original = temporal_events_module.evaluate_event

    def spy(event, valid_at, horizon):  # type: ignore[no-untyped-def]
        calls.append((valid_at, horizon))
        return original(event, valid_at, horizon)

    monkeypatch.setattr(temporal_events_module, "evaluate_event", spy)
    fn = _tool_fn(repo)

    fn(slug="testloven", valid_at="2026-05-20")
    assert calls and all(args == (date(2026, 5, 20), date(2026, 5, 10)) for args in calls)

    calls.clear()
    fn(slug="testloven", recorded_at="2026-05-05", valid_at="2026-05-20")
    assert calls and all(args == (date(2026, 5, 20), date(2026, 5, 1)) for args in calls)

    calls.clear()
    fn(slug="testloven")
    assert calls == []


# ---------- review #230: state identity, provenance, scope, transport ----------


def test_live_serves_the_committed_state_not_the_worktree(
    corpus: tuple[Path, str, str],
) -> None:
    """Blocker 2 reproducer: a dirty worktree must not leak into an answer
    whose reconciliation speaks for the committed HEAD."""
    repo, _sha1, sha2 = corpus
    dirty = _doc("Testloven", TESTLOVEN_STATE_2 + f"\n### § 9-1. Ny\n\nTekst.\n\n{DATED_NOTE}")
    (repo / "lover" / "testloven.md").write_text(dirty)

    result = _tool_fn(repo)(slug="testloven")

    assert result["corpus_commit"] == sha2
    assert all(event["provision"] != "§ 9-1" for event in result["events"])


def test_live_missing_worktree_file_still_serves_the_committed_facts(
    corpus: tuple[Path, str, str],
) -> None:
    """The worse variant from the review: a deleted worktree file must not
    become a successful empty answer under an attested HEAD."""
    repo, _sha1, _sha2 = corpus
    (repo / "lover" / "testloven.md").unlink()

    result = _tool_fn(repo)(slug="testloven")

    assert {event["provision"] for event in result["events"]} == {
        "Kapittel 1. Innleiande føresegner",
        "§ 1-1",
    }


def test_source_line_is_the_committed_file_line(corpus: tuple[Path, str, str]) -> None:
    """Blocker 3: derivation reads the raw committed Markdown, so the served
    source_line provenance is a line of the corpus file — frontmatter and
    H1 included — and matches direct derivation from the raw blob."""
    repo, _sha1, _sha2 = corpus
    raw = _doc("Testloven", TESTLOVEN_STATE_2)
    dated_line = raw.splitlines().index(DATED_NOTE.rstrip("\n")) + 1

    result = _tool_fn(repo)(slug="testloven", section_id="1-1")

    assert [event["source_line"] for event in result["events"]] == [dated_line]
    direct = derive_served_layer(raw, "testloven")
    assert [e.source_line for e in direct.events if e.provision == "§ 1-1"] == [dated_line]


def test_forskrift_is_a_loud_unsupported_scope_outcome(
    corpus: tuple[Path, str, str],
) -> None:
    """Blocker 4: T5 is unmeasured — a regulation must be refused with the
    scope stated, on both axes, never silently served."""
    repo, _sha1, _sha2 = corpus
    fn = _tool_fn(repo)

    with pytest.raises(TemporalScopeError, match="T5"):
        fn(slug="testforskriften")
    with pytest.raises(TemporalScopeError, match="gjeldende-lover"):
        fn(slug="testforskriften", recorded_at="2026-05-10")


def test_unsynchronised_clone_fails_closed_not_unattested(
    corpus: tuple[Path, str, str],
    tmp_path: Path,
) -> None:
    """Blocker 1, serving side: a plain clone of an attested origin must not
    read the remote proof as a false local absence."""
    repo, _sha1, sha2 = corpus
    _attest(repo, sha2)
    clone = tmp_path / "plain-clone"
    _run_git(tmp_path, "clone", str(repo), str(clone))

    with pytest.raises(AttestationError, match="not synchronised"):
        _tool_fn(clone)(slug="testloven")

    fetch_attestations(clone)
    assert _tool_fn(clone)(slug="testloven")["reconciliation"] == "attested"


def test_derivation_runs_once_per_document_per_state(
    corpus: tuple[Path, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M1 (review major 5): parse cost is first touch per document per
    state — repeat calls, on either axis, hit the per-state cache."""
    repo, _sha1, _sha2 = corpus
    calls: list[str | None] = []
    original = mcp_module.derive_served_layer

    def spy(body: str, document_ref: str | None = None):  # type: ignore[no-untyped-def]
        calls.append(document_ref)
        return original(body, document_ref)

    monkeypatch.setattr(mcp_module, "derive_served_layer", spy)
    fn = _tool_fn(repo)

    fn(slug="testloven")
    fn(slug="testloven", valid_at="2026-05-09")
    fn(slug="testloven", recorded_at="2026-05-10", valid_at="2026-05-09")

    assert calls == ["testloven"]


def test_valid_at_rejects_invalid_calendar_date(corpus: tuple[Path, str, str]) -> None:
    repo, _sha1, _sha2 = corpus

    with pytest.raises(ValueError, match="valid_at must be ISO date"):
        _tool_fn(repo)(slug="testloven", valid_at="2026-02-30")


def test_malformed_live_head_metadata_is_a_typed_evidence_failure(
    corpus: tuple[Path, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful git invocation with an invalid wire shape is still an
    unreadable serving-state identity, never an implementation exception."""
    repo, _sha1, _sha2 = corpus

    class MalformedGitResult:
        stdout = "sha-without-author-date\n"

    monkeypatch.setattr(mcp_module.subprocess, "run", lambda *args, **kwargs: MalformedGitResult())

    with pytest.raises(AttestationError, match="identity and knowledge horizon are unreadable"):
        _tool_fn(repo)(slug="testloven")


def test_live_head_git_failure_is_a_typed_evidence_failure(
    corpus: tuple[Path, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolving HEAD must ask subprocess to raise on a non-zero git exit."""
    repo, _sha1, _sha2 = corpus

    def failed_git(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        assert kwargs["check"] is True
        raise mcp_module.subprocess.CalledProcessError(128, args[0])

    monkeypatch.setattr(mcp_module.subprocess, "run", failed_git)

    with pytest.raises(AttestationError, match="identity and knowledge horizon are unreadable"):
        _tool_fn(repo)(slug="testloven")


def test_live_unknown_slug_names_the_current_head(
    corpus: tuple[Path, str, str],
) -> None:
    repo, _sha1, sha2 = corpus

    with pytest.raises(CorpusNotFoundError) as exc_info:
        _tool_fn(repo)(slug="finnesikke")

    assert f"at the current head (corpus_commit {sha2})" in str(exc_info.value)
