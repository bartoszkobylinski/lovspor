# Publication seal

## Purpose

A consumer of `lovverk` needs to know that a `main` head was published by the
lovspor sync job after its attestation gate and its audit passed, and that the
attestation it reads belongs to that head. Neither fact follows from the
repository alone:

- the attestation lives in a git note (`refs/notes/temporal-attestations`),
  which is unsigned and can be rewritten by anyone who can push that ref;
- a signed commit would authenticate the commit but not the note.

The publication seal binds both with one signature. It is produced by
`.github/workflows/sync.yml` and checked by `lovspor publication-seal verify`
or by a consumer applying the same rules.

## Format

An SSH-signed annotated tag:

- name: `refs/tags/sync/seal-<GITHUB_RUN_ID>-<GITHUB_RUN_ATTEMPT>`
- object: the corpus HEAD commit (type `commit`)
- message, exact bytes, LF line ends:

```text
lovspor-publication-seal v1
corpus_commit <40-hex HEAD>
attestation_ref refs/notes/temporal-attestations
attestation_blob <40-hex blob id of the note on HEAD>

<the note blob bytes, verbatim>
```

The tag is created with `--cleanup=verbatim`, so git does not edit the
message. The SSH signature (namespace `git`) covers the whole tag object:
object id, type, tag name, tagger and message. One signature therefore binds
the commit id (and through it the tree), the attestation bytes and the
publishing key.

## Trust chain

```text
 ops/publication-signers (public key, committed, owner-reviewed)
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

## Producer steps

In `sync.yml`, after `Run lovspor sync` and `Audit corpus integrity before
push`:

| Step | Key | Does |
| --- | --- | --- |
| Compose publication seal | no | `lovspor publication-seal compose` writes the message to `$RUNNER_TEMP`. Fails if HEAD has no attestation note or the note has no entry for HEAD. Writes nothing and prints `already-sealed <tag>` when a `sync/*` tag already seals HEAD with the same note and verifies against `ops/publication-signers`. |
| Sign publication seal | yes | Writes the key under `$RUNNER_TEMP` (umask 077, removed by a trap) and runs `git tag -s --cleanup=verbatim`. |
| Verify publication seal | no | `lovspor publication-seal verify` against `ops/publication-signers`: the new tag by name, or, on a no-op night, the existing seal by commit. |
| Push corpus changes if any | no | Pushes the notes, then the seal tag (only when one was created), then `main`. |

## Threat model

| Attacker capability | Effect | Mitigation |
| --- | --- | --- |
| Push access to `lovverk` without the seal key (stolen deploy key, compromised token) | Can push commits, notes, unsigned or self-signed tags | Consumers hold: no tag verifies against the pinned signers. |
| Rewrites or forges the attestation note | None on the seal decision | The signed copy embedded in the tag is what is checked. |
| Replays an old valid seal | The seal binds only its own commit | Consumers require C == `origin/main` head and descent from the served commit; rolling `main` back fails descent. |
| Forges a `sync/*` tag on HEAD so Compose skips | No new seal is made only if the forged tag verifies | Compose counts an existing seal only if it verifies; Verify checks it again before the push. |
| Deletes or moves a seal tag | Consumers hold | Tag ruleset protecting `refs/tags/sync/*` from update and deletion. |
| Malicious engine code or crafted upstream data in `lovspor sync` | Cannot read the key | The key exists only in the Sign step's environment; Sign runs git only. |
| Fork pull requests | No access | The workflow runs on schedule and manual dispatch only; forks get no secrets. |
| Write access to `lovspor` (can edit `sync.yml`) | Could change the Sign step to leak the key | Branch protection and review on `main`; a GitHub Environment restricted to `main` (see Owner setup). Residual: a merged malicious change. |
| Steals the seal key | Can seal arbitrary commits | Revocation (remove the key from every allowed-signers file) and a new key. |
| SHA-1 collisions on git object ids | Same strength as git commit signing | Noted, not mitigated here. |

## Key isolation: why a tag, not a signed commit

- The key is never in the environment of the step that runs `lovspor sync`,
  which executes engine code over upstream data. Signing commits would need
  the key wherever commits are made, which is inside that step.
- No history is rewritten. Commits stay as the sync made them; the seal is a
  new object beside them.
- The seal binds the unsigned note. A signed commit covers the tree, not the
  notes ref; the tag embeds the note bytes under the same signature.

## Fail-closed behaviour

- Compose, Sign or Verify failing fails the job before the push step: nothing
  is pushed, notes included.
- `LOVVERK_SEAL_REQUIRED` = `true` and no `LOVVERK_SEAL_SIGNING_KEY`: Sign
  fails the job.
- Variable unset (or not `true`) and no key: Sign prints a `::warning::`,
  Verify is skipped, no tag is pushed, and the corpus is published unsealed.
  Consumers that require a seal hold.
- `ops/publication-signers` holds no key line while a key is installed:
  Verify fails with "holds no key", so nothing is pushed.
- Compose requires an attestation note on HEAD in every phase. The sync step
  attests HEAD on every run, so a missing note is itself a failure.

## Owner setup

1. Generate the key (no passphrase: CI must use it unattended):

   ```bash
   ssh-keygen -t ed25519 -C lovverk-publication-seal -N '' -f ./lovverk-publication-seal
   ```

2. Store the private key as a secret, then delete the local copy (a lost key
   is rotated, not recovered):

   ```bash
   gh secret set LOVVERK_SEAL_SIGNING_KEY --repo bartoszkobylinski/lovspor < ./lovverk-publication-seal
   rm ./lovverk-publication-seal
   ```

   Recommended: hold it as an environment secret of a GitHub Environment whose
   deployment branches are restricted to `main`
   (`gh secret set LOVVERK_SEAL_SIGNING_KEY --repo bartoszkobylinski/lovspor --env <name>`).
   An environment secret is only visible to a job that declares
   `environment: <name>`; `sync.yml` does not declare one today, so this needs
   that one-line change to the sync job.

3. Commit the public key line to `ops/publication-signers` through a reviewed
   pull request:

   ```bash
   printf 'lovverk-publication-seal namespaces="git" %s\n' \
     "$(cut -d' ' -f1,2 ./lovverk-publication-seal.pub)" >> ops/publication-signers
   ```

4. Protect the seal tags on `lovverk` from update and deletion:

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

5. Once a sealed run has passed, require the seal:

   ```bash
   gh variable set LOVVERK_SEAL_REQUIRED --repo bartoszkobylinski/lovspor --body true
   ```

Consumers copy the public key line into their own allowed-signers file; they
do not read it from `lovspor` at verification time.

## Rotation

Overlap the keys with `valid-after` / `valid-before` (UTC). git passes the
tag's tagger date as the verification time, so each seal is judged by the key
valid when it was made:

```text
lovverk-publication-seal namespaces="git",valid-before="20270301000000Z" ssh-ed25519 <old key>
lovverk-publication-seal namespaces="git",valid-after="20270215000000Z" ssh-ed25519 <new key>
```

1. Generate the new key; add its line with `valid-after` and set
   `valid-before` on the old line, in `ops/publication-signers` and in every
   consumer's file.
2. Replace the `LOVVERK_SEAL_SIGNING_KEY` secret within the overlap.
3. After the overlap, the old line can stay (old seals keep verifying) or be
   removed.

The tagger date is written by the signer, so `valid-before` is no defence
against a stolen key: a thief can backdate. Use it for planned rotation only.

## Revocation

On suspected compromise: delete the key's line from `ops/publication-signers`
and from every consumer's file, delete or replace the secret, and install a
new key. Every seal made with the old key stops verifying, old ones included.
The next run on an unchanged HEAD finds no verifying seal and seals it again
with the new key; consumers hold until then.

## Migration plan

| Phase | State | Effect |
| --- | --- | --- |
| 0 | This change merged; secret absent, variable unset | Each run warns and publishes unsealed, as before. |
| 1 | Key installed and public key committed | Each run seals, verifies and pushes the tag. |
| 2 | `LOVVERK_SEAL_REQUIRED` = `true` | A run that cannot seal publishes nothing. |
| 3 | Consumers enable verification | Consumers serve only sealed heads. |

## Rollback

Unset the variable first, then delete the secret if sealing should stop
entirely:

```bash
gh variable delete LOVVERK_SEAL_REQUIRED --repo bartoszkobylinski/lovspor
gh secret delete LOVVERK_SEAL_SIGNING_KEY --repo bartoszkobylinski/lovspor
```

Publication continues unsealed; consumers that require seals hold on the last
sealed head. Deleting the secret while the variable is still `true` stops
publication instead (fail closed).

## Existing history

Commits and notes published before the seal stay as they are and stay
readable. Nothing is rewritten; the seal only adds tags.
