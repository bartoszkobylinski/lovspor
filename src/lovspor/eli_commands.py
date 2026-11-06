"""``lovspor ops eli-audit``: how much of the corpus has an official ELI (#819).

Read-only. It plans the corpus at one pinned commit exactly as the site
publisher does, maps every published document's ``ref_id`` with
:func:`lovspor.eli.act_eli` and prints the counts as JSON. Nothing is
written, nothing is requested from lovdata.no, and no URI is claimed to
resolve. Registered on the ``ops`` group by :mod:`lovspor.ops_cli`, because
:mod:`lovspor.cli` sits at its size ratchet; the decorated command only calls
:func:`eli_audit_report`, so the body stays inside the mutation gate.
"""

import json
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from lovspor.eli import audit_ref_ids
from lovspor.ops.commands import ops_app
from lovspor.publish.inventory import PublishError, build_inventory
from lovspor.snapshot import CorpusSnapshot


def eli_audit_report(corpus: Path, ref: str, sample_size: int) -> str:
    """The audit of every published document at ``ref``, as sorted JSON."""
    sha = _resolve_commit(corpus, ref)
    snapshot = CorpusSnapshot(corpus, sha)
    inventory = build_inventory(snapshot.manifest, snapshot.read_text)
    audit = audit_ref_ids((plan.ref_id for plan in inventory.documents), sample_size)
    report = {"corpus_commit": sha, **audit.model_dump()}
    return json.dumps(report, sort_keys=True, ensure_ascii=False, indent=1)


def _resolve_commit(corpus: Path, ref: str) -> str:
    """``ref`` as a full commit SHA, so the report names its input exactly."""
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],  # noqa: S607
            cwd=corpus,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError) as error:
        raise PublishError(f"not a readable corpus commit: {ref} in {corpus}") from error
    return result.stdout.strip()


@ops_app.command(name="eli-audit")
def eli_audit(
    corpus: Annotated[Path, typer.Option(help="A lovverk checkout; read through git only.")],
    ref: Annotated[str, typer.Option(help="Corpus commit to audit.")] = "HEAD",
    samples: Annotated[int, typer.Option(min=0, help="Sample ref_ids per reason.")] = 3,
) -> None:
    """Count published documents whose ref_id maps to an official act ELI."""
    try:
        typer.echo(eli_audit_report(corpus, ref, samples))
    except PublishError as error:
        typer.echo(f"eli audit refused: {error}", err=True)
        raise typer.Exit(code=1) from error
