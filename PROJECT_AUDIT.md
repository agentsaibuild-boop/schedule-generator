# Одит на проекта — Schedule Generator

**Дата на анализ:** 2026-03-10
**Версия:** 0.9.0
**Анализирано от:** Claude Code (Sonnet 4.6)
**Цел:** Обобщение на наследени грешки, логически проблеми и дълг от предния разработчик + текущото състояние

---

## 1. ХРОНОЛОГИЯ И КОНТЕКСТ

Проектът е разработен за ~1 седмица (18–25 февруари 2026) в 17 commit-а.
Темпото е изключително бързо — средно по 2–3 major feature-а на ден.
Това директно обяснява значителния технически дълг.

```
2026-02-18  Initial commit (Streamlit + чат + file conversion + knowledge)
2026-02-18  +5 commit-а в ЕДИН ден (Gantt, self-evolution, PDF, XML, installer, docs)
2026-02-19  Playwright E2E тестове
2026-02-20  Fixes (intent detection, NameError)
2026-02-25  Fix: corrupt schedule
2026-03-10  (сега) Anti-hallucination система — в прогрес
```

---

## 2. ПРАЗНИ / МЪРТВИ ФАЙЛОВЕ

### 2.1 knowledge/lessons/lessons_learned.md ⚠️ КРИТИЧНО
```
Текущо съдържание: "(Ще бъдат мигрирани от training/lessons_learned.md)"
```
- Файлът е **напълно празен** откъм реални уроци
- Споменава `training/lessons_learned.md` — тази директория **не съществува** в repo-то
- AI системата зарежда този файл в промптовете → дава празен контекст
- **Всеки генериран график работи без натрупани знания от минали проекти**

### 2.2 knowledge/lessons/pending_lessons.md
```
Текущо съдържание: "(Празно)"
```
- Механизмът за AI самообучение (pending → review → approved) е имплементиран в код
- Но никога не е бил попълван с реален урок
- `self_evolution.py` пише уроци тук — неясно дали реално се е тригерирал

### 2.3 knowledge/methodologies/*.md — 4 файла завършват с:
```
"(Ще бъде допълнена с правила от lessons_learned.md и skills)"
```
Засегнати файлове:
- `distribution_network.md` — основният тип проект (Плевен)
- `engineering_projects.md` — ИНЖЕНЕРИНГ
- `single_section.md` — единичен участък
- `supply_pipeline.md` — довеждащ водопровод

Всички имат само базова структура, без реални правила за генериране.

---

## 3. ЛОГИЧЕСКИ ГРЕШКИ И ПРОБЛЕМИ В АРХИТЕКТУРАТА

### 3.1 AI анализира само имена на файлове, не съдържание ⚠️ КРИТИЧНО (поправено в тази сесия)
**Файл:** `src/ai_processor.py` (преди промените от тази сесия)

```python
# СТАРА логика — праща само имена на файлове:
files_text = "\n".join([f"- {f['name']}" for f in converted_files])
# AI генерира графика ПО ИМЕНА, не по реалното съдържание на КСС
```

AI анализираше **имената на файловете**, а не тяхното съдържание.
Т.е. ако файлът се казва "КСС_Обект_А.xlsx", AI генерираше от това,
без да е прочел реалните количества и дейности вътре.

**Поправено в тази сесия** — `analyze_documents()` вече праща `all_text`.

### 3.2 Няма проверка за КСС преди конвертиране ⚠️ КРИТИЧНО (поправено в тази сесия)
Можеше да се пусне генериране с произволни файлове — без КСС.
AI щеше да генерира измислен график.
**Поправено** — `classify_files()` блокира процеса без КСС файл.

### 3.3 AI измисляше имена на улици (халюцинации) ⚠️ КРИТИЧНО (поправено в тази сесия)
Генерираните задачи съдържаха имена на улици/квартали, невидими в документите.
Нямаше механизъм за проверка или предупреждение.
**Поправено** — `_validate_task_locations()` + `extract_situation_locations()`.

### 3.4 23 фалшиви E2E теста (поправено преди тази сесия)
```
commit 4d816a2: "replace 23 fake E2E tests with 10 real functional tests"
```
Предният разработчик е имплементирал 23 теста, които са **минавали без реален browser**.
Тестовете не тестваха нищо — просто проверяваха дали Python модулите се импортират.
Поправено с реални Playwright тестове.

### 3.5 Corrupt schedule override на demo data (поправено преди тази сесия)
```
commit f08b3ae: "prevent corrupt schedule from overriding demo data"
```
При невалиден JSON от AI, приложението замествало demo данните с null/празно,
оставяйки потребителя с празен Gantt без обяснение.

### 3.6 NameError: schedule_json (поправено преди тази сесия)
```
commit 09d9b3c: "fix: schedule_json NameError"
```
Променлива използвана преди дефиниция — основна Python грешка.

### 3.7 `classify_files()` праща празен system_prompt на AI
**Файл:** `src/file_manager.py`, ред ~360
```python
result = ai_processor.router.chat(messages, system_prompt="")
```
`ai_router.py` логва warning при празен prompt (ред ~182).
AI класификацията на файлове работи без контекст → непредсказуеми резултати.

### 3.8 Case-sensitive проверка в _SKIP_WORDS (поправено в тази сесия)
**Файл:** `src/ai_processor.py`
```python
# ГРЕШКА: regex връща "Монтаж", но set съдържа "монтаж"
if token in _SKIP_WORDS:  # → никога не match-ва
```
Причиняваше фалшиви hallucination warnings за стандартни думи.
**Поправено** — `token.lower() in _SKIP_WORDS`.

---

## 4. ДУБЛИРАН КОД

### 4.1 COLOR_MAP — дефиниран на 2 места
- `src/gantt_chart.py` (ред ~14)
- `src/export_pdf.py` (ред ~73)

При промяна на цвят трябва да се обнови на 2 места. Риск от разминаване.

### 4.2 TYPE_LABELS — дефиниран на 2 места
- `src/gantt_chart.py` (ред ~29)
- `src/export_pdf.py` (ред ~86)

Същият проблем.

---

## 5. MAGIC NUMBERS (хардкоднати стойности без константи)

| Файл | Стойност | Смисъл | Брой повторения |
|------|----------|--------|-----------------|
| ai_router.py | `4096` | max_tokens (worker) | 4× |
| ai_router.py | `8192` | max_tokens (controller) | 2× |
| ai_router.py | `1024` | max_tokens (chat) | 2× |
| ai_router.py | `100` | min system_prompt length | 1× |
| ai_processor.py | `120_000` | text truncation chars | 1× |
| file_manager.py | `8000` | text preview chars | 1× |
| file_manager.py | `4096` | CSV sample size | 1× |
| export_xml.py | `"480"` | минути/ден | 3× |

---

## 6. EXCEPTION HANDLING — ТИХО ПОГЛЪЩАНЕ НА ГРЕШКИ

5 места в кода имат `except ... : pass` без logging:

| Файл | Ред | Exception | Риск |
|------|-----|-----------|------|
| ai_processor.py | ~275 | JSONDecodeError, AttributeError | Невидим parse fail |
| ai_router.py | ~1021 | JSONDecodeError, OSError | Невидим cache fail |
| ai_router.py | ~1034 | OSError | Невидим file fail |
| ai_router.py | ~1059 | JSONDecodeError | Невидим parse fail |
| ai_router.py | ~1068 | JSONDecodeError | Невидим parse fail |

---

## 7. LEGACY КОД (технически дълг)

### 7.1 Параметри запазени "за съвместимост"
```python
# ai_processor.py __init__:
def __init__(self, api_key: str = "", skills_path: str = "", ...):
    # Legacy param (kept for backward compat during transition)
    self._legacy_api_key = api_key
    # skills_path не се използва изобщо
```

### 7.2 Legacy методи
```python
# ai_processor.py:
def ask_clarification(self, context, question) -> str:
    return f"Уточняващ въпрос: {question}"  # ← не прави нищо реално

def process_documents(self, ...):
    return self.analyze_documents(...)  # ← просто делегира

def generate_schedule_legacy(self, ...):
    return self.generate_schedule(...)  # ← просто делегира
```

---

## 8. ТЕСТОВО ПОКРИТИЕ — ПРОПУСКИ

| Сценарий | Покрит? |
|----------|---------|
| Генериране с реален КСС | ❌ само E2E smoke |
| Типове проекти (разпределителна, инженеринг...) | ❌ |
| XML валидност за MS Project | ✅ unit тест |
| Hallucination detection | ❌ само ръчно тестване |
| classify_files() edge cases | ❌ |
| Грешка от AI (timeout, bad JSON) | ❌ |
| PDF генериране с Кирилица | ❌ |

---

## 9. SELF-EVOLUTION СИСТЕМА — СТАТУС НЕЯСЕН

`src/self_evolution.py` е имплементирана система за AI самоподобряване:
- AI може да предлага промени в собствения код
- 3 нива: green (автоматично) / yellow (с одобрение) / red (само четене)
- Има rollback механизъм

**Проблеми:**
- Никога не е тествана с реален сценарий
- `pending_lessons.md` е празен → AI не записва уроци
- Неясно дали системата реално се тригерира при грешки

---

## 10. ОБОБЩЕНА ОЦЕНКА

### Какво работи добре ✅
- Основният flow: файлове → конвертиране → AI анализ → Gantt
- XML експорт за MS Project (верифициран формат)
- PDF A3 Gantt с Кирилица
- Plotly визуализация с 9 слоя
- E2E тестове с Playwright (след поправката)
- Двоен AI (DeepSeek worker + Anthropic controller)
- Windows installer

### Какво е проблематично ⚠️
- Knowledge базата е **почти празна** — AI работи без натрупани знания
- Анти-халюцинационната система е **нова и нетествана** с реален проект
- Дублиран код в constants (COLOR_MAP, TYPE_LABELS)
- Тихо поглъщане на грешки в 5 места
- Липсва тестване по типове проекти

### Приоритети за следващата итерация
1. **ВИСОК:** Попълни `lessons_learned.md` с реални уроци от минали проекти
2. **ВИСОК:** Тествай anti-hallucination системата с реален проект
3. **СРЕДЕН:** Добави system_prompt в `classify_files()` AI повикването
4. **СРЕДЕН:** Извади COLOR_MAP и TYPE_LABELS в `src/constants.py`
5. **НИСЪК:** Замени bare `pass` с `logger.debug()` в except блоковете
6. **НИСЪК:** Изчисти legacy параметри и методи

---

*Файлът е генериран автоматично. Актуализирай при нови находки.*
