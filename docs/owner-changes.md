# Owner changes to `lokale-forskrifter/`

The local-regulation dataset in lovverk, `lokale-forskrifter/`, is written by
the maintainer's promotion tools, not by the sync. Once publication seals are
required (see [publication-seal.md](publication-seal.md)), a commit that the
sync workflow did not push stops publication. Maintainer changes to this
dataset will therefore reach lovverk `main` through the sync, pinned by commit
in this repository.

## Status

- **In place:** the pin file `ops/lovverk-owner-changes`, its grammar check
  (`lovspor ops owner-changes check`) and the pull-request checks below.
- **Not yet in place:** the sync step that applies pins. Until it exists, the
  sync does not read the pin file, and a pin has no effect. An empty file (only
  comments) is the normal state.

## Pin file

`ops/lovverk-owner-changes` is ASCII with LF line endings, at most 64 KiB, and
ends with a newline unless empty. Each line is one of:

- blank;
- a comment: `#` followed by printable ASCII;
- a pin: `<commit> <pull request> <attempt> <reason>`, separated by single spaces:
  - `commit`: the reviewed lovverk commit, 40 lowercase hex digits;
  - `pull request`: the lovverk pull request number that carries it, 1 to 9999999;
  - `attempt`: 1 to 99, raised by one to retry a pin whose attempt was used;
  - `reason`: 1 to 200 printable ASCII characters, starting with a non-space.

At most 20 pins. Each commit and each pull request number appears once: after
a rebase, the line is replaced (new commit, attempt back to 1), not added. Any
other content refuses the whole file, and every problem is reported with its
line number.

```text
# <commit> <pull request> <attempt> <reason>
0123456789abcdef0123456789abcdef01234567 42 1 Example regulation marked repealed
```

## Pull-request checks

Both run in the `fast-ci` job of `.github/workflows/pr-pipeline.yml`:

- the grammar check, on every pull request and every push to `main`;
- on a pull request that changes the pin file: it must be the only file
  changed (a rename into or out of it is refused), and no commit may carry an
  automated agent stamp (`[agent:<lane>]` at the end of its subject). The
  independent test author is not started for such a pull request, since its
  round would add a commit.

The file is listed in `.github/CODEOWNERS`.

## Planned procedure

1. Prepare the change on a lovverk branch based on current `main`, committing
   only files under `lokale-forskrifter/`. Open the lovverk pull request, let
   its `Corpus integrity` check pass, review it, and do not merge it.
2. Open a pull request here that adds or replaces one pin line for the
   reviewed commit (`git rev-parse HEAD` in the reviewed checkout).
3. After that pull request merges, a later sync run merges the pinned commit
   only if it changes nothing but regular files under `lokale-forskrifter/`,
   none of those paths changed on `main` since the commit branched, and the
   merged change is identical to the pinned one. Otherwise it skips the pin
   and files an issue; publication continues. Each attempt is tried in one run
   only.

Step 3 describes the planned sync step and is not active yet.
