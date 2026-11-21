# mutation-reports

Written by the nightly mutation lane of this repository
(`.github/workflows/nightly-mutation.yml`, `scripts/ci/mutation_register.py`).
Nothing here is edited by hand.

- `register.jsonl`: every mutant known to be alive, one JSON object per line.
  An entry leaves when a night measures its function again and a test kills
  the mutant, or when its function is no longer in the tree.
- `history.jsonl`: one line per night, failed nights included.
- `nights/<date>-<head>/report.md`: one night in full. `night.json` beside it
  has the same night in sums, when the lane recorded them.

A line whose `written_by` starts with `seed:` was merged from a run artifact
outside the lane.
