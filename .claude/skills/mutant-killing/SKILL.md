---
name: mutant-killing
description: Load when asked to kill surviving mutants from the register (register.jsonl on the mutation-reports branch), to work through the Nightly mutation issue's survivors, or to check why a claimed kill is still reported alive.
---

# Killing registered survivors

The nightly lane keeps every mutant known to be alive in `register.jsonl` on the
`mutation-reports` branch (`docs/agentic-ci.md`, "What is kept" and "Claimed kills").
This is the procedure for a session that works that list down. The rules of mutation
testing themselves are in the `mutation-testing` skill: load it too before deciding that
a mutant is equivalent.

## What the session may and may not do

- It writes **tests**, in a normal pull request made only of tests. Never a change of
  `src/` to make a mutant disappear.
- It never runs `mutmut run`. `scripts/ci/mutant_check.py try` is the allowed aid: it runs
  the named tests against a copy of `src/` that holds the registered change. Its answer is
  not a measurement.
- It never writes to the `mutation-reports` branch and never records a mutant as killed.
  The register changes only when the nightly lane measures the function again. The
  session's whole claim is one line in a commit message.
- Only the owner merges.

## Before picking anything

1. `git fetch origin`, then a fresh branch off `origin/main` in a worktree of its own
   (`test/kill-<area>`): other sessions share the checkouts.
2. Leave alone what another pull request already claims:
   `gh pr list --state open --limit 100 --json number,title,body --jq '.[] | select(.body | test("Kills-mutant:")) | "\(.number) \(.title)\n\(.body)"' | grep -E "^[0-9]+ |Kills-mutant:"`

## Picking

```
.venv/bin/python scripts/ci/mutant_check.py list                    # alive per file
.venv/bin/python scripts/ci/mutant_check.py list --file src/lovspor/x.py
.venv/bin/python scripts/ci/mutant_check.py show <key>              # the change itself
```

Take one file, or one family of functions in it, per pull request: small enough to review
in ten minutes. Read the function and its existing tests before writing anything.

Sort each entry into one of three:

- **A test can tell the change apart.** Write that test (below).
- **Provably equivalent.** No test can tell it apart. Do not write a test and do not touch
  the entry: follow the `mutation-testing` skill (`# pragma: no mutate` with a reason, or
  an entry in `mutation-equivalents/`, owner-reviewed), in a pull request of its own.
- **Only the wording of a message changes.** Leave it, list it in the pull request body as
  left alone and move on.

### A mutant on a string

`string: XX added`, `string: upper case` and `string: lower case` say what was done to the
string, not what the string is. About four in ten registered survivors are of these kinds
(1 130 of 2 759 on 2026-11-21), and the owner ruled on them that day:

- **A string that is data gets a test.** A finding or status code
  (`"privacy_review_required"`), a key or field name of an output (`"planned_documents"`),
  a value a comparison reads (`slot.state == "settled"`), the name of an environment
  variable. When such a mutant survives, no test checks the value. The test is an ordinary
  one of behaviour: it asserts the code, the field or the outcome, and it usually kills
  `XX added` and `upper case` of the same string together. By a rough count of the changed
  lines, about seven in ten string survivors were of this sort.
- **The wording of an exception or a refusal is left alone**
  (`raise _refused("an entry needs exactly ...")`). `XX added` wraps the whole string, so a
  test that matches a part of the message still passes; only a comparison of the whole
  message, character for character, kills it, and that test breaks at every rewording. What
  the message carries as data is the first case: the reason code, the id or the path it
  names.
- **A separator or a default** (`"; ".join(...)`, `value or ""`) is decided entry by
  entry: data when an output a reader or a program depends on changes with it, left alone
  when not. Say which in the pull request body.

Log calls are not in the register: `do_not_mutate_patterns` in `pyproject.toml` keeps
mutmut off them.

An entry whose status is `timeout` or `suspicious` was not killed and was not shown to
survive either. A test does not settle it; say so in the pull request body.

## Writing the test

- It states behaviour a reader of the function would expect, through the public surface
  when the function has one. A test that restates the implementation line by line kills
  the mutant and tells nobody anything.
- `tests/unit/`, in the file that already tests the module when there is one. HTTP is
  mocked with `pytest-httpx` only; logic is never mocked.
- It passes on the tree as it is. Then try it against the mutant:

```
.venv/bin/python scripts/ci/mutant_check.py try <key> -- tests/unit/test_x.py::test_name
```

  Exit 0 and `RED on the mutant`: the tests fail with the change in place. Exit 1,
  `GREEN`: they do not kill it, so write a better test or leave the entry. Exit 2: no
  answer, and the line says why. Exit 3: the function is no longer the one that was
  measured, so the entry is stale; leave it, the night that measures the function settles
  it.
- One test often kills several entries of a function. Try every key you mean to claim.

## The commit and the pull request

- The commit holds tests only. Its message names every key that went RED, one a line:

  ```
  test(identity): the report says which side a mismatch is on

  Kills-mutant: 0123456789abcdef
  Kills-mutant: 89abcdef01234567
  ```

  Never a key that was not tried, and never one that stayed GREEN: the line is what makes
  the nightly lane measure the function again, and the night's report says of each
  claimed key whether it was found killed.
- The pull request body repeats the `Kills-mutant:` lines (other sessions read them
  there), and lists what was left alone and why.
- `scripts/quality/verify-fast.sh`, `/security-check`, commit, push, draft pull request,
  ready when done. A pull request made only of tests gets no author round (#986).

## After the night

The night whose range holds the merged commit measures the claimed functions and writes
the outcome to its report and to its comment on the `Nightly mutation` issue
(`Claims by commits in the range: N, of them killed K, still alive M`). A key reported
`still alive` was not killed by what mutmut ran: `show` it again and read the report's
row before writing another test. A commit without the line is not looked at, and its
kills stay in the register until the function is measured for another reason.
