"""get_law / get_section / search_laws serve the local dataset opt-in (ADR-0016 5; slice S5).

Every call goes through the built server, the way a client reaches it. The
calls that existed before the dataset are pinned in
``test_mcp_existing_calls.py``; these are the new ones.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lovspor.local_corpus import OBSERVATION_NOTICE
from lovspor.mcp import CorpusReader, build_server
from lovspor.mcp_local import ServedCorpus
from tests.unit.local_dataset_fixtures import (
    LOCAL_ID,
    LOCAL_SLUG,
    LOCAL_TITLE,
    LOCAL_URL,
    OBSERVED_FIRST,
    OBSERVED_LAST,
    add_local_dataset,
    build_central,
    local_record,
    wire,
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


def _call(corpus: Path, name: str, **arguments: Any) -> Any:
    return wire(build_server(corpus), name, arguments)


def _text(corpus: Path, name: str, **arguments: Any) -> str:
    result = _call(corpus, name, **arguments)
    assert "error" not in result, result
    return str(result["content"][0]["text"])


def _structured(corpus: Path, name: str, **arguments: Any) -> Any:
    result = _call(corpus, name, **arguments)
    assert "error" not in result, result
    return result["structured"]


def _observation_block(text: str) -> dict[str, Any]:
    match = re.search(r"```json\n(.*)\n```\n\Z", text, re.DOTALL)
    assert match is not None, text[-400:]
    payload = json.loads(match.group(1))
    assert isinstance(payload, dict)
    return payload


class TestGetLaw:
    @pytest.mark.parametrize("address", [ADDRESS, LOCAL_ID])
    def test_a_local_regulation_is_served_with_its_observation(
        self, corpus: Path, address: str
    ) -> None:
        text = _text(corpus, "get_law", slug=address)
        markdown = (corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md").read_text("utf-8")

        assert text.startswith(markdown.rstrip() + "\n\n---\n\n**Observation")
        assert _observation_block(text) == {"observation": OBSERVATION}

    def test_the_observation_is_asserted_false_even_where_the_text_says_more(
        self, corpus: Path
    ) -> None:
        path = corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md"
        path.write_text(
            path.read_text("utf-8").replace("asserted: false", "asserted: true"), "utf-8"
        )

        observation = _observation_block(_text(corpus, "get_law", slug=ADDRESS))["observation"]

        assert observation["asserted"] is False
        assert observation["basis"] == "observed"

    def test_no_temporal_notice_is_composed_for_a_local_regulation(self, corpus: Path) -> None:
        path = corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md"
        announced = "\n\nEndret ved forskrift 1 jan 2099 nr. 1 (ikr. 1 jan 2099).\n"
        path.write_text(path.read_text("utf-8") + announced, "utf-8")

        assert "Temporal notice" not in _text(corpus, "get_law", slug=ADDRESS)

    def test_an_unknown_local_address_names_the_local_search(self, corpus: Path) -> None:
        error = _call(corpus, "get_law", slug="9999/finnesikke")["error"]

        assert "no current local regulation '9999/finnesikke'" in error
        assert "dataset='lokale-forskrifter'" in error

    def test_a_withdrawn_local_regulation_is_not_served(self, corpus: Path) -> None:
        write_local_manifest(corpus, {LOCAL_ID: local_record(status="removed")})

        assert "error" in _call(corpus, "get_law", slug=ADDRESS)

    def test_a_central_law_is_still_served_by_its_slug(self, corpus: Path) -> None:
        text = _text(corpus, "get_law", slug="proveloven")

        assert text.startswith('---\nid: "nl-19990101-001"')
        assert "Observation" not in text


class TestGetSection:
    def test_a_local_section_is_served_with_its_observation(self, corpus: Path) -> None:
        section = _structured(corpus, "get_section", slug=ADDRESS, section_id="§ 2.")

        assert section == {
            "slug": ADDRESS,
            "section_id": "2",
            "occurrence": 1,
            "heading": "§ 2. Vedlikehold",
            "parent_chapter": "",
            "layer": "main",
            "body": section["body"],
            "cross_references": [
                {
                    "text": "§ 1",
                    "target_slug": ADDRESS,
                    "target_section_id": "1",
                    "valid": True,
                    "reason": None,
                }
            ],
            "doc_id": LOCAL_ID,
            "dataset": "lokale-forskrifter",
            "observation": OBSERVATION,
        }
        assert "kontrolleres hver vår" in section["body"]

    def test_a_local_section_is_reached_by_id_too(self, corpus: Path) -> None:
        section = _structured(corpus, "get_section", slug=LOCAL_ID, section_id="1")

        assert section["slug"] == ADDRESS
        assert section["observation"]["asserted"] is False
        assert "trygge lekeplasser" in section["body"]

    def test_a_cross_reference_to_a_central_law_is_validated_there(self, corpus: Path) -> None:
        path = corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md"
        cited = (
            path.read_text("utf-8") + "\n## § 3 Hjemmel\n\nSe ordensloven § 2 og ordensloven § 9.\n"
        )
        path.write_text(cited, "utf-8")

        references = _structured(corpus, "get_section", slug=ADDRESS, section_id="3")[
            "cross_references"
        ]

        assert [(r["target_slug"], r["target_section_id"], r["valid"]) for r in references] == [
            ("ordensloven", "2", True),
            ("ordensloven", "9", False),
        ]

    def test_an_absent_local_section_lists_the_available_ones(self, corpus: Path) -> None:
        error = _call(corpus, "get_section", slug=ADDRESS, section_id="7")["error"]

        assert f"section '7' not found in '{ADDRESS}'; available: § 1, § 2" in error

    def test_recorded_at_reads_a_local_section_from_the_corpus_state(self, versioned: Path) -> None:
        """ADR-0016 5: ``recorded_at`` works on local documents unchanged (#569)."""
        path = versioned / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md"
        _append(path, "\n## § 3 Ny\n\nSkrevet etter at korpuset ble registrert.\n")

        section = _structured(
            versioned, "get_section", slug=ADDRESS, section_id="2", recorded_at="2026-09-03"
        )
        error = _call(
            versioned, "get_section", slug=ADDRESS, section_id="3", recorded_at="2026-09-03"
        )["error"]

        assert section["recorded_at"] == "2026-09-03"
        assert section["observation"] == OBSERVATION
        assert "kontrolleres hver vår" in section["body"]
        assert f"section '3' not found in '{ADDRESS}'; available: § 1, § 2" in error

    def test_a_central_section_keeps_its_temporal_notice(self, corpus: Path) -> None:
        section = _structured(corpus, "get_section", slug="proveloven", section_id="1")

        assert section["temporal_notice"] is None
        assert "observation" not in section


class TestSearchLaws:
    def test_the_local_dataset_is_searched_only_when_asked(self, corpus: Path) -> None:
        hits = _structured(corpus, "search_laws", query="lekeplasser", dataset="lokale-forskrifter")

        assert hits == {
            "result": [
                {
                    "slug": ADDRESS,
                    "doc_id": LOCAL_ID,
                    "title": LOCAL_TITLE,
                    "dataset": "lokale-forskrifter",
                    "authority_id": "9999",
                    "version": 1,
                    "observation": OBSERVATION,
                }
            ]
        }
        assert _structured(corpus, "search_laws", query="lekeplasser") == {"result": []}

    def test_a_local_search_never_returns_a_central_law(self, corpus: Path) -> None:
        assert _structured(corpus, "search_laws", query="lov", dataset="lokale-forskrifter") == {
            "result": []
        }

    def test_the_limit_bounds_a_local_search(self, corpus: Path) -> None:
        write_local_manifest(
            corpus,
            {LOCAL_ID: local_record(), "lk-9999-ffffffffffff": local_record(slug="lekeplasser-to")},
        )

        one = _structured(
            corpus, "search_laws", query="lekeplasser", dataset="lokale-forskrifter", limit=1
        )
        none = _structured(
            corpus, "search_laws", query="lekeplasser", dataset="lokale-forskrifter", limit=0
        )

        assert [hit["doc_id"] for hit in one["result"]] == [LOCAL_ID]
        assert none == {"result": []}

    def test_a_negative_limit_is_refused_for_a_local_search(self, corpus: Path) -> None:
        error = _call(
            corpus, "search_laws", query="lekeplasser", dataset="lokale-forskrifter", limit=-1
        )["error"]

        assert "limit must be non-negative" in error

    def test_a_corpus_without_the_dataset_finds_nothing_local(self, tmp_path: Path) -> None:
        build_central(tmp_path)

        assert _structured(
            tmp_path, "search_laws", query="lekeplasser", dataset="lokale-forskrifter"
        ) == {"result": []}


class TestServedCorpus:
    def test_warm_prebuilds_the_central_indices_and_cold_does_not(self, corpus: Path) -> None:
        cold, warm = CorpusReader(corpus), CorpusReader(corpus)

        ServedCorpus(cold, warm=False)
        ServedCorpus(warm, warm=True)

        assert cold._slug_index is None
        assert cold._body_index is None
        assert warm._slug_index is not None
        assert warm._body_index is not None


OBSERVATION_HEADING = (
    "**Observation — observed on the authority's website, not asserted (ADR-0016).**"
)
DUPLICATED_SECTION = "\n## § 2. Arkiv\n\nEn annen paragraf med samme nummer.\n"
COMMIT_DATE = "2026-09-02T10:00:00Z"


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text("utf-8") + text, "utf-8")


def _git(corpus: Path, *args: str) -> None:
    stamp = {"GIT_AUTHOR_DATE": COMMIT_DATE, "GIT_COMMITTER_DATE": COMMIT_DATE}
    env = {**os.environ, **stamp}
    subprocess.run(["git", *args], cwd=corpus, check=True, capture_output=True, env=env)


@pytest.fixture
def versioned(corpus: Path) -> Path:
    """The fixture corpus as one commit, so ``recorded_at`` can select it."""
    _append(corpus / "lover" / "ordensloven.md", DUPLICATED_SECTION)
    _git(corpus, "init", "-q", "-b", "main")
    _git(corpus, "config", "user.email", "test@example.com")
    _git(corpus, "config", "user.name", "Test")
    _git(corpus, "config", "commit.gpgsign", "false")
    _git(corpus, "add", "-A")
    _git(corpus, "commit", "-q", "-m", "corpus")
    return corpus


class TestExactWire:
    def test_the_observation_block_is_byte_exact(self, corpus: Path) -> None:
        markdown = (corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md").read_text("utf-8")
        block = json.dumps({"observation": OBSERVATION}, indent=2, ensure_ascii=False)

        text = _text(corpus, "get_law", slug=ADDRESS)

        assert text == (
            f"{markdown.rstrip()}\n\n---\n\n{OBSERVATION_HEADING}\n\n```json\n{block}\n```\n"
        )
        assert '"name": "Prøvestad"' in text

    def test_a_recorded_local_section_is_exact(self, versioned: Path) -> None:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=versioned, check=True, capture_output=True, text=True
        ).stdout.strip()

        section = _structured(
            versioned, "get_section", slug=LOCAL_ID, section_id="1", recorded_at="2026-09-03"
        )

        assert section == {
            "slug": ADDRESS,
            "section_id": "1",
            "occurrence": 1,
            "heading": "§ 1. Formål",
            "parent_chapter": "",
            "layer": "main",
            "body": "Forskriften skal gi trygge lekeplasser i Prøvestad kommune.",
            "cross_references": [],
            "doc_id": LOCAL_ID,
            "dataset": "lokale-forskrifter",
            "observation": OBSERVATION,
            "recorded_at": "2026-09-03",
            "corpus_commit": head,
            "content_hash": "c" * 64,
        }


class TestPassThrough:
    def test_a_local_section_honours_occurrence(self, corpus: Path) -> None:
        _append(corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md", DUPLICATED_SECTION)

        second = _structured(corpus, "get_section", slug=ADDRESS, section_id="2", occurrence=2)

        assert second["occurrence"] == 2
        assert "En annen paragraf" in second["body"]

    def test_a_central_section_honours_occurrence(self, versioned: Path) -> None:
        second = _structured(
            versioned, "get_section", slug="ordensloven", section_id="2", occurrence=2
        )

        assert second["occurrence"] == 2
        assert "En annen paragraf" in second["body"]

    def test_a_recorded_central_section_honours_occurrence(self, versioned: Path) -> None:
        second = _structured(
            versioned,
            "get_section",
            slug="ordensloven",
            section_id="2",
            occurrence=2,
            recorded_at="2026-09-03",
        )

        assert second["occurrence"] == 2
        assert "En annen paragraf" in second["body"]
        assert second["temporal_notice"]["status"] == "not_evaluated"

    def test_a_central_search_keeps_its_limit(self, corpus: Path) -> None:
        hits = _structured(corpus, "search_laws", query="lov", limit=1)["result"]

        assert len(hits) == 1

    @pytest.mark.parametrize("recorded_at", [None, "2026-09-03"])
    def test_search_body_keeps_dataset_and_limit(
        self, versioned: Path, recorded_at: str | None
    ) -> None:
        stamp = {} if recorded_at is None else {"recorded_at": recorded_at}

        def slugs(**arguments: Any) -> list[str]:
            result = _structured(versioned, "search_body", query="prøve", **arguments, **stamp)
            rows = result["result"]
            rows = rows["results"] if isinstance(rows, dict) else rows
            return sorted(row["slug"] for row in rows)

        assert slugs() == ["proveforskriften", "proveloven"]
        assert slugs(dataset="forskrifter") == ["proveforskriften"]
        assert len(slugs(limit=1)) == 1

    def test_a_cross_reference_to_a_central_law_reads_the_central_index(self, corpus: Path) -> None:
        _append(
            corpus / "lokale-forskrifter" / "9999" / f"{LOCAL_SLUG}.md",
            "\n## § 3 Hjemmel\n\nSe ordensloven § 3.\n",
        )

        references = _structured(corpus, "get_section", slug=ADDRESS, section_id="3")[
            "cross_references"
        ]

        assert [(r["target_slug"], r["target_section_id"], r["valid"]) for r in references] == [
            ("ordensloven", "3", False)
        ]
