"""``scripts/measure_corpus_quality.py`` (issue #624): the three measurements, on a tiny corpus.

The script is loaded from its path like ``test_audit_render_bytes.py`` does:
it reads the engine's output but is not part of the engine.
"""

import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "measure_corpus_quality.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("measure_corpus_quality", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


quality = _load_script()


def _doc(in_force: str | None, last_change: str | None, body: str) -> str:
    def scalar(value: str | None) -> str:
        return "null" if value is None else f'"{value}"'

    return (
        "---\n"
        'type: "lov"\n'
        "ministry:\n"
        '  - "Justisdepartementet"\n'
        f"date_in_force: {scalar(in_force)}\n"
        f"last_change_in_force: {scalar(last_change)}\n"
        "---\n"
        f"# Lov\n\n{body}\n"
    )


def _corpus(root: Path) -> Path:
    (root / "lover").mkdir(parents=True)
    docs = {
        "nl-20200101-001": ("lover/a.md", "2021-01-01", "2020-12-15", "Amtskasserere[^1] betaler."),
        "nl-20200101-002": ("lover/b.md", None, None, "Se vedlegg1 og CO2 [^2] her."),
        "nl-20200101-003": ("lover/c.md", "2020-01-01", "2022-01-01", "Ingenting her."),
        "nl-20200101-004": ("lover/d.md", "2020-01-01", None, "Fjernet."),
    }
    manifest = {"documents": {}}
    for doc_id, (path, in_force, last_change, body) in docs.items():
        (root / path).write_text(_doc(in_force, last_change, body), encoding="utf-8")
        status = "removed" if doc_id.endswith("004") else "current"
        record = {"markdown_path": path, "status": status, "doc_type": "lov", "title": doc_id}
        manifest["documents"][doc_id] = record
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _tarball(cache: Path, headers: dict[str, str | None]) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    with tarfile.open(cache / "gjeldende-lover.tar.bz2", mode="w:bz2") as archive:
        for doc_id, in_force in headers.items():
            dd = "" if in_force is None else f'<dd class="dateInForce">{in_force}</dd>'
            payload = f"<doc><header><dl>{dd}</dl></header></doc>".encode()
            info = tarfile.TarInfo(name=f"nl/{doc_id}.xml")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


class TestMeasure:
    def test_it_measures_current_documents_only_in_id_order(self, tmp_path: Path) -> None:
        found = quality.measure(_corpus(tmp_path / "lovverk"))

        assert found.current == 3
        assert found.reversed_dates == [
            {
                "id": "nl-20200101-001",
                "path": "lover/a.md",
                "date_in_force": "2021-01-01",
                "last_change": "2020-12-15",
            }
        ]
        assert found.undated == [
            {"id": "nl-20200101-002", "type": "lov", "title": "nl-20200101-002"}
        ]

    def test_markers_and_glued_shapes_are_counted(self, tmp_path: Path) -> None:
        found = quality.measure(_corpus(tmp_path / "lovverk"))

        assert (found.markers, found.attached_markers) == (2, 1)
        assert dict(found.glued_shape) == {"vedlegg1": 1}
        assert found.glued_shape_docs == 1

    def test_front_matter_without_the_fence_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no front matter"):
            quality.split_front_matter("# no fence\n")


class TestDateFieldClasses:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, quality.NO_FIELD),
            ("Kongen bestemmer", quality.TEXT_ONLY),
            ("Kongen bestemmer, 2004-01-01, 2005-01-01", quality.TEXT_THEN_DATES),
            ("2015-01-01, 2012-02-01", quality.LEADING_DATE),
        ],
    )
    def test_the_renderers_leading_date_rule_names_the_class(
        self, raw: str | None, expected: str
    ) -> None:
        assert quality.classify_date_field(raw) == expected

    def test_undated_rows_are_classified_from_the_cached_tarball(self, tmp_path: Path) -> None:
        corpus = _corpus(tmp_path / "lovverk")
        _tarball(tmp_path / "cache", {"nl-20200101-002": "Kongen bestemmer, 2021-01-01"})
        found = quality.measure(corpus)

        classes = quality.classify_undated(found, tmp_path / "cache")

        assert dict(classes) == {quality.TEXT_THEN_DATES: 1}
        assert found.undated[0]["xml_date_in_force"] == "Kongen bestemmer, 2021-01-01"

    def test_an_id_the_tarball_lacks_says_so(self, tmp_path: Path) -> None:
        corpus = _corpus(tmp_path / "lovverk")
        _tarball(tmp_path / "cache", {"nl-19990101-009": None})
        found = quality.measure(corpus)

        classes = quality.classify_undated(found, tmp_path / "cache")

        assert dict(classes) == {"not in the cached tarball": 1}


class TestTheCommand:
    def test_it_prints_the_report_and_writes_the_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        corpus = _corpus(tmp_path / "lovverk")
        _tarball(tmp_path / "cache", {"nl-20200101-002": None})
        out = tmp_path / "out.json"

        code = quality.main(
            [str(corpus), "--xml-cache", str(tmp_path / "cache"), "--json", str(out)]
        )

        assert code == 0
        printed = capsys.readouterr().out
        assert "current documents: 3" in printed
        assert "1. last_change_in_force before date_in_force: 1" in printed
        assert "   nl-20200101-001  2020-12-15 < 2021-01-01  lover/a.md" in printed
        assert "2. no date_in_force and no last_change_in_force: 1" in printed
        assert f"1  {quality.NO_FIELD}" in printed
        assert "3. [^n] markers: 2 (1 flush against a word)" in printed
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["undated_classes"] == {quality.NO_FIELD: 1}
        assert payload["glued_shape_top"] == [["vedlegg1", 1]]


class TestMeasurementBoundaries:
    @pytest.mark.parametrize(
        ("in_force", "last_change"),
        [("2020-01-01", "2020-01-01"), (None, "2020-01-01"), ("2020-01-01", None)],
    )
    def test_equal_dates_and_one_known_date_are_neither_reversed_nor_undated(
        self, in_force: str | None, last_change: str | None
    ) -> None:
        found = quality.Measurement()
        quality.measure_document("nl-example", {}, _doc(in_force, last_change, ""), found)

        assert found.current == 1
        assert found.reversed_dates == []
        assert found.undated == []

    def test_markers_are_counted_only_in_body_and_attachment_requires_a_letter(self) -> None:
        text = _doc("2020-01-01", None, "æ[^1] 2[^2] _[^3] .[^4] [^5] vedlegg1 vedlegg1")
        text = text.replace('type: "lov"', 'title: "ord[^99] vedlegg9"')
        found = quality.Measurement()

        quality.measure_document("nl-example", {}, text, found)

        assert (found.markers, found.attached_markers) == (5, 1)
        assert dict(found.glued_shape) == {"vedlegg1": 2}
        assert found.glued_shape_docs == 1
        assert found.as_json()["glued_shape_tokens"] == 2

    def test_measurement_is_repeatable_and_independent_of_manifest_order(
        self, tmp_path: Path
    ) -> None:
        corpus = _corpus(tmp_path / "lovverk")
        manifest_path = corpus / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # Make multiple rows in each list, so ordering is observable.
        (corpus / "lover/c.md").write_text(_doc(None, None, "vedlegg2"), encoding="utf-8")
        record = dict(manifest["documents"]["nl-20200101-001"])
        manifest["documents"]["nl-20200101-005"] = record
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        first = quality.measure(corpus).as_json()
        manifest["documents"] = dict(reversed(list(manifest["documents"].items())))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        assert quality.measure(corpus).as_json() == first
        assert quality.measure(corpus).as_json() == first
        assert [row["id"] for row in first["reversed_dates"]] == [
            "nl-20200101-001",
            "nl-20200101-005",
        ]
        assert [row["id"] for row in first["undated"]] == [
            "nl-20200101-002",
            "nl-20200101-003",
        ]

    def test_both_tarballs_are_read_without_extracting_members(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = tmp_path / "cache"
        _tarball(cache, {"nl-example": "Kongen bestemmer"})
        with tarfile.open(cache / "gjeldende-sentrale-forskrifter.tar.bz2", "w:bz2") as archive:
            payload = (
                b'<dd class="dateInForce">2020-01-01</dd>'
                b'<dd class="lastChangeInForce">2021-01-01</dd>'
            )
            member = tarfile.TarInfo("sf/sf-example.xml")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
            link = tarfile.TarInfo("sf/linked.xml")
            link.type = tarfile.SYMTYPE
            link.linkname = "sf-example.xml"
            archive.addfile(link)

        def refuse_extraction(*args: object, **kwargs: object) -> None:
            pytest.fail("measurement must read members without extracting to disk")

        monkeypatch.setattr(tarfile.TarFile, "extract", refuse_extraction)
        monkeypatch.setattr(tarfile.TarFile, "extractall", refuse_extraction)
        before = {p.relative_to(cache): p.read_bytes() for p in cache.iterdir()}

        assert quality.header_dates(cache, {"nl-example", "sf-example", "linked"}) == {
            "nl-example": {"dateInForce": "Kongen bestemmer"},
            "sf-example": {"dateInForce": "2020-01-01", "lastChangeInForce": "2021-01-01"},
        }
        assert {p.relative_to(cache): p.read_bytes() for p in cache.iterdir()} == before

    def test_empty_manifest_produces_zero_counts(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text('{"documents": {}}', encoding="utf-8")

        assert quality.measure(tmp_path).as_json() == {
            "current": 0,
            "reversed_dates": [],
            "undated": [],
            "markers": 0,
            "attached_markers": 0,
            "glued_shape_tokens": 0,
            "glued_shape_docs": 0,
            "glued_shape_top": [],
        }
