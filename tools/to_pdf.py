"""Готовият график става PDF — през самия MS Project, с изгледа, който носи.

ЗАЩО.  XML-ът доказва мрежата: връзки, ограничения, ресурси.  Той обаче НЕ
носи изгледа — таблици и изгледи се пазят в .mpp, не в MSPDI.  Затова
твърдения от рода на „скалата е по дни" и „ресурсите не са вдясно от лентите"
не могат да се проверят от XML-а и трябва да се ВИДЯТ.  PDF-ът е това
доказателство: каквото се вижда в него, това ще види и възложителят.

РАБОТИ СЕ ПРЯКО ОТ XML-а.  Преобразуването MSPDI → .mpp в самия MS Project
разваля назначенията: всички сочат към последния ресурс в листа (мерено
03.09.2026, възпроизвежда се с два реда и пет ресурса).  Затова изгледът се
нагласява върху ВНЕСЕНИЯ проект и се печата от него, без междинен .mpp.

Страницата е A2 напряко, свита по ШИРИНА до един лист; редовете текат надолу
колкото трябват.  Печата се точно срокът — от ден 1 до последния.

    python tools/to_pdf.py "график.xml" "график.pdf" --name "обект"
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: pjPaperA2 — най-големият формат, който MS Project познава (A1 го няма).
_A2 = 66
#: pjPDF
_PDF = 0
#: Свиване по ШИРИНА до един лист; надолу — колкото трябват.
_МАКС_ЛИСТА = 40
#: Широчина на дневната колона в проценти.  25 е МИНИМУМЪТ, който MS Project
#: приема — под него повикването се връща с „The argument value is not valid".
_СВИВАНЕ = 25
#: Колко пъти се започва отначало, когато MS Project умре по средата.
_ОПИТИ = 5


def _кирилица(път: Path) -> bool:
    return any("Ѐ" <= знак <= "ӿ" for знак in str(път))


def _убий_ms_project() -> None:
    subprocess.run(["taskkill", "/F", "/IM", "WINPROJ.EXE"], capture_output=True)


def _отвори(win32com, източник: Path):
    """Нова инстанция и отворен файл.

    `DispatchEx`, защото след taskkill старата инстанция е полумъртва и всяко
    повикване към нея се проваля.
    """
    win32com.client.gencache.EnsureDispatch("MSProject.Application")
    app = win32com.client.DispatchEx("MSProject.Application")
    app.Visible = False
    app.DisplayAlerts = False
    app.FileOpen(str(източник))
    _ = app.ActiveProject.Tasks.Count
    return app


def _един_опит(win32com, източник: Path, цел: Path, име_на_проекта: str,
               бележка: str = "") -> None:
    """Внася, нагласява изгледа, одитира мрежата и печата."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from to_mpp import (_докладвай_критичния_път, _заглавен_ред, _константи,
                        _ленти_без_текст, _мрежа, _наложи_дните,
                        _разписвай_напред, _сверка_по_дни, _скала, _таблица,
                        _търпеливо)

    app = _отвори(win32com, източник)
    try:
        проект = app.ActiveProject
        if източник.suffix.lower() == ".xml":
            _разписвай_напред(app, проект)
            _наложи_дните(проект, източник)
            _таблица(app)
            pj = _константи()
            # За печат дневната колона се свива, за да влязат 255 дни в
            # ширината на един лист — но скалата остава по ДНИ.  И двата ѝ
            # реда са ЧИСЛА: етикетът „Day 48" идва от езика на инсталацията
            # и в български график изглежда като чужд надпис.
            ден_номер = pj.get("pjCalendarLabelDayFromStart_dd", 56)
            _скала(app, pj.get("pjTimescaleDays", 4), ден_номер, ден_номер,
                   увеличение=_СВИВАНЕ, дребна_стъпка=10, едра_стъпка=10,
                   редове=1)
            _ленти_без_текст(app)
            _мрежа(app)
            _заглавен_ред(app)
            if име_на_проекта:
                # Без това в шапката и на нулевия ред пише името на временния
                # файл, а не обектът.
                проект.Title = име_на_проекта

            # ОДИТЪТ ВЪРВИ С ВСЯКО ИЗЧЕРТАВАНЕ.  Лист не тръгва към
            # възложителя, без да е казано дали MS Project разписва графика
            # така, както го смятаме.
            разлики = _сверка_по_дни(проект)
            if разлики:
                print(f"РАЗМИНАВАНЕ: {len(разлики)} задачи лягат на друг ден:")
                for ред in разлики[:10]:
                    print(f"    · {ред}")
            else:
                print("сверка по дни: нула разминавания")
            _докладвай_критичния_път(проект)

        # Печатните повиквания също чакат MS Project да се освободи —
        # „Call was rejected by callee" тук значи зает, не умрял, и не бива да
        # рестартира целия опит.
        изглед = _търпеливо(lambda: app.ActiveWindow.ActivePane.View().Name)
        try:
            _търпеливо(lambda: app.FilePageSetupPage(
                Name=изглед, Portrait=False, PagesWide=1,
                PagesTall=_МАКС_ЛИСТА, PaperSize=_A2))
        except Exception as грешка:
            print(f"ВНИМАНИЕ: страницата не се нагласи ({грешка})")
        for повикване in (
                lambda: app.FilePageSetupLegend(Name=изглед, TextWidth=0,
                                                LegendOn=False),
                lambda: app.FilePageSetupHeader(
                    Name=изглед, Alignment=1,
                    # Бележката стои в ШАПКАТА на всеки лист — тя е за целия
                    # график, не за отделен ред, и там не се реже.
                    Text="&[Project Title]" + (chr(10) + бележка if бележка
                                               else "")),
                lambda: app.FilePageSetupView(Name=изглед, AllSheetColumns=True,
                                              RepeatColumns=2, PrintNotes=False,
                                              PrintBlankPages=False)):
            try:
                _търпеливо(повикване)
            except Exception:
                continue

        # ПЕЧАТА СЕ ТОЧНО СРОКЪТ, от ден 1 до последния.  Без FromDate/ToDate
        # MS Project слага поле от двете страни: скалата тръгва от −13 и
        # завършва след последния ден — в тръжен график това е шум.
        # ГРАФИКАТА ЗАПОЧВА ОТ ДЕН −3 (неотменимо правило, 04.09.2026) — три
        # дни поле преди ден 1, за да се чете началото.  Таблицата обаче тръгва
        # от ДЕН 1 и свършва на последния ден: това са колоните „Начало (ден)"
        # и „Край (ден)", а те не зависят от печатния диапазон.
        from datetime import timedelta as _тд
        try:
            от_дата = проект.ProjectStart - _тд(days=3)
            до_дата = проект.ProjectFinish
        except Exception:
            от_дата = до_дата = None
        if цел.exists():
            цел.unlink()
        if от_дата is not None:
            _търпеливо(lambda: app.DocumentExport(
                str(цел), _PDF, True, False, 0, от_дата, до_дата))
        else:
            _търпеливо(lambda: app.DocumentExport(str(цел), _PDF, True))
    finally:
        try:
            app.FileCloseAll(0)
            app.Quit()
        except Exception:
            pass


def преобразувай(вход: Path, pdf: Path, име_на_проекта: str = "",
                 бележка: str = "") -> Path:
    import pywintypes
    import win32com
    import win32com.client  # noqa: F401  — нужен е за DispatchEx в _отвори

    работна = Path(tempfile.mkdtemp(prefix="pdf_"))
    източник, цел = вход, pdf
    if _кирилица(вход) or _кирилица(pdf):
        # Кирилица в ПЪТЯ убива COM повикванията — същото като в to_mpp.
        източник = работна / ("schedule" + вход.suffix.lower())
        shutil.copy2(вход, източник)
        цел = работна / "schedule.pdf"

    # ЦЕЛИЯТ ОПИТ СЕ ПОВТАРЯ, не само отварянето.  MS Project умира и по
    # СРЕДАТА на работата („The RPC server is unavailable" насред обхождането
    # на задачите); при 983 задачи това се случва през път.  Половин свършена
    # работа не се спасява — тръгва се отначало с чиста инстанция.
    последна = None
    for опит in range(1, _ОПИТИ + 1):
        try:
            _един_опит(win32com, източник, цел, име_на_проекта, бележка)
            break
        except (pywintypes.com_error, TypeError) as грешка:
            последна = грешка
            print(f"опит {опит}: MS Project прекъсна ({str(грешка)[:60]}) — "
                  f"започвам отначало")
            _убий_ms_project()
            time.sleep(8)
    else:
        raise SystemExit(f"MS Project не издържа {_ОПИТИ} опита: {последна}")

    if цел != pdf:
        pdf.parent.mkdir(parents=True, exist_ok=True)
        if pdf.exists():
            pdf.unlink()
        shutil.move(str(цел), str(pdf))
    shutil.rmtree(работна, ignore_errors=True)
    return pdf


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("вход", help=".xml (по-точно) или .mpp")
    parser.add_argument("pdf")
    parser.add_argument("--name", default="", help="име на обекта в шапката")
    parser.add_argument("--note", default="",
                        help="втори ред в шапката — напр. несъответствие в количествата")
    args = parser.parse_args()
    изход = преобразувай(Path(args.вход), Path(args.pdf), args.name, args.note)
    print(f"PDF: {изход} ({изход.stat().st_size} байта)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
