"""Synthetic observations for the forarbeider review tests. No real chapter text."""

from datetime import datetime
from pathlib import Path

from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_models import (
    ChapterResult,
    HoldReason,
    TextBlock,
    result_text,
    text_hash,
)
from lovspor.forarbeider.staging import write_observation

FIXTURE = Path(__file__).parents[1] / "fixtures" / "forarbeider" / "chapter-l.html"
URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=2"
RETRIEVED = "2100-04-01T12:00:00Z"
MARKER = "Synthetisk personopplysning som aldri skal i loggen"


def chapter() -> ChapterResult:
    moment = datetime.fromisoformat(RETRIEVED)
    return build_chapter_result(FIXTURE.read_bytes(), URL, moment)


def with_changed_text(result: ChapterResult) -> ChapterResult:
    assert result.blocks is not None
    blocks = (*result.blocks[:-1], TextBlock(kind="paragraph", text=MARKER))
    return result.model_copy(update={"blocks": blocks, "text_sha256": text_hash(blocks)})


def with_changed_source(result: ChapterResult) -> ChapterResult:
    return result.model_copy(update={"source_sha256": "f" * 64})


def withheld(result: ChapterResult) -> ChapterResult:
    reasons = (HoldReason.REVIEW_REQUIRED, HoldReason.TABLE)
    return result.model_copy(update={"blocks": None, "text_sha256": None, "hold_reasons": reasons})


def saved(result: ChapterResult, path: Path) -> Path:
    write_observation(path, result_text(result))
    return path
