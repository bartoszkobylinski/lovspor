"""``publish-check`` for the envelope (ADR-0014 Validation, envelope tests a, b, e, g).

One envelope is built once; every test copies it and breaks exactly one
thing, because the check's job is to name the one thing that is wrong.
"""

import json
import shutil
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from lovspor.release.check import EnvelopeReport, check_envelope, check_structure
from lovspor.release.envelope import (
    BUILD_PREFIX,
    FRAGMENT_NAME,
    RECORD_NAME,
    fragment_text,
    write_fragment,
)
from lovspor.release.errors import EnvelopeError, IncompleteEnvelopeError
from tests.unit.release_fixtures import World, build, make_world

FACTS = "site/site-facts.json"
CAPABILITIES = "site/deployment-capabilities.json"
MANIFEST = "corpus/site-manifest.json"


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return make_world(tmp_path_factory.mktemp("world"))


@pytest.fixture(scope="module")
def built(world: World, tmp_path_factory: pytest.TempPathFactory) -> Path:
    releases = tmp_path_factory.mktemp("built") / "releases"
    return releases / build(world, releases).release_content_id


@pytest.fixture
def copies(built: Path) -> Iterator[Callable[[str], Path]]:
    """Copies of the built envelope beside it — the fragment names that root — removed after."""
    made: list[Path] = []

    def copy_as(name: str) -> Path:
        copy = built.parent / name
        shutil.copytree(built, copy)
        made.append(copy)
        return copy

    yield copy_as
    for copy in made:
        shutil.rmtree(copy, ignore_errors=True)


@pytest.fixture
def envelope(copies: Callable[[str], Path]) -> Path:
    """This test's own copy, under a build name: the check runs before the rename."""
    return copies(f"{BUILD_PREFIX}{uuid.uuid4().hex}")


def _json(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_bytes())
    return payload


def _rewrite(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


class TestAPassingEnvelope:
    def test_passes_under_a_build_name_and_under_its_id(self, built: Path, envelope: Path) -> None:
        report = check_envelope(envelope)

        assert isinstance(report, EnvelopeReport)
        assert report.release_content_id == built.name
        assert report.corpus.documents == 1
        assert report.site_pages > 0
        assert check_envelope(built).release_content_id == built.name
        assert "envelope ok" in report.summary()
        assert built.name[:12] in report.summary()

    def test_the_structural_check_reads_the_key_the_facts_carry(self, envelope: Path) -> None:
        structure = check_structure(envelope)

        assert structure.release_key.model_dump() == _json(envelope / FACTS)["release_key"]
        assert structure.manifest.corpus_commit == _json(envelope / FACTS)["corpus_commit"]
        assert structure.document.state.hosted_state == "available"

    def test_the_pages_are_read_as_utf_8_whatever_the_process_locale(
        self, built: Path, c_locale: None
    ) -> None:
        """Every page carries the chrome's non-ASCII text; a root unit without LANG scans it."""
        assert check_envelope(built).release_content_id == built.name


class TestTwoTrees:
    def test_an_empty_directory_is_missing_both_trees_and_nothing_else_yet(
        self, tmp_path: Path
    ) -> None:
        """Step 2 runs before the id files exist, so only the trees are named."""
        empty = tmp_path / f"{BUILD_PREFIX}empty"
        empty.mkdir()

        with pytest.raises(IncompleteEnvelopeError) as caught:
            check_envelope(empty)
        assert str(caught.value) == f"{BUILD_PREFIX}empty: missing corpus/, site/"

    def test_a_corpus_without_a_site_is_refused(self, envelope: Path) -> None:
        shutil.rmtree(envelope / "site")

        with pytest.raises(IncompleteEnvelopeError, match="missing site/"):
            check_envelope(envelope)

    def test_a_site_without_a_corpus_is_refused(self, envelope: Path) -> None:
        shutil.rmtree(envelope / "corpus")

        with pytest.raises(IncompleteEnvelopeError, match="missing corpus/"):
            check_envelope(envelope)

    @pytest.mark.parametrize("name", [RECORD_NAME, FRAGMENT_NAME])
    def test_an_id_named_directory_without_its_id_files_is_incomplete_never_a_pass(
        self, copies: Callable[[str], Path], name: str
    ) -> None:
        copy = copies("2" * 64)
        (copy / name).unlink()

        with pytest.raises(IncompleteEnvelopeError, match=f"missing {name}"):
            check_envelope(copy)

    def test_an_id_named_directory_without_both_id_files_names_both(
        self, copies: Callable[[str], Path]
    ) -> None:
        copy = copies("3" * 64)
        (copy / RECORD_NAME).unlink()
        (copy / FRAGMENT_NAME).unlink()

        with pytest.raises(IncompleteEnvelopeError) as caught:
            check_envelope(copy)
        assert str(caught.value) == f"{'3' * 64}: missing {RECORD_NAME}, {FRAGMENT_NAME}"

    @pytest.mark.parametrize("name", ["site-facts.json", "sitemap-site.xml"])
    def test_a_site_root_file_is_required(self, envelope: Path, name: str) -> None:
        (envelope / "site" / name).unlink()

        with pytest.raises(EnvelopeError, match=f"site/{name} is missing"):
            check_envelope(envelope)

    def test_a_missing_manifest_is_named_as_unreadable(self, envelope: Path) -> None:
        (envelope / MANIFEST).unlink()

        with pytest.raises(EnvelopeError) as caught:
            check_envelope(envelope)
        assert str(caught.value).startswith(f"{MANIFEST} is unreadable: ")
        assert str(envelope / MANIFEST) in str(caught.value)

    def test_a_manifest_that_is_not_a_release_manifest_is_refused(self, envelope: Path) -> None:
        (envelope / MANIFEST).write_text("{}", encoding="utf-8")

        with pytest.raises(EnvelopeError) as caught:
            check_envelope(envelope)
        assert str(caught.value) == f"{MANIFEST} is not a release manifest"

    def test_a_facts_file_that_is_not_json_is_refused(self, envelope: Path) -> None:
        (envelope / FACTS).write_text("{", encoding="utf-8")

        with pytest.raises(EnvelopeError) as caught:
            check_envelope(envelope)
        assert str(caught.value).startswith(f"{FACTS} is not JSON: ")
        assert len(str(caught.value)) > len(f"{FACTS} is not JSON: ")

    def test_a_facts_file_without_the_corpus_commit_is_refused(self, envelope: Path) -> None:
        facts = _json(envelope / FACTS)
        del facts["corpus_commit"]
        _rewrite(envelope / FACTS, facts)

        with pytest.raises(EnvelopeError) as caught:
            check_envelope(envelope)
        assert str(caught.value) == f"{FACTS} carries no corpus_commit"


class TestCrossTreeAssertions:
    def test_the_site_must_describe_the_corpus_beside_it(self, envelope: Path) -> None:
        facts = _json(envelope / FACTS)
        facts["corpus_commit"] = "f" * 40
        _rewrite(envelope / FACTS, facts)
        corpus_commit = _json(envelope / MANIFEST)["corpus_commit"]

        with pytest.raises(EnvelopeError) as caught:
            check_envelope(envelope)
        assert str(caught.value) == (
            f"{FACTS} describes corpus {'f' * 12}, corpus/ is {corpus_commit[:12]}"
        )

    def test_the_manifest_hash_must_be_of_the_manifest_beside_it(self, envelope: Path) -> None:
        manifest = envelope / MANIFEST
        manifest.write_bytes(manifest.read_bytes() + b"\n")

        with pytest.raises(EnvelopeError, match="another site-manifest.json hash"):
            check_envelope(envelope)

    def test_the_capability_hash_must_be_of_the_document_beside_it(self, envelope: Path) -> None:
        document = envelope / CAPABILITIES
        document.write_bytes(document.read_bytes() + b"\n")

        with pytest.raises(EnvelopeError, match="records capability_sha256"):
            check_envelope(envelope)

    def test_an_invalid_capability_document_is_refused(self, envelope: Path) -> None:
        document = _json(envelope / CAPABILITIES)
        document["state"]["hosted_state"] = "unavailable"
        _rewrite(envelope / CAPABILITIES, document)

        with pytest.raises(EnvelopeError, match="deployment-capabilities.json"):
            check_envelope(envelope)

    @pytest.mark.parametrize(
        ("component", "value", "message"),
        [
            (
                "state_sha256",
                "0" * 64,
                "release_key.state_sha256 is not the capability document's state",
            ),
            (
                "lovspor_commit",
                "0" * 40,
                "release_key.lovspor_commit is not the capability document's checkout",
            ),
            (
                "corpus_commit",
                "0" * 40,
                "release_key.corpus_commit is not the corpus tree's commit",
            ),
        ],
    )
    def test_the_key_must_carry_the_documents_state_checkout_and_the_corpus_commit(
        self, envelope: Path, component: str, value: str, message: str
    ) -> None:
        facts = _json(envelope / FACTS)
        facts["release_key"][component] = value
        _rewrite(envelope / FACTS, facts)

        with pytest.raises(EnvelopeError) as caught:
            check_envelope(envelope)
        assert str(caught.value) == message

    def test_a_facts_file_without_the_key_is_refused(self, envelope: Path) -> None:
        facts = _json(envelope / FACTS)
        del facts["release_key"]
        _rewrite(envelope / FACTS, facts)

        with pytest.raises(EnvelopeError, match="carries no release_key"):
            check_envelope(envelope)

    def test_a_facts_file_that_is_not_an_object_is_refused(self, envelope: Path) -> None:
        (envelope / FACTS).write_text("[]", encoding="utf-8")

        with pytest.raises(EnvelopeError, match="not a JSON object"):
            check_envelope(envelope)


class TestTheId:
    def test_a_served_file_mutated_after_the_id_fails_with_both_ids_printed(
        self, envelope: Path
    ) -> None:
        page = next((envelope / "corpus" / "lov").rglob("index.html"))
        page.write_bytes(page.read_bytes() + b"<!-- edited -->")
        recorded = _json(envelope / FACTS)["release_content_id"]

        with pytest.raises(EnvelopeError, match="the trees hash to") as caught:
            check_envelope(envelope)
        assert recorded in str(caught.value)
        assert recorded not in str(caught.value).split("hash to")[1]

    def test_a_site_file_mutated_after_the_id_fails(self, envelope: Path) -> None:
        sitemap = envelope / "site" / "sitemap-site.xml"
        sitemap.write_bytes(sitemap.read_bytes() + b"\n")

        with pytest.raises(EnvelopeError):
            check_envelope(envelope)

    def test_the_facts_id_must_be_the_recomputed_one(self, envelope: Path) -> None:
        facts = _json(envelope / FACTS)
        facts["release_content_id"] = "1" * 64
        _rewrite(envelope / FACTS, facts)

        with pytest.raises(EnvelopeError, match="site/site-facts.json records release_content_id"):
            check_envelope(envelope)

    def test_the_record_id_must_be_the_recomputed_one(self, envelope: Path) -> None:
        record = _json(envelope / RECORD_NAME)
        record["release_content_id"] = "1" * 64
        _rewrite(envelope / RECORD_NAME, record)

        with pytest.raises(EnvelopeError, match="release.json records release_content_id"):
            check_envelope(envelope)

    def test_the_fragment_id_must_be_the_recomputed_one(self, envelope: Path) -> None:
        recorded = _json(envelope / FACTS)["release_content_id"]
        write_fragment(envelope, fragment_text(envelope.parent / recorded, "1" * 64))

        with pytest.raises(EnvelopeError, match="release.caddy records release_content_id"):
            check_envelope(envelope)

    def test_the_fragment_must_name_the_directory_under_the_id(self, envelope: Path) -> None:
        recorded = _json(envelope / FACTS)["release_content_id"]
        write_fragment(envelope, fragment_text(Path("/elsewhere") / recorded, recorded))

        with pytest.raises(EnvelopeError, match="release.caddy names /elsewhere"):
            check_envelope(envelope)

    def test_an_id_named_directory_must_hold_that_release(
        self, copies: Callable[[str], Path]
    ) -> None:
        copy = copies("1" * 64)

        with pytest.raises(EnvelopeError, match="holds release"):
            check_envelope(copy)


class TestTheRecord:
    def test_must_agree_with_the_facts_on_the_key(self, envelope: Path) -> None:
        record = _json(envelope / RECORD_NAME)
        record["release_key"]["toolchain_fingerprint"] = "0" * 64
        _rewrite(envelope / RECORD_NAME, record)

        with pytest.raises(EnvelopeError) as caught:
            check_envelope(envelope)
        assert str(caught.value) == "release.json release_key differs from site-facts.json"

    @pytest.mark.parametrize("field", ["capability_sha256", "site_manifest_sha256"])
    def test_must_agree_with_the_facts_on_the_input_hashes(
        self, envelope: Path, field: str
    ) -> None:
        record = _json(envelope / RECORD_NAME)
        record[field] = "0" * 64
        _rewrite(envelope / RECORD_NAME, record)

        with pytest.raises(EnvelopeError) as caught:
            check_envelope(envelope)
        assert str(caught.value) == f"release.json {field} differs from site-facts.json"

    def test_must_summarise_the_corpus_beside_it(self, envelope: Path) -> None:
        record = _json(envelope / RECORD_NAME)
        record["corpus"]["corpus_commit"] = "0" * 40
        _rewrite(envelope / RECORD_NAME, record)

        with pytest.raises(EnvelopeError) as caught:
            check_envelope(envelope)
        assert str(caught.value) == "release.json corpus summary names another corpus commit"

    def test_must_carry_the_documents_instant(self, envelope: Path) -> None:
        record = _json(envelope / RECORD_NAME)
        record["observed_at"] = "2030-01-01T00:00:00Z"
        _rewrite(envelope / RECORD_NAME, record)

        with pytest.raises(EnvelopeError) as caught:
            check_envelope(envelope)
        assert str(caught.value) == "release.json observed_at is not the capability document's"


class TestTheTrees:
    def test_a_corpus_defect_is_named_under_its_tree(self, envelope: Path) -> None:
        (envelope / "corpus" / "robots.txt").unlink()

        with pytest.raises(EnvelopeError, match="corpus/: .*robots.txt"):
            check_envelope(envelope)

    def test_a_missing_site_page_breaks_the_closure(self, envelope: Path) -> None:
        shutil.rmtree(envelope / "site" / "en")

        with pytest.raises(EnvelopeError, match="site/: page /en/ is not in the tree"):
            check_envelope(envelope)

    def test_a_script_on_a_site_page_is_refused(self, envelope: Path) -> None:
        page = envelope / "site" / "index.html"
        page.write_text(
            page.read_text(encoding="utf-8").replace("</body>", "<script></script></body>"),
            encoding="utf-8",
        )

        with pytest.raises(EnvelopeError, match="site/: page /"):
            check_envelope(envelope)

    def test_the_site_sitemap_must_list_the_emitted_set(self, envelope: Path) -> None:
        sitemap = envelope / "site" / "sitemap-site.xml"
        sitemap.write_bytes(sitemap.read_bytes().replace(b"<url>", b"<url><!-- -->", 1))
        facts = _json(envelope / FACTS)
        # keep the id assertion out of the way: this test is about the closure
        facts["release_content_id"] = None
        _rewrite(envelope / FACTS, facts)

        with pytest.raises(EnvelopeError, match="does not list the emitted page set"):
            check_envelope(envelope)
