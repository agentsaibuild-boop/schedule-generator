"""Тест: PDF и XML генерирането работят и дават download бутон."""
# -*- coding: utf-8 -*-

import pytest

pytestmark = pytest.mark.e2e

EXPORT_TIMEOUT = 30000


#: Приложението възстановява предишната сесия при старт — чат и график — и при
#: голям график това трае осезаемо.  Измерено 17.08.2026: разделите се появяват
#: след повече от 30 секунди и двата експортни теста падаха с „Експорт не е
#: видим", което чете като изтрит раздел.  Разделът си беше там.
TABS_TIMEOUT = 90000

#: Разделите на Streamlit НЕ са <button>.  Днешната версия ги рисува като
#: `[data-testid="stTab"]` с `role="tab"`, затова старият селектор
#: `button[role="tab"]` връщаше нула — човек вижда раздела на екрана, а тестът
#: чака вечно и после съобщава „Експорт не е видим".  Измерено 17.08.2026.
TAB = '[data-testid="stTab"]'



def _go_to_export_tab(page):
    """Навигира до Export таба."""
    export_tab = page.locator(TAB, has_text="Експорт")
    export_tab.first.wait_for(state="visible", timeout=TABS_TIMEOUT)
    export_tab.first.click()
    page.wait_for_timeout(1500)


def test_pdf_generates_and_download_appears(app_page):
    """
    Натискаме 'Генерирай PDF' и проверяваме че се появява download бутон.
    FAILURE означава: export_pdf.py е счупен или PDF генерирането хвърля грешка.
    """
    _go_to_export_tab(app_page)

    pdf_btn = app_page.locator('button', has_text="Генерирай PDF")
    pdf_btn.wait_for(state="visible", timeout=10000)
    pdf_btn.click()

    download_btn = app_page.locator('[data-testid="stDownloadButton"]').first
    download_btn.wait_for(state="visible", timeout=EXPORT_TIMEOUT)
    assert download_btn.is_visible(), (
        "Download бутон не се появи след 'Генерирай PDF' — export_pdf.py е счупен"
    )


def test_xml_generates_and_download_appears(app_page):
    """
    Натискаме 'Генерирай XML' и проверяваме че се появява download бутон.
    FAILURE означава: export_xml.py е счупен или XML генерирането хвърля грешка.
    """
    _go_to_export_tab(app_page)

    xml_btn = app_page.locator('button', has_text="Генерирай XML")
    xml_btn.wait_for(state="visible", timeout=10000)
    xml_btn.click()

    download_btn = app_page.locator('[data-testid="stDownloadButton"]').first
    download_btn.wait_for(state="visible", timeout=EXPORT_TIMEOUT)
    assert download_btn.is_visible(), (
        "Download бутон не се появи след 'Генерирай XML' — export_xml.py е счупен"
    )
