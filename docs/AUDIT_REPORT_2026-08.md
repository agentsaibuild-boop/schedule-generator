# Одиторски доклад — сесия август 2026 (ревизия 2)

> Ревизия 2 адресира 10-те критични корекции от независимия одитор:
> точна ревизия, before/after числа, structured provenance, разделяне
> unit↔E2E, смекчени абсолютни твърдения, HEAD↔история, fail-closed артефакт.

## 0. Одитирана ревизия (за възпроизводимост)

```
Audit target branch      : feat/structured-output-streaming   (PR #2)
Branch tip (жив)         : прочетете с `git rev-parse HEAD` (докладът е върхът)
Последна ЛОГИЧЕСКА промяна: 827efb889421044f88ac300d37636fdf96641ec9
                           (MSPDI dependency int-ID fix, §6; commit-ите над него —
                            доклад — НЕ променят логика; 1795 passed важи за tip-а)
Base main SHA            : 44302a9d3a5e7a34575b4a38d71d22222f25d0dc
Working tree             : clean
Archive SHA-256 @827efb8 : 02447726c7f0bba0f1d38af6a734c95d6ebde6fad9f07a55c648b2a845b9bd72
                           (git archive --format=tar 827efb8 | sha256sum)
```

**Важно за обхвата:** PR #1 е **merge-нат в main**; PR #2 (този HEAD) е **отворен,
непотвърден**. CI зелено на main НЕ доказва състоянието на PR #2 — за независима
проверка ползвайте горния HEAD (или чист ZIP от него). Твърденията по-долу,
маркирани „(PR #2)", НЕ са в main.

**Статус на тестовете:** **1795 unit/integration теста преминават, 1 пропуснат
(skip); директорията `tests/e2e` е ИЗКЛЮЧЕНА изцяло → 0 E2E теста изпълнени.**
(виж раздел 9 за какво това НЕ доказва.)

---

## 1. Резюме (какво и защо)

Сесията стартира от тест на реален търг (градски ВиК обект: водопровод PE
Ф90–Ф225, канализация PP Ф300–Ф1200, настилки, СКО/СВО). Тестът показа 59
„недоказани" продължителности **в AI-генерирания график**. Разследването
установи, че причината НЕ е липса на норми, а бъгове в разпознаването + реални
липси в данните. Всичко е адресирано, като детерминистичният гейт остава
последната дума (fail-closed).

| Тема | Commit(и) | В main? |
|------|-----------|---------|
| Премахване на клиентски данни (само HEAD, виж §7) | `ecb63f3` | да |
| Норми v0.5 | `454f498` | да |
| Sonnet worker (WORKER_MODEL override) | `a296ecc` | да |
| Fail-closed date-cascade | `741704b` | да |
| Security/PII scanner + CI | `85be567` | да |
| BOQ coverage / provenance | `3335079` | да |
| int-ID crash fix | `16c898c` | да |
| CI actions v7 | `d99c1a3` | да |
| **material enum + streaming + този доклад** | `1e9763e`, `dd11109` | **НЕ (PR #2)** |

---

## 2. Норми v0.5 — произход (structured provenance)

`config/productivities.json` е единственият **нормативен** източник за
детерминистично **ДОКАЗАНИТЕ** продължителности. AI-предложена стойност за
задача без приложимо правило остава `NOT_PROVEN` и НЕ се допуска до експорт —
т.е. файлът НЕ е единственият източник на *всички* стойности, а на доказаните.

Всяка v0.5 норма носи структуриран произход в `_provenance_v0_5` (config):

| Норма | Стойност | source_type | confidence | ⚠️ |
|-------|----------|-------------|-----------|----|
| PP Ф300–800 | 12 м/д | contractor_field_rule (2026-08) | provisional | полево |
| PP >Ф800 | 6 м/д | contractor_field_rule | provisional | полево |
| PE Ф200–350 | 40 м/д | contractor_field_rule | provisional | полево |
| СВО | 4 бр/д | contractor_field_rule | provisional | полево |
| СКО | 4 бр/д | contractor_field_rule | provisional | полево |
| Асфалт | 150 м²/д | range_selection (100–300, градски) | provisional | зависи от обекта |
| Плочи | 80 м²/д | derived (15–30 м²/раб × crew 4) | provisional | зависи от бригадата |
| Бордюр 15/25/50 | 20 м/д | range_selection (15–30) | provisional | зависи от бригадата |

**Обхват на операциите (нерешен въпрос):** по конвенция (урок #16)
`effective_rate` = ПЪЛНИЯТ цикъл, ограничен от най-бавната операция. За PP
изпълнителят даде стойността в същия контекст, но ТОЧНИЯТ обхват (укрепване?
изпитване? възстановяване?) **НЕ е изрично потвърден** — маркирано в
`_provenance_v0_5.operation_scope`, изисква потвърждение преди подписване.
`reviewed_by` е празно за всички → нито една норма не е независимо прегледана.

**Тестове:** `tests/test_duration_calculator.py` — PP, PE, СВО/СКО, настилки/бордюри.

---

## 3. Детекционни фикси — доказани от реален КСС

| # | Проблем (реални данни) | Фикс | Файл |
|---|------------------------|------|------|
| 1 | „Ф300" не се четеше като DN | `_DN_RE` приема Ф/Φ/Ø/⌀ | `duration_calculator.py` |
| 2 | Няма шаблон за PP | нов PP шаблон + хомоглиф РP→PP | `duration_calculator.py` |
| 3 | Материалът в клетка „diameter" не се четеше | haystack включва diameter | `duration_calculator.py` |
| 4 | Мерна единица „брой" не се разпознаваше | добавена към count единиците | `duration_calculator.py` |
| 5 | Промптът не позволяваше PP → моделът пишеше PE | `SUPPORTED_MATERIALS` enum + PP (PR #2) | `ai_processor.py` |

---

## 4. Before/after върху СЪЩИЯ реален КСС (детерминистично, възпроизводимо)

Измерено с калкулатора върху 24-те тръбни/бройкови/площни реда на реалния КСС.
**before** = commit `ecb63f3` (преди норми/детекция); **after** = HEAD. Без AI
(възпроизводимо; скрипт по-долу).

| Код | ПРЕДИ | СЛЕД |
|-----|------:|-----:|
| CALCULATED (доказани) | **0** | **14** |
| MISSING_DN | 13 | 0 |
| MISSING_LENGTH (заглавни/сумарни редове) | 6 | 6 |
| NOT_PARAMETRIC | 3 | 2 |
| MISSING_MATERIAL (Ф225 — липсва материал в КСС) | 1 | 1 |
| COUNT_NO_RATE (водомерна шахта — по решение) | 1 | 1 |

**Тълкуване:** 13-те MISSING_DN бяха „Ф300…" (нечетени от стария `_DN_RE`) →
сега 0. Доказаните скочиха 0 → 14 от 24. Остатъкът е коректен: 6 са
заглавни/сумарни редове (не дейности), 1 е реална липса на материал в КСС, 1 е
съзнателно нерешена шахта.

**⚠️ Разграничение:** горното е ДЕТЕРМИНИСТИЧНИЯТ слой върху КСС-редове.
Първоначалните „59 недоказани" бяха от AI-**генериран** (разгънат) график —
различна популация задачи. **Пълен before/after на самите 59 изисква нов AI
run** (за предпочитане Sonnet) и все още НЕ е направен (чака кредити). Настоящата
таблица доказва, че детекцията+нормите работят, НЕ че конкретните 59 са станали 14.

Скрипт: изважда `ecb63f3:src/duration_calculator.py` + config и пуска и двете
версии върху `КСС.json` (не е в репото — клиентски данни).

---

## 5. Fail-closed поведение — артефакт от реалния тест

Реален DeepSeek run (worker=`deepseek/deepseek-chat` през OpenRouter; controller
недостъпен → fallback). Всяка част беше маркирана от AI като „approved", но
детерминистичната валидация я ОТХВЪРЛИ:

| Част | AI статус | Гейт статус | Пример грешка | Exportable |
|------|-----------|-------------|---------------|-----------|
| Vodoprovodna | approved | **invalid** | задача (2) зависи от несъществуващо ID 1 | **False** |
| Kanalizaciya #1 | approved | **invalid** | 30 грешки; „Изкоп DN300 (2) → ID 1" | **False** |
| Kanalizaciya #2 | approved | **invalid** | 9 грешки + 3 цитатни несъответствия | **False** |
| Kanalizaciya #3 | approved | **invalid** | 16 грешки + 8 цитатни несъответствия | **False** |

**Ограничение на артефакта:** този run НЕ записа input/output hash-ове —
таблицата е от лога, не е bit-for-bit възпроизводима. Възпроизводимата част е
детерминистичната валидация (`tests/test_validation_gate.py`,
`tests/test_export_policy.py`): невалиден/недоказан график НЕ става
одобрен+експортируем под нито една политика.

---

## 6. Bug fixes + MSPDI структурна валидация (намерени от тестване)

**6а. int-ID crash** (`src/schedule_builder.py`) — `validate_schedule` хвърляше
`TypeError` при спатиален екип-конфликт, когато ID-тата са `int`. Поправено +
`tests/test_spatial.py::test_team_overlap_warning_survives_integer_task_ids`.

**6б. MSPDI зависимости изпускани при int ID** (`src/export_xml.py`) —
структурната валидация на експорта (одит §10.4) разкри втори int/string бъг:
`uid_map` се пълнеше със суровия `task['id']` (int при DeepSeek), а търсенето
беше `str(dep)` → миссмач → **зависимостите тихо изчезваха от MS Project XML**
(графикът губеше логиката си). Поправено (нормализация към `str()` от двете
страни) + `tests/test_xml_msproject_semantics.py::test_dependencies_survive_integer_task_ids`.

**6в. MSPDI структурни гаранции (потвърдени детерминистично, без реален MS
Project):** експортът на валиден график дава: `DurationFormat=5` (дни);
`ConstraintType=2` (Must Start On) + `ConstraintDate` = сметнатата дата → **датите
на детерминистичния гейт се ЗАКОВАВАТ** и MS Project не ги преизчислява;
milestone = `PT0H0M0S` + `Milestone=1`; зависимостите → `PredecessorLink`.
Бележка: кодът ползва `Manual=0` + `ConstraintType=2` (режим 'pinned'), НЕ
`Manual=1` както казва CLAUDE.md — двата подхода заковават датите; CLAUDE.md е
опростен спрямо реалния (audit-референтен) подход. **НЕ е доказано:** отваряне в
реален Microsoft Project (§10.4 остава частично).

---

## 7. Structured output + streaming (PR #2 — НЕ в main)

- **`build_schedule_response_schema()`** — JSON schema с `material` като enum.
  Отказът на structured-output режима **не прекратява автоматично** генерацията;
  системата прави повторен опит БЕЗ schema. Повторният provider call все пак
  може да се провали (мрежа, quota, authentication, model грешка).
- **Streaming** при `max_tokens ≥ 8000` — **намалява** риска от client-side
  timeout при дълги отговори; НЕ премахва provider/proxy/network/SDK timeout.
- **Разделение:** schema пази формата и валидните материали; референтната цялост
  на графа (фантомни ID) остава на детерминистичния гейт.
- **⚠️ Не е валидирано срещу жив модел** — API пътищата (`output_config` /
  `response_format`) са best-effort с fallback, но НЕ са изпълнявани срещу
  реален provider (чака Anthropic кредити).

**Тестове:** `tests/test_ai_router.py` (mock streaming/fallback),
`tests/test_ai_processor_pure.py` (enum синхрон).

---

## 8. Тестове — какво доказват и какво НЕ

- Изпълнено: `pytest tests/ --ignore=tests/e2e` → **1795 passed, 1 skipped**.
- **0 E2E теста изпълнени** (`tests/e2e` изключена).
- Следователно НЕ са доказани от този резултат: реалните provider SDK пътища,
  structured output срещу жив модел, streaming срещу жив модел, UI, MSPDI
  round-trip през MS Project.

---

## 9. Инфраструктура / сигурност

- **PII/секрет скан** (`tools/security_scan.py`): NFC+casefold, `--staged` чете
  git index, operational-error rc=3 (fail-closed). **Тестван срещу изброените
  сценарии** (staged-index, pathname, Unicode, NUL-safe, operational-error) —
  НЕ се твърди общо „незаобиколим".
- **CI** (`.github/workflows/ci.yml`): пълни unit тестове + 3 скана; actions v7.
  Изисква GitHub secret `PII_DENYLIST`. Зелено на main и на PR #2.
- **Клиентски данни — HEAD vs ИСТОРИЯ (корекция на предишна формулировка):**
  - `ecb63f3` премахва клиентски файлове от **текущия HEAD**. HEAD е чист
    (denylist скан rc=0).
  - **НО Git ИСТОРИЯТА все още съдържа** клиентски маркер (име на район, от
    denylist-а) в много по-стари commits (проверено с `git rev-list --all` +
    `git grep` срещу denylist термин — резултатът е непразен). Пълното
    премахване изисква history rewrite (git filter-repo / BFG) + force-push.
  - Забележка: това са имена на РАЙОН, не secrets. `.env` никога не е бил в git.
    Ако някога реален secret е бил commit-нат, той трябва да се ротира
    НЕЗАВИСИМО от изтриването.

---

## 10. Известни ограничения / остава да се направи (пълен списък)

1. Реален **Sonnet run** (чака Anthropic кредити) — worker пътят непроверен.
2. **Before/after на самите 59** от AI-генериран график (§4 е детерминистичен проксѝ).
3. **Live structured-output** и при двата provider пътя (Claude/DeepSeek).
4. **MSPDI round-trip** — структурата е валидирана детерминистично (§6в:
   DurationFormat=5, дати заковани, зависимости запазени след §6б); ОСТАВА
   отваряне в реален Microsoft Project (експорт → отваряне → визуална проверка).
5. **E2E / UI** прогон (0 изпълнени).
6. **История на Git** за клиентски данни (§9) — history rewrite, ако репото се споделя.
7. **Лабораторно потвърждение** на полевите норми + обхват на операциите (§2).
8. **Multi-column / производни количества** в КСС парсването.
9. Неимплементиран **EvidenceRelation** домейн модел (описан в
   `docs/DOMAIN_MODEL_EvidenceRelation.md`).
10. **Норми `reviewed_by` празно** — нито една не е независимо прегледана.

---

## 11. Как одиторът да провери (команди)

```bash
# 0. Точна ревизия
git rev-parse HEAD           # запишете SHA-то (branch tip)
git rev-parse dd11109        # последна логическа промяна (кодът под одит)
git status --short           # трябва: празно (clean)

# 1. Всички unit теста (0 E2E)
uvx --with-requirements requirements.txt pytest tests/ --ignore=tests/e2e
#   Очаквано: 1795 passed, 1 skipped

# 2. Норм-стойности + структуриран произход
python -c "import json;d=json.load(open('config/productivities.json',encoding='utf-8'));print(d['version']);import pprint;pprint.pprint(d['_provenance_v0_5'])"

# 3. Синхрон промпт↔детектор
python -c "from src.duration_calculator import SUPPORTED_MATERIALS;print(SUPPORTED_MATERIALS)"

# 4. Git ИСТОРИЯ за клиентски маркери (виж §9)
git rev-list --all | while read c; do git grep -liE "<denylist-термин>" "$c" -- 2>/dev/null; done | sort -u | head

# 5. История на промените
git log --oneline 44302a9..HEAD    # PR #2 delta спрямо main
```

---

## 12. Обобщение за одитора

Промените са консервативни и fail-closed. Тази ревизия добавя точния HEAD,
детерминистичен before/after (0→14 от 24 КСС-дейности), структуриран произход на
нормите, fail-closed артефакт и ясно разделяне unit↔E2E и HEAD↔история.
Абсолютните твърдения („never breaks", „removes timeout risk", „bypass-proof")
са смекчени. За пълна проверка е нужен чист audit ZIP от HEAD `dd11109` и
изпълнение на остатъка от раздел 10 (Sonnet run, MSPDI round-trip, live
structured output, E2E, history scan).
