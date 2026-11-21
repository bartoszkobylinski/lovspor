# Nightly mutation, 2026-11-21

- Verdict: **survivors**
- Range: `24f16450e135070a7a35cc74181626e0c7489bfd...10302f0a25e07346458b6a663667ac879123584a`
- Run: https://github.com/bartoszkobylinski/lovspor/actions/runs/44837229442
- Written by: seed: the artifact of run 44837229442, merged outside the lane on 2026-11-21; the kind of each mutant was rebuilt from its diff, the night did not record it
- Counts: `{"invalid": 0, "killed": 3115, "no_tests": 25, "skipped": 0, "survived": 605, "timeout": 0, "total": 3745, "unmeasured": 0}`, score 83.18

## By kind

Not recorded: the night has no `mutation-night.json`.

## Not killed this night (605)

<details><summary><code>src/lovspor/forarbeider/publication_clone.py</code>: 37</summary>

- `_git` · survived · argument: replaced by None · `d5ad04ff36c33ae1`
  ```diff
  -raise _refused(argv[0]) from error
  +raise _refused(None) from error
  ```
- `_git` · survived · number · `d1bc7f558ba7ac41`
  ```diff
  -raise _refused(argv[0]) from error
  +raise _refused(argv[1]) from error
  ```
- `_git` · survived · argument: replaced by None · `08a7093fc48b28e8`
  ```diff
  -raise _refused(argv[0])
  +raise _refused(None)
  ```
- `_git` · survived · number · `146b35b227d7fffd`
  ```diff
  -raise _refused(argv[0])
  +raise _refused(argv[1])
  ```
- `_commit_of` · survived · argument: replaced by None · `b6d55de88d0aeb14`
  ```diff
  -raise _refused("rev-parse") from error
  +raise _refused(None) from error
  ```
- `_commit_of` · survived · string: XX added · `fe4e73fa028d6927`
  ```diff
  -raise _refused("rev-parse") from error
  +raise _refused("XXrev-parseXX") from error
  ```
- `_commit_of` · survived · string: upper case · `8b825fdd8533152d`
  ```diff
  -raise _refused("rev-parse") from error
  +raise _refused("REV-PARSE") from error
  ```
- `_commit_of` · survived · argument: removed · `e6daea32077f7058`
  ```diff
  -return answer.out.decode("ascii", "replace").strip()
  +return answer.out.decode("ascii", ).strip()
  ```
- `_commit_of` · survived · string: upper case · `5fddfa395018ecbe`
  ```diff
  -return answer.out.decode("ascii", "replace").strip()
  +return answer.out.decode("ASCII", "replace").strip()
  ```
- `_commit_of` · survived · string: XX added · `9fa5aee13fba0c92`
  ```diff
  -return answer.out.decode("ascii", "replace").strip()
  +return answer.out.decode("ascii", "XXreplaceXX").strip()
  ```
- `_commit_of` · survived · string: upper case · `1aa21098dafbb9a5`
  ```diff
  -return answer.out.decode("ascii", "replace").strip()
  +return answer.out.decode("ascii", "REPLACE").strip()
  ```
- `work_directory` · survived · operator: and / or · `269308a3d635935c`
  ```diff
  -if resolved == work or resolved.is_relative_to(work) or work.is_relative_to(resolved):
  +if resolved == work or resolved.is_relative_to(work) and work.is_relative_to(resolved):
  ```
- `work_directory` · survived · operator: and / or · `481adcea46811588`
  ```diff
  -if resolved == work or resolved.is_relative_to(work) or work.is_relative_to(resolved):
  +if resolved == work and resolved.is_relative_to(work) or work.is_relative_to(resolved):
  ```
- `work_directory` · survived · argument: replaced by None · `196db7061d5f882a`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_FORBIDDEN, "source")
  +raise ForarbeiderRefusedError(None, "source")
  ```
- `work_directory` · survived · argument: replaced by None · `6c738f3a930556a4`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_FORBIDDEN, "source")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_FORBIDDEN, None)
  ```
- `work_directory` · survived · argument: removed · `c8154f7b68418837`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_FORBIDDEN, "source")
  +raise ForarbeiderRefusedError("source")
  ```
- `work_directory` · survived · argument: removed · `144cd1faa8defdab`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_FORBIDDEN, "source")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_FORBIDDEN, )
  ```
- `work_directory` · survived · string: XX added · `0fea3e2e6f8a2ee2`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_FORBIDDEN, "source")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_FORBIDDEN, "XXsourceXX")
  ```
- `work_directory` · survived · string: upper case · `06918f1db253fc73`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_FORBIDDEN, "source")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_FORBIDDEN, "SOURCE")
  ```
- `require_clone` · survived · argument: replaced by None · `04d7e3132809e06c`
  ```diff
  -raise _refused("clone") from error
  +raise _refused(None) from error
  ```
- `require_clone` · survived · string: XX added · `76563e27d17ab308`
  ```diff
  -raise _refused("clone") from error
  +raise _refused("XXcloneXX") from error
  ```
- `require_clone` · survived · string: upper case · `b02b12185703c0b6`
  ```diff
  -raise _refused("clone") from error
  +raise _refused("CLONE") from error
  ```
- `require_clone` · survived · operator: and / or · `a340130dc16b7a34`
  ```diff
  -if not stat.S_ISDIR(status.st_mode) or os.path.lexists(clone / ".git/objects/info/alternates"):
  +if not stat.S_ISDIR(status.st_mode) and os.path.lexists(clone / ".git/objects/info/alternates"):
  ```
- `require_clone` · survived · string: XX added · `6d3799cac3254514`
  ```diff
  -if not stat.S_ISDIR(status.st_mode) or os.path.lexists(clone / ".git/objects/info/alternates"):
  +if not stat.S_ISDIR(status.st_mode) or os.path.lexists(clone / "XX.git/objects/info/alternatesXX"):
  ```
- `require_clone` · survived · string: upper case · `823d788f2793cda9`
  ```diff
  -if not stat.S_ISDIR(status.st_mode) or os.path.lexists(clone / ".git/objects/info/alternates"):
  +if not stat.S_ISDIR(status.st_mode) or os.path.lexists(clone / ".GIT/OBJECTS/INFO/ALTERNATES"):
  ```
- `require_clone` · survived · argument: replaced by None · `5d3f50d1917044c6`
  ```diff
  -raise _refused("clone")
  +raise _refused(None)
  ```
- `require_clone` · survived · string: XX added · `1c34a8984fc21da3`
  ```diff
  -raise _refused("clone")
  +raise _refused("XXcloneXX")
  ```
- `require_clone` · survived · string: upper case · `ada758d3641d219f`
  ```diff
  -raise _refused("clone")
  +raise _refused("CLONE")
  ```
- `require_clone` · survived · operator: and / or · `5076877c3404a365`
  ```diff
  -if _git(clone, ("remote",)).strip() or head_of(clone) != base_commit:
  +if _git(clone, ("remote",)).strip() and head_of(clone) != base_commit:
  ```
- `require_clone` · survived · argument: replaced by None · `5d3f50d1917044c6-2`
  ```diff
  -raise _refused("clone")
  +raise _refused(None)
  ```
- `require_clone` · survived · string: XX added · `1c34a8984fc21da3-2`
  ```diff
  -raise _refused("clone")
  +raise _refused("XXcloneXX")
  ```
- `require_clone` · survived · string: upper case · `ada758d3641d219f-2`
  ```diff
  -raise _refused("clone")
  +raise _refused("CLONE")
  ```
- `changed_paths` · survived · break / continue swapped · `e9c2127b337d80c1`
  ```diff
  -continue
  +break
  ```
- `changed_paths` · survived · string: upper case · `73f387faf8aca41c`
  ```diff
  -text = record.decode("utf-8")
  +text = record.decode("UTF-8")
  ```
- `changed_paths` · survived · argument: replaced by None · `ee14daac7f8b712b`
  ```diff
  -raise _refused("status") from error
  +raise _refused(None) from error
  ```
- `changed_paths` · survived · string: XX added · `477c0cf6cfa8c1f9`
  ```diff
  -raise _refused("status") from error
  +raise _refused("XXstatusXX") from error
  ```
- `changed_paths` · survived · string: upper case · `55d4e3af5e5de093`
  ```diff
  -raise _refused("status") from error
  +raise _refused("STATUS") from error
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_models.py</code>: 12</summary>

- `_require_documents` · survived · argument: replaced by None · `9dd457967e57f589`
  ```diff
  -raise ValueError("documents are sorted by id, once each")
  +raise ValueError(None)
  ```
- `_require_documents` · survived · string: XX added · `8a56b131afe2549c`
  ```diff
  -raise ValueError("documents are sorted by id, once each")
  +raise ValueError("XXdocuments are sorted by id, once eachXX")
  ```
- `_require_documents` · survived · string: upper case · `00f14b6ed0e13951`
  ```diff
  -raise ValueError("documents are sorted by id, once each")
  +raise ValueError("DOCUMENTS ARE SORTED BY ID, ONCE EACH")
  ```
- `_require_files_cover` · survived · argument: replaced by None · `d5ae6350a7615ba3`
  ```diff
  -"files are exactly the documents, the index, the catalogue and the manifest"
  +None
  ```
- `_require_files_cover` · survived · string: XX added · `fef9de2c431b543f`
  ```diff
  -"files are exactly the documents, the index, the catalogue and the manifest"
  +"XXfiles are exactly the documents, the index, the catalogue and the manifestXX"
  ```
- `_require_files_cover` · survived · string: upper case · `d86a2e5e9060889b`
  ```diff
  -"files are exactly the documents, the index, the catalogue and the manifest"
  +"FILES ARE EXACTLY THE DOCUMENTS, THE INDEX, THE CATALOGUE AND THE MANIFEST"
  ```
- `_require_files_cover` · survived · argument: replaced by None · `fe45410f586b3a6f`
  ```diff
  -raise ValueError("a document's file is the one rendered")
  +raise ValueError(None)
  ```
- `_require_files_cover` · survived · string: XX added · `5101a9599b2bee3f`
  ```diff
  -raise ValueError("a document's file is the one rendered")
  +raise ValueError("XXa document's file is the one renderedXX")
  ```
- `_require_files_cover` · survived · string: upper case · `415e02e7f3b300eb`
  ```diff
  -raise ValueError("a document's file is the one rendered")
  +raise ValueError("A DOCUMENT'S FILE IS THE ONE RENDERED")
  ```
- `phase_of` · survived · argument: replaced by None · `2b71c46a9a3d9efe`
  ```diff
  -raise ValueError("not a publication file")
  +raise ValueError(None)
  ```
- `phase_of` · survived · string: XX added · `1abf9a4b9d562801`
  ```diff
  -raise ValueError("not a publication file")
  +raise ValueError("XXnot a publication fileXX")
  ```
- `phase_of` · survived · string: upper case · `5d0cef5c33d8ad45`
  ```diff
  -raise ValueError("not a publication file")
  +raise ValueError("NOT A PUBLICATION FILE")
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_prepare.py</code>: 68</summary>

- `_require_quarantine` · survived · operator: and / or · `79837296eb913be5`
  ```diff
  -sealed = found.status == "verified" and authentic(found, "quarantine_assessment")
  +sealed = found.status == "verified" or authentic(found, "quarantine_assessment")
  ```
- `_document` · survived · argument: replaced by None · `bd31ad340852b4be`
  ```diff
  -raise HaltError("row_missing", "unknown", document_id)
  +raise HaltError("row_missing", "unknown", None)
  ```
- `_document` · survived · argument: removed · `1241ab0ac264ba55`
  ```diff
  -raise HaltError("row_missing", "unknown", document_id)
  +raise HaltError("row_missing", "unknown", )
  ```
- `_document` · survived · argument: replaced by None · `4fc1a3256c5c1f4c`
  ```diff
  -raise HaltError("selection_mismatch", "unknown", document_id)
  +raise HaltError("selection_mismatch", "unknown", None)
  ```
- `_document` · survived · argument: removed · `6c9e21ac2ab893b4`
  ```diff
  -raise HaltError("selection_mismatch", "unknown", document_id)
  +raise HaltError("selection_mismatch", "unknown", )
  ```
- `_document` · survived · argument: replaced by None · `47b239f40fb3f5f8`
  ```diff
  -raise HaltError("binding_mismatch", "unknown", document_id)
  +raise HaltError("binding_mismatch", "unknown", None)
  ```
- `_document` · survived · argument: removed · `bb380e2ae9bd9a05`
  ```diff
  -raise HaltError("binding_mismatch", "unknown", document_id)
  +raise HaltError("binding_mismatch", "unknown", )
  ```
- `_document` · survived · argument: replaced by None · `bda1e759368b41d6`
  ```diff
  -raise HaltError("quarantined_document_in_batch", "unknown", document_id)
  +raise HaltError("quarantined_document_in_batch", "unknown", None)
  ```
- `_document` · survived · argument: removed · `0850f99051addbbf`
  ```diff
  -raise HaltError("quarantined_document_in_batch", "unknown", document_id)
  +raise HaltError("quarantined_document_in_batch", "unknown", )
  ```
- `_checked` · survived · ternary: condition forced · `d29a08213bb7bb2b`
  ```diff
  -quarantined = {item.document_id for item in found.documents} if found else set()
  +quarantined = {item.document_id for item in found.documents} if (found) or True else set()
  ```
- `_halted` · survived · argument: replaced by None · `b2f514ebdf6dffa9`
  ```diff
  -ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id or "", item.code)))
  +ordered = tuple(sorted(set(halts), key=None))
  ```
- `_halted` · survived · argument: removed · `e2bcfa39586325b0`
  ```diff
  -ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id or "", item.code)))
  +ordered = tuple(sorted(set(halts), ))
  ```
- `_halted` · survived · lambda: body replaced · `36e36c693d9c5e98`
  ```diff
  -ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id or "", item.code)))
  +ordered = tuple(sorted(set(halts), key=lambda item: None))
  ```
- `_halted` · survived · operator: and / or · `d627ae0fcea1db98`
  ```diff
  -ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id or "", item.code)))
  +ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id and "", item.code)))
  ```
- `_halted` · survived · string: XX added · `9ac5d447e30e5d2f`
  ```diff
  -ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id or "", item.code)))
  +ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id or "XXXX", item.code)))
  ```
- `_halted` · survived · assignment: value to None · `32ae0ab14c0bb886`
  ```diff
  -plan = request.plan.plan
  +plan = None
  ```
- `_halted` · survived · argument: replaced by None · `490892e42bc09082`
  ```diff
  -plan_id=plan.plan_id if plan else None,
  +plan_id=None,
  ```
- `_halted` · survived · ternary: condition forced · `9be9eb4de672eb52`
  ```diff
  -plan_id=plan.plan_id if plan else None,
  +plan_id=plan.plan_id if (plan) and False else None,
  ```
- `_halted` · survived · ternary: condition forced · `46346ca2371d5140`
  ```diff
  -plan_id=plan.plan_id if plan else None,
  +plan_id=plan.plan_id if (plan) or True else None,
  ```
- `_require_reusable` · survived · argument: replaced by None · `88f776e57adf9d78`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, None)
  ```
- `_require_reusable` · survived · string: XX added · `8f3c9c60b1d116e2`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "XXjournalXX")
  ```
- `_require_reusable` · survived · string: upper case · `7a8efa7048a1d538`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "JOURNAL")
  ```
- `_locked` · survived · argument: replaced by None · `30fbf1a93a363a85`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, None)
  ```
- `_locked` · survived · string: XX added · `7802f165ad6bafb2`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "XXjournalXX")
  ```
- `_locked` · survived · string: upper case · `03cb720f517eef0b`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "JOURNAL")
  ```
- `_locked` · survived · is / in negated · `4e3b97ab42193a92`
  ```diff
  -if journal is None:
  +if journal is not None:
  ```
- `_locked` · survived · argument: replaced by None · `2bac450dc106f756`
  ```diff
  -return _halted(request, evaluation.halts, clone, evaluation.local)
  +return _halted(request, evaluation.halts, None, evaluation.local)
  ```
- `_proposed` · survived · argument: replaced by None · `eb41b27aeace6e23`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError(None, "repository") from error
  ```
- `_proposed` · survived · argument: replaced by None · `04eb341eea1d727e`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("base_tree_inconsistent", None) from error
  ```
- `_proposed` · survived · argument: removed · `f47dc3034fc26cdb`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("repository") from error
  ```
- `_proposed` · survived · argument: removed · `0b048e920eddf259`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("base_tree_inconsistent", ) from error
  ```
- `_proposed` · survived · string: XX added · `c37f65aba6d7f8b1`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("XXbase_tree_inconsistentXX", "repository") from error
  ```
- `_proposed` · survived · string: upper case · `32945a501c89947c`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("BASE_TREE_INCONSISTENT", "repository") from error
  ```
- `_proposed` · survived · string: XX added · `ee3145078dc0e271`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("base_tree_inconsistent", "XXrepositoryXX") from error
  ```
- `_proposed` · survived · string: upper case · `593ffab3f6ecaed3`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("base_tree_inconsistent", "REPOSITORY") from error
  ```
- `_proposed` · survived · argument: replaced by None · `4651137c87c1a95a`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError(None, "repository")
  ```
- `_proposed` · survived · argument: replaced by None · `7504cfe03cbc5441`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("unexpected_change", None)
  ```
- `_proposed` · survived · argument: removed · `239b5db78ba43809`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("repository")
  ```
- `_proposed` · survived · argument: removed · `6e1914c108140195`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("unexpected_change", )
  ```
- `_proposed` · survived · string: XX added · `01d0437f1c66fc73`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("XXunexpected_changeXX", "repository")
  ```
- `_proposed` · survived · string: upper case · `c704ec7167a28313`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("UNEXPECTED_CHANGE", "repository")
  ```
- `_proposed` · survived · string: XX added · `c7992c71d1a1e932`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("unexpected_change", "XXrepositoryXX")
  ```
- `_proposed` · survived · string: upper case · `e6139698852b5e39`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("unexpected_change", "REPOSITORY")
  ```
- `_unchanged` · survived · argument: replaced by None · `6475dd515495494d`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
  +raise ForarbeiderRefusedError(None, "journal")
  ```
- `_unchanged` · survived · argument: replaced by None · `b93a628d130dd9e8`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, None)
  ```
- `_unchanged` · survived · argument: removed · `1509c83a1bafea0e`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
  +raise ForarbeiderRefusedError("journal")
  ```
- `_unchanged` · survived · argument: removed · `6dda39ad521260fc`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, )
  ```
- `_unchanged` · survived · string: XX added · `b56fca8a067c3c5f`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "XXjournalXX")
  ```
- `_unchanged` · survived · string: upper case · `21934ddff67ac4de`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
  +raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "JOURNAL")
  ```
- `_unchanged` · survived · argument: replaced by None · `dac9e555aca7adcd`
  ```diff
  -return PreparationOutcome("unchanged", transaction, (), (), clone)
  +return PreparationOutcome("unchanged", transaction, None, (), clone)
  ```
- `_unchanged` · survived · argument: replaced by None · `ca19f3fe6872e91c`
  ```diff
  -return PreparationOutcome("unchanged", transaction, (), (), clone)
  +return PreparationOutcome("unchanged", transaction, (), None, clone)
  ```
- `_unchanged` · survived · argument: replaced by None · `f16cda959d289350`
  ```diff
  -return PreparationOutcome("unchanged", transaction, (), (), clone)
  +return PreparationOutcome("unchanged", transaction, (), (), None)
  ```
- `_written` · survived · ternary: condition forced · `e6bd8ca260be1cfa`
  ```diff
  -detail = error.detail if error.detail in _READ_BACK else "write_refused"
  +detail = error.detail if (error.detail in _READ_BACK) or True else "write_refused"
  ```
- `_written` · survived · string: XX added · `b5f469ebaea357a9`
  ```diff
  -detail = error.detail if error.detail in _READ_BACK else "write_refused"
  +detail = error.detail if error.detail in _READ_BACK else "XXwrite_refusedXX"
  ```
- `_written` · survived · string: upper case · `4aceefc80838e532`
  ```diff
  -detail = error.detail if error.detail in _READ_BACK else "write_refused"
  +detail = error.detail if error.detail in _READ_BACK else "WRITE_REFUSED"
  ```
- `_written` · survived · argument: replaced by None · `23ecde0f67eb013a`
  ```diff
  -return _abandoned(clone, "write_failed")
  +return _abandoned(None, "write_failed")
  ```
- `_written` · survived · argument: replaced by None · `9072c9fafca56cd5`
  ```diff
  -return _abandoned(clone, "write_failed")
  +return _abandoned(clone, None)
  ```
- `_written` · survived · argument: removed · `75902a718f05186f`
  ```diff
  -return _abandoned(clone, "write_failed")
  +return _abandoned("write_failed")
  ```
- `_written` · survived · argument: removed · `0ae342218a0ac266`
  ```diff
  -return _abandoned(clone, "write_failed")
  +return _abandoned(clone, )
  ```
- `_written` · survived · string: XX added · `2ca14dda7c88fa1a`
  ```diff
  -return _abandoned(clone, "write_failed")
  +return _abandoned(clone, "XXwrite_failedXX")
  ```
- `_written` · survived · string: upper case · `784310bc38461993`
  ```diff
  -return _abandoned(clone, "write_failed")
  +return _abandoned(clone, "WRITE_FAILED")
  ```
- `recover_preparation` · survived · argument: replaced by None · `1439d546d111a52b`
  ```diff
  -return RecoveryOutcome("rolled_back", journal.transaction.transaction_id)
  +return RecoveryOutcome("rolled_back", None)
  ```
- `require_head` · survived · argument: replaced by None · `cfcd30ae57d962d1`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "head") from error
  +raise ForarbeiderRefusedError(None, "head") from error
  ```
- `require_head` · survived · argument: replaced by None · `88805d26dc4f7b69`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "head") from error
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, None) from error
  ```
- `require_head` · survived · argument: removed · `4772f05a6d33055b`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "head") from error
  +raise ForarbeiderRefusedError("head") from error
  ```
- `require_head` · survived · argument: removed · `7089bdf4a747d1df`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "head") from error
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, ) from error
  ```
- `require_head` · survived · string: XX added · `3c37cd5be02a19ce`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "head") from error
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "XXheadXX") from error
  ```
- `require_head` · survived · string: upper case · `4ad1d0f21e5d2cd2`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "head") from error
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "HEAD") from error
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_proposal.py</code>: 137</summary>

- `HaltError.__init__` · survived · argument: replaced by None · `19993b7ba97f6f2b`
  ```diff
  -super().__init__(code)
  +super().__init__(None)
  ```
- `HaltError.__init__` · survived · argument: replaced by None · `0cbf7337afe6b73d`
  ```diff
  -code=f"prepare:{code}", category=category, document_id=document_id
  +code=f"prepare:{code}", category=category, document_id=None
  ```
- `HaltError.__init__` · survived · argument: removed · `35876e323cc3a8cb`
  ```diff
  -code=f"prepare:{code}", category=category, document_id=document_id
  -)
  +code=f"prepare:{code}", category=category, )
  ```
- `_LocalError.__init__` · survived · argument: replaced by None · `1a8936261a71c672`
  ```diff
  -super().__init__(code)
  +super().__init__(None)
  ```
- `_artifact` · survived · ternary: condition forced · `b08d7aa048be5261`
  ```diff
  -data = None if key is None else artifacts.get(key)
  +data = None if (key is None) and False else artifacts.get(key)
  ```
- `_artifact` · survived · operator: and / or · `e19022d30c656f83`
  ```diff
  -if data is None or sha256(data).hexdigest() != key:
  +if data is None and sha256(data).hexdigest() != key:
  ```
- `_manifest` · survived · argument: replaced by None · `1c304eb37c0abf86`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch") from error
  +raise _LocalError(None) from error
  ```
- `_manifest` · survived · string: XX added · `489afb99c2313ffe`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch") from error
  +raise _LocalError("XXartifact_integrity_mismatchXX") from error
  ```
- `_manifest` · survived · string: upper case · `218ab654973d33a8`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch") from error
  +raise _LocalError("ARTIFACT_INTEGRITY_MISMATCH") from error
  ```
- `_manifest` · survived · argument: replaced by None · `6fb13b9501da1460`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch")
  +raise _LocalError(None)
  ```
- `_manifest` · survived · string: XX added · `c5b633ba20b6cdae`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch")
  +raise _LocalError("XXartifact_integrity_mismatchXX")
  ```
- `_manifest` · survived · string: upper case · `a90af4494900da3e`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch")
  +raise _LocalError("ARTIFACT_INTEGRITY_MISMATCH")
  ```
- `_extraction` · survived · argument: replaced by None · `c55fbded7b7495e9`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch")
  +raise _LocalError(None)
  ```
- `_extraction` · survived · string: XX added · `06cbbe827186f117`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch")
  +raise _LocalError("XXartifact_integrity_mismatchXX")
  ```
- `_extraction` · survived · string: upper case · `bb83a04c434dacf0`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch")
  +raise _LocalError("ARTIFACT_INTEGRITY_MISMATCH")
  ```
- `_extraction` · survived · argument: replaced by None · `e014e865c5f4d192`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch") from error
  +raise _LocalError(None) from error
  ```
- `_extraction` · survived · string: XX added · `1b16a48e024d9f2d`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch") from error
  +raise _LocalError("XXartifact_integrity_mismatchXX") from error
  ```
- `_extraction` · survived · string: upper case · `5b8d8bb52a190de4`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch") from error
  +raise _LocalError("ARTIFACT_INTEGRITY_MISMATCH") from error
  ```
- `_extraction` · survived · operator: and / or · `a6fabcbfc1413950`
  ```diff
  -if not reproduced or extraction.document_id != row.selection.document_id:
  +if not reproduced and extraction.document_id != row.selection.document_id:
  ```
- `_extraction` · survived · argument: replaced by None · `c55fbded7b7495e9-2`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch")
  +raise _LocalError(None)
  ```
- `_extraction` · survived · string: XX added · `06cbbe827186f117-2`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch")
  +raise _LocalError("XXartifact_integrity_mismatchXX")
  ```
- `_extraction` · survived · string: upper case · `bb83a04c434dacf0-2`
  ```diff
  -raise _LocalError("artifact_integrity_mismatch")
  +raise _LocalError("ARTIFACT_INTEGRITY_MISMATCH")
  ```
- `_decision` · survived · ternary: condition forced · `b334c7bed7bc6d45`
  ```diff
  -data = None if key is None else artifacts.get(key)
  +data = None if (key is None) and False else artifacts.get(key)
  ```
- `_decision` · survived · argument: replaced by None · `50390bc3c7b1740f`
  ```diff
  -raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id)
  +raise HaltError("fc7_decision_unavailable", "unknown", None)
  ```
- `_decision` · survived · argument: removed · `4f63a5d8bd83ef3a`
  ```diff
  -raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id)
  +raise HaltError("fc7_decision_unavailable", "unknown", )
  ```
- `_decision` · survived · argument: replaced by None · `a6d49e20634b1f18`
  ```diff
  -raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id) from error
  +raise HaltError(None, "unknown", row.selection.document_id) from error
  ```
- `_decision` · survived · argument: replaced by None · `0b3d102e01278ef3`
  ```diff
  -raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id) from error
  +raise HaltError("fc7_decision_unavailable", None, row.selection.document_id) from error
  ```
- `_decision` · survived · argument: replaced by None · `e66cd0928c3a6f40`
  ```diff
  -raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id) from error
  +raise HaltError("fc7_decision_unavailable", "unknown", None) from error
  ```
- `_decision` · survived · argument: removed · `7c44190a64cb1bc9`
  ```diff
  -raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id) from error
  +raise HaltError("unknown", row.selection.document_id) from error
  ```
- `_decision` · survived · argument: removed · `2be0345cef2bb1c0`
  ```diff
  -raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id) from error
  +raise HaltError("fc7_decision_unavailable", row.selection.document_id) from error
  ```
- `_decision` · survived · argument: removed · `38b275e13cdcc633`
  ```diff
  -raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id) from error
  +raise HaltError("fc7_decision_unavailable", "unknown", ) from error
  ```
- `_decision` · survived · string: XX added · `201d1c4a10529758`
  ```diff
  -raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id) from error
  +raise HaltError("XXfc7_decision_unavailableXX", "unknown", row.selection.document_id) from error
  ```
- `_decision` · survived · string: upper case · `7dd8d181e726187b`
  ```diff
  -raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id) from error
  +raise HaltError("FC7_DECISION_UNAVAILABLE", "unknown", row.selection.document_id) from error
  ```
- `_decision` · survived · string: XX added · `c3666cef681e85a8`
  ```diff
  -raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id) from error
  +raise HaltError("fc7_decision_unavailable", "XXunknownXX", row.selection.document_id) from error
  ```
- `_decision` · survived · string: upper case · `58d995df206cfacc`
  ```diff
  -raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id) from error
  +raise HaltError("fc7_decision_unavailable", "UNKNOWN", row.selection.document_id) from error
  ```
- `_require_allowed` · survived · operator: and / or · `a19f7dbfccc78c80`
  ```diff
  -== (selection.document_id, selection.version_id)
  -and (decision.policy_id, decision.policy_version, decision.policy_sha256)
  +== (selection.document_id, selection.version_id) or (decision.policy_id, decision.policy_version, decision.policy_sha256)
  ```
- `_require_allowed` · survived · operator: and / or · `b6608b2c9c15efba`
  ```diff
  -policy is not None
  -and (decision.document_id, decision.version_id)
  +policy is not None or (decision.document_id, decision.version_id)
  ```
- `_require_allowed` · survived · not / ~ removed · `8f6db9995dcf0bac`
  ```diff
  -entries = {entry.id for part in extraction.parts if not part.annex for entry in part.entries}
  +entries = {entry.id for part in extraction.parts if part.annex for entry in part.entries}
  ```
- `_require_allowed` · survived · operator: and / or · `9b5e5a1f6a54a9cd`
  ```diff
  -every = all(item.decision == "allow" for item in decision.entries) and all(
  +every = all(item.decision == "allow" for item in decision.entries) or all(
  ```
- `_require_allowed` · survived · operator: comparison · `48b545d19142dd62`
  ```diff
  -item.decision == "allow" for item in decision.attachments
  +item.decision != "allow" for item in decision.attachments
  ```
- `_require_allowed` · survived · string: XX added · `e6c385072727dc31`
  ```diff
  -item.decision == "allow" for item in decision.attachments
  +item.decision == "XXallowXX" for item in decision.attachments
  ```
- `_require_allowed` · survived · string: upper case · `154965f2d5976bca`
  ```diff
  -item.decision == "allow" for item in decision.attachments
  +item.decision == "ALLOW" for item in decision.attachments
  ```
- `_require_allowed` · survived · operator: and / or · `bc7ff3a696fa5e23`
  ```diff
  -if not (same and decision.document.rules_satisfied and every and entries <= allowed):
  +if not (same and decision.document.rules_satisfied and every or entries <= allowed):
  ```
- `_require_allowed` · survived · operator: comparison · `74a1ed14ec64fcab`
  ```diff
  -if not (same and decision.document.rules_satisfied and every and entries <= allowed):
  +if not (same and decision.document.rules_satisfied and every and entries < allowed):
  ```
- `_require_allowed` · survived · argument: replaced by None · `5b4283d9479b6b8a`
  ```diff
  -raise HaltError("fc7_decision_contradicts_plan", "unknown", selection.document_id)
  +raise HaltError("fc7_decision_contradicts_plan", "unknown", None)
  ```
- `_require_allowed` · survived · argument: removed · `e14c7a75e6fc6162`
  ```diff
  -raise HaltError("fc7_decision_contradicts_plan", "unknown", selection.document_id)
  +raise HaltError("fc7_decision_contradicts_plan", "unknown", )
  ```
- `_require_plan_claims` · survived · argument: replaced by None · `3e0bb5400188e707`
  ```diff
  -raise HaltError("policy_mismatch", "policy", selection.document_id)
  +raise HaltError(None, "policy", selection.document_id)
  ```
- `_require_plan_claims` · survived · argument: replaced by None · `925a7f4d5232af91`
  ```diff
  -raise HaltError("policy_mismatch", "policy", selection.document_id)
  +raise HaltError("policy_mismatch", None, selection.document_id)
  ```
- `_require_plan_claims` · survived · argument: replaced by None · `b3d6aae452486cd3`
  ```diff
  -raise HaltError("policy_mismatch", "policy", selection.document_id)
  +raise HaltError("policy_mismatch", "policy", None)
  ```
- `_require_plan_claims` · survived · argument: removed · `e43dbb8e1b02da17`
  ```diff
  -raise HaltError("policy_mismatch", "policy", selection.document_id)
  +raise HaltError("policy", selection.document_id)
  ```
- `_require_plan_claims` · survived · argument: removed · `bdd0e07acd969311`
  ```diff
  -raise HaltError("policy_mismatch", "policy", selection.document_id)
  +raise HaltError("policy_mismatch", selection.document_id)
  ```
- `_require_plan_claims` · survived · argument: removed · `8643f8598390fbb6`
  ```diff
  -raise HaltError("policy_mismatch", "policy", selection.document_id)
  +raise HaltError("policy_mismatch", "policy", )
  ```
- `_require_plan_claims` · survived · string: XX added · `1b495e4cea0d5dc7`
  ```diff
  -raise HaltError("policy_mismatch", "policy", selection.document_id)
  +raise HaltError("XXpolicy_mismatchXX", "policy", selection.document_id)
  ```
- `_require_plan_claims` · survived · string: upper case · `74292fc73b8f2742`
  ```diff
  -raise HaltError("policy_mismatch", "policy", selection.document_id)
  +raise HaltError("POLICY_MISMATCH", "policy", selection.document_id)
  ```
- `_require_plan_claims` · survived · string: XX added · `a59b05dd42c66bd0`
  ```diff
  -raise HaltError("policy_mismatch", "policy", selection.document_id)
  +raise HaltError("policy_mismatch", "XXpolicyXX", selection.document_id)
  ```
- `_require_plan_claims` · survived · string: upper case · `8f16d41c02d6c453`
  ```diff
  -raise HaltError("policy_mismatch", "policy", selection.document_id)
  +raise HaltError("policy_mismatch", "POLICY", selection.document_id)
  ```
- `_require_plan_claims` · survived · argument: replaced by None · `b93959d31e67810a`
  ```diff
  -raise HaltError("completeness_contradicts_plan", "unknown", selection.document_id)
  +raise HaltError(None, "unknown", selection.document_id)
  ```
- `_require_plan_claims` · survived · argument: replaced by None · `36d78f822088526a`
  ```diff
  -raise HaltError("completeness_contradicts_plan", "unknown", selection.document_id)
  +raise HaltError("completeness_contradicts_plan", None, selection.document_id)
  ```
- `_require_plan_claims` · survived · argument: replaced by None · `7745d1fdacb7cf53`
  ```diff
  -raise HaltError("completeness_contradicts_plan", "unknown", selection.document_id)
  +raise HaltError("completeness_contradicts_plan", "unknown", None)
  ```
- `_require_plan_claims` · survived · argument: removed · `4d5dc196c07a6797`
  ```diff
  -raise HaltError("completeness_contradicts_plan", "unknown", selection.document_id)
  +raise HaltError("unknown", selection.document_id)
  ```
- `_require_plan_claims` · survived · argument: removed · `ba46af55df1cc84d`
  ```diff
  -raise HaltError("completeness_contradicts_plan", "unknown", selection.document_id)
  +raise HaltError("completeness_contradicts_plan", selection.document_id)
  ```
- `_require_plan_claims` · survived · argument: removed · `e3bc003db91e17a2`
  ```diff
  -raise HaltError("completeness_contradicts_plan", "unknown", selection.document_id)
  +raise HaltError("completeness_contradicts_plan", "unknown", )
  ```
- `_require_plan_claims` · survived · string: XX added · `b67019e149460559`
  ```diff
  -raise HaltError("completeness_contradicts_plan", "unknown", selection.document_id)
  +raise HaltError("XXcompleteness_contradicts_planXX", "unknown", selection.document_id)
  ```
- `_require_plan_claims` · survived · string: upper case · `f796812226cf0ac6`
  ```diff
  -raise HaltError("completeness_contradicts_plan", "unknown", selection.document_id)
  +raise HaltError("COMPLETENESS_CONTRADICTS_PLAN", "unknown", selection.document_id)
  ```
- `_require_plan_claims` · survived · string: XX added · `1e90d5ae59233ba5`
  ```diff
  -raise HaltError("completeness_contradicts_plan", "unknown", selection.document_id)
  +raise HaltError("completeness_contradicts_plan", "XXunknownXX", selection.document_id)
  ```
- `_require_plan_claims` · survived · string: upper case · `575794189a5b8556`
  ```diff
  -raise HaltError("completeness_contradicts_plan", "unknown", selection.document_id)
  +raise HaltError("completeness_contradicts_plan", "UNKNOWN", selection.document_id)
  ```
- `_evidence` · survived · operator: and / or · `6667bed4f92d1a32`
  ```diff
  -return tuple(sorted(key for key in keys if key is not None)) or (digest_of(row.binding),)
  +return tuple(sorted(key for key in keys if key is not None)) and (digest_of(row.binding),)
  ```
- `_evidence` · survived · is / in negated · `3e7bdea242b9a548`
  ```diff
  -return tuple(sorted(key for key in keys if key is not None)) or (digest_of(row.binding),)
  +return tuple(sorted(key for key in keys if key is None)) or (digest_of(row.binding),)
  ```
- `_evidence` · survived · argument: replaced by None · `cdd5d90361cc0d25`
  ```diff
  -return tuple(sorted(key for key in keys if key is not None)) or (digest_of(row.binding),)
  +return tuple(sorted(key for key in keys if key is not None)) or (digest_of(None),)
  ```
- `_one` · survived · argument: replaced by None · `c0c0fda78c3c40cf`
  ```diff
  -raise HaltError("policy_mismatch", "policy", row.selection.document_id)
  +raise HaltError(None, "policy", row.selection.document_id)
  ```
- `_one` · survived · argument: replaced by None · `5a8702e0b734bcb4`
  ```diff
  -raise HaltError("policy_mismatch", "policy", row.selection.document_id)
  +raise HaltError("policy_mismatch", None, row.selection.document_id)
  ```
- `_one` · survived · argument: replaced by None · `7d837da5d357aa9f`
  ```diff
  -raise HaltError("policy_mismatch", "policy", row.selection.document_id)
  +raise HaltError("policy_mismatch", "policy", None)
  ```
- `_one` · survived · argument: removed · `55deec7981c23bc2`
  ```diff
  -raise HaltError("policy_mismatch", "policy", row.selection.document_id)
  +raise HaltError("policy", row.selection.document_id)
  ```
- `_one` · survived · argument: removed · `f740599062b2ecdb`
  ```diff
  -raise HaltError("policy_mismatch", "policy", row.selection.document_id)
  +raise HaltError("policy_mismatch", row.selection.document_id)
  ```
- `_one` · survived · argument: removed · `399489805088dd6d`
  ```diff
  -raise HaltError("policy_mismatch", "policy", row.selection.document_id)
  +raise HaltError("policy_mismatch", "policy", )
  ```
- `_one` · survived · string: XX added · `5ccee628edde2290`
  ```diff
  -raise HaltError("policy_mismatch", "policy", row.selection.document_id)
  +raise HaltError("XXpolicy_mismatchXX", "policy", row.selection.document_id)
  ```
- `_one` · survived · string: upper case · `c43e2c2a6423ded7`
  ```diff
  -raise HaltError("policy_mismatch", "policy", row.selection.document_id)
  +raise HaltError("POLICY_MISMATCH", "policy", row.selection.document_id)
  ```
- `_one` · survived · string: XX added · `875fd7169198cf3f`
  ```diff
  -raise HaltError("policy_mismatch", "policy", row.selection.document_id)
  +raise HaltError("policy_mismatch", "XXpolicyXX", row.selection.document_id)
  ```
- `_one` · survived · string: upper case · `ee81f58804d37dd3`
  ```diff
  -raise HaltError("policy_mismatch", "policy", row.selection.document_id)
  +raise HaltError("policy_mismatch", "POLICY", row.selection.document_id)
  ```
- `_failure` · survived · operator: arithmetic · `dc991544fb992f3d`
  ```diff
  -version = row.selection.version_id or "0" * 64
  +version = row.selection.version_id or "0" / 64
  ```
- `_failure` · survived · string: XX added · `243bb5cec8545d49`
  ```diff
  -version = row.selection.version_id or "0" * 64
  +version = row.selection.version_id or "XX0XX" * 64
  ```
- `_failure` · survived · number · `5daec2098ba87a91`
  ```diff
  -version = row.selection.version_id or "0" * 64
  +version = row.selection.version_id or "0" * 65
  ```
- `_parsed_catalogue` · survived · argument: replaced by None · `1605dd96da55e920`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError(None, "repository") from error
  ```
- `_parsed_catalogue` · survived · argument: replaced by None · `a920b569845ddad8`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("base_tree_inconsistent", None) from error
  ```
- `_parsed_catalogue` · survived · argument: removed · `bb65817b43913720`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("repository") from error
  ```
- `_parsed_catalogue` · survived · argument: removed · `6d043fddc5283d14`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("base_tree_inconsistent", ) from error
  ```
- `_parsed_catalogue` · survived · string: XX added · `a73f95d4b14ccfec`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("XXbase_tree_inconsistentXX", "repository") from error
  ```
- `_parsed_catalogue` · survived · string: upper case · `5a0993ce2108bc81`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("BASE_TREE_INCONSISTENT", "repository") from error
  ```
- `_parsed_catalogue` · survived · string: XX added · `f4792e491d46472c`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("base_tree_inconsistent", "XXrepositoryXX") from error
  ```
- `_parsed_catalogue` · survived · string: upper case · `de57cea61c2b805c`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository") from error
  +raise HaltError("base_tree_inconsistent", "REPOSITORY") from error
  ```
- `_parsed_catalogue` · survived · argument: replaced by None · `bbc5eeb8a86029ed`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository")
  +raise HaltError(None, "repository")
  ```
- `_parsed_catalogue` · survived · argument: replaced by None · `456489b4a67aaeac`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository")
  +raise HaltError("base_tree_inconsistent", None)
  ```
- `_parsed_catalogue` · survived · argument: removed · `0e7b13a8df93c7d5`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository")
  +raise HaltError("repository")
  ```
- `_parsed_catalogue` · survived · argument: removed · `eea469f939dc5b38`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository")
  +raise HaltError("base_tree_inconsistent", )
  ```
- `_parsed_catalogue` · survived · string: XX added · `f4dbacf89e6be0e8`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository")
  +raise HaltError("XXbase_tree_inconsistentXX", "repository")
  ```
- `_parsed_catalogue` · survived · string: upper case · `b4f66e4089db9a2e`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository")
  +raise HaltError("BASE_TREE_INCONSISTENT", "repository")
  ```
- `_parsed_catalogue` · survived · string: XX added · `19029c4ea29d2a76`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository")
  +raise HaltError("base_tree_inconsistent", "XXrepositoryXX")
  ```
- `_parsed_catalogue` · survived · string: upper case · `53674f2cfe759ca7`
  ```diff
  -raise HaltError("base_tree_inconsistent", "repository")
  +raise HaltError("base_tree_inconsistent", "REPOSITORY")
  ```
- `_entry` · survived · string: XX added · `b49a9d613acf7621`
  ```diff
  -version_id=item.planned.version_id or "",
  +version_id=item.planned.version_id or "XXXX",
  ```
- `_publication` · survived · string: XX added · `f46413fbe27c30d8`
  ```diff
  -version_id=planned.version_id or "",
  +version_id=planned.version_id or "XXXX",
  ```
- `_publication` · survived · string: XX added · `ce43a2d2693ebb10`
  ```diff
  -selection_sha256=planned.selection_sha256 or "",
  +selection_sha256=planned.selection_sha256 or "XXXX",
  ```
- `_publication` · survived · string: XX added · `00e688c426a152cd`
  ```diff
  -binding_sha256=planned.binding_sha256 or "",
  +binding_sha256=planned.binding_sha256 or "XXXX",
  ```
- `_publication` · survived · string: XX added · `a7e8271a54563d1e`
  ```diff
  -manifest_sha256=binding.manifest_sha256 or "",
  +manifest_sha256=binding.manifest_sha256 or "XXXX",
  ```
- `_publication` · survived · string: XX added · `2c86782187e02a1d`
  ```diff
  -policy_decision_sha256=binding.policy_decision_sha256 or "",
  +policy_decision_sha256=binding.policy_decision_sha256 or "XXXX",
  ```
- `_new_catalogue` · survived · argument: replaced by None · `03694552011080af`
  ```diff
  -raise HaltError("version_already_published", "repository", item.planned.document_id)
  +raise HaltError("version_already_published", "repository", None)
  ```
- `_new_catalogue` · survived · argument: removed · `957230c37237c44a`
  ```diff
  -raise HaltError("version_already_published", "repository", item.planned.document_id)
  +raise HaltError("version_already_published", "repository", )
  ```
- `_file` · survived · argument: replaced by None · `97641d2f9465c0a3`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError(None, "repository")
  ```
- `_file` · survived · argument: replaced by None · `8e6688699202de3f`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("unexpected_change", None)
  ```
- `_file` · survived · argument: removed · `4963f55e7f670d57`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("repository")
  ```
- `_file` · survived · argument: removed · `b09060bfabeb1a4a`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("unexpected_change", )
  ```
- `_file` · survived · string: XX added · `b7e45da1fe94e326`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("XXunexpected_changeXX", "repository")
  ```
- `_file` · survived · string: upper case · `f92c4cd2162064c1`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("UNEXPECTED_CHANGE", "repository")
  ```
- `_file` · survived · string: XX added · `0c76672c6ea9f502`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("unexpected_change", "XXrepositoryXX")
  ```
- `_file` · survived · string: upper case · `18a26e3cfd479380`
  ```diff
  -raise HaltError("unexpected_change", "repository")
  +raise HaltError("unexpected_change", "REPOSITORY")
  ```
- `_manifest_file` · survived · string: XX added · `1403e7e922e56ec1`
  ```diff
  -quarantine_sha256=basis.quarantine_sha256 or "",
  +quarantine_sha256=basis.quarantine_sha256 or "XXXX",
  ```
- `propose` · survived · argument: replaced by None · `f9adf7727e05737e`
  ```diff
  -raise HaltError("release_already_published", "repository")
  +raise HaltError(None, "repository")
  ```
- `propose` · survived · argument: replaced by None · `24369564e2af8747`
  ```diff
  -raise HaltError("release_already_published", "repository")
  +raise HaltError("release_already_published", None)
  ```
- `propose` · survived · argument: removed · `01f4874f003219a1`
  ```diff
  -raise HaltError("release_already_published", "repository")
  +raise HaltError("repository")
  ```
- `propose` · survived · argument: removed · `34051d4361e79143`
  ```diff
  -raise HaltError("release_already_published", "repository")
  +raise HaltError("release_already_published", )
  ```
- `propose` · survived · string: XX added · `9489ded006a8cc8b`
  ```diff
  -raise HaltError("release_already_published", "repository")
  +raise HaltError("XXrelease_already_publishedXX", "repository")
  ```
- `propose` · survived · string: upper case · `75337d0e102e7417`
  ```diff
  -raise HaltError("release_already_published", "repository")
  +raise HaltError("RELEASE_ALREADY_PUBLISHED", "repository")
  ```
- `propose` · survived · string: XX added · `74934df9cc8c4544`
  ```diff
  -raise HaltError("release_already_published", "repository")
  +raise HaltError("release_already_published", "XXrepositoryXX")
  ```
- `propose` · survived · string: upper case · `06843ecee6d8fc3b`
  ```diff
  -raise HaltError("release_already_published", "repository")
  +raise HaltError("release_already_published", "REPOSITORY")
  ```
- `propose` · survived · argument: replaced by None · `97a81afd5432521c`
  ```diff
  -raise HaltError("proposal_exceeds_batch_caps", "policy")
  +raise HaltError(None, "policy")
  ```
- `propose` · survived · argument: replaced by None · `7439df0fd30e6efd`
  ```diff
  -raise HaltError("proposal_exceeds_batch_caps", "policy")
  +raise HaltError("proposal_exceeds_batch_caps", None)
  ```
- `propose` · survived · argument: removed · `fc3f8b32c004d6e3`
  ```diff
  -raise HaltError("proposal_exceeds_batch_caps", "policy")
  +raise HaltError("policy")
  ```
- `propose` · survived · argument: removed · `f81ad3745f56c43c`
  ```diff
  -raise HaltError("proposal_exceeds_batch_caps", "policy")
  +raise HaltError("proposal_exceeds_batch_caps", )
  ```
- `propose` · survived · string: XX added · `df650ac9afce879e`
  ```diff
  -raise HaltError("proposal_exceeds_batch_caps", "policy")
  +raise HaltError("XXproposal_exceeds_batch_capsXX", "policy")
  ```
- `propose` · survived · string: upper case · `8b373d15a3f32095`
  ```diff
  -raise HaltError("proposal_exceeds_batch_caps", "policy")
  +raise HaltError("PROPOSAL_EXCEEDS_BATCH_CAPS", "policy")
  ```
- `propose` · survived · string: XX added · `f09e573879d25c6a`
  ```diff
  -raise HaltError("proposal_exceeds_batch_caps", "policy")
  +raise HaltError("proposal_exceeds_batch_caps", "XXpolicyXX")
  ```
- `propose` · survived · string: upper case · `b0a02764a58a9a42`
  ```diff
  -raise HaltError("proposal_exceeds_batch_caps", "policy")
  +raise HaltError("proposal_exceeds_batch_caps", "POLICY")
  ```
- `_transaction` · survived · argument: replaced by None · `810049eb146e8297`
  ```diff
  -base_catalogue_sha256=_sha(base.get(CATALOGUE_FILE)),
  +base_catalogue_sha256=None,
  ```
- `_transaction` · survived · string: XX added · `dfc005bddcd57fdf`
  ```diff
  -quarantine_sha256=target.plan.basis.quarantine_sha256 or "",
  +quarantine_sha256=target.plan.basis.quarantine_sha256 or "XXXX",
  ```
- `_transaction` · survived · argument: replaced by None · `204de0e774aa47f6`
  ```diff
  -base_catalogue_sha256=_sha(base.get(CATALOGUE_FILE)),
  +base_catalogue_sha256=_sha(None),
  ```
- `_transaction` · survived · argument: replaced by None · `9eb7b5aa811e7e4a`
  ```diff
  -base_catalogue_sha256=_sha(base.get(CATALOGUE_FILE)),
  +base_catalogue_sha256=_sha(base.get(None)),
  ```
- `stage_payloads` · survived · assignment: value to None · `b7e5a3a4e108d8d6`
  ```diff
  -previous = base.get(path)
  +previous = None
  ```
- `stage_payloads` · survived · argument: replaced by None · `12f65b73cbfb3cd3`
  ```diff
  -previous = base.get(path)
  +previous = base.get(None)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_render.py</code>: 54</summary>

- `_scalar` · survived · argument: replaced by None · `622f0a8c30366c4b`
  ```diff
  -return json.dumps(value, ensure_ascii=False)
  +return json.dumps(value, ensure_ascii=None)
  ```
- `_front_matter` · survived · argument: replaced by None · `ff17ea50170d0a07`
  ```diff
  -"source_url": _line(extraction.source_url, "source_url"),
  +"source_url": _line(extraction.source_url, None),
  ```
- `_front_matter` · survived · string: XX added · `7d04045cdf2ad698`
  ```diff
  -"source_url": _line(extraction.source_url, "source_url"),
  +"source_url": _line(extraction.source_url, "XXsource_urlXX"),
  ```
- `_front_matter` · survived · string: upper case · `96e3a12071eb24de`
  ```diff
  -"source_url": _line(extraction.source_url, "source_url"),
  +"source_url": _line(extraction.source_url, "SOURCE_URL"),
  ```
- `_front_matter` · survived · argument: replaced by None · `7a56f550c436cbe7`
  ```diff
  -"title": _line(extraction.title, "title"),
  +"title": _line(extraction.title, None),
  ```
- `_front_matter` · survived · string: XX added · `1b8d25540fe80d5f`
  ```diff
  -"title": _line(extraction.title, "title"),
  +"title": _line(extraction.title, "XXtitleXX"),
  ```
- `_front_matter` · survived · string: upper case · `c6d25d881e4c6baa`
  ```diff
  -"title": _line(extraction.title, "title"),
  +"title": _line(extraction.title, "TITLE"),
  ```
- `_span` · survived · argument: replaced by None · `ae88b516fbb9c045`
  ```diff
  -text = _inline(_line(span.text, "text"))
  +text = _inline(_line(span.text, None))
  ```
- `_span` · survived · string: XX added · `31e70ce4a4fdaef7`
  ```diff
  -text = _inline(_line(span.text, "text"))
  +text = _inline(_line(span.text, "XXtextXX"))
  ```
- `_span` · survived · string: upper case · `6f972c4269d0db5d`
  ```diff
  -text = _inline(_line(span.text, "text"))
  +text = _inline(_line(span.text, "TEXT"))
  ```
- `_span` · survived · string: XX added · `60070ab922e45746`
  ```diff
  -elif span.kind == "list_item":
  +elif span.kind == "XXlist_itemXX":
  ```
- `_span` · survived · string: upper case · `50d74a86d104f33a`
  ```diff
  -elif span.kind == "list_item":
  +elif span.kind == "LIST_ITEM":
  ```
- `_span` · survived · assignment: value to None · `2fdaa479a2324090`
  ```diff
  -line = f"{'  ' * max(span.level - 1, 0)}- {text}"
  +line = None
  ```
- `_span` · survived · operator: arithmetic · `c25cdb3ef73f9f35`
  ```diff
  -line = f"{'  ' * max(span.level - 1, 0)}- {text}"
  +line = f"{'  ' / max(span.level - 1, 0)}- {text}"
  ```
- `_span` · survived · string: XX added · `4c701995351f6b3c`
  ```diff
  -line = f"{'  ' * max(span.level - 1, 0)}- {text}"
  +line = f"{'XX  XX' * max(span.level - 1, 0)}- {text}"
  ```
- `_span` · survived · argument: replaced by None · `6daea76501e840fd`
  ```diff
  -line = f"{'  ' * max(span.level - 1, 0)}- {text}"
  +line = f"{'  ' * max(None, 0)}- {text}"
  ```
- `_span` · survived · argument: replaced by None · `5309b9fb036b9cde`
  ```diff
  -line = f"{'  ' * max(span.level - 1, 0)}- {text}"
  +line = f"{'  ' * max(span.level - 1, None)}- {text}"
  ```
- `_span` · survived · argument: removed · `e1720d82dc424c6c`
  ```diff
  -line = f"{'  ' * max(span.level - 1, 0)}- {text}"
  +line = f"{'  ' * max(0)}- {text}"
  ```
- `_span` · survived · argument: removed · `e2c95c1e27b14e50`
  ```diff
  -line = f"{'  ' * max(span.level - 1, 0)}- {text}"
  +line = f"{'  ' * max(span.level - 1, )}- {text}"
  ```
- `_span` · survived · operator: arithmetic · `ad804ba62dd06bea`
  ```diff
  -line = f"{'  ' * max(span.level - 1, 0)}- {text}"
  +line = f"{'  ' * max(span.level + 1, 0)}- {text}"
  ```
- `_span` · survived · number · `7713ec76022e8b4e`
  ```diff
  -line = f"{'  ' * max(span.level - 1, 0)}- {text}"
  +line = f"{'  ' * max(span.level - 2, 0)}- {text}"
  ```
- `_span` · survived · number · `c61003b35f17231f`
  ```diff
  -line = f"{'  ' * max(span.level - 1, 0)}- {text}"
  +line = f"{'  ' * max(span.level - 1, 1)}- {text}"
  ```
- `_span` · survived · string: XX added · `c1f3ec3f00bc76be`
  ```diff
  -return "> " * span.quote_depth + line
  +return "XX> XX" * span.quote_depth + line
  ```
- `_tight` · survived · operator: comparison · `e94bcb7e473f18a9`
  ```diff
  -and previous.kind == span.kind == "list_item"
  +and previous.kind != span.kind == "list_item"
  ```
- `_tight` · survived · operator: comparison · `68a24b76b10eac6b`
  ```diff
  -and previous.kind == span.kind == "list_item"
  +and previous.kind == span.kind != "list_item"
  ```
- `_tight` · survived · string: XX added · `7d628fc97c48a462`
  ```diff
  -and previous.kind == span.kind == "list_item"
  +and previous.kind == span.kind == "XXlist_itemXX"
  ```
- `_tight` · survived · string: upper case · `682c9fc5c36eed26`
  ```diff
  -and previous.kind == span.kind == "list_item"
  +and previous.kind == span.kind == "LIST_ITEM"
  ```
- `_tight` · survived · operator: comparison · `1002d6b2cd712a80`
  ```diff
  -and previous.list_group == span.list_group
  +and previous.list_group != span.list_group
  ```
- `_part` · survived · argument: replaced by None · `ea7ddbd9d47c4ce6`
  ```diff
  -if not _tight(previous, span):
  +if not _tight(None, span):
  ```
- `_part` · survived · assignment: value to None · `beff32717287b228`
  ```diff
  -previous = span
  +previous = None
  ```
- `render_version` · survived · argument: replaced by None · `667c4d407d8a8036`
  ```diff
  -f"# {_inline(_line(extraction.title, 'title'))}",
  +f"# {_inline(_line(extraction.title, None))}",
  ```
- `render_version` · survived · string: XX added · `844ec0f96473e356`
  ```diff
  -f"# {_inline(_line(extraction.title, 'title'))}",
  +f"# {_inline(_line(extraction.title, 'XXtitleXX'))}",
  ```
- `render_version` · survived · string: upper case · `36ab3235ad41d380`
  ```diff
  -f"# {_inline(_line(extraction.title, 'title'))}",
  +f"# {_inline(_line(extraction.title, 'TITLE'))}",
  ```
- `_reference` · survived · assignment: value to None · `1fbf0ba2bb96336e`
  ```diff
  -label = _TYPE_LABELS.get(entry.document_type, entry.document_type)
  +label = None
  ```
- `_reference` · survived · argument: replaced by None · `edae0b81c23acf59`
  ```diff
  -label = _TYPE_LABELS.get(entry.document_type, entry.document_type)
  +label = _TYPE_LABELS.get(None, entry.document_type)
  ```
- `_reference` · survived · argument: replaced by None · `919295f8cb5e541e`
  ```diff
  -label = _TYPE_LABELS.get(entry.document_type, entry.document_type)
  +label = _TYPE_LABELS.get(entry.document_type, None)
  ```
- `_reference` · survived · argument: removed · `4d0bcb0239372f46`
  ```diff
  -label = _TYPE_LABELS.get(entry.document_type, entry.document_type)
  +label = _TYPE_LABELS.get(entry.document_type)
  ```
- `_reference` · survived · argument: removed · `f6b52396e3014e93`
  ```diff
  -label = _TYPE_LABELS.get(entry.document_type, entry.document_type)
  +label = _TYPE_LABELS.get(entry.document_type, )
  ```
- `_index_lines` · survived · assignment: value to None · `470f51699dbc67d2`
  ```diff
  -title = _inline(_line(entry.title, "title"))
  +title = None
  ```
- `_index_lines` · survived · argument: replaced by None · `12964dbe23787f36`
  ```diff
  -title = _inline(_line(entry.title, "title"))
  +title = _inline(_line(entry.title, None))
  ```
- `_index_lines` · survived · string: XX added · `387ee074b46fe263`
  ```diff
  -title = _inline(_line(entry.title, "title"))
  +title = _inline(_line(entry.title, "XXtitleXX"))
  ```
- `_index_lines` · survived · string: upper case · `6c9fa4aac5666d39`
  ```diff
  -title = _inline(_line(entry.title, "title"))
  +title = _inline(_line(entry.title, "TITLE"))
  ```
- `render_index` · survived · string: XX added · `fb2dbaf2c38c5c3f`
  ```diff
  -lines = ["# Forarbeider", "", NOT_LAW, ">", RIGHTS, ""]
  +lines = ["XX# ForarbeiderXX", "", NOT_LAW, ">", RIGHTS, ""]
  ```
- `render_index` · survived · string: lower case · `c68e08a5b5b7ad36`
  ```diff
  -lines = ["# Forarbeider", "", NOT_LAW, ">", RIGHTS, ""]
  +lines = ["# forarbeider", "", NOT_LAW, ">", RIGHTS, ""]
  ```
- `render_index` · survived · string: upper case · `d1e087b8f7430ced`
  ```diff
  -lines = ["# Forarbeider", "", NOT_LAW, ">", RIGHTS, ""]
  +lines = ["# FORARBEIDER", "", NOT_LAW, ">", RIGHTS, ""]
  ```
- `render_index` · survived · string: XX added · `394661402edb0b8f`
  ```diff
  -lines = ["# Forarbeider", "", NOT_LAW, ">", RIGHTS, ""]
  +lines = ["# Forarbeider", "XXXX", NOT_LAW, ">", RIGHTS, ""]
  ```
- `render_index` · survived · string: XX added · `12358efd640305a7`
  ```diff
  -lines = ["# Forarbeider", "", NOT_LAW, ">", RIGHTS, ""]
  +lines = ["# Forarbeider", "", NOT_LAW, "XX>XX", RIGHTS, ""]
  ```
- `render_index` · survived · string: XX added · `546209fddd1583b4`
  ```diff
  -lines = ["# Forarbeider", "", NOT_LAW, ">", RIGHTS, ""]
  +lines = ["# Forarbeider", "", NOT_LAW, ">", RIGHTS, "XXXX"]
  ```
- `render_index` · survived · augmented assignment to plain · `f44e3d047058d1dd`
  ```diff
  -lines += list(_index_lines(catalogue.documents)) or ["No document is published."]
  +lines = list(_index_lines(catalogue.documents)) or ["No document is published."]
  ```
- `render_index` · survived · string: XX added · `ec02a6a49b7b4468`
  ```diff
  -lines += list(_index_lines(catalogue.documents)) or ["No document is published."]
  +lines += list(_index_lines(catalogue.documents)) or ["XXNo document is published.XX"]
  ```
- `render_index` · survived · string: lower case · `1618a41b94ae669f`
  ```diff
  -lines += list(_index_lines(catalogue.documents)) or ["No document is published."]
  +lines += list(_index_lines(catalogue.documents)) or ["no document is published."]
  ```
- `render_index` · survived · string: upper case · `075e4da20d3f2ed3`
  ```diff
  -lines += list(_index_lines(catalogue.documents)) or ["No document is published."]
  +lines += list(_index_lines(catalogue.documents)) or ["NO DOCUMENT IS PUBLISHED."]
  ```
- `render_index` · survived · string: XX added · `a7ede01381b5bc1f`
  ```diff
  -return ("\n".join(lines) + "\n").encode()
  +return ("XX\nXX".join(lines) + "\n").encode()
  ```
- `render_index` · survived · string: XX added · `398828085a84c26b`
  ```diff
  -return ("\n".join(lines) + "\n").encode()
  +return ("\n".join(lines) + "XX\nXX").encode()
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_stage.py</code>: 87</summary>

- `write_staged` · survived · argument: replaced by None · `e92bf4b704e9d6d5`
  ```diff
  -write_leaf(staging, name, payload, "staging")
  +write_leaf(staging, name, payload, None)
  ```
- `write_staged` · survived · string: XX added · `024103a5a515d3c5`
  ```diff
  -write_leaf(staging, name, payload, "staging")
  +write_leaf(staging, name, payload, "XXstagingXX")
  ```
- `write_staged` · survived · string: upper case · `92490ccceddc0d42`
  ```diff
  -write_leaf(staging, name, payload, "staging")
  +write_leaf(staging, name, payload, "STAGING")
  ```
- `save` · survived · argument: replaced by None · `de82fbff7e7e88ab`
  ```diff
  -with directory(clone / ".git", "journal") as git:
  +with directory(clone / ".git", None) as git:
  ```
- `save` · survived · string: XX added · `22e1ea0c407e0eff`
  ```diff
  -with directory(clone / ".git", "journal") as git:
  +with directory(clone / ".git", "XXjournalXX") as git:
  ```
- `save` · survived · string: upper case · `c567671b4990c6d4`
  ```diff
  -with directory(clone / ".git", "journal") as git:
  +with directory(clone / ".git", "JOURNAL") as git:
  ```
- `save` · survived · argument: replaced by None · `36a77adfb1233feb`
  ```diff
  -write_leaf(git, f"{_JOURNAL_NAME}.next", canonical_text(journal).encode(), "journal")
  +write_leaf(git, f"{_JOURNAL_NAME}.next", canonical_text(journal).encode(), None)
  ```
- `save` · survived · string: XX added · `a803347525887048`
  ```diff
  -write_leaf(git, f"{_JOURNAL_NAME}.next", canonical_text(journal).encode(), "journal")
  +write_leaf(git, f"{_JOURNAL_NAME}.next", canonical_text(journal).encode(), "XXjournalXX")
  ```
- `save` · survived · string: upper case · `4b1c7be40d5e432e`
  ```diff
  -write_leaf(git, f"{_JOURNAL_NAME}.next", canonical_text(journal).encode(), "journal")
  +write_leaf(git, f"{_JOURNAL_NAME}.next", canonical_text(journal).encode(), "JOURNAL")
  ```
- `pending_journal` · survived · argument: replaced by None · `adf538314ac93d90`
  ```diff
  -with directory(clone / ".git", "journal") as git:
  +with directory(clone / ".git", None) as git:
  ```
- `pending_journal` · survived · string: XX added · `235205bae87f3bf3`
  ```diff
  -with directory(clone / ".git", "journal") as git:
  +with directory(clone / ".git", "XXjournalXX") as git:
  ```
- `pending_journal` · survived · string: upper case · `fbb98a7e63c25714`
  ```diff
  -with directory(clone / ".git", "journal") as git:
  +with directory(clone / ".git", "JOURNAL") as git:
  ```
- `pending_journal` · survived · argument: replaced by None · `e5524128513d3ff1`
  ```diff
  -raw = read_leaf(git, _JOURNAL_NAME, "journal")
  +raw = read_leaf(git, _JOURNAL_NAME, None)
  ```
- `pending_journal` · survived · string: XX added · `ffb356848ad01af8`
  ```diff
  -raw = read_leaf(git, _JOURNAL_NAME, "journal")
  +raw = read_leaf(git, _JOURNAL_NAME, "XXjournalXX")
  ```
- `pending_journal` · survived · string: upper case · `792634c79f4ec6f7`
  ```diff
  -raw = read_leaf(git, _JOURNAL_NAME, "journal")
  +raw = read_leaf(git, _JOURNAL_NAME, "JOURNAL")
  ```
- `pending_journal` · survived · argument: replaced by None · `d237bc77c1328db2`
  ```diff
  -raise _mismatch("journal") from error
  +raise _mismatch(None) from error
  ```
- `pending_journal` · survived · string: XX added · `7ed29bfbb88e9aaf`
  ```diff
  -raise _mismatch("journal") from error
  +raise _mismatch("XXjournalXX") from error
  ```
- `pending_journal` · survived · string: upper case · `c9ea20f28663bb54`
  ```diff
  -raise _mismatch("journal") from error
  +raise _mismatch("JOURNAL") from error
  ```
- `pending_journal` · survived · argument: replaced by None · `4426e9869a120f64`
  ```diff
  -raise _mismatch("journal")
  +raise _mismatch(None)
  ```
- `pending_journal` · survived · string: XX added · `a4f94a3b82d59fbc`
  ```diff
  -raise _mismatch("journal")
  +raise _mismatch("XXjournalXX")
  ```
- `pending_journal` · survived · string: upper case · `7e2b5862c6001b64`
  ```diff
  -raise _mismatch("journal")
  +raise _mismatch("JOURNAL")
  ```
- `_require_parents` · survived · operator: and / or · `bb721d7623eddfbb`
  ```diff
  -if folder.is_symlink() or (os.path.lexists(folder) and not folder.is_dir()):
  +if folder.is_symlink() and (os.path.lexists(folder) and not folder.is_dir()):
  ```
- `_require_parents` · survived · argument: replaced by None · `376301ce23dce5ea`
  ```diff
  -raise _unsafe(path)
  +raise _unsafe(None)
  ```
- `current` · survived · operator: and / or · `8026c1cd9057fba5`
  ```diff
  -if target.is_symlink() or not target.is_file():
  +if target.is_symlink() and not target.is_file():
  ```
- `current` · survived · string: XX added · `ec96457a8f4bcaea`
  ```diff
  -return ""
  +return "XXXX"
  ```
- `read_file` · survived · argument: replaced by None · `d2c4ba65df29c55c`
  ```diff
  -with directory(target.parent, "publication") as parent:
  +with directory(target.parent, None) as parent:
  ```
- `read_file` · survived · string: XX added · `31f0b9e76efa63e5`
  ```diff
  -with directory(target.parent, "publication") as parent:
  +with directory(target.parent, "XXpublicationXX") as parent:
  ```
- `read_file` · survived · string: upper case · `c9760555b8965dee`
  ```diff
  -with directory(target.parent, "publication") as parent:
  +with directory(target.parent, "PUBLICATION") as parent:
  ```
- `read_file` · survived · argument: replaced by None · `7b912eeef9ad58c1`
  ```diff
  -return read_leaf(parent, target.name, "publication")
  +return read_leaf(parent, target.name, None)
  ```
- `read_file` · survived · string: XX added · `a599e683c79d8450`
  ```diff
  -return read_leaf(parent, target.name, "publication")
  +return read_leaf(parent, target.name, "XXpublicationXX")
  ```
- `read_file` · survived · string: upper case · `7343829228371699`
  ```diff
  -return read_leaf(parent, target.name, "publication")
  +return read_leaf(parent, target.name, "PUBLICATION")
  ```
- `read_file` · survived · argument: replaced by None · `47a4a87928273a2d`
  ```diff
  -raise _unsafe(path) from error
  +raise _unsafe(None) from error
  ```
- `staged` · survived · argument: replaced by None · `9faff7cb7008182d`
  ```diff
  -payload = read_leaf(staging, sha, "staging")
  +payload = read_leaf(staging, sha, None)
  ```
- `staged` · survived · string: XX added · `849ab8fd0ba913b6`
  ```diff
  -payload = read_leaf(staging, sha, "staging")
  +payload = read_leaf(staging, sha, "XXstagingXX")
  ```
- `staged` · survived · string: upper case · `738e5efe65d23cf7`
  ```diff
  -payload = read_leaf(staging, sha, "staging")
  +payload = read_leaf(staging, sha, "STAGING")
  ```
- `staged` · survived · argument: replaced by None · `8eec6105706dfdf0`
  ```diff
  -raise _mismatch("staging") from error
  +raise _mismatch(None) from error
  ```
- `staged` · survived · string: XX added · `465de5827a8b990d`
  ```diff
  -raise _mismatch("staging") from error
  +raise _mismatch("XXstagingXX") from error
  ```
- `staged` · survived · string: upper case · `070e4dc7ad645d12`
  ```diff
  -raise _mismatch("staging") from error
  +raise _mismatch("STAGING") from error
  ```
- `staged` · survived · argument: replaced by None · `588f6f953381b3ea`
  ```diff
  -raise _mismatch("staging")
  +raise _mismatch(None)
  ```
- `staged` · survived · string: XX added · `d73d61da7b8689e5`
  ```diff
  -raise _mismatch("staging")
  +raise _mismatch("XXstagingXX")
  ```
- `staged` · survived · string: upper case · `b0b8ef7ee98fc33d`
  ```diff
  -raise _mismatch("staging")
  +raise _mismatch("STAGING")
  ```
- `begin` · survived · argument: replaced by None · `1bd70f652e948f27`
  ```diff
  -return advance(clone, journal, "staged")
  +return advance(clone, journal, None)
  ```
- `begin` · survived · string: XX added · `6b3f9f42049b2c1c`
  ```diff
  -return advance(clone, journal, "staged")
  +return advance(clone, journal, "XXstagedXX")
  ```
- `begin` · survived · string: upper case · `725950dce01df56a`
  ```diff
  -return advance(clone, journal, "staged")
  +return advance(clone, journal, "STAGED")
  ```
- `_make` · survived · argument: removed · `d2af91cf17998a68`
  ```diff
  -folder.mkdir(mode=0o755, exist_ok=True)
  +folder.mkdir(exist_ok=True)
  ```
- `_make` · survived · number · `054de49d674c5f8a`
  ```diff
  -folder.mkdir(mode=0o755, exist_ok=True)
  +folder.mkdir(mode=494, exist_ok=True)
  ```
- `_perform` · survived · argument: replaced by None · `f5170e4358a202c2`
  ```diff
  -raise _mismatch("edited")
  +raise _mismatch(None)
  ```
- `_perform` · survived · string: XX added · `6101eb0979154bdd`
  ```diff
  -raise _mismatch("edited")
  +raise _mismatch("XXeditedXX")
  ```
- `_perform` · survived · string: upper case · `f104add47e6e30c3`
  ```diff
  -raise _mismatch("edited")
  +raise _mismatch("EDITED")
  ```
- `verify_proposal` · survived · argument: replaced by None · `6ec2ecbb4b8387df`
  ```diff
  -raise _mismatch("head")
  +raise _mismatch(None)
  ```
- `verify_proposal` · survived · string: XX added · `be72853e8b81e2b7`
  ```diff
  -raise _mismatch("head")
  +raise _mismatch("XXheadXX")
  ```
- `verify_proposal` · survived · string: upper case · `05b0dd9d79d60bd8`
  ```diff
  -raise _mismatch("head")
  +raise _mismatch("HEAD")
  ```
- `verify_proposal` · survived · operator: and / or · `7a3dbef1de29a7f5`
  ```diff
  -if len(reported) != len(expected) or set(reported) != expected:
  +if len(reported) != len(expected) and set(reported) != expected:
  ```
- `verify_proposal` · survived · argument: replaced by None · `674274bbc18178fc`
  ```diff
  -raise _mismatch("proposal_bytes")
  +raise _mismatch(None)
  ```
- `verify_proposal` · survived · string: XX added · `f9b691322605baa2`
  ```diff
  -raise _mismatch("proposal_bytes")
  +raise _mismatch("XXproposal_bytesXX")
  ```
- `verify_proposal` · survived · string: upper case · `763950b712f3e4cb`
  ```diff
  -raise _mismatch("proposal_bytes")
  +raise _mismatch("PROPOSAL_BYTES")
  ```
- `_undo` · survived · argument: replaced by None · `cd5180f1f251f58a`
  ```diff
  -raise _mismatch("edited")
  +raise _mismatch(None)
  ```
- `_undo` · survived · string: XX added · `21362f5e9d76937a`
  ```diff
  -raise _mismatch("edited")
  +raise _mismatch("XXeditedXX")
  ```
- `_undo` · survived · string: upper case · `d54efef4804482dd`
  ```diff
  -raise _mismatch("edited")
  +raise _mismatch("EDITED")
  ```
- `_undo` · survived · argument: replaced by None · `0f5cb7b483a7bb8c`
  ```diff
  -_make(clone, path, previous)
  +_make(None, path, previous)
  ```
- `_undo` · survived · argument: replaced by None · `ad5f845462c3dba2`
  ```diff
  -_make(clone, path, previous)
  +_make(clone, None, previous)
  ```
- `_undo` · survived · argument: replaced by None · `7a9380480c693b4a`
  ```diff
  -_make(clone, path, previous)
  +_make(clone, path, None)
  ```
- `_undo` · survived · argument: removed · `204c24d58bb161bd`
  ```diff
  -_make(clone, path, previous)
  +_make(path, previous)
  ```
- `_undo` · survived · argument: removed · `66cdb2ac113cdab1`
  ```diff
  -_make(clone, path, previous)
  +_make(clone, previous)
  ```
- `_undo` · survived · argument: removed · `a6fbc2317850ce29`
  ```diff
  -_make(clone, path, previous)
  +_make(clone, path, )
  ```
- `_prune` · survived · operator: and / or · `221c7f789aa05632`
  ```diff
  -if folder.is_dir() and not folder.is_symlink():
  +if folder.is_dir() or not folder.is_symlink():
  ```
- `_prune` · survived · argument: replaced by None · `e21c0714024d388a`
  ```diff
  -with suppress(OSError):
  +with suppress(None):
  ```
- `roll_back` · survived · string: XX added · `6ad9815426007c78`
  ```diff
  -if journal.state != "rolling_back":
  +if journal.state != "XXrolling_backXX":
  ```
- `roll_back` · survived · string: upper case · `b53fe6576b034cba`
  ```diff
  -if journal.state != "rolling_back":
  +if journal.state != "ROLLING_BACK":
  ```
- `roll_back` · survived · argument: replaced by None · `9ee17609d9722894`
  ```diff
  -_undo(clone, item.path, item.sha256, item.previous_sha256)
  +_undo(clone, item.path, item.sha256, None)
  ```
- `roll_back` · survived · argument: replaced by None · `5631241b985d764e`
  ```diff
  -raise _mismatch("rollback")
  +raise _mismatch(None)
  ```
- `roll_back` · survived · string: XX added · `e697af272acaea7e`
  ```diff
  -raise _mismatch("rollback")
  +raise _mismatch("XXrollbackXX")
  ```
- `roll_back` · survived · string: upper case · `0a832c6a1c952705`
  ```diff
  -raise _mismatch("rollback")
  +raise _mismatch("ROLLBACK")
  ```
- `clear_orphan_staging` · survived · True / False swapped · `58643f4e1ece5bdd`
  ```diff
  -return False
  +return True
  ```
- `clear_orphan_staging` · survived · operator: and / or · `a1a9e00f34f8abd8`
  ```diff
  -if staging.is_symlink() or not staging.is_dir():
  +if staging.is_symlink() and not staging.is_dir():
  ```
- `clear_orphan_staging` · survived · True / False swapped · `ef9b35a886b0f732`
  ```diff
  -return True
  +return False
  ```
- `_tree_files` · survived · argument: replaced by None · `dcba41536c8c8a6c`
  ```diff
  -raise _unsafe(name)
  +raise _unsafe(None)
  ```
- `base_files` · survived · assignment: value to None · `667c172d38e4cfbd`
  ```diff
  -previous = changed[path]
  +previous = None
  ```
- `base_files` · survived · assignment: value to None · `d0ff0748bcefefd0`
  ```diff
  -found[path] = staged(clone, previous)
  +found[path] = None
  ```
- `base_files` · survived · argument: replaced by None · `47ca4f69ed4639ef`
  ```diff
  -found[path] = staged(clone, previous)
  +found[path] = staged(None, previous)
  ```
- `base_files` · survived · argument: replaced by None · `966bdaf8f3a1b12d`
  ```diff
  -found[path] = staged(clone, previous)
  +found[path] = staged(clone, None)
  ```
- `base_files` · survived · argument: removed · `a86b6aa7450b014e`
  ```diff
  -found[path] = staged(clone, previous)
  +found[path] = staged(previous)
  ```
- `base_files` · survived · argument: removed · `e2669b78f041f48b`
  ```diff
  -found[path] = staged(clone, previous)
  +found[path] = staged(clone, )
  ```
- `base_files` · survived · break / continue swapped · `91feea09f12a83ba`
  ```diff
  -continue
  +break
  ```
- `base_files` · survived · argument: replaced by None · `e008dff3b18b1c43`
  ```diff
  -raise _mismatch("base_tree")
  +raise _mismatch(None)
  ```
- `base_files` · survived · string: XX added · `56b43c7d2538ab5b`
  ```diff
  -raise _mismatch("base_tree")
  +raise _mismatch("XXbase_treeXX")
  ```
- `base_files` · survived · string: upper case · `4438e47550b05fec`
  ```diff
  -raise _mismatch("base_tree")
  +raise _mismatch("BASE_TREE")
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_attribution.py</code>: 55</summary>

- `_ordered_halts` · survived · operator: and / or · `55fc36d25ab0f8d2`
  ```diff
  -return tuple(sorted(found, key=lambda item: (item.document_id or "", item.code)))
  +return tuple(sorted(found, key=lambda item: (item.document_id and "", item.code)))
  ```
- `_ordered_halts` · survived · string: XX added · `b6af07f67127a2ae`
  ```diff
  -return tuple(sorted(found, key=lambda item: (item.document_id or "", item.code)))
  +return tuple(sorted(found, key=lambda item: (item.document_id or "XXXX", item.code)))
  ```
- `_attribute` · survived · argument: replaced by None · `c19d423947afb4ba`
  ```diff
  -halts += _summary(doc.planned.document_id, code, reasons)
  +halts += _summary(None, code, reasons)
  ```
- `_attribute` · survived · augmented assignment to plain · `daf163cd7c837fa8`
  ```diff
  -local += found.local
  +local = found.local
  ```
- `_summary` · survived · argument: replaced by None · `db36d42bd4e2b162`
  ```diff
  -return [_unknown(f"unattributed:{code}", document_id)]
  +return [_unknown(f"unattributed:{code}", None)]
  ```
- `_exact` · survived · assignment: value to None · `00beb38a544e6a74`
  ```diff
  -document_id = doc.planned.document_id
  +document_id = None
  ```
- `_exact` · survived · argument: replaced by None · `85a089b2909f89ea`
  ```diff
  -failure = GlobalFailure(code=code, category=GLOBAL_CODES[code], document_id=document_id)
  +failure = GlobalFailure(code=code, category=GLOBAL_CODES[code], document_id=None)
  ```
- `_exact` · survived · argument: removed · `ff46830b871452c7`
  ```diff
  -failure = GlobalFailure(code=code, category=GLOBAL_CODES[code], document_id=document_id)
  +failure = GlobalFailure(code=code, category=GLOBAL_CODES[code], )
  ```
- `_exact` · survived · argument: replaced by None · `4925824c243117d0`
  ```diff
  -failure = GlobalFailure(code=code, category="authorization", document_id=document_id)
  +failure = GlobalFailure(code=code, category="authorization", document_id=None)
  ```
- `_exact` · survived · argument: removed · `9a14230451e11538`
  ```diff
  -failure = GlobalFailure(code=code, category="authorization", document_id=document_id)
  +failure = GlobalFailure(code=code, category="authorization", )
  ```
- `_exact` · survived · argument: replaced by None · `c34036c666ecc38e`
  ```diff
  -failure = GlobalFailure(code=code, category="repository", document_id=document_id)
  +failure = GlobalFailure(code=code, category="repository", document_id=None)
  ```
- `_exact` · survived · argument: removed · `d28cfb68e59add35`
  ```diff
  -failure = GlobalFailure(code=code, category="repository", document_id=document_id)
  +failure = GlobalFailure(code=code, category="repository", )
  ```
- `_exact` · survived · argument: replaced by None · `e558c4fde543d2f5`
  ```diff
  -return Attributed((), (_unknown(f"unattributed:{code}", document_id),))
  +return Attributed((), (_unknown(f"unattributed:{code}", None),))
  ```
- `_unknown` · survived · argument: replaced by None · `ae41cfbdd84cd278`
  ```diff
  -return GlobalFailure(code=code[:128], category="unknown", document_id=document_id)
  +return GlobalFailure(code=code[:128], category="unknown", document_id=None)
  ```
- `_unknown` · survived · argument: removed · `1c03103d1b500cc7`
  ```diff
  -return GlobalFailure(code=code[:128], category="unknown", document_id=document_id)
  +return GlobalFailure(code=code[:128], category="unknown", )
  ```
- `_unknown` · survived · number · `a01ccaeaf854b636`
  ```diff
  -return GlobalFailure(code=code[:128], category="unknown", document_id=document_id)
  +return GlobalFailure(code=code[:129], category="unknown", document_id=document_id)
  ```
- `_finding` · survived · argument: replaced by None · `f4fa114243a341e6`
  ```diff
  -evidence_sha256s=evidence_of(doc.row, doc.planned),
  +evidence_sha256s=evidence_of(doc.row, None),
  ```
- `_finding` · survived · ternary: condition forced · `47b9771905a8104e`
  ```diff
  -policy=doc.row.selection.policy if doc.row else None,
  +policy=doc.row.selection.policy if (doc.row) or True else None,
  ```
- `evidence_of` · survived · operator: and / or · `2ee598a2bb1db26d`
  ```diff
  -return planned.artifact_sha256s or (digest_of(planned),)
  +return planned.artifact_sha256s and (digest_of(planned),)
  ```
- `evidence_of` · survived · argument: replaced by None · `c9761ea7897dd527`
  ```diff
  -return planned.artifact_sha256s or (digest_of(planned),)
  +return planned.artifact_sha256s or (digest_of(None),)
  ```
- `evidence_of` · survived · operator: and / or · `130873da0e8a7ab6`
  ```diff
  -return tuple(found) or planned.artifact_sha256s or (digest_of(binding),)
  +return tuple(found) or planned.artifact_sha256s and (digest_of(binding),)
  ```
- `evidence_of` · survived · argument: replaced by None · `8d27e6690143c5bd`
  ```diff
  -return tuple(found) or planned.artifact_sha256s or (digest_of(binding),)
  +return tuple(found) or planned.artifact_sha256s or (digest_of(None),)
  ```
- `_spans` · survived · kind not recorded · `4a0e3a6cbc0cd393`
  ```diff
  -document_id = doc.planned.document_id
  +document_id = None
  ```
- `_spans` · survived · argument: replaced by None · `3d130f3291b64c63`
  ```diff
  -return Attributed((), (_unknown(f"fc7_decision_unreadable:{code}", document_id),))
  +return Attributed(None, (_unknown(f"fc7_decision_unreadable:{code}", document_id),))
  ```
- `_spans` · survived · argument: replaced by None · `b9e123b100bd7163`
  ```diff
  -return Attributed((), (_unknown(f"fc7_decision_unreadable:{code}", document_id),))
  +return Attributed((), None)
  ```
- `_spans` · survived · argument: removed · `0f3b62dde39e8afa`
  ```diff
  -return Attributed((), (_unknown(f"fc7_decision_unreadable:{code}", document_id),))
  +return Attributed((_unknown(f"fc7_decision_unreadable:{code}", document_id),))
  ```
- `_spans` · survived · argument: removed · `3437c6493e36c222`
  ```diff
  -return Attributed((), (_unknown(f"fc7_decision_unreadable:{code}", document_id),))
  +return Attributed((), )
  ```
- `_spans` · survived · argument: replaced by None · `87dd1b0bdcfec80d`
  ```diff
  -return Attributed((), (_unknown(f"fc7_decision_unreadable:{code}", document_id),))
  +return Attributed((), (_unknown(None, document_id),))
  ```
- `_spans` · survived · argument: replaced by None · `e3f8989b8a0dae12`
  ```diff
  -return Attributed((), (_unknown(f"fc7_decision_unreadable:{code}", document_id),))
  +return Attributed((), (_unknown(f"fc7_decision_unreadable:{code}", None),))
  ```
- `_spans` · survived · argument: removed · `56e9da1bcb817978`
  ```diff
  -return Attributed((), (_unknown(f"fc7_decision_unreadable:{code}", document_id),))
  +return Attributed((), (_unknown(document_id),))
  ```
- `_spans` · survived · argument: removed · `d103697c573f44c0`
  ```diff
  -return Attributed((), (_unknown(f"fc7_decision_unreadable:{code}", document_id),))
  +return Attributed((), (_unknown(f"fc7_decision_unreadable:{code}", ),))
  ```
- `_spans` · survived · argument: replaced by None · `2abf552b304cf7b2`
  ```diff
  -return Attributed((), (_unknown(f"fc7_decision_inconsistent:{code}", document_id),))
  +return Attributed((), (_unknown(f"fc7_decision_inconsistent:{code}", None),))
  ```
- `_spans` · survived · augmented assignment to plain · `b81cbd334d448e07`
  ```diff
  -halts += found.halts
  +halts = found.halts
  ```
- `_decision` · survived · operator: and / or · `02fc7cb002e6ea46`
  ```diff
  -if doc.row is None or doc.row.binding.policy_decision_sha256 is None:
  +if doc.row is None and doc.row.binding.policy_decision_sha256 is None:
  ```
- `_decision` · survived · operator: and / or · `c8e100aadd92f918`
  ```diff
  -if data is None or sha256(data).hexdigest() != key:
  +if data is None and sha256(data).hexdigest() != key:
  ```
- `_parsed` · survived · string method swapped · `b34519fd11072b7f`
  ```diff
  -head, _, number = body.partition(":")
  +head, _, number = body.rpartition(":")
  ```
- `_parsed` · survived · string method swapped · `2a929b5a99e7e1d4`
  ```diff
  -kind, _, value = head.partition("_")
  +kind, _, value = head.rpartition("_")
  ```
- `_parsed` · survived · ternary: condition forced · `1c5f66e611339b57`
  ```diff
  -return kind, value, int(number) if number.isdigit() else None
  +return kind, value, int(number) if (number.isdigit()) or True else None
  ```
- `_item` · survived · assignment: value to None · `39ee62e6ddacf623`
  ```diff
  -document_id = doc.planned.document_id
  +document_id = None
  ```
- `_item` · survived · argument: replaced by None · `5cb369ec4fd0a331`
  ```diff
  -return Attributed((), (_rule_failure(item.rule, document_id),))
  +return Attributed(None, (_rule_failure(item.rule, document_id),))
  ```
- `_item` · survived · argument: replaced by None · `d854ae3fc0929514`
  ```diff
  -return Attributed((), (_rule_failure(item.rule, document_id),))
  +return Attributed((), None)
  ```
- `_item` · survived · argument: removed · `b09861c4edc04660`
  ```diff
  -return Attributed((), (_rule_failure(item.rule, document_id),))
  +return Attributed((_rule_failure(item.rule, document_id),))
  ```
- `_item` · survived · argument: removed · `e65615d8f09a6edd`
  ```diff
  -return Attributed((), (_rule_failure(item.rule, document_id),))
  +return Attributed((), )
  ```
- `_item` · survived · argument: replaced by None · `5b9c7fb18870008e`
  ```diff
  -return Attributed((), (_rule_failure(item.rule, document_id),))
  +return Attributed((), (_rule_failure(None, document_id),))
  ```
- `_item` · survived · argument: replaced by None · `1e9b8ece40f12db7`
  ```diff
  -return Attributed((), (_rule_failure(item.rule, document_id),))
  +return Attributed((), (_rule_failure(item.rule, None),))
  ```
- `_item` · survived · argument: removed · `94305463308241dd`
  ```diff
  -return Attributed((), (_rule_failure(item.rule, document_id),))
  +return Attributed((), (_rule_failure(document_id),))
  ```
- `_item` · survived · argument: removed · `b0326b19d8c7107e`
  ```diff
  -return Attributed((), (_rule_failure(item.rule, document_id),))
  +return Attributed((), (_rule_failure(item.rule, ),))
  ```
- `_entry_scope` · survived · operator: and / or · `9e1f0b79b9221262`
  ```diff
  -if category == "extraction" and item.part_id:
  +if category == "extraction" or item.part_id:
  ```
- `_rule_category` · survived · is / in negated · `b8fb804dbc58d8dd`
  ```diff
  -if rule in _RIGHTS_RULES or rule.startswith("rights:"):
  +if rule not in _RIGHTS_RULES or rule.startswith("rights:"):
  ```
- `_span_category` · survived · argument: replaced by None · `7ffbe922f56cdb25`
  ```diff
  -raise ValueError(f"no local category for {code}")
  +raise ValueError(None)
  ```
- `_stage` · survived · argument: replaced by None · `ac32d080aa4f665a`
  ```diff
  -return Attributed((), (_unknown("stage_failure_unbound", report.document_id),))
  +return Attributed((), (_unknown("stage_failure_unbound", None),))
  ```
- `_stage` · survived · argument: replaced by None · `956ab6743b1044e7`
  ```diff
  -document_id=report.document_id,
  +document_id=None,
  ```
- `_stage` · survived · argument: removed · `578fc88939ebca2e`
  ```diff
  -document_id=report.document_id,
  -)
  +)
  ```
- `_stage` · survived · argument: replaced by None · `3ba2c65cc71e939a`
  ```diff
  -return Attributed((), (_unknown(f"unattributed:stage:{report.code}", report.document_id),))
  +return Attributed((), (_unknown(f"unattributed:stage:{report.code}", None),))
  ```
- `_stage_finding` · survived · ternary: condition forced · `d1ab561e0b6ea2aa`
  ```diff
  -"policy": row.selection.policy if row else None,
  +"policy": row.selection.policy if (row) or True else None,
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_plan.py</code>: 8</summary>

- `prepare_plan` · survived · string: XX added · `d1768b5ce624dc15`
  ```diff
  -return ("plan_inputs_incomplete",)
  +return ("XXplan_inputs_incompleteXX",)
  ```
- `prepare_plan` · survived · string: upper case · `37be42e61dab8db8`
  ```diff
  -return ("plan_inputs_incomplete",)
  +return ("PLAN_INPUTS_INCOMPLETE",)
  ```
- `_restricted` · survived · assignment: value to None · `ae97bbba32d75510`
  ```diff
  -kept = draft.disposition in ("missing", "outside_inventory")
  +kept = None
  ```
- `_restricted` · survived · string: XX added · `e31b491273896d4a`
  ```diff
  -kept = draft.disposition in ("missing", "outside_inventory")
  +kept = draft.disposition in ("XXmissingXX", "outside_inventory")
  ```
- `_restricted` · survived · string: upper case · `d9680d8268382e0a`
  ```diff
  -kept = draft.disposition in ("missing", "outside_inventory")
  +kept = draft.disposition in ("MISSING", "outside_inventory")
  ```
- `_restricted` · survived · string: XX added · `79b2b575f9d73333`
  ```diff
  -kept = draft.disposition in ("missing", "outside_inventory")
  +kept = draft.disposition in ("missing", "XXoutside_inventoryXX")
  ```
- `_restricted` · survived · string: upper case · `87ac38bb976237bc`
  ```diff
  -kept = draft.disposition in ("missing", "outside_inventory")
  +kept = draft.disposition in ("missing", "OUTSIDE_INVENTORY")
  ```
- `_restricted` · survived · ternary: condition forced · `c1ee6d12d5aa7ead`
  ```diff
  -disposition: Disposition = draft.disposition if kept else "quarantined"
  +disposition: Disposition = draft.disposition if (kept) and False else "quarantined"
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_plan_models.py</code>: 7</summary>

- `coverage_of` · survived · operator: and / or · `6589d28341703077`
  ```diff
  -incomplete = counted["missing"] or counted["outside_inventory"]
  +incomplete = counted["missing"] and counted["outside_inventory"]
  ```
- `coverage_of` · survived · string: XX added · `c34f220aff634649`
  ```diff
  -incomplete = counted["missing"] or counted["outside_inventory"]
  +incomplete = counted["XXmissingXX"] or counted["outside_inventory"]
  ```
- `coverage_of` · survived · string: upper case · `020b0a6c292b96c4`
  ```diff
  -incomplete = counted["missing"] or counted["outside_inventory"]
  +incomplete = counted["MISSING"] or counted["outside_inventory"]
  ```
- `coverage_of` · survived · string: XX added · `6c7ec58702776d2b`
  ```diff
  -incomplete = counted["missing"] or counted["outside_inventory"]
  +incomplete = counted["missing"] or counted["XXoutside_inventoryXX"]
  ```
- `coverage_of` · survived · string: upper case · `5208dee080de1b06`
  ```diff
  -incomplete = counted["missing"] or counted["outside_inventory"]
  +incomplete = counted["missing"] or counted["OUTSIDE_INVENTORY"]
  ```
- `coverage_of` · survived · string: XX added · `54ad10968943e631`
  ```diff
  -excluded=counted["excluded"],
  +excluded=counted["XXexcludedXX"],
  ```
- `coverage_of` · survived · string: upper case · `e956a25631651371`
  ```diff
  -excluded=counted["excluded"],
  +excluded=counted["EXCLUDED"],
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_quarantine.py</code>: 28</summary>

- `merge` · survived · argument: replaced by None · `6394df0e0b32d255`
  ```diff
  -else _observed_again(current, finding, at, plan_id)
  +else _observed_again(current, finding, at, None)
  ```
- `_unique` · survived · string: XX added · `2451e396ffce2a73`
  ```diff
  -RefusalReason.SCHEMA_INVALID, "conflicting quarantine findings"
  +RefusalReason.SCHEMA_INVALID, "XXconflicting quarantine findingsXX"
  ```
- `_observed_again` · survived · argument: replaced by None · `c019d148fa611f5c`
  ```diff
  -policy=finding.policy,
  +policy=None,
  ```
- `_observed_again` · survived · argument: replaced by None · `793438150939fd5c`
  ```diff
  -plan_id=plan_id,
  +plan_id=None,
  ```
- `_observed_again` · survived · argument: removed · `b5713b1ae1c3be14`
  ```diff
  -plan_id=plan_id,
  -)
  +)
  ```
- `_appended` · survived · ternary: condition forced · `e33c220dc7055efd`
  ```diff
  -"policy": event.policy.model_dump() if event.policy else None,
  +"policy": event.policy.model_dump() if (event.policy) or True else None,
  ```
- `_ledger` · survived · argument: replaced by None · `c254809afdf0b7bd`
  ```diff
  -exclusions=tuple(sorted(exclusions, key=lambda item: item.document_id)),
  +exclusions=tuple(sorted(exclusions, key=None)),
  ```
- `_ledger` · survived · argument: removed · `f82a8bad532ec400`
  ```diff
  -exclusions=tuple(sorted(exclusions, key=lambda item: item.document_id)),
  +exclusions=tuple(sorted(exclusions, )),
  ```
- `_ledger` · survived · lambda: body replaced · `368a4096a76a8157`
  ```diff
  -exclusions=tuple(sorted(exclusions, key=lambda item: item.document_id)),
  +exclusions=tuple(sorted(exclusions, key=lambda item: None)),
  ```
- `settle` · survived · argument: replaced by None · `2ee5caa1d4569c9f`
  ```diff
  -for item in sorted(verified, key=lambda found: found.sha256):
  +for item in sorted(verified, key=None):
  ```
- `settle` · survived · argument: removed · `59ba35cd6f5c7b51`
  ```diff
  -for item in sorted(verified, key=lambda found: found.sha256):
  +for item in sorted(verified, ):
  ```
- `settle` · survived · lambda: body replaced · `316f0d7d313e178a`
  ```diff
  -for item in sorted(verified, key=lambda found: found.sha256):
  +for item in sorted(verified, key=lambda found: None):
  ```
- `clears` · survived · operator: and / or · `ad0c4f8d6356b990`
  ```diff
  -if exception.action != "clearance" or record.policy is None:
  +if exception.action != "clearance" and record.policy is None:
  ```
- `clears` · survived · True / False swapped · `f2df386240c342d5`
  ```diff
  -return False
  +return True
  ```
- `clears` · survived · string: XX added · `4e86bf0afcbee265`
  ```diff
  -if exception.target == "finding":
  +if exception.target == "XXfindingXX":
  ```
- `clears` · survived · string: upper case · `f22676d4740235b9`
  ```diff
  -if exception.target == "finding":
  +if exception.target == "FINDING":
  ```
- `parse_ledger` · survived · argument: replaced by None · `0ba60705188c4c95`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "quarantine ledger")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `parse_ledger` · survived · string: XX added · `ccfac983e4797bb7`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "quarantine ledger")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXquarantine ledgerXX")
  ```
- `parse_ledger` · survived · string: upper case · `d315988727f40c15`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "quarantine ledger")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "QUARANTINE LEDGER")
  ```
- `metrics_of` · survived · string: XX added · `5e2c9e44f93fa622`
  ```diff
  -expected = {d.document_id for d in plan.documents if d.disposition != "outside_inventory"}
  +expected = {d.document_id for d in plan.documents if d.disposition != "XXoutside_inventoryXX"}
  ```
- `metrics_of` · survived · string: upper case · `dd9c96277fb1147c`
  ```diff
  -expected = {d.document_id for d in plan.documents if d.disposition != "outside_inventory"}
  +expected = {d.document_id for d in plan.documents if d.disposition != "OUTSIDE_INVENTORY"}
  ```
- `metrics_of` · survived · argument: replaced by None · `8ad5de9d396044e2`
  ```diff
  -spans_rights_unresolved=_count(active, "rights", _SPAN_SCOPES),
  +spans_rights_unresolved=_count(active, "rights", None),
  ```
- `metrics_of` · survived · argument: replaced by None · `3433a31a4b9674de`
  ```diff
  -spans_privacy_unresolved=_count(active, "privacy", _SPAN_SCOPES),
  +spans_privacy_unresolved=_count(active, "privacy", None),
  ```
- `metrics_of` · survived · argument: replaced by None · `7fb80174056e868f`
  ```diff
  -release_gaps=_gaps(active, ledger.exclusions),
  +release_gaps=_gaps(active, None),
  ```
- `_source_complete` · survived · operator: and / or · `63575438af02719e`
  ```diff
  -if document in rows and rows[document].selection.source_state is SourceState.SOURCE_COMPLETE
  +if document in rows or rows[document].selection.source_state is SourceState.SOURCE_COMPLETE
  ```
- `_gaps` · survived · assignment: value to None · `72436106839d4f94`
  ```diff
  -counted["owner_exclusion"] = len(exclusions)
  +counted["owner_exclusion"] = None
  ```
- `_gaps` · survived · string: XX added · `d16e427dacffa9e8`
  ```diff
  -counted["owner_exclusion"] = len(exclusions)
  +counted["XXowner_exclusionXX"] = len(exclusions)
  ```
- `_gaps` · survived · string: upper case · `80da9f942daba371`
  ```diff
  -counted["owner_exclusion"] = len(exclusions)
  +counted["OWNER_EXCLUSION"] = len(exclusions)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_quarantine_models.py</code>: 24</summary>

- `_sorted_once` · survived · argument: replaced by None · `bd9cc129d5e82343`
  ```diff
  -raise ValueError(f"{what} are sorted, once each")
  +raise ValueError(None)
  ```
- `QuarantineSet.reasons_of` · survived · argument: replaced by None · `e66c5382def48347`
  ```diff
  -return next(found, ())
  +return next(found, None)
  ```
- `_check_history` · survived · argument: replaced by None · `d2cf220ccd18b081`
  ```diff
  -raise ValueError("history is numbered 1, 2, 3... in order")
  +raise ValueError(None)
  ```
- `_check_history` · survived · string: XX added · `1c1bd491b054c50a`
  ```diff
  -raise ValueError("history is numbered 1, 2, 3... in order")
  +raise ValueError("XXhistory is numbered 1, 2, 3... in orderXX")
  ```
- `_check_history` · survived · string: upper case · `773c6e916520d062`
  ```diff
  -raise ValueError("history is numbered 1, 2, 3... in order")
  +raise ValueError("HISTORY IS NUMBERED 1, 2, 3... IN ORDER")
  ```
- `_check_history` · survived · operator: and / or · `ce33c91b3123b5d1`
  ```diff
  -if history[0].event != "observed" or history[0].at != record.first_seen:
  +if history[0].event != "observed" and history[0].at != record.first_seen:
  ```
- `_check_history` · survived · argument: replaced by None · `c1d1ff5ec434c1c7`
  ```diff
  -raise ValueError("history starts with the first observation, at first_seen")
  +raise ValueError(None)
  ```
- `_check_history` · survived · string: XX added · `188c32e32ad3ffef`
  ```diff
  -raise ValueError("history starts with the first observation, at first_seen")
  +raise ValueError("XXhistory starts with the first observation, at first_seenXX")
  ```
- `_check_history` · survived · string: upper case · `c5208f3b12f16a6f`
  ```diff
  -raise ValueError("history starts with the first observation, at first_seen")
  +raise ValueError("HISTORY STARTS WITH THE FIRST OBSERVATION, AT FIRST_SEEN")
  ```
- `_check_history` · survived · string: XX added · `85bbcef633e80381`
  ```diff
  -if any(item.event == "observed" for item in history[1:]):
  +if any(item.event == "XXobservedXX" for item in history[1:]):
  ```
- `_check_history` · survived · string: upper case · `1ab9fa5b28f20691`
  ```diff
  -if any(item.event == "observed" for item in history[1:]):
  +if any(item.event == "OBSERVED" for item in history[1:]):
  ```
- `_check_history` · survived · number · `b132c11dbcc9c4ab`
  ```diff
  -if any(item.event == "observed" for item in history[1:]):
  +if any(item.event == "observed" for item in history[2:]):
  ```
- `_check_history` · survived · argument: replaced by None · `7573175b8e112c13`
  ```diff
  -raise ValueError("a record is first observed once")
  +raise ValueError(None)
  ```
- `_check_history` · survived · string: XX added · `f25157a0355c62fb`
  ```diff
  -raise ValueError("a record is first observed once")
  +raise ValueError("XXa record is first observed onceXX")
  ```
- `_check_history` · survived · string: upper case · `a0bbc4f3758c6a42`
  ```diff
  -raise ValueError("a record is first observed once")
  +raise ValueError("A RECORD IS FIRST OBSERVED ONCE")
  ```
- `_check_history` · survived · argument: replaced by None · `3c1edb7e49d396e4`
  ```diff
  -raise ValueError("history is in time order")
  +raise ValueError(None)
  ```
- `_check_history` · survived · string: XX added · `6d4feee6f5555e5d`
  ```diff
  -raise ValueError("history is in time order")
  +raise ValueError("XXhistory is in time orderXX")
  ```
- `_check_history` · survived · string: upper case · `40c2285dd1efef86`
  ```diff
  -raise ValueError("history is in time order")
  +raise ValueError("HISTORY IS IN TIME ORDER")
  ```
- `_check_history` · survived · argument: replaced by None · `8d2e565dfa82a64b`
  ```diff
  -raise ValueError("a record's evidence and policy are its latest event's")
  +raise ValueError(None)
  ```
- `_check_history` · survived · string: XX added · `0a0cb3b568196595`
  ```diff
  -raise ValueError("a record's evidence and policy are its latest event's")
  +raise ValueError("XXa record's evidence and policy are its latest event'sXX")
  ```
- `_check_history` · survived · string: upper case · `d43b9e40374e2485`
  ```diff
  -raise ValueError("a record's evidence and policy are its latest event's")
  +raise ValueError("A RECORD'S EVIDENCE AND POLICY ARE ITS LATEST EVENT'S")
  ```
- `_check_history` · survived · argument: replaced by None · `f321e101fb97c63b`
  ```diff
  -raise ValueError("state follows from the history")
  +raise ValueError(None)
  ```
- `_check_history` · survived · string: XX added · `66c64737ce37165e`
  ```diff
  -raise ValueError("state follows from the history")
  +raise ValueError("XXstate follows from the historyXX")
  ```
- `_check_history` · survived · string: upper case · `5fbde2ffa45b888d`
  ```diff
  -raise ValueError("state follows from the history")
  +raise ValueError("STATE FOLLOWS FROM THE HISTORY")
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_quarantine_run.py</code>: 51</summary>

- `run_quarantine` · survived · assignment: value to None · `b04c759f2b8c864c`
  ```diff
  -failures = [_failure(code, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  +failures = None
  ```
- `run_quarantine` · survived · argument: replaced by None · `f3e05c3cbcd6f9b3`
  ```diff
  -failures = [_failure(code, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  +failures = [_failure(None, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  ```
- `run_quarantine` · survived · argument: replaced by None · `1f8941e54b1a156a`
  ```diff
  -failures = [_failure(code, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  +failures = [_failure(code, None) for code in prepared]
  ```
- `run_quarantine` · survived · argument: removed · `022000d3bf5a47b9`
  ```diff
  -failures = [_failure(code, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  +failures = [_failure(_PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  ```
- `run_quarantine` · survived · argument: removed · `8bfc5ef0b36e0e29`
  ```diff
  -failures = [_failure(code, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  +failures = [_failure(code, ) for code in prepared]
  ```
- `run_quarantine` · survived · argument: replaced by None · `bc9b10c8f3f0be37`
  ```diff
  -failures = [_failure(code, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  +failures = [_failure(code, _PLAN_FAILURES.get(None, "unknown")) for code in prepared]
  ```
- `run_quarantine` · survived · argument: replaced by None · `7df41d092c52bbb2`
  ```diff
  -failures = [_failure(code, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  +failures = [_failure(code, _PLAN_FAILURES.get(code, None)) for code in prepared]
  ```
- `run_quarantine` · survived · argument: removed · `f8820cdce612c3dc`
  ```diff
  -failures = [_failure(code, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  +failures = [_failure(code, _PLAN_FAILURES.get("unknown")) for code in prepared]
  ```
- `run_quarantine` · survived · argument: removed · `1168b610ef7db819`
  ```diff
  -failures = [_failure(code, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  +failures = [_failure(code, _PLAN_FAILURES.get(code, )) for code in prepared]
  ```
- `run_quarantine` · survived · string: XX added · `d676e2ba76a61d25`
  ```diff
  -failures = [_failure(code, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  +failures = [_failure(code, _PLAN_FAILURES.get(code, "XXunknownXX")) for code in prepared]
  ```
- `run_quarantine` · survived · string: upper case · `94ee6f43e19a5fab`
  ```diff
  -failures = [_failure(code, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
  +failures = [_failure(code, _PLAN_FAILURES.get(code, "UNKNOWN")) for code in prepared]
  ```
- `run_quarantine` · survived · argument: replaced by None · `ae874fabe1266db5`
  ```diff
  -return _halted(ledger, failures)
  +return _halted(None, failures)
  ```
- `run_quarantine` · survived · argument: replaced by None · `8d70f836733b3890`
  ```diff
  -return _halted(ledger, failures)
  +return _halted(ledger, None)
  ```
- `run_quarantine` · survived · argument: removed · `e0d7fe7116ac93be`
  ```diff
  -return _halted(ledger, failures)
  +return _halted(failures)
  ```
- `run_quarantine` · survived · argument: removed · `5e98339b27c24067`
  ```diff
  -return _halted(ledger, failures)
  +return _halted(ledger, )
  ```
- `run_quarantine` · survived · argument: replaced by None · `db7532824df50fb6`
  ```diff
  -return _halted(ledger, halts, (base, prepared), verdict.rejected)
  +return _halted(ledger, halts, (base, prepared), None)
  ```
- `run_quarantine` · survived · argument: removed · `e6ea5383df126511`
  ```diff
  -return _halted(ledger, halts, (base, prepared), verdict.rejected)
  +return _halted(ledger, halts, (base, prepared), )
  ```
- `run_quarantine` · survived · argument: replaced by None · `b5c1e02e37184c05`
  ```diff
  -merged = merge(ledger, attributed.local, run.at, base.plan_id)
  +merged = merge(ledger, attributed.local, run.at, None)
  ```
- `run_quarantine` · survived · argument: removed · `74fd514c53f9bfba`
  ```diff
  -merged = merge(ledger, attributed.local, run.at, base.plan_id)
  +merged = merge(ledger, attributed.local, run.at, )
  ```
- `run_quarantine` · survived · argument: replaced by None · `bf04c77c6fdd37db`
  ```diff
  -return _replanned(run, settled, (base, prepared), verdict.rejected)
  +return _replanned(run, settled, (base, prepared), None)
  ```
- `_clock` · survived · operator: comparison · `809133123d4f091e`
  ```diff
  -if latest is not None and at < latest:
  +if latest is not None and at <= latest:
  ```
- `_halted` · survived · operator: and / or · `2fcf5085f0a811a8`
  ```diff
  -ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id or "", item.code)))
  +ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id and "", item.code)))
  ```
- `_halted` · survived · string: XX added · `4c2e8466bcc7a786`
  ```diff
  -ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id or "", item.code)))
  +ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id or "XXXX", item.code)))
  ```
- `_halted` · survived · string: XX added · `3bb42d114fbc6de4`
  ```diff
  -plan = PlanEvidence(status="invalid", source=None, reasons=("plan_halted_global_failure",))
  +plan = PlanEvidence(status="invalid", source=None, reasons=("XXplan_halted_global_failureXX",))
  ```
- `_halted` · survived · string: upper case · `b6bd40ce1439f21a`
  ```diff
  -plan = PlanEvidence(status="invalid", source=None, reasons=("plan_halted_global_failure",))
  +plan = PlanEvidence(status="invalid", source=None, reasons=("PLAN_HALTED_GLOBAL_FAILURE",))
  ```
- `_halted` · survived · operator: and / or · `8c16bda1d28f045f`
  ```diff
  -metrics = metrics_of(built[0], built[1].rows, ledger) if built and ledger else None
  +metrics = metrics_of(built[0], built[1].rows, ledger) if built or ledger else None
  ```
- `_halted` · survived · argument: replaced by None · `c31a93c0a087e77f`
  ```diff
  -return QuarantineOutcome("halted", ordered, ledger, NO_QUARANTINE, plan, metrics, rejected)
  +return QuarantineOutcome("halted", ordered, ledger, NO_QUARANTINE, plan, metrics, None)
  ```
- `_replanned` · survived · ternary: condition forced · `39d0a9922e8b83d9`
  ```diff
  -metrics = metrics_of(plan.plan, prepared.rows, ledger) if plan.plan else None
  +metrics = metrics_of(plan.plan, prepared.rows, ledger) if (plan.plan) or True else None
  ```
- `_replanned` · survived · argument: replaced by None · `ef67bbffa1728b8c`
  ```diff
  -return QuarantineOutcome("replanned", (), ledger, sealed, plan, metrics, rejected)
  +return QuarantineOutcome("replanned", None, ledger, sealed, plan, metrics, rejected)
  ```
- `_replanned` · survived · argument: replaced by None · `189bc569e486cad5`
  ```diff
  -return QuarantineOutcome("replanned", (), ledger, sealed, plan, metrics, rejected)
  +return QuarantineOutcome("replanned", (), ledger, sealed, plan, metrics, None)
  ```
- `verify_exceptions` · survived · argument: replaced by None · `4ba867f8e02537bb`
  ```diff
  -return ExceptionVerdict((), (), ())
  +return ExceptionVerdict((), None, ())
  ```
- `verify_exceptions` · survived · argument: replaced by None · `bd79eaed8e636590`
  ```diff
  -return ExceptionVerdict((), (), (_failure("quarantine_revocation_not_verified", "trust"),))
  +return ExceptionVerdict((), None, (_failure("quarantine_revocation_not_verified", "trust"),))
  ```
- `verify_exceptions` · survived · argument: replaced by None · `08f3c2da4a5429b3`
  ```diff
  -return ExceptionVerdict((), ("exception_policy_unavailable",), ())
  +return ExceptionVerdict(None, ("exception_policy_unavailable",), ())
  ```
- `verify_exceptions` · survived · argument: replaced by None · `204e4307b51b961c`
  ```diff
  -return ExceptionVerdict((), ("exception_policy_unavailable",), ())
  +return ExceptionVerdict((), None, ())
  ```
- `verify_exceptions` · survived · argument: replaced by None · `6bf44605ac9bd09d`
  ```diff
  -return ExceptionVerdict((), ("exception_policy_unavailable",), ())
  +return ExceptionVerdict((), ("exception_policy_unavailable",), None)
  ```
- `verify_exceptions` · survived · argument: removed · `465712227bcaa32d`
  ```diff
  -return ExceptionVerdict((), ("exception_policy_unavailable",), ())
  +return ExceptionVerdict(("exception_policy_unavailable",), ())
  ```
- `verify_exceptions` · survived · argument: removed · `ba729f9b3dc09e00`
  ```diff
  -return ExceptionVerdict((), ("exception_policy_unavailable",), ())
  +return ExceptionVerdict((), ())
  ```
- `verify_exceptions` · survived · argument: removed · `975f038c15a2fb98`
  ```diff
  -return ExceptionVerdict((), ("exception_policy_unavailable",), ())
  +return ExceptionVerdict((), ("exception_policy_unavailable",), )
  ```
- `verify_exceptions` · survived · string: XX added · `0351f79b122e7322`
  ```diff
  -return ExceptionVerdict((), ("exception_policy_unavailable",), ())
  +return ExceptionVerdict((), ("XXexception_policy_unavailableXX",), ())
  ```
- `verify_exceptions` · survived · string: upper case · `d177d108d0408880`
  ```diff
  -return ExceptionVerdict((), ("exception_policy_unavailable",), ())
  +return ExceptionVerdict((), ("EXCEPTION_POLICY_UNAVAILABLE",), ())
  ```
- `verify_exceptions` · survived · argument: replaced by None · `ad27278b5d0c54fe`
  ```diff
  -return ExceptionVerdict((), _reasons(c for c, _ in checks), (halt,))
  +return ExceptionVerdict((), None, (halt,))
  ```
- `_judged` · survived · operator: and / or · `239047864746520a`
  ```diff
  -if not isinstance(record, QuarantineException) or check.record_sha256 is None:
  +if not isinstance(record, QuarantineException) and check.record_sha256 is None:
  ```
- `_judged` · survived · argument: replaced by None · `a05805b35bdb2358`
  ```diff
  -rejected.add("exception_unverifiable")
  +rejected.add(None)
  ```
- `_judged` · survived · string: XX added · `02ccce6a86cf4aef`
  ```diff
  -rejected.add("exception_unverifiable")
  +rejected.add("XXexception_unverifiableXX")
  ```
- `_judged` · survived · string: upper case · `4a3224dd1b264740`
  ```diff
  -rejected.add("exception_unverifiable")
  +rejected.add("EXCEPTION_UNVERIFIABLE")
  ```
- `_judged` · survived · break / continue swapped · `e664f049354ba39f`
  ```diff
  -continue
  +break
  ```
- `_judged` · survived · break / continue swapped · `e664f049354ba39f-2`
  ```diff
  -continue
  +break
  ```
- `_judged` · survived · argument: replaced by None · `b5deea05d65a6d9b`
  ```diff
  -ordered = tuple(sorted(accepted, key=lambda item: item.sha256))
  +ordered = tuple(sorted(accepted, key=None))
  ```
- `_judged` · survived · argument: removed · `6a309f60c95b7084`
  ```diff
  -ordered = tuple(sorted(accepted, key=lambda item: item.sha256))
  +ordered = tuple(sorted(accepted, ))
  ```
- `_judged` · survived · lambda: body replaced · `fc0f43d847186f3a`
  ```diff
  -ordered = tuple(sorted(accepted, key=lambda item: item.sha256))
  +ordered = tuple(sorted(accepted, key=lambda item: None))
  ```
- `_own_reasons` · survived · operator: comparison · `66b4184a76099299`
  ```diff
  -if judge.at < record.valid_from:
  +if judge.at <= record.valid_from:
  ```

</details>

<details><summary><code>src/lovspor/git_prepare.py</code>: 37</summary>

- `_absolute` · survived · operator: and / or · `029745c83964eb60`
  ```diff
  -return Path(part).is_absolute() and os.path.normpath(part) == part
  +return Path(part).is_absolute() or os.path.normpath(part) == part
  ```
- `_verified` · survived · string: XX added · `b8cfe926eafc0240`
  ```diff
  -part.endswith("^{commit}") and _commit(part.removesuffix("^{commit}"))
  +part.endswith("XX^{commit}XX") and _commit(part.removesuffix("^{commit}"))
  ```
- `_verified` · survived · string: upper case · `dcf1e2b4abb62bd6`
  ```diff
  -part.endswith("^{commit}") and _commit(part.removesuffix("^{commit}"))
  +part.endswith("^{COMMIT}") and _commit(part.removesuffix("^{commit}"))
  ```
- `_verified` · survived · string method swapped · `a9145b68481fd468`
  ```diff
  -part.endswith("^{commit}") and _commit(part.removesuffix("^{commit}"))
  +part.endswith("^{commit}") and _commit(part.removeprefix("^{commit}"))
  ```
- `_verified` · survived · string: XX added · `dac331243286cff8`
  ```diff
  -part.endswith("^{commit}") and _commit(part.removesuffix("^{commit}"))
  +part.endswith("^{commit}") and _commit(part.removesuffix("XX^{commit}XX"))
  ```
- `_verified` · survived · string: upper case · `0c750075a32e9cea`
  ```diff
  -part.endswith("^{commit}") and _commit(part.removesuffix("^{commit}"))
  +part.endswith("^{commit}") and _commit(part.removesuffix("^{COMMIT}"))
  ```
- `git_environment` · survived · string: XX added · `74c1ba4f5e1b8662`
  ```diff
  -"PATH": os.environ.get("PATH", os.defpath),
  +"XXPATHXX": os.environ.get("PATH", os.defpath),
  ```
- `git_environment` · survived · string: lower case · `299d856399b64675`
  ```diff
  -"PATH": os.environ.get("PATH", os.defpath),
  +"path": os.environ.get("PATH", os.defpath),
  ```
- `git_environment` · survived · argument: replaced by None · `233dc967eddf7e27`
  ```diff
  -"PATH": os.environ.get("PATH", os.defpath),
  +"PATH": os.environ.get("PATH", None),
  ```
- `git_environment` · survived · argument: removed · `83455c2ab1bfbcb5`
  ```diff
  -"PATH": os.environ.get("PATH", os.defpath),
  +"PATH": os.environ.get("PATH", ),
  ```
- `git_environment` · survived · string: XX added · `7b8478bd8df9d53e`
  ```diff
  -"PATH": os.environ.get("PATH", os.defpath),
  +"PATH": os.environ.get("XXPATHXX", os.defpath),
  ```
- `git_environment` · survived · string: lower case · `a7acab18e923f5a1`
  ```diff
  -"PATH": os.environ.get("PATH", os.defpath),
  +"PATH": os.environ.get("path", os.defpath),
  ```
- `git_environment` · survived · string: XX added · `58d98a8654f40317`
  ```diff
  -"LC_ALL": "C",
  +"XXLC_ALLXX": "C",
  ```
- `git_environment` · survived · string: lower case · `b56413ca19bad17b`
  ```diff
  -"LC_ALL": "C",
  +"lc_all": "C",
  ```
- `git_environment` · survived · string: XX added · `9aa018ccb8b619c6`
  ```diff
  -"LC_ALL": "C",
  +"LC_ALL": "XXCXX",
  ```
- `git_environment` · survived · string: lower case · `aaa7e33100e2a928`
  ```diff
  -"LC_ALL": "C",
  +"LC_ALL": "c",
  ```
- `git_environment` · survived · string: XX added · `1930aef34b1361e2`
  ```diff
  -"GIT_CONFIG_GLOBAL": os.devnull,
  +"XXGIT_CONFIG_GLOBALXX": os.devnull,
  ```
- `git_environment` · survived · string: lower case · `be1dee98a739d9cb`
  ```diff
  -"GIT_CONFIG_GLOBAL": os.devnull,
  +"git_config_global": os.devnull,
  ```
- `git_environment` · survived · string: XX added · `b2e1195e0dd99f88`
  ```diff
  -"GIT_OPTIONAL_LOCKS": "0",
  +"XXGIT_OPTIONAL_LOCKSXX": "0",
  ```
- `git_environment` · survived · string: lower case · `07bde7248a82f723`
  ```diff
  -"GIT_OPTIONAL_LOCKS": "0",
  +"git_optional_locks": "0",
  ```
- `git_environment` · survived · string: XX added · `ca761ae8ddff272e`
  ```diff
  -"GIT_OPTIONAL_LOCKS": "0",
  +"GIT_OPTIONAL_LOCKS": "XX0XX",
  ```
- `git_environment` · survived · string: XX added · `285cc8fe77c6fab4`
  ```diff
  -"GIT_CEILING_DIRECTORIES": str(directory.parent),
  +"XXGIT_CEILING_DIRECTORIESXX": str(directory.parent),
  ```
- `git_environment` · survived · string: lower case · `14cf23d631f415bb`
  ```diff
  -"GIT_CEILING_DIRECTORIES": str(directory.parent),
  +"git_ceiling_directories": str(directory.parent),
  ```
- `git_environment` · survived · argument: replaced by None · `caf4c5d2758504fe`
  ```diff
  -"GIT_CEILING_DIRECTORIES": str(directory.parent),
  +"GIT_CEILING_DIRECTORIES": str(None),
  ```
- `git_environment` · survived · string: XX added · `0e50dfd3b3569744`
  ```diff
  -"GIT_NO_REPLACE_OBJECTS": "1",
  +"XXGIT_NO_REPLACE_OBJECTSXX": "1",
  ```
- `git_environment` · survived · string: lower case · `aa62853fe8cad2ac`
  ```diff
  -"GIT_NO_REPLACE_OBJECTS": "1",
  +"git_no_replace_objects": "1",
  ```
- `git_environment` · survived · string: XX added · `42eef26060232799`
  ```diff
  -"GIT_NO_REPLACE_OBJECTS": "1",
  +"GIT_NO_REPLACE_OBJECTS": "XX1XX",
  ```
- `git_environment` · survived · string: XX added · `19f0024e1d3183a7`
  ```diff
  -"GIT_GRAFT_FILE": os.devnull,
  +"XXGIT_GRAFT_FILEXX": os.devnull,
  ```
- `git_environment` · survived · string: lower case · `ba798959db4c860a`
  ```diff
  -"GIT_GRAFT_FILE": os.devnull,
  +"git_graft_file": os.devnull,
  ```
- `_fits` · survived · argument: replaced by None · `c058b1a4b74a9cf8`
  ```diff
  -return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=True))
  +return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=None))
  ```
- `_fits` · survived · argument: removed · `9a12e3effe3cfa03`
  ```diff
  -return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=True))
  +return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, ))
  ```
- `_fits` · survived · True / False swapped · `c5ffc0d95c599b35`
  ```diff
  -return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=True))
  +return all(p(a) if callable(p) else p == a for a, p in zip(argv, shape, strict=False))
  ```
- `run_git` · survived · argument: replaced by None · `deffc95accf6eb0d`
  ```diff
  -raise GitPrepareError(f"git argument vector not allowed: {argv[:1]}")
  +raise GitPrepareError(None)
  ```
- `run_git` · survived · number · `65c021fe5ec04bf9`
  ```diff
  -raise GitPrepareError(f"git argument vector not allowed: {argv[:1]}")
  +raise GitPrepareError(f"git argument vector not allowed: {argv[:2]}")
  ```
- `run_git` · survived · argument: replaced by None · `49c9c72c2bab9ccf`
  ```diff
  -check=False,
  +check=None,
  ```
- `run_git` · survived · argument: removed · `b5a341440bd42dbd`
  ```diff
  -check=False,
  ```
- `run_git` · survived · True / False swapped · `7df9e3bc857839b4`
  ```diff
  -check=False,
  +check=True,
  ```

</details>

## The register after this night

- **2759** open in 92 file(s); this night 598 new, 5 left, 0 gone with their function

| kind | open |
|---|---:|
| argument: replaced by None | 707 |
| string: XX added | 602 |
| string: upper case | 501 |
| argument: removed | 325 |
| operator: and / or | 115 |
| ternary: condition forced | 95 |
| number | 86 |
| assignment: value to None | 62 |
| True / False swapped | 60 |
| operator: comparison | 47 |
| operator: arithmetic | 27 |
| string: lower case | 27 |
| is / in negated | 25 |
| string method swapped | 18 |
| not recorded | 15 |
| break / continue swapped | 13 |
| augmented assignment to plain | 12 |
| lambda: body replaced | 7 |
| operator: bitwise | 6 |
| not / ~ removed | 5 |
| assignment: None to empty string | 2 |
| operator: augmented | 2 |

| file | open |
|---|---:|
| src/lovspor/identity/report.py | 202 |
| src/lovspor/forarbeider/version_lineage.py | 144 |
| src/lovspor/forarbeider/publication_proposal.py | 137 |
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
| src/lovspor/forarbeider/publication_render.py | 54 |
| src/lovspor/forarbeider/attachment_discovery.py | 52 |
| src/lovspor/forarbeider/release_sources.py | 52 |
| src/lovspor/forarbeider/release_quarantine_run.py | 51 |
| src/lovspor/publication_seal.py | 48 |
| src/lovspor/identity/corpus_git.py | 47 |
| src/lovspor/forarbeider/version_diff.py | 42 |
| src/lovspor/forarbeider/version_release.py | 42 |
| src/lovspor/forarbeider/document_spans.py | 41 |
| src/lovspor/forarbeider/release_plan.py | 40 |
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
| src/lovspor/forarbeider/version_manifest.py | 26 |
| src/lovspor/sync/delta_embed.py | 25 |
| src/lovspor/forarbeider/publication.py | 24 |
| src/lovspor/forarbeider/release_control.py | 24 |
| src/lovspor/forarbeider/release_limits.py | 24 |
| src/lovspor/forarbeider/release_quarantine_models.py | 24 |
| src/lovspor/forarbeider/batch_release.py | 22 |
| src/lovspor/identity/plan.py | 22 |
| src/lovspor/forarbeider/batch_apply.py | 18 |
| src/lovspor/legaltime/structure.py | 18 |
| src/lovspor/identity/registry.py | 17 |
| src/lovspor/forarbeider/version_reader.py | 16 |
| src/lovspor/identity/audit.py | 16 |
| src/lovspor/forarbeider/release_signed.py | 15 |
| src/lovspor/identity/migration.py | 15 |
| src/lovspor/sync/orchestrator.py | 15 |
| src/lovspor/forarbeider/batch_withdraw.py | 14 |
| src/lovspor/identity/commands.py | 14 |
| src/lovspor/identity/history.py | 13 |
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
| src/lovspor/identity/apply.py | 5 |
| src/lovspor/forarbeider/version_models.py | 4 |
| src/lovspor/identity/walk.py | 4 |
| src/lovspor/forarbeider/corpus_render.py | 3 |
| src/lovspor/forarbeider/release_cli.py | 3 |
| src/lovspor/forarbeider/release_reader_cli.py | 3 |
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
| src/lovspor/history.py | 1 |
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
