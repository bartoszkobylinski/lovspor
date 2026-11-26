# Nightly mutation, 2026-11-27

- Verdict: **tool_failure** (not every mutant in the range was measured)
- Range: `72c39d283c93e509d41e65d59e1597779055568c...5c5508ed0aaf20ade60f0f38ff915c9e1f0f5da5`
- Run: https://github.com/bartoszkobylinski/lovspor/actions/runs/45911300947
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

## Claimed killed by a commit in this range (333)

| key | function | commit | this night found |
|---|---:|---:|---:|
| `009d684b3ff5747e` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `01f36a2cbe89a491` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `029745c83964eb60` | `src/lovspor/git_prepare.py` `_absolute` | `9b2e5f0f` | not decided: the night did not measure its function |
| `02d5c5c4e23b7bba` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `03076d3f55ea5401` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `03c0970192993ee4` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `03c1ed59b68fb59f` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `055c524d8ac32702` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `059e45e19f0edc52` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `07bde7248a82f723` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `07c459069ad42437` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `07c459069ad42437-2` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `07c459069ad42437-3` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `07c459069ad42437-4` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `07c459069ad42437-5` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `0b48dc3c8a3af9f7` | `src/lovspor/git_publish.py` `run_git` | `d4093a33` | not decided: the night did not measure its function |
| `0bcc858ca0eb760f` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `0c750075a32e9cea` | `src/lovspor/git_prepare.py` `_verified` | `9b2e5f0f` | not decided: the night did not measure its function |
| `0db41afdeeb67bcd` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `0e50dfd3b3569744` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `0e6fc7ba8f6a4a34` | `src/lovspor/git_publish.py` `run_git` | `d4093a33` | not decided: the night did not measure its function |
| `0f902945fa6a519b` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `0fd4efde4dd92012` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `101edebb077dea62` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `112fc5ad580d028b` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `119edfce682b7e48` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `14ab16c88e3376cf` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `14cf23d631f415bb` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `14f17e263792cd80` | `src/lovspor/forarbeider/release_lock.py` `held_by_us` | `b3bbc07b` | not decided: the night did not measure its function |
| `1520abfcf2d66979` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `16b0a58f2949a234` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `16e45d428d7715d7` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `171668280a74709b` | `src/lovspor/publication_seal.py` `_verify_tag` | `4c48452a` | not decided: the night did not measure its function |
| `171f7cf9bd4715de` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `1930aef34b1361e2` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `19baa98c85c357b6` | `src/lovspor/publication_seal.py` `_verify_signature` | `4c48452a` | not decided: the night did not measure its function |
| `19f0024e1d3183a7` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `1bd3e1be3db03880` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `1c343be2e427683d` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `1c4ddbd14fe47931` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `1d5b398efbb57618` | `src/lovspor/publication_seal.py` `_sync_tags` | `4c48452a` | not decided: the night did not measure its function |
| `1de162e635913d86` | `src/lovspor/publication_seal.py` `_tag_object` | `4c48452a` | not decided: the night did not measure its function |
| `1ecb2a9ccd60dd6e` | `src/lovspor/publication_seal.py` `_verify_tag` | `4c48452a` | not decided: the night did not measure its function |
| `1f861460589219a6` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `1f89b7ddc36f7217` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `1fab6523cd26e706` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `20370b837c8a5ae1` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `2051e62ab3f5b428` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `219901c43f77901d` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `226d7211f1898d6b` | `src/lovspor/regjeringen_fetch/normalize.py` `_value_end` | `9592c67d` | not decided: the night did not measure its function |
| `228a7c0017df7024` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `22ae90914aabe0a0` | `src/lovspor/identity/report.py` `_threshold_exceptions` | `37f17f1f` | not decided: the night did not measure its function |
| `233dc967eddf7e27` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `2371565671efda73` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `250fef3f8eb78130` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `2521b02be4fae599` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `2705656859e28318` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `27b934313e4e2836` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `285cc8fe77c6fab4` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `299d856399b64675` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `2a7f501327c5e36b` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `2ad3aec30a0fb9a5` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `2e9ca2e68592b035` | `src/lovspor/publication_seal.py` `_parse_seal` | `4c48452a` | not decided: the night did not measure its function |
| `2ff50cf009ade23d` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `30028e86352e5235` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `305dcd4dc799dfff` | `src/lovspor/git_publish.py` `_local_bare` | `d4093a33` | not decided: the night did not measure its function |
| `30c25a3b87eb3db9` | `src/lovspor/git_publish.py` `run_git` | `d4093a33` | not decided: the night did not measure its function |
| `319055bba3109e6c` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `31dfcd1a6d65cddc` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `31ebb0ab86309952` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `320e6e67829fa075` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `335709e475fd4949` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `34495538f4827dc1` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `345edba361389e4a` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `35ba64b9c8ec73fb` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `367063bb7678b44a` | `src/lovspor/regjeringen_fetch/normalize.py` `_parse_attribute` | `9592c67d` | not decided: the night did not measure its function |
| `37f4447d69167d8b` | `src/lovspor/git_publish.py` `_local_bare` | `d4093a33` | not decided: the night did not measure its function |
| `3819576f178ea41f` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `3832b1bf746207ca` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `38a597dd82d00854` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `390bdec7197b0245` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `3b17d6c7b3387d17` | `src/lovspor/publication_seal.py` `_verify_signature` | `4c48452a` | not decided: the night did not measure its function |
| `3cc3d807fdabbd19` | `src/lovspor/regjeringen_fetch/normalize.py` `_value_end` | `9592c67d` | not decided: the night did not measure its function |
| `3f2ca02600c700e2` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `3f6222808ae3d8d7` | `src/lovspor/publication_seal.py` `_verify_signature` | `4c48452a` | not decided: the night did not measure its function |
| `4061b001563125cb` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `408fc75bdd3a017b` | `src/lovspor/identity/report.py` `_cell` | `ede4f9c3` | not decided: the night did not measure its function |
| `40a5518fe61792fd` | `src/lovspor/identity/report.py` `_review_lists` | `c520ebfa` | not decided: the night did not measure its function |
| `41341ba4a989671a` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `41dcaafa77e91d16` | `src/lovspor/forarbeider/release_lock.py` `_at_name` | `b3bbc07b` | not decided: the night did not measure its function |
| `4262b6ea19c029d0` | `src/lovspor/publication_seal.py` `_parse_seal` | `4c48452a` | not decided: the night did not measure its function |
| `4276a8401adba600` | `src/lovspor/identity/report.py` `manifest_json` | `37f17f1f` | not decided: the night did not measure its function |
| `42bf3864ac57331e` | `src/lovspor/identity/report.py` `manifest_json` | `37f17f1f` | not decided: the night did not measure its function |
| `42eef26060232799` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `4437bbc73085f321` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `4490de5e428cafa4` | `src/lovspor/regjeringen_fetch/normalize.py` `_value_end` | `9592c67d` | not decided: the night did not measure its function |
| `44c07b246249ab02` | `src/lovspor/publication_seal.py` `verify_seal` | `4c48452a` | not decided: the night did not measure its function |
| `44d5b0f8e37e9ecb` | `src/lovspor/forarbeider/release_lock.py` `_claim` | `b3bbc07b` | not decided: the night did not measure its function |
| `46ad2c5d895672f3` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `48716b12a051ff5c` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `4886772aebd64d29` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `4a189ef6220f57d6` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `4a8cf39e97dbc0b7` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `4ac5ea2f6cbf1eca` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `4bfde39c9b98b8c1` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `4c0c62eb0f4b3e56` | `src/lovspor/publication_seal.py` `_verify_signature` | `4c48452a` | not decided: the night did not measure its function |
| `4ccad327bf228271` | `src/lovspor/publication_seal.py` `_git_out` | `b5a5086f` | not decided: the night did not measure its function |
| `4df0306fe97f5b4c` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `4efbedcf393165d3` | `src/lovspor/git_publish.py` `run_git` | `d4093a33` | not decided: the night did not measure its function |
| `501490e8f192436d` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `5022d3f30a03e937` | `src/lovspor/identity/report.py` `report_files` | `37f17f1f` | not decided: the night did not measure its function |
| `5214086de926bfc3` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `52b48872d476a432` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `53245f06f03d996e` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `536fbf0cc091509f` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `54e6274fbba2a869` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `551feb113c309ffa` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `556ba3177c2e0402` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `5781fd51f30d0b44` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `58aa9ca145121f42` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `58d98a8654f40317` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `59b3d5ea936591c9` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `59d0fb7f44afad6a` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `5a29a0cfcb518bd4` | `src/lovspor/publication_seal.py` `_verify_signature` | `4c48452a` | not decided: the night did not measure its function |
| `5a6df7989da70b4a` | `src/lovspor/regjeringen_fetch/normalize.py` `_value_end` | `9592c67d` | not decided: the night did not measure its function |
| `5b2672c3cd8771bd` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `5d9c868185c10f56` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `5dc11ce97f5eebb6` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `60cbee12b6265cd8` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `60ff35c3311268d7` | `src/lovspor/regjeringen_fetch/normalize.py` `_parse_tag` | `9592c67d` | not decided: the night did not measure its function |
| `61b732853c8c5d39` | `src/lovspor/identity/report.py` `_threshold_exceptions` | `37f17f1f` | not decided: the night did not measure its function |
| `63b98c64976104f1` | `src/lovspor/publication_seal.py` `_tag_object` | `4c48452a` | not decided: the night did not measure its function |
| `65c021fe5ec04bf9` | `src/lovspor/git_prepare.py` `run_git` | `9b2e5f0f` | not decided: the night did not measure its function |
| `67b7d12df45ec2ce` | `src/lovspor/regjeringen_fetch/normalize.py` `_value_end` | `9592c67d` | not decided: the night did not measure its function |
| `67cbe34abb429b07` | `src/lovspor/publication_seal.py` `_git_out` | `b5a5086f` | not decided: the night did not measure its function |
| `67fddbffb6c0f168` | `src/lovspor/publication_seal.py` `_parse_seal` | `4c48452a` | not decided: the night did not measure its function |
| `6821ac2f78d58f1f` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `6992b511ff2e4765` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `6a21c46b0279a736` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `6a486cb117060d69` | `src/lovspor/identity/report.py` `report_files` | `37f17f1f` | not decided: the night did not measure its function |
| `6a6748b4ad3416eb` | `src/lovspor/publication_seal.py` `_resolve_commit` | `b5a5086f` | not decided: the night did not measure its function |
| `6bab321abe241db4` | `src/lovspor/publication_seal.py` `_sync_tags` | `4c48452a` | not decided: the night did not measure its function |
| `6bd1122fe2c86e5f` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `6c2815ef5a826b00` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `6da5acfe344abf46` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `6e17c6fc03646def` | `src/lovspor/regjeringen_fetch/normalize.py` `_parse_tag` | `9592c67d` | not decided: the night did not measure its function |
| `6f74a83e4d555e96` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `7299b4ceca939d67` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `73011cb5bc8b0289` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `733c6138f5ec07fb` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `74078f43d4e85efe` | `src/lovspor/regjeringen_fetch/normalize.py` `_start_tags` | `bb3f9253` | not decided: the night did not measure its function |
| `74c1ba4f5e1b8662` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `74f660d10b8f6db1` | `src/lovspor/git_publish.py` `run_git` | `d4093a33` | not decided: the night did not measure its function |
| `751371578178d606` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `7568457e94f44c7a` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `75923e427f89cfcb` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `762d14dae31e6f11` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `78756f622b4276fc` | `src/lovspor/identity/report.py` `_identity_schema_lines` | `37f17f1f` | not decided: the night did not measure its function |
| `78756f622b4276fc-2` | `src/lovspor/identity/report.py` `_identity_schema_lines` | `37f17f1f` | not decided: the night did not measure its function |
| `78756f622b4276fc-3` | `src/lovspor/identity/report.py` `_identity_schema_lines` | `37f17f1f` | not decided: the night did not measure its function |
| `7ab2052f01b4400b` | `src/lovspor/identity/report.py` `_identity_schema_lines` | `37f17f1f` | not decided: the night did not measure its function |
| `7ae3620b587f9c33` | `src/lovspor/identity/report.py` `_cell` | `ede4f9c3` | not decided: the night did not measure its function |
| `7b1c122b6eed74f0` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `7b4b1343d24de603` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `7b8478bd8df9d53e` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `7c50fd5a34dc227f` | `src/lovspor/identity/report.py` `report_files` | `37f17f1f` | not decided: the night did not measure its function |
| `7df9e3bc857839b4` | `src/lovspor/git_prepare.py` `run_git` | `9b2e5f0f` | not decided: the night did not measure its function |
| `7e61576afd06d05e` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `814021cd7da8532b` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `8166b900c7a90f32` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `81f66ff23e43c2be` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `824a3a3873952d7f` | `src/lovspor/regjeringen_fetch/normalize.py` `_after_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `82ccb6219aef429e` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `83455c2ab1bfbcb5` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `83b12c5da44ed04b` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `83c143951aa8056b` | `src/lovspor/identity/report.py` `_threshold_exceptions` | `37f17f1f` | not decided: the night did not measure its function |
| `8499cb7cbe27e9c8` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `86374b3f8c3b21ac` | `src/lovspor/identity/report.py` `report_files` | `37f17f1f` | not decided: the night did not measure its function |
| `865bf52649ee9881` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `872825c73c45aa62` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `8788647a0fe62889` | `src/lovspor/identity/report.py` `_threshold_exceptions` | `37f17f1f` | not decided: the night did not measure its function |
| `87e0d1a42237c2ca` | `src/lovspor/regjeringen_fetch/normalize.py` `_value_end` | `9592c67d` | not decided: the night did not measure its function |
| `881b5ebcc945214c` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `88324fe1cc07561b` | `src/lovspor/regjeringen_fetch/normalize.py` `_value_end` | `9592c67d` | not decided: the night did not measure its function |
| `8ab4f2d47ebe53d6` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `8ae89858e2dc25a3` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `8af4aaa0d2893624` | `src/lovspor/identity/report.py` `_identity_fields` | `37f17f1f` | not decided: the night did not measure its function |
| `8d1a6c61fc5bb58c` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `8d34f638ccd21ddd` | `src/lovspor/identity/report.py` `report_files` | `37f17f1f` | not decided: the night did not measure its function |
| `8d8f2b9088f0f861` | `src/lovspor/publication_seal.py` `_tag_object` | `4c48452a` | not decided: the night did not measure its function |
| `8f2d2976f8e55b26` | `src/lovspor/identity/report.py` `_review_lists` | `c520ebfa` | not decided: the night did not measure its function |
| `90b8c4ed3c98a43a` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `914a4144a260303a` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `921ce7a2098f8900` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `9224dc9d7b735afc` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `92f9ef86da004151` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `949ff1a5eb12d92f` | `src/lovspor/publication_seal.py` `_require_signers` | `4c48452a` | not decided: the night did not measure its function |
| `95078855cdfad10d` | `src/lovspor/identity/report.py` `report_files` | `37f17f1f` | not decided: the night did not measure its function |
| `9517fa5a14e32d44` | `src/lovspor/publication_seal.py` `_parse_seal` | `4c48452a` | not decided: the night did not measure its function |
| `96e3856b4e491929` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `9703966f6ea97ece` | `src/lovspor/publication_seal.py` `_git_out` | `b5a5086f` | not decided: the night did not measure its function |
| `98a541d529356ce9` | `src/lovspor/publication_seal.py` `_tag_object` | `4c48452a` | not decided: the night did not measure its function |
| `98d70348b9a76ac9` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `99548133248555e7` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `9aa018ccb8b619c6` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `9c182e6b11eacd12` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `9dc91cdf0ab56dbc` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `a2b39bd0458325eb` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `a2fb1dfcaca0f266` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `a359110a5a47da83` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `a3f7ef52e7358a33` | `src/lovspor/forarbeider/release_lock.py` `_claim` | `b3bbc07b` | not decided: the night did not measure its function |
| `a439b5b047d9ebb7` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `a60bf1baa65168ce` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `a75cb8c8bfaebff0` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `a7acab18e923f5a1` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `a7beb74c2e68e8e5` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `a9145b68481fd468` | `src/lovspor/git_prepare.py` `_verified` | `9b2e5f0f` | not decided: the night did not measure its function |
| `aa62853fe8cad2ac` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `aaa7e33100e2a928` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `ac9db8ce63c6f349` | `src/lovspor/git_publish.py` `run_git` | `d4093a33` | not decided: the night did not measure its function |
| `ad4886d17e4f518e` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `ad4ee8b7a338e3ae` | `src/lovspor/regjeringen_fetch/normalize.py` `_value_end` | `9592c67d` | not decided: the night did not measure its function |
| `adc8342ec63c5007` | `src/lovspor/publication_seal.py` `_git_out` | `b5a5086f` | not decided: the night did not measure its function |
| `adfbbf85fee1848d` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `ae2686d6a5351992` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `ae5c4368a568d22d` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `aed76b2226ab3f8e` | `src/lovspor/publication_seal.py` `compose_seal` | `833ad2e2` | not decided: the night did not measure its function |
| `afa61bcb6c17b007` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `afd8bdd20a5e42db` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `b06d1688e723f1d8` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `b0887b57001ff923` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `b1499b8b9fbce485` | `src/lovspor/identity/report.py` `_threshold_exceptions` | `37f17f1f` | not decided: the night did not measure its function |
| `b1b2d1f354755e1b` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `b2342c386ce8b0f8` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `b2e1195e0dd99f88` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `b318959fb16911ac` | `src/lovspor/forarbeider/release_lock.py` `_claim` | `b3bbc07b` | not decided: the night did not measure its function |
| `b37d1f0ba6195f2b` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `b432a995c9607c40` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `b47a3d125290b4dd` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `b47d87c2d4d55343` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `b56413ca19bad17b` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `b5f462da31fbc4b9` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `b778425eb23fc82e` | `src/lovspor/identity/report.py` `_cell` | `ede4f9c3` | not decided: the night did not measure its function |
| `b8cfe926eafc0240` | `src/lovspor/git_prepare.py` `_verified` | `9b2e5f0f` | not decided: the night did not measure its function |
| `b8e3f9afce0aef34` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `b91b1bea53eca389` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `ba798959db4c860a` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `bc5d3756d0557e38` | `src/lovspor/regjeringen_fetch/normalize.py` `_parse_attribute` | `9592c67d` | not decided: the night did not measure its function |
| `bd69a23b4a127d25` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `bd797b31eac87679` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `bdd11df1fa3d352d` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `be1dee98a739d9cb` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `beef621cd6326f39` | `src/lovspor/regjeringen_fetch/normalize.py` `_parse_tag` | `9592c67d` | not decided: the night did not measure its function |
| `c16ed0529aae4a81` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `c2536b29631013ac` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `c27e7523be83b23f` | `src/lovspor/regjeringen_fetch/normalize.py` `_value_end` | `9592c67d` | not decided: the night did not measure its function |
| `c2d646c8d9d9295d` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `c35e1b9550a87d6b` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `c48a774ba9ef4c63` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `c4f5393228bafa67` | `src/lovspor/forarbeider/release_lock.py` `_at_name` | `b3bbc07b` | not decided: the night did not measure its function |
| `c574d91ebc1c1dfd` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `c6012bae2113d63c` | `src/lovspor/identity/report.py` `_identity_fields` | `37f17f1f` | not decided: the night did not measure its function |
| `c6692a577f8d61d1` | `src/lovspor/identity/report.py` `_identity_fields` | `37f17f1f` | not decided: the night did not measure its function |
| `c66f8cd0faeba885` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `c6b199b221d92c86` | `src/lovspor/git_publish.py` `run_git` | `d4093a33` | not decided: the night did not measure its function |
| `c76bc39eab4b8abd` | `src/lovspor/regjeringen_fetch/normalize.py` `_value_end` | `9592c67d` | not decided: the night did not measure its function |
| `c860ad70215dc247` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `ca761ae8ddff272e` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `cad7f91c3c85da74` | `src/lovspor/regjeringen_fetch/normalize.py` `_unquoted` | `9592c67d` | not decided: the night did not measure its function |
| `caf4c5d2758504fe` | `src/lovspor/git_prepare.py` `git_environment` | `9b2e5f0f` | not decided: the night did not measure its function |
| `cb8fd568787d2d6a` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `cc21f99cc5f914a8` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `ccb21da0e0162148` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `cd28fc23204f960f` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `cd6f86e12bd55ebd` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `ce29bdffb46de307` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `cf835b8e398613e4` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `d0ac7fff21c86e12` | `src/lovspor/publication_seal.py` `_tag_object` | `4c48452a` | not decided: the night did not measure its function |
| `d0b2e4cf5f860c8c` | `src/lovspor/git_publish.py` `run_git` | `d4093a33` | not decided: the night did not measure its function |
| `d0b9739e52f910b2` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `d15a76143a513a15` | `src/lovspor/regjeringen_fetch/normalize.py` `_value_end` | `9592c67d` | not decided: the night did not measure its function |
| `d426cf83674c1786` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `d4866ac19261c8f8` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `d4b3acb7d0d96e33` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `d4d5ad2fc04859c4` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `d6ac2a4dd44ddf87` | `src/lovspor/publication_seal.py` `_parse_seal` | `4c48452a` | not decided: the night did not measure its function |
| `d6fc6952db0a4399` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `d719c47ce3d7dfbb` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `d8c5282794695fae` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `dac331243286cff8` | `src/lovspor/git_prepare.py` `_verified` | `9b2e5f0f` | not decided: the night did not measure its function |
| `dc032ce152dac940` | `src/lovspor/forarbeider/release_lock.py` `_claim` | `b3bbc07b` | not decided: the night did not measure its function |
| `dc877f8b780dc3d8` | `src/lovspor/git_publish.py` `_local_bare` | `d4093a33` | not decided: the night did not measure its function |
| `dceb69e66dcd8d90` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `dcf1e2b4abb62bd6` | `src/lovspor/git_prepare.py` `_verified` | `9b2e5f0f` | not decided: the night did not measure its function |
| `defb51c10d3190bd` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `deffc95accf6eb0d` | `src/lovspor/git_prepare.py` `run_git` | `9b2e5f0f` | not decided: the night did not measure its function |
| `df9ba927c632815e` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `e0175097d3da95c0` | `src/lovspor/regjeringen_fetch/normalize.py` `_value_end` | `9592c67d` | not decided: the night did not measure its function |
| `e16d8c655c871e45` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `e2292bd6033f3324` | `src/lovspor/identity/report.py` `report_files` | `37f17f1f` | not decided: the night did not measure its function |
| `e27b98b32996b3ed` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `e50d18f2f36809f6` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `e6a3d280d1df0c1d` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `e8dba390148ba706` | `src/lovspor/forarbeider/release_lock.py` `_claim` | `b3bbc07b` | not decided: the night did not measure its function |
| `e927a3045a48d94a` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `ea41339a36e1409f` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `eb00549be63a99e6` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `eb46a694b80c4b3e` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `ebee3ee03a1abedf` | `src/lovspor/publication_seal.py` `_git_out` | `b5a5086f` | not decided: the night did not measure its function |
| `ecf68d0a6dce265a` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `ee1d88374066d295` | `src/lovspor/identity/report.py` `report_files` | `37f17f1f` | not decided: the night did not measure its function |
| `ee3812e4534ee1a9` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `ee4e8c2d7788106c` | `src/lovspor/identity/report.py` `_record_counts` | `25090495` | not decided: the night did not measure its function |
| `ee7278874c898bc4` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |
| `ef2638358514c9bb` | `src/lovspor/publication_seal.py` `_verify_tag` | `4c48452a` | not decided: the night did not measure its function |
| `ef4a6091dd0ec187` | `src/lovspor/publication_seal.py` `_verify_signature` | `4c48452a` | not decided: the night did not measure its function |
| `efbaa6c9aeca5035` | `src/lovspor/git_publish.py` `run_git` | `d4093a33` | not decided: the night did not measure its function |
| `f00ffd564f01dd2d` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `f122afbff075d49a` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `f2f7150162f4fcbd` | `src/lovspor/regjeringen_fetch/normalize.py` `_skip_non_start_tag` | `bb3f9253` | not decided: the night did not measure its function |
| `f333115f58ac29bd` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `f41d26efb4afc283` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `f43cf69074e29de3` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `f4ccff08cd34359b` | `src/lovspor/publication_seal.py` `_verify_signature` | `4c48452a` | not decided: the night did not measure its function |
| `f6c7e2911840df76` | `src/lovspor/identity/report.py` `render_markdown` | `b439856b` | not decided: the night did not measure its function |
| `f7441b6638483c85` | `src/lovspor/identity/report.py` `_slug_counts` | `25090495` | not decided: the night did not measure its function |
| `f78c8b599c0284db` | `src/lovspor/git_publish.py` `environment` | `8668d8cd` | not decided: the night did not measure its function |
| `f7cf82e41049bce6` | `src/lovspor/publication_seal.py` `_verify_signature` | `4c48452a` | not decided: the night did not measure its function |
| `f889e9b3221189e7` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `f89e2e31e3c793cb` | `src/lovspor/identity/report.py` `_holder_text` | `ede4f9c3` | not decided: the night did not measure its function |
| `fa2d7c6e38c30ace` | `src/lovspor/identity/report.py` `_table` | `ede4f9c3` | not decided: the night did not measure its function |
| `fcaee6b1ee342c79` | `src/lovspor/identity/report.py` `_header` | `b7331cf0` | not decided: the night did not measure its function |
| `fcde30999f2e2d24` | `src/lovspor/identity/report.py` `report_data` | `c520ebfa` | not decided: the night did not measure its function |

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
