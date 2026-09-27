"""Observatory commands that audit the archive without changing it.

`verify` asks whether the log and the stored bytes account for each other;
`composition` asks what the log is made of. Both only read, and both refuse
to guess past a damaged log.
"""

import typer

from lovspor.observatory.app import observatory_app
from lovspor.observatory.log import ObservationLog, SnapshotVerification, verify_snapshot
from lovspor.observatory.outcomes import ArchiveComposition, collect_composition
from lovspor.observatory.registry_io import _root


# Each pairs a defect list with what its presence means. Kept together so the
# report cannot drift from the model: a field added to SnapshotVerification and
# not added here would be counted by `ok` and never explained to anyone.
def _defects(result: SnapshotVerification) -> list[str]:
    counted = (
        (result.missing_blobs, "blobs gone with no tombstone"),
        (result.hash_mismatches, "blobs that no longer hash to their record"),
        (result.orphan_blobs, "blobs no record mentions"),
        (result.unremoved_tombstones, "tombstoned blobs still on disk"),
        (result.tombstones_without_observation, "tombstones for hashes never observed"),
        (result.observations_after_tombstone, "observations appended after their tombstone"),
    )
    return [f"{len(found)} {label}" for found, label in counted if found]


def _log_damage(result: SnapshotVerification) -> list[str]:
    """The two log defects, each with the action it calls for.

    They call for opposite actions, which is the whole reason the audit
    separates them: one line may be dropped, the other must not be touched.
    """
    damage = []
    if result.incomplete_final_record:
        damage.append(
            "the final record was never finished — an interrupted run leaves exactly this, "
            "and the fetch it describes was never recorded. `observatory repair` removes that "
            "one line and keeps the log as it stood."
        )
    if result.malformed_lines:
        numbers = ", ".join(str(number) for number in result.malformed_lines)
        damage.append(
            f"line(s) {numbers} are corrupted — an interrupted append cannot produce this, "
            "so the storage itself is suspect. Do not truncate: restore from backup."
        )
    return damage


@observatory_app.command("verify")
def verify() -> None:
    """Audit the snapshot: the log and the stored bytes must account for each other.

    Exits non-zero when they do not, so a scheduled run can act on it. A
    tombstoned blob is not a defect — a recorded, explained removal is the
    sanctioned way for bytes to disappear.
    """
    result = verify_snapshot(ObservationLog(_root()))
    typer.echo(f"artifacts checked: {result.artifacts_checked}")
    if result.tombstoned:
        typer.echo(f"removed under a tombstone: {len(result.tombstoned)} (sanctioned)")
    for line in _log_damage(result):
        typer.echo(f"  {line}")
    for line in _defects(result):
        typer.echo(f"  {line}")
    if result.ok:
        typer.echo("snapshot ok")
        return
    typer.echo("snapshot NOT ok")
    raise typer.Exit(1)


@observatory_app.command("composition")
def composition() -> None:
    """What the archive is made of, and the failure rate it actually has.

    The log files a followed redirect as a `fetch_failure`, because that hop
    returned no bytes — and 75% of everything it calls a failure is one. Read
    by `kind` alone the archive reports a failure rate four times its real one
    (issue #188), and until this command there was nowhere to read it any other
    way: every summary the engine prints is derived from what a fetch returned,
    not from the log, so an auditor opening the archive wrote their own query
    and got the wrong number.

    One streamed pass. The counts are the answer, not the archive, so the
    memory this needs is the size of the report (issue #199).
    """
    log = ObservationLog(_root())
    found = ArchiveComposition()
    if not log.scan_into(collect_composition(found)).complete:
        typer.echo(
            "Refused: the observation log is damaged. Run `observatory verify` first.", err=True
        )
        raise typer.Exit(1)
    _echo_composition(found)


def _echo_composition(found: ArchiveComposition) -> None:
    """The composition, with both rates side by side.

    The wrong figure is printed next to the right one on purpose: somebody has
    already quoted it, and a report that silently replaces it leaves them
    unable to tell which number they had.
    """
    typer.echo(f"records:          {found.records}")
    typer.echo(f"  artifacts:      {found.artifacts}")
    typer.echo(f"  redirect hops:  {found.hops}  (recorded as fetch_failure, not failures)")
    typer.echo(f"  lost documents: {found.lost}")
    typer.echo(f"  tombstones:     {found.tombstones}")
    typer.echo(
        f"corrections:      {found.refiled} re-filed, {found.record_tombstones} record "
        "tombstones  (not observations; not in the total or the rates)"
    )
    typer.echo(f"\nlost documents:   {found.loss_rate:.2%} of all records")
    typer.echo(f"counting kind alone: {found.naive_failure_rate:.2%} — the #188 figure, overstated")
    if found.by_outcome:
        typer.echo("\nfailures by outcome")
        for outcome, count in found.by_outcome.most_common():
            typer.echo(f"  {outcome:32} {count}")
