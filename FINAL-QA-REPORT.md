# FINAL QA REPORT — ВиК Schedule Generator v0.9.0
_Дата: 2026-03-11 | Методология: static analysis + py_compile + pytest_

---

## Резюме

| Категория | Критични | Средни | Леки |
|-----------|----------|--------|------|
| Links / Navigation | 0 | 0 | 1 |
| UI Integrity | 1 | 2 | 2 |
| Routes / API | 2 | 3 | 1 |
| Imports / Deps | 0 | 1 | 3 |
| AI Prompts (Adversary) | 4 | 3 | 0 |
| **ОБЩО** | **7** | **9** | **7** |

**Тестове:** `1/1 PASSED` (unit) | E2E: изискват стартирано приложение

---

## КРИТИЧНИ — блокират потребителя или дават грешни резултати

### C1 — `project_type` се губи между analyze → generate
**Файл:** [src/chat_handler.py](src/chat_handler.py) | **Засяга:** всяко генериране
`analyze_documents()` записва `project_type` в JSON, но `_handle_generate_schedule()` чете от `project_context.get("type", "")`. При липса на ръчна настройка → `project_type = ""` → AI генерира с грешна методика.
**Fix:** Прочети `project_type` от резултата на `analyze_documents()` и го пази в session_state.

### C2 — OUT-OF-SCOPE не е валиден изход на класификатора
**Файл:** [src/ai_processor.py](src/ai_processor.py) | **Засяга:** HDD/аварийни проекти
Промптът изброява 5 типа без `out_of_scope`. HDD проект → получава методика за нормално строителство → генерира физически невъзможен график.
**Fix:** Добави `"out_of_scope"` като валиден тип в класификационния промпт.

### C3 — КПС не разпознава зависимостта от Тласкател
**Файл:** [src/chat_handler.py](src/chat_handler.py) / [src/ai_processor.py](src/ai_processor.py)
`_start_sequence_questionnaire()` не разпознава "тласкател" като водопроводен обект → AI може да планира КПС паралелно с Тласкател (нарушение на урок #38).
**Fix:** Добави "тласкател" в keywords за водопровод; добави explicit правило в промпта: "КПС стартира СЛЕД завършване на Тласкател (FS зависимост)."

### C4 — CI vs PE производителност: ~74% занижение
**Файл:** [src/ai_processor.py](src/ai_processor.py) промпт | **Засяга:** DN300 CI проекти
При Харманли (673м DN300 CI): AI генерира ~22д вместо реалните 84д. CI тръби са 10–12× по-бавни от PE.
**Fix:** Добави в промпта lookup table с производителности по материал: PE→55м/д, CI→6м/д.

### C5 — `test_exports.py` return вместо assert (warning → потенциален false positive)
**Файл:** [tests/test_exports.py](tests/test_exports.py) | **Ред:** ~последна функция
```
PytestReturnNotNoneWarning: test function returned <class 'bool'> instead of None
```
Тестът минава, но `return True` не е `assert True` — при промяна на pytest поведението може да спре да хваща грешки.
**Fix:** Смени `return result` с `assert result`.

### C6 — Враца Tier lookup само в lessons, не в промпта
**Файл:** [src/ai_processor.py](src/ai_processor.py)
При Враца-тип разпределителна мрежа AI прилага Плевен-формулата → ~10% грешка в продължителностите.
**Fix:** Добави Tier lookup table в системния промпт: Tier1(≤5км)→X дни, Tier2(5-15км)→Y дни, Tier3(>15км)→Z дни.

### C7 — "В/К" token: false positive в двете keyword групи
**Файл:** [src/chat_handler.py](src/chat_handler.py)
"В/К" е едновременно в `_WATER_KEYWORDS` и `_SEWER_KEYWORDS` → задейства въпросника за последователност при водопровод-only проекти → объркване на потребителя.
**Fix:** Премахни "В/К" от единия списък или добави проверка за exclusive match.

---

## СРЕДНИ — нещо не работи правилно, но има workaround

### M1 — Дублирани inline imports в chat_handler.py
**Файл:** [src/chat_handler.py](src/chat_handler.py) | **Редове:** 501, 551, 564, 1209, 1242, 1377, 1797, 1851
`json`, `re`, `logging` се импортират повторно вътре в методи като `_json`, `_re`, `_log`. Модулът вече ги импортира на ниво файл (редове 11-12). Излишни повторения → объркване при поддръжка.
**Fix:** Изтрий inline imports — вече са налични на ниво модул.

### M2 — Шаблонни вместо параметрични продължителности
**Файл:** [src/ai_processor.py](src/ai_processor.py) промпт
AI дава еднакви ~23д за всички DN90 клонове независимо от дължината. Реален диапазон: 12д (70м) до 40д (635м). Грешка ~35%.
**Fix:** Добави в промпта: "Продължителността = CEILING(дължина_м / производителност_м_ден)".

### M3 — Дезинфекция логика: per-section vs накрая
**Файл:** [src/ai_processor.py](src/ai_processor.py) промпт | Уроци #09, #10, #33
AI объърква дали дезинфекцията е per-section (разпределителна мрежа) или накрая (довеждащ). Варира 2–6 дни по DN и материал.
**Fix:** Добави lookup: DN90-110→2д, Mixed large→4д, DN300 CI forest→6д.

### M4 — ИНЖЕНЕРИНГ без ключова дума → ~140д вместо ~800д
**Файл:** [src/ai_processor.py](src/ai_processor.py) класификационен промпт
Проект "Технически проект + строителство" без думата "инженеринг" се класифицира като единичен участък. График: 140д вместо 800д.
**Fix:** Добави в промпта: trigger keywords за ИНЖЕНЕРИНГ — "технически проект", "ПУП", "проектиране", "проект и строителство".

### M5 — Verifier (Anthropic) не знае project_type
**Файл:** [src/ai_router.py](src/ai_router.py) / [src/ai_processor.py](src/ai_processor.py)
Anthropic проверява по общи правила без да знае дали е водопровод, канализация или ИНЖЕНЕРИНГ → не може да наложи методика-специфични ограничения.
**Fix:** Предай `project_type` в верификационния промпт.

### M6 — Фантомни фази добавят ~25д
**Файл:** [src/ai_processor.py](src/ai_processor.py) промпт | Урок #41
AI добавя "Административна подготовка" (+12д), "Въвеждане ВОБД" (+3д), "Демобилизация" (+10д) = +25д невалидна продължителност.
**Fix:** Добави в промпта: "НЕ добавяй фази: Административна подготовка, ВОБД, Демобилизация — те не са в строителния график."

### M7 — Настилки без отделна бригада
**Файл:** [src/ai_processor.py](src/ai_processor.py) промпт | Урок #36
AI планира настилки без ~30д lag след основното изкопаване и без отделен екип. Физически невъзможно.
**Fix:** Добави правило: "Настилки = ОТДЕЛНА бригада, стартира с lag ≥30д след Изкопни работи (SS+30д)."

### M8 — Безизкопно полагане: 150 вместо 56 м/ден
**Файл:** [src/ai_processor.py](src/ai_processor.py) промпт | Урок #32
AI използва ~150м/ден за HDD. Реалното е 56м/ден (DN90-110). Грешка ~63%.
**Fix:** Добави в производителностите: "Безизкопно/HDD DN90-110 = 56 м/ден, ОТДЕЛЕН Drill Team."

### M9 — Липсва timeout за AI повиквания
**Файл:** [src/ai_router.py](src/ai_router.py)
`_chat_deepseek()` и `_chat_anthropic()` нямат явен timeout. При бавен API → Streamlit замръзва без индикация.
**Fix:** Добави `timeout=120` в OpenAI client init и `timeout=httpx.Timeout(120)` за Anthropic.

---

## ЛЕКИ — code quality, cleanup

### L1 — pytest warning: `return` вместо `assert`
**Файл:** [tests/test_exports.py](tests/test_exports.py) — вижте C5 по-горе.

### L2 — `_find_uid_by_id` е dead code
**Файл:** [src/export_xml.py](src/export_xml.py) | **Ред:** 435
Функцията е дефинирана но никога не се вика — `uid_map.get()` се ползва директно навсякъде.
**Fix:** Изтрий функцията.

### L3 — Административна подготовка hardcoded в 12д
**Файл:** [src/ai_processor.py](src/ai_processor.py) | Урок #37
Ако е нужно, 12д трябва да идват от `config/productivities.json`, не от промпта.

### L4 — `import io as _io` вътре в функция
**Файл:** [src/export_pdf.py](src/export_pdf.py) | **Ред:** 182
`io` е вече импортиран на ниво модул (ред 13). Inline re-import е излишен.

### L5 — Настройките за permissions в `.claude/settings.json` не покриват piped commands
**Файл:** [.claude/settings.json](.claude/settings.json)
`"Bash(grep *)"` не съвпада с `grep ... | grep ...`. Piped команди изискват одобрение въпреки allow правилата.
**Fix:** Добави `"Bash(*)"` или по-специфични pipe patterns.

### L6 — `QA-LINKS.md`, `QA-UI.md`, `QA-ROUTES.md`, `QA-DEPS.md` не са генерирани
Четирите QA агента ударили rate limit. Само тази синтеза е налична.

### L7 — Self-evolution разчита на `_get_anthropic()` директно
**Файл:** [src/self_evolution.py](src/self_evolution.py) | **Редове:** 180, 247
Вика private метод `router._get_anthropic()` — нарушение на encapsulation. При рефакторинг на router → self_evolution се чупи без грешка при import.

---

## Потвърдено OK

| Компонент | Статус |
|-----------|--------|
| Синтаксис (py_compile) | PASS — всички файлове |
| Unit тест (test_exports.py) | PASS |
| session_state["pdf_ready"] | OK — guarded с `.get()` |
| session_state["xml_ready"] | OK — guarded с `.get()` |
| API fallback DeepSeek→Anthropic | OK — двупосочен |
| XML export (OutlineNumber) | FIXED тази сесия |
| XML export (_flatten_schedule) | FIXED тази сесия — рекурсивна |
| XML export (dependency_type/lag) | FIXED тази сесия |
| Без `print()` в src/ | OK — чисто |
| Без `TODO/FIXME/HACK` | OK |
| Без bare `except: pass` | OK |

---

## Приоритетен план за действие

1. **Веднага** (C1, C2): Fix `project_type` pipeline + добави `out_of_scope` в класификатора
2. **Тази седмица** (C3, C4, C6): КПС зависимост + CI производителност + Враца Tier lookup
3. **Следваща** (M1-M9): Prompt refinements за фантомни фази, настилки, безизкопно, timeout
4. **Cleanup** (L1-L7): assert вместо return, dead code, inline imports
