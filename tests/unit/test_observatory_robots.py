"""Tests for lovspor.observatory.robots — one robots.txt semantics, ours.

Issue #351: ``urllib.robotparser`` resolves an Allow/Disallow conflict by file
order up to 3.13 and by longest match from 3.14, so the crawler's compliance
used to depend on which interpreter ran it. Every case below has one answer
whichever order the site wrote its rules in.
"""

import ast
from pathlib import Path

import pytest

from lovspor.observatory.robots import RobotsPolicy, Rule

UA = "lovspor-observatory/0.1 (+https://lovspor.no/observatory)"
HOST = "https://example.invalid"


def policy(text: str) -> RobotsPolicy:
    return RobotsPolicy.parse(text.splitlines())


class TestRuleOrderDoesNotMatter:
    @pytest.mark.parametrize(
        "text",
        [
            "User-agent: *\nAllow: /\nDisallow: /sitemap.xml\n",
            "User-agent: *\nDisallow: /sitemap.xml\nAllow: /\n",
        ],
    )
    def test_a_narrower_disallow_beats_a_broad_allow_in_either_order(self, text: str) -> None:
        """The case from #351: on 3.12 the first order allowed the fetch."""
        assert policy(text).allows(UA, f"{HOST}/sitemap.xml") is False
        assert policy(text).allows(UA, f"{HOST}/kunngjoringer/") is True

    @pytest.mark.parametrize(
        "text",
        [
            "User-agent: *\nDisallow: /a\nAllow: /a/b\n",
            "User-agent: *\nAllow: /a/b\nDisallow: /a\n",
        ],
    )
    def test_a_narrower_allow_beats_a_broad_disallow_in_either_order(self, text: str) -> None:
        assert policy(text).allows(UA, f"{HOST}/a/b/c") is True
        assert policy(text).allows(UA, f"{HOST}/a/x") is False

    def test_an_allow_wins_a_tie_of_equal_length(self) -> None:
        text = "User-agent: *\nDisallow: /page\nAllow: /page\n"

        assert policy(text).allows(UA, f"{HOST}/page") is True


class TestWildcards:
    def test_match_length_is_one_more_than_the_matching_rule_length(self) -> None:
        rule = Rule("/files/*", allow=False, anchored=False)

        assert rule.match_length("/files/private/document") == len("/files/*") + 1
        assert rule.match_length("/public/document") == 0

    def test_a_star_matches_any_run_of_characters(self) -> None:
        text = "User-agent: *\nDisallow: /*.pdf\n"

        assert policy(text).allows(UA, f"{HOST}/docs/plan.pdf") is False
        assert policy(text).allows(UA, f"{HOST}/docs/plan.html") is True

    def test_a_dollar_anchors_the_rule_to_the_end_of_the_path(self) -> None:
        text = "User-agent: *\nDisallow: /*.pdf$\n"

        assert policy(text).allows(UA, f"{HOST}/plan.pdf") is False
        assert policy(text).allows(UA, f"{HOST}/plan.pdf?view=1") is True

    def test_a_literal_rule_can_be_anchored_to_the_end_of_the_path(self) -> None:
        text = "User-agent: *\nDisallow: /pageX$\n"

        assert policy(text).allows(UA, f"{HOST}/pageX") is False
        assert policy(text).allows(UA, f"{HOST}/pageX/child") is True

    def test_runs_of_wildcards_and_anchors_have_their_defined_meaning(self) -> None:
        text = "User-agent: *\nDisallow: /docs/**/private$$*$\n"

        assert policy(text).allows(UA, f"{HOST}/docs/2026/private") is False
        assert policy(text).allows(UA, f"{HOST}/docs/2026/private/child") is True

    def test_several_wildcards_preserve_every_literal_part(self) -> None:
        text = "User-agent: *\nDisallow: /a*b*c$\n"

        assert policy(text).allows(UA, f"{HOST}/a-one-b-two-c") is False
        assert policy(text).allows(UA, f"{HOST}/a-one-c") is True
        assert policy(text).allows(UA, f"{HOST}/a-one-b-two-d") is True

    def test_a_longer_wildcard_rule_beats_a_shorter_literal_rule(self) -> None:
        text = "User-agent: *\nAllow: /files/\nDisallow: /files/*/private\n"

        assert policy(text).allows(UA, f"{HOST}/files/2026/private/x") is False
        assert policy(text).allows(UA, f"{HOST}/files/2026/public/x") is True

    def test_specificity_is_the_rule_s_length_not_the_text_a_wildcard_swallowed(self) -> None:
        """RFC 9309 §2.2.2 — the codex-tests lane's round-3 finding: a short
        wildcard rule cannot outrank a longer literal one just because the
        request path is long."""
        text = "User-agent: *\nDisallow: /files/*\nAllow: /files/public/\n"

        assert policy(text).allows(UA, f"{HOST}/files/public/a-very-long-document-name.pdf") is True
        assert (
            policy(text).allows(UA, f"{HOST}/files/private/a-very-long-document-name.pdf") is False
        )


class TestWhichGroupApplies:
    def test_a_group_naming_the_product_token_binds_this_crawler(self) -> None:
        text = "User-agent: lovspor-observatory\nDisallow: /\n\nUser-agent: *\nAllow: /\n"

        assert policy(text).allows(UA, f"{HOST}/") is False
        assert policy(text).allows(UA, f"{HOST}/anything") is False

    def test_the_named_group_wins_over_the_star_group_whatever_the_order(self) -> None:
        text = "User-agent: *\nDisallow: /\n\nUser-agent: lovspor-observatory\nAllow: /\n"

        assert policy(text).allows(UA, f"{HOST}/x") is True

    def test_the_token_is_matched_case_insensitively(self) -> None:
        text = "User-agent: Lovspor-Observatory\nDisallow: /\n"

        assert policy(text).allows(UA, f"{HOST}/x") is False

    def test_a_group_for_another_crawler_does_not_bind_this_one(self) -> None:
        text = "User-agent: lovspor\nDisallow: /\n"

        assert policy(text).allows(UA, f"{HOST}/x") is True

    def test_several_user_agent_lines_share_one_group(self) -> None:
        text = "User-agent: a\nUser-agent: lovspor-observatory\nDisallow: /\n"

        assert policy(text).allows(UA, f"{HOST}/x") is False

    def test_consecutive_agents_after_a_closed_group_share_the_next_group(self) -> None:
        text = (
            "User-agent: first\nDisallow: /first\n"
            "User-agent: second\nUser-agent: lovspor-observatory\nDisallow: /private\n"
        )

        assert policy(text).allows(UA, f"{HOST}/private/document") is False
        assert policy(text).allows(UA, f"{HOST}/public") is True

    def test_every_group_naming_the_token_is_one_rule_set(self) -> None:
        """RFC 9309 §2.2.1. Taking only the first group would let the later
        Disallow go unenforced — the codex-tests lane's finding on this PR."""
        text = (
            "User-agent: lovspor-observatory\nAllow: /\n\n"
            "User-agent: lovspor-observatory\nDisallow: /private\n"
        )

        assert policy(text).allows(UA, f"{HOST}/private/document") is False
        assert policy(text).allows(UA, f"{HOST}/public") is True

    def test_repeated_star_groups_are_combined_too(self) -> None:
        text = "User-agent: *\nAllow: /\n\nUser-agent: *\nDisallow: /private\n"

        assert policy(text).allows(UA, f"{HOST}/private/x") is False

    def test_a_file_with_no_group_for_us_allows_everything(self) -> None:
        text = "User-agent: other\nDisallow: /\n"

        assert policy(text).allows(UA, f"{HOST}/x") is True

    def test_rules_before_any_user_agent_line_are_ignored(self) -> None:
        assert policy("Disallow: /\nUser-agent: *\nAllow: /\n").allows(UA, f"{HOST}/x") is True


class TestWhatTheFileSays:
    @pytest.mark.xfail(strict=True, reason="codex proposal, round 4 — owner decision, see #248")
    def test_robots_txt_is_implicitly_allowed_even_when_everything_else_is_disallowed(
        self,
    ) -> None:
        """RFC 9309 §2.2.2 makes the policy document itself implicitly allowed."""
        parsed = policy("User-agent: *\nDisallow: /\n")

        assert parsed.allows(UA, f"{HOST}/robots.txt") is True
        assert parsed.allows(UA, f"{HOST}/private") is False

    def test_an_empty_disallow_permits_everything(self) -> None:
        assert policy("User-agent: *\nDisallow:\n").allows(UA, f"{HOST}/x") is True

    def test_comments_and_blank_lines_are_skipped(self) -> None:
        text = "# policy\n\nUser-agent: *  # all\nDisallow: /private # keep out\n"

        assert policy(text).allows(UA, f"{HOST}/private/x") is False
        assert policy(text).allows(UA, f"{HOST}/public") is True

    def test_the_first_hash_starts_the_comment(self) -> None:
        text = "User-agent: *\nDisallow: /private # first # second\n"

        assert policy(text).allows(UA, f"{HOST}/private/document") is False

    def test_only_a_nonempty_sitemap_directive_declares_a_sitemap(self) -> None:
        text = "Unknown: https://a/not-a-sitemap.xml\nSitemap:\n"

        assert policy(text).sitemaps() == ()

    def test_sitemaps_are_collected_from_anywhere(self) -> None:
        text = "Sitemap: https://a/1.xml\nUser-agent: *\nDisallow: /\nSitemap: https://a/2.xml\n"

        assert policy(text).sitemaps() == ("https://a/1.xml", "https://a/2.xml")

    def test_an_empty_file_allows_everything_and_declares_nothing(self) -> None:
        assert policy("").allows(UA, f"{HOST}/x") is True
        assert policy("").sitemaps() == ()

    def test_a_norwegian_path_matches_whether_encoded_or_not(self) -> None:
        text = "User-agent: *\nDisallow: /høring\n"

        assert policy(text).allows(UA, f"{HOST}/h%C3%B8ring/2026") is False
        assert policy(text).allows(UA, f"{HOST}/høring/2026") is False

    def test_an_encoded_reserved_character_stays_encoded(self) -> None:
        """RFC 9309 §2.2.2: `%2F` is not a path separator, so
        `/documents%2Fprivate` is a different path from `/documents/private`
        — the codex-tests lane's round-2 finding on this PR."""
        text = "User-agent: *\nDisallow: /documents/private\n"

        assert policy(text).allows(UA, f"{HOST}/documents%2Fprivate") is True
        assert policy(text).allows(UA, f"{HOST}/documents/private/x") is False

    def test_a_lone_percent_is_preserved_as_a_reserved_character(self) -> None:
        parsed = policy("User-agent: *\nDisallow: /%\n")

        assert parsed.allows(UA, f"{HOST}/%") is False
        assert parsed.allows(UA, f"{HOST}/public") is True

    def test_an_encoded_star_in_a_rule_is_a_literal_not_a_wildcard(self) -> None:
        text = "User-agent: *\nDisallow: /documents/file-%2A.pdf\n"

        assert policy(text).allows(UA, f"{HOST}/documents/file-%2A.pdf") is False
        assert policy(text).allows(UA, f"{HOST}/documents/file-public.pdf") is True

    def test_an_encoded_unreserved_character_is_the_same_as_the_literal(self) -> None:
        text = "User-agent: *\nDisallow: /a%2Db\n"

        assert policy(text).allows(UA, f"{HOST}/a-b/x") is False
        assert policy(text).allows(UA, f"{HOST}/a%2Db/x") is False

    def test_the_query_string_is_part_of_the_matched_path(self) -> None:
        text = "User-agent: *\nDisallow: /search?q=\n"

        assert policy(text).allows(UA, f"{HOST}/search?q=lov") is False
        assert policy(text).allows(UA, f"{HOST}/search") is True

    def test_a_url_without_an_explicit_path_is_the_root_path(self) -> None:
        parsed = policy("User-agent: *\nDisallow: /$\n")

        assert parsed.allows(UA, HOST) is False


def test_the_decision_does_not_delegate_to_the_interpreter() -> None:
    """The point of the module: no import of urllib.robotparser, so a Python
    bump cannot change what the crawler fetches."""
    source = Path("src/lovspor/observatory/robots.py").read_text(encoding="utf-8")
    imported = {
        name.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for name in node.names
    } | {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)}

    assert "urllib.robotparser" not in imported
