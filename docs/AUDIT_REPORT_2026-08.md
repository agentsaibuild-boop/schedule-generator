# Одиторски доклад — сесия август 2026 (ревизия 2)

> Ревизия 2 адресира 10-те критични корекции от независимия одитор:
> точна ревизия, before/after числа, structured provenance, разделяне
> unit↔E2E, смекчени абсолютни твърдения, HEAD↔история, fail-closed артефакт.

## 0. Одитирана ревизия (за възпроизводимост)

```
Audit target branch      : feat/structured-output-streaming   (PR #2)
Branch tip (жив)         : прочетете с `git rev-parse HEAD` (докладът е върхът)
Последна ЛОГИЧЕСКА промяна: cc5b913eaf0ec07f12583be788fa8f0c625db696
                           (Sonnet §5 re-run фиксове §6е; commit-ите над него —
                            доклад — НЕ променят логика; 1803 passed (Win))
Base main SHA            : 44302a9d3a5e7a34575b4a38d71d22222f25d0dc
Working tree             : clean
Archive SHA-256 @cc5b913 : ce9fa2fb4a3a6e1b03807201f66c11ff82e35d7281f53dc2baad1950afc42f54
                           (git archive --format=tar cc5b913 | sha256sum)
```

**Важно за обхвата:** PR #1 е **merge-нат в main**; PR #2 (този HEAD) е **отворен,
непотвърден**. CI зелено на main НЕ доказва състоянието на PR #2 — за независима
проверка ползвайте горния HEAD (или чист ZIP от него). Твърденията по-долу,
маркирани „(PR #2)", НЕ са в main.

**Статус на тестовете:** **1803 unit/integration теста преминават, 1 пропуснат
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
| **material enum + streaming + одит v27 фиксове + доклад** | `1e9763e`…`cc5b913` | **НЕ (PR #2)** |

---

## 2. Норми v0.5 — произход (structured provenance)

`config/productivities.json` е единственият **нормативен** източник за
детерминистично **ДОКАЗАНИТЕ** продължителности. AI-предложена стойност за
задача без приложимо правило остава `NOT_PROVEN` — файлът НЕ е източник на
*всички* стойности, а на доказаните.

**⚠️ Корекция на предишно надценяване (одит т.1):** NOT_PROVEN **не значи
автоматично „не се експортира".** Реалното поведение по политика (`EXPORT_POLICY`,
`_export_decision`):
- **strict** — недоказани продължителности (и mismatch/uncovered) **блокират** експорта.
- **provisional (ПО ПОДРАЗБИРАНЕ)** / **lenient** — одобрен+валиден график **Е
  експортируем**, а недоказаните излизат като `export_blockers` в РЕЗУЛТАТА/UI.
Т.е. fail-closed важи безусловно за **невалиден** и **неодобрен** график (всички
политики); за недоказани ДУРАЦИИ — само под strict. Тестовете изрично изискват
provisional експорт да е разрешен.

⚠️ **Уточнение (одит т.: маркер в файловете):** предупрежденията/blockers са в
РЕЗУЛТАТА и UI, **НЕ във самите PDF/XML файлове**. Експортите носят само общия
AI-disclosure маркер (EU AI Act чл. 50), НЕ отделен „предварителен" печат.
Вграждане на blockers/„предварителен" във файла е бъдеща работа (§10).

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
различна популация задачи. Sonnet §5 re-run **е направен** (§6е, през OpenRouter),
но чисто пълно покритие остава да се докара. Настоящата таблица доказва, че
детекцията+нормите работят, НЕ че конкретните 59 са станали 14.

**Възпроизводимо от пакета (одит т.7):** реалният `КСС.json` е клиентски и не е
в репото, но `tests/fixtures/synthetic_kss_rows.json` (генерична нотация, СЪЩИТЕ
капани: Ф-диаметри, PP, „брой", кв.м) + `tools/kss_coverage_demo.py` дават
проверимо покритие **13/16** доказани. Одиторът пуска:
```
python tools/kss_coverage_demo.py      # → 13/16 доказани, всички по очакване
```
За before: `git checkout ecb63f3 -- src/duration_calculator.py config/productivities.json`
→ пусни пак → **0 доказани** (всички Ф-диаметри падат MISSING_DN) → върни с
`git checkout HEAD -- ...`. Тоест 0→13 е независимо възпроизводимо от репото.

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
`tests/test_export_policy.py`): **НЕВАЛИДЕН или НЕОДОБРЕН** график не става
експортируем под нито една политика. (Забележка — синхрон с §2: недоказаните
ДУРАЦИИ на иначе валиден+одобрен график НЕ блокират под provisional/lenient,
а само под strict.)

⚠️ **Важно уточнение (одит v27, т.2):** тази таблица е от DeepSeek run **преди**
поправката на int-ID валидатора. Валидаторът е давал false-positive „несъществуващо
ID" за числови зависимости → част от „invalid" тук е артефакт на бъга, не на
модела. Таблицата трябва да се **пре-пусне** (Sonnet) след §6г фикса, преди да е
доказателство за поведението на модела.

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

### 6г. Одит v27 — намерени от втория одит (7098b26), поправени

| # | Находка (възпроизведена от одитора) | Фикс | Тест |
|---|-------------------------------------|------|------|
| т.2 **(КРИТИЧНО)** | `validate_schedule` отхвърляше ВАЛИДЕН график с int ID (A.id=1, B.deps=[1]) като „несъществуващо ID"; cycle-detection също счупен | нормализация `task_id`→str навсякъде (`_task_key`) | `test_validation_gate.py::test_valid_schedule_with_integer_ids_*` |
| т.3 | MSPDI `OutlineNumber` при числов `parent_id` даваше „1" вместо „1.1" | нормализация `parent_id`→str | `test_..._survives_integer_parent_id` |
| т.4 | security scanner: път с малки букви заобикаляше блокирането | `re.IGNORECASE` на `_PATH_RE` | `test_security_scan.py::test_machine_path_lowercase_*` |
| т.5 | дробна дурация → `PT12.0H0M0S`, importer четеше 0 | цяло число часове (`int(round(...))`) | `test_fractional_duration_is_integer_hours` |
| т.6 | milestone `Start=08:00`, `Finish=17:00` (непоследователно) | `Finish == Start` за milestone | `test_milestone_start_equals_finish` |

**⚠️ Важно следствие от т.2:** валидаторът е давал **false-positive** „фантомни
зависимости" за числови ID. Затова „гейтът отхвърли всяка част" в §5 частично се
дължи на този бъг, НЕ само на слаб модел. След фикса частите с валидни числови
зависимости вече не се отхвърлят фалшиво — §5 таблицата трябва да се пре-пусне
със Sonnet (чака кредити), преди да се твърди „моделът дава счупени графи".

### 6д. Одит v28 — двете незатворени находки от третия одит (5e345cf), затворени

| # | Находка | Фикс | Тест |
|---|---------|------|------|
| т.1 | дробна дурация: XML `PT12.0H`→ поправено, но round-trip 1.5→2 и Start/Finish (8ч) vs Duration (12ч) се разминаваха | **договор: цели работни дни** — export ceil-ва до цял ден (1.5д→2д, `PT16H`, Finish обхваща 2 дни, консистентно) + валидаторско предупреждение | `test_fractional_duration_ceils_to_whole_days_consistently` |
| т.2 | MSPDI йерархия частична: OutlineLevel на дете оставаше 1 (не 2); child-преди-parent губеше йерархията и дублираше номера | **order-independent hierarchy pass** (`_compute_hierarchy`): ниво от дълбочината на parent-веригата, номер независим от реда | `test_outline_hierarchy_is_order_independent` |

Освен това: махнато „bypass-proof" от коментарите на скенера/хука; §2/§5 текст
синхронизиран с реалната export политика; „предварителен" маркер уточнен (не е
във файловете); бройки 1801→1803.

### 6е. §5 RE-RUN със Sonnet 5 (през OpenRouter, 2026-08) — направен

Пуснат реалният търг с **Claude Sonnet 5 през OpenRouter** (Anthropic директните
кредити са изчерпани; OpenRouter има). Разход: **~$2.30**.

**Резултати:**
- ✅ **НЯМА „несъществуващо ID" false-positives** при валидни числови/структурни
  зависимости → **потвърждава т.2**: старите „фантомни зависимости" в §5-таблицата
  бяха бъг на валидатора, НЕ на модела.
- 🐛→✅ **КРИТИЧЕН production бъг, намерен от този тест:** `response_schema`
  (json_schema от PR #2) при строги provider-и (Anthropic/OpenRouter) **ИЗТРИВАШЕ
  всички недекларирани полета** — Sonnet върна `{"tasks":[{"material":"PE"}]}`
  (id/name/duration изтрити) → генерацията се чупеше. Пълната schema после удари
  union-type лимита (>16). **Решение:** `{"type":"json_object"}` режим (валиден
  JSON без ограничаване на полета, всички provider-и); материалният enum остава в
  промпта. Commit `912bbf9`.
- 🐛→✅ **OpenRouter worker пътят:** твърд таван 8192, без streaming → truncation.
  Добавени streaming (`_openai_request`) + `GEN_MAX_TOKENS`/`ANALYSIS_MAX_TOKENS`
  (commits `912bbf9`, `cc5b913`).
- ⚠️ **Ново реално наблюдение (НЕ false-positive):** Sonnet понякога пише
  зависимостите като `"V03 (SS+30)"` — вгражда тип/лаг В ID-то. Валидаторът
  правилно ги отхвърля (няма задача с това ID) → **коректно fail-closed на реална
  грешка на модела**. Подобрение (парсване на този формат / промпт) = бъдеща работа.

**Извод:** дефинитивният §5 re-run **засилва доверието в гейта** (реалните
рехекции са реални, false-positive-ите ги няма) и разкри критичен бъг, който щеше
да счупи всеки силен provider. Пълно чисто покритие остава да се докара (анализ/
генерация тавани + дефолт-конфиг за Sonnet).

**За постоянна употреба (`.env`):** `DEEPSEEK_MODEL=anthropic/claude-sonnet-5`
+ `GEN_MAX_TOKENS=32000` + `MAX_ROWS_PER_PART=50` (или `deepseek/deepseek-v4-flash`
за ~10× по-евтино).

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

- Изпълнено: `pytest tests/ --ignore=tests/e2e` → **1803 passed, 1 skipped** (Windows).
- **Платформена разлика (одит):** 1 тест (tab/newline в име на файл) се пропуска
  на Windows, но се изпълнява на Linux → там резултатът е **1804 passed, 0 skipped**.
  Collection total = 1804. Затова манифест-числото зависи от платформата.
- **0 E2E теста изпълнени** (`tests/e2e` изключена).
- Следователно НЕ са доказани от този резултат: реалните provider SDK пътища,
  structured output срещу жив модел, streaming срещу жив модел, UI, MSPDI
  round-trip през реален MS Project.
- **„CI зелено" НЕ е проверимо от `git archive`** — иска GitHub Actions run/log.
  От пакета се проверява само локалното пускане на тестовете (командата горе).

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

> Одит v27 (§6г) поправи 5 кодови находки (int-ID валидатор, MSPDI outline,
> scanner lowercase, дробни дурации, milestone). Остатъкът по-долу е реален.

1. Реален **Sonnet run** (чака Anthropic кредити) — worker пътят непроверен.
   ⚠️ Заедно с §5: **пре-пусни таблицата** — int-ID false-positives (т.2) са
   надували „счупените графи", затова текущата §5 НЕ е чисто доказателство за
   слаб модел.
2. **Before/after на самите 59** от AI-генериран график. Детерминистичният
   проксѝ вече е независимо възпроизводим (§4: synthetic fixture, 0→13).
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
git rev-parse cc5b913        # последна логическа промяна (кодът под одит)
git status --short           # трябва: празно (clean)

# 1. Всички unit теста (0 E2E)
uvx --with-requirements requirements.txt pytest tests/ --ignore=tests/e2e
#   Очаквано: 1803 passed, 1 skipped

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
детерминистичен before/after (независимо възпроизводим 0→13 от 16 върху
synthetic fixture; реалният КСС даде 0→14 от 24, но иска Git история), структуриран произход на
нормите, fail-closed артефакт и ясно разделяне unit↔E2E и HEAD↔история.
Абсолютните твърдения („never breaks", „removes timeout risk", „bypass-proof")
са смекчени. За пълна проверка е нужен чист audit ZIP от HEAD `cc5b913` и
изпълнение на остатъка от раздел 10 (Sonnet run, MSPDI round-trip, live
structured output, E2E, history scan).
