"""The local-regulations dataset as the MCP server reads it (ADR-0016 3, 5; slice S5)."""

import json
import os
from pathlib import Path

import pytest

from lovspor.errors import AmbiguousSlugError, CorpusNotFoundError, LocalCorpusError
from lovspor.local_corpus import (
    LOCAL_DATASET,
    OBSERVATION_NOTICE,
    LocalDataset,
    is_local_address,
)
from tests.unit.local_dataset_fixtures import (
    LOCAL_ID,
    LOCAL_SLUG,
    LOCAL_TITLE,
    LOCAL_URL,
    OBSERVED_FIRST,
    OBSERVED_LAST,
    add_local_dataset,
    build_central,
    front_matter,
    local_record,
    observations,
    write_local_document,
    write_local_manifest,
)

ADDRESS = f"9999/{LOCAL_SLUG}"
OBSERVATION = {
    "basis": "observed",
    "asserted": False,
    "authority": {"id": "9999", "type": "kommune", "name": "Prøvestad"},
    "source_url": LOCAL_URL,
    "observed_at_first": OBSERVED_FIRST,
    "observed_at_last": OBSERVED_LAST,
    "notice": OBSERVATION_NOTICE,
}


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    build_central(tmp_path)
    add_local_dataset(tmp_path)
    return tmp_path


class TestAddress:
    @pytest.mark.parametrize(
        "address",
        [
            ADDRESS,
            "0301/x",
            LOCAL_ID,
            "lk-03-0123456789ab",
            "lf-20191212-2077",
            "lf-20191212-20771",
        ],
    )
    def test_a_qualified_slug_or_a_local_id_is_local(self, address: str) -> None:
        assert is_local_address(address)

    @pytest.mark.parametrize(
        "address",
        [
            LOCAL_SLUG,
            "skatteloven",
            "lk-9999-0a1b2c3d4e5",
            "lk-9999-0a1b2c3d4e5fa",
            "lk-999-0a1b2c3d4e5f",
            "lk-9999-0A1B2C3D4E5F",
            "lf-2019121-2077",
            "lf-20191212-207",
            "sf-20200114-0063",
            "xlk-9999-0a1b2c3d4e5f",
        ],
    )
    def test_a_bare_slug_or_any_other_id_is_not_local(self, address: str) -> None:
        assert not is_local_address(address)


class TestResolve:
    def test_the_qualified_slug_and_the_id_name_the_same_document(self, corpus: Path) -> None:
        dataset = LocalDataset(corpus)

        assert dataset.resolve(ADDRESS)[0] == LOCAL_ID
        assert dataset.resolve(LOCAL_ID)[0] == LOCAL_ID
        assert dataset.resolve(ADDRESS)[1].title == LOCAL_TITLE

    @pytest.mark.parametrize(
        "address", [LOCAL_SLUG, f"0301/{LOCAL_SLUG}", "9999/annen", "lk-9999-ffffffffffff", "9999/"]
    )
    def test_an_unknown_address_is_not_found(self, corpus: Path, address: str) -> None:
        with pytest.raises(CorpusNotFoundError, match="no current local regulation") as raised:
            LocalDataset(corpus).resolve(address)

        assert "lokale-forskrifter" in str(raised.value)
        assert not isinstance(raised.value, LocalCorpusError)

    def test_a_removed_document_is_not_served(self, corpus: Path) -> None:
        write_local_manifest(corpus, {LOCAL_ID: local_record(status="removed")})

        with pytest.raises(CorpusNotFoundError):
            LocalDataset(corpus).resolve(ADDRESS)
        with pytest.raises(CorpusNotFoundError):
            LocalDataset(corpus).resolve(LOCAL_ID)

    def test_a_removed_record_before_a_current_one_hides_nothing(self, corpus: Path) -> None:
        write_local_manifest(
            corpus,
            {
                "lk-0001-000000000000": local_record(authority_id="9999", status="removed"),
                LOCAL_ID: local_record(),
                "lk-9999-ffffffffffff": local_record(slug="annen"),
            },
        )
        dataset = LocalDataset(corpus)

        assert dataset.resolve(ADDRESS)[0] == LOCAL_ID
        assert dataset.resolve("9999/annen")[0] == "lk-9999-ffffffffffff"

    def test_two_current_documents_under_one_address_are_ambiguous(self, corpus: Path) -> None:
        write_local_manifest(
            corpus, {LOCAL_ID: local_record(), "lk-9999-ffffffffffff": local_record()}
        )

        with pytest.raises(AmbiguousSlugError, match="lk-9999-ffffffffffff") as raised:
            LocalDataset(corpus).resolve(ADDRESS)

        assert LOCAL_ID in str(raised.value)

    def test_the_same_slug_under_another_authority_is_another_document(self, corpus: Path) -> None:
        other = "lk-0301-111111111111"
        write_local_manifest(
            corpus, {LOCAL_ID: local_record(), other: local_record(authority_id="0301")}
        )

        assert LocalDataset(corpus).resolve(f"0301/{LOCAL_SLUG}")[0] == other


class TestManifest:
    def test_a_corpus_without_the_dataset_serves_an_empty_one(self, tmp_path: Path) -> None:
        build_central(tmp_path)
        dataset = LocalDataset(tmp_path)

        assert dataset.matches("lekeplasser") == []
        with pytest.raises(CorpusNotFoundError, match="no current local regulation"):
            dataset.resolve(ADDRESS)

    def test_an_unreadable_manifest_is_a_typed_error(self, corpus: Path) -> None:
        (corpus / "lokale-forskrifter" / "manifest.json").write_text("{", encoding="utf-8")

        with pytest.raises(LocalCorpusError, match="manifest.json"):
            LocalDataset(corpus).resolve(ADDRESS)

    def test_a_manifest_of_another_version_is_refused(self, corpus: Path) -> None:
        path = corpus / "lokale-forskrifter" / "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**payload, "version": 2}), encoding="utf-8")

        with pytest.raises(LocalCorpusError):
            LocalDataset(corpus).matches("x")

    def test_a_rewritten_manifest_is_read_again(self, corpus: Path) -> None:
        dataset = LocalDataset(corpus)
        assert dataset.resolve(ADDRESS)[0] == LOCAL_ID
        path = corpus / "lokale-forskrifter" / "manifest.json"
        before = path.stat().st_mtime_ns

        write_local_manifest(corpus, {LOCAL_ID: local_record(status="removed")})
        os.utime(path, ns=(before + 10**9, before + 10**9))

        with pytest.raises(CorpusNotFoundError):
            dataset.resolve(ADDRESS)

    def test_an_unchanged_manifest_is_not_reread(self, corpus: Path) -> None:
        dataset = LocalDataset(corpus)
        first = dataset.manifest()

        assert dataset.manifest() is first


class TestDocument:
    def test_the_document_carries_its_markdown_and_observation(self, corpus: Path) -> None:
        document = LocalDataset(corpus).document(ADDRESS)

        assert document.doc_id == LOCAL_ID
        assert document.address == ADDRESS
        assert document.markdown.startswith("---\nid: ")
        assert "## § 2 Vedlikehold" in document.markdown
        assert document.observation.model_dump(mode="json") == OBSERVATION

    def test_the_observation_keys_are_in_the_adr_order(self, corpus: Path) -> None:
        observation = LocalDataset(corpus).document(LOCAL_ID).observation

        assert list(observation.model_dump(mode="json")) == list(OBSERVATION)

    def test_a_missing_markdown_file_is_a_typed_error(self, corpus: Path) -> None:
        (corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md").unlink()

        with pytest.raises(LocalCorpusError, match="git pull"):
            LocalDataset(corpus).document(ADDRESS)

    def test_a_missing_observations_file_is_a_typed_error(self, corpus: Path) -> None:
        (corpus / "lokale-forskrifter" / "9999" / "observations" / f"{LOCAL_SLUG}.json").unlink()

        with pytest.raises(LocalCorpusError, match="observations"):
            LocalDataset(corpus).document(ADDRESS)

    def test_observations_of_another_document_are_refused(self, corpus: Path) -> None:
        path = corpus / "lokale-forskrifter" / "9999" / "observations" / f"{LOCAL_SLUG}.json"
        path.write_text(json.dumps(observations(doc_id="lk-9999-ffffffffffff")), encoding="utf-8")

        with pytest.raises(LocalCorpusError, match="lk-9999-ffffffffffff"):
            LocalDataset(corpus).document(ADDRESS)

    def test_observations_without_the_served_version_are_refused(self, corpus: Path) -> None:
        path = corpus / "lokale-forskrifter" / "9999" / "observations" / f"{LOCAL_SLUG}.json"
        payload = observations()
        payload["versions"][0]["version"] = 2
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(LocalCorpusError, match="version 1"):
            LocalDataset(corpus).document(ADDRESS)

    def test_unreadable_observations_are_a_typed_error(self, corpus: Path) -> None:
        path = corpus / "lokale-forskrifter" / "9999" / "observations" / f"{LOCAL_SLUG}.json"
        path.write_text("[]", encoding="utf-8")

        with pytest.raises(LocalCorpusError, match="observations"):
            LocalDataset(corpus).document(ADDRESS)

    def test_the_observed_version_is_picked_among_several(self, corpus: Path) -> None:
        path = corpus / "lokale-forskrifter" / "9999" / "observations" / f"{LOCAL_SLUG}.json"
        payload = observations()
        older = {**payload["versions"][0], "version": 0, "observed_at_last": "2026-08-25T00:00:00Z"}
        payload["versions"] = [older, payload["versions"][0]]
        path.write_text(json.dumps(payload), encoding="utf-8")

        assert LocalDataset(corpus).document(ADDRESS).observation.observed_at_last == OBSERVED_LAST

    def test_a_front_matter_naming_another_id_is_refused(self, corpus: Path) -> None:
        write_local_document(corpus, doc_id="lk-9999-ffffffffffff")

        with pytest.raises(LocalCorpusError, match="lk-9999-ffffffffffff"):
            LocalDataset(corpus).document(ADDRESS)

    @pytest.mark.parametrize(
        "markdown",
        [
            "# no front matter\n",
            "---\nid: lk-9999\n---\n",
            '---\nid: "x"\nnot a field\n---\n',
            '---\nid: "x"\n',
        ],
    )
    def test_an_unreadable_front_matter_is_a_typed_error(self, corpus: Path, markdown: str) -> None:
        (corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md").write_text(markdown, "utf-8")

        with pytest.raises(LocalCorpusError, match="front matter"):
            LocalDataset(corpus).document(ADDRESS)

    def test_a_manifest_path_outside_the_dataset_is_refused(self, corpus: Path) -> None:
        record = {**local_record(), "markdown_path": "lover/proveloven.md"}
        write_local_manifest(corpus, {LOCAL_ID: record})

        with pytest.raises(LocalCorpusError, match="outside"):
            LocalDataset(corpus).document(ADDRESS)

    def test_a_manifest_path_escaping_the_corpus_is_refused(self, corpus: Path) -> None:
        record = {**local_record(), "markdown_path": "lokale-forskrifter/../../etc/passwd"}
        write_local_manifest(corpus, {LOCAL_ID: record})

        with pytest.raises(LocalCorpusError, match="outside"):
            LocalDataset(corpus).document(ADDRESS)


class TestSearch:
    @pytest.mark.parametrize("query", ["lekeplasser", "LEKEPLASSER", "9999/forskrift", "prøvestad"])
    def test_slug_title_and_authority_match(self, corpus: Path, query: str) -> None:
        assert [doc_id for doc_id, _ in LocalDataset(corpus).matches(query)] == [LOCAL_ID]

    @pytest.mark.parametrize("query", ["", "   ", "skatt", "vedlikehold"])
    def test_a_blank_query_or_a_body_word_matches_nothing(self, corpus: Path, query: str) -> None:
        assert LocalDataset(corpus).matches(query) == []

    def test_removed_records_do_not_match_and_hide_nothing(self, corpus: Path) -> None:
        write_local_manifest(
            corpus,
            {
                "lk-0001-000000000000": local_record(status="removed"),
                LOCAL_ID: local_record(),
                "lk-9999-ffffffffffff": local_record(slug="lekeplasser-to"),
            },
        )

        assert [doc_id for doc_id, _ in LocalDataset(corpus).matches("lekeplasser")] == [
            LOCAL_ID,
            "lk-9999-ffffffffffff",
        ]

    def test_a_hit_is_labelled_observed_not_asserted(self, corpus: Path) -> None:
        dataset = LocalDataset(corpus)
        doc_id, record = dataset.matches("lekeplasser")[0]

        assert dataset.hit(doc_id, record) == {
            "slug": ADDRESS,
            "doc_id": LOCAL_ID,
            "title": LOCAL_TITLE,
            "dataset": LOCAL_DATASET,
            "authority_id": "9999",
            "version": 1,
            "observation": OBSERVATION,
        }


class TestExactErrors:
    def test_an_ambiguous_address_names_every_candidate(self, corpus: Path) -> None:
        other = "lk-9999-ffffffffffff"
        write_local_manifest(corpus, {LOCAL_ID: local_record(), other: local_record()})

        with pytest.raises(AmbiguousSlugError) as raised:
            LocalDataset(corpus).resolve(ADDRESS)

        assert str(raised.value) == f"local address {ADDRESS!r} names 2: {LOCAL_ID}, {other}"

    def test_a_missing_file_names_itself_and_the_remedy(self, corpus: Path) -> None:
        path = corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md"
        path.unlink()

        with pytest.raises(LocalCorpusError) as raised:
            LocalDataset(corpus).document(ADDRESS)

        assert str(raised.value) == (
            f"the local manifest references {path.resolve()} but the file is missing; "
            "run 'git pull' in the corpus to refresh"
        )

    def test_a_non_utf8_file_is_not_read_in_another_encoding(self, corpus: Path) -> None:
        path = corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md"
        path.write_bytes(path.read_text("utf-8").encode("latin-1"))

        with pytest.raises(UnicodeDecodeError):
            LocalDataset(corpus).document(ADDRESS)

    def test_an_unreadable_front_matter_names_the_file(self, corpus: Path) -> None:
        path = corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md"
        path.write_text("# no front matter\n", "utf-8")

        with pytest.raises(LocalCorpusError) as raised:
            LocalDataset(corpus).document(ADDRESS)

        assert str(raised.value).startswith(f"{path.resolve()} has no readable front matter: ")


class TestFrontMatterGrammar:
    @pytest.mark.parametrize(
        ("markdown", "reason"),
        [
            ("# tittel\n" + front_matter(), "no front matter block"),
            ("ingen front matter\n", "no front matter block"),
            ('---\nid: "x"\n', "front matter block is not closed"),
            ('---\nid: "x"\nnot a field\n---\n', "not a front-matter field: 'not a field'"),
        ],
    )
    def test_each_refusal_says_why(self, corpus: Path, markdown: str, reason: str) -> None:
        (corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md").write_text(markdown, "utf-8")

        with pytest.raises(LocalCorpusError) as raised:
            LocalDataset(corpus).document(ADDRESS)

        assert str(raised.value).endswith(f"has no readable front matter: {reason}")

    def test_a_rule_in_the_body_does_not_end_the_front_matter_late(self, corpus: Path) -> None:
        path = corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md"
        path.write_text(path.read_text("utf-8") + "\n---\n\nEtter en linje.\n", "utf-8")

        assert LocalDataset(corpus).document(ADDRESS).observation.model_dump() == OBSERVATION
