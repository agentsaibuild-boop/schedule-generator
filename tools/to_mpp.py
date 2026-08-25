"""Изнесеният MSPDI става .mpp — и то с ИЗГЛЕДА, който човекът иска.

ЗАЩО.  MSPDI XML носи задачите, но НЕ носи изгледа: кои колони се виждат, как
е разграфена скалата и какво пише до лентите.  Затова файлът, отворен в MS
Project, излизаше с английските колони по подразбиране, с ДАТИ и с имената на
ресурсите вдясно от лентите — точно трите неща, които изпълнителят не иска
(25.08.2026).

Изгледът се пази в самия .mpp.  Тук той се сглобява през MS Project:

  * таблица „Тръжен график" с ЕДИНАЙСЕТТЕ колони на еталона Илиянци, на
    български, в същия ред — и БЕЗ „Начало"/„Край" като дати.  Денят пътува в
    Number4/Number5 („Начало (ден)", „Край (ден)"), както го пише експортът;
  * скала, разграфена на ДНИ ОТ НАЧАЛОТО НА ПРОЕКТА (`DayFromStart`), тоест
    „1, 2, 3 …" вместо календар.  В процедура договор няма, Протокол 2а няма,
    затова всяка календарна дата е измислена;
  * до лентите не се пише нищо — ресурсите са КОЛОНА вляво.

Работи само на машина с MS Project.

    python tools/to_mpp.py график.xml график.mpp
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

#: Таблицата: (поле в MS Project, заглавие на колоната, ширина).
#: Редът е на еталона: ID · Вид дейност / Участък · ед.мярка · диаметър · к-во ·
#: Срок · Последователност · Начало (ден) · Край (ден) · ЕКИП · Ресурси.
_КОЛОНИ = (
    ("ID", "ID", 6),
    ("Name", "Вид дейност / Участък", 52),
    ("Text2", "ед.мярка", 8),
    ("Text1", "диаметър", 9),
    ("Number1", "к-во", 10),
    ("Duration", "Срок", 9),
    ("Predecessors", "Последователност", 14),
    ("Number4", "Начало (ден)", 11),
    ("Number5", "Край (ден)", 11),
    ("Text3", "ЕКИП", 8),
    ("Resource Names", "Ресурси", 34),
)

_ИМЕ_НА_ТАБЛИЦАТА = "Тръжен график"

#: Един работен ден в минути — както го обявява и самият експорт.
_МИНУТИ_НА_ДЕН = 480

#: Константите на MS Project, които ни трябват.  Взимат се по име от
#: генерирания type library кеш, а не се пишат наизуст.
_ДНИ = 4                     # pjTimescaleDays
_ДЕН_ОТ_НАЧАЛОТО = 56        # pjCalendarLabelDayFromStart_dd → „1, 2, 3 …"
_ДЕН_ОТ_НАЧАЛОТО_С_ДУМА = 40  # pjCalendarLabelDayFromStart_Day_dd → „Ден 1"


def _константи() -> dict[str, int]:
    """Числата на MS Project, прочетени от неговия собствен type library."""
    import glob
    import re

    from win32com.client import gencache

    намерени: dict[str, int] = {}
    корен = gencache.GetGeneratePath()
    for път in glob.glob(os.path.join(корен, "A7107640*", "*.py")):
        текст = Path(път).read_text(encoding="mbcs", errors="replace")
        for име, стойност in re.findall(r"^\s*(pj\w+)\s*=\s*(-?\d+)", текст, re.M):
            намерени[име] = int(стойност)
    return намерени


def преобразувай(xml: Path, mpp: Path, име_на_проекта: str = "") -> Path:
    """Отвори MSPDI, нагласи изгледа, запиши .mpp."""
    try:
        import win32com.client
    except ImportError as exc:      # pragma: no cover — зависи от машината
        raise SystemExit(
            "Липсва pywin32 — .mpp се пише само от самия MS Project.\n"
            "  python -m pip install pywin32") from exc

    app = win32com.client.gencache.EnsureDispatch("MSProject.Application")
    app.Visible = False
    app.DisplayAlerts = False

    pj_const = _константи()
    дни = pj_const.get("pjTimescaleDays", _ДНИ)
    ден_номер = pj_const.get("pjCalendarLabelDayFromStart_dd", _ДЕН_ОТ_НАЧАЛОТО)
    ден_с_дума = pj_const.get("pjCalendarLabelDayFromStart_Day_dd",
                              _ДЕН_ОТ_НАЧАЛОТО_С_ДУМА)

    if mpp.exists():
        mpp.unlink()

    try:
        app.FileOpen(str(xml))
        проект = app.ActiveProject
        if име_на_проекта:
            проект.Title = име_на_проекта

        _разписвай_напред(app, проект)
        _наложи_дните(проект, xml)
        _таблица(app)
        _скала(app, дни, ден_номер, ден_с_дума)
        _ленти_без_текст(app)

        разлики = _сверка_по_дни(проект)
        if разлики:
            print(f"ВНИМАНИЕ: {len(разлики)} задачи лягат на друг ден в MS "
                  f"Project, отколкото в нашия график.  Първите: "
                  f"{разлики[:5]}")

        app.FileSaveAs(Name=str(mpp), FormatID="MSProject.MPP")
        return mpp
    finally:
        app.FileCloseAll(0)
        app.Quit()


def _разписвай_напред(app, проект) -> None:
    """Графикът се брои НАПРЕД от ден 1, а не назад от края.

    МЕРЕНО 25.08.2026: след внос MS Project слагаше задачи ПРЕДИ началото на
    проекта — „наш ден 2, MS Project −2".  Това е разписване от крайната дата
    (ALAP): всяка задача се лепи за края и се разстила назад.  Тръжният график
    брои от ден 1 нататък, затова посоката се налага изрично.
    """
    try:
        проект.ScheduleFromStart = True
    except Exception:
        pass
    try:
        app.CalculateProject()
    except Exception:
        pass


def _наложи_дните(проект, xml: Path) -> None:
    """Денят и продължителността от НАШИЯ график стават такива и в MS Project.

    ЗАЩО НЕ ПРЕЗ XML-А.  Вносът на MSPDI не удържа нито продължителността, нито
    „Must Start On": мерено на Тръстеник (1013 задачи, 25.08.2026), MS Project
    зануляваше продължителността на 434 задачи и ги обявяваше за milestone-и —
    в готовия файл колоната „Срок" пишеше нула, а лентите бяха точки.  На малък
    файл същият експорт се внася правилно, тоест причината е във вноса.

    Затова графикът се НАЛАГА: началото идва от `Начало (ден)` (Number4), а
    продължителността — от самия XML, който сме написали.  Милиметрова работа,
    но алтернативата е график без ленти.
    """
    from datetime import timedelta

    искани = _продължителности_от_xml(xml)
    начало = проект.ProjectStart
    наложени = провалени = 0
    for задача in проект.Tasks:
        if задача is None or задача.Summary:
            continue
        първи = int(задача.Number4 or 0)
        if първи <= 0:
            continue
        минути = искани.get(int(задача.UniqueID))
        if минути is None:
            последен = int(задача.Number5 or 0)
            минути = max(последен - първи + 1, 1) * _МИНУТИ_НА_ДЕН
        try:
            # ПРОДЪЛЖИТЕЛНОСТТА ПЪРВА, И СЕ ПРОВЕРЯВА.  Задача, която вносът е
            # обявил за точка, не приема продължителност от първия път —
            # мерено: 434 задачи оставаха „0 days" въпреки успешното
            # присвояване.  Затова се пише, чете се обратно и при нужда се
            # маха флагът „точка" и се пише пак.
            # ФИКСИРАНА ПРОДЪЛЖИТЕЛНОСТ.  При тип „фиксирани ресурси" (0) и
            # нулев обем работа MS Project ПРЕСМЯТА продължителността обратно
            # на нула: мерено — 328 задачи не приемаха числото, докато типът им
            # не стане 1.  Същият тип носи и графикът на изпълнителя.
            if минути > 0:
                задача.Type = 1                # pjFixedDuration
                if задача.Milestone:
                    задача.Milestone = False
            задача.Duration = минути
            задача.ConstraintType = 2          # pjMSO — Must Start On
            задача.ConstraintDate = начало + timedelta(days=първи - 1)
            if минути > 0 and int(задача.Duration or 0) != минути:
                задача.Duration = минути
            if int(задача.Duration or 0) != минути:
                провалени += 1
            else:
                наложени += 1
        except Exception:
            провалени += 1
    print(f"наложени дни от нашия график: {наложени} задачи"
          + (f"; {провалени} не приеха" if провалени else ""))


def _продължителности_от_xml(xml: Path) -> dict[int, int]:
    """UID → продължителност в минути, прочетена от нашия собствен MSPDI."""
    import xml.etree.ElementTree as ET

    NS = "{http://schemas.microsoft.com/project}"
    готови: dict[int, int] = {}
    for задача in ET.parse(str(xml)).getroot().iter(f"{NS}Task"):
        uid = задача.findtext(f"{NS}UID")
        текст = задача.findtext(f"{NS}Duration") or ""
        ако_обобщаваща = (задача.findtext(f"{NS}Summary") or "0") == "1"
        if uid is None or ако_обобщаваща:
            continue
        часове = 0
        if текст.startswith("PT") and "H" in текст:
            try:
                часове = int(текст[2:текст.index("H")])
            except ValueError:
                часове = 0
        готови[int(uid)] = часове * 60
    return готови


def _сверка_по_дни(проект) -> list[str]:
    """Ляга ли задачата на ДЕНЯ, който нашият график ѝ дава.

    „Начало (ден)" (Number4) идва от нас; MS Project си разписва датите сам по
    зависимостите.  Разминат ли се двете, графикът в MS Project не е нашият —
    и това трябва да се КАЖЕ, а не да се открие от възложителя.
    """
    начало = проект.ProjectStart
    разлики: list[str] = []
    for задача in проект.Tasks:
        if задача is None or задача.Summary:
            continue
        наш = int(задача.Number4 or 0)
        if наш <= 0:
            continue
        техен = (задача.Start - начало).days + 1
        if abs(техен - наш) > 0:
            разлики.append(f"{задача.Name[:28]}: наш ден {наш}, MS Project {техен}")
    return разлики


def _таблица(app) -> None:
    """Единайсетте колони на еталона, на български, без дати."""
    поле, заглавие, ширина = _КОЛОНИ[0]
    app.TableEditEx(Name=_ИМЕ_НА_ТАБЛИЦАТА, TaskTable=True, Create=True,
                    OverwriteExisting=True, FieldName=поле, Title=заглавие,
                    Width=ширина, ShowInMenu=True, LockFirstColumn=False)
    # ПОЗИЦИЯТА СЕ БРОИ ОТ НУЛА: първата колона е създадена със самата
    # таблица, затова втората влиза на позиция 1.
    for позиция, (поле, заглавие, ширина) in enumerate(_КОЛОНИ[1:], start=1):
        app.TableEditEx(Name=_ИМЕ_НА_ТАБЛИЦАТА, TaskTable=True, Create=False,
                        NewFieldName=поле, Title=заглавие, Width=ширина,
                        ColumnPosition=позиция, HeaderTextWrap=True)
    app.TableApply(Name=_ИМЕ_НА_ТАБЛИЦАТА)


def _скала(app, дни: int, ден_номер: int, ден_с_дума: int) -> None:
    """Скалата брои ДНИ ОТ ДЕН 1 — никъде дата.

    Горният ред пише „Ден 1", долният „1, 2, 3 …".  Тръжният график няма
    календар: началото е Протокол 2а, който още го няма.
    """
    app.TimescaleEdit(MajorUnits=дни, MinorUnits=дни,
                      MajorLabel=ден_с_дума, MinorLabel=ден_номер,
                      MajorCount=10, MinorCount=1, TierCount=2)


def _ленти_без_текст(app) -> None:
    """До лентите не се пише нищо — „след графиката да няма нищо".

    Ресурсите са колона вляво.  MS Project по подразбиране изписва имената им
    ВДЯСНО от всяка лента и точно това човекът поиска да махнем.
    """
    for индекс in range(1, 60):
        try:
            app.GanttBarStyleEdit(Item=индекс, LeftText="", RightText="",
                                  TopText="", BottomText="", InsideText="")
        except Exception:            # стиловете свършват — толкова са
            break


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml")
    parser.add_argument("mpp")
    parser.add_argument("--name", default="", help="име на проекта във файла")
    args = parser.parse_args()

    изход = преобразувай(Path(args.xml), Path(args.mpp), args.name)
    print(f"MPP: {изход} ({изход.stat().st_size} байта)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
