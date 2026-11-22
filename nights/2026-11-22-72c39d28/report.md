# Nightly mutation, 2026-11-22

- Verdict: **survivors**
- Range: `10302f0a25e07346458b6a663667ac879123584a...72c39d283c93e509d41e65d59e1597779055568c`
- Run: https://github.com/bartoszkobylinski/lovspor/actions/runs/44962079361
- Written by: the nightly-mutation lane
- Counts: `{"invalid": 0, "killed": 3584, "no_tests": 7, "skipped": 0, "survived": 588, "timeout": 0, "total": 4179, "unmeasured": 0}`, score 85.76

## By kind

| kind | mutants | killed | survived | killed % | time |
|---|---:|---:|---:|---:|---:|
| argument: replaced by None | 1235 | 1103 | 130 | 89.3 % | 7145 s |
| argument: removed | 890 | 842 | 48 | 94.6 % | 4087 s |
| string: XX added | 503 | 372 | 131 | 74.0 % | 5961 s |
| string: upper case | 444 | 329 | 115 | 74.1 % | 4970 s |
| assignment: value to None | 398 | 381 | 16 | 95.7 % | 1215 s |
| operator: comparison | 143 | 135 | 8 | 94.4 % | 652 s |
| operator: and / or | 101 | 53 | 47 | 52.5 % | 1820 s |
| ternary: condition forced | 98 | 65 | 33 | 66.3 % | 1306 s |
| is / in negated | 92 | 88 | 4 | 95.7 % | 430 s |
| number | 75 | 57 | 17 | 76.0 % | 865 s |
| operator: arithmetic | 51 | 48 | 3 | 94.1 % | 238 s |
| not / ~ removed | 49 | 47 | 1 | 95.9 % | 112 s |
| string: lower case | 33 | 19 | 14 | 57.6 % | 812 s |
| True / False swapped | 21 | 11 | 10 | 52.4 % | 415 s |
| operator: bitwise | 14 | 12 | 1 | 85.7 % | 91 s |
| lambda: body replaced | 9 | 9 | 0 | 100.0 % | 50 s |
| string method swapped | 8 | 2 | 6 | 25.0 % | 83 s |
| break / continue swapped | 7 | 6 | 1 | 85.7 % | 58 s |
| assignment: None to empty string | 4 | 2 | 2 | 50.0 % | 52 s |
| augmented assignment to plain | 2 | 1 | 1 | 50.0 % | 43 s |
| operator: augmented | 2 | 2 | 0 | 100.0 % | 2 s |

## By file

| file | mutants | killed | killed % | time |
|---|---:|---:|---:|---:|
| src/lovspor/forarbeider/publication_authorize.py | 678 | 537 | 79.2 % | 7154 s |
| src/lovspor/forarbeider/publication_path_b.py | 521 | 422 | 81.0 % | 4491 s |
| src/lovspor/forarbeider/publication_integrity.py | 368 | 314 | 85.3 % | 1872 s |
| src/lovspor/git_publish.py | 138 | 85 | 61.6 % | 3530 s |
| src/lovspor/forarbeider/publication_execute.py | 510 | 458 | 89.8 % | 4143 s |
| src/lovspor/forarbeider/publication_attempts.py | 240 | 191 | 79.6 % | 2341 s |
| src/lovspor/forarbeider/publication_remote_recovery.py | 556 | 508 | 91.4 % | 2480 s |
| src/lovspor/forarbeider/publication_path_a.py | 194 | 156 | 80.4 % | 923 s |
| src/lovspor/forarbeider/publication_remote.py | 310 | 284 | 91.6 % | 1011 s |
| src/lovspor/forarbeider/publication_sandbox.py | 239 | 224 | 93.7 % | 1092 s |
| src/lovspor/forarbeider/publication_receipts.py | 83 | 78 | 94.0 % | 642 s |
| src/lovspor/identity/history.py | 114 | 109 | 95.6 % | 227 s |
| src/lovspor/forarbeider/publication_execution_models.py | 35 | 31 | 88.6 % | 202 s |
| src/lovspor/history.py | 79 | 76 | 96.2 % | 154 s |
| src/lovspor/sync/orchestrator.py | 88 | 85 | 96.6 % | 107 s |
| src/lovspor/forarbeider/publication_recovery_models.py | 26 | 26 | 100.0 % | 37 s |

## Functions with mutants left alive

| function | tests | alive | mutants | time |
|---|---:|---:|---:|---:|
| `lovspor.git_publish.x_environment` | 119 | 33 | 49 | 2014 s |
| `lovspor.forarbeider.publication_path_b.x_push` | 119 | 27 | 73 | 2409 s |
| `lovspor.forarbeider.publication_integrity.x_authorization` | 35 | 20 | 94 | 794 s |
| `lovspor.forarbeider.publication_authorize.x__policy` | 81 | 18 | 55 | 844 s |
| `lovspor.forarbeider.publication_path_a.x__stale` | 31 | 18 | 49 | 344 s |
| `lovspor.forarbeider.publication_authorize.x__plan` | 81 | 16 | 35 | 722 s |
| `lovspor.forarbeider.publication_authorize.x__quarantine` | 81 | 14 | 47 | 647 s |
| `lovspor.git_publish.x_run_git` | 128 | 13 | 52 | 1089 s |
| `lovspor.forarbeider.publication_attempts.x__parsed` | 65 | 12 | 25 | 441 s |
| `lovspor.forarbeider.publication_authorize.x__publisher` | 81 | 12 | 36 | 540 s |
| `lovspor.forarbeider.publication_authorize.x__limits` | 81 | 11 | 48 | 509 s |
| `lovspor.forarbeider.publication_execute.x__facts` | 74 | 11 | 50 | 726 s |
| `lovspor.forarbeider.publication_path_b.x_ref_event` | 14 | 11 | 48 | 189 s |
| `lovspor.forarbeider.publication_remote_recovery.x__ledger` | 26 | 11 | 56 | 273 s |
| `lovspor.forarbeider.publication_authorize.x__path_identity` | 81 | 10 | 32 | 481 s |
| `lovspor.forarbeider.publication_authorize.x__proposal` | 81 | 10 | 40 | 566 s |
| `lovspor.forarbeider.publication_authorize.x__remote` | 81 | 10 | 21 | 437 s |
| `lovspor.forarbeider.publication_path_a.x_judged` | 32 | 10 | 51 | 239 s |
| `lovspor.forarbeider.publication_attempts.x__claim` | 110 | 9 | 54 | 552 s |
| `lovspor.forarbeider.publication_path_b.x__content_reasons` | 20 | 9 | 53 | 183 s |
| `lovspor.forarbeider.publication_remote.x__headers` | 15 | 9 | 34 | 137 s |
| `lovspor.forarbeider.publication_remote_recovery.x__reader` | 50 | 9 | 26 | 290 s |
| `lovspor.forarbeider.publication_attempts.x_create_once` | 88 | 8 | 28 | 386 s |
| `lovspor.forarbeider.publication_integrity.x__manifest` | 40 | 8 | 31 | 221 s |
| `lovspor.forarbeider.publication_path_b.x_split_signature` | 25 | 8 | 35 | 206 s |
| `lovspor.forarbeider.publication_path_b.x_verify_commit` | 20 | 8 | 60 | 180 s |
| `lovspor.forarbeider.publication_attempts.x_private_workspace` | None | 7 | 7 | 0 s |
| `lovspor.forarbeider.publication_authorize.x__approval_reasons` | 81 | 7 | 13 | 308 s |
| `lovspor.forarbeider.publication_authorize.x__control` | 81 | 7 | 36 | 344 s |
| `lovspor.forarbeider.publication_execute.x__remote` | 87 | 7 | 25 | 378 s |
| `lovspor.forarbeider.publication_execute.x__send` | 61 | 7 | 39 | 370 s |
| `lovspor.forarbeider.publication_execute.x__settled` | 88 | 7 | 57 | 879 s |
| `lovspor.forarbeider.publication_path_b.x_remote_head` | 119 | 7 | 27 | 474 s |
| `lovspor.forarbeider.publication_remote_recovery.x__attempt` | 15 | 7 | 55 | 124 s |
| `lovspor.forarbeider.publication_attempts.x__folder` | 110 | 6 | 15 | 341 s |
| `lovspor.forarbeider.publication_authorize.x__budget` | 81 | 6 | 38 | 300 s |
| `lovspor.forarbeider.publication_integrity.x__not_stopped` | 23 | 6 | 26 | 122 s |
| `lovspor.forarbeider.publication_remote.x__github_commit` | 29 | 6 | 33 | 143 s |
| `lovspor.forarbeider.publication_remote.x__signature_reasons` | 15 | 6 | 38 | 135 s |
| `lovspor.forarbeider.publication_authorize.x__checks` | 81 | 5 | 55 | 309 s |
| `lovspor.forarbeider.publication_authorize.x__within` | 78 | 5 | 5 | 208 s |
| `lovspor.forarbeider.publication_integrity.x__manifest_file` | 40 | 5 | 17 | 139 s |
| `lovspor.forarbeider.publication_integrity.x_integrity_checks` | 40 | 5 | 66 | 191 s |
| `lovspor.forarbeider.publication_remote_recovery.x__not_published` | 3 | 5 | 36 | 56 s |
| `lovspor.forarbeider.publication_sandbox.x__intact` | 119 | 5 | 38 | 362 s |
| `lovspor.forarbeider.publication_execute.x__require_authorized` | 67 | 4 | 32 | 210 s |
| `lovspor.forarbeider.publication_execution_models.x_decide` | 82 | 4 | 35 | 202 s |
| `lovspor.forarbeider.publication_path_a.x__commit` | 29 | 4 | 30 | 95 s |
| `lovspor.forarbeider.publication_path_b.x__git` | 23 | 4 | 20 | 81 s |
| `lovspor.forarbeider.publication_path_b.x_remote_tree` | 15 | 4 | 25 | 76 s |
| `lovspor.forarbeider.publication_remote_recovery.x__reconcile` | 41 | 4 | 31 | 291 s |
| `lovspor.forarbeider.publication_sandbox.x_create_disposable_remote` | 119 | 4 | 46 | 279 s |
| `lovspor.git_publish.x__local_bare` | 119 | 4 | 11 | 235 s |
| `lovspor.forarbeider.publication_attempts.x__walk` | 110 | 3 | 20 | 199 s |
| `lovspor.forarbeider.publication_attempts.x_append` | 76 | 3 | 24 | 146 s |
| `lovspor.forarbeider.publication_authorize.x__bot_signature` | 81 | 3 | 22 | 401 s |
| `lovspor.forarbeider.publication_authorize.x__plan_of` | 81 | 3 | 19 | 156 s |
| `lovspor.forarbeider.publication_execute.x__after` | 57 | 3 | 53 | 180 s |
| `lovspor.forarbeider.publication_execute.x__denied` | 14 | 3 | 43 | 71 s |
| `lovspor.forarbeider.publication_execute.x__files` | 65 | 3 | 12 | 117 s |
| `lovspor.forarbeider.publication_execute.x__publish` | 67 | 3 | 53 | 185 s |
| `lovspor.forarbeider.publication_integrity.x__files` | 40 | 3 | 18 | 97 s |
| `lovspor.forarbeider.publication_integrity.x_sealed_plan` | 35 | 3 | 19 | 78 s |
| `lovspor.forarbeider.publication_path_a.x_payload_of` | 44 | 3 | 42 | 131 s |
| `lovspor.forarbeider.publication_path_a.x_send` | 44 | 3 | 21 | 113 s |
| `lovspor.forarbeider.publication_path_b.x_blob_id` | 20 | 3 | 9 | 49 s |
| `lovspor.forarbeider.publication_path_b.x_init_bare` | 119 | 3 | 21 | 189 s |
| `lovspor.forarbeider.publication_path_b.x_remote_object` | 15 | 3 | 12 | 43 s |
| `lovspor.forarbeider.publication_path_b.x_signed_object` | 23 | 3 | 20 | 71 s |
| `lovspor.forarbeider.publication_remote_recovery.x__authorization_fields` | 38 | 3 | 15 | 82 s |
| `lovspor.forarbeider.publication_sandbox.xǁSimulatedGitHubǁ_apply` | 51 | 3 | 33 | 125 s |
| `lovspor.git_publish.x__fits` | 128 | 3 | 15 | 184 s |
| `lovspor.identity.history.x__to_preserve` | 18 | 3 | 28 | 41 s |
| `lovspor.forarbeider.publication_authorize.x__trust_root` | 81 | 2 | 15 | 102 s |
| `lovspor.forarbeider.publication_authorize.x__verified` | 81 | 2 | 22 | 115 s |
| `lovspor.forarbeider.publication_execute.x__step` | 95 | 2 | 16 | 136 s |
| `lovspor.forarbeider.publication_integrity.x__policy_held` | 24 | 2 | 38 | 78 s |
| `lovspor.forarbeider.publication_path_b.x__answer` | 119 | 2 | 6 | 119 s |
| `lovspor.forarbeider.publication_path_b.x__parent` | 20 | 2 | 15 | 49 s |
| `lovspor.forarbeider.publication_path_b.x_build_commit` | 23 | 2 | 60 | 101 s |
| `lovspor.forarbeider.publication_receipts.x__read` | 16 | 2 | 15 | 38 s |
| `lovspor.forarbeider.publication_remote.x_history` | 54 | 2 | 26 | 290 s |
| `lovspor.forarbeider.publication_remote_recovery.x__conflict` | 1 | 2 | 11 | 16 s |
| `lovspor.forarbeider.publication_remote_recovery.x__drafts` | 41 | 2 | 27 | 292 s |
| `lovspor.forarbeider.publication_remote_recovery.x__verdict` | 38 | 2 | 22 | 78 s |
| `lovspor.forarbeider.publication_sandbox.x__write_new` | 119 | 2 | 18 | 133 s |
| `lovspor.history.x_extract_earlier_lineage` | 19 | 2 | 37 | 36 s |
| `lovspor.identity.history.x__displaced_lineage` | 109 | 2 | 36 | 124 s |
| `lovspor.sync.orchestrator.x__commit_history_followup` | 11 | 2 | 35 | 25 s |
| `lovspor.forarbeider.publication_attempts.x__names` | 110 | 1 | 16 | 172 s |
| `lovspor.forarbeider.publication_execute.x__context` | 88 | 1 | 39 | 484 s |
| `lovspor.forarbeider.publication_execute.x__latch_reasons` | 14 | 1 | 13 | 31 s |
| `lovspor.forarbeider.publication_integrity.x__attempt` | 35 | 1 | 7 | 53 s |
| `lovspor.forarbeider.publication_integrity.x__known_by` | 24 | 1 | 11 | 33 s |
| `lovspor.forarbeider.publication_path_b.x__blobs` | 20 | 1 | 19 | 39 s |
| `lovspor.forarbeider.publication_path_b.x__identity` | 23 | 1 | 2 | 18 s |
| `lovspor.forarbeider.publication_path_b.x__refused` | 1 | 1 | 4 | 5 s |
| `lovspor.forarbeider.publication_receipts.x__folder` | 99 | 1 | 6 | 69 s |
| `lovspor.forarbeider.publication_receipts.x__refused` | 2 | 1 | 4 | 6 s |
| `lovspor.forarbeider.publication_receipts.x_append` | 38 | 1 | 16 | 39 s |
| `lovspor.forarbeider.publication_remote.x__bare_commit` | 15 | 1 | 62 | 108 s |
| `lovspor.forarbeider.publication_remote.x__changed` | 15 | 1 | 38 | 84 s |
| `lovspor.forarbeider.publication_remote.x__walk` | 54 | 1 | 36 | 49 s |
| `lovspor.forarbeider.publication_remote_recovery.x__draft` | 38 | 1 | 62 | 133 s |
| `lovspor.forarbeider.publication_remote_recovery.x__reservation` | 26 | 1 | 13 | 144 s |
| `lovspor.forarbeider.publication_remote_recovery.x__settle` | 38 | 1 | 52 | 246 s |
| `lovspor.forarbeider.publication_sandbox.x__read` | 119 | 1 | 12 | 68 s |
| `lovspor.history.x__walk` | 156 | 1 | 32 | 97 s |
| `lovspor.sync.orchestrator.x__generate_and_apply_history` | 97 | 1 | 53 | 82 s |

## Slowest mutants

| mutant | status | kind | seconds | estimate |
|---|---:|---:|---:|---:|
| `lovspor.git_publish.x_run_git__mutmut_4` | survived | number | 60.149 | 8.382 |
| `lovspor.forarbeider.publication_sandbox.x_create_disposable_remote__mutmut_14` | survived | argument: replaced by None | 60.135 | 8.38 |
| `lovspor.git_publish.x_run_git__mutmut_46` | survived | number | 60.124 | 8.382 |
| `lovspor.git_publish.x_run_git__mutmut_14` | survived | string: upper case | 60.05 | 8.382 |
| `lovspor.forarbeider.publication_sandbox.x__intact__mutmut_19` | survived | string: upper case | 60.0 | 8.38 |
| `lovspor.forarbeider.publication_sandbox.x_create_disposable_remote__mutmut_15` | survived | number | 59.981 | 8.38 |
| `lovspor.git_publish.x_run_git__mutmut_45` | survived | argument: replaced by None | 59.84 | 8.382 |
| `lovspor.git_publish.x_run_git__mutmut_12` | survived | argument: replaced by None | 59.644 | 8.382 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_30` | survived | argument: removed | 59.617 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_29` | survived | argument: replaced by None | 59.466 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_28` | survived | argument: replaced by None | 59.363 | 8.38 |
| `lovspor.git_publish.x_run_git__mutmut_20` | survived | argument: replaced by None | 59.349 | 8.382 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_27` | survived | argument: replaced by None | 59.331 | 8.38 |
| `lovspor.git_publish.x_run_git__mutmut_13` | survived | string: XX added | 59.326 | 8.382 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_23` | survived | string: XX added | 59.299 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_22` | survived | string: upper case | 59.293 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_21` | survived | string: XX added | 59.286 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_31` | survived | argument: removed | 59.216 | 8.38 |
| `lovspor.git_publish.x_run_git__mutmut_26` | survived | argument: removed | 59.184 | 8.382 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_73` | survived | string: upper case | 59.163 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_72` | survived | string: XX added | 59.148 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_35` | survived | string: XX added | 59.13 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_32` | survived | argument: removed | 59.117 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_24` | survived | string: upper case | 59.106 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_20` | survived | argument: removed | 59.074 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_19` | survived | argument: removed | 59.042 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_36` | survived | string: upper case | 59.012 | 8.38 |
| `lovspor.git_publish.x_run_git__mutmut_34` | survived | ternary: condition forced | 58.952 | 8.382 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_34` | survived | string: upper case | 58.931 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_17` | survived | argument: replaced by None | 58.911 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_33` | survived | string: XX added | 58.869 | 8.38 |
| `lovspor.git_publish.x__fits__mutmut_15` | survived | True / False swapped | 58.848 | 8.382 |
| `lovspor.git_publish.x_run_git__mutmut_3` | survived | argument: replaced by None | 58.814 | 8.382 |
| `lovspor.forarbeider.publication_sandbox.x__write_new__mutmut_7` | survived | argument: removed | 58.792 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_15` | survived | argument: replaced by None | 58.719 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_18` | survived | argument: removed | 58.703 | 8.38 |
| `lovspor.forarbeider.publication_sandbox.x__write_new__mutmut_10` | survived | number | 58.644 | 8.38 |
| `lovspor.git_publish.x_run_git__mutmut_19` | survived | argument: replaced by None | 58.558 | 8.382 |
| `lovspor.git_publish.x_run_git__mutmut_36` | survived | string: XX added | 58.481 | 8.382 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_47` | survived | operator: and / or | 58.435 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_16` | survived | argument: replaced by None | 58.406 | 8.38 |
| `lovspor.git_publish.x_run_git__mutmut_25` | survived | argument: removed | 58.358 | 8.382 |
| `lovspor.git_publish.x__fits__mutmut_11` | survived | argument: replaced by None | 58.099 | 8.382 |
| `lovspor.forarbeider.publication_sandbox.x_create_disposable_remote__mutmut_7` | survived | True / False swapped | 58.09 | 8.38 |
| `lovspor.forarbeider.publication_sandbox.x_create_disposable_remote__mutmut_6` | survived | assignment: value to None | 58.019 | 8.38 |
| `lovspor.git_publish.x__fits__mutmut_14` | survived | argument: removed | 58.019 | 8.382 |
| `lovspor.forarbeider.publication_path_b.x_remote_head__mutmut_23` | survived | string: XX added | 57.862 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_push__mutmut_63` | survived | string: upper case | 57.853 | 8.38 |
| `lovspor.forarbeider.publication_sandbox.x__read__mutmut_6` | survived | operator: bitwise | 57.831 | 8.38 |
| `lovspor.forarbeider.publication_path_b.x_remote_head__mutmut_20` | survived | argument: removed | 57.83 | 8.38 |

## Not killed this night (588)

<details><summary><code>src/lovspor/forarbeider/publication_attempts.py</code>: 42</summary>

- `create_once` · survived · argument: replaced by None · `d808e634e6016d00`
  ```diff
  -with directory(folder, "attempts") as parent:
  +with directory(folder, None) as parent:
  ```
- `create_once` · survived · string: XX added · `990c1093f055201d`
  ```diff
  -with directory(folder, "attempts") as parent:
  +with directory(folder, "XXattemptsXX") as parent:
  ```
- `create_once` · survived · string: upper case · `0f20f48227df97df`
  ```diff
  -with directory(folder, "attempts") as parent:
  +with directory(folder, "ATTEMPTS") as parent:
  ```
- `create_once` · survived · argument: removed · `fd5ebbadccfd2a05`
  ```diff
  -descriptor = os.open(name, _CREATE, 0o600, dir_fd=parent)
  +descriptor = os.open(name, _CREATE, dir_fd=parent)
  ```
- `create_once` · survived · number · `582760e0ccc64d0a`
  ```diff
  -descriptor = os.open(name, _CREATE, 0o600, dir_fd=parent)
  +descriptor = os.open(name, _CREATE, 385, dir_fd=parent)
  ```
- `create_once` · survived · argument: replaced by None · `92fa8993d81cd432`
  ```diff
  -raise _refused("attempts") from error
  +raise _refused(None) from error
  ```
- `create_once` · survived · string: XX added · `6219720a5c55fce7`
  ```diff
  -raise _refused("attempts") from error
  +raise _refused("XXattemptsXX") from error
  ```
- `create_once` · survived · string: upper case · `d40ee80b969671e3`
  ```diff
  -raise _refused("attempts") from error
  +raise _refused("ATTEMPTS") from error
  ```
- `_folder` · survived · True / False swapped · `706c7e37c41196f1`
  ```diff
  -def _folder(workspace: Path, transaction_id: str, create: bool = False) -> Path:
  +def _folder(workspace: Path, transaction_id: str, create: bool = True) -> Path:
  ```
- `_folder` · survived · number · `8399098365d93e67`
  ```diff
  -folder.mkdir(mode=0o700)
  +folder.mkdir(mode=449)
  ```
- `_folder` · survived · operator: and / or · `d6106a507e18ab0a`
  ```diff
  -if os.path.lexists(folder) and (folder.is_symlink() or not folder.is_dir()):
  +if os.path.lexists(folder) and (folder.is_symlink() and not folder.is_dir()):
  ```
- `_folder` · survived · argument: replaced by None · `e2862b32eec92406`
  ```diff
  -raise _refused("attempts")
  +raise _refused(None)
  ```
- `_folder` · survived · string: XX added · `b3639fafea33c8c0`
  ```diff
  -raise _refused("attempts")
  +raise _refused("XXattemptsXX")
  ```
- `_folder` · survived · string: upper case · `d5e9b239c4c06e33`
  ```diff
  -raise _refused("attempts")
  +raise _refused("ATTEMPTS")
  ```
- `_names` · survived · string: XX added · `494f4647d3dbf476`
  ```diff
  -raise _refused("attempts")
  +raise _refused("XXattemptsXX")
  ```
- `_parsed` · survived · argument: replaced by None · `bd4a37636e74744b`
  ```diff
  -with directory(folder, "attempts") as parent:
  +with directory(folder, None) as parent:
  ```
- `_parsed` · survived · string: XX added · `1d0186b856884043`
  ```diff
  -with directory(folder, "attempts") as parent:
  +with directory(folder, "XXattemptsXX") as parent:
  ```
- `_parsed` · survived · string: upper case · `aefd6058cfac9908`
  ```diff
  -with directory(folder, "attempts") as parent:
  +with directory(folder, "ATTEMPTS") as parent:
  ```
- `_parsed` · survived · argument: replaced by None · `b79fc4f6d23f2e63`
  ```diff
  -raw = read_leaf(parent, name, "attempts")
  +raw = read_leaf(parent, name, None)
  ```
- `_parsed` · survived · string: XX added · `c193dc56f78c84be`
  ```diff
  -raw = read_leaf(parent, name, "attempts")
  +raw = read_leaf(parent, name, "XXattemptsXX")
  ```
- `_parsed` · survived · string: upper case · `7ac53fa4d4d432e4`
  ```diff
  -raw = read_leaf(parent, name, "attempts")
  +raw = read_leaf(parent, name, "ATTEMPTS")
  ```
- `_parsed` · survived · argument: replaced by None · `77c48d2b7c3ee07f`
  ```diff
  -raise _refused("attempts") from error
  +raise _refused(None) from error
  ```
- `_parsed` · survived · string: XX added · `3c5325ca790d299c`
  ```diff
  -raise _refused("attempts") from error
  +raise _refused("XXattemptsXX") from error
  ```
- `_parsed` · survived · string: upper case · `9d3fa4509a784e07`
  ```diff
  -raise _refused("attempts") from error
  +raise _refused("ATTEMPTS") from error
  ```
- `_parsed` · survived · argument: replaced by None · `73bcedb95fac2db7`
  ```diff
  -raise _refused("attempts")
  +raise _refused(None)
  ```
- `_parsed` · survived · string: XX added · `5b93a05f865d29bc`
  ```diff
  -raise _refused("attempts")
  +raise _refused("XXattemptsXX")
  ```
- `_parsed` · survived · string: upper case · `b8966190eb629c0a`
  ```diff
  -raise _refused("attempts")
  +raise _refused("ATTEMPTS")
  ```
- `_walk` · survived · argument: replaced by None · `5fc8708db5b7156d`
  ```diff
  -raise _refused("attempts")
  +raise _refused(None)
  ```
- `_walk` · survived · string: XX added · `e564e9c07de8c938`
  ```diff
  -raise _refused("attempts")
  +raise _refused("XXattemptsXX")
  ```
- `_walk` · survived · string: upper case · `f5d5b9c8fc0a03d2`
  ```diff
  -raise _refused("attempts")
  +raise _refused("ATTEMPTS")
  ```
- `append` · survived · argument: replaced by None · `e32e8b221438b625`
  ```diff
  -raise _refused("attempts")
  +raise _refused(None)
  ```
- `append` · survived · string: XX added · `598f3a498214f65c`
  ```diff
  -raise _refused("attempts")
  +raise _refused("XXattemptsXX")
  ```
- `append` · survived · string: upper case · `05e0d12bf7eefb71`
  ```diff
  -raise _refused("attempts")
  +raise _refused("ATTEMPTS")
  ```
- `_claim` · survived · argument: replaced by None · `e39b379002ea17a7`
  ```diff
  -named = os.stat(_LOCK, dir_fd=parent, follow_symlinks=False)
  +named = os.stat(_LOCK, dir_fd=parent, follow_symlinks=None)
  ```
- `_claim` · survived · argument: removed · `4ac7a40a59f28b40`
  ```diff
  -named = os.stat(_LOCK, dir_fd=parent, follow_symlinks=False)
  +named = os.stat(_LOCK, dir_fd=parent, )
  ```
- `_claim` · survived · True / False swapped · `752253cdd546fa81`
  ```diff
  -named = os.stat(_LOCK, dir_fd=parent, follow_symlinks=False)
  +named = os.stat(_LOCK, dir_fd=parent, follow_symlinks=True)
  ```
- `_claim` · survived · argument: replaced by None · `02ace0c4c290cff0`
  ```diff
  -raise _lock_refused(RefusalReason.CORPUS_LOCKED, "workspace") from error
  +raise _lock_refused(None, "workspace") from error
  ```
- `_claim` · survived · argument: replaced by None · `8593810d4d8aad3f`
  ```diff
  -raise _lock_refused(RefusalReason.CORPUS_LOCKED, "workspace") from error
  +raise _lock_refused(RefusalReason.CORPUS_LOCKED, None) from error
  ```
- `_claim` · survived · argument: removed · `f2914cf3db80901a`
  ```diff
  -raise _lock_refused(RefusalReason.CORPUS_LOCKED, "workspace") from error
  +raise _lock_refused("workspace") from error
  ```
- `_claim` · survived · argument: removed · `34046488a92e91d1`
  ```diff
  -raise _lock_refused(RefusalReason.CORPUS_LOCKED, "workspace") from error
  +raise _lock_refused(RefusalReason.CORPUS_LOCKED, ) from error
  ```
- `_claim` · survived · string: XX added · `82fb5d24cb7bd2b6`
  ```diff
  -raise _lock_refused(RefusalReason.CORPUS_LOCKED, "workspace") from error
  +raise _lock_refused(RefusalReason.CORPUS_LOCKED, "XXworkspaceXX") from error
  ```
- `_claim` · survived · string: upper case · `e5586c04d175e1cf`
  ```diff
  -raise _lock_refused(RefusalReason.CORPUS_LOCKED, "workspace") from error
  +raise _lock_refused(RefusalReason.CORPUS_LOCKED, "WORKSPACE") from error
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_authorize.py</code>: 141</summary>

- `_proposal` · survived · string: XX added · `1e9d95a0b47af86e`
  ```diff
  -return _check("proposal", ["proposal_not_prepared"])
  +return _check("proposal", ["XXproposal_not_preparedXX"])
  ```
- `_proposal` · survived · string: upper case · `f51d951c1dc843e2`
  ```diff
  -return _check("proposal", ["proposal_not_prepared"])
  +return _check("proposal", ["PROPOSAL_NOT_PREPARED"])
  ```
- `_proposal` · survived · argument: replaced by None · `c4ab063609c293c2`
  ```diff
  -return _check("proposal", ["proposal_not_this_transaction"])
  +return _check(None, ["proposal_not_this_transaction"])
  ```
- `_proposal` · survived · argument: replaced by None · `510682a27bc1625a`
  ```diff
  -return _check("proposal", ["proposal_not_this_transaction"])
  +return _check("proposal", None)
  ```
- `_proposal` · survived · argument: removed · `12308bec11e804af`
  ```diff
  -return _check("proposal", ["proposal_not_this_transaction"])
  +return _check(["proposal_not_this_transaction"])
  ```
- `_proposal` · survived · argument: removed · `30500cd2a2b65eab`
  ```diff
  -return _check("proposal", ["proposal_not_this_transaction"])
  +return _check("proposal", )
  ```
- `_proposal` · survived · string: XX added · `36b2c5f072706627`
  ```diff
  -return _check("proposal", ["proposal_not_this_transaction"])
  +return _check("XXproposalXX", ["proposal_not_this_transaction"])
  ```
- `_proposal` · survived · string: upper case · `2591f4107cc22bb1`
  ```diff
  -return _check("proposal", ["proposal_not_this_transaction"])
  +return _check("PROPOSAL", ["proposal_not_this_transaction"])
  ```
- `_proposal` · survived · string: XX added · `2f425f6e944749d4`
  ```diff
  -return _check("proposal", ["proposal_not_this_transaction"])
  +return _check("proposal", ["XXproposal_not_this_transactionXX"])
  ```
- `_proposal` · survived · string: upper case · `6556ecfd893b7f51`
  ```diff
  -return _check("proposal", ["proposal_not_this_transaction"])
  +return _check("proposal", ["PROPOSAL_NOT_THIS_TRANSACTION"])
  ```
- `_plan_of` · survived · operator: and / or · `74cd7085ad909c69`
  ```diff
  -sealed = evidence.status == "verified" and authentic(evidence, "batch_plan")
  +sealed = evidence.status == "verified" or authentic(evidence, "batch_plan")
  ```
- `_plan_of` · survived · operator: and / or · `31ba9366548bf986`
  ```diff
  -if plan is None or not sealed or evidence.plan_sha256 != digest_of(plan):
  +if plan is None or not sealed and evidence.plan_sha256 != digest_of(plan):
  ```
- `_plan_of` · survived · operator: and / or · `673e48196f719447`
  ```diff
  -if plan is None or not sealed or evidence.plan_sha256 != digest_of(plan):
  +if plan is None and not sealed or evidence.plan_sha256 != digest_of(plan):
  ```
- `_plan` · survived · argument: replaced by None · `445c6455a7217037`
  ```diff
  -return _check("plan", ["plan_unverified"])
  +return _check(None, ["plan_unverified"])
  ```
- `_plan` · survived · argument: replaced by None · `dbc54aee2d5e323b`
  ```diff
  -return _check("plan", ["plan_unverified"])
  +return _check("plan", None)
  ```
- `_plan` · survived · argument: removed · `191da7306995f288`
  ```diff
  -return _check("plan", ["plan_unverified"])
  +return _check(["plan_unverified"])
  ```
- `_plan` · survived · argument: removed · `a0bbe20fd2879509`
  ```diff
  -return _check("plan", ["plan_unverified"])
  +return _check("plan", )
  ```
- `_plan` · survived · string: XX added · `0727282ce768bcc7`
  ```diff
  -return _check("plan", ["plan_unverified"])
  +return _check("XXplanXX", ["plan_unverified"])
  ```
- `_plan` · survived · string: upper case · `a6219591d5481c18`
  ```diff
  -return _check("plan", ["plan_unverified"])
  +return _check("PLAN", ["plan_unverified"])
  ```
- `_plan` · survived · string: XX added · `fdb5bd84788ced6d`
  ```diff
  -return _check("plan", ["plan_unverified"])
  +return _check("plan", ["XXplan_unverifiedXX"])
  ```
- `_plan` · survived · string: upper case · `a79ca0aefc359597`
  ```diff
  -return _check("plan", ["plan_unverified"])
  +return _check("plan", ["PLAN_UNVERIFIED"])
  ```
- `_plan` · survived · argument: removed · `8cde60c8f62b2072`
  ```diff
  -batch = next((b for b in plan.batches if b.batch_id == content.batch_id), None)
  +batch = next((b for b in plan.batches if b.batch_id == content.batch_id), )
  ```
- `_plan` · survived · ternary: condition forced · `f417943673e53936`
  ```diff
  -reasons = [] if plan.plan_id == content.plan_id else ["plan_not_the_transactions"]
  +reasons = [] if (plan.plan_id == content.plan_id) or True else ["plan_not_the_transactions"]
  ```
- `_plan` · survived · string: XX added · `cbd1415a917ff85d`
  ```diff
  -reasons = [] if plan.plan_id == content.plan_id else ["plan_not_the_transactions"]
  +reasons = [] if plan.plan_id == content.plan_id else ["XXplan_not_the_transactionsXX"]
  ```
- `_plan` · survived · string: upper case · `4081c6c201cb2f0d`
  ```diff
  -reasons = [] if plan.plan_id == content.plan_id else ["plan_not_the_transactions"]
  +reasons = [] if plan.plan_id == content.plan_id else ["PLAN_NOT_THE_TRANSACTIONS"]
  ```
- `_plan` · survived · operator: and / or · `0e5146f7404eb708`
  ```diff
  -if batch is None or batch.document_ids != documents:
  +if batch is None and batch.document_ids != documents:
  ```
- `_plan` · survived · argument: replaced by None · `0cbf93b3ec799ad0`
  ```diff
  -reasons.append("batch_not_the_transactions")
  +reasons.append(None)
  ```
- `_plan` · survived · string: XX added · `754b28e4185f7945`
  ```diff
  -reasons.append("batch_not_the_transactions")
  +reasons.append("XXbatch_not_the_transactionsXX")
  ```
- `_plan` · survived · string: upper case · `18dc276344b6b1d6`
  ```diff
  -reasons.append("batch_not_the_transactions")
  +reasons.append("BATCH_NOT_THE_TRANSACTIONS")
  ```
- `_quarantine` · survived · operator: and / or · `52c06495f9ec1ff5`
  ```diff
  -sealed = found.status == "verified" and authentic(found, "quarantine_assessment")
  +sealed = found.status == "verified" or authentic(found, "quarantine_assessment")
  ```
- `_quarantine` · survived · ternary: condition forced · `0598974be11a8c8f`
  ```diff
  -digest = digest_of(found.quarantine) if found.quarantine is not None else None
  +digest = digest_of(found.quarantine) if (found.quarantine is not None) or True else None
  ```
- `_quarantine` · survived · operator: and / or · `8b8e5b047e62a677`
  ```diff
  -same = digest is not None and digest == found.quarantine_sha256 == content.quarantine_sha256
  +same = digest is not None or digest == found.quarantine_sha256 == content.quarantine_sha256
  ```
- `_quarantine` · survived · operator: and / or · `8c7526c53c19d709`
  ```diff
  -honoured = plan is not None and plan.basis.quarantine_sha256 == content.quarantine_sha256
  +honoured = plan is not None or plan.basis.quarantine_sha256 == content.quarantine_sha256
  ```
- `_quarantine` · survived · ternary: condition forced · `2caa7a35b59766af`
  ```diff
  -{item.document_id for item in found.quarantine.documents} if found.quarantine else set()
  +{item.document_id for item in found.quarantine.documents} if (found.quarantine) and False else set()
  ```
- `_quarantine` · survived · ternary: condition forced · `1a2633d77a78ac60`
  ```diff
  -{item.document_id for item in found.quarantine.documents} if found.quarantine else set()
  +{item.document_id for item in found.quarantine.documents} if (found.quarantine) or True else set()
  ```
- `_quarantine` · survived · ternary: condition forced · `8a24f863bbfe3529`
  ```diff
  -reasons = [] if sealed and same and honoured else ["quarantine_not_the_transactions"]
  +reasons = [] if (sealed and same and honoured) or True else ["quarantine_not_the_transactions"]
  ```
- `_quarantine` · survived · operator: and / or · `9a5f6a0e92a9d9cc`
  ```diff
  -reasons = [] if sealed and same and honoured else ["quarantine_not_the_transactions"]
  +reasons = [] if sealed and same or honoured else ["quarantine_not_the_transactions"]
  ```
- `_quarantine` · survived · operator: and / or · `b1f9fa2ca55debc3`
  ```diff
  -reasons = [] if sealed and same and honoured else ["quarantine_not_the_transactions"]
  +reasons = [] if sealed or same and honoured else ["quarantine_not_the_transactions"]
  ```
- `_quarantine` · survived · string: XX added · `6bdac50827966be6`
  ```diff
  -reasons = [] if sealed and same and honoured else ["quarantine_not_the_transactions"]
  +reasons = [] if sealed and same and honoured else ["XXquarantine_not_the_transactionsXX"]
  ```
- `_quarantine` · survived · string: upper case · `eef60099d8ffa0a0`
  ```diff
  -reasons = [] if sealed and same and honoured else ["quarantine_not_the_transactions"]
  +reasons = [] if sealed and same and honoured else ["QUARANTINE_NOT_THE_TRANSACTIONS"]
  ```
- `_quarantine` · survived · argument: replaced by None · `7f21e2a3a326fdb9`
  ```diff
  -reasons.append("quarantined_document_in_transaction")
  +reasons.append(None)
  ```
- `_quarantine` · survived · string: XX added · `4854e90a0e652b4f`
  ```diff
  -reasons.append("quarantined_document_in_transaction")
  +reasons.append("XXquarantined_document_in_transactionXX")
  ```
- `_quarantine` · survived · string: upper case · `2bc66bfa20496535`
  ```diff
  -reasons.append("quarantined_document_in_transaction")
  +reasons.append("QUARANTINED_DOCUMENT_IN_TRANSACTION")
  ```
- `_trust_root` · survived · string: XX added · `89c744af3c8c9b8a`
  ```diff
  -"trust_root", [] if same and context.root.entries else ["trust_root_not_the_plans"]
  +"trust_root", [] if same and context.root.entries else ["XXtrust_root_not_the_plansXX"]
  ```
- `_trust_root` · survived · string: upper case · `2f0693bc2f20dcfa`
  ```diff
  -"trust_root", [] if same and context.root.entries else ["trust_root_not_the_plans"]
  +"trust_root", [] if same and context.root.entries else ["TRUST_ROOT_NOT_THE_PLANS"]
  ```
- `_approval_reasons` · survived · string: XX added · `b0b2fef9eec5c6c8`
  ```diff
  -("policy_approval", verdict.policy_approval),
  +("XXpolicy_approvalXX", verdict.policy_approval),
  ```
- `_approval_reasons` · survived · string: upper case · `570ddbf9f0149819`
  ```diff
  -("policy_approval", verdict.policy_approval),
  +("POLICY_APPROVAL", verdict.policy_approval),
  ```
- `_approval_reasons` · survived · string: XX added · `0d30ce043914cb89`
  ```diff
  -("automated_publication", verdict.automated_publication),
  +("XXautomated_publicationXX", verdict.automated_publication),
  ```
- `_approval_reasons` · survived · string: upper case · `323293bfe95c5889`
  ```diff
  -("automated_publication", verdict.automated_publication),
  +("AUTOMATED_PUBLICATION", verdict.automated_publication),
  ```
- `_approval_reasons` · survived · operator: comparison · `7e7424c877a4fee1`
  ```diff
  -f"revocation:{r}" for c in verdict.revocations if c.status != "verified" for r in c.reasons
  +f"revocation:{r}" for c in verdict.revocations if c.status == "verified" for r in c.reasons
  ```
- `_approval_reasons` · survived · string: XX added · `a2e9f70b53269a93`
  ```diff
  -f"revocation:{r}" for c in verdict.revocations if c.status != "verified" for r in c.reasons
  +f"revocation:{r}" for c in verdict.revocations if c.status != "XXverifiedXX" for r in c.reasons
  ```
- `_approval_reasons` · survived · string: upper case · `c10eeaf114c5f389`
  ```diff
  -f"revocation:{r}" for c in verdict.revocations if c.status != "verified" for r in c.reasons
  +f"revocation:{r}" for c in verdict.revocations if c.status != "VERIFIED" for r in c.reasons
  ```
- `_policy` · survived · assignment: value to None · `91ed65dedafc9738`
  ```diff
  -pending = _unavailable("fc7b_pending_blockers", "policy_unreadable")
  +pending = None
  ```
- `_policy` · survived · argument: replaced by None · `4d83e7563646980b`
  ```diff
  -pending = _unavailable("fc7b_pending_blockers", "policy_unreadable")
  +pending = _unavailable(None, "policy_unreadable")
  ```
- `_policy` · survived · argument: replaced by None · `98a72e3998b8fe49`
  ```diff
  -pending = _unavailable("fc7b_pending_blockers", "policy_unreadable")
  +pending = _unavailable("fc7b_pending_blockers", None)
  ```
- `_policy` · survived · argument: removed · `124d35f82925632d`
  ```diff
  -pending = _unavailable("fc7b_pending_blockers", "policy_unreadable")
  +pending = _unavailable("policy_unreadable")
  ```
- `_policy` · survived · argument: removed · `bea5df8d5990a5e9`
  ```diff
  -pending = _unavailable("fc7b_pending_blockers", "policy_unreadable")
  +pending = _unavailable("fc7b_pending_blockers", )
  ```
- `_policy` · survived · string: XX added · `228901671afacbe1`
  ```diff
  -pending = _unavailable("fc7b_pending_blockers", "policy_unreadable")
  +pending = _unavailable("XXfc7b_pending_blockersXX", "policy_unreadable")
  ```
- `_policy` · survived · string: upper case · `bc0b13aeeed91794`
  ```diff
  -pending = _unavailable("fc7b_pending_blockers", "policy_unreadable")
  +pending = _unavailable("FC7B_PENDING_BLOCKERS", "policy_unreadable")
  ```
- `_policy` · survived · string: XX added · `1431ce676e95cc8a`
  ```diff
  -pending = _unavailable("fc7b_pending_blockers", "policy_unreadable")
  +pending = _unavailable("fc7b_pending_blockers", "XXpolicy_unreadableXX")
  ```
- `_policy` · survived · string: upper case · `57c71f2e7e390bcf`
  ```diff
  -pending = _unavailable("fc7b_pending_blockers", "policy_unreadable")
  +pending = _unavailable("fc7b_pending_blockers", "POLICY_UNREADABLE")
  ```
- `_policy` · survived · argument: replaced by None · `311f4cb0988575f6`
  ```diff
  -return _check("policy", [f"policy:{error.detail}"]), pending
  +return _check(None, [f"policy:{error.detail}"]), pending
  ```
- `_policy` · survived · argument: replaced by None · `43ff28ed39da955a`
  ```diff
  -return _check("policy", [f"policy:{error.detail}"]), pending
  +return _check("policy", None), pending
  ```
- `_policy` · survived · argument: removed · `04f6f86205f28888`
  ```diff
  -return _check("policy", [f"policy:{error.detail}"]), pending
  +return _check([f"policy:{error.detail}"]), pending
  ```
- `_policy` · survived · argument: removed · `ff611a5937da8388`
  ```diff
  -return _check("policy", [f"policy:{error.detail}"]), pending
  +return _check("policy", ), pending
  ```
- `_policy` · survived · string: XX added · `29071c490f9038b7`
  ```diff
  -return _check("policy", [f"policy:{error.detail}"]), pending
  +return _check("XXpolicyXX", [f"policy:{error.detail}"]), pending
  ```
- `_policy` · survived · string: upper case · `6a83151bea02673c`
  ```diff
  -return _check("policy", [f"policy:{error.detail}"]), pending
  +return _check("POLICY", [f"policy:{error.detail}"]), pending
  ```
- `_policy` · survived · string: XX added · `22b0bfce19dafda3`
  ```diff
  -reasons.append("policy_not_the_plans")
  +reasons.append("XXpolicy_not_the_plansXX")
  ```
- `_policy` · survived · string: upper case · `d00c30620ef26afe`
  ```diff
  -reasons.append("policy_not_the_plans")
  +reasons.append("POLICY_NOT_THE_PLANS")
  ```
- `_policy` · survived · string: XX added · `2baa95f8de8d4654`
  ```diff
  -pending = _unavailable("fc7b_pending_blockers", ",".join(verdict.blockers))
  +pending = _unavailable("fc7b_pending_blockers", "XX,XX".join(verdict.blockers))
  ```
- `_publisher` · survived · argument: removed · `7504b0f42ac20abf`
  ```diff
  -entry = next((e for e in context.root.entries if e.principal == principal), None)
  +entry = next((e for e in context.root.entries if e.principal == principal), )
  ```
- `_publisher` · survived · operator: and / or · `c7ebfabad774ba82`
  ```diff
  -good = entry is not None and entry.role == "bot_execution" and wanted <= set(entry.namespaces)
  +good = entry is not None and entry.role == "bot_execution" or wanted <= set(entry.namespaces)
  ```
- `_publisher` · survived · operator: and / or · `b37fc7a583c6d9d6`
  ```diff
  -good = entry is not None and entry.role == "bot_execution" and wanted <= set(entry.namespaces)
  +good = entry is not None or entry.role == "bot_execution" and wanted <= set(entry.namespaces)
  ```
- `_publisher` · survived · assignment: value to None · `b1d820b1d78258f7`
  ```diff
  -revoked = entry is not None and entry.key_blob in context.root.revoked_blobs
  +revoked = None
  ```
- `_publisher` · survived · is / in negated · `6572cbb39a5102a9`
  ```diff
  -revoked = entry is not None and entry.key_blob in context.root.revoked_blobs
  +revoked = entry is None and entry.key_blob in context.root.revoked_blobs
  ```
- `_publisher` · survived · ternary: condition forced · `ab1474ebd25d44db`
  ```diff
  -reasons = [] if good and not revoked else ["publisher_not_a_listed_bot_key"]
  +reasons = [] if (good and not revoked) or True else ["publisher_not_a_listed_bot_key"]
  ```
- `_publisher` · survived · operator: and / or · `9b5c9dd9d94d61c7`
  ```diff
  -reasons = [] if good and not revoked else ["publisher_not_a_listed_bot_key"]
  +reasons = [] if good or not revoked else ["publisher_not_a_listed_bot_key"]
  ```
- `_publisher` · survived · string: XX added · `3c122b9016e61587`
  ```diff
  -reasons = [] if good and not revoked else ["publisher_not_a_listed_bot_key"]
  +reasons = [] if good and not revoked else ["XXpublisher_not_a_listed_bot_keyXX"]
  ```
- `_publisher` · survived · string: upper case · `a154728a09acb173`
  ```diff
  -reasons = [] if good and not revoked else ["publisher_not_a_listed_bot_key"]
  +reasons = [] if good and not revoked else ["PUBLISHER_NOT_A_LISTED_BOT_KEY"]
  ```
- `_publisher` · survived · ternary: condition forced · `5e40b5943f01b430`
  ```diff
  -return _check("publisher", reasons), None if entry is None else entry.fingerprint
  +return _check("publisher", reasons), None if (entry is None) and False else entry.fingerprint
  ```
- `_publisher` · survived · ternary: condition forced · `0c6c08e66011aa95`
  ```diff
  -return _check("publisher", reasons), None if entry is None else entry.fingerprint
  +return _check("publisher", reasons), None if (entry is None) or True else entry.fingerprint
  ```
- `_publisher` · survived · is / in negated · `c381eecb1a2a9c86`
  ```diff
  -return _check("publisher", reasons), None if entry is None else entry.fingerprint
  +return _check("publisher", reasons), None if entry is not None else entry.fingerprint
  ```
- `_within` · survived · operator: and / or · `66cede097bfcaac5`
  ```diff
  -and counts.files <= caps.max_files_per_batch
  -and counts.bytes <= caps.max_bytes_per_batch
  +and counts.files <= caps.max_files_per_batch or counts.bytes <= caps.max_bytes_per_batch
  ```
- `_within` · survived · operator: and / or · `753aab09f2839b38`
  ```diff
  -counts.documents <= caps.max_documents_per_batch
  -and counts.files <= caps.max_files_per_batch
  +counts.documents <= caps.max_documents_per_batch or counts.files <= caps.max_files_per_batch
  ```
- `_within` · survived · operator: comparison · `f5c0e5f47ebcb0b5`
  ```diff
  -counts.documents <= caps.max_documents_per_batch
  +counts.documents < caps.max_documents_per_batch
  ```
- `_within` · survived · operator: comparison · `230f11f5bb7a5669`
  ```diff
  -and counts.files <= caps.max_files_per_batch
  +and counts.files < caps.max_files_per_batch
  ```
- `_within` · survived · operator: comparison · `ddb40dab6035f951`
  ```diff
  -and counts.bytes <= caps.max_bytes_per_batch
  +and counts.bytes < caps.max_bytes_per_batch
  ```
- `_limits` · survived · operator: and / or · `60a1a65c31f365f2`
  ```diff
  -if evidence.status != "verified" or evidence.constraints is None:
  +if evidence.status != "verified" and evidence.constraints is None:
  ```
- `_limits` · survived · operator: and / or · `e148531831574209`
  ```diff
  -return _check("limits", reasons or ["limits_unverified"]), evidence
  +return _check("limits", reasons and ["limits_unverified"]), evidence
  ```
- `_limits` · survived · string: XX added · `c85b6480cde52568`
  ```diff
  -return _check("limits", reasons or ["limits_unverified"]), evidence
  +return _check("limits", reasons or ["XXlimits_unverifiedXX"]), evidence
  ```
- `_limits` · survived · string: upper case · `353303f1ec2433dd`
  ```diff
  -return _check("limits", reasons or ["limits_unverified"]), evidence
  +return _check("limits", reasons or ["LIMITS_UNVERIFIED"]), evidence
  ```
- `_limits` · survived · operator: and / or · `99143ea9a27b04a4`
  ```diff
  -if plan is None or evidence.limits_sha256 != plan.basis.limits_sha256:
  +if plan is None and evidence.limits_sha256 != plan.basis.limits_sha256:
  ```
- `_limits` · survived · argument: replaced by None · `d7391490178ce648`
  ```diff
  -reasons.append("limits_not_the_plans")
  +reasons.append(None)
  ```
- `_limits` · survived · string: XX added · `b17b015f1b45935a`
  ```diff
  -reasons.append("limits_not_the_plans")
  +reasons.append("XXlimits_not_the_plansXX")
  ```
- `_limits` · survived · string: upper case · `8996c95def74f41e`
  ```diff
  -reasons.append("limits_not_the_plans")
  +reasons.append("LIMITS_NOT_THE_PLANS")
  ```
- `_limits` · survived · argument: replaced by None · `78656c3c6286fc2e`
  ```diff
  -reasons.append("batch_over_limits")
  +reasons.append(None)
  ```
- `_limits` · survived · string: XX added · `00a7af19c6bde3dc`
  ```diff
  -reasons.append("batch_over_limits")
  +reasons.append("XXbatch_over_limitsXX")
  ```
- `_limits` · survived · string: upper case · `e3ecce2695b7c5a5`
  ```diff
  -reasons.append("batch_over_limits")
  +reasons.append("BATCH_OVER_LIMITS")
  ```
- `_control` · survived · assignment: value to None · `3231ba9334b13a0d`
  ```diff
  -policy_id = plan.basis.policy.policy_id if plan else None
  +policy_id = None
  ```
- `_control` · survived · ternary: condition forced · `5143d151436dc5f7`
  ```diff
  -policy_id = plan.basis.policy.policy_id if plan else None
  +policy_id = plan.basis.policy.policy_id if (plan) and False else None
  ```
- `_control` · survived · ternary: condition forced · `233a900e8a1bcdc6`
  ```diff
  -policy_id = plan.basis.policy.policy_id if plan else None
  +policy_id = plan.basis.policy.policy_id if (plan) or True else None
  ```
- `_control` · survived · argument: replaced by None · `0f68270bfbbd5485`
  ```diff
  -request = ControlRequest(context.inputs.control, policy_id, context.now)
  +request = ControlRequest(context.inputs.control, None, context.now)
  ```
- `_control` · survived · augmented assignment to plain · `c885e21f238e43c1`
  ```diff
  -reasons += [f"active_stop:{s}" for s in evidence.active_stops]
  +reasons = [f"active_stop:{s}" for s in evidence.active_stops]
  ```
- `_control` · survived · string: XX added · `7dc98a451cb9e4e6`
  ```diff
  -return _check("stop_control", reasons or ["stopped"]), evidence
  +return _check("stop_control", reasons or ["XXstoppedXX"]), evidence
  ```
- `_control` · survived · string: upper case · `60e06d0873614c10`
  ```diff
  -return _check("stop_control", reasons or ["stopped"]), evidence
  +return _check("stop_control", reasons or ["STOPPED"]), evidence
  ```
- `_budget` · survived · argument: replaced by None · `c323eb1e1b24b8cf`
  ```diff
  -evidence = verify_ledger(LedgerRequest(context.inputs.ledger, limits, day, release))
  +evidence = verify_ledger(LedgerRequest(context.inputs.ledger, limits, None, release))
  ```
- `_budget` · survived · operator: and / or · `6de61bc396bd9ef2`
  ```diff
  -if evidence.status != "verified" or evidence.release_state != "reserved":
  +if evidence.status != "verified" and evidence.release_state != "reserved":
  ```
- `_budget` · survived · string: XX added · `01217bb1bbccb1c6`
  ```diff
  -reasons.append("budget_not_reserved_for_this_transaction")
  +reasons.append("XXbudget_not_reserved_for_this_transactionXX")
  ```
- `_budget` · survived · string: upper case · `de08361fde0446f2`
  ```diff
  -reasons.append("budget_not_reserved_for_this_transaction")
  +reasons.append("BUDGET_NOT_RESERVED_FOR_THIS_TRANSACTION")
  ```
- `_budget` · survived · string: XX added · `6358c4648293b9d1`
  ```diff
  -reasons.append("reservation_smaller_than_the_batch")
  +reasons.append("XXreservation_smaller_than_the_batchXX")
  ```
- `_budget` · survived · string: upper case · `79bd20561662c530`
  ```diff
  -reasons.append("reservation_smaller_than_the_batch")
  +reasons.append("RESERVATION_SMALLER_THAN_THE_BATCH")
  ```
- `_remote` · survived · argument: replaced by None · `9c8ecab50aa325d3`
  ```diff
  -return _check("remote_head", ["remote_unreadable"]), None
  +return _check(None, ["remote_unreadable"]), None
  ```
- `_remote` · survived · argument: replaced by None · `d88ae2683aca54a6`
  ```diff
  -return _check("remote_head", ["remote_unreadable"]), None
  +return _check("remote_head", None), None
  ```
- `_remote` · survived · argument: removed · `a9617edcca73dff2`
  ```diff
  -return _check("remote_head", ["remote_unreadable"]), None
  +return _check(["remote_unreadable"]), None
  ```
- `_remote` · survived · argument: removed · `6306e8b154346cc5`
  ```diff
  -return _check("remote_head", ["remote_unreadable"]), None
  +return _check("remote_head", ), None
  ```
- `_remote` · survived · string: XX added · `a7e4eb6148d657bc`
  ```diff
  -return _check("remote_head", ["remote_unreadable"]), None
  +return _check("XXremote_headXX", ["remote_unreadable"]), None
  ```
- `_remote` · survived · string: upper case · `004c04bc93f325b8`
  ```diff
  -return _check("remote_head", ["remote_unreadable"]), None
  +return _check("REMOTE_HEAD", ["remote_unreadable"]), None
  ```
- `_remote` · survived · string: XX added · `ca00ec5e7838232b`
  ```diff
  -return _check("remote_head", ["remote_unreadable"]), None
  +return _check("remote_head", ["XXremote_unreadableXX"]), None
  ```
- `_remote` · survived · string: upper case · `3ba136dfca5239e7`
  ```diff
  -return _check("remote_head", ["remote_unreadable"]), None
  +return _check("remote_head", ["REMOTE_UNREADABLE"]), None
  ```
- `_remote` · survived · string: XX added · `fd751da6aed738c1`
  ```diff
  -return _check("remote_head", [] if head == base else ["remote_head_not_the_base"]), head
  +return _check("remote_head", [] if head == base else ["XXremote_head_not_the_baseXX"]), head
  ```
- `_remote` · survived · string: upper case · `2bf732ace473d98f`
  ```diff
  -return _check("remote_head", [] if head == base else ["remote_head_not_the_base"]), head
  +return _check("remote_head", [] if head == base else ["REMOTE_HEAD_NOT_THE_BASE"]), head
  ```
- `_path_identity` · survived · ternary: condition forced · `d90b3aa068dda651`
  ```diff
  -reasons = [] if settings.path == path else ["configured_path_not_the_transactions"]
  +reasons = [] if (settings.path == path) or True else ["configured_path_not_the_transactions"]
  ```
- `_path_identity` · survived · string: XX added · `290a7bf6e16ea652`
  ```diff
  -reasons = [] if settings.path == path else ["configured_path_not_the_transactions"]
  +reasons = [] if settings.path == path else ["XXconfigured_path_not_the_transactionsXX"]
  ```
- `_path_identity` · survived · string: upper case · `59b21ca885e1f989`
  ```diff
  -reasons = [] if settings.path == path else ["configured_path_not_the_transactions"]
  +reasons = [] if settings.path == path else ["CONFIGURED_PATH_NOT_THE_TRANSACTIONS"]
  ```
- `_path_identity` · survived · string: XX added · `a4ad20aaf966487e`
  ```diff
  -reasons.append("app_identity_not_the_pinned_one")
  +reasons.append("XXapp_identity_not_the_pinned_oneXX")
  ```
- `_path_identity` · survived · string: upper case · `5de5c647e28bfa56`
  ```diff
  -reasons.append("app_identity_not_the_pinned_one")
  +reasons.append("APP_IDENTITY_NOT_THE_PINNED_ONE")
  ```
- `_path_identity` · survived · string: XX added · `bfa8bde6f5d1816f`
  ```diff
  -if path == "B" and context.facts.app is not None:
  +if path == "XXBXX" and context.facts.app is not None:
  ```
- `_path_identity` · survived · string: lower case · `e5019170671f5475`
  ```diff
  -if path == "B" and context.facts.app is not None:
  +if path == "b" and context.facts.app is not None:
  ```
- `_path_identity` · survived · argument: replaced by None · `d72737891aa685f1`
  ```diff
  -reasons.append("path_b_has_no_app")
  +reasons.append(None)
  ```
- `_path_identity` · survived · string: XX added · `57d7f732dcfb3950`
  ```diff
  -reasons.append("path_b_has_no_app")
  +reasons.append("XXpath_b_has_no_appXX")
  ```
- `_path_identity` · survived · string: upper case · `01dad93f5b687af4`
  ```diff
  -reasons.append("path_b_has_no_app")
  +reasons.append("PATH_B_HAS_NO_APP")
  ```
- `_bot_signature` · survived · operator: and / or · `c671d13405ee9651`
  ```diff
  -if facts.local_commit is None or facts.commit_reasons is None:
  +if facts.local_commit is None and facts.commit_reasons is None:
  ```
- `_bot_signature` · survived · string: XX added · `7882f7293a8a9510`
  ```diff
  -yield _check("bot_signature", ["local_commit_missing"])
  +yield _check("bot_signature", ["XXlocal_commit_missingXX"])
  ```
- `_bot_signature` · survived · string: upper case · `5a9e810dcb710225`
  ```diff
  -yield _check("bot_signature", ["local_commit_missing"])
  +yield _check("bot_signature", ["LOCAL_COMMIT_MISSING"])
  ```
- `_checks` · survived · argument: replaced by None · `e9f08f3c14ff303f`
  ```diff
  -control_check, control = _control(context, plan)
  +control_check, control = _control(context, None)
  ```
- `_checks` · survived · string: XX added · `a94fbe3725d80690`
  ```diff
  -latch = _check("stop_latch", ["stop_latch_set"] if context.facts.latched else [])
  +latch = _check("stop_latch", ["XXstop_latch_setXX"] if context.facts.latched else [])
  ```
- `_checks` · survived · string: upper case · `7f60228e7abb6474`
  ```diff
  -latch = _check("stop_latch", ["stop_latch_set"] if context.facts.latched else [])
  +latch = _check("stop_latch", ["STOP_LATCH_SET"] if context.facts.latched else [])
  ```
- `_checks` · survived · argument: replaced by None · `44500fe336646a5f`
  ```diff
  -plan, limits, control, ledger, head, fingerprint
  +None, limits, control, ledger, head, fingerprint
  ```
- `_checks` · survived · argument: replaced by None · `f5f7bdd2c884c985`
  ```diff
  -plan, limits, control, ledger, head, fingerprint
  +plan, limits, control, ledger, head, None
  ```
- `_verified` · survived · ternary: condition forced · `a588265f63313085`
  ```diff
  -"policy": found.plan.basis.policy if found.plan else None,
  +"policy": found.plan.basis.policy if (found.plan) and False else None,
  ```
- `_verified` · survived · ternary: condition forced · `33dfff9c22037b54`
  ```diff
  -"policy": found.plan.basis.policy if found.plan else None,
  +"policy": found.plan.basis.policy if (found.plan) or True else None,
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_execute.py</code>: 52</summary>

- `_step` · survived · argument: replaced by None · `00f3209edd5a3030`
  ```diff
  -return _Step(sequence, previous, attempt_id_of(tid, sequence))
  +return _Step(sequence, previous, attempt_id_of(None, sequence))
  ```
- `_step` · survived · argument: replaced by None · `62cf5db8f59ef932`
  ```diff
  -return _Step(sequence, previous, attempt_id_of(tid, sequence))
  +return _Step(sequence, previous, attempt_id_of(tid, None))
  ```
- `_settled` · survived · operator: and / or · `cf7d8cf04ac69408`
  ```diff
  -if r.transaction_id == tid and r.published
  +if r.transaction_id == tid or r.published
  ```
- `_settled` · survived · argument: replaced by None · `f85b2074f1c81f80`
  ```diff
  -"unchanged", receipted[0].commit, None, None, found[-1] if found else None
  +"unchanged", None, None, None, found[-1] if found else None
  ```
- `_settled` · survived · argument: replaced by None · `6b3da145e06d27e9`
  ```diff
  -"unchanged", receipted[0].commit, None, None, found[-1] if found else None
  +"unchanged", receipted[0].commit, None, None, None
  ```
- `_settled` · survived · ternary: condition forced · `ed32bd2255ac2e86`
  ```diff
  -"unchanged", receipted[0].commit, None, None, found[-1] if found else None
  +"unchanged", receipted[0].commit, None, None, found[-1] if (found) and False else None
  ```
- `_settled` · survived · argument: replaced by None · `5885d7b5a4e7d261`
  ```diff
  -return ExecutionOutcome("unchanged", last.commit, None, None, last)
  +return ExecutionOutcome("unchanged", last.commit, None, None, None)
  ```
- `_settled` · survived · argument: replaced by None · `06b9914e44c27315`
  ```diff
  -return ExecutionOutcome("recovery_required", last.commit, None, None, last)
  +return ExecutionOutcome("recovery_required", None, None, None, last)
  ```
- `_settled` · survived · argument: replaced by None · `e5284bc38ca5712f`
  ```diff
  -return ExecutionOutcome("recovery_required", last.commit, None, None, last)
  +return ExecutionOutcome("recovery_required", last.commit, None, None, None)
  ```
- `_files` · survived · argument: replaced by None · `4c7e135ddb38994f`
  ```diff
  -raise _not_eligible("proposal_bytes")
  +raise _not_eligible(None)
  ```
- `_files` · survived · string: XX added · `564677cc52d54a51`
  ```diff
  -raise _not_eligible("proposal_bytes")
  +raise _not_eligible("XXproposal_bytesXX")
  ```
- `_files` · survived · string: upper case · `429f1ee74957ed7d`
  ```diff
  -raise _not_eligible("proposal_bytes")
  +raise _not_eligible("PROPOSAL_BYTES")
  ```
- `_remote` · survived · argument: replaced by None · `a8e65d340215d13e`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible(None)
  ```
- `_remote` · survived · string: XX added · `3df26eeed03c76d7`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("XXadapterXX")
  ```
- `_remote` · survived · string: upper case · `41395b9a06b3d800`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("ADAPTER")
  ```
- `_remote` · survived · operator: and / or · `e1a27200c359861e`
  ```diff
  -if environment.remote is None or environment.signer is None:
  +if environment.remote is None and environment.signer is None:
  ```
- `_remote` · survived · argument: replaced by None · `a8e65d340215d13e-2`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible(None)
  ```
- `_remote` · survived · string: XX added · `3df26eeed03c76d7-2`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("XXadapterXX")
  ```
- `_remote` · survived · string: upper case · `41395b9a06b3d800-2`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("ADAPTER")
  ```
- `_facts` · survived · ternary: condition forced · `85b7ed0dd33ab1e1`
  ```diff
  -return AdapterFacts(app=transport.identity if transport else None, latched=latched)
  +return AdapterFacts(app=transport.identity if (transport) or True else None, latched=latched)
  ```
- `_facts` · survived · argument: replaced by None · `afd01763b2beb99b`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible(None)
  ```
- `_facts` · survived · string: XX added · `3d10b1026409976b`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("XXadapterXX")
  ```
- `_facts` · survived · string: upper case · `3275e4a3ff5222db`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("ADAPTER")
  ```
- `_facts` · survived · argument: replaced by None · `4398bdcb4c5e752a`
  ```diff
  -local_commit=None, commit_reasons=(f"local_commit:{error.detail}",), latched=latched
  +local_commit=None, commit_reasons=None, latched=latched
  ```
- `_facts` · survived · argument: replaced by None · `65910340ad31c0ab`
  ```diff
  -local_commit=None, commit_reasons=(f"local_commit:{error.detail}",), latched=latched
  +local_commit=None, commit_reasons=(f"local_commit:{error.detail}",), latched=None
  ```
- `_facts` · survived · argument: removed · `0c54ce4d6eafdb88`
  ```diff
  -local_commit=None, commit_reasons=(f"local_commit:{error.detail}",), latched=latched
  +commit_reasons=(f"local_commit:{error.detail}",), latched=latched
  ```
- `_facts` · survived · argument: removed · `9666a24421c9cd67`
  ```diff
  -local_commit=None, commit_reasons=(f"local_commit:{error.detail}",), latched=latched
  +local_commit=None, latched=latched
  ```
- `_facts` · survived · argument: removed · `08616fb6d05d8a9b`
  ```diff
  -local_commit=None, commit_reasons=(f"local_commit:{error.detail}",), latched=latched
  -)
  +local_commit=None, commit_reasons=(f"local_commit:{error.detail}",), )
  ```
- `_facts` · survived · argument: replaced by None · `d3eb10053e3ea041`
  ```diff
  -return AdapterFacts(local_commit=check.commit, commit_reasons=check.reasons, latched=latched)
  +return AdapterFacts(local_commit=check.commit, commit_reasons=check.reasons, latched=None)
  ```
- `_facts` · survived · argument: removed · `d0b2a32690633ffc`
  ```diff
  -return AdapterFacts(local_commit=check.commit, commit_reasons=check.reasons, latched=latched)
  +return AdapterFacts(local_commit=check.commit, commit_reasons=check.reasons, )
  ```
- `_latch_reasons` · survived · is / in negated · `26ec928dc5bb124f`
  ```diff
  -if "revoked" in reason or reason.startswith("active_stop:")
  +if "revoked" not in reason or reason.startswith("active_stop:")
  ```
- `_context` · survived · argument: replaced by None · `f42a7d0b31070def`
  ```diff
  -return AuthorizationContext(*base, lambda: None, _utc_now(), step.attempt_id, "production")
  +return AuthorizationContext(*base, None, _utc_now(), step.attempt_id, "production")
  ```
- `_denied` · survived · assignment: value to None · `c53f656123339bc5`
  ```diff
  -digest = evidence_digest(evidence)
  +digest = None
  ```
- `_denied` · survived · argument: replaced by None · `b259a6e25fef58ac`
  ```diff
  -return ExecutionOutcome("denied", None, evidence, digest, record)
  +return ExecutionOutcome("denied", None, evidence, None, record)
  ```
- `_denied` · survived · argument: replaced by None · `11bf52057e2e541e`
  ```diff
  -return ExecutionOutcome("denied", None, evidence, digest, record)
  +return ExecutionOutcome("denied", None, evidence, digest, None)
  ```
- `_require_authorized` · survived · argument: replaced by None · `748616b6a9fd49bc`
  ```diff
  -raise _not_eligible("production_execution_disabled")
  +raise _not_eligible(None)
  ```
- `_require_authorized` · survived · string: XX added · `4f4cc1fe5b1d0b53`
  ```diff
  -raise _not_eligible("production_execution_disabled")
  +raise _not_eligible("XXproduction_execution_disabledXX")
  ```
- `_require_authorized` · survived · string: upper case · `ada79a27d913a027`
  ```diff
  -raise _not_eligible("production_execution_disabled")
  +raise _not_eligible("PRODUCTION_EXECUTION_DISABLED")
  ```
- `_require_authorized` · survived · operator: comparison · `59b93c162a49ee85`
  ```diff
  -if not 0 <= age <= FRESH_SECONDS:
  +if not 0 <= age < FRESH_SECONDS:
  ```
- `_publish` · survived · argument: replaced by None · `172733c30db105e8`
  ```diff
  -raise _not_eligible("production_execution_disabled")
  +raise _not_eligible(None)
  ```
- `_publish` · survived · string: XX added · `70a3aafc4854e9c6`
  ```diff
  -raise _not_eligible("production_execution_disabled")
  +raise _not_eligible("XXproduction_execution_disabledXX")
  ```
- `_publish` · survived · string: upper case · `52d1795303646043`
  ```diff
  -raise _not_eligible("production_execution_disabled")
  +raise _not_eligible("PRODUCTION_EXECUTION_DISABLED")
  ```
- `_after` · survived · string: XX added · `0dc96e858463202f`
  ```diff
  -publication_attempts.latch(request.workspace, ("outcome_unknown",), at, tid)
  +publication_attempts.latch(request.workspace, ("XXoutcome_unknownXX",), at, tid)
  ```
- `_after` · survived · string: upper case · `be886aaf750c4638`
  ```diff
  -publication_attempts.latch(request.workspace, ("outcome_unknown",), at, tid)
  +publication_attempts.latch(request.workspace, ("OUTCOME_UNKNOWN",), at, tid)
  ```
- `_after` · survived · argument: replaced by None · `9cb182989ec58fb5`
  ```diff
  -return ExecutionOutcome(sent.status, sent.commit, evidence, digest, record)
  +return ExecutionOutcome(sent.status, sent.commit, evidence, digest, None)
  ```
- `_send` · survived · argument: replaced by None · `43fa4d5840bd2e2c`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible(None)
  ```
- `_send` · survived · string: XX added · `cedba85d9078e8ff`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("XXadapterXX")
  ```
- `_send` · survived · string: upper case · `9c37ad754e39c054`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("ADAPTER")
  ```
- `_send` · survived · operator: and / or · `70c310a97eac8c34`
  ```diff
  -if environment.remote is None or evidence.local_commit is None:
  +if environment.remote is None and evidence.local_commit is None:
  ```
- `_send` · survived · argument: replaced by None · `43fa4d5840bd2e2c-2`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible(None)
  ```
- `_send` · survived · string: XX added · `cedba85d9078e8ff-2`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("XXadapterXX")
  ```
- `_send` · survived · string: upper case · `9c37ad754e39c054-2`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("ADAPTER")
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_execution_models.py</code>: 4</summary>

- `decide` · survived · string: XX added · `b9b28edd3e757f58`
  ```diff
  -return "denied", tuple(blocking or ["production_execution_disabled"]), ()
  +return "denied", tuple(blocking or ["XXproduction_execution_disabledXX"]), ()
  ```
- `decide` · survived · string: upper case · `6b74f9aca3d42e0a`
  ```diff
  -return "denied", tuple(blocking or ["production_execution_disabled"]), ()
  +return "denied", tuple(blocking or ["PRODUCTION_EXECUTION_DISABLED"]), ()
  ```
- `decide` · survived · string: XX added · `e8fd2f22585d0362`
  ```diff
  -return "denied", tuple(blocking or ["no_checks"]), ()
  +return "denied", tuple(blocking or ["XXno_checksXX"]), ()
  ```
- `decide` · survived · string: upper case · `2a7ce03cdd006426`
  ```diff
  -return "denied", tuple(blocking or ["no_checks"]), ()
  +return "denied", tuple(blocking or ["NO_CHECKS"]), ()
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_integrity.py</code>: 54</summary>

- `sealed_plan` · survived · operator: and / or · `de74496524f8017d`
  ```diff
  -sealed = evidence.status == "verified" and authentic(evidence, "batch_plan")
  +sealed = evidence.status == "verified" or authentic(evidence, "batch_plan")
  ```
- `sealed_plan` · survived · operator: and / or · `812b8f55f3420893`
  ```diff
  -if plan is None or not sealed or evidence.plan_sha256 != digest_of(plan):
  +if plan is None or not sealed and evidence.plan_sha256 != digest_of(plan):
  ```
- `sealed_plan` · survived · operator: and / or · `ce965244d7286f68`
  ```diff
  -if plan is None or not sealed or evidence.plan_sha256 != digest_of(plan):
  +if plan is None and not sealed or evidence.plan_sha256 != digest_of(plan):
  ```
- `_files` · survived · argument: replaced by None · `41e7e783a9f3bec2`
  ```diff
  -if any(sha256(found.get(p, b"")).hexdigest() != digest for p, digest in wanted.items()):
  +if any(sha256(found.get(p, None)).hexdigest() != digest for p, digest in wanted.items()):
  ```
- `_files` · survived · argument: removed · `a45d8ee22023b295`
  ```diff
  -if any(sha256(found.get(p, b"")).hexdigest() != digest for p, digest in wanted.items()):
  +if any(sha256(found.get(p, )).hexdigest() != digest for p, digest in wanted.items()):
  ```
- `_files` · survived · string: XX added · `52aefd0ebe0ac617`
  ```diff
  -if any(sha256(found.get(p, b"")).hexdigest() != digest for p, digest in wanted.items()):
  +if any(sha256(found.get(p, b"XXXX")).hexdigest() != digest for p, digest in wanted.items()):
  ```
- `_manifest_file` · survived · operator: and / or · `e4df1b4887a449da`
  ```diff
  -if releases != [content.release_manifest_path] or raw is None:
  +if releases != [content.release_manifest_path] and raw is None:
  ```
- `_manifest_file` · survived · string: XX added · `281b431bf8e8a545`
  ```diff
  -return "manifest_not_the_one_release_file"
  +return "XXmanifest_not_the_one_release_fileXX"
  ```
- `_manifest_file` · survived · string: upper case · `1658329d5e83ef5f`
  ```diff
  -return "manifest_not_the_one_release_file"
  +return "MANIFEST_NOT_THE_ONE_RELEASE_FILE"
  ```
- `_manifest_file` · survived · string: XX added · `475d6305dfdff073`
  ```diff
  -return "manifest_unreadable"
  +return "XXmanifest_unreadableXX"
  ```
- `_manifest_file` · survived · string: upper case · `943ac8eb7d672b17`
  ```diff
  -return "manifest_unreadable"
  +return "MANIFEST_UNREADABLE"
  ```
- `_manifest` · survived · ternary: condition forced · `dea1d26ba7d78320`
  ```diff
  -policy: PolicyReference | None = plan.basis.policy if plan is not None else None
  +policy: PolicyReference | None = plan.basis.policy if (plan is not None) or True else None
  ```
- `_manifest` · survived · argument: replaced by None · `41dd85a9007d5e39`
  ```diff
  -reasons.append("manifest_not_this_transaction")
  +reasons.append(None)
  ```
- `_manifest` · survived · string: XX added · `020d27a1b1f66e95`
  ```diff
  -reasons.append("manifest_not_this_transaction")
  +reasons.append("XXmanifest_not_this_transactionXX")
  ```
- `_manifest` · survived · string: upper case · `14b43cfba9075c87`
  ```diff
  -reasons.append("manifest_not_this_transaction")
  +reasons.append("MANIFEST_NOT_THIS_TRANSACTION")
  ```
- `_manifest` · survived · operator: and / or · `b84b575d02e3ae50`
  ```diff
  -if policy is None or found.policy != policy:
  +if policy is None and found.policy != policy:
  ```
- `_manifest` · survived · argument: replaced by None · `e78c91a2ec20e94c`
  ```diff
  -reasons.append("manifest_policy_not_the_plans")
  +reasons.append(None)
  ```
- `_manifest` · survived · string: XX added · `f11e8b320cd29009`
  ```diff
  -reasons.append("manifest_policy_not_the_plans")
  +reasons.append("XXmanifest_policy_not_the_plansXX")
  ```
- `_manifest` · survived · string: upper case · `82486fd7495b08d7`
  ```diff
  -reasons.append("manifest_policy_not_the_plans")
  +reasons.append("MANIFEST_POLICY_NOT_THE_PLANS")
  ```
- `integrity_checks` · survived · ternary: condition forced · `a2e77681b6057286`
  ```diff
  -message = [] if ours(commit, inputs) else ["message_not_the_transactions"]
  +message = [] if (ours(commit, inputs)) or True else ["message_not_the_transactions"]
  ```
- `integrity_checks` · survived · string: XX added · `bf9e03e5ee6f079d`
  ```diff
  -message = [] if ours(commit, inputs) else ["message_not_the_transactions"]
  +message = [] if ours(commit, inputs) else ["XXmessage_not_the_transactionsXX"]
  ```
- `integrity_checks` · survived · string: upper case · `8bf003f4f41d6ab0`
  ```diff
  -message = [] if ours(commit, inputs) else ["message_not_the_transactions"]
  +message = [] if ours(commit, inputs) else ["MESSAGE_NOT_THE_TRANSACTIONS"]
  ```
- `integrity_checks` · survived · string: XX added · `2fae3e9264a3a09c`
  ```diff
  -reasons=("not_signed_ar6_pending",),
  +reasons=("XXnot_signed_ar6_pendingXX",),
  ```
- `integrity_checks` · survived · string: upper case · `deb483e41d11c6c0`
  ```diff
  -reasons=("not_signed_ar6_pending",),
  +reasons=("NOT_SIGNED_AR6_PENDING",),
  ```
- `_known_by` · survived · operator: comparison · `b32c32a3aca38782`
  ```diff
  -if when is None or when <= at:
  +if when is None or when < at:
  ```
- `_policy_held` · survived · argument: removed · `6163d6c5d598e4c4`
  ```diff
  -verdict = verify_authorization(replace(offered, checked_at=at, revocations=revocations), root)
  +verdict = verify_authorization(replace(offered, revocations=revocations), root)
  ```
- `_policy_held` · survived · operator: and / or · `03f57598495a8856`
  ```diff
  -same = plan is not None and plan.basis.policy == ours_policy
  +same = plan is not None or plan.basis.policy == ours_policy
  ```
- `_not_stopped` · survived · assignment: value to None · `850951d819f94640`
  ```diff
  -plan = sealed_plan(inputs)
  +plan = None
  ```
- `_not_stopped` · survived · assignment: value to None · `59022abb22ddcbcd`
  ```diff
  -policy_id = plan.basis.policy.policy_id if plan is not None else None
  +policy_id = None
  ```
- `_not_stopped` · survived · ternary: condition forced · `bfa3279ef9bba05f`
  ```diff
  -policy_id = plan.basis.policy.policy_id if plan is not None else None
  +policy_id = plan.basis.policy.policy_id if (plan is not None) and False else None
  ```
- `_not_stopped` · survived · ternary: condition forced · `78ee08485fadbaa1`
  ```diff
  -policy_id = plan.basis.policy.policy_id if plan is not None else None
  +policy_id = plan.basis.policy.policy_id if (plan is not None) or True else None
  ```
- `_not_stopped` · survived · is / in negated · `de76f415963f3f84`
  ```diff
  -policy_id = plan.basis.policy.policy_id if plan is not None else None
  +policy_id = plan.basis.policy.policy_id if plan is None else None
  ```
- `_not_stopped` · survived · argument: replaced by None · `0d2a0b8499a5e65a`
  ```diff
  -return verify_control(ControlRequest(history, policy_id, at), root).state == "not_stopped"
  +return verify_control(ControlRequest(history, None, at), root).state == "not_stopped"
  ```
- `_attempt` · survived · operator: and / or · `457d2e11d260d1e9`
  ```diff
  -if record.state == "prepared_to_send" and same:
  +if record.state == "prepared_to_send" or same:
  ```
- `authorization` · survived · argument: replaced by None · `03856579968db5af`
  ```diff
  -return Verdict("authorization_unknown", ("untimed_key_revocation",), attempt)
  +return Verdict("authorization_unknown", ("untimed_key_revocation",), None)
  ```
- `authorization` · survived · argument: replaced by None · `365b89d07b85a04e`
  ```diff
  -return Verdict("unauthorized", ("policy_not_valid_at_attempt",), attempt)
  +return Verdict("unauthorized", ("policy_not_valid_at_attempt",), None)
  ```
- `authorization` · survived · argument: replaced by None · `0cfed3825356461c`
  ```diff
  -return Verdict("authorization_unknown", missing, attempt)
  +return Verdict("authorization_unknown", missing, None)
  ```
- `authorization` · survived · operator: arithmetic · `adf3373449618e1e`
  ```diff
  -samples = [held_at(inputs, root, event_at + k * _UNCERTAINTY) for k in (-1, 0, 1)]
  +samples = [held_at(inputs, root, event_at - k * _UNCERTAINTY) for k in (-1, 0, 1)]
  ```
- `authorization` · survived · operator: arithmetic · `c00ae2c0b0a519e8`
  ```diff
  -samples = [held_at(inputs, root, event_at + k * _UNCERTAINTY) for k in (-1, 0, 1)]
  +samples = [held_at(inputs, root, event_at + k * _UNCERTAINTY) for k in (+1, 0, 1)]
  ```
- `authorization` · survived · number · `9699cd405d0bf81d`
  ```diff
  -samples = [held_at(inputs, root, event_at + k * _UNCERTAINTY) for k in (-1, 0, 1)]
  +samples = [held_at(inputs, root, event_at + k * _UNCERTAINTY) for k in (-2, 0, 1)]
  ```
- `authorization` · survived · number · `7099089baeb8f208`
  ```diff
  -samples = [held_at(inputs, root, event_at + k * _UNCERTAINTY) for k in (-1, 0, 1)]
  +samples = [held_at(inputs, root, event_at + k * _UNCERTAINTY) for k in (-1, 1, 1)]
  ```
- `authorization` · survived · number · `76ea490d25b901c8`
  ```diff
  -samples = [held_at(inputs, root, event_at + k * _UNCERTAINTY) for k in (-1, 0, 1)]
  +samples = [held_at(inputs, root, event_at + k * _UNCERTAINTY) for k in (-1, 0, 2)]
  ```
- `authorization` · survived · argument: replaced by None · `92a46d1850005c29`
  ```diff
  -return Verdict("authorized", (), attempt)
  +return Verdict("authorized", (), None)
  ```
- `authorization` · survived · argument: replaced by None · `62d149da4e24beee`
  ```diff
  -return Verdict("unauthorized", ("policy_not_valid_at_publication",), attempt)
  +return Verdict(None, ("policy_not_valid_at_publication",), attempt)
  ```
- `authorization` · survived · argument: replaced by None · `d7c19513dd331f8c`
  ```diff
  -return Verdict("unauthorized", ("policy_not_valid_at_publication",), attempt)
  +return Verdict("unauthorized", None, attempt)
  ```
- `authorization` · survived · argument: replaced by None · `b50c04019fdb8633`
  ```diff
  -return Verdict("unauthorized", ("policy_not_valid_at_publication",), attempt)
  +return Verdict("unauthorized", ("policy_not_valid_at_publication",), None)
  ```
- `authorization` · survived · argument: removed · `4153c32f5ce7df05`
  ```diff
  -return Verdict("unauthorized", ("policy_not_valid_at_publication",), attempt)
  +return Verdict(("policy_not_valid_at_publication",), attempt)
  ```
- `authorization` · survived · argument: removed · `61d32b3e049a1959`
  ```diff
  -return Verdict("unauthorized", ("policy_not_valid_at_publication",), attempt)
  +return Verdict("unauthorized", attempt)
  ```
- `authorization` · survived · argument: removed · `32737a78ee38898e`
  ```diff
  -return Verdict("unauthorized", ("policy_not_valid_at_publication",), attempt)
  +return Verdict("unauthorized", ("policy_not_valid_at_publication",), )
  ```
- `authorization` · survived · string: XX added · `c240a42f82f47660`
  ```diff
  -return Verdict("unauthorized", ("policy_not_valid_at_publication",), attempt)
  +return Verdict("XXunauthorizedXX", ("policy_not_valid_at_publication",), attempt)
  ```
- `authorization` · survived · string: upper case · `5895fab0e103c200`
  ```diff
  -return Verdict("unauthorized", ("policy_not_valid_at_publication",), attempt)
  +return Verdict("UNAUTHORIZED", ("policy_not_valid_at_publication",), attempt)
  ```
- `authorization` · survived · string: XX added · `ea0e2db3d1524d1a`
  ```diff
  -return Verdict("unauthorized", ("policy_not_valid_at_publication",), attempt)
  +return Verdict("unauthorized", ("XXpolicy_not_valid_at_publicationXX",), attempt)
  ```
- `authorization` · survived · string: upper case · `2a26905df1625c2b`
  ```diff
  -return Verdict("unauthorized", ("policy_not_valid_at_publication",), attempt)
  +return Verdict("unauthorized", ("POLICY_NOT_VALID_AT_PUBLICATION",), attempt)
  ```
- `authorization` · survived · argument: replaced by None · `23e965521a298896`
  ```diff
  -return Verdict("authorization_unknown", ("within_clock_uncertainty",), attempt)
  +return Verdict("authorization_unknown", ("within_clock_uncertainty",), None)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_path_a.py</code>: 38</summary>

- `payload_of` · survived · string: upper case · `f63098a233eead2f`
  ```diff
  -{"path": item.path, "contents": base64.b64encode(files[item.path]).decode("ascii")}
  +{"path": item.path, "contents": base64.b64encode(files[item.path]).decode("ASCII")}
  ```
- `payload_of` · survived · string: XX added · `9177cf148c99cffc`
  ```diff
  -"fileChanges": {"additions": additions, "deletions": []},
  +"fileChanges": {"additions": additions, "XXdeletionsXX": []},
  ```
- `payload_of` · survived · string: upper case · `b716ee1d75b4696f`
  ```diff
  -"fileChanges": {"additions": additions, "deletions": []},
  +"fileChanges": {"additions": additions, "DELETIONS": []},
  ```
- `_stale` · survived · operator: and / or · `7ebd3a949c127391`
  ```diff
  -if not isinstance(errors, list) or not errors:
  +if not isinstance(errors, list) and not errors:
  ```
- `_stale` · survived · assignment: value to None · `8bb0433724350ec1`
  ```diff
  -data = answer.get("data")
  +data = None
  ```
- `_stale` · survived · argument: replaced by None · `f3950d02c525bc88`
  ```diff
  -data = answer.get("data")
  +data = answer.get(None)
  ```
- `_stale` · survived · string: XX added · `0c4771930c86a584`
  ```diff
  -data = answer.get("data")
  +data = answer.get("XXdataXX")
  ```
- `_stale` · survived · string: upper case · `e8eab8329f1c9a44`
  ```diff
  -data = answer.get("data")
  +data = answer.get("DATA")
  ```
- `_stale` · survived · assignment: value to None · `28773e146e4d9f4b`
  ```diff
  -created = data.get("createCommitOnBranch") if isinstance(data, Mapping) else None
  +created = None
  ```
- `_stale` · survived · ternary: condition forced · `ff2c9c6c41bb1185`
  ```diff
  -created = data.get("createCommitOnBranch") if isinstance(data, Mapping) else None
  +created = data.get("createCommitOnBranch") if (isinstance(data, Mapping)) and False else None
  ```
- `_stale` · survived · argument: replaced by None · `175e7fa8d0bb1b67`
  ```diff
  -created = data.get("createCommitOnBranch") if isinstance(data, Mapping) else None
  +created = data.get(None) if isinstance(data, Mapping) else None
  ```
- `_stale` · survived · string: XX added · `d38a280955b08daf`
  ```diff
  -created = data.get("createCommitOnBranch") if isinstance(data, Mapping) else None
  +created = data.get("XXcreateCommitOnBranchXX") if isinstance(data, Mapping) else None
  ```
- `_stale` · survived · string: lower case · `d70399c2bf397ff1`
  ```diff
  -created = data.get("createCommitOnBranch") if isinstance(data, Mapping) else None
  +created = data.get("createcommitonbranch") if isinstance(data, Mapping) else None
  ```
- `_stale` · survived · string: upper case · `4b040dd10be118a9`
  ```diff
  -created = data.get("createCommitOnBranch") if isinstance(data, Mapping) else None
  +created = data.get("CREATECOMMITONBRANCH") if isinstance(data, Mapping) else None
  ```
- `_stale` · survived · argument: replaced by None · `c1083f9ac1334eaf`
  ```diff
  -messages = [e.get("message", "") for e in errors if isinstance(e, Mapping)]
  +messages = [e.get("message", None) for e in errors if isinstance(e, Mapping)]
  ```
- `_stale` · survived · argument: removed · `f284b35b522bca6b`
  ```diff
  -messages = [e.get("message", "") for e in errors if isinstance(e, Mapping)]
  +messages = [e.get("message", ) for e in errors if isinstance(e, Mapping)]
  ```
- `_stale` · survived · string: XX added · `7c22f5e0e49a100e`
  ```diff
  -messages = [e.get("message", "") for e in errors if isinstance(e, Mapping)]
  +messages = [e.get("message", "XXXX") for e in errors if isinstance(e, Mapping)]
  ```
- `_stale` · survived · operator: and / or · `8b21476c6baee06f`
  ```diff
  -kind == "STALE_DATA" and _STALE in str(text)
  +kind == "STALE_DATA" or _STALE in str(text)
  ```
- `_stale` · survived · argument: replaced by None · `f16f5b70cb56ddea`
  ```diff
  -for kind, text in zip(types, messages, strict=True)
  +for kind, text in zip(types, messages, strict=None)
  ```
- `_stale` · survived · argument: removed · `9e9cc4818558ce81`
  ```diff
  -for kind, text in zip(types, messages, strict=True)
  +for kind, text in zip(types, messages, )
  ```
- `_stale` · survived · True / False swapped · `1b74ed6083eb5949`
  ```diff
  -for kind, text in zip(types, messages, strict=True)
  +for kind, text in zip(types, messages, strict=False)
  ```
- `_commit` · survived · argument: replaced by None · `de53a91068db7f5b`
  ```diff
  -if answer.get("errors"):
  +if answer.get(None):
  ```
- `_commit` · survived · string: XX added · `49334a427a368dbc`
  ```diff
  -if answer.get("errors"):
  +if answer.get("XXerrorsXX"):
  ```
- `_commit` · survived · string: upper case · `a7a08c4e5ccb51fe`
  ```diff
  -if answer.get("errors"):
  +if answer.get("ERRORS"):
  ```
- `_commit` · survived · ternary: condition forced · `2187a74674f25df9`
  ```diff
  -created = data.get("createCommitOnBranch") if isinstance(data, Mapping) else None
  +created = data.get("createCommitOnBranch") if (isinstance(data, Mapping)) or True else None
  ```
- `judged` · survived · string: XX added · `9fdfbd37eb549167`
  ```diff
  -return Sent("outcome_unknown", None, ("response_malformed",))
  +return Sent("outcome_unknown", None, ("XXresponse_malformedXX",))
  ```
- `judged` · survived · string: upper case · `ca71f6e501db3b95`
  ```diff
  -return Sent("outcome_unknown", None, ("response_malformed",))
  +return Sent("outcome_unknown", None, ("RESPONSE_MALFORMED",))
  ```
- `judged` · survived · string: XX added · `d7153d7fe8c59213`
  ```diff
  -return Sent("refused", None, ("expected_head_mismatch",))
  +return Sent("refused", None, ("XXexpected_head_mismatchXX",))
  ```
- `judged` · survived · string: upper case · `862d125b0cd62173`
  ```diff
  -return Sent("refused", None, ("expected_head_mismatch",))
  +return Sent("refused", None, ("EXPECTED_HEAD_MISMATCH",))
  ```
- `judged` · survived · string: XX added · `9fdfbd37eb549167-2`
  ```diff
  -return Sent("outcome_unknown", None, ("response_malformed",))
  +return Sent("outcome_unknown", None, ("XXresponse_malformedXX",))
  ```
- `judged` · survived · string: upper case · `ca71f6e501db3b95-2`
  ```diff
  -return Sent("outcome_unknown", None, ("response_malformed",))
  +return Sent("outcome_unknown", None, ("RESPONSE_MALFORMED",))
  ```
- `judged` · survived · argument: replaced by None · `7d7ab4e03b9843e1`
  ```diff
  -return Sent("outcome_unknown", oid, ("main_not_at_returned_commit",))
  +return Sent("outcome_unknown", None, ("main_not_at_returned_commit",))
  ```
- `judged` · survived · argument: replaced by None · `736c6ca6570ce451`
  ```diff
  -return Sent("outcome_unknown", oid, ("main_not_at_returned_commit",))
  +return Sent("outcome_unknown", oid, None)
  ```
- `judged` · survived · string: XX added · `0feb859d5a79f967`
  ```diff
  -return Sent("outcome_unknown", oid, ("main_not_at_returned_commit",))
  +return Sent("outcome_unknown", oid, ("XXmain_not_at_returned_commitXX",))
  ```
- `judged` · survived · string: upper case · `acb051c69c055f25`
  ```diff
  -return Sent("outcome_unknown", oid, ("main_not_at_returned_commit",))
  +return Sent("outcome_unknown", oid, ("MAIN_NOT_AT_RETURNED_COMMIT",))
  ```
- `send` · survived · string: XX added · `4ec5498dd41af3ff`
  ```diff
  -return Sent("outcome_unknown", None, ("response_lost",))
  +return Sent("outcome_unknown", None, ("XXresponse_lostXX",))
  ```
- `send` · survived · string: upper case · `1fa70289cd44ccd3`
  ```diff
  -return Sent("outcome_unknown", None, ("response_lost",))
  +return Sent("outcome_unknown", None, ("RESPONSE_LOST",))
  ```
- `send` · survived · assignment: None to empty string · `fef4f3b19aa5dc05`
  ```diff
  -read = None
  +read = ""
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_path_b.py</code>: 99</summary>

- `_refused` · survived · argument: replaced by None · `23ab21a2b3a9b68d`
  ```diff
  -return ForarbeiderRefusedError(RefusalReason.CHECKOUT_UNVERIFIABLE, detail)
  +return ForarbeiderRefusedError(RefusalReason.CHECKOUT_UNVERIFIABLE, None)
  ```
- `_git` · survived · argument: replaced by None · `2da64ec3d802e317`
  ```diff
  -raise _refused(argv[0]) from error
  +raise _refused(None) from error
  ```
- `_git` · survived · number · `d48057edcc14b3cf`
  ```diff
  -raise _refused(argv[0]) from error
  +raise _refused(argv[1]) from error
  ```
- `_git` · survived · argument: replaced by None · `bc88465fc6714fe6`
  ```diff
  -raise _refused(argv[0])
  +raise _refused(None)
  ```
- `_git` · survived · number · `85625963afd9c545`
  ```diff
  -raise _refused(argv[0])
  +raise _refused(argv[1])
  ```
- `_answer` · survived · argument: replaced by None · `9442f76505139560`
  ```diff
  -raise _refused(argv[0]) from error
  +raise _refused(None) from error
  ```
- `_answer` · survived · number · `2f49de6dc6739ca8`
  ```diff
  -raise _refused(argv[0]) from error
  +raise _refused(argv[1]) from error
  ```
- `blob_id` · survived · argument: replaced by None · `fbd8814c7fce78b1`
  ```diff
  -return sha1(b"blob %d\0" % len(payload) + payload, usedforsecurity=False).hexdigest()
  +return sha1(b"blob %d\0" % len(payload) + payload, usedforsecurity=None).hexdigest()
  ```
- `blob_id` · survived · argument: removed · `d03320925ecff000`
  ```diff
  -return sha1(b"blob %d\0" % len(payload) + payload, usedforsecurity=False).hexdigest()
  +return sha1(b"blob %d\0" % len(payload) + payload, ).hexdigest()
  ```
- `blob_id` · survived · True / False swapped · `3e8934754e5ebdf6`
  ```diff
  -return sha1(b"blob %d\0" % len(payload) + payload, usedforsecurity=False).hexdigest()
  +return sha1(b"blob %d\0" % len(payload) + payload, usedforsecurity=True).hexdigest()
  ```
- `_identity` · survived · assignment: value to None · `b29cf11546c8ef5b`
  ```diff
  -name = signer.principal
  +name = None
  ```
- `signed_object` · survived · argument: replaced by None · `ce63a5a0400f7cee`
  ```diff
  -lines = signature.rstrip(b"\n").split(b"\n")
  +lines = signature.rstrip(None).split(b"\n")
  ```
- `signed_object` · survived · string method swapped · `60470ddce0178458`
  ```diff
  -lines = signature.rstrip(b"\n").split(b"\n")
  +lines = signature.lstrip(b"\n").split(b"\n")
  ```
- `signed_object` · survived · string: XX added · `cb162268ae27b0eb`
  ```diff
  -lines = signature.rstrip(b"\n").split(b"\n")
  +lines = signature.rstrip(b"XX\nXX").split(b"\n")
  ```
- `split_signature` · survived · string method swapped · `ad8eabd0b1b3f90a`
  ```diff
  -header, _, message = raw.partition(b"\n\n")
  +header, _, message = raw.rpartition(b"\n\n")
  ```
- `split_signature` · survived · assignment: value to None · `0a52decd40ac2f5d`
  ```diff
  -in_signature = False
  +in_signature = None
  ```
- `split_signature` · survived · True / False swapped · `00b1e036d43ccd6b`
  ```diff
  -in_signature = False
  +in_signature = True
  ```
- `split_signature` · survived · operator: and / or · `438ff373c3668638`
  ```diff
  -elif in_signature and line.startswith(b" "):
  +elif in_signature or line.startswith(b" "):
  ```
- `split_signature` · survived · assignment: value to None · `0a52decd40ac2f5d-2`
  ```diff
  -in_signature = False
  +in_signature = None
  ```
- `split_signature` · survived · True / False swapped · `00b1e036d43ccd6b-2`
  ```diff
  -in_signature = False
  +in_signature = True
  ```
- `split_signature` · survived · ternary: condition forced · `d019de97e3748995`
  ```diff
  -return payload, (b"\n".join(signature) + b"\n") if signature else b""
  +return payload, (b"\n".join(signature) + b"\n") if (signature) or True else b""
  ```
- `split_signature` · survived · string: XX added · `7b1ba59553d81fdb`
  ```diff
  -return payload, (b"\n".join(signature) + b"\n") if signature else b""
  +return payload, (b"\n".join(signature) + b"\n") if signature else b"XXXX"
  ```
- `build_commit` · survived · string: upper case · `1abee3be6eae2807`
  ```diff
  -tree = _git(clone, ("write-tree",)).decode("ascii").strip()
  +tree = _git(clone, ("write-tree",)).decode("ASCII").strip()
  ```
- `build_commit` · survived · string: upper case · `82105c3d725f2de0`
  ```diff
  -.decode("ascii")
  +.decode("ASCII")
  ```
- `_parent` · survived · string: XX added · `6fb2c7cd94b6d13c`
  ```diff
  -for line in raw.split(b"\n\n")[0].split(b"\n")
  +for line in raw.split(b"XX\n\nXX")[0].split(b"\n")
  ```
- `_parent` · survived · ternary: condition forced · `bd9fc5d3a8fc4fbf`
  ```diff
  -return parents[0] if len(parents) == 1 else None
  +return parents[0] if (len(parents) == 1) or True else None
  ```
- `_blobs` · survived · string method swapped · `99f288db97613edd`
  ```diff
  -meta, _, path = record.partition(b"\t")
  +meta, _, path = record.rpartition(b"\t")
  ```
- `_content_reasons` · survived · ternary: condition forced · `33e556651aacbbcf`
  ```diff
  -reasons = [] if _parent(raw) == base else ["commit_parent_not_base"]
  +reasons = [] if (_parent(raw) == base) or True else ["commit_parent_not_base"]
  ```
- `_content_reasons` · survived · string: XX added · `58ab65c5bdfd1541`
  ```diff
  -reasons = [] if _parent(raw) == base else ["commit_parent_not_base"]
  +reasons = [] if _parent(raw) == base else ["XXcommit_parent_not_baseXX"]
  ```
- `_content_reasons` · survived · string: upper case · `077f1c35080840c5`
  ```diff
  -reasons = [] if _parent(raw) == base else ["commit_parent_not_base"]
  +reasons = [] if _parent(raw) == base else ["COMMIT_PARENT_NOT_BASE"]
  ```
- `_content_reasons` · survived · argument: replaced by None · `739d8cdaf5b1f6fd`
  ```diff
  -reasons.append("commit_paths_not_proposal")
  +reasons.append(None)
  ```
- `_content_reasons` · survived · string: XX added · `88fcf1d62207fd49`
  ```diff
  -reasons.append("commit_paths_not_proposal")
  +reasons.append("XXcommit_paths_not_proposalXX")
  ```
- `_content_reasons` · survived · string: upper case · `7bdc610171ac51ab`
  ```diff
  -reasons.append("commit_paths_not_proposal")
  +reasons.append("COMMIT_PATHS_NOT_PROPOSAL")
  ```
- `_content_reasons` · survived · argument: replaced by None · `716470e6fe5ee20d`
  ```diff
  -reasons.append("commit_bytes_not_proposal")
  +reasons.append(None)
  ```
- `_content_reasons` · survived · string: XX added · `8f7ad79c8650b3d9`
  ```diff
  -reasons.append("commit_bytes_not_proposal")
  +reasons.append("XXcommit_bytes_not_proposalXX")
  ```
- `_content_reasons` · survived · string: upper case · `5509f9e87d99af3f`
  ```diff
  -reasons.append("commit_bytes_not_proposal")
  +reasons.append("COMMIT_BYTES_NOT_PROPOSAL")
  ```
- `verify_commit` · survived · operator: and / or · `f83fe332995c7432`
  ```diff
  -elif signer != principal or check.entry is None or check.entry.role != "bot_execution":
  +elif signer != principal or check.entry is None and check.entry.role != "bot_execution":
  ```
- `verify_commit` · survived · operator: and / or · `f956adc62005916b`
  ```diff
  -elif signer != principal or check.entry is None or check.entry.role != "bot_execution":
  +elif signer != principal and check.entry is None or check.entry.role != "bot_execution":
  ```
- `verify_commit` · survived · argument: replaced by None · `faf25b2072847d42`
  ```diff
  -reasons.append("bot_signature:not_the_bot")
  +reasons.append(None)
  ```
- `verify_commit` · survived · string: XX added · `f7bfce5dc2a49007`
  ```diff
  -reasons.append("bot_signature:not_the_bot")
  +reasons.append("XXbot_signature:not_the_botXX")
  ```
- `verify_commit` · survived · string: upper case · `84b48aead6dac619`
  ```diff
  -reasons.append("bot_signature:not_the_bot")
  +reasons.append("BOT_SIGNATURE:NOT_THE_BOT")
  ```
- `verify_commit` · survived · argument: replaced by None · `b7c76bfff691bb84`
  ```diff
  -return CommitCheck(commit, not reasons, tuple(reasons), signer)
  +return CommitCheck(commit, None, tuple(reasons), signer)
  ```
- `verify_commit` · survived · argument: replaced by None · `7fe323a4765d851d`
  ```diff
  -return CommitCheck(commit, not reasons, tuple(reasons), signer)
  +return CommitCheck(commit, not reasons, tuple(reasons), None)
  ```
- `verify_commit` · survived · not / ~ removed · `d855cb51548b8285`
  ```diff
  -return CommitCheck(commit, not reasons, tuple(reasons), signer)
  +return CommitCheck(commit, reasons, tuple(reasons), signer)
  ```
- `init_bare` · survived · argument: replaced by None · `2628cb3acfd1d1f6`
  ```diff
  -raise _refused("init")
  +raise _refused(None)
  ```
- `init_bare` · survived · string: XX added · `d10899c08225a57c`
  ```diff
  -raise _refused("init")
  +raise _refused("XXinitXX")
  ```
- `init_bare` · survived · string: upper case · `48284a4620b6553f`
  ```diff
  -raise _refused("init")
  +raise _refused("INIT")
  ```
- `remote_head` · survived · argument: replaced by None · `5ff22ef573ad5beb`
  ```diff
  -raise _refused("ls-remote")
  +raise _refused(None)
  ```
- `remote_head` · survived · string: XX added · `51c607142b385056`
  ```diff
  -raise _refused("ls-remote")
  +raise _refused("XXls-remoteXX")
  ```
- `remote_head` · survived · string: upper case · `cbeb8105af5a3ee3`
  ```diff
  -raise _refused("ls-remote")
  +raise _refused("LS-REMOTE")
  ```
- `remote_head` · survived · argument: removed · `7b136641e8683267`
  ```diff
  -line = answer.out.decode("ascii", "replace").strip()
  +line = answer.out.decode("ascii", ).strip()
  ```
- `remote_head` · survived · string: upper case · `e486e06c70873c1c`
  ```diff
  -line = answer.out.decode("ascii", "replace").strip()
  +line = answer.out.decode("ASCII", "replace").strip()
  ```
- `remote_head` · survived · string: XX added · `949de21f0879951e`
  ```diff
  -line = answer.out.decode("ascii", "replace").strip()
  +line = answer.out.decode("ascii", "XXreplaceXX").strip()
  ```
- `remote_head` · survived · string: upper case · `f3ffdad8662aea36`
  ```diff
  -line = answer.out.decode("ascii", "replace").strip()
  +line = answer.out.decode("ascii", "REPLACE").strip()
  ```
- `push` · survived · argument: replaced by None · `4160cdd7901ce756`
  ```diff
  -return Sent("outcome_unknown", commit, ("push_could_not_run",))
  +return Sent(None, commit, ("push_could_not_run",))
  ```
- `push` · survived · argument: replaced by None · `41a5ad12f4d99a43`
  ```diff
  -return Sent("outcome_unknown", commit, ("push_could_not_run",))
  +return Sent("outcome_unknown", None, ("push_could_not_run",))
  ```
- `push` · survived · argument: replaced by None · `6caecd9efde02297`
  ```diff
  -return Sent("outcome_unknown", commit, ("push_could_not_run",))
  +return Sent("outcome_unknown", commit, None)
  ```
- `push` · survived · argument: removed · `1ad58f9da9c4ee0d`
  ```diff
  -return Sent("outcome_unknown", commit, ("push_could_not_run",))
  +return Sent(commit, ("push_could_not_run",))
  ```
- `push` · survived · argument: removed · `137515d272ebeeb1`
  ```diff
  -return Sent("outcome_unknown", commit, ("push_could_not_run",))
  +return Sent("outcome_unknown", ("push_could_not_run",))
  ```
- `push` · survived · argument: removed · `cf0b61d3cc67d3a8`
  ```diff
  -return Sent("outcome_unknown", commit, ("push_could_not_run",))
  +return Sent("outcome_unknown", commit, )
  ```
- `push` · survived · string: XX added · `7a065414bc7a970d`
  ```diff
  -return Sent("outcome_unknown", commit, ("push_could_not_run",))
  +return Sent("XXoutcome_unknownXX", commit, ("push_could_not_run",))
  ```
- `push` · survived · string: upper case · `b22bfda0b5de34eb`
  ```diff
  -return Sent("outcome_unknown", commit, ("push_could_not_run",))
  +return Sent("OUTCOME_UNKNOWN", commit, ("push_could_not_run",))
  ```
- `push` · survived · string: XX added · `f510ecf6e5a10582`
  ```diff
  -return Sent("outcome_unknown", commit, ("push_could_not_run",))
  +return Sent("outcome_unknown", commit, ("XXpush_could_not_runXX",))
  ```
- `push` · survived · string: upper case · `86e3e733094545e2`
  ```diff
  -return Sent("outcome_unknown", commit, ("push_could_not_run",))
  +return Sent("outcome_unknown", commit, ("PUSH_COULD_NOT_RUN",))
  ```
- `push` · survived · argument: replaced by None · `57a8c2dc30635a62`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_unreadable_after_push",))
  +return Sent(None, commit, ("remote_unreadable_after_push",))
  ```
- `push` · survived · argument: replaced by None · `e9afc801ce73ef72`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_unreadable_after_push",))
  +return Sent("outcome_unknown", None, ("remote_unreadable_after_push",))
  ```
- `push` · survived · argument: replaced by None · `3874b256a28296d2`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_unreadable_after_push",))
  +return Sent("outcome_unknown", commit, None)
  ```
- `push` · survived · argument: removed · `0a2343e6ded19e10`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_unreadable_after_push",))
  +return Sent(commit, ("remote_unreadable_after_push",))
  ```
- `push` · survived · argument: removed · `f373e39a9b304d43`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_unreadable_after_push",))
  +return Sent("outcome_unknown", ("remote_unreadable_after_push",))
  ```
- `push` · survived · argument: removed · `0c4152f070f7d50c`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_unreadable_after_push",))
  +return Sent("outcome_unknown", commit, )
  ```
- `push` · survived · string: XX added · `6cdb2beb79d69ebd`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_unreadable_after_push",))
  +return Sent("XXoutcome_unknownXX", commit, ("remote_unreadable_after_push",))
  ```
- `push` · survived · string: upper case · `5f811618b8febfed`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_unreadable_after_push",))
  +return Sent("OUTCOME_UNKNOWN", commit, ("remote_unreadable_after_push",))
  ```
- `push` · survived · string: XX added · `a2beaa44ce1ceba9`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_unreadable_after_push",))
  +return Sent("outcome_unknown", commit, ("XXremote_unreadable_after_pushXX",))
  ```
- `push` · survived · string: upper case · `3a910a9ff9e42f74`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_unreadable_after_push",))
  +return Sent("outcome_unknown", commit, ("REMOTE_UNREADABLE_AFTER_PUSH",))
  ```
- `push` · survived · operator: and / or · `41363b7f1d730f8f`
  ```diff
  -rejected = answer.code != 0 and b"[rejected]" in answer.out + answer.err
  +rejected = answer.code != 0 or b"[rejected]" in answer.out + answer.err
  ```
- `push` · survived · argument: replaced by None · `4f335c77696ae3f6`
  ```diff
  -return Sent("refused", commit, ("push_rejected_non_fast_forward",))
  +return Sent("refused", None, ("push_rejected_non_fast_forward",))
  ```
- `push` · survived · string: XX added · `150ba97c4f9aee28`
  ```diff
  -return Sent("refused", commit, ("push_rejected_non_fast_forward",))
  +return Sent("refused", commit, ("XXpush_rejected_non_fast_forwardXX",))
  ```
- `push` · survived · string: upper case · `138060d4e4ec6663`
  ```diff
  -return Sent("refused", commit, ("push_rejected_non_fast_forward",))
  +return Sent("refused", commit, ("PUSH_REJECTED_NON_FAST_FORWARD",))
  ```
- `push` · survived · argument: replaced by None · `dadd99deb66df47c`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_not_at_pushed_commit",))
  +return Sent("outcome_unknown", None, ("remote_not_at_pushed_commit",))
  ```
- `push` · survived · string: XX added · `c255c067d0435946`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_not_at_pushed_commit",))
  +return Sent("outcome_unknown", commit, ("XXremote_not_at_pushed_commitXX",))
  ```
- `push` · survived · string: upper case · `c3f035540c53354e`
  ```diff
  -return Sent("outcome_unknown", commit, ("remote_not_at_pushed_commit",))
  +return Sent("outcome_unknown", commit, ("REMOTE_NOT_AT_PUSHED_COMMIT",))
  ```
- `remote_object` · survived · argument: replaced by None · `8a89e688bcfdb26b`
  ```diff
  -raise _refused("cat-file")
  +raise _refused(None)
  ```
- `remote_object` · survived · string: XX added · `3ec81f7cf3b3b32d`
  ```diff
  -raise _refused("cat-file")
  +raise _refused("XXcat-fileXX")
  ```
- `remote_object` · survived · string: upper case · `5bf792b52ac33233`
  ```diff
  -raise _refused("cat-file")
  +raise _refused("CAT-FILE")
  ```
- `remote_tree` · survived · argument: replaced by None · `825e25e94f58d2c2`
  ```diff
  -raise _refused("ls-tree")
  +raise _refused(None)
  ```
- `remote_tree` · survived · string: XX added · `1c46f36e2ec489ad`
  ```diff
  -raise _refused("ls-tree")
  +raise _refused("XXls-treeXX")
  ```
- `remote_tree` · survived · string: upper case · `95fc535549b96b85`
  ```diff
  -raise _refused("ls-tree")
  +raise _refused("LS-TREE")
  ```
- `remote_tree` · survived · string method swapped · `d601f9de886cbe52`
  ```diff
  -meta, _, path = record.partition(b"\t")
  +meta, _, path = record.rpartition(b"\t")
  ```
- `ref_event` · survived · argument: replaced by None · `c9c37a1363522ad7`
  ```diff
  -text = (bare / "logs" / "refs" / "heads" / "main").read_text("utf-8")
  +text = (bare / "logs" / "refs" / "heads" / "main").read_text(None)
  ```
- `ref_event` · survived · string: upper case · `4e139901178188d8`
  ```diff
  -text = (bare / "logs" / "refs" / "heads" / "main").read_text("utf-8")
  +text = (bare / "logs" / "refs" / "heads" / "main").read_text("UTF-8")
  ```
- `ref_event` · survived · string method swapped · `e6e38979e28dda43`
  ```diff
  -head, _, _message = line.partition("\t")
  +head, _, _message = line.rpartition("\t")
  ```
- `ref_event` · survived · operator: comparison · `ff5802faab6640dd`
  ```diff
  -if len(parts) < _REFLOG_FIELDS or (parts[0], parts[1]) != (before, after):
  +if len(parts) <= _REFLOG_FIELDS or (parts[0], parts[1]) != (before, after):
  ```
- `ref_event` · survived · argument: replaced by None · `b9351324213f5739`
  ```diff
  -return RefEvent(before, after, " ".join(parts[2:-2]), at)
  +return RefEvent(None, after, " ".join(parts[2:-2]), at)
  ```
- `ref_event` · survived · argument: replaced by None · `b86ef9266979fac3`
  ```diff
  -return RefEvent(before, after, " ".join(parts[2:-2]), at)
  +return RefEvent(before, None, " ".join(parts[2:-2]), at)
  ```
- `ref_event` · survived · argument: replaced by None · `8d1b91e1f30ebb8e`
  ```diff
  -return RefEvent(before, after, " ".join(parts[2:-2]), at)
  +return RefEvent(before, after, None, at)
  ```
- `ref_event` · survived · string: XX added · `b430476990938804`
  ```diff
  -return RefEvent(before, after, " ".join(parts[2:-2]), at)
  +return RefEvent(before, after, "XX XX".join(parts[2:-2]), at)
  ```
- `ref_event` · survived · number · `8abe5819cdb5c8b4`
  ```diff
  -return RefEvent(before, after, " ".join(parts[2:-2]), at)
  +return RefEvent(before, after, " ".join(parts[3:-2]), at)
  ```
- `ref_event` · survived · operator: arithmetic · `9dcba75861294e29`
  ```diff
  -return RefEvent(before, after, " ".join(parts[2:-2]), at)
  +return RefEvent(before, after, " ".join(parts[2:+2]), at)
  ```
- `ref_event` · survived · number · `1e24889f93516727`
  ```diff
  -return RefEvent(before, after, " ".join(parts[2:-2]), at)
  +return RefEvent(before, after, " ".join(parts[2:-3]), at)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_receipts.py</code>: 5</summary>

- `_refused` · survived · argument: replaced by None · `693d9e9d00e3be46`
  ```diff
  -return ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, FOLDER)
  +return ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, None)
  ```
- `_folder` · survived · operator: and / or · `c75d84962936c9cd`
  ```diff
  -if os.path.lexists(folder) and (folder.is_symlink() or not folder.is_dir()):
  +if os.path.lexists(folder) and (folder.is_symlink() and not folder.is_dir()):
  ```
- `_read` · survived · argument: replaced by None · `89746946fbaa737e`
  ```diff
  -with directory(folder, FOLDER) as parent:
  +with directory(folder, None) as parent:
  ```
- `_read` · survived · argument: replaced by None · `44603e212d323bd2`
  ```diff
  -raw = read_leaf(parent, name, FOLDER)
  +raw = read_leaf(parent, name, None)
  ```
- `append` · survived · number · `9fe62f0fa2a93857`
  ```diff
  -folder.mkdir(mode=0o700)
  +folder.mkdir(mode=449)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_remote.py</code>: 26</summary>

- `_github_commit` · survived · string: XX added · `8a2383c58e5419c7`
  ```diff
  -reasons.append("commit_not_made_by_an_app")
  +reasons.append("XXcommit_not_made_by_an_appXX")
  ```
- `_github_commit` · survived · string: upper case · `91ca68be598a6bdd`
  ```diff
  -reasons.append("commit_not_made_by_an_app")
  +reasons.append("COMMIT_NOT_MADE_BY_AN_APP")
  ```
- `_github_commit` · survived · string: XX added · `95158a6d7d404691`
  ```diff
  -reasons.append("commit_not_by_the_pinned_app")
  +reasons.append("XXcommit_not_by_the_pinned_appXX")
  ```
- `_github_commit` · survived · string: upper case · `e3d28a8237d6249f`
  ```diff
  -reasons.append("commit_not_by_the_pinned_app")
  +reasons.append("COMMIT_NOT_BY_THE_PINNED_APP")
  ```
- `_github_commit` · survived · string: XX added · `2a4839cc445efb64`
  ```diff
  -reasons.append("github_signature_not_verified")
  +reasons.append("XXgithub_signature_not_verifiedXX")
  ```
- `_github_commit` · survived · string: upper case · `b011c25656f85d76`
  ```diff
  -reasons.append("github_signature_not_verified")
  +reasons.append("GITHUB_SIGNATURE_NOT_VERIFIED")
  ```
- `_headers` · survived · assignment: None to empty string · `42499778d04718f1`
  ```diff
  -stated: datetime | None = None
  +stated: datetime | None = ""
  ```
- `_headers` · survived · argument: removed · `07eb1f04caf549bb`
  ```diff
  -parents.append(line[7:].decode("ascii", "replace"))
  +parents.append(line[7:].decode("ascii", ))
  ```
- `_headers` · survived · string: upper case · `c46a7d2814cf54f0`
  ```diff
  -parents.append(line[7:].decode("ascii", "replace"))
  +parents.append(line[7:].decode("ASCII", "replace"))
  ```
- `_headers` · survived · string: XX added · `36b88569c0920f44`
  ```diff
  -parents.append(line[7:].decode("ascii", "replace"))
  +parents.append(line[7:].decode("ascii", "XXreplaceXX"))
  ```
- `_headers` · survived · string: upper case · `81487d76770e2ba9`
  ```diff
  -parents.append(line[7:].decode("ascii", "replace"))
  +parents.append(line[7:].decode("ascii", "REPLACE"))
  ```
- `_headers` · survived · argument: removed · `398558997e98c9a1`
  ```diff
  -return parents, stated, message.decode("utf-8", "replace")
  +return parents, stated, message.decode("utf-8", )
  ```
- `_headers` · survived · string: upper case · `04cd52e68798bb5d`
  ```diff
  -return parents, stated, message.decode("utf-8", "replace")
  +return parents, stated, message.decode("UTF-8", "replace")
  ```
- `_headers` · survived · string: XX added · `e495bde37fece723`
  ```diff
  -return parents, stated, message.decode("utf-8", "replace")
  +return parents, stated, message.decode("utf-8", "XXreplaceXX")
  ```
- `_headers` · survived · string: upper case · `5c7121f59c8f72b7`
  ```diff
  -return parents, stated, message.decode("utf-8", "replace")
  +return parents, stated, message.decode("utf-8", "REPLACE")
  ```
- `_changed` · survived · ternary: condition forced · `7e460e25f4efbc2a`
  ```diff
  -None if blob is None else publication_path_b.remote_object(bare, "blob", blob)
  +None if (blob is None) and False else publication_path_b.remote_object(bare, "blob", blob)
  ```
- `_signature_reasons` · survived · string: XX added · `3044729e5be33e91`
  ```diff
  -return ("commit_time_unreadable",)
  +return ("XXcommit_time_unreadableXX",)
  ```
- `_signature_reasons` · survived · string: upper case · `5709e65d0f41868e`
  ```diff
  -return ("commit_time_unreadable",)
  +return ("COMMIT_TIME_UNREADABLE",)
  ```
- `_signature_reasons` · survived · operator: and / or · `a6d485a2660b2f69`
  ```diff
  -if entry is None or entry.principal != principal or entry.role != "bot_execution":
  +if entry is None or entry.principal != principal and entry.role != "bot_execution":
  ```
- `_signature_reasons` · survived · operator: and / or · `89db8edd67d31926`
  ```diff
  -if entry is None or entry.principal != principal or entry.role != "bot_execution":
  +if entry is None and entry.principal != principal or entry.role != "bot_execution":
  ```
- `_signature_reasons` · survived · string: XX added · `8f96e67e6fe483fa`
  ```diff
  -return ("bot_signature:not_the_bot",)
  +return ("XXbot_signature:not_the_botXX",)
  ```
- `_signature_reasons` · survived · string: upper case · `04277f4bb4f81f6b`
  ```diff
  -return ("bot_signature:not_the_bot",)
  +return ("BOT_SIGNATURE:NOT_THE_BOT",)
  ```
- `_bare_commit` · survived · string method swapped · `a7de46e4961b512b`
  ```diff
  -headline, _, body = message.partition("\n\n")
  +headline, _, body = message.rpartition("\n\n")
  ```
- `_walk` · survived · argument: replaced by None · `4e7bcf618a9f4d70`
  ```diff
  -return History((), "remote_main_unreadable")
  +return History((), None)
  ```
- `history` · survived · argument: replaced by None · `6c9a255853bac184`
  ```diff
  -return History((), "remote_main_unreadable")
  +return History(None, "remote_main_unreadable")
  ```
- `history` · survived · argument: replaced by None · `c6416758a53fd485`
  ```diff
  -return History((), "remote_head_moved_during_read")
  +return History(None, "remote_head_moved_during_read")
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_remote_recovery.py</code>: 48</summary>

- `_reader` · survived · argument: replaced by None · `2090bc5d4ae47c4b`
  ```diff
  -raise _not_eligible("recovery_needs_a_sandboxed_adapter")
  +raise _not_eligible(None)
  ```
- `_reader` · survived · string: XX added · `6392c50a83c0423f`
  ```diff
  -raise _not_eligible("recovery_needs_a_sandboxed_adapter")
  +raise _not_eligible("XXrecovery_needs_a_sandboxed_adapterXX")
  ```
- `_reader` · survived · string: upper case · `c9c20ed9ff24a14e`
  ```diff
  -raise _not_eligible("recovery_needs_a_sandboxed_adapter")
  +raise _not_eligible("RECOVERY_NEEDS_A_SANDBOXED_ADAPTER")
  ```
- `_reader` · survived · argument: replaced by None · `16598c732a4712a7`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible(None)
  ```
- `_reader` · survived · string: XX added · `a13858454c3c7e44`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("XXadapterXX")
  ```
- `_reader` · survived · string: upper case · `ccb71ce30fb16ca5`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("ADAPTER")
  ```
- `_reader` · survived · argument: replaced by None · `16598c732a4712a7-2`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible(None)
  ```
- `_reader` · survived · string: XX added · `a13858454c3c7e44-2`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("XXadapterXX")
  ```
- `_reader` · survived · string: upper case · `ccb71ce30fb16ca5-2`
  ```diff
  -raise _not_eligible("adapter")
  +raise _not_eligible("ADAPTER")
  ```
- `_verdict` · survived · string: XX added · `23ee20303a6fc86e`
  ```diff
  -return Verdict("authorization_unknown", ("not_a_pipeline_commit",), None)
  +return Verdict("authorization_unknown", ("XXnot_a_pipeline_commitXX",), None)
  ```
- `_verdict` · survived · string: upper case · `7ca254a46e400a28`
  ```diff
  -return Verdict("authorization_unknown", ("not_a_pipeline_commit",), None)
  +return Verdict("authorization_unknown", ("NOT_A_PIPELINE_COMMIT",), None)
  ```
- `_authorization_fields` · survived · assignment: value to None · `d1f78a110aec1d8f`
  ```diff
  -attempt = verdict.attempt
  +attempt = None
  ```
- `_authorization_fields` · survived · ternary: condition forced · `2ae57474d0e60c6e`
  ```diff
  -"attempt_id": attempt.attempt_id if attempt else None,
  +"attempt_id": attempt.attempt_id if (attempt) and False else None,
  ```
- `_authorization_fields` · survived · ternary: condition forced · `0e37fd4cd2f97d96`
  ```diff
  -"attempt_at": attempt.recorded_at if attempt else None,
  +"attempt_at": attempt.recorded_at if (attempt) and False else None,
  ```
- `_draft` · survived · ternary: condition forced · `2a92bbeb010aaeb6`
  ```diff
  -"parent": commit.parents[0] if len(commit.parents) == 1 else None,
  +"parent": commit.parents[0] if (len(commit.parents) == 1) or True else None,
  ```
- `_drafts` · survived · operator: and / or · `cf689bf1a3910946`
  ```diff
  -if draft["integrity"] != "integrity_verified" or len(drafts) > 1:
  +if draft["integrity"] != "integrity_verified" and len(drafts) > 1:
  ```
- `_drafts` · survived · number · `ff6df701833ebe1b`
  ```diff
  -if draft["integrity"] != "integrity_verified" or len(drafts) > 1:
  +if draft["integrity"] != "integrity_verified" or len(drafts) > 2:
  ```
- `_conflict` · survived · string: XX added · `25d43d28f36f59ac`
  ```diff
  -_halt(request, ("receipt_conflict",), at)
  +_halt(request, ("XXreceipt_conflictXX",), at)
  ```
- `_conflict` · survived · string: upper case · `51b0d305ee74da35`
  ```diff
  -_halt(request, ("receipt_conflict",), at)
  +_halt(request, ("RECEIPT_CONFLICT",), at)
  ```
- `_reconcile` · survived · argument: replaced by None · `1e041aa51c231002`
  ```diff
  -raise _conflict(request, at)
  +raise _conflict(None, at)
  ```
- `_reconcile` · survived · argument: replaced by None · `e094742e0c1ead17`
  ```diff
  -raise _conflict(request, at)
  +raise _conflict(request, None)
  ```
- `_reconcile` · survived · argument: removed · `b7b29b678647d91e`
  ```diff
  -raise _conflict(request, at)
  +raise _conflict(at)
  ```
- `_reconcile` · survived · argument: removed · `e25cc74b6a424fe3`
  ```diff
  -raise _conflict(request, at)
  +raise _conflict(request, )
  ```
- `_attempt` · survived · assignment: value to None · `72c75296017821b3`
  ```diff
  -found = publication_attempts.records(request.workspace, tid)
  +found = None
  ```
- `_attempt` · survived · assignment: value to None · `c78e7409513e0556`
  ```diff
  -last = found[-1] if found else None
  +last = None
  ```
- `_attempt` · survived · ternary: condition forced · `8a381f524f895804`
  ```diff
  -last = found[-1] if found else None
  +last = found[-1] if (found) and False else None
  ```
- `_attempt` · survived · ternary: condition forced · `f0c526c52068a9c0`
  ```diff
  -"attempt_id": last.attempt_id if last else attempt_id_of(tid, sequence),
  +"attempt_id": last.attempt_id if (last) and False else attempt_id_of(tid, sequence),
  ```
- `_attempt` · survived · argument: replaced by None · `f22cd62764076ba6`
  ```diff
  -"attempt_id": last.attempt_id if last else attempt_id_of(tid, sequence),
  +"attempt_id": last.attempt_id if last else attempt_id_of(None, sequence),
  ```
- `_attempt` · survived · argument: replaced by None · `351829408d4ed6c4`
  ```diff
  -"attempt_id": last.attempt_id if last else attempt_id_of(tid, sequence),
  +"attempt_id": last.attempt_id if last else attempt_id_of(tid, None),
  ```
- `_attempt` · survived · ternary: condition forced · `56065e49ee696ecf`
  ```diff
  -"authorization_sha256": last.authorization_sha256 if last else None,
  +"authorization_sha256": last.authorization_sha256 if (last) and False else None,
  ```
- `_reservation` · survived · ternary: condition forced · `c536769f36a1f363`
  ```diff
  -return reserved[0] if len(reserved) == 1 else None
  +return reserved[0] if (len(reserved) == 1) or True else None
  ```
- `_ledger` · survived · argument: replaced by None · `27735b732cff8b0f`
  ```diff
  -return LedgerPlan(outcome="refused", reasons=("ledger_reservation_missing",), entry=None)
  +return LedgerPlan(outcome=None, reasons=("ledger_reservation_missing",), entry=None)
  ```
- `_ledger` · survived · argument: replaced by None · `28191c52a168b2b8`
  ```diff
  -return LedgerPlan(outcome="refused", reasons=("ledger_reservation_missing",), entry=None)
  +return LedgerPlan(outcome="refused", reasons=None, entry=None)
  ```
- `_ledger` · survived · argument: removed · `e8d4cedf3e9464b8`
  ```diff
  -return LedgerPlan(outcome="refused", reasons=("ledger_reservation_missing",), entry=None)
  +return LedgerPlan(reasons=("ledger_reservation_missing",), entry=None)
  ```
- `_ledger` · survived · argument: removed · `d1f69da3addb660a`
  ```diff
  -return LedgerPlan(outcome="refused", reasons=("ledger_reservation_missing",), entry=None)
  +return LedgerPlan(outcome="refused", entry=None)
  ```
- `_ledger` · survived · argument: removed · `74365a95118a508d`
  ```diff
  -return LedgerPlan(outcome="refused", reasons=("ledger_reservation_missing",), entry=None)
  +return LedgerPlan(outcome="refused", reasons=("ledger_reservation_missing",), )
  ```
- `_ledger` · survived · string: XX added · `bb1bb7bac03b8641`
  ```diff
  -return LedgerPlan(outcome="refused", reasons=("ledger_reservation_missing",), entry=None)
  +return LedgerPlan(outcome="XXrefusedXX", reasons=("ledger_reservation_missing",), entry=None)
  ```
- `_ledger` · survived · string: upper case · `eeb74f1477c1c300`
  ```diff
  -return LedgerPlan(outcome="refused", reasons=("ledger_reservation_missing",), entry=None)
  +return LedgerPlan(outcome="REFUSED", reasons=("ledger_reservation_missing",), entry=None)
  ```
- `_ledger` · survived · string: XX added · `27d533d74e7257db`
  ```diff
  -return LedgerPlan(outcome="refused", reasons=("ledger_reservation_missing",), entry=None)
  +return LedgerPlan(outcome="refused", reasons=("XXledger_reservation_missingXX",), entry=None)
  ```
- `_ledger` · survived · string: upper case · `b86a6a63bb3d9511`
  ```diff
  -return LedgerPlan(outcome="refused", reasons=("ledger_reservation_missing",), entry=None)
  +return LedgerPlan(outcome="refused", reasons=("LEDGER_RESERVATION_MISSING",), entry=None)
  ```
- `_ledger` · survived · operator: and / or · `f6d3d5b2a24bc745`
  ```diff
  -at = max(receipt.remote_event_at or receipt.attempt_at or slot.recorded_at, slot.recorded_at)
  +at = max(receipt.remote_event_at or receipt.attempt_at and slot.recorded_at, slot.recorded_at)
  ```
- `_ledger` · survived · operator: and / or · `7536ab7ff6a7bd1d`
  ```diff
  -at = max(receipt.remote_event_at or receipt.attempt_at or slot.recorded_at, slot.recorded_at)
  +at = max(receipt.remote_event_at and receipt.attempt_at or slot.recorded_at, slot.recorded_at)
  ```
- `_not_published` · survived · string: XX added · `3e8464aaa73c3411`
  ```diff
  -if found and found[-1].state in ("prepared_to_send", "outcome_unknown"):
  +if found and found[-1].state in ("prepared_to_send", "XXoutcome_unknownXX"):
  ```
- `_not_published` · survived · string: upper case · `196e37837c26aaf9`
  ```diff
  -if found and found[-1].state in ("prepared_to_send", "outcome_unknown"):
  +if found and found[-1].state in ("prepared_to_send", "OUTCOME_UNKNOWN"):
  ```
- `_not_published` · survived · string: XX added · `992b1f39a68ee268`
  ```diff
  -_attempt(request, "refused", {"reasons": ("recovered_not_published",)}, at)
  +_attempt(request, "refused", {"reasons": ("XXrecovered_not_publishedXX",)}, at)
  ```
- `_not_published` · survived · string: upper case · `6d3548cc73d35108`
  ```diff
  -_attempt(request, "refused", {"reasons": ("recovered_not_published",)}, at)
  +_attempt(request, "refused", {"reasons": ("RECOVERED_NOT_PUBLISHED",)}, at)
  ```
- `_not_published` · survived · argument: replaced by None · `702b40006becf1cc`
  ```diff
  -return RecoveryOutcome("not_published", None, None, ())
  +return RecoveryOutcome("not_published", None, None, None)
  ```
- `_settle` · survived · argument: replaced by None · `2e47000f86b0a9a1`
  ```diff
  -return RecoveryOutcome(classification, primary, ledger, ())
  +return RecoveryOutcome(classification, primary, ledger, None)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_sandbox.py</code>: 15</summary>

- `SimulatedGitHub._apply` · survived · number · `7fbfa06bd908969b`
  ```diff
  -oid = format(len(self.commits) + 1, "040x")
  +oid = format(len(self.commits) + 2, "040x")
  ```
- `SimulatedGitHub._apply` · survived · string: upper case · `ea9a92f71f3702b7`
  ```diff
  -oid = format(len(self.commits) + 1, "040x")
  +oid = format(len(self.commits) + 1, "040X")
  ```
- `SimulatedGitHub._apply` · survived · argument: replaced by None · `98c81846770054ec`
  ```diff
  -oid,
  +None,
  ```
- `_read` · survived · operator: bitwise · `5ad422ab439000ed`
  ```diff
  -descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
  +descriptor = os.open(path, os.O_RDONLY & os.O_NOFOLLOW)
  ```
- `_intact` · survived · True / False swapped · `bb4d7abc702ddbde`
  ```diff
  -return False
  +return True
  ```
- `_intact` · survived · string: XX added · `8cf8f0f74afcc33a`
  ```diff
  -unexpected = ("hooks", "objects/info/alternates", "objects/info/http-alternates", "commondir")
  +unexpected = ("hooks", "objects/info/alternates", "XXobjects/info/http-alternatesXX", "commondir")
  ```
- `_intact` · survived · string: upper case · `f0e9683e479ebdfb`
  ```diff
  -unexpected = ("hooks", "objects/info/alternates", "objects/info/http-alternates", "commondir")
  +unexpected = ("hooks", "objects/info/alternates", "OBJECTS/INFO/HTTP-ALTERNATES", "commondir")
  ```
- `_intact` · survived · string: XX added · `5b3b98106d39b84b`
  ```diff
  -unexpected = ("hooks", "objects/info/alternates", "objects/info/http-alternates", "commondir")
  +unexpected = ("hooks", "objects/info/alternates", "objects/info/http-alternates", "XXcommondirXX")
  ```
- `_intact` · survived · string: upper case · `6a78dd80509e9ae6`
  ```diff
  -unexpected = ("hooks", "objects/info/alternates", "objects/info/http-alternates", "commondir")
  +unexpected = ("hooks", "objects/info/alternates", "objects/info/http-alternates", "COMMONDIR")
  ```
- `_write_new` · survived · argument: removed · `96f5376e30adf9d1`
  ```diff
  -descriptor = os.open(path, flags | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
  +descriptor = os.open(path, flags | os.O_WRONLY | os.O_NOFOLLOW, )
  ```
- `_write_new` · survived · number · `72254939a0cfb14b`
  ```diff
  -descriptor = os.open(path, flags | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
  +descriptor = os.open(path, flags | os.O_WRONLY | os.O_NOFOLLOW, 385)
  ```
- `create_disposable_remote` · survived · assignment: value to None · `595844bec7c67431`
  ```diff
  -fresh = False
  +fresh = None
  ```
- `create_disposable_remote` · survived · True / False swapped · `e18d2df8650386a4`
  ```diff
  -fresh = False
  +fresh = True
  ```
- `create_disposable_remote` · survived · argument: replaced by None · `44f5e328b65d2156`
  ```diff
  -remote = DisposableRemote(sandbox, secrets.token_hex(32))
  +remote = DisposableRemote(sandbox, secrets.token_hex(None))
  ```
- `create_disposable_remote` · survived · number · `12cbd1e57b459c0e`
  ```diff
  -remote = DisposableRemote(sandbox, secrets.token_hex(32))
  +remote = DisposableRemote(sandbox, secrets.token_hex(33))
  ```

</details>

<details><summary><code>src/lovspor/git_publish.py</code>: 53</summary>

- `_local_bare` · survived · operator: and / or · `37f4447d69167d8b`
  ```diff
  -and "://" not in part
  -and part.endswith(".git")
  +and "://" not in part or part.endswith(".git")
  ```
- `_local_bare` · survived · operator: and / or · `305dcd4dc799dfff`
  ```diff
  -and os.path.normpath(part) == part
  -and "://" not in part
  +and os.path.normpath(part) == part or "://" not in part
  ```
- `_local_bare` · survived · operator: and / or · `dc877f8b780dc3d8`
  ```diff
  -Path(part).is_absolute()
  -and os.path.normpath(part) == part
  +Path(part).is_absolute() or os.path.normpath(part) == part
  ```
- `_local_bare` · survived · string: XX added · `96ef58f8320eac77`
  ```diff
  -and "://" not in part
  +and "XX://XX" not in part
  ```
- `_fits` · survived · argument: replaced by None · `9d83585cf7edf064`
  ```diff
  -return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=True))
  +return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=None))
  ```
- `_fits` · survived · argument: removed · `d98b3300f3d58b45`
  ```diff
  -return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=True))
  +return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, ))
  ```
- `_fits` · survived · True / False swapped · `be927db61e1fae3c`
  ```diff
  -return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=True))
  +return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=False))
  ```
- `environment` · survived · string: XX added · `335709e475fd4949`
  ```diff
  -"PATH": os.environ.get("PATH", os.defpath),
  +"XXPATHXX": os.environ.get("PATH", os.defpath),
  ```
- `environment` · survived · string: lower case · `345edba361389e4a`
  ```diff
  -"PATH": os.environ.get("PATH", os.defpath),
  +"path": os.environ.get("PATH", os.defpath),
  ```
- `environment` · survived · argument: replaced by None · `1bd3e1be3db03880`
  ```diff
  -"PATH": os.environ.get("PATH", os.defpath),
  +"PATH": os.environ.get("PATH", None),
  ```
- `environment` · survived · argument: removed · `afa61bcb6c17b007`
  ```diff
  -"PATH": os.environ.get("PATH", os.defpath),
  +"PATH": os.environ.get("PATH", ),
  ```
- `environment` · survived · string: XX added · `2a7f501327c5e36b`
  ```diff
  -"PATH": os.environ.get("PATH", os.defpath),
  +"PATH": os.environ.get("XXPATHXX", os.defpath),
  ```
- `environment` · survived · string: lower case · `60cbee12b6265cd8`
  ```diff
  -"PATH": os.environ.get("PATH", os.defpath),
  +"PATH": os.environ.get("path", os.defpath),
  ```
- `environment` · survived · string: XX added · `f78c8b599c0284db`
  ```diff
  -"LC_ALL": "C",
  +"XXLC_ALLXX": "C",
  ```
- `environment` · survived · string: lower case · `eb46a694b80c4b3e`
  ```diff
  -"LC_ALL": "C",
  +"lc_all": "C",
  ```
- `environment` · survived · string: XX added · `d4d5ad2fc04859c4`
  ```diff
  -"LC_ALL": "C",
  +"LC_ALL": "XXCXX",
  ```
- `environment` · survived · string: lower case · `c574d91ebc1c1dfd`
  ```diff
  -"LC_ALL": "C",
  +"LC_ALL": "c",
  ```
- `environment` · survived · string: XX added · `c2536b29631013ac`
  ```diff
  -"GIT_CONFIG_NOSYSTEM": "1",
  +"XXGIT_CONFIG_NOSYSTEMXX": "1",
  ```
- `environment` · survived · string: lower case · `cd28fc23204f960f`
  ```diff
  -"GIT_CONFIG_NOSYSTEM": "1",
  +"git_config_nosystem": "1",
  ```
- `environment` · survived · string: XX added · `5b2672c3cd8771bd`
  ```diff
  -"GIT_CONFIG_GLOBAL": os.devnull,
  +"XXGIT_CONFIG_GLOBALXX": os.devnull,
  ```
- `environment` · survived · string: lower case · `03076d3f55ea5401`
  ```diff
  -"GIT_CONFIG_GLOBAL": os.devnull,
  +"git_config_global": os.devnull,
  ```
- `environment` · survived · string: XX added · `d6fc6952db0a4399`
  ```diff
  -"GIT_TERMINAL_PROMPT": "0",
  +"XXGIT_TERMINAL_PROMPTXX": "0",
  ```
- `environment` · survived · string: lower case · `914a4144a260303a`
  ```diff
  -"GIT_TERMINAL_PROMPT": "0",
  +"git_terminal_prompt": "0",
  ```
- `environment` · survived · string: XX added · `c16ed0529aae4a81`
  ```diff
  -"GIT_TERMINAL_PROMPT": "0",
  +"GIT_TERMINAL_PROMPT": "XX0XX",
  ```
- `environment` · survived · string: XX added · `14ab16c88e3376cf`
  ```diff
  -"GIT_CEILING_DIRECTORIES": str(directory.parent),
  +"XXGIT_CEILING_DIRECTORIESXX": str(directory.parent),
  ```
- `environment` · survived · string: lower case · `171f7cf9bd4715de`
  ```diff
  -"GIT_CEILING_DIRECTORIES": str(directory.parent),
  +"git_ceiling_directories": str(directory.parent),
  ```
- `environment` · survived · argument: replaced by None · `59d0fb7f44afad6a`
  ```diff
  -"GIT_CEILING_DIRECTORIES": str(directory.parent),
  +"GIT_CEILING_DIRECTORIES": str(None),
  ```
- `environment` · survived · string: XX added · `762d14dae31e6f11`
  ```diff
  -"GIT_NO_REPLACE_OBJECTS": "1",
  +"XXGIT_NO_REPLACE_OBJECTSXX": "1",
  ```
- `environment` · survived · string: lower case · `41341ba4a989671a`
  ```diff
  -"GIT_NO_REPLACE_OBJECTS": "1",
  +"git_no_replace_objects": "1",
  ```
- `environment` · survived · string: XX added · `a439b5b047d9ebb7`
  ```diff
  -"GIT_NO_REPLACE_OBJECTS": "1",
  +"GIT_NO_REPLACE_OBJECTS": "XX1XX",
  ```
- `environment` · survived · string: XX added · `5781fd51f30d0b44`
  ```diff
  -"GIT_GRAFT_FILE": os.devnull,
  +"XXGIT_GRAFT_FILEXX": os.devnull,
  ```
- `environment` · survived · string: lower case · `adfbbf85fee1848d`
  ```diff
  -"GIT_GRAFT_FILE": os.devnull,
  +"git_graft_file": os.devnull,
  ```
- `environment` · survived · string: XX added · `73011cb5bc8b0289`
  ```diff
  -found["GIT_COMMITTER_NAME"] = "lovspor-simulated-remote"
  +found["XXGIT_COMMITTER_NAMEXX"] = "lovspor-simulated-remote"
  ```
- `environment` · survived · string: lower case · `30028e86352e5235`
  ```diff
  -found["GIT_COMMITTER_NAME"] = "lovspor-simulated-remote"
  +found["git_committer_name"] = "lovspor-simulated-remote"
  ```
- `environment` · survived · string: XX added · `b47a3d125290b4dd`
  ```diff
  -found["GIT_COMMITTER_NAME"] = "lovspor-simulated-remote"
  +found["GIT_COMMITTER_NAME"] = "XXlovspor-simulated-remoteXX"
  ```
- `environment` · survived · string: upper case · `055c524d8ac32702`
  ```diff
  -found["GIT_COMMITTER_NAME"] = "lovspor-simulated-remote"
  +found["GIT_COMMITTER_NAME"] = "LOVSPOR-SIMULATED-REMOTE"
  ```
- `environment` · survived · string: XX added · `319055bba3109e6c`
  ```diff
  -found["GIT_COMMITTER_EMAIL"] = "remote@lovspor.invalid"
  +found["XXGIT_COMMITTER_EMAILXX"] = "remote@lovspor.invalid"
  ```
- `environment` · survived · string: lower case · `112fc5ad580d028b`
  ```diff
  -found["GIT_COMMITTER_EMAIL"] = "remote@lovspor.invalid"
  +found["git_committer_email"] = "remote@lovspor.invalid"
  ```
- `environment` · survived · string: XX added · `6bd1122fe2c86e5f`
  ```diff
  -found["GIT_COMMITTER_EMAIL"] = "remote@lovspor.invalid"
  +found["GIT_COMMITTER_EMAIL"] = "XXremote@lovspor.invalidXX"
  ```
- `environment` · survived · string: upper case · `defb51c10d3190bd`
  ```diff
  -found["GIT_COMMITTER_EMAIL"] = "remote@lovspor.invalid"
  +found["GIT_COMMITTER_EMAIL"] = "REMOTE@LOVSPOR.INVALID"
  ```
- `run_git` · survived · argument: replaced by None · `c6b199b221d92c86`
  ```diff
  -raise GitPublishError(f"git argument vector not allowed: {argv[:1]}")
  +raise GitPublishError(None)
  ```
- `run_git` · survived · number · `74f660d10b8f6db1`
  ```diff
  -raise GitPublishError(f"git argument vector not allowed: {argv[:1]}")
  +raise GitPublishError(f"git argument vector not allowed: {argv[:2]}")
  ```
- `run_git` · survived · argument: replaced by None · `d0b2e4cf5f860c8c`
  ```diff
  -raise GitPublishError("the index file lives in the clone's .git")
  +raise GitPublishError(None)
  ```
- `run_git` · survived · string: XX added · `bdd7a761241896bc`
  ```diff
  -raise GitPublishError("the index file lives in the clone's .git")
  +raise GitPublishError("XXthe index file lives in the clone's .gitXX")
  ```
- `run_git` · survived · string: upper case · `4efbedcf393165d3`
  ```diff
  -raise GitPublishError("the index file lives in the clone's .git")
  +raise GitPublishError("THE INDEX FILE LIVES IN THE CLONE'S .GIT")
  ```
- `run_git` · survived · argument: replaced by None · `0e6fc7ba8f6a4a34`
  ```diff
  -timeout=TIMEOUT_SECONDS,
  +timeout=None,
  ```
- `run_git` · survived · argument: replaced by None · `36bbf34c6b2f20d0`
  ```diff
  -check=False,
  +check=None,
  ```
- `run_git` · survived · argument: removed · `30c25a3b87eb3db9`
  ```diff
  -timeout=TIMEOUT_SECONDS,
  ```
- `run_git` · survived · argument: removed · `531464fe33d48586`
  ```diff
  -check=False,
  ```
- `run_git` · survived · ternary: condition forced · `ac9db8ce63c6f349`
  ```diff
  -input=stdin if stdin is not None else b"",
  +input=stdin if (stdin is not None) or True else b"",
  ```
- `run_git` · survived · string: XX added · `c59890b9ae929437`
  ```diff
  -input=stdin if stdin is not None else b"",
  +input=stdin if stdin is not None else b"XXXX",
  ```
- `run_git` · survived · argument: replaced by None · `0b48dc3c8a3af9f7`
  ```diff
  -raise GitPublishError(f"git {argv[0]} could not run") from error
  +raise GitPublishError(None) from error
  ```
- `run_git` · survived · number · `efbaa6c9aeca5035`
  ```diff
  -raise GitPublishError(f"git {argv[0]} could not run") from error
  +raise GitPublishError(f"git {argv[1]} could not run") from error
  ```

</details>

<details><summary><code>src/lovspor/history.py</code>: 3</summary>

- `extract_earlier_lineage` · survived · operator: and / or · `756797c40525c782`
  ```diff
  -if from_path and to_path:
  +if from_path or to_path:
  ```
- `extract_earlier_lineage` · survived · assignment: value to None · `17d4fa08415ce34e`
  ```diff
  -path = from_path
  +path = None
  ```
- `_walk` · survived · operator: and / or · `cd02f570d467c2a2`
  ```diff
  -if from_path and to_path:
  +if from_path or to_path:
  ```

</details>

<details><summary><code>src/lovspor/identity/history.py</code>: 5</summary>

- `_to_preserve` · survived · argument: replaced by None · `c14125b32a09731b`
  ```diff
  -steps = sorted([*own, *steps], key=lambda step: step.event.date, reverse=True)
  +steps = sorted([*own, *steps], key=lambda step: step.event.date, reverse=None)
  ```
- `_to_preserve` · survived · argument: removed · `f6dc22d53abca77f`
  ```diff
  -steps = sorted([*own, *steps], key=lambda step: step.event.date, reverse=True)
  +steps = sorted([*own, *steps], key=lambda step: step.event.date, )
  ```
- `_to_preserve` · survived · True / False swapped · `af18fed0b91b9c5c`
  ```diff
  -steps = sorted([*own, *steps], key=lambda step: step.event.date, reverse=True)
  +steps = sorted([*own, *steps], key=lambda step: step.event.date, reverse=False)
  ```
- `_displaced_lineage` · survived · argument: replaced by None · `bddf798ebda9bd3a`
  ```diff
  -prior_id = HistoryRecord.model_validate(json.loads(path.read_text(encoding="utf-8"))).doc_id
  +prior_id = HistoryRecord.model_validate(json.loads(path.read_text(encoding=None))).doc_id
  ```
- `_displaced_lineage` · survived · string: upper case · `76555bc64be9e6e1`
  ```diff
  -prior_id = HistoryRecord.model_validate(json.loads(path.read_text(encoding="utf-8"))).doc_id
  +prior_id = HistoryRecord.model_validate(json.loads(path.read_text(encoding="UTF-8"))).doc_id
  ```

</details>

<details><summary><code>src/lovspor/sync/orchestrator.py</code>: 3</summary>

- `_commit_history_followup` · survived · argument: replaced by None · `ff360e59f3e20db4`
  ```diff
  -if has_staged_changes(repo):
  +if has_staged_changes(None):
  ```
- `_commit_history_followup` · survived · operator: comparison · `ead549b0fc75e805`
  ```diff
  -documents = sum(p.parent.name == "history" and p.suffix == ".json" for p in history_paths)
  +documents = sum(p.parent.name == "history" and p.suffix != ".json" for p in history_paths)
  ```
- `_generate_and_apply_history` · survived · break / continue swapped · `1ce60683b03bbecd`
  ```diff
  -continue
  +break
  ```

</details>

## The register after this night

- **3344** open in 104 file(s); this night 586 new, 1 left, 0 gone with their function

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
