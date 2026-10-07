# Mutation survivor remediation report

Only the 128 survivors in the supplied mutation-result.json were investigated. None had a registered equivalent field. Each diff was applied individually to its named function, then `uv run pytest tests/unit/test_promotion_batch.py -q -x` was run. Production files were restored after each check. `git checkout -- src/` was attempted but denied because the sandbox disallows `.git/index.lock`; restoration instead used the exact bytes from `git show HEAD:<path>`.

Result: **127/128 confirmed killed, 1 survived (BLOCKED likely_equivalent)**. No failed check was counted as a kill unless pytest reported a failing test. These are individual replay results, not a new full mutation score. The supplied run reported 494/622 killed and 128 survived; it did not report a suspicious count. The mutation script and full suite were not rerun, following the task’s restriction to touched test paths.

Test additions:

- `test_batch_report_bytes_and_assessment_fields`: ready, mixed, duplicate-ID, listed and approved batches; complete Markdown bytes, JSON serialization, counts, CLI output, prepared fields and hold/refusal explanations.
- `test_report_preserves_trailing_hold_detail`: trailing spaces, X and newlines in valid assessment details; preserve text while emitting exactly one final newline.
- `test_missing_spec_diagnostic`: missing spec names the path and underlying OS error.
- `test_sample_rate_diagnostic`: non-string and invalid-decimal rates retain useful exact validation messages.
- Strengthened existing dry-run and completed-write tests to check complete output lines.

Bugs found: no production bug established. Coverage delta: not measured; only the touched test file was run. Edge cases exercised: missing spec, malformed rate, unavailable artifact, nested report directories, personal data, duplicate IDs, listed subsets, mixed rejected/awaiting reviews, and trailing whitespace in report details.

## Individual verdicts

### Survivor 1: `src/lovspor/promotion/batch_commands.py` — `_spec`

```diff
--- src/lovspor/promotion/batch_commands.py
+++ src/lovspor/promotion/batch_commands.py
@@ -2,7 +2,7 @@
     try:
         return BatchSpec.model_validate_json(path.read_bytes())
     except OSError as exc:
-        msg = f"cannot read the batch spec at {path}: {exc}"
+        msg = None
         raise PromotionRefusedError(msg) from exc
     except ValidationError as exc:
         msg = f"the batch spec does not validate: {exc}"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_missing_spec_diagnostic`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 2: `src/lovspor/promotion/batch_commands.py` — `_spec`

```diff
--- src/lovspor/promotion/batch_commands.py
+++ src/lovspor/promotion/batch_commands.py
@@ -3,7 +3,7 @@
         return BatchSpec.model_validate_json(path.read_bytes())
     except OSError as exc:
         msg = f"cannot read the batch spec at {path}: {exc}"
-        raise PromotionRefusedError(msg) from exc
+        raise PromotionRefusedError(None) from exc
     except ValidationError as exc:
         msg = f"the batch spec does not validate: {exc}"
         raise PromotionRefusedError(msg) from exc
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_missing_spec_diagnostic`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 3: `src/lovspor/promotion/batch_commands.py` — `batch_impl`

```diff
--- src/lovspor/promotion/batch_commands.py
+++ src/lovspor/promotion/batch_commands.py
@@ -9,6 +9,6 @@
     if assessment.gate.verdict == "blocked":
         raise typer.Exit(BLOCKED_EXIT_CODE)
     if not request.write:
-        typer.echo("Dry run: nothing written, nothing recorded.")
+        typer.echo("XXDry run: nothing written, nothing recorded.XX")
         return
     _write_next(root, inputs, assessment, now)
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::TestGate::test_a_dry_run_of_a_passing_batch_writes_nothing`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 4: `src/lovspor/promotion/batch_commands.py` — `_write_next`

```diff
--- src/lovspor/promotion/batch_commands.py
+++ src/lovspor/promotion/batch_commands.py
@@ -3,7 +3,7 @@
 ) -> None:
     pending = assessment.to_write
     if not pending:
-        typer.echo("Nothing to write: no approved item of this batch is missing from the corpus.")
+        typer.echo("XXNothing to write: no approved item of this batch is missing from the corpus.XX")
         return
     item = pending[0]
     context = PromotionContext(
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::TestGate::test_a_passing_gate_writes_one_item_per_run_and_never_commits`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 5: `src/lovspor/promotion/batch_commands.py` — `_echo_summary`

```diff
--- src/lovspor/promotion/batch_commands.py
+++ src/lovspor/promotion/batch_commands.py
@@ -1,5 +1,5 @@
 def _echo_summary(assessment: BatchAssessment, paths: tuple[Path, Path]) -> None:
-    counts = ", ".join(f"{name} {count}" for name, count in summary(assessment).items())
+    counts = None
     typer.echo(f"Batch {assessment.spec.batch_id}: {counts}")
     for reason, count in assessment.holds_by_reason.items():
         typer.echo(f"  held {reason}: {count}")
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 6: `src/lovspor/promotion/batch_commands.py` — `_echo_summary`

```diff
--- src/lovspor/promotion/batch_commands.py
+++ src/lovspor/promotion/batch_commands.py
@@ -1,5 +1,5 @@
 def _echo_summary(assessment: BatchAssessment, paths: tuple[Path, Path]) -> None:
-    counts = ", ".join(f"{name} {count}" for name, count in summary(assessment).items())
+    counts = "XX, XX".join(f"{name} {count}" for name, count in summary(assessment).items())
     typer.echo(f"Batch {assessment.spec.batch_id}: {counts}")
     for reason, count in assessment.holds_by_reason.items():
         typer.echo(f"  held {reason}: {count}")
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 7: `src/lovspor/promotion/batch_commands.py` — `_echo_summary`

```diff
--- src/lovspor/promotion/batch_commands.py
+++ src/lovspor/promotion/batch_commands.py
@@ -1,6 +1,6 @@
 def _echo_summary(assessment: BatchAssessment, paths: tuple[Path, Path]) -> None:
     counts = ", ".join(f"{name} {count}" for name, count in summary(assessment).items())
-    typer.echo(f"Batch {assessment.spec.batch_id}: {counts}")
+    typer.echo(None)
     for reason, count in assessment.holds_by_reason.items():
         typer.echo(f"  held {reason}: {count}")
     typer.echo(f"Gate: {assessment.gate.verdict}")
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 8: `src/lovspor/promotion/batch_commands.py` — `_echo_summary`

```diff
--- src/lovspor/promotion/batch_commands.py
+++ src/lovspor/promotion/batch_commands.py
@@ -3,7 +3,7 @@
     typer.echo(f"Batch {assessment.spec.batch_id}: {counts}")
     for reason, count in assessment.holds_by_reason.items():
         typer.echo(f"  held {reason}: {count}")
-    typer.echo(f"Gate: {assessment.gate.verdict}")
+    typer.echo(None)
     for key in assessment.gate.rejected:
         typer.echo(f"  rejected in sample, blocks the batch: {key.sha256} {key.source_url}")
     if assessment.gate.awaiting_review:
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 9: `src/lovspor/promotion/batch_commands.py` — `_echo_summary`

```diff
--- src/lovspor/promotion/batch_commands.py
+++ src/lovspor/promotion/batch_commands.py
@@ -8,4 +8,4 @@
         typer.echo(f"  rejected in sample, blocks the batch: {key.sha256} {key.source_url}")
     if assessment.gate.awaiting_review:
         typer.echo(f"  {len(assessment.gate.awaiting_review)} sampled item(s) await review")
-    typer.echo(f"Report: {paths[0]} (+ {paths[1].name})")
+    typer.echo(None)
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 10: `src/lovspor/promotion/batch_commands.py` — `_echo_summary`

```diff
--- src/lovspor/promotion/batch_commands.py
+++ src/lovspor/promotion/batch_commands.py
@@ -8,4 +8,4 @@
         typer.echo(f"  rejected in sample, blocks the batch: {key.sha256} {key.source_url}")
     if assessment.gate.awaiting_review:
         typer.echo(f"  {len(assessment.gate.awaiting_review)} sampled item(s) await review")
-    typer.echo(f"Report: {paths[0]} (+ {paths[1].name})")
+    typer.echo(f"Report: {paths[1]} (+ {paths[1].name})")
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 11: `src/lovspor/promotion/batch_report.py` — `summary`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -5,7 +5,7 @@
         "candidates": len(items),
         "ready": sum(i.outcome == "ready" for i in items),
         "unchanged": sum(i.outcome == "unchanged" for i in items),
-        "held": sum(i.outcome == "held" for i in items),
+        "XXheldXX": sum(i.outcome == "held" for i in items),
         "refused": sum(i.outcome == "refused" for i in items),
         "sampled": sum(i.sampled for i in items),
         "would_write": len(assessment.to_write),
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 12: `src/lovspor/promotion/batch_report.py` — `summary`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -5,7 +5,7 @@
         "candidates": len(items),
         "ready": sum(i.outcome == "ready" for i in items),
         "unchanged": sum(i.outcome == "unchanged" for i in items),
-        "held": sum(i.outcome == "held" for i in items),
+        "HELD": sum(i.outcome == "held" for i in items),
         "refused": sum(i.outcome == "refused" for i in items),
         "sampled": sum(i.sampled for i in items),
         "would_write": len(assessment.to_write),
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 13: `src/lovspor/promotion/batch_report.py` — `summary`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -5,7 +5,7 @@
         "candidates": len(items),
         "ready": sum(i.outcome == "ready" for i in items),
         "unchanged": sum(i.outcome == "unchanged" for i in items),
-        "held": sum(i.outcome == "held" for i in items),
+        "held": sum(i.outcome != "held" for i in items),
         "refused": sum(i.outcome == "refused" for i in items),
         "sampled": sum(i.sampled for i in items),
         "would_write": len(assessment.to_write),
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 14: `src/lovspor/promotion/batch_report.py` — `summary`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -5,7 +5,7 @@
         "candidates": len(items),
         "ready": sum(i.outcome == "ready" for i in items),
         "unchanged": sum(i.outcome == "unchanged" for i in items),
-        "held": sum(i.outcome == "held" for i in items),
+        "held": sum(i.outcome == "XXheldXX" for i in items),
         "refused": sum(i.outcome == "refused" for i in items),
         "sampled": sum(i.sampled for i in items),
         "would_write": len(assessment.to_write),
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 15: `src/lovspor/promotion/batch_report.py` — `summary`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -5,7 +5,7 @@
         "candidates": len(items),
         "ready": sum(i.outcome == "ready" for i in items),
         "unchanged": sum(i.outcome == "unchanged" for i in items),
-        "held": sum(i.outcome == "held" for i in items),
+        "held": sum(i.outcome == "HELD" for i in items),
         "refused": sum(i.outcome == "refused" for i in items),
         "sampled": sum(i.sampled for i in items),
         "would_write": len(assessment.to_write),
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 16: `src/lovspor/promotion/batch_report.py` — `summary`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -6,7 +6,7 @@
         "ready": sum(i.outcome == "ready" for i in items),
         "unchanged": sum(i.outcome == "unchanged" for i in items),
         "held": sum(i.outcome == "held" for i in items),
-        "refused": sum(i.outcome == "refused" for i in items),
+        "XXrefusedXX": sum(i.outcome == "refused" for i in items),
         "sampled": sum(i.sampled for i in items),
         "would_write": len(assessment.to_write),
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 17: `src/lovspor/promotion/batch_report.py` — `summary`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -6,7 +6,7 @@
         "ready": sum(i.outcome == "ready" for i in items),
         "unchanged": sum(i.outcome == "unchanged" for i in items),
         "held": sum(i.outcome == "held" for i in items),
-        "refused": sum(i.outcome == "refused" for i in items),
+        "REFUSED": sum(i.outcome == "refused" for i in items),
         "sampled": sum(i.sampled for i in items),
         "would_write": len(assessment.to_write),
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 18: `src/lovspor/promotion/batch_report.py` — `summary`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -6,7 +6,7 @@
         "ready": sum(i.outcome == "ready" for i in items),
         "unchanged": sum(i.outcome == "unchanged" for i in items),
         "held": sum(i.outcome == "held" for i in items),
-        "refused": sum(i.outcome == "refused" for i in items),
+        "refused": sum(i.outcome != "refused" for i in items),
         "sampled": sum(i.sampled for i in items),
         "would_write": len(assessment.to_write),
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 19: `src/lovspor/promotion/batch_report.py` — `summary`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -6,7 +6,7 @@
         "ready": sum(i.outcome == "ready" for i in items),
         "unchanged": sum(i.outcome == "unchanged" for i in items),
         "held": sum(i.outcome == "held" for i in items),
-        "refused": sum(i.outcome == "refused" for i in items),
+        "refused": sum(i.outcome == "XXrefusedXX" for i in items),
         "sampled": sum(i.sampled for i in items),
         "would_write": len(assessment.to_write),
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 20: `src/lovspor/promotion/batch_report.py` — `summary`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -6,7 +6,7 @@
         "ready": sum(i.outcome == "ready" for i in items),
         "unchanged": sum(i.outcome == "unchanged" for i in items),
         "held": sum(i.outcome == "held" for i in items),
-        "refused": sum(i.outcome == "refused" for i in items),
+        "refused": sum(i.outcome == "REFUSED" for i in items),
         "sampled": sum(i.sampled for i in items),
         "would_write": len(assessment.to_write),
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 21: `src/lovspor/promotion/batch_report.py` — `summary`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -7,6 +7,6 @@
         "unchanged": sum(i.outcome == "unchanged" for i in items),
         "held": sum(i.outcome == "held" for i in items),
         "refused": sum(i.outcome == "refused" for i in items),
-        "sampled": sum(i.sampled for i in items),
+        "XXsampledXX": sum(i.sampled for i in items),
         "would_write": len(assessment.to_write),
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 22: `src/lovspor/promotion/batch_report.py` — `summary`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -7,6 +7,6 @@
         "unchanged": sum(i.outcome == "unchanged" for i in items),
         "held": sum(i.outcome == "held" for i in items),
         "refused": sum(i.outcome == "refused" for i in items),
-        "sampled": sum(i.sampled for i in items),
+        "SAMPLED": sum(i.sampled for i in items),
         "would_write": len(assessment.to_write),
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 23: `src/lovspor/promotion/batch_report.py` — `report_json`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
         "summary": summary(assessment),
         "holds_by_reason": assessment.holds_by_reason,
     }
-    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
+    return json.dumps(payload, sort_keys=None, indent=2, ensure_ascii=False) + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 24: `src/lovspor/promotion/batch_report.py` — `report_json`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
         "summary": summary(assessment),
         "holds_by_reason": assessment.holds_by_reason,
     }
-    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
+    return json.dumps(payload, sort_keys=True, indent=None, ensure_ascii=False) + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 25: `src/lovspor/promotion/batch_report.py` — `report_json`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
         "summary": summary(assessment),
         "holds_by_reason": assessment.holds_by_reason,
     }
-    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
+    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=None) + "\n"
```

Class: **BLOCKED — likely_equivalent**.

Attempt: `test_batch_report_bytes_and_assessment_fields` checks the exact JSON bytes for Unicode Norwegian titles across five real CLI batch scenarios. All tests passed with the mutation applied.

Equivalence argument: the only change is `ensure_ascii=False` to `ensure_ascii=None`. Both values are false in Python. Python’s JSONEncoder uses the truth value of `self.ensure_ascii` to select the string encoder; both select `encode_basestring` rather than `encode_basestring_ascii`, including in the accelerated encoder. The other JSON arguments and the payload are identical. Thus every serializable assessment produces identical bytes. No intended output behavior distinguishes the two values. Human review is required to decide whether to register equivalence; no waiver was added.

### Survivor 26: `src/lovspor/promotion/batch_report.py` — `report_json`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
         "summary": summary(assessment),
         "holds_by_reason": assessment.holds_by_reason,
     }
-    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
+    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 27: `src/lovspor/promotion/batch_report.py` — `report_json`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
         "summary": summary(assessment),
         "holds_by_reason": assessment.holds_by_reason,
     }
-    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
+    return json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 28: `src/lovspor/promotion/batch_report.py` — `report_json`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
         "summary": summary(assessment),
         "holds_by_reason": assessment.holds_by_reason,
     }
-    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
+    return json.dumps(payload, sort_keys=True, indent=2, ) + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 29: `src/lovspor/promotion/batch_report.py` — `report_json`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
         "summary": summary(assessment),
         "holds_by_reason": assessment.holds_by_reason,
     }
-    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
+    return json.dumps(payload, sort_keys=False, indent=2, ensure_ascii=False) + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 30: `src/lovspor/promotion/batch_report.py` — `report_json`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
         "summary": summary(assessment),
         "holds_by_reason": assessment.holds_by_reason,
     }
-    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
+    return json.dumps(payload, sort_keys=True, indent=3, ensure_ascii=False) + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 31: `src/lovspor/promotion/batch_report.py` — `report_json`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
         "summary": summary(assessment),
         "holds_by_reason": assessment.holds_by_reason,
     }
-    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
+    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 32: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -2,7 +2,7 @@
     lines = [
         *_heading(assessment),
         *_gate(assessment),
-        *_table(("measure", "count"), summary(assessment).items()),
+        *_table(("XXmeasureXX", "count"), summary(assessment).items()),
         "",
         "## Holds by reason",
         "",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 33: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -2,7 +2,7 @@
     lines = [
         *_heading(assessment),
         *_gate(assessment),
-        *_table(("measure", "count"), summary(assessment).items()),
+        *_table(("MEASURE", "count"), summary(assessment).items()),
         "",
         "## Holds by reason",
         "",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 34: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -2,7 +2,7 @@
     lines = [
         *_heading(assessment),
         *_gate(assessment),
-        *_table(("measure", "count"), summary(assessment).items()),
+        *_table(("measure", "XXcountXX"), summary(assessment).items()),
         "",
         "## Holds by reason",
         "",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 35: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -2,7 +2,7 @@
     lines = [
         *_heading(assessment),
         *_gate(assessment),
-        *_table(("measure", "count"), summary(assessment).items()),
+        *_table(("measure", "COUNT"), summary(assessment).items()),
         "",
         "## Holds by reason",
         "",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 36: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,7 +3,7 @@
         *_heading(assessment),
         *_gate(assessment),
         *_table(("measure", "count"), summary(assessment).items()),
-        "",
+        "XXXX",
         "## Holds by reason",
         "",
         *_table(("reason", "count"), assessment.holds_by_reason.items()),
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 37: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -4,7 +4,7 @@
         *_gate(assessment),
         *_table(("measure", "count"), summary(assessment).items()),
         "",
-        "## Holds by reason",
+        "XX## Holds by reasonXX",
         "",
         *_table(("reason", "count"), assessment.holds_by_reason.items()),
         "",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 38: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -4,7 +4,7 @@
         *_gate(assessment),
         *_table(("measure", "count"), summary(assessment).items()),
         "",
-        "## Holds by reason",
+        "## holds by reason",
         "",
         *_table(("reason", "count"), assessment.holds_by_reason.items()),
         "",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 39: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -4,7 +4,7 @@
         *_gate(assessment),
         *_table(("measure", "count"), summary(assessment).items()),
         "",
-        "## Holds by reason",
+        "## HOLDS BY REASON",
         "",
         *_table(("reason", "count"), assessment.holds_by_reason.items()),
         "",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 40: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -5,7 +5,7 @@
         *_table(("measure", "count"), summary(assessment).items()),
         "",
         "## Holds by reason",
-        "",
+        "XXXX",
         *_table(("reason", "count"), assessment.holds_by_reason.items()),
         "",
         *_sample(assessment, corpus),
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 41: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -6,7 +6,7 @@
         "",
         "## Holds by reason",
         "",
-        *_table(("reason", "count"), assessment.holds_by_reason.items()),
+        *_table(("XXreasonXX", "count"), assessment.holds_by_reason.items()),
         "",
         *_sample(assessment, corpus),
         *_holds(assessment.items),
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 42: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -6,7 +6,7 @@
         "",
         "## Holds by reason",
         "",
-        *_table(("reason", "count"), assessment.holds_by_reason.items()),
+        *_table(("REASON", "count"), assessment.holds_by_reason.items()),
         "",
         *_sample(assessment, corpus),
         *_holds(assessment.items),
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 43: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -6,7 +6,7 @@
         "",
         "## Holds by reason",
         "",
-        *_table(("reason", "count"), assessment.holds_by_reason.items()),
+        *_table(("reason", "XXcountXX"), assessment.holds_by_reason.items()),
         "",
         *_sample(assessment, corpus),
         *_holds(assessment.items),
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 44: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -6,7 +6,7 @@
         "",
         "## Holds by reason",
         "",
-        *_table(("reason", "count"), assessment.holds_by_reason.items()),
+        *_table(("reason", "COUNT"), assessment.holds_by_reason.items()),
         "",
         *_sample(assessment, corpus),
         *_holds(assessment.items),
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 45: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -7,7 +7,7 @@
         "## Holds by reason",
         "",
         *_table(("reason", "count"), assessment.holds_by_reason.items()),
-        "",
+        "XXXX",
         *_sample(assessment, corpus),
         *_holds(assessment.items),
     ]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 46: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -8,7 +8,7 @@
         "",
         *_table(("reason", "count"), assessment.holds_by_reason.items()),
         "",
-        *_sample(assessment, corpus),
+        *_sample(assessment, None),
         *_holds(assessment.items),
     ]
     return "\n".join(lines).rstrip("\n") + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 47: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -11,4 +11,4 @@
         *_sample(assessment, corpus),
         *_holds(assessment.items),
     ]
-    return "\n".join(lines).rstrip("\n") + "\n"
+    return "\n".join(lines).rstrip(None) + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_report_preserves_trailing_hold_detail[ ]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 48: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -11,4 +11,4 @@
         *_sample(assessment, corpus),
         *_holds(assessment.items),
     ]
-    return "\n".join(lines).rstrip("\n") + "\n"
+    return "\n".join(lines).lstrip("\n") + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_report_preserves_trailing_hold_detail[\n\n]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 49: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -11,4 +11,4 @@
         *_sample(assessment, corpus),
         *_holds(assessment.items),
     ]
-    return "\n".join(lines).rstrip("\n") + "\n"
+    return "XX\nXX".join(lines).rstrip("\n") + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 50: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -11,4 +11,4 @@
         *_sample(assessment, corpus),
         *_holds(assessment.items),
     ]
-    return "\n".join(lines).rstrip("\n") + "\n"
+    return "\n".join(lines).rstrip("XX\nXX") + "\n"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_report_preserves_trailing_hold_detail[X]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 51: `src/lovspor/promotion/batch_report.py` — `report_markdown`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -11,4 +11,4 @@
         *_sample(assessment, corpus),
         *_holds(assessment.items),
     ]
-    return "\n".join(lines).rstrip("\n") + "\n"
+    return "\n".join(lines).rstrip("\n") + "XX\nXX"
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 52: `src/lovspor/promotion/batch_report.py` — `write_report`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,7 +3,7 @@
 ) -> tuple[Path, Path]:
     """Write both files under ``directory``, refused inside the corpus or a ``forbidden`` tree."""
     target = _outside(directory, (corpus, *forbidden))
-    target.mkdir(parents=True, exist_ok=True)
+    target.mkdir(parents=None, exist_ok=True)
     stem = f"batch-{assessment.spec.batch_id}"
     markdown, sidecar = target / f"{stem}.md", target / f"{stem}.json"
     atomic_write_text(markdown, report_markdown(assessment, corpus))
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 53: `src/lovspor/promotion/batch_report.py` — `write_report`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,7 +3,7 @@
 ) -> tuple[Path, Path]:
     """Write both files under ``directory``, refused inside the corpus or a ``forbidden`` tree."""
     target = _outside(directory, (corpus, *forbidden))
-    target.mkdir(parents=True, exist_ok=True)
+    target.mkdir(exist_ok=True)
     stem = f"batch-{assessment.spec.batch_id}"
     markdown, sidecar = target / f"{stem}.md", target / f"{stem}.json"
     atomic_write_text(markdown, report_markdown(assessment, corpus))
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 54: `src/lovspor/promotion/batch_report.py` — `write_report`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,7 +3,7 @@
 ) -> tuple[Path, Path]:
     """Write both files under ``directory``, refused inside the corpus or a ``forbidden`` tree."""
     target = _outside(directory, (corpus, *forbidden))
-    target.mkdir(parents=True, exist_ok=True)
+    target.mkdir(parents=False, exist_ok=True)
     stem = f"batch-{assessment.spec.batch_id}"
     markdown, sidecar = target / f"{stem}.md", target / f"{stem}.json"
     atomic_write_text(markdown, report_markdown(assessment, corpus))
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 55: `src/lovspor/promotion/batch_report.py` — `write_report`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -6,6 +6,6 @@
     target.mkdir(parents=True, exist_ok=True)
     stem = f"batch-{assessment.spec.batch_id}"
     markdown, sidecar = target / f"{stem}.md", target / f"{stem}.json"
-    atomic_write_text(markdown, report_markdown(assessment, corpus))
+    atomic_write_text(markdown, report_markdown(assessment, None))
     atomic_write_text(sidecar, report_json(assessment))
     return markdown, sidecar
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 56: `src/lovspor/promotion/batch_report.py` — `_heading`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -2,7 +2,7 @@
     spec = assessment.spec
     return [
         f"# Promotion batch {spec.batch_id}",
-        "",
+        "XXXX",
         f"- authority: {spec.authority_id} (KLASS {spec.klass_version})",
         f"- classifier: {spec.classifier_version}, output sha256 "
         f"{assessment.classifier_output_sha256}",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 57: `src/lovspor/promotion/batch_report.py` — `_heading`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -8,7 +8,7 @@
         f"{assessment.classifier_output_sha256}",
         f"- extractor v{assessment.extractor_version}, renderer v{assessment.renderer_version}",
         f"- sample rate: {spec.sample_rate} (stated in the batch spec; ADR-0016 4g recommends 1 "
-        "= 100 % for the first authority and every new adapter family)",
+        "XX= 100 % for the first authority and every new adapter family)XX",
         f"- listed artifacts: {len(spec.artifacts) or 'all candidates'}",
         "",
     ]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 58: `src/lovspor/promotion/batch_report.py` — `_heading`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -8,7 +8,7 @@
         f"{assessment.classifier_output_sha256}",
         f"- extractor v{assessment.extractor_version}, renderer v{assessment.renderer_version}",
         f"- sample rate: {spec.sample_rate} (stated in the batch spec; ADR-0016 4g recommends 1 "
-        "= 100 % for the first authority and every new adapter family)",
+        "= 100 % FOR THE FIRST AUTHORITY AND EVERY NEW ADAPTER FAMILY)",
         f"- listed artifacts: {len(spec.artifacts) or 'all candidates'}",
         "",
     ]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 59: `src/lovspor/promotion/batch_report.py` — `_heading`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -9,6 +9,6 @@
         f"- extractor v{assessment.extractor_version}, renderer v{assessment.renderer_version}",
         f"- sample rate: {spec.sample_rate} (stated in the batch spec; ADR-0016 4g recommends 1 "
         "= 100 % for the first authority and every new adapter family)",
-        f"- listed artifacts: {len(spec.artifacts) or 'all candidates'}",
+        f"- listed artifacts: {len(spec.artifacts) and 'all candidates'}",
         "",
     ]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 60: `src/lovspor/promotion/batch_report.py` — `_heading`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -9,6 +9,6 @@
         f"- extractor v{assessment.extractor_version}, renderer v{assessment.renderer_version}",
         f"- sample rate: {spec.sample_rate} (stated in the batch spec; ADR-0016 4g recommends 1 "
         "= 100 % for the first authority and every new adapter family)",
-        f"- listed artifacts: {len(spec.artifacts) or 'all candidates'}",
+        f"- listed artifacts: {len(spec.artifacts) or 'XXall candidatesXX'}",
         "",
     ]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 61: `src/lovspor/promotion/batch_report.py` — `_heading`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -9,6 +9,6 @@
         f"- extractor v{assessment.extractor_version}, renderer v{assessment.renderer_version}",
         f"- sample rate: {spec.sample_rate} (stated in the batch spec; ADR-0016 4g recommends 1 "
         "= 100 % for the first authority and every new adapter family)",
-        f"- listed artifacts: {len(spec.artifacts) or 'all candidates'}",
+        f"- listed artifacts: {len(spec.artifacts) or 'ALL CANDIDATES'}",
         "",
     ]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 62: `src/lovspor/promotion/batch_report.py` — `_heading`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -10,5 +10,5 @@
         f"- sample rate: {spec.sample_rate} (stated in the batch spec; ADR-0016 4g recommends 1 "
         "= 100 % for the first authority and every new adapter family)",
         f"- listed artifacts: {len(spec.artifacts) or 'all candidates'}",
-        "",
+        "XXXX",
     ]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 63: `src/lovspor/promotion/batch_report.py` — `_gate`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,6 +3,6 @@
     blocking = [
         f"- rejected in sample, blocks the batch: {k.sha256} {k.source_url}" for k in gate.rejected
     ]
-    blocking += [f"- awaiting review: {k.sha256} {k.source_url}" for k in gate.awaiting_review]
+    blocking = [f"- awaiting review: {k.sha256} {k.source_url}" for k in gate.awaiting_review]
     heading = [f"## Gate: {gate.verdict.upper()}", ""]
     return [*heading, *blocking, ""] if blocking else heading
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 64: `src/lovspor/promotion/batch_report.py` — `_gate`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -4,5 +4,5 @@
         f"- rejected in sample, blocks the batch: {k.sha256} {k.source_url}" for k in gate.rejected
     ]
     blocking += [f"- awaiting review: {k.sha256} {k.source_url}" for k in gate.awaiting_review]
-    heading = [f"## Gate: {gate.verdict.upper()}", ""]
+    heading = [f"## Gate: {gate.verdict.upper()}", "XXXX"]
     return [*heading, *blocking, ""] if blocking else heading
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 65: `src/lovspor/promotion/batch_report.py` — `_gate`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -5,4 +5,4 @@
     ]
     blocking += [f"- awaiting review: {k.sha256} {k.source_url}" for k in gate.awaiting_review]
     heading = [f"## Gate: {gate.verdict.upper()}", ""]
-    return [*heading, *blocking, ""] if blocking else heading
+    return [*heading, *blocking, ""] if (blocking) and False else heading
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 66: `src/lovspor/promotion/batch_report.py` — `_gate`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -5,4 +5,4 @@
     ]
     blocking += [f"- awaiting review: {k.sha256} {k.source_url}" for k in gate.awaiting_review]
     heading = [f"## Gate: {gate.verdict.upper()}", ""]
-    return [*heading, *blocking, ""] if blocking else heading
+    return [*heading, *blocking, ""] if (blocking) or True else heading
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[approved]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 67: `src/lovspor/promotion/batch_report.py` — `_gate`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -5,4 +5,4 @@
     ]
     blocking += [f"- awaiting review: {k.sha256} {k.source_url}" for k in gate.awaiting_review]
     heading = [f"## Gate: {gate.verdict.upper()}", ""]
-    return [*heading, *blocking, ""] if blocking else heading
+    return [*heading, *blocking, "XXXX"] if blocking else heading
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 68: `src/lovspor/promotion/batch_report.py` — `_table`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -1,3 +1,3 @@
 def _table(header: tuple[str, str], rows: Iterable[tuple[str, int]]) -> list[str]:
     body = [f"| {name} | {count} |" for name, count in rows]
-    return [f"| {header[0]} | {header[1]} |", "|---|--:|", *(body or ["| (none) | 0 |"])]
+    return [f"| {header[1]} | {header[1]} |", "|---|--:|", *(body or ["| (none) | 0 |"])]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 69: `src/lovspor/promotion/batch_report.py` — `_table`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -1,3 +1,3 @@
 def _table(header: tuple[str, str], rows: Iterable[tuple[str, int]]) -> list[str]:
     body = [f"| {name} | {count} |" for name, count in rows]
-    return [f"| {header[0]} | {header[1]} |", "|---|--:|", *(body or ["| (none) | 0 |"])]
+    return [f"| {header[0]} | {header[1]} |", "XX|---|--:|XX", *(body or ["| (none) | 0 |"])]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 70: `src/lovspor/promotion/batch_report.py` — `_table`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -1,3 +1,3 @@
 def _table(header: tuple[str, str], rows: Iterable[tuple[str, int]]) -> list[str]:
     body = [f"| {name} | {count} |" for name, count in rows]
-    return [f"| {header[0]} | {header[1]} |", "|---|--:|", *(body or ["| (none) | 0 |"])]
+    return [f"| {header[0]} | {header[1]} |", "|---|--:|", *(body or ["XX| (none) | 0 |XX"])]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 71: `src/lovspor/promotion/batch_report.py` — `_table`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -1,3 +1,3 @@
 def _table(header: tuple[str, str], rows: Iterable[tuple[str, int]]) -> list[str]:
     body = [f"| {name} | {count} |" for name, count in rows]
-    return [f"| {header[0]} | {header[1]} |", "|---|--:|", *(body or ["| (none) | 0 |"])]
+    return [f"| {header[0]} | {header[1]} |", "|---|--:|", *(body or ["| (NONE) | 0 |"])]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 72: `src/lovspor/promotion/batch_report.py` — `_sample`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -1,5 +1,5 @@
 def _sample(assessment: BatchAssessment, corpus: Path) -> list[str]:
-    lines = ["## Sample for review", ""]
+    lines = ["XX## Sample for reviewXX", ""]
     for item in (i for i in assessment.items if i.sampled):
         lines += [
             f"### {item.title}",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 73: `src/lovspor/promotion/batch_report.py` — `_sample`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -1,5 +1,5 @@
 def _sample(assessment: BatchAssessment, corpus: Path) -> list[str]:
-    lines = ["## Sample for review", ""]
+    lines = ["## sample for review", ""]
     for item in (i for i in assessment.items if i.sampled):
         lines += [
             f"### {item.title}",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 74: `src/lovspor/promotion/batch_report.py` — `_sample`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -1,5 +1,5 @@
 def _sample(assessment: BatchAssessment, corpus: Path) -> list[str]:
-    lines = ["## Sample for review", ""]
+    lines = ["## SAMPLE FOR REVIEW", ""]
     for item in (i for i in assessment.items if i.sampled):
         lines += [
             f"### {item.title}",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 75: `src/lovspor/promotion/batch_report.py` — `_sample`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -1,5 +1,5 @@
 def _sample(assessment: BatchAssessment, corpus: Path) -> list[str]:
-    lines = ["## Sample for review", ""]
+    lines = ["## Sample for review", "XXXX"]
     for item in (i for i in assessment.items if i.sampled):
         lines += [
             f"### {item.title}",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 76: `src/lovspor/promotion/batch_report.py` — `_sample`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -1,7 +1,7 @@
 def _sample(assessment: BatchAssessment, corpus: Path) -> list[str]:
     lines = ["## Sample for review", ""]
     for item in (i for i in assessment.items if i.sampled):
-        lines += [
+        lines = [
             f"### {item.title}",
             "",
             f"- {item.key.source_url}",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 77: `src/lovspor/promotion/batch_report.py` — `_sample`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,7 +3,7 @@
     for item in (i for i in assessment.items if i.sampled):
         lines += [
             f"### {item.title}",
-            "",
+            "XXXX",
             f"- {item.key.source_url}",
             f"- sha256 {item.key.sha256}",
             f"- {item.doc_id} v{item.version} -> {item.markdown_path} ({item.outcome})",
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 78: `src/lovspor/promotion/batch_report.py` — `_sample`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -8,7 +8,7 @@
             f"- sha256 {item.key.sha256}",
             f"- {item.doc_id} v{item.version} -> {item.markdown_path} ({item.outcome})",
             f"- review: {item.review.value}",
-            f"- classifier: {item.classifier.class_name} on {', '.join(item.classifier.evidence)}",
+            f"- classifier: {item.classifier.class_name} on {'XX, XX'.join(item.classifier.evidence)}",
             f"- read it: `{_preview(assessment, item, corpus)}`",
             "",
         ]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 79: `src/lovspor/promotion/batch_report.py` — `_sample`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -9,7 +9,7 @@
             f"- {item.doc_id} v{item.version} -> {item.markdown_path} ({item.outcome})",
             f"- review: {item.review.value}",
             f"- classifier: {item.classifier.class_name} on {', '.join(item.classifier.evidence)}",
-            f"- read it: `{_preview(assessment, item, corpus)}`",
+            f"- read it: `{_preview(assessment, item, None)}`",
             "",
         ]
     return lines
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 80: `src/lovspor/promotion/batch_report.py` — `_sample`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -10,6 +10,6 @@
             f"- review: {item.review.value}",
             f"- classifier: {item.classifier.class_name} on {', '.join(item.classifier.evidence)}",
             f"- read it: `{_preview(assessment, item, corpus)}`",
-            "",
+            "XXXX",
         ]
     return lines
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 81: `src/lovspor/promotion/batch_report.py` — `_preview`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -1,5 +1,5 @@
 def _preview(assessment: BatchAssessment, item: BatchItem, corpus: Path) -> str:
     spec = assessment.spec
     words = ["lovspor", "promote", "preview", "--authority", spec.authority_id]
-    words += ["--artifact", item.key.sha256, "--corpus", str(corpus)]
+    words += ["--artifact", item.key.sha256, "XX--corpusXX", str(corpus)]
     return shlex.join([*words, "--klass-version", spec.klass_version])
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 82: `src/lovspor/promotion/batch_report.py` — `_preview`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -1,5 +1,5 @@
 def _preview(assessment: BatchAssessment, item: BatchItem, corpus: Path) -> str:
     spec = assessment.spec
     words = ["lovspor", "promote", "preview", "--authority", spec.authority_id]
-    words += ["--artifact", item.key.sha256, "--corpus", str(corpus)]
+    words += ["--artifact", item.key.sha256, "--CORPUS", str(corpus)]
     return shlex.join([*words, "--klass-version", spec.klass_version])
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 83: `src/lovspor/promotion/batch_report.py` — `_preview`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -1,5 +1,5 @@
 def _preview(assessment: BatchAssessment, item: BatchItem, corpus: Path) -> str:
     spec = assessment.spec
     words = ["lovspor", "promote", "preview", "--authority", spec.authority_id]
-    words += ["--artifact", item.key.sha256, "--corpus", str(corpus)]
+    words += ["--artifact", item.key.sha256, "--corpus", str(None)]
     return shlex.join([*words, "--klass-version", spec.klass_version])
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 84: `src/lovspor/promotion/batch_report.py` — `_preview`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -2,4 +2,4 @@
     spec = assessment.spec
     words = ["lovspor", "promote", "preview", "--authority", spec.authority_id]
     words += ["--artifact", item.key.sha256, "--corpus", str(corpus)]
-    return shlex.join([*words, "--klass-version", spec.klass_version])
+    return shlex.join([*words, "XX--klass-versionXX", spec.klass_version])
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 85: `src/lovspor/promotion/batch_report.py` — `_preview`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -2,4 +2,4 @@
     spec = assessment.spec
     words = ["lovspor", "promote", "preview", "--authority", spec.authority_id]
     words += ["--artifact", item.key.sha256, "--corpus", str(corpus)]
-    return shlex.join([*words, "--klass-version", spec.klass_version])
+    return shlex.join([*words, "--KLASS-VERSION", spec.klass_version])
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 86: `src/lovspor/promotion/batch_report.py` — `_holds`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -2,5 +2,5 @@
     held: list[str] = []
     for item in (i for i in items if i.hold is not None):
         held.append(f"- `{item.hold}` {item.key.sha256} {item.key.source_url}: {item.detail}")
-        held += [f"  - personal data: {h.kind.value} on line {h.line}" for h in item.personal_data]
+        held = [f"  - personal data: {h.kind.value} on line {h.line}" for h in item.personal_data]
     return ["## Held and refused", "", *(held or ["(none)"])]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 87: `src/lovspor/promotion/batch_report.py` — `_holds`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
     for item in (i for i in items if i.hold is not None):
         held.append(f"- `{item.hold}` {item.key.sha256} {item.key.source_url}: {item.detail}")
         held += [f"  - personal data: {h.kind.value} on line {h.line}" for h in item.personal_data]
-    return ["## Held and refused", "", *(held or ["(none)"])]
+    return ["XX## Held and refusedXX", "", *(held or ["(none)"])]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 88: `src/lovspor/promotion/batch_report.py` — `_holds`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
     for item in (i for i in items if i.hold is not None):
         held.append(f"- `{item.hold}` {item.key.sha256} {item.key.source_url}: {item.detail}")
         held += [f"  - personal data: {h.kind.value} on line {h.line}" for h in item.personal_data]
-    return ["## Held and refused", "", *(held or ["(none)"])]
+    return ["## held and refused", "", *(held or ["(none)"])]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 89: `src/lovspor/promotion/batch_report.py` — `_holds`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
     for item in (i for i in items if i.hold is not None):
         held.append(f"- `{item.hold}` {item.key.sha256} {item.key.source_url}: {item.detail}")
         held += [f"  - personal data: {h.kind.value} on line {h.line}" for h in item.personal_data]
-    return ["## Held and refused", "", *(held or ["(none)"])]
+    return ["## HELD AND REFUSED", "", *(held or ["(none)"])]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 90: `src/lovspor/promotion/batch_report.py` — `_holds`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
     for item in (i for i in items if i.hold is not None):
         held.append(f"- `{item.hold}` {item.key.sha256} {item.key.source_url}: {item.detail}")
         held += [f"  - personal data: {h.kind.value} on line {h.line}" for h in item.personal_data]
-    return ["## Held and refused", "", *(held or ["(none)"])]
+    return ["## Held and refused", "XXXX", *(held or ["(none)"])]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 91: `src/lovspor/promotion/batch_report.py` — `_holds`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
     for item in (i for i in items if i.hold is not None):
         held.append(f"- `{item.hold}` {item.key.sha256} {item.key.source_url}: {item.detail}")
         held += [f"  - personal data: {h.kind.value} on line {h.line}" for h in item.personal_data]
-    return ["## Held and refused", "", *(held or ["(none)"])]
+    return ["## Held and refused", "", *(held or ["XX(none)XX"])]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 92: `src/lovspor/promotion/batch_report.py` — `_holds`

```diff
--- src/lovspor/promotion/batch_report.py
+++ src/lovspor/promotion/batch_report.py
@@ -3,4 +3,4 @@
     for item in (i for i in items if i.hold is not None):
         held.append(f"- `{item.hold}` {item.key.sha256} {item.key.source_url}: {item.detail}")
         held += [f"  - personal data: {h.kind.value} on line {h.line}" for h in item.personal_data]
-    return ["## Held and refused", "", *(held or ["(none)"])]
+    return ["## Held and refused", "", *(held or ["(NONE)"])]
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 93: `src/lovspor/promotion/batch.py` — `_rate`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -1,6 +1,6 @@
 def _rate(value: object) -> Decimal:
     if not isinstance(value, str):
-        msg = 'sample_rate is a string such as "1" (100 %) or "0.05"; it has no default'
+        msg = None
         raise ValueError(msg)
     try:
         return parse_sample_rate(value)
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_sample_rate_diagnostic[1]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 94: `src/lovspor/promotion/batch.py` — `_rate`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -1,6 +1,6 @@
 def _rate(value: object) -> Decimal:
     if not isinstance(value, str):
-        msg = 'sample_rate is a string such as "1" (100 %) or "0.05"; it has no default'
+        msg = 'XXsample_rate is a string such as "1" (100 %) or "0.05"; it has no defaultXX'
         raise ValueError(msg)
     try:
         return parse_sample_rate(value)
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_sample_rate_diagnostic[1]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 95: `src/lovspor/promotion/batch.py` — `_rate`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -1,6 +1,6 @@
 def _rate(value: object) -> Decimal:
     if not isinstance(value, str):
-        msg = 'sample_rate is a string such as "1" (100 %) or "0.05"; it has no default'
+        msg = 'SAMPLE_RATE IS A STRING SUCH AS "1" (100 %) OR "0.05"; IT HAS NO DEFAULT'
         raise ValueError(msg)
     try:
         return parse_sample_rate(value)
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_sample_rate_diagnostic[1]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 96: `src/lovspor/promotion/batch.py` — `_rate`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -1,7 +1,7 @@
 def _rate(value: object) -> Decimal:
     if not isinstance(value, str):
         msg = 'sample_rate is a string such as "1" (100 %) or "0.05"; it has no default'
-        raise ValueError(msg)
+        raise ValueError(None)
     try:
         return parse_sample_rate(value)
     except PromotionRefusedError as exc:
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_sample_rate_diagnostic[1]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 97: `src/lovspor/promotion/batch.py` — `_rate`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -5,4 +5,4 @@
     try:
         return parse_sample_rate(value)
     except PromotionRefusedError as exc:
-        raise ValueError(str(exc)) from exc
+        raise ValueError(None) from exc
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_sample_rate_diagnostic[not-a-rate]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 98: `src/lovspor/promotion/batch.py` — `_rate`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -5,4 +5,4 @@
     try:
         return parse_sample_rate(value)
     except PromotionRefusedError as exc:
-        raise ValueError(str(exc)) from exc
+        raise ValueError(str(None)) from exc
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_sample_rate_diagnostic[not-a-rate]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 99: `src/lovspor/promotion/batch.py` — `assess_item`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -7,7 +7,7 @@
         artifact = read_artifact(inputs.log, inputs.fetches, candidate.key, None)
         prepared = prepare(artifact, inputs.authority, inputs.corpus)
     except PromotionRefusedError as exc:
-        return item.model_copy(update={"detail": str(exc)})
+        return item.model_copy(update=None)
     if isinstance(prepared, Held):
         return item.model_copy(update=_held(prepared))
     review = _review(inputs.decisions.latest_decision(candidate.key), prepared)
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 100: `src/lovspor/promotion/batch.py` — `assess_item`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -7,7 +7,7 @@
         artifact = read_artifact(inputs.log, inputs.fetches, candidate.key, None)
         prepared = prepare(artifact, inputs.authority, inputs.corpus)
     except PromotionRefusedError as exc:
-        return item.model_copy(update={"detail": str(exc)})
+        return item.model_copy(update={"XXdetailXX": str(exc)})
     if isinstance(prepared, Held):
         return item.model_copy(update=_held(prepared))
     review = _review(inputs.decisions.latest_decision(candidate.key), prepared)
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 101: `src/lovspor/promotion/batch.py` — `assess_item`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -7,7 +7,7 @@
         artifact = read_artifact(inputs.log, inputs.fetches, candidate.key, None)
         prepared = prepare(artifact, inputs.authority, inputs.corpus)
     except PromotionRefusedError as exc:
-        return item.model_copy(update={"detail": str(exc)})
+        return item.model_copy(update={"DETAIL": str(exc)})
     if isinstance(prepared, Held):
         return item.model_copy(update=_held(prepared))
     review = _review(inputs.decisions.latest_decision(candidate.key), prepared)
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 102: `src/lovspor/promotion/batch.py` — `assess_item`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -7,7 +7,7 @@
         artifact = read_artifact(inputs.log, inputs.fetches, candidate.key, None)
         prepared = prepare(artifact, inputs.authority, inputs.corpus)
     except PromotionRefusedError as exc:
-        return item.model_copy(update={"detail": str(exc)})
+        return item.model_copy(update={"detail": str(None)})
     if isinstance(prepared, Held):
         return item.model_copy(update=_held(prepared))
     review = _review(inputs.decisions.latest_decision(candidate.key), prepared)
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 103: `src/lovspor/promotion/batch.py` — `_held`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -1,6 +1,6 @@
 def _held(held: Held) -> dict[str, object]:
     return {
-        "outcome": "held",
+        "XXoutcomeXX": "held",
         "hold": f"{held.stage}:{held.reason}",
         "detail": held.detail,
         "personal_data": held.personal_data,
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 104: `src/lovspor/promotion/batch.py` — `_held`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -1,6 +1,6 @@
 def _held(held: Held) -> dict[str, object]:
     return {
-        "outcome": "held",
+        "OUTCOME": "held",
         "hold": f"{held.stage}:{held.reason}",
         "detail": held.detail,
         "personal_data": held.personal_data,
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 105: `src/lovspor/promotion/batch.py` — `_held`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -1,6 +1,6 @@
 def _held(held: Held) -> dict[str, object]:
     return {
-        "outcome": "held",
+        "outcome": "XXheldXX",
         "hold": f"{held.stage}:{held.reason}",
         "detail": held.detail,
         "personal_data": held.personal_data,
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 106: `src/lovspor/promotion/batch.py` — `_held`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -1,6 +1,6 @@
 def _held(held: Held) -> dict[str, object]:
     return {
-        "outcome": "held",
+        "outcome": "HELD",
         "hold": f"{held.stage}:{held.reason}",
         "detail": held.detail,
         "personal_data": held.personal_data,
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 107: `src/lovspor/promotion/batch.py` — `_held`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -2,6 +2,6 @@
     return {
         "outcome": "held",
         "hold": f"{held.stage}:{held.reason}",
-        "detail": held.detail,
+        "XXdetailXX": held.detail,
         "personal_data": held.personal_data,
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 108: `src/lovspor/promotion/batch.py` — `_held`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -2,6 +2,6 @@
     return {
         "outcome": "held",
         "hold": f"{held.stage}:{held.reason}",
-        "detail": held.detail,
+        "DETAIL": held.detail,
         "personal_data": held.personal_data,
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[mixed]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 109: `src/lovspor/promotion/batch.py` — `_placed`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -3,7 +3,7 @@
         "outcome": "unchanged" if prepared.unchanged else "ready",
         "hold": None,
         "doc_id": prepared.identity.doc_id,
-        "title": prepared.extracted.fields.title,
+        "XXtitleXX": prepared.extracted.fields.title,
         "version": prepared.version,
         "markdown_path": prepared.markdown_path,
         "content_hash": prepared.identity.content_hash,
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 110: `src/lovspor/promotion/batch.py` — `_placed`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -3,7 +3,7 @@
         "outcome": "unchanged" if prepared.unchanged else "ready",
         "hold": None,
         "doc_id": prepared.identity.doc_id,
-        "title": prepared.extracted.fields.title,
+        "TITLE": prepared.extracted.fields.title,
         "version": prepared.version,
         "markdown_path": prepared.markdown_path,
         "content_hash": prepared.identity.content_hash,
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 111: `src/lovspor/promotion/batch.py` — `_placed`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -4,7 +4,7 @@
         "hold": None,
         "doc_id": prepared.identity.doc_id,
         "title": prepared.extracted.fields.title,
-        "version": prepared.version,
+        "XXversionXX": prepared.version,
         "markdown_path": prepared.markdown_path,
         "content_hash": prepared.identity.content_hash,
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 112: `src/lovspor/promotion/batch.py` — `_placed`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -4,7 +4,7 @@
         "hold": None,
         "doc_id": prepared.identity.doc_id,
         "title": prepared.extracted.fields.title,
-        "version": prepared.version,
+        "VERSION": prepared.version,
         "markdown_path": prepared.markdown_path,
         "content_hash": prepared.identity.content_hash,
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 113: `src/lovspor/promotion/batch.py` — `_placed`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -5,6 +5,6 @@
         "doc_id": prepared.identity.doc_id,
         "title": prepared.extracted.fields.title,
         "version": prepared.version,
-        "markdown_path": prepared.markdown_path,
+        "XXmarkdown_pathXX": prepared.markdown_path,
         "content_hash": prepared.identity.content_hash,
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 114: `src/lovspor/promotion/batch.py` — `_placed`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -5,6 +5,6 @@
         "doc_id": prepared.identity.doc_id,
         "title": prepared.extracted.fields.title,
         "version": prepared.version,
-        "markdown_path": prepared.markdown_path,
+        "MARKDOWN_PATH": prepared.markdown_path,
         "content_hash": prepared.identity.content_hash,
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 115: `src/lovspor/promotion/batch.py` — `_placed`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -6,5 +6,5 @@
         "title": prepared.extracted.fields.title,
         "version": prepared.version,
         "markdown_path": prepared.markdown_path,
-        "content_hash": prepared.identity.content_hash,
+        "XXcontent_hashXX": prepared.identity.content_hash,
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 116: `src/lovspor/promotion/batch.py` — `_placed`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -6,5 +6,5 @@
         "title": prepared.extracted.fields.title,
         "version": prepared.version,
         "markdown_path": prepared.markdown_path,
-        "content_hash": prepared.identity.content_hash,
+        "CONTENT_HASH": prepared.identity.content_hash,
     }
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[ready]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 117: `src/lovspor/promotion/batch.py` — `_hold_same_ids`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -2,7 +2,7 @@
     """Hold every promotable item whose id another item of the batch also mints."""
     minted = Counter(i.doc_id for i in items if i.promotable)
     return tuple(
-        i.model_copy(update=_same_id(i.doc_id, minted[i.doc_id]))
+        i.model_copy(update=_same_id(None, minted[i.doc_id]))
         if i.promotable and minted[i.doc_id] > 1
         else i
         for i in items
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 118: `src/lovspor/promotion/batch.py` — `_hold_same_ids`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -2,7 +2,7 @@
     """Hold every promotable item whose id another item of the batch also mints."""
     minted = Counter(i.doc_id for i in items if i.promotable)
     return tuple(
-        i.model_copy(update=_same_id(i.doc_id, minted[i.doc_id]))
+        i.model_copy(update=_same_id(i.doc_id, None))
         if i.promotable and minted[i.doc_id] > 1
         else i
         for i in items
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 119: `src/lovspor/promotion/batch.py` — `_same_id`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -1,6 +1,3 @@
 def _same_id(doc_id: str | None, count: int) -> dict[str, object]:
-    detail = (
-        f"{count} candidates of this batch mint {doc_id}; which URL is primary is a "
-        "recorded human decision (ADR-0016 1d) — promote the primary one alone"
-    )
+    detail = None
     return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 120: `src/lovspor/promotion/batch.py` — `_same_id`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -1,6 +1,6 @@
 def _same_id(doc_id: str | None, count: int) -> dict[str, object]:
     detail = (
         f"{count} candidates of this batch mint {doc_id}; which URL is primary is a "
-        "recorded human decision (ADR-0016 1d) — promote the primary one alone"
+        "XXrecorded human decision (ADR-0016 1d) — promote the primary one aloneXX"
     )
     return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 121: `src/lovspor/promotion/batch.py` — `_same_id`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -1,6 +1,6 @@
 def _same_id(doc_id: str | None, count: int) -> dict[str, object]:
     detail = (
         f"{count} candidates of this batch mint {doc_id}; which URL is primary is a "
-        "recorded human decision (ADR-0016 1d) — promote the primary one alone"
+        "recorded human decision (adr-0016 1d) — promote the primary one alone"
     )
     return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 122: `src/lovspor/promotion/batch.py` — `_same_id`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -1,6 +1,6 @@
 def _same_id(doc_id: str | None, count: int) -> dict[str, object]:
     detail = (
         f"{count} candidates of this batch mint {doc_id}; which URL is primary is a "
-        "recorded human decision (ADR-0016 1d) — promote the primary one alone"
+        "RECORDED HUMAN DECISION (ADR-0016 1D) — PROMOTE THE PRIMARY ONE ALONE"
     )
     return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 123: `src/lovspor/promotion/batch.py` — `_same_id`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -3,4 +3,4 @@
         f"{count} candidates of this batch mint {doc_id}; which URL is primary is a "
         "recorded human decision (ADR-0016 1d) — promote the primary one alone"
     )
-    return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
+    return {"XXoutcomeXX": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 124: `src/lovspor/promotion/batch.py` — `_same_id`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -3,4 +3,4 @@
         f"{count} candidates of this batch mint {doc_id}; which URL is primary is a "
         "recorded human decision (ADR-0016 1d) — promote the primary one alone"
     )
-    return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
+    return {"OUTCOME": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 125: `src/lovspor/promotion/batch.py` — `_same_id`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -3,4 +3,4 @@
         f"{count} candidates of this batch mint {doc_id}; which URL is primary is a "
         "recorded human decision (ADR-0016 1d) — promote the primary one alone"
     )
-    return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
+    return {"outcome": "XXheldXX", "hold": SAME_ID_IN_BATCH, "detail": detail}
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 126: `src/lovspor/promotion/batch.py` — `_same_id`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -3,4 +3,4 @@
         f"{count} candidates of this batch mint {doc_id}; which URL is primary is a "
         "recorded human decision (ADR-0016 1d) — promote the primary one alone"
     )
-    return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
+    return {"outcome": "HELD", "hold": SAME_ID_IN_BATCH, "detail": detail}
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 127: `src/lovspor/promotion/batch.py` — `_same_id`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -3,4 +3,4 @@
         f"{count} candidates of this batch mint {doc_id}; which URL is primary is a "
         "recorded human decision (ADR-0016 1d) — promote the primary one alone"
     )
-    return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
+    return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "XXdetailXX": detail}
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.

### Survivor 128: `src/lovspor/promotion/batch.py` — `_same_id`

```diff
--- src/lovspor/promotion/batch.py
+++ src/lovspor/promotion/batch.py
@@ -3,4 +3,4 @@
         f"{count} candidates of this batch mint {doc_id}; which URL is primary is a "
         "recorded human decision (ADR-0016 1d) — promote the primary one alone"
     )
-    return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
+    return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "DETAIL": detail}
```

Class: **killable_by_correct_test**.

Confirmed killing test: `tests/unit/test_promotion_batch.py::test_batch_report_bytes_and_assessment_fields[duplicates]`. The individually applied diff produced a pytest assertion failure; the unmutated file passes.
