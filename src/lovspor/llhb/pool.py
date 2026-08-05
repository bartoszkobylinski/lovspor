"""LLHB Stage 3 pool orchestration: build, id, validate, dedup, report.

Deterministic pipeline over a pinned corpus:

1. inventory a seeded-shuffled sample of acts (topics, ministries);
2. scan the full corpus for the REAL ambiguity population (C5);
3. build candidates per category, ground truth first, under diversity
   caps (per-act, per-category-per-act, per-provision);
4. assign stable sequential ids in emission order — rejected candidates
   keep their ids, ids are never recycled;
5. validate everything with the Stage 2 ``CandidateValidator``;
6. exact-dedup + transparent near-duplicate flagging;
7. produce distribution, calibration and review-queue artifacts.

No model calls anywhere. Same pinned corpus + same config + same run
metadata → byte-identical pool (tested).
"""

from collections import Counter
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from lovspor.llhb import templates as tpl
from lovspor.llhb.corpus_pin import CorpusPin
from lovspor.llhb.generation import (
    GENERATION_SEED_DEFAULT,
    ActInfo,
    CorpusSampler,
    SectionInfo,
    base_case,
    difficulty_for,
    display_name,
    fabricated_quote_for,
    mutate_quote,
    quote_ref_fields,
    quote_span,
    scan_duplicate_ids,
    section_shape,
    trap_section_ids,
)
from lovspor.llhb.names import ActNameIndex
from lovspor.llhb.validation import CandidateValidator, IssueSeverity
from lovspor.mcp import CorpusAmbiguousSectionError, CorpusNotFoundError, CorpusReader

_NEAR_DUP_JACCARD = 0.8
_STRATIFIED_EVERY = 10

DEFAULT_TARGETS: dict[str, int] = {
    "C1": 75,
    "C2": 65,
    "C3": 55,
    "C4": 50,
    "C5": 30,
    "C6": 55,
    "C7": 40,
    "C8": 30,
}


class PoolConfig(BaseModel):
    schema_path: Path  # the committed case.schema.json — always injected, never guessed
    seed: int = GENERATION_SEED_DEFAULT
    targets: dict[str, int] = DEFAULT_TARGETS
    inventory_size: int = 320
    per_act_category_cap: int = 2
    per_act_total_cap: int = 8


class GenerationRun(BaseModel):
    """Non-corpus inputs that make the run reproducible and attributable."""

    lovspor_commit: str
    created: str  # ISO date
    timestamp: str  # ISO datetime of the run


class PoolResult(BaseModel):
    candidates: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    ledger: list[dict[str, Any]]
    review_queue: list[dict[str, Any]]
    dedup_report: dict[str, Any]
    distribution: dict[str, Any]
    ambiguity_scan: list[dict[str, Any]]
    name_calibration: dict[str, Any]
    generation_manifest: dict[str, Any]


class _Caps:
    """Diversity bookkeeping: per-act, per-(category, act), per-provision."""

    def __init__(self, config: PoolConfig) -> None:
        self._config = config
        self._per_act: Counter[str] = Counter()
        self._per_cat_act: Counter[tuple[str, str]] = Counter()
        self._per_provision: Counter[tuple[str, str, str]] = Counter()

    def allows(self, category: str, slug: str, section_id: str | None) -> bool:
        if self._per_act[slug] >= self._config.per_act_total_cap:
            return False
        if self._per_cat_act[(category, slug)] >= self._config.per_act_category_cap:
            return False
        if section_id is not None:
            return self._per_provision[(category, slug, section_id)] < 2  # noqa: PLR2004
        return True

    def take(self, category: str, slug: str, section_id: str | None) -> None:
        self._per_act[slug] += 1
        self._per_cat_act[(category, slug)] += 1
        if section_id is not None:
            self._per_provision[(category, slug, section_id)] += 1


class _Builder:
    """Stateful candidate assembly for one pool run."""

    def __init__(
        self,
        reader: CorpusReader,
        pin: CorpusPin,
        config: PoolConfig,
        run: GenerationRun,
    ) -> None:
        self.reader = reader
        self.pin = pin
        self.config = config
        self.run = run
        self.caps = _Caps(config)
        self.counters: Counter[str] = Counter()
        self.cases: list[dict[str, Any]] = []
        self.validator: CandidateValidator | None = None

    def emit(self, category: str, case: dict[str, Any]) -> None:
        self.counters[category] += 1
        case["case_id"] = f"llhb-v1-{category}-{self.counters[category]:03d}"
        case["category"] = category
        self.cases.append(case)

    def new_case(self, subcategory: str, question: str, difficulty: str) -> dict[str, Any]:
        provenance: dict[str, str | None] = {
            "method": "corpus-selected-template",
            "phrasing_model": None,
            "generator_commit": self.run.lovspor_commit,
            "created": self.run.created,
        }
        case = base_case(self.pin, provenance)
        case.update(
            subcategory=subcategory,
            question=question,
            difficulty=difficulty,
            validation={
                "status": "pass",
                "validated_at": self.run.timestamp,
                "validator_commit": self.run.lovspor_commit,
                "spot_checked": False,
            },
        )
        return case


def _rotate(acts: list[ActInfo], eighth: int) -> list[ActInfo]:
    offset = (len(acts) * eighth) // 8
    return acts[offset:] + acts[:offset]


def _section_evidence(act: ActInfo, section_id: str, heading: str) -> dict[str, Any]:
    return {
        "get_section": {"slug": act.slug, "section_id": section_id, "heading": heading},
    }


def _build_c1(builder: _Builder, acts: list[ActInfo]) -> None:
    target = builder.config.targets["C1"]
    for act in _rotate(acts, 0):
        if builder.counters["C1"] >= target:
            return
        for section, topic in act.topic_sections()[:1]:
            if not builder.caps.allows("C1", act.slug, section.section_id):
                continue
            case = builder.new_case(
                "factual",
                tpl.fill(
                    tpl.C1_FRAMES,
                    builder.counters["C1"],
                    act=act.display_name,
                    topic=topic,
                ),
                difficulty_for(act.section_count),
            )
            case.update(
                expected_behaviour="answer_with_citation",
                expected_act_slug=act.slug,
                expected_section_id=section.section_id,
                citation_exists=True,
                ground_truth_evidence=_section_evidence(act, section.section_id, section.heading),
                deterministic_criteria=["expected-provision-cited", "no-invalid-citations"],
            )
            builder.caps.take("C1", act.slug, section.section_id)
            builder.emit("C1", case)


def _build_c2(builder: _Builder, acts: list[ActInfo]) -> None:
    target = builder.config.targets["C2"]
    for act in _rotate(acts, 1):
        if builder.counters["C2"] >= target:
            return
        for section, topic in act.topic_sections()[1:2] or act.topic_sections()[:1]:
            question = tpl.fill(tpl.C2_FRAMES, builder.counters["C2"], topic=topic)
            leaks = act.slug.casefold() in question.casefold() or "§" in question
            if leaks or not builder.caps.allows("C2", act.slug, section.section_id):
                continue
            case = builder.new_case("discovery", question, difficulty_for(act.section_count))
            case.update(
                expected_behaviour="identify_provision",
                expected_act_slug=act.slug,
                expected_section_id=section.section_id,
                citation_exists=True,
                ground_truth_evidence=_section_evidence(act, section.section_id, section.heading),
                deterministic_criteria=["expected-provision-cited", "no-invalid-citations"],
            )
            builder.caps.take("C2", act.slug, section.section_id)
            builder.emit("C2", case)


def _build_c3(builder: _Builder, acts: list[ActInfo]) -> None:
    target = builder.config.targets["C3"]
    for act in _rotate(acts, 2):
        if builder.counters["C3"] >= target:
            return
        traps = trap_section_ids(act)
        if not traps or not builder.caps.allows("C3", act.slug, None):
            continue
        strategy, trap = traps[builder.counters["C3"] % len(traps)]
        verdict = builder.reader.validate_citation(f"{act.slug} § {trap}")
        if verdict["valid"]:
            continue
        case = builder.new_case(
            strategy,
            tpl.fill(tpl.C3_FRAMES, builder.counters["C3"], act=act.display_name, section=trap),
            difficulty_for(act.section_count),
        )
        case.update(
            expected_behaviour="reject_citation",
            claimed_act_slug=act.slug,
            claimed_section_id=trap,
            citation_exists=False,
            ground_truth_evidence={"validate_citation": verdict},
            deterministic_criteria=["claimed-citation-not-asserted", "no-invalid-citations"],
        )
        builder.caps.take("C3", act.slug, None)
        builder.emit("C3", case)


def _claimed_exists(reader: CorpusReader, slug: str, section_id: str) -> bool | None:
    """True/False existence of the trap pair; None = ambiguous (skip)."""
    try:
        reader.get_section(slug, section_id)
    except CorpusAmbiguousSectionError:
        return None
    except CorpusNotFoundError:
        return False
    return True


def _pair_wrong_act(acts: list[ActInfo], act: ActInfo, offset: int) -> ActInfo | None:
    """Deterministic related-act pick: same ministry first, else same type."""
    others = [a for a in acts if a.slug != act.slug]
    same_ministry = [a for a in others if act.ministry and a.ministry == act.ministry]
    pool = same_ministry or [a for a in others if a.doc_type == act.doc_type]
    return pool[offset % len(pool)] if pool else None


def _build_c4(builder: _Builder, acts: list[ActInfo]) -> None:
    target = builder.config.targets["C4"]
    for act in _rotate(acts, 3):
        if builder.counters["C4"] >= target:
            return
        picks = act.topic_sections()
        wrong = _pair_wrong_act(acts, act, builder.counters["C4"])
        if not picks or wrong is None:
            continue
        section, topic = picks[0]
        exists = _claimed_exists(builder.reader, wrong.slug, section.section_id)
        if exists is None or not builder.caps.allows("C4", act.slug, section.section_id):
            continue
        case = builder.new_case(
            "wrong-act",
            tpl.fill(
                tpl.C4_FRAMES,
                builder.counters["C4"],
                act=wrong.display_name,
                section=section.section_id,
                topic=topic,
            ),
            difficulty_for(act.section_count),
        )
        case.update(
            expected_behaviour="reject_premise",
            expected_act_slug=act.slug,
            expected_section_id=section.section_id,
            claimed_act_slug=wrong.slug,
            claimed_section_id=section.section_id,
            citation_exists=exists,
            ground_truth_evidence=_section_evidence(act, section.section_id, section.heading),
            deterministic_criteria=[
                "claimed-attribution-not-asserted",
                "expected-provision-cited",
                "no-invalid-citations",
            ],
        )
        builder.caps.take("C4", act.slug, section.section_id)
        builder.emit("C4", case)


def _build_c5(
    builder: _Builder,
    duplicates: list[dict[str, Any]],
    tombstones: list[tuple[str, str]],
) -> None:
    target = builder.config.targets["C5"]
    for finding in duplicates:
        if builder.counters["C5"] >= target:
            return
        slug = str(finding["slug"])
        dup_ids = cast(dict[str, int], finding["duplicates"])
        for section_id, count in sorted(dup_ids.items()):
            if builder.counters["C5"] >= target or not builder.caps.allows("C5", slug, section_id):
                continue
            name = _display_for_slug(builder.reader, slug)
            case = builder.new_case(
                "duplicate-section-id",
                tpl.fill(
                    tpl.C5_DUPLICATE_FRAMES,
                    builder.counters["C5"],
                    act=name,
                    section=section_id,
                ),
                "hard",
            )
            case.update(
                expected_behaviour="answer_with_citation",
                expected_act_slug=slug,
                expected_section_id=section_id,
                citation_exists=True,
                ground_truth_evidence={"duplicate_occurrences": {"count": count}},
                deterministic_criteria=["ambiguity-handled", "no-invalid-citations"],
            )
            builder.caps.take("C5", slug, section_id)
            builder.emit("C5", case)
    _build_c5_tombstones(builder, tombstones)


def _build_c5_tombstones(builder: _Builder, tombstones: list[tuple[str, str]]) -> None:
    target = builder.config.targets["C5"]
    for slug, name in tombstones:
        if builder.counters["C5"] >= target:
            return
        case = builder.new_case(
            "repealed-as-current",
            tpl.fill(tpl.C5_TOMBSTONE_FRAMES, builder.counters["C5"], act=name),
            "medium",
        )
        case.update(
            expected_behaviour="reject_citation",
            claimed_act_slug=slug,
            citation_exists=False,
            ground_truth_evidence={"manifest_status": {"slug": slug, "status": "removed"}},
            deterministic_criteria=["repealed-not-asserted-current", "no-invalid-citations"],
        )
        builder.emit("C5", case)


def _build_c6(builder: _Builder, acts: list[ActInfo]) -> None:
    target = builder.config.targets["C6"]
    for act in _rotate(acts, 4):
        if builder.counters["C6"] >= target:
            return
        picks = act.topic_sections()
        if not picks:
            continue
        section, topic = picks[-1]
        if not builder.caps.allows("C6", act.slug, section.section_id):
            continue
        if builder.counters["C6"] % 2 == 0:
            _emit_c6_nonexistent(builder, act, section.section_id, topic)
        else:
            _emit_c6_misattribution(builder, acts, act, (section, topic))


def _emit_c6_nonexistent(builder: _Builder, act: ActInfo, section_id: str, topic: str) -> None:
    traps = trap_section_ids(act)
    if not traps:
        return
    _, trap = traps[builder.counters["C6"] % len(traps)]
    case = builder.new_case(
        "nonexistent-support",
        tpl.fill(
            tpl.C6_NONEXISTENT_FRAMES,
            builder.counters["C6"],
            act=act.display_name,
            section=trap,
            topic=topic,
        ),
        difficulty_for(act.section_count),
    )
    case.update(
        expected_behaviour="reject_premise",
        expected_act_slug=act.slug,
        expected_section_id=section_id,
        claimed_act_slug=act.slug,
        claimed_section_id=trap,
        citation_exists=False,
        ground_truth_evidence={"trap": {"slug": act.slug, "section_id": trap, "absent": True}},
        deterministic_criteria=["false-premise-not-endorsed", "no-invalid-citations"],
    )
    builder.caps.take("C6", act.slug, section_id)
    builder.emit("C6", case)


def _emit_c6_misattribution(
    builder: _Builder,
    acts: list[ActInfo],
    act: ActInfo,
    pick: tuple[SectionInfo, str],
) -> None:
    section, topic = pick
    section_id = section.section_id
    wrong = _pair_wrong_act(acts, act, builder.counters["C6"])
    if wrong is None:
        return
    exists = _claimed_exists(builder.reader, wrong.slug, section_id)
    if exists is None:
        return
    case = builder.new_case(
        "attribution-mismatch",
        tpl.fill(
            tpl.C6_MISATTRIBUTION_FRAMES,
            builder.counters["C6"],
            act=wrong.display_name,
            section=section_id,
            topic=topic,
        ),
        difficulty_for(act.section_count),
    )
    case.update(
        expected_behaviour="reject_premise",
        expected_act_slug=act.slug,
        expected_section_id=section_id,
        claimed_act_slug=wrong.slug,
        claimed_section_id=section_id,
        citation_exists=exists,
        # The REAL heading, not the derived topic — the evidence field must
        # mirror the get_section oracle output it claims to record (Codex,
        # PR #17 finding 2).
        ground_truth_evidence=_section_evidence(act, section_id, section.heading),
        deterministic_criteria=["false-premise-not-endorsed", "no-invalid-citations"],
    )
    builder.caps.take("C6", act.slug, section_id)
    builder.emit("C6", case)


def _build_c7(builder: _Builder, acts: list[ActInfo]) -> None:
    target = builder.config.targets["C7"]
    subtypes = ("authentic", "fabricated", "modified")
    for act in _rotate(acts, 5):
        if builder.counters["C7"] >= target:
            return
        picks = act.topic_sections()
        if not picks or not builder.caps.allows("C7", act.slug, picks[0][0].section_id):
            continue
        section, topic = picks[0]
        subtype = subtypes[builder.counters["C7"] % len(subtypes)]
        _emit_c7(builder, act, (section.section_id, topic), subtype)


def _emit_c7(
    builder: _Builder,
    act: ActInfo,
    pick: tuple[str, str],
    subtype: str,
) -> None:
    section_id, topic = pick
    span = quote_span(builder.reader, act.slug, section_id)
    fields = _c7_fields(builder, act, section_id, (topic, subtype, span))
    if fields is None:
        return
    quote_text, extra = fields
    case = builder.new_case(
        subtype,
        tpl.fill(
            tpl.C7_FRAMES,
            builder.counters["C7"],
            act=act.display_name,
            section=section_id,
            quote=quote_text,
        ),
        difficulty_for(act.section_count),
    )
    case.update(expected_act_slug=act.slug, expected_section_id=section_id, **extra)
    builder.caps.take("C7", act.slug, section_id)
    builder.emit("C7", case)


def _c7_fields(
    builder: _Builder,
    act: ActInfo,
    section_id: str,
    details: tuple[str, str, tuple[int, int, str] | None],
) -> tuple[str, dict[str, Any]] | None:
    topic, subtype, span = details
    if subtype == "authentic":
        if span is None:
            return None
        return tpl.QUOTE_PLACEHOLDER, {
            "expected_behaviour": "verify_quote",
            "citation_exists": True,
            "quote_ref": quote_ref_fields(act.slug, section_id, span),
            "ground_truth_evidence": {"quote_ref": {"span": list(span[:2])}},
            "deterministic_criteria": ["quote-verified"],
        }
    text = fabricated_quote_for(topic) if subtype == "fabricated" else _mutated(span)
    if text is None:
        return None
    return text, {
        "expected_behaviour": "deny_quote",
        "citation_exists": True,
        "fabricated_quote_text": text,
        "ground_truth_evidence": {"fabricated": {"subtype": subtype}},
        "deterministic_criteria": ["fabricated-quote-not-presented", "no-invalid-citations"],
    }


def _mutated(span: tuple[int, int, str] | None) -> str | None:
    return mutate_quote(span[2]) if span is not None else None


def _build_c8(builder: _Builder, acts: list[ActInfo]) -> None:
    target = builder.config.targets["C8"]
    classes = tuple(tpl.C8_FRAMES)
    for act in _rotate(acts, 6):
        if builder.counters["C8"] >= target:
            return
        picks = act.topic_sections()
        if not picks:
            continue
        topic = picks[0][1]
        scope_class = classes[builder.counters["C8"] % len(classes)]
        question = tpl.C8_FRAMES[scope_class].format(topic=topic, act=act.display_name)
        case = builder.new_case(scope_class, question, "medium")
        case.update(
            expected_behaviour="abstain",
            ground_truth_evidence={
                "scope": {
                    "source_class": scope_class,
                    "in_corpus": False,
                    "authority": (
                        "docs/legal-and-sources.md — the corpus contains current statutes "
                        "and central regulations only"
                    ),
                },
            },
            deterministic_criteria=["no-invented-citations", "no-fabricated-resolution"],
        )
        builder.emit("C8", case)


def _display_for_slug(reader: CorpusReader, slug: str) -> str:
    for record in reader.manifest.documents.values():
        if record.slug == slug:
            return display_name(record)
    return slug


def _tombstones(reader: CorpusReader) -> list[tuple[str, str]]:
    return sorted(
        (record.slug, display_name(record))
        for record in reader.manifest.documents.values()
        if record.status == "removed" and record.slug
    )


def _inventory(reader: CorpusReader, config: PoolConfig) -> list[ActInfo]:
    sampler = CorpusSampler(reader, config.seed)
    acts: list[ActInfo] = []
    for doc_id in sampler.shuffled_current_doc_ids():
        if len(acts) >= config.inventory_size:
            break
        act = sampler.act_info(doc_id)
        if act is not None and act.topic_sections():
            acts.append(act)
    return acts


def _name_calibration(reader: CorpusReader) -> dict[str, Any]:
    index = ActNameIndex.from_manifest(reader.manifest)
    entries = index._entries  # calibration inspects the built index (same package)
    collisions = {k: [e.slug for e in v] for k, v in sorted(entries.items()) if len(v) > 1}
    slug_only = sorted(
        {
            record.slug
            for record in reader.manifest.documents.values()
            if record.slug and display_name(record) == (record.title or record.slug)
        },
    )
    return {
        "documents": sum(1 for r in reader.manifest.documents.values() if r.slug),
        "keys": len(entries),
        "collision_keys": collisions,
        "collision_count": len(collisions),
        "docs_without_short_name": len(slug_only),
        "docs_without_short_name_sample": slug_only[:25],
    }


def _normalized_question(case: dict[str, Any]) -> str:
    return " ".join(str(case["question"]).casefold().split())


def _dedup(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seen: dict[str, str] = {}
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, str]] = []
    for case in cases:
        key = _normalized_question(case)
        if key in seen:
            removed.append({"case_id": str(case["case_id"]), "duplicate_of": seen[key]})
        else:
            seen[key] = str(case["case_id"])
            kept.append(case)
    flags = _near_duplicates(kept)
    return kept, {"exact_removed": removed, "near_duplicate_flags": flags}


def _near_duplicates(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    by_category: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        by_category.setdefault(str(case["category"]), []).append(case)
    for members in by_category.values():
        tokens = [set(_normalized_question(c).split()) for c in members]
        for i, first in enumerate(members):
            for j in range(i + 1, len(members)):
                union = tokens[i] | tokens[j]
                score = len(tokens[i] & tokens[j]) / len(union) if union else 0.0
                if score >= _NEAR_DUP_JACCARD:
                    flags.append(
                        {
                            "a": str(first["case_id"]),
                            "b": str(members[j]["case_id"]),
                            "jaccard": round(score, 3),
                        },
                    )
    return flags


def _distribution(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_category = Counter(str(c["category"]) for c in cases)
    acts = Counter(
        str(c.get("expected_act_slug") or c.get("claimed_act_slug") or "-") for c in cases
    )
    shapes = Counter(
        section_shape(str(c["expected_section_id"]))
        for c in cases
        if c.get("expected_section_id") is not None
    )
    provisions = Counter(
        (str(c["category"]), str(c["expected_act_slug"]), str(c["expected_section_id"]))
        for c in cases
        if c.get("expected_act_slug") and c.get("expected_section_id")
    )
    return {
        "by_category": dict(sorted(by_category.items())),
        "unique_acts": len([a for a in acts if a != "-"]),
        "top_acts": acts.most_common(15),
        "section_id_shapes": dict(sorted(shapes.items())),
        "max_provision_reuse": max(provisions.values(), default=0),
    }


def _review_queue(
    cases: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    flags: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reasons: dict[str, list[str]] = {}

    def add(case_id: str, reason: str) -> None:
        reasons.setdefault(case_id, []).append(reason)

    kept_ids = {str(case["case_id"]) for case in cases}
    for index, case in enumerate(cases):
        case_id = str(case["case_id"])
        if case["category"] in ("C5", "C8"):
            add(case_id, f"{case['category']}-mandatory-manual-review")
        elif index % _STRATIFIED_EVERY == 0:
            add(case_id, "stratified-10pct-sample")
    for entry in ledger:
        # The ledger covers every EMITTED case; the queue must only reference
        # cases that survived dedup, or it fills with orphans (Codex, PR #17
        # finding 1: dedup-removed C8 duplicates surfaced as queue entries).
        if str(entry["case_id"]) not in kept_ids:
            continue
        if any(i["severity"] == IssueSeverity.WARNING for i in entry["issues"]):
            add(str(entry["case_id"]), "validator-warning")
    for flag in flags:
        add(str(flag["a"]), "near-duplicate")
        add(str(flag["b"]), "near-duplicate")
    return [{"case_id": cid, "reasons": sorted(set(rs))} for cid, rs in sorted(reasons.items())]


def generate_pool(
    reader: CorpusReader,
    pin: CorpusPin,
    config: PoolConfig,
    run: GenerationRun,
) -> PoolResult:
    """Run the full Stage 3 pipeline; see module docstring."""
    from lovspor.llhb.schema import load_schema  # noqa: PLC0415 — avoids jsonschema at import

    acts = _inventory(reader, config)
    duplicates = scan_duplicate_ids(reader)
    builder = _Builder(reader, pin, config, run)
    builder.validator = CandidateValidator(reader, load_schema(config.schema_path), pin)
    _build_c1(builder, acts)
    _build_c2(builder, acts)
    _build_c3(builder, acts)
    _build_c4(builder, acts)
    _build_c5(builder, duplicates, _tombstones(reader))
    _build_c6(builder, acts)
    _build_c7(builder, acts)
    _build_c8(builder, acts)
    return _finalize(builder, (acts, duplicates), config, run)


def _finalize(
    builder: _Builder,
    inputs: tuple[list[ActInfo], list[dict[str, Any]]],
    config: PoolConfig,
    run: GenerationRun,
) -> PoolResult:
    acts, duplicates = inputs
    valid, rejected, ledger = _validate_all(builder)
    kept, dedup_report = _dedup(valid)
    queue = _review_queue(kept, ledger, dedup_report["near_duplicate_flags"])
    return PoolResult(
        candidates=kept,
        rejected=rejected,
        ledger=ledger,
        review_queue=queue,
        dedup_report=dedup_report,
        distribution=_distribution(kept),
        ambiguity_scan=duplicates,
        name_calibration=_name_calibration(builder.reader),
        generation_manifest=_generation_manifest(builder, (config, run), len(acts)),
    )


def _validate_all(
    builder: _Builder,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    validator = builder.validator
    if validator is None:
        raise RuntimeError("pool builder has no validator; use generate_pool()")
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    for case in builder.cases:
        issues = validator.validate_case(case)
        errors = [i for i in issues if i.severity == IssueSeverity.ERROR]
        ledger.append(
            {
                "case_id": case["case_id"],
                "status": "fail" if errors else "pass",
                "issues": [i.model_dump() for i in issues],
                "validated_at": builder.run.timestamp,
                "validator_commit": builder.run.lovspor_commit,
            },
        )
        if errors:
            case["validation"]["status"] = "quarantined"
            rejected.append({"case": case, "issues": [i.model_dump() for i in errors]})
        else:
            valid.append(case)
    return valid, rejected, ledger


def _generation_manifest(
    builder: _Builder,
    setup: tuple[PoolConfig, GenerationRun],
    inventoried: int,
) -> dict[str, Any]:
    from lovspor.llhb.abbreviations import ABBREVIATIONS_VERSION  # noqa: PLC0415
    from lovspor.llhb.stances import STANCE_RULES_VERSION  # noqa: PLC0415

    config, run = setup
    return {
        "corpus_pin": {
            "lovverk_commit": builder.pin.lovverk_commit,
            "manifest_generated_at": builder.pin.manifest_generated_at.isoformat(),
        },
        "lovspor_commit": run.lovspor_commit,
        "created": run.created,
        "timestamp": run.timestamp,
        "seed": config.seed,
        "targets": dict(config.targets),
        "caps": {
            "per_act_category_cap": config.per_act_category_cap,
            "per_act_total_cap": config.per_act_total_cap,
            "inventory_size": config.inventory_size,
        },
        "acts_inventoried": inventoried,
        "emitted_by_category": dict(sorted(builder.counters.items())),
        "versions": {
            "templates": tpl.TEMPLATES_VERSION,
            "abbreviations": ABBREVIATIONS_VERSION,
            "stance_rules": STANCE_RULES_VERSION,
        },
        "phrasing": {"template": sum(builder.counters.values()), "llm_assisted": 0},
    }
