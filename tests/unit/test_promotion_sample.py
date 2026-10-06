"""The owner's spot-check sample of a promotion batch (ADR-0016 4g, slice S8)."""

from __future__ import annotations

import hashlib
from decimal import Decimal, localcontext

import pytest

from lovspor.errors import PromotionRefusedError
from lovspor.promotion.decisions import ArtifactKey
from lovspor.promotion.sample import draw_sample, parse_sample_rate, sample_size


def key(n: int) -> ArtifactKey:
    return ArtifactKey(
        authority_id="0301",
        sha256=f"{n:064x}",
        source_url=f"https://eksempel.kommune.invalid/forskrift/{n}",
    )


POPULATION = tuple(key(n) for n in range(40))


class TestParseSampleRate:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1", Decimal(1)),
            ("1.0", Decimal(1)),
            ("0.05", Decimal("0.05")),
            (" 0.5 ", Decimal("0.5")),
        ],
    )
    def test_a_rate_in_the_unit_interval_is_read_exactly(
        self, text: str, expected: Decimal
    ) -> None:
        assert parse_sample_rate(text) == expected

    @pytest.mark.parametrize("text", ["0", "-0.1", "1.01", "5%", "", "abc", "NaN", "inf"])
    def test_anything_else_is_refused(self, text: str) -> None:
        with pytest.raises(PromotionRefusedError, match="sample rate"):
            parse_sample_rate(text)


class TestSampleSize:
    @pytest.mark.parametrize(
        ("rate", "population", "expected"),
        [
            (Decimal(1), 40, 40),
            (Decimal("0.05"), 40, 2),
            (Decimal("0.05"), 41, 3),
            (Decimal("0.01"), 3, 1),
            (Decimal("0.5"), 0, 0),
        ],
    )
    def test_the_size_is_the_rate_rounded_up(
        self, rate: Decimal, population: int, expected: int
    ) -> None:
        assert sample_size(rate, population) == expected

    def test_a_rate_past_the_context_precision_still_rounds_up(self) -> None:
        """A Decimal product is rounded to the context's 28 digits before the
        ceiling, so 0.5000…0001 x 2 came out as exactly 1 and the review
        sample shrank by an item (Codex test on PR #564)."""
        rate = parse_sample_rate("0.50000000000000000000000000001")
        with localcontext() as context:
            context.prec = 28
            assert sample_size(rate, 2) == 2


class TestDrawSample:
    def test_empty_population_draws_nothing(self) -> None:
        assert draw_sample(iter(()), "batch", Decimal(1)) == ()

    def test_duplicates_do_not_inflate_fractional_sample_size(self) -> None:
        unique = POPULATION[:3]
        duplicated = iter((*unique, *unique, *unique))

        drawn = draw_sample(duplicated, "batch", Decimal("0.5"))

        assert len(drawn) == 2
        assert drawn == draw_sample(unique, "batch", Decimal("0.5"))

    def test_tiny_positive_rate_draws_one_item(self) -> None:
        assert len(draw_sample(POPULATION, "batch", parse_sample_rate("1e-100"))) == 1

    def test_the_same_seed_draws_the_same_sample(self) -> None:
        first = draw_sample(POPULATION, "batch-0301-1", Decimal("0.25"))
        again = draw_sample(POPULATION, "batch-0301-1", Decimal("0.25"))

        assert first == again
        assert len(first) == 10

    def test_the_input_order_does_not_move_the_sample(self) -> None:
        forward = draw_sample(POPULATION, "batch-0301-1", Decimal("0.25"))
        backward = draw_sample(tuple(reversed(POPULATION)), "batch-0301-1", Decimal("0.25"))

        assert forward == backward

    def test_another_seed_draws_another_sample(self) -> None:
        one = draw_sample(POPULATION, "batch-0301-1", Decimal("0.25"))
        two = draw_sample(POPULATION, "batch-0301-2", Decimal("0.25"))

        assert one != two

    def test_the_sample_is_listed_in_a_stable_order(self) -> None:
        drawn = draw_sample(POPULATION, "batch-0301-1", Decimal("0.25"))

        assert list(drawn) == sorted(drawn, key=lambda k: (k.source_url, k.sha256))

    def test_a_full_rate_samples_every_item(self) -> None:
        drawn = draw_sample(POPULATION, "batch-0301-1", Decimal(1))

        assert set(drawn) == set(POPULATION)

    def test_a_repeated_item_is_drawn_once(self) -> None:
        drawn = draw_sample((key(1), key(1), key(2)), "batch", Decimal(1))

        assert drawn == (key(1), key(2))

    def test_the_draw_is_pinned_so_a_change_of_method_is_seen(self) -> None:
        drawn = draw_sample(POPULATION, "batch-0301-1", Decimal("0.1"))

        assert [k.source_url.rsplit("/", 1)[1] for k in drawn] == PINNED

    @pytest.mark.parametrize("seed", ["", "  "])
    def test_a_blank_seed_is_refused(self, seed: str) -> None:
        with pytest.raises(PromotionRefusedError, match="seed") as exc_info:
            draw_sample(POPULATION, seed, Decimal(1))

        assert str(exc_info.value) == "the sample seed (the batch id) must not be blank"

    def test_unicode_seed_and_urls_follow_the_documented_utf8_rank(self) -> None:
        seed = "blå-批次"
        population = tuple(
            ArtifactKey(
                authority_id="0301",
                sha256=f"{n:064x}",
                source_url=f"https://eksempel.invalid/§/規則/{n}",
            )
            for n in range(8)
        )
        ranked = sorted(
            population,
            key=lambda item: hashlib.sha256(
                f"{seed}\x1f{item.sha256}\x1f{item.source_url}".encode()
            ).hexdigest(),
        )
        expected = tuple(sorted(ranked[:2], key=lambda item: (item.source_url, item.sha256)))

        assert draw_sample(population, seed, Decimal("0.25")) == expected

    def test_a_seed_with_an_unpaired_surrogate_cannot_be_ranked(self) -> None:
        seed = "batch-\ud800"

        with pytest.raises(UnicodeEncodeError) as exc_info:
            draw_sample((key(1),), seed, Decimal(1))

        assert exc_info.value.object == f"{seed}\x1f{key(1).sha256}\x1f{key(1).source_url}"
        assert exc_info.value.reason == "surrogates not allowed"


#: Computed by hand from the documented rank (SHA-256 of seed, hash and URL), not by the code.
PINNED = ["16", "17", "22", "3"]
