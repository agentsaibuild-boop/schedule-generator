# ВиК Schedule Generator — Ръководство за разработчика

## Какво прави приложението
Streamlit app за автоматично генериране на строителни графици (Gantt) за ВиК инфраструктурни проекти в България. Използва DeepSeek V3 (работник) + Anthropic Sonnet (контрольор) като двоен AI.

## Структура на кода
app.py                  ← Главен Streamlit app (стартирай оттук)
src/
  ai_router.py          ← DeepSeek/Anthropic routing + fallback
  ai_processor.py       ← System prompts + генериране
  chat_handler.py       ← Intent detection + чат логика
  file_manager.py       ← PDF/Excel/DOCX → JSON конвертиране
  schedule_builder.py   ← Изграждане на графика
  gantt_chart.py        ← Plotly Gantt (9 слоя)
  export_pdf.py         ← A3 PDF експорт
  export_xml.py         ← MSPDI XML за MS Project
  knowledge_manager.py  ← Зарежда knowledge/ за AI промптове
  project_manager.py    ← Скорошни проекти + прогрес
  self_evolution.py     ← AI пише собствен код (с rollback)
  docs_updater.py       ← Auto-update на документацията

## Ключови правила
- knowledge/ съдържа знания за AI-а — НЕ ги редактирай без причина
- config/productivities.json — производителности v0.4, верифицирани
- XML експорт ЗАДЪЛЖИТЕЛНО с DurationFormat=5 и Manual=1
- Кирилица в PDF изисква DejaVu Sans от fonts/
- .env файловете НЕ са в git

## Стартиране
start.bat  ← стартира app на localhost:8501

## Тестове
uvx --with-requirements requirements.txt pytest tests/ --ignore=tests/e2e   ← unit тестове (325 теста в 16 файла)
pytest tests/e2e/ -v               ← E2E Playwright тестове (10 теста)

- Unit тестовете не изискват стартирано приложение, но изискват пълния Python стек (.env не е нужен)
- E2E тестовете изискват стартирано приложение и реални API ключове (.env)
- Pre-commit hook: автоматично пуска всички unit тестове при всеки git commit
  Инсталирай: cp hooks/pre-commit .git/hooks/pre-commit
- Всеки тест файл има "FAILURE означава:" коментар — показва кой модул е счупен

## Текуща версия: 0.9.0
