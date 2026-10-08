# Title mutation remediation

Input: mutation-result.json for commit ea97a7750c12cc02aa90e17e65daf1e7061777ed.
Only its two listed survivors were inspected. Neither has an equivalent registration.

## Attempts and verdicts

Both attempts exercise the real `read_title` implementation without mocks. Added
`test_uppercase_continuation_reads_first_word_across_whitespace` and
`test_dangling_title_reads_last_word_across_whitespace` in
`tests/unit/test_promotion_title.py`: each covers all seven function words and
space, repeated space, tab and nonbreaking space, including leading/trailing
whitespace and headings with enough words to exercise a second split.

### src/lovspor/promotion/title.py:47 — _continues

```diff
-    capitals = title.isupper() and line.isupper() and _dangles(line.split(maxsplit=1)[0])
+    capitals = title.isupper() and line.isupper() and _dangles(line.split(maxsplit=2)[0])
```

**BLOCKED — likely_equivalent.** Attempt: apply this change alone and run the
touched test file; all 107 cases pass. For any Python string, positive split
limits 1 and 2 produce the same first whitespace-delimited token. Extra splits
affect only subsequent elements, which this expression never reads. Strings
with no tokens would raise IndexError with either limit; here whitespace-only
strings also fail `line.isupper()` before reaching the split. Thus this change
cannot affect the result or exceptions for reachable inputs. Human review is
required to decide whether to register equivalence; no waiver was added.

### src/lovspor/promotion/title.py:48 — _continues

```diff
-    return capitals or _dangles(title.rsplit(maxsplit=1)[-1])
+    return capitals or _dangles(title.rsplit(maxsplit=2)[-1])
```

**BLOCKED — likely_equivalent.** Attempt: apply this change alone and run the
touched test file; all 107 cases pass. For any Python string, positive reverse
split limits 1 and 2 produce the same last whitespace-delimited token. Extra
splits affect only earlier elements, which this expression never reads. With no
tokens both variants raise IndexError when evaluated; `capitals` short-circuits
both identically. Thus the result and exception behavior are identical for
every reachable input. Human review is required to decide whether to register
equivalence; no waiver was added.

## Verification and limitations

- Original implementation: 107 passed; each individually mutated implementation:
  107 passed. Neither survivor was killed.
- Supplied mutation score: 95/97 killed (97.94%), 2 survived, 0 timeout. The input
  does not provide a separate suspicious count. No new mutation score is claimed.
- No production bug demonstrated. Coverage delta was not measured; only the
  touched test file was run, as requested.
- `git checkout -- src/` was attempted after each mutation but the sandbox denied
  creation of `.git/index.lock`. Exact reverse patches restored production code;
  `git diff -- src/` is empty.
- Default uv cache access was denied; runs used a writable cache under `/tmp`.
