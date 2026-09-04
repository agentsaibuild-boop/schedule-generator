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
#: ШЕСТТЕ КОЛОНИ, които инженерът иска — и нищо повече (неотменимо правило,
#: 04.09.2026).  Ако потрябва друга, ще бъде поискана изрично.
_КОЛОНИ = (
    ("Name", "Вид дейност / Участък", 82),
    ("Duration", "Срок", 8),
    ("Number4", "Начало (ден)", 10),
    ("Number5", "Край (ден)", 10),
    ("Predecessors", "Последователност", 14),
    ("Resource Names", "Ресурси", 60),
)

_ИМЕ_НА_ТАБЛИЦАТА = "Тръжен график"

#: Един ден — за точките, които стоят на границата между два дни.
from datetime import timedelta as _timedelta

_ЕДИН_ДЕН = _timedelta(days=1)

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


def преобразувай(xml: Path, mpp: Path, име_на_проекта: str = "",
                 заковавай: bool = False) -> Path:
    """Отвори MSPDI, нагласи изгледа, запиши .mpp.

    РАБОТИ СЕ В ЛАТИНСКА ПАПКА.  Мерено 03.09.2026: `FileOpen` УБИВА MS Project
    („The remote procedure call failed“), когато в ПЪТЯ има кирилица — папката
    `…\Тръстеник_график\…` гърми, същият файл в латинска папка се отваря.
    Кирилица в самото ИМЕ на файла минава.  Затова тук се работи във временна
    латинска папка, а готовият .mpp се премества на исканото място.
    """
    import shutil
    import tempfile

    работна = Path(tempfile.mkdtemp(prefix="mpp_"))
    if _кирилица(xml) or _кирилица(mpp):
        работен_xml = работна / "schedule.xml"
        shutil.copy2(xml, работен_xml)
        работен_mpp = работна / "schedule.mpp"
        _преобразувай(работен_xml, работен_mpp, име_на_проекта, заковавай)
        mpp.parent.mkdir(parents=True, exist_ok=True)
        if mpp.exists():
            mpp.unlink()
        shutil.move(str(работен_mpp), str(mpp))
        shutil.rmtree(работна, ignore_errors=True)
        return mpp
    shutil.rmtree(работна, ignore_errors=True)
    return _преобразувай(xml, mpp, име_на_проекта, заковавай)


def _кирилица(път: Path) -> bool:
    return any("Ѐ" <= знак <= "ӿ" for знак in str(път))


def _преобразувай(xml: Path, mpp: Path, име_на_проекта: str = "",
                  заковавай: bool = False) -> Path:
    """Същинската работа — вече на път без кирилица."""
    try:
        import win32com.client
    except ImportError as exc:      # pragma: no cover — зависи от машината
        raise SystemExit(
            "Липсва pywin32 — .mpp се пише само от самия MS Project.\n"
            "  python -m pip install pywin32") from exc

    app = _отвори_проекта(win32com, xml)

    pj_const = _константи()
    дни = pj_const.get("pjTimescaleDays", _ДНИ)
    ден_номер = pj_const.get("pjCalendarLabelDayFromStart_dd", _ДЕН_ОТ_НАЧАЛОТО)
    ден_с_дума = pj_const.get("pjCalendarLabelDayFromStart_Day_dd",
                              _ДЕН_ОТ_НАЧАЛОТО_С_ДУМА)

    if mpp.exists():
        mpp.unlink()

    try:
        проект = _търпеливо(lambda: app.ActiveProject)
        if име_на_проекта:
            проект.Title = име_на_проекта

        _разписвай_напред(app, проект)
        _наложи_дните(проект, xml, заковавай)
        _таблица(app)
        _скала(app, дни, ден_номер, ден_с_дума)
        _ленти_без_текст(app)
        _мрежа(app)
        _заглавен_ред(app)

        разлики = _сверка_по_дни(проект)
        if разлики:
            print(f"РАЗМИНАВАНЕ: {len(разлики)} задачи лягат на друг ден в MS "
                  f"Project, отколкото в нашия график — това са липсващи или "
                  f"грешни връзки в мрежата:")
            for ред in разлики[:15]:
                print(f"    · {ред}")
        else:
            print("сверка по дни: MS Project разписва графика ТОЧНО както го "
                  "смятаме — нула разминавания")
        _докладвай_критичния_път(проект)

        _търпеливо(lambda: app.FileSaveAs(Name=str(mpp),
                                          FormatID="MSProject.MPP"))
        return mpp
    finally:
        try:
            _търпеливо(lambda: app.FileCloseAll(0))
            _търпеливо(lambda: app.Quit())
        except Exception:
            pass


#: RPC_E_CALL_REJECTED — MS Project е зает и отказва повикването.
_ЗАЕТ = -2147418111


def _търпеливо(действие, опити: int = 300, пауза: float = 1.0):
    """Повтаря повикването, докато MS Project спре да е зает.

    При 1042 задачи вносът пуска преизчисление, което трае секунди; всяко
    обръщение през това време се връща с „Call was rejected by callee“.
    Правилният отговор е да се изчака, а не да се откажем — иначе .mpp-то
    просто не се получава.
    """
    import time

    import pywintypes

    последна = None
    for _ in range(опити):
        try:
            return действие()
        except pywintypes.com_error as грешка:      # noqa: PERF203
            if грешка.args[0] != _ЗАЕТ:
                raise
            последна = грешка
            time.sleep(пауза)
    raise последна


def _отвори_проекта(win32com, xml: Path, опити: int = 10):
    """Отваря файла, а при срив рестартира MS Project и опитва пак.

    МЕРЕНО 03.09.2026: при файл с 1042 задачи, 999 връзки и 8206 назначения
    `FileOpen` УБИВА WINPROJ.EXE през път — веднъж минава, следващия път се
    връща „The remote procedure call failed“.  Съдържанието е същото, тоест
    причината не е в него.  Единственият честен отговор е да се опита пак с
    чиста инстанция, а не да се обяви, че файлът е лош.
    """
    import subprocess
    import time

    import pywintypes

    последна = None
    for опит in range(1, опити + 1):
        try:
            # DispatchEx вдига НОВА инстанция.  EnsureDispatch се закача за
            # вече работеща, а след taskkill тя е полумъртва и следващото
            # повикване се връща с „The remote procedure call failed“.
            win32com.client.gencache.EnsureDispatch("MSProject.Application")
            app = win32com.client.DispatchEx("MSProject.Application")
            app.Visible = False
            app.DisplayAlerts = False
            app.FileOpen(str(xml))
            _търпеливо(lambda: app.ActiveProject.Tasks.Count)
            if опит > 1:
                print(f"MS Project отвори файла от {опит}-ия опит")
            return app
        except (pywintypes.com_error, TypeError) as грешка:
            последна = грешка
            try:
                app.Quit()
            except Exception:
                pass
            subprocess.run(["taskkill", "/F", "/IM", "WINPROJ.EXE"],
                           capture_output=True)
            time.sleep(8)          # MS Project иска време да се разчисти
    raise SystemExit(f"MS Project не успя да отвори {xml.name}: {последна}")


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


def _наложи_дните(проект, xml: Path, заковавай: bool = False) -> None:
    """Денят и продължителността от НАШИЯ график стават такива и в MS Project.

    ЗАЩО НЕ ПРЕЗ XML-А.  Вносът на MSPDI не удържа нито продължителността, нито
    „Must Start On": мерено на Тръстеник (1013 задачи, 25.08.2026), MS Project
    зануляваше продължителността на 434 задачи и ги обявяваше за milestone-и —
    в готовия файл колоната „Срок" пишеше нула, а лентите бяха точки.  На малък
    файл същият експорт се внася правилно, тоест причината е във вноса.

    Затова се налага ПРОДЪЛЖИТЕЛНОСТТА.  Началото — НЕ.

    ЗАЩО НЕ И НАЧАЛОТО (03.09.2026).  Дотук тук се пишеше и
    `ConstraintType = 2` (Must Start On) с дата от `Начало (ден)`.  Резултатът
    е график, в който всичките 1044 задачи са заковани: зависимостите стават
    декорация, критичен път няма, резерв няма, а промяна на една
    продължителност не пренарежда нищо.  Точно това върна възложителят.

    Сега задачите остават такива, каквито ги е обявил XML-ът — ASAP, с краен
    срок само по договорните точки — MS Project ги разписва САМ по връзките, а
    после `_сверка_по_дни` казва къде неговият ден се различава от нашия.
    Всяко разминаване е ЛИПСВАЩА ВРЪЗКА в мрежата, не грешка на MS Project.

    `заковавай=True` връща старото поведение — за отпечатване на вече приет
    график, не за подаване.
    """
    from datetime import datetime, timedelta

    искани = _продължителности_от_xml(xml)
    ограничения = _ограничения_от_xml(xml)
    # РЪЧНО ПРЕСМЯТАНЕ.  Иначе MS Project преизчислява целия график след всяка
    # промяна на продължителност и отказва следващото повикване като зает.
    for изключване in (lambda: setattr(проект.Application, "Calculation", 0),
                       lambda: setattr(проект.Application, "ScreenUpdating", False)):
        try:
            _търпеливо(изключване)
        except Exception:
            pass
    начало = _търпеливо(lambda: проект.ProjectStart)
    наложени = провалени = 0
    for задача in _търпеливо(lambda: проект.Tasks):
        if задача is None or _търпеливо(lambda: задача.Summary):
            continue
        първи = int(_търпеливо(lambda: задача.Number4) or 0)
        if първи <= 0:
            continue
        # ДНИТЕ СА ИСТИНАТА.  „Начало (ден)" и „Край (ден)" са това, което
        # човекът чете в колоните и в PDF-а; продължителността трябва да е
        # РАЗЛИКАТА между тях, иначе лентата е по-дълга от дните и покрива
        # следващата дейност (мерено: 120 застъпени двойки в готовия файл, при
        # нула в самия график).  Стойността от XML-а е само резерва.
        последен = int(_търпеливо(lambda: задача.Number5) or 0)
        точка = bool(искани.get(int(задача.UniqueID)) == 0)
        минути = (0 if точка
                  else max(последен - първи + 1, 1) * _МИНУТИ_НА_ДЕН)
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
                задача.EffortDriven = False
                if задача.Milestone:
                    задача.Milestone = False
            задача.Duration = минути
            if not заковавай:
                # ОГРАНИЧЕНИЕТО Е НАШЕТО, не това, което вносът е измислил.
                вид, дата = ограничения.get(int(задача.UniqueID), (0, ""))
                задача.ConstraintType = вид
                if вид not in (0, 1) and len(дата) >= 19:
                    # ДАТАТА Е ДАТА, не низ.  Подадена като текст, MS Project я
                    # чете по локала и краят на проекта се озовава някъде в
                    # началото: мерено — среден резерв −224 дни при график,
                    # който свършва по-рано от срока си.
                    задача.ConstraintDate = datetime.strptime(
                        дата[:19], "%Y-%m-%dT%H:%M:%S")
            if заковавай:
                задача.ConstraintType = 2      # pjMSO — Must Start On
                задача.ConstraintDate = начало + timedelta(days=първи - 1)
            if минути > 0 and int(задача.Duration or 0) != минути:
                задача.Duration = минути
            if int(задача.Duration or 0) != минути:
                провалени += 1
            else:
                наложени += 1
        except Exception:
            провалени += 1
    for връщане in (lambda: setattr(проект.Application, "Calculation", 1),
                    lambda: setattr(проект.Application, "ScreenUpdating", True)):
        try:
            _търпеливо(връщане)
        except Exception:
            pass
    try:
        _търпеливо(lambda: проект.Application.CalculateProject())
    except Exception:
        pass
    print(f"наложени дни от нашия график: {наложени} задачи"
          + (f"; {провалени} не приеха" if провалени else ""))


def _ограничения_от_xml(xml: Path) -> dict[int, tuple[int, str]]:
    """UID → (ConstraintType, ConstraintDate) така, както сме ги ОБЯВИЛИ.

    Вносът на MS Project си слага собствени ограничения (мерено: SNLT на 981
    задачи).  Затова след вноса нашите се НАЛАГАТ обратно: ASAP навсякъде и
    краен срок само там, където сме казали.
    """
    import xml.etree.ElementTree as ET

    NS = "{http://schemas.microsoft.com/project}"
    готови: dict[int, tuple[int, str]] = {}
    for задача in ET.parse(str(xml)).getroot().iter(f"{NS}Task"):
        uid = задача.findtext(f"{NS}UID")
        if uid is None:
            continue
        вид = задача.findtext(f"{NS}ConstraintType")
        дата = задача.findtext(f"{NS}ConstraintDate") or ""
        if вид is not None:
            готови[int(uid)] = (int(вид), дата)
    return готови


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
    # ПО ДАТИ, НЕ ПО ЧАСОВЕ.  pywin32 връща едни свойства с часова зона, а
    # други без: `ProjectStart` идва като 08:00+00:00, а началото на задача —
    # като 06:00+00:00 (същите 08:00 местно време, преобразувани).  Изваждането
    # на двете дава 22 часа по-малко и всяка сверка лъжеше с един ден.
    начало = проект.ProjectStart.date()
    разлики: list[str] = []
    for задача in проект.Tasks:
        if задача is None or задача.Summary:
            continue
        наш = int(задача.Number4 or 0)
        if наш <= 0:
            continue
        # ТОЧКИТЕ НЕ СЕ СВЕРЯВАТ ПО ДЕН.  Milestone няма продължителност: той
        # стои в мига 17:00, който е едновременно край на ден N и начало на
        # ден N+1.  Кой от двата се изписва е въпрос на надпис, не на график —
        # затова се мерят само задачите с работа, а точките се броят отделно.
        if задача.Milestone:
            continue
        техен = (задача.Start.date() - начало).days + 1
        if abs(техен - наш) > 0:
            разлики.append(f"{задача.Name[:28]}: наш ден {наш}, MS Project {техен}")
    return разлики


def _докладвай_критичния_път(проект) -> None:
    """Критичен път и резерв — доказателството, че мрежата РАБОТИ.

    При заковани дати MS Project не смята нищо: нула критични задачи и нула
    резерв навсякъде.  Ако тук излезе критичен път с разумна дължина, значи
    зависимостите наистина управляват графика.
    """
    критични = общо = 0
    резерв: list[int] = []
    for задача in _търпеливо(lambda: проект.Tasks):
        if задача is None or _търпеливо(lambda: задача.Summary):
            continue
        общо += 1
        if _търпеливо(lambda: задача.Critical):
            критични += 1
        резерв.append(int(_търпеливо(lambda: задача.TotalSlack) or 0) // 480)
    ср = sum(резерв) / len(резерв) if резерв else 0
    print(f"критичен път: {критични} от {общо} листови задачи · "
          f"среден резерв {ср:.1f} дни · най-голям {max(резерв or [0])} дни")


def _таблица(app) -> None:
    """Единайсетте колони на еталона, на български, без дати."""
    поле, заглавие, ширина = _КОЛОНИ[0]
    app.TableEditEx(Name=_ИМЕ_НА_ТАБЛИЦАТА, TaskTable=True, Create=True,
                    OverwriteExisting=True, FieldName=поле, Title=заглавие,
                    Width=ширина, ShowInMenu=True, LockFirstColumn=False,
                    HeaderTextWrap=True, WrapText=True)
    # ПОЗИЦИЯТА СЕ БРОИ ОТ НУЛА: първата колона е създадена със самата
    # таблица, затова втората влиза на позиция 1.
    for позиция, (поле, заглавие, ширина) in enumerate(_КОЛОНИ[1:], start=1):
        app.TableEditEx(Name=_ИМЕ_НА_ТАБЛИЦАТА, TaskTable=True, Create=False,
                        NewFieldName=поле, Title=заглавие, Width=ширина,
                        ColumnPosition=позиция, HeaderTextWrap=True,
                        WrapText=True)
    app.TableApply(Name=_ИМЕ_НА_ТАБЛИЦАТА)


def _скала(app, дни: int, ден_номер: int, ден_с_дума: int,
           увеличение: int = 100, дребна_стъпка: int = 1,
           едра_стъпка: int = 10, редове: int = 2) -> None:
    """Скалата брои ДНИ ОТ ДЕН 1 — никъде дата.

    Горният ред пише „Ден 1", долният „1, 2, 3 …".  Тръжният график няма
    календар: началото е Протокол 2а, който още го няма.

    `увеличение` свива широчината на дневната колона (в проценти; MS Project
    приема най-малко 25 — под това повикването се връща с „The argument value
    is not valid").  За печат
    255 дни трябва да влязат в ширината на един лист; `ZoomTimescale(Entire)`
    също ги събира, но ПРЕЗАПИСВА скалата на тримесечия и месеци с ДАТИ —
    тоест точно това, което възложителят отхвърли.
    """
    app.TimescaleEdit(MajorUnits=дни, MinorUnits=дни,
                      MajorLabel=ден_с_дума, MinorLabel=ден_номер,
                      MajorCount=едра_стъпка, MinorCount=дребна_стъпка,
                      TierCount=редове, Enlarge=увеличение)


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


def _мрежа(app) -> None:
    """Разграфка в самата графика — „иначе се изгубваш“.

    Забележка на инженера (03.09.2026): без линии лентата на ден 140 не може
    да се съпостави с лентата на ден 141.  Цветът и видът на линията се задават
    с КОНСТАНТИТЕ на MS Project (`pjSilver`, `pjDot`), а не с RGB число — при
    RGB повикването се връща „Exception occurred“ и мрежата тихо липсва.
    """
    СРЕБЪРНО, ТОЧКИ, ПЛЪТНА = 15, 3, 0        # pjSilver, pjDot, pjSolid
    редове = ((0, ТОЧКИ),      # pjGanttRows — редовете в графиката
              (1, ТОЧКИ),      # pjBarRows
              (2, ПЛЪТНА),     # pjMajorColumns — едрите колони на скалата
              (3, ТОЧКИ),      # pjMinorColumns — всеки ден
              (5, ТОЧКИ),      # pjGanttSheetRows — редовете на таблицата
              (6, ТОЧКИ))      # pjGanttSheetColumns — колоните на таблицата
    сложени = []
    for вид, стил in редове:
        try:
            app.GridlinesEdit(Item=вид, NormalType=стил, NormalColor=СРЕБЪРНО)
            сложени.append(вид)
        except Exception:
            continue
    print(f"мрежа: {len(сложени)} от {len(редове)} вида линии {сложени}")


def _заглавен_ред(app) -> None:
    """Обектът и общият срок стоят на първия ред на самия график.

    „Няма наименование на обекта, нито за колко дни ще бъде изпълнен.“  И
    двете ги има в обобщаващата задача на проекта — тя просто не се показваше.
    Параметърът се казва `ProjectSummary`; `ProjectSummaryTask` не съществува
    в тази версия и хвърля TypeError, а не COM грешка.
    """
    try:
        app.OptionsViewEx(ProjectSummary=True)
        print("заглавен ред: показан")
    except Exception as грешка:
        print(f"ВНИМАНИЕ: заглавният ред не се включи ({грешка})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml")
    parser.add_argument("mpp")
    parser.add_argument("--name", default="", help="име на проекта във файла")
    parser.add_argument("--pin", action="store_true",
                        help="закови всяко начало (старото поведение)")
    args = parser.parse_args()

    изход = преобразувай(Path(args.xml), Path(args.mpp), args.name, args.pin)
    print(f"MPP: {изход} ({изход.stat().st_size} байта)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
