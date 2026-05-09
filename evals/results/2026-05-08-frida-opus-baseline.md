# lovspor MCP persona eval baseline - 2026-05-08

## Run metadata

- Date: 2026-05-08
- lovspor commit: `981ae18+dirty`
- lovverk-fixture-checksum: `sha256:d329b8e0951700229f5125aef06087a307e62547eca968d0bc38c9ca6b778344`
- Personas: 1
- Scenarios: 10

## Summary

| Persona | Pass | Partial | Fail | Gap-revealed |
|---|---:|---:|---:|---:|
| Frida Lindqvist (`frida`) | 5 | 3 | 2 | 0 |

## Per-persona breakdown

### Frida Lindqvist (`frida`)

| Scenario | Status | Note |
|---|---|---|
| `frida_001` | partial | 2 criteria failed |
| `frida_002` | pass | all criteria passed |
| `frida_003` | pass | all criteria passed |
| `frida_004` | fail | 2 criteria failed |
| `frida_005` | pass | all criteria passed |
| `frida_006` | pass | all criteria passed |
| `frida_007` | partial | 3 criteria failed |
| `frida_008` | partial | 1 criteria failed |
| `frida_009` | pass | all criteria passed |
| `frida_010` | fail | unexpected get_section error: Error executing tool get_section: section '9-6' not found in 'husleieloven'; available: § 9-5, § 9-7, § 9-8 |

## Failure traces

### frida_001

Scenario YAML:

```yaml
id: frida_001
persona: frida
intent: Verify a verbatim quote before citing it in an article
user_query: 'Bekreft at husleieloven § 9-5 inneholder ordlyden "protestere skriftlig
  innen én måned" før jeg siterer paragrafen i artikkelen.

  '
expected_tool_calls:
- tool: get_section
  slug_match: husleieloven
  section_id: 9-5
- tool: verify_quote
  slug: husleieloven
  section_id: 9-5
  quote: protestere skriftlig innen én måned
success_criteria:
- kind: tool_called
  tool: get_section
- kind: tool_called
  tool: verify_quote
- kind: response_contains
  target: verified
- kind: response_contains
  target: protestere skriftlig innen én måned
failure_modes:
- get_section returned the section but verify_quote was skipped before publication.
- The verbatim quote is paraphrased and verify_quote silently passes.
reveals_gap: null
```

Actual tool calls:

```yaml
- tool: ToolSearch
  args:
    query: select:mcp__lovverk__get_section,mcp__lovverk__verify_quote,mcp__lovverk__search_laws,mcp__lovverk__validate_citation
    max_results: 10
  ok: true
  response:
  - type: tool_reference
    tool_name: mcp__lovverk__get_section
  - type: tool_reference
    tool_name: mcp__lovverk__verify_quote
  - type: tool_reference
    tool_name: mcp__lovverk__search_laws
  - type: tool_reference
    tool_name: mcp__lovverk__validate_citation
- tool: search_laws
  args:
    query: husleieloven
  ok: true
  response:
    result:
    - slug: husleieloven
      doc_id: lov-1999-03-26-017
      title: Lov om husleieavtaler (husleieloven)
      dataset: lover
      last_changed: '2026-03-27'
      total_changes: 2
- tool: verify_quote
  args:
    slug: husleieloven
    section_id: 9-5
    quote: protestere skriftlig innen én måned
  ok: true
  response:
    verified: true
    slug: husleieloven
    section_id: 9-5
    reason: null
```

Diff against expected criteria:

```diff
--- expected
+++ actual
@@ -1,8 +1,12 @@
 - kind: tool_called
-  expected: pass
+  passed: false
+  note: tool get_section was not called
 - kind: tool_called
-  expected: pass
+  passed: true
+  note: tool verify_quote was called
 - kind: response_contains
-  expected: pass
+  passed: true
+  note: response contained 'verified'
 - kind: response_contains
-  expected: pass
+  passed: false
+  note: response did not contain 'protestere skriftlig innen én måned'
```

### frida_004

Scenario YAML:

```yaml
id: frida_004
persona: frida
intent: Use validate_citation as the off-ramp for an ambiguous citation
user_query: 'En kilde har sendt meg sitatet "§ 5-12" uten å nevne hvilken lov det
  gjelder. Kan denne henvisningen bekreftes som den står?

  '
expected_tool_calls:
- tool: validate_citation
  citation: § 5-12
success_criteria:
- kind: tool_called
  tool: validate_citation
- kind: response_contains
  target: ambiguous
failure_modes:
- validate_citation guesses an act and returns valid=true, sending Frida down the
  wrong path.
- The reason field does not explain why the citation cannot be confirmed.
reveals_gap: null
```

Actual tool calls:

```yaml
[]
```

Diff against expected criteria:

```diff
--- expected
+++ actual
@@ -1,4 +1,6 @@
 - kind: tool_called
-  expected: pass
+  passed: false
+  note: tool validate_citation was not called
 - kind: response_contains
-  expected: pass
+  passed: false
+  note: response did not contain 'ambiguous'
```

### frida_007

Scenario YAML:

```yaml
id: frida_007
persona: frida
intent: Map an EU regulation citation to the Norwegian implementing act
user_query: 'For en artikkel om GDPR (forordning 2016/679) trenger jeg å bekrefte
  hvilke norske lover implementerer den.

  '
expected_tool_calls:
- tool: search_eu_implementations
  eu_doc_id: 32016R0679
- tool: get_eu_basis
  slug_match: personopplysningsloven
success_criteria:
- kind: tool_called
  tool: search_eu_implementations
- kind: tool_called
  tool: get_eu_basis
- kind: list_contains_slug
  tool: search_eu_implementations
  slug: personopplysningsloven
- kind: response_contains
  target: 32016R0679
failure_modes:
- The article cites the wrong implementing act because the CELEX lookup was skipped.
- Direction is reversed (Norwegian act → EU basis only) without confirming the implementation
  pair.
reveals_gap: null
```

Actual tool calls:

```yaml
- tool: ToolSearch
  args:
    query: select:mcp__lovverk__search_eu_implementations,mcp__lovverk__get_eu_basis,mcp__lovverk__search_laws,mcp__lovverk__get_section,mcp__lovverk__verify_quote
    max_results: 5
  ok: true
  response:
  - type: tool_reference
    tool_name: mcp__lovverk__search_eu_implementations
  - type: tool_reference
    tool_name: mcp__lovverk__get_eu_basis
  - type: tool_reference
    tool_name: mcp__lovverk__search_laws
  - type: tool_reference
    tool_name: mcp__lovverk__get_section
  - type: tool_reference
    tool_name: mcp__lovverk__verify_quote
- tool: search_eu_implementations
  args:
    eu_doc_id: 32016R0679
  ok: true
  response:
    result:
    - slug: personopplysningsloven
      doc_id: lov-2018-06-15-038
      title: Lov om behandling av personopplysninger (personopplysningsloven)
      dataset: lover
```

Diff against expected criteria:

```diff
--- expected
+++ actual
@@ -1,8 +1,12 @@
 - kind: tool_called
-  expected: pass
+  passed: true
+  note: tool search_eu_implementations was called
 - kind: tool_called
-  expected: pass
+  passed: false
+  note: tool get_eu_basis was not called
 - kind: list_contains_slug
-  expected: pass
+  passed: false
+  note: result list did not contain slug personopplysningsloven
 - kind: response_contains
-  expected: pass
+  passed: false
+  note: response did not contain '32016R0679'
```

### frida_008

Scenario YAML:

```yaml
id: frida_008
persona: frida
intent: Handle an empty body-search result gracefully without inventing law
user_query: 'Finnes det noen norsk lov som regulerer "kvantedatamaskinregulering"
  i dette korpuset? Hvis ikke, si det rett ut.

  '
expected_tool_calls:
- tool: search_body
  query: kvantedatamaskinregulering
  query_contains:
  - kvantedatamaskinregulering
success_criteria:
- kind: tool_called
  tool: search_body
- kind: result_count_equals
  tool: search_body
  count: 0
failure_modes:
- The empty result is glossed over and an unrelated act is cited instead.
- search_body silently rewrites the query and returns false-positive matches.
reveals_gap: null
```

Actual tool calls:

```yaml
- tool: ToolSearch
  args:
    query: select:mcp__lovverk__semantic_search,mcp__lovverk__search_laws,mcp__lovverk__search_body
    max_results: 5
  ok: true
  response:
  - type: tool_reference
    tool_name: mcp__lovverk__semantic_search
  - type: tool_reference
    tool_name: mcp__lovverk__search_laws
  - type: tool_reference
    tool_name: mcp__lovverk__search_body
- tool: search_laws
  args:
    query: kvantedatamaskin
  ok: true
  response:
    result: []
- tool: search_body
  args:
    query: kvantedatamaskin
  ok: true
  response:
    result: []
- tool: search_body
  args:
    query: kvante
  ok: true
  response:
    result: []
- tool: semantic_search
  args:
    query: regulering av kvantedatamaskiner og kvanteteknologi
    limit: 5
  ok: true
  response:
    result:
    - slug: personopplysningsloven
      section_id: '2'
      score: 0.372117817401886
      title: Lov om behandling av personopplysninger (personopplysningsloven)
      dataset: lover
      citation_hint: § 2 personopplysningsloven
    - slug: personopplysningsloven
      section_id: '1'
      score: 0.35848844051361084
      title: Lov om behandling av personopplysninger (personopplysningsloven)
      dataset: lover
      citation_hint: § 1 personopplysningsloven
    - slug: bokforingsloven
      section_id: '5'
      score: 0.34551969170570374
      title: Lov om bokføring (bokføringsloven)
      dataset: lover
      citation_hint: § 5 bokforingsloven
    - slug: grannelova-gl
      section_id: '10'
      score: 0.3395432233810425
      title: Lov om rettshøve mellom grannar (grannelova)
      dataset: lover
      citation_hint: § 10 grannelova-gl
    - slug: hvitvaskingsloven
      section_id: '4'
      score: 0.327997088432312
      title: Lov om tiltak mot hvitvasking og terrorfinansiering (hvitvaskingsloven)
      dataset: lover
      citation_hint: § 4 hvitvaskingsloven
```

Diff against expected criteria:

```diff
--- expected
+++ actual
@@ -1,4 +1,6 @@
 - kind: tool_called
-  expected: pass
+  passed: true
+  note: tool search_body was called
 - kind: result_count_equals
-  expected: pass
+  passed: false
+  note: result counts [] did not satisfy equaled 0
```

### frida_010

Scenario YAML:

```yaml
id: frida_010
persona: frida
intent: Run the full Sprint 9 anti-hallucination chain end-to-end before publishing
user_query: 'Skriv et avsnitt om utleiers oppsigelsesfrist i Norge. Bruk kun verifiserte
  sitater og oppgi paragrafen.

  '
expected_tool_calls:
- tool: semantic_search
  query: utleiers oppsigelsesfrist
  query_contains:
  - oppsigelse
  - frist
  dataset: lover
- tool: get_section
  slug_match: husleieloven
  section_id: 9-5
- tool: verify_quote
  slug: husleieloven
  section_id: 9-5
  quote: Oppsigelse fra utleier skal være skriftlig og begrunnet
success_criteria:
- kind: tool_called
  tool: semantic_search
- kind: tool_called
  tool: get_section
- kind: tool_called
  tool: verify_quote
- kind: list_contains_slug
  tool: semantic_search
  slug: husleieloven
- kind: response_contains
  target: verified
failure_modes:
- One step in the chain is skipped (e.g., verify_quote omitted) and a verbatim quote
  ships unverified.
- semantic_search returns the right candidate but the AI quotes from memory instead
  of reading the section text.
reveals_gap: null
```

Actual tool calls:

```yaml
- tool: ToolSearch
  args:
    query: select:mcp__lovverk__semantic_search,mcp__lovverk__search_laws,mcp__lovverk__search_body,mcp__lovverk__get_section,mcp__lovverk__verify_quote,mcp__lovverk__validate_citation
    max_results: 10
  ok: true
  response:
  - type: tool_reference
    tool_name: mcp__lovverk__semantic_search
  - type: tool_reference
    tool_name: mcp__lovverk__search_laws
  - type: tool_reference
    tool_name: mcp__lovverk__search_body
  - type: tool_reference
    tool_name: mcp__lovverk__get_section
  - type: tool_reference
    tool_name: mcp__lovverk__verify_quote
  - type: tool_reference
    tool_name: mcp__lovverk__validate_citation
- tool: search_laws
  args:
    query: husleie
  ok: true
  response:
    result:
    - slug: husleieloven
      doc_id: lov-1999-03-26-017
      title: Lov om husleieavtaler (husleieloven)
      dataset: lover
      last_changed: '2026-03-27'
      total_changes: 2
- tool: semantic_search
  args:
    query: utleiers oppsigelsesfrist leieforhold
    limit: 10
  ok: true
  response:
    result:
    - slug: husleieloven
      section_id: 9-5
      score: 0.6671204566955566
      title: Lov om husleieavtaler (husleieloven)
      dataset: lover
      citation_hint: § 9-5 husleieloven
    - slug: arbeidsmiljoloven
      section_id: 15-3
      score: 0.5801169872283936
      title: Lov om arbeidsmiljø, arbeidstid og stillingsvern mv. (arbeidsmiljøloven)
      dataset: lover
      citation_hint: § 15-3 arbeidsmiljoloven
    - slug: husleieloven
      section_id: 9-7
      score: 0.5522661805152893
      title: Lov om husleieavtaler (husleieloven)
      dataset: lover
      citation_hint: § 9-7 husleieloven
    - slug: arbeidsmiljoloven
      section_id: 15-7
      score: 0.46751439571380615
      title: Lov om arbeidsmiljø, arbeidstid og stillingsvern mv. (arbeidsmiljøloven)
      dataset: lover
      citation_hint: § 15-7 arbeidsmiljoloven
    - slug: husleieloven
      section_id: 9-8
      score: 0.466011643409729
      title: Lov om husleieavtaler (husleieloven)
      dataset: lover
      citation_hint: § 9-8 husleieloven
    - slug: arbeidsmiljoloven
      section_id: 15-11
      score: 0.44323405623435974
      title: Lov om arbeidsmiljø, arbeidstid og stillingsvern mv. (arbeidsmiljøloven)
      dataset: lover
      citation_hint: § 15-11 arbeidsmiljoloven
    - slug: arbeidsmiljoloven
      section_id: 15-1
      score: 0.42559054493904114
      title: Lov om arbeidsmiljø, arbeidstid og stillingsvern mv. (arbeidsmiljøloven)
      dataset: lover
      citation_hint: § 15-1 arbeidsmiljoloven
    - slug: arbeidsmiljoloven
      section_id: 15-14
      score: 0.42022398114204407
      title: Lov om arbeidsmiljø, arbeidstid og stillingsvern mv. (arbeidsmiljøloven)
      dataset: lover
      citation_hint: § 15-14 arbeidsmiljoloven
    - slug: kjopsloven
      section_id: '32'
      score: 0.40916121006011963
      title: Lov om kjøp (kjøpsloven)
      dataset: lover
      citation_hint: § 32 kjopsloven
    - slug: kjopsloven
      section_id: '30'
      score: 0.38655686378479004
      title: Lov om kjøp (kjøpsloven)
      dataset: lover
      citation_hint: § 30 kjopsloven
- tool: get_section
  args:
    slug: husleieloven
    section_id: 9-6
  ok: false
  response: 'Error executing tool get_section: section ''9-6'' not found in ''husleieloven'';
    available: § 9-5, § 9-7, § 9-8'
- tool: get_section
  args:
    slug: husleieloven
    section_id: 9-5
  ok: true
  response:
    slug: husleieloven
    section_id: 9-5
    heading: § 9-5. Utleierens oppsigelse av tidsubestemt leieavtale
    parent_chapter: Kapittel 9. Opphør av leieavtaler
    body: Oppsigelse fra utleier skal være skriftlig og begrunnet. Oppsigelsen skal
      opplyse om at leieren kan protestere skriftlig innen én måned.
    cross_references: []
- tool: get_section
  args:
    slug: husleieloven
    section_id: 9-7
  ok: true
  response:
    slug: husleieloven
    section_id: 9-7
    heading: § 9-7. Fravikelse
    parent_chapter: Kapittel 9. Opphør av leieavtaler
    body: Leieren plikter å fravike husrommet når leieavtalen er opphørt og det foreligger
      tvangsgrunnlag.
    cross_references: []
- tool: get_section
  args:
    slug: husleieloven
    section_id: 9-8
  ok: true
  response:
    slug: husleieloven
    section_id: 9-8
    heading: § 9-8. Leierens død
    parent_chapter: Kapittel 9. Opphør av leieavtaler
    body: Ved leierens død gjelder særlige regler om hvem som kan tre inn i leieavtalen.
    cross_references: []
- tool: search_body
  args:
    query: oppsigelsesfrist
    limit: 10
  ok: true
  response:
    result:
    - slug: arbeidsmiljoloven
      doc_id: lov-2005-06-17-062
      title: Lov om arbeidsmiljø, arbeidstid og stillingsvern mv. (arbeidsmiljøloven)
      dataset: lover
      match_count: 2
      snippet: '...tisk mulig drøftes med arbeidstaker. ### § 15-3. Oppsigelsesfrister
        Hvis ikke annet er skriftlig avtalt eller fast...'
- tool: verify_quote
  args:
    slug: husleieloven
    section_id: 9-5
    quote: Oppsigelse fra utleier skal være skriftlig og begrunnet. Oppsigelsen skal
      opplyse om at leieren kan protestere skriftlig innen én måned.
  ok: true
  response:
    verified: true
    slug: husleieloven
    section_id: 9-5
    reason: null
- tool: semantic_search
  args:
    query: oppsigelsestid tre måneder husleie
    limit: 5
  ok: true
  response:
    result:
    - slug: husleieloven
      section_id: 9-5
      score: 0.6217032670974731
      title: Lov om husleieavtaler (husleieloven)
      dataset: lover
      citation_hint: § 9-5 husleieloven
    - slug: arbeidsmiljoloven
      section_id: 15-3
      score: 0.5807130336761475
      title: Lov om arbeidsmiljø, arbeidstid og stillingsvern mv. (arbeidsmiljøloven)
      dataset: lover
      citation_hint: § 15-3 arbeidsmiljoloven
    - slug: husleieloven
      section_id: 9-7
      score: 0.563765287399292
      title: Lov om husleieavtaler (husleieloven)
      dataset: lover
      citation_hint: § 9-7 husleieloven
    - slug: arbeidsmiljoloven
      section_id: 15-11
      score: 0.4554864764213562
      title: Lov om arbeidsmiljø, arbeidstid og stillingsvern mv. (arbeidsmiljøloven)
      dataset: lover
      citation_hint: § 15-11 arbeidsmiljoloven
    - slug: husleieloven
      section_id: 9-8
      score: 0.43636590242385864
      title: Lov om husleieavtaler (husleieloven)
      dataset: lover
      citation_hint: § 9-8 husleieloven
```

Diff against expected criteria:

```diff
--- expected
+++ actual
@@ -1,10 +1,15 @@
 - kind: tool_called
-  expected: pass
+  passed: true
+  note: tool semantic_search was called
 - kind: tool_called
-  expected: pass
+  passed: true
+  note: tool get_section was called
 - kind: tool_called
-  expected: pass
+  passed: true
+  note: tool verify_quote was called
 - kind: list_contains_slug
-  expected: pass
+  passed: false
+  note: result list did not contain slug husleieloven
 - kind: response_contains
-  expected: pass
+  passed: true
+  note: response contained 'verified'
```

## Gaps revealed

_No declared gaps were exercised._

## Recommendation

No roadmap gap dominated this run.
