# Register of provably-equivalent mutants (issue #122)

An equivalent mutant is mutated code that computes exactly what the original
computes. No test can distinguish the two, so no test can ever kill it: a PR
touching such code had a permanently red required check, and a required check
that cannot go green teaches people to ignore it.

An entry here moves the GATE'S VERDICT and nothing else. Counts, score and the
survivor list stay exactly as measured — the raw signal is the point, so a
future regression that adds new equivalent mutants stays visible
(docs/decisions.md §9c: "equivalent survivors are registered, not chased").

## Layout: one file per entry (issue #516)

Every entry is its own TOML file holding exactly one `[[equivalent]]` table,
under a directory named after its module:
`mutation-equivalents/<module path under src/lovspor, dotted>/<symbol>-<hash>.toml`,
for example `observatory.model/record_to_json_line-949a71a3.toml`. `<hash>` is
the first 8 hex digits of the SHA-256 of `file`, `symbol` and the mutation's
`-`/`+` lines; any unique name works, the loader reads every `*.toml` below this
directory. A single register file made every PR append after the same last
entry, so any two PRs registering equivalents conflicted on each other's merge.
A file holding more or fewer than one entry is refused, and so is a revived
`mutation-equivalents.toml` at the repo root, which is no longer read.

## Rules, all enforced by scripts/ci/mutation_to_json.py

- An entry is matched by `file` + the -/+ lines of `mutation`, NEVER by
  mutant id. Ids renumber whenever the file changes upstream of the mutant,
  so an id-keyed register drifts onto a different mutation with nobody
  touching it. Leading whitespace is ignored; the code itself is not.
- `justification` is mandatory and must be an argument, not an assertion.
  "Trivial" is not an argument. An entry missing any required field is
  refused, reported in the job summary, and has no effect.
- A survivor whose mutation diff could not be recovered never matches
  anything: the gate fails closed rather than waive what it cannot read.
- An entry arguing from a DEPENDENCY's behaviour, rather than from Python's
  own semantics, must name the test that pins that behaviour in
  `assumption_test` (issue #132). Dependencies move; a justification that
  quietly became false would keep waiving a survivor that is now a real
  defect, and nothing else re-reads these arguments. The field is a pytest
  node id, checked by tests/unit/test_mutation_equivalents_assumptions.py.
  The parser ignores it — it costs the entry nothing and buys a red test the
  day the assumption stops holding.
- Registering a mutant that is NOT equivalent silences a real gap. That is
  what review of this directory is for — treat a new file here as a
  test-deletion diff, because that is its effect.

Check the register parses: `uv run python scripts/ci/mutation_to_json.py --check-equivalents`
