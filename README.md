# ВиК График Генератор

Локално Streamlit приложение за **автоматично генериране на строителни линейни графици** (Gantt) за ВиК инфраструктурни проекти. Използва двоен AI — DeepSeek V3 за анализ и генериране, Anthropic Claude за проверка и контрол на качеството.

**Версия:** 0.9.0

## Основни функции

- **Анализ на тендерна документация** — автоматично конвертира PDF, Excel, Word и CSV файлове в JSON
- **Генериране на строителен график** — AI анализира документите и създава пълен линеен график
- **Двоен AI** — DeepSeek V3 (работник) + Anthropic Sonnet 4.6 (контрольор) с автоматични корекции до 3 цикъла
- **Интерактивен Gantt** — 9 слоя визуализация (критичен път, зависимости, екипи, milestones и др.)
- **Експорт** — PDF (A3 landscape), MSPDI XML (за MS Project), JSON
- **Самоеволюиращо се приложение** — AI може да модифицира собствения си код с 3 нива на защита
- **85+ научени урока** от 8 реални проекта, вградени в базата знания

<!-- TODO: добави скрийншот -->

## Бърз старт

### Инсталация

Двоен клик на **`install.bat`** — инсталира Python, създава виртуална среда и настройва приложението автоматично.

> При първо стартиране ще ви трябват API ключове (обърнете се към администратора).

### Стартиране

Двоен клик на **`start.bat`** или иконата **"ВиК Графици"** на десктопа.

Приложението се отваря в браузъра на адрес `http://localhost:8501`.

### Работа с приложението

1. В страничната лента изберете папка с тендерна документация
2. Приложението автоматично конвертира файловете
3. Опишете в чата какъв график ви трябва
4. AI анализира документите и генерира график
5. Прегледайте Gantt диаграмата и свалете PDF/XML

## Системни изисквания

- **Windows 10 или 11**
- **Интернет връзка** (за AI API-тата на DeepSeek и Anthropic)
- **Python 3.12+** (инсталира се автоматично от `install.bat`)

## Архитектура

Приложението се състои от Streamlit UI, двоен AI маршрутизатор, система за конвертиране на файлове, интерактивен Gantt и модули за експорт.

Подробна техническа документация: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Структура на проекта

<!-- FILE_TREE_START -->
```
schedule-generator/
├── app.py                    # Главно Streamlit приложение
├── requirements.txt          # Python зависимости
├── install.bat               # Инсталатор (Python + venv + пакети)
├── start.bat                 # Стартиране на приложението
├── update.bat                # Обновяване (git pull + pip upgrade)
├── .env.example              # Шаблон за API ключове
├── .env.company.example      # Шаблон за фирмени ключове
├── .streamlit/
│   └── config.toml           # Streamlit тема и настройки
├── config/
│   ├── app_config.json       # Конфигурация на приложението
│   └── productivities.json   # Производителности по DN (v0.4)
├── src/
│   ├── ai_processor.py       # Оркестрация на AI pipeline
│   ├── ai_router.py          # Двоен AI маршрутизатор
│   ├── chat_handler.py       # Обработка на чат съобщения
│   ├── docs_updater.py       # Автоматично обновяване на документация
│   ├── export_pdf.py         # PDF експорт (A3 Gantt)
│   ├── export_xml.py         # MSPDI XML експорт (MS Project)
│   ├── file_manager.py       # Конвертиране на файлове
│   ├── gantt_chart.py        # Интерактивен Plotly Gantt
│   ├── knowledge_manager.py  # 3-нивова база знания
│   ├── project_manager.py    # Управление на проекти
│   ├── schedule_builder.py   # Изграждане на график от AI отговор
│   └── self_evolution.py     # Самоеволюция (3 нива)
├── knowledge/
│   ├── evolution_log.json    # Лог на промените от самоеволюция
│   ├── lessons/              # Научени уроци
│   ├── methodologies/        # Методики по тип проект
│   └── skills/               # Техническо ядро (производителности)
├── fonts/
│   ├── DejaVuSans.ttf        # Шрифт за кирилица в PDF
│   └── DejaVuSans-Bold.ttf
├── tests/
│   ├── test_exports.py       # Unit тестове за PDF и XML експорт
│   └── e2e/                  # Playwright E2E тестове (10 теста)
│       ├── conftest.py       # Streamlit server fixture (реални API ключове)
│       ├── test_gantt.py     # Gantt chart: render, слоеве, филтри (3 теста)
│       ├── test_chat_interaction.py  # Чат: AI отговор, input clear (2 теста)
│       ├── test_export_functional.py # Експорт: PDF, XML download (2 теста)
│       └── test_sidebar_structure.py # Sidebar: секции, AI providers, бутони (3 теста)
├── tools/
│   └── README.md             # Инструкции за python-installer.exe
├── hooks/
│   └── pre-commit            # Pre-commit hook (стартира тестовете)
├── install-hooks.bat         # Инсталатор на pre-commit hook
├── pytest.ini                # Pytest конфигурация
└── docs/
    ├── ARCHITECTURE.md        # Техническа архитектура
    ├── UI_WIREFRAME.md        # UI Wireframe + елемент регистър
    ├── CHANGES_2026-02-18-19.md # Дневник на промените (18-19 фев)
    └── AUTO_UPDATE_DOCS.md    # Документация за auto-update системата
```
<!-- FILE_TREE_END -->

## Конфигурация

Приложението се конфигурира чрез `.env` файл в главната папка:

```env
ANTHROPIC_API_KEY=sk-ant-...     # Anthropic Claude (контрольор)
DEEPSEEK_API_KEY=sk-...          # DeepSeek V3 (работник)
ADMIN_CODE=...                   # Код за RED-level самоеволюция
```

> При инсталация чрез `install.bat`, ключовете се копират от `.env.company` (подготвен от администратора).

## Зависимости

<!-- DEPS_START -->
| Пакет | Версия | Предназначение |
|-------|--------|----------------|
| streamlit | >=1.30.0 | Уеб интерфейс |
| anthropic | >=0.40.0 | Anthropic Claude API (контрольор) |
| openai | >=1.12.0 | DeepSeek API (OpenAI-съвместим) |
| plotly | >=5.18.0 | Интерактивен Gantt chart |
| pandas | >=2.0.0 | Таблици и данни |
| reportlab | >=4.0.0 | PDF генериране (A3 Gantt) |
| python-dotenv | >=1.0.0 | Зареждане на .env конфигурация |
| PyPDF2 | >=3.0.0 | Четене на PDF файлове (legacy) |
| openpyxl | >=3.1.0 | Четене на Excel файлове |
| watchdog | >=3.0.0 | Наблюдение на файлови промени |
| PyMuPDF | >=1.23.0 | Основно PDF извличане (fitz) + OCR |
| python-docx | >=1.1.0 | Четене на Word документи |
| pytest | >=9.0.0 | Тестова рамка |
| playwright | >=1.40.0 | E2E browser тестове |
| pytest-playwright | >=0.5.0 | Pytest + Playwright интеграция |
<!-- DEPS_END -->

## За разработчици

### Ръчна инсталация

```bash
# Клониране на репото
git clone <repo-url>
cd schedule-generator

# Виртуална среда
python -m venv venv
venv\Scripts\activate       # Windows
source venv/bin/activate    # Linux/Mac

# Инсталация на пакети
pip install -r requirements.txt

# Копиране на конфигурацията
copy .env.example .env
# Редактирайте .env и добавете API ключовете
```

### Стартиране в dev mode

```bash
streamlit run app.py
```

Приложението се отваря на `http://localhost:8501` с hot-reload при промяна на файлове.

### Тестове

```bash
# Unit тестове (PDF + XML export)
python -m pytest tests/test_exports.py

# E2E тестове (10 теста — изискват реален .env с API ключове)
python -m pytest tests/e2e/ -v

# Всички тестове
python -m pytest tests/ -v
```

> **Pre-commit hook**: При `git commit` автоматично се пускат 11 теста (1 unit + 10 E2E).
> Инсталация: `install-hooks.bat`

## Версия и промени

Текуща версия: **0.9.0**

Пълен списък на промените: [CHANGELOG.md](CHANGELOG.md)

## Лиценз

**Proprietary** — РАИ Комерс. Частно репозитори, всички права запазени.
