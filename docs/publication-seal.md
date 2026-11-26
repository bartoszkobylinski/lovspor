# Publication seal

## Purpose

A consumer of `lovverk` needs to know that a `main` head was published by the
lovspor sync workflow after its attestation gate and its audit passed, and that
the attestation it reads belongs to that head. Neither fact follows from the
repository alone:

- the attestation lives in a git note (`refs/notes/temporal-attestations`),
  which is unsigned and can be rewritten by anyone who can push that ref;
- a signed commit would authenticate the commit but not the note.

The publication seal binds both with one signature. It is produced by
`.github/workflows/sync.yml` and checked by `lovspor publication-seal verify`
or by a consumer applying the same rules.

What a seal attests: "this head was published by the sync workflow, after its
gate and audit". It does not attest that the engine code that built the head
was uncompromised (see Threat model).

## Format

An SSH-signed annotated tag:

- name: `refs/tags/sync/seal-<GITHUB_RUN_ID>-<GITHUB_RUN_ATTEMPT>`
- object: the published corpus head (type `commit`)
- tagger: `lovspor-sync[bot] <lovspor-sync@noreply.github.com> <epoch> +0000`
- message, exact bytes, LF line ends:

```text
lovspor-publication-seal v1
corpus_commit <40-hex head>
attestation_ref refs/notes/temporal-attestations
attestation_blob <40-hex blob id of the note on the head>

<the note blob bytes, verbatim>
```

The SSH signature (namespace `git`) covers the whole tag object: object id,
type, tag name, tagger and message. One signature therefore binds the commit
id (and through it the tree), the attestation bytes and the publishing key.
The object is built as unsigned bytes, signed with `ssh-keygen -Y sign`, and
written with `git mktag`, so `git verify-tag` checks it like any signed tag.

## Trust chain

```text
 ops/publication-signers  (public key; committed, owner-reviewed)
            |
            | git -c gpg.ssh.allowedSignersFile=... verify-tag T
            v
 tag T  refs/tags/sync/seal-<run>-<attempt>   (SSH signature, namespace "git")
   |-- object  C  (type commit) ------------------> corpus commit C --> tree
   '-- message
         corpus_commit C
         attestation_blob B
         <note bytes>  -- JSON entry with corpus_commit == C
                          (the signed copy; refs/notes/* is not consulted)

 producer custody:
   sealed head H0 --(sync job: verify H0, sync, audit, compose, push C)--> C
   C --(seal job: re-derive from origin, sign, verify, push T)--> T
```

A consumer accepts candidate commit C only if some tag T in
`refs/tags/sync/*`:

1. is an annotated tag (`git cat-file -t T` is `tag`) whose header names
   `object C` and `type commit`;
2. passes `git -c gpg.ssh.allowedSignersFile=S verify-tag T` with exit 0
   (an unsigned tag or an unknown key exits non-zero);
3. has message line 1 `lovspor-publication-seal v1` and `corpus_commit C`;
4. embeds JSON (after the blank line) that parses to a list, or an object,
   containing an entry whose `corpus_commit` is C.

The notes ref is not used for the decision. A consumer also requires that C
is the `origin/main` head it is about to serve and that C descends from the
commit it serves now; the seal only proves that C was published.

Consumers fetch the tags explicitly:
`git fetch origin 'refs/tags/sync/*:refs/tags/sync/*'`.

## Producer

Two jobs in `sync.yml`.

**`sync` job** (engine code, no seal key, no new secret):

| Step | Does |
| --- | --- |
| Fetch publication seal tags | Fetches `refs/tags/sync/*` after the clone and the notes fetch. |
| Verify starting head seal | With `LOVVERK_SEAL_REQUIRED` = `true`, the head the run starts from must carry a verifying seal, or the job stops before any corpus work. Otherwise, once `ops/publication-signers` holds a key, a failure is a `::warning::`. |
| Compose publication seal | After the audit: `lovspor publication-seal compose` writes the unsigned tag object for HEAD. Fails if HEAD has no attestation note or the note has no entry for HEAD. Writes nothing (`already-sealed <tag>`) when any `sync/*` tag on HEAD verifies against `ops/publication-signers`: a sealed head is never sealed again. |
| Upload publication seal payload | Hands the unsigned object to the `seal` job as an artifact; `seal_state`, `seal_tag` and `head` are job outputs. |
| Push corpus changes if any | Unchanged: notes, then `main`. |

**`seal` job** (fresh runner, `environment: lovverk-seal`, `contents: read`,
only when `seal_state` is `composed`): checks out `scripts/seal/sign-and-tag.sh`
and `ops/publication-signers` from the same lovspor commit, downloads the
payload, configures the deploy key with the same pinned host key as the sync
job, then runs the script. It runs git, `ssh-keygen` and coreutils only, no
uv, Python or engine code. The script:

1. clones `main` and the notes ref from origin (blobs on demand);
2. requires origin `main` == the `head` output == the payload's object, the
   tag name to belong to this run, and the payload to equal, byte for byte,
   the tag it re-derives from origin's commit and origin's note;
3. signs those bytes with `/usr/bin/ssh-keygen -Y sign -n git` under
   `env -i`, the key in a `mktemp` directory created with umask 077 and
   removed by a trap;
4. writes the tag with `git mktag` and `git update-ref`;
5. runs `git verify-tag` against the pinned `ops/publication-signers`;
6. pushes the tag.

The seal therefore lands shortly after `main`. Consumers that require a seal
hold until it appears, which is the fail-closed direction.

## Threat model

| Attacker capability | Effect | Mitigation |
| --- | --- | --- |
| Push access to `lovverk` without the seal key (stolen deploy key, compromised token) | Can push commits, notes, unsigned or self-signed tags | Consumers hold: no tag verifies. With sealing required, the next sync run refuses to start from an unsealed head, so a pushed commit is never laundered into a seal by a nightly run. |
| Rewrites or forges the attestation note | None on the seal decision | The signed copy embedded in the tag is what is checked; a sealed head is never re-sealed, so a later note cannot earn a seal. |
| Replays an old valid seal | The seal binds only its own commit | Consumers require C == `origin/main` head and descent from the served commit; rolling `main` back fails descent. |
| Moves `main` between the sync push and the seal job | Nothing is sealed | The seal job requires origin `main` == the head the sync job reported. |
| Deletes or moves a seal tag | Consumers hold | Tag ruleset protecting `refs/tags/sync/*` from update and deletion. |
| Malicious engine code or crafted upstream data in the `sync` job (git config, `BASH_ENV`, `GITHUB_ENV`, `GITHUB_PATH`, the runner's sudo) | Cannot reach the key | The key exists only in the `seal` job, a fresh runner that runs no engine code and takes nothing from the sync job but the payload, which it re-derives from origin. |
| Compromised `sync` job (the same malicious engine code) | Gets its own newly published head sealed | Residual. What is sealed is what that run published; the seal attests publication by the workflow, not that the engine was uncompromised. |
| Fork pull requests | No access | The workflow runs on schedule and manual dispatch only; forks get no secrets. The environment admits `main` only. |
| A workflow run from a branch other than `main` (a dispatch of an unreviewed branch, a pushed workflow file, a same-repository pull request) | Would read every repository secret | Neither lovverk key is a repository secret: both are secrets of environments that admit `main` only (`lovverk-sync`, `lovverk-seal`). The sync workflow is never dispatched on another branch (Live verification). |
| Write access to `lovspor` (can edit `sync.yml` or the script) | Could change the seal job to leak the key | Branch protection and review on `main`; the environment is restricted to `main`. Residual: a merged malicious change. |
| Steals the seal key | Can seal arbitrary commits | Revocation (remove the key from every allowed-signers file) and a new key. |
| SHA-1 collisions on git object ids | Same strength as git commit signing | Noted, not mitigated here. |

## Key isolation: why a tag, signed in its own job

- The key is never on a runner that executes `lovspor sync`. A step-level
  secret is not isolation within one job: earlier steps can plant git
  configuration, environment files or, with the runner's passwordless sudo,
  anything else a later step executes. Only a fresh job isolates it.
- No history is rewritten. Commits stay as the sync made them; the seal is a
  new object beside them.
- The seal binds the unsigned note. A signed commit covers the tree, not the
  notes ref; the tag embeds the note bytes under the same signature.

## Fail-closed behaviour

- Starting head without a verifying seal while `LOVVERK_SEAL_REQUIRED` =
  `true`: the sync job stops before any corpus work.
- Compose failing fails the sync job before its push.
- Any re-derivation mismatch, a signing failure or a `verify-tag` failure
  fails the seal job before the tag is pushed.
- No `LOVVERK_SEAL_SIGNING_KEY` with `LOVVERK_SEAL_REQUIRED` = `true`: the
  seal job fails. The sync's publication has already happened; consumers that
  require a seal hold, and the next run refuses to start from that head.
- Variable not `true` and no key: the seal job prints a `::warning::` and
  pushes nothing.
- `ops/publication-signers` holds no key line: the seal job refuses, and no
  seal ever counts as existing.
- Compose requires an attestation note on HEAD in every phase. The sync step
  attests HEAD on every run, so a missing note is itself a failure.

A failed or cancelled seal job files (or comments on) the issue "Publication
seal did not complete" with the run link and the unsealed head. A run cancelled after
its push skips the seal job; the sync job's own "Nightly sync did not complete"
issue then names the published, unsealed head, and only a re-bootstrap recovers
it. A failed seal job is
recovered by re-running only that job, within six hours of
the compose (the tagger time is bounded) and while origin `main` is still the
head it was composed for. Re-running the whole workflow does not help: with
sealing required, the sync job refuses the unsealed head. Otherwise the owner
re-bootstraps (see Bootstrap and Revocation). The seal job runs whenever the
sync job's push completed, even if a later notification step of that job
failed.

## Owner setup

Each step is a separate decision. Steps 1 and 2 change nothing about what is
published; run them between sync runs (`gh run list --repo
bartoszkobylinski/lovspor --workflow sync.yml --status in_progress` prints
nothing).

1. Create both environments, each admitting the `main` branch only:

   ```bash
   for env in lovverk-sync lovverk-seal; do
     gh api --method PUT "repos/bartoszkobylinski/lovspor/environments/$env" --input - <<'EOF'
   {"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
   EOF
     gh api --method POST "repos/bartoszkobylinski/lovspor/environments/$env/deployment-branch-policies" \
       -f name=main -f type=branch
   done
   for env in lovverk-sync lovverk-seal; do
     gh api "repos/bartoszkobylinski/lovspor/environments/$env/deployment-branch-policies" \
       --jq "\"$env: \" + ([.branch_policies[] | .name + \" \" + .type] | join(\", \"))"
   done
   ```

   Expected: `lovverk-sync: main branch` and `lovverk-seal: main branch`.

2. Move the lovverk write key out of repository secrets. A secret value
   cannot be read back, so this is a deploy-key rotation: install a new key in
   both environments, let a scheduled run publish with it, then remove the old
   key and the repository secret.

   ```bash
   ssh-keygen -t ed25519 -C lovverk-deploy -N '' -f ./lovverk-deploy
   gh repo deploy-key add ./lovverk-deploy.pub --repo bartoszkobylinski/lovverk \
     --allow-write --title "lovspor sync (environment secret)"
   gh secret set LOVVERK_DEPLOY_KEY --repo bartoszkobylinski/lovspor --env lovverk-sync < ./lovverk-deploy
   gh secret set LOVVERK_DEPLOY_KEY --repo bartoszkobylinski/lovspor --env lovverk-seal < ./lovverk-deploy
   rm ./lovverk-deploy ./lovverk-deploy.pub
   ```

   An environment secret takes precedence over a repository secret of the
   same name. After the next scheduled run succeeds, delete the repository
   secret and the old deploy key (`gh repo deploy-key list` shows its id):

   ```bash
   gh secret delete LOVVERK_DEPLOY_KEY --repo bartoszkobylinski/lovspor
   gh repo deploy-key list --repo bartoszkobylinski/lovverk
   gh repo deploy-key delete <old key id> --repo bartoszkobylinski/lovverk
   ```

   The run after that is the one that proves the environment key alone works.

3. Generate the seal key (no passphrase: CI must use it unattended), store it
   in `lovverk-seal`, and delete the local private key (a lost key is rotated,
   not recovered). Keep the `.pub` file for step 4:

   ```bash
   ssh-keygen -t ed25519 -C lovverk-publication-seal -N '' -f ./lovverk-publication-seal
   gh secret set LOVVERK_SEAL_SIGNING_KEY --repo bartoszkobylinski/lovspor --env lovverk-seal < ./lovverk-publication-seal
   rm ./lovverk-publication-seal
   ```

4. Commit the public key line to `ops/publication-signers` through a reviewed
   pull request:

   ```bash
   printf 'lovverk-publication-seal namespaces="git" %s\n' \
     "$(cut -d' ' -f1,2 ./lovverk-publication-seal.pub)" >> ops/publication-signers
   ```

5. Protect the seal tags on `lovverk` from update and deletion (no bypass, so
   the seals are a permanent ledger; a forged new tag under `sync/` does not
   verify):

   ```bash
   gh api --method POST repos/bartoszkobylinski/lovverk/rulesets --input - <<'EOF'
   {
     "name": "publication seals are immutable",
     "target": "tag",
     "enforcement": "active",
     "conditions": {"ref_name": {"include": ["refs/tags/sync/*"], "exclude": []}},
     "rules": [{"type": "update"}, {"type": "deletion"}]
   }
   EOF
   ```

   Check: `gh api repos/bartoszkobylinski/lovverk/rulesets --jq '.[] | .name + " " + .target + " " + .enforcement'`.

6. Only after origin `main` passes the gate below, require the seal:

   ```bash
   gh variable set LOVVERK_SEAL_REQUIRED --repo bartoszkobylinski/lovspor --body true
   ```

Consumers copy the public key line into their own allowed-signers file; they
do not read it from `lovspor` at verification time.

## Live verification

The sync workflow is dispatched on `main` only, never on an unmerged branch: a
run executes the workflow file of the branch it runs on. Evidence before the
merge comes from the tests. After it, each phase is checked on scheduled runs
and from an unprivileged clone; nothing below needs a credential.

- **Phase 0** (merged, nothing installed): the next scheduled run publishes as
  before; its `seal` job warns that no signing key is configured and pushes no
  tag.
- **Phase 1** (keys installed): after a scheduled run that published, check the
  seal from a fresh clone:

  ```bash
  work=$(mktemp -d)
  git clone --quiet https://github.com/bartoszkobylinski/lovverk "$work/lovverk"
  git -C "$work/lovverk" fetch --quiet origin \
    '+refs/tags/sync/*:refs/tags/sync/*' '+refs/notes/*:refs/notes/*'
  git -C "$work/lovverk" tag --points-at HEAD --list 'sync/*'
  uv run lovspor publication-seal verify --corpus-path "$work/lovverk" \
    --commit HEAD --allowed-signers ops/publication-signers
  ```

  Run the last command from a `lovspor` checkout of `main`. Expected: exit 0
  and a `verified` line naming a `sync/seal-<run>-<attempt>` tag and the
  `lovverk-publication-seal` signer.

**Gate before `LOVVERK_SEAL_REQUIRED` = `true`, and before any consumer starts
requiring seals:** the Phase 1 check passes on the current origin `main` head
(a signed seal naming that exact commit and embedding its attestation entry),
and all lovverk history pushed during phase 1 has been reviewed (Bootstrap).
A head without a passing check is not enforced on.

## Bootstrap

In phase 1 (key installed, not required), the first run that seals seals the
head it publishes, built on whatever `main` held when sealing began. Every
phase-1 run is trust on first use, not only the first: with sealing not
required, a head pushed by someone else (for example with a stolen deploy key)
is sealed by the next run with only a warning. Before setting
`LOVVERK_SEAL_REQUIRED` = `true`, review all lovverk history pushed during
phase 1, not only its last head, and keep phase 1 short. From then on every run must start from a
sealed head, so custody is continuous.

## Rotation

Overlap the keys with `valid-after` / `valid-before` (UTC). git passes the
tag's tagger date as the verification time, so each seal is judged by the key
valid when it was made:

```text
lovverk-publication-seal namespaces="git",valid-before="20270301000000Z" ssh-ed25519 <old key>
lovverk-publication-seal namespaces="git",valid-after="20270215000000Z" ssh-ed25519 <new key>
```

1. Generate the new key and print its line; add that line with `valid-after`
   and set `valid-before` on the old line, in `ops/publication-signers` (a
   reviewed pull request) and in every consumer's file:

   ```bash
   ssh-keygen -t ed25519 -C lovverk-publication-seal -N '' -f ./lovverk-publication-seal-new
   printf 'lovverk-publication-seal namespaces="git",valid-after="YYYYMMDD000000Z" %s\n' \
     "$(cut -d' ' -f1,2 ./lovverk-publication-seal-new.pub)"
   ```
2. Within the overlap, once the new line is on `main` and in every consumer's
   file, replace the environment secret and delete the local private key:

   ```bash
   gh secret set LOVVERK_SEAL_SIGNING_KEY --repo bartoszkobylinski/lovspor --env lovverk-seal < ./lovverk-publication-seal-new
   rm ./lovverk-publication-seal-new
   ```
3. After the overlap, the old line can stay (old seals keep verifying) or be
   removed.

The tagger date is written by the signer, so `valid-before` is no defence
against a stolen key: a thief can backdate. Use it for planned rotation only.

## Revocation

On suspected compromise: delete the key's line from `ops/publication-signers`
and from every consumer's file, delete or replace the secret, and install a
new key (Owner setup step 3 commands, then step 4 with the new public key). Every seal made with the old key stops verifying, old ones included.
With sealing required, the next run then refuses to start from the current
head: unset `LOVVERK_SEAL_REQUIRED`, check the head, let one run seal with the
new key, and set the variable again (the Bootstrap step).

## Migration plan

| Phase | State | Effect |
| --- | --- | --- |
| 0 | This change merged; secret absent, variable unset, no key in `ops/publication-signers` | The seal job warns; publication continues unsealed, as before. |
| 1 | Environments restricted to `main`, write key moved into them, seal key installed in `lovverk-seal`, public key committed | Each run seals the head it publishes; an unsealed starting head is a warning. |
| 2 | `LOVVERK_SEAL_REQUIRED` = `true`, after a sealed head exists | Runs start only from a sealed head; a missing key fails the seal job. |
| 3 | Consumers enable verification | Consumers serve only sealed heads. |

## Rollback

Unset the variable first, then delete the secret if sealing should stop
entirely:

```bash
gh variable delete LOVVERK_SEAL_REQUIRED --repo bartoszkobylinski/lovspor
gh secret delete LOVVERK_SEAL_SIGNING_KEY --repo bartoszkobylinski/lovspor --env lovverk-seal
```

Publication continues unsealed; consumers that require seals hold on the last
sealed head. Deleting the secret while the variable is still `true` makes the
seal job fail and, from the next run, stops publication (fail closed).

## Existing history

Commits and notes published before the seal stay as they are and stay
readable. Nothing is rewritten; the seal only adds tags.
