"""The ``authority`` filter and the local counts of ``corpus_status`` (ADR-0016 5; slice S10b).

Both are opt-in parameters defined only for the local dataset: omitted,
every existing call answers as before (``test_mcp_existing_calls.py``).
"""

from pathlib import Path
from typing import Any

import pytest

from lovspor.local_corpus import STATUS_NOTICE
from lovspor.mcp import build_server
from tests.unit.local_dataset_fixtures import (
    LOCAL_ID,
    OBSERVED_FIRST,
    build_central,
    local_record,
    wire,
    write_local_document,
    write_local_manifest,
)

OSLO_ID = "lk-0301-0a1b2c3d4e5f"
LOCAL = "lokale-forskrifter"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """Two authorities with one current regulation each, and one withdrawn at 9999."""
    build_central(tmp_path)
    write_local_document(tmp_path)
    write_local_document(tmp_path, OSLO_ID, "0301")
    write_local_manifest(
        tmp_path,
        {
            LOCAL_ID: local_record(),
            OSLO_ID: local_record(authority_id="0301"),
            "lk-9999-ffffffffffff": local_record(slug="trukket", status="removed"),
        },
    )
    return tmp_path


def _call(corpus: Path, name: str, **arguments: Any) -> Any:
    return wire(build_server(corpus), name, arguments)


def _found(corpus: Path, **arguments: Any) -> list[str]:
    result = _call(corpus, "search_laws", query="lekeplasser", **arguments)
    assert "error" not in result, result
    return [hit["doc_id"] for hit in result["structured"]["result"]]


class TestAuthority:
    def test_it_narrows_a_local_search_to_one_authority(self, corpus: Path) -> None:
        assert _found(corpus, dataset=LOCAL) == [OSLO_ID, LOCAL_ID]
        assert _found(corpus, dataset=LOCAL, authority="0301") == [OSLO_ID]
        assert _found(corpus, dataset=LOCAL, authority="9999") == [LOCAL_ID]
        assert _found(corpus, dataset=LOCAL, authority="4601") == []

    def test_it_combines_with_the_limit(self, corpus: Path) -> None:
        assert _found(corpus, dataset=LOCAL, authority="0301", limit=0) == []

    def test_authority_is_filtered_before_the_limit(self, corpus: Path) -> None:
        # Oslo sorts first in the manifest; limiting before filtering loses 9999.
        assert _found(corpus, dataset=LOCAL, authority="9999", limit=1) == [LOCAL_ID]

    @pytest.mark.parametrize(
        ("query", "expected"),
        [("  LEKEPLASSER  ", [LOCAL_ID]), ("9999/", [LOCAL_ID]), ("0301/", []), (" ", [])],
    )
    def test_authority_and_query_both_constrain_results(
        self, corpus: Path, query: str, expected: list[str]
    ) -> None:
        result = _call(corpus, "search_laws", query=query, dataset=LOCAL, authority="9999")

        assert "error" not in result, result
        assert [hit["doc_id"] for hit in result["structured"]["result"]] == expected

    @pytest.mark.parametrize("dataset", [None, "lover", "forskrifter"])
    def test_it_is_refused_outside_the_local_dataset(
        self, corpus: Path, dataset: str | None
    ) -> None:
        arguments = {"authority": "0301"} | ({"dataset": dataset} if dataset else {})

        error = _call(corpus, "search_laws", query="lov", **arguments)["error"]

        assert "authority filters only dataset='lokale-forskrifter'" in error

    @pytest.mark.parametrize("authority", ["301", "03011", "oslo", "", " 0301"])
    def test_a_value_that_cannot_be_a_klass_code_is_refused(
        self, corpus: Path, authority: str
    ) -> None:
        error = _call(
            corpus, "search_laws", query="lekeplasser", dataset=LOCAL, authority=authority
        )["error"]

        assert "authority is a KLASS code" in error

    def test_a_fylkeskommune_code_is_accepted(self, corpus: Path) -> None:
        assert _found(corpus, dataset=LOCAL, authority="46") == []


class TestCorpusStatus:
    def test_omitted_it_is_the_central_status_alone(self, corpus: Path) -> None:
        status = _call(corpus, "corpus_status")["structured"]

        assert "local" not in status

    def test_the_local_block_counts_per_authority(self, corpus: Path) -> None:
        central = _call(corpus, "corpus_status")["structured"]
        status = _call(corpus, "corpus_status", dataset=LOCAL)["structured"]

        assert {k: v for k, v in status.items() if k != "local"} == central
        assert status["local"] == {
            "dataset": LOCAL,
            "manifest_generated_at": OBSERVED_FIRST,
            "current_documents": 2,
            "removed_documents": 1,
            "authorities": [
                {"authority_id": "0301", "authority_type": "kommune", "current": 1, "removed": 0},
                {"authority_id": "9999", "authority_type": "kommune", "current": 1, "removed": 1},
            ],
            "asserted": False,
            "notice": STATUS_NOTICE,
        }

    def test_a_corpus_without_the_dataset_counts_nothing(self, tmp_path: Path) -> None:
        build_central(tmp_path)

        local = _call(tmp_path, "corpus_status", dataset=LOCAL)["structured"]["local"]

        assert (local["current_documents"], local["removed_documents"], local["authorities"]) == (
            0,
            0,
            [],
        )
        assert local["manifest_generated_at"] is None

    def test_withdrawn_only_authorities_are_counted_without_document_files(
        self, tmp_path: Path
    ) -> None:
        build_central(tmp_path)
        # Withdrawn records remain in the manifest even without served files.
        county = local_record(authority_id="46", status="removed")
        county["authority_type"] = "fylkeskommune"
        write_local_manifest(
            tmp_path,
            {
                "lk-9999-ffffffffffff": local_record(status="removed"),
                "lk-46-ffffffffffff": county,
                "lk-46-eeeeeeeeeeee": {
                    **local_record(slug="annen-trukket", authority_id="46", status="removed"),
                    "authority_type": "fylkeskommune",
                },
            },
        )

        local = _call(tmp_path, "corpus_status", dataset=LOCAL)["structured"]["local"]

        assert local["current_documents"] == 0
        assert local["removed_documents"] == 3
        assert local["authorities"] == [
            {"authority_id": "46", "authority_type": "fylkeskommune", "current": 0, "removed": 2},
            {"authority_id": "9999", "authority_type": "kommune", "current": 0, "removed": 1},
        ]
        assert _found(tmp_path, dataset=LOCAL, authority="46") == []

    @pytest.mark.parametrize("dataset", ["lover", "forskrifter", "kommunale"])
    def test_another_dataset_is_refused(self, corpus: Path, dataset: str) -> None:
        error = _call(corpus, "corpus_status", dataset=dataset)["error"]

        assert "corpus_status takes dataset='lokale-forskrifter' or none" in error


def test_both_parameters_are_in_the_served_schemas(tmp_path: Path) -> None:
    build_central(tmp_path)
    tools = build_server(tmp_path)._tool_manager._tools

    assert set(tools["search_laws"].parameters["properties"]) == {
        "query",
        "dataset",
        "limit",
        "authority",
    }
    assert tools["search_laws"].parameters["required"] == ["query"]
    assert set(tools["corpus_status"].parameters["properties"]) == {"dataset"}
    assert (
        "required" not in tools["corpus_status"].parameters
        or not (tools["corpus_status"].parameters["required"])
    )
