"""The envelope's on-disk shape: fragment, record, marker, link pass (ADR-0014 Decision 6)."""

import json
import os
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.release.envelope import (
    BUILD_PREFIX,
    CORPUS_PATHS,
    FRAGMENT_NAME,
    MARKER_NAME,
    RECORD_NAME,
    RELEASE_VAR,
    CorpusSummary,
    Marker,
    ReleaseRecord,
    fragment_paths,
    fragment_release_id,
    fragment_text,
    is_build_dir,
    is_complete,
    is_release_id,
    missing_parts,
    read_fragment,
    read_marker,
    read_record,
    release_dir,
    write_fragment,
    write_marker,
    write_record,
)
from lovspor.release.errors import IncompleteEnvelopeError, ReleaseError
from lovspor.release.linking import hardlink_unchanged
from lovspor.site.fingerprint import ReleaseKey

ID_A = "a" * 64
ID_B = "b" * 64


def _record(content_id: str = ID_A) -> ReleaseRecord:
    return ReleaseRecord(
        schema_version="1",
        release_content_id=content_id,
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


def _complete(root: Path, content_id: str = ID_A) -> Path:
    (root / "corpus").mkdir(parents=True)
    (root / "site").mkdir()
    write_record(root, _record(content_id))
    write_fragment(root, fragment_text(root, content_id))
    return root


class TestNames:
    def test_a_release_id_is_sixty_four_hex_digits(self) -> None:
        assert is_release_id(ID_A)
        assert not is_release_id(ID_A[:-1])
        assert not is_release_id(ID_A.upper())
        assert not is_release_id("20260908T120000Z-abcdef123456")

    def test_a_build_name_is_never_a_release_id(self, tmp_path: Path) -> None:
        build = tmp_path / f"{BUILD_PREFIX}{ID_A}"
        build.mkdir()

        assert is_build_dir(build)
        assert not is_release_id(build.name)
        assert not is_build_dir(tmp_path / ID_A)

    def test_release_dir_is_the_id_under_the_releases_root(self, tmp_path: Path) -> None:
        assert release_dir(tmp_path, ID_A) == tmp_path / ID_A


class TestFragment:
    def test_generated_fragment_is_byte_exact(self) -> None:
        """The comment lines are the operator's only warning; the directives are Caddy's."""
        root = Path("/var/www/lovspor-releases") / ID_A

        assert fragment_text(root, ID_A) == (
            f"# lovspor release {ID_A} — written by `lovspor release build`"
            " (ADR-0014 Decision 6). Do not edit.\n"
            "# Imported into the site block by the host's Caddyfile. Every path below names one\n"
            "# immutable release directory, so the configuration is one release, never a mix.\n"
            f"vars lovspor_release {ID_A}\n"
            "@lovspor_corpus path /lov /lov/* /forskrift /forskrift/* /sitemap.xml /sitemaps/*"
            " /robots.txt /site-manifest.json\n"
            "handle @lovspor_corpus {\n"
            f"\timport {root}/corpus/redirects*.caddy\n"
            f"\troot * {root}/corpus\n"
            "\tfile_server\n"
            "}\n"
            "handle {\n"
            f"\troot * {root}/site\n"
            "\tfile_server\n"
            "}\n"
        )

    def test_carries_the_vars_directive_and_only_absolute_immutable_paths(self) -> None:
        text = fragment_text(Path("/var/www/lovspor-releases") / ID_A, ID_A)

        assert f"vars {RELEASE_VAR} {ID_A}" in text.splitlines()
        assert fragment_release_id(text) == ID_A
        assert fragment_paths(text) == (
            f"/var/www/lovspor-releases/{ID_A}/corpus/redirects*.caddy",
            f"/var/www/lovspor-releases/{ID_A}/corpus",
            f"/var/www/lovspor-releases/{ID_A}/site",
        )
        assert "lovspor-current" not in text
        assert "{$" not in text

    def test_the_corpus_matcher_is_adr_0013s_plus_the_manifest(self) -> None:
        text = fragment_text(Path("/r") / ID_A, ID_A)

        (matcher,) = [line for line in text.splitlines() if line.startswith("@lovspor_corpus")]
        assert matcher == "@lovspor_corpus path " + " ".join(CORPUS_PATHS)
        assert "/site-manifest.json" in CORPUS_PATHS
        assert "/robots.txt" in CORPUS_PATHS
        assert "handle @lovspor_corpus {" in text
        assert re.search(r"^handle \{$", text, re.MULTILINE)

    def test_the_corpus_map_is_imported_from_the_release_and_the_roots_are_ordered(self) -> None:
        text = fragment_text(Path("/r") / ID_A, ID_A)
        lines = [line.strip() for line in text.splitlines()]

        corpus_handle = lines.index("handle @lovspor_corpus {")
        assert lines[corpus_handle + 1] == f"import /r/{ID_A}/corpus/redirects*.caddy"
        assert lines[corpus_handle + 2] == f"root * /r/{ID_A}/corpus"
        assert lines[corpus_handle + 3] == "file_server"
        site_handle = lines.index("handle {")
        assert lines[site_handle + 1] == f"root * /r/{ID_A}/site"
        assert lines[site_handle + 2] == "file_server"

    @pytest.mark.parametrize(
        "text",
        [
            "root * /r/x/corpus\n",
            f"vars {RELEASE_VAR} {ID_A}\nvars {RELEASE_VAR} {ID_B}\n",
            f"vars {RELEASE_VAR} not-an-id\n",
            f"vars other {ID_A}\n",
        ],
    )
    def test_anything_but_exactly_one_valid_vars_line_has_no_id(self, text: str) -> None:
        assert fragment_release_id(text) is None

    def test_round_trips_through_the_envelope(self, tmp_path: Path) -> None:
        write_fragment(tmp_path, fragment_text(tmp_path / ID_A, ID_A))

        assert read_fragment(tmp_path) == fragment_text(tmp_path / ID_A, ID_A)
        assert not (tmp_path / f"{FRAGMENT_NAME}.tmp").exists()

    def test_is_read_as_utf_8_whatever_the_process_locale(
        self, tmp_path: Path, c_locale: None
    ) -> None:
        """The header's em dash is outside ASCII; a root unit without LANG still reads it."""
        text = fragment_text(tmp_path / ID_A, ID_A)
        (tmp_path / FRAGMENT_NAME).write_bytes(text.encode("utf-8"))

        assert read_fragment(tmp_path) == text

    def test_an_absent_fragment_is_an_incomplete_envelope(self, tmp_path: Path) -> None:
        with pytest.raises(IncompleteEnvelopeError, match=FRAGMENT_NAME):
            read_fragment(tmp_path)


class TestRecord:
    def test_round_trips_with_sorted_keys_and_no_builder_clock(self, tmp_path: Path) -> None:
        write_record(tmp_path, _record())

        raw = (tmp_path / RECORD_NAME).read_bytes()
        assert read_record(tmp_path) == _record()
        assert list(json.loads(raw)) == sorted(json.loads(raw))
        assert set(json.loads(raw)) == {
            "schema_version",
            "release_content_id",
            "release_key",
            "site_manifest_sha256",
            "capability_sha256",
            "observed_at",
            "observer",
            "corpus",
        }

    def test_an_unknown_field_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ReleaseRecord.model_validate({**_record().model_dump(), "built_at": "now"})

    def test_a_missing_or_malformed_record_is_an_incomplete_envelope(self, tmp_path: Path) -> None:
        with pytest.raises(IncompleteEnvelopeError, match="unreadable"):
            read_record(tmp_path)
        (tmp_path / RECORD_NAME).write_text("{}", encoding="utf-8")
        with pytest.raises(IncompleteEnvelopeError, match="not a release record"):
            read_record(tmp_path)


class TestCompleteness:
    def test_both_trees_the_record_and_the_fragment_are_required(self, tmp_path: Path) -> None:
        assert missing_parts(tmp_path) == ("corpus/", "site/", RECORD_NAME, FRAGMENT_NAME)
        assert not is_complete(tmp_path)

        _complete(tmp_path)

        assert is_complete(tmp_path)
        assert missing_parts(tmp_path) == ()

    @pytest.mark.parametrize("part", ["corpus", "site", RECORD_NAME, FRAGMENT_NAME])
    def test_each_part_alone_makes_the_envelope_incomplete(self, tmp_path: Path, part: str) -> None:
        _complete(tmp_path)
        target = tmp_path / part
        if target.is_dir():
            target.rmdir()
        else:
            target.unlink()

        assert not is_complete(tmp_path)
        assert missing_parts(tmp_path) == (part + "/" if part in {"corpus", "site"} else part,)


class TestMarker:
    def test_absent_means_nothing_was_ever_live(self, tmp_path: Path) -> None:
        assert read_marker(tmp_path) is None

    def test_round_trips_by_atomic_rename(self, tmp_path: Path) -> None:
        write_marker(tmp_path, Marker(active=ID_A, previous=None))
        write_marker(tmp_path, Marker(active=ID_B, previous=ID_A))

        assert read_marker(tmp_path) == Marker(active=ID_B, previous=ID_A)
        assert (tmp_path / MARKER_NAME).read_bytes() == (
            b'{\n "active": "' + ID_B.encode() + b'",\n "previous": "' + ID_A.encode() + b'"\n}\n'
        )
        assert not (tmp_path / f"{MARKER_NAME}.tmp").exists()

    def test_a_malformed_marker_is_refused_not_guessed(self, tmp_path: Path) -> None:
        (tmp_path / MARKER_NAME).write_text('{"active": "x"}', encoding="utf-8")
        with pytest.raises(IncompleteEnvelopeError, match="not a marker"):
            read_marker(tmp_path)

    @pytest.mark.xfail(strict=True, reason="codex proposal, round 4 — owner decision, see #248")
    @pytest.mark.xfail(strict=True, reason="codex proposal, round 4 — owner decision, see #248")
    @pytest.mark.parametrize(
        "marker",
        [
            {"active": "not-a-release-id", "previous": None},
            {"active": ID_A, "previous": "../outside-the-releases-root"},
        ],
    )
    def test_release_ids_in_a_marker_must_be_valid(
        self, tmp_path: Path, marker: dict[str, str | None]
    ) -> None:
        """ACTIVE names finalized release-id directories, never arbitrary paths."""
        (tmp_path / MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")

        with pytest.raises(IncompleteEnvelopeError, match="not a marker"):
            read_marker(tmp_path)

    def test_the_marker_carries_no_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            Marker.model_validate({"active": ID_A, "previous": None, "current": ID_A})


class TestHardlinkUnchanged:
    def _trees(self, tmp_path: Path) -> tuple[Path, Path]:
        live = tmp_path / "live"
        build = tmp_path / "build"
        for root in (live, build):
            (root / "corpus" / "lov").mkdir(parents=True)
            (root / "corpus" / "lov" / "same.html").write_bytes(b"same")
            (root / "corpus" / "lov" / "changed.html").write_bytes(root.name.encode())
        (build / "corpus" / "new.html").write_bytes(b"new")
        (live / "corpus" / "gone.html").write_bytes(b"gone")
        return build, live

    def test_links_only_byte_identical_files_at_the_same_path(self, tmp_path: Path) -> None:
        build, live = self._trees(tmp_path)
        os.utime(live / "corpus" / "lov" / "same.html", (1_700_000_000, 1_700_000_000))
        for root in (live, build):
            (root / "corpus" / "robots.txt").write_bytes(b"User-agent: *\n")

        linked = hardlink_unchanged(build, live)

        assert linked == 2
        same = build / "corpus" / "lov" / "same.html"
        assert same.stat().st_ino == (live / "corpus" / "lov" / "same.html").stat().st_ino
        assert same.stat().st_mtime == 1_700_000_000
        assert (build / "corpus" / "robots.txt").stat().st_nlink == 2
        assert (build / "corpus" / "lov" / "changed.html").read_bytes() == b"build"
        assert (build / "corpus" / "lov" / "changed.html").stat().st_nlink == 1
        assert (build / "corpus" / "new.html").stat().st_nlink == 1
        assert not (build / "corpus" / "gone.html").exists()
        assert not list(build.rglob("*.link"))

    def test_a_same_size_different_content_file_is_not_linked(self, tmp_path: Path) -> None:
        build, live = self._trees(tmp_path)
        (build / "corpus" / "lov" / "changed.html").write_bytes(b"live!")

        hardlink_unchanged(build, live)

        assert (build / "corpus" / "lov" / "changed.html").read_bytes() == b"live!"
        assert (build / "corpus" / "lov" / "changed.html").stat().st_nlink == 1

    def test_counts_every_linked_file(self, tmp_path: Path) -> None:
        build, live = self._trees(tmp_path)
        for root in (build, live):
            (root / "corpus" / "second.html").write_bytes(b"also same")

        assert hardlink_unchanged(build, live) == 2

    def test_symlinks_are_neither_followed_nor_linked(self, tmp_path: Path) -> None:
        build, live = self._trees(tmp_path)
        (build / "corpus" / "alias.html").symlink_to(live / "corpus" / "gone.html")
        (live / "corpus" / "alias.html").write_bytes(b"gone")
        (live / "corpus" / "lov" / "linked.html").symlink_to(live / "corpus" / "gone.html")
        (build / "corpus" / "lov" / "linked.html").write_bytes(b"gone")

        linked = hardlink_unchanged(build, live)

        assert linked == 1
        assert (build / "corpus" / "alias.html").is_symlink()
        assert (live / "corpus" / "alias.html").stat().st_nlink == 1
        assert (build / "corpus" / "lov" / "linked.html").stat().st_nlink == 1

    def test_an_unlinkable_twin_is_a_named_refusal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        build, live = self._trees(tmp_path)

        def refuse_link(source: Path, destination: Path) -> None:
            del source, destination
            raise PermissionError("read-only filesystem")

        monkeypatch.setattr("lovspor.release.linking.os.link", refuse_link)

        with pytest.raises(ReleaseError, match="cannot hard-link.*read-only filesystem"):
            hardlink_unchanged(build, live)

        assert not list(build.rglob("*.link"))
