# Nightly mutation, 2026-11-20

- Verdict: **survivors**
- Range: `0eeb839cb6978a1cea003715dfadc1abbb1db0fa...24f16450e135070a7a35cc74181626e0c7489bfd`
- Run: https://github.com/bartoszkobylinski/lovspor/actions/runs/44780986245
- Written by: seed: the artifact of run 44780986245, merged outside the lane on 2026-11-21; the kind of each mutant was rebuilt from its diff, the night did not record it
- Counts: `{"invalid": 0, "killed": 24810, "no_tests": 30, "skipped": 0, "survived": 2195, "timeout": 21, "total": 27056, "unmeasured": 0}`, score 91.7
- Note: 50 registered equivalent(s) are not in the register

## By kind

Not recorded: the night has no `mutation-night.json`.

## Not killed this night (2166)

<details><summary><code>src/lovspor/corpus_audit.py</code>: 5</summary>

- `_identity_schema_findings` · survived · argument: removed · `296a9c55ec9d819b`
  ```diff
  -return [AuditFinding(kind="identity_schema", path=IDENTITY_FILENAME, detail=str(exc))]
  +return [AuditFinding(kind="identity_schema", path=IDENTITY_FILENAME, )]
  ```
- `_identity_schema_findings` · survived · string: XX added · `55a57917562b9f41`
  ```diff
  -return [AuditFinding(kind="identity_schema", path=IDENTITY_FILENAME, detail=str(exc))]
  +return [AuditFinding(kind="XXidentity_schemaXX", path=IDENTITY_FILENAME, detail=str(exc))]
  ```
- `_identity_schema_findings` · survived · argument: replaced by None · `c9f687a4698a7579`
  ```diff
  -return [AuditFinding(kind="identity_schema", path=IDENTITY_FILENAME, detail=str(exc))]
  +return [AuditFinding(kind="identity_schema", path=IDENTITY_FILENAME, detail=str(None))]
  ```
- `audit_published_corpus` · survived · argument: replaced by None · `42fb8a3659ae06d2`
  ```diff
  -report = audit_corpus(corpus_root, manifest, renderer_version)
  +report = audit_corpus(corpus_root, manifest, None)
  ```
- `audit_published_corpus` · survived · argument: removed · `2814e62497156ee5`
  ```diff
  -report = audit_corpus(corpus_root, manifest, renderer_version)
  +report = audit_corpus(corpus_root, manifest, )
  ```

</details>

<details><summary><code>src/lovspor/embeddings/provenance.py</code>: 2</summary>

- `parse_attested` · survived · number · `0a79d96177657b57`
  ```diff
  -raise ConfigError(f"{ATTESTED_FILENAME}: not a digest pair: {entry[:140]!r}")
  +raise ConfigError(f"{ATTESTED_FILENAME}: not a digest pair: {entry[:141]!r}")
  ```
- `_is_sha256` · survived · string: XX added · `3b358df832af0196`
  ```diff
  -return len(value) == _SHA256_HEX and not value.strip("0123456789abcdef")
  +return len(value) == _SHA256_HEX and not value.strip("XX0123456789abcdefXX")
  ```

</details>

<details><summary><code>src/lovspor/embeddings/quantize.py</code>: 2</summary>

- `quantize_at_scale` · survived · argument: replaced by None · `fe37fc3c1cac928a`
  ```diff
  -return np.clip(np.round(vectors / scale), -_INT8_MAX, _INT8_MAX).astype(np.int8)
  +return np.clip(np.round(vectors / scale), None, _INT8_MAX).astype(np.int8)
  ```
- `quantize_at_scale` · survived · argument: replaced by None · `ac6006d1a8e8cc81`
  ```diff
  -return np.clip(np.round(vectors / scale), -_INT8_MAX, _INT8_MAX).astype(np.int8)
  +return np.clip(np.round(vectors / scale), -_INT8_MAX, None).astype(np.int8)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/attachment_discovery.py</code>: 52</summary>

- `canonical_url` · survived · string method swapped · `3cb9434277541491`
  ```diff
  -host = parts.netloc.lower().removesuffix(":443").removesuffix(":80")
  +host = parts.netloc.lower().removesuffix(":443").removeprefix(":80")
  ```
- `canonical_url` · survived · string method swapped · `bfbef54cf7c0bfc5`
  ```diff
  -host = parts.netloc.lower().removesuffix(":443").removesuffix(":80")
  +host = parts.netloc.lower().removeprefix(":443").removesuffix(":80")
  ```
- `canonical_url` · survived · string: XX added · `cca95a52ccaa701a`
  ```diff
  -host = parts.netloc.lower().removesuffix(":443").removesuffix(":80")
  +host = parts.netloc.lower().removesuffix("XX:443XX").removesuffix(":80")
  ```
- `canonical_url` · survived · string: XX added · `4bd6bfd6056e86bf`
  ```diff
  -host = parts.netloc.lower().removesuffix(":443").removesuffix(":80")
  +host = parts.netloc.lower().removesuffix(":443").removesuffix("XX:80XX")
  ```
- `_gap_key` · survived · operator: and / or · `3aaf88b7fa54a352`
  ```diff
  -return (gap.code, gap.page_sha256 or "", gap.locator or "", gap.detail or "")
  +return (gap.code, gap.page_sha256 and "", gap.locator or "", gap.detail or "")
  ```
- `_gap_key` · survived · string: XX added · `b649b26dc6046f5d`
  ```diff
  -return (gap.code, gap.page_sha256 or "", gap.locator or "", gap.detail or "")
  +return (gap.code, gap.page_sha256 or "XXXX", gap.locator or "", gap.detail or "")
  ```
- `_gap_key` · survived · operator: and / or · `4c69291b75d1d0ca`
  ```diff
  -return (gap.code, gap.page_sha256 or "", gap.locator or "", gap.detail or "")
  +return (gap.code, gap.page_sha256 or "", gap.locator and "", gap.detail or "")
  ```
- `_gap_key` · survived · string: XX added · `d07f42d56951e461`
  ```diff
  -return (gap.code, gap.page_sha256 or "", gap.locator or "", gap.detail or "")
  +return (gap.code, gap.page_sha256 or "", gap.locator or "XXXX", gap.detail or "")
  ```
- `_gap_key` · survived · operator: and / or · `bdd0dc64672a7648`
  ```diff
  -return (gap.code, gap.page_sha256 or "", gap.locator or "", gap.detail or "")
  +return (gap.code, gap.page_sha256 or "", gap.locator or "", gap.detail and "")
  ```
- `_gap_key` · survived · string: XX added · `6f4cc5b0a0d9139b`
  ```diff
  -return (gap.code, gap.page_sha256 or "", gap.locator or "", gap.detail or "")
  +return (gap.code, gap.page_sha256 or "", gap.locator or "", gap.detail or "XXXX")
  ```
- `_coverage_gaps` · survived · assignment: value to None · `c44abeb240e28105`
  ```diff
  -detail = f"ch{chapter.chapter_number}"
  +detail = None
  ```
- `_coverage_gaps` · survived · argument: replaced by None · `5b713e0b83610476`
  ```diff
  -yield DiscoveryGap(code="chapter_page_missing", detail=detail)
  +yield DiscoveryGap(code="chapter_page_missing", detail=None)
  ```
- `_coverage_gaps` · survived · argument: removed · `30de135fbd70f47b`
  ```diff
  -yield DiscoveryGap(code="chapter_page_missing", detail=detail)
  +yield DiscoveryGap(code="chapter_page_missing", )
  ```
- `_coverage_gaps` · survived · is / in negated · `a3bc61d8553a00c7`
  ```diff
  -if any(gap.code is GapCode.NAVIGATION_DISAGREES for gap in extraction.gaps):
  +if any(gap.code is not GapCode.NAVIGATION_DISAGREES for gap in extraction.gaps):
  ```
- `_coverage_gaps` · survived · argument: replaced by None · `3530099ecb465c68`
  ```diff
  -yield DiscoveryGap(code="navigation_unproved")
  +yield DiscoveryGap(code=None)
  ```
- `_coverage_gaps` · survived · string: XX added · `7aa2c9680b125530`
  ```diff
  -yield DiscoveryGap(code="navigation_unproved")
  +yield DiscoveryGap(code="XXnavigation_unprovedXX")
  ```
- `_coverage_gaps` · survived · string: upper case · `a2788b79a632ac08`
  ```diff
  -yield DiscoveryGap(code="navigation_unproved")
  +yield DiscoveryGap(code="NAVIGATION_UNPROVED")
  ```
- `_landing` · survived · argument: replaced by None · `7756ac32abf430e9`
  ```diff
  -gap = DiscoveryGap(code="landing_page_refused", page_sha256=sha, detail=error.reason.value)
  +gap = DiscoveryGap(code="landing_page_refused", page_sha256=None, detail=error.reason.value)
  ```
- `_landing` · survived · argument: replaced by None · `d1539ae14e6862e8`
  ```diff
  -gap = DiscoveryGap(code="landing_page_refused", page_sha256=sha, detail=error.reason.value)
  +gap = DiscoveryGap(code="landing_page_refused", page_sha256=sha, detail=None)
  ```
- `_landing` · survived · argument: removed · `e68587087601bce9`
  ```diff
  -gap = DiscoveryGap(code="landing_page_refused", page_sha256=sha, detail=error.reason.value)
  +gap = DiscoveryGap(code="landing_page_refused", detail=error.reason.value)
  ```
- `_landing` · survived · argument: removed · `22b6581abc92d075`
  ```diff
  -gap = DiscoveryGap(code="landing_page_refused", page_sha256=sha, detail=error.reason.value)
  +gap = DiscoveryGap(code="landing_page_refused", page_sha256=sha, )
  ```
- `_landing` · survived · argument: replaced by None · `737dd80630ba2883`
  ```diff
  -return None, None, [DiscoveryGap(code="landing_identity_mismatch", page_sha256=sha)]
  +return None, None, [DiscoveryGap(code=None, page_sha256=sha)]
  ```
- `_landing` · survived · argument: replaced by None · `0b5651d43a699aa7`
  ```diff
  -return None, None, [DiscoveryGap(code="landing_identity_mismatch", page_sha256=sha)]
  +return None, None, [DiscoveryGap(code="landing_identity_mismatch", page_sha256=None)]
  ```
- `_landing` · survived · argument: removed · `2023c878b8aac009`
  ```diff
  -return None, None, [DiscoveryGap(code="landing_identity_mismatch", page_sha256=sha)]
  +return None, None, [DiscoveryGap(page_sha256=sha)]
  ```
- `_landing` · survived · argument: removed · `620584ce3dbbabf2`
  ```diff
  -return None, None, [DiscoveryGap(code="landing_identity_mismatch", page_sha256=sha)]
  +return None, None, [DiscoveryGap(code="landing_identity_mismatch", )]
  ```
- `_landing` · survived · string: XX added · `5bb03ee611fcd086`
  ```diff
  -return None, None, [DiscoveryGap(code="landing_identity_mismatch", page_sha256=sha)]
  +return None, None, [DiscoveryGap(code="XXlanding_identity_mismatchXX", page_sha256=sha)]
  ```
- `_landing` · survived · string: upper case · `0c55a24c5361f474`
  ```diff
  -return None, None, [DiscoveryGap(code="landing_identity_mismatch", page_sha256=sha)]
  +return None, None, [DiscoveryGap(code="LANDING_IDENTITY_MISMATCH", page_sha256=sha)]
  ```
- `_references` · survived · ternary: condition forced · `de5a41d52a0ad35c`
  ```diff
  -editions = _edition_urls(facts) if item.role == "landing" else frozenset()
  +editions = _edition_urls(facts) if (item.role == "landing") or True else frozenset()
  ```
- `_references` · survived · break / continue swapped · `ead654eaf645be37`
  ```diff
  -continue
  +break
  ```
- `_references` · survived · argument: removed · `6ba307b387153fbb`
  ```diff
  -gaps.append(DiscoveryGap(code="unknown_container", **found.model_dump()))
  +gaps.append(DiscoveryGap(code="unknown_container", ))
  ```
- `_edition_urls` · survived · operator: and / or · `685662a131f42c90`
  ```diff
  -if facts is None or facts.original_edition_url is None:
  +if facts is None and facts.original_edition_url is None:
  ```
- `_document_url` · survived · argument: replaced by None · `5dcae0d952fa0b42`
  ```diff
  -parts = urlsplit(canonical_url(page_url, page_url))
  +parts = urlsplit(canonical_url(None, page_url))
  ```
- `_kind` · survived · string: XX added · `c60f01908779d1a1`
  ```diff
  -return "ignored"
  +return "XXignoredXX"
  ```
- `_kind` · survived · string: upper case · `5b383df84ab8b178`
  ```diff
  -return "ignored"
  +return "IGNORED"
  ```
- `_outside_region_kind` · survived · operator: and / or · `c859598cc98f5502`
  ```diff
  -if file_like and _within_classes(element, _DOWNLOADS) and _RENDITION.search(url):
  +if file_like and _within_classes(element, _DOWNLOADS) or _RENDITION.search(url):
  ```
- `_outside_region_kind` · survived · string: XX added · `22f653fca307b0ea`
  ```diff
  -return "ignored"
  +return "XXignoredXX"
  ```
- `_outside_region_kind` · survived · string: upper case · `283d77222d96aba8`
  ```diff
  -return "ignored"
  +return "IGNORED"
  ```
- `_outside_region_kind` · survived · argument: replaced by None · `4b29be3fe71d8784`
  ```diff
  -own = urlsplit(url)._replace(query="").geturl() == context.document_url
  +own = urlsplit(url)._replace(query=None).geturl() == context.document_url
  ```
- `_harmless` · survived · operator: and / or · `956d7241ecf22356`
  ```diff
  -return not stripped or stripped.startswith("#") or stripped.lower().startswith(_IGNORED_SCHEMES)
  +return not stripped or stripped.startswith("#") and stripped.lower().startswith(_IGNORED_SCHEMES)
  ```
- `_harmless` · survived · operator: and / or · `191de591d6722e4b`
  ```diff
  -return not stripped or stripped.startswith("#") or stripped.lower().startswith(_IGNORED_SCHEMES)
  +return not stripped and stripped.startswith("#") or stripped.lower().startswith(_IGNORED_SCHEMES)
  ```
- `_harmless` · survived · string: XX added · `3bdff6dcea610a0b`
  ```diff
  -return not stripped or stripped.startswith("#") or stripped.lower().startswith(_IGNORED_SCHEMES)
  +return not stripped or stripped.startswith("XX#XX") or stripped.lower().startswith(_IGNORED_SCHEMES)
  ```
- `_harmless` · survived · string method swapped · `ed4b1cbd0d2daa15`
  ```diff
  -return not stripped or stripped.startswith("#") or stripped.lower().startswith(_IGNORED_SCHEMES)
  +return not stripped or stripped.startswith("#") or stripped.upper().startswith(_IGNORED_SCHEMES)
  ```
- `_region_kind` · survived · operator: and / or · `b0b2cba33be73969`
  ```diff
  -if element.tag in _EMBEDS or element.get("href") is None:
  +if element.tag in _EMBEDS and element.get("href") is None:
  ```
- `_region_kind` · survived · string: XX added · `41bfc4e28003f3ea`
  ```diff
  -return "ignored" if urlsplit(url).scheme in {"http", "https"} else "unrecognized_reference"
  +return "ignored" if urlsplit(url).scheme in {"XXhttpXX", "https"} else "unrecognized_reference"
  ```
- `_region_kind` · survived · string: upper case · `76d819d89c84f84f`
  ```diff
  -return "ignored" if urlsplit(url).scheme in {"http", "https"} else "unrecognized_reference"
  +return "ignored" if urlsplit(url).scheme in {"HTTP", "https"} else "unrecognized_reference"
  ```
- `_within_classes` · survived · string: XX added · `65b53e66cfe489d9`
  ```diff
  -return any(set((node.get("class") or "").split()) & names for node in nodes)
  +return any(set((node.get("class") or "XXXX").split()) & names for node in nodes)
  ```
- `_suspect_container` · survived · string: XX added · `fdc98ce02f35a3b3`
  ```diff
  -classes = set((element.get("class") or "").split())
  +classes = set((element.get("class") or "XXXX").split())
  ```
- `_suspect_container` · survived · operator: and / or · `a6acefb9db9bddf8`
  ```diff
  -if not classes or classes & _DOWNLOADS or not _within_main(element) or _in(element, _REGION):
  +if not classes or classes & _DOWNLOADS or not _within_main(element) and _in(element, _REGION):
  ```
- `_rendition` · survived · ternary: condition forced · `3a1667268ca33b53`
  ```diff
  -{"url": url, "format": found["format"] if found else "", "found_at": places}
  +{"url": url, "format": found["format"] if (found) or True else "", "found_at": places}
  ```
- `_rendition` · survived · string: XX added · `4d9bd4250e357902`
  ```diff
  -{"url": url, "format": found["format"] if found else "", "found_at": places}
  +{"url": url, "format": found["format"] if found else "XXXX", "found_at": places}
  ```
- `_rendition_gaps` · survived · argument: replaced by None · `9ad5c633e350f5c9`
  ```diff
  -return [DiscoveryGap(code="renditions_disagree", detail=rendition.url)]
  +return [DiscoveryGap(code="renditions_disagree", detail=None)]
  ```
- `_rendition_gaps` · survived · argument: removed · `9af7543ddff1b07f`
  ```diff
  -return [DiscoveryGap(code="renditions_disagree", detail=rendition.url)]
  +return [DiscoveryGap(code="renditions_disagree", )]
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/batch_apply.py</code>: 18</summary>

- `owner_of` · survived · operator: comparison · `5629b8775c38b23f`
  ```diff
  -if at > now:
  +if at >= now:
  ```
- `_chapter_release` · survived · operator: and / or · `a0a60f7bad67b1fb`
  ```diff
  -if result is None or planned.clearance is None or result.text_sha256 != planned.text_sha256:
  +if result is None or planned.clearance is None and result.text_sha256 != planned.text_sha256:
  ```
- `batch_draft` · survived · argument: replaced by None · `57f17f7da47c7670`
  ```diff
  -raise _not_reproduced("base_manifest")
  +raise _not_reproduced(None)
  ```
- `batch_draft` · survived · string: XX added · `1d8d56edba0de2b4`
  ```diff
  -raise _not_reproduced("base_manifest")
  +raise _not_reproduced("XXbase_manifestXX")
  ```
- `batch_draft` · survived · string: upper case · `d5089d0717800a52`
  ```diff
  -raise _not_reproduced("base_manifest")
  +raise _not_reproduced("BASE_MANIFEST")
  ```
- `phrase_of` · survived · number · `46b2879e9ace6658`
  ```diff
  -f"apply {verdict.plan_sha256[:12]} binding {verdict.binding_sha256[:12]} "
  +f"apply {verdict.plan_sha256[:12]} binding {verdict.binding_sha256[:13]} "
  ```
- `_summary` · survived · string: XX added · `b430e4db6201cd51`
  ```diff
  -return "\n".join(lines)
  +return "XX\nXX".join(lines)
  ```
- `receipt_of` · survived · argument: replaced by None · `9632585dd2ee8734`
  ```diff
  -base_manifest_sha256=binding.base_manifest_sha256,
  +base_manifest_sha256=None,
  ```
- `_write` · survived · argument: replaced by None · `93992930b9e075d9`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CONFIRMATION_MISMATCH, "files")
  +raise ForarbeiderRefusedError(None, "files")
  ```
- `_write` · survived · argument: replaced by None · `1b84536d4a4221aa`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CONFIRMATION_MISMATCH, "files")
  +raise ForarbeiderRefusedError(RefusalReason.CONFIRMATION_MISMATCH, None)
  ```
- `_write` · survived · argument: removed · `bc6cd9bf7fd86e47`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CONFIRMATION_MISMATCH, "files")
  +raise ForarbeiderRefusedError("files")
  ```
- `_write` · survived · argument: removed · `c4be4cdf88ffe108`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CONFIRMATION_MISMATCH, "files")
  +raise ForarbeiderRefusedError(RefusalReason.CONFIRMATION_MISMATCH, )
  ```
- `_write` · survived · string: XX added · `8bb26dbd740f7c5e`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CONFIRMATION_MISMATCH, "files")
  +raise ForarbeiderRefusedError(RefusalReason.CONFIRMATION_MISMATCH, "XXfilesXX")
  ```
- `_write` · survived · string: upper case · `2be052fb7d1731a9`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CONFIRMATION_MISMATCH, "files")
  +raise ForarbeiderRefusedError(RefusalReason.CONFIRMATION_MISMATCH, "FILES")
  ```
- `_finished` · survived · operator: and / or · `0bef5e3de8d517a5`
  ```diff
  -f"base {MANIFEST_PATH} {receipt.base_manifest_sha256 or 'absent'} "
  +f"base {MANIFEST_PATH} {receipt.base_manifest_sha256 and 'absent'} "
  ```
- `_finished` · survived · string: XX added · `da419dbf8c0cb49e`
  ```diff
  -f"base {MANIFEST_PATH} {receipt.base_manifest_sha256 or 'absent'} "
  +f"base {MANIFEST_PATH} {receipt.base_manifest_sha256 or 'XXabsentXX'} "
  ```
- `_finished` · survived · string: upper case · `21e90d3feef7f3be`
  ```diff
  -f"base {MANIFEST_PATH} {receipt.base_manifest_sha256 or 'absent'} "
  +f"base {MANIFEST_PATH} {receipt.base_manifest_sha256 or 'ABSENT'} "
  ```
- `_finished` · survived · string: XX added · `18b6b0693c60d187`
  ```diff
  -return "\n".join([*lines, NOT_COMMITTED, NOT_AUTHENTICATED])
  +return "XX\nXX".join([*lines, NOT_COMMITTED, NOT_AUTHENTICATED])
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/batch_records.py</code>: 1</summary>

- `_utc` · survived · argument: replaced by None · `a0e03702bd56455d`
  ```diff
  -return value.astimezone(UTC)
  +return value.astimezone(None)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/batch_recover.py</code>: 5</summary>

- `_roll_back_release` · survived · string: XX added · `0228a18b4080f798`
  ```diff
  -" of it remains in the working tree; run batch apply again to release"
  +"XX of it remains in the working tree; run batch apply again to releaseXX"
  ```
- `_roll_back_release` · survived · string: upper case · `f024a06577a1d30c`
  ```diff
  -" of it remains in the working tree; run batch apply again to release"
  +" OF IT REMAINS IN THE WORKING TREE; RUN BATCH APPLY AGAIN TO RELEASE"
  ```
- `_finish_withdrawal` · survived · operator: and / or · `6bb3751cdb2be688`
  ```diff
  -if intent is None or digest(record_text(intent).encode()) != journal.decision_sha256:
  +if intent is None and digest(record_text(intent).encode()) != journal.decision_sha256:
  ```
- `_settle` · survived · string: XX added · `fecc5562fb7d82f7`
  ```diff
  -return "RECOVERED orphaned staging removed" if removed else "RECOVERED nothing pending"
  +return "XXRECOVERED orphaned staging removedXX" if removed else "RECOVERED nothing pending"
  ```
- `_settle` · survived · string: XX added · `3f8830845b2f4322`
  ```diff
  -return "RECOVERED orphaned staging removed" if removed else "RECOVERED nothing pending"
  +return "RECOVERED orphaned staging removed" if removed else "XXRECOVERED nothing pendingXX"
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/batch_release.py</code>: 22</summary>

- `times_of` · survived · argument: replaced by None · `8ba5a1462a52649c`
  ```diff
  -source_published_at=release.metadata.published_at,
  +source_published_at=None,
  ```
- `times_of` · survived · ternary: condition forced · `7ee23e377a771fe3`
  ```diff
  -policy_decided_at=decision.policy_decided_at if policy else None,
  +policy_decided_at=decision.policy_decided_at if (policy) or True else None,
  ```
- `rendering_of` · survived · argument: replaced by None · `29e989bb439768ef`
  ```diff
  -item.clearance.review_decided_at
  -if isinstance(item.clearance, ReviewClearance)
  -else None,
  +None,
  ```
- `rendering_of` · survived · ternary: condition forced · `46ed00903e99729e`
  ```diff
  -if isinstance(item.clearance, ReviewClearance)
  +if (isinstance(item.clearance, ReviewClearance)) and False
  ```
- `member_v2_of_release` · survived · argument: replaced by None · `71b893c29b247721`
  ```diff
  -raise _invalid("member") from error
  +raise _invalid(None) from error
  ```
- `member_v2_of_release` · survived · string: XX added · `0b23a3a959a49963`
  ```diff
  -raise _invalid("member") from error
  +raise _invalid("XXmemberXX") from error
  ```
- `member_v2_of_release` · survived · string: upper case · `a62499e079ac2d80`
  ```diff
  -raise _invalid("member") from error
  +raise _invalid("MEMBER") from error
  ```
- `_conflicts` · survived · assignment: value to None · `0d1b3299505b2c9d`
  ```diff
  -path = destination_of(document)
  +path = None
  ```
- `_conflicts` · survived · operator: and / or · `8e538d469a25d615`
  ```diff
  -or member.document.source_document_id == document.source_document_id
  -or member.path == path
  +or member.document.source_document_id == document.source_document_id and member.path == path
  ```
- `_conflicts` · survived · operator: and / or · `6c7eb078f417816c`
  ```diff
  -member.document.id == document.id
  -or member.document.source_document_id == document.source_document_id
  +member.document.id == document.id and member.document.source_document_id == document.source_document_id
  ```
- `manifest_draft` · survived · argument: replaced by None · `46c3104134c2d550`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, "manifest") from error
  +raise ForarbeiderRefusedError(None, "manifest") from error
  ```
- `manifest_draft` · survived · argument: replaced by None · `c373d2a733a3b33f`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, "manifest") from error
  +raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, None) from error
  ```
- `manifest_draft` · survived · argument: removed · `94f0434f918eb3a4`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, "manifest") from error
  +raise ForarbeiderRefusedError("manifest") from error
  ```
- `manifest_draft` · survived · argument: removed · `feaab147f36a654c`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, "manifest") from error
  +raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, ) from error
  ```
- `manifest_draft` · survived · string: XX added · `a4691fd66e3f54c3`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, "manifest") from error
  +raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, "XXmanifestXX") from error
  ```
- `manifest_draft` · survived · string: upper case · `c52d235934758b58`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, "manifest") from error
  +raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, "MANIFEST") from error
  ```
- `withdrawn_member` · survived · argument: replaced by None · `21e0dbacd8c59396`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
  +raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, None)
  ```
- `withdrawn_member` · survived · string: XX added · `0c6c9e7a639f3c9b`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
  +raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "XXdocumentXX")
  ```
- `withdrawn_member` · survived · string: upper case · `6b48bcf259974644`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
  +raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "DOCUMENT")
  ```
- `withdraw_v2_draft` · survived · argument: replaced by None · `0b267b2587b1aa9b`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
  +raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, None)
  ```
- `withdraw_v2_draft` · survived · string: XX added · `3172966034396038`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
  +raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "XXdocumentXX")
  ```
- `withdraw_v2_draft` · survived · string: upper case · `5fa7fa951b7b87a5`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
  +raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "DOCUMENT")
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/batch_withdraw.py</code>: 14</summary>

- `_completed` · survived · ternary: condition forced · `c215dcc058216e59`
  ```diff
  -changed = _changed(intent) if head == intent.head_commit else frozenset()
  +changed = _changed(intent) if (head == intent.head_commit) or True else frozenset()
  ```
- `released_member` · survived · operator: comparison · `31bb8970bf1904b3`
  ```diff
  -found = next((item for item in members if item.document.id == document), None)
  +found = next((item for item in members if item.document.id != document), None)
  ```
- `_base` · survived · argument: replaced by None · `a9343502d9416df1`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
  +raise ForarbeiderRefusedError(None, "document")
  ```
- `_base` · survived · argument: replaced by None · `6d86d3d32ef347f7`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
  +raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, None)
  ```
- `_base` · survived · argument: removed · `b38680891715f349`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
  +raise ForarbeiderRefusedError("document")
  ```
- `_base` · survived · argument: removed · `366631fbd187513d`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
  +raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, )
  ```
- `_base` · survived · string: XX added · `5f874c2645f5c129`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
  +raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "XXdocumentXX")
  ```
- `_base` · survived · string: upper case · `b3d73f92ed204fe4`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
  +raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "DOCUMENT")
  ```
- `_withdraw` · survived · argument: replaced by None · `62adc502965dbee6`
  ```diff
  -typer.echo(_preview(intent))
  +typer.echo(None)
  ```
- `_preview` · survived · assignment: value to None · `1f899eb25de03314`
  ```diff
  -chapters = ",".join(str(number) for number in intent.chapters_withdrawn)
  +chapters = None
  ```
- `_preview` · survived · string: XX added · `04b332d109409998`
  ```diff
  -chapters = ",".join(str(number) for number in intent.chapters_withdrawn)
  +chapters = "XX,XX".join(str(number) for number in intent.chapters_withdrawn)
  ```
- `_preview` · survived · argument: replaced by None · `9eb6ee82fb618b01`
  ```diff
  -chapters = ",".join(str(number) for number in intent.chapters_withdrawn)
  +chapters = ",".join(str(None) for number in intent.chapters_withdrawn)
  ```
- `_preview` · survived · string: XX added · `efc336dd6d690860`
  ```diff
  -return "\n".join([*lines, TAKEDOWN_WARNING])
  +return "XX\nXX".join([*lines, TAKEDOWN_WARNING])
  ```
- `release_withdraw_impl` · survived · string: XX added · `5cc8a11016aa335f`
  ```diff
  -return "\n".join([*lines, HISTORY_WARNING, TAKEDOWN_WARNING])
  +return "XX\nXX".join([*lines, HISTORY_WARNING, TAKEDOWN_WARNING])
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/corpus_render.py</code>: 3</summary>

- `member_v2_of` · survived · argument: replaced by None · `173489f9e52c97e2`
  ```diff
  -pairs = zip(rendering.chapters, clearances, strict=True)
  +pairs = zip(rendering.chapters, clearances, strict=None)
  ```
- `member_v2_of` · survived · argument: removed · `2bbe4260fabdda2e`
  ```diff
  -pairs = zip(rendering.chapters, clearances, strict=True)
  +pairs = zip(rendering.chapters, clearances, )
  ```
- `member_v2_of` · survived · True / False swapped · `da97513334136a4d`
  ```diff
  -pairs = zip(rendering.chapters, clearances, strict=True)
  +pairs = zip(rendering.chapters, clearances, strict=False)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/corpus_transaction.py</code>: 1</summary>

- `clear_orphaned_staging` · survived · operator: and / or · `aea546db528d894c`
  ```diff
  -if staging.is_symlink() or not staging.is_dir():
  +if staging.is_symlink() and not staging.is_dir():
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/document_classify.py</code>: 2</summary>

- `_privacy` · survived · string: XX added · `cf2130a86f5cb1de`
  ```diff
  -("special_category_term", _SPECIAL_CATEGORY),
  +("XXspecial_category_termXX", _SPECIAL_CATEGORY),
  ```
- `_privacy` · survived · string: upper case · `2db0f1ce9cd3de9b`
  ```diff
  -("special_category_term", _SPECIAL_CATEGORY),
  +("SPECIAL_CATEGORY_TERM", _SPECIAL_CATEGORY),
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/document_extract.py</code>: 33</summary>

- `extract_document` · survived · argument: removed · `669acd749dc079dc`
  ```diff
  -for index, gap in enumerate(gaps, 1)
  +for index, gap in enumerate(gaps, )
  ```
- `extract_document` · survived · number · `7d31b1060f560bf1`
  ```diff
  -for index, gap in enumerate(gaps, 1)
  +for index, gap in enumerate(gaps, 2)
  ```
- `_proved` · survived · argument: replaced by None · `d78a986ca152e051`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SOURCE_IDENTITY_MISSING, "no page proved")
  +raise ForarbeiderRefusedError(RefusalReason.SOURCE_IDENTITY_MISSING, None)
  ```
- `_proved` · survived · string: XX added · `b9d1f8e11e92123c`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SOURCE_IDENTITY_MISSING, "no page proved")
  +raise ForarbeiderRefusedError(RefusalReason.SOURCE_IDENTITY_MISSING, "XXno page provedXX")
  ```
- `_proved` · survived · string: upper case · `42d22b698429a15e`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SOURCE_IDENTITY_MISSING, "no page proved")
  +raise ForarbeiderRefusedError(RefusalReason.SOURCE_IDENTITY_MISSING, "NO PAGE PROVED")
  ```
- `_proved` · survived · argument: replaced by None · `5ab6f072bf05d559`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, "pages of two documents")
  +raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, None)
  ```
- `_proved` · survived · string: XX added · `4f5e4f0ca6c4919f`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, "pages of two documents")
  +raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, "XXpages of two documentsXX")
  ```
- `_proved` · survived · string: upper case · `5619dd3f862e85f1`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, "pages of two documents")
  +raise ForarbeiderRefusedError(RefusalReason.CONFLICTING_IDENTITY, "PAGES OF TWO DOCUMENTS")
  ```
- `_read_all` · survived · break / continue swapped · `ee5a0366b1a73e8f`
  ```diff
  -continue
  +break
  ```
- `_read_all` · survived · argument: replaced by None · `6224020f5cc10291`
  ```diff
  -refused.sort(key=lambda item: (item[0].url, hashlib.sha256(item[0].payload).hexdigest()))
  +refused.sort(key=None)
  ```
- `_document_gaps` · survived · argument: replaced by None · `b825e97be33151ed`
  ```diff
  -gaps.append(_document_gap(GapCode.NAVIGATION_DISAGREES, None, None))
  +gaps.append(None)
  ```
- `_document_gaps` · survived · argument: replaced by None · `7650249037fd9f82`
  ```diff
  -gaps.append(_document_gap(GapCode.NAVIGATION_DISAGREES, None, None))
  +gaps.append(_document_gap(None, None, None))
  ```
- `_document_gaps` · survived · argument: removed · `fd3745b9a8c67d7c`
  ```diff
  -gaps.append(_document_gap(GapCode.NAVIGATION_DISAGREES, None, None))
  +gaps.append(_document_gap(None, None))
  ```
- `_document_gaps` · survived · argument: removed · `e1d7560aa37da563`
  ```diff
  -gaps.append(_document_gap(GapCode.NAVIGATION_DISAGREES, None, None))
  +gaps.append(_document_gap(GapCode.NAVIGATION_DISAGREES, None))
  ```
- `_document_gaps` · survived · argument: removed · `9c5815b14c93f3dd`
  ```diff
  -gaps.append(_document_gap(GapCode.NAVIGATION_DISAGREES, None, None))
  +gaps.append(_document_gap(GapCode.NAVIGATION_DISAGREES, None, ))
  ```
- `_document_gaps` · survived · argument: replaced by None · `b2ad18ed3d668883`
  ```diff
  -gaps.append(_document_gap(GapCode.CONFLICTING_PAGES, chapter.url, chapter))
  +gaps.append(_document_gap(GapCode.CONFLICTING_PAGES, None, chapter))
  ```
- `_document_gaps` · survived · argument: replaced by None · `16b5809d004aa623`
  ```diff
  -gaps.append(_document_gap(GapCode.NOT_FETCHED, chapter.url, chapter))
  +gaps.append(_document_gap(GapCode.NOT_FETCHED, None, chapter))
  ```
- `_document_gaps` · survived · argument: replaced by None · `55e3eee173bd3dfa`
  ```diff
  -_document_gap(GapCode.PAGE_REFUSED, page.url, None, reason) for page, reason in refused
  +_document_gap(GapCode.PAGE_REFUSED, page.url, None, None) for page, reason in refused
  ```
- `_document_gaps` · survived · argument: removed · `9d0c3b37ab915933`
  ```diff
  -_document_gap(GapCode.PAGE_REFUSED, page.url, None, reason) for page, reason in refused
  +_document_gap(GapCode.PAGE_REFUSED, page.url, None, ) for page, reason in refused
  ```
- `_document_gap` · survived · string: XX added · `d5c0ce72b560836e`
  ```diff
  -return Gap(id="pending", code=code, scope="document", locator=locator, detail=detail)
  +return Gap(id="XXpendingXX", code=code, scope="document", locator=locator, detail=detail)
  ```
- `_document_gap` · survived · string: upper case · `390dc119978644c6`
  ```diff
  -return Gap(id="pending", code=code, scope="document", locator=locator, detail=detail)
  +return Gap(id="PENDING", code=code, scope="document", locator=locator, detail=detail)
  ```
- `_entries` · survived · ternary: condition forced · `667597775de11468`
  ```diff
  -code = GapCode.AMBIGUOUS_REGION if regions else GapCode.MISSING_REGION
  +code = GapCode.AMBIGUOUS_REGION if (regions) and False else GapCode.MISSING_REGION
  ```
- `_entries` · survived · number · `df6c6cde5a075e88`
  ```diff
  -empty = Coverage(source_characters=0, span_characters=0, gap_characters=0, exact=False)
  +empty = Coverage(source_characters=1, span_characters=0, gap_characters=0, exact=False)
  ```
- `_entries` · survived · number · `d3fbf8eecdb13eed`
  ```diff
  -empty = Coverage(source_characters=0, span_characters=0, gap_characters=0, exact=False)
  +empty = Coverage(source_characters=0, span_characters=1, gap_characters=0, exact=False)
  ```
- `_entries` · survived · number · `50cbad256fe4b1d8`
  ```diff
  -empty = Coverage(source_characters=0, span_characters=0, gap_characters=0, exact=False)
  +empty = Coverage(source_characters=0, span_characters=0, gap_characters=1, exact=False)
  ```
- `_entries` · survived · argument: replaced by None · `a19aa36ce7ab65a3`
  ```diff
  -return [_part_gap(part_id, 1, code)], empty
  +return [_part_gap(None, 1, code)], empty
  ```
- `_entries` · survived · argument: replaced by None · `a4889129f3bd30cc`
  ```diff
  -return [_part_gap(part_id, 1, code)], empty
  +return [_part_gap(part_id, None, code)], empty
  ```
- `_entries` · survived · number · `fbceee0804a7c47e`
  ```diff
  -return [_part_gap(part_id, 1, code)], empty
  +return [_part_gap(part_id, 2, code)], empty
  ```
- `_entries` · survived · argument: replaced by None · `807619bba47573b9`
  ```diff
  -codes.append(GapCode.COVERAGE_MISMATCH)
  +codes.append(None)
  ```
- `_entries` · survived · argument: replaced by None · `59862eb12e575016`
  ```diff
  -entries += [_part_gap(part_id, index, code) for index, code in enumerate(codes, 1)]
  +entries += [_part_gap(None, index, code) for index, code in enumerate(codes, 1)]
  ```
- `_entries` · survived · argument: replaced by None · `c0660a7f29ee7f01`
  ```diff
  -entries += [_part_gap(part_id, index, code) for index, code in enumerate(codes, 1)]
  +entries += [_part_gap(part_id, None, code) for index, code in enumerate(codes, 1)]
  ```
- `_entries` · survived · argument: removed · `f274360ac4925bf2`
  ```diff
  -entries += [_part_gap(part_id, index, code) for index, code in enumerate(codes, 1)]
  +entries += [_part_gap(part_id, index, code) for index, code in enumerate(codes, )]
  ```
- `_entries` · survived · number · `2b4c585daa4d0efe`
  ```diff
  -entries += [_part_gap(part_id, index, code) for index, code in enumerate(codes, 1)]
  +entries += [_part_gap(part_id, index, code) for index, code in enumerate(codes, 2)]
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/document_models.py</code>: 2</summary>

- `_level_allowed` · survived · operator: comparison · `f18878b5ade7fffe`
  ```diff
  -return 1 <= level <= _DEEPEST_HEADING
  +return 1 <= level < _DEEPEST_HEADING
  ```
- `extraction_text` · survived · argument: removed · `435adb6518ad1242`
  ```diff
  -return json.dumps(extraction.model_dump(mode="json"), **JSON_LAYOUT) + "\n"
  +return json.dumps(extraction.model_dump(mode="json"), ) + "\n"
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/document_spans.py</code>: 41</summary>

- `_classes` · survived · string: XX added · `bf2c05abb481814a`
  ```diff
  -return set((element.get("class") or "").split())
  +return set((element.get("class") or "XXXX").split())
  ```
- `_gap_code` · survived · string: XX added · `8d817f05553521d5`
  ```diff
  -if element.get("data-footnote") is not None:
  +if element.get("XXdata-footnoteXX") is not None:
  ```
- `_gap_code` · survived · string: upper case · `228a9b680a70fb6e`
  ```diff
  -if element.get("data-footnote") is not None:
  +if element.get("DATA-FOOTNOTE") is not None:
  ```
- `_is_inline` · survived · operator: and / or · `e522c4ed0902617d`
  ```diff
  -return not isinstance(child.tag, str) or child.tag == "br" or child.tag in _INLINE
  +return not isinstance(child.tag, str) and child.tag == "br" or child.tag in _INLINE
  ```
- `_is_inline` · survived · string: XX added · `bdcc255f6a7ab11f`
  ```diff
  -return not isinstance(child.tag, str) or child.tag == "br" or child.tag in _INLINE
  +return not isinstance(child.tag, str) or child.tag == "XXbrXX" or child.tag in _INLINE
  ```
- `_is_inline` · survived · string: upper case · `637d543f9c69bdfe`
  ```diff
  -return not isinstance(child.tag, str) or child.tag == "br" or child.tag in _INLINE
  +return not isinstance(child.tag, str) or child.tag == "BR" or child.tag in _INLINE
  ```
- `EntryWalker.__init__` · survived · assignment: value to None · `0a053c82ebc02f94`
  ```diff
  -self._section = "region"
  +self._section = None
  ```
- `EntryWalker.__init__` · survived · string: XX added · `9b1c6f7de6f58730`
  ```diff
  -self._section = "region"
  +self._section = "XXregionXX"
  ```
- `EntryWalker.__init__` · survived · string: upper case · `453c5f9cda143284`
  ```diff
  -self._section = "region"
  +self._section = "REGION"
  ```
- `EntryWalker._flow` · survived · argument: replaced by None · `7cd460a5e3434c19`
  ```diff
  -self._loose(child.tail, container)
  +self._loose(None, container)
  ```
- `EntryWalker._flow` · survived · argument: replaced by None · `6a13913aaee05b6b`
  ```diff
  -self._loose(child.tail, container)
  +self._loose(child.tail, None)
  ```
- `EntryWalker._block` · survived · string: XX added · `5fbd7d0765a68196`
  ```diff
  -elif tag == "div" and _classes(element) <= _WRAPPERS:
  +elif tag == "XXdivXX" and _classes(element) <= _WRAPPERS:
  ```
- `EntryWalker._block` · survived · string: upper case · `88cadf944ea99606`
  ```diff
  -elif tag == "div" and _classes(element) <= _WRAPPERS:
  +elif tag == "DIV" and _classes(element) <= _WRAPPERS:
  ```
- `EntryWalker._block` · survived · operator: comparison · `df55a7b357782d3c`
  ```diff
  -elif tag == "div" and _classes(element) <= _WRAPPERS:
  +elif tag == "div" and _classes(element) < _WRAPPERS:
  ```
- `EntryWalker._block` · survived · argument: replaced by None · `53f061b8aeaa87ae`
  ```diff
  -self._flow(element, style)
  +self._flow(None, style)
  ```
- `EntryWalker._block` · survived · argument: replaced by None · `a6d89f99a0fcc26f`
  ```diff
  -self._flow(element, style)
  +self._flow(element, None)
  ```
- `EntryWalker._block` · survived · argument: removed · `26a49a0e9ab6e673`
  ```diff
  -self._flow(element, style)
  +self._flow(style)
  ```
- `EntryWalker._block` · survived · argument: removed · `5d7af3ec9a617c29`
  ```diff
  -self._flow(element, style)
  +self._flow(element, )
  ```
- `EntryWalker._section_div` · survived · assignment: value to None · `741cf15c5b60f785`
  ```diff
  -outer = self._section
  +outer = None
  ```
- `EntryWalker._section_div` · survived · assignment: value to None · `51d5564101e93cb8`
  ```diff
  -self._section = outer
  +self._section = None
  ```
- `EntryWalker._section_name` · survived · string: XX added · `f7094659d75e4ef7`
  ```diff
  -name = element.get("id") or "section"
  +name = element.get("id") or "XXsectionXX"
  ```
- `EntryWalker._section_name` · survived · string: upper case · `297b7bf711be048d`
  ```diff
  -name = element.get("id") or "section"
  +name = element.get("id") or "SECTION"
  ```
- `EntryWalker._section_name` · survived · assignment: value to None · `39429db9914e3913`
  ```diff
  -copy = 2
  +copy = None
  ```
- `EntryWalker._section_name` · survived · number · `3755e45999d95b34`
  ```diff
  -copy = 2
  +copy = 3
  ```
- `EntryWalker._section_name` · survived · augmented assignment to plain · `25f7c2e10218c72b`
  ```diff
  -copy += 1
  +copy = 1
  ```
- `EntryWalker._section_name` · survived · operator: augmented · `e5923cf6f620ac43`
  ```diff
  -copy += 1
  +copy -= 1
  ```
- `EntryWalker._section_name` · survived · number · `3b896ca2e78dd071`
  ```diff
  -copy += 1
  +copy += 2
  ```
- `EntryWalker._section_name` · survived · argument: removed · `696e11ee777bc295`
  ```diff
  -self._gap(GapCode.DUPLICATE_SECTION_ID, element, None, scope="part")
  +self._gap(GapCode.DUPLICATE_SECTION_ID, element, None, )
  ```
- `EntryWalker._quote` · survived · ternary: condition forced · `ae8f140c83c14c74`
  ```diff
  -group = self._quotes if style.quote_group is None else style.quote_group
  +group = self._quotes if (style.quote_group is None) or True else style.quote_group
  ```
- `EntryWalker._quote` · survived · argument: removed · `656f4b6447f2a278`
  ```diff
  -style, kind="paragraph", level=0, quote_group=group, quote_depth=style.quote_depth + 1
  +style, kind="paragraph", quote_group=group, quote_depth=style.quote_depth + 1
  ```
- `EntryWalker._list` · survived · ternary: condition forced · `f8307fbba9bfc5a6`
  ```diff
  -group = self._lists if style.list_group is None or not nested else style.list_group
  +group = self._lists if (style.list_group is None or not nested) or True else style.list_group
  ```
- `EntryWalker._list` · survived · is / in negated · `09a9a7c8b2cb5f96`
  ```diff
  -group = self._lists if style.list_group is None or not nested else style.list_group
  +group = self._lists if style.list_group is not None or not nested else style.list_group
  ```
- `EntryWalker._list` · survived · ternary: condition forced · `9e715650c08fd336`
  ```diff
  -inner = replace(style, kind="list_item", level=style.level + 1 if nested else 1)
  +inner = replace(style, kind="list_item", level=style.level + 1 if (nested) or True else 1)
  ```
- `EntryWalker._list` · survived · argument: replaced by None · `be98f33b0dd17f7d`
  ```diff
  -self._loose(element.text, element)
  +self._loose(None, element)
  ```
- `EntryWalker._list` · survived · argument: replaced by None · `57af32347c6ac577`
  ```diff
  -self._loose(element.text, element)
  +self._loose(element.text, None)
  ```
- `EntryWalker._list` · survived · argument: replaced by None · `6f6abcfea1df07e9`
  ```diff
  -self._loose(item.tail, element)
  +self._loose(None, element)
  ```
- `EntryWalker._list` · survived · argument: replaced by None · `8390b3b37fddb070`
  ```diff
  -self._loose(item.tail, element)
  +self._loose(item.tail, None)
  ```
- `EntryWalker._gap` · survived · argument: replaced by None · `a52166cb91c675e8`
  ```diff
  -source_line=element.sourceline,
  +source_line=None,
  ```
- `EntryWalker._gap` · survived · argument: removed · `1896ab620e03dfab`
  ```diff
  -source_line=element.sourceline,
  ```
- `EntryWalker._locator` · survived · string: XX added · `174b271b08bdcef8`
  ```diff
  -return "/".join(reversed(steps)) or "."
  +return "/".join(reversed(steps)) or "XX.XX"
  ```
- `EntryWalker._section_name` · timeout · is / in negated · `e353bfa69c54148e`
  ```diff
  -while f"{name}~{copy}" in self._seen:
  +while f"{name}~{copy}" not in self._seen:
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/policy_authorization.py</code>: 28</summary>

- `parse_authorization` · survived · argument: replaced by None · `40bc801a0de6cc30`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None) from error
  ```
- `parse_authorization` · survived · string: XX added · `8de17f0e7a922b43`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXauthorizationXX") from error
  ```
- `parse_authorization` · survived · string: upper case · `61fb0ef965c1a923`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "AUTHORIZATION") from error
  ```
- `parse_authorization` · survived · argument: replaced by None · `9fb87a39812fdf25`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization")
  +raise ForarbeiderRefusedError(None, "authorization")
  ```
- `parse_authorization` · survived · argument: replaced by None · `d81cd765f0ee3625`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `parse_authorization` · survived · argument: removed · `4ec8d12bd66919ed`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization")
  +raise ForarbeiderRefusedError("authorization")
  ```
- `parse_authorization` · survived · argument: removed · `ecc900a8e86016dd`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, )
  ```
- `parse_authorization` · survived · string: XX added · `28900267b1167e06`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXauthorizationXX")
  ```
- `parse_authorization` · survived · string: upper case · `b06c1e3c39054cdb`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "AUTHORIZATION")
  ```
- `verify_authorization_record` · survived · string: XX added · `79a42d4a4d1db7ec`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization not reproduced")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXauthorization not reproducedXX")
  ```
- `_utc` · survived · argument: replaced by None · `2ccd2d8ee4c2255c`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, None)
  ```
- `_utc` · survived · string: XX added · `0e859274d605a6ee`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "XXcheck time must be UTCXX")
  ```
- `_utc` · survived · string: lower case · `f63820deff5a07bf`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be utc")
  ```
- `_utc` · survived · string: upper case · `c5cae5b3bbc523fc`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "CHECK TIME MUST BE UTC")
  ```
- `_signed` · survived · argument: replaced by None · `316e92f5ef741219`
  ```diff
  -proof = _Found(_id(record), _evidence(signed, found))
  +proof = _Found(None, _evidence(signed, found))
  ```
- `_parsed` · survived · string: XX added · `7d91669f211633a7`
  ```diff
  -return record, ""
  +return record, "XXXX"
  ```
- `_evidence` · survived · argument: replaced by None · `c57d619416e60f1c`
  ```diff
  -signature_sha256=sha256(signed.signature).hexdigest(),
  +signature_sha256=None,
  ```
- `_approval_reasons` · survived · operator: comparison · `f6b948666cd362a5`
  ```diff
  -if record.signed_at > ctx.at:
  +if record.signed_at >= ctx.at:
  ```
- `_binding_reasons` · survived · argument: replaced by None · `883c427864d7cc1f`
  ```diff
  -reasons.append("bound_policy_approval_not_verified")
  +reasons.append(None)
  ```
- `_binding_reasons` · survived · string: XX added · `14342506f05dc8e8`
  ```diff
  -reasons.append("bound_policy_approval_not_verified")
  +reasons.append("XXbound_policy_approval_not_verifiedXX")
  ```
- `_binding_reasons` · survived · string: upper case · `2a6831311183858b`
  ```diff
  -reasons.append("bound_policy_approval_not_verified")
  +reasons.append("BOUND_POLICY_APPROVAL_NOT_VERIFIED")
  ```
- `_verdict` · survived · argument: replaced by None · `f2f9848523dbd77e`
  ```diff
  -blockers=_blockers(ctx.root, checks, every, reference),
  +blockers=_blockers(ctx.root, checks, every, None),
  ```
- `_blockers` · survived · string: XX added · `286158cfe8b4450f`
  ```diff
  -found += [f"{c.kind}_{c.status}" for c in checks if c.status != "verified"]
  +found += [f"{c.kind}_{c.status}" for c in checks if c.status != "XXverifiedXX"]
  ```
- `_blockers` · survived · string: upper case · `cd7c42016e250bc5`
  ```diff
  -found += [f"{c.kind}_{c.status}" for c in checks if c.status != "verified"]
  +found += [f"{c.kind}_{c.status}" for c in checks if c.status != "VERIFIED"]
  ```
- `_blockers` · survived · operator: and / or · `fdf2a5cdb321f0c2`
  ```diff
  -if any(c.kind == "revocation" and c.status != "verified" for c in every):
  +if any(c.kind == "revocation" or c.status != "verified" for c in every):
  ```
- `_blockers` · survived · string: XX added · `31436fd1838bbbce`
  ```diff
  -if any(c.kind == "revocation" and c.status != "verified" for c in every):
  +if any(c.kind == "revocation" and c.status != "XXverifiedXX" for c in every):
  ```
- `_blockers` · survived · string: upper case · `56c8366c1f702c47`
  ```diff
  -if any(c.kind == "revocation" and c.status != "verified" for c in every):
  +if any(c.kind == "revocation" and c.status != "VERIFIED" for c in every):
  ```
- `_blockers` · survived · operator: and / or · `e4ce02fa5dba6202`
  ```diff
  -if checks[1].status == "verified" and reference is None:
  +if checks[1].status == "verified" or reference is None:
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/policy_authorization_models.py</code>: 5</summary>

- `_utc` · survived · operator: and / or · `885b23a8250f44ce`
  ```diff
  -if offset is None or offset.total_seconds() != 0:
  +if offset is None and offset.total_seconds() != 0:
  ```
- `_utc` · survived · argument: replaced by None · `801c808509543e4e`
  ```diff
  -raise ValueError("times are stated in UTC")
  +raise ValueError(None)
  ```
- `_utc` · survived · string: XX added · `2c2d74d77e0abdef`
  ```diff
  -raise ValueError("times are stated in UTC")
  +raise ValueError("XXtimes are stated in UTCXX")
  ```
- `_utc` · survived · string: lower case · `ac591d55adfb4475`
  ```diff
  -raise ValueError("times are stated in UTC")
  +raise ValueError("times are stated in utc")
  ```
- `_utc` · survived · string: upper case · `8f3babbb41a62c53`
  ```diff
  -raise ValueError("times are stated in UTC")
  +raise ValueError("TIMES ARE STATED IN UTC")
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/policy_evaluation.py</code>: 37</summary>

- `parse_decision` · survived · argument: replaced by None · `df030eab59997a0e`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy decision") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None) from error
  ```
- `parse_decision` · survived · string: XX added · `2ee3d78adf9fa012`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy decision") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXpolicy decisionXX") from error
  ```
- `parse_decision` · survived · string: upper case · `f909cdfbfd699594`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy decision") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "POLICY DECISION") from error
  ```
- `parse_decision` · survived · argument: replaced by None · `2ba567fadc1a7ab3`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy decision")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `parse_decision` · survived · string: XX added · `6524c0200200a681`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy decision")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXpolicy decisionXX")
  ```
- `parse_decision` · survived · string: upper case · `e054b520b4d8f6a0`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy decision")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "POLICY DECISION")
  ```
- `verify_decision` · survived · argument: replaced by None · `f5710d6fc6f30a17`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "decision not reproduced")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `verify_decision` · survived · string: XX added · `6850dd08dd9da2b6`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "decision not reproduced")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXdecision not reproducedXX")
  ```
- `verify_decision` · survived · string: upper case · `e7ef362df0f555b9`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "decision not reproduced")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "DECISION NOT REPRODUCED")
  ```
- `_entry` · survived · argument: replaced by None · `5ea74388feb2e8c9`
  ```diff
  -part_id=item.part_id,
  +part_id=None,
  ```
- `_entry` · survived · argument: replaced by None · `05ac11447dd6b4d1`
  ```diff
  -source_sha256=item.source_sha256,
  +source_sha256=None,
  ```
- `_findings` · survived · string: XX added · `d3aab9fc181990e9`
  ```diff
  -yield _Finding("unresolved", "policy_scope", "policy_not_applicable")
  +yield _Finding("unresolved", "XXpolicy_scopeXX", "policy_not_applicable")
  ```
- `_findings` · survived · string: upper case · `9f9fba4e815990e9`
  ```diff
  -yield _Finding("unresolved", "policy_scope", "policy_not_applicable")
  +yield _Finding("unresolved", "POLICY_SCOPE", "policy_not_applicable")
  ```
- `_findings` · survived · string: XX added · `f25261a00bcedc77`
  ```diff
  -yield _Finding("unresolved", "policy_scope", "policy_not_applicable")
  +yield _Finding("unresolved", "policy_scope", "XXpolicy_not_applicableXX")
  ```
- `_findings` · survived · string: upper case · `438577bf8ebf57da`
  ```diff
  -yield _Finding("unresolved", "policy_scope", "policy_not_applicable")
  +yield _Finding("unresolved", "policy_scope", "POLICY_NOT_APPLICABLE")
  ```
- `_rights_findings` · survived · string: XX added · `0b65e6378cc4a5e4`
  ```diff
  -yield _Finding("deny", "conflicting_evidence", "conflicting_evidence")
  +yield _Finding("deny", "conflicting_evidence", "XXconflicting_evidenceXX")
  ```
- `_rights_findings` · survived · string: upper case · `b0d08647bca228fc`
  ```diff
  -yield _Finding("deny", "conflicting_evidence", "conflicting_evidence")
  +yield _Finding("deny", "conflicting_evidence", "CONFLICTING_EVIDENCE")
  ```
- `_rights_findings` · survived · string: XX added · `a6b4ef526f9afbb6`
  ```diff
  -yield _Finding("unresolved", "unknown_rights", "rights_unknown")
  +yield _Finding("unresolved", "XXunknown_rightsXX", "rights_unknown")
  ```
- `_rights_findings` · survived · string: upper case · `0ac39219a0a78fd3`
  ```diff
  -yield _Finding("unresolved", "unknown_rights", "rights_unknown")
  +yield _Finding("unresolved", "UNKNOWN_RIGHTS", "rights_unknown")
  ```
- `_rights_findings` · survived · string: XX added · `cb3c283179e76185`
  ```diff
  -yield _Finding("deny", "third_party_work", "third_party_work_never_cleared")
  +yield _Finding("deny", "XXthird_party_workXX", "third_party_work_never_cleared")
  ```
- `_rights_findings` · survived · string: upper case · `744af6419ce61f79`
  ```diff
  -yield _Finding("deny", "third_party_work", "third_party_work_never_cleared")
  +yield _Finding("deny", "THIRD_PARTY_WORK", "third_party_work_never_cleared")
  ```
- `_privacy_findings` · survived · string: XX added · `e90d23a9a54506c0`
  ```diff
  -yield _Finding("unresolved", "privacy", "privacy_review_required")
  +yield _Finding("unresolved", "XXprivacyXX", "privacy_review_required")
  ```
- `_privacy_findings` · survived · string: upper case · `30b88aa01d5ccd4e`
  ```diff
  -yield _Finding("unresolved", "privacy", "privacy_review_required")
  +yield _Finding("unresolved", "PRIVACY", "privacy_review_required")
  ```
- `_privacy_findings` · survived · argument: replaced by None · `96633229ecaf11ef`
  ```diff
  -yield _Finding("unresolved", "privacy", "privacy_unresolved")
  +yield _Finding("unresolved", None, "privacy_unresolved")
  ```
- `_privacy_findings` · survived · string: XX added · `fd1ff7e62a87190c`
  ```diff
  -yield _Finding("unresolved", "privacy", "privacy_unresolved")
  +yield _Finding("unresolved", "XXprivacyXX", "privacy_unresolved")
  ```
- `_privacy_findings` · survived · string: upper case · `6d2bf23b0bb3d883`
  ```diff
  -yield _Finding("unresolved", "privacy", "privacy_unresolved")
  +yield _Finding("unresolved", "PRIVACY", "privacy_unresolved")
  ```
- `_privacy_findings` · survived · string: XX added · `4627473508b82582`
  ```diff
  -yield _Finding("unresolved", "privacy", "privacy_unresolved")
  +yield _Finding("unresolved", "privacy", "XXprivacy_unresolvedXX")
  ```
- `_privacy_findings` · survived · string: upper case · `13e05accd9ae2104`
  ```diff
  -yield _Finding("unresolved", "privacy", "privacy_unresolved")
  +yield _Finding("unresolved", "privacy", "PRIVACY_UNRESOLVED")
  ```
- `_privacy_findings` · survived · argument: replaced by None · `78409ff3d6d3407a`
  ```diff
  -yield _Finding("allow", "privacy", "privacy_residual_risk_unaccepted")
  +yield _Finding("allow", None, "privacy_residual_risk_unaccepted")
  ```
- `_privacy_findings` · survived · string: XX added · `b36f9a34f9ae5a40`
  ```diff
  -yield _Finding("allow", "privacy", "privacy_residual_risk_unaccepted")
  +yield _Finding("allow", "XXprivacyXX", "privacy_residual_risk_unaccepted")
  ```
- `_privacy_findings` · survived · string: upper case · `c5f1c19684472fe8`
  ```diff
  -yield _Finding("allow", "privacy", "privacy_residual_risk_unaccepted")
  +yield _Finding("allow", "PRIVACY", "privacy_residual_risk_unaccepted")
  ```
- `_attachment_findings` · survived · argument: replaced by None · `0a53f1b5d928c789`
  ```diff
  -yield _Finding("deny", rule, "third_party_work_never_cleared")
  +yield _Finding("deny", None, "third_party_work_never_cleared")
  ```
- `_attachment_findings` · survived · string: XX added · `b38a33b61da224f5`
  ```diff
  -yield _Finding("allow", rule, "rule_matched:captured")
  +yield _Finding("allow", rule, "XXrule_matched:capturedXX")
  ```
- `_attachment_findings` · survived · string: upper case · `669ce277547c1da5`
  ```diff
  -yield _Finding("allow", rule, "rule_matched:captured")
  +yield _Finding("allow", rule, "RULE_MATCHED:CAPTURED")
  ```
- `_document` · survived · argument: replaced by None · `e7f2bcdca0c8f8b3`
  ```diff
  -blockers.append("no_entries")
  +blockers.append(None)
  ```
- `_document` · survived · string: XX added · `5cfd22ff22b922ba`
  ```diff
  -blockers.append("no_entries")
  +blockers.append("XXno_entriesXX")
  ```
- `_document` · survived · string: upper case · `b56dd2e58b113186`
  ```diff
  -blockers.append("no_entries")
  +blockers.append("NO_ENTRIES")
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/policy_trust.py</code>: 74</summary>

- `fingerprint` · survived · string: XX added · `a5328746ba350b75`
  ```diff
  -return "SHA256:" + base64.b64encode(sha256(blob).digest()).decode().rstrip("=")
  +return "SHA256:" + base64.b64encode(sha256(blob).digest()).decode().rstrip("XX=XX")
  ```
- `parse_trust_root` · survived · string: XX added · `a9d50183539dfd2a`
  ```diff
  -raise _refused("a key is listed twice")
  +raise _refused("XXa key is listed twiceXX")
  ```
- `parse_trust_root` · survived · argument: replaced by None · `2de0ac6710925141`
  ```diff
  -raise _refused("a principal is listed twice")
  +raise _refused(None)
  ```
- `parse_trust_root` · survived · string: XX added · `c817392786287f90`
  ```diff
  -raise _refused("a principal is listed twice")
  +raise _refused("XXa principal is listed twiceXX")
  ```
- `parse_trust_root` · survived · string: upper case · `d17a3a32186e3f9b`
  ```diff
  -raise _refused("a principal is listed twice")
  +raise _refused("A PRINCIPAL IS LISTED TWICE")
  ```
- `_read` · survived · operator: comparison · `180d52e46e0cea2e`
  ```diff
  -if len(data) > MAX_TRUST_FILE_BYTES:
  +if len(data) >= MAX_TRUST_FILE_BYTES:
  ```
- `_read` · survived · argument: replaced by None · `f2b62b67e2d24444`
  ```diff
  -raise _refused(f"{path.name} is too large")
  +raise _refused(None)
  ```
- `_lines` · survived · string: upper case · `0dc6143e778aa435`
  ```diff
  -text = data.decode("ascii")
  +text = data.decode("ASCII")
  ```
- `_lines` · survived · argument: replaced by None · `f5a412318534f824`
  ```diff
  -raise _refused("not ASCII") from error
  +raise _refused(None) from error
  ```
- `_lines` · survived · string: XX added · `87d88aca31b75200`
  ```diff
  -raise _refused("not ASCII") from error
  +raise _refused("XXnot ASCIIXX") from error
  ```
- `_lines` · survived · string: lower case · `91acb83776c9314c`
  ```diff
  -raise _refused("not ASCII") from error
  +raise _refused("not ascii") from error
  ```
- `_lines` · survived · string: upper case · `f2f9580c9a7fbfee`
  ```diff
  -raise _refused("not ASCII") from error
  +raise _refused("NOT ASCII") from error
  ```
- `_entry` · survived · number · `ad3863c8bc51df85`
  ```diff
  -if len(fields) not in (4, 5) or not _PRINCIPAL.match(fields[0]):
  +if len(fields) not in (4, 6) or not _PRINCIPAL.match(fields[0]):
  ```
- `_entry` · survived · argument: replaced by None · `a0cdb668587d0c7d`
  ```diff
  -raise _refused("an entry is not <principal> <options> ssh-ed25519 <key>")
  +raise _refused(None)
  ```
- `_entry` · survived · string: XX added · `786cfedb51073822`
  ```diff
  -raise _refused("an entry is not <principal> <options> ssh-ed25519 <key>")
  +raise _refused("XXan entry is not <principal> <options> ssh-ed25519 <key>XX")
  ```
- `_entry` · survived · string: upper case · `b33cfb92e0b7384a`
  ```diff
  -raise _refused("an entry is not <principal> <options> ssh-ed25519 <key>")
  +raise _refused("AN ENTRY IS NOT <PRINCIPAL> <OPTIONS> SSH-ED25519 <KEY>")
  ```
- `_entry` · survived · operator: comparison · `dc9b4d204c2c0359`
  ```diff
  -if after >= before:
  +if after > before:
  ```
- `_entry` · survived · argument: replaced by None · `679103ae8affa9ea`
  ```diff
  -raise _refused("an entry's validity window is empty")
  +raise _refused(None)
  ```
- `_entry` · survived · string: XX added · `bec7ca1144f38ebd`
  ```diff
  -raise _refused("an entry's validity window is empty")
  +raise _refused("XXan entry's validity window is emptyXX")
  ```
- `_entry` · survived · string: upper case · `ba5c9d84ab5d48dc`
  ```diff
  -raise _refused("an entry's validity window is empty")
  +raise _refused("AN ENTRY'S VALIDITY WINDOW IS EMPTY")
  ```
- `_options` · survived · operator: and / or · `56063e75e0d5e924`
  ```diff
  -if match is None or match.group(1) in found:
  +if match is None and match.group(1) in found:
  ```
- `_options` · survived · number · `31b03ea0f260334d`
  ```diff
  -if match is None or match.group(1) in found:
  +if match is None or match.group(2) in found:
  ```
- `_options` · survived · argument: replaced by None · `3df7b01ed72c2a9c`
  ```diff
  -raise _refused("malformed options")
  +raise _refused(None)
  ```
- `_options` · survived · string: XX added · `1d20d0f31662ca63`
  ```diff
  -raise _refused("malformed options")
  +raise _refused("XXmalformed optionsXX")
  ```
- `_options` · survived · string: upper case · `d72b1663e1edae74`
  ```diff
  -raise _refused("malformed options")
  +raise _refused("MALFORMED OPTIONS")
  ```
- `_options` · survived · argument: replaced by None · `2a94350f0a9ccf72`
  ```diff
  -raise _refused("an entry needs exactly namespaces, valid-after and valid-before")
  +raise _refused(None)
  ```
- `_options` · survived · string: XX added · `960f652c0e6bc50d`
  ```diff
  -raise _refused("an entry needs exactly namespaces, valid-after and valid-before")
  +raise _refused("XXan entry needs exactly namespaces, valid-after and valid-beforeXX")
  ```
- `_options` · survived · string: upper case · `aa296a072879d1f5`
  ```diff
  -raise _refused("an entry needs exactly namespaces, valid-after and valid-before")
  +raise _refused("AN ENTRY NEEDS EXACTLY NAMESPACES, VALID-AFTER AND VALID-BEFORE")
  ```
- `_instant` · survived · argument: replaced by None · `23abc0384336450b`
  ```diff
  -raise _refused("a validity time is not YYYYMMDDHHMMSSZ")
  +raise _refused(None)
  ```
- `_instant` · survived · string: XX added · `bfe5046fb81e3565`
  ```diff
  -raise _refused("a validity time is not YYYYMMDDHHMMSSZ")
  +raise _refused("XXa validity time is not YYYYMMDDHHMMSSZXX")
  ```
- `_instant` · survived · string: lower case · `8d39750960aecf40`
  ```diff
  -raise _refused("a validity time is not YYYYMMDDHHMMSSZ")
  +raise _refused("a validity time is not yyyymmddhhmmssz")
  ```
- `_instant` · survived · string: upper case · `223f91003d9f5278`
  ```diff
  -raise _refused("a validity time is not YYYYMMDDHHMMSSZ")
  +raise _refused("A VALIDITY TIME IS NOT YYYYMMDDHHMMSSZ")
  ```
- `_instant` · survived · argument: replaced by None · `e223dca1d9e819ba`
  ```diff
  -raise _refused("a validity time is not a calendar time") from error
  +raise _refused(None) from error
  ```
- `_instant` · survived · string: XX added · `d5a6a21b6dbf64ea`
  ```diff
  -raise _refused("a validity time is not a calendar time") from error
  +raise _refused("XXa validity time is not a calendar timeXX") from error
  ```
- `_instant` · survived · string: upper case · `3c726c5da11f95f8`
  ```diff
  -raise _refused("a validity time is not a calendar time") from error
  +raise _refused("A VALIDITY TIME IS NOT A CALENDAR TIME") from error
  ```
- `_role` · survived · string: XX added · `7b87b9287a6d953f`
  ```diff
  -return "bot_execution"
  +return "XXbot_executionXX"
  ```
- `_role` · survived · string: upper case · `9537e4eea00a7658`
  ```diff
  -return "bot_execution"
  +return "BOT_EXECUTION"
  ```
- `_role` · survived · argument: replaced by None · `05d76a1ead949107`
  ```diff
  -raise _refused("an entry must hold exactly one role: owner policy or bot execution")
  +raise _refused(None)
  ```
- `_role` · survived · string: XX added · `e24a15a4b4c3144c`
  ```diff
  -raise _refused("an entry must hold exactly one role: owner policy or bot execution")
  +raise _refused("XXan entry must hold exactly one role: owner policy or bot executionXX")
  ```
- `_role` · survived · string: upper case · `d0eabf5fb88e875e`
  ```diff
  -raise _refused("an entry must hold exactly one role: owner policy or bot execution")
  +raise _refused("AN ENTRY MUST HOLD EXACTLY ONE ROLE: OWNER POLICY OR BOT EXECUTION")
  ```
- `_public_key` · survived · operator: and / or · `9b2d36653131621d`
  ```diff
  -if len(fields) not in (2, 3) or fields[0] != _KEY_TYPE:
  +if len(fields) not in (2, 3) and fields[0] != _KEY_TYPE:
  ```
- `_public_key` · survived · number · `cf9be9927cd71612`
  ```diff
  -if len(fields) not in (2, 3) or fields[0] != _KEY_TYPE:
  +if len(fields) not in (2, 4) or fields[0] != _KEY_TYPE:
  ```
- `_public_key` · survived · argument: replaced by None · `904dadc7725b9de9`
  ```diff
  -raise _refused("only ssh-ed25519 public keys are accepted")
  +raise _refused(None)
  ```
- `_public_key` · survived · string: XX added · `8244d726ca41e996`
  ```diff
  -raise _refused("only ssh-ed25519 public keys are accepted")
  +raise _refused("XXonly ssh-ed25519 public keys are acceptedXX")
  ```
- `_public_key` · survived · string: upper case · `084c29b93a8ebd8f`
  ```diff
  -raise _refused("only ssh-ed25519 public keys are accepted")
  +raise _refused("ONLY SSH-ED25519 PUBLIC KEYS ARE ACCEPTED")
  ```
- `_public_key` · survived · argument: replaced by None · `ebc51739151d727d`
  ```diff
  -blob = base64.b64decode(fields[1], validate=True)
  +blob = base64.b64decode(fields[1], validate=None)
  ```
- `_public_key` · survived · argument: removed · `9e68eaecc4ff5417`
  ```diff
  -blob = base64.b64decode(fields[1], validate=True)
  +blob = base64.b64decode(fields[1], )
  ```
- `_public_key` · survived · True / False swapped · `01199d9fddc1a7d9`
  ```diff
  -blob = base64.b64decode(fields[1], validate=True)
  +blob = base64.b64decode(fields[1], validate=False)
  ```
- `_public_key` · survived · argument: replaced by None · `ddb45aa2b12ffdb1`
  ```diff
  -raise _refused("a public key is not base64") from error
  +raise _refused(None) from error
  ```
- `_public_key` · survived · string: XX added · `6a4b793397e2d1f3`
  ```diff
  -raise _refused("a public key is not base64") from error
  +raise _refused("XXa public key is not base64XX") from error
  ```
- `_public_key` · survived · string: upper case · `cac2b6d1603b97d9`
  ```diff
  -raise _refused("a public key is not base64") from error
  +raise _refused("A PUBLIC KEY IS NOT BASE64") from error
  ```
- `_public_key` · survived · operator: and / or · `427fa72791f80195`
  ```diff
  -if parts is None or parts[0] != _KEY_TYPE.encode() or len(parts[1]) != _ED25519_KEY_BYTES:
  +if parts is None or parts[0] != _KEY_TYPE.encode() and len(parts[1]) != _ED25519_KEY_BYTES:
  ```
- `_public_key` · survived · operator: and / or · `57e730cdf295a59c`
  ```diff
  -if parts is None or parts[0] != _KEY_TYPE.encode() or len(parts[1]) != _ED25519_KEY_BYTES:
  +if parts is None and parts[0] != _KEY_TYPE.encode() or len(parts[1]) != _ED25519_KEY_BYTES:
  ```
- `_public_key` · survived · argument: replaced by None · `ca6f9a8f56c44fa4`
  ```diff
  -raise _refused("a public key blob is malformed")
  +raise _refused(None)
  ```
- `_public_key` · survived · string: XX added · `a7ed04b9740084da`
  ```diff
  -raise _refused("a public key blob is malformed")
  +raise _refused("XXa public key blob is malformedXX")
  ```
- `_public_key` · survived · string: upper case · `5945aeb1ec7d1a42`
  ```diff
  -raise _refused("a public key blob is malformed")
  +raise _refused("A PUBLIC KEY BLOB IS MALFORMED")
  ```
- `_strings` · survived · operator: arithmetic · `fd4f50ec6c3e2020`
  ```diff
  -if at + 4 > len(blob):
  +if at - 4 > len(blob):
  ```
- `_strings` · survived · number · `470a0f912771dc16`
  ```diff
  -if at + 4 > len(blob):
  +if at + 5 > len(blob):
  ```
- `_strings` · survived · operator: comparison · `4eeb3a8917047476`
  ```diff
  -if at + 4 > len(blob):
  +if at + 4 >= len(blob):
  ```
- `_strings` · survived · argument: removed · `724a298d76707cdd`
  ```diff
  -size = int.from_bytes(blob[at : at + 4], "big")
  +size = int.from_bytes(blob[at : at + 4], )
  ```
- `_strings` · survived · ternary: condition forced · `6d3f13cda1548862`
  ```diff
  -return found if at == len(blob) else None
  +return found if (at == len(blob)) or True else None
  ```
- `envelope` · survived · string: upper case · `488a1fbe5cb6f621`
  ```diff
  -return Envelope(fields[0], fields[1].decode("ascii"))
  +return Envelope(fields[0], fields[1].decode("ASCII"))
  ```
- `_dearmored` · survived · string: upper case · `3f27016b55d355c8`
  ```diff
  -lines = signature.decode("ascii").strip().splitlines()
  +lines = signature.decode("ASCII").strip().splitlines()
  ```
- `_dearmored` · survived · operator: and / or · `1c07dc0e8f6ce88f`
  ```diff
  -if len(lines) < _ARMOR_MIN_LINES or (lines[0], lines[-1]) != _ARMOR:
  +if len(lines) < _ARMOR_MIN_LINES and (lines[0], lines[-1]) != _ARMOR:
  ```
- `_dearmored` · survived · operator: comparison · `64e5d21c01cebe31`
  ```diff
  -if len(lines) < _ARMOR_MIN_LINES or (lines[0], lines[-1]) != _ARMOR:
  +if len(lines) <= _ARMOR_MIN_LINES or (lines[0], lines[-1]) != _ARMOR:
  ```
- `_dearmored` · survived · argument: replaced by None · `f9946c1cca6dff5d`
  ```diff
  -return base64.b64decode("".join(lines[1:-1]), validate=True)
  +return base64.b64decode("".join(lines[1:-1]), validate=None)
  ```
- `_dearmored` · survived · argument: removed · `32affca13c417c88`
  ```diff
  -return base64.b64decode("".join(lines[1:-1]), validate=True)
  +return base64.b64decode("".join(lines[1:-1]), )
  ```
- `_dearmored` · survived · True / False swapped · `86de01020b75b72b`
  ```diff
  -return base64.b64decode("".join(lines[1:-1]), validate=True)
  +return base64.b64decode("".join(lines[1:-1]), validate=False)
  ```
- `check_signature` · survived · ternary: condition forced · `cdf002990478a532`
  ```diff
  -claimed = envelope(signed.signature) if len(signed.signature) <= MAX_SIGNATURE_BYTES else None
  +claimed = envelope(signed.signature) if (len(signed.signature) <= MAX_SIGNATURE_BYTES) or True else None
  ```
- `check_signature` · survived · operator: comparison · `82417560590a04d9`
  ```diff
  -claimed = envelope(signed.signature) if len(signed.signature) <= MAX_SIGNATURE_BYTES else None
  +claimed = envelope(signed.signature) if len(signed.signature) < MAX_SIGNATURE_BYTES else None
  ```
- `check_signature` · survived · argument: replaced by None · `408483f0f8fd6f14`
  ```diff
  -return SignatureCheck(False, refusal or "signer_not_in_trust_root", namespace, entry)
  +return SignatureCheck(False, refusal or "signer_not_in_trust_root", None, entry)
  ```
- `check_signature` · survived · string: XX added · `2fa384d6269a4a0b`
  ```diff
  -return SignatureCheck(False, refusal or "signer_not_in_trust_root", namespace, entry)
  +return SignatureCheck(False, refusal or "XXsigner_not_in_trust_rootXX", namespace, entry)
  ```
- `check_signature` · survived · string: upper case · `3861b7c4cb5e892a`
  ```diff
  -return SignatureCheck(False, refusal or "signer_not_in_trust_root", namespace, entry)
  +return SignatureCheck(False, refusal or "SIGNER_NOT_IN_TRUST_ROOT", namespace, entry)
  ```
- `check_signature` · survived · argument: replaced by None · `3cb56879951bd320`
  ```diff
  -return SignatureCheck(reason == "signature_verified", reason, namespace, entry)
  +return SignatureCheck(reason == "signature_verified", reason, None, entry)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication.py</code>: 24</summary>

- `_checked` · survived · operator: comparison · `d5a81792ad4396e7`
  ```diff
  -if at > _now():
  +if at >= _now():
  ```
- `_checked` · survived · argument: replaced by None · `5da13d6948379f6f`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "decided")
  +raise ForarbeiderRefusedError(None, "decided")
  ```
- `_checked` · survived · argument: replaced by None · `4eaa66b2e27d5d91`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "decided")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, None)
  ```
- `_checked` · survived · argument: removed · `539fb2cd61b6181c`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "decided")
  +raise ForarbeiderRefusedError("decided")
  ```
- `_checked` · survived · argument: removed · `ed078bb6dd5fbbbb`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "decided")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, )
  ```
- `_checked` · survived · string: XX added · `ade6fb03016fe557`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "decided")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "XXdecidedXX")
  ```
- `_checked` · survived · string: upper case · `bc3b50691a738d9c`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "decided")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "DECIDED")
  ```
- `attest_impl` · survived · argument: replaced by None · `2e9b065bd56ed290`
  ```diff
  -return _lines(existing, "UNCHANGED")
  +return _lines(existing, None)
  ```
- `attest_impl` · survived · string: XX added · `b2b779d0b0cb7a4e`
  ```diff
  -return _lines(existing, "UNCHANGED")
  +return _lines(existing, "XXUNCHANGEDXX")
  ```
- `attest_impl` · survived · string: lower case · `689f9588288e0bda`
  ```diff
  -return _lines(existing, "UNCHANGED")
  +return _lines(existing, "unchanged")
  ```
- `attest_impl` · survived · assignment: value to None · `72f6fe2eccf96c11`
  ```diff
  -manifest = (state.base_manifest_sha256 or "absent")[:12]
  +manifest = None
  ```
- `attest_impl` · survived · string: XX added · `4b1f2b1150abcfa8`
  ```diff
  -manifest = (state.base_manifest_sha256 or "absent")[:12]
  +manifest = (state.base_manifest_sha256 or "XXabsentXX")[:12]
  ```
- `attest_impl` · survived · string: upper case · `928378125dcbe025`
  ```diff
  -manifest = (state.base_manifest_sha256 or "absent")[:12]
  +manifest = (state.base_manifest_sha256 or "ABSENT")[:12]
  ```
- `attest_impl` · survived · number · `2ee9c2a91b6cfd1e`
  ```diff
  -manifest = (state.base_manifest_sha256 or "absent")[:12]
  +manifest = (state.base_manifest_sha256 or "absent")[:13]
  ```
- `attest_impl` · survived · number · `52e0984fb7206e37`
  ```diff
  -phrase = f"attest {ask.commit[:12]} on main of {REMOTE} manifest {manifest}"
  +phrase = f"attest {ask.commit[:13]} on main of {REMOTE} manifest {manifest}"
  ```
- `attest_impl` · survived · argument: replaced by None · `10004abb7c39c734`
  ```diff
  -return _lines(record, "ATTESTED")
  +return _lines(record, None)
  ```
- `attest_impl` · survived · string: XX added · `2fba85236bf2cc7b`
  ```diff
  -return _lines(record, "ATTESTED")
  +return _lines(record, "XXATTESTEDXX")
  ```
- `attest_impl` · survived · string: lower case · `78c7b179895d8fc0`
  ```diff
  -return _lines(record, "ATTESTED")
  +return _lines(record, "attested")
  ```
- `_lines` · survived · string: XX added · `051dda946553e860`
  ```diff
  -return "\n".join(
  +return "XX\nXX".join(
  ```
- `_lines` · survived · operator: and / or · `16d2684beafa7e46`
  ```diff
  -f"manifest {record.manifest_sha256 or 'absent'}",
  +f"manifest {record.manifest_sha256 and 'absent'}",
  ```
- `_lines` · survived · string: XX added · `7b3768e80340d68f`
  ```diff
  -f"manifest {record.manifest_sha256 or 'absent'}",
  +f"manifest {record.manifest_sha256 or 'XXabsentXX'}",
  ```
- `_lines` · survived · string: upper case · `34f48c316acc0818`
  ```diff
  -f"manifest {record.manifest_sha256 or 'absent'}",
  +f"manifest {record.manifest_sha256 or 'ABSENT'}",
  ```
- `_lines` · survived · string: XX added · `debbd46f9c158c52`
  ```diff
  -"basis owner_asserted_unverified remote_verified=false identity_authenticated=false",
  +"XXbasis owner_asserted_unverified remote_verified=false identity_authenticated=falseXX",
  ```
- `_lines` · survived · string: upper case · `5d58acdc86dfd5a5`
  ```diff
  -"basis owner_asserted_unverified remote_verified=false identity_authenticated=false",
  +"BASIS OWNER_ASSERTED_UNVERIFIED REMOTE_VERIFIED=FALSE IDENTITY_AUTHENTICATED=FALSE",
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/publication_policy.py</code>: 6</summary>

- `parse_policy` · survived · argument: replaced by None · `cc101457b081d967`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None) from error
  ```
- `parse_policy` · survived · string: XX added · `9d6fdd5a7f0d6b71`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXpolicyXX") from error
  ```
- `parse_policy` · survived · string: upper case · `2330b804dfd4db36`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "POLICY") from error
  ```
- `parse_policy` · survived · argument: replaced by None · `5604d5342475067f`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `parse_policy` · survived · string: XX added · `ea990119576e0dd5`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXpolicyXX")
  ```
- `parse_policy` · survived · string: upper case · `c13d6654adef5153`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "POLICY")
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_checkout.py</code>: 37</summary>

- `canonical_checkout` · survived · argument: replaced by None · `930e562d92bd0fb3`
  ```diff
  -if not raw.is_absolute() or raw.resolve(strict=True) != raw:
  +if not raw.is_absolute() or raw.resolve(strict=None) != raw:
  ```
- `canonical_checkout` · survived · True / False swapped · `1e42fc070ef0c5cb`
  ```diff
  -if not raw.is_absolute() or raw.resolve(strict=True) != raw:
  +if not raw.is_absolute() or raw.resolve(strict=False) != raw:
  ```
- `forbidden_checkouts` · survived · argument: replaced by None · `db50f5a372f25774`
  ```diff
  -configured = os.environ.get("LOVVERK_CORPUS_PATH", "")
  +configured = os.environ.get("LOVVERK_CORPUS_PATH", None)
  ```
- `forbidden_checkouts` · survived · argument: removed · `40a9c0d2a56fef28`
  ```diff
  -configured = os.environ.get("LOVVERK_CORPUS_PATH", "")
  +configured = os.environ.get("LOVVERK_CORPUS_PATH", )
  ```
- `require_git_version` · survived · string: upper case · `9a2009a58db980af`
  ```diff
  -match = _GIT_VERSION.fullmatch(reading.out.decode("utf-8").strip())
  +match = _GIT_VERSION.fullmatch(reading.out.decode("UTF-8").strip())
  ```
- `_text` · survived · string: upper case · `4e098859365417f5`
  ```diff
  -return out.decode("utf-8")
  +return out.decode("UTF-8")
  ```
- `local_config` · survived · string method swapped · `fe24bd929ac5c426`
  ```diff
  -key, _, value = entry.partition("\n")
  +key, _, value = entry.rpartition("\n")
  ```
- `current_branch` · survived · argument: replaced by None · `f556a30651c11fe2`
  ```diff
  -commit = _text(head.out, "rev-parse").removesuffix("\n")
  +commit = _text(head.out, None).removesuffix("\n")
  ```
- `current_branch` · survived · string: XX added · `6b90fa897b59a306`
  ```diff
  -commit = _text(head.out, "rev-parse").removesuffix("\n")
  +commit = _text(head.out, "XXrev-parseXX").removesuffix("\n")
  ```
- `current_branch` · survived · string: upper case · `e837abb8419f8b3e`
  ```diff
  -commit = _text(head.out, "rev-parse").removesuffix("\n")
  +commit = _text(head.out, "REV-PARSE").removesuffix("\n")
  ```
- `trusted_roots` · survived · argument: replaced by None · `02cf250d3364c40a`
  ```diff
  -lines = _text(_git(path, ("rev-list", "--max-parents=0", "HEAD")), "rev-list").split()
  +lines = _text(_git(path, ("rev-list", "--max-parents=0", "HEAD")), None).split()
  ```
- `trusted_roots` · survived · string: XX added · `985e8ea7511dcf37`
  ```diff
  -lines = _text(_git(path, ("rev-list", "--max-parents=0", "HEAD")), "rev-list").split()
  +lines = _text(_git(path, ("rev-list", "--max-parents=0", "HEAD")), "XXrev-listXX").split()
  ```
- `trusted_roots` · survived · string: upper case · `053dc86959d00e5e`
  ```diff
  -lines = _text(_git(path, ("rev-list", "--max-parents=0", "HEAD")), "rev-list").split()
  +lines = _text(_git(path, ("rev-list", "--max-parents=0", "HEAD")), "REV-LIST").split()
  ```
- `status_path` · survived · argument: replaced by None · `941fa7996188ff4f`
  ```diff
  -fields = record.split(b" ", 8)
  +fields = record.split(None, 8)
  ```
- `status_path` · survived · string: upper case · `ccc83842d1a9ceb3`
  ```diff
  -return raw.decode("utf-8")
  +return raw.decode("UTF-8")
  ```
- `verify_identity` · survived · argument: replaced by None · `41fde8e939d336a1`
  ```diff
  -raise _refused(RefusalReason.CHECKOUT_PENDING_CHANGE, "lock")
  +raise _refused(None, "lock")
  ```
- `verify_identity` · survived · argument: replaced by None · `614cb89dfee66a60`
  ```diff
  -raise _refused(RefusalReason.CHECKOUT_PENDING_CHANGE, "lock")
  +raise _refused(RefusalReason.CHECKOUT_PENDING_CHANGE, None)
  ```
- `verify_identity` · survived · argument: removed · `c567c532253f7f06`
  ```diff
  -raise _refused(RefusalReason.CHECKOUT_PENDING_CHANGE, "lock")
  +raise _refused("lock")
  ```
- `verify_identity` · survived · argument: removed · `0b253f9495c3d616`
  ```diff
  -raise _refused(RefusalReason.CHECKOUT_PENDING_CHANGE, "lock")
  +raise _refused(RefusalReason.CHECKOUT_PENDING_CHANGE, )
  ```
- `verify_identity` · survived · string: XX added · `5abb743b15aea9df`
  ```diff
  -raise _refused(RefusalReason.CHECKOUT_PENDING_CHANGE, "lock")
  +raise _refused(RefusalReason.CHECKOUT_PENDING_CHANGE, "XXlockXX")
  ```
- `verify_identity` · survived · string: upper case · `b34cb5d5b238cc37`
  ```diff
  -raise _refused(RefusalReason.CHECKOUT_PENDING_CHANGE, "lock")
  +raise _refused(RefusalReason.CHECKOUT_PENDING_CHANGE, "LOCK")
  ```
- `read_back_of` · survived · argument: replaced by None · `a0830ce14728f607`
  ```diff
  -raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, "head")
  +raise _refused(None, "head")
  ```
- `read_back_of` · survived · argument: replaced by None · `00b644a53c61f99e`
  ```diff
  -raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, "head")
  +raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, None)
  ```
- `read_back_of` · survived · argument: removed · `eeab96555b480823`
  ```diff
  -raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, "head")
  +raise _refused("head")
  ```
- `read_back_of` · survived · argument: removed · `6fb4820eb501fda0`
  ```diff
  -raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, "head")
  +raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, )
  ```
- `read_back_of` · survived · string: XX added · `8bab952fdaf6ef7f`
  ```diff
  -raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, "head")
  +raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, "XXheadXX")
  ```
- `read_back_of` · survived · string: upper case · `5e86c375056a3337`
  ```diff
  -raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, "head")
  +raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, "HEAD")
  ```
- `read_back_of` · survived · argument: replaced by None · `226fc011efbf0765`
  ```diff
  -raise _refused(RefusalReason.CORPUS_LOCKED, "checkout")
  +raise _refused(None, "checkout")
  ```
- `read_back_of` · survived · argument: replaced by None · `ceebb6e5139437b8`
  ```diff
  -raise _refused(RefusalReason.CORPUS_LOCKED, "checkout")
  +raise _refused(RefusalReason.CORPUS_LOCKED, None)
  ```
- `read_back_of` · survived · argument: removed · `d3af9b79943a74be`
  ```diff
  -raise _refused(RefusalReason.CORPUS_LOCKED, "checkout")
  +raise _refused("checkout")
  ```
- `read_back_of` · survived · argument: removed · `e562eba1522216e7`
  ```diff
  -raise _refused(RefusalReason.CORPUS_LOCKED, "checkout")
  +raise _refused(RefusalReason.CORPUS_LOCKED, )
  ```
- `read_back_of` · survived · string: XX added · `07298bc1e902c814`
  ```diff
  -raise _refused(RefusalReason.CORPUS_LOCKED, "checkout")
  +raise _refused(RefusalReason.CORPUS_LOCKED, "XXcheckoutXX")
  ```
- `read_back_of` · survived · string: upper case · `ce42f122bbcbf2b6`
  ```diff
  -raise _refused(RefusalReason.CORPUS_LOCKED, "checkout")
  +raise _refused(RefusalReason.CORPUS_LOCKED, "CHECKOUT")
  ```
- `read_back_of` · survived · argument: replaced by None · `be9965851d841441`
  ```diff
  -raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, MANIFEST_PATH)
  +raise _refused(None, MANIFEST_PATH)
  ```
- `read_back_of` · survived · argument: replaced by None · `8bf17c12f51d0747`
  ```diff
  -raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, MANIFEST_PATH)
  +raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, None)
  ```
- `read_back_of` · survived · argument: removed · `a3c23b41e1be5919`
  ```diff
  -raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, MANIFEST_PATH)
  +raise _refused(MANIFEST_PATH)
  ```
- `read_back_of` · survived · argument: removed · `7b041e0426063171`
  ```diff
  -raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, MANIFEST_PATH)
  +raise _refused(RefusalReason.CORPUS_TREE_MISMATCH, )
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_cli.py</code>: 3</summary>

- `_chapter_numbers` · survived · argument: replaced by None · `96e83596dfbd9dc2`
  ```diff
  -raise typer.BadParameter("chapters are numbers separated by commas")
  +raise typer.BadParameter(None)
  ```
- `_chapter_numbers` · survived · string: XX added · `35b1ec53cb9c527e`
  ```diff
  -raise typer.BadParameter("chapters are numbers separated by commas")
  +raise typer.BadParameter("XXchapters are numbers separated by commasXX")
  ```
- `_chapter_numbers` · survived · string: upper case · `857865c40774ea66`
  ```diff
  -raise typer.BadParameter("chapters are numbers separated by commas")
  +raise typer.BadParameter("CHAPTERS ARE NUMBERS SEPARATED BY COMMAS")
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_contract_models.py</code>: 10</summary>

- `trusted` · survived · string: XX added · `4de8fc5acfcfad25`
  ```diff
  -and item.verifier != "not_implemented"
  +and item.verifier != "XXnot_implementedXX"
  ```
- `trusted` · survived · string: upper case · `f942b5202589564d`
  ```diff
  -and item.verifier != "not_implemented"
  +and item.verifier != "NOT_IMPLEMENTED"
  ```
- `_Evidence._require` · survived · argument: replaced by None · `18d71962ad186915`
  ```diff
  -raise ValueError("verified evidence names its source and carries its record")
  +raise ValueError(None)
  ```
- `_Evidence._require` · survived · string: XX added · `e5fc87b5e37e4793`
  ```diff
  -raise ValueError("verified evidence names its source and carries its record")
  +raise ValueError("XXverified evidence names its source and carries its recordXX")
  ```
- `_Evidence._require` · survived · string: upper case · `4fb1cc95dd79da7a`
  ```diff
  -raise ValueError("verified evidence names its source and carries its record")
  +raise ValueError("VERIFIED EVIDENCE NAMES ITS SOURCE AND CARRIES ITS RECORD")
  ```
- `_sorted_once` · survived · argument: replaced by None · `786ec9380ada35e2`
  ```diff
  -raise ValueError("document ids are sorted, once each")
  +raise ValueError(None)
  ```
- `_sorted_once` · survived · string: XX added · `a9a806990e55938a`
  ```diff
  -raise ValueError("document ids are sorted, once each")
  +raise ValueError("XXdocument ids are sorted, once eachXX")
  ```
- `_sorted_once` · survived · string: upper case · `cbd8267b513e0b77`
  ```diff
  -raise ValueError("document ids are sorted, once each")
  +raise ValueError("DOCUMENT IDS ARE SORTED, ONCE EACH")
  ```
- `verified` · survived · kind not recorded · `64d368de581ee9a7`
  ```diff
  -if not trusted(kind, evidence.status, evidence.source) or kind not in VERIFIERS:
  +if not trusted(kind, evidence.status, evidence.source) and kind not in VERIFIERS:
  ```
- `_reason_key` · survived · string: XX added · `c84bbe14312c2193`
  ```diff
  -return (reason.document_id or "", reason.code)
  +return (reason.document_id or "XXXX", reason.code)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_control.py</code>: 24</summary>

- `verify_control` · survived · string: XX added · `49dc1e8c73df1e6b`
  ```diff
  -failed = {r for c in checks for r in c.reasons} or {"control_record_unverified"}
  +failed = {r for c in checks for r in c.reasons} or {"XXcontrol_record_unverifiedXX"}
  ```
- `verify_control` · survived · string: upper case · `8caebcc01dd47467`
  ```diff
  -failed = {r for c in checks for r in c.reasons} or {"control_record_unverified"}
  +failed = {r for c in checks for r in c.reasons} or {"CONTROL_RECORD_UNVERIFIED"}
  ```
- `_stopped` · survived · argument: replaced by None · `86e67b7577017908`
  ```diff
  -policy_id=request.policy_id,
  +policy_id=None,
  ```
- `_stopped` · survived · argument: removed · `49bf184edebeb592`
  ```diff
  -policy_id=request.policy_id,
  ```
- `_stopped` · survived · argument: removed · `6d3bf782f74d6e1e`
  ```diff
  -state="stopped",
  ```
- `_stopped` · survived · argument: removed · `a19e2597c90a83c6`
  ```diff
  -records=len(request.history),
  -)
  +)
  ```
- `_chain` · survived · string: XX added · `bd42a975a6a131bb`
  ```diff
  -reasons.append("control_sequence_gap")
  +reasons.append("XXcontrol_sequence_gapXX")
  ```
- `_chain` · survived · string: upper case · `b695f5cda899a102`
  ```diff
  -reasons.append("control_sequence_gap")
  +reasons.append("CONTROL_SEQUENCE_GAP")
  ```
- `_chain` · survived · string: XX added · `8a0c5d69a1524c75`
  ```diff
  -reasons.append("control_chain_broken")
  +reasons.append("XXcontrol_chain_brokenXX")
  ```
- `_chain` · survived · string: upper case · `284bbc1ea7200fbf`
  ```diff
  -reasons.append("control_chain_broken")
  +reasons.append("CONTROL_CHAIN_BROKEN")
  ```
- `_chain` · survived · operator: comparison · `920968e87bd903bf`
  ```diff
  -if index and record.signed_at <= records[index - 1].signed_at:
  +if index and record.signed_at < records[index - 1].signed_at:
  ```
- `_chain` · survived · string: XX added · `49d534f42b39e9aa`
  ```diff
  -reasons.append("control_reordered")
  +reasons.append("XXcontrol_reorderedXX")
  ```
- `_chain` · survived · string: upper case · `b3522b67cc0cd624`
  ```diff
  -reasons.append("control_reordered")
  +reasons.append("CONTROL_REORDERED")
  ```
- `_chain` · survived · argument: replaced by None · `947f3151bef70b57`
  ```diff
  -reasons.append("control_duplicate")
  +reasons.append(None)
  ```
- `_chain` · survived · string: XX added · `97e503a374c149dc`
  ```diff
  -reasons.append("control_duplicate")
  +reasons.append("XXcontrol_duplicateXX")
  ```
- `_chain` · survived · string: upper case · `69617b917d79773a`
  ```diff
  -reasons.append("control_duplicate")
  +reasons.append("CONTROL_DUPLICATE")
  ```
- `_walk` · survived · argument: replaced by None · `65b8ff7a716fc984`
  ```diff
  -for record, digest in zip(records, digests, strict=True):
  +for record, digest in zip(records, digests, strict=None):
  ```
- `_walk` · survived · argument: removed · `950311dfd3da97d7`
  ```diff
  -for record, digest in zip(records, digests, strict=True):
  +for record, digest in zip(records, digests, ):
  ```
- `_walk` · survived · True / False swapped · `16d07641e39e1c10`
  ```diff
  -for record, digest in zip(records, digests, strict=True):
  +for record, digest in zip(records, digests, strict=False):
  ```
- `_verified` · survived · operator: comparison · `cb1aeab914160e2b`
  ```diff
  -active = sorted(t for t in covering if not (t in walk.leases and at < walk.leases[t]))
  +active = sorted(t for t in covering if not (t in walk.leases and at <= walk.leases[t]))
  ```
- `_verified` · survived · argument: replaced by None · `194163b953d5ee44`
  ```diff
  -trust_root_sha256=root.sha256,
  +trust_root_sha256=None,
  ```
- `_verified` · survived · argument: removed · `50b80dc8f1641be8`
  ```diff
  -records=len(digests),
  ```
- `_verified` · survived · argument: removed · `9618ad1510ae9afe`
  ```diff
  -trust_root_sha256=root.sha256,
  -)
  +)
  ```
- `_verified` · survived · ternary: condition forced · `1b60c60d77cbfae6`
  ```diff
  -reasons=("resume_expired",) if expired else (),
  +reasons=("resume_expired",) if (expired) or True else (),
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_gates.py</code>: 97</summary>

- `parse_verdict` · survived · argument: replaced by None · `e9f4d3b542cde1b4`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "gate verdict") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None) from error
  ```
- `parse_verdict` · survived · string: XX added · `cf25a45ffd9b718f`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "gate verdict") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXgate verdictXX") from error
  ```
- `parse_verdict` · survived · string: upper case · `b2fdc9a677fb9c23`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "gate verdict") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "GATE VERDICT") from error
  ```
- `parse_verdict` · survived · argument: replaced by None · `768f808f75004a0a`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "gate verdict")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `parse_verdict` · survived · string: XX added · `cef50123f4fe3828`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "gate verdict")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXgate verdictXX")
  ```
- `parse_verdict` · survived · string: upper case · `e3d7629b1c65cd2f`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "gate verdict")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "GATE VERDICT")
  ```
- `_result` · survived · string: XX added · `a114819e4dc4b1ed`
  ```diff
  -ordered = tuple(sorted(set(reasons), key=lambda r: (r.document_id or "", r.code)))
  +ordered = tuple(sorted(set(reasons), key=lambda r: (r.document_id or "XXXX", r.code)))
  ```
- `_exact` · survived · argument: replaced by None · `1b4dfbe6a94852dc`
  ```diff
  -for selection, binding in zip(manifest.content.documents, manifest.bindings, strict=True):
  +for selection, binding in zip(manifest.content.documents, manifest.bindings, strict=None):
  ```
- `_exact` · survived · argument: removed · `7e73953ff6017d0e`
  ```diff
  -for selection, binding in zip(manifest.content.documents, manifest.bindings, strict=True):
  +for selection, binding in zip(manifest.content.documents, manifest.bindings, ):
  ```
- `_exact` · survived · True / False swapped · `a32116804c46b5c5`
  ```diff
  -for selection, binding in zip(manifest.content.documents, manifest.bindings, strict=True):
  +for selection, binding in zip(manifest.content.documents, manifest.bindings, strict=False):
  ```
- `_exact` · survived · argument: replaced by None · `aafd532646514580`
  ```diff
  -yield GateReason(code="batch_selection_mismatch", document_id=selection.document_id)
  +yield GateReason(code="batch_selection_mismatch", document_id=None)
  ```
- `_exact` · survived · argument: removed · `fa23fc2518e8b13e`
  ```diff
  -yield GateReason(code="batch_selection_mismatch", document_id=selection.document_id)
  +yield GateReason(code="batch_selection_mismatch", )
  ```
- `_unplanned` · survived · argument: replaced by None · `27d3d97d3c8af8c4`
  ```diff
  -if disposition.get(document) != "planned":
  +if disposition.get(None) != "planned":
  ```
- `_unplanned` · survived · string: XX added · `8815fa6669227112`
  ```diff
  -if disposition.get(document) != "planned":
  +if disposition.get(document) != "XXplannedXX":
  ```
- `_unplanned` · survived · string: upper case · `dddcb15930a95b79`
  ```diff
  -if disposition.get(document) != "planned":
  +if disposition.get(document) != "PLANNED":
  ```
- `_unplanned` · survived · argument: replaced by None · `06d1b00750cb8d73`
  ```diff
  -yield GateReason(code="document_not_planned", document_id=document)
  +yield GateReason(code="document_not_planned", document_id=None)
  ```
- `_unplanned` · survived · argument: removed · `6c84a3afc2ca9fc1`
  ```diff
  -yield GateReason(code="document_not_planned", document_id=document)
  +yield GateReason(code="document_not_planned", )
  ```
- `_lineage_anchor` · survived · argument: replaced by None · `f61f70876b483f02`
  ```diff
  -yield GateReason(code="anchor_for_unknown_document", document_id=document)
  +yield GateReason(code="anchor_for_unknown_document", document_id=None)
  ```
- `_lineage_anchor` · survived · argument: removed · `4762f549b66b5e0e`
  ```diff
  -yield GateReason(code="anchor_for_unknown_document", document_id=document)
  +yield GateReason(code="anchor_for_unknown_document", )
  ```
- `_lineage_anchor` · survived · string: XX added · `63b952fdd70ffc66`
  ```diff
  -if binding.lineage_status != "verified":
  +if binding.lineage_status != "XXverifiedXX":
  ```
- `_lineage_anchor` · survived · string: upper case · `312690abd31e7f1e`
  ```diff
  -if binding.lineage_status != "verified":
  +if binding.lineage_status != "VERIFIED":
  ```
- `_lineage_anchor` · survived · argument: replaced by None · `f281ab3c28da1d6e`
  ```diff
  -yield GateReason(code=f"lineage_{binding.lineage_status}", document_id=document)
  +yield GateReason(code=f"lineage_{binding.lineage_status}", document_id=None)
  ```
- `_lineage_anchor` · survived · argument: removed · `fbb4540312d3761d`
  ```diff
  -yield GateReason(code=f"lineage_{binding.lineage_status}", document_id=document)
  +yield GateReason(code=f"lineage_{binding.lineage_status}", )
  ```
- `_anchored` · survived · operator: comparison · `4da4fc188ada5521`
  ```diff
  -elif binding.lineage_entries > record.lineage_entries:
  +elif binding.lineage_entries >= record.lineage_entries:
  ```
- `_manifest_reproduced` · survived · argument: replaced by None · `e7f6c11b7ef7bcaf`
  ```diff
  -if not authentic(evidence.reproduction, "manifest_reproduction"):
  +if not authentic(evidence.reproduction, None):
  ```
- `_manifest_reproduced` · survived · string: XX added · `78f95427401df905`
  ```diff
  -if not authentic(evidence.reproduction, "manifest_reproduction"):
  +if not authentic(evidence.reproduction, "XXmanifest_reproductionXX"):
  ```
- `_manifest_reproduced` · survived · string: upper case · `53e17eed588f8ef8`
  ```diff
  -if not authentic(evidence.reproduction, "manifest_reproduction"):
  +if not authentic(evidence.reproduction, "MANIFEST_REPRODUCTION"):
  ```
- `_manifest_reproduced` · survived · argument: replaced by None · `f346fb1f6592a491`
  ```diff
  -yield GateReason(code=f"reproduction:{finding.code}", document_id=finding.document_id)
  +yield GateReason(code=f"reproduction:{finding.code}", document_id=None)
  ```
- `_manifest_reproduced` · survived · argument: removed · `3a96a7ddf6c84dcc`
  ```diff
  -yield GateReason(code=f"reproduction:{finding.code}", document_id=finding.document_id)
  +yield GateReason(code=f"reproduction:{finding.code}", )
  ```
- `_source_evidence` · survived · ternary: condition forced · `90c515640ea64084`
  ```diff
  -excluded = ["document_excluded"] if selection.status == "excluded" else []
  +excluded = ["document_excluded"] if (selection.status == "excluded") or True else []
  ```
- `_source_evidence` · survived · argument: replaced by None · `bea09c8fb2ed291b`
  ```diff
  -return excluded + _of(selection, "integrity", "source")
  +return excluded + _of(selection, None, "source")
  ```
- `_source_evidence` · survived · argument: replaced by None · `efe8055de43ab126`
  ```diff
  -return excluded + _of(selection, "integrity", "source")
  +return excluded + _of(selection, "integrity", None)
  ```
- `_source_evidence` · survived · argument: removed · `c657e7b3bce18837`
  ```diff
  -return excluded + _of(selection, "integrity", "source")
  +return excluded + _of(selection, "source")
  ```
- `_source_evidence` · survived · argument: removed · `e1ed88288f3d431d`
  ```diff
  -return excluded + _of(selection, "integrity", "source")
  +return excluded + _of(selection, "integrity", )
  ```
- `_source_evidence` · survived · string: XX added · `8afda852ea282d07`
  ```diff
  -return excluded + _of(selection, "integrity", "source")
  +return excluded + _of(selection, "XXintegrityXX", "source")
  ```
- `_source_evidence` · survived · string: upper case · `7386f40664544762`
  ```diff
  -return excluded + _of(selection, "integrity", "source")
  +return excluded + _of(selection, "INTEGRITY", "source")
  ```
- `_source_evidence` · survived · string: XX added · `12514923a7c2dfe6`
  ```diff
  -return excluded + _of(selection, "integrity", "source")
  +return excluded + _of(selection, "integrity", "XXsourceXX")
  ```
- `_source_evidence` · survived · string: upper case · `4b3c0cf991eea43d`
  ```diff
  -return excluded + _of(selection, "integrity", "source")
  +return excluded + _of(selection, "integrity", "SOURCE")
  ```
- `_authorization` · survived · string: XX added · `fcce7d91506c552f`
  ```diff
  -if registry.origin != "engine_pinned":
  +if registry.origin != "XXengine_pinnedXX":
  ```
- `_authorization` · survived · string: upper case · `8f27318198fbdfb5`
  ```diff
  -if registry.origin != "engine_pinned":
  +if registry.origin != "ENGINE_PINNED":
  ```
- `_authorization` · survived · argument: replaced by None · `56921195263d13f7`
  ```diff
  -_of(s, "authorization") + [c for c in _of(s, "integrity") if c.startswith("fc7b_")]
  +_of(s, "authorization") + [c for c in _of(s, None) if c.startswith("fc7b_")]
  ```
- `_authorization` · survived · argument: removed · `8766a1bd8f01bcdf`
  ```diff
  -_of(s, "authorization") + [c for c in _of(s, "integrity") if c.startswith("fc7b_")]
  +_of(s, "authorization") + [c for c in _of(s, ) if c.startswith("fc7b_")]
  ```
- `_authorization` · survived · string: XX added · `f4d3097b15a43891`
  ```diff
  -_of(s, "authorization") + [c for c in _of(s, "integrity") if c.startswith("fc7b_")]
  +_of(s, "authorization") + [c for c in _of(s, "XXintegrityXX") if c.startswith("fc7b_")]
  ```
- `_authorization` · survived · string: upper case · `fce07c86c7894289`
  ```diff
  -_of(s, "authorization") + [c for c in _of(s, "integrity") if c.startswith("fc7b_")]
  +_of(s, "authorization") + [c for c in _of(s, "INTEGRITY") if c.startswith("fc7b_")]
  ```
- `_authorization` · survived · string: XX added · `ddccf15d4ec344ce`
  ```diff
  -_of(s, "authorization") + [c for c in _of(s, "integrity") if c.startswith("fc7b_")]
  +_of(s, "authorization") + [c for c in _of(s, "integrity") if c.startswith("XXfc7b_XX")]
  ```
- `_authorization` · survived · string: upper case · `607fd30597ec7afe`
  ```diff
  -_of(s, "authorization") + [c for c in _of(s, "integrity") if c.startswith("fc7b_")]
  +_of(s, "authorization") + [c for c in _of(s, "integrity") if c.startswith("FC7B_")]
  ```
- `_freshness` · survived · argument: replaced by None · `e5942d0ed8247c14`
  ```diff
  -evidence.manifest.content.documents, evidence.manifest.bindings, strict=True
  +evidence.manifest.content.documents, evidence.manifest.bindings, strict=None
  ```
- `_freshness` · survived · argument: removed · `757abe394d34768f`
  ```diff
  -evidence.manifest.content.documents, evidence.manifest.bindings, strict=True
  -):
  +evidence.manifest.content.documents, evidence.manifest.bindings, ):
  ```
- `_freshness` · survived · True / False swapped · `323ee40e920ac8cc`
  ```diff
  -evidence.manifest.content.documents, evidence.manifest.bindings, strict=True
  +evidence.manifest.content.documents, evidence.manifest.bindings, strict=False
  ```
- `_automated_publication` · survived · string: XX added · `1985ef31b0adce6f`
  ```diff
  -yield GateReason(code="approval_for_unknown_policy")
  +yield GateReason(code="XXapproval_for_unknown_policyXX")
  ```
- `_automated_publication` · survived · string: upper case · `f2bf10493e932a07`
  ```diff
  -yield GateReason(code="approval_for_unknown_policy")
  +yield GateReason(code="APPROVAL_FOR_UNKNOWN_POLICY")
  ```
- `_automated_publication` · survived · string: XX added · `0e725ee95e7c0db1`
  ```diff
  -yield GateReason(code="d11_approval_missing")
  +yield GateReason(code="XXd11_approval_missingXX")
  ```
- `_automated_publication` · survived · string: upper case · `fa4b681e266055da`
  ```diff
  -yield GateReason(code="d11_approval_missing")
  +yield GateReason(code="D11_APPROVAL_MISSING")
  ```
- `_approvals` · survived · argument: replaced by None · `590f003ac5f16cd0`
  ```diff
  -if not authentic(item, "policy_approval"):
  +if not authentic(item, None):
  ```
- `_approvals` · survived · string: XX added · `983a2489a50a24cd`
  ```diff
  -if not authentic(item, "policy_approval"):
  +if not authentic(item, "XXpolicy_approvalXX"):
  ```
- `_approvals` · survived · string: upper case · `7a732cbc883740ee`
  ```diff
  -if not authentic(item, "policy_approval"):
  +if not authentic(item, "POLICY_APPROVAL"):
  ```
- `_approvals` · survived · string: XX added · `b196a847da8c06aa`
  ```diff
  -if item.automated_publication != "verified":
  +if item.automated_publication != "XXverifiedXX":
  ```
- `_approvals` · survived · string: upper case · `aaffb40851641eb0`
  ```diff
  -if item.automated_publication != "verified":
  +if item.automated_publication != "VERIFIED":
  ```
- `_approvals` · survived · operator: comparison · `ba973496bac45e48`
  ```diff
  -if item.policy_approval != "verified":
  +if item.policy_approval == "verified":
  ```
- `_approvals` · survived · string: XX added · `f3b8413bbe75fb2f`
  ```diff
  -if item.policy_approval != "verified":
  +if item.policy_approval != "XXverifiedXX":
  ```
- `_approvals` · survived · string: upper case · `cc88b83625a7173c`
  ```diff
  -if item.policy_approval != "verified":
  +if item.policy_approval != "VERIFIED":
  ```
- `_approvals` · survived · argument: replaced by None · `21bdb69c92a9c77e`
  ```diff
  -yield GateReason(code="policy_approval_missing")
  +yield GateReason(code=None)
  ```
- `_approvals` · survived · string: XX added · `efa93c3c33ed12d9`
  ```diff
  -yield GateReason(code="policy_approval_missing")
  +yield GateReason(code="XXpolicy_approval_missingXX")
  ```
- `_approvals` · survived · string: upper case · `f6f40222b258e13f`
  ```diff
  -yield GateReason(code="policy_approval_missing")
  +yield GateReason(code="POLICY_APPROVAL_MISSING")
  ```
- `_approvals` · survived · is / in negated · `8f46aec20902227b`
  ```diff
  -if item.max_documents_per_batch is None or documents > item.max_documents_per_batch:
  +if item.max_documents_per_batch is not None or documents > item.max_documents_per_batch:
  ```
- `_approvals` · survived · operator: comparison · `abcf599ad51c0c49`
  ```diff
  -if item.max_documents_per_batch is None or documents > item.max_documents_per_batch:
  +if item.max_documents_per_batch is None or documents >= item.max_documents_per_batch:
  ```
- `_publisher` · survived · operator: comparison · `636af7515b2d8e75`
  ```diff
  -(not set(found.namespaces) <= BOT_NAMESPACES, "publisher_scope_exceeded"),
  +(not set(found.namespaces) < BOT_NAMESPACES, "publisher_scope_exceeded"),
  ```
- `_publisher` · survived · string: XX added · `5b8232d2763e835c`
  ```diff
  -(evidence.registry.origin != "engine_pinned", "publisher_registry_unpinned"),
  +(evidence.registry.origin != "XXengine_pinnedXX", "publisher_registry_unpinned"),
  ```
- `_publisher` · survived · string: upper case · `a4cc7a898bc51677`
  ```diff
  -(evidence.registry.origin != "engine_pinned", "publisher_registry_unpinned"),
  +(evidence.registry.origin != "ENGINE_PINNED", "publisher_registry_unpinned"),
  ```
- `_publisher` · survived · argument: replaced by None · `3f9a442e8a214b6c`
  ```diff
  -(not authentic(found, "publisher_identity"), "publisher_unverified"),
  +(not authentic(found, None), "publisher_unverified"),
  ```
- `_publisher` · survived · string: XX added · `4aedfa0031f24ebe`
  ```diff
  -(not authentic(found, "publisher_identity"), "publisher_unverified"),
  +(not authentic(found, "XXpublisher_identityXX"), "publisher_unverified"),
  ```
- `_publisher` · survived · string: upper case · `df4db1bd26cbd813`
  ```diff
  -(not authentic(found, "publisher_identity"), "publisher_unverified"),
  +(not authentic(found, "PUBLISHER_IDENTITY"), "publisher_unverified"),
  ```
- `_constraints` · survived · string: XX added · `e06fcb39a317f4b7`
  ```diff
  -found.append("limits_policy_mismatch")
  +found.append("XXlimits_policy_mismatchXX")
  ```
- `_constraints` · survived · string: upper case · `bf59dab083c223d2`
  ```diff
  -found.append("limits_policy_mismatch")
  +found.append("LIMITS_POLICY_MISMATCH")
  ```
- `_ledger` · survived · string: XX added · `89f4539d2be3910e`
  ```diff
  -found.append("ledger_limits_mismatch")
  +found.append("XXledger_limits_mismatchXX")
  ```
- `_ledger` · survived · string: upper case · `870b0256d6c85882`
  ```diff
  -found.append("ledger_limits_mismatch")
  +found.append("LEDGER_LIMITS_MISMATCH")
  ```
- `_ledger` · survived · string: XX added · `2576dbbb0d4f7bb3`
  ```diff
  -if ledger.release_state in ("settled", "cancelled"):
  +if ledger.release_state in ("settled", "XXcancelledXX"):
  ```
- `_ledger` · survived · string: upper case · `30462d042858e8dc`
  ```diff
  -if ledger.release_state in ("settled", "cancelled"):
  +if ledger.release_state in ("settled", "CANCELLED"):
  ```
- `_ledger` · survived · assignment: value to None · `ec4db1797fa1cfb5`
  ```diff
  -held = ledger.release_reserved
  +held = None
  ```
- `_ledger` · survived · operator: comparison · `a50eb1c033bc0915`
  ```diff
  -if held is not None and (held.documents, held.files, held.bytes) != (
  +if held is not None and (held.documents, held.files, held.bytes) == (
  ```
- `_ledger` · survived · argument: replaced by None · `825f9ea8ea9529f1`
  ```diff
  -found.append("reservation_contradicts_batch")
  +found.append(None)
  ```
- `_ledger` · survived · string: XX added · `76bf6d5bdaebfecf`
  ```diff
  -found.append("reservation_contradicts_batch")
  +found.append("XXreservation_contradicts_batchXX")
  ```
- `_ledger` · survived · string: upper case · `f5c5cd1d5c1a877b`
  ```diff
  -found.append("reservation_contradicts_batch")
  +found.append("RESERVATION_CONTRADICTS_BATCH")
  ```
- `_ledger` · survived · ternary: condition forced · `84e29bab8c3fe2ab`
  ```diff
  -planned = _planned(ledger.committed, ledger.reserved, None if held else batch)
  +planned = _planned(ledger.committed, ledger.reserved, None if (held) and False else batch)
  ```
- `_batch` · survived · string: XX added · `305dfcb1a81e6643`
  ```diff
  -("documents", batch.documents, MAX_BATCH_DOCUMENTS, "max_documents_per_batch"),
  +("XXdocumentsXX", batch.documents, MAX_BATCH_DOCUMENTS, "max_documents_per_batch"),
  ```
- `_batch` · survived · string: upper case · `43ee00be98ea9438`
  ```diff
  -("documents", batch.documents, MAX_BATCH_DOCUMENTS, "max_documents_per_batch"),
  +("DOCUMENTS", batch.documents, MAX_BATCH_DOCUMENTS, "max_documents_per_batch"),
  ```
- `_batch` · survived · string: XX added · `c6ac8b07d3f0edb1`
  ```diff
  -("documents", batch.documents, MAX_BATCH_DOCUMENTS, "max_documents_per_batch"),
  +("documents", batch.documents, MAX_BATCH_DOCUMENTS, "XXmax_documents_per_batchXX"),
  ```
- `_batch` · survived · string: upper case · `e6fa68e5c3a00bb4`
  ```diff
  -("documents", batch.documents, MAX_BATCH_DOCUMENTS, "max_documents_per_batch"),
  +("documents", batch.documents, MAX_BATCH_DOCUMENTS, "MAX_DOCUMENTS_PER_BATCH"),
  ```
- `_planned` · survived · ternary: condition forced · `add1a26e19174904`
  ```diff
  -extra = (0, 0, 0, 0) if batch is None else (batch.documents, batch.files, batch.bytes, 1)
  +extra = (0, 0, 0, 0) if (batch is None) and False else (batch.documents, batch.files, batch.bytes, 1)
  ```
- `_planned` · survived · number · `f2690f0ec84c63ca`
  ```diff
  -extra = (0, 0, 0, 0) if batch is None else (batch.documents, batch.files, batch.bytes, 1)
  +extra = (1, 0, 0, 0) if batch is None else (batch.documents, batch.files, batch.bytes, 1)
  ```
- `_planned` · survived · number · `59c5da98e7c9e29c`
  ```diff
  -extra = (0, 0, 0, 0) if batch is None else (batch.documents, batch.files, batch.bytes, 1)
  +extra = (0, 1, 0, 0) if batch is None else (batch.documents, batch.files, batch.bytes, 1)
  ```
- `_planned` · survived · number · `250261d0e74449a0`
  ```diff
  -extra = (0, 0, 0, 0) if batch is None else (batch.documents, batch.files, batch.bytes, 1)
  +extra = (0, 0, 1, 0) if batch is None else (batch.documents, batch.files, batch.bytes, 1)
  ```
- `_planned` · survived · number · `8edc668b4204b7ef`
  ```diff
  -extra = (0, 0, 0, 0) if batch is None else (batch.documents, batch.files, batch.bytes, 1)
  +extra = (0, 0, 0, 1) if batch is None else (batch.documents, batch.files, batch.bytes, 1)
  ```
- `_planned` · survived · operator: arithmetic · `751e55c776d35388`
  ```diff
  -documents=committed.documents + reserved.documents + extra[0],
  +documents=committed.documents - reserved.documents + extra[0],
  ```
- `_planned` · survived · operator: arithmetic · `c3cc63f4fdeaca43`
  ```diff
  -files=committed.files + reserved.files + extra[1],
  +files=committed.files - reserved.files + extra[1],
  ```
- `_planned` · survived · operator: arithmetic · `a3a757ec55dd5873`
  ```diff
  -bytes=committed.bytes + reserved.bytes + extra[2],
  +bytes=committed.bytes - reserved.bytes + extra[2],
  ```
- `_planned` · survived · operator: arithmetic · `fcbd79edc89d2c93`
  ```diff
  -commits=committed.commits + reserved.commits + extra[3],
  +commits=committed.commits - reserved.commits + extra[3],
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_inventory.py</code>: 9</summary>

- `_inventory` · survived · operator: comparison · `985adb8ef06f68f1`
  ```diff
  -if ctx.at < record.valid_from:
  +if ctx.at <= record.valid_from:
  ```
- `_inventory` · survived · argument: removed · `90bfe40942dc1f86`
  ```diff
  -check._replace(status="invalid", reasons=tuple(reasons), record=None) if reasons else check
  +check._replace(status="invalid", reasons=tuple(reasons), ) if reasons else check
  ```
- `_verdict` · survived · operator: comparison · `91ef20214a0d601c`
  ```diff
  -if len(good) > 1:
  +if len(good) >= 1:
  ```
- `_sealed` · survived · operator: and / or · `cff36f7058955b50`
  ```diff
  -if not isinstance(record, SignedInventory) or entry is None:
  +if not isinstance(record, SignedInventory) and entry is None:
  ```
- `_sealed` · survived · argument: replaced by None · `3517a940c2e0ea86`
  ```diff
  -return _missing(("inventory_unverifiable",))
  +return _missing(None)
  ```
- `_sealed` · survived · string: XX added · `ac87b52416c9d2a3`
  ```diff
  -return _missing(("inventory_unverifiable",))
  +return _missing(("XXinventory_unverifiableXX",))
  ```
- `_sealed` · survived · string: upper case · `ced9f0b273353c3c`
  ```diff
  -return _missing(("inventory_unverifiable",))
  +return _missing(("INVENTORY_UNVERIFIABLE",))
  ```
- `_sealed` · survived · argument: replaced by None · `4befc3f93bbab0e4`
  ```diff
  -signer_fingerprint=entry.fingerprint,
  +signer_fingerprint=None,
  ```
- `_sealed` · survived · argument: removed · `be00665a0aa68a1d`
  ```diff
  -signer_fingerprint=entry.fingerprint,
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_ledger.py</code>: 98</summary>

- `verify_ledger` · survived · string: XX added · `6b2bc9998dba9604`
  ```diff
  -return LedgerEvidence(status="missing", source=None, reasons=("budget_ledger_empty",))
  +return LedgerEvidence(status="missing", source=None, reasons=("XXbudget_ledger_emptyXX",))
  ```
- `verify_ledger` · survived · string: upper case · `f915aa3ddd44aac4`
  ```diff
  -return LedgerEvidence(status="missing", source=None, reasons=("budget_ledger_empty",))
  +return LedgerEvidence(status="missing", source=None, reasons=("BUDGET_LEDGER_EMPTY",))
  ```
- `verify_ledger` · survived · argument: replaced by None · `2e82888b3bcc9119`
  ```diff
  -day=request.day,
  +day=None,
  ```
- `verify_ledger` · survived · argument: replaced by None · `e510f3ab592b3e57`
  ```diff
  -limits_sha256=request.limits.limits_sha256,
  +limits_sha256=None,
  ```
- `verify_ledger` · survived · argument: replaced by None · `21b8502a759dd741`
  ```diff
  -release_reserved=slot.amounts if slot and slot.state == "reserved" else None,
  +release_reserved=None,
  ```
- `verify_ledger` · survived · argument: removed · `5a9a14bedef61347`
  ```diff
  -day=request.day,
  ```
- `verify_ledger` · survived · argument: removed · `b1eaa12d29819b29`
  ```diff
  -limits_sha256=request.limits.limits_sha256,
  ```
- `verify_ledger` · survived · argument: removed · `35bb8909dda66ff3`
  ```diff
  -release_reserved=slot.amounts if slot and slot.state == "reserved" else None,
  ```
- `verify_ledger` · survived · argument: removed · `2a19cc92b9fcfe63`
  ```diff
  -entries=books.count,
  ```
- `verify_ledger` · survived · ternary: condition forced · `e82b3bc3da0326db`
  ```diff
  -release_state="none" if slot is None else slot.state,
  +release_state="none" if (slot is None) and False else slot.state,
  ```
- `verify_ledger` · survived · string: XX added · `3098c98009c444b5`
  ```diff
  -release_state="none" if slot is None else slot.state,
  +release_state="XXnoneXX" if slot is None else slot.state,
  ```
- `verify_ledger` · survived · string: upper case · `ddf4da8fde1b5f1a`
  ```diff
  -release_state="none" if slot is None else slot.state,
  +release_state="NONE" if slot is None else slot.state,
  ```
- `verify_ledger` · survived · ternary: condition forced · `daf0f077005b2ca8`
  ```diff
  -release_reserved=slot.amounts if slot and slot.state == "reserved" else None,
  +release_reserved=slot.amounts if (slot and slot.state == "reserved") and False else None,
  ```
- `verify_ledger` · survived · ternary: condition forced · `8efe7060f7071f3d`
  ```diff
  -release_reserved=slot.amounts if slot and slot.state == "reserved" else None,
  +release_reserved=slot.amounts if (slot and slot.state == "reserved") or True else None,
  ```
- `verify_ledger` · survived · operator: and / or · `f43624a49ecc1bb3`
  ```diff
  -release_reserved=slot.amounts if slot and slot.state == "reserved" else None,
  +release_reserved=slot.amounts if slot or slot.state == "reserved" else None,
  ```
- `verify_ledger` · survived · operator: comparison · `6ff80a1a5ad450fb`
  ```diff
  -release_reserved=slot.amounts if slot and slot.state == "reserved" else None,
  +release_reserved=slot.amounts if slot and slot.state != "reserved" else None,
  ```
- `verify_ledger` · survived · string: XX added · `18571af276e85005`
  ```diff
  -release_reserved=slot.amounts if slot and slot.state == "reserved" else None,
  +release_reserved=slot.amounts if slot and slot.state == "XXreservedXX" else None,
  ```
- `verify_ledger` · survived · string: upper case · `24fe694156105f1d`
  ```diff
  -release_reserved=slot.amounts if slot and slot.state == "reserved" else None,
  +release_reserved=slot.amounts if slot and slot.state == "RESERVED" else None,
  ```
- `plan_entry` · survived · operator: and / or · `7107bc50620a532a`
  ```diff
  -reasons = list(books.reasons) or _order(books, operation)
  +reasons = list(books.reasons) and _order(books, operation)
  ```
- `plan_entry` · survived · string: XX added · `179cacc9ecdc7e5e`
  ```diff
  -limits_sha256 = limits.limits_sha256 or ""
  +limits_sha256 = limits.limits_sha256 or "XXXX"
  ```
- `plan_entry` · survived · operator: and / or · `57b6e16a6cc19cea`
  ```diff
  -if not verified("publication_limits", limits) or not limits_sha256:
  +if not verified("publication_limits", limits) and not limits_sha256:
  ```
- `plan_entry` · survived · argument: replaced by None · `48e4e781ef6f33fb`
  ```diff
  -reasons.append("limits_unverified")
  +reasons.append(None)
  ```
- `plan_entry` · survived · string: XX added · `abd7e8c1cac96e6b`
  ```diff
  -reasons.append("limits_unverified")
  +reasons.append("XXlimits_unverifiedXX")
  ```
- `plan_entry` · survived · string: upper case · `7d3b82c5405f6987`
  ```diff
  -reasons.append("limits_unverified")
  +reasons.append("LIMITS_UNVERIFIED")
  ```
- `plan_entry` · survived · ternary: condition forced · `03c542325d52cc6b`
  ```diff
  -judged = _judge(books, operation) if not reasons else _Judgement("refused", ())
  +judged = _judge(books, operation) if (not reasons) or True else _Judgement("refused", ())
  ```
- `plan_entry` · survived · argument: replaced by None · `5b258f43ff238a0c`
  ```diff
  -judged = _judge(books, operation) if not reasons else _Judgement("refused", ())
  +judged = _judge(books, operation) if not reasons else _Judgement(None, ())
  ```
- `plan_entry` · survived · argument: replaced by None · `42c89f07f611dceb`
  ```diff
  -judged = _judge(books, operation) if not reasons else _Judgement("refused", ())
  +judged = _judge(books, operation) if not reasons else _Judgement("refused", None)
  ```
- `plan_entry` · survived · argument: removed · `1b74687799b9d026`
  ```diff
  -judged = _judge(books, operation) if not reasons else _Judgement("refused", ())
  +judged = _judge(books, operation) if not reasons else _Judgement(())
  ```
- `plan_entry` · survived · argument: removed · `c7274c1703659523`
  ```diff
  -judged = _judge(books, operation) if not reasons else _Judgement("refused", ())
  +judged = _judge(books, operation) if not reasons else _Judgement("refused", )
  ```
- `plan_entry` · survived · string: XX added · `fd774b2451b8c0ef`
  ```diff
  -judged = _judge(books, operation) if not reasons else _Judgement("refused", ())
  +judged = _judge(books, operation) if not reasons else _Judgement("XXrefusedXX", ())
  ```
- `plan_entry` · survived · string: upper case · `653921e8b06e56e2`
  ```diff
  -judged = _judge(books, operation) if not reasons else _Judgement("refused", ())
  +judged = _judge(books, operation) if not reasons else _Judgement("REFUSED", ())
  ```
- `plan_entry` · survived · augmented assignment to plain · `55391ff29ab6d679`
  ```diff
  -reasons += judged.reasons
  +reasons = judged.reasons
  ```
- `_take` · survived · argument: replaced by None · `0a5181dfd974625b`
  ```diff
  -books.reasons.append(_DUPLICATE.get(entry.operation, "ledger_duplicate_entry"))
  +books.reasons.append(_DUPLICATE.get(entry.operation, None))
  ```
- `_take` · survived · argument: removed · `c0882fe738d31ca1`
  ```diff
  -books.reasons.append(_DUPLICATE.get(entry.operation, "ledger_duplicate_entry"))
  +books.reasons.append(_DUPLICATE.get(entry.operation, ))
  ```
- `_take` · survived · string: XX added · `cf8f698f29b192fb`
  ```diff
  -books.reasons.append(_DUPLICATE.get(entry.operation, "ledger_duplicate_entry"))
  +books.reasons.append(_DUPLICATE.get(entry.operation, "XXledger_duplicate_entryXX"))
  ```
- `_take` · survived · string: upper case · `ff393e34e6e4daa5`
  ```diff
  -books.reasons.append(_DUPLICATE.get(entry.operation, "ledger_duplicate_entry"))
  +books.reasons.append(_DUPLICATE.get(entry.operation, "LEDGER_DUPLICATE_ENTRY"))
  ```
- `_take` · survived · number · `effddc073c8ca17d`
  ```diff
  -books.count + 1,
  +books.count + 2,
  ```
- `_record` · survived · operator: and / or · `0899f66173f31750`
  ```diff
  -if entry.operation == "reconcile" or entry.release_id is None or entry.batch_id is None:
  +if entry.operation == "reconcile" or entry.release_id is None and entry.batch_id is None:
  ```
- `_record` · survived · operator: and / or · `f4fea9ade5193cda`
  ```diff
  -if entry.operation == "reconcile" or entry.release_id is None or entry.batch_id is None:
  +if entry.operation == "reconcile" and entry.release_id is None or entry.batch_id is None:
  ```
- `_record` · survived · string: XX added · `9d3fe812f281e98a`
  ```diff
  -if entry.operation == "reconcile" or entry.release_id is None or entry.batch_id is None:
  +if entry.operation == "XXreconcileXX" or entry.release_id is None or entry.batch_id is None:
  ```
- `_record` · survived · string: upper case · `cb5aaf199be791d5`
  ```diff
  -if entry.operation == "reconcile" or entry.release_id is None or entry.batch_id is None:
  +if entry.operation == "RECONCILE" or entry.release_id is None or entry.batch_id is None:
  ```
- `_record` · survived · argument: replaced by None · `0c098d7d8b0f38a9`
  ```diff
  -books.settled[slot.day] = _plus(books.settled.get(slot.day, _ZERO), entry.amounts)
  +books.settled[slot.day] = _plus(books.settled.get(None, _ZERO), entry.amounts)
  ```
- `_judge` · survived · kind not recorded · `d1984e2e5657b04d`
  ```diff
  -settled = books.settled.get(operation.day, _ZERO)
  +settled = books.settled.get(operation.day, None)
  ```
- `_judge` · survived · kind not recorded · `86e0d77c1f331962`
  ```diff
  -settled = books.settled.get(operation.day, _ZERO)
  +settled = books.settled.get(operation.day, )
  ```
- `_judge` · survived · argument: replaced by None · `405a85d2ef1b73ed`
  ```diff
  -return _Judgement("refused" if mismatch else "new", mismatch)
  +return _Judgement(None, mismatch)
  ```
- `_judge` · survived · ternary: condition forced · `94dfcbdd81bc26d6`
  ```diff
  -return _Judgement("refused" if mismatch else "new", mismatch)
  +return _Judgement("refused" if (mismatch) and False else "new", mismatch)
  ```
- `_judge` · survived · ternary: condition forced · `b3dbb09daae00c8e`
  ```diff
  -return _Judgement("refused" if mismatch else "new", mismatch)
  +return _Judgement("refused" if (mismatch) or True else "new", mismatch)
  ```
- `_judge` · survived · string: XX added · `56824c704203ba3c`
  ```diff
  -return _Judgement("refused" if mismatch else "new", mismatch)
  +return _Judgement("XXrefusedXX" if mismatch else "new", mismatch)
  ```
- `_judge` · survived · string: upper case · `c37dcbec7cc8e886`
  ```diff
  -return _Judgement("refused" if mismatch else "new", mismatch)
  +return _Judgement("REFUSED" if mismatch else "new", mismatch)
  ```
- `_judge` · survived · string: XX added · `d0f2e2b97b905d33`
  ```diff
  -return _Judgement("refused" if mismatch else "new", mismatch)
  +return _Judgement("refused" if mismatch else "XXnewXX", mismatch)
  ```
- `_judge` · survived · string: upper case · `a2521ed635b263c0`
  ```diff
  -return _Judgement("refused" if mismatch else "new", mismatch)
  +return _Judgement("refused" if mismatch else "NEW", mismatch)
  ```
- `_judge` · survived · string: XX added · `ba21dfbfda52fb47`
  ```diff
  -slot = books.slots.get(operation.release_id or "")
  +slot = books.slots.get(operation.release_id or "XXXX")
  ```
- `_reserve` · survived · argument: replaced by None · `2642fcd23b2612a6`
  ```diff
  -return _Judgement("refused", (spent[slot.state],))
  +return _Judgement(None, (spent[slot.state],))
  ```
- `_reserve` · survived · string: XX added · `5b59b6e2eaa007b8`
  ```diff
  -return _Judgement("refused", (spent[slot.state],))
  +return _Judgement("XXrefusedXX", (spent[slot.state],))
  ```
- `_reserve` · survived · string: upper case · `4c631986bd14acf1`
  ```diff
  -return _Judgement("refused", (spent[slot.state],))
  +return _Judgement("REFUSED", (spent[slot.state],))
  ```
- `_reserve` · survived · argument: replaced by None · `ce78a858cff181ee`
  ```diff
  -return _Judgement("refused", ("ledger_conflicting_reservation",))
  +return _Judgement(None, ("ledger_conflicting_reservation",))
  ```
- `_reserve` · survived · string: XX added · `fb21a79251e6040f`
  ```diff
  -return _Judgement("refused", ("ledger_conflicting_reservation",))
  +return _Judgement("XXrefusedXX", ("ledger_conflicting_reservation",))
  ```
- `_reserve` · survived · string: upper case · `3e6c4ec5c066b2f1`
  ```diff
  -return _Judgement("refused", ("ledger_conflicting_reservation",))
  +return _Judgement("REFUSED", ("ledger_conflicting_reservation",))
  ```
- `_reserve` · survived · argument: replaced by None · `0f1e5f58334705b5`
  ```diff
  -return _Judgement("refused", over) if over else _Judgement("new")
  +return _Judgement(None, over) if over else _Judgement("new")
  ```
- `_reserve` · survived · string: XX added · `842004b54deb80a5`
  ```diff
  -return _Judgement("refused", over) if over else _Judgement("new")
  +return _Judgement("XXrefusedXX", over) if over else _Judgement("new")
  ```
- `_reserve` · survived · string: upper case · `9264fb43c0f8d26c`
  ```diff
  -return _Judgement("refused", over) if over else _Judgement("new")
  +return _Judgement("REFUSED", over) if over else _Judgement("new")
  ```
- `_settle` · survived · string: XX added · `b332bdc63c8aa1f2`
  ```diff
  -if slot is None or slot.state == "cancelled":
  +if slot is None or slot.state == "XXcancelledXX":
  ```
- `_settle` · survived · string: upper case · `7c7d4347e83ef5ac`
  ```diff
  -if slot is None or slot.state == "cancelled":
  +if slot is None or slot.state == "CANCELLED":
  ```
- `_settle` · survived · argument: replaced by None · `f3b9771b879b7225`
  ```diff
  -return _Judgement("refused", ("ledger_commit_without_reservation",))
  +return _Judgement(None, ("ledger_commit_without_reservation",))
  ```
- `_settle` · survived · string: XX added · `ad0cbcb6777e00a9`
  ```diff
  -return _Judgement("refused", ("ledger_commit_without_reservation",))
  +return _Judgement("XXrefusedXX", ("ledger_commit_without_reservation",))
  ```
- `_settle` · survived · string: upper case · `b9f6b0f1f15323a8`
  ```diff
  -return _Judgement("refused", ("ledger_commit_without_reservation",))
  +return _Judgement("REFUSED", ("ledger_commit_without_reservation",))
  ```
- `_settle` · survived · operator: and / or · `f18a084f707788fd`
  ```diff
  -if slot.state == "settled" and _same_settlement(slot, operation):
  +if slot.state == "settled" or _same_settlement(slot, operation):
  ```
- `_settle` · survived · string: XX added · `a8c95917414fc76c`
  ```diff
  -(slot.batch_id != operation.batch_id, "ledger_conflicting_reservation"),
  +(slot.batch_id != operation.batch_id, "XXledger_conflicting_reservationXX"),
  ```
- `_settle` · survived · string: upper case · `0303dfe2a3fe1050`
  ```diff
  -(slot.batch_id != operation.batch_id, "ledger_conflicting_reservation"),
  +(slot.batch_id != operation.batch_id, "LEDGER_CONFLICTING_RESERVATION"),
  ```
- `_settle` · survived · string: XX added · `2d1482b2de4ed0be`
  ```diff
  -(slot.state == "settled", "ledger_duplicate_settlement"),
  +(slot.state == "XXsettledXX", "ledger_duplicate_settlement"),
  ```
- `_settle` · survived · string: upper case · `5b33711340ec2c0b`
  ```diff
  -(slot.state == "settled", "ledger_duplicate_settlement"),
  +(slot.state == "SETTLED", "ledger_duplicate_settlement"),
  ```
- `_settle` · survived · string: XX added · `42c7fe541d60e588`
  ```diff
  -(slot.state == "settled", "ledger_duplicate_settlement"),
  +(slot.state == "settled", "XXledger_duplicate_settlementXX"),
  ```
- `_settle` · survived · string: upper case · `9d7de82043199852`
  ```diff
  -(slot.state == "settled", "ledger_duplicate_settlement"),
  +(slot.state == "settled", "LEDGER_DUPLICATE_SETTLEMENT"),
  ```
- `_settle` · survived · argument: replaced by None · `d9dc2bac81943aff`
  ```diff
  -return _Judgement("refused", reasons) if reasons else _Judgement("new")
  +return _Judgement(None, reasons) if reasons else _Judgement("new")
  ```
- `_settle` · survived · string: XX added · `5ed814b5d5f248c2`
  ```diff
  -return _Judgement("refused", reasons) if reasons else _Judgement("new")
  +return _Judgement("XXrefusedXX", reasons) if reasons else _Judgement("new")
  ```
- `_settle` · survived · string: upper case · `c82320ee51da0293`
  ```diff
  -return _Judgement("refused", reasons) if reasons else _Judgement("new")
  +return _Judgement("REFUSED", reasons) if reasons else _Judgement("new")
  ```
- `_cancel` · survived · string: XX added · `eb185fc425fc45c7`
  ```diff
  -if slot is not None and slot.state == "cancelled" and _same(slot, operation):
  +if slot is not None and slot.state == "XXcancelledXX" and _same(slot, operation):
  ```
- `_cancel` · survived · string: upper case · `b59bd197be3d59b1`
  ```diff
  -if slot is not None and slot.state == "cancelled" and _same(slot, operation):
  +if slot is not None and slot.state == "CANCELLED" and _same(slot, operation):
  ```
- `_cancel` · survived · argument: replaced by None · `55c268d1df29895d`
  ```diff
  -if slot is not None and slot.state == "cancelled" and _same(slot, operation):
  +if slot is not None and slot.state == "cancelled" and _same(None, operation):
  ```
- `_cancel` · survived · argument: replaced by None · `77368d3adc64226f`
  ```diff
  -if slot is not None and slot.state == "cancelled" and _same(slot, operation):
  +if slot is not None and slot.state == "cancelled" and _same(slot, None):
  ```
- `_cancel` · survived · argument: removed · `48fc0e7076e7062d`
  ```diff
  -if slot is not None and slot.state == "cancelled" and _same(slot, operation):
  +if slot is not None and slot.state == "cancelled" and _same(operation):
  ```
- `_cancel` · survived · argument: removed · `f12c75dee24e21c4`
  ```diff
  -if slot is not None and slot.state == "cancelled" and _same(slot, operation):
  +if slot is not None and slot.state == "cancelled" and _same(slot, ):
  ```
- `_cancel` · survived · argument: replaced by None · `a8b419baa2c16129`
  ```diff
  -return _Judgement("repeat")
  +return _Judgement(None)
  ```
- `_cancel` · survived · string: XX added · `80f202de6a1b047d`
  ```diff
  -return _Judgement("repeat")
  +return _Judgement("XXrepeatXX")
  ```
- `_cancel` · survived · string: upper case · `b52f3220a5f8993b`
  ```diff
  -return _Judgement("repeat")
  +return _Judgement("REPEAT")
  ```
- `_cancel` · survived · argument: replaced by None · `13899937935ade40`
  ```diff
  -return _Judgement("refused", ("ledger_cancel_without_reservation",))
  +return _Judgement(None, ("ledger_cancel_without_reservation",))
  ```
- `_cancel` · survived · string: XX added · `b5d2fa23edf36cfe`
  ```diff
  -return _Judgement("refused", ("ledger_cancel_without_reservation",))
  +return _Judgement("XXrefusedXX", ("ledger_cancel_without_reservation",))
  ```
- `_cancel` · survived · string: upper case · `548b838616333a5b`
  ```diff
  -return _Judgement("refused", ("ledger_cancel_without_reservation",))
  +return _Judgement("REFUSED", ("ledger_cancel_without_reservation",))
  ```
- `_cancel` · survived · argument: replaced by None · `f0092370f406ebf8`
  ```diff
  -return _Judgement("refused", ("ledger_conflicting_reservation",))
  +return _Judgement(None, ("ledger_conflicting_reservation",))
  ```
- `_cancel` · survived · argument: replaced by None · `3ada7c520425f941`
  ```diff
  -return _Judgement("refused", ("ledger_conflicting_reservation",))
  +return _Judgement("refused", None)
  ```
- `_cancel` · survived · argument: removed · `dec99e62ff2002f3`
  ```diff
  -return _Judgement("refused", ("ledger_conflicting_reservation",))
  +return _Judgement(("ledger_conflicting_reservation",))
  ```
- `_cancel` · survived · argument: removed · `f4a87ac5e94373a3`
  ```diff
  -return _Judgement("refused", ("ledger_conflicting_reservation",))
  +return _Judgement("refused", )
  ```
- `_cancel` · survived · string: XX added · `22a193f43d298f47`
  ```diff
  -return _Judgement("refused", ("ledger_conflicting_reservation",))
  +return _Judgement("XXrefusedXX", ("ledger_conflicting_reservation",))
  ```
- `_cancel` · survived · string: upper case · `c9b125efbe37898e`
  ```diff
  -return _Judgement("refused", ("ledger_conflicting_reservation",))
  +return _Judgement("REFUSED", ("ledger_conflicting_reservation",))
  ```
- `_cancel` · survived · string: XX added · `de87bcda432c1ae2`
  ```diff
  -return _Judgement("refused", ("ledger_conflicting_reservation",))
  +return _Judgement("refused", ("XXledger_conflicting_reservationXX",))
  ```
- `_cancel` · survived · string: upper case · `628735da7bb2ac6c`
  ```diff
  -return _Judgement("refused", ("ledger_conflicting_reservation",))
  +return _Judgement("refused", ("LEDGER_CONFLICTING_RESERVATION",))
  ```
- `_over_budget` · survived · operator: and / or · `9e70e44067a1cda6`
  ```diff
  -if constraints is None or not verified("publication_limits", books.limits):
  +if constraints is None and not verified("publication_limits", books.limits):
  ```
- `_over_budget` · survived · argument: replaced by None · `8742fe49b51d1020`
  ```diff
  -planned = _plus(_plus(books.settled.get(day, _ZERO), _reserved(books, day)), operation.amounts)
  +planned = _plus(_plus(books.settled.get(None, _ZERO), _reserved(books, day)), operation.amounts)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_limits.py</code>: 24</summary>

- `verify_limits` · survived · string: XX added · `a89e7ade74f358b6`
  ```diff
  -["revocation_not_verified"] if any(c.status != "verified" for c in revocations) else []
  +["revocation_not_verified"] if any(c.status != "XXverifiedXX" for c in revocations) else []
  ```
- `verify_limits` · survived · string: upper case · `88db351998a13f91`
  ```diff
  -["revocation_not_verified"] if any(c.status != "verified" for c in revocations) else []
  +["revocation_not_verified"] if any(c.status != "VERIFIED" for c in revocations) else []
  ```
- `utc` · survived · operator: and / or · `f475ef045ba9dda7`
  ```diff
  -if offset is None or offset.total_seconds() != 0:
  +if offset is None and offset.total_seconds() != 0:
  ```
- `utc` · survived · argument: replaced by None · `8965de5d8cb24f7b`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
  +raise ForarbeiderRefusedError(None, "check time must be UTC")
  ```
- `utc` · survived · argument: replaced by None · `827c4c9a03a741ac`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, None)
  ```
- `utc` · survived · argument: removed · `8f6784ddd9ced3b3`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
  +raise ForarbeiderRefusedError("check time must be UTC")
  ```
- `utc` · survived · argument: removed · `52585026503fc7ca`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, )
  ```
- `utc` · survived · string: XX added · `08a1981158a3e0db`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "XXcheck time must be UTCXX")
  ```
- `utc` · survived · string: lower case · `f0293d207c459cd6`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be utc")
  ```
- `utc` · survived · string: upper case · `734af87538bfb77f`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
  +raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "CHECK TIME MUST BE UTC")
  ```
- `_limits` · survived · operator: comparison · `2c72db48c990378d`
  ```diff
  -if ctx.at < record.valid_from:
  +if ctx.at <= record.valid_from:
  ```
- `_limits` · survived · operator: comparison · `c60c097e0d90a8ee`
  ```diff
  -if ctx.at >= record.valid_until:
  +if ctx.at > record.valid_until:
  ```
- `_limits` · survived · argument: removed · `b2a4147c8fce2933`
  ```diff
  -check._replace(status="invalid", reasons=tuple(reasons), record=None) if reasons else check
  +check._replace(status="invalid", reasons=tuple(reasons), ) if reasons else check
  ```
- `_verdict` · survived · operator: comparison · `b0d3d867ebecac71`
  ```diff
  -if len(good) > 1:
  +if len(good) >= 1:
  ```
- `_sealed` · survived · operator: and / or · `8bceb3f73278b50e`
  ```diff
  -if not isinstance(record, ReleaseLimits) or entry is None:
  +if not isinstance(record, ReleaseLimits) and entry is None:
  ```
- `_sealed` · survived · argument: replaced by None · `3ce1f604ccb0e9c9`
  ```diff
  -return _missing(("limits_unverifiable",))
  +return _missing(None)
  ```
- `_sealed` · survived · string: XX added · `daeef2dd1fae81fd`
  ```diff
  -return _missing(("limits_unverifiable",))
  +return _missing(("XXlimits_unverifiableXX",))
  ```
- `_sealed` · survived · string: upper case · `ba9bc683fdaff32e`
  ```diff
  -return _missing(("limits_unverifiable",))
  +return _missing(("LIMITS_UNVERIFIABLE",))
  ```
- `_sealed` · survived · argument: replaced by None · `f29d71bc0bd2729e`
  ```diff
  -policy=PolicyReference(
  -policy_id=ctx.policy.policy_id,
  -policy_version=ctx.policy.policy_version,
  -policy_sha256=policy_digest(ctx.policy),
  -),
  +policy=None,
  ```
- `_sealed` · survived · argument: replaced by None · `2d3f98dc9d94ea43`
  ```diff
  -limits_id=record.limits_id,
  +limits_id=None,
  ```
- `_sealed` · survived · argument: replaced by None · `036b47c2c868efdf`
  ```diff
  -signer_fingerprint=entry.fingerprint,
  +signer_fingerprint=None,
  ```
- `_sealed` · survived · argument: removed · `16f7a88b4478ce6e`
  ```diff
  -policy=PolicyReference(
  -policy_id=ctx.policy.policy_id,
  -policy_version=ctx.policy.policy_version,
  -policy_sha256=policy_digest(ctx.policy),
  -),
  ```
- `_sealed` · survived · argument: removed · `2a7df3953197d5b0`
  ```diff
  -limits_id=record.limits_id,
  ```
- `_sealed` · survived · argument: removed · `f0c55d2538c11a33`
  ```diff
  -signer_fingerprint=entry.fingerprint,
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_lock.py</code>: 9</summary>

- `_at_name` · survived · argument: replaced by None · `3c656c270b78a0d3`
  ```diff
  -return os.stat(_NAME, dir_fd=git, follow_symlinks=False)
  +return os.stat(_NAME, dir_fd=git, follow_symlinks=None)
  ```
- `_at_name` · survived · argument: removed · `c4f5393228bafa67`
  ```diff
  -return os.stat(_NAME, dir_fd=git, follow_symlinks=False)
  +return os.stat(_NAME, dir_fd=git, )
  ```
- `_at_name` · survived · True / False swapped · `41dcaafa77e91d16`
  ```diff
  -return os.stat(_NAME, dir_fd=git, follow_symlinks=False)
  +return os.stat(_NAME, dir_fd=git, follow_symlinks=True)
  ```
- `_claim` · survived · operator: and / or · `b318959fb16911ac`
  ```diff
  -if named is None or (named.st_dev, named.st_ino) != (mine.st_dev, mine.st_ino):
  +if named is None and (named.st_dev, named.st_ino) != (mine.st_dev, mine.st_ino):
  ```
- `_claim` · survived · operator: and / or · `dc032ce152dac940`
  ```diff
  -private = mine.st_uid == os.geteuid() and not stat.S_IMODE(mine.st_mode) & 0o077
  +private = mine.st_uid == os.geteuid() or not stat.S_IMODE(mine.st_mode) & 0o077
  ```
- `_claim` · survived · number · `44d5b0f8e37e9ecb`
  ```diff
  -private = mine.st_uid == os.geteuid() and not stat.S_IMODE(mine.st_mode) & 0o077
  +private = mine.st_uid == os.geteuid() and not stat.S_IMODE(mine.st_mode) & 64
  ```
- `_claim` · survived · operator: and / or · `a3f7ef52e7358a33`
  ```diff
  -if not stat.S_ISREG(mine.st_mode) or mine.st_nlink != 1 or not private:
  +if not stat.S_ISREG(mine.st_mode) or mine.st_nlink != 1 and not private:
  ```
- `_claim` · survived · operator: and / or · `e8dba390148ba706`
  ```diff
  -if not stat.S_ISREG(mine.st_mode) or mine.st_nlink != 1 or not private:
  +if not stat.S_ISREG(mine.st_mode) and mine.st_nlink != 1 or not private:
  ```
- `held_by_us` · survived · True / False swapped · `14f17e263792cd80`
  ```diff
  -return False
  +return True
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_plan.py</code>: 37</summary>

- `plan_release` · survived · argument: replaced by None · `02f4893d52e0b228`
  ```diff
  -manifest = build_release(request.offers, request.artifacts, root)
  +manifest = build_release(request.offers, request.artifacts, None)
  ```
- `plan_release` · survived · argument: removed · `271d10fe475556ff`
  ```diff
  -manifest = build_release(request.offers, request.artifacts, root)
  +manifest = build_release(request.offers, request.artifacts, )
  ```
- `plan_release` · survived · argument: replaced by None · `6fd01da58ed956e3`
  ```diff
  -return _invalid(["plan_inputs_incomplete"])
  +return _invalid(None)
  ```
- `plan_release` · survived · string: XX added · `00118927614d1e44`
  ```diff
  -return _invalid(["plan_inputs_incomplete"])
  +return _invalid(["XXplan_inputs_incompleteXX"])
  ```
- `plan_release` · survived · string: upper case · `0b9e7a80ffb06f29`
  ```diff
  -return _invalid(["plan_inputs_incomplete"])
  +return _invalid(["PLAN_INPUTS_INCOMPLETE"])
  ```
- `_context` · survived · operator: and / or · `a8d98db5cf9fa79b`
  ```diff
  -if record is None or version is None or digest is None or inventory.policy is None:
  +if record is None or version is None or digest is None and inventory.policy is None:
  ```
- `_context` · survived · operator: and / or · `4c5e2b4f500e75b8`
  ```diff
  -if record is None or version is None or digest is None or inventory.policy is None:
  +if record is None or version is None and digest is None or inventory.policy is None:
  ```
- `_context` · survived · operator: and / or · `80a69f41154b3fa0`
  ```diff
  -if record is None or version is None or digest is None or inventory.policy is None:
  +if record is None and version is None or digest is None or inventory.policy is None:
  ```
- `_context` · survived · operator: and / or · `1acb5c2ba4fcafc5`
  ```diff
  -if limits.constraints is None or limits.limits_sha256 is None:
  +if limits.constraints is None and limits.limits_sha256 is None:
  ```
- `_rows` · survived · argument: replaced by None · `6c9073f08d63c29c`
  ```diff
  -pairs = zip(manifest.content.documents, manifest.bindings, strict=True)
  +pairs = zip(manifest.content.documents, manifest.bindings, strict=None)
  ```
- `_rows` · survived · argument: removed · `47f704d5d4c37326`
  ```diff
  -pairs = zip(manifest.content.documents, manifest.bindings, strict=True)
  +pairs = zip(manifest.content.documents, manifest.bindings, )
  ```
- `_rows` · survived · True / False swapped · `71b15961fe938bce`
  ```diff
  -pairs = zip(manifest.content.documents, manifest.bindings, strict=True)
  +pairs = zip(manifest.content.documents, manifest.bindings, strict=False)
  ```
- `_draft` · survived · argument: replaced by None · `48b1a416544b10fa`
  ```diff
  -return _unsized(document, "outside_inventory", found, ("document_outside_inventory",))
  +return _unsized(document, "outside_inventory", None, ("document_outside_inventory",))
  ```
- `_draft` · survived · argument: replaced by None · `2e806f27726d5ea4`
  ```diff
  -return _unsized(document, "excluded", found, _own_blockers(selection))
  +return _unsized(document, "excluded", None, _own_blockers(selection))
  ```
- `_draft` · survived · argument: replaced by None · `e7d7175f9bfce453`
  ```diff
  -return _unsized(document, "excluded", found, damaged)
  +return _unsized(document, "excluded", None, damaged)
  ```
- `_unsized` · survived · number · `1c31a3f998cfa554`
  ```diff
  -return _Draft(document, disposition, None, None, None, (), 0, reasons)
  +return _Draft(document, disposition, None, None, None, (), 1, reasons)
  ```
- `_blocking` · survived · operator: comparison · `69b77403fdc4d81f`
  ```diff
  -if files > caps.files or size > caps.bytes:
  +if files >= caps.files or size > caps.bytes:
  ```
- `_blocking` · survived · operator: comparison · `57081a9096fdf5fa`
  ```diff
  -if files > caps.files or size > caps.bytes:
  +if files > caps.files or size >= caps.bytes:
  ```
- `_batch` · survived · string: XX added · `baeccc5d89ecbd42`
  ```diff
  -+ ",".join(d.artifact_sha256s)
  ++ "XX,XX".join(d.artifact_sha256s)
  ```
- `parse_plan` · survived · argument: replaced by None · `78d882d098a3727c`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release plan") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None) from error
  ```
- `parse_plan` · survived · string: XX added · `b9a9884205c6a788`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release plan") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXrelease planXX") from error
  ```
- `parse_plan` · survived · string: upper case · `2e4c3786ead8f9c4`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release plan") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "RELEASE PLAN") from error
  ```
- `parse_plan` · survived · argument: replaced by None · `2e233d7d35540b21`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release plan")
  +raise ForarbeiderRefusedError(None, "release plan")
  ```
- `parse_plan` · survived · argument: replaced by None · `615601fcbfc6e6a1`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release plan")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `parse_plan` · survived · argument: removed · `3bc5f4a387d9f483`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release plan")
  +raise ForarbeiderRefusedError("release plan")
  ```
- `parse_plan` · survived · argument: removed · `2136be63f989d6bd`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release plan")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, )
  ```
- `parse_plan` · survived · string: XX added · `bab8b081b08be3c0`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release plan")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXrelease planXX")
  ```
- `parse_plan` · survived · string: upper case · `76bbf91e0ef634c4`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release plan")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "RELEASE PLAN")
  ```
- `verify_plan` · survived · ternary: condition forced · `2ade97c092c2920d`
  ```diff
  -found = [] if offered else ["plan_malformed"]
  +found = [] if (offered) and False else ["plan_malformed"]
  ```
- `verify_plan` · survived · string: XX added · `c310306e17c6c616`
  ```diff
  -found = [] if offered else ["plan_malformed"]
  +found = [] if offered else ["XXplan_malformedXX"]
  ```
- `verify_plan` · survived · string: upper case · `37ad3f5516933ee5`
  ```diff
  -found = [] if offered else ["plan_malformed"]
  +found = [] if offered else ["PLAN_MALFORMED"]
  ```
- `verify_plan` · survived · ternary: condition forced · `69728f59cb686c5c`
  ```diff
  -found += [] if rebuilt else ["plan_unverifiable"]
  +found += [] if (rebuilt) and False else ["plan_unverifiable"]
  ```
- `verify_plan` · survived · ternary: condition forced · `8b40d554ca1e95e0`
  ```diff
  -found += [] if rebuilt else ["plan_unverifiable"]
  +found += [] if (rebuilt) or True else ["plan_unverifiable"]
  ```
- `verify_plan` · survived · string: XX added · `3f82949f3021d5cc`
  ```diff
  -found += [] if rebuilt else ["plan_unverifiable"]
  +found += [] if rebuilt else ["XXplan_unverifiableXX"]
  ```
- `verify_plan` · survived · string: upper case · `71b9b10b0c5e5e39`
  ```diff
  -found += [] if rebuilt else ["plan_unverifiable"]
  +found += [] if rebuilt else ["PLAN_UNVERIFIABLE"]
  ```
- `verify_plan` · survived · string: XX added · `9715d70096d36eb6`
  ```diff
  -return PlanVerification(status="stale", findings=("plan_stale",))
  +return PlanVerification(status="stale", findings=("XXplan_staleXX",))
  ```
- `verify_plan` · survived · string: upper case · `c2f41929729c5353`
  ```diff
  -return PlanVerification(status="stale", findings=("plan_stale",))
  +return PlanVerification(status="stale", findings=("PLAN_STALE",))
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_plan_models.py</code>: 28</summary>

- `_sorted_once` · survived · argument: replaced by None · `88d61ce696f6e9e0`
  ```diff
  -raise ValueError(f"{what} are sorted, once each")
  +raise ValueError(None)
  ```
- `plan_digest` · survived · argument: removed · `bd60f9a0e60a029f`
  ```diff
  -return digest_of(basis, *documents, *batches)
  +return digest_of(basis, *batches)
  ```
- `plan_digest` · survived · argument: removed · `423abadda37c3d9b`
  ```diff
  -return digest_of(basis, *documents, *batches)
  +return digest_of(basis, *documents, )
  ```
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
- `_check_batches` · survived · argument: replaced by None · `2c710c51d4e86fdc`
  ```diff
  -raise ValueError("batches are numbered 1, 2, 3... in order")
  +raise ValueError(None)
  ```
- `_check_batches` · survived · string: XX added · `1f35e2d5b28a6a11`
  ```diff
  -raise ValueError("batches are numbered 1, 2, 3... in order")
  +raise ValueError("XXbatches are numbered 1, 2, 3... in orderXX")
  ```
- `_check_batches` · survived · string: upper case · `f924d109987eca8b`
  ```diff
  -raise ValueError("batches are numbered 1, 2, 3... in order")
  +raise ValueError("BATCHES ARE NUMBERED 1, 2, 3... IN ORDER")
  ```
- `_check_batches` · survived · argument: replaced by None · `b172a7b9d59ca20d`
  ```diff
  -raise ValueError("every planned document is in exactly one batch, and only those")
  +raise ValueError(None)
  ```
- `_check_batches` · survived · string: XX added · `dd78c5b40a8b7456`
  ```diff
  -raise ValueError("every planned document is in exactly one batch, and only those")
  +raise ValueError("XXevery planned document is in exactly one batch, and only thoseXX")
  ```
- `_check_batches` · survived · string: upper case · `22625396901b0b0a`
  ```diff
  -raise ValueError("every planned document is in exactly one batch, and only those")
  +raise ValueError("EVERY PLANNED DOCUMENT IS IN EXACTLY ONE BATCH, AND ONLY THOSE")
  ```
- `_check_batch` · survived · argument: replaced by None · `1527d4f531d56b36`
  ```diff
  -raise ValueError("a batch's documents name the batch")
  +raise ValueError(None)
  ```
- `_check_batch` · survived · string: XX added · `bae8d794de5a943b`
  ```diff
  -raise ValueError("a batch's documents name the batch")
  +raise ValueError("XXa batch's documents name the batchXX")
  ```
- `_check_batch` · survived · string: upper case · `5da6d1f604158a9f`
  ```diff
  -raise ValueError("a batch's documents name the batch")
  +raise ValueError("A BATCH'S DOCUMENTS NAME THE BATCH")
  ```
- `_check_batch` · survived · argument: replaced by None · `c0d771c5a2f4f361`
  ```diff
  -raise ValueError("a batch's size is its documents' sum")
  +raise ValueError(None)
  ```
- `_check_batch` · survived · string: XX added · `9ec6da1c0770de2a`
  ```diff
  -raise ValueError("a batch's size is its documents' sum")
  +raise ValueError("XXa batch's size is its documents' sumXX")
  ```
- `_check_batch` · survived · string: upper case · `9a3d726615df0477`
  ```diff
  -raise ValueError("a batch's size is its documents' sum")
  +raise ValueError("A BATCH'S SIZE IS ITS DOCUMENTS' SUM")
  ```
- `_check_batch` · survived · argument: replaced by None · `9c66a8521ac08c12`
  ```diff
  -raise ValueError("a batch keeps its caps")
  +raise ValueError(None)
  ```
- `_check_batch` · survived · string: XX added · `34d7a97ce8c1505c`
  ```diff
  -raise ValueError("a batch keeps its caps")
  +raise ValueError("XXa batch keeps its capsXX")
  ```
- `_check_batch` · survived · string: upper case · `8442a9393d02d3eb`
  ```diff
  -raise ValueError("a batch keeps its caps")
  +raise ValueError("A BATCH KEEPS ITS CAPS")
  ```
- `_within` · survived · operator: and / or · `6c08b9b55348c971`
  ```diff
  -return size[0] <= caps.documents and size[1] <= caps.files and size[2] <= caps.bytes
  +return size[0] <= caps.documents and size[1] <= caps.files or size[2] <= caps.bytes
  ```
- `_within` · survived · operator: and / or · `ffe73bfb718fc796`
  ```diff
  -return size[0] <= caps.documents and size[1] <= caps.files and size[2] <= caps.bytes
  +return size[0] <= caps.documents or size[1] <= caps.files and size[2] <= caps.bytes
  ```
- `_within` · survived · number · `53e18a74b7e0b1fa`
  ```diff
  -return size[0] <= caps.documents and size[1] <= caps.files and size[2] <= caps.bytes
  +return size[1] <= caps.documents and size[1] <= caps.files and size[2] <= caps.bytes
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_provenance.py</code>: 1</summary>

- `authentic` · survived · operator: and / or · `800dc2952b12f661`
  ```diff
  -if provenance is None or provenance.kind != kind or provenance.verifier != VERIFIERS[kind]:
  +if provenance is None or provenance.kind != kind and provenance.verifier != VERIFIERS[kind]:
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_reader.py</code>: 65</summary>

- `require_v2` · survived · argument: replaced by None · `2bbeaf47cc045cfd`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "manifest")
  +raise ForarbeiderRefusedError(None, "manifest")
  ```
- `require_v2` · survived · argument: replaced by None · `90746f9843066753`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "manifest")
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, None)
  ```
- `require_v2` · survived · argument: removed · `8dfcba6df52dedb2`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "manifest")
  +raise ForarbeiderRefusedError("manifest")
  ```
- `require_v2` · survived · argument: removed · `9900e4c464aa3321`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "manifest")
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, )
  ```
- `require_v2` · survived · string: XX added · `4d297fe4289fc4eb`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "manifest")
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "XXmanifestXX")
  ```
- `require_v2` · survived · string: upper case · `82e35c37d98ef91a`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "manifest")
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "MANIFEST")
  ```
- `confirmation_of` · survived · operator: comparison · `74fe844831b3243a`
  ```diff
  -if any((item.path, item.sha256) == written for item in record.writes)
  +if any((item.path, item.sha256) != written for item in record.writes)
  ```
- `confirmation_of` · survived · argument: replaced by None · `d3ead24c058a1d66`
  ```diff
  -raise _mismatch("documents")
  +raise _mismatch(None)
  ```
- `confirmation_of` · survived · string: XX added · `0d5c09d750c95914`
  ```diff
  -raise _mismatch("documents")
  +raise _mismatch("XXdocumentsXX")
  ```
- `confirmation_of` · survived · string: upper case · `a62ffd1e5eaeadc0`
  ```diff
  -raise _mismatch("documents")
  +raise _mismatch("DOCUMENTS")
  ```
- `_planned` · survived · argument: removed · `956198bbb07a1206`
  ```diff
  -found = next((item for item in plan.documents if item.document == member.document), None)
  +found = next((item for item in plan.documents if item.document == member.document), )
  ```
- `_planned` · survived · operator: and / or · `1c2bc9b5ba1d6883`
  ```diff
  -if found is None or found.destination != member.path:
  +if found is None and found.destination != member.path:
  ```
- `_planned` · survived · argument: replaced by None · `f93b0c4b2958b709`
  ```diff
  -raise _mismatch("plan")
  +raise _mismatch(None)
  ```
- `_planned` · survived · string: XX added · `9a2f9f29ab39255f`
  ```diff
  -raise _mismatch("plan")
  +raise _mismatch("XXplanXX")
  ```
- `_planned` · survived · string: upper case · `c3a90ca9d1f113bf`
  ```diff
  -raise _mismatch("plan")
  +raise _mismatch("PLAN")
  ```
- `require_planned` · survived · argument: replaced by None · `a72edd2fd83efd49`
  ```diff
  -pairs = list(zip(member.chapters, included, strict=False))
  +pairs = list(zip(member.chapters, included, strict=None))
  ```
- `require_planned` · survived · argument: removed · `df32b2c94f1d1318`
  ```diff
  -pairs = list(zip(member.chapters, included, strict=False))
  +pairs = list(zip(member.chapters, included, ))
  ```
- `require_planned` · survived · True / False swapped · `0e1a9fc2e05ead54`
  ```diff
  -pairs = list(zip(member.chapters, included, strict=False))
  +pairs = list(zip(member.chapters, included, strict=True))
  ```
- `require_planned` · survived · argument: replaced by None · `5f657a56081b4a7c`
  ```diff
  -raise _mismatch("binding")
  +raise _mismatch(None)
  ```
- `require_planned` · survived · string: XX added · `a205203a4c815f9b`
  ```diff
  -raise _mismatch("binding")
  +raise _mismatch("XXbindingXX")
  ```
- `require_planned` · survived · string: upper case · `0e251910b6abd345`
  ```diff
  -raise _mismatch("binding")
  +raise _mismatch("BINDING")
  ```
- `require_planned` · survived · argument: replaced by None · `31e59f905d2a3df9`
  ```diff
  -raise _mismatch("times")
  +raise _mismatch(None)
  ```
- `require_planned` · survived · string: XX added · `0918eda7f81fdc3e`
  ```diff
  -raise _mismatch("times")
  +raise _mismatch("XXtimesXX")
  ```
- `require_planned` · survived · string: upper case · `575d1febf368c4e0`
  ```diff
  -raise _mismatch("times")
  +raise _mismatch("TIMES")
  ```
- `require_cited` · survived · operator: and / or · `938faa4881777512`
  ```diff
  -if clearance.report_sha256 is None or clearance.authorization_sha256 is None:
  +if clearance.report_sha256 is None and clearance.authorization_sha256 is None:
  ```
- `require_cited` · survived · argument: replaced by None · `fe6b5ad0b9d59926`
  ```diff
  -raise _mismatch("clearance")
  +raise _mismatch(None)
  ```
- `require_cited` · survived · string: XX added · `098a1eb07a3a720e`
  ```diff
  -raise _mismatch("clearance")
  +raise _mismatch("XXclearanceXX")
  ```
- `require_cited` · survived · string: upper case · `0c721384625a5374`
  ```diff
  -raise _mismatch("clearance")
  +raise _mismatch("CLEARANCE")
  ```
- `front_matter` · survived · number · `e889dd9c5561ba38`
  ```diff
  -end = lines.index(_FRONT, 1)
  +end = lines.index(_FRONT, 2)
  ```
- `front_matter` · survived · argument: replaced by None · `454d013c85adf05c`
  ```diff
  -raise ValueError("no front matter")
  +raise ValueError(None)
  ```
- `front_matter` · survived · string: XX added · `2605e8590901895d`
  ```diff
  -raise ValueError("no front matter")
  +raise ValueError("XXno front matterXX")
  ```
- `front_matter` · survived · string: upper case · `7547a1cc724a770a`
  ```diff
  -raise ValueError("no front matter")
  +raise ValueError("NO FRONT MATTER")
  ```
- `front_matter` · survived · argument: removed · `e351697f20e3af3c`
  ```diff
  -pairs = [line.split(": ", 1) for line in lines[1:end]]
  +pairs = [line.split(": ", ) for line in lines[1:end]]
  ```
- `front_matter` · survived · string method swapped · `b2f8bd31ed3a2795`
  ```diff
  -pairs = [line.split(": ", 1) for line in lines[1:end]]
  +pairs = [line.rsplit(": ", 1) for line in lines[1:end]]
  ```
- `front_matter` · survived · number · `5571020ce59f6903`
  ```diff
  -pairs = [line.split(": ", 1) for line in lines[1:end]]
  +pairs = [line.split(": ", 2) for line in lines[1:end]]
  ```
- `front_matter` · survived · argument: replaced by None · `581d4630b439e532`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "front_matter") from error
  +raise ForarbeiderRefusedError(None, "front_matter") from error
  ```
- `front_matter` · survived · argument: replaced by None · `0ff1e697182a9d19`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "front_matter") from error
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, None) from error
  ```
- `front_matter` · survived · argument: removed · `ad9649303c6d9d93`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "front_matter") from error
  +raise ForarbeiderRefusedError("front_matter") from error
  ```
- `front_matter` · survived · argument: removed · `75863cfedfd9c9e2`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "front_matter") from error
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, ) from error
  ```
- `front_matter` · survived · string: XX added · `ebcd44c99575e904`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "front_matter") from error
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "XXfront_matterXX") from error
  ```
- `front_matter` · survived · string: upper case · `547a444382315052`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "front_matter") from error
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "FRONT_MATTER") from error
  ```
- `_identity` · survived · ternary: condition forced · `ec4ccac71c789968`
  ```diff
  -"published_at": None if published is None else published.isoformat(),
  +"published_at": None if (published is None) or True else published.isoformat(),
  ```
- `verify_release` · survived · string: upper case · `427116f3db10f474`
  ```diff
  -text = markdown.decode("utf-8")
  +text = markdown.decode("UTF-8")
  ```
- `open_release_shelf` · survived · argument: replaced by None · `92704a302c29e36d`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "concurrent")
  +raise ForarbeiderRefusedError(None, "concurrent")
  ```
- `open_release_shelf` · survived · argument: replaced by None · `4f5f0bee140d982b`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "concurrent")
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, None)
  ```
- `open_release_shelf` · survived · argument: removed · `cc50fbe0979a31fa`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "concurrent")
  +raise ForarbeiderRefusedError("concurrent")
  ```
- `open_release_shelf` · survived · argument: removed · `a05750da05500fd8`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "concurrent")
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, )
  ```
- `open_release_shelf` · survived · string: XX added · `0f1c0e618f73e032`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "concurrent")
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "XXconcurrentXX")
  ```
- `open_release_shelf` · survived · string: upper case · `c9bda6947ea93c17`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "concurrent")
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "CONCURRENT")
  ```
- `open_release_shelf` · survived · argument: replaced by None · `f4c4c585fed558a1`
  ```diff
  -return ReleaseShelf(state, attested, dataset.manifest_sha256, releases)
  +return ReleaseShelf(state, attested, None, releases)
  ```
- `chapter_markdown` · survived · argument: replaced by None · `b4551d0da7377ac1`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "chapter")
  +raise ForarbeiderRefusedError(None, "chapter")
  ```
- `chapter_markdown` · survived · argument: replaced by None · `81125eefa3da3c16`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "chapter")
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, None)
  ```
- `chapter_markdown` · survived · argument: removed · `5ea1c387d042474e`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "chapter")
  +raise ForarbeiderRefusedError("chapter")
  ```
- `chapter_markdown` · survived · argument: removed · `bfac652302bf7b84`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "chapter")
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, )
  ```
- `chapter_markdown` · survived · string: XX added · `6727a1f4634281b2`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "chapter")
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "XXchapterXX")
  ```
- `chapter_markdown` · survived · string: upper case · `3d328d89643e79c6`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "chapter")
  +raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "CHAPTER")
  ```
- `chapter_markdown` · survived · assignment: value to None · `8d2a19b2191abf7e`
  ```diff
  -rest = [
  -index for index, line in enumerate(lines) if index > start and line.startswith("Source: <")
  -]
  +rest = None
  ```
- `chapter_markdown` · survived · string: XX added · `999e1295369bf645`
  ```diff
  -index for index, line in enumerate(lines) if index > start and line.startswith("Source: <")
  +index for index, line in enumerate(lines) if index > start and line.startswith("XXSource: <XX")
  ```
- `chapter_markdown` · survived · string: lower case · `c9b512a1cd8af60c`
  ```diff
  -index for index, line in enumerate(lines) if index > start and line.startswith("Source: <")
  +index for index, line in enumerate(lines) if index > start and line.startswith("source: <")
  ```
- `chapter_markdown` · survived · string: upper case · `60486e365954e1a1`
  ```diff
  -index for index, line in enumerate(lines) if index > start and line.startswith("Source: <")
  +index for index, line in enumerate(lines) if index > start and line.startswith("SOURCE: <")
  ```
- `chapter_markdown` · survived · assignment: value to None · `0037922b9c6c3b0f`
  ```diff
  -end = rest[0] if rest else len(lines)
  +end = None
  ```
- `chapter_markdown` · survived · ternary: condition forced · `04806cc1d40d118c`
  ```diff
  -end = rest[0] if rest else len(lines)
  +end = rest[0] if (rest) and False else len(lines)
  ```
- `chapter_markdown` · survived · number · `1526b738c3ff0429`
  ```diff
  -end = rest[0] if rest else len(lines)
  +end = rest[1] if rest else len(lines)
  ```
- `chapter_markdown` · survived · argument: replaced by None · `40a5cf97da414694`
  ```diff
  -return receipt, "\n".join(lines[start:end]).rstrip("\n") + "\n"
  +return receipt, "\n".join(lines[start:end]).rstrip(None) + "\n"
  ```
- `chapter_markdown` · survived · string: XX added · `d69e159637a686b3`
  ```diff
  -return receipt, "\n".join(lines[start:end]).rstrip("\n") + "\n"
  +return receipt, "\n".join(lines[start:end]).rstrip("XX\nXX") + "\n"
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_reader_cli.py</code>: 3</summary>

- `_echo` · survived · argument: replaced by None · `eaa30e291d44490d`
  ```diff
  -typer.echo(refusal_line(error), err=True)
  +typer.echo(refusal_line(error), err=None)
  ```
- `_echo` · survived · argument: removed · `3eda47ebc43ca153`
  ```diff
  -typer.echo(refusal_line(error), err=True)
  +typer.echo(refusal_line(error), )
  ```
- `_echo` · survived · True / False swapped · `35f1c489eafc9a3e`
  ```diff
  -typer.echo(refusal_line(error), err=True)
  +typer.echo(refusal_line(error), err=False)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_reader_views.py</code>: 9</summary>

- `reference_of` · survived · assignment: value to None · `c1b2cd230a718b0b`
  ```diff
  -session = document.session.replace("-", _EN_DASH)
  +session = None
  ```
- `reference_of` · survived · string: XX added · `f967f1fd20d7b3b9`
  ```diff
  -session = document.session.replace("-", _EN_DASH)
  +session = document.session.replace("XX-XX", _EN_DASH)
  ```
- `reference_of` · survived · assignment: value to None · `9a33986e99a5f2b0`
  ```diff
  -label = _TYPE_LABELS[document.document_type]
  +label = None
  ```
- `_notice` · survived · ternary: condition forced · `7bc284a713c0bbac`
  ```diff
  -return PARTIAL_NOTICE if _partial(member) else None
  +return PARTIAL_NOTICE if (_partial(member)) or True else None
  ```
- `release_list_record` · survived · argument: replaced by None · `494cde01f12a0ceb`
  ```diff
  -manifest_sha256=shelf.manifest_sha256,
  +manifest_sha256=None,
  ```
- `release_document_record` · survived · argument: replaced by None · `2ed9d197b6d85a98`
  ```diff
  -ministry=member.ministry,
  +ministry=None,
  ```
- `release_document_record` · survived · argument: replaced by None · `c0d7a671e085eac6`
  ```diff
  -markdown_sha256=member.markdown_sha256,
  +markdown_sha256=None,
  ```
- `release_chapter_record` · survived · argument: replaced by None · `d62e6af5d506ec66`
  ```diff
  -partial_notice=_notice(member),
  +partial_notice=None,
  ```
- `release_provenance_record` · survived · argument: replaced by None · `d60f536a244ca170`
  ```diff
  -manifest_sha256=shelf.manifest_sha256,
  +manifest_sha256=None,
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_signed.py</code>: 15</summary>

- `verify_owner_record` · survived · argument: replaced by None · `f9f7c386babdf663`
  ```diff
  -return OwnerCheck("invalid", (refusal,))
  +return OwnerCheck(None, (refusal,))
  ```
- `verify_owner_record` · survived · string: XX added · `90e4343886c519fc`
  ```diff
  -return OwnerCheck("invalid", (refusal,))
  +return OwnerCheck("XXinvalidXX", (refusal,))
  ```
- `verify_owner_record` · survived · string: upper case · `9665711101fe0cbf`
  ```diff
  -return OwnerCheck("invalid", (refusal,))
  +return OwnerCheck("INVALID", (refusal,))
  ```
- `verify_owner_record` · survived · argument: replaced by None · `60e2171c243b22b9`
  ```diff
  -return OwnerCheck("missing", ("signature_missing",))
  +return OwnerCheck(None, ("signature_missing",))
  ```
- `verify_owner_record` · survived · string: XX added · `38f65a98faa3c078`
  ```diff
  -return OwnerCheck("missing", ("signature_missing",))
  +return OwnerCheck("XXmissingXX", ("signature_missing",))
  ```
- `verify_owner_record` · survived · string: upper case · `9d7c445834fcef01`
  ```diff
  -return OwnerCheck("missing", ("signature_missing",))
  +return OwnerCheck("MISSING", ("signature_missing",))
  ```
- `verify_owner_record` · survived · argument: replaced by None · `957c402d6e8a0704`
  ```diff
  -return OwnerCheck("invalid", reasons, entry=found.entry)
  +return OwnerCheck("invalid", reasons, entry=None)
  ```
- `verify_owner_record` · survived · argument: removed · `7505fa3b392db4bc`
  ```diff
  -return OwnerCheck("invalid", reasons, entry=found.entry)
  +return OwnerCheck("invalid", reasons, )
  ```
- `parsed` · survived · string: XX added · `adc02404acd1dd50`
  ```diff
  -return record, ""
  +return record, "XXXX"
  ```
- `_refusals` · survived · argument: replaced by None · `6904cf86e2cdd80e`
  ```diff
  -reasons.append("signer_not_owner")
  +reasons.append(None)
  ```
- `_refusals` · survived · string: XX added · `ed0c03913abb9798`
  ```diff
  -reasons.append("signer_not_owner")
  +reasons.append("XXsigner_not_ownerXX")
  ```
- `_refusals` · survived · string: upper case · `eafcb8ffe9b53621`
  ```diff
  -reasons.append("signer_not_owner")
  +reasons.append("SIGNER_NOT_OWNER")
  ```
- `_refusals` · survived · operator: comparison · `551f63fb2aa4d057`
  ```diff
  -if stamped.signed_at > at:
  +if stamped.signed_at >= at:
  ```
- `_refusals` · survived · operator: comparison · `fd24321f0d3a342f`
  ```diff
  -if not entry.valid_after <= at < entry.valid_before:
  +if not entry.valid_after < at < entry.valid_before:
  ```
- `_refusals` · survived · operator: comparison · `c188e1c7f6df587b`
  ```diff
  -if not entry.valid_after <= at < entry.valid_before:
  +if not entry.valid_after <= at <= entry.valid_before:
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/release_sources.py</code>: 52</summary>

- `Unavailable.anchor` · survived · string: XX added · `2d17f7a0bbce3106`
  ```diff
  -reasons=("anchor_source_not_implemented",),
  +reasons=("XXanchor_source_not_implementedXX",),
  ```
- `Unavailable.anchor` · survived · string: upper case · `8f74634ba708b178`
  ```diff
  -reasons=("anchor_source_not_implemented",),
  +reasons=("ANCHOR_SOURCE_NOT_IMPLEMENTED",),
  ```
- `Unavailable.assessment` · survived · string: XX added · `7d0039bd498d3c47`
  ```diff
  -reasons=("legal_assessment_source_not_implemented",),
  +reasons=("XXlegal_assessment_source_not_implementedXX",),
  ```
- `Unavailable.assessment` · survived · string: upper case · `9352f95c41d08843`
  ```diff
  -reasons=("legal_assessment_source_not_implemented",),
  +reasons=("LEGAL_ASSESSMENT_SOURCE_NOT_IMPLEMENTED",),
  ```
- `_one_root` · survived · string: XX added · `5897fdea2d87d8a9`
  ```diff
  -stored = artifacts.get(binding.authorization_sha256 or "")
  +stored = artifacts.get(binding.authorization_sha256 or "XXXX")
  ```
- `_one_root` · survived · break / continue swapped · `bb42b51aac49ea73`
  ```diff
  -continue
  +break
  ```
- `_one_root` · survived · break / continue swapped · `bb42b51aac49ea73-2`
  ```diff
  -continue
  +break
  ```
- `_one_root` · survived · string: XX added · `91025468eeb4e2f6`
  ```diff
  -"was verified under another root",
  +"XXwas verified under another rootXX",
  ```
- `_one_root` · survived · string: upper case · `f4b6b54cb648ce55`
  ```diff
  -"was verified under another root",
  +"WAS VERIFIED UNDER ANOTHER ROOT",
  ```
- `_controls` · survived · assignment: value to None · `bbdaeb2c025bfdbc`
  ```diff
  -policy = _single_policy(manifest)
  +policy = None
  ```
- `_controls` · survived · argument: replaced by None · `9c423b8d1fa31d06`
  ```diff
  -material = LimitsRequest(_policy_bytes(policy, request.offers), sources.limits.limits(), at)
  +material = LimitsRequest(None, sources.limits.limits(), at)
  ```
- `_controls` · survived · argument: replaced by None · `66d048b7426f27a6`
  ```diff
  -material = LimitsRequest(_policy_bytes(policy, request.offers), sources.limits.limits(), at)
  +material = LimitsRequest(_policy_bytes(None, request.offers), sources.limits.limits(), at)
  ```
- `_controls` · survived · argument: replaced by None · `14856358c15bbf96`
  ```diff
  -history = ControlRequest(sources.control.history(), _policy_id(policy), at)
  +history = ControlRequest(sources.control.history(), None, at)
  ```
- `_controls` · survived · argument: replaced by None · `afdf73ddb16306e8`
  ```diff
  -history = ControlRequest(sources.control.history(), _policy_id(policy), at)
  +history = ControlRequest(sources.control.history(), _policy_id(None), at)
  ```
- `_controls` · survived · argument: replaced by None · `d4eb55aec2b986cf`
  ```diff
  -ledger = LedgerRequest(sources.ledger.entries(), limits, at.date(), request.intent.release_id)
  +ledger = LedgerRequest(None, limits, at.date(), request.intent.release_id)
  ```
- `_controls` · survived · argument: replaced by None · `e002c342ed5f2743`
  ```diff
  -ledger = LedgerRequest(sources.ledger.entries(), limits, at.date(), request.intent.release_id)
  +ledger = LedgerRequest(sources.ledger.entries(), None, at.date(), request.intent.release_id)
  ```
- `_controls` · survived · argument: replaced by None · `5995f0d13ed3d382`
  ```diff
  -ledger = LedgerRequest(sources.ledger.entries(), limits, at.date(), request.intent.release_id)
  +ledger = LedgerRequest(sources.ledger.entries(), limits, None, request.intent.release_id)
  ```
- `_controls` · survived · argument: replaced by None · `6b94b5a1110b9fed`
  ```diff
  -ledger = LedgerRequest(sources.ledger.entries(), limits, at.date(), request.intent.release_id)
  +ledger = LedgerRequest(sources.ledger.entries(), limits, at.date(), None)
  ```
- `_single_policy` · survived · is / in negated · `c81458a56eab8e90`
  ```diff
  -found = {d.policy for d in manifest.content.documents if d.policy is not None}
  +found = {d.policy for d in manifest.content.documents if d.policy is None}
  ```
- `_single_policy` · survived · ternary: condition forced · `9d01dbab2bd92777`
  ```diff
  -return next(iter(found)) if len(found) == 1 else None
  +return next(iter(found)) if (len(found) == 1) and False else None
  ```
- `_single_policy` · survived · ternary: condition forced · `3b7d294310ba80de`
  ```diff
  -return next(iter(found)) if len(found) == 1 else None
  +return next(iter(found)) if (len(found) == 1) or True else None
  ```
- `_single_policy` · survived · operator: comparison · `fa8dac4e4bafed89`
  ```diff
  -return next(iter(found)) if len(found) == 1 else None
  +return next(iter(found)) if len(found) != 1 else None
  ```
- `_single_policy` · survived · number · `31588a97bc7a3c89`
  ```diff
  -return next(iter(found)) if len(found) == 1 else None
  +return next(iter(found)) if len(found) == 2 else None
  ```
- `_policy_id` · survived · ternary: condition forced · `1d97134bd44b6127`
  ```diff
  -return None if policy is None else policy.policy_id
  +return None if (policy is None) and False else policy.policy_id
  ```
- `_policy_id` · survived · ternary: condition forced · `39b40259f7b19512`
  ```diff
  -return None if policy is None else policy.policy_id
  +return None if (policy is None) or True else policy.policy_id
  ```
- `_policy_id` · survived · is / in negated · `d7ed4102d8b05d72`
  ```diff
  -return None if policy is None else policy.policy_id
  +return None if policy is not None else policy.policy_id
  ```
- `_policy_bytes` · survived · is / in negated · `1d232f7ec0e88b81`
  ```diff
  -if policy is None:
  +if policy is not None:
  ```
- `_policy_bytes` · survived · assignment: value to None · `f268044e749bbc6f`
  ```diff
  -offered = offer.authorization
  +offered = None
  ```
- `_policy_bytes` · survived · operator: and / or · `e77d43cb03982b7e`
  ```diff
  -offered is not None
  -and policy_digest(parse_policy(offered.policy)) == policy.policy_sha256
  +offered is not None or policy_digest(parse_policy(offered.policy)) == policy.policy_sha256
  ```
- `_policy_bytes` · survived · is / in negated · `113998a7d705fd04`
  ```diff
  -offered is not None
  +offered is None
  ```
- `_policy_bytes` · survived · operator: comparison · `fd67021304de5cbc`
  ```diff
  -and policy_digest(parse_policy(offered.policy)) == policy.policy_sha256
  +and policy_digest(parse_policy(offered.policy)) != policy.policy_sha256
  ```
- `registry_of` · survived · argument: replaced by None · `d18bcc72fee9526e`
  ```diff
  -owner_keys=roles.count("owner_policy"),
  +owner_keys=roles.count(None),
  ```
- `registry_of` · survived · string: XX added · `2cb8025211c77bfe`
  ```diff
  -owner_keys=roles.count("owner_policy"),
  +owner_keys=roles.count("XXowner_policyXX"),
  ```
- `registry_of` · survived · string: upper case · `2698b8d18e84a52d`
  ```diff
  -owner_keys=roles.count("owner_policy"),
  +owner_keys=roles.count("OWNER_POLICY"),
  ```
- `registry_of` · survived · argument: replaced by None · `ddb7cacda976cd72`
  ```diff
  -bot_keys=roles.count("bot_execution"),
  +bot_keys=roles.count(None),
  ```
- `registry_of` · survived · string: XX added · `12117fb0f5839434`
  ```diff
  -bot_keys=roles.count("bot_execution"),
  +bot_keys=roles.count("XXbot_executionXX"),
  ```
- `registry_of` · survived · string: upper case · `f1dbc05fed89d067`
  ```diff
  -bot_keys=roles.count("bot_execution"),
  +bot_keys=roles.count("BOT_EXECUTION"),
  ```
- `publisher_of` · survived · argument: replaced by None · `6f57c38cadaf421b`
  ```diff
  -key_fingerprint=None if entry is None else entry.fingerprint,
  +key_fingerprint=None,
  ```
- `publisher_of` · survived · ternary: condition forced · `62f401eacf1fe72a`
  ```diff
  -key_fingerprint=None if entry is None else entry.fingerprint,
  +key_fingerprint=None if (entry is None) or True else entry.fingerprint,
  ```
- `publisher_of` · survived · ternary: condition forced · `669024f6d77bab49`
  ```diff
  -namespaces=() if entry is None else tuple(sorted(entry.namespaces)),
  +namespaces=() if (entry is None) or True else tuple(sorted(entry.namespaces)),
  ```
- `approvals_of` · survived · argument: replaced by None · `d008e5744f55a30a`
  ```diff
  -sorted(found, key=lambda item: (item.policy.policy_sha256, item.model_dump_json()))
  +sorted(found, key=None)
  ```
- `approvals_of` · survived · argument: removed · `316187edef3be264`
  ```diff
  -sorted(found, key=lambda item: (item.policy.policy_sha256, item.model_dump_json()))
  +sorted(found, )
  ```
- `approvals_of` · survived · lambda: body replaced · `25e7e82e36670539`
  ```diff
  -sorted(found, key=lambda item: (item.policy.policy_sha256, item.model_dump_json()))
  +sorted(found, key=lambda item: None)
  ```
- `_approval` · survived · is / in negated · `a62e7ed00171e1f1`
  ```diff
  -signatures = [check.signature for check in checks if check.signature is not None]
  +signatures = [check.signature for check in checks if check.signature is None]
  ```
- `_approval` · survived · argument: replaced by None · `cb3ebb1e89367bac`
  ```diff
  -max_documents_per_batch=_limit(request, verdict.automated_publication),
  +max_documents_per_batch=None,
  ```
- `_approval` · survived · argument: replaced by None · `7687726f77de14db`
  ```diff
  -legal_reference_stated=verdict.legal_assessment_reference_stated,
  +legal_reference_stated=None,
  ```
- `_approval` · survived · is / in negated · `83393fa44145c270`
  ```diff
  -approver_roles=tuple(sorted({s.role for s in signatures if s.role is not None})),
  +approver_roles=tuple(sorted({s.role for s in signatures if s.role is None})),
  ```
- `_limit` · survived · operator: and / or · `a0d0dd47b4135f1e`
  ```diff
  -if check.status != "verified" or request.automated_publication is None:
  +if check.status != "verified" and request.automated_publication is None:
  ```
- `_limit` · survived · operator: comparison · `9b7faae1d3878f49`
  ```diff
  -if check.status != "verified" or request.automated_publication is None:
  +if check.status == "verified" or request.automated_publication is None:
  ```
- `_limit` · survived · string: XX added · `af8a6851c4f3a8fe`
  ```diff
  -if check.status != "verified" or request.automated_publication is None:
  +if check.status != "XXverifiedXX" or request.automated_publication is None:
  ```
- `_limit` · survived · string: upper case · `9027baaa1824939f`
  ```diff
  -if check.status != "verified" or request.automated_publication is None:
  +if check.status != "VERIFIED" or request.automated_publication is None:
  ```
- `_limit` · survived · is / in negated · `f8e155a76c945ab4`
  ```diff
  -if check.status != "verified" or request.automated_publication is None:
  +if check.status != "verified" or request.automated_publication is not None:
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/span_assessment.py</code>: 59</summary>

- `parse_assessment` · survived · argument: replaced by None · `4ffddc96e524c47e`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "assessment") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None) from error
  ```
- `parse_assessment` · survived · string: XX added · `5079d5b91ebf4bda`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "assessment") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXassessmentXX") from error
  ```
- `parse_assessment` · survived · string: upper case · `b4840687e72dd776`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "assessment") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "ASSESSMENT") from error
  ```
- `parse_assessment` · survived · argument: replaced by None · `167e4e3cb9615ee8`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "assessment")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `parse_assessment` · survived · string: XX added · `f3a88476e0e888c4`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "assessment")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXassessmentXX")
  ```
- `parse_assessment` · survived · string: upper case · `846d0eb83873427c`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "assessment")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "ASSESSMENT")
  ```
- `verify_assessment` · survived · argument: replaced by None · `823031ed2f74b34a`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "assessment not reproduced")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `verify_assessment` · survived · string: XX added · `61d8045a3756626b`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "assessment not reproduced")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXassessment not reproducedXX")
  ```
- `verify_assessment` · survived · string: upper case · `4e559bcb5aa78583`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "assessment not reproduced")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "ASSESSMENT NOT REPRODUCED")
  ```
- `_contexts` · survived · assignment: None to empty string · `efae2e873172e5af`
  ```diff
  -previous: Span | Gap | None = None
  +previous: Span | Gap | None = ""
  ```
- `_contexts` · survived · operator: comparison · `b6c9c613c40fdae7`
  ```diff
  -headings = [item for item in headings if item[0] < entry.level]
  +headings = [item for item in headings if item[0] <= entry.level]
  ```
- `_contexts` · survived · operator: and / or · `24b0eacb9706681d`
  ```diff
  -if group is not None and group not in intros:
  +if group is not None or group not in intros:
  ```
- `_contexts` · survived · ternary: condition forced · `74dc954745dc37d0`
  ```diff
  -intro = intros.get(group) if group is not None else None
  +intro = intros.get(group) if (group is not None) or True else None
  ```
- `_intro` · survived · string method swapped · `80897f34a410c3af`
  ```diff
  -return previous if previous.text.rstrip().endswith(":") else None
  +return previous if previous.text.lstrip().endswith(":") else None
  ```
- `_span` · survived · argument: replaced by None · `0a8b7f9b564c480b`
  ```diff
  -text_sha256=span.text_sha256,
  +text_sha256=None,
  ```
- `_rights` · survived · string: XX added · `3e06b43b06503ab6`
  ```diff
  -return RightsClass.THIRD_PARTY_WORK, Evidence.TEXTUAL_MARKER, ("third_party_marker",), ()
  +return RightsClass.THIRD_PARTY_WORK, Evidence.TEXTUAL_MARKER, ("XXthird_party_markerXX",), ()
  ```
- `_rights` · survived · string: upper case · `a06e2a1ddcc63537`
  ```diff
  -return RightsClass.THIRD_PARTY_WORK, Evidence.TEXTUAL_MARKER, ("third_party_marker",), ()
  +return RightsClass.THIRD_PARTY_WORK, Evidence.TEXTUAL_MARKER, ("THIRD_PARTY_MARKER",), ()
  ```
- `_quotation` · survived · operator: and / or · `5631d1944588098e`
  ```diff
  -if speaker is not None and context.intro is not None:
  +if speaker is not None or context.intro is not None:
  ```
- `_quotation` · survived · string: XX added · `790b989d0325284c`
  ```diff
  -reasons = ("quotation", "consultation_section")
  +reasons = ("XXquotationXX", "consultation_section")
  ```
- `_quotation` · survived · string: upper case · `83de680f52ebdb1e`
  ```diff
  -reasons = ("quotation", "consultation_section")
  +reasons = ("QUOTATION", "consultation_section")
  ```
- `_quotation` · survived · string: XX added · `78cc8373312310b1`
  ```diff
  -reasons = ("quotation", "consultation_section")
  +reasons = ("quotation", "XXconsultation_sectionXX")
  ```
- `_quotation` · survived · string: upper case · `8ac93752b7e38b5f`
  ```diff
  -reasons = ("quotation", "consultation_section")
  +reasons = ("quotation", "CONSULTATION_SECTION")
  ```
- `_quotation` · survived · assignment: value to None · `20e3160829f8c77f`
  ```diff
  -reasons = ("quotation", "draft_law_chapter")
  +reasons = None
  ```
- `_quotation` · survived · string: XX added · `e2be195ff5547f5e`
  ```diff
  -reasons = ("quotation", "draft_law_chapter")
  +reasons = ("XXquotationXX", "draft_law_chapter")
  ```
- `_quotation` · survived · string: upper case · `75ba55f4e8ed9c25`
  ```diff
  -reasons = ("quotation", "draft_law_chapter")
  +reasons = ("QUOTATION", "draft_law_chapter")
  ```
- `_quotation` · survived · string: XX added · `4f79b120efaefd8c`
  ```diff
  -reasons = ("quotation", "draft_law_chapter")
  +reasons = ("quotation", "XXdraft_law_chapterXX")
  ```
- `_quotation` · survived · string: upper case · `3315b29ddd7a0313`
  ```diff
  -reasons = ("quotation", "draft_law_chapter")
  +reasons = ("quotation", "DRAFT_LAW_CHAPTER")
  ```
- `_attributed_quotation` · survived · string: XX added · `b54760aac4f4bf7e`
  ```diff
  -reasons = ("quotation", *speaker.reasons)
  +reasons = ("XXquotationXX", *speaker.reasons)
  ```
- `_attributed_quotation` · survived · string: upper case · `77e4b57b724142e2`
  ```diff
  -reasons = ("quotation", *speaker.reasons)
  +reasons = ("QUOTATION", *speaker.reasons)
  ```
- `_speaker` · survived · string: XX added · `a75f89460bd3b59f`
  ```diff
  -return _Speaker("conflict", ("named_party_and_legal_source",))
  +return _Speaker("conflict", ("XXnamed_party_and_legal_sourceXX",))
  ```
- `_speaker` · survived · string: upper case · `bb95f47a96fcc833`
  ```diff
  -return _Speaker("conflict", ("named_party_and_legal_source",))
  +return _Speaker("conflict", ("NAMED_PARTY_AND_LEGAL_SOURCE",))
  ```
- `_speaker` · survived · argument: replaced by None · `b92c415a0ae02673`
  ```diff
  -return _Speaker("own", ())
  +return _Speaker("own", None)
  ```
- `_speaker` · survived · ternary: condition forced · `a7e7770b7dc6a25b`
  ```diff
  -return _Speaker("legal", ()) if _LEGAL.search(intro) else None
  +return _Speaker("legal", ()) if (_LEGAL.search(intro)) or True else None
  ```
- `_speaker` · survived · argument: replaced by None · `f5ecaf69133701d4`
  ```diff
  -return _Speaker("legal", ()) if _LEGAL.search(intro) else None
  +return _Speaker("legal", None) if _LEGAL.search(intro) else None
  ```
- `_named` · survived · break / continue swapped · `16e9cc194096349e`
  ```diff
  -continue
  +break
  ```
- `_named` · survived · argument: replaced by None · `1d26335d62022194`
  ```diff
  -return _Speaker("own", ())
  +return _Speaker(None, ())
  ```
- `_named` · survived · argument: replaced by None · `04bdf4fc732bba9c`
  ```diff
  -return _Speaker("own", ())
  +return _Speaker("own", None)
  ```
- `_named` · survived · string: XX added · `23052158b2bd9bd7`
  ```diff
  -return _Speaker("own", ())
  +return _Speaker("XXownXX", ())
  ```
- `_named` · survived · string: upper case · `2af42151a5f94fa6`
  ```diff
  -return _Speaker("own", ())
  +return _Speaker("OWN", ())
  ```
- `_named` · survived · argument: replaced by None · `1927de9be84d6e68`
  ```diff
  -return _Speaker("public" if _PUBLIC.search(subject) else "party", ())
  +return _Speaker("public" if _PUBLIC.search(subject) else "party", None)
  ```
- `_prose` · survived · string: XX added · `7135bea8459fa2ad`
  ```diff
  -return RightsClass.STATUTE_TEXT, Evidence.CHAPTER_CONTEXT, ("draft_law_chapter",), ()
  +return RightsClass.STATUTE_TEXT, Evidence.CHAPTER_CONTEXT, ("XXdraft_law_chapterXX",), ()
  ```
- `_prose` · survived · string: upper case · `c8a716e42c05fce7`
  ```diff
  -return RightsClass.STATUTE_TEXT, Evidence.CHAPTER_CONTEXT, ("draft_law_chapter",), ()
  +return RightsClass.STATUTE_TEXT, Evidence.CHAPTER_CONTEXT, ("DRAFT_LAW_CHAPTER",), ()
  ```
- `_prose` · survived · string: XX added · `69e1abdd6b9181fe`
  ```diff
  -return RightsClass.OFFICIAL_TEXT, Evidence.STRUCTURE, ("heading",), ()
  +return RightsClass.OFFICIAL_TEXT, Evidence.STRUCTURE, ("XXheadingXX",), ()
  ```
- `_prose` · survived · string: upper case · `9d00e18d7aa12f85`
  ```diff
  -return RightsClass.OFFICIAL_TEXT, Evidence.STRUCTURE, ("heading",), ()
  +return RightsClass.OFFICIAL_TEXT, Evidence.STRUCTURE, ("HEADING",), ()
  ```
- `_prose` · survived · ternary: condition forced · `746d4601c790e15e`
  ```diff
  -speaker = _named(_OPENING, span.text) if span.kind == "paragraph" else None
  +speaker = _named(_OPENING, span.text) if (span.kind == "paragraph") or True else None
  ```
- `_prose` · survived · string: XX added · `20eb2b1859cd03a2`
  ```diff
  -if speaker is not None and speaker.kind in {"public", "party"}:
  +if speaker is not None and speaker.kind in {"XXpublicXX", "party"}:
  ```
- `_prose` · survived · string: upper case · `d5bb89f32563f545`
  ```diff
  -if speaker is not None and speaker.kind in {"public", "party"}:
  +if speaker is not None and speaker.kind in {"PUBLIC", "party"}:
  ```
- `_prose` · survived · ternary: condition forced · `56afcffc5d08a70f`
  ```diff
  -reason = "speaker_public_body" if speaker.kind == "public" else "speaker_named_party"
  +reason = "speaker_public_body" if (speaker.kind == "public") and False else "speaker_named_party"
  ```
- `_prose` · survived · string: XX added · `b50739cc5e12c371`
  ```diff
  -reason = "speaker_public_body" if speaker.kind == "public" else "speaker_named_party"
  +reason = "XXspeaker_public_bodyXX" if speaker.kind == "public" else "speaker_named_party"
  ```
- `_prose` · survived · string: upper case · `a5a344a21edc75ce`
  ```diff
  -reason = "speaker_public_body" if speaker.kind == "public" else "speaker_named_party"
  +reason = "SPEAKER_PUBLIC_BODY" if speaker.kind == "public" else "speaker_named_party"
  ```
- `_prose` · survived · string: XX added · `38d2ea3f0fc4347d`
  ```diff
  -reason = "speaker_public_body" if speaker.kind == "public" else "speaker_named_party"
  +reason = "speaker_public_body" if speaker.kind == "XXpublicXX" else "speaker_named_party"
  ```
- `_prose` · survived · string: upper case · `652f8c1f5cdf951d`
  ```diff
  -reason = "speaker_public_body" if speaker.kind == "public" else "speaker_named_party"
  +reason = "speaker_public_body" if speaker.kind == "PUBLIC" else "speaker_named_party"
  ```
- `_prose` · survived · ternary: condition forced · `04e21b989fe38eb7`
  ```diff
  -context_reason = ("consultation_section",) if context.consultation else ()
  +context_reason = ("consultation_section",) if (context.consultation) or True else ()
  ```
- `_prose` · survived · string: XX added · `7110a49c6d4fbec7`
  ```diff
  -return RightsClass.OFFICIAL_TEXT, Evidence.DEFAULT_RULE, ("government_prose",), ()
  +return RightsClass.OFFICIAL_TEXT, Evidence.DEFAULT_RULE, ("XXgovernment_proseXX",), ()
  ```
- `_prose` · survived · string: upper case · `077886a278e3b163`
  ```diff
  -return RightsClass.OFFICIAL_TEXT, Evidence.DEFAULT_RULE, ("government_prose",), ()
  +return RightsClass.OFFICIAL_TEXT, Evidence.DEFAULT_RULE, ("GOVERNMENT_PROSE",), ()
  ```
- `_gap` · survived · argument: replaced by None · `9e0b640591c98fdd`
  ```diff
  -source_sha256=part.source_sha256 if part else None,
  +source_sha256=None,
  ```
- `_gap` · survived · argument: replaced by None · `ea558b75680a6a0d`
  ```diff
  -text_sha256=None if gap.text is None else text_digest(gap.text),
  +text_sha256=None,
  ```
- `_gap` · survived · ternary: condition forced · `2d97dd6409349b85`
  ```diff
  -source_sha256=part.source_sha256 if part else None,
  +source_sha256=part.source_sha256 if (part) and False else None,
  ```
- `_gap` · survived · ternary: condition forced · `e6e12c72e8bf7855`
  ```diff
  -text_sha256=None if gap.text is None else text_digest(gap.text),
  +text_sha256=None if (gap.text is None) or True else text_digest(gap.text),
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/span_privacy.py</code>: 12</summary>

- `screen` · survived · string: XX added · `759e95aed12327e7`
  ```diff
  -reasons = _hits(text or "")
  +reasons = _hits(text or "XXXX")
  ```
- `screen` · survived · string: XX added · `903f95a23dcbbe44`
  ```diff
  -return PrivacyState.UNRESOLVED, ("not_screened_as_a_span",)
  +return PrivacyState.UNRESOLVED, ("XXnot_screened_as_a_spanXX",)
  ```
- `screen` · survived · string: upper case · `928196812318c3d8`
  ```diff
  -return PrivacyState.UNRESOLVED, ("not_screened_as_a_span",)
  +return PrivacyState.UNRESOLVED, ("NOT_SCREENED_AS_A_SPAN",)
  ```
- `screen_gap` · survived · string: XX added · `ca65547a13e18a74`
  ```diff
  -reasons = _hits(text or "")
  +reasons = _hits(text or "XXXX")
  ```
- `_hits` · survived · string: XX added · `fc2ba16849f43c0e`
  ```diff
  -if _SPECIAL.search(_EXEMPT.sub("", text)):
  +if _SPECIAL.search(_EXEMPT.sub("XXXX", text)):
  ```
- `_checks` · survived · True / False swapped · `1c556aff538c2d23`
  ```diff
  -return False
  +return True
  ```
- `_checks` · survived · argument: replaced by None · `376d0b50c1440a22`
  ```diff
  -for weights, position in zip(_WEIGHTS, (9, 10), strict=True):
  +for weights, position in zip(_WEIGHTS, (9, 10), strict=None):
  ```
- `_checks` · survived · argument: removed · `651d08d49f04bee7`
  ```diff
  -for weights, position in zip(_WEIGHTS, (9, 10), strict=True):
  +for weights, position in zip(_WEIGHTS, (9, 10), ):
  ```
- `_checks` · survived · True / False swapped · `7222fd6f9d5f88c6`
  ```diff
  -for weights, position in zip(_WEIGHTS, (9, 10), strict=True):
  +for weights, position in zip(_WEIGHTS, (9, 10), strict=False):
  ```
- `_checks` · survived · argument: replaced by None · `9aaa0d0d7a2865b0`
  ```diff
  -remainder = sum(w * d for w, d in zip(weights, digits, strict=False)) % 11
  +remainder = sum(w * d for w, d in zip(weights, digits, strict=None)) % 11
  ```
- `_checks` · survived · argument: removed · `6174fa9d73cf9ac3`
  ```diff
  -remainder = sum(w * d for w, d in zip(weights, digits, strict=False)) % 11
  +remainder = sum(w * d for w, d in zip(weights, digits, )) % 11
  ```
- `_checks` · survived · number · `c010cf7790e81f19`
  ```diff
  -if check == 10 or check != digits[position]:  # noqa: PLR2004 - mod 11 has no digit 10
  +if check == 11 or check != digits[position]:  # noqa: PLR2004 - mod 11 has no digit 10
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/version_completeness.py</code>: 31</summary>

- `publication_completeness` · survived · number · `7155a6bb255e0e3e`
  ```diff
  -spans_total=sum(1 for _ in _spans(content)),
  +spans_total=sum(2 for _ in _spans(content)),
  ```
- `_chapter_reasons` · survived · argument: replaced by None · `eb4db53f3b8d6975`
  ```diff
  -yield Reason(code="chapter_inventory_missing", effect=Effect.UNKNOWN)
  +yield Reason(code=None, effect=Effect.UNKNOWN)
  ```
- `_chapter_reasons` · survived · argument: replaced by None · `ba98944927461054`
  ```diff
  -yield Reason(code="chapter_inventory_missing", effect=Effect.UNKNOWN)
  +yield Reason(code="chapter_inventory_missing", effect=None)
  ```
- `_chapter_reasons` · survived · argument: removed · `2c38c6791b899d97`
  ```diff
  -yield Reason(code="chapter_inventory_missing", effect=Effect.UNKNOWN)
  +yield Reason(effect=Effect.UNKNOWN)
  ```
- `_chapter_reasons` · survived · argument: removed · `ce0f4dddc325826a`
  ```diff
  -yield Reason(code="chapter_inventory_missing", effect=Effect.UNKNOWN)
  +yield Reason(code="chapter_inventory_missing", )
  ```
- `_chapter_reasons` · survived · string: XX added · `f9e26e7cf9624fb6`
  ```diff
  -yield Reason(code="chapter_inventory_missing", effect=Effect.UNKNOWN)
  +yield Reason(code="XXchapter_inventory_missingXX", effect=Effect.UNKNOWN)
  ```
- `_chapter_reasons` · survived · string: upper case · `0a834becccfbe9d2`
  ```diff
  -yield Reason(code="chapter_inventory_missing", effect=Effect.UNKNOWN)
  +yield Reason(code="CHAPTER_INVENTORY_MISSING", effect=Effect.UNKNOWN)
  ```
- `_chapter_reasons` · survived · argument: replaced by None · `38c7043b4d1259c4`
  ```diff
  -yield Reason(code="chapter_unlisted", effect=Effect.INCOMPLETE, part_id=present[number])
  +yield Reason(code=None, effect=Effect.INCOMPLETE, part_id=present[number])
  ```
- `_chapter_reasons` · survived · argument: replaced by None · `5f8313c8fafa134c`
  ```diff
  -yield Reason(code="chapter_unlisted", effect=Effect.INCOMPLETE, part_id=present[number])
  +yield Reason(code="chapter_unlisted", effect=None, part_id=present[number])
  ```
- `_chapter_reasons` · survived · argument: replaced by None · `26216b989c97364e`
  ```diff
  -yield Reason(code="chapter_unlisted", effect=Effect.INCOMPLETE, part_id=present[number])
  +yield Reason(code="chapter_unlisted", effect=Effect.INCOMPLETE, part_id=None)
  ```
- `_chapter_reasons` · survived · argument: removed · `8c084ed20871fdd0`
  ```diff
  -yield Reason(code="chapter_unlisted", effect=Effect.INCOMPLETE, part_id=present[number])
  +yield Reason(effect=Effect.INCOMPLETE, part_id=present[number])
  ```
- `_chapter_reasons` · survived · argument: removed · `ebdcfbb7161f0e67`
  ```diff
  -yield Reason(code="chapter_unlisted", effect=Effect.INCOMPLETE, part_id=present[number])
  +yield Reason(code="chapter_unlisted", part_id=present[number])
  ```
- `_chapter_reasons` · survived · argument: removed · `aae5e9378fc3df4f`
  ```diff
  -yield Reason(code="chapter_unlisted", effect=Effect.INCOMPLETE, part_id=present[number])
  +yield Reason(code="chapter_unlisted", effect=Effect.INCOMPLETE, )
  ```
- `_chapter_reasons` · survived · string: XX added · `3e2203977153cc27`
  ```diff
  -yield Reason(code="chapter_unlisted", effect=Effect.INCOMPLETE, part_id=present[number])
  +yield Reason(code="XXchapter_unlistedXX", effect=Effect.INCOMPLETE, part_id=present[number])
  ```
- `_chapter_reasons` · survived · string: upper case · `3d4cb7e08921b5a4`
  ```diff
  -yield Reason(code="chapter_unlisted", effect=Effect.INCOMPLETE, part_id=present[number])
  +yield Reason(code="CHAPTER_UNLISTED", effect=Effect.INCOMPLETE, part_id=present[number])
  ```
- `_gap_reasons` · survived · argument: replaced by None · `ba0b8ec2ca17800f`
  ```diff
  -detail=gap.locator,
  +detail=None,
  ```
- `_gap_reasons` · survived · argument: removed · `e4ca1077edd82aaf`
  ```diff
  -detail=gap.locator,
  -)
  +)
  ```
- `_attachment_reasons` · survived · assignment: value to None · `e2a56426e3b40c7b`
  ```diff
  -detail = " ".join(filter(None, (gap.locator, gap.detail))) or None
  +detail = None
  ```
- `_attachment_reasons` · survived · operator: and / or · `0564f76d28a14a46`
  ```diff
  -detail = " ".join(filter(None, (gap.locator, gap.detail))) or None
  +detail = " ".join(filter(None, (gap.locator, gap.detail))) and None
  ```
- `_attachment_reasons` · survived · string: XX added · `a2394739477198ea`
  ```diff
  -detail = " ".join(filter(None, (gap.locator, gap.detail))) or None
  +detail = "XX XX".join(filter(None, (gap.locator, gap.detail))) or None
  ```
- `_attachment_reasons` · survived · argument: replaced by None · `2c2f2539d808f231`
  ```diff
  -yield Reason(code=f"discovery_{gap.code}", effect=Effect.UNKNOWN, detail=detail)
  +yield Reason(code=f"discovery_{gap.code}", effect=Effect.UNKNOWN, detail=None)
  ```
- `_attachment_reasons` · survived · argument: removed · `dc3be6f249212e64`
  ```diff
  -yield Reason(code=f"discovery_{gap.code}", effect=Effect.UNKNOWN, detail=detail)
  +yield Reason(code=f"discovery_{gap.code}", effect=Effect.UNKNOWN, )
  ```
- `_attachment_reasons` · survived · argument: replaced by None · `b9f6e0aa34026ee8`
  ```diff
  -yield Reason(code=code, effect=effect, detail=item.url)
  +yield Reason(code=code, effect=effect, detail=None)
  ```
- `_attachment_reasons` · survived · argument: removed · `ab9634ec9581485c`
  ```diff
  -yield Reason(code=code, effect=effect, detail=item.url)
  +yield Reason(code=code, effect=effect, )
  ```
- `_entry_reasons` · survived · ternary: condition forced · `39d832cfa20825de`
  ```diff
  -effect = Effect.INCOMPLETE if codes else Effect.UNKNOWN
  +effect = Effect.INCOMPLETE if (codes) or True else Effect.UNKNOWN
  ```
- `_attachment_publication` · survived · argument: replaced by None · `a66af79b6632d403`
  ```diff
  -detail=item.url,
  +detail=None,
  ```
- `_attachment_publication` · survived · argument: removed · `9cc4958d69b13cbb`
  ```diff
  -detail=item.url,
  -)
  +)
  ```
- `_attachment_publication` · survived · ternary: condition forced · `78664bc1dbd8e487`
  ```diff
  -code="attachment_clearance_absent" if captured else "attachment_unpublishable",
  +code="attachment_clearance_absent" if (captured) or True else "attachment_unpublishable",
  ```
- `_attachment_publication` · survived · string: XX added · `66af952ccb740523`
  ```diff
  -code="attachment_clearance_absent" if captured else "attachment_unpublishable",
  +code="attachment_clearance_absent" if captured else "XXattachment_unpublishableXX",
  ```
- `_attachment_publication` · survived · string: upper case · `477e9fea1b618921`
  ```diff
  -code="attachment_clearance_absent" if captured else "attachment_unpublishable",
  +code="attachment_clearance_absent" if captured else "ATTACHMENT_UNPUBLISHABLE",
  ```
- `_attachment_publication` · survived · ternary: condition forced · `d5872387d0aab5a5`
  ```diff
  -effect=Effect.UNKNOWN if captured else Effect.INCOMPLETE,
  +effect=Effect.UNKNOWN if (captured) or True else Effect.INCOMPLETE,
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/version_diff.py</code>: 42</summary>

- `load_snapshot` · survived · argument: replaced by None · `6541e288e37dbfaf`
  ```diff
  -findings = entry_findings(sha(raw), entry, artifacts)
  +findings = entry_findings(None, entry, artifacts)
  ```
- `load_snapshot` · survived · argument: replaced by None · `4dca70725ed6d55d`
  ```diff
  -raise _conflict(",".join(sorted({finding.code for finding in findings})))
  +raise _conflict(None)
  ```
- `load_snapshot` · survived · string: XX added · `e056caeacf3f34fa`
  ```diff
  -raise _conflict(",".join(sorted({finding.code for finding in findings})))
  +raise _conflict("XX,XX".join(sorted({finding.code for finding in findings})))
  ```
- `diff_snapshots` · survived · argument: replaced by None · `466c630c2fc4ac69`
  ```diff
  -raise _conflict("two documents")
  +raise _conflict(None)
  ```
- `diff_snapshots` · survived · string: XX added · `1437cf06a175397b`
  ```diff
  -raise _conflict("two documents")
  +raise _conflict("XXtwo documentsXX")
  ```
- `diff_snapshots` · survived · string: upper case · `2bcc57193adcfe0f`
  ```diff
  -raise _conflict("two documents")
  +raise _conflict("TWO DOCUMENTS")
  ```
- `diff_snapshots` · survived · argument: replaced by None · `1ff36dad7cc83b79`
  ```diff
  -changed = any(body.values()) or bool(blockers.added or blockers.removed)
  +changed = any(body.values()) or bool(None)
  ```
- `diff_snapshots` · survived · operator: and / or · `9e95275d334f3759`
  ```diff
  -changed = any(body.values()) or bool(blockers.added or blockers.removed)
  +changed = any(body.values()) or bool(blockers.added and blockers.removed)
  ```
- `diff_snapshots` · survived · argument: replaced by None · `c200fa6fd0abcf50`
  ```diff
  -causes = _causes(older.entry, newer.entry, changed=changed)
  +causes = _causes(older.entry, newer.entry, changed=None)
  ```
- `_conflict` · survived · argument: replaced by None · `1bec6df5c54c3f0b`
  ```diff
  -return ForarbeiderRefusedError(RefusalReason.LINEAGE_CONFLICT, detail)
  +return ForarbeiderRefusedError(RefusalReason.LINEAGE_CONFLICT, None)
  ```
- `_causes` · survived · argument: replaced by None · `1fdd387a4049076c`
  ```diff
  -causes.append("authorization_changed")
  +causes.append(None)
  ```
- `_causes` · survived · string: XX added · `ee1503a7c87c1295`
  ```diff
  -causes.append("authorization_changed")
  +causes.append("XXauthorization_changedXX")
  ```
- `_causes` · survived · string: upper case · `cd10dc477a4cc6e4`
  ```diff
  -causes.append("authorization_changed")
  +causes.append("AUTHORIZATION_CHANGED")
  ```
- `_causes` · survived · ternary: condition forced · `bedd810c49295b64`
  ```diff
  -return ("unexplained_difference",) if changed else ("no_change",)
  +return ("unexplained_difference",) if (changed) and False else ("no_change",)
  ```
- `_causes` · survived · string: XX added · `6c05aed9294d8826`
  ```diff
  -return ("unexplained_difference",) if changed else ("no_change",)
  +return ("XXunexplained_differenceXX",) if changed else ("no_change",)
  ```
- `_causes` · survived · string: upper case · `284bf27b09215723`
  ```diff
  -return ("unexplained_difference",) if changed else ("no_change",)
  +return ("UNEXPLAINED_DIFFERENCE",) if changed else ("no_change",)
  ```
- `_digest` · survived · ternary: condition forced · `73f924c4fba834cf`
  ```diff
  -data = value.model_dump(mode="json", exclude=exclude) if isinstance(value, BaseModel) else value
  +data = value.model_dump(mode="json", exclude=exclude) if (isinstance(value, BaseModel)) or True else value
  ```
- `_digest` · survived · argument: replaced by None · `a8a946b28a059c41`
  ```diff
  -data = value.model_dump(mode="json", exclude=exclude) if isinstance(value, BaseModel) else value
  +data = value.model_dump(mode=None, exclude=exclude) if isinstance(value, BaseModel) else value
  ```
- `_digest` · survived · argument: replaced by None · `15db49f5826f78cf`
  ```diff
  -data = value.model_dump(mode="json", exclude=exclude) if isinstance(value, BaseModel) else value
  +data = value.model_dump(mode="json", exclude=None) if isinstance(value, BaseModel) else value
  ```
- `_digest` · survived · argument: removed · `be9dc65098395d1f`
  ```diff
  -data = value.model_dump(mode="json", exclude=exclude) if isinstance(value, BaseModel) else value
  +data = value.model_dump(exclude=exclude) if isinstance(value, BaseModel) else value
  ```
- `_digest` · survived · argument: removed · `2aa5c2e7347524ed`
  ```diff
  -data = value.model_dump(mode="json", exclude=exclude) if isinstance(value, BaseModel) else value
  +data = value.model_dump(mode="json", ) if isinstance(value, BaseModel) else value
  ```
- `_digest` · survived · string: XX added · `50046751bb70c80c`
  ```diff
  -data = value.model_dump(mode="json", exclude=exclude) if isinstance(value, BaseModel) else value
  +data = value.model_dump(mode="XXjsonXX", exclude=exclude) if isinstance(value, BaseModel) else value
  ```
- `_digest` · survived · string: upper case · `df28a119c8f7b460`
  ```diff
  -data = value.model_dump(mode="json", exclude=exclude) if isinstance(value, BaseModel) else value
  +data = value.model_dump(mode="JSON", exclude=exclude) if isinstance(value, BaseModel) else value
  ```
- `_digest` · survived · argument: removed · `3630ff8df83d7210`
  ```diff
  -return sha((json.dumps(data, **JSON_LAYOUT) + "\n").encode())
  +return sha((json.dumps(data, ) + "\n").encode())
  ```
- `_digest` · survived · string: XX added · `f1fa23c2e56d31f6`
  ```diff
  -return sha((json.dumps(data, **JSON_LAYOUT) + "\n").encode())
  +return sha((json.dumps(data, **JSON_LAYOUT) + "XX\nXX").encode())
  ```
- `_chapters` · survived · argument: replaced by None · `78a4feee37341fdd`
  ```diff
  -return {f"chapter-{c.chapter_number}": _digest(c) for c in manifest.content.chapters_listed}
  +return {f"chapter-{c.chapter_number}": _digest(None) for c in manifest.content.chapters_listed}
  ```
- `_parts` · survived · argument: replaced by None · `955d1b8bdb82c253`
  ```diff
  -return {part.id: _digest(part, {"entries"}) for part in manifest.content.parts}
  +return {part.id: _digest(part, None) for part in manifest.content.parts}
  ```
- `_parts` · survived · argument: removed · `a21e201e0b1320bc`
  ```diff
  -return {part.id: _digest(part, {"entries"}) for part in manifest.content.parts}
  +return {part.id: _digest(part, ) for part in manifest.content.parts}
  ```
- `_parts` · survived · string: XX added · `d30f1faefb4e2c1f`
  ```diff
  -return {part.id: _digest(part, {"entries"}) for part in manifest.content.parts}
  +return {part.id: _digest(part, {"XXentriesXX"}) for part in manifest.content.parts}
  ```
- `_parts` · survived · string: upper case · `ce5445ec7ef75ec1`
  ```diff
  -return {part.id: _digest(part, {"entries"}) for part in manifest.content.parts}
  +return {part.id: _digest(part, {"ENTRIES"}) for part in manifest.content.parts}
  ```
- `_entries` · survived · argument: replaced by None · `a22b062813f0cc06`
  ```diff
  -return {entry.id: _digest(entry, {"rights", "privacy"}) for entry in every}
  +return {entry.id: _digest(entry, None) for entry in every}
  ```
- `_entries` · survived · argument: removed · `4ad29be2cb29a496`
  ```diff
  -return {entry.id: _digest(entry, {"rights", "privacy"}) for entry in every}
  +return {entry.id: _digest(entry, ) for entry in every}
  ```
- `_entries` · survived · string: XX added · `d79e3b4a5828c1a3`
  ```diff
  -return {entry.id: _digest(entry, {"rights", "privacy"}) for entry in every}
  +return {entry.id: _digest(entry, {"XXrightsXX", "privacy"}) for entry in every}
  ```
- `_entries` · survived · string: upper case · `2b665b3c4de6c0dd`
  ```diff
  -return {entry.id: _digest(entry, {"rights", "privacy"}) for entry in every}
  +return {entry.id: _digest(entry, {"RIGHTS", "privacy"}) for entry in every}
  ```
- `_entries` · survived · string: XX added · `cc891ac1739fa37e`
  ```diff
  -return {entry.id: _digest(entry, {"rights", "privacy"}) for entry in every}
  +return {entry.id: _digest(entry, {"rights", "XXprivacyXX"}) for entry in every}
  ```
- `_entries` · survived · string: upper case · `7d7f469089c86bd8`
  ```diff
  -return {entry.id: _digest(entry, {"rights", "privacy"}) for entry in every}
  +return {entry.id: _digest(entry, {"rights", "PRIVACY"}) for entry in every}
  ```
- `_assessments` · survived · break / continue swapped · `2b6e20264ce54b84`
  ```diff
  -continue
  +break
  ```
- `_decisions` · survived · operator: and / or · `51b55a3b0ed906ed`
  ```diff
  -if older is None or newer is None:
  +if older is None and newer is None:
  ```
- `_decisions` · survived · argument: replaced by None · `75eb05d4f07cea57`
  ```diff
  -"attachment",
  +None,
  ```
- `_decisions` · survived · string: XX added · `b2ac48d27fe6e17c`
  ```diff
  -"attachment",
  +"XXattachmentXX",
  ```
- `_decisions` · survived · string: upper case · `21ed2aab5e5f4b25`
  ```diff
  -"attachment",
  +"ATTACHMENT",
  ```
- `_decided` · survived · operator: bitwise · `42e5ce9f027bc4c2`
  ```diff
  -for key in sorted(older.keys() & newer.keys())
  +for key in sorted(older.keys() | newer.keys())
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/version_lineage.py</code>: 144</summary>

- `parse_entry` · survived · argument: replaced by None · `f84a676e32bc3e83`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "lineage entry") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None) from error
  ```
- `parse_entry` · survived · string: XX added · `67149cfdbe35d332`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "lineage entry") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXlineage entryXX") from error
  ```
- `parse_entry` · survived · string: upper case · `fe1d6e22362436ab`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "lineage entry") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "LINEAGE ENTRY") from error
  ```
- `parse_entry` · survived · argument: replaced by None · `407f20d26eaf3007`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "lineage entry")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `parse_entry` · survived · string: XX added · `8a76c540d16a95e5`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "lineage entry")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXlineage entryXX")
  ```
- `parse_entry` · survived · string: upper case · `c72c141a0a1763b5`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "lineage entry")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "LINEAGE ENTRY")
  ```
- `record_observation` · survived · argument: replaced by None · `979030ad3afab568`
  ```diff
  -return Recorded(head.entry, _raw_of(history, head.key), store, appended=False)
  +return Recorded(None, _raw_of(history, head.key), store, appended=False)
  ```
- `_intact` · survived · argument: replaced by None · `7d39aa7a5d9e297b`
  ```diff
  -raise _conflict("history: " + ",".join(problems))
  +raise _conflict(None)
  ```
- `_intact` · survived · string: XX added · `407b71c9c14a909e`
  ```diff
  -raise _conflict("history: " + ",".join(problems))
  +raise _conflict("XXhistory: XX" + ",".join(problems))
  ```
- `_intact` · survived · string: upper case · `aaf04d4ea6eebd0b`
  ```diff
  -raise _conflict("history: " + ",".join(problems))
  +raise _conflict("HISTORY: " + ",".join(problems))
  ```
- `_intact` · survived · string: XX added · `fad07e3e46eb54a7`
  ```diff
  -raise _conflict("history: " + ",".join(problems))
  +raise _conflict("history: " + "XX,XX".join(problems))
  ```
- `_position` · survived · argument: replaced by None · `105e709498b0ad7d`
  ```diff
  -raise _conflict("another document")
  +raise _conflict(None)
  ```
- `_position` · survived · string: XX added · `570b90848c3031b9`
  ```diff
  -raise _conflict("another document")
  +raise _conflict("XXanother documentXX")
  ```
- `_position` · survived · string: upper case · `655da98cdb409cc5`
  ```diff
  -raise _conflict("another document")
  +raise _conflict("ANOTHER DOCUMENT")
  ```
- `_position` · survived · argument: replaced by None · `9274836d15a61ad4`
  ```diff
  -raise _conflict("observed before the head")
  +raise _conflict(None)
  ```
- `_position` · survived · string: XX added · `b86b071fdb832ed9`
  ```diff
  -raise _conflict("observed before the head")
  +raise _conflict("XXobserved before the headXX")
  ```
- `_position` · survived · string: upper case · `d5dceb0d0fdd3953`
  ```diff
  -raise _conflict("observed before the head")
  +raise _conflict("OBSERVED BEFORE THE HEAD")
  ```
- `_position` · survived · argument: replaced by None · `387bc09170c0c5f5`
  ```diff
  -raise _conflict("a version returns after another")
  +raise _conflict(None)
  ```
- `_position` · survived · string: XX added · `14856cbd40adc2dc`
  ```diff
  -raise _conflict("a version returns after another")
  +raise _conflict("XXa version returns after anotherXX")
  ```
- `_position` · survived · string: upper case · `d36433335c000fca`
  ```diff
  -raise _conflict("a version returns after another")
  +raise _conflict("A VERSION RETURNS AFTER ANOTHER")
  ```
- `_evaluation` · survived · argument: replaced by None · `3e09d16bc91a2507`
  ```diff
  -raise _conflict("an authorization without a policy")
  +raise _conflict(None)
  ```
- `_evaluation` · survived · string: XX added · `4754615caea2ac88`
  ```diff
  -raise _conflict("an authorization without a policy")
  +raise _conflict("XXan authorization without a policyXX")
  ```
- `_evaluation` · survived · string: upper case · `8853f2eb444ea660`
  ```diff
  -raise _conflict("an authorization without a policy")
  +raise _conflict("AN AUTHORIZATION WITHOUT A POLICY")
  ```
- `_verdict` · survived · argument: replaced by None · `110174129d10fec3`
  ```diff
  -raise _conflict("an authorization needs both the request and a trust root")
  +raise _conflict(None)
  ```
- `_verdict` · survived · string: XX added · `30b2ef0e2a479b30`
  ```diff
  -raise _conflict("an authorization needs both the request and a trust root")
  +raise _conflict("XXan authorization needs both the request and a trust rootXX")
  ```
- `_verdict` · survived · string: upper case · `261802de86592725`
  ```diff
  -raise _conflict("an authorization needs both the request and a trust root")
  +raise _conflict("AN AUTHORIZATION NEEDS BOTH THE REQUEST AND A TRUST ROOT")
  ```
- `_verdict` · survived · argument: replaced by None · `84d601d1ef920ffd`
  ```diff
  -raise _conflict("the authorization names another policy")
  +raise _conflict(None)
  ```
- `_verdict` · survived · string: XX added · `4112610e0a54784d`
  ```diff
  -raise _conflict("the authorization names another policy")
  +raise _conflict("XXthe authorization names another policyXX")
  ```
- `_verdict` · survived · string: upper case · `3b1fb46430d2734e`
  ```diff
  -raise _conflict("the authorization names another policy")
  +raise _conflict("THE AUTHORIZATION NAMES ANOTHER POLICY")
  ```
- `_pages` · survived · ternary: condition forced · `99e918a181eceb2d`
  ```diff
  -landing = [] if source.landing is None else [source.landing]
  +landing = [] if (source.landing is None) and False else [source.landing]
  ```
- `_observed_at` · survived · augmented assignment to plain · `2b8638dfebe7ec04`
  ```diff
  -times += [item.retrieved_at for item in provenance.supporting]
  +times = [item.retrieved_at for item in provenance.supporting]
  ```
- `_observed_at` · survived · argument: replaced by None · `f2999643c84f15ec`
  ```diff
  -return max(times).astimezone(UTC)
  +return max(times).astimezone(None)
  ```
- `_conflict` · survived · argument: replaced by None · `68733b03ce75d3f5`
  ```diff
  -return ForarbeiderRefusedError(RefusalReason.LINEAGE_CONFLICT, detail)
  +return ForarbeiderRefusedError(RefusalReason.LINEAGE_CONFLICT, None)
  ```
- `verify_lineage` · survived · argument: replaced by None · `47224eeae6d2ce05`
  ```diff
  -found.extend(entry_findings(key, entry, artifacts))
  +found.extend(entry_findings(None, entry, artifacts))
  ```
- `verify_lineage` · survived · argument: replaced by None · `3b2fcfb134cbcef1`
  ```diff
  -document_id=None if head is None else head.entry.document_id,
  +document_id=None,
  ```
- `verify_lineage` · survived · argument: replaced by None · `08031831814bd129`
  ```diff
  -expected_head_sha256=expected_head,
  +expected_head_sha256=None,
  ```
- `verify_lineage` · survived · ternary: condition forced · `71608457f8a6e7c5`
  ```diff
  -document_id=None if head is None else head.entry.document_id,
  +document_id=None if (head is None) or True else head.entry.document_id,
  ```
- `_finding_order` · survived · assignment: value to None · `a99fd56ad142896c`
  ```diff
  -sequence = -1 if finding.sequence is None else finding.sequence
  +sequence = None
  ```
- `_finding_order` · survived · ternary: condition forced · `9054c8b1d4a46eb3`
  ```diff
  -sequence = -1 if finding.sequence is None else finding.sequence
  +sequence = -1 if (finding.sequence is None) or True else finding.sequence
  ```
- `_finding_order` · survived · operator: arithmetic · `bc0900f3d4fe3701`
  ```diff
  -sequence = -1 if finding.sequence is None else finding.sequence
  +sequence = +1 if finding.sequence is None else finding.sequence
  ```
- `_finding_order` · survived · number · `6d466100579df01c`
  ```diff
  -sequence = -1 if finding.sequence is None else finding.sequence
  +sequence = -2 if finding.sequence is None else finding.sequence
  ```
- `_finding_order` · survived · operator: and / or · `180149b350ae0063`
  ```diff
  -return (sequence, finding.code, finding.entry_sha256 or "", finding.detail or "")
  +return (sequence, finding.code, finding.entry_sha256 and "", finding.detail or "")
  ```
- `_finding_order` · survived · string: XX added · `7acc3e1df7f6b51a`
  ```diff
  -return (sequence, finding.code, finding.entry_sha256 or "", finding.detail or "")
  +return (sequence, finding.code, finding.entry_sha256 or "XXXX", finding.detail or "")
  ```
- `_finding_order` · survived · operator: and / or · `f4c3a8cf35dbb9a6`
  ```diff
  -return (sequence, finding.code, finding.entry_sha256 or "", finding.detail or "")
  +return (sequence, finding.code, finding.entry_sha256 or "", finding.detail and "")
  ```
- `_finding_order` · survived · string: XX added · `25552417cabe33ae`
  ```diff
  -return (sequence, finding.code, finding.entry_sha256 or "", finding.detail or "")
  +return (sequence, finding.code, finding.entry_sha256 or "", finding.detail or "XXXX")
  ```
- `_parsed` · survived · argument: replaced by None · `edceda2887349d58`
  ```diff
  -findings.append(_finding("entry_malformed", "invalid", sha(raw)))
  +findings.append(_finding("entry_malformed", "invalid", None))
  ```
- `_parsed` · survived · argument: removed · `fd21e3fda2d32d27`
  ```diff
  -findings.append(_finding("entry_malformed", "invalid", sha(raw)))
  +findings.append(_finding("entry_malformed", "invalid", ))
  ```
- `_finding` · survived · ternary: condition forced · `7a91af3205edbbf6`
  ```diff
  -"sequence": None if entry is None else entry.sequence,
  +"sequence": None if (entry is None) or True else entry.sequence,
  ```
- `_walk` · survived · argument: replaced by None · `f71d7e461a140497`
  ```diff
  -findings.extend([_finding("fork", "invalid", link.key, link.entry)] if fork else [])
  +findings.extend([_finding("fork", "invalid", None, link.entry)] if fork else [])
  ```
- `_walk` · survived · argument: replaced by None · `4c1e4c284aca7ed7`
  ```diff
  -findings.extend([_finding("fork", "invalid", link.key, link.entry)] if fork else [])
  +findings.extend([_finding("fork", "invalid", link.key, None)] if fork else [])
  ```
- `_walk` · survived · argument: removed · `b55a38393dfe361a`
  ```diff
  -findings.extend([_finding("fork", "invalid", link.key, link.entry)] if fork else [])
  +findings.extend([_finding("fork", "invalid", link.key, )] if fork else [])
  ```
- `_link_findings` · survived · argument: replaced by None · `0eb6e2c31959afb6`
  ```diff
  -return [_finding(code, "invalid", link.key, child) for code, failed in checks if failed]
  +return [_finding(code, "invalid", None, child) for code, failed in checks if failed]
  ```
- `_link_findings` · survived · argument: replaced by None · `568d541b912c3986`
  ```diff
  -return [_finding(code, "invalid", link.key, child) for code, failed in checks if failed]
  +return [_finding(code, "invalid", link.key, None) for code, failed in checks if failed]
  ```
- `_link_findings` · survived · argument: removed · `1ef72d1e39defa52`
  ```diff
  -return [_finding(code, "invalid", link.key, child) for code, failed in checks if failed]
  +return [_finding(code, "invalid", link.key, ) for code, failed in checks if failed]
  ```
- `_unreached` · survived · argument: replaced by None · `b176d8ff7485c9c0`
  ```diff
  -code, effect = _orphan(entry, parent, placed)
  +code, effect = _orphan(entry, None, placed)
  ```
- `_unreached` · survived · argument: replaced by None · `f82d8a054689d860`
  ```diff
  -findings.append(_finding(code, effect, key, entry))
  +findings.append(_finding(code, effect, key, None))
  ```
- `_unreached` · survived · argument: removed · `12843e08260c142d`
  ```diff
  -findings.append(_finding(code, effect, key, entry))
  +findings.append(_finding(code, effect, key, ))
  ```
- `_ancestry` · survived · argument: replaced by None · `bd91c7981cfe6057`
  ```diff
  -seen.add(parent)
  +seen.add(None)
  ```
- `_ancestry` · survived · operator: and / or · `6cb3a996c98a237e`
  ```diff
  -if parent is None or parent in reached:
  +if parent is None and parent in reached:
  ```
- `_ancestry` · survived · argument: replaced by None · `5810d9f792992cc1`
  ```diff
  -return "entry_unreachable", _orphan(parsed[key], parent, placed)[1]
  +return "entry_unreachable", _orphan(parsed[key], None, placed)[1]
  ```
- `_anchor` · survived · argument: replaced by None · `5c966c7da0f22564`
  ```diff
  -return [_finding("head_mismatch", "invalid", expected_head)]
  +return [_finding("head_mismatch", "invalid", None)]
  ```
- `_anchor` · survived · argument: removed · `940c9e31d76a207c`
  ```diff
  -return [_finding("head_mismatch", "invalid", expected_head)]
  +return [_finding("head_mismatch", "invalid", )]
  ```
- `_Checker.note` · survived · argument: replaced by None · `ddf686b2630d1dd1`
  ```diff
  -found = _finding(code, effect, self.key, self.entry)
  +found = _finding(code, effect, None, self.entry)
  ```
- `_Checker.note` · survived · argument: replaced by None · `110bc950cd31c19c`
  ```diff
  -found = _finding(code, effect, self.key, self.entry)
  +found = _finding(code, effect, self.key, None)
  ```
- `_Checker.note` · survived · argument: removed · `df31dc0fc55bac84`
  ```diff
  -found = _finding(code, effect, self.key, self.entry)
  +found = _finding(code, effect, self.key, )
  ```
- `_Checker.fetch` · survived · argument: replaced by None · `ca1763f59c240f7c`
  ```diff
  -self.note("artifact_missing", "gap", kind)
  +self.note("artifact_missing", "gap", None)
  ```
- `_Checker.fetch` · survived · argument: replaced by None · `fe34c0976fa5403a`
  ```diff
  -self.note("artifact_altered", "invalid", kind)
  +self.note("artifact_altered", "invalid", None)
  ```
- `_Checker.bound` · survived · argument: replaced by None · `fd2940dd7d13415c`
  ```diff
  -self.note("artifact_mismatch", "invalid", kind)
  +self.note("artifact_mismatch", "invalid", None)
  ```
- `_Checker.rebuilt` · survived · assignment: value to None · `ee73ff5d213c0a51`
  ```diff
  -same = False
  +same = None
  ```
- `_Checker.rebuilt` · survived · True / False swapped · `b84599328eefa630`
  ```diff
  -same = False
  +same = True
  ```
- `entry_findings` · survived · argument: replaced by None · `7e47dbcb7d7325be`
  ```diff
  -checker = _Checker(key, entry, artifacts)
  +checker = _Checker(None, entry, artifacts)
  ```
- `entry_findings` · survived · operator: and / or · `ef57af039a46961f`
  ```diff
  -if problem is not None or (evidence is None and not checker.findings):
  +if problem is not None and (evidence is None and not checker.findings):
  ```
- `entry_findings` · survived · argument: replaced by None · `d591060064b6f765`
  ```diff
  -checker.note("evidence_not_reconstructed", "invalid", problem or "inputs")
  +checker.note("evidence_not_reconstructed", "invalid", None)
  ```
- `entry_findings` · survived · operator: and / or · `ce21a1d3d80f3ab5`
  ```diff
  -checker.note("evidence_not_reconstructed", "invalid", problem or "inputs")
  +checker.note("evidence_not_reconstructed", "invalid", problem and "inputs")
  ```
- `entry_findings` · survived · string: XX added · `cf675b9a3507ead5`
  ```diff
  -checker.note("evidence_not_reconstructed", "invalid", problem or "inputs")
  +checker.note("evidence_not_reconstructed", "invalid", problem or "XXinputsXX")
  ```
- `entry_findings` · survived · string: upper case · `8bfe72d52ab3d142`
  ```diff
  -checker.note("evidence_not_reconstructed", "invalid", problem or "inputs")
  +checker.note("evidence_not_reconstructed", "invalid", problem or "INPUTS")
  ```
- `entry_findings` · survived · argument: replaced by None · `e3b627c31f7cab36`
  ```diff
  -checker.rebuilt(built, lambda: manifest_text(build_version_manifest(evidence)), "manifest")
  +checker.rebuilt(built, lambda: manifest_text(build_version_manifest(evidence)), None)
  ```
- `entry_findings` · survived · string: XX added · `98ae8608a2a42ca2`
  ```diff
  -checker.rebuilt(built, lambda: manifest_text(build_version_manifest(evidence)), "manifest")
  +checker.rebuilt(built, lambda: manifest_text(build_version_manifest(evidence)), "XXmanifestXX")
  ```
- `entry_findings` · survived · string: upper case · `f610d8daa21f2451`
  ```diff
  -checker.rebuilt(built, lambda: manifest_text(build_version_manifest(evidence)), "manifest")
  +checker.rebuilt(built, lambda: manifest_text(build_version_manifest(evidence)), "MANIFEST")
  ```
- `_source_bytes` · survived · argument: replaced by None · `706466225ff23d0f`
  ```diff
  -found = {d: checker.fetch(d, "source") for d in checker.entry.source_sha256s}
  +found = {d: checker.fetch(d, None) for d in checker.entry.source_sha256s}
  ```
- `_source_bytes` · survived · string: XX added · `159749a9d118d6a3`
  ```diff
  -found = {d: checker.fetch(d, "source") for d in checker.entry.source_sha256s}
  +found = {d: checker.fetch(d, "XXsourceXX") for d in checker.entry.source_sha256s}
  ```
- `_source_bytes` · survived · string: upper case · `3abbc1a06e06f580`
  ```diff
  -found = {d: checker.fetch(d, "source") for d in checker.entry.source_sha256s}
  +found = {d: checker.fetch(d, "SOURCE") for d in checker.entry.source_sha256s}
  ```
- `_parse` · survived · argument: replaced by None · `4726a6ae99d6d9e2`
  ```diff
  -raw = checker.fetch(digest, kind)
  +raw = checker.fetch(digest, None)
  ```
- `_manifest` · survived · argument: replaced by None · `0837fd6118488976`
  ```diff
  -checker.bound(manifest.version_id == entry.version_id, "manifest version")
  +checker.bound(manifest.version_id == entry.version_id, None)
  ```
- `_manifest` · survived · string: XX added · `6f35017228402213`
  ```diff
  -checker.bound(manifest.version_id == entry.version_id, "manifest version")
  +checker.bound(manifest.version_id == entry.version_id, "XXmanifest versionXX")
  ```
- `_manifest` · survived · string: upper case · `86f45b55c25cef69`
  ```diff
  -checker.bound(manifest.version_id == entry.version_id, "manifest version")
  +checker.bound(manifest.version_id == entry.version_id, "MANIFEST VERSION")
  ```
- `_manifest` · survived · argument: replaced by None · `3c7a50d0121b8e65`
  ```diff
  -checker.bound(content.document.document_id == entry.document_id, "manifest document")
  +checker.bound(content.document.document_id == entry.document_id, None)
  ```
- `_manifest` · survived · string: XX added · `f423ce385a6d6e2d`
  ```diff
  -checker.bound(content.document.document_id == entry.document_id, "manifest document")
  +checker.bound(content.document.document_id == entry.document_id, "XXmanifest documentXX")
  ```
- `_manifest` · survived · string: upper case · `1788879dc557a076`
  ```diff
  -checker.bound(content.document.document_id == entry.document_id, "manifest document")
  +checker.bound(content.document.document_id == entry.document_id, "MANIFEST DOCUMENT")
  ```
- `_manifest` · survived · argument: replaced by None · `8f5b4f40f2ef6ce4`
  ```diff
  -checker.bound(_sources(provenance) == entry.source_sha256s, "manifest sources")
  +checker.bound(_sources(provenance) == entry.source_sha256s, None)
  ```
- `_manifest` · survived · string: XX added · `e1dbfbfae8cf123f`
  ```diff
  -checker.bound(_sources(provenance) == entry.source_sha256s, "manifest sources")
  +checker.bound(_sources(provenance) == entry.source_sha256s, "XXmanifest sourcesXX")
  ```
- `_manifest` · survived · string: upper case · `203c65a59cf12f63`
  ```diff
  -checker.bound(_sources(provenance) == entry.source_sha256s, "manifest sources")
  +checker.bound(_sources(provenance) == entry.source_sha256s, "MANIFEST SOURCES")
  ```
- `_manifest` · survived · argument: replaced by None · `d1d7fe5e29bc98fb`
  ```diff
  -checker.bound(_observed_at(provenance) == entry.observed_at, "manifest observation time")
  +checker.bound(_observed_at(provenance) == entry.observed_at, None)
  ```
- `_manifest` · survived · string: XX added · `17af5d8dae9b7bff`
  ```diff
  -checker.bound(_observed_at(provenance) == entry.observed_at, "manifest observation time")
  +checker.bound(_observed_at(provenance) == entry.observed_at, "XXmanifest observation timeXX")
  ```
- `_manifest` · survived · string: upper case · `e00b166ba37e36da`
  ```diff
  -checker.bound(_observed_at(provenance) == entry.observed_at, "manifest observation time")
  +checker.bound(_observed_at(provenance) == entry.observed_at, "MANIFEST OBSERVATION TIME")
  ```
- `_manifest` · survived · argument: replaced by None · `780d9d7036832347`
  ```diff
  -checker.bound(processing == recorded, "manifest processing versions")
  +checker.bound(processing == recorded, None)
  ```
- `_manifest` · survived · string: XX added · `1f588991d716b808`
  ```diff
  -checker.bound(processing == recorded, "manifest processing versions")
  +checker.bound(processing == recorded, "XXmanifest processing versionsXX")
  ```
- `_manifest` · survived · string: upper case · `aeeb779d39e0074f`
  ```diff
  -checker.bound(processing == recorded, "manifest processing versions")
  +checker.bound(processing == recorded, "MANIFEST PROCESSING VERSIONS")
  ```
- `_evidence` · survived · string: XX added · `d5f0e24adfac8dca`
  ```diff
  -return None, "source mapping"
  +return None, "XXsource mappingXX"
  ```
- `_evidence` · survived · string: upper case · `0a73811a5c1da242`
  ```diff
  -return None, "source mapping"
  +return None, "SOURCE MAPPING"
  ```
- `_evidence` · survived · string: XX added · `d2bb23c6f0653809`
  ```diff
  -return None, "landing records"
  +return None, "XXlanding recordsXX"
  ```
- `_evidence` · survived · string: upper case · `9db9d04eb16cfb3f`
  ```diff
  -return None, "landing records"
  +return None, "LANDING RECORDS"
  ```
- `_source_evidence` · survived · ternary: condition forced · `68dc4d4584012c0f`
  ```diff
  -landing = supporting["landing"][0] if supporting["landing"] else None
  +landing = supporting["landing"][0] if (supporting["landing"]) or True else None
  ```
- `_assessment` · survived · argument: replaced by None · `3cb57d9b64a73b18`
  ```diff
  -checker.bound(assessment.version_id == entry.version_id, "assessment version")
  +checker.bound(assessment.version_id == entry.version_id, None)
  ```
- `_assessment` · survived · string: XX added · `35d3c6e378588f6f`
  ```diff
  -checker.bound(assessment.version_id == entry.version_id, "assessment version")
  +checker.bound(assessment.version_id == entry.version_id, "XXassessment versionXX")
  ```
- `_assessment` · survived · string: upper case · `a007eeb78225fcd9`
  ```diff
  -checker.bound(assessment.version_id == entry.version_id, "assessment version")
  +checker.bound(assessment.version_id == entry.version_id, "ASSESSMENT VERSION")
  ```
- `_assessment` · survived · argument: replaced by None · `1cf49201e94183dc`
  ```diff
  -checker.bound(assessment.assessment_version == recorded, "assessment processing version")
  +checker.bound(assessment.assessment_version == recorded, None)
  ```
- `_assessment` · survived · string: XX added · `3617a044c59851f4`
  ```diff
  -checker.bound(assessment.assessment_version == recorded, "assessment processing version")
  +checker.bound(assessment.assessment_version == recorded, "XXassessment processing versionXX")
  ```
- `_assessment` · survived · string: upper case · `724545392eff910b`
  ```diff
  -checker.bound(assessment.assessment_version == recorded, "assessment processing version")
  +checker.bound(assessment.assessment_version == recorded, "ASSESSMENT PROCESSING VERSION")
  ```
- `_decision` · survived · argument: replaced by None · `83b4c52d97e5f8c5`
  ```diff
  -checker.bound(decision.version_id == entry.version_id, "decision version")
  +checker.bound(decision.version_id == entry.version_id, None)
  ```
- `_decision` · survived · string: XX added · `53f2f336da6b7c9f`
  ```diff
  -checker.bound(decision.version_id == entry.version_id, "decision version")
  +checker.bound(decision.version_id == entry.version_id, "XXdecision versionXX")
  ```
- `_decision` · survived · string: upper case · `6f4cb16af3f173c0`
  ```diff
  -checker.bound(decision.version_id == entry.version_id, "decision version")
  +checker.bound(decision.version_id == entry.version_id, "DECISION VERSION")
  ```
- `_decision` · survived · argument: replaced by None · `65ce5cb14bb378b7`
  ```diff
  -checker.bound(decision.assessment_sha256 == entry.assessment_sha256, "decision assessment")
  +checker.bound(decision.assessment_sha256 == entry.assessment_sha256, None)
  ```
- `_decision` · survived · string: XX added · `f0ec851f2e1c3955`
  ```diff
  -checker.bound(decision.assessment_sha256 == entry.assessment_sha256, "decision assessment")
  +checker.bound(decision.assessment_sha256 == entry.assessment_sha256, "XXdecision assessmentXX")
  ```
- `_decision` · survived · string: upper case · `91d055b18cd44125`
  ```diff
  -checker.bound(decision.assessment_sha256 == entry.assessment_sha256, "decision assessment")
  +checker.bound(decision.assessment_sha256 == entry.assessment_sha256, "DECISION ASSESSMENT")
  ```
- `_decision` · survived · argument: replaced by None · `f8ca0aa5e01492e1`
  ```diff
  -checker.bound(decision.policy_sha256 == entry.policy_sha256, "decision policy")
  +checker.bound(decision.policy_sha256 == entry.policy_sha256, None)
  ```
- `_decision` · survived · string: XX added · `f1f6915addd1a49a`
  ```diff
  -checker.bound(decision.policy_sha256 == entry.policy_sha256, "decision policy")
  +checker.bound(decision.policy_sha256 == entry.policy_sha256, "XXdecision policyXX")
  ```
- `_decision` · survived · string: upper case · `815d89eac2d162df`
  ```diff
  -checker.bound(decision.policy_sha256 == entry.policy_sha256, "decision policy")
  +checker.bound(decision.policy_sha256 == entry.policy_sha256, "DECISION POLICY")
  ```
- `_decision` · survived · argument: replaced by None · `694b6abfa80e20f7`
  ```diff
  -checker.bound(decision.evaluator_version == entry.processing.evaluator_version, "evaluator")
  +checker.bound(decision.evaluator_version == entry.processing.evaluator_version, None)
  ```
- `_decision` · survived · string: XX added · `292bca8ca8edcaaa`
  ```diff
  -checker.bound(decision.evaluator_version == entry.processing.evaluator_version, "evaluator")
  +checker.bound(decision.evaluator_version == entry.processing.evaluator_version, "XXevaluatorXX")
  ```
- `_decision` · survived · string: upper case · `aa50bdc0b3ab18c5`
  ```diff
  -checker.bound(decision.evaluator_version == entry.processing.evaluator_version, "evaluator")
  +checker.bound(decision.evaluator_version == entry.processing.evaluator_version, "EVALUATOR")
  ```
- `_decision` · survived · not / ~ removed · `5c31732219f8f64a`
  ```diff
  -elif not checker.findings:
  +elif checker.findings:
  ```
- `_decision` · survived · argument: replaced by None · `e5910fe6f94e15ab`
  ```diff
  -checker.note("artifact_not_rebuilt", "invalid", "decision inputs")
  +checker.note(None, "invalid", "decision inputs")
  ```
- `_decision` · survived · argument: replaced by None · `c45cde9215ca02c6`
  ```diff
  -checker.note("artifact_not_rebuilt", "invalid", "decision inputs")
  +checker.note("artifact_not_rebuilt", None, "decision inputs")
  ```
- `_decision` · survived · argument: replaced by None · `085bafebef1712ec`
  ```diff
  -checker.note("artifact_not_rebuilt", "invalid", "decision inputs")
  +checker.note("artifact_not_rebuilt", "invalid", None)
  ```
- `_decision` · survived · argument: removed · `f8e61e84862932f5`
  ```diff
  -checker.note("artifact_not_rebuilt", "invalid", "decision inputs")
  +checker.note("invalid", "decision inputs")
  ```
- `_decision` · survived · argument: removed · `4ecdf85660c7514b`
  ```diff
  -checker.note("artifact_not_rebuilt", "invalid", "decision inputs")
  +checker.note("artifact_not_rebuilt", "decision inputs")
  ```
- `_decision` · survived · argument: removed · `4a0121cc2b9b5841`
  ```diff
  -checker.note("artifact_not_rebuilt", "invalid", "decision inputs")
  +checker.note("artifact_not_rebuilt", "invalid", )
  ```
- `_decision` · survived · string: XX added · `7ac69f1a546c738a`
  ```diff
  -checker.note("artifact_not_rebuilt", "invalid", "decision inputs")
  +checker.note("XXartifact_not_rebuiltXX", "invalid", "decision inputs")
  ```
- `_decision` · survived · string: upper case · `39e4a8c23c38da73`
  ```diff
  -checker.note("artifact_not_rebuilt", "invalid", "decision inputs")
  +checker.note("ARTIFACT_NOT_REBUILT", "invalid", "decision inputs")
  ```
- `_decision` · survived · string: XX added · `128c28c28a29c7f8`
  ```diff
  -checker.note("artifact_not_rebuilt", "invalid", "decision inputs")
  +checker.note("artifact_not_rebuilt", "XXinvalidXX", "decision inputs")
  ```
- `_decision` · survived · string: upper case · `27396c7446229293`
  ```diff
  -checker.note("artifact_not_rebuilt", "invalid", "decision inputs")
  +checker.note("artifact_not_rebuilt", "INVALID", "decision inputs")
  ```
- `_decision` · survived · string: XX added · `9562981512a54684`
  ```diff
  -checker.note("artifact_not_rebuilt", "invalid", "decision inputs")
  +checker.note("artifact_not_rebuilt", "invalid", "XXdecision inputsXX")
  ```
- `_decision` · survived · string: upper case · `b9d0f58cb7e97bae`
  ```diff
  -checker.note("artifact_not_rebuilt", "invalid", "decision inputs")
  +checker.note("artifact_not_rebuilt", "invalid", "DECISION INPUTS")
  ```
- `_authorization` · survived · argument: replaced by None · `fc14c85729eb647c`
  ```diff
  -verdict = _parse(checker, entry.authorization_sha256, "authorization", parse_authorization)
  +verdict = _parse(checker, entry.authorization_sha256, None, parse_authorization)
  ```
- `_authorization` · survived · string: XX added · `fe4e210d3c0a8bbc`
  ```diff
  -verdict = _parse(checker, entry.authorization_sha256, "authorization", parse_authorization)
  +verdict = _parse(checker, entry.authorization_sha256, "XXauthorizationXX", parse_authorization)
  ```
- `_authorization` · survived · string: upper case · `dd2386c5ce2d60ae`
  ```diff
  -verdict = _parse(checker, entry.authorization_sha256, "authorization", parse_authorization)
  +verdict = _parse(checker, entry.authorization_sha256, "AUTHORIZATION", parse_authorization)
  ```
- `_authorization` · survived · argument: replaced by None · `be121f0be22c8e49`
  ```diff
  -checker.bound(verdict.policy_sha256 == entry.policy_sha256, "authorization policy")
  +checker.bound(verdict.policy_sha256 == entry.policy_sha256, None)
  ```
- `_authorization` · survived · string: XX added · `dd3af94021e27520`
  ```diff
  -checker.bound(verdict.policy_sha256 == entry.policy_sha256, "authorization policy")
  +checker.bound(verdict.policy_sha256 == entry.policy_sha256, "XXauthorization policyXX")
  ```
- `_authorization` · survived · string: upper case · `01074114e790440b`
  ```diff
  -checker.bound(verdict.policy_sha256 == entry.policy_sha256, "authorization policy")
  +checker.bound(verdict.policy_sha256 == entry.policy_sha256, "AUTHORIZATION POLICY")
  ```
- `_authorization` · survived · argument: replaced by None · `8fef01086e592efb`
  ```diff
  -checker.bound(verdict.verifier_version == recorded, "authorization verifier")
  +checker.bound(verdict.verifier_version == recorded, None)
  ```
- `_authorization` · survived · string: XX added · `805d6a21f3fced20`
  ```diff
  -checker.bound(verdict.verifier_version == recorded, "authorization verifier")
  +checker.bound(verdict.verifier_version == recorded, "XXauthorization verifierXX")
  ```
- `_authorization` · survived · string: upper case · `4adb3b15ce97217a`
  ```diff
  -checker.bound(verdict.verifier_version == recorded, "authorization verifier")
  +checker.bound(verdict.verifier_version == recorded, "AUTHORIZATION VERIFIER")
  ```
- `_ancestry` · timeout · kind not recorded · `a3c8ec9d6c325f1c`
  ```diff
  -if parent in seen:
  +if parent not in seen:
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/version_manifest.py</code>: 26</summary>

- `parse_version_manifest` · survived · operator: and / or · `2a0067ebef7de06a`
  ```diff
  -if not isinstance(payload, dict) or payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
  +if not isinstance(payload, dict) and payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
  ```
- `parse_version_manifest` · survived · argument: replaced by None · `ec86554ac58de4ae`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "manifest")
  +raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, None)
  ```
- `parse_version_manifest` · survived · string: XX added · `12038d28d8ec58aa`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "manifest")
  +raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "XXmanifestXX")
  ```
- `parse_version_manifest` · survived · string: upper case · `d3e62de0211f3432`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "manifest")
  +raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "MANIFEST")
  ```
- `parse_version_manifest` · survived · argument: replaced by None · `d9d95c7d305a2712`
  ```diff
  -raise _invalid("manifest") from error
  +raise _invalid(None) from error
  ```
- `parse_version_manifest` · survived · string: XX added · `2c5b4358ec5b3b5b`
  ```diff
  -raise _invalid("manifest") from error
  +raise _invalid("XXmanifestXX") from error
  ```
- `parse_version_manifest` · survived · string: upper case · `651ea27af9a6c087`
  ```diff
  -raise _invalid("manifest") from error
  +raise _invalid("MANIFEST") from error
  ```
- `parse_version_manifest` · survived · argument: replaced by None · `3340a900fb016541`
  ```diff
  -raise _invalid("manifest")
  +raise _invalid(None)
  ```
- `parse_version_manifest` · survived · string: XX added · `e9a8f76f881ead58`
  ```diff
  -raise _invalid("manifest")
  +raise _invalid("XXmanifestXX")
  ```
- `parse_version_manifest` · survived · string: upper case · `f7e367d7a1e45c7c`
  ```diff
  -raise _invalid("manifest")
  +raise _invalid("MANIFEST")
  ```
- `verify_version_manifest` · survived · argument: replaced by None · `aea3b460fcea91a6`
  ```diff
  -raise _invalid("manifest not rebuilt from the evidence")
  +raise _invalid(None)
  ```
- `verify_version_manifest` · survived · string: XX added · `8ddc655607600970`
  ```diff
  -raise _invalid("manifest not rebuilt from the evidence")
  +raise _invalid("XXmanifest not rebuilt from the evidenceXX")
  ```
- `verify_version_manifest` · survived · string: upper case · `aed5d2ef6176ac7c`
  ```diff
  -raise _invalid("manifest not rebuilt from the evidence")
  +raise _invalid("MANIFEST NOT REBUILT FROM THE EVIDENCE")
  ```
- `_invalid` · survived · argument: replaced by None · `5c112e51f906ec17`
  ```diff
  -return ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, detail)
  +return ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, None)
  ```
- `_require_consistent` · survived · argument: replaced by None · `54cc43be9c794dd8`
  ```diff
  -raise _invalid("version id")
  +raise _invalid(None)
  ```
- `_require_consistent` · survived · string: XX added · `8af9c92fc83bf5f0`
  ```diff
  -raise _invalid("version id")
  +raise _invalid("XXversion idXX")
  ```
- `_require_consistent` · survived · string: upper case · `5579ed5349927eae`
  ```diff
  -raise _invalid("version id")
  +raise _invalid("VERSION ID")
  ```
- `_require_consistent` · survived · argument: replaced by None · `3e8eb376ca400c63`
  ```diff
  -raise _invalid("completeness")
  +raise _invalid(None)
  ```
- `_require_consistent` · survived · string: XX added · `11d7f65a71b6542e`
  ```diff
  -raise _invalid("completeness")
  +raise _invalid("XXcompletenessXX")
  ```
- `_require_consistent` · survived · string: upper case · `45f43b5905f1b2f0`
  ```diff
  -raise _invalid("completeness")
  +raise _invalid("COMPLETENESS")
  ```
- `_gap` · survived · argument: replaced by None · `24db209e79cab7d7`
  ```diff
  -locator=gap.locator,
  +locator=None,
  ```
- `_gap` · survived · argument: replaced by None · `87c6ed0cd510abb7`
  ```diff
  -text_sha256=None if gap.text is None else text_digest(gap.text),
  +text_sha256=None,
  ```
- `_gap` · survived · ternary: condition forced · `c9ea83bd7088c648`
  ```diff
  -text_sha256=None if gap.text is None else text_digest(gap.text),
  +text_sha256=None if (gap.text is None) or True else text_digest(gap.text),
  ```
- `_attachments` · survived · argument: replaced by None · `a2320328eb3dbb9c`
  ```diff
  -by_url.setdefault(canonical_url(page.url, page.url), {})[_sha(page.payload)] = page.payload
  +by_url.setdefault(canonical_url(None, page.url), {})[_sha(page.payload)] = page.payload
  ```
- `_attachments` · survived · argument: replaced by None · `419c4efc110128ca`
  ```diff
  -_attachment(item, by_url.get(item.url, {})) for item in discovery.attachments
  +_attachment(item, by_url.get(item.url, None)) for item in discovery.attachments
  ```
- `_attachments` · survived · argument: removed · `652cef956d1f1c1a`
  ```diff
  -_attachment(item, by_url.get(item.url, {})) for item in discovery.attachments
  +_attachment(item, by_url.get(item.url, )) for item in discovery.attachments
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/version_models.py</code>: 4</summary>

- `_require_counts_follow_discovery` · survived · string: XX added · `816919087233b80f`
  ```diff
  -raise ValueError("attachment discovery is unverified: no count, no source_complete")
  +raise ValueError("XXattachment discovery is unverified: no count, no source_completeXX")
  ```
- `_require_counts_follow_discovery` · survived · argument: replaced by None · `1b9a3e625e654c20`
  ```diff
  -raise ValueError("attachment counts are not those of the verified discovery")
  +raise ValueError(None)
  ```
- `_require_counts_follow_discovery` · survived · string: XX added · `0c80427c6322c290`
  ```diff
  -raise ValueError("attachment counts are not those of the verified discovery")
  +raise ValueError("XXattachment counts are not those of the verified discoveryXX")
  ```
- `_require_counts_follow_discovery` · survived · string: upper case · `d9e1f69b063cb38b`
  ```diff
  -raise ValueError("attachment counts are not those of the verified discovery")
  +raise ValueError("ATTACHMENT COUNTS ARE NOT THOSE OF THE VERIFIED DISCOVERY")
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/version_reader.py</code>: 16</summary>

- `read_version_summaries` · survived · ternary: condition forced · `f362eb2f253ba42b`
  ```diff
  -version = payload.get("schema_version") if isinstance(payload, dict) else None
  +version = payload.get("schema_version") if (isinstance(payload, dict)) or True else None
  ```
- `read_version_summaries` · survived · argument: replaced by None · `01fb93e0a858b96d`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "manifest")
  +raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, None)
  ```
- `read_version_summaries` · survived · string: XX added · `e5a75f32be69e1cb`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "manifest")
  +raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "XXmanifestXX")
  ```
- `read_version_summaries` · survived · string: upper case · `6d1b2129f547cf81`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "manifest")
  +raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "MANIFEST")
  ```
- `_unproved` · survived · string: XX added · `60b8e286e5d94f8d`
  ```diff
  -"publication_state": PublicationState.UNKNOWN
  +"XXpublication_stateXX": PublicationState.UNKNOWN
  ```
- `_unproved` · survived · string: upper case · `ea483088cb785120`
  ```diff
  -"publication_state": PublicationState.UNKNOWN
  +"PUBLICATION_STATE": PublicationState.UNKNOWN
  ```
- `_unproved` · survived · ternary: condition forced · `35a4394495a8eee9`
  ```diff
  -if summary.publication_state is PublicationState.PUBLICATION_COMPLETE
  +if (summary.publication_state is PublicationState.PUBLICATION_COMPLETE) and False
  ```
- `_unproved` · survived · ternary: condition forced · `55bdf7afa0c2d8ee`
  ```diff
  -if summary.publication_state is PublicationState.PUBLICATION_COMPLETE
  +if (summary.publication_state is PublicationState.PUBLICATION_COMPLETE) or True
  ```
- `_unproved` · survived · is / in negated · `d47b4ddbddd63611`
  ```diff
  -if summary.publication_state is PublicationState.PUBLICATION_COMPLETE
  +if summary.publication_state is not PublicationState.PUBLICATION_COMPLETE
  ```
- `_lifted` · survived · is / in negated · `98c4a61ce0106e06`
  ```diff
  -unreleased = [number for number in member.listed_chapters if number not in released]
  +unreleased = [number for number in member.listed_chapters if number in released]
  ```
- `_lifted` · survived · argument: replaced by None · `ee0b0ff775b44fbe`
  ```diff
  -Reason(code="legacy_manifest", effect=Effect.INCOMPLETE, detail=f"v{version}"),
  +Reason(code="legacy_manifest", effect=Effect.INCOMPLETE, detail=None),
  ```
- `_lifted` · survived · argument: removed · `a139426200a9ef5b`
  ```diff
  -Reason(code="legacy_manifest", effect=Effect.INCOMPLETE, detail=f"v{version}"),
  +Reason(code="legacy_manifest", effect=Effect.INCOMPLETE, ),
  ```
- `_lifted` · survived · argument: replaced by None · `f807a0c743f9971d`
  ```diff
  -Reason(code="chapter_not_released", effect=Effect.INCOMPLETE, detail=f"ch{number}")
  +Reason(code="chapter_not_released", effect=Effect.INCOMPLETE, detail=None)
  ```
- `_lifted` · survived · argument: removed · `e7f2afa1806659ed`
  ```diff
  -Reason(code="chapter_not_released", effect=Effect.INCOMPLETE, detail=f"ch{number}")
  +Reason(code="chapter_not_released", effect=Effect.INCOMPLETE, )
  ```
- `_lifted` · survived · string: XX added · `a9fd901ab12d81e0`
  ```diff
  -Reason(code="chapter_not_released", effect=Effect.INCOMPLETE, detail=f"ch{number}")
  +Reason(code="XXchapter_not_releasedXX", effect=Effect.INCOMPLETE, detail=f"ch{number}")
  ```
- `_lifted` · survived · string: upper case · `c869255e2767458c`
  ```diff
  -Reason(code="chapter_not_released", effect=Effect.INCOMPLETE, detail=f"ch{number}")
  +Reason(code="CHAPTER_NOT_RELEASED", effect=Effect.INCOMPLETE, detail=f"ch{number}")
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/version_release.py</code>: 42</summary>

- `parse_release` · survived · argument: replaced by None · `150c9cbf201bc3b7`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release manifest") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None) from error
  ```
- `parse_release` · survived · string: XX added · `eab1fede500fb5a2`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release manifest") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXrelease manifestXX") from error
  ```
- `parse_release` · survived · string: upper case · `5e5212cc52857f08`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release manifest") from error
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "RELEASE MANIFEST") from error
  ```
- `parse_release` · survived · argument: replaced by None · `9c9c501ea9a2584d`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release manifest")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `parse_release` · survived · string: XX added · `ee419a655670aa62`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release manifest")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXrelease manifestXX")
  ```
- `parse_release` · survived · string: upper case · `2cd1068dcebfe2f4`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release manifest")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "RELEASE MANIFEST")
  ```
- `build_release` · survived · argument: replaced by None · `5f183c0aacd8182d`
  ```diff
  -built = [_document(o, inputs) for o in sorted(offers, key=lambda o: o.document_id)]
  +built = [_document(o, inputs) for o in sorted(offers, key=None)]
  ```
- `build_release` · survived · argument: removed · `fac6c2e1da92fbdd`
  ```diff
  -built = [_document(o, inputs) for o in sorted(offers, key=lambda o: o.document_id)]
  +built = [_document(o, inputs) for o in sorted(offers, )]
  ```
- `_check_identities` · survived · argument: replaced by None · `70b260fee79c5d21`
  ```diff
  -raise _conflict("document offered twice")
  +raise _conflict(None)
  ```
- `_check_identities` · survived · string: XX added · `3dec99298fc47fc2`
  ```diff
  -raise _conflict("document offered twice")
  +raise _conflict("XXdocument offered twiceXX")
  ```
- `_check_identities` · survived · string: upper case · `b43bb832bac52d1d`
  ```diff
  -raise _conflict("document offered twice")
  +raise _conflict("DOCUMENT OFFERED TWICE")
  ```
- `_check_identities` · survived · break / continue swapped · `0867f9d4ea61582a`
  ```diff
  -continue
  +break
  ```
- `_check_identities` · survived · argument: replaced by None · `69e3e9dde12e4439`
  ```diff
  -raise _conflict("lineage entry of another document")
  +raise _conflict(None)
  ```
- `_check_identities` · survived · string: XX added · `19b8650f2f8cc31f`
  ```diff
  -raise _conflict("lineage entry of another document")
  +raise _conflict("XXlineage entry of another documentXX")
  ```
- `_check_identities` · survived · string: upper case · `fc1c6de65da32251`
  ```diff
  -raise _conflict("lineage entry of another document")
  +raise _conflict("LINEAGE ENTRY OF ANOTHER DOCUMENT")
  ```
- `_check_offer` · survived · argument: replaced by None · `6cbcba2db4a06a7b`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "document id")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `_check_offer` · survived · string: XX added · `2320fba1cb835d48`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "document id")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXdocument idXX")
  ```
- `_check_offer` · survived · string: upper case · `12b6400ce07052b7`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "document id")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "DOCUMENT ID")
  ```
- `_check_offer` · survived · argument: replaced by None · `7f9af1f9c1a181e5`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "claimed head")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, None)
  ```
- `_check_offer` · survived · string: XX added · `1680aee5392b8331`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "claimed head")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "XXclaimed headXX")
  ```
- `_check_offer` · survived · string: upper case · `0f71ab20b9c43f77`
  ```diff
  -raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "claimed head")
  +raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "CLAIMED HEAD")
  ```
- `_conflict` · survived · argument: replaced by None · `fa151322921781c6`
  ```diff
  -return ForarbeiderRefusedError(RefusalReason.LINEAGE_CONFLICT, detail)
  +return ForarbeiderRefusedError(RefusalReason.LINEAGE_CONFLICT, None)
  ```
- `_selected` · survived · argument: replaced by None · `bc1f96b4f4b6c792`
  ```diff
  -publication_state=manifest.publication.state,
  +publication_state=None,
  ```
- `_policy` · survived · string: XX added · `43ae75e17efd8012`
  ```diff
  -found += [("policy", f"fc7_{value}") for value in ("deny", "unresolved") if value in decided]
  +found += [("policy", f"fc7_{value}") for value in ("XXdenyXX", "unresolved") if value in decided]
  ```
- `_policy` · survived · string: upper case · `2f66875e52912478`
  ```diff
  -found += [("policy", f"fc7_{value}") for value in ("deny", "unresolved") if value in decided]
  +found += [("policy", f"fc7_{value}") for value in ("DENY", "unresolved") if value in decided]
  ```
- `_authorization` · survived · string: XX added · `ebb6e66a3617e536`
  ```diff
  -if verdict.status != "signatures_verified":
  +if verdict.status != "XXsignatures_verifiedXX":
  ```
- `_authorization` · survived · string: upper case · `f4e72b129540d3e5`
  ```diff
  -if verdict.status != "signatures_verified":
  +if verdict.status != "SIGNATURES_VERIFIED":
  ```
- `_differences` · survived · augmented assignment to plain · `6652524162cb3781`
  ```diff
  -found += _compared(offered, rebuilt)
  +found = _compared(offered, rebuilt)
  ```
- `_differences` · survived · string: XX added · `cab5920300c889b9`
  ```diff
  -return tuple(sorted(found, key=lambda item: (item.document_id or "", item.code)))
  +return tuple(sorted(found, key=lambda item: (item.document_id or "XXXX", item.code)))
  ```
- `_compared` · survived · string: XX added · `dd35f19c5136cce3`
  ```diff
  -("bindings_mismatch", offered.bindings_sha256 != rebuilt.bindings_sha256),
  +("XXbindings_mismatchXX", offered.bindings_sha256 != rebuilt.bindings_sha256),
  ```
- `_compared` · survived · string: upper case · `fe22723755f83756`
  ```diff
  -("bindings_mismatch", offered.bindings_sha256 != rebuilt.bindings_sha256),
  +("BINDINGS_MISMATCH", offered.bindings_sha256 != rebuilt.bindings_sha256),
  ```
- `_compared` · survived · argument: replaced by None · `f2f14506838d1093`
  ```diff
  -found.append(ReleaseFinding(code="manifest_differs"))
  +found.append(None)
  ```
- `_compared` · survived · argument: replaced by None · `85ba0ed3cfc5784b`
  ```diff
  -found.append(ReleaseFinding(code="manifest_differs"))
  +found.append(ReleaseFinding(code=None))
  ```
- `_compared` · survived · string: XX added · `8472688132acc7ac`
  ```diff
  -found.append(ReleaseFinding(code="manifest_differs"))
  +found.append(ReleaseFinding(code="XXmanifest_differsXX"))
  ```
- `_compared` · survived · string: upper case · `2f7a9d36a4f11a82`
  ```diff
  -found.append(ReleaseFinding(code="manifest_differs"))
  +found.append(ReleaseFinding(code="MANIFEST_DIFFERS"))
  ```
- `_by_document` · survived · argument: replaced by None · `1d3edd0d33df8130`
  ```diff
  -rows = zip(manifest.content.documents, manifest.bindings, manifest.observations, strict=True)
  +rows = zip(manifest.content.documents, manifest.bindings, manifest.observations, strict=None)
  ```
- `_by_document` · survived · argument: removed · `d3cf3977865aadd9`
  ```diff
  -rows = zip(manifest.content.documents, manifest.bindings, manifest.observations, strict=True)
  +rows = zip(manifest.content.documents, manifest.bindings, manifest.observations, )
  ```
- `_by_document` · survived · True / False swapped · `9b3564120cb7b0b6`
  ```diff
  -rows = zip(manifest.content.documents, manifest.bindings, manifest.observations, strict=True)
  +rows = zip(manifest.content.documents, manifest.bindings, manifest.observations, strict=False)
  ```
- `_by_document` · survived · number · `c8143936acdf578b`
  ```diff
  -return {row[0].document_id: _Document(*row) for row in rows}
  +return {row[1].document_id: _Document(*row) for row in rows}
  ```
- `_document_differences` · survived · argument: replaced by None · `196150ecc4530e3b`
  ```diff
  -for part, before, after in zip(_Document._fields, offered, rebuilt, strict=True)
  +for part, before, after in zip(_Document._fields, offered, rebuilt, strict=None)
  ```
- `_document_differences` · survived · argument: removed · `684162b1786f9f71`
  ```diff
  -for part, before, after in zip(_Document._fields, offered, rebuilt, strict=True)
  +for part, before, after in zip(_Document._fields, offered, rebuilt, )
  ```
- `_document_differences` · survived · True / False swapped · `2939b8460f8ed0ba`
  ```diff
  -for part, before, after in zip(_Document._fields, offered, rebuilt, strict=True)
  +for part, before, after in zip(_Document._fields, offered, rebuilt, strict=False)
  ```

</details>

<details><summary><code>src/lovspor/forarbeider/version_release_models.py</code>: 6</summary>

- `status_of` · survived · string: XX added · `a1021861333d2020`
  ```diff
  -return "blocked" if blockers else "included"
  +return "blocked" if blockers else "XXincludedXX"
  ```
- `status_of` · survived · string: upper case · `72e51427d88b632a`
  ```diff
  -return "blocked" if blockers else "included"
  +return "blocked" if blockers else "INCLUDED"
  ```
- `counts_of` · survived · argument: replaced by None · `4bd1896689a21eea`
  ```diff
  -included=statuses.count("included"),
  +included=statuses.count(None),
  ```
- `counts_of` · survived · string: XX added · `a5cde937009765d3`
  ```diff
  -included=statuses.count("included"),
  +included=statuses.count("XXincludedXX"),
  ```
- `counts_of` · survived · string: upper case · `67df4745f47c3f30`
  ```diff
  -included=statuses.count("included"),
  +included=statuses.count("INCLUDED"),
  ```
- `digest_of` · survived · string: XX added · `1ac8725f3dde6d03`
  ```diff
  -text = "".join(canonical_text(record) for record in records)
  +text = "XXXX".join(canonical_text(record) for record in records)
  ```

</details>

<details><summary><code>src/lovspor/history.py</code>: 1</summary>

- `_lineage` · survived · operator: and / or · `8a8d696ff6140fd2`
  ```diff
  -if from_path and to_path:
  +if from_path or to_path:
  ```

</details>

<details><summary><code>src/lovspor/identity/apply.py</code>: 5</summary>

- `_move` · survived · argument: replaced by None · `1be974949c2cc7ce`
  ```diff
  -raise IdentityMigrationError(f"{move.target}: planned move target already exists")
  +raise IdentityMigrationError(None)
  ```
- `_write_histories` · survived · argument: replaced by None · `76fa67e09a7c56d1`
  ```diff
  -target.parent.mkdir(parents=True, exist_ok=True)
  +target.parent.mkdir(parents=None, exist_ok=True)
  ```
- `_write_histories` · survived · argument: removed · `34f2a4a581c32e3c`
  ```diff
  -target.parent.mkdir(parents=True, exist_ok=True)
  +target.parent.mkdir(exist_ok=True)
  ```
- `_write_histories` · survived · True / False swapped · `ffc460521ded3ad7`
  ```diff
  -target.parent.mkdir(parents=True, exist_ok=True)
  +target.parent.mkdir(parents=False, exist_ok=True)
  ```
- `_digest` · survived · ternary: condition forced · `6b44781dbe7ce368`
  ```diff
  -return sha256_bytes(path.read_bytes()) if path.is_file() else None
  +return sha256_bytes(path.read_bytes()) if (path.is_file()) or True else None
  ```

</details>

<details><summary><code>src/lovspor/identity/apply_commands.py</code>: 29</summary>

- `_read_sums` · survived · string: upper case · `91a44bf46d9ec232`
  ```diff
  -lines = raw.decode("utf-8").splitlines()
  +lines = raw.decode("UTF-8").splitlines()
  ```
- `_read_sums` · survived · argument: replaced by None · `e9654683fff74869`
  ```diff
  -raise IdentityMigrationError(f"{SUMS_FILENAME} is not UTF-8: {exc}") from exc
  +raise IdentityMigrationError(None) from exc
  ```
- `_read_sums` · survived · string method swapped · `d848878777c863c7`
  ```diff
  -digest, sep, name = line.partition("  ")
  +digest, sep, name = line.rpartition("  ")
  ```
- `_read_sums` · survived · operator: and / or · `dea58a5ee421abe2`
  ```diff
  -if not sep or not name or name in sums:
  +if not sep and not name or name in sums:
  ```
- `_read_sums` · survived · argument: replaced by None · `faea42dc4b0306a0`
  ```diff
  -raise IdentityMigrationError(f"{SUMS_FILENAME}: malformed line {line!r}")
  +raise IdentityMigrationError(None)
  ```
- `verified_plan_files` · survived · argument: replaced by None · `0a73ac6361b76087`
  ```diff
  -raise IdentityMigrationError(f"plan files unlisted {unlisted}, missing {missing}")
  +raise IdentityMigrationError(None)
  ```
- `_plan_refs` · survived · argument: replaced by None · `bf71a06f0465786f`
  ```diff
  -data = json.loads((plan_dir / "report.json").read_text(encoding="utf-8"))
  +data = json.loads((plan_dir / "report.json").read_text(encoding=None))
  ```
- `_plan_refs` · survived · string: upper case · `7ec52ce52c270abc`
  ```diff
  -data = json.loads((plan_dir / "report.json").read_text(encoding="utf-8"))
  +data = json.loads((plan_dir / "report.json").read_text(encoding="UTF-8"))
  ```
- `_plan_refs` · survived · argument: replaced by None · `4faf54e022416701`
  ```diff
  -raise IdentityMigrationError(f"{plan_dir}: unreadable report.json: {exc}") from exc
  +raise IdentityMigrationError(None) from exc
  ```
- `require_clean_base` · survived · argument: replaced by None · `7bd687c3616581ad`
  ```diff
  -raise IdentityMigrationError(f"{repo} is not the root of its git work tree")
  +raise IdentityMigrationError(None)
  ```
- `require_clean_base` · survived · number · `fdbcc0919018934d`
  ```diff
  -raise IdentityMigrationError(f"the checkout is not clean: {dirty[:5]}")
  +raise IdentityMigrationError(f"the checkout is not clean: {dirty[:6]}")
  ```
- `_require_applicable` · survived · argument: replaced by None · `abef59b9684ced8c`
  ```diff
  -raise IdentityMigrationError("the plan is blocked; see report.json")
  +raise IdentityMigrationError(None)
  ```
- `_require_applicable` · survived · string: XX added · `d254323c329f3fad`
  ```diff
  -raise IdentityMigrationError("the plan is blocked; see report.json")
  +raise IdentityMigrationError("XXthe plan is blocked; see report.jsonXX")
  ```
- `_require_applicable` · survived · string: upper case · `2dfe29640ac43f14`
  ```diff
  -raise IdentityMigrationError("the plan is blocked; see report.json")
  +raise IdentityMigrationError("THE PLAN IS BLOCKED; SEE REPORT.JSON")
  ```
- `_require_applicable` · survived · argument: replaced by None · `9c88db6c3f26490f`
  ```diff
  -raise IdentityMigrationError("the plan has nothing to do at this base")
  +raise IdentityMigrationError(None)
  ```
- `_require_applicable` · survived · string: XX added · `9a3abda41b87438f`
  ```diff
  -raise IdentityMigrationError("the plan has nothing to do at this base")
  +raise IdentityMigrationError("XXthe plan has nothing to do at this baseXX")
  ```
- `_require_applicable` · survived · string: upper case · `e7d8709f9cd7b14e`
  ```diff
  -raise IdentityMigrationError("the plan has nothing to do at this base")
  +raise IdentityMigrationError("THE PLAN HAS NOTHING TO DO AT THIS BASE")
  ```
- `identity_apply_impl` · survived · string: XX added · `943918fcde69b490`
  ```diff
  -"base_commit": plan.base.commit,
  +"XXbase_commitXX": plan.base.commit,
  ```
- `identity_apply_impl` · survived · string: upper case · `99f4825154388858`
  ```diff
  -"base_commit": plan.base.commit,
  +"BASE_COMMIT": plan.base.commit,
  ```
- `identity_apply_impl` · survived · string: XX added · `ca6b13b2eb2520d4`
  ```diff
  -"changed_paths": len(plan.changed_paths),
  +"XXchanged_pathsXX": len(plan.changed_paths),
  ```
- `identity_apply_impl` · survived · string: upper case · `50428725278ce80b`
  ```diff
  -"changed_paths": len(plan.changed_paths),
  +"CHANGED_PATHS": len(plan.changed_paths),
  ```
- `identity_apply_impl` · survived · string: XX added · `73681c63b487d4a2`
  ```diff
  -"planned_files": len(expected_digests(plan)),
  +"XXplanned_filesXX": len(expected_digests(plan)),
  ```
- `identity_apply_impl` · survived · string: upper case · `7288df98641ecd7c`
  ```diff
  -"planned_files": len(expected_digests(plan)),
  +"PLANNED_FILES": len(expected_digests(plan)),
  ```
- `identity_apply_impl` · survived · argument: replaced by None · `543cc2dcdef3825c`
  ```diff
  -return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)
  +return code, json.dumps(summary, sort_keys=None, ensure_ascii=False)
  ```
- `identity_apply_impl` · survived · argument: replaced by None · `4069e76d19fb76e7`
  ```diff
  -return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)
  +return code, json.dumps(summary, sort_keys=True, ensure_ascii=None)
  ```
- `identity_apply_impl` · survived · argument: removed · `a1e6faa4e6f7d4d2`
  ```diff
  -return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)
  +return code, json.dumps(summary, ensure_ascii=False)
  ```
- `identity_apply_impl` · survived · argument: removed · `794dd5883191f31e`
  ```diff
  -return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)
  +return code, json.dumps(summary, sort_keys=True, )
  ```
- `identity_apply_impl` · survived · True / False swapped · `5461840152eadbca`
  ```diff
  -return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)
  +return code, json.dumps(summary, sort_keys=False, ensure_ascii=False)
  ```
- `identity_apply_impl` · survived · True / False swapped · `2291c943b5aa8402`
  ```diff
  -return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)
  +return code, json.dumps(summary, sort_keys=True, ensure_ascii=True)
  ```

</details>

<details><summary><code>src/lovspor/identity/apply_guards.py</code>: 3</summary>

- `require_pinned` · survived · argument: replaced by None · `2f70586dbc05f13c`
  ```diff
  -raise IdentityMigrationError(f"{plan_dir}: no readable {SUMS_FILENAME}: {exc}") from exc
  +raise IdentityMigrationError(None) from exc
  ```
- `require_safe_names` · survived · string: XX added · `57278b5720ac03d1`
  ```diff
  -if name.startswith("/") or "\\" in name or unsafe:
  +if name.startswith("XX/XX") or "\\" in name or unsafe:
  ```
- `require_confined` · survived · number · `1716577f2bc5ee6f`
  ```diff
  -f"paths through a symlink or leading out of {repo}: {escaping[:5]}"
  +f"paths through a symlink or leading out of {repo}: {escaping[:6]}"
  ```

</details>

<details><summary><code>src/lovspor/identity/audit.py</code>: 16</summary>

- `_role` · survived · operator: comparison · `442e5b0b9c0e00fc`
  ```diff
  -return "prior" if index < owner_index else "later"
  +return "prior" if index <= owner_index else "later"
  ```
- `_reuse_kinds` · survived · argument: replaced by None · `975dd2d5fb3b3a5c`
  ```diff
  -for span, fate in zip(spans, fates, strict=True):
  +for span, fate in zip(spans, fates, strict=None):
  ```
- `_reuse_kinds` · survived · argument: removed · `dfdbe2ae79895ec9`
  ```diff
  -for span, fate in zip(spans, fates, strict=True):
  +for span, fate in zip(spans, fates, ):
  ```
- `_reuse_kinds` · survived · True / False swapped · `006298f6b7ac21d0`
  ```diff
  -for span, fate in zip(spans, fates, strict=True):
  +for span, fate in zip(spans, fates, strict=False):
  ```
- `_reuse_kinds` · survived · operator: comparison · `9bbc5633b70c22c2`
  ```diff
  -after = facts.walk.position(span.first_commit) > facts.threshold
  +after = facts.walk.position(span.first_commit) >= facts.threshold
  ```
- `_reuse_kinds` · survived · ternary: condition forced · `f0704bbc68b0ee30`
  ```diff
  -kinds.add(POST if after else SAME)
  +kinds.add(POST if (after) or True else SAME)
  ```
- `_audit_one` · survived · argument: replaced by None · `51f5e8c6ae9d6c44`
  ```diff
  -entry = facts.registry.slugs.get(tomb.directory, {}).get(tomb.slug)
  +entry = facts.registry.slugs.get(tomb.directory, None).get(tomb.slug)
  ```
- `_audit_one` · survived · argument: removed · `2c9204d979e9cbd2`
  ```diff
  -entry = facts.registry.slugs.get(tomb.directory, {}).get(tomb.slug)
  +entry = facts.registry.slugs.get(tomb.directory, ).get(tomb.slug)
  ```
- `_audit_one` · survived · ternary: condition forced · `4c655e38751a4f92`
  ```diff
  -owner = None if entry is None else entry.owner
  +owner = None if (entry is None) and False else entry.owner
  ```
- `_audit_one` · survived · argument: replaced by None · `ab64d669dc6fcab1`
  ```diff
  -owning_side={tomb.doc_id: "restored", holder: "holder"}.get(owner or "", "other"),
  +owning_side={tomb.doc_id: "restored", holder: "holder"}.get(owner or "", None),
  ```
- `_audit_one` · survived · argument: removed · `bbcc7a9f0df8f1dd`
  ```diff
  -owning_side={tomb.doc_id: "restored", holder: "holder"}.get(owner or "", "other"),
  +owning_side={tomb.doc_id: "restored", holder: "holder"}.get(owner or "", ),
  ```
- `_audit_one` · survived · string: XX added · `7061d51cd924d0d3`
  ```diff
  -owning_side={tomb.doc_id: "restored", holder: "holder"}.get(owner or "", "other"),
  +owning_side={tomb.doc_id: "restored", holder: "holder"}.get(owner or "XXXX", "other"),
  ```
- `_audit_one` · survived · string: XX added · `b95cd06039bd41fa`
  ```diff
  -owning_side={tomb.doc_id: "restored", holder: "holder"}.get(owner or "", "other"),
  +owning_side={tomb.doc_id: "restored", holder: "holder"}.get(owner or "", "XXotherXX"),
  ```
- `_audit_one` · survived · string: upper case · `439b062f573bcf91`
  ```diff
  -owning_side={tomb.doc_id: "restored", holder: "holder"}.get(owner or "", "other"),
  +owning_side={tomb.doc_id: "restored", holder: "holder"}.get(owner or "", "OTHER"),
  ```
- `_audit_one` · survived · string: XX added · `1103e5a77f4a6746`
  ```diff
  -outcome="",
  +outcome="XXXX",
  ```
- `_audit_one` · survived · operator: and / or · `47bd541f0790cfb1`
  ```diff
  -recorded = entry is not None and tomb.doc_id in {p.doc_id for p in entry.prior_holders}
  +recorded = entry is not None or tomb.doc_id in {p.doc_id for p in entry.prior_holders}
  ```

</details>

<details><summary><code>src/lovspor/identity/commands.py</code>: 14</summary>

- `identity_plan_impl` · survived · argument: replaced by None · `1d527a3ba0db504f`
  ```diff
  -out.mkdir(parents=True, exist_ok=True)
  +out.mkdir(parents=None, exist_ok=True)
  ```
- `identity_plan_impl` · survived · argument: replaced by None · `5423ffdf56a6a8ed`
  ```diff
  -out.mkdir(parents=True, exist_ok=True)
  +out.mkdir(parents=True, exist_ok=None)
  ```
- `identity_plan_impl` · survived · argument: removed · `cc1329293e0ec481`
  ```diff
  -out.mkdir(parents=True, exist_ok=True)
  +out.mkdir(exist_ok=True)
  ```
- `identity_plan_impl` · survived · argument: removed · `ab66c3bb653e16cd`
  ```diff
  -out.mkdir(parents=True, exist_ok=True)
  +out.mkdir(parents=True, )
  ```
- `identity_plan_impl` · survived · True / False swapped · `fd02b367eda83ded`
  ```diff
  -out.mkdir(parents=True, exist_ok=True)
  +out.mkdir(parents=False, exist_ok=True)
  ```
- `identity_plan_impl` · survived · True / False swapped · `fa42e270868ea05a`
  ```diff
  -out.mkdir(parents=True, exist_ok=True)
  +out.mkdir(parents=True, exist_ok=False)
  ```
- `identity_plan_impl` · survived · string: XX added · `548b187112b010f5`
  ```diff
  -"u1_threshold": data["u1_threshold"],
  +"XXu1_thresholdXX": data["u1_threshold"],
  ```
- `identity_plan_impl` · survived · string: upper case · `c807a1c6271ba2f4`
  ```diff
  -"u1_threshold": data["u1_threshold"],
  +"U1_THRESHOLD": data["u1_threshold"],
  ```
- `identity_plan_impl` · survived · argument: replaced by None · `702073e3c6faaba8`
  ```diff
  -return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)
  +return code, json.dumps(summary, sort_keys=None, ensure_ascii=False)
  ```
- `identity_plan_impl` · survived · argument: replaced by None · `a55247a8f3e979c6`
  ```diff
  -return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)
  +return code, json.dumps(summary, sort_keys=True, ensure_ascii=None)
  ```
- `identity_plan_impl` · survived · argument: removed · `efa48d97f041a256`
  ```diff
  -return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)
  +return code, json.dumps(summary, ensure_ascii=False)
  ```
- `identity_plan_impl` · survived · argument: removed · `3a8899527b24711a`
  ```diff
  -return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)
  +return code, json.dumps(summary, sort_keys=True, )
  ```
- `identity_plan_impl` · survived · True / False swapped · `22575ee034e1d82d`
  ```diff
  -return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)
  +return code, json.dumps(summary, sort_keys=False, ensure_ascii=False)
  ```
- `identity_plan_impl` · survived · True / False swapped · `1124f2b81a42e745`
  ```diff
  -return code, json.dumps(summary, sort_keys=True, ensure_ascii=False)
  +return code, json.dumps(summary, sort_keys=True, ensure_ascii=True)
  ```

</details>

<details><summary><code>src/lovspor/identity/corpus_git.py</code>: 47</summary>

- `_git` · survived · string: XX added · `32777a2460dc2a20`
  ```diff
  -raise IdentityMigrationError(f"git {' '.join(args[:2])} failed in {repo}: {exc}") from exc
  +raise IdentityMigrationError(f"git {'XX XX'.join(args[:2])} failed in {repo}: {exc}") from exc
  ```
- `_git` · survived · number · `a3ac7c54db8ce9c4`
  ```diff
  -raise IdentityMigrationError(f"git {' '.join(args[:2])} failed in {repo}: {exc}") from exc
  +raise IdentityMigrationError(f"git {' '.join(args[:3])} failed in {repo}: {exc}") from exc
  ```
- `resolve_commit` · survived · argument: removed · `b9d52b491e0070a2`
  ```diff
  -return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").decode().strip()
  +return _git(repo, "rev-parse", "--quiet", f"{ref}^{{commit}}").decode().strip()
  ```
- `resolve_commit` · survived · argument: removed · `81c8a1d6884f227e`
  ```diff
  -return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").decode().strip()
  +return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
  ```
- `resolve_commit` · survived · string: upper case · `6d5582bdf6e7e157`
  ```diff
  -return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").decode().strip()
  +return _git(repo, "rev-parse", "--verify", "--QUIET", f"{ref}^{{commit}}").decode().strip()
  ```
- `status_paths` · survived · argument: removed · `bf35d42a18ff4a75`
  ```diff
  -out = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
  +out = _git(repo, "status", "-z", "--untracked-files=all", "--no-renames")
  ```
- `status_paths` · survived · argument: removed · `43eea72872ca0c34`
  ```diff
  -out = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
  +out = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", )
  ```
- `status_paths` · survived · string: upper case · `7362b3075407127d`
  ```diff
  -return sorted(entry[3:].decode("utf-8") for entry in out.split(b"\0") if entry)
  +return sorted(entry[3:].decode("UTF-8") for entry in out.split(b"\0") if entry)
  ```
- `first_commit_adding` · survived · argument: removed · `7f18830c89830436`
  ```diff
  -"--full-history",
  ```
- `first_commit_adding` · survived · argument: removed · `922ea6ea9c174905`
  ```diff
  -"--topo-order",
  ```
- `first_commit_adding` · survived · argument: removed · `1450bd7b3ce2f564`
  ```diff
  -"--diff-filter=A",
  ```
- `first_commit_adding` · survived · argument: removed · `9558293a57707dfb`
  ```diff
  -rev,
  ```
- `first_commit_adding` · survived · argument: removed · `c6a09ab5d7e7fa92`
  ```diff
  -"--",
  ```
- `first_commit_adding` · survived · argument: removed · `326385a9150c0639`
  ```diff
  -path,
  -)
  +)
  ```
- `commits_touching_since` · survived · argument: removed · `effc1805e6c9b6f9`
  ```diff
  -out = _git(repo, "log", *args, rev, "--not", f"{since}^@", "--", path)
  +out = _git(repo, "log", *args, rev, f"{since}^@", "--", path)
  ```
- `commits_touching_since` · survived · argument: removed · `08e6dc132f8d3fcb`
  ```diff
  -out = _git(repo, "log", *args, rev, "--not", f"{since}^@", "--", path)
  +out = _git(repo, "log", *args, rev, "--not", "--", path)
  ```
- `commits_touching_since` · survived · argument: removed · `4994f7beb1b5e6ee`
  ```diff
  -out = _git(repo, "log", *args, rev, "--not", f"{since}^@", "--", path)
  +out = _git(repo, "log", *args, rev, "--not", f"{since}^@", path)
  ```
- `commits_touching_since` · survived · argument: removed · `47594c24d31b4a39`
  ```diff
  -out = _git(repo, "log", *args, rev, "--not", f"{since}^@", "--", path)
  +out = _git(repo, "log", *args, rev, "--not", f"{since}^@", "--", )
  ```
- `parents_since` · survived · argument: removed · `98367048a240ed43`
  ```diff
  -repo, "rev-list", "--topo-order", "--reverse", "--parents", rev, "--not", f"{since}^@"
  +repo, "rev-list", "--reverse", "--parents", rev, "--not", f"{since}^@"
  ```
- `parents_since` · survived · argument: removed · `e4d7dec2833c681e`
  ```diff
  -repo, "rev-list", "--topo-order", "--reverse", "--parents", rev, "--not", f"{since}^@"
  +repo, "rev-list", "--topo-order", "--reverse", "--parents", rev, f"{since}^@"
  ```
- `parents_since` · survived · argument: removed · `481b52adf0334cd4`
  ```diff
  -repo, "rev-list", "--topo-order", "--reverse", "--parents", rev, "--not", f"{since}^@"
  -)
  +repo, "rev-list", "--topo-order", "--reverse", "--parents", rev, "--not", )
  ```
- `manifest_commits` · survived · argument: removed · `72f0ff4985ba8d47`
  ```diff
  -out = _git(repo, "log", "--topo-order", "--reverse", "--format=%H", base, "--", MANIFEST_PATH)
  +out = _git(repo, "log", "--reverse", "--format=%H", base, "--", MANIFEST_PATH)
  ```
- `read_blobs` · survived · argument: replaced by None · `cb3471af785bcae4`
  ```diff
  -return dict(zip(paths, _parse_batch(out, len(paths)), strict=True))
  +return dict(zip(paths, _parse_batch(out, len(paths)), strict=None))
  ```
- `read_blobs` · survived · argument: removed · `10085891c4f6926e`
  ```diff
  -return dict(zip(paths, _parse_batch(out, len(paths)), strict=True))
  +return dict(zip(paths, _parse_batch(out, len(paths)), ))
  ```
- `read_blobs` · survived · True / False swapped · `cf51f63a381f59e7`
  ```diff
  -return dict(zip(paths, _parse_batch(out, len(paths)), strict=True))
  +return dict(zip(paths, _parse_batch(out, len(paths)), strict=False))
  ```
- `_parse_batch` · survived · assignment: value to None · `96952775b72b169a`
  ```diff
  -offset = 0
  +offset = None
  ```
- `_parse_batch` · survived · number · `8a3155e90d728038`
  ```diff
  -offset = 0
  +offset = 1
  ```
- `_parse_batch` · survived · number · `eabbf8abd62c2ffd`
  ```diff
  -offset = header_end + size + 1
  +offset = header_end + size + 2
  ```
- `rename_row` · survived · ternary: condition forced · `6fabafd27e777a97`
  ```diff
  -directory = path.rsplit("/", 1)[0] if "/" in path else "."
  +directory = path.rsplit("/", 1)[0] if ("/" in path) and False else "."
  ```
- `rename_row` · survived · ternary: condition forced · `ccb700f41b27831b`
  ```diff
  -directory = path.rsplit("/", 1)[0] if "/" in path else "."
  +directory = path.rsplit("/", 1)[0] if ("/" in path) or True else "."
  ```
- `rename_row` · survived · argument: removed · `67d45a408242b4c0`
  ```diff
  -directory = path.rsplit("/", 1)[0] if "/" in path else "."
  +directory = path.rsplit("/", )[0] if "/" in path else "."
  ```
- `rename_row` · survived · string method swapped · `1240b5232640bbc5`
  ```diff
  -directory = path.rsplit("/", 1)[0] if "/" in path else "."
  +directory = path.split("/", 1)[0] if "/" in path else "."
  ```
- `rename_row` · survived · number · `81c02748a7580324`
  ```diff
  -directory = path.rsplit("/", 1)[0] if "/" in path else "."
  +directory = path.rsplit("/", 2)[0] if "/" in path else "."
  ```
- `rename_row` · survived · string: XX added · `c9f1a88b860ff9c5`
  ```diff
  -directory = path.rsplit("/", 1)[0] if "/" in path else "."
  +directory = path.rsplit("/", 1)[0] if "XX/XX" in path else "."
  ```
- `rename_row` · survived · is / in negated · `855d896364c73fa9`
  ```diff
  -directory = path.rsplit("/", 1)[0] if "/" in path else "."
  +directory = path.rsplit("/", 1)[0] if "/" not in path else "."
  ```
- `rename_row` · survived · string: XX added · `6f5631445278d8e5`
  ```diff
  -directory = path.rsplit("/", 1)[0] if "/" in path else "."
  +directory = path.rsplit("/", 1)[0] if "/" in path else "XX.XX"
  ```
- `rename_row` · survived · argument: removed · `1b6c8964a595a9d0`
  ```diff
  -deleted = _git(repo, *_DELETED, commit.parent, commit.sha, "--", directory)
  +deleted = _git(repo, *_DELETED, commit.sha, "--", directory)
  ```
- `rename_row` · survived · argument: removed · `17ae7cfd8836468b`
  ```diff
  -deleted = _git(repo, *_DELETED, commit.parent, commit.sha, "--", directory)
  +deleted = _git(repo, *_DELETED, commit.parent, commit.sha, directory)
  ```
- `rename_row` · survived · argument: removed · `450b4548a93d35a0`
  ```diff
  -deleted = _git(repo, *_DELETED, commit.parent, commit.sha, "--", directory)
  +deleted = _git(repo, *_DELETED, commit.parent, commit.sha, "--", )
  ```
- `rename_row` · survived · argument: removed · `84f9050a95360a37`
  ```diff
  -rows = _git(repo, *_RENAMES, commit.parent, commit.sha, "--", path, *sources)
  +rows = _git(repo, *_RENAMES, commit.sha, "--", path, *sources)
  ```
- `rename_row` · survived · argument: removed · `0f9748dac0dd3ec1`
  ```diff
  -return next((row for row in rows.decode().splitlines() if " => " in row), None)
  +return next((row for row in rows.decode().splitlines() if " => " in row), )
  ```
- `topo_commits` · survived · argument: removed · `f9f73870c3d85799`
  ```diff
  -out = _git(repo, _LITERAL, "rev-list", "--topo-order", tip, "--", *paths)
  +out = _git(repo, "rev-list", "--topo-order", tip, "--", *paths)
  ```
- `topo_commits` · survived · argument: removed · `25078a90e6296d0f`
  ```diff
  -out = _git(repo, _LITERAL, "rev-list", "--topo-order", tip, "--", *paths)
  +out = _git(repo, _LITERAL, "rev-list", tip, "--", *paths)
  ```
- `topo_commits` · survived · argument: removed · `f6e9925b1abaead9`
  ```diff
  -out = _git(repo, _LITERAL, "rev-list", "--topo-order", tip, "--", *paths)
  +out = _git(repo, _LITERAL, "rev-list", "--topo-order", tip, "--", )
  ```
- `list_files` · survived · argument: removed · `a45820a9f9311f7c`
  ```diff
  -out = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", *directories)
  +out = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, *directories)
  ```
- `list_files` · survived · argument: removed · `e8c1e5e087885c34`
  ```diff
  -out = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", *directories)
  +out = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", )
  ```
- `list_files` · survived · string: XX added · `4347e73214758439`
  ```diff
  -out = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", *directories)
  +out = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "XX--XX", *directories)
  ```

</details>

<details><summary><code>src/lovspor/identity/history.py</code>: 13</summary>

- `merge_append_only` · survived · string: XX added · `90ad875e27265381`
  ```diff
  -"a per-document history never changes identity (#871)",
  +"XXa per-document history never changes identity (#871)XX",
  ```
- `merge_append_only` · survived · string: upper case · `d2f0d0bd35a5ad5f`
  ```diff
  -"a per-document history never changes identity (#871)",
  +"A PER-DOCUMENT HISTORY NEVER CHANGES IDENTITY (#871)",
  ```
- `read_identity_history` · survived · argument: replaced by None · `8472cfffa376300f`
  ```diff
  -return IdentityHistory.model_validate(json.loads(path.read_text(encoding="utf-8")))
  +return IdentityHistory.model_validate(json.loads(path.read_text(encoding=None)))
  ```
- `read_identity_history` · survived · string: upper case · `bbf194e326a8f752`
  ```diff
  -return IdentityHistory.model_validate(json.loads(path.read_text(encoding="utf-8")))
  +return IdentityHistory.model_validate(json.loads(path.read_text(encoding="UTF-8")))
  ```
- `identity_history_json` · survived · argument: replaced by None · `ed2d79c55e86eae2`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=None, indent=2, ensure_ascii=False) + "\n"
  ```
- `identity_history_json` · survived · argument: replaced by None · `df0cb744dc36e2a0`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, indent=None, ensure_ascii=False) + "\n"
  ```
- `identity_history_json` · survived · argument: replaced by None · `84c917b7caad21ae`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=None) + "\n"
  ```
- `identity_history_json` · survived · argument: removed · `96fc4f3086d60f16`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
  ```
- `identity_history_json` · survived · argument: removed · `70b323c5bcf1401e`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, ensure_ascii=False) + "\n"
  ```
- `identity_history_json` · survived · argument: removed · `45963c540ba9bf42`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, indent=2, ) + "\n"
  ```
- `identity_history_json` · survived · True / False swapped · `a534f6a5871eae60`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=False, indent=2, ensure_ascii=False) + "\n"
  ```
- `identity_history_json` · survived · number · `1df5454861c654f8`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, indent=3, ensure_ascii=False) + "\n"
  ```
- `identity_history_json` · survived · True / False swapped · `e1ca8502cf613e49`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
  ```

</details>

<details><summary><code>src/lovspor/identity/history_marker.py</code>: 27</summary>

- `_readable_history` · survived · argument: replaced by None · `92343366f259b4b5`
  ```diff
  -raise _unproven(repo, "it is not the root of a git repository")
  +raise _unproven(None, "it is not the root of a git repository")
  ```
- `_readable_history` · survived · string: XX added · `938380c931cf804b`
  ```diff
  -raise _unproven(repo, "it is not the root of a git repository")
  +raise _unproven(repo, "XXit is not the root of a git repositoryXX")
  ```
- `_readable_history` · survived · assignment: value to None · `ef7468417386bd1c`
  ```diff
  -detail = "it is a shallow clone whose history is cut off (`git fetch --unshallow`)"
  +detail = None
  ```
- `_readable_history` · survived · string: XX added · `ae1118e78c699243`
  ```diff
  -detail = "it is a shallow clone whose history is cut off (`git fetch --unshallow`)"
  +detail = "XXit is a shallow clone whose history is cut off (`git fetch --unshallow`)XX"
  ```
- `_readable_history` · survived · string: upper case · `4977ac0f74df8df9`
  ```diff
  -detail = "it is a shallow clone whose history is cut off (`git fetch --unshallow`)"
  +detail = "IT IS A SHALLOW CLONE WHOSE HISTORY IS CUT OFF (`GIT FETCH --UNSHALLOW`)"
  ```
- `_readable_history` · survived · argument: replaced by None · `24c63db5f913cf18`
  ```diff
  -raise _unproven(repo, detail)
  +raise _unproven(None, detail)
  ```
- `_readable_history` · survived · argument: replaced by None · `fb1b9b56e42e6766`
  ```diff
  -raise _unproven(repo, detail)
  +raise _unproven(repo, None)
  ```
- `migration_evidence` · survived · string: XX added · `703c1c3d675f65c8`
  ```diff
  -def migration_evidence(repo: Path, rev: str = "HEAD") -> str | None:
  +def migration_evidence(repo: Path, rev: str = "XXHEADXX") -> str | None:
  ```
- `migration_evidence` · survived · string: lower case · `29c69b03359a9411`
  ```diff
  -def migration_evidence(repo: Path, rev: str = "HEAD") -> str | None:
  +def migration_evidence(repo: Path, rev: str = "head") -> str | None:
  ```
- `migration_evidence` · survived · argument: replaced by None · `e068d8da1035a2aa`
  ```diff
  -return _evidence(repo, rev)
  +return _evidence(repo, None)
  ```
- `_evidence` · survived · argument: replaced by None · `4453eb95db1ae213`
  ```diff
  -raise _unproven(repo, f"cannot read the corpus history: {exc}") from exc
  +raise _unproven(None, f"cannot read the corpus history: {exc}") from exc
  ```
- `_restore_source` · survived · argument: replaced by None · `795631b99cc8655f`
  ```diff
  -if read_blob(repo, touched, IDENTITY_FILENAME) is not None:
  +if read_blob(None, touched, IDENTITY_FILENAME) is not None:
  ```
- `_restore_source` · survived · argument: replaced by None · `bc860d0caf1bc237`
  ```diff
  -if read_blob(repo, touched, IDENTITY_FILENAME) is not None:
  +if read_blob(repo, None, IDENTITY_FILENAME) is not None:
  ```
- `_restore_source` · survived · argument: replaced by None · `ffba040168c2ca5f`
  ```diff
  -if read_blob(repo, touched, IDENTITY_FILENAME) is not None:
  +if read_blob(repo, touched, None) is not None:
  ```
- `refuse_downgrade` · survived · argument: replaced by None · `bccaa26705c99147`
  ```diff
  -raise _unproven(repo, f"the commit that last touched it is unreadable: {exc}") from exc
  +raise _unproven(None, f"the commit that last touched it is unreadable: {exc}") from exc
  ```
- `refuse_downgrade` · survived · argument: replaced by None · `f60ebc2c01ca0d9b`
  ```diff
  -raise _unproven(repo, f"the commit that last touched it is unreadable: {exc}") from exc
  +raise _unproven(repo, None) from exc
  ```
- `refuse_downgrade` · survived · argument: removed · `952541ac4c8133f3`
  ```diff
  -raise _unproven(repo, f"the commit that last touched it is unreadable: {exc}") from exc
  +raise _unproven(f"the commit that last touched it is unreadable: {exc}") from exc
  ```
- `refuse_downgrade` · survived · argument: removed · `44cb4c87d6a10781`
  ```diff
  -raise _unproven(repo, f"the commit that last touched it is unreadable: {exc}") from exc
  +raise _unproven(repo, ) from exc
  ```
- `_document_count` · survived · string: upper case · `07f1d4984f77a93f`
  ```diff
  -data = json.loads(blob.decode("utf-8"))
  +data = json.loads(blob.decode("UTF-8"))
  ```
- `_document_count` · survived · argument: replaced by None · `dd7a2f8102376b4d`
  ```diff
  -raise IdentitySchemaError(f"{where}: unreadable: {exc}") from exc
  +raise IdentitySchemaError(None) from exc
  ```
- `_document_count` · survived · ternary: condition forced · `ef27886700eaeb4a`
  ```diff
  -documents = data.get("documents") if isinstance(data, dict) else None
  +documents = data.get("documents") if (isinstance(data, dict)) or True else None
  ```
- `_document_count` · survived · argument: replaced by None · `e9fa8011d668f6c7`
  ```diff
  -raise IdentitySchemaError(f"{where}: no documents mapping")
  +raise IdentitySchemaError(None)
  ```
- `committed_documents` · survived · assignment: value to None · `bcfee04a9fa56eff`
  ```diff
  -where = f"{repo}: HEAD:{MANIFEST_PATH}"
  +where = None
  ```
- `committed_documents` · survived · argument: replaced by None · `7f58b5db98e2a448`
  ```diff
  -raise IdentitySchemaError(f"{where}: cannot be read: {exc}") from exc
  +raise IdentitySchemaError(None) from exc
  ```
- `committed_documents` · survived · ternary: condition forced · `bd94e307df613ebf`
  ```diff
  -return 0 if blob is None else _document_count(blob, where)
  +return 0 if (blob is None) and False else _document_count(blob, where)
  ```
- `committed_documents` · survived · number · `1d94ba1f2425d077`
  ```diff
  -return 0 if blob is None else _document_count(blob, where)
  +return 1 if blob is None else _document_count(blob, where)
  ```
- `committed_documents` · survived · argument: replaced by None · `a77490ddc0ab9f24`
  ```diff
  -return 0 if blob is None else _document_count(blob, where)
  +return 0 if blob is None else _document_count(blob, None)
  ```

</details>

<details><summary><code>src/lovspor/identity/migration.py</code>: 15</summary>

- `restored_record` · survived · assignment: value to None · `5abeb6fac8816c21`
  ```diff
  -missing = seen.missing_since or base_commit
  +missing = None
  ```
- `restored_record` · survived · operator: and / or · `734c852deb201976`
  ```diff
  -missing = seen.missing_since or base_commit
  +missing = seen.missing_since and base_commit
  ```
- `restored_record` · survived · argument: replaced by None · `40999b40cb82c6c7`
  ```diff
  -title=last.title,
  +title=None,
  ```
- `restored_record` · survived · argument: removed · `87e4f322f6805107`
  ```diff
  -title=last.title,
  ```
- `restored_record` · survived · argument: replaced by None · `f6dbe6faca786be7`
  ```diff
  -removed_reason=RESTORED_REASON.format(seen=seen.commit, missing=missing),
  +removed_reason=RESTORED_REASON.format(seen=seen.commit, missing=None),
  ```
- `restored_tombstones` · survived · argument: replaced by None · `3ebb04150ed6c688`
  ```diff
  -record = restored_record(doc_id, walk, base_commit)
  +record = restored_record(doc_id, walk, None)
  ```
- `restored_tombstones` · survived · argument: replaced by None · `e92de53e7f30e8c3`
  ```diff
  -title=record.title,
  +title=None,
  ```
- `suffix_note` · survived · string: XX added · `721cb3bb33642a6e`
  ```diff
  -f"{slug} (owned by {registry.owner_of(directory, slug) or 'nobody; held by a sibling'})"
  +f"{slug} (owned by {registry.owner_of(directory, slug) or 'XXnobody; held by a siblingXX'})"
  ```
- `suffix_note` · survived · string: upper case · `2ba17c6e3267be75`
  ```diff
  -f"{slug} (owned by {registry.owner_of(directory, slug) or 'nobody; held by a sibling'})"
  +f"{slug} (owned by {registry.owner_of(directory, slug) or 'NOBODY; HELD BY A SIBLING'})"
  ```
- `u1_renames` · survived · assignment: value to None · `c322ce50a0285124`
  ```diff
  -note = suffix_note(registry, directory, rename)
  +note = None
  ```
- `u1_renames` · survived · argument: replaced by None · `5bfc09abbc15d8ae`
  ```diff
  -note = suffix_note(registry, directory, rename)
  +note = suffix_note(None, directory, rename)
  ```
- `u1_renames` · survived · argument: replaced by None · `f81bb5256385aa85`
  ```diff
  -note = suffix_note(registry, directory, rename)
  +note = suffix_note(registry, None, rename)
  ```
- `u1_renames` · survived · argument: replaced by None · `e5447267f522dc23`
  ```diff
  -renames.append(rename.model_copy(update={"suffix_note": note}))
  +renames.append(rename.model_copy(update=None))
  ```
- `u1_renames` · survived · string: XX added · `159563e513b0e11f`
  ```diff
  -renames.append(rename.model_copy(update={"suffix_note": note}))
  +renames.append(rename.model_copy(update={"XXsuffix_noteXX": note}))
  ```
- `u1_renames` · survived · string: upper case · `90ee1efe9e911df5`
  ```diff
  -renames.append(rename.model_copy(update={"suffix_note": note}))
  +renames.append(rename.model_copy(update={"SUFFIX_NOTE": note}))
  ```

</details>

<details><summary><code>src/lovspor/identity/moves.py</code>: 6</summary>

- `_quoted` · survived · argument: replaced by None · `0a0aa6a3daa09dcd`
  ```diff
  -return json.dumps(value, ensure_ascii=False)
  +return json.dumps(value, ensure_ascii=None)
  ```
- `_quoted` · survived · argument: removed · `c6d040e12cbbd0f5`
  ```diff
  -return json.dumps(value, ensure_ascii=False)
  +return json.dumps(value, )
  ```
- `_quoted` · survived · True / False swapped · `b826cda6d8d48ec1`
  ```diff
  -return json.dumps(value, ensure_ascii=False)
  +return json.dumps(value, ensure_ascii=True)
  ```
- `_markdown_move` · survived · argument: replaced by None · `93bd06621699b425`
  ```diff
  -raise IdentityMigrationError(f"{source}: no Markdown at the base")
  +raise IdentityMigrationError(None)
  ```
- `_markdown_move` · survived · string: upper case · `286a7a492e4adddd`
  ```diff
  -text = rewrite_slug(blob.decode("utf-8"), rename, source)
  +text = rewrite_slug(blob.decode("UTF-8"), rename, source)
  ```
- `_markdown_move` · survived · string: upper case · `9cd84488a752d0ea`
  ```diff
  -digest = sha256_bytes(text.encode("utf-8"))
  +digest = sha256_bytes(text.encode("UTF-8"))
  ```

</details>

<details><summary><code>src/lovspor/identity/plan.py</code>: 22</summary>

- `_json_blob` · survived · string: upper case · `2cccc2cd84c1bdea`
  ```diff
  -return json.loads(blob.decode("utf-8"))
  +return json.loads(blob.decode("UTF-8"))
  ```
- `_read_manifest` · survived · argument: replaced by None · `dcd4571d438815f0`
  ```diff
  -return Manifest.model_validate(_json_blob(blob, f"{commit}:{MANIFEST_PATH}"))
  +return Manifest.model_validate(_json_blob(blob, None))
  ```
- `_read_identity_schema` · survived · assignment: value to None · `0553fcb648744e6a`
  ```diff
  -where = f"{commit}:{IDENTITY_FILENAME}"
  +where = None
  ```
- `_read_identity_schema` · survived · argument: replaced by None · `41b17a90f7015edc`
  ```diff
  -schema = parse_declaration(read_blob(repo, commit, IDENTITY_FILENAME), where)
  +schema = parse_declaration(read_blob(repo, commit, IDENTITY_FILENAME), None)
  ```
- `_read_identity_schema` · survived · argument: replaced by None · `c8b7ead58d67554d`
  ```diff
  -require_consistent(schema, registry is not None, commit)
  +require_consistent(schema, registry is not None, None)
  ```
- `_history_doc_id` · survived · string: XX added · `07ef3ddb97ec35b0`
  ```diff
  -data = json.loads((blob or b"").decode("utf-8"))
  +data = json.loads((blob or b"XXXX").decode("utf-8"))
  ```
- `_history_doc_id` · survived · string: upper case · `3f60a77b485531d9`
  ```diff
  -data = json.loads((blob or b"").decode("utf-8"))
  +data = json.loads((blob or b"").decode("UTF-8"))
  ```
- `_history_doc_id` · survived · ternary: condition forced · `e7f0a7b75a5e8493`
  ```diff
  -return value if isinstance(value, str) else None
  +return value if (isinstance(value, str)) or True else None
  ```
- `_require_covered` · survived · argument: replaced by None · `e0c5265bddb9039c`
  ```diff
  -require_registry_covers(registry, manifest, commit)
  +require_registry_covers(registry, manifest, None)
  ```
- `_require_covered` · survived · argument: removed · `d1ddefd4ca644ae2`
  ```diff
  -require_registry_history(repo, registry, commit)
  +require_registry_history(repo, registry, )
  ```
- `resolve_threshold` · survived · operator: arithmetic · `f6ee788277b03349`
  ```diff
  -return Threshold(ref=None, commit=None, position=-1, problem=None)
  +return Threshold(ref=None, commit=None, position=+1, problem=None)
  ```
- `resolve_threshold` · survived · number · `2726da76183dcf34`
  ```diff
  -return Threshold(ref=None, commit=None, position=-1, problem=None)
  +return Threshold(ref=None, commit=None, position=-2, problem=None)
  ```
- `resolve_threshold` · survived · argument: replaced by None · `21ab575ede652338`
  ```diff
  -return Threshold(ref=ref, commit=None, position=-1, problem=f"{ref} is not a commit")
  +return Threshold(ref=None, commit=None, position=-1, problem=f"{ref} is not a commit")
  ```
- `resolve_threshold` · survived · operator: arithmetic · `59821ae0e0785c40`
  ```diff
  -return Threshold(ref=ref, commit=None, position=-1, problem=f"{ref} is not a commit")
  +return Threshold(ref=ref, commit=None, position=+1, problem=f"{ref} is not a commit")
  ```
- `resolve_threshold` · survived · number · `3f071f2a14a18a3d`
  ```diff
  -return Threshold(ref=ref, commit=None, position=-1, problem=f"{ref} is not a commit")
  +return Threshold(ref=ref, commit=None, position=-2, problem=f"{ref} is not a commit")
  ```
- `resolve_threshold` · survived · argument: replaced by None · `088c67dafa8610e4`
  ```diff
  -return Threshold(ref=ref, commit=commit, position=-1, problem=problem)
  +return Threshold(ref=None, commit=commit, position=-1, problem=problem)
  ```
- `resolve_threshold` · survived · argument: replaced by None · `c0990d893776dc98`
  ```diff
  -return Threshold(ref=ref, commit=commit, position=-1, problem=problem)
  +return Threshold(ref=ref, commit=None, position=-1, problem=problem)
  ```
- `resolve_threshold` · survived · operator: arithmetic · `fb70004acc3118eb`
  ```diff
  -return Threshold(ref=ref, commit=commit, position=-1, problem=problem)
  +return Threshold(ref=ref, commit=commit, position=+1, problem=problem)
  ```
- `resolve_threshold` · survived · number · `60e8c55dc05f5b2b`
  ```diff
  -return Threshold(ref=ref, commit=commit, position=-1, problem=problem)
  +return Threshold(ref=ref, commit=commit, position=-2, problem=problem)
  ```
- `_planned_manifest` · survived · argument: replaced by None · `18200da3bc19255f`
  ```diff
  -documents[doc_id] = restored_record(doc_id, walk, base.commit)
  +documents[doc_id] = restored_record(doc_id, walk, None)
  ```
- `_repair` · survived · argument: replaced by None · `f020ad6f9bf36c84`
  ```diff
  -restored=restored_tombstones(walk, base.manifest, base.commit),
  +restored=restored_tombstones(walk, base.manifest, None),
  ```
- `_repair_targets` · survived · break / continue swapped · `98a96d05caa4ffe7`
  ```diff
  -continue
  +break
  ```

</details>

<details><summary><code>src/lovspor/identity/registry.py</code>: 17</summary>

- `ensure_append_only` · survived · string: XX added · `3594ab9e39fc51ce`
  ```diff
  -"or change — a registry entry is never deleted or rewritten (#871)",
  +"XXor change — a registry entry is never deleted or rewritten (#871)XX",
  ```
- `read_registry` · survived · argument: replaced by None · `7e84d9ac17bbdcb8`
  ```diff
  -return SlugRegistry.model_validate(json.loads(path.read_text(encoding="utf-8")))
  +return SlugRegistry.model_validate(json.loads(path.read_text(encoding=None)))
  ```
- `read_registry` · survived · string: upper case · `092f9f03599666b9`
  ```diff
  -return SlugRegistry.model_validate(json.loads(path.read_text(encoding="utf-8")))
  +return SlugRegistry.model_validate(json.loads(path.read_text(encoding="UTF-8")))
  ```
- `registry_json` · survived · argument: replaced by None · `b4e1af632cb420c3`
  ```diff
  -data = registry.model_dump(mode="json")
  +data = registry.model_dump(mode=None)
  ```
- `registry_json` · survived · string: XX added · `b10aa980661af432`
  ```diff
  -data = registry.model_dump(mode="json")
  +data = registry.model_dump(mode="XXjsonXX")
  ```
- `registry_json` · survived · string: upper case · `719c7bf13d20eec9`
  ```diff
  -data = registry.model_dump(mode="json")
  +data = registry.model_dump(mode="JSON")
  ```
- `registry_json` · survived · argument: replaced by None · `cf4cc30aea29589d`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=None, indent=2, ensure_ascii=False) + "\n"
  ```
- `registry_json` · survived · argument: replaced by None · `b470bbc234645330`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, indent=None, ensure_ascii=False) + "\n"
  ```
- `registry_json` · survived · argument: replaced by None · `c7a7dde05877aef3`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=None) + "\n"
  ```
- `registry_json` · survived · argument: removed · `027f3d86708e11b1`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
  ```
- `registry_json` · survived · argument: removed · `48b8266773129c32`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, ensure_ascii=False) + "\n"
  ```
- `registry_json` · survived · True / False swapped · `c123c5ba0af0f204`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=False, indent=2, ensure_ascii=False) + "\n"
  ```
- `registry_json` · survived · number · `7be12f8696b25f2f`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, indent=3, ensure_ascii=False) + "\n"
  ```
- `corpus_identity` · survived · assignment: None to empty string · `d9a1717fea857e68`
  ```diff
  -registry = None
  +registry = ""
  ```
- `corpus_identity` · survived · argument: replaced by None · `2f73f772389bb69e`
  ```diff
  -require_consistent(schema, (repo / REGISTRY_FILENAME).exists(), str(repo))
  +require_consistent(schema, (repo / REGISTRY_FILENAME).exists(), None)
  ```
- `corpus_identity` · survived · argument: replaced by None · `89302acc0c3815b7`
  ```diff
  -require_consistent(schema, (repo / REGISTRY_FILENAME).exists(), str(repo))
  +require_consistent(schema, (repo / REGISTRY_FILENAME).exists(), str(None))
  ```
- `registered_ownership` · survived · argument: replaced by None · `2dfb41a6ecfd9b95`
  ```diff
  -merged[dataset] = {**merged.get(dataset, {}), **identity.registry.ownership(directory)}
  +merged[dataset] = {**merged.get(None, {}), **identity.registry.ownership(directory)}
  ```

</details>

<details><summary><code>src/lovspor/identity/registry_history.py</code>: 31</summary>

- `_require_full_history` · survived · argument: replaced by None · `e7ddaf7c0d3ccef5`
  ```diff
  -raise _unreadable(repo, "it is not the root of a git repository")
  +raise _unreadable(None, "it is not the root of a git repository")
  ```
- `_require_full_history` · survived · string: XX added · `d1653db5ef0526b4`
  ```diff
  -raise _unreadable(repo, "it is not the root of a git repository")
  +raise _unreadable(repo, "XXit is not the root of a git repositoryXX")
  ```
- `_require_full_history` · survived · assignment: value to None · `be8cb05028d6bc0f`
  ```diff
  -detail = "it is a shallow clone whose history is cut off (`git fetch --unshallow`)"
  +detail = None
  ```
- `_require_full_history` · survived · string: XX added · `ce67f37b1b0e3e33`
  ```diff
  -detail = "it is a shallow clone whose history is cut off (`git fetch --unshallow`)"
  +detail = "XXit is a shallow clone whose history is cut off (`git fetch --unshallow`)XX"
  ```
- `_require_full_history` · survived · string: upper case · `2c712a5dcf0e5d71`
  ```diff
  -detail = "it is a shallow clone whose history is cut off (`git fetch --unshallow`)"
  +detail = "IT IS A SHALLOW CLONE WHOSE HISTORY IS CUT OFF (`GIT FETCH --UNSHALLOW`)"
  ```
- `_require_full_history` · survived · argument: replaced by None · `48a1ebcc3c4fd9df`
  ```diff
  -raise _unreadable(repo, detail)
  +raise _unreadable(None, detail)
  ```
- `_require_full_history` · survived · argument: replaced by None · `1fb413533f453af6`
  ```diff
  -raise _unreadable(repo, detail)
  +raise _unreadable(repo, None)
  ```
- `_slug_entries` · survived · string: XX added · `96a139b4b2529fd6`
  ```diff
  -data = json.loads((blob or b"{}").decode("utf-8"))
  +data = json.loads((blob or b"XX{}XX").decode("utf-8"))
  ```
- `_slug_entries` · survived · string: upper case · `9b80623171269183`
  ```diff
  -data = json.loads((blob or b"{}").decode("utf-8"))
  +data = json.loads((blob or b"{}").decode("UTF-8"))
  ```
- `_slug_entries` · survived · ternary: condition forced · `34d9dee7ce112467`
  ```diff
  -slugs = data.get("slugs") if isinstance(data, dict) else None
  +slugs = data.get("slugs") if (isinstance(data, dict)) or True else None
  ```
- `_ancestor_bits` · survived · number · `b001b8f538cab187`
  ```diff
  -mask = 0
  +mask = 1
  ```
- `_ancestor_bits` · survived · augmented assignment to plain · `ebbc0e3522aa1aa9`
  ```diff
  -mask |= contains.get(parent, 0) | bit.get(parent, 0)
  +mask = contains.get(parent, 0) | bit.get(parent, 0)
  ```
- `_ancestor_bits` · survived · number · `89700883077cbfa8`
  ```diff
  -mask |= contains.get(parent, 0) | bit.get(parent, 0)
  +mask |= contains.get(parent, 1) | bit.get(parent, 0)
  ```
- `_ancestor_bits` · survived · number · `ed9f2722765c384b`
  ```diff
  -mask |= contains.get(parent, 0) | bit.get(parent, 0)
  +mask |= contains.get(parent, 0) | bit.get(parent, 1)
  ```
- `_divergence` · survived · string: XX added · `5d64b7a3b019898c`
  ```diff
  -adds = " and as ".join(
  +adds = "XX and as XX".join(
  ```
- `_divergence` · survived · string: upper case · `8f9ade7a84a0ef6e`
  ```diff
  -adds = " and as ".join(
  +adds = " AND AS ".join(
  ```
- `_check_unrelated` · survived · operator: bitwise · `7c31833f7cf2c98e`
  ```diff
  -unrelated = walk.absorbed & ~walk.ancestors[commit]
  +unrelated = walk.absorbed | ~walk.ancestors[commit]
  ```
- `_check_unrelated` · survived · argument: replaced by None · `c1bedfa52f320587`
  ```diff
  -held = walk.first.get(directory, {})
  +held = walk.first.get(directory, None)
  ```
- `_check_unrelated` · survived · argument: removed · `b87f44409d17b450`
  ```diff
  -held = walk.first.get(directory, {})
  +held = walk.first.get(directory, )
  ```
- `_check_unrelated` · survived · operator: bitwise · `0166623d98ef76ee`
  ```diff
  -if not unrelated & walk.bit[first_commit]:
  +if not unrelated | walk.bit[first_commit]:
  ```
- `_absorb` · survived · assignment: value to None · `71333377a1ecda68`
  ```diff
  -linear = not walk.absorbed & ~walk.ancestors[commit]
  +linear = None
  ```
- `_absorb` · survived · operator: bitwise · `48740d7e97eb5300`
  ```diff
  -linear = not walk.absorbed & ~walk.ancestors[commit]
  +linear = not walk.absorbed | ~walk.ancestors[commit]
  ```
- `_absorb` · survived · not / ~ removed · `a5570a9bafc363b6`
  ```diff
  -linear = not walk.absorbed & ~walk.ancestors[commit]
  +linear = not walk.absorbed & walk.ancestors[commit]
  ```
- `_violations` · survived · argument: replaced by None · `c5bd786c8bdff26c`
  ```diff
  -held = registry.slugs.get(directory, {})
  +held = registry.slugs.get(directory, None)
  ```
- `_violations` · survived · argument: removed · `520c0fcb58f4e161`
  ```diff
  -held = registry.slugs.get(directory, {})
  +held = registry.slugs.get(directory, )
  ```
- `require_registry_history` · survived · argument: replaced by None · `97256f5f642de6ba`
  ```diff
  -raise _unreadable(repo, f"its history cannot be read: {exc}") from exc
  +raise _unreadable(None, f"its history cannot be read: {exc}") from exc
  ```
- `require_registry_history` · survived · argument: replaced by None · `d01fb888c9012e2d`
  ```diff
  -raise _unreadable(repo, f"its history cannot be read: {exc}") from exc
  +raise _unreadable(repo, None) from exc
  ```
- `require_registry_history` · survived · argument: removed · `e02bb7288b1729ab`
  ```diff
  -raise _unreadable(repo, f"its history cannot be read: {exc}") from exc
  +raise _unreadable(f"its history cannot be read: {exc}") from exc
  ```
- `require_registry_history` · survived · argument: removed · `aa6b2d8a711fde46`
  ```diff
  -raise _unreadable(repo, f"its history cannot be read: {exc}") from exc
  +raise _unreadable(repo, ) from exc
  ```
- `verified_corpus_identity` · survived · argument: replaced by None · `2b27f2bfd401f8c5`
  ```diff
  -require_registry_covers(identity.registry, read_manifest(manifest_path), str(repo))
  +require_registry_covers(identity.registry, read_manifest(manifest_path), None)
  ```
- `verified_corpus_identity` · survived · argument: replaced by None · `c30977f1820cc2d6`
  ```diff
  -require_registry_covers(identity.registry, read_manifest(manifest_path), str(repo))
  +require_registry_covers(identity.registry, read_manifest(manifest_path), str(None))
  ```

</details>

<details><summary><code>src/lovspor/identity/repair.py</code>: 11</summary>

- `_doc` · survived · argument: removed · `c80d1ade355ad98b`
  ```diff
  -return None if blob is None else frontmatter_id(blob.decode("utf-8", errors="replace"))
  +return None if blob is None else frontmatter_id(blob.decode(errors="replace"))
  ```
- `_doc` · survived · argument: removed · `e5cbc3b44a649e17`
  ```diff
  -return None if blob is None else frontmatter_id(blob.decode("utf-8", errors="replace"))
  +return None if blob is None else frontmatter_id(blob.decode("utf-8", ))
  ```
- `_doc` · survived · string: upper case · `55dedbd23c9fbd75`
  ```diff
  -return None if blob is None else frontmatter_id(blob.decode("utf-8", errors="replace"))
  +return None if blob is None else frontmatter_id(blob.decode("UTF-8", errors="replace"))
  ```
- `_doc` · survived · string: XX added · `b294b4d6fa45618e`
  ```diff
  -return None if blob is None else frontmatter_id(blob.decode("utf-8", errors="replace"))
  +return None if blob is None else frontmatter_id(blob.decode("utf-8", errors="XXreplaceXX"))
  ```
- `_doc` · survived · string: upper case · `7c8b0508aef9e16d`
  ```diff
  -return None if blob is None else frontmatter_id(blob.decode("utf-8", errors="replace"))
  +return None if blob is None else frontmatter_id(blob.decode("utf-8", errors="REPLACE"))
  ```
- `_steps` · survived · operator: and / or · `925da8453ef643f4`
  ```diff
  -for spec in (f"{c.sha}:{segment.path}", f"{c.parent or c.sha + '^'}:{segment.path}")
  +for spec in (f"{c.sha}:{segment.path}", f"{c.parent and c.sha + '^'}:{segment.path}")
  ```
- `_steps` · survived · string: XX added · `eeeba6fe72cfcbc8`
  ```diff
  -for spec in (f"{c.sha}:{segment.path}", f"{c.parent or c.sha + '^'}:{segment.path}")
  +for spec in (f"{c.sha}:{segment.path}", f"{c.parent or c.sha + 'XX^XX'}:{segment.path}")
  ```
- `_moved_in` · survived · operator: and / or · `1649d78f6d480b6d`
  ```diff
  -if step.after is None or step.before is not None:
  +if step.after is None and step.before is not None:
  ```
- `_moved_in` · survived · ternary: condition forced · `43d4f8dfe200a67d`
  ```diff
  -return (from_path, row) if from_path else None
  +return (from_path, row) if (from_path) or True else None
  ```
- `trace_path` · survived · augmented assignment to plain · `b159f69486785b3f`
  ```diff
  -seen |= {s.path for s in fresh}
  +seen = {s.path for s in fresh}
  ```
- `trace_path` · survived · operator: augmented · `d59907aad271d539`
  ```diff
  -seen |= {s.path for s in fresh}
  +seen &= {s.path for s in fresh}
  ```

</details>

<details><summary><code>src/lovspor/identity/repair_plan.py</code>: 27</summary>

- `_base_file_events` · survived · string: XX added · `1a96c291b110f7d1`
  ```diff
  -data = json.loads((blob or b"{}").decode("utf-8"))
  +data = json.loads((blob or b"XX{}XX").decode("utf-8"))
  ```
- `_base_file_events` · survived · string: upper case · `030c0385de07895e`
  ```diff
  -data = json.loads((blob or b"{}").decode("utf-8"))
  +data = json.loads((blob or b"{}").decode("UTF-8"))
  ```
- `_base_file_events` · survived · argument: replaced by None · `16c55847183b91f3`
  ```diff
  -type=e.get("type"),
  +type=None,
  ```
- `_base_file_events` · survived · argument: replaced by None · `f749a28d53e7aec4`
  ```diff
  -type=e.get("type"),
  +type=e.get(None),
  ```
- `_base_file_events` · survived · string: XX added · `a057564384e21ae0`
  ```diff
  -type=e.get("type"),
  +type=e.get("XXtypeXX"),
  ```
- `_base_file_events` · survived · string: upper case · `94df84f48174e849`
  ```diff
  -type=e.get("type"),
  +type=e.get("TYPE"),
  ```
- `_identity_history` · survived · argument: replaced by None · `02501bf2e699679a`
  ```diff
  -ordered = sorted(events, key=lambda e: e.event.date, reverse=True)
  +ordered = sorted(events, key=lambda e: e.event.date, reverse=None)
  ```
- `_identity_history` · survived · argument: removed · `843140467867c055`
  ```diff
  -ordered = sorted(events, key=lambda e: e.event.date, reverse=True)
  +ordered = sorted(events, key=lambda e: e.event.date, )
  ```
- `_identity_history` · survived · True / False swapped · `e9fb602120cf0a46`
  ```diff
  -ordered = sorted(events, key=lambda e: e.event.date, reverse=True)
  +ordered = sorted(events, key=lambda e: e.event.date, reverse=False)
  ```
- `_identity_history` · survived · argument: replaced by None · `158f559ecc884b33`
  ```diff
  -raise IdentityMigrationError(f"cannot hold the history of {doc_id!r}: {exc}") from exc
  +raise IdentityMigrationError(None) from exc
  ```
- `_prior` · survived · string: upper case · `8cb25aebe5541ec5`
  ```diff
  -return IdentityHistory.model_validate(json.loads(blob.decode("utf-8")))
  +return IdentityHistory.model_validate(json.loads(blob.decode("UTF-8")))
  ```
- `_prior_conflicts` · survived · argument: replaced by None · `0a7a052dad05414a`
  ```diff
  -path=e.path,
  +path=None,
  ```
- `_by_id_plan` · survived · argument: replaced by None · `dfd53d628b127f86`
  ```diff
  -for path, ((directory, doc_id), events) in zip(paths, groups.items(), strict=True):
  +for path, ((directory, doc_id), events) in zip(paths, groups.items(), strict=None):
  ```
- `_by_id_plan` · survived · argument: removed · `6ba9f9a7d0d53f20`
  ```diff
  -for path, ((directory, doc_id), events) in zip(paths, groups.items(), strict=True):
  +for path, ((directory, doc_id), events) in zip(paths, groups.items(), ):
  ```
- `_by_id_plan` · survived · True / False swapped · `6d46c1a7745562a0`
  ```diff
  -for path, ((directory, doc_id), events) in zip(paths, groups.items(), strict=True):
  +for path, ((directory, doc_id), events) in zip(paths, groups.items(), strict=False):
  ```
- `_by_id_plan` · survived · argument: replaced by None · `2e671117c2db5e72`
  ```diff
  -prior = _prior(blobs[path], f"{commit}:{path}")
  +prior = _prior(blobs[path], None)
  ```
- `_by_id_plan` · survived · augmented assignment to plain · `1ef3ebb5f593f008`
  ```diff
  -conflicts += _prior_conflicts(path, prior, directory, owners)
  +conflicts = _prior_conflicts(path, prior, directory, owners)
  ```
- `_invariant` · survived · argument: replaced by None · `afd91d9653b84242`
  ```diff
  -f"{directory}: {commit} at {path} is in {sorted(holders.get(key, set()))}, "
  +f"{directory}: {commit} at {path} is in {sorted(holders.get(None, set()))}, "
  ```
- `_invariant` · survived · argument: replaced by None · `bcddf69f01d33dc0`
  ```diff
  -f"{directory}: {commit} at {path} is in {sorted(holders.get(key, set()))}, "
  +f"{directory}: {commit} at {path} is in {sorted(holders.get(key, None))}, "
  ```
- `_invariant` · survived · argument: removed · `9d69a8af24bbe559`
  ```diff
  -f"{directory}: {commit} at {path} is in {sorted(holders.get(key, set()))}, "
  +f"{directory}: {commit} at {path} is in {sorted(holders.get(key, ))}, "
  ```
- `_invariant` · survived · argument: replaced by None · `e2aba996f7719c75`
  ```diff
  -if holders.get(key, set()) != {doc_id}
  +if holders.get(key, None) != {doc_id}
  ```
- `_invariant` · survived · argument: removed · `e86b903ab489178c`
  ```diff
  -if holders.get(key, set()) != {doc_id}
  +if holders.get(key, ) != {doc_id}
  ```
- `_renamed_conflicts` · survived · string: XX added · `53a178c4bb20b98c`
  ```diff
  -file=path, commit="", path=None, doc_in_file=named, attributed_to=doc_id
  +file=path, commit="XXXX", path=None, doc_in_file=named, attributed_to=doc_id
  ```
- `_named_doc` · survived · string: XX added · `97c86c66dee70483`
  ```diff
  -data = json.loads((blob or b"").decode("utf-8"))
  +data = json.loads((blob or b"XXXX").decode("utf-8"))
  ```
- `_named_doc` · survived · string: upper case · `830099dc0b2e6de7`
  ```diff
  -data = json.loads((blob or b"").decode("utf-8"))
  +data = json.loads((blob or b"").decode("UTF-8"))
  ```
- `_named_doc` · survived · ternary: condition forced · `8b5487f9628ab5d8`
  ```diff
  -return value if isinstance(value, str) else None
  +return value if (isinstance(value, str)) or True else None
  ```
- `_changed` · survived · string: upper case · `c71e8d395a67bf28`
  ```diff
  -return sorted(path for path, text in files.items() if blobs[path] != text.encode("utf-8"))
  +return sorted(path for path, text in files.items() if blobs[path] != text.encode("UTF-8"))
  ```

</details>

<details><summary><code>src/lovspor/identity/repair_report.py</code>: 63</summary>

- `_role` · survived · ternary: condition forced · `2eef80f7ecdb45c4`
  ```diff
  -return f"renamed to {renamed[doc_id]}" if doc_id in renamed else "other holder"
  +return f"renamed to {renamed[doc_id]}" if (doc_id in renamed) and False else "other holder"
  ```
- `_role` · survived · string: XX added · `b0f3eef60e51d447`
  ```diff
  -return f"renamed to {renamed[doc_id]}" if doc_id in renamed else "other holder"
  +return f"renamed to {renamed[doc_id]}" if doc_id in renamed else "XXother holderXX"
  ```
- `_role` · survived · string: upper case · `b0f40c2312103585`
  ```diff
  -return f"renamed to {renamed[doc_id]}" if doc_id in renamed else "other holder"
  +return f"renamed to {renamed[doc_id]}" if doc_id in renamed else "OTHER HOLDER"
  ```
- `_event_rows` · survived · string: XX added · `19ea1411bb0a11b6`
  ```diff
  -"directory": r.target.directory,
  +"XXdirectoryXX": r.target.directory,
  ```
- `_event_rows` · survived · string: upper case · `8bb120fc6e0cb86a`
  ```diff
  -"directory": r.target.directory,
  +"DIRECTORY": r.target.directory,
  ```
- `_event_rows` · survived · string: XX added · `555fe11e98566715`
  ```diff
  -"slug": r.target.slug,
  +"XXslugXX": r.target.slug,
  ```
- `_event_rows` · survived · string: upper case · `76998d45cb8eceab`
  ```diff
  -"slug": r.target.slug,
  +"SLUG": r.target.slug,
  ```
- `_event_rows` · survived · argument: replaced by None · `510073e16c09a894`
  ```diff
  -**e.model_dump(mode="json", exclude={"event"}),
  +**e.model_dump(mode=None, exclude={"event"}),
  ```
- `_event_rows` · survived · argument: replaced by None · `aaff231c87ade9e1`
  ```diff
  -**e.model_dump(mode="json", exclude={"event"}),
  +**e.model_dump(mode="json", exclude=None),
  ```
- `_event_rows` · survived · argument: removed · `a74401fd9509b5ac`
  ```diff
  -**e.model_dump(mode="json", exclude={"event"}),
  +**e.model_dump(exclude={"event"}),
  ```
- `_event_rows` · survived · argument: removed · `d7cbd0fc685c8958`
  ```diff
  -**e.model_dump(mode="json", exclude={"event"}),
  +**e.model_dump(mode="json", ),
  ```
- `_event_rows` · survived · string: XX added · `a9bd6a9cdbea5d65`
  ```diff
  -**e.model_dump(mode="json", exclude={"event"}),
  +**e.model_dump(mode="XXjsonXX", exclude={"event"}),
  ```
- `_event_rows` · survived · string: upper case · `c40f745c1537a653`
  ```diff
  -**e.model_dump(mode="json", exclude={"event"}),
  +**e.model_dump(mode="JSON", exclude={"event"}),
  ```
- `_event_rows` · survived · string: XX added · `176cafd9603b05ef`
  ```diff
  -**e.model_dump(mode="json", exclude={"event"}),
  +**e.model_dump(mode="json", exclude={"XXeventXX"}),
  ```
- `_event_rows` · survived · string: upper case · `3e8e2e3122b67bb7`
  ```diff
  -**e.model_dump(mode="json", exclude={"event"}),
  +**e.model_dump(mode="json", exclude={"EVENT"}),
  ```
- `_event_rows` · survived · string: XX added · `a29a4bbb1e7e48cc`
  ```diff
  -"type": e.event.type,
  +"XXtypeXX": e.event.type,
  ```
- `_event_rows` · survived · string: upper case · `7881f2ced852b11e`
  ```diff
  -"type": e.event.type,
  +"TYPE": e.event.type,
  ```
- `_event_rows` · survived · string: XX added · `4dd4b811121ee4fb`
  ```diff
  -"subject": e.event.subject,
  +"XXsubjectXX": e.event.subject,
  ```
- `_event_rows` · survived · string: upper case · `939fa14e110f1a6f`
  ```diff
  -"subject": e.event.subject,
  +"SUBJECT": e.event.subject,
  ```
- `_event_rows` · survived · string: XX added · `bf55d577b2f61016`
  ```diff
  -"date": e.event.date.isoformat(),
  +"XXdateXX": e.event.date.isoformat(),
  ```
- `_event_rows` · survived · string: upper case · `73a3877aea2be347`
  ```diff
  -"date": e.event.date.isoformat(),
  +"DATE": e.event.date.isoformat(),
  ```
- `_excluded_rows` · survived · string: XX added · `76a42dd5381e06b9`
  ```diff
  -{"directory": r.target.directory, "slug": r.target.slug, **x.model_dump(mode="json")}
  +{"XXdirectoryXX": r.target.directory, "slug": r.target.slug, **x.model_dump(mode="json")}
  ```
- `_excluded_rows` · survived · string: upper case · `c1313b8a77053c51`
  ```diff
  -{"directory": r.target.directory, "slug": r.target.slug, **x.model_dump(mode="json")}
  +{"DIRECTORY": r.target.directory, "slug": r.target.slug, **x.model_dump(mode="json")}
  ```
- `_excluded_rows` · survived · string: XX added · `93aa88f828202274`
  ```diff
  -{"directory": r.target.directory, "slug": r.target.slug, **x.model_dump(mode="json")}
  +{"directory": r.target.directory, "XXslugXX": r.target.slug, **x.model_dump(mode="json")}
  ```
- `_excluded_rows` · survived · string: upper case · `79f7e206451f3675`
  ```diff
  -{"directory": r.target.directory, "slug": r.target.slug, **x.model_dump(mode="json")}
  +{"directory": r.target.directory, "SLUG": r.target.slug, **x.model_dump(mode="json")}
  ```
- `_excluded_rows` · survived · argument: replaced by None · `5603c74d15da824d`
  ```diff
  -{"directory": r.target.directory, "slug": r.target.slug, **x.model_dump(mode="json")}
  +{"directory": r.target.directory, "slug": r.target.slug, **x.model_dump(mode=None)}
  ```
- `_excluded_rows` · survived · string: XX added · `2f33425cb68655d9`
  ```diff
  -{"directory": r.target.directory, "slug": r.target.slug, **x.model_dump(mode="json")}
  +{"directory": r.target.directory, "slug": r.target.slug, **x.model_dump(mode="XXjsonXX")}
  ```
- `_excluded_rows` · survived · string: upper case · `04ccbf1e7a3ed453`
  ```diff
  -{"directory": r.target.directory, "slug": r.target.slug, **x.model_dump(mode="json")}
  +{"directory": r.target.directory, "slug": r.target.slug, **x.model_dump(mode="JSON")}
  ```
- `_base_file_rows` · survived · string: XX added · `9cb14121378563fa`
  ```diff
  -{"slug": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode="json")}
  +{"XXslugXX": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode="json")}
  ```
- `_base_file_rows` · survived · string: upper case · `ee2179debb458cf0`
  ```diff
  -{"slug": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode="json")}
  +{"SLUG": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode="json")}
  ```
- `_base_file_rows` · survived · string: XX added · `30e02d1a1df9df1b`
  ```diff
  -{"slug": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode="json")}
  +{"slug": r.target.slug, "XXdoc_in_fileXX": r.target.file_doc_id, **e.model_dump(mode="json")}
  ```
- `_base_file_rows` · survived · string: upper case · `4d8b7d3f21d33fc5`
  ```diff
  -{"slug": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode="json")}
  +{"slug": r.target.slug, "DOC_IN_FILE": r.target.file_doc_id, **e.model_dump(mode="json")}
  ```
- `_base_file_rows` · survived · argument: replaced by None · `ac2ba42a06af5aac`
  ```diff
  -{"slug": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode="json")}
  +{"slug": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode=None)}
  ```
- `_base_file_rows` · survived · string: XX added · `87aa98457a8aeb9c`
  ```diff
  -{"slug": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode="json")}
  +{"slug": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode="XXjsonXX")}
  ```
- `_base_file_rows` · survived · string: upper case · `2ddcca61b680a9f7`
  ```diff
  -{"slug": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode="json")}
  +{"slug": r.target.slug, "doc_in_file": r.target.file_doc_id, **e.model_dump(mode="JSON")}
  ```
- `_segment_rows` · survived · string: XX added · `c38808b133880692`
  ```diff
  -{"slug": r.target.slug, **s.model_dump(mode="json")}
  +{"XXslugXX": r.target.slug, **s.model_dump(mode="json")}
  ```
- `_segment_rows` · survived · string: upper case · `e378f7a4dfd331c5`
  ```diff
  -{"slug": r.target.slug, **s.model_dump(mode="json")}
  +{"SLUG": r.target.slug, **s.model_dump(mode="json")}
  ```
- `_segment_rows` · survived · argument: replaced by None · `be77f1fa7961648f`
  ```diff
  -{"slug": r.target.slug, **s.model_dump(mode="json")}
  +{"slug": r.target.slug, **s.model_dump(mode=None)}
  ```
- `_segment_rows` · survived · string: XX added · `de08a03aeee9b245`
  ```diff
  -{"slug": r.target.slug, **s.model_dump(mode="json")}
  +{"slug": r.target.slug, **s.model_dump(mode="XXjsonXX")}
  ```
- `_segment_rows` · survived · string: upper case · `47dad506dbae74c4`
  ```diff
  -{"slug": r.target.slug, **s.model_dump(mode="json")}
  +{"slug": r.target.slug, **s.model_dump(mode="JSON")}
  ```
- `_file_rows` · survived · string: XX added · `5a09d5922af539ae`
  ```diff
  -{"file": path, "sha256": _sha256(text), "changed": path in changed}
  +{"XXfileXX": path, "sha256": _sha256(text), "changed": path in changed}
  ```
- `_file_rows` · survived · string: upper case · `02e977cd8d8216ef`
  ```diff
  -{"file": path, "sha256": _sha256(text), "changed": path in changed}
  +{"FILE": path, "sha256": _sha256(text), "changed": path in changed}
  ```
- `_file_rows` · survived · string: XX added · `1ca92b0c7bd698f3`
  ```diff
  -{"file": path, "sha256": _sha256(text), "changed": path in changed}
  +{"file": path, "XXsha256XX": _sha256(text), "changed": path in changed}
  ```
- `_file_rows` · survived · string: upper case · `b5d4b1ef98527267`
  ```diff
  -{"file": path, "sha256": _sha256(text), "changed": path in changed}
  +{"file": path, "SHA256": _sha256(text), "changed": path in changed}
  ```
- `_file_rows` · survived · string: XX added · `39eabc09f8e3c695`
  ```diff
  -{"file": path, "sha256": _sha256(text), "changed": path in changed}
  +{"file": path, "sha256": _sha256(text), "XXchangedXX": path in changed}
  ```
- `_file_rows` · survived · string: upper case · `f887a9d159f6b0be`
  ```diff
  -{"file": path, "sha256": _sha256(text), "changed": path in changed}
  +{"file": path, "sha256": _sha256(text), "CHANGED": path in changed}
  ```
- `_file_rows` · survived · is / in negated · `7cef29b075139877`
  ```diff
  -{"file": path, "sha256": _sha256(text), "changed": path in changed}
  +{"file": path, "sha256": _sha256(text), "changed": path not in changed}
  ```
- `_sha256` · survived · string: upper case · `96ccdc9f94cbb19d`
  ```diff
  -return hashlib.sha256(text.encode("utf-8")).hexdigest()
  +return hashlib.sha256(text.encode("UTF-8")).hexdigest()
  ```
- `repair_rows` · survived · argument: replaced by None · `c01f6d49b180c8c0`
  ```diff
  -"history_repair_conflicts": [c.model_dump(mode="json") for c in repair.conflicts],
  +"history_repair_conflicts": [c.model_dump(mode=None) for c in repair.conflicts],
  ```
- `repair_rows` · survived · string: XX added · `90dac93a4e747a5f`
  ```diff
  -"history_repair_conflicts": [c.model_dump(mode="json") for c in repair.conflicts],
  +"history_repair_conflicts": [c.model_dump(mode="XXjsonXX") for c in repair.conflicts],
  ```
- `repair_rows` · survived · string: upper case · `1472da9c0970ad94`
  ```diff
  -"history_repair_conflicts": [c.model_dump(mode="json") for c in repair.conflicts],
  +"history_repair_conflicts": [c.model_dump(mode="JSON") for c in repair.conflicts],
  ```
- `repair_rows` · survived · string: XX added · `56934ec98bc87383`
  ```diff
  -"history_repair_invariant_violations": [
  +"XXhistory_repair_invariant_violationsXX": [
  ```
- `repair_rows` · survived · string: upper case · `41f428561c906f90`
  ```diff
  -"history_repair_invariant_violations": [
  +"HISTORY_REPAIR_INVARIANT_VIOLATIONS": [
  ```
- `repair_rows` · survived · string: XX added · `88582449cf2b5602`
  ```diff
  -{"violation": v} for v in repair.invariant_violations
  +{"XXviolationXX": v} for v in repair.invariant_violations
  ```
- `repair_rows` · survived · string: upper case · `a01a5b374676f05c`
  ```diff
  -{"violation": v} for v in repair.invariant_violations
  +{"VIOLATION": v} for v in repair.invariant_violations
  ```
- `repair_counts` · survived · string: XX added · `7b892d0454adb2db`
  ```diff
  -"history_repair_slugs": len(repair.repairs),
  +"XXhistory_repair_slugsXX": len(repair.repairs),
  ```
- `repair_counts` · survived · string: upper case · `bbebc76d20cd26a9`
  ```diff
  -"history_repair_slugs": len(repair.repairs),
  +"HISTORY_REPAIR_SLUGS": len(repair.repairs),
  ```
- `repair_counts` · survived · string: XX added · `22b92f8bf9e47412`
  ```diff
  -"history_repair_files": len(repair.files),
  +"XXhistory_repair_filesXX": len(repair.files),
  ```
- `repair_counts` · survived · string: upper case · `1a17101ba5d993da`
  ```diff
  -"history_repair_files": len(repair.files),
  +"HISTORY_REPAIR_FILES": len(repair.files),
  ```
- `repair_counts` · survived · string: XX added · `4b9990d4784f45c0`
  ```diff
  -"history_repair_changed_files": len(repair.changed),
  +"XXhistory_repair_changed_filesXX": len(repair.changed),
  ```
- `repair_counts` · survived · string: upper case · `439316fd52ff21d5`
  ```diff
  -"history_repair_changed_files": len(repair.changed),
  +"HISTORY_REPAIR_CHANGED_FILES": len(repair.changed),
  ```
- `repair_counts` · survived · string: XX added · `a7632d183f36a10e`
  ```diff
  -"history_repair_invariant_violations": len(repair.invariant_violations),
  +"XXhistory_repair_invariant_violationsXX": len(repair.invariant_violations),
  ```
- `repair_counts` · survived · string: upper case · `61845b852fdb5e67`
  ```diff
  -"history_repair_invariant_violations": len(repair.invariant_violations),
  +"HISTORY_REPAIR_INVARIANT_VIOLATIONS": len(repair.invariant_violations),
  ```

</details>

<details><summary><code>src/lovspor/identity/report.py</code>: 202</summary>

- `manifest_json` · survived · argument: replaced by None · `1c5f3d5ca5f5e313`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=None) + "\n"
  ```
- `manifest_json` · survived · argument: removed · `42bf3864ac57331e`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, indent=2, ) + "\n"
  ```
- `manifest_json` · survived · True / False swapped · `4276a8401adba600`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
  ```
- `sha256_text` · survived · string: upper case · `886b92301d037a4d`
  ```diff
  -return hashlib.sha256(text.encode("utf-8")).hexdigest()
  +return hashlib.sha256(text.encode("UTF-8")).hexdigest()
  ```
- `_record_counts` · survived · string: XX added · `afd8bdd20a5e42db`
  ```diff
  -"manifest_revisions": plan.manifest_revisions,
  +"XXmanifest_revisionsXX": plan.manifest_revisions,
  ```
- `_record_counts` · survived · string: upper case · `2051e62ab3f5b428`
  ```diff
  -"manifest_revisions": plan.manifest_revisions,
  +"MANIFEST_REVISIONS": plan.manifest_revisions,
  ```
- `_record_counts` · survived · string: XX added · `01f36a2cbe89a491`
  ```diff
  -"base_documents": len(base_ids),
  +"XXbase_documentsXX": len(base_ids),
  ```
- `_record_counts` · survived · string: upper case · `cd6f86e12bd55ebd`
  ```diff
  -"base_documents": len(base_ids),
  +"BASE_DOCUMENTS": len(base_ids),
  ```
- `_record_counts` · survived · string: XX added · `82ccb6219aef429e`
  ```diff
  -"planned_documents": len(plan.planned_manifest.documents),
  +"XXplanned_documentsXX": len(plan.planned_manifest.documents),
  ```
- `_record_counts` · survived · string: upper case · `03c0970192993ee4`
  ```diff
  -"planned_documents": len(plan.planned_manifest.documents),
  +"PLANNED_DOCUMENTS": len(plan.planned_manifest.documents),
  ```
- `_record_counts` · survived · string: XX added · `9c182e6b11eacd12`
  ```diff
  -"history_files": len(plan.base.history),
  +"XXhistory_filesXX": len(plan.base.history),
  ```
- `_record_counts` · survived · string: upper case · `ee4e8c2d7788106c`
  ```diff
  -"history_files": len(plan.base.history),
  +"HISTORY_FILES": len(plan.base.history),
  ```
- `_record_counts` · survived · string: XX added · `1f861460589219a6`
  ```diff
  -"orphan_history_doc_ids": len(plan.orphan_history_doc_ids),
  +"XXorphan_history_doc_idsXX": len(plan.orphan_history_doc_ids),
  ```
- `_record_counts` · survived · string: upper case · `b1b2d1f354755e1b`
  ```diff
  -"orphan_history_doc_ids": len(plan.orphan_history_doc_ids),
  +"ORPHAN_HISTORY_DOC_IDS": len(plan.orphan_history_doc_ids),
  ```
- `_record_counts` · survived · string: XX added · `7b1c122b6eed74f0`
  ```diff
  -"orphan_history_doc_ids_not_restored": len(plan.orphans_not_restored),
  +"XXorphan_history_doc_ids_not_restoredXX": len(plan.orphans_not_restored),
  ```
- `_record_counts` · survived · string: upper case · `228a7c0017df7024`
  ```diff
  -"orphan_history_doc_ids_not_restored": len(plan.orphans_not_restored),
  +"ORPHAN_HISTORY_DOC_IDS_NOT_RESTORED": len(plan.orphans_not_restored),
  ```
- `_record_counts` · survived · string: XX added · `0fd4efde4dd92012`
  ```diff
  -"history_owner_mismatches": len(plan.history_mismatches),
  +"XXhistory_owner_mismatchesXX": len(plan.history_mismatches),
  ```
- `_record_counts` · survived · string: upper case · `d4866ac19261c8f8`
  ```diff
  -"history_owner_mismatches": len(plan.history_mismatches),
  +"HISTORY_OWNER_MISMATCHES": len(plan.history_mismatches),
  ```
- `_record_counts` · survived · string: XX added · `c2d646c8d9d9295d`
  ```diff
  -"unrecoverable_doc_ids": len(plan.unrecoverable),
  +"XXunrecoverable_doc_idsXX": len(plan.unrecoverable),
  ```
- `_record_counts` · survived · string: upper case · `03c1ed59b68fb59f`
  ```diff
  -"unrecoverable_doc_ids": len(plan.unrecoverable),
  +"UNRECOVERABLE_DOC_IDS": len(plan.unrecoverable),
  ```
- `_record_counts` · survived · string: XX added · `c48a774ba9ef4c63`
  ```diff
  -"changed_paths": len(plan.changed_paths),
  +"XXchanged_pathsXX": len(plan.changed_paths),
  ```
- `_record_counts` · survived · string: upper case · `5d9c868185c10f56`
  ```diff
  -"changed_paths": len(plan.changed_paths),
  +"CHANGED_PATHS": len(plan.changed_paths),
  ```
- `_slug_counts` · survived · string: XX added · `38a597dd82d00854`
  ```diff
  -"registry_entries": sum(len(e) for e in plan.planned_registry.slugs.values()),
  +"XXregistry_entriesXX": sum(len(e) for e in plan.planned_registry.slugs.values()),
  ```
- `_slug_counts` · survived · string: upper case · `101edebb077dea62`
  ```diff
  -"registry_entries": sum(len(e) for e in plan.planned_registry.slugs.values()),
  +"REGISTRY_ENTRIES": sum(len(e) for e in plan.planned_registry.slugs.values()),
  ```
- `_slug_counts` · survived · string: XX added · `a7beb74c2e68e8e5`
  ```diff
  -"registry_conflicts": len(plan.registry_conflicts),
  +"XXregistry_conflictsXX": len(plan.registry_conflicts),
  ```
- `_slug_counts` · survived · string: upper case · `c35e1b9550a87d6b`
  ```diff
  -"registry_conflicts": len(plan.registry_conflicts),
  +"REGISTRY_CONFLICTS": len(plan.registry_conflicts),
  ```
- `_slug_counts` · survived · string: XX added · `c860ad70215dc247`
  ```diff
  -"reused_slugs": len(plan.fates),
  +"XXreused_slugsXX": len(plan.fates),
  ```
- `_slug_counts` · survived · string: upper case · `f7441b6638483c85`
  ```diff
  -"reused_slugs": len(plan.fates),
  +"REUSED_SLUGS": len(plan.fates),
  ```
- `_slug_counts` · survived · string: XX added · `31ebb0ab86309952`
  ```diff
  -"reused_slugs_pre_threshold": sum(1 for f in plan.fates if PRE in f.reuse),
  +"XXreused_slugs_pre_thresholdXX": sum(1 for f in plan.fates if PRE in f.reuse),
  ```
- `_slug_counts` · survived · string: upper case · `ae2686d6a5351992`
  ```diff
  -"reused_slugs_pre_threshold": sum(1 for f in plan.fates if PRE in f.reuse),
  +"REUSED_SLUGS_PRE_THRESHOLD": sum(1 for f in plan.fates if PRE in f.reuse),
  ```
- `_slug_counts` · survived · number · `f122afbff075d49a`
  ```diff
  -"reused_slugs_pre_threshold": sum(1 for f in plan.fates if PRE in f.reuse),
  +"reused_slugs_pre_threshold": sum(2 for f in plan.fates if PRE in f.reuse),
  ```
- `_slug_counts` · survived · is / in negated · `1520abfcf2d66979`
  ```diff
  -"reused_slugs_pre_threshold": sum(1 for f in plan.fates if PRE in f.reuse),
  +"reused_slugs_pre_threshold": sum(1 for f in plan.fates if PRE not in f.reuse),
  ```
- `_slug_counts` · survived · string: XX added · `865bf52649ee9881`
  ```diff
  -"reused_slugs_post_threshold": sum(1 for f in plan.fates if POST in f.reuse),
  +"XXreused_slugs_post_thresholdXX": sum(1 for f in plan.fates if POST in f.reuse),
  ```
- `_slug_counts` · survived · string: upper case · `cf835b8e398613e4`
  ```diff
  -"reused_slugs_post_threshold": sum(1 for f in plan.fates if POST in f.reuse),
  +"REUSED_SLUGS_POST_THRESHOLD": sum(1 for f in plan.fates if POST in f.reuse),
  ```
- `_slug_counts` · survived · number · `b06d1688e723f1d8`
  ```diff
  -"reused_slugs_post_threshold": sum(1 for f in plan.fates if POST in f.reuse),
  +"reused_slugs_post_threshold": sum(2 for f in plan.fates if POST in f.reuse),
  ```
- `_slug_counts` · survived · is / in negated · `df9ba927c632815e`
  ```diff
  -"reused_slugs_post_threshold": sum(1 for f in plan.fates if POST in f.reuse),
  +"reused_slugs_post_threshold": sum(1 for f in plan.fates if POST not in f.reuse),
  ```
- `_slug_counts` · survived · string: XX added · `ecf68d0a6dce265a`
  ```diff
  -"u1_renames": len(plan.renames),
  +"XXu1_renamesXX": len(plan.renames),
  ```
- `_slug_counts` · survived · string: upper case · `bd69a23b4a127d25`
  ```diff
  -"u1_renames": len(plan.renames),
  +"U1_RENAMES": len(plan.renames),
  ```
- `_slug_counts` · survived · string: XX added · `390bdec7197b0245`
  ```diff
  -"u1_renames_with_doc_id_suffix": sum(1 for r in plan.renames if r.suffix_note),
  +"XXu1_renames_with_doc_id_suffixXX": sum(1 for r in plan.renames if r.suffix_note),
  ```
- `_slug_counts` · survived · string: upper case · `6821ac2f78d58f1f`
  ```diff
  -"u1_renames_with_doc_id_suffix": sum(1 for r in plan.renames if r.suffix_note),
  +"U1_RENAMES_WITH_DOC_ID_SUFFIX": sum(1 for r in plan.renames if r.suffix_note),
  ```
- `_slug_counts` · survived · number · `bdd11df1fa3d352d`
  ```diff
  -"u1_renames_with_doc_id_suffix": sum(1 for r in plan.renames if r.suffix_note),
  +"u1_renames_with_doc_id_suffix": sum(2 for r in plan.renames if r.suffix_note),
  ```
- `_slug_counts` · survived · string: XX added · `814021cd7da8532b`
  ```diff
  -"restored_tombstone_collisions": len(plan.collisions),
  +"XXrestored_tombstone_collisionsXX": len(plan.collisions),
  ```
- `_slug_counts` · survived · string: upper case · `31dfcd1a6d65cddc`
  ```diff
  -"restored_tombstone_collisions": len(plan.collisions),
  +"RESTORED_TOMBSTONE_COLLISIONS": len(plan.collisions),
  ```
- `_threshold_exceptions` · survived · string: XX added · `22ae90914aabe0a0`
  ```diff
  -{"directory": directory, "slug": slug, **entry.model_dump(mode="json")}
  +{"XXdirectoryXX": directory, "slug": slug, **entry.model_dump(mode="json")}
  ```
- `_threshold_exceptions` · survived · string: upper case · `b1499b8b9fbce485`
  ```diff
  -{"directory": directory, "slug": slug, **entry.model_dump(mode="json")}
  +{"DIRECTORY": directory, "slug": slug, **entry.model_dump(mode="json")}
  ```
- `_threshold_exceptions` · survived · argument: replaced by None · `83c143951aa8056b`
  ```diff
  -{"directory": directory, "slug": slug, **entry.model_dump(mode="json")}
  +{"directory": directory, "slug": slug, **entry.model_dump(mode=None)}
  ```
- `_threshold_exceptions` · survived · string: XX added · `8788647a0fe62889`
  ```diff
  -{"directory": directory, "slug": slug, **entry.model_dump(mode="json")}
  +{"directory": directory, "slug": slug, **entry.model_dump(mode="XXjsonXX")}
  ```
- `_threshold_exceptions` · survived · string: upper case · `61b732853c8c5d39`
  ```diff
  -{"directory": directory, "slug": slug, **entry.model_dump(mode="json")}
  +{"directory": directory, "slug": slug, **entry.model_dump(mode="JSON")}
  ```
- `_review_lists` · survived · argument: replaced by None · `2a7d34252b54550f`
  ```diff
  -"slug_fates": [f.model_dump(mode="json") for f in plan.fates],
  +"slug_fates": [f.model_dump(mode=None) for f in plan.fates],
  ```
- `_review_lists` · survived · string: XX added · `00dc35bd03b9a009`
  ```diff
  -"slug_fates": [f.model_dump(mode="json") for f in plan.fates],
  +"slug_fates": [f.model_dump(mode="XXjsonXX") for f in plan.fates],
  ```
- `_review_lists` · survived · string: upper case · `d83eaab1dbaf9ddf`
  ```diff
  -"slug_fates": [f.model_dump(mode="json") for f in plan.fates],
  +"slug_fates": [f.model_dump(mode="JSON") for f in plan.fates],
  ```
- `_review_lists` · survived · argument: replaced by None · `a1b7e308093723fd`
  ```diff
  -"u1_renames": [r.model_dump(mode="json") for r in plan.renames],
  +"u1_renames": [r.model_dump(mode=None) for r in plan.renames],
  ```
- `_review_lists` · survived · string: XX added · `1010e330a068057a`
  ```diff
  -"u1_renames": [r.model_dump(mode="json") for r in plan.renames],
  +"u1_renames": [r.model_dump(mode="XXjsonXX") for r in plan.renames],
  ```
- `_review_lists` · survived · string: upper case · `45d745f9565be1d2`
  ```diff
  -"u1_renames": [r.model_dump(mode="json") for r in plan.renames],
  +"u1_renames": [r.model_dump(mode="JSON") for r in plan.renames],
  ```
- `_review_lists` · survived · argument: replaced by None · `3815f40046201c1a`
  ```diff
  -"restored_tombstone_collisions": [c.model_dump(mode="json") for c in plan.collisions],
  +"restored_tombstone_collisions": [c.model_dump(mode=None) for c in plan.collisions],
  ```
- `_review_lists` · survived · string: XX added · `8b9aa22289ef740a`
  ```diff
  -"restored_tombstone_collisions": [c.model_dump(mode="json") for c in plan.collisions],
  +"restored_tombstone_collisions": [c.model_dump(mode="XXjsonXX") for c in plan.collisions],
  ```
- `_review_lists` · survived · string: upper case · `674ca0413cba128f`
  ```diff
  -"restored_tombstone_collisions": [c.model_dump(mode="json") for c in plan.collisions],
  +"restored_tombstone_collisions": [c.model_dump(mode="JSON") for c in plan.collisions],
  ```
- `_review_lists` · survived · argument: replaced by None · `7b7c466c93a1f574`
  ```diff
  -"history_owner_mismatches": [m.model_dump(mode="json") for m in plan.history_mismatches],
  +"history_owner_mismatches": [m.model_dump(mode=None) for m in plan.history_mismatches],
  ```
- `_review_lists` · survived · string: XX added · `377464546b5716ff`
  ```diff
  -"history_owner_mismatches": [m.model_dump(mode="json") for m in plan.history_mismatches],
  +"history_owner_mismatches": [m.model_dump(mode="XXjsonXX") for m in plan.history_mismatches],
  ```
- `_review_lists` · survived · string: upper case · `3c330c5a5ef48159`
  ```diff
  -"history_owner_mismatches": [m.model_dump(mode="json") for m in plan.history_mismatches],
  +"history_owner_mismatches": [m.model_dump(mode="JSON") for m in plan.history_mismatches],
  ```
- `_review_lists` · survived · argument: replaced by None · `893eb2a5b6bf13a6`
  ```diff
  -"registry_conflicts": [c.model_dump(mode="json") for c in plan.registry_conflicts],
  +"registry_conflicts": [c.model_dump(mode=None) for c in plan.registry_conflicts],
  ```
- `_review_lists` · survived · string: XX added · `ae14fbac8b67435e`
  ```diff
  -"registry_conflicts": [c.model_dump(mode="json") for c in plan.registry_conflicts],
  +"registry_conflicts": [c.model_dump(mode="XXjsonXX") for c in plan.registry_conflicts],
  ```
- `_review_lists` · survived · string: upper case · `1323b1cecc908633`
  ```diff
  -"registry_conflicts": [c.model_dump(mode="json") for c in plan.registry_conflicts],
  +"registry_conflicts": [c.model_dump(mode="JSON") for c in plan.registry_conflicts],
  ```
- `_review_lists` · survived · string: XX added · `40a5518fe61792fd`
  ```diff
  -"restored_tombstones": [t.model_dump(mode="json") for t in plan.restored],
  +"XXrestored_tombstonesXX": [t.model_dump(mode="json") for t in plan.restored],
  ```
- `_review_lists` · survived · string: upper case · `8f2d2976f8e55b26`
  ```diff
  -"restored_tombstones": [t.model_dump(mode="json") for t in plan.restored],
  +"RESTORED_TOMBSTONES": [t.model_dump(mode="json") for t in plan.restored],
  ```
- `_review_lists` · survived · argument: replaced by None · `6039e2bf1a25ceb5`
  ```diff
  -"restored_tombstones": [t.model_dump(mode="json") for t in plan.restored],
  +"restored_tombstones": [t.model_dump(mode=None) for t in plan.restored],
  ```
- `_review_lists` · survived · string: XX added · `9105a4c0dbac496f`
  ```diff
  -"restored_tombstones": [t.model_dump(mode="json") for t in plan.restored],
  +"restored_tombstones": [t.model_dump(mode="XXjsonXX") for t in plan.restored],
  ```
- `_review_lists` · survived · string: upper case · `76be46ab2be1ffa3`
  ```diff
  -"restored_tombstones": [t.model_dump(mode="json") for t in plan.restored],
  +"restored_tombstones": [t.model_dump(mode="JSON") for t in plan.restored],
  ```
- `_identity_fields` · survived · string: XX added · `8af4aaa0d2893624`
  ```diff
  -"base_registry_present": plan.base.registry is not None,
  +"XXbase_registry_presentXX": plan.base.registry is not None,
  ```
- `_identity_fields` · survived · string: upper case · `c6012bae2113d63c`
  ```diff
  -"base_registry_present": plan.base.registry is not None,
  +"BASE_REGISTRY_PRESENT": plan.base.registry is not None,
  ```
- `_identity_fields` · survived · is / in negated · `c6692a577f8d61d1`
  ```diff
  -"base_registry_present": plan.base.registry is not None,
  +"base_registry_present": plan.base.registry is None,
  ```
- `report_data` · survived · string: XX added · `733c6138f5ec07fb`
  ```diff
  -"schema_version": REPORT_SCHEMA_VERSION,
  +"XXschema_versionXX": REPORT_SCHEMA_VERSION,
  ```
- `report_data` · survived · string: upper case · `ea41339a36e1409f`
  ```diff
  -"schema_version": REPORT_SCHEMA_VERSION,
  +"SCHEMA_VERSION": REPORT_SCHEMA_VERSION,
  ```
- `report_data` · survived · string: XX added · `6c2815ef5a826b00`
  ```diff
  -"issue": 871,
  +"XXissueXX": 871,
  ```
- `report_data` · survived · string: upper case · `4a8cf39e97dbc0b7`
  ```diff
  -"issue": 871,
  +"ISSUE": 871,
  ```
- `report_data` · survived · number · `119edfce682b7e48`
  ```diff
  -"issue": 871,
  +"issue": 872,
  ```
- `report_data` · survived · string: XX added · `2521b02be4fae599`
  ```diff
  -"mode": "dry-run",
  +"XXmodeXX": "dry-run",
  ```
- `report_data` · survived · string: upper case · `d426cf83674c1786`
  ```diff
  -"mode": "dry-run",
  +"MODE": "dry-run",
  ```
- `report_data` · survived · string: XX added · `0bcc858ca0eb760f`
  ```diff
  -"mode": "dry-run",
  +"mode": "XXdry-runXX",
  ```
- `report_data` · survived · string: upper case · `4886772aebd64d29`
  ```diff
  -"mode": "dry-run",
  +"mode": "DRY-RUN",
  ```
- `report_data` · survived · string: XX added · `536fbf0cc091509f`
  ```diff
  -"nothing_to_do": plan.nothing_to_do,
  +"XXnothing_to_doXX": plan.nothing_to_do,
  ```
- `report_data` · survived · string: upper case · `fcde30999f2e2d24`
  ```diff
  -"nothing_to_do": plan.nothing_to_do,
  +"NOTHING_TO_DO": plan.nothing_to_do,
  ```
- `report_data` · survived · string: XX added · `16e45d428d7715d7`
  ```diff
  -"history_repair_note": HISTORY_REPAIR_NOTE,
  +"XXhistory_repair_noteXX": HISTORY_REPAIR_NOTE,
  ```
- `report_data` · survived · string: upper case · `ee7278874c898bc4`
  ```diff
  -"history_repair_note": HISTORY_REPAIR_NOTE,
  +"HISTORY_REPAIR_NOTE": HISTORY_REPAIR_NOTE,
  ```
- `report_data` · survived · argument: replaced by None · `f757ae4a6a304d55`
  ```diff
  -"u1_threshold": plan.threshold.model_dump(mode="json"),
  +"u1_threshold": plan.threshold.model_dump(mode=None),
  ```
- `report_data` · survived · string: XX added · `c05035f345cedf09`
  ```diff
  -"u1_threshold": plan.threshold.model_dump(mode="json"),
  +"u1_threshold": plan.threshold.model_dump(mode="XXjsonXX"),
  ```
- `report_data` · survived · string: upper case · `e57e178f4b0e99c6`
  ```diff
  -"u1_threshold": plan.threshold.model_dump(mode="json"),
  +"u1_threshold": plan.threshold.model_dump(mode="JSON"),
  ```
- `report_data` · survived · string: XX added · `7e61576afd06d05e`
  ```diff
  -"unrecoverable_doc_ids": plan.unrecoverable,
  +"XXunrecoverable_doc_idsXX": plan.unrecoverable,
  ```
- `report_data` · survived · string: upper case · `872825c73c45aa62`
  ```diff
  -"unrecoverable_doc_ids": plan.unrecoverable,
  +"UNRECOVERABLE_DOC_IDS": plan.unrecoverable,
  ```
- `report_data` · survived · string: XX added · `b432a995c9607c40`
  ```diff
  -"orphan_history_doc_ids_not_restored": plan.orphans_not_restored,
  +"XXorphan_history_doc_ids_not_restoredXX": plan.orphans_not_restored,
  ```
- `report_data` · survived · string: upper case · `0f902945fa6a519b`
  ```diff
  -"orphan_history_doc_ids_not_restored": plan.orphans_not_restored,
  +"ORPHAN_HISTORY_DOC_IDS_NOT_RESTORED": plan.orphans_not_restored,
  ```
- `_holder_text` · survived · argument: replaced by None · `009d684b3ff5747e`
  ```diff
  -facts = [str(holder[k]) for k in ("role", "status") if k in holder]
  +facts = [str(None) for k in ("role", "status") if k in holder]
  ```
- `_holder_text` · survived · string: XX added · `1fab6523cd26e706`
  ```diff
  -facts = [str(holder[k]) for k in ("role", "status") if k in holder]
  +facts = [str(holder[k]) for k in ("XXroleXX", "status") if k in holder]
  ```
- `_holder_text` · survived · string: upper case · `a60bf1baa65168ce`
  ```diff
  -facts = [str(holder[k]) for k in ("role", "status") if k in holder]
  +facts = [str(holder[k]) for k in ("ROLE", "status") if k in holder]
  ```
- `_holder_text` · survived · string: XX added · `7299b4ceca939d67`
  ```diff
  -facts = [str(holder[k]) for k in ("role", "status") if k in holder]
  +facts = [str(holder[k]) for k in ("role", "XXstatusXX") if k in holder]
  ```
- `_holder_text` · survived · string: upper case · `320e6e67829fa075`
  ```diff
  -facts = [str(holder[k]) for k in ("role", "status") if k in holder]
  +facts = [str(holder[k]) for k in ("role", "STATUS") if k in holder]
  ```
- `_holder_text` · survived · assignment: value to None · `6f74a83e4d555e96`
  ```diff
  -moved = f" → {holder['renamed_to']}" if holder.get("renamed_to") else ""
  +moved = None
  ```
- `_holder_text` · survived · ternary: condition forced · `f333115f58ac29bd`
  ```diff
  -moved = f" → {holder['renamed_to']}" if holder.get("renamed_to") else ""
  +moved = f" → {holder['renamed_to']}" if (holder.get("renamed_to")) and False else ""
  ```
- `_holder_text` · survived · argument: replaced by None · `3819576f178ea41f`
  ```diff
  -moved = f" → {holder['renamed_to']}" if holder.get("renamed_to") else ""
  +moved = f" → {holder['renamed_to']}" if holder.get(None) else ""
  ```
- `_holder_text` · survived · string: XX added · `f889e9b3221189e7`
  ```diff
  -moved = f" → {holder['renamed_to']}" if holder.get("renamed_to") else ""
  +moved = f" → {holder['renamed_to']}" if holder.get("XXrenamed_toXX") else ""
  ```
- `_holder_text` · survived · string: upper case · `ae5c4368a568d22d`
  ```diff
  -moved = f" → {holder['renamed_to']}" if holder.get("renamed_to") else ""
  +moved = f" → {holder['renamed_to']}" if holder.get("RENAMED_TO") else ""
  ```
- `_holder_text` · survived · string: XX added · `99548133248555e7`
  ```diff
  -moved = f" → {holder['renamed_to']}" if holder.get("renamed_to") else ""
  +moved = f" → {holder['renamed_to']}" if holder.get("renamed_to") else "XXXX"
  ```
- `_holder_text` · survived · not / ~ removed · `d4b3acb7d0d96e33`
  ```diff
  -if not moved and holder.get("slug_after_plan"):
  +if moved and holder.get("slug_after_plan"):
  ```
- `_holder_text` · survived · argument: replaced by None · `9224dc9d7b735afc`
  ```diff
  -if not moved and holder.get("slug_after_plan"):
  +if not moved and holder.get(None):
  ```
- `_holder_text` · survived · string: XX added · `1f89b7ddc36f7217`
  ```diff
  -if not moved and holder.get("slug_after_plan"):
  +if not moved and holder.get("XXslug_after_planXX"):
  ```
- `_holder_text` · survived · string: upper case · `f89e2e31e3c793cb`
  ```diff
  -if not moved and holder.get("slug_after_plan"):
  +if not moved and holder.get("SLUG_AFTER_PLAN"):
  ```
- `_holder_text` · survived · assignment: value to None · `4a189ef6220f57d6`
  ```diff
  -moved = f", now at `{holder['slug_after_plan']}`"
  +moved = None
  ```
- `_holder_text` · survived · string: XX added · `250fef3f8eb78130`
  ```diff
  -return f"`{holder['doc_id']}` ({', '.join([*facts, span])}){moved}"
  +return f"`{holder['doc_id']}` ({'XX, XX'.join([*facts, span])}){moved}"
  ```
- `_cell` · survived · string: XX added · `7ae3620b587f9c33`
  ```diff
  -return "; ".join(_holder_text(holder) for holder in value)
  +return "XX; XX".join(_holder_text(holder) for holder in value)
  ```
- `_cell` · survived · string: XX added · `408fc75bdd3a017b`
  ```diff
  -return ", ".join(str(item) for item in value)
  +return "XX, XX".join(str(item) for item in value)
  ```
- `_cell` · survived · argument: replaced by None · `b778425eb23fc82e`
  ```diff
  -return ", ".join(str(item) for item in value)
  +return ", ".join(str(None) for item in value)
  ```
- `_table` · survived · string: XX added · `96e3856b4e491929`
  ```diff
  -return ["(none)", ""]
  +return ["XX(none)XX", ""]
  ```
- `_table` · survived · string: upper case · `c66f8cd0faeba885`
  ```diff
  -return ["(none)", ""]
  +return ["(NONE)", ""]
  ```
- `_table` · survived · string: XX added · `2705656859e28318`
  ```diff
  -return ["(none)", ""]
  +return ["(none)", "XXXX"]
  ```
- `_table` · survived · string: XX added · `5214086de926bfc3`
  ```diff
  -lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
  +lines = ["XX| XX" + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
  ```
- `_table` · survived · string: XX added · `e927a3045a48d94a`
  ```diff
  -lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
  +lines = ["| " + "XX | XX".join(columns) + " |", "|" + "---|" * len(columns)]
  ```
- `_table` · survived · string: XX added · `fa2d7c6e38c30ace`
  ```diff
  -lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
  +lines = ["| " + " | ".join(columns) + "XX |XX", "|" + "---|" * len(columns)]
  ```
- `_table` · survived · string: XX added · `219901c43f77901d`
  ```diff
  -lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
  +lines = ["| " + " | ".join(columns) + " |", "XX|XX" + "---|" * len(columns)]
  ```
- `_table` · survived · string: XX added · `dceb69e66dcd8d90`
  ```diff
  -lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
  +lines = ["| " + " | ".join(columns) + " |", "|" + "XX---|XX" * len(columns)]
  ```
- `_table` · survived · augmented assignment to plain · `98d70348b9a76ac9`
  ```diff
  -lines += ["| " + " | ".join(_cell(row.get(c)) for c in columns) + " |" for row in rows]
  +lines = ["| " + " | ".join(_cell(row.get(c)) for c in columns) + " |" for row in rows]
  ```
- `_table` · survived · string: XX added · `ce29bdffb46de307`
  ```diff
  -lines += ["| " + " | ".join(_cell(row.get(c)) for c in columns) + " |" for row in rows]
  +lines += ["XX| XX" + " | ".join(_cell(row.get(c)) for c in columns) + " |" for row in rows]
  ```
- `_table` · survived · string: XX added · `8ae89858e2dc25a3`
  ```diff
  -lines += ["| " + " | ".join(_cell(row.get(c)) for c in columns) + " |" for row in rows]
  +lines += ["| " + "XX | XX".join(_cell(row.get(c)) for c in columns) + " |" for row in rows]
  ```
- `_table` · survived · string: XX added · `e50d18f2f36809f6`
  ```diff
  -lines += ["| " + " | ".join(_cell(row.get(c)) for c in columns) + " |" for row in rows]
  +lines += ["| " + " | ".join(_cell(row.get(c)) for c in columns) + "XX |XX" for row in rows]
  ```
- `_table` · survived · string: XX added · `f41d26efb4afc283`
  ```diff
  -return [*lines, ""]
  +return [*lines, "XXXX"]
  ```
- `_header` · survived · assignment: value to None · `9dc91cdf0ab56dbc`
  ```diff
  -counts = [{"count": k, "value": v} for k, v in _counts(plan).items()]
  +counts = None
  ```
- `_header` · survived · string: XX added · `53245f06f03d996e`
  ```diff
  -counts = [{"count": k, "value": v} for k, v in _counts(plan).items()]
  +counts = [{"XXcountXX": k, "value": v} for k, v in _counts(plan).items()]
  ```
- `_header` · survived · string: upper case · `7b4b1343d24de603`
  ```diff
  -counts = [{"count": k, "value": v} for k, v in _counts(plan).items()]
  +counts = [{"COUNT": k, "value": v} for k, v in _counts(plan).items()]
  ```
- `_header` · survived · string: XX added · `4061b001563125cb`
  ```diff
  -counts = [{"count": k, "value": v} for k, v in _counts(plan).items()]
  +counts = [{"count": k, "XXvalueXX": v} for k, v in _counts(plan).items()]
  ```
- `_header` · survived · string: upper case · `4df0306fe97f5b4c`
  ```diff
  -counts = [{"count": k, "value": v} for k, v in _counts(plan).items()]
  +counts = [{"count": k, "VALUE": v} for k, v in _counts(plan).items()]
  ```
- `_header` · survived · assignment: value to None · `27b934313e4e2836`
  ```diff
  -sums = [{"file": k, "sha256": v} for k, v in _planned_sha256(plan).items()]
  +sums = None
  ```
- `_header` · survived · string: XX added · `54e6274fbba2a869`
  ```diff
  -sums = [{"file": k, "sha256": v} for k, v in _planned_sha256(plan).items()]
  +sums = [{"XXfileXX": k, "sha256": v} for k, v in _planned_sha256(plan).items()]
  ```
- `_header` · survived · string: upper case · `46ad2c5d895672f3`
  ```diff
  -sums = [{"file": k, "sha256": v} for k, v in _planned_sha256(plan).items()]
  +sums = [{"FILE": k, "sha256": v} for k, v in _planned_sha256(plan).items()]
  ```
- `_header` · survived · string: XX added · `cc21f99cc5f914a8`
  ```diff
  -sums = [{"file": k, "sha256": v} for k, v in _planned_sha256(plan).items()]
  +sums = [{"file": k, "XXsha256XX": v} for k, v in _planned_sha256(plan).items()]
  ```
- `_header` · survived · string: upper case · `551feb113c309ffa`
  ```diff
  -sums = [{"file": k, "sha256": v} for k, v in _planned_sha256(plan).items()]
  +sums = [{"file": k, "SHA256": v} for k, v in _planned_sha256(plan).items()]
  ```
- `_header` · survived · string: XX added · `0db41afdeeb67bcd`
  ```diff
  -changed = "".join(f"{path}\n" for path in plan.changed_paths)
  +changed = "XXXX".join(f"{path}\n" for path in plan.changed_paths)
  ```
- `_header` · survived · string: XX added · `fcaee6b1ee342c79`
  ```diff
  -"# Identity migration dry run (#871)",
  +"XX# Identity migration dry run (#871)XX",
  ```
- `_header` · survived · string: lower case · `881b5ebcc945214c`
  ```diff
  -"# Identity migration dry run (#871)",
  +"# identity migration dry run (#871)",
  ```
- `_header` · survived · string: upper case · `b91b1bea53eca389`
  ```diff
  -"# Identity migration dry run (#871)",
  +"# IDENTITY MIGRATION DRY RUN (#871)",
  ```
- `_header` · survived · string: XX added · `07c459069ad42437`
  ```diff
  -"",
  +"XXXX",
  ```
- `_header` · survived · string: XX added · `07c459069ad42437-2`
  ```diff
  -"",
  +"XXXX",
  ```
- `_header` · survived · string: XX added · `1c4ddbd14fe47931`
  ```diff
  -"## Counts",
  +"XX## CountsXX",
  ```
- `_header` · survived · string: lower case · `501490e8f192436d`
  ```diff
  -"## Counts",
  +"## counts",
  ```
- `_header` · survived · string: upper case · `8d1a6c61fc5bb58c`
  ```diff
  -"## Counts",
  +"## COUNTS",
  ```
- `_header` · survived · string: XX added · `07c459069ad42437-3`
  ```diff
  -"",
  +"XXXX",
  ```
- `_header` · survived · argument: replaced by None · `2ff50cf009ade23d`
  ```diff
  -*_table(counts, ("count", "value")),
  +*_table(None, ("count", "value")),
  ```
- `_header` · survived · string: XX added · `cb8fd568787d2d6a`
  ```diff
  -*_table(counts, ("count", "value")),
  +*_table(counts, ("XXcountXX", "value")),
  ```
- `_header` · survived · string: upper case · `59b3d5ea936591c9`
  ```diff
  -*_table(counts, ("count", "value")),
  +*_table(counts, ("COUNT", "value")),
  ```
- `_header` · survived · string: XX added · `75923e427f89cfcb`
  ```diff
  -*_table(counts, ("count", "value")),
  +*_table(counts, ("count", "XXvalueXX")),
  ```
- `_header` · survived · string: upper case · `e16d8c655c871e45`
  ```diff
  -*_table(counts, ("count", "value")),
  +*_table(counts, ("count", "VALUE")),
  ```
- `_header` · survived · string: XX added · `b5f462da31fbc4b9`
  ```diff
  -"## Checksums",
  +"XX## ChecksumsXX",
  ```
- `_header` · survived · string: lower case · `4ac5ea2f6cbf1eca`
  ```diff
  -"## Checksums",
  +"## checksums",
  ```
- `_header` · survived · string: upper case · `58aa9ca145121f42`
  ```diff
  -"## Checksums",
  +"## CHECKSUMS",
  ```
- `_header` · survived · string: XX added · `07c459069ad42437-4`
  ```diff
  -"",
  +"XXXX",
  ```
- `_header` · survived · argument: replaced by None · `b0887b57001ff923`
  ```diff
  -*_table(sums, ("file", "sha256")),
  +*_table(None, ("file", "sha256")),
  ```
- `_header` · survived · string: XX added · `6da5acfe344abf46`
  ```diff
  -*_table(sums, ("file", "sha256")),
  +*_table(sums, ("XXfileXX", "sha256")),
  ```
- `_header` · survived · string: upper case · `8499cb7cbe27e9c8`
  ```diff
  -*_table(sums, ("file", "sha256")),
  +*_table(sums, ("FILE", "sha256")),
  ```
- `_header` · survived · string: XX added · `ccb21da0e0162148`
  ```diff
  -*_table(sums, ("file", "sha256")),
  +*_table(sums, ("file", "XXsha256XX")),
  ```
- `_header` · survived · string: upper case · `a359110a5a47da83`
  ```diff
  -*_table(sums, ("file", "sha256")),
  +*_table(sums, ("file", "SHA256")),
  ```
- `_header` · survived · string: XX added · `07c459069ad42437-5`
  ```diff
  -"",
  +"XXXX",
  ```
- `_identity_schema_lines` · survived · string: XX added · `7ab2052f01b4400b`
  ```diff
  -"## Identity schema",
  +"XX## Identity schemaXX",
  ```
- `_identity_schema_lines` · survived · string: XX added · `78756f622b4276fc`
  ```diff
  -"",
  +"XXXX",
  ```
- `_identity_schema_lines` · survived · string: XX added · `78756f622b4276fc-2`
  ```diff
  -"",
  +"XXXX",
  ```
- `_identity_schema_lines` · survived · string: XX added · `78756f622b4276fc-3`
  ```diff
  -"",
  +"XXXX",
  ```
- `render_markdown` · survived · string: XX added · `1c343be2e427683d`
  ```diff
  -lines += [f"## {title}", "", *_table(lists[key], columns)]
  +lines += [f"## {title}", "XXXX", *_table(lists[key], columns)]
  ```
- `render_markdown` · survived · argument: replaced by None · `059e45e19f0edc52`
  ```diff
  -lines += [f"## {title}", "", *_table(lists[key], columns)]
  +lines += [f"## {title}", "", *_table(None, columns)]
  ```
- `render_markdown` · survived · assignment: value to None · `83b12c5da44ed04b`
  ```diff
  -unrecoverable = [{"doc_id": doc_id} for doc_id in plan.unrecoverable]
  +unrecoverable = None
  ```
- `render_markdown` · survived · string: XX added · `751371578178d606`
  ```diff
  -unrecoverable = [{"doc_id": doc_id} for doc_id in plan.unrecoverable]
  +unrecoverable = [{"XXdoc_idXX": doc_id} for doc_id in plan.unrecoverable]
  ```
- `render_markdown` · survived · string: upper case · `92f9ef86da004151`
  ```diff
  -unrecoverable = [{"doc_id": doc_id} for doc_id in plan.unrecoverable]
  +unrecoverable = [{"DOC_ID": doc_id} for doc_id in plan.unrecoverable]
  ```
- `render_markdown` · survived · string: XX added · `d719c47ce3d7dfbb`
  ```diff
  -lines += ["## Unrecoverable doc_ids", "", *_table(unrecoverable, ("doc_id",))]
  +lines += ["XX## Unrecoverable doc_idsXX", "", *_table(unrecoverable, ("doc_id",))]
  ```
- `render_markdown` · survived · string: lower case · `7568457e94f44c7a`
  ```diff
  -lines += ["## Unrecoverable doc_ids", "", *_table(unrecoverable, ("doc_id",))]
  +lines += ["## unrecoverable doc_ids", "", *_table(unrecoverable, ("doc_id",))]
  ```
- `render_markdown` · survived · string: upper case · `20370b837c8a5ae1`
  ```diff
  -lines += ["## Unrecoverable doc_ids", "", *_table(unrecoverable, ("doc_id",))]
  +lines += ["## UNRECOVERABLE DOC_IDS", "", *_table(unrecoverable, ("doc_id",))]
  ```
- `render_markdown` · survived · string: XX added · `ee3812e4534ee1a9`
  ```diff
  -lines += ["## Unrecoverable doc_ids", "", *_table(unrecoverable, ("doc_id",))]
  +lines += ["## Unrecoverable doc_ids", "XXXX", *_table(unrecoverable, ("doc_id",))]
  ```
- `render_markdown` · survived · argument: replaced by None · `90b8c4ed3c98a43a`
  ```diff
  -lines += ["## Unrecoverable doc_ids", "", *_table(unrecoverable, ("doc_id",))]
  +lines += ["## Unrecoverable doc_ids", "", *_table(None, ("doc_id",))]
  ```
- `render_markdown` · survived · string: XX added · `2371565671efda73`
  ```diff
  -lines += ["## Unrecoverable doc_ids", "", *_table(unrecoverable, ("doc_id",))]
  +lines += ["## Unrecoverable doc_ids", "", *_table(unrecoverable, ("XXdoc_idXX",))]
  ```
- `render_markdown` · survived · string: upper case · `8ab4f2d47ebe53d6`
  ```diff
  -lines += ["## Unrecoverable doc_ids", "", *_table(unrecoverable, ("doc_id",))]
  +lines += ["## Unrecoverable doc_ids", "", *_table(unrecoverable, ("DOC_ID",))]
  ```
- `render_markdown` · survived · assignment: value to None · `a75cb8c8bfaebff0`
  ```diff
  -not_restored = [{"doc_id": doc_id} for doc_id in plan.orphans_not_restored]
  +not_restored = None
  ```
- `render_markdown` · survived · string: XX added · `f00ffd564f01dd2d`
  ```diff
  -not_restored = [{"doc_id": doc_id} for doc_id in plan.orphans_not_restored]
  +not_restored = [{"XXdoc_idXX": doc_id} for doc_id in plan.orphans_not_restored]
  ```
- `render_markdown` · survived · string: upper case · `81f66ff23e43c2be`
  ```diff
  -not_restored = [{"doc_id": doc_id} for doc_id in plan.orphans_not_restored]
  +not_restored = [{"DOC_ID": doc_id} for doc_id in plan.orphans_not_restored]
  ```
- `render_markdown` · survived · string: XX added · `16b0a58f2949a234`
  ```diff
  -lines += ["## Orphan-history doc_ids not restored", "", *_table(not_restored, ("doc_id",))]
  +lines += ["XX## Orphan-history doc_ids not restoredXX", "", *_table(not_restored, ("doc_id",))]
  ```
- `render_markdown` · survived · string: lower case · `34495538f4827dc1`
  ```diff
  -lines += ["## Orphan-history doc_ids not restored", "", *_table(not_restored, ("doc_id",))]
  +lines += ["## orphan-history doc_ids not restored", "", *_table(not_restored, ("doc_id",))]
  ```
- `render_markdown` · survived · string: upper case · `02d5c5c4e23b7bba`
  ```diff
  -lines += ["## Orphan-history doc_ids not restored", "", *_table(not_restored, ("doc_id",))]
  +lines += ["## ORPHAN-HISTORY DOC_IDS NOT RESTORED", "", *_table(not_restored, ("doc_id",))]
  ```
- `render_markdown` · survived · string: XX added · `8166b900c7a90f32`
  ```diff
  -lines += ["## Orphan-history doc_ids not restored", "", *_table(not_restored, ("doc_id",))]
  +lines += ["## Orphan-history doc_ids not restored", "XXXX", *_table(not_restored, ("doc_id",))]
  ```
- `render_markdown` · survived · argument: replaced by None · `f6c7e2911840df76`
  ```diff
  -lines += ["## Orphan-history doc_ids not restored", "", *_table(not_restored, ("doc_id",))]
  +lines += ["## Orphan-history doc_ids not restored", "", *_table(None, ("doc_id",))]
  ```
- `render_markdown` · survived · string: XX added · `e6a3d280d1df0c1d`
  ```diff
  -lines += ["## Orphan-history doc_ids not restored", "", *_table(not_restored, ("doc_id",))]
  +lines += ["## Orphan-history doc_ids not restored", "", *_table(not_restored, ("XXdoc_idXX",))]
  ```
- `render_markdown` · survived · string: upper case · `35ba64b9c8ec73fb`
  ```diff
  -lines += ["## Orphan-history doc_ids not restored", "", *_table(not_restored, ("doc_id",))]
  +lines += ["## Orphan-history doc_ids not restored", "", *_table(not_restored, ("DOC_ID",))]
  ```
- `render_markdown` · survived · string: XX added · `f43cf69074e29de3`
  ```diff
  -lines += ["## History repair", "", f"{HISTORY_REPAIR_NOTE}.", ""]
  +lines += ["XX## History repairXX", "", f"{HISTORY_REPAIR_NOTE}.", ""]
  ```
- `render_markdown` · survived · string: lower case · `4437bbc73085f321`
  ```diff
  -lines += ["## History repair", "", f"{HISTORY_REPAIR_NOTE}.", ""]
  +lines += ["## history repair", "", f"{HISTORY_REPAIR_NOTE}.", ""]
  ```
- `render_markdown` · survived · string: upper case · `a2fb1dfcaca0f266`
  ```diff
  -lines += ["## History repair", "", f"{HISTORY_REPAIR_NOTE}.", ""]
  +lines += ["## HISTORY REPAIR", "", f"{HISTORY_REPAIR_NOTE}.", ""]
  ```
- `render_markdown` · survived · string: XX added · `6992b511ff2e4765`
  ```diff
  -lines += ["## History repair", "", f"{HISTORY_REPAIR_NOTE}.", ""]
  +lines += ["## History repair", "XXXX", f"{HISTORY_REPAIR_NOTE}.", ""]
  ```
- `render_markdown` · survived · string: XX added · `b2342c386ce8b0f8`
  ```diff
  -lines += ["## History repair", "", f"{HISTORY_REPAIR_NOTE}.", ""]
  +lines += ["## History repair", "", f"{HISTORY_REPAIR_NOTE}.", "XXXX"]
  ```
- `render_markdown` · survived · not / ~ removed · `b8e3f9afce0aef34`
  ```diff
  -lines += [f"Invariant (each event in exactly one document's history): `{not violations}`."]
  +lines += [f"Invariant (each event in exactly one document's history): `{violations}`."]
  ```
- `render_markdown` · survived · string: XX added · `eb00549be63a99e6`
  ```diff
  -lines += [*(f"- {violation}" for violation in violations), ""]
  +lines += [*(f"- {violation}" for violation in violations), "XXXX"]
  ```
- `render_markdown` · survived · string: XX added · `3832b1bf746207ca`
  ```diff
  -return "\n".join(lines)
  +return "XX\nXX".join(lines)
  ```
- `report_files` · survived · argument: replaced by None · `ee1d88374066d295`
  ```diff
  -json.dumps(report_data(plan), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +json.dumps(report_data(plan), sort_keys=None, indent=2, ensure_ascii=False) + "\n"
  ```
- `report_files` · survived · argument: replaced by None · `6a486cb117060d69`
  ```diff
  -json.dumps(report_data(plan), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +json.dumps(report_data(plan), sort_keys=True, indent=None, ensure_ascii=False) + "\n"
  ```
- `report_files` · survived · argument: replaced by None · `e517d9f49e1bf7af`
  ```diff
  -json.dumps(report_data(plan), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +json.dumps(report_data(plan), sort_keys=True, indent=2, ensure_ascii=None) + "\n"
  ```
- `report_files` · survived · argument: removed · `e2292bd6033f3324`
  ```diff
  -json.dumps(report_data(plan), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +json.dumps(report_data(plan), indent=2, ensure_ascii=False) + "\n"
  ```
- `report_files` · survived · argument: removed · `5022d3f30a03e937`
  ```diff
  -json.dumps(report_data(plan), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +json.dumps(report_data(plan), sort_keys=True, ensure_ascii=False) + "\n"
  ```
- `report_files` · survived · argument: removed · `86374b3f8c3b21ac`
  ```diff
  -json.dumps(report_data(plan), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +json.dumps(report_data(plan), sort_keys=True, indent=2, ) + "\n"
  ```
- `report_files` · survived · True / False swapped · `7c50fd5a34dc227f`
  ```diff
  -json.dumps(report_data(plan), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +json.dumps(report_data(plan), sort_keys=False, indent=2, ensure_ascii=False) + "\n"
  ```
- `report_files` · survived · number · `8d34f638ccd21ddd`
  ```diff
  -json.dumps(report_data(plan), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +json.dumps(report_data(plan), sort_keys=True, indent=3, ensure_ascii=False) + "\n"
  ```
- `report_files` · survived · True / False swapped · `95078855cdfad10d`
  ```diff
  -json.dumps(report_data(plan), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
  +json.dumps(report_data(plan), sort_keys=True, indent=2, ensure_ascii=True) + "\n"
  ```

</details>

<details><summary><code>src/lovspor/identity/schema.py</code>: 9</summary>

- `parse_declaration` · survived · string: upper case · `0267c24e38496db9`
  ```diff
  -declared = IdentityDeclaration.model_validate(json.loads(blob.decode("utf-8")))
  +declared = IdentityDeclaration.model_validate(json.loads(blob.decode("UTF-8")))
  ```
- `declared_schema` · survived · argument: replaced by None · `26ae325db6bad60d`
  ```diff
  -return parse_declaration(path.read_bytes() if path.exists() else None, str(path))
  +return parse_declaration(path.read_bytes() if path.exists() else None, None)
  ```
- `declared_schema` · survived · argument: replaced by None · `d3039a4413106698`
  ```diff
  -return parse_declaration(path.read_bytes() if path.exists() else None, str(path))
  +return parse_declaration(path.read_bytes() if path.exists() else None, str(None))
  ```
- `declaration_json` · survived · argument: replaced by None · `39e750cd3b58653a`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2) + "\n"
  +return json.dumps(data, sort_keys=None, indent=2) + "\n"
  ```
- `declaration_json` · survived · argument: replaced by None · `daab3a57cd13e130`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2) + "\n"
  +return json.dumps(data, sort_keys=True, indent=None) + "\n"
  ```
- `declaration_json` · survived · argument: removed · `a801b7c8af4c2f71`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2) + "\n"
  +return json.dumps(data, indent=2) + "\n"
  ```
- `declaration_json` · survived · argument: removed · `6bb8dc7f235bab20`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2) + "\n"
  +return json.dumps(data, sort_keys=True, ) + "\n"
  ```
- `declaration_json` · survived · True / False swapped · `6cd965c5712f90cc`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2) + "\n"
  +return json.dumps(data, sort_keys=False, indent=2) + "\n"
  ```
- `declaration_json` · survived · number · `c86bed97350b3ad1`
  ```diff
  -return json.dumps(data, sort_keys=True, indent=2) + "\n"
  +return json.dumps(data, sort_keys=True, indent=3) + "\n"
  ```

</details>

<details><summary><code>src/lovspor/identity/walk.py</code>: 4</summary>

- `raw_address` · survived · operator: and / or · `82c19b7203c2db53`
  ```diff
  -return path.parts[0], slug if isinstance(slug, str) and slug else path.stem
  +return path.parts[0], slug if isinstance(slug, str) or slug else path.stem
  ```
- `parse_revision` · survived · string: upper case · `f61e319210bd9d85`
  ```diff
  -data = json.loads(blob.decode("utf-8"))
  +data = json.loads(blob.decode("UTF-8"))
  ```
- `_observe` · survived · argument: replaced by None · `ed81250dc495b174`
  ```diff
  -address = raw_address(doc_id, record, commit)
  +address = raw_address(None, record, commit)
  ```
- `_observe` · survived · argument: replaced by None · `c55cd981700b7f8f`
  ```diff
  -address = raw_address(doc_id, record, commit)
  +address = raw_address(doc_id, record, None)
  ```

</details>

<details><summary><code>src/lovspor/legaltime/document.py</code>: 2</summary>

- `StatedPlacement.read` · survived · ternary: condition forced · `a9bb5076b65470cd`
  ```diff
  -origin: Literal["sentence"] | None = "sentence" if chapter is not None else None
  +origin: Literal["sentence"] | None = "sentence" if (chapter is not None) or True else None
  ```
- `StatedPlacement.titled` · survived · ternary: condition forced · `d3cd0edba7c1d6a4`
  ```diff
  -origin = "title" if read is not None else self.chapter_from
  +origin = "title" if (read is not None) or True else self.chapter_from
  ```

</details>

<details><summary><code>src/lovspor/legaltime/identity.py</code>: 1</summary>

- `_check_link` · survived · operator: and / or · `1bd9335404f15acc`
  ```diff
  -if end is None or end.ended_by != "rename":
  +if end is None and end.ended_by != "rename":
  ```

</details>

<details><summary><code>src/lovspor/legaltime/payload_list.py</code>: 11</summary>

- `Listing.__init__` · survived · assignment: value to None · `572f640c9fe5d3de`
  ```diff
  -self.active = False
  +self.active = None
  ```
- `Listing.__init__` · survived · assignment: value to None · `f2125584d702eff0`
  ```diff
  -self.item: Item = "none"
  +self.item: Item = None
  ```
- `Listing.__init__` · survived · string: XX added · `53a5d4ddb8c6b8ab`
  ```diff
  -self.item: Item = "none"
  +self.item: Item = "XXnoneXX"
  ```
- `Listing.__init__` · survived · string: upper case · `69661f9902e26942`
  ```diff
  -self.item: Item = "none"
  +self.item: Item = "NONE"
  ```
- `Listing.open` · survived · string: XX added · `879758150447a23f`
  ```diff
  -self.active, self.item = True, "none"
  +self.active, self.item = True, "XXnoneXX"
  ```
- `Listing.open` · survived · string: upper case · `b0a2792ecba25722`
  ```diff
  -self.active, self.item = True, "none"
  +self.active, self.item = True, "NONE"
  ```
- `Listing.step` · survived · assignment: value to None · `1ecd3511e75f57bf`
  ```diff
  -self.active = False  # another article: the reader closes the block itself
  +self.active = None  # another article: the reader closes the block itself
  ```
- `Listing.step` · survived · string: XX added · `dbac5b79a6d41241`
  ```diff
  -self.item = "open" if opens else "unproven"
  +self.item = "open" if opens else "XXunprovenXX"
  ```
- `Listing.step` · survived · string: upper case · `cab35364a6adc0cd`
  ```diff
  -self.item = "open" if opens else "unproven"
  +self.item = "open" if opens else "UNPROVEN"
  ```
- `Listing.step` · survived · ternary: condition forced · `2a3c560257a10807`
  ```diff
  -self.item = "open" if opens else self.item  # an unnumbered intro binds its own block
  +self.item = "open" if (opens) or True else self.item  # an unnumbered intro binds its own block
  ```
- `Listing.step` · survived · True / False swapped · `2fdc95fb88b7cbbd`
  ```diff
  -return (None, True, state[2])
  +return (None, False, state[2])
  ```

</details>

<details><summary><code>src/lovspor/legaltime/rename_research.py</code>: 64</summary>

- `Research.canonical_json` · survived · argument: replaced by None · `19fe9faeb8cdafdd`
  ```diff
  -return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
  +return json.dumps(self.model_dump(mode="json"), sort_keys=None, ensure_ascii=False)
  ```
- `Research.canonical_json` · survived · argument: replaced by None · `580c7080f2f257b6`
  ```diff
  -return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
  +return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=None)
  ```
- `Research.canonical_json` · survived · argument: removed · `2e86d15e55be6cd4`
  ```diff
  -return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
  +return json.dumps(self.model_dump(mode="json"), ensure_ascii=False)
  ```
- `Research.canonical_json` · survived · argument: removed · `f3bbcc994019af44`
  ```diff
  -return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
  +return json.dumps(self.model_dump(mode="json"), sort_keys=True, )
  ```
- `Research.canonical_json` · survived · True / False swapped · `67be5140beb5f173`
  ```diff
  -return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
  +return json.dumps(self.model_dump(mode="json"), sort_keys=False, ensure_ascii=False)
  ```
- `Research.canonical_json` · survived · True / False swapped · `886a49f082908414`
  ```diff
  -return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
  +return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)
  ```
- `Research.projection` · survived · assignment: value to None · `05ff18617ab728c8`
  ```diff
  -refusals = sorted(
  -({k: v for k, v in r.items() if k != "op_id"} for r in self._dumped("refusals")),
  -key=lambda r: (r["member"], r["sentence_sha256"]),
  -)
  +refusals = None
  ```
- `Research.projection` · survived · argument: replaced by None · `411a3d9316e8a72f`
  ```diff
  -key=lambda r: (r["member"], r["sentence_sha256"]),
  +key=None,
  ```
- `Research.projection` · survived · argument: removed · `afb2f901f2784619`
  ```diff
  -key=lambda r: (r["member"], r["sentence_sha256"]),
  -)
  +)
  ```
- `Research.projection` · survived · operator: comparison · `99cdce7798a3d5d9`
  ```diff
  -({k: v for k, v in r.items() if k != "op_id"} for r in self._dumped("refusals")),
  +({k: v for k, v in r.items() if k == "op_id"} for r in self._dumped("refusals")),
  ```
- `Research.projection` · survived · string: XX added · `cf1190983979189e`
  ```diff
  -({k: v for k, v in r.items() if k != "op_id"} for r in self._dumped("refusals")),
  +({k: v for k, v in r.items() if k != "XXop_idXX"} for r in self._dumped("refusals")),
  ```
- `Research.projection` · survived · string: upper case · `b325c71b6f579502`
  ```diff
  -({k: v for k, v in r.items() if k != "op_id"} for r in self._dumped("refusals")),
  +({k: v for k, v in r.items() if k != "OP_ID"} for r in self._dumped("refusals")),
  ```
- `Research.projection` · survived · lambda: body replaced · `b95668579850b127`
  ```diff
  -key=lambda r: (r["member"], r["sentence_sha256"]),
  +key=lambda r: None,
  ```
- `Research.projection` · survived · string: XX added · `50a63ba9efaec98c`
  ```diff
  -key=lambda r: (r["member"], r["sentence_sha256"]),
  +key=lambda r: (r["XXmemberXX"], r["sentence_sha256"]),
  ```
- `Research.projection` · survived · string: upper case · `32d7e35adb6b90bc`
  ```diff
  -key=lambda r: (r["member"], r["sentence_sha256"]),
  +key=lambda r: (r["MEMBER"], r["sentence_sha256"]),
  ```
- `Research.projection` · survived · string: XX added · `a4c7a1f423417613`
  ```diff
  -key=lambda r: (r["member"], r["sentence_sha256"]),
  +key=lambda r: (r["member"], r["XXsentence_sha256XX"]),
  ```
- `Research.projection` · survived · string: upper case · `b796a8752409aa16`
  ```diff
  -key=lambda r: (r["member"], r["sentence_sha256"]),
  +key=lambda r: (r["member"], r["SENTENCE_SHA256"]),
  ```
- `Research.projection` · survived · assignment: value to None · `e796c3a0fc48b1fe`
  ```diff
  -steps = [_projected_step(c) for c in self._dumped("steps")]
  +steps = None
  ```
- `Research.projection` · survived · argument: replaced by None · `74ea99ca20b32d5a`
  ```diff
  -{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  +None, sort_keys=True, ensure_ascii=False
  ```
- `Research.projection` · survived · argument: replaced by None · `74d08eb8ef65fed2`
  ```diff
  -{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  +{"refusals": refusals, "steps": steps}, sort_keys=None, ensure_ascii=False
  ```
- `Research.projection` · survived · argument: replaced by None · `2bb9952af5472eb6`
  ```diff
  -{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  +{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=None
  ```
- `Research.projection` · survived · argument: removed · `27a3fedff4a55cc9`
  ```diff
  -{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  +{"refusals": refusals, "steps": steps}, ensure_ascii=False
  ```
- `Research.projection` · survived · argument: removed · `42e18bfdb6e8fd11`
  ```diff
  -{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  -)
  +{"refusals": refusals, "steps": steps}, sort_keys=True, )
  ```
- `Research.projection` · survived · string: XX added · `414ce95565bc58ac`
  ```diff
  -{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  +{"XXrefusalsXX": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  ```
- `Research.projection` · survived · string: upper case · `9173df81b4ed4275`
  ```diff
  -{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  +{"REFUSALS": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  ```
- `Research.projection` · survived · string: XX added · `cb5ec21ca07cc301`
  ```diff
  -{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  +{"refusals": refusals, "XXstepsXX": steps}, sort_keys=True, ensure_ascii=False
  ```
- `Research.projection` · survived · string: upper case · `232fa15feef15ebd`
  ```diff
  -{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  +{"refusals": refusals, "STEPS": steps}, sort_keys=True, ensure_ascii=False
  ```
- `Research.projection` · survived · True / False swapped · `6e7989ac64cc3ce1`
  ```diff
  -{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  +{"refusals": refusals, "steps": steps}, sort_keys=False, ensure_ascii=False
  ```
- `Research.projection` · survived · True / False swapped · `1bf4807b7c059a1f`
  ```diff
  -{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
  +{"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=True
  ```
- `_projected_step` · survived · assignment: value to None · `63e70221104c93d8`
  ```diff
  -step["renames"] = sorted(renames, key=lambda r: r["proof"]["sentence_sha256"])
  +step["renames"] = None
  ```
- `_projected_step` · survived · assignment: value to None · `adf13121649043cb`
  ```diff
  -gaps = sorted(g.split(": ", 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  +gaps = None  # type: ignore[attr-defined]
  ```
- `_projected_step` · survived · argument: replaced by None · `3b176844239c5b79`
  ```diff
  -gaps = sorted(g.split(": ", 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  +gaps = sorted(g.split(None, 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  ```
- `_projected_step` · survived · argument: replaced by None · `eaca3305d42564f4`
  ```diff
  -gaps = sorted(g.split(": ", 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  +gaps = sorted(g.split(": ", None)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  ```
- `_projected_step` · survived · argument: removed · `69d501ceec9f2cfc`
  ```diff
  -gaps = sorted(g.split(": ", 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  +gaps = sorted(g.split(1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  ```
- `_projected_step` · survived · argument: removed · `c1734caf4f6ddb35`
  ```diff
  -gaps = sorted(g.split(": ", 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  +gaps = sorted(g.split(": ", )[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  ```
- `_projected_step` · survived · string method swapped · `808370fbb8bfe424`
  ```diff
  -gaps = sorted(g.split(": ", 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  +gaps = sorted(g.rsplit(": ", 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  ```
- `_projected_step` · survived · string: XX added · `9a10fbd387ec6c8b`
  ```diff
  -gaps = sorted(g.split(": ", 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  +gaps = sorted(g.split("XX: XX", 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  ```
- `_projected_step` · survived · number · `08b4c301a39cbaa2`
  ```diff
  -gaps = sorted(g.split(": ", 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  +gaps = sorted(g.split(": ", 2)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  ```
- `_projected_step` · survived · number · `9dcb3513329d56c7`
  ```diff
  -gaps = sorted(g.split(": ", 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
  +gaps = sorted(g.split(": ", 1)[1] for g in candidate["gaps"])  # type: ignore[attr-defined]
  ```
- `_projected_step` · survived · string: XX added · `c225a0344898ddf3`
  ```diff
  -return {**candidate, "step": step, "gaps": gaps}
  +return {**candidate, "step": step, "XXgapsXX": gaps}
  ```
- `_projected_step` · survived · string: upper case · `f5af6b0641a92f69`
  ```diff
  -return {**candidate, "step": step, "gaps": gaps}
  +return {**candidate, "step": step, "GAPS": gaps}
  ```
- `parse_rename` · survived · operator: and / or · `e1d4ce568d9dd3c4`
  ```diff
  -if source is None or target is None:
  +if source is None and target is None:
  ```
- `parse_rename` · survived · argument: replaced by None · `134d011d608b2d0f`
  ```diff
  -return ParsedRename(refusal=OUTSIDE)
  +return ParsedRename(refusal=None)
  ```
- `_partial` · survived · argument: replaced by None · `7aa2d238080e9ef1`
  ```diff
  -return ParsedRename(refusal=PARTIAL)
  +return ParsedRename(refusal=None)
  ```
- `adapt` · survived · argument: replaced by None · `f31a12c9ee0a2141`
  ```diff
  -steps=steps, refusals=tuple(sorted(refusals, key=lambda r: (r.member, r.op_id)))
  +steps=steps, refusals=tuple(sorted(refusals, key=None))
  ```
- `adapt` · survived · argument: removed · `e94e617be023782f`
  ```diff
  -steps=steps, refusals=tuple(sorted(refusals, key=lambda r: (r.member, r.op_id)))
  +steps=steps, refusals=tuple(sorted(refusals, ))
  ```
- `adapt` · survived · lambda: body replaced · `a396e00917e6c368`
  ```diff
  -steps=steps, refusals=tuple(sorted(refusals, key=lambda r: (r.member, r.op_id)))
  +steps=steps, refusals=tuple(sorted(refusals, key=lambda r: None))
  ```
- `_adapted` · survived · argument: replaced by None · `9afe7ded185ea9a7`
  ```diff
  -return _refusal(event, None, UNKNOWN_MEMBER, read)
  +return _refusal(event, None, UNKNOWN_MEMBER, None)
  ```
- `_adapted` · survived · argument: removed · `5a67fc04eda60ef1`
  ```diff
  -return _refusal(event, None, UNKNOWN_MEMBER, read)
  +return _refusal(event, None, UNKNOWN_MEMBER, )
  ```
- `_adapted` · survived · argument: replaced by None · `9358d48d976fb466`
  ```diff
  -return _refusal(event, document, UNBOUND, read)
  +return _refusal(event, None, UNBOUND, read)
  ```
- `_adapted` · survived · argument: replaced by None · `9db9119331d354d3`
  ```diff
  -return _refusal(event, document, UNBOUND, read)
  +return _refusal(event, document, UNBOUND, None)
  ```
- `_adapted` · survived · argument: removed · `ae8ce7cf618817d2`
  ```diff
  -return _refusal(event, document, UNBOUND, read)
  +return _refusal(event, document, UNBOUND, )
  ```
- `_adapted` · survived · argument: replaced by None · `7974c3af9f598ef5`
  ```diff
  -return _refusal(event, document, parsed.refusal, parsed)
  +return _refusal(event, None, parsed.refusal, parsed)
  ```
- `_adapted` · survived · argument: replaced by None · `85d61bc17f2f4f4c`
  ```diff
  -return _refusal(event, document, parsed.refusal, parsed)
  +return _refusal(event, document, parsed.refusal, None)
  ```
- `_adapted` · survived · argument: removed · `043fa6e9339138c2`
  ```diff
  -return _refusal(event, document, parsed.refusal, parsed)
  +return _refusal(event, document, parsed.refusal, )
  ```
- `_adapted` · survived · argument: replaced by None · `54733e8934d740af`
  ```diff
  -return _refusal(event, document, reason, parsed)
  +return _refusal(event, None, reason, parsed)
  ```
- `_refusal` · survived · argument: replaced by None · `be7b6450859a843e`
  ```diff
  -member_sha256=None if document is None else document.sha256,
  +member_sha256=None,
  ```
- `_refusal` · survived · argument: replaced by None · `4394e9ebdd70ccba`
  ```diff
  -sentence_sha256=_sha(event.operation.instruction), act=event.operation.act,
  +sentence_sha256=_sha(event.operation.instruction), act=None,
  ```
- `_refusal` · survived · argument: replaced by None · `1644d4ae645789eb`
  ```diff
  -source=read.source, target=read.target, reason=reason, placement=read.placement,
  +source=read.source, target=read.target, reason=reason, placement=None,
  ```
- `_refusal` · survived · argument: replaced by None · `9cae1bf7bccb225a`
  ```diff
  -effective=timing.dated_on, known_from=timing.known_from,
  +effective=None, known_from=timing.known_from,
  ```
- `_refusal` · survived · argument: replaced by None · `8e9f11c04a51ae8f`
  ```diff
  -effective=timing.dated_on, known_from=timing.known_from,
  +effective=timing.dated_on, known_from=None,
  ```
- `_refusal` · survived · ternary: condition forced · `9b2218924cd140ba`
  ```diff
  -member_sha256=None if document is None else document.sha256,
  +member_sha256=None if (document is None) or True else document.sha256,
  ```
- `_sha` · survived · string: upper case · `eb6a33e40f582220`
  ```diff
  -return hashlib.sha256(text.encode("utf-8")).hexdigest()
  +return hashlib.sha256(text.encode("UTF-8")).hexdigest()
  ```
- `_digest` · survived · argument: replaced by None · `6d2cf32d63c94064`
  ```diff
  -return _sha(json.dumps(parts, ensure_ascii=False))
  +return _sha(json.dumps(parts, ensure_ascii=None))
  ```

</details>

<details><summary><code>src/lovspor/legaltime/step.py</code>: 6</summary>

- `_kind` · survived · ternary: condition forced · `5ab686c6435c54f5`
  ```diff
  -return op.kind if isinstance(op, Finer) else type(op).__name__.lower()
  +return op.kind if (isinstance(op, Finer)) and False else type(op).__name__.lower()
  ```
- `_ambiguous` · survived · operator: and / or · `1d5fedef24d2535a`
  ```diff
  -refill = isinstance(op, Insertion) and op.label in vacated
  +refill = isinstance(op, Insertion) or op.label in vacated
  ```
- `_resolved` · survived · ternary: condition forced · `5a055173cb2f92b1`
  ```diff
  -return op.model_copy(update={"placement": None if isinstance(proven, str) else proven})
  +return op.model_copy(update={"placement": None if (isinstance(proven, str)) and False else proven})
  ```
- `apply_step` · survived · argument: replaced by None · `b7a41ddd41ae9f86`
  ```diff
  -ordered = tuple(sorted(refusals, key=lambda r: (r.op_id, r.kind, r.reason, str(r.address))))
  +ordered = tuple(sorted(refusals, key=lambda r: (r.op_id, r.kind, r.reason, str(None))))
  ```
- `apply_step` · survived · operator: and / or · `f0ed7ff12b84b14a`
  ```diff
  -if step.effective is None or not accepted:
  +if step.effective is None and not accepted:
  ```
- `_accept` · timeout · kind not recorded · `72aa94988b673f1d`
  ```diff
  -survivors = [op for index, op in enumerate(survivors) if index not in found]
  +survivors = [op for index, op in enumerate(survivors) if index in found]
  ```

</details>

<details><summary><code>src/lovspor/legaltime/step_evidence.py</code>: 58</summary>

- `Step0.canonical_json` · survived · argument: replaced by None · `2552228ea6c27125`
  ```diff
  -self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
  +self.model_dump(mode="json"), sort_keys=True, ensure_ascii=None, separators=(",", ":")
  ```
- `_check_source` · survived · operator: and / or · `2e024358a110fc75`
  ```diff
  -if identity is None or identity.intervals[0].start.sentence_sha256 != digest:
  +if identity is None and identity.intervals[0].start.sentence_sha256 != digest:
  ```
- `node_digest` · survived · argument: replaced by None · `99726007a428f700`
  ```diff
  -canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
  +canonical = json.dumps(record, sort_keys=None, ensure_ascii=False, separators=(",", ":"))
  ```
- `node_digest` · survived · argument: replaced by None · `508e62fe2a87abc4`
  ```diff
  -canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
  +canonical = json.dumps(record, sort_keys=True, ensure_ascii=None, separators=(",", ":"))
  ```
- `node_digest` · survived · argument: removed · `ccc06a991d31581a`
  ```diff
  -canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
  +canonical = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
  ```
- `node_digest` · survived · True / False swapped · `d8e7fba935f1eda6`
  ```diff
  -canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
  +canonical = json.dumps(record, sort_keys=False, ensure_ascii=False, separators=(",", ":"))
  ```
- `node_digest` · survived · string: upper case · `90e735a8ba898b32`
  ```diff
  -return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
  +return hashlib.sha256(canonical.encode("UTF-8")).hexdigest()
  ```
- `build_step0` · survived · argument: replaced by None · `5f7210c562ab98d1`
  ```diff
  -return _refused(document, act, [Problem(node=None, reason=OUTLINE, detail=str(error))])
  +return _refused(document, act, [Problem(node=None, reason=OUTLINE, detail=None)])
  ```
- `build_step0` · survived · argument: removed · `8181a77310072d18`
  ```diff
  -return _refused(document, act, [Problem(node=None, reason=OUTLINE, detail=str(error))])
  +return _refused(document, act, [Problem(node=None, reason=OUTLINE, )])
  ```
- `build_step0` · survived · argument: replaced by None · `6e0f951659eb145f`
  ```diff
  -return _refused(document, act, [Problem(node=None, reason=OUTLINE, detail=str(error))])
  +return _refused(document, act, [Problem(node=None, reason=OUTLINE, detail=str(None))])
  ```
- `_header_problems` · survived · argument: replaced by None · `b1e72c766faa5d28`
  ```diff
  -found.append(Problem(node=None, reason=NOT_ENACTING, detail=document.header.refid))
  +found.append(Problem(node=None, reason=NOT_ENACTING, detail=None))
  ```
- `_header_problems` · survived · argument: removed · `cc173d257160e98e`
  ```diff
  -found.append(Problem(node=None, reason=NOT_ENACTING, detail=document.header.refid))
  +found.append(Problem(node=None, reason=NOT_ENACTING, ))
  ```
- `_node_problems` · survived · argument: replaced by None · `7b0e5bf85cd84e26`
  ```diff
  -for provision, key in zip(document.provisions, keys, strict=True):
  +for provision, key in zip(document.provisions, keys, strict=None):
  ```
- `_node_problems` · survived · argument: removed · `6672dbad91fc3354`
  ```diff
  -for provision, key in zip(document.provisions, keys, strict=True):
  +for provision, key in zip(document.provisions, keys, ):
  ```
- `_node_problems` · survived · True / False swapped · `da728bcf10ee49c0`
  ```diff
  -for provision, key in zip(document.provisions, keys, strict=True):
  +for provision, key in zip(document.provisions, keys, strict=False):
  ```
- `_node_problems` · survived · argument: replaced by None · `b57c1b62cd0d11a7`
  ```diff
  -found.append(Problem(node=node, reason=MISLABELLED, detail=provision.label))
  +found.append(Problem(node=node, reason=MISLABELLED, detail=None))
  ```
- `_node_problems` · survived · argument: removed · `66f736e98a3479be`
  ```diff
  -found.append(Problem(node=node, reason=MISLABELLED, detail=provision.label))
  +found.append(Problem(node=node, reason=MISLABELLED, ))
  ```
- `_hierarchy_problems` · survived · string: XX added · `02ddba14d435d74b`
  ```diff
  -if kinds not in ([], ["chapter"], ["part"], ["chapter", "part"]):
  +if kinds not in ([], ["XXchapterXX"], ["part"], ["chapter", "part"]):
  ```
- `_hierarchy_problems` · survived · string: upper case · `17ae840323bf10f5`
  ```diff
  -if kinds not in ([], ["chapter"], ["part"], ["chapter", "part"]):
  +if kinds not in ([], ["CHAPTER"], ["part"], ["chapter", "part"]):
  ```
- `_hierarchy_problems` · survived · assignment: value to None · `647283427906a0ce`
  ```diff
  -detail = ">".join(str(e.name) for e in enclosures)
  +detail = None
  ```
- `_hierarchy_problems` · survived · string: XX added · `1192baa72c8a4698`
  ```diff
  -detail = ">".join(str(e.name) for e in enclosures)
  +detail = "XX>XX".join(str(e.name) for e in enclosures)
  ```
- `_hierarchy_problems` · survived · argument: replaced by None · `5e2fe8e85c026667`
  ```diff
  -detail = ">".join(str(e.name) for e in enclosures)
  +detail = ">".join(str(None) for e in enclosures)
  ```
- `_hierarchy_problems` · survived · argument: replaced by None · `ac1e02fd414bd67f`
  ```diff
  -return [Problem(node=node, reason=UNREAD_SECTION, detail=detail)]
  +return [Problem(node=None, reason=UNREAD_SECTION, detail=detail)]
  ```
- `_hierarchy_problems` · survived · argument: replaced by None · `c1f37a5bd21f55c5`
  ```diff
  -return [Problem(node=node, reason=UNREAD_SECTION, detail=detail)]
  +return [Problem(node=node, reason=UNREAD_SECTION, detail=None)]
  ```
- `_hierarchy_problems` · survived · argument: removed · `4004511a799f4f50`
  ```diff
  -return [Problem(node=node, reason=UNREAD_SECTION, detail=detail)]
  +return [Problem(node=node, reason=UNREAD_SECTION, )]
  ```
- `_exact_key` · survived · operator: and / or · `0de2c422e0ed4232`
  ```diff
  -if key is None or not _PRINTED.fullmatch(provision.label) or provision.key != key:
  +if key is None and not _PRINTED.fullmatch(provision.label) or provision.key != key:
  ```
- `build_operation` · survived · argument: replaced by None · `e09b50be2c3a9430`
  ```diff
  -return _unbuilt(event, document, reason or UNKNOWN_MEMBER)
  +return _unbuilt(event, None, reason or UNKNOWN_MEMBER)
  ```
- `_typed` · survived · argument: replaced by None · `dcb80e16c7321bb6`
  ```diff
  -return _unbuilt(event, document, RANGE)
  +return _unbuilt(event, None, RANGE)
  ```
- `_typed` · survived · argument: replaced by None · `b77c4bd660e921ac`
  ```diff
  -return _unbuilt(event, document, f"{KIND}: {op.kind}")
  +return _unbuilt(event, None, f"{KIND}: {op.kind}")
  ```
- `_typed` · survived · argument: replaced by None · `e6b476e7ff5359ad`
  ```diff
  -return _unbuilt(event, document, NO_SECTION)
  +return _unbuilt(event, None, NO_SECTION)
  ```
- `_section_operation` · survived · argument: replaced by None · `8014c9028a8561fc`
  ```diff
  -return _unbuilt(event, document, UNHEADED)
  +return _unbuilt(event, None, UNHEADED)
  ```
- `_section_operation` · survived · argument: replaced by None · `24884b236deb1cec`
  ```diff
  -return _insertion(event, document, payload, proof)
  +return _insertion(event, None, payload, proof)
  ```
- `_insertion` · survived · argument: replaced by None · `f6463033636197b3`
  ```diff
  -return _unbuilt(event, document, problem, evidence)
  +return _unbuilt(event, None, problem, evidence)
  ```
- `_unstated` · survived · ternary: condition forced · `36ca1e110c790535`
  ```diff
  -return UNREAD_CHAPTER if payload.chapter is None and payload.part is None else None
  +return UNREAD_CHAPTER if (payload.chapter is None and payload.part is None) and False else None
  ```
- `_unstated` · survived · is / in negated · `c8debf711f75db36`
  ```diff
  -return UNREAD_CHAPTER if payload.chapter is None and payload.part is None else None
  +return UNREAD_CHAPTER if payload.chapter is None and payload.part is not None else None
  ```
- `_inherited` · survived · string: XX added · `2f278b90288682e9`
  ```diff
  -name = (enclosure.name or "").lower()
  +name = (enclosure.name or "XXXX").lower()
  ```
- `_inherited` · survived · operator: and / or · `2b61cb59e39c4717`
  ```diff
  -if enclosure.tag != "section" or not name:
  +if enclosure.tag != "section" and not name:
  ```
- `_inherited` · survived · operator: and / or · `77f59adda1573959`
  ```diff
  -if chapter is None and _CHAPTER_NAME.fullmatch(name):
  +if chapter is None or _CHAPTER_NAME.fullmatch(name):
  ```
- `_inherited` · survived · operator: and / or · `3d3cb3b719d9341b`
  ```diff
  -elif part is None and _PART_NAME.fullmatch(name):
  +elif part is None or _PART_NAME.fullmatch(name):
  ```
- `_inherited` · survived · is / in negated · `8426a606ff2f8765`
  ```diff
  -elif part is None and _PART_NAME.fullmatch(name):
  +elif part is not None and _PART_NAME.fullmatch(name):
  ```
- `_inherited` · survived · argument: replaced by None · `a5332acccd982f50`
  ```diff
  -elif part is None and _PART_NAME.fullmatch(name):
  +elif part is None and _PART_NAME.fullmatch(None):
  ```
- `_inherited` · survived · assignment: value to None · `3db2e8d017802575`
  ```diff
  -part = name
  +part = None
  ```
- `_inherited` · survived · argument: replaced by None · `f54cd6c004862213`
  ```diff
  -return Pair(chapter=chapter, part=part)
  +return Pair(chapter=chapter, part=None)
  ```
- `_inherited` · survived · argument: removed · `bbeb28ff99fe88d6`
  ```diff
  -return Pair(chapter=chapter, part=part)
  +return Pair(chapter=chapter, )
  ```
- `_proven` · survived · argument: replaced by None · `1bff90e8a700a7d3`
  ```diff
  -raise CandidateError(f"an undated event is never built: {event.operation.id}")
  +raise CandidateError(None)
  ```
- `_unbuilt` · survived · argument: replaced by None · `3e3c233bb8b17d20`
  ```diff
  -member_sha256=None if document is None else document.sha256,
  +member_sha256=None,
  ```
- `_unbuilt` · survived · argument: replaced by None · `fbe3fa4101bf00ca`
  ```diff
  -effective=timing.dated_on, known_from=timing.known_from, placement=evidence,
  +effective=timing.dated_on, known_from=None, placement=evidence,
  ```
- `_unbuilt` · survived · ternary: condition forced · `8e60ac3d466ecfee`
  ```diff
  -member_sha256=None if document is None else document.sha256,
  +member_sha256=None if (document is None) or True else document.sha256,
  ```
- `Build.canonical_json` · survived · argument: replaced by None · `5c747f3abc7cbf7f`
  ```diff
  -return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
  +return json.dumps(self.model_dump(mode="json"), sort_keys=None, ensure_ascii=False)
  ```
- `Build.canonical_json` · survived · argument: replaced by None · `7c356c298c7c9c3a`
  ```diff
  -return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
  +return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=None)
  ```
- `Build.canonical_json` · survived · argument: removed · `f8aedd15909b8f8c`
  ```diff
  -return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
  +return json.dumps(self.model_dump(mode="json"), ensure_ascii=False)
  ```
- `Build.canonical_json` · survived · argument: removed · `9231e2828a8ebb6f`
  ```diff
  -return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
  +return json.dumps(self.model_dump(mode="json"), sort_keys=True, )
  ```
- `Build.canonical_json` · survived · True / False swapped · `3ed9f703d2ec57fa`
  ```diff
  -return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
  +return json.dumps(self.model_dump(mode="json"), sort_keys=False, ensure_ascii=False)
  ```
- `Build.canonical_json` · survived · True / False swapped · `e1ca00389e59fc18`
  ```diff
  -return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
  +return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)
  ```
- `build_candidates` · survived · operator: and / or · `a132831531b68d7c`
  ```diff
  -ordered = sorted(events, key=lambda e: (e.member, e.operation.id, e.operation.act or ""))
  +ordered = sorted(events, key=lambda e: (e.member, e.operation.id, e.operation.act and ""))
  ```
- `build_candidates` · survived · string: XX added · `037cd3b2e91915c3`
  ```diff
  -ordered = sorted(events, key=lambda e: (e.member, e.operation.id, e.operation.act or ""))
  +ordered = sorted(events, key=lambda e: (e.member, e.operation.id, e.operation.act or "XXXX"))
  ```
- `build_candidates` · survived · argument: removed · `b359131169b74ac6`
  ```diff
  -rename_refusals=tuple(o for _, o in outcomes if isinstance(o, RenameRefusal)),
  ```
- `_candidate` · survived · argument: removed · `d239e85bc3288809`
  ```diff
  -renames=tuple(o for o in ops if isinstance(o, Rename)),
  ```

</details>

<details><summary><code>src/lovspor/legaltime/structure.py</code>: 18</summary>

- `Structure.parts` · survived · augmented assignment to plain · `0ab310b88bf49229`
  ```diff
  -stated |= {e.pair.part for m in self.memberships for e in m.events if e.pair}
  +stated = {e.pair.part for m in self.memberships for e in m.events if e.pair}
  ```
- `Structure.canonical_json` · survived · argument: replaced by None · `474c2e449afdd88c`
  ```diff
  -self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
  +self.model_dump(mode="json"), sort_keys=None, ensure_ascii=False, separators=(",", ":")
  ```
- `Structure.canonical_json` · survived · argument: replaced by None · `ea83cd505e2eaf46`
  ```diff
  -self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
  +self.model_dump(mode="json"), sort_keys=True, ensure_ascii=None, separators=(",", ":")
  ```
- `Structure.canonical_json` · survived · argument: replaced by None · `3dedceb8d8d0c1a7`
  ```diff
  -self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
  +self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=None
  ```
- `Structure.canonical_json` · survived · argument: removed · `355612fd8fb7a64c`
  ```diff
  -self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
  +self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
  ```
- `Structure.canonical_json` · survived · argument: removed · `d2816f86d7261d8e`
  ```diff
  -self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
  +self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
  ```
- `Structure.canonical_json` · survived · argument: removed · `fe264aa17a63fb46`
  ```diff
  -self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
  -)
  +self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, )
  ```
- `Structure.canonical_json` · survived · True / False swapped · `c9aa25940b00c45f`
  ```diff
  -self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
  +self.model_dump(mode="json"), sort_keys=False, ensure_ascii=False, separators=(",", ":")
  ```
- `Structure.canonical_json` · survived · True / False swapped · `7ffdf359e0943e84`
  ```diff
  -self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
  +self.model_dump(mode="json"), sort_keys=True, ensure_ascii=True, separators=(",", ":")
  ```
- `enacted_structure` · survived · assignment: value to None · `da96e70ab95fc3f6`
  ```diff
  -missing = sorted(set(labels) ^ set(stamps))
  +missing = None
  ```
- `enacted_structure` · survived · operator: bitwise · `ca75d4590daba346`
  ```diff
  -missing = sorted(set(labels) ^ set(stamps))
  +missing = sorted(set(labels) & set(stamps))
  ```
- `enacted_structure_by_id` · survived · string: XX added · `c977c35b826942f2`
  ```diff
  -raise StructureError("step 0 stamps enacted identities only")
  +raise StructureError("XXstep 0 stamps enacted identities onlyXX")
  ```
- `enacted_structure_by_id` · survived · operator: bitwise · `e012a71e98b58978`
  ```diff
  -f"enacting structure and step 0 disagree on {sorted(set(ids) ^ set(stamps))}"
  +f"enacting structure and step 0 disagree on {sorted(set(ids) & set(stamps))}"
  ```
- `_outline` · survived · argument: replaced by None · `ddbaec8b97de6d5e`
  ```diff
  -for witness, pair, proof in sorted(stated, key=lambda item: item[0]):
  +for witness, pair, proof in sorted(stated, key=None):
  ```
- `_outline` · survived · argument: removed · `90ee09b886913c72`
  ```diff
  -for witness, pair, proof in sorted(stated, key=lambda item: item[0]):
  +for witness, pair, proof in sorted(stated, ):
  ```
- `placed_pair` · survived · argument: removed · `d57e59bbbb7c8345`
  ```diff
  -return Pair(chapter=None, part=read.part)
  +return Pair(part=read.part)
  ```
- `inserted_pair` · survived · ternary: condition forced · `324ae69cdb6075f5`
  ```diff
  -entry = None if stated.chapter is None else structure.enclosing(stated.chapter)
  +entry = None if (stated.chapter is None) and False else structure.enclosing(stated.chapter)
  ```
- `paired` · survived · string: XX added · `09f9b7e4eddcc3fe`
  ```diff
  -raise StructureError("the label ledger and the structure map name other identities")
  +raise StructureError("XXthe label ledger and the structure map name other identitiesXX")
  ```

</details>

<details><summary><code>src/lovspor/publication_seal.py</code>: 48</summary>

- `seal_message` · survived · string: upper case · `4b9f75a4522606f2`
  ```diff
  -return header.encode("ascii") + note
  +return header.encode("ASCII") + note
  ```
- `seal_payload` · survived · string: upper case · `9ac44e609dac8fbe`
  ```diff
  -return header.encode("ascii") + message
  +return header.encode("ASCII") + message
  ```
- `compose_seal` · survived · argument: replaced by None · `aed76b2226ab3f8e`
  ```diff
  -note = _git_out(repo, ["cat-file", "blob", blob], SealCompositionError)
  +note = _git_out(repo, ["cat-file", "blob", blob], None)
  ```
- `verify_seal` · survived · string: XX added · `44c07b246249ab02`
  ```diff
  -raise SealVerificationError("; ".join(failures))
  +raise SealVerificationError("XX; XX".join(failures))
  ```
- `_verify_tag` · survived · string: upper case · `171668280a74709b`
  ```diff
  -oid = _git_out(repo, ["rev-parse", "--verify", "--quiet", ref], SealVerificationError)
  +oid = _git_out(repo, ["rev-parse", "--verify", "--QUIET", ref], SealVerificationError)
  ```
- `_verify_tag` · survived · argument: replaced by None · `ef2638358514c9bb`
  ```diff
  -kind = _git_out(repo, ["cat-file", "-t", oid_text], SealVerificationError).strip()
  +kind = _git_out(repo, ["cat-file", "-t", oid_text], None).strip()
  ```
- `_verify_tag` · survived · argument: replaced by None · `1ecb2a9ccd60dd6e`
  ```diff
  -_check_message(message, commit, ref)
  +_check_message(message, commit, None)
  ```
- `_verify_signature` · survived · string: XX added · `19baa98c85c357b6`
  ```diff
  -config = ["-c", "gpg.ssh.program=ssh-keygen", "-c", f"gpg.ssh.allowedSignersFile={signers}"]
  +config = ["-c", "XXgpg.ssh.program=ssh-keygenXX", "-c", f"gpg.ssh.allowedSignersFile={signers}"]
  ```
- `_verify_signature` · survived · string: upper case · `4c0c62eb0f4b3e56`
  ```diff
  -config = ["-c", "gpg.ssh.program=ssh-keygen", "-c", f"gpg.ssh.allowedSignersFile={signers}"]
  +config = ["-c", "GPG.SSH.PROGRAM=SSH-KEYGEN", "-c", f"gpg.ssh.allowedSignersFile={signers}"]
  ```
- `_verify_signature` · survived · string: XX added · `f7cf82e41049bce6`
  ```diff
  -output = result.stderr.decode(errors="replace")
  +output = result.stderr.decode(errors="XXreplaceXX")
  ```
- `_verify_signature` · survived · string: upper case · `3b17d6c7b3387d17`
  ```diff
  -output = result.stderr.decode(errors="replace")
  +output = result.stderr.decode(errors="REPLACE")
  ```
- `_verify_signature` · survived · operator: and / or · `5a29a0cfcb518bd4`
  ```diff
  -if result.returncode != 0 or match is None:
  +if result.returncode != 0 and match is None:
  ```
- `_verify_signature` · survived · assignment: value to None · `f4ccff08cd34359b`
  ```diff
  -reason = " ".join(output.split()) or f"exit {result.returncode}"
  +reason = None
  ```
- `_verify_signature` · survived · operator: and / or · `ef4a6091dd0ec187`
  ```diff
  -reason = " ".join(output.split()) or f"exit {result.returncode}"
  +reason = " ".join(output.split()) and f"exit {result.returncode}"
  ```
- `_verify_signature` · survived · string: XX added · `3f6222808ae3d8d7`
  ```diff
  -reason = " ".join(output.split()) or f"exit {result.returncode}"
  +reason = "XX XX".join(output.split()) or f"exit {result.returncode}"
  ```
- `_parse_seal` · survived · string method swapped · `d6ac2a4dd44ddf87`
  ```diff
  -header, separator, attestation = message.partition(b"\n\n")
  +header, separator, attestation = message.rpartition(b"\n\n")
  ```
- `_parse_seal` · survived · argument: removed · `fe63ca57ce588594`
  ```diff
  -lines = header.decode("utf-8", errors="replace").split("\n")
  +lines = header.decode(errors="replace").split("\n")
  ```
- `_parse_seal` · survived · argument: removed · `2e9ca2e68592b035`
  ```diff
  -lines = header.decode("utf-8", errors="replace").split("\n")
  +lines = header.decode("utf-8", ).split("\n")
  ```
- `_parse_seal` · survived · string: upper case · `dbc7433690bd7f97`
  ```diff
  -lines = header.decode("utf-8", errors="replace").split("\n")
  +lines = header.decode("UTF-8", errors="replace").split("\n")
  ```
- `_parse_seal` · survived · string: XX added · `4262b6ea19c029d0`
  ```diff
  -lines = header.decode("utf-8", errors="replace").split("\n")
  +lines = header.decode("utf-8", errors="XXreplaceXX").split("\n")
  ```
- `_parse_seal` · survived · string: upper case · `67fddbffb6c0f168`
  ```diff
  -lines = header.decode("utf-8", errors="replace").split("\n")
  +lines = header.decode("utf-8", errors="REPLACE").split("\n")
  ```
- `_parse_seal` · survived · string method swapped · `9517fa5a14e32d44`
  ```diff
  -fields = dict(line.partition(" ")[::2] for line in lines[1:])
  +fields = dict(line.rpartition(" ")[::2] for line in lines[1:])
  ```
- `_tag_object` · survived · argument: replaced by None · `d0ac7fff21c86e12`
  ```diff
  -raw = _git_out(repo, ["cat-file", "tag", oid], PublicationSealError)
  +raw = _git_out(repo, ["cat-file", "tag", oid], None)
  ```
- `_tag_object` · survived · argument: removed · `43865af5b9b4f4a3`
  ```diff
  -lines = header.decode("utf-8", errors="replace").split("\n")
  +lines = header.decode(errors="replace").split("\n")
  ```
- `_tag_object` · survived · argument: removed · `1de162e635913d86`
  ```diff
  -lines = header.decode("utf-8", errors="replace").split("\n")
  +lines = header.decode("utf-8", ).split("\n")
  ```
- `_tag_object` · survived · string: upper case · `e3cb7efee4e1a801`
  ```diff
  -lines = header.decode("utf-8", errors="replace").split("\n")
  +lines = header.decode("UTF-8", errors="replace").split("\n")
  ```
- `_tag_object` · survived · string: XX added · `98a541d529356ce9`
  ```diff
  -lines = header.decode("utf-8", errors="replace").split("\n")
  +lines = header.decode("utf-8", errors="XXreplaceXX").split("\n")
  ```
- `_tag_object` · survived · string: upper case · `63b98c64976104f1`
  ```diff
  -lines = header.decode("utf-8", errors="replace").split("\n")
  +lines = header.decode("utf-8", errors="REPLACE").split("\n")
  ```
- `_tag_object` · survived · string method swapped · `8d8f2b9088f0f861`
  ```diff
  -return dict(line.partition(" ")[::2] for line in lines), message
  +return dict(line.rpartition(" ")[::2] for line in lines), message
  ```
- `_sync_tags` · survived · argument: replaced by None · `6bab321abe241db4`
  ```diff
  -listed = _git_out(repo, [*args, SEAL_TAG_PREFIX], PublicationSealError).decode()
  +listed = _git_out(repo, [*args, SEAL_TAG_PREFIX], None).decode()
  ```
- `_sync_tags` · survived · argument: replaced by None · `1d5b398efbb57618`
  ```diff
  -pairs = (line.split(" ", 1) for line in listed.splitlines() if line)
  +pairs = (line.split(None, 1) for line in listed.splitlines() if line)
  ```
- `_sync_tags` · survived · argument: removed · `3e7f889e4bd78a22`
  ```diff
  -pairs = (line.split(" ", 1) for line in listed.splitlines() if line)
  +pairs = (line.split(" ", ) for line in listed.splitlines() if line)
  ```
- `_sync_tags` · survived · string method swapped · `ebf5acd094f26b51`
  ```diff
  -pairs = (line.split(" ", 1) for line in listed.splitlines() if line)
  +pairs = (line.rsplit(" ", 1) for line in listed.splitlines() if line)
  ```
- `_sync_tags` · survived · number · `7e2d62379e97ba40`
  ```diff
  -pairs = (line.split(" ", 1) for line in listed.splitlines() if line)
  +pairs = (line.split(" ", 2) for line in listed.splitlines() if line)
  ```
- `_require_signers` · survived · argument: replaced by None · `191add991a5e6e31`
  ```diff
  -text = path.read_text(encoding="utf-8")
  +text = path.read_text(encoding=None)
  ```
- `_require_signers` · survived · string: upper case · `2055b432f6535914`
  ```diff
  -text = path.read_text(encoding="utf-8")
  +text = path.read_text(encoding="UTF-8")
  ```
- `_require_signers` · survived · string method swapped · `949ff1a5eb12d92f`
  ```diff
  -line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
  +line for line in text.splitlines() if line.strip() and not line.rstrip().startswith("#")
  ```
- `_require_signers` · survived · string: XX added · `b80228b478bf2d54`
  ```diff
  -"the owner commits the publication-seal public key"
  +"XXthe owner commits the publication-seal public keyXX"
  ```
- `_require_signers` · survived · string: upper case · `3439f29f88e27a17`
  ```diff
  -"the owner commits the publication-seal public key"
  +"THE OWNER COMMITS THE PUBLICATION-SEAL PUBLIC KEY"
  ```
- `_resolve_commit` · survived · string: upper case · `3a9b14c57ad55214`
  ```diff
  -args = ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}"]
  +args = ["rev-parse", "--verify", "--QUIET", "--end-of-options", f"{rev}^{{commit}}"]
  ```
- `_resolve_commit` · survived · string: upper case · `6a6748b4ad3416eb`
  ```diff
  -args = ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}"]
  +args = ["rev-parse", "--verify", "--quiet", "--END-OF-OPTIONS", f"{rev}^{{commit}}"]
  ```
- `_git_out` · survived · assignment: value to None · `ebee3ee03a1abedf`
  ```diff
  -stderr = " ".join(result.stderr.decode(errors="replace").split())
  +stderr = None
  ```
- `_git_out` · survived · string: XX added · `67cbe34abb429b07`
  ```diff
  -stderr = " ".join(result.stderr.decode(errors="replace").split())
  +stderr = "XX XX".join(result.stderr.decode(errors="replace").split())
  ```
- `_git_out` · survived · string: XX added · `adc8342ec63c5007`
  ```diff
  -stderr = " ".join(result.stderr.decode(errors="replace").split())
  +stderr = " ".join(result.stderr.decode(errors="XXreplaceXX").split())
  ```
- `_git_out` · survived · string: upper case · `9703966f6ea97ece`
  ```diff
  -stderr = " ".join(result.stderr.decode(errors="replace").split())
  +stderr = " ".join(result.stderr.decode(errors="REPLACE").split())
  ```
- `_git_out` · survived · string: XX added · `4ccad327bf228271`
  ```diff
  -raise error(f"git {' '.join(args)} (cwd={repo}) exited {result.returncode}: {stderr}")
  +raise error(f"git {'XX XX'.join(args)} (cwd={repo}) exited {result.returncode}: {stderr}")
  ```
- `_git` · survived · argument: replaced by None · `5f75cb7875e29272`
  ```diff
  -check=False,
  +check=None,
  ```
- `_git` · survived · argument: removed · `91c64d1b41fda8ef`
  ```diff
  -check=False,
  -)
  +)
  ```

</details>

<details><summary><code>src/lovspor/regjeringen_fetch/normalize.py</code>: 72</summary>

- `_start_tags` · survived · assignment: value to None · `19e79cb9cef93251`
  ```diff
  -position = 0
  +position = None
  ```
- `_start_tags` · survived · operator: arithmetic · `74078f43d4e85efe`
  ```diff
  -while position != -1 and (opening := raw.find(b"<", position)) != -1:
  +while position != +1 and (opening := raw.find(b"<", position)) != -1:
  ```
- `_start_tags` · survived · number · `1322603cb0054ee4`
  ```diff
  -while position != -1 and (opening := raw.find(b"<", position)) != -1:
  +while position != -2 and (opening := raw.find(b"<", position)) != -1:
  ```
- `_after_start_tag` · survived · number · `44959cf831767286`
  ```diff
  -return -1
  +return -2
  ```
- `_after_start_tag` · survived · argument: removed · `824a3a3873952d7f`
  ```diff
  -found = closing.search(raw, position)
  +found = closing.search(raw, )
  ```
- `_after_start_tag` · survived · number · `2a1554944f346821`
  ```diff
  -return -1 if found is None else found.start()
  +return -2 if found is None else found.start()
  ```
- `_skip_non_start_tag` · survived · string: XX added · `b37d1f0ba6195f2b`
  ```diff
  -if raw.startswith(b"<!--", opening):
  +if raw.startswith(b"XX<!--XX", opening):
  ```
- `_skip_non_start_tag` · survived · string method swapped · `a2b39bd0458325eb`
  ```diff
  -closing = raw.find(b"-->", opening + 4)
  +closing = raw.rfind(b"-->", opening + 4)
  ```
- `_skip_non_start_tag` · survived · number · `d0b9739e52f910b2`
  ```diff
  -closing = raw.find(b"-->", opening + 4)
  +closing = raw.find(b"-->", opening + 5)
  ```
- `_skip_non_start_tag` · survived · number · `fab9c64b58539d2b`
  ```diff
  -return -1 if closing == -1 else closing + 3
  +return -2 if closing == -1 else closing + 3
  ```
- `_skip_non_start_tag` · survived · operator: arithmetic · `52b48872d476a432`
  ```diff
  -return -1 if closing == -1 else closing + 3
  +return -1 if closing == -1 else closing - 3
  ```
- `_skip_non_start_tag` · survived · string: XX added · `d8c5282794695fae`
  ```diff
  -if after == b"/" and raw[opening + 2 : opening + 3] in _LETTER_BYTES:
  +if after == b"XX/XX" and raw[opening + 2 : opening + 3] in _LETTER_BYTES:
  ```
- `_skip_non_start_tag` · survived · operator: arithmetic · `5dc11ce97f5eebb6`
  ```diff
  -if after == b"/" and raw[opening + 2 : opening + 3] in _LETTER_BYTES:
  +if after == b"/" and raw[opening - 2 : opening + 3] in _LETTER_BYTES:
  ```
- `_skip_non_start_tag` · survived · number · `f2f7150162f4fcbd`
  ```diff
  -if after == b"/" and raw[opening + 2 : opening + 3] in _LETTER_BYTES:
  +if after == b"/" and raw[opening + 3 : opening + 3] in _LETTER_BYTES:
  ```
- `_skip_non_start_tag` · survived · operator: arithmetic · `6a21c46b0279a736`
  ```diff
  -if after == b"/" and raw[opening + 2 : opening + 3] in _LETTER_BYTES:
  +if after == b"/" and raw[opening + 2 : opening - 3] in _LETTER_BYTES:
  ```
- `_skip_non_start_tag` · survived · number · `48716b12a051ff5c`
  ```diff
  -if after == b"/" and raw[opening + 2 : opening + 3] in _LETTER_BYTES:
  +if after == b"/" and raw[opening + 2 : opening + 4] in _LETTER_BYTES:
  ```
- `_skip_non_start_tag` · survived · is / in negated · `bd797b31eac87679`
  ```diff
  -if after == b"/" and raw[opening + 2 : opening + 3] in _LETTER_BYTES:
  +if after == b"/" and raw[opening + 2 : opening + 3] not in _LETTER_BYTES:
  ```
- `_skip_non_start_tag` · survived · string: XX added · `3f2ca02600c700e2`
  ```diff
  -if after in (b"!", b"?", b"/"):
  +if after in (b"!", b"?", b"XX/XX"):
  ```
- `_skip_non_start_tag` · survived · number · `b47d87c2d4d55343`
  ```diff
  -closing = raw.find(b">", opening + 2)
  +closing = raw.find(b">", opening + 3)
  ```
- `_skip_non_start_tag` · survived · ternary: condition forced · `2ad3aec30a0fb9a5`
  ```diff
  -return -1 if closing == -1 else closing + 1
  +return -1 if (closing == -1) and False else closing + 1
  ```
- `_skip_non_start_tag` · survived · operator: arithmetic · `4bfde39c9b98b8c1`
  ```diff
  -return -1 if closing == -1 else closing + 1
  +return +1 if closing == -1 else closing + 1
  ```
- `_skip_non_start_tag` · survived · number · `ad4886d17e4f518e`
  ```diff
  -return -1 if closing == -1 else closing + 1
  +return -2 if closing == -1 else closing + 1
  ```
- `_skip_non_start_tag` · survived · operator: arithmetic · `556ba3177c2e0402`
  ```diff
  -return -1 if closing == -1 else closing + 1
  +return -1 if closing == +1 else closing + 1
  ```
- `_skip_non_start_tag` · survived · number · `e27b98b32996b3ed`
  ```diff
  -return -1 if closing == -1 else closing + 1
  +return -1 if closing == -2 else closing + 1
  ```
- `_skip_non_start_tag` · survived · operator: arithmetic · `0f9a6fd9736ca681`
  ```diff
  -return -1 if closing == -1 else closing + 1
  +return -1 if closing == -1 else closing - 1
  ```
- `_skip_non_start_tag` · survived · number · `921ce7a2098f8900`
  ```diff
  -return -1 if closing == -1 else closing + 1
  +return -1 if closing == -1 else closing + 2
  ```
- `_parse_tag` · survived · number · `6e17c6fc03646def`
  ```diff
  -return None, opening + 1
  +return None, opening + 2
  ```
- `_parse_tag` · survived · number · `6de7efac52f0cfdf`
  ```diff
  -end = _skip(raw, opening + 1, _TAG_NAME)
  +end = _skip(raw, opening + 2, _TAG_NAME)
  ```
- `_parse_tag` · survived · operator: arithmetic · `0338e5f08f0bf054`
  ```diff
  -return _Tag(name, tuple(attributes)), after_space + 1
  +return _Tag(name, tuple(attributes)), after_space - 1
  ```
- `_parse_tag` · survived · operator: arithmetic · `60ff35c3311268d7`
  ```diff
  -return _Tag(name, tuple(attributes)), after_space + 2
  +return _Tag(name, tuple(attributes)), after_space - 2
  ```
- `_parse_tag` · survived · number · `beef621cd6326f39`
  ```diff
  -return _Tag(name, tuple(attributes)), after_space + 2
  +return _Tag(name, tuple(attributes)), after_space + 3
  ```
- `_parse_tag` · survived · operator: comparison · `d51104ced5c1b951`
  ```diff
  -if after_space == end or after_space >= len(raw):
  +if after_space == end or after_space > len(raw):
  ```
- `_parse_tag` · survived · operator: arithmetic · `4f235d4562d3cad0`
  ```diff
  -return None, max(after_space, opening + 1)
  +return None, max(after_space, opening - 1)
  ```
- `_parse_tag` · survived · number · `681819ca7614e49e`
  ```diff
  -return None, max(after_space, opening + 1)
  +return None, max(after_space, opening + 2)
  ```
- `_parse_attribute` · survived · operator: arithmetic · `367063bb7678b44a`
  ```diff
  -return None, start + 1
  +return None, start - 1
  ```
- `_parse_attribute` · survived · number · `bc5d3756d0557e38`
  ```diff
  -return None, start + 1
  +return None, start + 2
  ```
- `_parse_attribute` · survived · argument: replaced by None · `32331c4f14500dfe`
  ```diff
  -return _Attribute(name, None, None, name_end), name_end
  +return _Attribute(name, None, None, None), name_end
  ```
- `_value_end` · survived · operator: comparison · `4490de5e428cafa4`
  ```diff
  -if start >= len(raw):
  +if start > len(raw):
  ```
- `_value_end` · survived · True / False swapped · `4f8e17fbd47d71c7`
  ```diff
  -return False, start
  +return True, start
  ```
- `_value_end` · survived · number · `88324fe1cc07561b`
  ```diff
  -closing = raw.find(raw[start : start + 1], start + 1)
  +closing = raw.find(raw[start : start + 1], start + 2)
  ```
- `_value_end` · survived · ternary: condition forced · `ad4ee8b7a338e3ae`
  ```diff
  -return (False, len(raw)) if closing == -1 else (True, closing + 1)
  +return (False, len(raw)) if (closing == -1) and False else (True, closing + 1)
  ```
- `_value_end` · survived · True / False swapped · `54618b9d397ab3d4`
  ```diff
  -return (False, len(raw)) if closing == -1 else (True, closing + 1)
  +return (True, len(raw)) if closing == -1 else (True, closing + 1)
  ```
- `_value_end` · survived · operator: arithmetic · `87e0d1a42237c2ca`
  ```diff
  -return (False, len(raw)) if closing == -1 else (True, closing + 1)
  +return (False, len(raw)) if closing == +1 else (True, closing + 1)
  ```
- `_value_end` · survived · number · `3cc3d807fdabbd19`
  ```diff
  -return (False, len(raw)) if closing == -1 else (True, closing + 1)
  +return (False, len(raw)) if closing == -2 else (True, closing + 1)
  ```
- `_value_end` · survived · ternary: condition forced · `226d7211f1898d6b`
  ```diff
  -return (True, end) if end > start else (False, start + 1)
  +return (True, end) if (end > start) and False else (False, start + 1)
  ```
- `_value_end` · survived · ternary: condition forced · `e0175097d3da95c0`
  ```diff
  -return (True, end) if end > start else (False, start + 1)
  +return (True, end) if (end > start) or True else (False, start + 1)
  ```
- `_value_end` · survived · True / False swapped · `c27e7523be83b23f`
  ```diff
  -return (True, end) if end > start else (False, start + 1)
  +return (False, end) if end > start else (False, start + 1)
  ```
- `_value_end` · survived · operator: comparison · `d15a76143a513a15`
  ```diff
  -return (True, end) if end > start else (False, start + 1)
  +return (True, end) if end >= start else (False, start + 1)
  ```
- `_value_end` · survived · True / False swapped · `c76bc39eab4b8abd`
  ```diff
  -return (True, end) if end > start else (False, start + 1)
  +return (True, end) if end > start else (True, start + 1)
  ```
- `_value_end` · survived · operator: arithmetic · `5a6df7989da70b4a`
  ```diff
  -return (True, end) if end > start else (False, start + 1)
  +return (True, end) if end > start else (False, start - 1)
  ```
- `_value_end` · survived · number · `67b7d12df45ec2ce`
  ```diff
  -return (True, end) if end > start else (False, start + 1)
  +return (True, end) if end > start else (False, start + 2)
  ```
- `_unquoted` · survived · string: XX added · `15d88002e5581ce6`
  ```diff
  -value = attribute.value or b""
  +value = attribute.value or b"XXXX"
  ```
- `_unquoted` · survived · ternary: condition forced · `cad7f91c3c85da74`
  ```diff
  -return value[1:-1] if value[:1] in (b'"', b"'") else value
  +return value[1:-1] if (value[:1] in (b'"', b"'")) or True else value
  ```
- `_replace` · survived · assignment: value to None · `bc48212e16cf43b6`
  ```diff
  -position = 0
  +position = None
  ```
- `_start_tags` · timeout · argument: replaced by None · `6ace95e5cfcc94f6`
  ```diff
  -while position != -1 and (opening := raw.find(b"<", position)) != -1:
  +while position != -1 and (opening := raw.find(b"<", None)) != -1:
  ```
- `_start_tags` · timeout · argument: removed · `ce3ea614679ac830`
  ```diff
  -while position != -1 and (opening := raw.find(b"<", position)) != -1:
  +while position != -1 and (opening := raw.find(b"<", )) != -1:
  ```
- `_start_tags` · timeout · operator: arithmetic · `806b5b564bf76715`
  ```diff
  -while position != -1 and (opening := raw.find(b"<", position)) != -1:
  +while position != -1 and (opening := raw.find(b"<", position)) != +1:
  ```
- `_start_tags` · timeout · number · `b0d119ae7317689f`
  ```diff
  -while position != -1 and (opening := raw.find(b"<", position)) != -1:
  +while position != -1 and (opening := raw.find(b"<", position)) != -2:
  ```
- `_start_tags` · timeout · is / in negated · `d5d94679814deaed`
  ```diff
  -if skipped is not None:
  +if skipped is None:
  ```
- `_start_tags` · timeout · assignment: value to None · `8e920b7e56a9769c`
  ```diff
  -position = skipped
  +position = None
  ```
- `_start_tags` · timeout · assignment: value to None · `b082ad7ff9235ab3`
  ```diff
  -position = _after_start_tag(raw, tag, position)
  +position = None
  ```
- `_after_start_tag` · timeout · kind not recorded · `d42178196b1f2f7d`
  ```diff
  -return -1 if found is None else found.start()
  +return +1 if found is None else found.start()
  ```
- `_skip_non_start_tag` · timeout · kind not recorded · `4966cefce2dc0fea`
  ```diff
  -closing = raw.find(b"-->", opening + 4)
  +closing = raw.find(b"-->", None)
  ```
- `_skip_non_start_tag` · timeout · kind not recorded · `e2754be68234cfe8`
  ```diff
  -closing = raw.find(b"-->", opening + 4)
  +closing = raw.find(b"-->", )
  ```
- `_skip_non_start_tag` · timeout · kind not recorded · `1163b6d403f0d60b`
  ```diff
  -return _parse_tag(raw, opening + 1)[1]  # an end tag: only its extent matters
  +return _parse_tag(raw, opening - 1)[1]  # an end tag: only its extent matters
  ```
- `_skip_non_start_tag` · timeout · argument: replaced by None · `6a48f49e06e8af1e`
  ```diff
  -closing = raw.find(b">", opening + 2)
  +closing = raw.find(b">", None)
  ```
- `_skip_non_start_tag` · timeout · argument: removed · `e3e857413b4e6602`
  ```diff
  -closing = raw.find(b">", opening + 2)
  +closing = raw.find(b">", )
  ```
- `_skip` · timeout · kind not recorded · `604d4e9e84757e33`
  ```diff
  -position += 1
  +position = 1
  ```
- `_until` · timeout · kind not recorded · `0a776c301d956628`
  ```diff
  -position += 1
  +position = 1
  ```
- `_until` · timeout · kind not recorded · `df7ec81d2965adee`
  ```diff
  -position += 1
  +position -= 1
  ```
- `_parse_tag` · timeout · kind not recorded · `7d51d05ee25add51`
  ```diff
  -return None, opening + 1
  +return None, opening - 1
  ```
- `_parse_tag` · timeout · kind not recorded · `cbf025e187fe3d8e`
  ```diff
  -end = _skip(raw, opening + 1, _TAG_NAME)
  +end = _skip(raw, opening - 1, _TAG_NAME)
  ```

</details>

<details><summary><code>src/lovspor/ssh_signature.py</code>: 3</summary>

- `ssh_verify` · survived · argument: replaced by None · `7da1b0e54f903d05`
  ```diff
  -moment = check.verify_time.astimezone(UTC)
  +moment = check.verify_time.astimezone(None)
  ```
- `_run` · survived · string: XX added · `00d5a0ab89dc64f4`
  ```diff
  -return done.stdout.decode(errors="replace") if done.returncode == 0 else ""
  +return done.stdout.decode(errors="XXreplaceXX") if done.returncode == 0 else ""
  ```
- `_run` · survived · string: upper case · `bc0b6b876650dadd`
  ```diff
  -return done.stdout.decode(errors="replace") if done.returncode == 0 else ""
  +return done.stdout.decode(errors="REPLACE") if done.returncode == 0 else ""
  ```

</details>

<details><summary><code>src/lovspor/sync/delta_embed.py</code>: 25</summary>

- `RepayHeadroom.spend` · survived · string: lower case · `092d2dbc0ba1cf80`
  ```diff
  -"tokens left under the re-embed guard's token ceiling. That call was not sent "
  +"tokens left under the re-embed guard's token ceiling. that call was not sent "
  ```
- `embedding_target` · survived · operator: and / or · `b2507df2a5bdc34b`
  ```diff
  -if embedder is None or space_id is None or space_id == UNKNOWN_SPACE_ID:
  +if embedder is None or space_id is None and space_id == UNKNOWN_SPACE_ID:
  ```
- `embedding_target` · survived · operator: and / or · `e4cd1f757d311f9c`
  ```diff
  -if embedder is None or space_id is None or space_id == UNKNOWN_SPACE_ID:
  +if embedder is None and space_id is None or space_id == UNKNOWN_SPACE_ID:
  ```
- `load_prior_vectors` · survived · ternary: condition forced · `c7b37b2125fa1b86`
  ```diff
  -sidecar = _committed_sidecar(repo, record, target) if inputs else None
  +sidecar = _committed_sidecar(repo, record, target) if (inputs) or True else None
  ```
- `load_prior_vectors` · survived · argument: replaced by None · `c019cccfa5e299f2`
  ```diff
  -for item, (_, row) in zip(inputs, sidecar.sections, strict=True):
  +for item, (_, row) in zip(inputs, sidecar.sections, strict=None):
  ```
- `load_prior_vectors` · survived · argument: removed · `118e44ce97323946`
  ```diff
  -for item, (_, row) in zip(inputs, sidecar.sections, strict=True):
  +for item, (_, row) in zip(inputs, sidecar.sections, ):
  ```
- `load_prior_vectors` · survived · True / False swapped · `b62f01612d19466d`
  ```diff
  -for item, (_, row) in zip(inputs, sidecar.sections, strict=True):
  +for item, (_, row) in zip(inputs, sidecar.sections, strict=False):
  ```
- `ReusingEmbedder.quantized_rows` · survived · argument: replaced by None · `831ccacca11bc7df`
  ```diff
  -fresh = dict(zip(paid, self._encode(paid), strict=True))
  +fresh = dict(zip(paid, self._encode(paid), strict=None))
  ```
- `ReusingEmbedder.quantized_rows` · survived · argument: removed · `4b716222b2585541`
  ```diff
  -fresh = dict(zip(paid, self._encode(paid), strict=True))
  +fresh = dict(zip(paid, self._encode(paid), ))
  ```
- `ReusingEmbedder.quantized_rows` · survived · True / False swapped · `b6fc554c04fcc985`
  ```diff
  -fresh = dict(zip(paid, self._encode(paid), strict=True))
  +fresh = dict(zip(paid, self._encode(paid), strict=False))
  ```
- `ReusingEmbedder.quantized_rows` · survived · argument: replaced by None · `fe8b722ad5445909`
  ```diff
  -return np.vstack(cast(list[np.ndarray], rows)), scale
  +return np.vstack(cast(None, rows)), scale
  ```
- `ReusingEmbedder._keeps_scale` · survived · True / False swapped · `d143753935212110`
  ```diff
  -return False
  +return True
  ```
- `ReusingEmbedder._repaid` · survived · argument: replaced by None · `a8a6ee11c1335af1`
  ```diff
  -vectors = {**fresh, **dict(zip(carried, self._encode(carried), strict=True))}
  +vectors = {**fresh, **dict(zip(carried, self._encode(carried), strict=None))}
  ```
- `ReusingEmbedder._repaid` · survived · argument: removed · `0f7a5e1dcb5cfb4e`
  ```diff
  -vectors = {**fresh, **dict(zip(carried, self._encode(carried), strict=True))}
  +vectors = {**fresh, **dict(zip(carried, self._encode(carried), ))}
  ```
- `ReusingEmbedder._repaid` · survived · True / False swapped · `4077df43d98c979c`
  ```diff
  -vectors = {**fresh, **dict(zip(carried, self._encode(carried), strict=True))}
  +vectors = {**fresh, **dict(zip(carried, self._encode(carried), strict=False))}
  ```
- `ReusingEmbedder._encode` · survived · argument: replaced by None · `ebdf7d24e6c5aa9c`
  ```diff
  -return np.empty((0, self.get_dimension()), dtype=np.float32)
  +return np.empty((0, self.get_dimension()), dtype=None)
  ```
- `ReusingEmbedder._encode` · survived · argument: removed · `9a1991b6fc43edc0`
  ```diff
  -return np.empty((0, self.get_dimension()), dtype=np.float32)
  +return np.empty((0, self.get_dimension()), )
  ```
- `ReusingEmbedder._encode` · survived · argument: replaced by None · `65ff3b8bdbb750ff`
  ```diff
  -vectors = np.asarray(self._inner.encode(texts), dtype=np.float32)
  +vectors = np.asarray(self._inner.encode(texts), dtype=None)
  ```
- `ReusingEmbedder._encode` · survived · argument: removed · `9fbd7fc77de8da66`
  ```diff
  -vectors = np.asarray(self._inner.encode(texts), dtype=np.float32)
  +vectors = np.asarray(self._inner.encode(texts), )
  ```
- `document_vectors` · survived · argument: replaced by None · `2f84c61655c45d70`
  ```diff
  -return stored.vectors, cast(float, stored.scale)
  +return stored.vectors, cast(None, stored.scale)
  ```
- `_reaches_max` · survived · argument: replaced by None · `5459f471d2e0b6b1`
  ```diff
  -return int(np.abs(row.astype(np.int16)).max(initial=0)) == int(_INT8_MAX)
  +return int(np.abs(row.astype(np.int16)).max(initial=None)) == int(_INT8_MAX)
  ```
- `_reaches_max` · survived · argument: replaced by None · `5a4281ef812b2786`
  ```diff
  -return int(np.abs(row.astype(np.int16)).max(initial=0)) == int(_INT8_MAX)
  +return int(np.abs(row.astype(None)).max(initial=0)) == int(_INT8_MAX)
  ```
- `_reaches_max` · survived · number · `fe537259f3430974`
  ```diff
  -return int(np.abs(row.astype(np.int16)).max(initial=0)) == int(_INT8_MAX)
  +return int(np.abs(row.astype(np.int16)).max(initial=1)) == int(_INT8_MAX)
  ```
- `_committed_inputs` · survived · argument: replaced by None · `c17fd0ecbc123789`
  ```diff
  -inputs = build_embedding_inputs(path.read_text(encoding="utf-8"))
  +inputs = build_embedding_inputs(path.read_text(encoding=None))
  ```
- `_committed_inputs` · survived · string: upper case · `f58f46a7735b337f`
  ```diff
  -inputs = build_embedding_inputs(path.read_text(encoding="utf-8"))
  +inputs = build_embedding_inputs(path.read_text(encoding="UTF-8"))
  ```

</details>

<details><summary><code>src/lovspor/sync/input_annotation.py</code>: 1</summary>

- `_apply_annotation` · survived · argument: replaced by None · `829755bfb6444a83`
  ```diff
  -replace_manifest(Manifest(generated_at=datetime.now(UTC), documents=new_records), manifest_path)
  +replace_manifest(Manifest(generated_at=datetime.now(None), documents=new_records), manifest_path)
  ```

</details>

<details><summary><code>src/lovspor/sync/orchestrator.py</code>: 15</summary>

- `_ensure_clean_corpus` · survived · string: XX added · `5f420b27920a2edc`
  ```diff
  -"prior sync likely crashed after writing the manifest but before "
  +"XXprior sync likely crashed after writing the manifest but before XX"
  ```
- `_ensure_clean_corpus` · survived · string: upper case · `958e6350cf20f1df`
  ```diff
  -"prior sync likely crashed after writing the manifest but before "
  +"PRIOR SYNC LIKELY CRASHED AFTER WRITING THE MANIFEST BUT BEFORE "
  ```
- `_ensure_clean_corpus` · survived · string: XX added · `54139b260d3b9cb6`
  ```diff
  -"residue, `git reset --hard` and `git clean -fd`, then re-run.",
  +"XXresidue, `git reset --hard` and `git clean -fd`, then re-run.XX",
  ```
- `_ensure_clean_corpus` · survived · string: upper case · `b12970710756a191`
  ```diff
  -"residue, `git reset --hard` and `git clean -fd`, then re-run.",
  +"RESIDUE, `GIT RESET --HARD` AND `GIT CLEAN -FD`, THEN RE-RUN.",
  ```
- `_collect_upstream` · survived · argument: replaced by None · `8d5bac5f0e0750ac`
  ```diff
  -owners = registered_ownership(settings.lovverk_repo_path, _prior_slug_ownership(prior))
  +owners = registered_ownership(settings.lovverk_repo_path, _prior_slug_ownership(None))
  ```
- `_collect_upstream` · survived · argument: replaced by None · `5eb893eb1ee098ce`
  ```diff
  -final_slugs = resolve_collisions(base_slugs, owners.get(dataset, {}))
  +final_slugs = resolve_collisions(base_slugs, owners.get(dataset, None))
  ```
- `_collect_upstream` · survived · argument: removed · `c161ef078bc00f0c`
  ```diff
  -final_slugs = resolve_collisions(base_slugs, owners.get(dataset, {}))
  +final_slugs = resolve_collisions(base_slugs, owners.get(dataset, ))
  ```
- `_write_one` · survived · operator: arithmetic · `d0be5594a4c50b96`
  ```diff
  -embedding_sidecar_sha256=space_id and sidecar_sha256(written_paths[-1]),
  +embedding_sidecar_sha256=space_id and sidecar_sha256(written_paths[+1]),
  ```
- `_commit_history_followup` · survived · argument: replaced by None · `ff360e59f3e20db4`
  ```diff
  -if has_staged_changes(repo):
  +if has_staged_changes(None):
  ```
- `_generate_and_apply_history` · survived · break / continue swapped · `1ce60683b03bbecd`
  ```diff
  -continue
  +break
  ```
- `_guard_change_reembed` · survived · ternary: condition forced · `983a44c57e35b56e`
  ```diff
  -grant_headroom(target, None if allow else settings.reembed_guard_max_tokens - tokens)
  +grant_headroom(target, None if (allow) and False else settings.reembed_guard_max_tokens - tokens)
  ```
- `_run_sprint9_embeddings_migration` · survived · string: XX added · `e0a1963a5046557c`
  ```diff
  -"embedding_sidecar_sha256": space_id and sidecar_sha256(new_path),
  +"XXembedding_sidecar_sha256XX": space_id and sidecar_sha256(new_path),
  ```
- `_run_sprint9_embeddings_migration` · survived · string: upper case · `cbe9e0b2367ea9df`
  ```diff
  -"embedding_sidecar_sha256": space_id and sidecar_sha256(new_path),
  +"EMBEDDING_SIDECAR_SHA256": space_id and sidecar_sha256(new_path),
  ```
- `_run_sprint9_embeddings_migration` · survived · operator: and / or · `efac24fd8b50d744`
  ```diff
  -"embedding_sidecar_sha256": space_id and sidecar_sha256(new_path),
  +"embedding_sidecar_sha256": space_id or sidecar_sha256(new_path),
  ```
- `mark_undersized_embeddings_stale` · survived · argument: replaced by None · `839ecde29672df65`
  ```diff
  -replace_manifest(Manifest(generated_at=datetime.now(UTC), documents=new_records), manifest_path)
  +replace_manifest(Manifest(generated_at=datetime.now(None), documents=new_records), manifest_path)
  ```

</details>

## The register after this night

- **2166** open in 81 file(s); this night 2166 new, 0 left, 0 gone with their function

| kind | open |
|---|---:|
| argument: replaced by None | 530 |
| string: XX added | 476 |
| string: upper case | 405 |
| argument: removed | 241 |
| operator: and / or | 84 |
| ternary: condition forced | 83 |
| number | 77 |
| True / False swapped | 55 |
| assignment: value to None | 49 |
| operator: comparison | 40 |
| operator: arithmetic | 24 |
| is / in negated | 22 |
| string: lower case | 16 |
| string method swapped | 15 |
| not recorded | 14 |
| augmented assignment to plain | 9 |
| break / continue swapped | 9 |
| operator: bitwise | 6 |
| not / ~ removed | 4 |
| lambda: body replaced | 3 |
| assignment: None to empty string | 2 |
| operator: augmented | 2 |

| file | open |
|---|---:|
| src/lovspor/identity/report.py | 202 |
| src/lovspor/forarbeider/version_lineage.py | 144 |
| src/lovspor/forarbeider/release_ledger.py | 98 |
| src/lovspor/forarbeider/release_gates.py | 97 |
| src/lovspor/forarbeider/policy_trust.py | 74 |
| src/lovspor/regjeringen_fetch/normalize.py | 72 |
| src/lovspor/forarbeider/release_reader.py | 65 |
| src/lovspor/legaltime/rename_research.py | 64 |
| src/lovspor/identity/repair_report.py | 63 |
| src/lovspor/forarbeider/span_assessment.py | 59 |
| src/lovspor/legaltime/step_evidence.py | 58 |
| src/lovspor/forarbeider/attachment_discovery.py | 52 |
| src/lovspor/forarbeider/release_sources.py | 52 |
| src/lovspor/publication_seal.py | 48 |
| src/lovspor/identity/corpus_git.py | 47 |
| src/lovspor/forarbeider/version_diff.py | 42 |
| src/lovspor/forarbeider/version_release.py | 42 |
| src/lovspor/forarbeider/document_spans.py | 41 |
| src/lovspor/forarbeider/policy_evaluation.py | 37 |
| src/lovspor/forarbeider/release_checkout.py | 37 |
| src/lovspor/forarbeider/release_plan.py | 37 |
| src/lovspor/forarbeider/document_extract.py | 33 |
| src/lovspor/forarbeider/version_completeness.py | 31 |
| src/lovspor/identity/registry_history.py | 31 |
| src/lovspor/identity/apply_commands.py | 29 |
| src/lovspor/forarbeider/policy_authorization.py | 28 |
| src/lovspor/forarbeider/release_plan_models.py | 28 |
| src/lovspor/identity/history_marker.py | 27 |
| src/lovspor/identity/repair_plan.py | 27 |
| src/lovspor/forarbeider/version_manifest.py | 26 |
| src/lovspor/sync/delta_embed.py | 25 |
| src/lovspor/forarbeider/publication.py | 24 |
| src/lovspor/forarbeider/release_control.py | 24 |
| src/lovspor/forarbeider/release_limits.py | 24 |
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
