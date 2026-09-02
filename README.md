# ВиК График Генератор

Локално Streamlit приложение, което изготвя **строителен линеен график (Gantt)** за ВиК инфраструктурни тръжни процедури в България — готов за подаване с офертата.

Входът са количествата (какво, колко, в каква мярка) и обявените срокове. Изходът е график, който спазва тавана на възложителя, сумира КСС, носи WBS и критичен път и се експортира към MS Project и PDF A3.

Езиковият модел чете документи и води чата. **Самият график го прави кодът** — пакети, вериги, зависимости, дати и CPM не се питат от LLM.

**Версия:** 0.9.0  
**Собственик:** РАИ Комерс (proprietary)

## За кого е

Изпълнител на ВиК СМР, който подготвя оферта. Графикът трябва да е **отговарящ**: ден над обявения максимален срок прави офертата неотговаряща; ден под него поема риск без насрещна полза. Приложението държи срока като таван, не като пожелание.

## Какво произвежда

От количества и (по желание) тендерна документация системата изгражда:

- **пакети за изпълнение** — КСС се разчленява от кода, не от езиковия модел
- **технологични вериги, WBS, зависимости и CPM** — също от кода
- **продължителности** — от верифицирани норми (`config/productivities.json`), после калибрирани към обявеното темпо
- **ресурсно изравняване** — по измерен парк (`config/resource_capacity.json`) и обявените екипи
- **Gantt** с 9 слоя и експорт **PDF A3**, **MSPDI XML** (за MS Project) и JSON

КСС не е задължителен вход: нужни са количества. КСС дава проследимост до реда в таблицата — одиторска функция, не графична.

## Как работи

1. Избирате папка с документи (PDF, Excel, Word, CSV) или въвеждате количества на ръка.
2. Приложението конвертира файловете. Ситуационните чертежи се четат веднъж: възлите (РШ, ОТ, СКО) идват от чертежа, не от КСС.
3. Три въпроса към изпълнителя: кое е първо — вода или канал; как се полага водопроводът (изкоп или сондаж); колко екипа.
4. Кодът строи графика: пакети → покритие на КСС → вериги → WBS → дати → изравняване → непрекъснати дейности (надзор, лаборатория, пътни работи извън траншеята) → CPM.
5. Обявеният срок за проектиране / СМР / приемане се налага като таван. График над тавана не излиза мълчаливо.
6. Преглеждате Gantt и сваляте PDF или XML за MS Project.

Без геометричен източник (DWG/DXF, GIS, таблица с участъци) системата не твърди възли — казва, че не може да ги определи, и работи с етапи на изпълнение.

## Правила, които графикът спазва

- **Обявеният максимален срок е максимумът и на графика.** Покрива мобилизация + строителство + приемане, освен ако фазата има свой обявен срок. Надзорът се котви за строителството.
- **Каналът тръгва от заустването** и върви от едрите тръби към дребните — като приоритет в реда на пакетите, не като твърда зависимост, която сериализира обекта.
- **Възстановяването на настилките е процес, не бариера:** n-тата настилка чака n-тата вълна подземна работа.
- Работата **извън траншеята** (пътна, надзор, лаборатория, доставки) е един непрекъснат ред, не задача на участък.
- Каквото кодът разпредели вместо модела, го казва: бележка „РАЗПРЕДЕЛЕНО ОТ КОДА“.

## Основни функции

- Четене на тендерна документация и ситуационни чертежи (PDF, Excel, Word, CSV)
- Детерминистично изграждане на график (пакети, вериги, WBS, CPM, ресурс)
- Въпросник към изпълнителя (ред на мрежите, метод на полагане, екипи)
- Интерактивен Gantt — 9 слоя (критичен път, зависимости, екипи, milestones)
- Експорт PDF A3, MSPDI XML за MS Project, JSON
- Проследимост на количествата до документ, лист и ред
- Маркиране „генерирано от AI“ (EU AI Act чл. 50) там, където моделът е участвал
- База знания с методики и научени уроци от реални процедури
- Двоен AI за чат и четене на документи — DeepSeek V3 (работник) + Anthropic Sonnet (контрольор)

## Бърз старт

### Инсталация

Двоен клик на **`install.bat`** — инсталира Python, създава виртуална среда и настройва приложението автоматично.

> При първо стартиране ще ви трябват API ключове (обърнете се към администратора).

### Стартиране

Двоен клик на **`start.bat`** или иконата **"ВиК Графици"** на десктопа.

Приложението се отваря в браузъра на адрес `http://localhost:8501`.

### Работа с приложението

1. В страничната лента изберете папка с тендерна документация (или въведете количества на ръка)
2. Приложението конвертира файловете и чете ситуационните чертежи
3. Отговорете на трите въпроса: ред вода/канал, метод на полагане, брой екипи
4. Кодът изгражда графика; чатът остава за уточнения
5. Прегледайте Gantt диаграмата и свалете PDF/XML за MS Project

## Системни изисквания

- **Windows 10 или 11**
- **Интернет връзка** (за AI API-тата на DeepSeek и Anthropic — чат и четене на документи)
- **Python 3.12+** (инсталира се автоматично от `install.bat`)

## Архитектура

Streamlit UI, детерминистичен pipeline за графика (`schedule_builder`, `work_package`, `duration_calculator`, `execution_batches`), двоен AI маршрутизатор за чат и документи, конвертиране на файлове, интерактивен Gantt и експорт към PDF/XML.

Правилата за разработчици са в [CLAUDE.md](CLAUDE.md). Пълен брийф на системата: [docs/BRIEF_NA_SISTEMATA.md](docs/BRIEF_NA_SISTEMATA.md). Подробна техническа документация: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Период 18–31.08 за одитора: [docs/BRIEF_ZA_ODITORA_2026-08-31.md](docs/BRIEF_ZA_ODITORA_2026-08-31.md).

## Структура на проекта

<!-- FILE_TREE_START -->
```
schedule-generator/
├── config/
│   ├── app_config.json
│   ├── cumulative_usage.json
│   ├── productivities.json
│   ├── project_summary.json
│   └── projects_history.json
├── docs/
│   ├── ARCHITECTURE.md
│   ├── AUTO_UPDATE_DOCS.md
│   ├── CHANGES_2026-02-18-19.md
│   └── UI_WIREFRAME.md
├── fonts/
│   ├── DejaVuSans-Bold.ttf
│   └── DejaVuSans.ttf
├── hooks/
│   └── pre-commit
├── knowledge/
│   ├── lessons/
│   │   ├── lessons_learned.md
│   │   └── pending_lessons.md
│   ├── methodologies/
│   │   ├── distribution_network.md
│   │   ├── engineering_projects.md
│   │   ├── README.md  # Документация за потребители
│   │   ├── single_section.md
│   │   └── supply_pipeline.md
│   ├── skills/
│   │   ├── references/
│   │   └── SKILL.md
│   └── evolution_log.json
├── src/
│   ├── __init__.py
│   ├── ai_processor.py  # Оркестрация на AI pipeline
│   ├── ai_router.py  # Двоен AI маршрутизатор
│   ├── chat_handler.py  # Обработка на чат съобщения
│   ├── constants.py
│   ├── docs_updater.py  # Автоматично обновяване на документация
│   ├── export_pdf.py  # PDF експорт (A3 Gantt)
│   ├── export_xml.py  # MSPDI XML експорт (MS Project)
│   ├── file_manager.py  # Конвертиране на файлове
│   ├── gantt_chart.py  # Интерактивен Plotly Gantt
│   ├── knowledge_manager.py  # 3-нивова база знания
│   ├── project_manager.py  # Управление на проекти
│   ├── schedule_builder.py  # Изграждане на график от AI отговор
│   └── self_evolution.py  # Самоеволюция (3 нива)
├── tests/
│   ├── e2e/
│   │   ├── screenshots/
│   │   ├── __init__.py
│   │   ├── conftest.py
│   │   ├── streamlit_test.log
│   │   ├── test_chat_interaction.py
│   │   ├── test_export_functional.py
│   │   ├── test_gantt.py
│   │   └── test_sidebar_structure.py
│   ├── output/
│   │   ├── test_schedule.pdf
│   │   └── test_schedule.xml
│   ├── __init__.py
│   ├── test_ai_processor_pure.py
│   ├── test_ai_router.py
│   ├── test_chat_handler_pure.py
│   ├── test_classify_files.py
│   ├── test_docs_updater.py
│   ├── test_ensure_schedule_list.py
│   ├── test_export_pdf_utils.py
│   ├── test_exports.py
│   ├── test_extract_project_type.py
│   ├── test_file_manager.py
│   ├── test_gantt_chart.py
│   ├── test_hallucination_detection.py
│   ├── test_handle_confirm_change.py
│   ├── test_intent_keywords.py
│   ├── test_knowledge_manager.py
│   ├── test_out_of_scope_guard.py
│   ├── test_parse_json_response.py
│   ├── test_project_manager.py
│   ├── test_schedule_builder_adjust.py
│   ├── test_schedule_builder_build.py
│   ├── test_schedule_builder_dataframe.py
│   ├── test_self_evolution.py
│   ├── test_sequence_keywords.py
│   ├── test_sequence_questionnaire_c1.py
│   ├── test_validate_schedule.py
│   └── test_xml_structure.py
├── tmp/
│   ├── adversary_report.md
│   ├── prompt_engineer_changes.md
│   └── validation_report.md
├── tools/
│   └── README.md  # Документация за потребители
├── _export_msp.py
├── _export_xml_novi_iskar.py
├── _generate_novi_iskar.py
├── _test_enrich.py
├── _tmp_test.py
├── ACCURACY.md
├── API's.env.txt
├── app.py  # Главно Streamlit приложение
├── CHANGELOG.md  # Списък на промените
├── CLAUDE.md
├── FINAL-QA-REPORT.md
├── install-hooks.bat
├── install.bat  # Инсталатор (Python + venv + пакети)
├── PROJECT_AUDIT.md
├── pytest.ini
├── README.md  # Документация за потребители
├── README_INSTALL.md  # Инструкции за инсталация
├── requirements.txt  # Python зависимости
├── start.bat  # Стартиране на приложението
├── test_situation_ocr.py
└── update.bat  # Обновяване (git pull + pip upgrade)
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
| PyPDF2 | >=3.0.0 | Четене на PDF файлове |
| openpyxl | >=3.1.0 | Четене на Excel файлове |
| watchdog | >=3.0.0 | Наблюдение на файлови промени |
| PyMuPDF | >=1.23.0 | OCR на сканирани PDF-и |
| python-docx | >=1.1.0 | Четене на Word документи |
| pytest | >=9.0.0 |  |
| playwright | >=1.40.0 |  |
| pytest-playwright | >=0.5.0 |  |
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

# Unit тестове (без E2E и без snapshot върху стари прогони)
python -m pytest tests/ --ignore=tests/e2e -m "not snapshot"
```

> **Pre-commit hook**: При `git commit` се пускат security/PII скан и unit тестовете.
> E2E се пускат отделно: `python -m pytest tests/e2e/ -q -m e2e`.
> Инсталация: `install-hooks.bat` или `cp hooks/pre-commit .git/hooks/pre-commit`

## Версия и промени

Текуща версия: **0.9.0**

Пълен списък на промените: [CHANGELOG.md](CHANGELOG.md)

## Лиценз

**Proprietary** — РАИ Комерс. Частно репозитори, всички права запазени.
