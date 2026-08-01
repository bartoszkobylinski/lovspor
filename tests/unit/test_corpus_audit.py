"""Corpus audit — reconcile what is on disk against what the manifest claims.

Nothing in the engine did this. The 48 orphaned documents found on 2026-07-12
(files committed to `lovverk` with no manifest record at all) accumulated for
seven weeks and surfaced only because a section count failed to add up. Every
mechanism in the sync compares *upstream* against the *manifest*; a file that
is in neither is invisible to all of them and can never self-heal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.corpus_audit import (
    ADVISORY_KINDS,
    INTEGRITY_KINDS,
    AuditFinding,
    AuditReport,
    audit_corpus,
)
from lovspor.storage.manifest import Manifest, ManifestRecord


def _record(
    slug: str,
    *,
    status: str = "current",
    dataset: str = "gjeldende-lover",
    renderer_version: int | None = 3,
) -> ManifestRecord:
    return ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path=f"lover/{slug}.md",
        source_dataset=dataset,
        last_seen=datetime(2026, 7, 12, tzinfo=UTC),
        status=status,  # type: ignore[arg-type]
        slug=slug,
        title=slug.title(),
        renderer_version=renderer_version,
    )


def _manifest(**records: ManifestRecord) -> Manifest:
    return Manifest(
        generated_at=datetime(2026, 7, 12, tzinfo=UTC),
        documents=dict(records),
    )


def _seed(root: Path, *slugs: str) -> None:
    (root / "lover").mkdir(parents=True, exist_ok=True)
    for slug in slugs:
        (root / "lover" / f"{slug}.md").write_text(f"# {slug}\n", encoding="utf-8")


def _kinds(findings: tuple[AuditFinding, ...]) -> list[tuple[str, str]]:
    return [(f.kind, f.path) for f in findings]


def test_clean_corpus_reports_no_findings(tmp_path: Path) -> None:
    _seed(tmp_path, "skatteloven")
    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert report.findings == ()
    assert report.clean is True
    assert report.documents_checked == 1


def test_detects_an_orphan_document_on_disk(tmp_path: Path) -> None:
    """The 2026-07-12 bug: a file committed to the corpus with NO manifest
    record. Invisible to change detection, so it never self-heals."""
    _seed(tmp_path, "skatteloven", "endr-i-kjøretøyforskriften")
    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert _kinds(report.findings) == [
        ("orphan_document", "lover/endr-i-kjøretøyforskriften.md"),
    ]
    assert report.clean is False


def test_detects_a_tombstoned_document_whose_file_was_never_deleted(tmp_path: Path) -> None:
    """The canary for the *other* half of the 2026-07-12 bug: a removal that
    wrote its manifest tombstone but skipped deleting the file. Before PR #89
    the tombstone was then dropped and the file became an untraceable orphan."""
    _seed(tmp_path, "skatteloven", "opphevet-lov")
    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven"), nl2=_record("opphevet-lov", status="removed")),
    )

    assert _kinds(report.findings) == [
        ("tombstoned_but_present", "lover/opphevet-lov.md"),
    ]


def test_a_tombstone_superseded_at_the_same_path_is_duplicate_ownership_not_tombstoned(
    tmp_path: Path,
) -> None:
    """Two acts can slugify to one path: when a new regulation replaces an old
    one under the same short title, the old record is tombstoned while the new
    one — a different doc id — renders to the *same* markdown_path.

    The file on disk belongs to the live act, so `tombstoned_but_present` must
    stay suppressed: that finding reads "its file was never deleted", and acting
    on it deletes text that is in force. But the shared pointer itself is real
    drift — it fuses two documents' identities in every path-keyed consumer
    (`git log --follow` history, timetravel), which is exactly how the 2026 act
    inherited the 2009 act's events. Real case: sf-20090520-0534 (removed) and
    sf-20260710-1545 (current) both own forskrift-om-omregningsfaktorer.md.
    So it surfaces as `duplicate_path_ownership`, whose fix is manifest-side.
    """
    _seed(tmp_path, "omregningsfaktorer")
    report = audit_corpus(
        tmp_path,
        _manifest(
            old=_record("omregningsfaktorer", status="removed"),
            new=_record("omregningsfaktorer"),
        ),
    )

    assert _kinds(report.findings) == [
        ("duplicate_path_ownership", "lover/omregningsfaktorer.md"),
    ]
    [finding] = report.findings
    assert finding.doc_id == "new"  # the single current owner
    assert "new (current)" in finding.detail
    assert "old (removed)" in finding.detail


def test_a_missing_live_owner_on_a_tombstoned_shared_path_is_still_missing(tmp_path: Path) -> None:
    """The tombstone suppression only applies when the shared path is present.

    If a current record and a tombstone collide on one markdown_path but the file
    is absent, the audit must still report the live record as missing — the new
    tombstone guard must not mask a real drift on the current owner. The shared
    pointer is additionally reported as duplicate ownership.
    """
    report = audit_corpus(
        tmp_path,
        _manifest(
            old=_record("omregningsfaktorer", status="removed"),
            new=_record("omregningsfaktorer"),
        ),
    )

    assert _kinds(report.findings) == [
        ("duplicate_path_ownership", "lover/omregningsfaktorer.md"),
        ("missing_document", "lover/omregningsfaktorer.md"),
    ]
    assert report.documents_checked == 1


def test_detects_a_current_document_missing_from_disk(tmp_path: Path) -> None:
    """The inverse drift: the manifest promises a law the corpus does not have.
    get_law would raise on a slug that INDEX.md advertises."""
    _seed(tmp_path, "skatteloven")
    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven"), nl2=_record("forsvunnet-lov")),
    )

    assert _kinds(report.findings) == [("missing_document", "lover/forsvunnet-lov.md")]


def test_detects_an_orphan_embedding_sidecar(tmp_path: Path) -> None:
    """A crashed embeddings backfill leaves .bin files with no owning record."""
    _seed(tmp_path, "skatteloven")
    embeddings = tmp_path / "lover" / "embeddings"
    embeddings.mkdir(parents=True)
    (embeddings / "skatteloven.bin").write_bytes(b"\x00")
    (embeddings / "spøkelse.bin").write_bytes(b"\x00")

    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert _kinds(report.findings) == [("orphan_embedding", "lover/embeddings/spøkelse.bin")]


def test_a_removed_record_does_not_own_a_leftover_embedding_sidecar(tmp_path: Path) -> None:
    """A tombstoned record is not a current owner of its sidecar; if the
    embedding file is still on disk, the audit must still report it as drift."""
    _seed(tmp_path, "skatteloven")
    embeddings = tmp_path / "lover" / "embeddings"
    embeddings.mkdir(parents=True)
    (embeddings / "opphevet-lov.bin").write_bytes(b"\x00")

    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven"), nl2=_record("opphevet-lov", status="removed")),
    )

    assert _kinds(report.findings) == [("orphan_embedding", "lover/embeddings/opphevet-lov.bin")]


def test_detects_a_stale_render(tmp_path: Path) -> None:
    """Renderer-version self-healing re-renders these, but a doc stuck below the
    current version across many syncs means the backfill is not converging."""
    _seed(tmp_path, "skatteloven")
    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven", renderer_version=1)),
        renderer_version=3,
    )

    assert _kinds(report.findings) == [("stale_render", "lover/skatteloven.md")]


def test_a_tombstone_whose_file_is_gone_is_not_a_finding(tmp_path: Path) -> None:
    """A correctly-executed removal: tombstone retained, file deleted. Clean."""
    _seed(tmp_path, "skatteloven")
    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven"), nl2=_record("opphevet-lov", status="removed")),
    )

    assert report.findings == ()
    assert report.clean is True


def test_findings_are_sorted_so_the_report_is_stable(tmp_path: Path) -> None:
    """Byte-stable output: the audit is meant to be diffable across runs and
    gateable in CI, so two runs on the same corpus must agree exactly."""
    _seed(tmp_path, "b-lov", "a-lov", "c-lov")
    manifest = _manifest(nl1=_record("a-lov"))

    first = audit_corpus(tmp_path, manifest)
    second = audit_corpus(tmp_path, manifest)

    assert first == second
    assert _kinds(first.findings) == [
        ("orphan_document", "lover/b-lov.md"),
        ("orphan_document", "lover/c-lov.md"),
    ]


def test_documents_checked_counts_current_records_only(tmp_path: Path) -> None:
    _seed(tmp_path, "skatteloven")
    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven"), nl2=_record("opphevet", status="removed")),
    )

    assert report.documents_checked == 1


def test_history_files_are_never_reported_as_orphans(tmp_path: Path) -> None:
    """A correct removal keeps `history/<slug>.{json,md}` — it is the legal audit
    trail that the act existed and was repealed. Reporting it as drift would
    invite a cleanup that destroys exactly the evidence the corpus preserves.
    Pinned as a test because it is a tempting 'bug' for a future contributor.
    """
    _seed(tmp_path, "skatteloven")
    history = tmp_path / "lover" / "history"
    history.mkdir(parents=True)
    # History for a repealed act with no manifest record at all — the exact shape
    # left behind by the 48 orphans, and still not a finding.
    (history / "opphevet-lov.json").write_text("{}", encoding="utf-8")
    (history / "opphevet-lov.md").write_text("# history\n", encoding="utf-8")

    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert report.findings == ()
    assert report.clean is True


def test_a_missing_dataset_dir_does_not_stop_the_scan_of_the_others(tmp_path: Path) -> None:
    """From the Codex mutmut run on PR #136: a surviving `continue` -> `break`
    mutant in the dataset loop.

    The code was right but nothing pinned it — every other fixture here creates
    `lover/`, so no test exercised a corpus where the first dataset directory is
    absent. If that `continue` ever became a `break`, the audit would silently
    stop scanning and report a whole dataset as clean. A false negative in the
    tool whose entire purpose is catching false negatives.
    """
    # No `lover/` directory at all — only regulations on disk.
    (tmp_path / "forskrifter").mkdir()
    (tmp_path / "forskrifter" / "spøkelse.md").write_text("# orphan\n", encoding="utf-8")
    embeddings = tmp_path / "forskrifter" / "embeddings"
    embeddings.mkdir()
    (embeddings / "spøkelse.bin").write_bytes(b"\x00")

    report = audit_corpus(tmp_path, _manifest())

    assert _kinds(report.findings) == [
        ("orphan_document", "forskrifter/spøkelse.md"),
        ("orphan_embedding", "forskrifter/embeddings/spøkelse.bin"),
    ]


def test_index_md_is_never_reported_as_an_orphan_document(tmp_path: Path) -> None:
    """From Codex mutmut on PR #136: nothing pinned the INDEX.md exclusion.

    INDEX.md is generated metadata, not an act, and no manifest record claims it.
    If the exclusion ever broke, the audit would report it as an orphan in every
    dataset on every run — permanently non-clean, permanently exiting 1 in CI.
    An audit that always cries wolf is one nobody reads, which is how the 48
    orphans stayed invisible in the first place.
    """
    _seed(tmp_path, "skatteloven")
    (tmp_path / "lover" / "INDEX.md").write_text("# Lover\n\n_1 current documents_\n", "utf-8")

    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert report.findings == ()
    assert report.clean is True


def test_findings_and_reports_are_immutable(tmp_path: Path) -> None:
    """From Codex mutmut on PR #136: `model_config = {"frozen": True}` survived on
    both models — nothing pinned it.

    An audit result is evidence. A caller that can quietly mutate a finding, or
    append to `findings` after the fact, can make the corpus look clean without
    the corpus changing.
    """
    _seed(tmp_path, "skatteloven", "spøkelse")
    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))
    [finding] = report.findings

    with pytest.raises(ValidationError):
        finding.kind = "not_a_finding"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        report.findings = ()  # type: ignore[misc]


def test_a_finding_with_no_owning_record_carries_no_doc_id(tmp_path: Path) -> None:
    """From Codex mutmut on PR #136: the `doc_id: str | None = None` default
    survived being changed to `""`.

    An orphan has no manifest record by definition, so there is no doc_id to
    report. Empty-string would read as 'a document whose id is blank' rather than
    'no record exists' — a small lie about provenance, and this project treats a
    plausible-looking wrong value as worse than an absent one.
    """
    _seed(tmp_path, "skatteloven", "spøkelse")
    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))
    [orphan] = report.findings

    assert orphan.kind == "orphan_document"
    assert orphan.doc_id is None

    # A finding that DOES have an owning record reports it.
    _seed(tmp_path, "opphevet-lov")
    tombstoned = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven"), nl2=_record("opphevet-lov", status="removed")),
    )
    [f] = [x for x in tombstoned.findings if x.kind == "tombstoned_but_present"]
    assert f.doc_id == "nl2"


def test_flags_a_section_heading_the_grammar_cannot_parse(tmp_path: Path) -> None:
    """The round trip nobody checked: the renderer writes § headings, two
    parsers read them back, and a shape neither recognizes disappears with no
    warning. 2 347 headings were unreachable that way — arbeidsmiljøloven's
    whole kapittel 2 A among them — and the only symptom was an
    available-sections list that omitted them while reading as authoritative."""
    (tmp_path / "lover").mkdir(parents=True)
    (tmp_path / "lover" / "skatteloven.md").write_text(
        "# Skatteloven\n### § 5-12. Kjent form\n### § ø-1. Ukjent form\n",
        encoding="utf-8",
    )
    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert _kinds(report.findings) == [("unparsed_section_heading", "lover/skatteloven.md")]
    assert report.findings[0].detail == "line 3: ### § ø-1. Ukjent form"


@pytest.mark.parametrize(
    "heading",
    [
        "### § 8-7 a. Oppfølging mv. i regi av Arbeids- og velferdsetaten",
        "### § 2 A-1. Rett til å varsle om kritikkverdige forhold i virksomheten",
        "### § 17aa. Forbud mot å tilby lagringskapasitet",
        "## § 8.1",
        "### § 2 Plan og bygningslovens anvendelse",
        "###### § 10-4-1. Beløpsgrense for betaling",
    ],
)
def test_real_corpus_heading_shapes_produce_no_finding(tmp_path: Path, heading: str) -> None:
    (tmp_path / "lover").mkdir(parents=True)
    (tmp_path / "lover" / "skatteloven.md").write_text(f"# Lov\n{heading}\n", encoding="utf-8")
    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert report.findings == ()


# --- duplicate_path_ownership: one path, one owner, in both directions ---


def test_two_current_records_on_one_path_are_duplicate_ownership(tmp_path: Path) -> None:
    """Two *current* records on one path is worse than the tombstone case: both
    claim to be the file. No single owner exists, so the finding carries no
    doc_id — attaching either would be a guess about which record is right."""
    _seed(tmp_path, "skatteloven")
    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven"), nl2=_record("skatteloven")),
    )

    assert _kinds(report.findings) == [
        ("duplicate_path_ownership", "lover/skatteloven.md"),
    ]
    [finding] = report.findings
    assert finding.doc_id is None
    assert "2 manifest records resolve to this path" in finding.detail


def test_a_current_record_whose_slug_renders_elsewhere_is_duplicate_ownership(
    tmp_path: Path,
) -> None:
    """The mirror image: one doc id, two paths. A current record whose
    markdown_path disagrees with what its own slug renders to claims one file
    now and will write another on the next re-render — a half-applied rename."""
    _seed(tmp_path, "gammelt-navn")
    drifted = _record("nytt-navn").model_copy(update={"markdown_path": "lover/gammelt-navn.md"})
    report = audit_corpus(tmp_path, _manifest(nl1=drifted))

    assert _kinds(report.findings) == [
        ("duplicate_path_ownership", "lover/gammelt-navn.md"),
    ]
    [finding] = report.findings
    assert finding.doc_id == "nl1"
    assert "slug 'nytt-navn' renders to lover/nytt-navn.md" in finding.detail


def test_a_record_without_a_slug_cannot_be_split_ownership(tmp_path: Path) -> None:
    """Pre-Sprint-4 records carry no slug, so no second path can be derived for
    them — absence of evidence is not drift."""
    _seed(tmp_path, "skatteloven")
    slugless = _record("skatteloven").model_copy(update={"slug": None})
    report = audit_corpus(tmp_path, _manifest(nl1=slugless))

    assert report.findings == ()


def test_a_tombstone_pair_on_one_path_is_still_duplicate_ownership(tmp_path: Path) -> None:
    """'Any status mix' includes removed+removed: two tombstones fused on one
    path still poison every path-keyed consumer of the audit trail."""
    report = audit_corpus(
        tmp_path,
        _manifest(
            old=_record("omregningsfaktorer", status="removed"),
            older=_record("omregningsfaktorer", status="removed"),
        ),
    )

    assert _kinds(report.findings) == [
        ("duplicate_path_ownership", "lover/omregningsfaktorer.md"),
    ]
    assert report.findings[0].doc_id is None  # no current owner to name


# --- identity_mismatch: the file's own id versus the manifest's ---


def _seed_with_frontmatter(root: Path, slug: str, doc_id: str) -> None:
    (root / "lover").mkdir(parents=True, exist_ok=True)
    (root / "lover" / f"{slug}.md").write_text(
        f'---\nid: "{doc_id}"\ntype: "lov"\n---\n\n# {slug}\n',
        encoding="utf-8",
    )


def test_detects_a_frontmatter_id_that_contradicts_the_owning_record(tmp_path: Path) -> None:
    """The invariant the 2026-07-14 omregningsfaktorer overwrite skirted: a file
    whose frontmatter says it is one document, owned in the manifest by another.
    Whichever artifact is wrong, they cannot both be right — that is drift."""
    _seed_with_frontmatter(tmp_path, "skatteloven", doc_id="nl2")
    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert _kinds(report.findings) == [("identity_mismatch", "lover/skatteloven.md")]
    [finding] = report.findings
    assert finding.doc_id == "nl1"
    assert 'declares id "nl2"' in finding.detail


def test_a_matching_frontmatter_id_is_not_a_finding(tmp_path: Path) -> None:
    _seed_with_frontmatter(tmp_path, "skatteloven", doc_id="nl1")
    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert report.findings == ()


def test_a_file_declaring_no_id_is_not_an_identity_finding(tmp_path: Path) -> None:
    """Absence of evidence is not a mismatch. A file with no frontmatter id
    cannot be compared, and inventing a verdict for it would be a fabricated
    finding — this project treats those as worse than a missing one."""
    _seed(tmp_path, "skatteloven")  # seeds a bare '# skatteloven' body, no frontmatter

    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert report.findings == ()


def test_the_identity_check_only_reads_current_records(tmp_path: Path) -> None:
    """A tombstoned record's leftover file already surfaces as
    tombstoned_but_present; second-guessing its identity on top would double-
    report one drift event under two kinds."""
    _seed_with_frontmatter(tmp_path, "opphevet-lov", doc_id="noe-helt-annet")
    _seed_with_frontmatter(tmp_path, "skatteloven", doc_id="nl1")
    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven"), nl2=_record("opphevet-lov", status="removed")),
    )

    assert _kinds(report.findings) == [("tombstoned_but_present", "lover/opphevet-lov.md")]


def test_the_identity_scan_stays_within_its_line_budget(tmp_path: Path) -> None:
    """The check reads the head of each file, never whole acts. An id line
    buried past the scan window is treated as absent, not streamed for."""
    (tmp_path / "lover").mkdir(parents=True)
    filler = "\n".join(f"line {i}" for i in range(60))
    (tmp_path / "lover" / "skatteloven.md").write_text(
        f'{filler}\nid: "nl2"\n',
        encoding="utf-8",
    )

    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert report.findings == ()


# --- severity: integrity blocks, advisory informs ---


def test_findings_split_into_integrity_and_advisory(tmp_path: Path) -> None:
    """One corpus with both flavors of drift: the orphan is corruption, the
    unparsed heading is registered follow-up work. The report must keep them
    apart so a CI gate can block on the first without going permanently red
    over the second."""
    (tmp_path / "lover").mkdir(parents=True)
    (tmp_path / "lover" / "skatteloven.md").write_text(
        "# Skatteloven\n### § ø-1. Ukjent form\n",
        encoding="utf-8",
    )
    (tmp_path / "lover" / "spøkelse.md").write_text("# orphan\n", encoding="utf-8")

    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert [f.kind for f in report.integrity_findings] == ["orphan_document"]
    assert [f.kind for f in report.advisory_findings] == ["unparsed_section_heading"]
    assert report.clean is False


def test_an_unregistered_kind_classifies_as_integrity() -> None:
    """Fail closed: a future check nobody added to ADVISORY_KINDS must block,
    not slip through as informational."""
    report = AuditReport(
        corpus_root="/x",
        documents_checked=0,
        findings=(AuditFinding(kind="brand_new_check", path="lover/x.md"),),
    )

    assert report.integrity_findings == report.findings
    assert report.advisory_findings == ()


def test_the_severity_registers_are_disjoint_and_cover_every_emitted_kind() -> None:
    """A kind in both sets would make severity ambiguous; a kind in neither is
    the fail-closed case tested above — but every kind the module actually
    emits today should be deliberately registered, not incidentally blocking."""
    emitted = {
        "duplicate_path_ownership",
        "identity_mismatch",
        "malformed_language",
        "missing_document",
        "orphan_document",
        "orphan_embedding",
        "stale_render",
        "tombstoned_but_present",
        "unparsed_section_heading",
    }

    assert not INTEGRITY_KINDS & ADVISORY_KINDS
    assert emitted == INTEGRITY_KINDS | ADVISORY_KINDS


# --- malformed_language: advisory guard for constrained-semantics frontmatter ---


def _seed_with_language(root: Path, slug: str, language_line: str | None) -> None:
    (root / "lover").mkdir(parents=True, exist_ok=True)
    lines = ["---", f'id: "{slug}"']
    if language_line is not None:
        lines.append(language_line)
    lines += ["---", "", f"# {slug}", ""]
    (root / "lover" / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")


def test_malformed_language_is_flagged_as_advisory(tmp_path: Path) -> None:
    _seed_with_language(
        tmp_path,
        "broken",
        'language: "nb<br/>nr. 5czn (direktiv <a href="',
    )
    report = audit_corpus(tmp_path, _manifest(broken=_record("broken")))
    kinds = [f.kind for f in report.findings]
    assert kinds == ["malformed_language"]
    assert report.integrity_findings == ()
    finding = report.advisory_findings[0]
    assert finding.path == "lover/broken.md"
    assert "nb<br/>" in finding.detail


def test_valid_and_empty_language_produce_no_finding(tmp_path: Path) -> None:
    _seed_with_language(tmp_path, "valid", 'language: "nb"')
    _seed_with_language(tmp_path, "blank", 'language: ""')
    _seed_with_language(tmp_path, "absent", None)
    report = audit_corpus(
        tmp_path,
        _manifest(
            valid=_record("valid"),
            blank=_record("blank"),
            absent=_record("absent"),
        ),
    )
    assert [f.kind for f in report.findings] == []


def test_malformed_language_detail_truncates_long_values(tmp_path: Path) -> None:
    _seed_with_language(tmp_path, "long", f'language: "{"x" * 200}<{"y" * 40}"')
    report = audit_corpus(tmp_path, _manifest(long=_record("long")))
    (finding,) = report.findings
    assert finding.kind == "malformed_language"
    assert len(finding.detail) < 140
