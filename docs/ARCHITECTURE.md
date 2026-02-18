# Архитектура на ВиК График Генератор

Техническа документация за разработчици.

## Обзор

```
┌─────────────────────────────────────────────────┐
│                  ПОТРЕБИТЕЛ                       │
│           (браузър, http://localhost:8501)        │
└───────────────────────┬─────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────┐
│                   app.py                         │
│            Streamlit UI Framework                │
│     ┌──────────┐  ┌───────────────────┐         │
│     │ Чат 45%  │  │ Gantt 55%         │         │
│     │          │  │ (gantt_chart.py)   │         │
│     └────┬─────┘  └──────────┬────────┘         │
└──────────┼───────────────────┼──────────────────┘
           │                   │
┌──────────▼───────────────────▼──────────────────┐
│              chat_handler.py                     │
│         Intent Detection + Routing               │
└──────────┬──────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────┐
│              ai_processor.py                     │
│    System Prompts + Document Analysis            │
└──────────┬──────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────┐
│               ai_router.py                       │
│    ┌───────────────┐  ┌──────────────────┐      │
│    │ DeepSeek V3   │  │ Anthropic 4.6    │      │
│    │ (Работник)    │  │ (Контрольор)     │      │
│    │ - Чат         │  │ - Проверка       │      │
│    │ - OCR         │  │ - Правила        │      │
│    │ - Анализ      │  │ - Уроци          │      │
│    │ - Генериране  │  │ - Self-evolution  │      │
│    └───────┬───────┘  └────────┬─────────┘      │
│            │ Fallback ←────────┘                 │
└─────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────┐
│           Поддържащи модули                      │
│  ┌────────────┐ ┌─────────────┐ ┌────────────┐  │
│  │file_manager│ │knowledge_mgr│ │project_mgr │  │
│  │ PDF→JSON   │ │ 3 нива      │ │ Recent 5   │  │
│  │ XLSX→JSON  │ │ Кеширане    │ │ Прогрес    │  │
│  └────────────┘ └─────────────┘ └────────────┘  │
│  ┌────────────┐ ┌─────────────┐ ┌────────────┐  │
│  │export_pdf  │ │export_xml   │ │self_evolve │  │
│  │ A3 Gantt   │ │ MSPDI       │ │ 3 нива     │  │
│  └────────────┘ └─────────────┘ └────────────┘  │
└─────────────────────────────────────────────────┘
```

## Компоненти

### app.py — Главно приложение (1302 реда)

Streamlit UI фреймуърк. Управлява layout (45% чат / 55% визуализация), sidebar навигация, session state (16 ключа) и инициализация на всички мениджъри. Проверява конфигурацията при стартиране.

### src/ai_router.py — Двоен AI маршрутизатор (938 реда)

Ядрото на AI системата. Маршрутизира заявки между DeepSeek V3 (работник) и Anthropic Claude Sonnet 4.6 (контрольор). Поддържа двупосочен fallback и следи разходите в реално време.

### src/ai_processor.py — AI оркестратор (409 реда)

Изгражда system prompts (3 нива: minimal, full, verification). Управлява pipeline-а: анализ на документи → генериране → проверка → корекции (до 3 цикъла). Прилага Правило #0 (само конвертирани .json файлове).

### src/chat_handler.py — Обработка на чат (1095 реда)

Детектира намерение на потребителя чрез scoring на ключови думи (7 intent типа). Маршрутизира към съответния handler: зареждане на проект, генериране, модификация, експорт, запис на урок, еволюция, общ разговор.

### src/file_manager.py — Конвертиране на файлове (873 реда)

Имплементира Правило #0 (конвертирай ВСИЧКО преди анализ). Конвертори: PDF (текст + OCR fallback), Excel (merged cells), DOCX, CSV. Записва JSON в `converted/` с manifest кеш (`_manifest.json`).

### src/gantt_chart.py — Интерактивен Gantt chart

Plotly `go.Figure` с 9 типа задачи (български надписи и цветове), 8 превключваеми слоя, click-to-select и филтри по екип/фаза/тип. Генерира демо график при празен state.

### src/knowledge_manager.py — База знания (551 реда)

3-нивова система: уроци (конкретни случаи), методики (обобщени правила), skills (производителности и формули). Кеширане с timestamp проверка. 3 нива на prompt: minimal, full, verification.

### src/project_manager.py — Управление на проекти (427 реда)

Персистентна история в `config/projects_history.json`. MD5 хеш на пътя = project ID. Последни 5 проекта с бързо възобновяване. Статус-aware welcome съобщения на български.

### src/schedule_builder.py — Изграждане на график (120 реда)

Изгражда структуриран график от AI отговора. Валидация (errors/warnings). Конвертиране към pandas DataFrame за табличен изглед.

### src/export_pdf.py — PDF експорт

A3 landscape PDF чрез ReportLab. Таблица + Gantt bars + критичен път + легенда. DejaVu Sans шрифт за кирилица. Multi-page поддръжка.

### src/export_xml.py — MSPDI XML експорт

MSPDI XML формат за MS Project. DurationFormat=5, Manual scheduling, Custom Fields за DN/екип/фаза. 7-дневен календар (без почивни дни).

### src/self_evolution.py — Самоеволюция (748 реда)

AI-управлявана модификация на приложението. 3 нива на промени с различна защита. Git backup преди RED промени. Автоматичен rollback при грешка.

### src/docs_updater.py — Автоматична документация

Следи промени в кода и обновява README.md, CHANGELOG.md и ARCHITECTURE.md. Работи с git diff и HTML маркери в документите.

## Потоци на данни

### 1. Зареждане на проект

```
Потребител избира папка
    │
    ▼
ProjectManager.register_project()
    │ MD5 hash → project ID
    │ Записва в projects_history.json
    ▼
FileManager.scan_project()
    │ Сканира PDF, XLSX, DOCX, CSV файлове
    ▼
FileManager.convert_all()
    │ PDF → PyPDF2 текст (OCR fallback чрез PyMuPDF + DeepSeek vision)
    │ XLSX → openpyxl → JSON (merged cells handling)
    │ DOCX → python-docx → JSON
    │ CSV → auto-detect encoding → JSON
    ▼
converted/_manifest.json
    │ Кеш: original size + mtime → пропуска непроменени файлове
    ▼
Готово за AI анализ
```

### 2. Генериране на график

```
Потребител: "Генерирай график"
    │
    ▼
ChatHandler → intent: "generate_schedule"
    │
    ▼
AIProcessor.generate_schedule()
    │
    ├─ build_full_prompt() ← KnowledgeManager (уроци + методики + skills)
    │
    ├─ AIRouter.chat() → DeepSeek V3
    │  │ Анализира конвертираните JSON документи
    │  │ Генерира JSON график
    │  ▼
    │  ScheduleBuilder.build_from_ai_response()
    │
    ├─ AIRouter.verify_schedule() → Anthropic Claude
    │  │ Проверява правила, уроци, количества
    │  │ Връща: approved / needs_corrections
    │  ▼
    │  Ако needs_corrections:
    │     AIRouter.apply_corrections() → DeepSeek V3
    │     (повтаря до 3 пъти)
    │
    ▼
Одобрен график → session_state → Gantt визуализация
```

### 3. Експорт

```
Schedule JSON (от session_state)
    │
    ├─► export_to_pdf()
    │   ReportLab → A3 landscape bytes
    │   Таблица + Gantt bars + критичен път
    │
    ├─► export_to_mspdi_xml()
    │   xml.etree → MSPDI XML bytes
    │   MS Project → File → Open → Save As .mpp
    │
    └─► json.dumps()
        JSON bytes за директно сваляне
```

## Двоен AI — подробности

### Модели и цени

| Модел | Роля | API | Цена ($/M tokens) | Скорост |
|-------|------|-----|--------------------|---------|
| DeepSeek V3 | Работник | OpenAI SDK (`api.deepseek.com`) | $0.14 / $0.28 (in/out) | Бърз |
| Claude Sonnet 4.6 | Контрольор | Anthropic SDK | $3.00 / $15.00 (in/out) | По-бавен |

**DeepSeek е ~100x по-евтин** от Anthropic. Затова DeepSeek обработва всичко "тежко" (чат, генериране, OCR, корекции), а Anthropic се използва само за проверка и критични решения.

### Fallback логика

```
Нормален режим:
  DeepSeek → чат, генериране, OCR, корекции
  Anthropic → проверка, уроци, самоеволюция

Ако DeepSeek е недостъпен:
  Anthropic поема ВСИЧКО (по-скъпо, но работи)

Ако Anthropic е недостъпен:
  DeepSeek поема ВСИЧКО (без проверка, предупреждение)

Ако И ДВАТА са недостъпни:
  Offline mode — само преглед на данни, без AI
```

### Knowledge нива

| Ниво | Кога | Размер | Съдържание |
|------|------|--------|------------|
| Minimal | OCR, прост чат | ~1500 tokens | Основни правила, текущ проект |
| Full | Генериране, анализ | ~5000-8000 tokens | Уроци + методики + productivities |
| Verification | Проверка на график | ~3000 tokens | Стриктни правила, чеклист, уроци |

## Система за знания (3 нива)

```
knowledge/
├── skills/                  ← Ниво 1: Техническо ядро
│   ├── SKILL.md             │  Производителности, формули, правила
│   └── references/          │  Експортни формати, чеклисти
│       ├── productivities.md
│       ├── export-formats.md
│       ├── project-types.md
│       └── workflow-rules.md
│
├── methodologies/           ← Ниво 2: Обобщени правила
│   ├── distribution_network.md  Разпределителна мрежа
│   ├── supply_pipeline.md       Довеждащ водопровод
│   ├── single_section.md        Единичен участък
│   └── engineering_projects.md  Инженеринг проекти
│
└── lessons/                 ← Ниво 3: Конкретни случаи
    ├── lessons_learned.md   85+ урока от 8 проекта
    └── pending_lessons.md   Чакащи за одобрение
```

**Поток:** Нов урок → DeepSeek предлага → Anthropic проверява → одобрен/отхвърлен → `lessons_learned.md`.

## Самоеволюция

### Процес

```
Потребител: "Добави поддръжка за DWG файлове"
    │
    ▼
SelfEvolution.analyze_request()
    │ Anthropic Claude анализира заявката
    │ Определя ниво: GREEN / YELLOW / RED
    ▼
SelfEvolution.generate_changes()
    │ Claude генерира конкретни промени
    │ Чете текущите файлове за контекст
    ▼
Потвърждение от потребител
    │ GREEN: автоматично
    │ YELLOW: "Да" бутон
    │ RED: админ код + "Да"
    ▼
SelfEvolution.create_backup()   ← само за RED
    │ git add -A && git commit
    ▼
SelfEvolution.apply_changes()
    │ Файлови операции: create / modify / delete
    │ pip install за нови пакети
    ▼
SelfEvolution.test_changes()
    │ Syntax check на всички .py
    │ Import тест
    │ JSON валидация на конфиги
    ▼
DocsUpdater.run_all_updates()   ← автоматично обновяване на документацията
    │
    ▼
evolution_log.json ← запис на промяната
```

### Нива на защита

| Ниво | Цвят | Файлове | Админ код | Потвърждение | Git backup |
|------|------|---------|-----------|--------------|------------|
| GREEN | 🟢 | `.md` в `knowledge/` | Не | Не (auto) | Не |
| YELLOW | 🟡 | `.json` в `config/` | Не | Да | Не |
| RED | 🔴 | `.py`, `requirements.txt` | Да | Да | Да |

## Файлови конвенции

- **Python код:** Английски (имена на променливи, функции, коментари)
- **UI текстове:** Български (всичко видимо от потребителя)
- **Commits:** Conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`)
- **Branching:** `main` (единствен клон засега)
- **Encoding:** UTF-8 навсякъде
- **Line endings:** LF (Unix style)

## Зависимости

| Пакет | Минимална версия | Предназначение |
|-------|-----------------|----------------|
| streamlit | 1.30.0 | Уеб фреймуърк — интерфейс, session state, sidebar |
| anthropic | 0.40.0 | Anthropic Claude API — контрольор, проверка, еволюция |
| openai | 1.12.0 | DeepSeek API (OpenAI-съвместим) — работник, генериране |
| plotly | 5.18.0 | Интерактивни графики — Gantt chart с 9 слоя |
| pandas | 2.0.0 | Таблични данни — DataFrame за табличен изглед |
| reportlab | 4.0.0 | PDF генериране — A3 landscape Gantt диаграми |
| python-dotenv | 1.0.0 | Зареждане на `.env` файлове с API ключове |
| PyPDF2 | 3.0.0 | Четене на PDF — текстови слой |
| openpyxl | 3.1.0 | Четене на Excel — merged cells, формули |
| watchdog | 3.0.0 | Наблюдение на файлови промени (Streamlit) |
| PyMuPDF (fitz) | 1.23.0 | Рендериране на PDF страници за OCR (200 DPI → base64) |
| python-docx | 1.1.0 | Четене на Word документи |
