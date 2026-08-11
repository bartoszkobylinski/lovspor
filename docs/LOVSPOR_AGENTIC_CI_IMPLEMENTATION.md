# Lovspor Agentic CI — finalna specyfikacja implementacyjna

**Wersja:** 1.0  
**Data:** 2026-08-11  
**Cel:** zautomatyzować przekazywanie pracy Claude Code → GitHub → Codex → testy → mutation testing bez ręcznego kopiowania kontekstu i bez używania LLM do czekania na długie joby.

---

## 1. Decyzja architektoniczna

Docelowy interfejs człowieka ma być prosty:

```text
$ cd lovspor
$ claude

> /goal Zaimplementuj LOV-XXX zgodnie ze specyfikacją, zachowaj mały zakres PR,
> uruchom lokalne szybkie testy i otwórz PR. Nie czekaj na zdalny Codex ani mutation tests.
```

Od chwili utworzenia PR sterowanie przejmuje GitHub Actions.

```text
                           BARTEK
                             │
                             ▼
                     Claude Code local
                    (Claude subscription)
                             │
                     implementation only
                             │
                       small PR opened
                             │
═════════════════════════════╪══════════════════════════════
                             ▼
                        GitHub Actions
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
          fast CI         Codex tests      lint/type
                           │
                           │ ChatGPT-managed Codex auth
                           │ self-hosted trusted runner
                           ▼
                     test-only commit?
                      │          │
                     YES        NO
                      │          │
                      ▼          └──────────────┐
              push to PR branch                │
                      │                        │
                new PR run                     │
                      │                        │
                      └───────────┬────────────┘
                                  ▼
                           mutation runner
                              NO LLM
                                  │
                          mutation-result.json
                                  │
                          ┌───────┴────────┐
                          ▼                ▼
                        PASS              FAIL
                          │                │
                          ▼                ▼
                    READY TO MERGE    Codex mutation
                                      remediation
                                      tests only
                                          │
                                  max 2 remediation
                                      cycles
                                          │
                              ┌───────────┴───────────┐
                              ▼                       ▼
                            PASS                    BLOCKED
                                                     │
                                                     ▼
                                              human decision
```

### Twarde role

**Claude Code local**
- implementuje production code;
- respektuje `CLAUDE.md`, frozen decisions i metodologię;
- uruchamia szybkie lokalne testy;
- tworzy mały PR;
- nie odpala ręcznie Codexa;
- nie czeka na mutation testing.

**Codex CI**
- jest niezależnym test engineerem;
- może modyfikować wyłącznie dozwolone pliki testowe;
- nie może modyfikować production code, metodologii, benchmark decisions ani corpus/raw;
- najpierw dopisuje testy wynikające z diffu PR;
- jeżeli mutation testing znajdzie survivors, może maksymalnie 2 razy dopisać testy zabijające uzasadnione mutanty;
- jeśli nie potrafi bezpiecznie rozwiązać problemu testami, zgłasza `BLOCKED` zamiast zgadywać.

**GitHub Actions / klasyczny kod**
- pytest, lint, type checking, census, validators;
- mutation testing;
- parsowanie wyników do JSON;
- kontrola zakresu zmian Codexa;
- anty-loop;
- required checks;
- artefakty i statusy.

**Człowiek**
- merge;
- zmiany metodologii;
- frozen benchmark decisions;
- akceptacja equivalent mutants / wyjątków;
- wszystkie przypadki `BLOCKED`.

---

## 2. Dlaczego taki wariant

1. PR jest naturalnym handoffem między lokalnym Claude a automatyzacją.
2. LLM nie zużywa kontekstu ani czasu na oczekiwanie na mutation tests.
3. Mutation testing pozostaje deterministycznym jobem CPU.
4. Codex jest niezależny od autora implementation code.
5. Każda zmiana Codexa jest mechanicznie ograniczona do testów.
6. System kończy się albo `PASS`, albo jawnym `BLOCKED`; nic nie może zostać po cichu pominięte.
7. Małe, częste PR-y pozostają podstawową jednostką pracy.

---

## 3. Billing i uwierzytelnianie

### 3.1 Claude Code

W podstawowej wersji Claude działa lokalnie, więc używa zwykłego logowania Claude Code w ramach subskrypcji.

Nie jest potrzebny `ANTHROPIC_API_KEY`.

Jeżeli później zostanie dodany Claude Code GitHub Action do automatycznych production fixes, używać:

```bash
claude setup-token
```

oraz GitHub Secret:

```text
CLAUDE_CODE_OAUTH_TOKEN
```

Nie używać do tego `ANTHROPIC_API_KEY`, jeśli celem jest rozliczanie w ramach subskrypcji Claude.

### 3.2 Codex

Codex w CI ma działać jako konto Codex/ChatGPT, a nie przez OpenAI API.

**Nie używać:**

```text
OPENAI_API_KEY
CODEX_API_KEY
```

na runnerze Codexa.

Wymagany jest prywatny, zaufany self-hosted GitHub Actions runner z trwałym `CODEX_HOME`.

OpenAI traktuje `auth.json` jak hasło. Runner nie może obsługiwać publicznych/forkowych PR-ów ani innych niezaufanych repozytoriów.

---

## 4. Self-hosted runner dla Codexa

### 4.1 Wymagania

Dedykowana maszyna/VM Linux, bez danych produkcyjnych i bez produkcyjnych sekretów.

Minimalnie:
- Git;
- Python/toolchain Lovsporu;
- GitHub Actions runner;
- Codex CLI;
- `jq`;
- dostęp outbound HTTPS.

Nadać runnerowi label:

```text
codex
```

Workflow będzie używał:

```yaml
runs-on: [self-hosted, linux, codex]
```

### 4.2 Instalacja Codexa

Na runnerze jako użytkownik usługi GitHub Actions:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

Ustawić trwały katalog:

```bash
mkdir -p "$HOME/.codex-lovspor"
chmod 700 "$HOME/.codex-lovspor"
```

Docelowo workflow ma mieć:

```yaml
env:
  CODEX_HOME: /home/<runner-user>/.codex-lovspor
```

Dostosować ścieżkę do rzeczywistego użytkownika usługi.

### 4.3 Seed ChatGPT-managed auth

W `${CODEX_HOME}/config.toml`:

```toml
cli_auth_credentials_store = "file"
```

Na zaufanej maszynie wykonać:

```bash
codex login
```

Następnie bezpiecznie umieścić wygenerowany `auth.json` jako:

```text
${CODEX_HOME}/auth.json
```

Uprawnienia:

```bash
chmod 600 "${CODEX_HOME}/auth.json"
```

Kontrola:

```bash
jq '{
  auth_mode,
  has_tokens: (.tokens != null),
  has_refresh_token: ((.tokens.refresh_token // "") != ""),
  last_refresh
}' "${CODEX_HOME}/auth.json"
```

Wymagane:

```text
auth_mode == "chatgpt"
has_refresh_token == true
```

Codex ma sam odświeżać `auth.json`; katalog musi przetrwać między jobami.

### 4.4 Serializacja

Jeden `auth.json` nie powinien być używany równolegle przez wiele runnerów.

Najprościej: jedna maszyna z jednym runnerem `codex`.

Dodatkowo można dać jobowi:

```yaml
concurrency:
  group: lovspor-codex-subscription
  queue: max
```

---

## 5. Token do automatycznych commitów na PR

Nie używać do pushowania zmian Codexa zwykłego `GITHUB_TOKEN`, ponieważ push wykonany `GITHUB_TOKEN` nie uruchamia standardowo kolejnego workflow i utrudni to poprawne ponowne odpalenie pipeline'u.

Utworzyć **fine-grained GitHub PAT** ograniczony wyłącznie do repo Lovspor.

Minimalne permissions:

```text
Contents: Read and write
Pull requests: Read and write
Metadata: Read
```

GitHub Secret:

```text
LOVSPOR_CI_PUSH_TOKEN
```

Docelowo można zastąpić PAT własnym GitHub App. Na pierwszą wersję PAT jest prostszy.

---

## 6. Struktura plików do dodania

Implementacja ma utworzyć lub uzupełnić:

```text
.github/
  workflows/
    pr-pipeline.yml
    mutation-remediation.yml
  codex/
    pr-tests.md
    mutation-remediation.md

scripts/
  ci/
    assert_codex_scope.sh
    mutation_to_json.py
    mutation_gate.py
    pr_context.sh

docs/
  agentic-ci.md

AGENTS.md                # reguły Codexa, jeśli repo już używa AGENTS.md -> uzupełnić
CLAUDE.md                # uzupełnić o handoff/PR rules, nie duplikować całej dokumentacji
```

**Nie zmieniać istniejącego mutation toola tylko dlatego, że przykład używa innej nazwy.** Implementer ma najpierw wykryć, jak Lovspor uruchamia mutation testing obecnie, i opakować istniejący mechanizm.

---

## 7. Reguły zakresu Codexa

`scripts/ci/assert_codex_scope.sh` ma być deterministycznym guardem.

Implementer ma ustalić faktyczne katalogi testowe Lovsporu i zdefiniować allowlistę. Przykładowo:

```text
tests/**
benchmarks/tests/**
test_*.py
**/test_*.py
**/*_test.py
```

Niedozwolone dla Codexa:

```text
src/**
app/**
lovspor/**           # jeśli to production package
corpus/raw/**
benchmark frozen decisions
methodology docs
migrations
production config
```

Guard działa tak:

```bash
BASE_SHA="$1"

changed="$(git diff --name-only "$BASE_SHA"..HEAD)"

# każdy plik spoza allowlisty => exit 1
```

**Prompt nie jest zabezpieczeniem. Guard jest zabezpieczeniem.**

Jeśli Codex dotknie niedozwolonego pliku:
- workflow FAIL;
- zmiana nie jest commitowana;
- raport zawiera listę plików.

---

## 8. Prompt Codexa — PR test author

`.github/codex/pr-tests.md`:

```text
You are the independent test engineer for Lovspor.

Your job is to inspect the current pull request diff against the base branch and add
ONLY the tests required to validate the changed behavior.

Priorities:
1. regression protection for changed behavior,
2. negative and boundary cases,
3. project invariants,
4. error handling,
5. benchmark/data integrity rules relevant to this diff.

Hard constraints:
- modify test files only;
- never modify production code;
- never modify frozen methodology or benchmark decisions;
- never silently drop, weaken, skip, xfail, or broaden an assertion merely to make tests pass;
- do not lower coverage or mutation thresholds;
- do not change corpus/raw;
- do not edit CI configuration;
- keep additions scoped to this PR.

If a correct new test exposes a production bug or requires a methodological decision,
do not repair production code. Record the issue clearly in your final result.

Run the smallest relevant test set after editing.
```

---

## 9. `pr-pipeline.yml`

### 9.1 Trigger

```yaml
name: PR Pipeline

on:
  pull_request:
    types: [opened, synchronize, reopened]

concurrency:
  group: pr-${{ github.event.pull_request.number }}
  cancel-in-progress: true
```

`cancel-in-progress: true` jest ważne: jeśli Codex dopisze commit, stary mutation run nie powinien mielić starego SHA.

### 9.2 Fast CI

Job na GitHub-hosted runnerze:

```yaml
fast-ci:
  runs-on: ubuntu-latest
  permissions:
    contents: read
```

Implementer ma podłączyć istniejące komendy Lovsporu:
- unit/integration fast set;
- lint;
- type checking;
- szybkie validators;
- ewentualny benchmark census, jeśli jest szybki.

Nie uruchamiać tutaj pełnego mutation suite.

### 9.3 Codex test author

Job:

```yaml
codex-tests:
  runs-on: [self-hosted, linux, codex]
  permissions:
    contents: read
  env:
    CODEX_HOME: /home/<runner-user>/.codex-lovspor
```

Warunek bezpieczeństwa:

```yaml
if: github.event.pull_request.head.repo.full_name == github.repository
```

Runner z `auth.json` **nigdy** nie ma pracować na fork PR.

#### Anty-loop

Przed uruchomieniem Codexa sprawdzić HEAD commit message.

Jeżeli zawiera:

```text
[agent:codex-tests]
```

lub:

```text
[agent:codex-mutation]
```

to pominąć ponowne generowanie testów dla tego samego agent-generated HEAD i przejść dalej do CI/mutation.

Jeżeli HEAD pochodzi z nowej zmiany Claude/człowieka, Codex ma ponownie przeanalizować diff.

#### Checkout

Checkout ma wskazywać **head branch PR**, nie merge ref, i używać tokenu pozwalającego później pushnąć commit:

```yaml
- uses: actions/checkout@v5
  with:
    ref: ${{ github.event.pull_request.head.ref }}
    fetch-depth: 0
    token: ${{ secrets.LOVSPOR_CI_PUSH_TOKEN }}
```

Przed Codexem zapisać:

```bash
BEFORE_SHA="$(git rev-parse HEAD)"
```

#### Wywołanie Codexa

Uruchomić nieinteraktywnie przez `codex exec`, z `workspace-write` i promptem z repo.

Implementer ma sprawdzić `codex exec --help` na aktualnie zainstalowanej wersji i użyć wspieranej składni. Intencja:

```bash
codex exec \
  --sandbox workspace-write \
  "$(cat .github/codex/pr-tests.md)"
```

Po runie:

1. `assert_codex_scope.sh "$BEFORE_SHA"`;
2. uruchomić relevant tests;
3. jeśli nie ma zmian -> `pushed=false`;
4. jeśli są poprawne zmiany testowe -> commit i push.

Commit:

```text
test: add independent coverage for PR #<N> [agent:codex-tests]
```

Push używa `LOVSPOR_CI_PUSH_TOKEN`.

Job ma wystawić output:

```text
pushed=true|false
```

### 9.4 Mutation job

Mutation uruchamia się dopiero, gdy:
- fast CI jest zielone;
- Codex zakończył analizę;
- Codex **nie pushnął właśnie nowego commita**.

Pseudo-warunek:

```yaml
needs: [fast-ci, codex-tests]
if: >-
  always() &&
  needs.fast-ci.result == 'success' &&
  needs.codex-tests.result == 'success' &&
  needs.codex-tests.outputs.pushed != 'true'
```

Jeżeli Codex pushnął test commit, push uruchomi nowy `pull_request:synchronize`, a nowy run wykona mutation na aktualnym SHA.

Mutation job:
- **bez LLM**;
- uruchamia istniejący pełny mutation command Lovsporu;
- zawsze zapisuje surowy output;
- parsuje wynik do `mutation-result.json`;
- uploaduje artifact nawet przy FAIL;
- wystawia normalny GitHub check.

---

## 10. `mutation-result.json` — kontrakt między deterministic i agentic

`scripts/ci/mutation_to_json.py` ma stworzyć stabilny kontrakt.

Minimalny schema:

```json
{
  "schema_version": 1,
  "commit": "<40-char sha>",
  "completed": true,
  "baseline_tests_passed": true,
  "tool": "<current mutation tool>",
  "tool_exit_code": 0,
  "mutants": {
    "total": 487,
    "killed": 478,
    "survived": 7,
    "timeout": 2,
    "invalid": 0,
    "skipped": 0
  },
  "score": 98.15,
  "gate": {
    "passed": false,
    "reason": "surviving_mutants"
  },
  "survivors": [
    {
      "id": "...",
      "file": "...",
      "line": 121,
      "symbol": "resolve_version",
      "operator": "..."
    }
  ]
}
```

### Ważne

Implementer **nie może wymyślić nowego progu mutation score**.

Ma zachować obecną politykę Lovsporu. Jeśli obecnie:
- każdy survivor = fail, zachować to;
- istnieje baseline/allowlist equivalent mutants, zachować i sformalizować;
- narzędzie ma własny exit-code gate, odwzorować go.

`mutation_gate.py` ma być prostym deterministycznym programem zwracającym `0/1`, nie LLM-em.

Artifact:

```text
mutation-result-<SHA>
```

zawiera:

```text
mutation-result.json
mutation-raw.log
```

opcjonalnie HTML/report istniejącego narzędzia.

---

## 11. Automatyczna remediation surviving mutants

Workflow:

```text
.github/workflows/mutation-remediation.yml
```

Trigger:

```yaml
on:
  workflow_run:
    workflows: ["PR Pipeline"]
    types: [completed]
```

Uruchamiać remediation tylko, jeśli istnieje poprawny `mutation-result.json` z:

```text
gate.passed == false
```

Nie zakładać, że każdy failure `PR Pipeline` jest mutation failure.

### Security

`workflow_run` jest uprzywilejowanym triggerem. Dlatego:
- działać wyłącznie dla runów pochodzących z tego samego prywatnego repo;
- nie checkoutować forków;
- artifact traktować jak dane, nigdy `eval`/`source`;
- walidować JSON schema;
- sprawdzić `result.commit == workflow_run.head_sha` przed uruchomieniem Codexa.

### Remediation limit

Maksymalnie:

```text
2
```

commity typu:

```text
[agent:codex-mutation]
```

na danym PR bez nowej ręcznej/Claude zmiany production code.

Po limicie:

```text
BLOCKED
```

oraz label, np.:

```text
needs-human:mutation
```

### Prompt Codexa — mutation remediation

`.github/codex/mutation-remediation.md`:

```text
You are the independent mutation-test remediation engineer for Lovspor.

Read mutation-result.json and inspect ONLY the listed surviving mutants.

Your allowed action is to add or strengthen tests that correctly specify existing intended behavior.

Hard constraints:
- tests only;
- do not change production code;
- do not weaken or delete existing assertions;
- do not skip/xfail tests to satisfy the gate;
- do not change mutation thresholds;
- do not add an equivalent-mutant waiver;
- do not change methodology, frozen benchmark decisions, corpus/raw, or CI configuration.

For every survivor classify it as one of:
- killable_by_correct_test
- likely_equivalent
- specification_ambiguous
- production_behavior_question
- tool_noise

Only edit tests for killable_by_correct_test.
For every other class, report it as BLOCKED and explain why human review is required.

Run the smallest relevant tests after editing.
```

Po Codex run:
- mechanical scope guard;
- jeśli poprawne test changes -> commit:

```text
test: kill mutation survivors for PR #<N> [agent:codex-mutation]
```

- push przez `LOVSPOR_CI_PUSH_TOKEN`;
- nowy `pull_request:synchronize` uruchamia pełny pipeline ponownie.

Jeśli brak bezpiecznej zmiany testowej:
- nie commitować;
- oznaczyć PR jako `needs-human:mutation`;
- dodać krótki komentarz z klasyfikacją survivorów i linkiem do workflow/artifactu.

---

## 12. Co dzieje się, gdy nowy test Codexa nie przechodzi

To jest ważny safety case.

Codex nie może „naprawić” production code.

Jeśli poprawny test odkrywa błąd implementacji:

```text
BLOCKED: production behavior
```

PR dostaje label:

```text
needs-implementation-fix
```

Na wersji 1.0 człowiek wraca do Claude Code i mówi np.:

```text
PR #143 ma needs-implementation-fix. Przeczytaj failing test i popraw implementation,
nie zmieniaj testu, chyba że potrafisz wykazać sprzeczność ze specyfikacją.
```

Po pushu Claude'a pipeline zaczyna się od początku i Codex ponownie sprawdza nowy production diff.

### Dlaczego nie automatyzujemy tego od razu

W Lovsporze błędna automatyczna zmiana production behavior jest droższa niż pojedyncza ręczna eskalacja.

Po 10–20 PR-ach można dodać osobny Claude Code GitHub Action, który reaguje na `needs-implementation-fix`, używając `CLAUDE_CODE_OAUTH_TOKEN`, ale nie należy robić tego w pierwszym wdrożeniu.

---

## 13. CLAUDE.md — reguła handoffu

Dodać krótki fragment, nie cały proces:

```markdown
## PR handoff

- Keep implementation changes scoped to small, reviewable PRs.
- Production implementation is owned by Claude/human; independent test generation is owned by remote Codex CI.
- Before opening a PR, run the fast local verification defined by the repo.
- Open the PR when implementation is ready.
- Do not manually invoke Codex for PR testing; GitHub Actions owns that handoff.
- Do not wait locally for full mutation testing.
- Never modify frozen methodology or benchmark decisions without an explicit human decision.
- A remote BLOCKED result is a required escalation, not permission to guess.
```

---

## 14. AGENTS.md — reguła Codexa

Dodać/uzupełnić:

```markdown
## CI test-engineer role

When Codex is invoked by CI for a pull request:

- You are an independent test engineer, not the production-code implementer.
- Modify only repository-approved test paths.
- Production code, methodology, frozen benchmark decisions, raw corpus, thresholds, and CI policy are read-only.
- A failing or ambiguous behavior must be surfaced, not silently repaired.
- Do not weaken tests, skip tests, or change thresholds to make the pipeline green.
```

---

## 15. Branch protection / ruleset

Dla `main` ustawić:

- require pull request before merge;
- require status checks;
- block direct pushes (poza świadomym emergency bypass);
- wymagane checks po ustabilizowaniu nazw:

```text
fast-ci
mutation
```

Codex job może być wymaganym checkiem, ale najlepiej wymagany jest **stan końcowy pipeline'u**, nie przejściowy run, który sam pushuje nowy commit.

Ważne: required check musi odnosić się do najnowszego SHA PR.

Merge pozostaje ręczny.

---

## 16. Anti-loop i anti-stale — obowiązkowe invariants

Implementacja jest niekompletna, jeśli nie ma wszystkich poniższych mechanizmów.

### A. Stary workflow nie może wygrać z nowym SHA

```yaml
concurrency:
  group: pr-${{ github.event.pull_request.number }}
  cancel-in-progress: true
```

### B. Codex test commit nie może sam ponownie generować tych samych PR tests

Commit marker:

```text
[agent:codex-tests]
```

### C. Mutation remediation nie może zapętlić się bez limitu

Marker:

```text
[agent:codex-mutation]
```

limit = 2.

### D. Każdy structured result musi być związany z SHA

```text
mutation-result.json.commit == current SHA
```

W przeciwnym razie ignorować wynik jako stale.

### E. Scope guard zawsze po Codexie

Nie polegać wyłącznie na promptach.

### F. Same-repository only

Codex subscription runner nie wykonuje kodu z forków.

---

## 17. Mutation performance

Nie optymalizować mutation testing przez LLM.

Najpierw wdrożyć obecny pełny mutation suite jako deterministyczny job.

Po uzyskaniu stabilnego pipeline można dodać dwa poziomy:

```text
PR: changed-scope mutation
nightly/main: full mutation
```

ale tylko jeśli używane narzędzie pozwala wiarygodnie ograniczyć scope bez utraty ważnych interakcji.

Pełny suite nadal powinien okresowo działać na `main` (np. nightly), nawet jeśli PR-y dostaną szybszy scoped mutation check.

---

## 18. GitHub Actions compute

Mutation job może działać na:

### Opcja A — GitHub-hosted

```yaml
runs-on: ubuntu-latest
```

Najprostsza konfiguracja.

### Opcja B — osobny self-hosted CPU runner

Jeśli mutation testing jest długi/drogi w minutach GitHuba:

```yaml
runs-on: [self-hosted, linux, mutation]
```

To powinien być **inny logiczny runner/label niż Codex**. Mutation runner nie potrzebuje `auth.json` Codexa.

Preferowana separacja:

```text
codex runner     -> ma Codex auth, wykonuje krótkie agent tasks
mutation runner  -> nie ma LLM auth, miele CPU
```

---

## 19. Observability

Każdy PR powinien dać się zrozumieć bez otwierania terminala.

GitHub job summary dla mutation ma pokazywać:

```text
SHA
Total mutants
Killed
Survived
Timeout
Score
Gate PASS/FAIL
Remediation cycle
Artifact name
```

Nie wrzucać całego mutation logu do komentarza PR.

PR comment tylko przy problemie:

```text
Mutation gate failed: 4 survivors.
Codex remediation cycle: 1/2.
2 tests added; rerunning pipeline.
```

lub:

```text
Mutation remediation BLOCKED after 2 cycles.
Reason: 1 likely equivalent mutant, 1 specification-ambiguous mutant.
Human decision required.
```

---

## 20. Rollout — wdrażać w tej kolejności

### Phase 0 — audit repo

Claude Code ma najpierw bez zmian ustalić:
- aktualne komendy fast tests;
- aktualne mutation command/tool;
- katalogi testowe;
- production paths;
- obecne GitHub workflows;
- branch protection assumptions;
- obecny `CLAUDE.md` i `AGENTS.md`;
- czy repo jest prywatne;
- czy PR-y pochodzą wyłącznie z własnego repo.

Wynik zapisać w krótkiej sekcji planu przed implementacją.

### Phase 1 — deterministic CI

Najpierw:
- fast CI;
- mutation wrapper;
- `mutation-result.json`;
- artifacts;
- concurrency;
- required checks.

Bez Codexa.

Przetestować na jednym ręcznym PR.

### Phase 2 — Codex PR tests

Dodać:
- self-hosted runner;
- ChatGPT-managed auth;
- prompt;
- scope guard;
- test-only commit;
- PAT push;
- anti-loop.

Przetestować na PR, w którym celowo brakuje oczywistego regression testu.

### Phase 3 — mutation remediation

Dodać:
- `workflow_run` handler;
- artifact validation;
- Codex mutation prompt;
- max 2 cycles;
- BLOCKED labels/comments.

### Phase 4 — dopiero po 10–20 PR-ach

Opcjonalnie rozważyć Claude GitHub Action do `needs-implementation-fix`.

Nie wdrażać tego w pierwszej iteracji.

---

## 21. Test akceptacyjny całego systemu

Implementacja nie jest zakończona, dopóki nie przejdą wszystkie scenariusze.

### Scenario 1 — normalny PR bez brakujących testów

1. Claude local otwiera PR.
2. Fast CI PASS.
3. Codex analizuje PR i nie zmienia nic.
4. Mutation PASS.
5. PR = READY TO MERGE.

### Scenario 2 — Codex znajduje brakujący test

1. Claude otwiera PR.
2. Codex dodaje wyłącznie test.
3. Scope guard PASS.
4. Codex pushuje `[agent:codex-tests]`.
5. Nowy `synchronize` run startuje automatycznie.
6. Stary run zostaje anulowany.
7. Na nowym SHA Codex nie zapętla się.
8. Mutation działa na nowym SHA.

### Scenario 3 — mutation survivor killable testem

1. Mutation FAIL.
2. `mutation-result.json` zapisuje survivor.
3. Remediation uruchamia Codexa.
4. Codex dodaje test-only commit `[agent:codex-mutation]`.
5. Pipeline startuje ponownie.
6. Mutation PASS.

### Scenario 4 — ambiguous/equivalent mutant

1. Mutation FAIL.
2. Codex klasyfikuje survivor jako non-safe-to-fix.
3. Nie modyfikuje production code.
4. PR otrzymuje `needs-human:mutation`.
5. Pipeline kończy się `BLOCKED`, nie fałszywym PASS.

### Scenario 5 — Codex próbuje dotknąć production code

1. Scope guard wykrywa plik.
2. Job FAIL.
3. Nic nie jest commitowane ani pushowane.

### Scenario 6 — stale result

1. Mutation kończy się dla starego SHA po nowym pushu.
2. Stary run jest anulowany albo downstream porównuje SHA.
3. Stary wynik nie wywołuje remediation dla nowego HEAD.

### Scenario 7 — fork/untrusted PR

1. Codex self-hosted job nie uruchamia się.
2. Żaden Codex auth ani write token nie jest udostępniany niezaufanemu kodowi.

---

## 22. Definition of Done dla wdrożenia infrastruktury

Wdrożenie uznajemy za gotowe tylko wtedy, gdy:

```text
[ ] Existing Lovspor tests still pass
[ ] Existing mutation policy is unchanged
[ ] PR Pipeline runs on opened/synchronize/reopened
[ ] Concurrency cancels stale PR runs
[ ] Codex runs automatically on trusted same-repo PRs
[ ] Codex uses ChatGPT-managed auth, not OPENAI_API_KEY
[ ] Codex can change only allowed test paths
[ ] Codex test commit automatically triggers a fresh PR pipeline
[ ] Codex-generated commits cannot create an infinite loop
[ ] Mutation testing runs without any LLM involved
[ ] mutation-result.json is produced and tied to exact SHA
[ ] Mutation artifacts are uploaded on PASS and FAIL
[ ] Mutation remediation is limited to 2 Codex cycles
[ ] Non-test/ambiguous mutation cases become BLOCKED
[ ] No LLM can change methodology/frozen benchmark decisions automatically
[ ] main requires final CI checks
[ ] Merge remains human-controlled
[ ] Documentation explains auth rotation/reseed for Codex
```

---

## 23. Prompt do Claude Code — implementacja tego dokumentu

Po skopiowaniu tego pliku do repo jako np.:

```text
docs/LOVSPOR_AGENTIC_CI_IMPLEMENTATION.md
```

uruchomić Claude Code w repo i dać:

```text
/goal Implement the agentic CI architecture described in
@docs/LOVSPOR_AGENTIC_CI_IMPLEMENTATION.md.

Treat that document as the target architecture, but first audit the current repository
and adapt commands, paths, test directories, mutation tooling, and existing workflows to
what actually exists. Do not replace working project conventions merely to match examples
in the document.

Non-negotiable constraints:
- preserve the current mutation-testing policy and thresholds;
- preserve frozen benchmark/methodology decisions;
- Codex must never be allowed to modify production code;
- no OpenAI API-key based Codex automation;
- do not introduce ANTHROPIC_API_KEY billing;
- never expose Codex auth to fork/untrusted PR code;
- preserve small-PR workflow;
- no automatic merge;
- stop and report BLOCKED when a required human/security decision cannot be inferred safely.

Implementation order:
1. audit and write the concrete repo-specific plan;
2. deterministic CI + mutation JSON contract;
3. Codex test-author integration with mechanical scope guard and anti-loop;
4. mutation remediation with two-cycle limit;
5. documentation and acceptance tests.

Before declaring the goal complete, demonstrate the acceptance scenarios from section 21
as far as they can be exercised without exposing secrets. For any step that requires me to
create a GitHub secret, PAT, self-hosted runner, or interactive Codex login, stop at a clearly
labeled HUMAN SETUP checkpoint, tell me exactly what command/UI action I must perform, and
continue with all repo-side work that does not require that secret.

Do not implement the optional Phase 4 Claude GitHub auto-fix yet.
```

### Jak pracować podczas setupu

Claude może dojść do checkpointu typu:

```text
HUMAN SETUP REQUIRED:
1. Register runner with label codex.
2. Seed CODEX_HOME/auth.json.
3. Add LOVSPOR_CI_PUSH_TOKEN secret.
```

Po wykonaniu tych czynności nie trzeba odpalać Codexa ręcznie. W tej samej sesji Claude:
- weryfikuje runner/workflow;
- odpala testowy PR/workflow;
- poprawia wyłącznie infrastrukturę;
- kończy goal po przejściu acceptance checks.

---

## 24. Codzienny workflow po wdrożeniu

Normalna praca:

```text
$ claude

> /goal Zaimplementuj LOV-231 zgodnie z issue/spec.
> Zachowaj mały PR, uruchom szybkie lokalne testy i otwórz PR.
```

Po otwarciu PR:

```text
Claude local                    DONE
Codex manual terminal           NIEPOTRZEBNY
manual mutation command         NIEPOTRZEBNY
copy/paste Claude -> Codex      NIEPOTRZEBNY
polling długich testów przez AI NIEPOTRZEBNY
```

GitHub sam doprowadza PR do:

```text
READY TO MERGE
```

albo:

```text
BLOCKED + konkretny powód
```

I dopiero wtedy człowiek podejmuje decyzję.

---

## 25. Źródła techniczne zweryfikowane 2026-08-11

Dokument opiera się na aktualnej dokumentacji:

- Anthropic: Claude Code GitHub Actions — subscription OAuth przez `CLAUDE_CODE_OAUTH_TOKEN`, generowany `claude setup-token`.
- Anthropic: Claude Code `/goal` — session-scoped praca aż do spełnienia warunku.
- OpenAI: Maintain Codex account auth in CI/CD (advanced) — ChatGPT-managed `auth.json`, trwały `CODEX_HOME`, trusted private self-hosted runner, serializowane użycie auth.
- OpenAI: Codex non-interactive mode — `codex exec` do automatyzacji.
- GitHub: `GITHUB_TOKEN` — push wykonany tym tokenem nie powinien być używany, jeśli ma wywołać kolejny standardowy workflow; do tego użyć GitHub App/PAT.
- GitHub: workflow concurrency — anulowanie stale runs po nowym SHA.
- GitHub: `workflow_run` security — nie wykonywać niezaufanego kodu/artifactów z forków w uprzywilejowanym kontekście.
- GitHub: required status checks — merge blokowany do PASS checków dla aktualnego SHA.

