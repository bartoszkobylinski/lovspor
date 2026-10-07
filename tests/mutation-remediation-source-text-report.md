# Source-text mutation remediation

Scope: the sole survivor in the supplied mutation-result.json for commit
`fb6fa032f2706d45c40a7c5ddf5c0ca50a5f3043`. No registered equivalent was present.

## Survivor: BLOCKED — likely_equivalent

Location: `src/lovspor/promotion/source_text.py:216`, `_lines_of` (symbol starts at 212).

```diff
-    blocks = _block_text(copy.deepcopy(element)).split("\n")
+    blocks = _block_text(copy.copy(element)).split("\n")
```

Attempted killing test:
`tests/unit/test_promotion_source_text.py::test_reading_article_lines_preserves_nested_tree_for_next_candidate`.
It drives the real reader on nested HTML with block elements, inline emphasis,
a line break, a comment, attributes, sibling articles and trailing text. It
asserts exact lines, repeated-read stability and byte-identical serialization
of the original tree after reading both an article and the whole region.
No module implementation is mocked.

Replay: applied the supplied one-line diff by hand and ran
`uv run pytest tests/unit/test_promotion_source_text.py -q` with a writable
`UV_CACHE_DIR`. All **105 tests passed**, both before mutation and with mutation.
The attempt did not kill the survivor.

Equivalence argument: the reachable elements are lxml HTML elements from the
reader's parser. The environment uses lxml 6.1.0, also recorded in `uv.lock:866`.
The installed `lxml/etree.pyx:923-925` implements `_Element.__deepcopy__(self, memo)`
as `return self.__copy__()`. Its `__copy__` implementation at lines 927-935 uses
`_copyDocRoot(... ) # recursive`. `HtmlElement` inherits through `ElementBase`
from `_Element`, with neither HTML nor ElementBase overriding these methods.
Thus both expressions call the same recursive tree-copy operation for every
element reachable through this reader, independent of tree depth, text, tails,
attributes or comments. `_block_text` sees the same copied XML and leaves the
original unchanged in both versions. A synthetic class overriding copying
would not be an input produced by the real reader.

Human review is required to decide whether to register equivalence; no waiver
was added and the mutation gate remains unresolved.

Production restoration: `git checkout -- src/` was attempted but the sandbox
denied `.git/index.lock`. The one-line patch was reversed exactly instead;
`git diff -- src/` is empty. Final changes are under `tests/` only.

## Results

- Test addition: tree preservation and repeatability described above.
- Bugs found: none established.
- Supplied mutation score: **100/101 killed**, **1 survived**; suspicious count
  absent from the supplied JSON. Individual replay: **0/1 killed**. No new
  full mutation score is claimed; the mutation script was not rerun because
  this task restricts execution to touched test files.
- Coverage delta: not measured; no coverage run was requested for this task.
- Edge cases exercised: nested blocks, inline text, breaks, comments, sibling
  articles and trailing text. No additional product decision was identified.
