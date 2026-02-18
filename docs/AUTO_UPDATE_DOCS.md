# Автоматично обновяване на документацията

Системата за auto-update поддържа документацията синхронизирана с кода без ръчна намеса.

## Как работи

1. **При стартиране** — sidebar-ът показва дали документацията е актуална
2. **При self-evolution** — след успешна промяна на код, документацията се обновява автоматично
3. **Ръчно** — бутон "Обнови документацията" в sidebar-а

## Какво се обновява автоматично

| Документ | Секция | Тригер | Какво прави |
|----------|--------|--------|-------------|
| README.md | Файлово дърво | `src/*.py`, `app.py` | Сканира структурата и генерира ASCII дърво |
| README.md | Зависимости | `requirements.txt` | Чете пакетите и генерира таблица |
| README.md | Версия | `CHANGELOG.md` | Извлича последната версия |
| ARCHITECTURE.md | Компоненти | `src/*.py` | Проверява за недокументирани модули |

## HTML маркери

Секциите за автоматично обновяване са оградени с HTML коментари:

```markdown
<!-- FILE_TREE_START -->
(автоматично генерирано съдържание)
<!-- FILE_TREE_END -->

<!-- DEPS_START -->
(автоматично генерирана таблица)
<!-- DEPS_END -->
```

Всичко **извън** тези маркери се запазва при обновяване.

## Конфигурация

По подразбиране се използва вградената конфигурация. За да я промените, създайте `config/docs_update_config.json`:

```json
{
    "README.md": {
        "sections_to_update": ["file_tree", "dependencies", "version"],
        "triggers": ["requirements.txt", "src/*.py", "app.py"]
    },
    "CHANGELOG.md": {
        "sections_to_update": ["latest_version"],
        "triggers": ["*.py", "*.bat", "*.toml"]
    },
    "docs/ARCHITECTURE.md": {
        "sections_to_update": ["components", "dependencies"],
        "triggers": ["src/*.py"]
    }
}
```

## Как се определя дали е нужно обновяване

1. `DocsUpdater` проверява `git diff --name-only HEAD~1`
2. Ако някой променен файл съвпада с trigger pattern → документът е маркиран за обновяване
3. Ако git не е наличен → проверката се пропуска, без crash

## Добавяне на нова секция за auto-update

1. Добавете HTML маркери в документа: `<!-- MARKER_START -->` и `<!-- MARKER_END -->`
2. Добавете метод `update_<doc>_<section>()` в `DocsUpdater`
3. Добавете секцията в конфигурацията (triggers и sections_to_update)

## Файлове

- `src/docs_updater.py` — основен модул
- `config/docs_update_config.json` — конфигурация (по избор)
- Обновявани документи: `README.md`, `CHANGELOG.md`, `docs/ARCHITECTURE.md`
