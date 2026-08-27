"""Tests for lovspor.observatory.events — what an operator decided, and why.

Nothing is mocked. Events are appended to a real file under a real validated
root, because the append-only property and "can this still be read after the
registry was rewritten" are the behaviour under test — an in-memory fixture
would prove neither.
"""

import fcntl
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.errors import LogIntegrityError, ParseError
from lovspor.observatory import events
from lovspor.observatory.events import (
    SOURCE_EVENTS_FILENAME,
    SourceDomainReplaced,
    append_source_event,
    domain_replacement,
    events_for,
    read_source_events,
    record_fingerprint,
    source_events_path,
)
from lovspor.observatory.registry import AccessPolicyCheck, SourceRecord, activate
from lovspor.observatory.storage import ObservatoryRoot

CHANGED_AT = datetime(2026, 8, 24, 11, 0, tzinfo=UTC)
REASON = "official haugesund.no redirects to haugesund.kommune.no"


@pytest.fixture
def root(tmp_path: Path) -> ObservatoryRoot:
    return ObservatoryRoot(tmp_path / "observatory", ())


def _source(domain: str = "haugesund.no") -> SourceRecord:
    return SourceRecord(
        authority_type="kommune",
        authority_id="1106",
        name="Haugesund",
        canonical_domain=domain,
    )


def _policy(domain: str = "haugesund.no") -> AccessPolicyCheck:
    return AccessPolicyCheck(
        checked_at=datetime(2026, 8, 24, 10, 35, tzinfo=UTC),
        robots_txt_url=f"https://www.{domain}/robots.txt",
        robots_allows=True,
        terms_reviewed=True,
        terms_permit_capture=True,
        rate_limit_seconds=7.0,
        user_agent="lovspor-observatory/0.1",
        reviewed_by="Bartosz Kobyliński",
    )


def _event(**overrides: object) -> SourceDomainReplaced:
    fields: dict[str, object] = {
        "authority_id": "1106",
        "from_domain": "haugesund.no",
        "to_domain": "haugesund.kommune.no",
        "reason": REASON,
        "changed_at": CHANGED_AT,
        "changed_by": "Bartosz Kobyliński",
        "previous_record_sha256": "a" * 64,
    }
    fields.update(overrides)
    return SourceDomainReplaced.model_validate(fields)


class TestTheFingerprintIdentifiesARecord:
    """A timestamp says when a clearance was withdrawn. Only the content says
    *which* — and the registry that held it has been rewritten since."""

    def test_it_is_the_hash_of_the_canonical_registry_json(self) -> None:
        """Recomputable from an archived sources.json with nothing but a
        SHA-256 implementation, which is what makes it evidence rather than an
        internal identifier."""
        record = activate(_source(), _policy())
        canonical = json.dumps(record.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)

        assert record_fingerprint(record) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def test_two_records_differing_only_in_clearance_fingerprint_differently(self) -> None:
        assert record_fingerprint(_source()) != record_fingerprint(activate(_source(), _policy()))

    def test_the_same_record_fingerprints_the_same_way_twice(self) -> None:
        assert record_fingerprint(activate(_source(), _policy())) == record_fingerprint(
            activate(_source(), _policy())
        )

    def test_norwegian_characters_do_not_change_the_hash_by_escaping(self) -> None:
        """`ensure_ascii=False` matches how the registry is written; escaping
        here would make the fingerprint unreproducible from the file."""
        record = _source().model_copy(update={"name": "Bærum"})

        canonical = json.dumps(record.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        assert "Bærum" in canonical
        assert record_fingerprint(record) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TestAppendingADecision:
    def test_an_append_is_locked_and_synced_before_returning(
        self, root: ObservatoryRoot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two writers — an operator at a terminal and a scheduled job — and a
        torn line costs the answer to which clearance was withdrawn."""
        locked: list[tuple[int, int]] = []
        synced: list[int] = []
        monkeypatch.setattr(
            events.fcntl,
            "flock",
            lambda descriptor, operation: locked.append((descriptor, operation)),
        )
        monkeypatch.setattr(events.os, "fsync", synced.append)

        append_source_event(root, _event())

        assert len(locked) == 1
        assert locked[0][1] == fcntl.LOCK_EX
        assert synced == [locked[0][0]]

    def test_the_append_encoding_is_explicitly_utf8(
        self, root: ObservatoryRoot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_open = Path.open
        encodings: list[str | None] = []

        def recording_open(path: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            encodings.append(kwargs.get("encoding"))  # type: ignore[arg-type]
            return original_open(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "open", recording_open)

        append_source_event(root, _event())

        assert encodings == ["utf-8"]

    def test_a_deep_archive_root_is_created_for_the_first_event(self, tmp_path: Path) -> None:
        root = ObservatoryRoot(tmp_path / "a" / "b" / "observatory", ())

        append_source_event(root, _event())

        assert source_events_path(root).exists()

    def test_the_log_lives_beside_the_registry_it_explains(self, root: ObservatoryRoot) -> None:
        assert source_events_path(root) == root.path / SOURCE_EVENTS_FILENAME

    def test_a_second_event_does_not_replace_the_first(self, root: ObservatoryRoot) -> None:
        """Append-only is the whole point: the previous decision is exactly
        what `sources.json` cannot keep."""
        append_source_event(root, _event())
        append_source_event(root, _event(to_domain="haugesund.example.invalid"))

        recorded = read_source_events(source_events_path(root))

        assert [event.to_domain for event in recorded] == [
            "haugesund.kommune.no",
            "haugesund.example.invalid",
        ]

    def test_each_event_is_one_line(self, root: ObservatoryRoot) -> None:
        append_source_event(root, _event())
        append_source_event(root, _event())

        assert len(source_events_path(root).read_text(encoding="utf-8").splitlines()) == 2

    def test_an_event_round_trips_without_losing_a_field(self, root: ObservatoryRoot) -> None:
        append_source_event(root, _event())

        assert read_source_events(source_events_path(root)) == [_event()]


class TestReadingTheHistory:
    def test_no_file_yet_is_not_an_error(self, root: ObservatoryRoot) -> None:
        """An archive that has never needed a migration has no history, which
        is not the same as an unreadable one."""
        assert read_source_events(source_events_path(root)) == []

    def test_blank_lines_between_events_are_ignored(self, root: ObservatoryRoot) -> None:
        append_source_event(root, _event())
        path = source_events_path(root)
        path.write_text(f"\n{path.read_text(encoding='utf-8')}\n\n", encoding="utf-8")

        assert len(read_source_events(path)) == 1

    def test_an_unreadable_line_refuses_rather_than_being_skipped(
        self, root: ObservatoryRoot
    ) -> None:
        """Skipping would shrink the decision history silently, which is the
        one outcome an append-only record must not produce."""
        path = source_events_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"event": "source_domain_replaced"}\n', encoding="utf-8")

        with pytest.raises(LogIntegrityError, match="unreadable source event") as exc:
            read_source_events(path)

        assert str(exc.value).startswith(f"{path}:1:")

    def test_damage_after_a_good_event_reports_its_own_line(self, root: ObservatoryRoot) -> None:
        append_source_event(root, _event())
        path = source_events_path(root)
        path.write_text(path.read_text(encoding="utf-8") + "{ truncated\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError) as exc:
            read_source_events(path)

        assert str(exc.value).startswith(f"{path}:2:")

    def test_an_unknown_field_is_damage_too(self, root: ObservatoryRoot) -> None:
        """`extra="forbid"`: a field a newer writer added must not be dropped
        on the floor by an older reader answering from a partial record."""
        append_source_event(root, _event())
        path = source_events_path(root)
        line = json.loads(path.read_text(encoding="utf-8"))
        line["approved_by"] = "someone"
        path.write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError):
            read_source_events(path)

    def test_a_naive_timestamp_is_damage(self, root: ObservatoryRoot) -> None:
        append_source_event(root, _event())
        path = source_events_path(root)
        line = json.loads(path.read_text(encoding="utf-8"))
        line["changed_at"] = "2026-08-24T11:00:00"
        path.write_text(f"{json.dumps(line)}\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError):
            read_source_events(path)

    def test_one_authoritys_history_is_selectable(self, root: ObservatoryRoot) -> None:
        append_source_event(root, _event())
        append_source_event(root, _event(authority_id="4601", from_domain="bergen.no"))

        assert [event.authority_id for event in events_for(source_events_path(root), "1106")] == [
            "1106"
        ]

    def test_an_authority_with_no_history_reads_as_empty(self, root: ObservatoryRoot) -> None:
        append_source_event(root, _event())

        assert events_for(source_events_path(root), "4601") == []


class TestBuildingTheEventFromTheRecord:
    """The event is built before the registry is written, because the
    fingerprint has to identify the clearance being withdrawn."""

    def test_it_fingerprints_the_record_as_it_stands(self) -> None:
        record = activate(_source(), _policy())

        event = domain_replacement(
            record=record,
            to_domain="haugesund.kommune.no",
            reason=REASON,
            changed_at=CHANGED_AT,
            changed_by="Bartosz Kobyliński",
        )

        assert event.previous_record_sha256 == record_fingerprint(record)
        assert (event.from_domain, event.to_domain) == ("haugesund.no", "haugesund.kommune.no")

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("reason", ""),
            ("reason", " \t "),
            ("changed_by", ""),
            ("changed_by", " \t "),
        ],
    )
    def test_an_unattributed_or_unexplained_change_is_refused(self, field: str, value: str) -> None:
        """Both are human input about traffic to someone else's server. An
        empty one is not evidence that anybody decided anything, and this
        module never fills one in."""
        arguments: dict[str, object] = {
            "record": activate(_source(), _policy()),
            "to_domain": "haugesund.kommune.no",
            "reason": REASON,
            "changed_at": CHANGED_AT,
            "changed_by": "Bartosz Kobyliński",
        }
        arguments[field] = value

        with pytest.raises(ParseError, match="refusing to record this replacement"):
            domain_replacement(**arguments)  # type: ignore[arg-type]

    def test_a_name_typed_with_stray_whitespace_is_stored_trimmed(self) -> None:
        """An event log holding two spellings of one person cannot be grouped
        by who decided what."""
        event = domain_replacement(
            record=activate(_source(), _policy()),
            to_domain="haugesund.kommune.no",
            reason=f"  {REASON}\n",
            changed_at=CHANGED_AT,
            changed_by="  Bartosz Kobyliński  ",
        )

        assert event.changed_by == "Bartosz Kobyliński"
        assert event.reason == REASON

    def test_a_blank_domain_is_refused_like_a_blank_reason(self) -> None:
        with pytest.raises(ParseError):
            domain_replacement(
                record=activate(_source(), _policy()),
                to_domain="   ",
                reason=REASON,
                changed_at=CHANGED_AT,
                changed_by="Bartosz Kobyliński",
            )

    def test_a_naive_timestamp_is_refused_at_the_boundary(self) -> None:
        with pytest.raises(ParseError):
            domain_replacement(
                record=_source(),
                to_domain="haugesund.kommune.no",
                reason=REASON,
                changed_at=datetime(2026, 8, 24, 11, 0),
                changed_by="Bartosz Kobyliński",
            )

    def test_the_recorded_event_names_itself(self) -> None:
        assert _event().event == "source_domain_replaced"


def test_the_events_module_does_not_reach_for_os_fsync_indirectly() -> None:
    """The append path is the only writer, and it fsyncs. Guards the import
    the lock/sync test monkeypatches, so that test cannot silently pass
    against a module that stopped using it."""
    assert events.os is os
