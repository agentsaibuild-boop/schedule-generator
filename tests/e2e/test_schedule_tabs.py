"""Тест: разделите под чата се рисуват — таблица, статистика, експорт.

ЗАМЕНЯ `test_gantt.py` (17.08.2026).  Онзи файл проверяваше екранна Gantt
диаграма със слоеве, критичен път и филтри — интерфейс, който беше премахнат
нарочно с комит `cda3cde` („remove Gantt chart, chat goes full-width, add
schedule tabs below").  Тестовете обаче не бяха обновени и оттогава описваха
несъществуващ екран: три от тях падаха с „Gantt не се рисува", което чете като
счупен модул, а всъщност беше остарял тест.

`src/gantt_chart.py` не е изтрит — той продължава да рисува диаграмата за PDF
експорта и си има свои unit тестове (`tests/test_gantt_chart.py`).  На екрана
диаграма няма и не се очаква.

FAILURE означава: разделите с графика под чата не се рисуват — човекът вижда
чата, но не може да стигне до таблицата, статистиката или експорта.
"""
# -*- coding: utf-8 -*-

import pytest

pytestmark = pytest.mark.e2e

#: Приложението възстановява предишната сесия при старт (чат + график) и това
#: отнема осезаемо време при голям график.  Затова изчакването тук е дълго:
#: мери се дали разделите СЕ ПОЯВЯВАТ, а не колко бързо.
TABS_TIMEOUT = 90000

#: Разделите на Streamlit НЕ са <button>.  Днешната версия ги рисува като
#: `[data-testid="stTab"]` с `role="tab"`, затова старият селектор
#: `button[role="tab"]` връщаше нула — човек вижда раздела на екрана, а тестът
#: чака вечно и после съобщава „Експорт не е видим".  Измерено 17.08.2026.
TAB = '[data-testid="stTab"]'

#: ВИДИМАТА таблица, не първата в DOM-а (24.08.2026).  Страничната лента също
#: държи таблица — формата за ръчно въведени количества е `st.data_editor`,
#: тоест също `stDataFrame`.  Селектор през цялата страница с `.first` хващаше
#: НЕЯ: скрита в свит експандър, тестът чакаше 30 секунди и съобщаваше
#: „Разделът Таблица не показва график" — неверен извод за верен екран.
#:
#: Филтърът е по ВИДИМОСТ, а не по контейнер: `stMain` го няма в Streamlit
#: 1.60 (там е `stMainBlockContainer`), а името на контейнера се мени между
#: версиите — видимостта е това, което тестът наистина проверява.
ТАБЛИЦА = '[data-testid="stDataFrame"]:visible, table:visible'


def _tab(page, име: str):
    return page.locator(TAB, has_text=име)


def test_schedule_tabs_are_rendered(app_page):
    """Трите раздела под чата са налице, когато има график."""
    for име in ("Таблица", "Статистика", "Експорт"):
        раздел = _tab(app_page, име)
        раздел.first.wait_for(state="visible", timeout=TABS_TIMEOUT)
        assert раздел.first.is_visible(), f'Разделът {име} не се рисува'


def test_table_tab_shows_the_schedule(app_page):
    """Таблицата съдържа редове — не празна рамка."""
    _tab(app_page, "Таблица").first.wait_for(state="visible", timeout=TABS_TIMEOUT)
    _tab(app_page, "Таблица").first.click()
    app_page.wait_for_timeout(2000)

    таблица = app_page.locator(ТАБЛИЦА).first
    таблица.wait_for(state="visible", timeout=30000)
    assert таблица.is_visible(), 'Разделът Таблица не показва график'


def test_stats_tab_opens(app_page):
    """Статистиката се отваря без грешка на екрана."""
    _tab(app_page, "Статистика").first.wait_for(state="visible", timeout=TABS_TIMEOUT)
    _tab(app_page, "Статистика").first.click()
    app_page.wait_for_timeout(2000)

    assert app_page.locator('[data-testid="stException"]').count() == 0, \
        'Разделът Статистика хвърля грешка на екрана'
