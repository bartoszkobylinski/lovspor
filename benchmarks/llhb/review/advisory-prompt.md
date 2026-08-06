# Stage 3.6-F advisory annotations — task for the pre-screening model

Authorized by DECISIONS.md ruling #22 (2026-08-06): ADVISORY only. You do
not decide anything. You never touch the decisions file. Your output is a
set of recommendations the owner reads while making every final call in the
review CLI.

## Input

The four review packets for the regenerated pool:

    benchmarks/llhb/dataset/candidates/regen/review/packet-A-c5.md
    benchmarks/llhb/dataset/candidates/regen/review/packet-B-c8.md
    benchmarks/llhb/dataset/candidates/regen/review/packet-C-near-duplicates.md
    benchmarks/llhb/dataset/candidates/regen/review/packet-D-stratified.md

Full case records: `benchmarks/llhb/dataset/candidates/regen/candidates.jsonl`.

Ground truth must be checked against the PINNED corpus, never a live one:

    git clone https://github.com/bartoszkobylinski/lovverk /tmp/lovverk-pin
    git -C /tmp/lovverk-pin checkout 6ec7059d53d25ddae99d8a64bf5157a90c4c166c

Materialize any referenced provision with:

    uv run python benchmarks/llhb/review/review_cli.py \
      --data-dir benchmarks/llhb/dataset/candidates/regen \
      show-source <case_id> --corpus /tmp/lovverk-pin

## Task — for EVERY case in the queue (85)

Write one JSON line to
`benchmarks/llhb/dataset/candidates/regen/review/advisory.jsonl`:

```json
{"case_id": "...", "recommendation": "keep|drop|needs_fix",
 "rationale": "2-4 sentences grounded in the pinned corpus text",
 "language_flag": null,
 "corpus_checked": true}
```

- `recommendation` — what you would decide and why, judged on: (1) does the
  ground-truth evidence match the pinned corpus text, (2) is the trap fair
  (no leaks, no absurd premise), (3) is the question answerable as worded.
- `rationale` — cite what you actually compared (slug, §, what the text
  says). No legal interpretation — corpus-text comparison only.
- `language_flag` — null, or a short note when the Norwegian bokmål wording
  reads unnaturally (word choice, morphology, template artifacts).
- `corpus_checked` — true only if you materialized the referenced text;
  false with a reason in the rationale otherwise.

## Hard constraints (ruling #22)

- Write ONLY `advisory.jsonl`. Never touch
  `manual-review-decisions.jsonl`, the packets, the pool artifacts, or any
  source file.
- You do not define ground truth. If you believe the evidence is wrong,
  recommend `needs_fix` and say why — the owner verifies.
- No score, no aggregate verdict — per-case annotations only.
