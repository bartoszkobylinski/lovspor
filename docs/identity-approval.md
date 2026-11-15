# Identity approval: who may change slug ownership, and how (#938)

The slug registry (`slugs.json` in lovverk) keeps every historical owner. A
change to an owner is accepted only when it is listed in
`src/lovspor/identity/owner_reassignments.json` and that list is approved by
its sha256 digest (#911, #921, ADR-0026 draft). This page is the procedure
for that approval and the repository protection around it.

## What protects the list

| Layer | Setting | Effect |
|---|---|---|
| `.github/CODEOWNERS` | `/src/lovspor/identity/`, `/docs/identity-approval.md` and `CODEOWNERS` itself are owned by the repository owner | A PR touching them needs a code-owner review |
| Ruleset `main-branch-protection` | `require_code_owner_review: true`, `dismiss_stale_reviews_on_push: true`, required checks `fast-ci`, `mutation`, `test (3.12/3.13/3.14)`, no deletion, no force push | Owned paths cannot reach `main` without the owner's act; a push after a review voids it |
| Ruleset bypass | the owner only, `bypass_mode: pull_request` | No one pushes to `main` directly; the owner can merge a PR past an unmet requirement, which GitHub records as a bypass on that PR |
| Engine | `identity-apply` and `lovspor audit` refuse an unapproved list, or one edited after approval (exit 2 / `identity_schema`) | A list nobody signed changes no owner, whatever was merged |

### The limit of the repository layer

This is a single-account repository: GitHub cannot tell the owner from an
automation that holds the owner's token. A code-owner review cannot be given
by the PR's author, so every PR touching an owned path is merged by an owner
bypass, and an agent with the owner's token could perform that bypass too.
The repository layer therefore makes the act explicit and visible (a bypass
on a PR, never a silent push to `main`); it does not prove who acted. The
proof of consent is the step below, which happens outside any agent session.

## The owner's signing procedure

The list and the two commands below ship with #917.

1. Read the dossier for the list under review (the 56 threshold exceptions
   and 13 reassignments, their Git evidence and their 200/301/410 URL
   outcome) and the plan pin it is bound to.
2. In a terminal the owner controls, on the PR branch, print the digest and
   compare it with the dossier:
   `uv run lovspor ops identity-exceptions-digest --path src/lovspor/identity/owner_reassignments.json`
3. Approve it, with the owner's own name:
   `uv run lovspor ops identity-exceptions-approve --path src/lovspor/identity/owner_reassignments.json --digest <digest> --by <name> --on <YYYY-MM-DD>`
   The command refuses (exit 2, nothing written) unless the digest is the
   list's own and the list is pending.
4. Commit that one file alone, signed by the owner's own key
   (`git commit -S`), and push. A signing key that asks for the owner's
   presence on each use (a passphrase or a hardware-backed key) is what
   keeps an agent from producing this commit.
5. Merge the PR yourself; GitHub records the bypass.

An agent never runs step 3 or 4. An approval written by anyone else is a
fabricated approval: revert it and re-run the procedure.

## Audit

- `git log --show-signature -- src/lovspor/identity/owner_reassignments.json`:
  the approval commit is signed by the owner's key and touches only that file.
- The approval's `digest` equals the output of step 2 on the merged file.
- `lovspor audit` on the migrated corpus passes `identity_schema`.
- The PR shows the owner's bypass, and no direct push to `main` exists
  (`gh api repos/bartoszkobylinski/lovspor/rulesets` keeps
  `bypass_mode: pull_request`).

No CI job verifies the commit signature yet; until one does, the audit above
is done by hand before migration M is applied.
