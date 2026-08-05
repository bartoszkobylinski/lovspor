"""Act-name index: mechanical key derivation, exact lookup, longest-match scan."""

from datetime import UTC, datetime

from lovspor.llhb.names import ActNameIndex, normalize_name
from lovspor.storage.manifest import Manifest, ManifestRecord


def _record(slug: str, title: str, status: str = "current") -> ManifestRecord:
    return ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path=f"lover/{slug}.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 27, tzinfo=UTC),
        status=status,  # type: ignore[arg-type]
        slug=slug,
        title=title,
    )


def _manifest(records: dict[str, ManifestRecord]) -> Manifest:
    return Manifest(generated_at=datetime(2026, 8, 5, tzinfo=UTC), documents=records)


def test_normalize_name_folds_case_whitespace_and_nfkc() -> None:
    assert normalize_name("  Arbeidsmiljøloven ") == "arbeidsmiljøloven"
    assert normalize_name("Lov om \t skatt") == "lov om skatt"


def test_index_keys_slug_title_and_law_name_parenthetical() -> None:
    index = ActNameIndex.from_manifest(
        _manifest(
            {
                "nl-1": _record(
                    "skatteloven-sktl",
                    "Lov om skatt av formue og inntekt (skatteloven)",
                ),
            },
        ),
    )
    for name in (
        "skatteloven-sktl",
        "skatteloven",
        "Lov om skatt av formue og inntekt (skatteloven)",
    ):
        entries = index.lookup(name)
        assert [e.slug for e in entries] == ["skatteloven-sktl"], name


def test_non_law_parentheticals_are_not_keys() -> None:
    index = ActNameIndex.from_manifest(
        _manifest({"nl-1": _record("x-loven", "Lov om x (forkortet xl.) (x-stuff)")}),
    )
    assert index.lookup("forkortet xl.") == []
    assert index.lookup("x-stuff") == []


def test_lookup_is_exact_never_fuzzy() -> None:
    index = ActNameIndex.from_pairs([("arbeidsmiljøloven", "arbeidsmiljøloven")])
    assert index.lookup("arbeidsmiljølov") == []
    assert index.lookup("arbeidsmiljøloven x") == []


def test_shared_name_yields_all_candidates() -> None:
    index = ActNameIndex.from_manifest(
        _manifest(
            {
                "nl-1": _record("gammel-loven", "Lov om y (samleloven)", status="removed"),
                "nl-2": _record("ny-loven", "Lov om z (samleloven)"),
            },
        ),
    )
    assert {(e.slug, e.status) for e in index.lookup("samleloven")} == {
        ("gammel-loven", "removed"),
        ("ny-loven", "current"),
    }


def test_scan_finds_leftmost_longest_word_bounded_mentions() -> None:
    index = ActNameIndex.from_pairs(
        [("skatteloven", "skatteloven-sktl"), ("skatteloven-sktl", "skatteloven-sktl")],
    )
    mentions = index.scan("Etter skatteloven-sktl og Skatteloven gjelder dette.")
    assert [(m.text, m.key) for m in mentions] == [
        ("skatteloven-sktl", "skatteloven-sktl"),
        ("Skatteloven", "skatteloven"),
    ]


def test_scan_never_matches_inside_longer_tokens() -> None:
    index = ActNameIndex.from_pairs([("skatteloven", "skatteloven-sktl")])
    assert index.scan("skattelovenX og skatteloven-annet") == []


def test_empty_index_scans_nothing() -> None:
    assert ActNameIndex.from_pairs([]).scan("skatteloven § 5") == []
