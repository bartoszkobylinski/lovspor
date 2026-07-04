"""Property-based invariants for lovspor.history.

These cover the model and renderer contract: any valid ``HistoryRecord``
must JSON round-trip via Pydantic and must render to non-empty,
trailing-newline-terminated Markdown without crashing.
"""

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from lovspor.history import (
    HistoryEvent,
    HistoryRecord,
    render_history_markdown,
)

_EVENT_TYPE = st.sampled_from(["added", "updated", "renamed", "removed"])
_COMMIT_SHA = st.text(
    alphabet="abcdef0123456789",
    min_size=7,
    max_size=7,
)
_SUBJECT = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x017F),
    min_size=1,
    max_size=200,
)
_PATH = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-/.",
    min_size=1,
    max_size=80,
)
# Slugs are basenames in HistoryRecord (validated to forbid /, \, ..,
# null) so we restrict to the alphabet that lovspor.rendering.slug
# actually emits in production.
_SAFE_SLUG = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-æøåäöü",
    min_size=1,
    max_size=80,
)
_DATE = st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 1, 1))


@st.composite
def _events(draw: st.DrawFn) -> HistoryEvent:
    event_type = draw(_EVENT_TYPE)
    is_rename = event_type == "renamed"
    return HistoryEvent(
        date=draw(_DATE),
        commit=draw(_COMMIT_SHA),
        type=event_type,
        subject=draw(_SUBJECT),
        from_path=draw(_PATH) if is_rename else None,
        to_path=draw(_PATH) if is_rename else None,
        lines_added=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=10000))),
        lines_removed=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=10000))),
    )


@st.composite
def _records(draw: st.DrawFn) -> HistoryRecord:
    return HistoryRecord(
        slug=draw(_SAFE_SLUG),
        doc_id=draw(_PATH),
        events=draw(st.lists(_events(), min_size=0, max_size=20)),
    )


@given(_records())
def test_history_record_json_round_trips(record: HistoryRecord) -> None:
    """Pydantic JSON dump + load is an identity on HistoryRecord."""
    payload = record.model_dump(mode="json")
    assert HistoryRecord.model_validate(payload) == record


@given(_records())
def test_render_never_crashes(record: HistoryRecord) -> None:
    """The renderer must accept any valid record without raising."""
    output = render_history_markdown(record)
    assert isinstance(output, str)


@given(_records())
def test_render_output_ends_with_newline(record: HistoryRecord) -> None:
    """Trailing newline contract — keeps diffs tidy."""
    output = render_history_markdown(record)
    assert output.endswith("\n")


@given(_records())
def test_render_output_starts_with_frontmatter_then_heading(record: HistoryRecord) -> None:
    """Output opens with NLOD front matter, then the slug's H1 heading."""
    output = render_history_markdown(record)
    assert output.startswith("---\n")
    assert 'source_license: "NLOD 2.0"' in output
    assert f"---\n\n# {record.slug} — Change history" in output
