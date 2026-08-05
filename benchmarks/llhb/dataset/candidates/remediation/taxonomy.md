# LLHB Stage 3.6 — root-cause taxonomy and measured blast radius

Evidence artifact (Stage 3.6-A). Derived from the immutable Stage 3.5
owner-review snapshot (119/119: 38 keep, 54 drop, 27 needs_fix) plus a
deterministic sweep of all 390 candidates against the pinned corpus
(lovverk `6ec7059d`). Machine-readable lists: `blast-radius.json`
(rules `llhb-remediation-rules-v1`, reproducible via
`benchmarks/llhb/remediation/analyze_blast_radius.py`).

Owner rulings governing remediation (2026-08-05):

1. **Objective rule match → automatic quarantine** (never automatic
   drop); borderline/subjective classification and every new or
   rewritten case → owner review.
2. **C5 duplicates:** ground truth encodes ALL deterministically valid
   occurrences (`valid_occurrences: [..]`, computed by the oracle after
   document-layer classification — never a hand-picked subset), with
   `expected_behaviour: must_disambiguate`. Scoring passes any
   behaviour that surfaces the ambiguity (flags multiple sections,
   asks to disambiguate, presents the variants); the failure is
   silently picking one occurrence and presenting it as unambiguous.
   No specific sentence is required.
3. **Vedlegg vs Veileder:** a normative vedlegg with its own `§`
   numbering can create REAL ambiguity; an embedded veileder /
   commentary heading is not a second statutory section. The parser
   currently cannot tell them apart — that is production defect RC3,
   fixed in its own PR (B2) BEFORE any new C5 population is generated:
   the benchmark must not freeze known parser artifacts as "real
   ambiguity".
4. **C5 target remains 15.** Feasibility under corrected semantics is
   unknown until the post-parser-fix corpus re-scan; any target change
   is an explicit pre-freeze methodology amendment, not a silent
   adjustment to a generator mistake.

## Taxonomy

| RC | Root cause | Review evidence | Blast radius (measured) | Disposition |
|---|---|---|---|---|
| RC1 | `manifest status=removed` conflated with legal repeal; amendment-act titles non-unique | 16 C5 drops + C5-023 | 17/17 `repealed-as-current` (the one owner KEEP, C5-022, is a *midlertidig* act — a genuinely expirable instrument, kept on its own merit) | quarantine subcategory; delete generator path; scoring: `repealed` verdict must stop mapping to hallucination |
| RC2 | C5 duplicate ground truth pins nothing: `answer_with_citation`, no occurrence set, no disambiguation demand | 11 metadata-error | 13/13 `duplicate-section-id` | schema amendment (`valid_occurrences`, `must_disambiguate`) + metadata remediation |
| RC3 | Embedded Veileder headings parsed as act sections → false duplicate | C5-009/-010 classification-review | exactly those 2 in pool; 6 of 8 duplicate documents corpus-wide have vedlegg/veileder-layer occurrences | production issue + PR (B2); these 2 cases await reclassification after the fix |
| RC4 | Generic/meta heading topics can't anchor discovery questions | 21 C2 + 13 C8 drops | C2 unreviewed: 16 objective (meta-lexicon) + 14 borderline (≤2 words, non-meta); C8 fully reviewed (no residue); C1 exempt (act named; owner kept all 11 reviewed) | objective → quarantine; borderline → owner review; generator topic filter |
| RC5 | C6 nonexistent-support framing («ubetinget rett»/«kreve» + meta topic, pasted headings) rejects itself without citation verification | 5 wording-only | 28/28 nonexistent-support | regenerate wording: TRUE fact + false § as the sole trap |
| RC6 | C7 span/mutation quality: spans not sentence-bounded; conspicuous mutations; absurd fabrications from meta topics | 7 needs_fix | authentic 14/14 spans not sentence-bounded; all 13 modified; all 13 fabricated re-checked | regenerate quote material with sentence-boundary spans + plausible mutations |
| RC7 | Trap id has an existing sibling (`§ 1` claimed while `§ 1-1` exists) | C4-021 drop | 12/50 C4 | quarantine + sibling guard in trap construction |
| RC8 | Case-level wording (C2-026 needs context; C8-006 wording) | 2 needs_fix | 2 | per-case rewrite + re-review |

## Pool arithmetic (before remediation)

390 valid − 54 owner drops = 336 eligible-before-quarantine. Automatic
quarantine (objective, deduplicated against drops): RC1 remainder,
RC4-objective, RC7 → computed in stage D from `blast-radius.json`.
Remediation (not removal): RC2 metadata, RC5 wording, RC6 quote
material, RC8 rewrites. New material (C5 post-parser-fix re-scan, C2/C8
replacements) enters with NEW case ids; retired ids are never reused.

## Stage 3.6 order (owner-approved)

A evidence (this artifact) → B remediation tooling (RC1/2/4/5/6/7 +
scoring semantics + versioned C5 schema amendment) → **B2 production
RC3 fix (separate PR, mandatory before C5 regeneration)** → C corpus
re-scan on fixed parser → D automatic quarantine → E replacements with
new ids → F owner review (replacements, C5, C8, borderline) → G
feasibility report against ORIGINAL targets → H owner target decision →
I Stage 4 selection + freeze.
