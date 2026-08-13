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
  schedule_builder.py   ← Изграждане, валидация, преизчисляване на графика
  duration_calculator.py ← Детерминистични продължителности (НЕ от LLM)
  gantt_chart.py        ← Plotly Gantt (9 слоя)
  export_pdf.py         ← A3 PDF експорт
  export_xml.py         ← MSPDI XML за MS Project
  knowledge_manager.py  ← Зарежда knowledge/ + подбор на уроци по релевантност
  prompt_safety.py      ← Ограждане на недоверен текст (anti prompt-injection)
  spatial.py            ← Пикетаж, сблъсък на бригади, открит изкоп
  work_package.py       ← Пространствен пакет: Σ количества = КСС, вериги, WBS
  provenance.py         ← Произход на количествата (документ, лист, ред)
  json_contract.py      ← Договор за JSON отговорите от AI (без тихи провали)
  ai_disclosure.py      ← Маркиране „генерирано от AI" (EU AI Act чл. 50)
  project_manager.py    ← Скорошни проекти + прогрес
  self_evolution.py     ← AI пише собствен код (с rollback)
  docs_updater.py       ← Auto-update на документацията

## Ключови правила
- knowledge/ съдържа знания за AI-а — НЕ ги редактирай без причина
- config/productivities.json — производителности v0.4, верифицирани.
  Това е ЕДИНСТВЕНИЯТ източник за продължителности — чете се от
  duration_calculator.py. НЕ връщай аритметика (ceil, тарифи) в промптовете.
- ГЕНЕРАЦИЯТА е ПАКЕТНА: моделът връща физически участъци (`packages`), а
  веригите, зависимостите, WBS-ът, датите и CPM идват от кода
  (chat_handler._try_package_generation → ai_processor.generate_schedule_packaged).
  `PACKAGE_GENERATION=0` връща стария плосък път; при неуспех пада сам.
  Ред на пакетния път: пакети → Σ=КСС гейт → допитване за неразпределени →
  пренасочване на позиции в подходяща верига → фронтове → вериги → WBS →
  кръстосани връзки → продължителности от нормите → дати → CPM.
- Възлите (РШ/ОТ) НЕ са в КСС — четат се от ситуационните чертежи с vision
  (`extract_situation_segments`). Изисква OCR_MODEL с реален vision достъп;
  `SITUATION_SEGMENTS=0` изключва.
- config/tech_chains.json — технологичните вериги, ИЗВЛЕЧЕНИ от еталонен човешки
  график (46× канализационен участък, 23× водопроводен). `covers` трябва да е на
  речника на provenance._PRODUCTION_CLASSES, иначе покритието не ги вижда.
- XML експорт ЗАДЪЛЖИТЕЛНО с DurationFormat=5. Режим 'milestones' (по
  подразбиране) дава Manual=0 + ConstraintType=0 и constraint само по договорни
  milestone-и (Deadline + FNLT); 'pinned' → ConstraintType=2 (старото поведение);
  'flexible' → ConstraintType=0 без изключения
- Кирилица в PDF изисква DejaVu Sans от fonts/
- .env файловете НЕ са в git

## Стартиране
start.bat  ← стартира app на localhost:8501

## Тестове
uvx --with-requirements requirements.txt pytest tests/ --ignore=tests/e2e   ← unit тестове (2207 теста в 82 файла)
pytest tests/e2e/ -v               ← E2E Playwright тестове (10 теста)

- Unit тестовете не изискват стартирано приложение, но изискват пълния Python стек (.env не е нужен)
- E2E тестовете изискват стартирано приложение и реални API ключове (.env)
- Pre-commit hook: автоматично пуска всички unit тестове при всеки git commit
  Инсталирай: cp hooks/pre-commit .git/hooks/pre-commit
- Всеки тест файл има "FAILURE означава:" коментар — показва кой модул е счупен

## Текуща версия: 0.9.0
