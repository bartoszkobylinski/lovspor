# Nightly mutation, 2026-11-23

- Verdict: **tool_failure** (not every mutant in the range was measured)
- Range: `72c39d283c93e509d41e65d59e1597779055568c...467e36a82b235b123272d983aaa313984583f1f3`
- Run: https://github.com/bartoszkobylinski/lovspor/actions/runs/45083564797
- Written by: the nightly-mutation lane
- Counts: `{"invalid": 0, "killed": 0, "no_tests": 0, "skipped": 0, "survived": 0, "timeout": 0, "total": 0, "unmeasured": 0}`, score None
- Note: the register was not changed: only a completed night changes it

## By kind

| kind | mutants | killed | survived | killed % | time |
|---|---:|---:|---:|---:|---:|

## By file

| file | mutants | killed | killed % | time |
|---|---:|---:|---:|---:|

## Functions with mutants left alive

| function | tests | alive | mutants | time |
|---|---:|---:|---:|---:|

## Slowest mutants

| mutant | status | kind | seconds | estimate |
|---|---:|---:|---:|---:|

## Claimed killed by a commit in this range (8)

| key | function | commit | this night found |
|---|---:|---:|---:|
| `14f17e263792cd80` | `src/lovspor/forarbeider/release_lock.py` `held_by_us` | `b3bbc07b` | not decided: the night did not measure its function |
| `41dcaafa77e91d16` | `src/lovspor/forarbeider/release_lock.py` `_at_name` | `b3bbc07b` | not decided: the night did not measure its function |
| `44d5b0f8e37e9ecb` | `src/lovspor/forarbeider/release_lock.py` `_claim` | `b3bbc07b` | not decided: the night did not measure its function |
| `a3f7ef52e7358a33` | `src/lovspor/forarbeider/release_lock.py` `_claim` | `b3bbc07b` | not decided: the night did not measure its function |
| `b318959fb16911ac` | `src/lovspor/forarbeider/release_lock.py` `_claim` | `b3bbc07b` | not decided: the night did not measure its function |
| `c4f5393228bafa67` | `src/lovspor/forarbeider/release_lock.py` `_at_name` | `b3bbc07b` | not decided: the night did not measure its function |
| `dc032ce152dac940` | `src/lovspor/forarbeider/release_lock.py` `_claim` | `b3bbc07b` | not decided: the night did not measure its function |
| `e8dba390148ba706` | `src/lovspor/forarbeider/release_lock.py` `_claim` | `b3bbc07b` | not decided: the night did not measure its function |

## Not killed this night (0)

## The register after this night

- **3344** open in 104 file(s); this night 0 new, 0 left, 0 gone with their function

| kind | open |
|---|---:|
| argument: replaced by None | 836 |
| string: XX added | 733 |
| string: upper case | 616 |
| argument: removed | 373 |
| operator: and / or | 161 |
| ternary: condition forced | 128 |
| number | 103 |
| assignment: value to None | 78 |
| True / False swapped | 70 |
| operator: comparison | 55 |
| string: lower case | 41 |
| operator: arithmetic | 30 |
| is / in negated | 29 |
| string method swapped | 24 |
| not recorded | 15 |
| augmented assignment to plain | 13 |
| break / continue swapped | 13 |
| lambda: body replaced | 7 |
| operator: bitwise | 7 |
| not / ~ removed | 6 |
| assignment: None to empty string | 4 |
| operator: augmented | 2 |

| file | open |
|---|---:|
| src/lovspor/identity/report.py | 202 |
| src/lovspor/forarbeider/version_lineage.py | 144 |
| src/lovspor/forarbeider/publication_authorize.py | 141 |
| src/lovspor/forarbeider/publication_proposal.py | 137 |
| src/lovspor/forarbeider/publication_path_b.py | 99 |
| src/lovspor/forarbeider/release_ledger.py | 98 |
| src/lovspor/forarbeider/release_gates.py | 97 |
| src/lovspor/forarbeider/publication_stage.py | 87 |
| src/lovspor/forarbeider/policy_trust.py | 74 |
| src/lovspor/regjeringen_fetch/normalize.py | 72 |
| src/lovspor/forarbeider/publication_prepare.py | 68 |
| src/lovspor/forarbeider/release_reader.py | 65 |
| src/lovspor/legaltime/rename_research.py | 64 |
| src/lovspor/identity/repair_report.py | 63 |
| src/lovspor/forarbeider/span_assessment.py | 59 |
| src/lovspor/legaltime/step_evidence.py | 58 |
| src/lovspor/forarbeider/release_attribution.py | 55 |
| src/lovspor/forarbeider/publication_integrity.py | 54 |
| src/lovspor/forarbeider/publication_render.py | 54 |
| src/lovspor/git_publish.py | 53 |
| src/lovspor/forarbeider/attachment_discovery.py | 52 |
| src/lovspor/forarbeider/publication_execute.py | 52 |
| src/lovspor/forarbeider/release_sources.py | 52 |
| src/lovspor/forarbeider/release_quarantine_run.py | 51 |
| src/lovspor/forarbeider/publication_remote_recovery.py | 48 |
| src/lovspor/publication_seal.py | 48 |
| src/lovspor/identity/corpus_git.py | 47 |
| src/lovspor/forarbeider/publication_attempts.py | 42 |
| src/lovspor/forarbeider/version_diff.py | 42 |
| src/lovspor/forarbeider/version_release.py | 42 |
| src/lovspor/forarbeider/document_spans.py | 41 |
| src/lovspor/forarbeider/release_plan.py | 40 |
| src/lovspor/forarbeider/publication_path_a.py | 38 |
| src/lovspor/forarbeider/policy_evaluation.py | 37 |
| src/lovspor/forarbeider/publication_clone.py | 37 |
| src/lovspor/forarbeider/release_checkout.py | 37 |
| src/lovspor/git_prepare.py | 37 |
| src/lovspor/forarbeider/document_extract.py | 33 |
| src/lovspor/forarbeider/version_completeness.py | 31 |
| src/lovspor/identity/registry_history.py | 31 |
| src/lovspor/identity/apply_commands.py | 29 |
| src/lovspor/forarbeider/policy_authorization.py | 28 |
| src/lovspor/forarbeider/release_plan_models.py | 28 |
| src/lovspor/forarbeider/release_quarantine.py | 28 |
| src/lovspor/identity/history_marker.py | 27 |
| src/lovspor/identity/repair_plan.py | 27 |
| src/lovspor/forarbeider/publication_remote.py | 26 |
| src/lovspor/forarbeider/version_manifest.py | 26 |
| src/lovspor/sync/delta_embed.py | 25 |
| src/lovspor/forarbeider/publication.py | 24 |
| src/lovspor/forarbeider/release_control.py | 24 |
| src/lovspor/forarbeider/release_limits.py | 24 |
| src/lovspor/forarbeider/release_quarantine_models.py | 24 |
| src/lovspor/forarbeider/batch_release.py | 22 |
| src/lovspor/identity/plan.py | 22 |
| src/lovspor/forarbeider/batch_apply.py | 18 |
| src/lovspor/identity/history.py | 18 |
| src/lovspor/legaltime/structure.py | 18 |
| src/lovspor/identity/registry.py | 17 |
| src/lovspor/forarbeider/version_reader.py | 16 |
| src/lovspor/identity/audit.py | 16 |
| src/lovspor/sync/orchestrator.py | 16 |
| src/lovspor/forarbeider/publication_sandbox.py | 15 |
| src/lovspor/forarbeider/release_signed.py | 15 |
| src/lovspor/identity/migration.py | 15 |
| src/lovspor/forarbeider/batch_withdraw.py | 14 |
| src/lovspor/identity/commands.py | 14 |
| src/lovspor/forarbeider/publication_models.py | 12 |
| src/lovspor/forarbeider/span_privacy.py | 12 |
| src/lovspor/identity/repair.py | 11 |
| src/lovspor/legaltime/payload_list.py | 11 |
| src/lovspor/forarbeider/release_contract_models.py | 10 |
| src/lovspor/forarbeider/release_inventory.py | 9 |
| src/lovspor/forarbeider/release_lock.py | 9 |
| src/lovspor/forarbeider/release_reader_views.py | 9 |
| src/lovspor/identity/schema.py | 9 |
| src/lovspor/forarbeider/publication_policy.py | 6 |
| src/lovspor/forarbeider/version_release_models.py | 6 |
| src/lovspor/identity/moves.py | 6 |
| src/lovspor/legaltime/step.py | 6 |
| src/lovspor/corpus_audit.py | 5 |
| src/lovspor/forarbeider/batch_recover.py | 5 |
| src/lovspor/forarbeider/policy_authorization_models.py | 5 |
| src/lovspor/forarbeider/publication_receipts.py | 5 |
| src/lovspor/identity/apply.py | 5 |
| src/lovspor/forarbeider/publication_execution_models.py | 4 |
| src/lovspor/forarbeider/version_models.py | 4 |
| src/lovspor/identity/walk.py | 4 |
| src/lovspor/forarbeider/corpus_render.py | 3 |
| src/lovspor/forarbeider/release_cli.py | 3 |
| src/lovspor/forarbeider/release_reader_cli.py | 3 |
| src/lovspor/history.py | 3 |
| src/lovspor/identity/apply_guards.py | 3 |
| src/lovspor/ssh_signature.py | 3 |
| src/lovspor/embeddings/provenance.py | 2 |
| src/lovspor/embeddings/quantize.py | 2 |
| src/lovspor/forarbeider/document_classify.py | 2 |
| src/lovspor/forarbeider/document_models.py | 2 |
| src/lovspor/legaltime/document.py | 2 |
| src/lovspor/forarbeider/batch_records.py | 1 |
| src/lovspor/forarbeider/corpus_transaction.py | 1 |
| src/lovspor/forarbeider/release_provenance.py | 1 |
| src/lovspor/legaltime/identity.py | 1 |
| src/lovspor/sync/input_annotation.py | 1 |

| oldest entries | first seen | kind |
|---|---:|---:|
| `src/lovspor/corpus_audit.py` `audit_published_corpus` `2814e62497156ee5` | 2026-11-20 | argument: removed |
| `src/lovspor/corpus_audit.py` `_identity_schema_findings` `296a9c55ec9d819b` | 2026-11-20 | argument: removed |
| `src/lovspor/corpus_audit.py` `audit_published_corpus` `42fb8a3659ae06d2` | 2026-11-20 | argument: replaced by None |
| `src/lovspor/corpus_audit.py` `_identity_schema_findings` `55a57917562b9f41` | 2026-11-20 | string: XX added |
| `src/lovspor/corpus_audit.py` `_identity_schema_findings` `c9f687a4698a7579` | 2026-11-20 | argument: replaced by None |
| `src/lovspor/embeddings/provenance.py` `parse_attested` `0a79d96177657b57` | 2026-11-20 | number |
| `src/lovspor/embeddings/provenance.py` `_is_sha256` `3b358df832af0196` | 2026-11-20 | string: XX added |
| `src/lovspor/embeddings/quantize.py` `quantize_at_scale` `ac6006d1a8e8cc81` | 2026-11-20 | argument: replaced by None |
| `src/lovspor/embeddings/quantize.py` `quantize_at_scale` `fe37fc3c1cac928a` | 2026-11-20 | argument: replaced by None |
| `src/lovspor/forarbeider/attachment_discovery.py` `_landing` `0b5651d43a699aa7` | 2026-11-20 | argument: replaced by None |
| `src/lovspor/forarbeider/attachment_discovery.py` `_landing` `0c55a24c5361f474` | 2026-11-20 | string: upper case |
| `src/lovspor/forarbeider/attachment_discovery.py` `_harmless` `191de591d6722e4b` | 2026-11-20 | operator: and / or |
| `src/lovspor/forarbeider/attachment_discovery.py` `_landing` `2023c878b8aac009` | 2026-11-20 | argument: removed |
| `src/lovspor/forarbeider/attachment_discovery.py` `_landing` `22b6581abc92d075` | 2026-11-20 | argument: removed |
| `src/lovspor/forarbeider/attachment_discovery.py` `_outside_region_kind` `22f653fca307b0ea` | 2026-11-20 | string: XX added |
| `src/lovspor/forarbeider/attachment_discovery.py` `_outside_region_kind` `283d77222d96aba8` | 2026-11-20 | string: upper case |
| `src/lovspor/forarbeider/attachment_discovery.py` `_coverage_gaps` `30de135fbd70f47b` | 2026-11-20 | argument: removed |
| `src/lovspor/forarbeider/attachment_discovery.py` `_coverage_gaps` `3530099ecb465c68` | 2026-11-20 | argument: replaced by None |
| `src/lovspor/forarbeider/attachment_discovery.py` `_rendition` `3a1667268ca33b53` | 2026-11-20 | ternary: condition forced |
| `src/lovspor/forarbeider/attachment_discovery.py` `_gap_key` `3aaf88b7fa54a352` | 2026-11-20 | operator: and / or |
