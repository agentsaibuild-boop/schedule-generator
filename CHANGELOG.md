# Промени (Changelog)

Всички значими промени по проекта са документирани тук.
Форматът следва [Keep a Changelog](https://keepachangelog.com/bg/1.0.0/).

## [0.7.1] — 2026-02-18

### Подобрено
- PDF конвертиране: PyMuPDF/fitz вместо PyPDF2 — значително по-добро извличане на текст
- 3-степенна стратегия за PDF: GOOD (fitz директно) → PARTIAL (DeepSeek reformat) → SCANNED (DeepSeek OCR)
- Тест с Герман (31 PDF): 27 GOOD, 1 PARTIAL, 3 SCANNED — 0 API извиквания за 90% от файловете

### Добавено
- `AIProcessor.reformat_text()` — преформатиране на частичен текст чрез DeepSeek (евтино, text-only)
- `AIRouter.reformat_text()` — DeepSeek-first с Anthropic fallback за текстови задачи

### Поправено
- DeepSeek V3.2 pricing: $0.28/$0.42 per 1M tokens (актуализирано от старите цени)
- PDF конвертирането вече не задейства скъп OCR за файлове с добър текст

## [0.7.0] — 2026-02-18

### Добавено
- Windows инсталатор: `install.bat`, `start.bat`, `update.bat`
- Пряк път "ВиК Графици" на десктопа
- `.env.company` за фирмени API ключове
- `.streamlit/config.toml` с тема и настройки
- `README_INSTALL.md` — инструкции за колегите
- Проверка на конфигурацията при стартиране

## [0.6.0] — 2026-02-18

### Добавено
- PDF експорт (A3 landscape): таблица + Gantt + критичен път + легенда
- MSPDI XML експорт: DurationFormat=5, Manual=1, Custom Fields, 7-дневен календар
- JSON експорт
- DejaVu Sans шрифт за кирилица в PDF
- Multi-page PDF поддръжка
- Тестове за PDF и XML (`tests/test_exports.py`)

## [0.5.5] — 2026-02-18

### Подобрено
- DeepSeek ВИНАГИ получава knowledge context (3 нива: minimal/full/verification)
- Стриктен JSON pipeline: конвертиране е задължителна първа стъпка
- Кеширане на knowledge файлове с timestamp проверка

### Добавено
- ProjectManager: скорошни 5 проекта с възобновяване от последен прогрес
- Welcome съобщение с контекст при зареждане на проект
- `get_all_knowledge_for_prompt()` с 3 нива

## [0.5.0] — 2026-02-18

### Добавено
- Интерактивен Gantt с Plotly: 9 слоя (toggle)
- Критичен път (червено), зависимости (стрелки), milestones (диаманти)
- Филтри: по екип, фаза, тип + zoom (дни/седмици/месеци)
- Click-to-select с детайлен панел
- Таблица с условно форматиране, статистика, експорт таб
- Демо график: 16 задачи, 25 поддейности, 780 дни, 12 екипа

## [0.4.0] — 2026-02-18

### Добавено
- Самоеволюция: AI модифицира собствения си код
- 3 нива: 🟢 Знания, 🟡 Конфигурация, 🔴 Код (с админ защита)
- Git backup преди промяна, автоматичен rollback при грешка
- `evolution_log.json` — персистентен лог на промени
- Rollback бутон в sidebar

## [0.3.0] — 2026-02-18

### Добавено
- AIRouter: DeepSeek V3 (работник) + Anthropic Sonnet 4.6 (контрольор)
- Автоматичен цикъл на корекции (макс. 3 опита)
- Двупосочен fallback (ако единият е down → другият поема)
- Разходи по модел в sidebar
- Health check при стартиране
- AI проверка на уроци преди запис

## [0.2.0] — 2026-02-18

### Добавено
- Конвертиране: PDF (текст + OCR), Excel, DOCX, CSV → JSON
- Manifest система (`_manifest.json`) за кеширане
- OCR чрез vision API за сканирани PDF-и
- Progress bar и детайлен отчет при конвертиране

## [0.1.0] — 2026-02-18

### Добавено
- Начална структура на проекта (35 файла)
- Streamlit интерфейс: чат (45%) + Gantt (55%) split layout
- Knowledge Manager: 3-нивова база знания
- Демо данни и базов Gantt chart
