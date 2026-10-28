"""LF ids whose text is held, so a wave does not ask for them (#606).

Two sources, kept apart so the manifest can say which one left an id out:

- ``corpus``: the promoted local regulations of a ``lovverk`` checkout, read
  from ``lokale-forskrifter/manifest.json`` through the promotion reader.
  Only ``current`` records with an ``lf-`` id count; a removed record or an
  ``lk-`` id holds nothing. A checkout that is missing, not a ``lovverk``
  checkout, inside this repository or whose manifest does not read refuses
  the wave: it never falls back to "nothing held".
- ``file``: the operator's ``--held`` file, for texts held outside the corpus.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lovspor.errors import InnsynRefusedError, PromotionRefusedError
from lovspor.observatory.storage import engine_root
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.identity import ledger_lf_id

HeldSource = Literal["corpus", "file"]


class HeldIds(BaseModel):
    """The ids held, by source; ``corpus`` wins when both name an id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    corpus: frozenset[str] = frozenset()
    file: frozenset[str] = frozenset()

    @property
    def all(self) -> frozenset[str]:
        return self.corpus | self.file

    def source(self, lf_id: str) -> HeldSource | None:
        if lf_id in self.corpus:
            return "corpus"
        if lf_id in self.file:
            return "file"
        return None


def corpus_held_ids(corpus: Path) -> frozenset[str]:
    """The LF ids of the current ``lf-`` documents of the local dataset in ``corpus``.

    Raises:
        InnsynRefusedError: ``corpus`` is not a readable ``lovverk`` checkout.
    """
    try:
        checkout = CorpusCheckout(corpus, [engine_root()])
        documents = checkout.local_manifest().documents
    except PromotionRefusedError as exc:
        raise InnsynRefusedError(str(exc)) from exc
    except OSError as exc:
        raise InnsynRefusedError(f"{corpus}: {exc}") from exc
    current = (doc_id for doc_id, record in documents.items() if record.status == "current")
    return frozenset(lf_id for lf_id in map(ledger_lf_id, current) if lf_id is not None)


def file_held_ids(path: Path | None) -> frozenset[str]:
    """LF ids whose text is held: one per line, ``#`` starts a comment."""
    if path is None:
        return frozenset()
    try:
        lines = path.read_bytes().decode().splitlines()
    except FileNotFoundError as exc:
        raise InnsynRefusedError(f"{path}: no such --held file") from exc
    except UnicodeDecodeError as exc:
        raise InnsynRefusedError(f"{path}: not UTF-8 at byte {exc.start}") from exc
    ids = (line.partition("#")[0].strip() for line in lines)
    return frozenset(lf_id for lf_id in ids if lf_id)
