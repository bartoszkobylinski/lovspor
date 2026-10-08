"""The canonical page of one regulation embedded on N pages (#566, owner decision (a)).

``choose_canonical`` reads only what the group's members carry: each page's
URL and sha256 and the regulation title extracted from the text. The page's
own HTML title is not on a member, so "about the regulation" is read from the
URL alone.
"""

from __future__ import annotations

import itertools

import pytest

from lovspor.promotion.batch import BatchItem, CandidateGroup, choose_canonical, group_candidates
from lovspor.promotion.decisions import ArtifactKey, ClassifierEvidence

AUTHORITY = "0301"
SITE = "https://www.eksempel.kommune.invalid"
TITLE = "Forskrift om skoleregler og skoledemokrati i Eksempel-skolen, Eksempel kommune"
DOC_ID = "lk-0301-000000000000"


def member(url: str, *, sha: str = "a", title: str | None = TITLE) -> BatchItem:
    return BatchItem(
        key=ArtifactKey(authority_id=AUTHORITY, sha256=sha * 64, source_url=url),
        classifier=ClassifierEvidence(classifier_version="r1", class_name="enacted"),
        outcome="ready",
        doc_id=DOC_ID,
        title=title,
        content_hash="c" * 64,
    )


def chosen(*members: BatchItem) -> str:
    return choose_canonical(CandidateGroup(members=members)).source_url


class TestAboutTheRegulation:
    def test_the_page_whose_slug_shares_most_title_words_wins_over_a_shorter_path(self) -> None:
        about = f"{SITE}/skole/a/b/skoleregler-og-skoledemokrati"
        partly = f"{SITE}/skole/skoleregler"

        assert chosen(member(f"{SITE}/skole/"), member(partly), member(about)) == about

    def test_one_shared_title_word_beats_none(self) -> None:
        about = f"{SITE}/skole/nyheter/skoledemokrati-uka"

        assert chosen(member(f"{SITE}/skole/"), member(about)) == about

    def test_a_title_word_inside_a_compound_slug_word_counts(self) -> None:
        about = f"{SITE}/skole/hvilke-ordensregler-gjelder-for-grunnskolen"

        assert chosen(member(f"{SITE}/skole/roverud/"), member(about)) == about

    def test_a_slug_word_inside_a_longer_title_word_does_not_count(self) -> None:
        news = f"{SITE}/skole/nyheter/skole-og-demokrati"

        assert chosen(member(news), member(f"{SITE}/skole/sfo")) == f"{SITE}/skole/sfo"

    def test_each_title_word_counts_once_however_many_slug_words_hold_it(self) -> None:
        twice = f"{SITE}/a/skoleregler-skoleregler-skolereglene"
        two_words = f"{SITE}/a/b/skoleregler-skoledemokrati"

        assert chosen(member(twice), member(two_words)) == two_words

    def test_words_naming_the_site_say_nothing_about_the_page(self) -> None:
        news = f"{SITE}/skole/nyheter/eksempel-kommune-feirer"

        assert chosen(member(news), member(f"{SITE}/skole/sfo")) == f"{SITE}/skole/sfo"

    def test_only_the_pages_own_slug_counts_not_its_parent_sections(self) -> None:
        deeper = f"{SITE}/skoleregler/skoledemokrati/sfo"

        assert chosen(member(deeper), member(f"{SITE}/skole/sfo")) == f"{SITE}/skole/sfo"

    @pytest.mark.parametrize("short", ["om", "og", "i"])
    def test_words_under_four_letters_do_not_count(self, short: str) -> None:
        news = f"{SITE}/skole/nyheter/{short}"

        assert chosen(member(news), member(f"{SITE}/skole/abc")) == f"{SITE}/skole/abc"

    def test_a_four_letter_title_word_counts(self) -> None:
        about = f"{SITE}/x/y/slam"
        title = "Forskrift om slam"

        assert chosen(member(f"{SITE}/x", title=title), member(about, title=title)) == about

    @pytest.mark.parametrize(
        ("title", "slug"),
        [
            ("Forskrift om særskilt gebyr", "saerskilt"),
            ("Forskrift om særskilt gebyr", "sarskilt"),
            ("Forskrift om gebyr for tømming", "tomming"),
            ("Forskrift om gebyr for tømming", "toemming"),
            ("Forskrift om åpningstider", "apningstider"),
            ("Forskrift om åpningstider", "aapningstider"),
            ("Forskrift om kafédrift", "kafedrift"),
            ("FORSKRIFT OM FEIING", "Feiing"),
        ],
    )
    def test_norwegian_letters_fold_as_slugs_spell_them(self, title: str, slug: str) -> None:
        about = f"{SITE}/a/b/c/{slug}"

        assert chosen(member(f"{SITE}/a", title=title), member(about, title=title)) == about

    def test_a_percent_encoded_file_name_is_read_as_words(self) -> None:
        pdf = f"{SITE}/download/1/Forskrift%20om%20skoledemokrati.PDF"
        plus = f"{SITE}/download/2/Forskrift+om+skoledemokrati+til+engelsk.PDF"

        assert chosen(member(f"{SITE}/download/x.pdf"), member(plus), member(pdf)) == pdf

    @pytest.mark.parametrize("title", [None, "", "Om i og"])
    def test_without_title_words_the_url_path_alone_decides(self, title: str | None) -> None:
        pages = (f"{SITE}/a/skoleregler", f"{SITE}/b")

        assert chosen(*(member(url, title=title) for url in pages)) == f"{SITE}/b"


class TestShortestPath:
    def test_fewer_path_segments_win_before_path_length(self) -> None:
        shallow = f"{SITE}/barnehage-skole-utdanning/skolefritidsordning-sfo/"

        assert chosen(member(f"{SITE}/a/b/c"), member(shallow)) == shallow

    def test_a_trailing_slash_is_not_a_segment(self) -> None:
        assert chosen(member(f"{SITE}/a/b/"), member(f"{SITE}/abc/de")) == f"{SITE}/a/b/"

    def test_path_length_is_measured_decoded(self) -> None:
        encoded = f"{SITE}/a/b%20c"

        assert chosen(member(f"{SITE}/a/bcde"), member(encoded)) == encoded

    def test_the_query_string_is_not_part_of_the_path(self) -> None:
        query = f"{SITE}/a/bc?side=skoleregler-skoledemokrati"

        assert chosen(member(f"{SITE}/a/b"), member(query)) == f"{SITE}/a/b"

    def test_equal_paths_tie_break_by_url(self) -> None:
        assert chosen(member(f"{SITE}/a/y"), member(f"{SITE}/a/x")) == f"{SITE}/a/x"

    def test_the_same_url_tie_breaks_by_sha256(self) -> None:
        url = f"{SITE}/a/x"
        group = CandidateGroup(members=(member(url, sha="b"), member(url, sha="a")))

        assert choose_canonical(group).sha256 == "a" * 64


def test_the_choice_does_not_depend_on_member_order() -> None:
    pages = [
        member(f"{SITE}/skole/sfo"),
        member(f"{SITE}/skole/nyheter/skoleregler"),
        member(f"{SITE}/skole/nyheter/skoleregler", sha="b"),
        member(f"{SITE}/x"),
    ]
    choices = {
        choose_canonical(CandidateGroup(members=order)) for order in itertools.permutations(pages)
    }

    assert choices == {pages[1].key}


def test_a_folded_group_is_one_promotable_item_from_the_chosen_page() -> None:
    pages = (member(f"{SITE}/skole/sfo"), member(f"{SITE}/a/b/skoleregler", sha="b"))

    (item,) = group_candidates(pages)

    assert item == pages[1].model_copy(update={"sources": (pages[1].key, pages[0].key)})
    assert item.promotable
